"""Button capture: pick SCROLL_BTN / SCROLL_BTN_H / DISABLED_BTN by pressing them.

Primary mode listens on the daemon's virtual output node (`veloscroll-mouse`): the daemon
forwards every unconsumed event there verbatim, so any button that is NOT currently one of
SCROLL_BTN/SCROLL_BTN_H/DISABLED_BTN appears exactly as pressed while the service keeps running.

Those three configured codes are swallowed by the daemon and never reach the virtual node. When
a target field still holds such a code (e.g. re-capturing the current DISABLED_BTN), the dialog
says so and offers [Stop daemon & capture directly]: stop the veloscroll service via the helper,
listen on the device's physical node(s) instead, then start it again — issued on every close /
cancel path once we are the ones who stopped it.

When no device is grabbed yet there is no virtual node at all; the dialog listens on physical
nodes directly without touching the service (the nodes are readable ungrabbed). Clicking a code
in the list only SELECTS it: [Save] writes it into the shared edit buffer via ConfigTab.set_key_raw,
[Apply] commits config.ini now with the selection included (same privileged path as the tab's Apply),
and Close discards an unsaved selection without writing anything.

The listening itself runs through the helper service's StartCapture (root, polkit-gated per start) —
strictly read-only by design (nodes are opened O_RDONLY and never written to, so no injection is
possible): press edges arrive as PressEvent signals until the stream ends (all nodes vanished or its
1 h cap), at which point [Refresh source] starts a new one. Source detection uses in-process
enumeration as a fast path only when the user has direct /dev/input access; otherwise it falls back
to root (`Devices` over D-Bus), so none of this needs input-group membership — just the helper service.
"""
import json

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QVBoxLayout)

from .devices import enumerate_local, virtual_name
from .proc import PrivError, PrivJob, CaptureThread
from .values import resolve as resolve_values

CAPTURE_KEYS = ("SCROLL_BTN", "DISABLED_BTN", "SCROLL_BTN_H")

_CODES_BY_NAME = None


def code_name(code):
    """evdev name for a key code (e.g. 275 -> BTN_SIDE); the decimal alone when unknown."""
    global _CODES_BY_NAME
    if _CODES_BY_NAME is None:
        try:
            from evdev import ecodes as e
            _CODES_BY_NAME = {v: k for k, v in vars(e).items()
                              if isinstance(v, int) and not isinstance(v, bool)
                              and (k.startswith("BTN_") or k.startswith("KEY_"))}
        except Exception:
            _CODES_BY_NAME = {}
    return _CODES_BY_NAME.get(code, f"code {code}")


