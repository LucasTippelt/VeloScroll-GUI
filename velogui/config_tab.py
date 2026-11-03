"""Merged Status+Config page: one table over the remaining keys with Apply/Cancel.

Columns per key row: Key | Effective value (live, read-only) | Edit (typed widget) |
Description (static hint, effective-fallback note when unset, or the validation error).
The Daemon group (service state / grabbed devices / reload health) and the Permissions
panel sit on the same page above and below the table.

This tab owns the edit buffer for ALL keys (the 30 daemon keys plus the GUI-owned STARTUP_TAB)
and creates one _FieldRow per key; other tabs place their share of rows elsewhere instead of in
this table — the feel & scroll-mode fields (SCROLL_INVERT_KEYS) on the Scroll/Invert tab, the ramp
options (RAMP_KEYS) on the Ramp tab next to their live curves, the device & button fields
(DEVICE_KEYS) on the Device Picker tab next to the mouse selector — and every tab carries its own
Apply/Cancel bar (apply_bar.py) registered here: all bars drive the same buffer, so a change made
on any tab is applied/discarded from any. STARTUP_TAB (which tab opens on startup) has its combo
row in this page's table; the daemon ignores the key itself.

- Typed widgets per key kind (checkboxes, spin boxes, text fields accepting the
  daemon's own syntax: evdev name or decimal for buttons, pair/path/none for DEVICE_MATCH).
- The three button keys carry a [Capture…] button opening the press-and-pick capture dialog
  (press the physical button, click its code in the list; [Save] writes it into this buffer and
  [Apply] commits config.ini with it included — Close discards an unsaved selection).
- Nullable *_H / button / device fields show their effective fallback when unset;
  clearing an existing value writes explicit `none`.
- Validation mirrors the daemon inline (per-key parse + check_config cross-checks) and
  blocks Apply; priv.py re-validates with the same parser as defense-in-depth.
- Apply runs in a worker thread via priv.py apply-config; after success the reload is
  confirmed from the state file (~2 s when STATE_FILE is on) or, without one, from the
  journal (priv.py logs) — banner flips to success/failure accordingly.
- Cancel restores the editor from memory and, when a change was already applied, writes
  the previous text back through the same privileged path so the daemon reloads it.
"""
import math
import os

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QGroupBox,
                                QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QTableWidget,
                                QTableWidgetItem, QVBoxLayout, QWidget)

from . import BUS_NAME, CONFIG_PATH, TABS
from . import values as vmod
from .apply_bar import ApplyBar
from .capture import CaptureDialog
from .config_model import FALLBACK, GUI_KEYS, KEY_KINDS, NULLABLE, RAMP_KEYS, _render, ConfigModel
from .errors import popup_error
from .permissions_panel import PermissionsPanel, SERVICE_FIX
from .proc import PrivError, PrivJob, run_priv
from .update_check import UpdateCheckJob
from .update_dialog import UpdateDialog

_SERVICE_COLORS = {"active": "#16a34a", "failed": "#dc2626"}
_UPDATE_BTN_AMBER = "background-color: #fef3c7; color: #1f2937;"   # update-button tint while an update is available — same pair as backups_tab's CHANGED_BG/CHANGED_FG, legible under light AND dark themes

# (title, static hint) per key — the short form of config.ini.example's comments
TITLES = {
    "DPI": ("DPI", "counts/inch (your mouse's current DPI)"),
    "DEADZONE_IN": ("DEADZONE_IN", "inches; nothing scrolls below this"),
    "FULL_IN": ("FULL_IN", "inches to full rate (> DEADZONE_IN)"),
    "MAX_RATE": ("MAX_RATE", "notches/s at FULL_IN and beyond"),
    "MIN_RATE": ("MIN_RATE", "notches/s just past the deadzone (> 0)"),
    "CURVE": ("CURVE", ">1 gentle start · =1 linear · <1 steep start"),
    "OUT_SMOOTH": ("OUT_SMOOTH", "0..1 how quickly the rate follows (lower = smoother)"),
    "TICK": ("TICK", "ticker period in seconds (~250 Hz at 0.004)"),
    "DEADZONE_IN_H": ("DEADZONE_IN_H", "horizontal deadzone override"),
    "FULL_IN_H": ("FULL_IN_H", "horizontal full-rate distance override"),
    "MIN_RATE_H": ("MIN_RATE_H", "horizontal floor rate override"),
    "MAX_RATE_H": ("MAX_RATE_H", "horizontal top rate override"),
    "CURVE_H": ("CURVE_H", "horizontal ramp shape override"),
    "OUT_SMOOTH_H": ("OUT_SMOOTH_H", "horizontal smoothing override"),
    "INVERT": ("INVERT", "flip scroll direction (natural scrolling)"),
    "INVERT_H": ("INVERT_H", "H-axis variant of INVERT; none falls back to vertical"),
    "TOGGLE_SCROLL": ("TOGGLE_SCROLL", "True = tap to toggle, False = hold-to-scroll"),
    "VERTICAL_H": ("VERTICAL_H", "H button drives a second vertical scroll"),
    "LEFT_CLICK_RESET": ("LEFT_CLICK_RESET", "left release mid-session: true = fresh origin, false = keep scrolling at old speed"),
    "TOGGLE_SCROLL_H": ("TOGGLE_SCROLL_H", "horizontal variant of TOGGLE_SCROLL"),
    "NAME_MATCH": ("NAME_MATCH", "fallback selector: substring of the device name"),
    "DEVICE_MATCH": ("DEVICE_MATCH", "USB pair (0x46d, 0x4093) · stable path · none"),
    "SCROLL_BTN": ("SCROLL_BTN", "evdev name or decimal code; none not allowed"),
    "DISABLED_BTN": ("DISABLED_BTN", "swallowed button; evdev name/decimal or none"),
    "SCROLL_BTN_H": ("SCROLL_BTN_H", "horizontal hold button; none disables"),
    "LOG_LEVEL": ("LOG_LEVEL", "0..3 — 2 = config detail (reload lines)"),
    "LATENCY_LOG": ("LATENCY_LOG", "per-second latency summary lines"),
    "MAX_BACKUPS": ("MAX_BACKUPS", "-1 unlimited · 0 disabled · N keep newest N"),
    "STATE_FILE": ("STATE_FILE", "write /run/veloscroll/state for GUIs (GUI switch)"),
    "DISTANCE_FILE": ("DISTANCE_FILE", "write /run/veloscroll/distance live feed for the curve markers (GUI switch)"),
    "STARTUP_TAB": ("STARTUP_TAB", "tab opened on startup — GUI only, the daemon ignores this key"),
    "TRAY_LED": ("TRAY_LED", "status-LED tray icon: green while a scroll session is active, gray when idle — GUI only (applies live; the daemon ignores this key)"),
}

