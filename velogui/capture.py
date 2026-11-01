"""Button capture (chunk 4): pick SCROLL_BTN / SCROLL_BTN_H / DISABLED_BTN by pressing them.

Primary mode listens on the daemon's virtual output node (`veloscroll-mouse`): the daemon
forwards every unconsumed event there verbatim, so any button that is NOT currently one of
SCROLL_BTN/SCROLL_BTN_H/DISABLED_BTN appears exactly as pressed while the service keeps running.

Those three configured codes are swallowed by the daemon and never reach the virtual node. When
a target field still holds such a code (e.g. re-capturing the current DISABLED_BTN), the dialog
says so and offers [Stop daemon & capture directly]: `priv.py service stop`, listen on the
device's physical node(s) instead, then `priv.py service start` again — issued on every close /
cancel path once we are the ones who stopped it.

When no device is grabbed yet there is no virtual node at all; the dialog listens on physical
nodes directly without touching the service (the nodes are readable ungrabbed). Clicking a code
in the list only SELECTS it: [Save] writes it into the shared edit buffer via ConfigTab.set_key_raw,
[Apply] commits config.ini now with the selection included (same privileged path as the tab's Apply),
and Close discards an unsaved selection without writing anything.
"""
import select

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QVBoxLayout)

from .devices import enumerate_local, virtual_name
from .proc import PrivError, PrivJob
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


def find_virtual_node():
    """Path of the daemon's virtual output node among /dev/input/event*, else None (no grab yet)."""
    vname = virtual_name()
    devices, _complete = enumerate_local()
    for dev in devices:
        if dev["name"] == vname:
            return dev["path"]
    return None


def physical_node_paths(state):
    """Physical nodes to listen on when the virtual one is unavailable.

    The grabbed device(s)' own node(s) from the state file; when there is no state data, every
    readable non-virtual event node — any of them may be the user's mouse."""
    if isinstance(state, dict):
        paths = [d.get("path") for d in (state.get("devices_grabbed") or []) if d.get("path")]
        if paths:
            return paths
    vname = virtual_name()
    devices, _complete = enumerate_local()
    return [dev["path"] for dev in devices if dev["name"] != vname]


class CaptureThread(QThread):
    """Reads EV_KEY press edges (value == 1) from one or more /dev/input nodes off the GUI thread.

    BTN_LEFT is dropped: assigning a code IS a left click in this dialog, so capturing it would flood
    the list with UI noise on every assignment. One select() over every node's fd with a short timeout,
    draining each readable node with the non-blocking read_one() — so a hot mouse and a quiet keyboard
    share one loop without blocking."""

    pressed = Signal(int)      # key code of a press edge (repeats are value == 2 and ignored)
    failed = Signal(str)       # could not open any node / all vanished mid-capture

    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self._paths = list(paths)
        self._stop = False

    def stop(self):
        """Ask for shutdown and wait briefly; the select timeout bounds how long this can take."""
        self._stop = True
        self.wait(1500)

    def run(self):
        try:
            import evdev
            from evdev import ecodes as e
        except ImportError as exc:
            self.failed.emit(f"cannot import evdev (is python3-evdev installed?): {exc}")
            return
        devs = []
        errors = []
        for p in self._paths:
            try:
                devs.append(evdev.InputDevice(p))
            except OSError as exc:
                errors.append(f"{p}: {exc.strerror or exc}")
        if not devs:
            detail = "; ".join(errors[:3]) or "no candidate nodes"
            hint = (" (permission denied usually means the user is not in the `input` group — OR this GUI "
                    "process started before that membership took effect, so its credentials are stale; "
                    "restart the GUI. See the Permissions panel)" if any("denied" in x for x in errors) else "")
            self.failed.emit(f"could not open any input node: {detail}{hint}")
            return
        try:
            by_fd = {d.fileno(): d for d in devs}
            while not self._stop and by_fd:
                try:
                    ready, _w, _x = select.select(list(by_fd), [], [], 0.2)
                except (OSError, ValueError):
                    break
                for fd in ready:
                    dev = by_fd.get(fd)
                    if dev is None:
                        continue
                    while True:
                        try:
                            ev = dev.read_one()
                        except OSError:
                            del by_fd[fd]          # node vanished mid-capture (unplug / daemon released it)
                            break
                        if ev is None:
                            break
                        # BTN_LEFT is UI noise here — clicking a code in the list to assign it IS a left click
                        if ev.type == e.EV_KEY and ev.value == 1 and ev.code != e.BTN_LEFT:
                            self.pressed.emit(ev.code)
        finally:
            for d in devs:
                try:
                    d.close()
                except Exception:
                    pass