class CaptureDialog(QDialog):
    """Per-target capture dialog for one button key (see module docstring).

    Non-modal on purpose: the user can press Apply on the tab behind it while captures keep
    arriving. The [Stop daemon & capture directly] flow is a small state machine whose only exit
    from the stopped state is close/cancel, which always issues `priv.py service start` first. If the
    polkit prompt for StartCapture is denied or cancelled, the dialog closes itself — there is nothing
    to listen on and the user clearly does not want to capture."""

    MODE_VIRTUAL = "virtual"      # daemon running — listening on its virtual node
    MODE_DIRECT = "direct"        # no virtual node — physical nodes directly, service untouched
    MODE_STOPPED = "stopped"      # we stopped the service — it must be started again on exit

    def __init__(self, key, config_tab, watcher, parent=None):
        super().__init__(parent)
        self.key = key
        self.config_tab = config_tab
        self.watcher = watcher

        self._mode = None
        self._phase = "idle"          # idle | stopping | restarting | done
        self._we_stopped = False
        self._close_requested = False
        self._thread = None
        self._job = None
        self._resolving = False       # a root-fallback enumeration (priv.py devices) is in flight
        self._devices = []            # last resolved device list (in-process or root fallback)
        self._stop_path = []          # physical node(s) captured before the service stop
        self._selected_code = None    # last code clicked in the list — nothing is written until [Save] or [Apply]

        self.setWindowTitle(f"Capture {key}")
        self.resize(480, 460)

        lay = QVBoxLayout(self)
        header = QLabel(f"<b>Capture {key}</b> — press the buttons on your mouse; "
                        f"click a code below to assign it.")
        header.setWordWrap(True)
        lay.addWidget(header)

        self.lbl_source = QLabel("")    # no stylesheet: palette default text color (theme-safe)
        self.lbl_source.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.lbl_source)

        self.lbl_swallowed = QLabel("")
        self.lbl_swallowed.setWordWrap(True)
        self.lbl_swallowed.setStyleSheet("color: #b45309;")
        self.lbl_swallowed.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.lbl_swallowed)

        btns_top = QHBoxLayout()
        self.btn_stop_daemon = QPushButton("Stop daemon & capture directly")
        self.btn_stop_daemon.setToolTip(
            "Stops the veloscroll service, listens on the physical device node instead, and "
            "restarts the service when this dialog closes. Only needed for codes the daemon swallows.")
        self.btn_stop_daemon.clicked.connect(self._on_stop_daemon)
        btn_refresh = QPushButton("Refresh source")
        btn_refresh.setToolTip("Re-detect the virtual / physical nodes (after a service restart or grab change)")
        btn_refresh.clicked.connect(self._start_default)
        btns_top.addWidget(self.btn_stop_daemon, 1)
        btns_top.addWidget(btn_refresh)
        lay.addLayout(btns_top)

        self.list = QListWidget()
        self.list.itemClicked.connect(self._on_item_clicked)
        lay.addWidget(self.list, 1)

        self.lbl_status = QLabel("listening…")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.lbl_status)

        btns = QHBoxLayout()
        btn_clear = QPushButton("Clear list")
        btn_clear.clicked.connect(self.list.clear)
        self.btn_save = QPushButton("Save")
        self.btn_save.setToolTip(
            "Write the selected code into this tab's edit buffer — it stays pending until you press "
            "[Apply] (here or on any tab)")
        self.btn_save.clicked.connect(self._on_save)
        self.btn_apply = QPushButton("Apply")
        self.btn_apply.setToolTip(
            "Write config.ini now with the selected code included, plus any other pending edits — same as "
            "the tab's Apply (goes through the polkit-gated helper service)")
        self.btn_apply.clicked.connect(self._on_apply)
        btn_close = QPushButton("Close")
        btn_close.setDefault(True)
        btn_close.setToolTip("Close without writing anything new — an unsaved selection is discarded")
        btn_close.clicked.connect(self.close)
        btns.addStretch(1)
        btns.addWidget(btn_clear)
        btns.addWidget(self.btn_save)
        btns.addWidget(self.btn_apply)
        btns.addWidget(btn_close)
        lay.addLayout(btns)

        # re-render the swallowed-codes note from live state (an Apply behind this dialog changes it)
        self._note_timer = QTimer(self)
        self._note_timer.setInterval(2000)
        self._note_timer.timeout.connect(self._render_swallowed_note)
        self._note_timer.start()

        self._start_default()

    # -- source selection ----------------------------------------------------------

    def _swallowed_codes(self):
        """{code: key} for every code the running daemon currently swallows (never reaches the virtual node)."""
        values, _dataset, _errors = resolve_values(getattr(self.watcher, "payload", None))
        if not isinstance(values, dict) or not values:
            return {}
        out = {}
        for key in CAPTURE_KEYS:
            v = values.get(key)
            if isinstance(v, int) and not isinstance(v, bool):
                out[v] = key
        return out

    def _resolve_devices(self, on_ready):
        """Full device list via the fastest available path — in-process enumeration when it is complete
        and non-empty (the user has direct /dev/input access), else the root fallback `priv.py devices`.

        Always ends in on_ready(devices, None) with [{"path","name"}, …] (possibly empty), or
        on_ready([], (kind, detail)) when even the fallback fails."""
        self._resolving = True
        devices, complete = enumerate_local()
        if complete and devices:
            self._resolving = False
            on_ready(devices, None)
            return
        job = PrivJob(["devices"], self)

        def ok(out):
            try:
                raw = json.loads(out or "[]")
                devs = [{"path": d["path"], "name": d["name"]} for d in raw]
            except (ValueError, KeyError) as exc:
                on_ready([], ("command_failed", f"unparseable JSON ({exc})"))
                return
            on_ready(devs, None)

        def bad(kind, detail):
            on_ready([], (kind, detail))

        job.finished_ok.connect(ok)
        job.failed.connect(bad)
        job.start()

    def _physical_paths(self, state=None):
        """Physical node(s) to listen on when the virtual one is unavailable — the grabbed device's
        own path(s) from the state file first (no enumeration needed), else every non-virtual node
        we know about."""
        if state is None:
            state = getattr(self.watcher, "payload", None)
        if isinstance(state, dict):
            paths = [d.get("path") for d in (state.get("devices_grabbed") or []) if d.get("path")]
            if paths:
                return paths
        vname = virtual_name()
        return [d["path"] for d in self._devices if d["name"] != vname]

    def _start_default(self):
        """(Re)pick the listening source from scratch: virtual node first, physical direct otherwise.

        Source detection is two-stage — in-process when complete and non-empty (direct /dev/input
        access), else the root fallback; state-file grabbed paths need no enumeration at all."""
        if self._phase in ("stopping", "restarting") or self._resolving:
            return
        self.lbl_status.setText("detecting input nodes…")
        self._resolve_devices(self._on_sources_resolved)

    def _on_sources_resolved(self, devices, error):
        self._resolving = False
        if error is not None:
            kind, detail = error
            hint = " — install and start the helper service (see the Permissions panel)" \
                if kind == PrivError.KIND_SERVICE_MISSING else ""
            self.lbl_status.setText(f"could not enumerate input nodes ({detail}){hint}")
            return
        self._devices = devices
        vname = virtual_name()
        vpath = next((d["path"] for d in devices if d["name"] == vname), None)
        if vpath is not None:
            self._mode = CaptureDialog.MODE_VIRTUAL
            self._we_stopped = False
            self.lbl_source.setText(f"listening on the virtual node {vpath} (daemon keeps running)")
            self.btn_stop_daemon.setEnabled(True)
            self._start_thread([vpath])
        else:
            paths = self._physical_paths()
            self._mode = CaptureDialog.MODE_DIRECT
            self._we_stopped = False
            src = ", ".join(paths) if paths else "no readable nodes"
            extra = (" the daemon may grab its device within ~2–4 s and silence this view — "
                     "close and reopen to switch to the virtual node") \
                if getattr(self.watcher, "service_state", "") == "active" else ""
            self.lbl_source.setText(f"no virtual node yet (no device grabbed) — listening on physical nodes: {src}.{extra}")
            self.btn_stop_daemon.setEnabled(False)     # nothing to stop; the daemon holds no grab
            self._start_thread(paths)

    def _stop_thread(self):
        t, self._thread = self._thread, None
        if t is not None:
            t.stop()
            t.wait(800)          # run() exits within ~200 ms of stop(); never delete a live thread
            if not t.isRunning():
                t.deleteLater()

    def _start_thread(self, paths):
        self._stop_thread()
        if not paths:
            self.lbl_status.setText(
                "no input node to listen on — check the Permissions panel (the helper service)")
            return
        self._thread = CaptureThread(paths, self)
        self._thread.pressed.connect(self._on_pressed)
        self._thread.failed.connect(self._on_listener_failed)
        self._thread.start()

    # -- stop-daemon fallback --------------------------------------------------------

    def _on_stop_daemon(self):
        if self._phase != "idle" or self._mode != CaptureDialog.MODE_VIRTUAL:
            return
        state = getattr(self.watcher, "payload", None)
        self._stop_path = self._physical_paths(state)  # grabbed path(s) as the daemon knows them, read BEFORE stopping
        self._phase = "stopping"
        self.btn_stop_daemon.setEnabled(False)
        self._stop_thread()                            # the virtual node goes quiet once the daemon stops anyway
        self.lbl_status.setText("stopping the veloscroll service…")
        job = PrivJob(["service", "stop"], self)
        job.finished_ok.connect(self._on_stopped)
        job.failed.connect(self._on_stop_failed)
        job.start()

    def _on_stopped(self, _out):
        if self._phase != "stopping":
            return
        self._phase = "idle"                       # the service op is done; "stopped" is a mode, not an in-flight phase
        self._we_stopped = True
        self._mode = CaptureDialog.MODE_STOPPED
        self.lbl_source.setText("daemon stopped — listening on the physical node(s) directly; "
                                "the service restarts when this dialog closes")
        if not self._stop_path:
            # no grabbed paths in the state file — derive them from whatever device list we resolved
            self._stop_path = self._physical_paths()
        if self._close_requested:                         # close was hit while the stop was still in flight —
            self.lbl_status.setText("daemon stopped — restarting…")   # no point listening; go straight to the restart
            self._begin_restart()
        else:
            self.lbl_status.setText("daemon stopped — press the buttons you want (everything reaches this list)")
            self._start_thread(self._stop_path)

    def _on_stop_failed(self, kind, detail):
        if self._phase != "stopping":
            return
        hint = " — install and start the helper service (see the Permissions panel)" \
            if kind == PrivError.KIND_SERVICE_MISSING else ""
        self._phase = "idle"                            # nothing is in flight anymore; close must work again
        self.btn_stop_daemon.setEnabled(True)          # offer the retry now that we are back on the virtual node
        self._mode = CaptureDialog.MODE_VIRTUAL         # back to where we were: daemon still running
        self.lbl_status.setText(f"could not stop the service ({detail}){hint} — staying on the virtual node")
        vname = virtual_name()
        vpath = next((d["path"] for d in self._devices if d["name"] == vname), None)   # from our last resolution
        if vpath is not None:
            self._start_thread([vpath])
        if self._close_requested:                         # nothing was stopped, so a plain close is safe now
            self.accept()

    def _begin_restart(self):
        """Issue the guaranteed `priv.py service start` and let go of the dialog.

        The only exit from MODE_STOPPED goes through here (plain close/cancel, or a close that
        raced an in-flight stop), so the restart is issued on every path."""
        if self._phase == "restarting":                   # a double close / stray signal must not re-fire this
            return
        self._phase = "restarting"
        self.hide()                                       # gone for the user; the restart runs behind, ~1 s
        job = PrivJob(["service", "start"], self)
        job.finished_ok.connect(self._on_restarted)
        job.failed.connect(lambda _k, d: self._finish_restart(
            f"could NOT restart veloscroll ({d}) — start it yourself: systemctl start veloscroll"))
        job.start()

    # -- capture list -----------------------------------------------------------------

    def _on_pressed(self, code):
        item = QListWidgetItem(f"{code}   {code_name(code)}")
        self.list.insertItem(0, item)
        while self.list.count() > 300:                   # bound the list; newest first
            self.list.takeItem(self.list.count() - 1)
        assigned = (self.config_tab.model.values.get(self.key) == code)
        if assigned:
            f = item.font(); f.setBold(True); item.setFont(f)
        cur = self.config_tab.model.values.get(self.key)
        cur_text = "current value" if cur is not None else "no current value"
        self.lbl_status.setText(
            f"pressed {code} ({code_name(code)}) — click it to assign · {self.key}: {cur_text}")

    def _on_item_clicked(self, item):
        code = int(item.text().split()[0])
        self._selected_code = code                       # selection only — nothing is written yet
        self.lbl_status.setText(f"{self.key} = {code} ({code_name(code)}) selected")

    def _on_save(self):
        if self._selected_code is None:
            self.lbl_status.setText("click a code in the list first — then [Save] puts it into the edit buffer")
            return
        code = self._selected_code
        self.config_tab.set_key_raw(self.key, str(code))
        self.lbl_status.setText(
            f"{self.key} = {code} ({code_name(code)}) saved to the edit buffer — press [Apply] to write config.ini")

    def _on_apply(self):
        if self._selected_code is not None:
            # "apply it directly": the picked value goes into the buffer first, so this commit includes it
            self.config_tab.set_key_raw(self.key, str(self._selected_code))
        self.config_tab.apply()                          # unchanged privileged path — commits the whole buffer

    def _on_listener_failed(self, kind, detail):
        self._thread = None
        if kind == PrivError.KIND_NOT_AUTHORIZED:
            # the polkit prompt was denied or cancelled — the user does not want to capture; go away.
            # closeEvent handles every state (incl. restarting a daemon we stopped for direct capture).
            self.close()
            return
        # no re-arm by design (the service never restarts a stream) — [Refresh source] starts a new one
        self.lbl_status.setText(f"listener stopped: {detail} — press [Refresh source] to start again")

    # -- swallowed-codes note -----------------------------------------------------------

    def _render_swallowed_note(self):
        if self._mode == CaptureDialog.MODE_STOPPED:
            self.lbl_swallowed.setText("")               # nothing is swallowed with the daemon down
            return
        sw = self._swallowed_codes()
        cur = self.config_tab.model.values.get(self.key)
        parts = []
        for code, key in sorted(sw.items()):
            parts.append(f"{code} ({code_name(code)}) — {key}")
        if not parts:
            self.lbl_swallowed.setText("")
            return
        text = ("the daemon swallows these codes while running — they will NOT appear here: " + "; ".join(parts))
        if isinstance(cur, int) and cur in sw:
            text += f"\n{self.key} currently IS {cur} ({code_name(cur)}) — press [Stop daemon & capture directly] to see it."
        self.lbl_swallowed.setText(text)

    # -- close / restart guarantee --------------------------------------------------------

    def shutdown(self):
        """App-exit hook: stop the listener without any service-side effects (ConfigTab handles the
        guaranteed `service start` itself, synchronously, before quitting)."""
        self._note_timer.stop()
        self._stop_thread()

    def _shutdown(self):
        if self._thread is not None:
            self._thread.stop()
            self._thread = None
        self._note_timer.stop()
        self._phase = "done"

    def closeEvent(self, event):
        if self._phase in ("stopping", "restarting"):
            # an in-flight service op decides the outcome — finish it first, then hide ourselves
            self._close_requested = True
            event.ignore()
            return
        self._note_timer.stop()
        self._stop_thread()
        if self._mode == CaptureDialog.MODE_STOPPED and self._we_stopped:
            # the service must come back before this dialog can really go away
            self._begin_restart()          # emits finished itself once the restart is done
        else:
            event.accept()
            # a plain closeEvent accept only HIDES a non-modal QDialog — it does not emit `finished`,
            # which ConfigTab relies on to drop the dialog from its registry; accept() == done(Accepted)
            self.accept()

    def _on_restarted(self, _out):
        self._finish_restart(None)

    def _finish_restart(self, error_text):
        if self._phase != "restarting":                  # a double close / stray signal must not re-fire this
            return
        self._stop_thread()                              # safety net: no listener may outlive the dialog's done()
        self._we_stopped = False
        win = self.window()
        if error_text is None and hasattr(win, "config_banner"):
            win.config_banner("veloscroll service restarted after button capture", "success")
        elif error_text is not None:
            from .errors import popup_error              # local import: errors.py stays dialog-free otherwise
            popup_error(win, "Button capture — restart failed",
                        error_text + "\n\nThe daemon will pick up any captured value at its next start.")
        self._phase = "done"
        self.accept()