SECTIONS = (   # display order per plan, minus SCROLL_INVERT_KEYS (Scroll/Invert tab), RAMP_KEYS (Ramp tab),
              # DEVICE_KEYS (Device Picker tab) and the GUI-owned lifecycle keys STATE_FILE / DISTANCE_FILE
              # (no selector rows — forced on while open, off at exit): gui / logging / misc
    ("GUI", ("STARTUP_TAB", "TRAY_LED")),
    ("Logging", ("LOG_LEVEL", "LATENCY_LOG")),
    ("Misc", ("MAX_BACKUPS",)),
)

# float spin-box geometry per key: (decimals, min, max, step)
FLOAT_GEOM = {
    "DPI": (0, 1.0, 20000.0, 10.0),
    "DEADZONE_IN": (3, 0.001, 50.0, 0.005),
    "FULL_IN": (3, 0.005, 100.0, 0.05),
    "MAX_RATE": (1, 0.1, 10000.0, 1.0),
    "MIN_RATE": (1, 0.1, 10000.0, 0.5),
    "CURVE": (2, 0.01, 10.0, 0.05),
    "OUT_SMOOTH": (2, 0.01, 1.0, 0.05),
    "TICK": (4, 0.0005, 1.0, 0.001),
}

# float spin-box geometry per nullable *_H ramp key: (decimals, min, max, step) — value 0 means unset
# (shown via specialValueText and written as "none"), mirroring the vertical keys' > 0 ranges
NFLOAT_GEOM = {
    "DEADZONE_IN_H": (3, 0.0, 50.0, 0.005),
    "FULL_IN_H": (3, 0.0, 100.0, 0.05),
    "MIN_RATE_H": (1, 0.0, 10000.0, 0.5),
    "MAX_RATE_H": (1, 0.0, 10000.0, 1.0),
    "CURVE_H": (2, 0.0, 10.0, 0.05),
    "OUT_SMOOTH_H": (2, 0.0, 1.0, 0.05),
}

RED_BORDER = "border: 1px solid #dc2626; border-radius: 3px;"
OK_BORDER = ""


def _fmt_float(v, decimals):
    s = f"{v:.{decimals}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