class CaptureDialog(QDialog):
    """Per-target capture dialog for one button key (see module docstring).

    Non-modal on purpose: the user can press Apply on the tab behind it while captures keep
    arriving. The [Stop daemon & capture directly] flow is a small state machine whose only exit
    from the stopped state is close/cancel, which always issues `priv.py service start` first."""

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
        self._stop_path = []          # physical node(s) captured before the service stop
        self._selected_code = None    # last code clicked in the list — nothing is written until [Save] or [Apply]

        self.setWindowTitle(f"Capture {key}")
        self.resize(480, 460)

        lay = QVBoxLayout(self)
        header = QLabel(f"<b>Capture {key}</b> — press the buttons on your mouse; "
                        f"click a code below to assign it.")
        header.setWordWrap(True)
        lay.addWidget(header)

        self.lbl_source = QLabel("")
        self.lbl_source.setStyleSheet("color: gray;")
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
            "the tab's Apply (needs the scoped sudoers rule)")
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

    def _start_default(self):
        """(Re)pick the listening source from scratch: virtual node first, physical direct otherwise."""
        if self._phase in ("stopping", "restarting"):
            return
        vpath = find_virtual_node()
        if vpath is not None:
            self._mode = CaptureDialog.MODE_VIRTUAL
            self._we_stopped = False
            self.lbl_source.setText(f"listening on the virtual node {vpath} (daemon keeps running)")
            self.btn_stop_daemon.setEnabled(True)
            self._start_thread([vpath])
        else:
            paths = physical_node_paths(getattr(self.watcher, "payload", None))
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
            if not t.isRunning():          # a wedged thread must never be deleted out from under itself
                t.deleteLater()

    def _start_thread(self, paths):
        self._stop_thread()
        if not paths:
            self.lbl_status.setText("no input node to listen on — check the Permissions panel (input group)")
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
        self._stop_path = physical_node_paths(state)   # grabbed path(s) as the daemon knows them, read BEFORE stopping
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
            self._stop_path = physical_node_paths(None)   # state is stale now: fall back to a fresh enumeration
        if self._close_requested:                         # close was hit while the stop was still in flight —
            self.lbl_status.setText("daemon stopped — restarting…")   # no point listening; go straight to the restart
            self._begin_restart()
        else:
            self.lbl_status.setText("daemon stopped — press the buttons you want (everything reaches this list)")
            self._start_thread(self._stop_path)

    def _on_stop_failed(self, kind, detail):
        if self._phase != "stopping":
            return
        hint = " — install the scoped sudoers rule (see the Permissions panel)" \
            if kind == PrivError.KIND_RULE_MISSING else ""
        self._phase = "idle"                            # nothing is in flight anymore; close must work again
        self.btn_stop_daemon.setEnabled(True)          # offer the retry now that we are back on the virtual node
        self._mode = CaptureDialog.MODE_VIRTUAL         # back to where we were: daemon still running
        self.lbl_status.setText(f"could not stop the service ({detail}){hint} — staying on the virtual node")
        vpath = find_virtual_node()
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

    def _on_listener_failed(self, detail):
        self._thread = None
        self.lbl_status.setText(f"listener stopped: {detail}")

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