class _FieldRow(QWidget):
    """One table row's data: a typed editor widget + its description label.

    ConfigTab places `container` in the Edit column and `hint` in the Description column;
    this object owns both so get_raw()/set_display()/set_state() keep working unchanged.
    For button keys, `container` wraps the editor plus a [Capture…] button that
    opens the press-and-pick capture dialog; for every other key it is just the editor.
    get_raw() returns the exact text that would be written for the key ("" = unset);
    set_display(value) initializes the editor from a parsed effective value."""

    changed = Signal()

    def __init__(self, key, kind):
        super().__init__()
        self.key = key
        title, hint = TITLES[key]

        if kind == "bool":
            self.editor = QCheckBox()
            self.editor.toggled.connect(lambda _checked: self.changed.emit())   # toggled(bool) -> argument-less changed()
        elif kind == "tri":
            self.editor = QComboBox()
            self.editor.addItems(["(unset — falls back to vertical)", "False", "True"])
            self.editor.currentIndexChanged.connect(lambda _i: self.changed.emit())
        elif kind == "tab":                                        # STARTUP_TAB: one item per tab, in display order
            self.editor = QComboBox()
            self.editor.addItems(TABS)
            self.editor.currentIndexChanged.connect(lambda _i: self.changed.emit())
        elif kind == "int":
            self.editor = QSpinBox()
            if key == "LOG_LEVEL":
                self.editor.setRange(0, 3)
            else:                                        # MAX_BACKUPS
                self.editor.setRange(-1, 99)
            self.editor.valueChanged.connect(lambda _v: self.changed.emit())
        elif kind == "float":
            dec, lo, hi, step = FLOAT_GEOM[key]
            self.editor = QDoubleSpinBox()
            self.editor.setDecimals(dec)
            self.editor.setRange(lo, hi)
            self.editor.setSingleStep(step)
            self.editor.valueChanged.connect(lambda _v: self.changed.emit())
        elif kind == "nfloat":                           # nullable *_H ramp floats — spin box with 0 = unset
            dec, lo, hi, step = NFLOAT_GEOM[key]
            self.editor = QDoubleSpinBox()
            self.editor.setDecimals(dec)
            self.editor.setRange(lo, hi)
            self.editor.setSingleStep(step)
            self.editor.setSpecialValueText("(unset)")
            self.editor.valueChanged.connect(lambda _v: self.changed.emit())
        else:                                            # btn / name / device — the daemon's text syntax
            ph = {k: f"(unset — {FALLBACK[k][0]})" for k in NULLABLE}.get(key, "")
            if key == "NAME_MATCH":
                ph = "(built-in default applies)"
            self.editor = QLineEdit()
            self.editor.setPlaceholderText(ph)
            self.editor.textEdited.connect(lambda _t: self.changed.emit())

        # button keys get the press-and-pick capture button next to their editor; every other
        # key's container is just the editor. `container` and `hint` become children of a tab's cell /
        # grid (reparented by setCellWidget / addWidget); this row object keeps them together so
        # get_raw()/set_display()/set_state() keep working unchanged no matter which tab hosts the row.
        if kind == "btn":
            inner = QWidget()
            h = QHBoxLayout(inner)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(self.editor, 1)
            self.capture_btn = QPushButton("Capture…")
            self.capture_btn.setToolTip(f"Press the button on your mouse to capture its code into {key}")
            h.addWidget(self.capture_btn, 0)
            self.container = inner
        else:
            self.capture_btn = None
            self.container = self.editor

        # no stylesheet: palette default text color (theme-safe black/white); set_state applies red on error
        self.hint = QLabel(hint)
        self.hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._hint_default = hint

    def get_raw(self):
        e = self.editor
        if isinstance(e, QCheckBox):
            return "True" if e.isChecked() else "False"
        if isinstance(e, QComboBox):
            if self.key in GUI_KEYS:                     # STARTUP_TAB: every item is a real value (a tab label)
                return e.itemText(e.currentIndex())
            return "" if e.currentIndex() == 0 else e.itemText(e.currentIndex())   # tri: index 0 = unset
        if isinstance(e, (QSpinBox, QDoubleSpinBox)):
            v = e.value()
            if self.key in NFLOAT_GEOM and v == 0.0:    # spin-box floor = unset ("none" at write time)
                return ""
            return _fmt_float(v, e.decimals()) if isinstance(e, QDoubleSpinBox) else str(int(v))
        return e.text().strip()

    def set_display(self, value, raw=""):
        e = self.editor
        if isinstance(e, QCheckBox):
            e.blockSignals(True); e.setChecked(bool(value)); e.blockSignals(False)
        elif isinstance(e, QComboBox):
            if self.key in GUI_KEYS:                     # STARTUP_TAB: pick the stored label; absent/unknown -> first tab (the default)
                i = e.findText(str(value)) if value is not None else -1
                idx = i if i >= 0 else 0
            else:                                        # tri: index 0 = unset
                idx = 0 if value is None else (2 if value is True else 1)
            e.blockSignals(True); e.setCurrentIndex(idx); e.blockSignals(False)
        elif isinstance(e, (QSpinBox, QDoubleSpinBox)):
            e.blockSignals(True)
            try:
                v = 0.0 if value is None and self.key in NFLOAT_GEOM else float(value)   # unset -> spin-box floor
                e.setValue(v)
            except (TypeError, ValueError):
                pass
            e.blockSignals(False)
        else:                                            # text field: keep the file's own formatting when present
            e.blockSignals(True); e.setText(raw if raw is not None else ""); e.blockSignals(False)

    def set_state(self, error="", dynamic_hint=None):
        """error (red border + red label) wins over any hint; otherwise show the effective-fallback
        note for unset nullable keys or the static default hint."""
        if error:
            self.editor.setStyleSheet(RED_BORDER)
            self.hint.setText(error)
            self.hint.setStyleSheet("color: #dc2626;")
        else:
            self.editor.setStyleSheet(OK_BORDER)
            text = dynamic_hint if dynamic_hint is not None else self._hint_default
            self.hint.setText(text)
            self.hint.setStyleSheet("")    # clear the error's red — palette default text color (theme-safe)


class ConfigTab(QWidget):
    dirty_changed = Signal(bool)

    CONFIRM_TIMEOUT_MS = 10000     # state file should reflect an accepted reload well before this

    def __init__(self, watcher, parent=None):
        super().__init__(parent)
        self.watcher = watcher
        self.model = ConfigModel(self)
        self.rows = {}                # key -> _FieldRow, one per all 30 keys (the Ramp tab places its share out of this table)
        self._bars = []               # every tab's Apply/Cancel bar — driven from here (see register_bar)
        self.disk_text = ""           # last known content of CONFIG_PATH (dirty comparison target)
        self.pre_apply_text = None    # content before the last successful apply (Cancel reverts to it)
        self._disk_sig = None         # (mtime_ns, size) of disk_text
        self._check_manual = False    # a user-initiated [Check for updates…] is in flight (completion feedback)
        self._busy = False
        self._confirm = None          # {"values": {...}} while waiting for a reload confirmation
        self._confirm_timer = QTimer(self)
        self._confirm_timer.setSingleShot(True)
        self._confirm_timer.timeout.connect(self._confirm_timeout)

        self._fg_norm = QBrush()                       # null brush = default item color
        self._fg_red = QBrush(QColor("#dc2626"))
        self._eff_items = {}                           # key -> effective-value QTableWidgetItem

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)

        # -- daemon group (merged from the old Status tab) --------------------------
        dg = QGroupBox("Daemon")
        grid = QHBoxLayout(dg)
        self.lbl_service_dot = QLabel("●")
        self.lbl_service = QLabel("checking…")
        self.lbl_devices = QLabel("")
        self.lbl_reload = QLabel("")
        for l in (self.lbl_devices, self.lbl_reload):
            l.setTextInteractionFlags(Qt.TextSelectableByMouse)
        grid.addWidget(self.lbl_service_dot, 0, Qt.AlignVCenter)
        grid.addWidget(self.lbl_service)
        grid.addStretch(1)
        dev_col = QVBoxLayout()
        # no forced color (theme-safe): only the size is set — palette default text color applies
        dev_title = QLabel("grabbed devices")
        dev_title.setStyleSheet("font-size: small;")
        reload_col = QVBoxLayout()
        reload_title = QLabel("config reload health")
        reload_title.setStyleSheet("font-size: small;")
        dev_col.addWidget(dev_title)
        dev_col.addWidget(self.lbl_devices)
        reload_col.addWidget(reload_title)
        reload_col.addWidget(self.lbl_reload)
        grid.addLayout(dev_col, 1)
        grid.addLayout(reload_col, 1)
        # the two one-click updates stacked (daemon first, GUI directly below it) plus the on-demand
        # re-check of both repos against their origins, vertically centered in their cell — a wrapper
        # widget because QBoxLayout.addLayout() takes no alignment argument
        update_wrap = QWidget()
        update_col = QVBoxLayout(update_wrap)
        update_col.setContentsMargins(0, 0, 0, 0)
        update_col.addStretch(1)
        self.btn_update = QPushButton("Update daemon…")     # one-click update per docs/UPDATING.md; instance attrs — _on_update_check tints them amber while an update is available
        self.btn_update.setToolTip(
            "Pull the latest daemon code from git and restart the service (docs/UPDATING.md). "
            "The dialog shows a transcript of every step plus a rollback command for the previous commit.")
        self.btn_update.clicked.connect(self._open_update_dialog)
        update_col.addWidget(self.btn_update)
        self.btn_update_gui = QPushButton("Update GUI…")
        self.btn_update_gui.setToolTip(
            "Pull the latest GUI code from git (git pull --ff-only in /opt/veloscroll-gui). The GUI is not a "
            "service — after a successful pull that changes code, it closes & reopens itself automatically. "
            "The dialog shows a transcript of every step plus a rollback command for the previous commit.")
        self.btn_update_gui.clicked.connect(self._open_gui_update_dialog)
        update_col.addWidget(self.btn_update_gui)
        btn_check_updates = QPushButton("Check for updates…")
        btn_check_updates.setToolTip(
            "Re-check now whether the daemon or GUI repo is behind its origin (read-only git ls-remote, "
            "no changes). An available update shows in the amber note below; a clean check flashes a "
            "status-bar confirmation instead.")
        btn_check_updates.clicked.connect(self._on_check_updates)
        update_col.addWidget(btn_check_updates)
        # startup update indicator (update_check.py): amber = actionable news only — hidden when both
        # repos are up to date or the check is unknown (offline / non-git install); no green state
        self.lbl_update_avail = QLabel("")
        self.lbl_update_avail.setStyleSheet("color: #d97706; font-size: small;")
        self.lbl_update_avail.hide()
        update_col.addWidget(self.lbl_update_avail)
        update_col.addStretch(1)
        grid.addWidget(update_wrap, 0, Qt.AlignVCenter)
        root.addWidget(dg)

        # -- unified values table: Key | Effective value | Edit | Description --------
        self.lbl_dataset = QLabel("")    # no stylesheet: palette default text color (theme-safe)
        self.lbl_dataset.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.lbl_dataset)

        # one row object per key — the Scroll/Invert tab takes its share (SCROLL_INVERT_KEYS), the Ramp
        # tab its RAMP_KEYS and the Device Picker tab its DEVICE_KEYS out of this table at construction
        # time; only SECTIONS' keys stay here
        self._capture_dialogs = {}          # key -> open CaptureDialog, one per button key
        for key in vmod.ALL_KEYS:
            row = _FieldRow(key, KEY_KINDS[key])
            row.changed.connect(lambda k=key: self._on_row_changed(k))
            if row.capture_btn is not None:
                row.capture_btn.clicked.connect(
                    lambda _pressed=False, k=key: self._open_capture(k))
            self.rows[key] = row

        # only the non-scroll / non-ramp / non-device sections remain on this page; the Scroll/Invert,
        # Ramp and Device Picker tabs build their own tables with make_section_table (the same layout)
        # for their shares of the rows
        self.table, _bands = self.make_section_table(SECTIONS)
        root.addWidget(self.table, 1)

        # this page's own Apply/Cancel bar; the Ramp and Device Picker tabs register theirs too —
        # all of them act on the same shared edit buffer. It must be added to the layout (a registered
        # bar alone is invisible), directly below the table so it sits where edits happen.
        self.bar = ApplyBar(self.apply, self.cancel)
        self.register_bar(self.bar)
        root.addWidget(self.bar)

        # -- permissions (merged from the old Status tab) ----------------------------
        root.addWidget(PermissionsPanel())

        self._load_from_disk()
        self.watcher.state_changed.connect(self._on_state)

        app = QApplication.instance()       # a quit must never leave the daemon stopped by a capture dialog
        if app is not None:
            app.aboutToQuit.connect(self._on_app_quit)

        self._disk_timer = QTimer(self)     # pick up external config.ini edits while the tab is open
        self._disk_timer.setInterval(2000)
        self._disk_timer.timeout.connect(self._tick_disk)
        self._disk_timer.start()

    def register_bar(self, bar):
        """A tab page's Apply/Cancel bar (apply_bar.py). The buffer is one, so validation state,
        the dirty mark, and busy state are pushed to every registered bar from this tab."""
        self._bars.append(bar)

    def make_section_table(self, sections, description=True):
        """A section table with the same layout as this page's main table: Key | Effective value |
        Edit | Description columns (description=False drops the last column — the Ramp tab shows its
        focused field's hint at the bottom instead), one band row per section (same colors), 34 px rows.
        The edit cells host the shared _FieldRow container/hint widgets and every effective-value cell
        joins self._eff_items so refresh() keeps it live exactly like the main table's.

        Returns (table, {section name: band item}) — callers may rename a band live (the Ramp tab does)."""
        cols = 4 if description else 3
        tbl = QTableWidget(0, cols)
        tbl.setHorizontalHeaderLabels(("Key", "Effective value", "Edit") + (("Description",) if description else ()))
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)   # editing happens through the embedded widgets
        tbl.setSelectionMode(QTableWidget.NoSelection)
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.setColumnWidth(0, 170)
        tbl.setColumnWidth(1, 230)
        tbl.setColumnWidth(2, 320)
        tbl.verticalHeader().setDefaultSectionSize(34)
        bands = {}
        for name, keys in sections:
            r = tbl.rowCount()
            tbl.insertRow(r)
            sec = QTableWidgetItem(name)
            f = sec.font(); f.setBold(True); sec.setFont(f)
            # explicit colors (not Qt.lightGray): a forced light background with the palette's white text
            # is unreadable under dark themes — this pair stays legible in both
            sec.setBackground(QColor("#d1d5db"))
            sec.setForeground(QColor("#1f2937"))
            tbl.setItem(r, 0, sec)
            tbl.setSpan(r, 0, 1, cols)
            bands[name] = sec
            for key in keys:
                r = tbl.rowCount()
                tbl.insertRow(r)
                row = self.rows[key]
                tbl.setItem(r, 0, QTableWidgetItem(key))
                eff = QTableWidgetItem("")
                tbl.setItem(r, 1, eff)
                self._eff_items[key] = eff
                tbl.setCellWidget(r, 2, row.container)
                if description:
                    tbl.setCellWidget(r, 3, row.hint)
        return tbl, bands

    # -- live refresh (merged Status role) ------------------------------------------

    def refresh(self):
        """Re-render the live parts of the page: daemon group + effective-value column.

        The Edit widgets are never touched here — an unapplied edit stays in place while
        the Effective value beside it shows what the daemon actually has."""
        w = self.watcher
        state = w.payload
        s = w.service_state

        color = _SERVICE_COLORS.get(s, "#9ca3af")
        self.lbl_service_dot.setStyleSheet(f"color: {color}; font-size: 16px;")
        self.lbl_service.setText(f"service: {s}")

        if isinstance(state, dict):
            devs = state.get("devices_grabbed") or []
            text = "\n".join(
                f"{d.get('name', '?')} ({d.get('vendor', '?')}/{d.get('product', '?')}) — {d.get('path', '?')}"
                for d in devs) if devs else ("none yet" if s == "active" else "—")
        else:
            text = "no state data"
        self.lbl_devices.setText(text)

        if isinstance(state, dict):
            parts = []
            if state.get("reload_ok", True):
                parts.append("reload OK")
            else:
                parts.append(f"reload rejected: {state.get('reload_error') or 'unknown error'}")
            try:
                cfg_now = os.stat(CONFIG_PATH).st_mtime_ns
                if w.state_mtime_ns is not None and s == "active" and cfg_now > w.state_mtime_ns:
                    parts.append("pending reload (~2 s)")
            except OSError:
                pass
            self.lbl_reload.setText(" · ".join(parts))
        else:
            self.lbl_reload.setText(
                "no state data (set STATE_FILE = True to enable)" if s == "active" else "service not active")

        rows, dataset, errors = vmod.build_rows(state)
        by_key = {r["key"]: r for r in rows if "key" in r}
        for key, item in self._eff_items.items():
            r = by_key.get(key)
            if r is None:
                continue
            item.setText(r["value_text"])
            item.setForeground(self._fg_red if r["source_text"] == vmod.DS_UNAVAILABLE else self._fg_norm)
        label = f"effective values — {dataset}"
        if errors:
            label += "  ·  " + "; ".join(errors)
        self.lbl_dataset.setText(label)

        self._refresh_tick_hint(state)

    def _refresh_tick_hint(self, state):
        """TICK row's description: live Hz figure + period from the actually active tickrate (the same
        resolution as the Effective column — unapplied edits don't change it); static TITLES text when
        unavailable or malformed. Written only on change (no churn every 2 s refresh)."""
        values, _, _ = vmod.resolve(state)   # state-file snapshot first, else in-process load_config()
        tick = values.get("TICK") if isinstance(values, dict) else None
        text = TITLES["TICK"][1]             # static example — the initial and degraded-state wording
        if not isinstance(tick, bool) and isinstance(tick, (int, float)):
            try:
                t = float(tick)
            except (OverflowError, ValueError):
                t = None
            if t is not None and math.isfinite(t) and t > 0:
                text = f"ticker period in seconds (~{round(1 / t)} Hz at {vmod.format_value('TICK', tick)})"
        row = self.rows["TICK"]
        if row.hint.styleSheet():      # a validation error is showing — let it keep the label until revalidated
            return
        if row.hint.text() != text:    # no churn every 2 s refresh
            row.hint.setText(text)

    # -- loading / refresh ------------------------------------------------------

    def _read_config_file(self):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None

    def _load_from_disk(self):
        text = self._read_config_file()
        if text is None:
            self.disk_text = ""
            self.model.load_text("")
        else:
            self.disk_text = text
            self.model.load_text(text)
        try:
            st = os.stat(CONFIG_PATH)
            self._disk_sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            self._disk_sig = None
        self.reset_rows()
        if text is None:
            self._bar_message(f"cannot read {CONFIG_PATH} — nothing to edit until it exists")

    def reset_rows(self):
        """Re-sync every row's editor from the buffer (initial load / after Apply / Cancel / disk reload).

        Public because the Ramp tab calls it once more after it has taken its rows' widgets —
        those were created before their editors got a parent, so they need this final sync."""
        origin = self.model.origin()
        for key, row in self.rows.items():
            row.set_display(self.model.values.get(key), raw=origin.get(key, ""))
        self._refresh_validation()

    def _tick_disk(self):
        if self._busy:
            return
        try:
            st = os.stat(CONFIG_PATH)
        except OSError:
            return
        sig = (st.st_mtime_ns, st.st_size)
        if sig == self._disk_sig:
            return
        text = self._read_config_file()
        if text is None or text == self.disk_text:
            self._disk_sig = sig
            return
        if self.model.is_dirty_against(self.disk_text):
            # the user has unapplied edits — keep them, but make the external change visible on every tab
            self._bar_message(
                f"config.ini changed on disk while you were editing (kept your edits; "
                f"Cancel now reverts to YOUR edits' starting point, not the new file)")
            return
        self.disk_text = text
        self.pre_apply_text = None          # an external write invalidates our revert target
        self.model.load_text(text)
        self._disk_sig = sig
        self.reset_rows()

    def _on_row_changed(self, key):
        raw = self.rows[key].get_raw()
        if raw == "" and key in NULLABLE and self.model.origin().get(key, "") != "":
            raw = "none"                    # clearing an existing value must disable it explicitly
        self.model.set_raw(key, raw)
        self._refresh_validation()

    def set_key_raw(self, key, raw):
        """External writer (device picker / button capture): apply a raw value to the
        edit buffer and sync this page's row editor + validation, as if the user had typed it."""
        self.model.set_raw(key, raw)
        row = self.rows.get(key)
        if row is not None:
            # refresh the row widget from the new state without re-reading its own (stale) text;
            # _refresh_validation below re-renders every hint/error anyway
            row.set_display(self.model.values.get(key), raw=raw)
        self._refresh_validation()

    # -- button capture ------------------------------------------------------------------

    def _open_capture(self, key):
        """[Capture…] on a button row: one live dialog per key at a time (non-modal — Apply stays reachable)."""
        dlg = self._capture_dialogs.get(key)
        if dlg is not None and dlg.isVisible():
            dlg.raise_()
            dlg.activateWindow()
            return
        dlg = CaptureDialog(key, self, self.watcher, parent=self.window())
        dlg.finished.connect(lambda _result, k=key: self._close_capture(k))
        self._capture_dialogs[key] = dlg
        dlg.show()

    def _close_capture(self, key):
        dlg = self._capture_dialogs.pop(key, None)
        if dlg is not None:
            dlg.deleteLater()

    # -- one-click updates (daemon + GUI) -------------------------------------------

    def _open_update_dialog(self):
        """[Update daemon…] on the Daemon group: modal dialog with confirm + transcript (update_dialog.py)."""
        UpdateDialog(self.window(), target="daemon").exec()
        self.check_updates()          # a pulled update clears/refreshes the indicator

    def _open_gui_update_dialog(self):
        """[Update GUI…] directly below [Update daemon…]: same dialog for the /opt/veloscroll-gui checkout."""
        UpdateDialog(self.window(), target="gui").exec()
        self.check_updates()          # a pulled update clears/refreshes the indicator

    def _on_check_updates(self):
        """[Check for updates…] on the Daemon group: re-run the read-only local-vs-origin comparison now.

        The indicator row doubles as progress ("checking for updates…"); a check that finds nothing
        still gets a status-bar confirmation — but only when the user asked for it (the startup and
        post-update-dialog checks stay silent, keeping the indicator actionable-news-only)."""
        self._check_manual = True
        self.lbl_update_avail.setText("checking for updates…")
        self.lbl_update_avail.show()
        self.check_updates()                    # a no-op start when one is already running (its result still counts)

    def check_updates(self):
        """Compare local HEAD against origin's for both repos (read-only, off-thread); the result
        drives lbl_update_avail. Started once from MainWindow.start(), re-run after any UpdateDialog
        finishes and on demand via [Check for updates…] — no periodic re-check."""
        if getattr(self, "_update_job", None) is not None and self._update_job.isRunning():
            return                                  # one check at a time (a stale result would race it)
        job = UpdateCheckJob(self)
        job.results.connect(self._on_update_check)
        self._update_job = job
        job.start()

    def _on_update_check(self, results):
        daemon_avail, gui_avail = results
        manual = self._check_manual               # set by [Check for updates…]; the other callers stay silent
        self._check_manual = False
        # amber tint per target (None/unknown stays unstyled — actionable news only, like the label)
        self.btn_update.setStyleSheet(_UPDATE_BTN_AMBER if daemon_avail else "")
        self.btn_update_gui.setStyleSheet(_UPDATE_BTN_AMBER if gui_avail else "")
        if daemon_avail and gui_avail:
            text = "Update to DAEMON and GUI available"
        elif daemon_avail:
            text = "Update to DAEMON available"
        elif gui_avail:
            text = "Update to GUI available"
        else:                                       # neither has an update, or the check was unknown (offline / non-git)
            self.lbl_update_avail.hide()
            if manual:                              # a user-initiated check still gets a completion note
                if daemon_avail is False or gui_avail is False:
                    self._banner("checked — no updates available", "info")
                else:
                    self._banner("update check inconclusive (offline or not a git checkout?)", "info")
            return
        self.lbl_update_avail.setText(text)
        self.lbl_update_avail.show()

    def _on_app_quit(self):
        """Quit must never leave the daemon down because a capture dialog stopped it."""
        need_restart = False
        for dlg in list(self._capture_dialogs.values()):
            try:
                if getattr(dlg, "_we_stopped", False) and dlg._phase not in ("restarting", "done"):
                    need_restart = True
                dlg.shutdown()
            except Exception:
                pass
        if need_restart:
            try:
                run_priv(["service", "start"], timeout=10)
            except PrivError:
                pass                     # the service was already stopped before we got here — nothing to do
        self._wait_idle()                # let an in-flight Apply (the startup lifecycle write) settle first
        self._lifecycle_off_sync()       # STATE_FILE + DISTANCE_FILE have no selector rows: off while no GUI is open

    # -- validation / dirty state -------------------------------------------------

    def _bar_message(self, text):
        """Ad-hoc (non-validation) message for every tab's bar error label."""
        for bar in self._bars:
            bar.lbl_error.setText(text)

    def _refresh_validation(self):
        values, field_errors, form_errors = self.model.validate()
        for key, row in self.rows.items():
            dyn = None                       # unset nullable keys show their effective fallback
            if key in NULLABLE and self.model.values.get(key) is None:
                label, source_key = FALLBACK[key]
                ref = self.model.values.get(source_key) if source_key else None
                dyn = f"(unset — {label}" + (f": {ref})" if ref is not None else ")")
            row.set_state(field_errors.get(key, ""), dyn)
        if "*" in field_errors:              # file-level syntax error from the initial load
            text = field_errors["*"]
        elif form_errors:
            text = " · ".join(form_errors)
        else:
            text = ""
        dirty = self.model.is_dirty_against(self.disk_text)
        if values is None or "*" in field_errors:
            dirty = False                    # unparseable file / unavailable daemon: nothing meaningful to apply
        for bar in self._bars:               # every tab sees the same state (the buffer is one)
            bar.set_state(text, dirty)
        self.dirty_changed.emit(dirty)

    # -- Apply --------------------------------------------------------------------

    def apply(self):
        if self._busy:
            return
        values, field_errors, form_errors = self.model.validate()
        self._refresh_validation()                    # paint the inline error state before blocking on it
        n = len([k for k in field_errors if k != "*"]) + len(form_errors)
        if ("*" in field_errors) or n:
            first = (field_errors.get("*") or next(iter((v for v in form_errors), "")))
            self._banner(f"cannot apply — {n} validation error(s): {first}", "warning", sticky=True)
            return
        text = self.model.render_text()
        if text == self.disk_text:
            self._banner("no changes to apply", "success")
            return
        # the config text goes over stdin — priv.py never opens a caller-chosen path as root
        self._set_busy(True, "Applying…")
        self._pending = (text, values)
        job = PrivJob(["apply-config"], self, input_text=text)
        job.finished_ok.connect(self._on_apply_done)      # Signal(str): priv.py stdout
        job.failed.connect(self._on_priv_fail)            # Signal(str, str): (kind, detail)
        job.start()

    def _take_pending(self):
        """Pop the in-flight job's data; None when nothing is pending (each job signals exactly once)."""
        if getattr(self, "_pending", None) is None:
            return None
        pending = self._pending
        self._pending = None
        self._set_busy(False)
        return pending

    def _on_apply_done(self, out):
        pending = self._take_pending()
        if pending is None:
            return
        text, values = pending
        self.pre_apply_text = self.disk_text      # Cancel can revert to this after the apply
        self.disk_text = text
        try:
            st = os.stat(CONFIG_PATH)
            self._disk_sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            pass
        self.model.load_text(text)               # editor's starting point follows what is now on disk
        self.reset_rows()
        if self.watcher.service_state != "active":
            self._banner("config applied — daemon is not running; it picks this up at its next start", "success")
        else:
            self._start_confirm(values)

    def _on_priv_fail(self, kind, detail):
        pending = self._take_pending()
        if pending is None:
            return
        if kind == PrivError.KIND_SERVICE_MISSING:
            popup_error(self.window(), "Privileged write refused — helper service missing",
                        f"The root helper service ({BUS_NAME}) is not running on the system bus, so the "
                        f"privileged operation could not be issued.\n\n"
                        f"Install it once (needs your password), then use [Re-check] in Status → Permissions:\n\n{SERVICE_FIX}")
        elif kind == PrivError.KIND_NOT_AUTHORIZED:
            popup_error(self.window(), "Privileged write refused — authorization", detail or str(kind))
        else:
            popup_error(self.window(), "Privileged operation failed", detail or str(kind))

    def _start_confirm(self, values):
        self._confirm = {"values": {k: _norm(v) for k, v in values.items()}}
        # transient progress note — bottom status bar (info), not the top banner strip; the final
        # success/failure flash replaces it once the reload is confirmed or rejected
        self._banner("config written — waiting for the daemon to reload (~2 s)", "info")
        self._confirm_timer.start(self.CONFIRM_TIMEOUT_MS)

    def _on_state(self, payload):
        if not isinstance(payload, dict) or self._confirm is None:
            return
        vals = payload.get("values")
        if not (isinstance(vals, dict) and vals):
            return
        if payload.get("reload_ok") is False:
            self._finish_confirm(False, f"the daemon rejected the new config: {payload.get('reload_error') or 'unknown error'}")
            return
        got = {k: _norm(vals.get(k)) for k in vals}
        if all(got.get(k) == v for k, v in self._confirm["values"].items()):
            self._finish_confirm(True, "the daemon reloaded the new config (state file)")

    def _confirm_timeout(self):
        if self._confirm is None:
            return
        # no state-file confirmation arrived — fall back to the journal
        job = PrivJob(["logs", 60], self)
        job.finished_ok.connect(self._analyze_journal)
        job.failed.connect(lambda _k, d: self._finish_confirm(None, f"could not confirm reload from the journal ({d})"))
        job.start()

    def _analyze_journal(self, out):
        last_ok = last_rej = -1
        rej_line = ""
        for i, line in enumerate((out or "").splitlines()):
            if "config reloaded:" in line:
                last_ok = i
            elif "reload rejected" in line:
                last_rej = i
                rej_line = line.split("veloscroll[", 1)[-1].strip()
        if last_rej > last_ok and last_rej >= 0:
            self._finish_confirm(False, f"the daemon rejected the new config (journal): {rej_line}")
        elif last_ok >= 0:
            self._finish_confirm(True, "the daemon reloaded the new config (journal)")
        else:
            # LOG_LEVEL < 2 hides both lines — an inconclusive but applied write
            self._confirm = None
            self._banner("config applied — could not confirm reload from the journal "
                         "(LOG_LEVEL below 2 hides those lines); check the Status tab", "warning")

    def _finish_confirm(self, ok, detail):
        self._confirm = None
        self._confirm_timer.stop()
        if ok is True:
            self._banner(f"applied — {detail}", "success")
        elif ok is False:
            self._banner(detail, "error", sticky=True)

    # -- Cancel ---------------------------------------------------------------------

    def cancel(self):
        if self._busy:
            return
        dirty = self.model.is_dirty_against(self.disk_text)
        if dirty:
            self.model.load_text(self.disk_text)      # discard unapplied edits (memory only — disk already matches)
            self.reset_rows()
            self._banner("changes discarded", "success")
            return
        if self.pre_apply_text is not None and self.pre_apply_text != self.disk_text:
            # a change was applied earlier in this session — write the previous text back so the daemon reloads it
            prev = self.pre_apply_text
            # the previous text goes over stdin — priv.py never opens a caller-chosen path as root
            self._set_busy(True, "Reverting…")
            self._pending = (prev, None)

            def _on_revert_done(_out):
                pending = self._take_pending()
                if pending is None:
                    return
                text = pending[0]
                self.disk_text = text
                self.pre_apply_text = None        # the revert is done; nothing left to roll back to
                self.model.load_text(text)
                try:
                    st = os.stat(CONFIG_PATH)
                    self._disk_sig = (st.st_mtime_ns, st.st_size)
                except OSError:
                    pass
                self.reset_rows()
                if self.watcher.service_state == "active":
                    self._start_confirm(self.model.validate()[0] or {})   # daemon should reload the old values back
                else:
                    self._banner("reverted — daemon is not running; previous config takes effect at its next start", "success")

            job = PrivJob(["apply-config"], self, input_text=prev)
            job.finished_ok.connect(_on_revert_done)   # Signal(str): priv.py stdout
            job.failed.connect(self._on_priv_fail)     # Signal(str, str): (kind, detail) — shared with Apply
            job.start()
        else:
            self._banner("nothing to revert — the config matches what is on disk", "success")

    # -- lifecycle keys (no selector rows: the GUI owns STATE_FILE and DISTANCE_FILE) ---

    def _auto_set(self, updates):
        """Force {key: bool} through the normal Apply path — one combined write for every key that
        differs from disk; a no-op when nothing does."""
        if self._busy:
            return
        changed = False
        for key, want in updates.items():
            raw = "True" if want else "False"
            if self.model.raws.get(key, "") != raw:
                self.model.set_raw(key, raw)
                changed = True
        if changed:
            self.apply()

    def auto_set_startup_keys(self, distance_on=True):
        """Startup bookkeeping: STATE_FILE is always forced on (the dashboard needs the state file);
        DISTANCE_FILE is forced on only when `distance_on` — the Ramp tab's opt-out checkbox. An
        opted-out key is left exactly as configured, never force-off at startup."""
        updates = {"STATE_FILE": True}
        if distance_on:
            updates["DISTANCE_FILE"] = True
        self._auto_set(updates)

    def auto_set_state_file(self, on):
        """Force STATE_FILE to `on` through the normal Apply path when it differs from disk."""
        self._auto_set({"STATE_FILE": on})

    def enable_state_file(self):
        """Banner action: turn STATE_FILE on through the normal Apply path."""
        self.auto_set_state_file(True)

    def _lifecycle_off_sync(self):
        """Exit bookkeeping: force every lifecycle key that is currently True back to False with a
        direct privileged write of just those lines — aboutToQuit has no event loop left to finish a
        QThread job. Best effort only; unapplied user edits are deliberately NOT written (the render
        is over disk_text, not the buffer)."""
        updates = {key: "False" for key in ("STATE_FILE", "DISTANCE_FILE")
                   if self.model.raws.get(key, "") == "True"}
        if not updates or not (self.disk_text or "").strip():
            return                                   # nothing on to turn off / file was never readable
        try:
            run_priv(["apply-config"], timeout=10, input_text=_render(self.disk_text, updates))
        except PrivError:
            pass                                     # best effort at exit — the daemon keeps its current state

    def _wait_idle(self, limit_ms=5000):
        """Bounded wait for an in-flight Apply/Cancel (e.g. the startup STATE_FILE write) so the exit
        bookkeeping sees a settled disk_text; aboutToQuit may block briefly — it is app shutdown."""
        waited = 0
        while self._busy and waited < limit_ms:
            QThread.msleep(100)
            waited += 100

    # -- busy / banner helpers ---------------------------------------------------------

    def _set_busy(self, busy, label=""):
        self._busy = busy
        for bar in self._bars:               # a running op blocks Apply/Cancel on every tab at once
            bar.set_busy(busy, label)

    def _banner(self, text, kind="warning", sticky=False):
        # routed through the main window's banner (wired in MainWindow)
        win = self.window()
        if hasattr(win, "config_banner"):
            win.config_banner(text, kind=kind, sticky=sticky)


def _norm(v):
    """State-file JSON stores DEVICE_MATCH as [vendor, product]; compare it against the tuple form."""
    if isinstance(v, list) and len(v) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in v):
        return tuple(v)
    return v
