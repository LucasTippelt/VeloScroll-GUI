"""Presets tab: save the current config under a name; load key groups from saved presets.

All writes go through the helper service's `Preset(ops)` call — a strict allowlist passthrough to
veloscroll_preset.py (same grammar, re-validated there). The preset list is parsed in-process (the files are
world-readable); only save/load need root. A load writes config.ini directly; the daemon hot-reloads it within ~2 s and
ConfigTab's disk watcher resyncs the edit buffer automatically — no extra wiring here.

Selecting a row shows its key→value preview against the current effective values on the right — changed
keys highlighted amber (the same typed comparison as the Backups tab); it re-diffs when the state file
changes. The load panel below the preset list shows one row per key group (feel / distances / buttons-keys /
device / scroll) plus an "all" shortcut; each checked row contributes `COMMAND NAME` to a single CLI
invocation, applied in row order. The exact command is shown for confirmation before anything runs, and the
transcript pane keeps the output of every operation. [Delete selected…] removes the preset picked in the list
after a confirm/cancel popup (its .bak-* backups are kept — `restore` can put it back).
"""
import configparser
import glob
import os
import re
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                                QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QTableWidget,
                                QTableWidgetItem, QVBoxLayout, QWidget)

from . import CONFIG_PATH
from .proc import PrivError, PrivJob
from .values import _daemon_module, canon_value, format_value, resolve

# Must stay in sync with veloscroll_preset.NAME_RE (the helper re-validates every name as defense-in-depth).
NAME_RE = re.compile(r"[A-Za-z0-9._-]+")

PRESET_DIR = os.path.join(os.path.dirname(CONFIG_PATH), "presets")   # /etc/veloscroll/presets

# display label -> CLI command; row order == application order (the CLI applies pairs in the given order)
GROUPS = (
    ("feel", "loadfeel"),
    ("distances", "loaddistances"),
    ("buttons-keys", "loadkeys"),
    ("device", "loaddevice"),
    ("scroll", "loadscroll"),
)


def list_presets():
    """[(name, mtime, key_count)] for every /etc/veloscroll/presets/*.ini, name-sorted.

    Parsed in-process (world-readable); a malformed file is skipped rather than failing the whole list."""
    out = []
    for path in glob.glob(os.path.join(PRESET_DIR, "*.ini")):
        base = os.path.basename(path)[:-4]
        if not NAME_RE.fullmatch(base):
            continue                                  # e.g. a stray .bak- file is not a preset
        try:
            cp = configparser.ConfigParser(interpolation=None)
            cp.optionxform = str
            with open(path, "r", encoding="utf-8") as f:
                cp.read_string(f.read())
            keys = sum(1 for k in (cp["veloscroll"].items() if cp.has_section("veloscroll") else [])
                       if k[1].strip())
        except (OSError, configparser.Error):
            continue
        out.append((base, os.path.getmtime(path), keys))
    return sorted(out)


def _preset_keys(name):
    """[veloscroll] raw key→value of one preset; None when unreadable/malformed."""
    path = os.path.join(PRESET_DIR, name + ".ini")
    try:
        cp = configparser.ConfigParser(interpolation=None)
        cp.optionxform = str
        with open(path, "r", encoding="utf-8") as f:
            cp.read_string(f.read())
        if not cp.has_section("veloscroll"):
            return {}
        return {k: v for k, v in cp["veloscroll"].items() if v.strip()}
    except (OSError, configparser.Error):
        return None


def _typed(mod, key, raw):
    """Parse a preset's raw value with the daemon's own parser for a typed comparison; (value|None, ok)."""
    if mod is None:
        return None, False
    try:
        return mod._parse_ini_value(key, raw.strip()), True
    except Exception:
        return None, False


CHANGED_BG = QColor("#fef3c7")     # amber tint: value differs from the current effective one — same pair as backups_tab's CHANGED_BG/CHANGED_FG
CHANGED_FG = QColor("#1f2937")     # explicit dark text on that light tint (legible under light AND dark themes)


class PresetsTab(QWidget):
    def __init__(self, watcher, parent=None):
        super().__init__()
        self.watcher = watcher
        self._job = None

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        header = QLabel("Named presets of the daemon keys — save snapshots the current config; "
                        "loads apply one key group each (the daemon reloads within ~2 s); "
                        "delete removes the selected preset.")
        # no stylesheet: palette default text color (theme-safe black on light, white on dark)
        root.addWidget(header)

        body = QHBoxLayout()

        # -- left: preset list + load panel -------------------------------------------
        left = QVBoxLayout()
        self.tbl_presets = QTableWidget(0, 3)
        self.tbl_presets.setHorizontalHeaderLabels(("preset", "modified", "keys"))
        # preset / modified / keys share the table width equally (and keep doing so on resize)
        self.tbl_presets.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_presets.verticalHeader().setVisible(False)
        self.tbl_presets.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl_presets.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_presets.itemSelectionChanged.connect(self._on_preset_selected)
        left.addWidget(self.tbl_presets, 1)

        load_box = QGroupBox("load key groups (checked rows run in one command, top to bottom)")
        grid_lay = QVBoxLayout(load_box)
        self._rows = []
        for label, cmd in GROUPS:
            row = QHBoxLayout()
            cb = QCheckBox(label + ":")
            combo = QComboBox()
            combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
            row.addWidget(cb)
            row.addWidget(combo, 1)
            grid_lay.addLayout(row)
            self._rows.append((cb, combo))
        all_row = QHBoxLayout()
        cb_all = QCheckBox("all (loadall — every key except device & buttons):")
        combo_all = QComboBox()
        combo_all.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        all_row.addWidget(cb_all)
        all_row.addWidget(combo_all, 1)
        grid_lay.addLayout(all_row)
        self._rows.append((cb_all, combo_all))

        btn_load = QPushButton("Load selected…")
        btn_load.setToolTip("Shows the exact command for confirmation, then runs it as root")
        btn_load.clicked.connect(self._on_load)
        grid_lay.addWidget(btn_load)
        left.addWidget(load_box)

        save_row = QHBoxLayout()
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("preset name (letters, digits, '.', '_', '-')")
        btn_save = QPushButton("Save current as…")
        btn_save.setToolTip("Snapshot every daemon key except MAX_BACKUPS/STATE_FILE/DISTANCE_FILE into presets/<name>.ini")
        btn_save.clicked.connect(self._on_save)
        save_row.addWidget(btn_save)
        save_row.addWidget(self.ed_name, 1)
        left.addLayout(save_row)

        btn_row = QHBoxLayout()
        self.btn_delete = QPushButton("Delete selected…")
        self.btn_delete.setToolTip(
            "Permanently remove the preset selected in the list above (asks for confirmation first; "
            "its .bak-* backups are kept and can be restored from the Backups tab)")
        # explicit red (the codebase's error color) with hover/pressed/disabled states — legible under both themes
        self.btn_delete.setStyleSheet(
            "QPushButton { background: #dc2626; color: white; border-radius: 3px; padding: 3px 10px; }"
            "QPushButton:hover { background: #b91c1c; }"
            "QPushButton:pressed { background: #991b1b; }"
            "QPushButton:disabled { background: #fca5a5; color: #7f1d1d; }")
        self.btn_delete.clicked.connect(self._on_delete)
        btn_list = QPushButton("Refresh list")
        btn_list.setToolTip("Re-scan /etc/veloscroll/presets (after a manual edit or another GUI)")
        btn_list.clicked.connect(self.refresh)
        btn_row.addWidget(self.btn_delete)
        btn_row.addWidget(btn_list)
        btn_row.addStretch(1)
        left.addLayout(btn_row)
        body.addLayout(left, 5)

        # -- right: preview + transcript ----------------------------------------
        right = QVBoxLayout()
        lbl_detail = QLabel("preview — amber rows differ from the current effective value")
        lbl_detail.setStyleSheet("font-size: small;")    # no forced color (theme-safe)
        right.addWidget(lbl_detail)
        self.tbl_diff = QTableWidget(0, 3)
        self.tbl_diff.setHorizontalHeaderLabels(("key", "in preset", "current"))
        # key / in preset / current share the table width equally (and keep doing so on resize)
        self.tbl_diff.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_diff.verticalHeader().setVisible(False)
        self.tbl_diff.setEditTriggers(QTableWidget.NoEditTriggers)
        right.addWidget(self.tbl_diff, 3)

        self.lbl_status = QLabel("select a preset to preview it against the current values")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("font-size: small;")    # no forced color (theme-safe)
        self.lbl_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right.addWidget(self.lbl_status)

        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setPlaceholderText("operation transcript (newest at the bottom)")
        right.addWidget(self.txt_log, 2)
        body.addLayout(right, 4)
        root.addLayout(body, 1)

        # re-diff against the new current values when the state file changes (e.g. after a daemon reload)
        self.watcher.state_changed.connect(self._on_state)

    # -- list -----------------------------------------------------------------------

    def refresh(self):
        """(Re)scan the preset dir and refill the table + every name combo."""
        entries = list_presets()
        presets = [p[0] for p in entries]
        self.tbl_presets.setRowCount(len(entries))
        for i, (name, mtime, keys) in enumerate(entries):
            self.tbl_presets.setItem(i, 0, QTableWidgetItem(name))
            item_t = QTableWidgetItem(time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)))
            item_t.setData(Qt.UserRole, name)          # keep the row order stable for selection restore
            self.tbl_presets.setItem(i, 1, item_t)
            self.tbl_presets.setItem(i, 2, QTableWidgetItem(str(keys)))
        sel = self._selected_name()
        for _cb, combo in self._rows:
            cur = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(presets)
            if cur in presets:
                combo.setCurrentText(cur)
            combo.blockSignals(False)
        if sel and sel in presets:                      # restore the selection across a refresh
            for i in range(self.tbl_presets.rowCount()):
                if self.tbl_presets.item(i, 0).text() == sel:
                    self.tbl_presets.selectRow(i)
                    break
            self._build_preview()                       # selectRow alone does not re-fire when the selection is unchanged
        else:                                           # pruned away between scans — nothing left to preview
            self._clear_preview()

    def _selected_name(self):
        rows = self.tbl_presets.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.tbl_presets.item(rows[0].row(), 0)
        return item.text() if item else None

    # -- preview / diff ---------------------------------------------------------------

    def _on_state(self, payload):
        if self._selected_name() is not None:
            self._build_preview()

    def _clear_preview(self, status="select a preset to preview it against the current values"):
        self.tbl_diff.setRowCount(0)
        self.lbl_status.setText(status)

    def _build_preview(self):
        """Key→value table of the selected preset against the current effective values; amber rows differ.

        The same typed comparison as the Backups tab (the daemon's own parser on both sides, canon_value
        for pairs — a DEVICE_MATCH pair parses to (v, p) but the state file carries [v, p])."""
        name = self._selected_name()
        keys = _preset_keys(name) if name else None
        if keys is None:
            self._clear_preview("select a preset to preview it against the current values"
                                if not name else "selected preset is unreadable or has no [veloscroll] section")
            return
        values, _ds, _errs = resolve(getattr(self.watcher, "payload", None))
        mod = _daemon_module()
        self.tbl_diff.setRowCount(len(keys))
        n_changed = 0
        for i, (key, raw) in enumerate(sorted(keys.items())):
            typed, ok = _typed(mod, key, raw) if values is not None else (None, False)
            cur_text = format_value(key, values.get(key)) if values is not None else "—"
            changed = ok and values is not None and canon_value(typed) != canon_value(values.get(key))
            self.tbl_diff.setItem(i, 0, QTableWidgetItem(key))
            item_p = QTableWidgetItem(raw.strip())
            if not ok:
                item_p.setToolTip(f"could not parse {raw!r} as a {key} value — compared as text")
            self.tbl_diff.setItem(i, 1, item_p)
            self.tbl_diff.setItem(i, 2, QTableWidgetItem(cur_text))
            if changed:
                n_changed += 1
                for c in range(3):
                    item = self.tbl_diff.item(i, c)
                    item.setBackground(QBrush(CHANGED_BG))
                    item.setForeground(QBrush(CHANGED_FG))   # dark-on-amber stays legible under light AND dark themes
        self.lbl_status.setText(f"{len(keys)} key(s) in this preset — {n_changed} differ from the current "
                                f"effective value (amber); loading changes exactly those")

    def _on_preset_selected(self):
        if self._selected_name() is None:
            self._clear_preview()
            return
        self._build_preview()

    # -- save -----------------------------------------------------------------------

    def _log(self, line):
        stamp = time.strftime("%H:%M:%S")
        self.txt_log.appendPlainText(f"[{stamp}] {line}")

    def _on_save(self):
        name = self.ed_name.text().strip()
        if not NAME_RE.fullmatch(name or ""):
            QMessageBox.warning(self, "Save preset",
                                f"invalid preset name {name!r} — use only letters, digits, '.', '_' or '-'")
            return
        if self._job is not None and self._job.isRunning():
            return
        self._log(f"helper: preset save {name}")
        job = PrivJob(["preset", "save", name], self)
        job.finished_ok.connect(lambda out: self._finish(job, f"save {name}", out))
        job.failed.connect(self._fail)
        self._job = job
        job.start()

    def _on_delete(self):
        name = self._selected_name()
        if not NAME_RE.fullmatch(name or ""):
            QMessageBox.warning(self, "Delete preset", "select a preset in the list first")
            return
        if self._job is not None and self._job.isRunning():
            return                                       # one operation at a time (the transcript would interleave)
        box = QMessageBox(self)
        box.setWindowTitle("Delete preset")
        box.setText(f"Delete preset '{name}'?")
        box.setInformativeText(
            f"This permanently removes {PRESET_DIR}/{name}.ini. Its .bak-* backups (if any) are kept "
            "and can be put back from the Backups tab.")
        box.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)         # destructive: Cancel is the safe default
        if box.exec() != QMessageBox.Ok:
            return
        self._log(f"helper: preset delete {name}")
        job = PrivJob(["preset", "delete", name], self)
        job.finished_ok.connect(lambda out: self._finish(job, f"delete {name}", out))
        job.failed.connect(self._fail)
        self._job = job
        job.start()

    # -- load -----------------------------------------------------------------------

    def _build_ops(self):
        """[(cmd, name)] for every checked row that has a preset chosen; None when nothing is selected."""
        ops = []
        for (cb, combo), (_label, cmd) in zip(self._rows[:-1], GROUPS):
            if cb.isChecked():
                name = combo.currentText()
                if not NAME_RE.fullmatch(name or ""):
                    return None                         # no preset exists / none chosen — refuse rather than guess
                ops.append((cmd, name))
        cb_all, combo_all = self._rows[-1]
        if cb_all.isChecked():
            name = combo_all.currentText()
            if not NAME_RE.fullmatch(name or ""):
                return None
            ops.append(("loadall", name))
        return ops or None

    def _on_load(self):
        if self._job is not None and self._job.isRunning():
            return                                       # one operation at a time (the transcript would interleave)
        ops = self._build_ops()
        if ops is None:
            QMessageBox.warning(self, "Load presets",
                                "tick at least one group and choose a preset for each ticked row")
            return
        tokens = []
        for cmd, name in ops:
            tokens += [cmd, name]
        cmdline = "preset " + " ".join(tokens)
        box = QMessageBox(self)
        box.setWindowTitle("Load presets")
        box.setText(f"Run this as root (applied top to bottom; the daemon reloads within ~2 s)?\n\n{cmdline}")
        box.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        if box.exec() != QMessageBox.Ok:
            return
        self._log(f"$ {cmdline}")
        job = PrivJob(["preset", *tokens], self)
        job.finished_ok.connect(lambda out: self._finish(job, "load", out))
        job.failed.connect(self._fail)
        self._job = job
        job.start()

    # -- completion -------------------------------------------------------------------

    def _finish(self, job, what, out):
        if job is not self._job:
            return                                       # a newer operation superseded this one
        self._job = None
        for line in (out or "").splitlines():
            self._log("  " + line)
        self.refresh()                                  # new .ini / pruned backups may have appeared

    def _fail(self, kind, detail):
        job = self.sender()
        if job is not self._job:
            return                                       # a newer operation superseded this one
        self._job = None
        hint = " — install and start the helper service (see the Permissions panel)" \
            if kind == PrivError.KIND_SERVICE_MISSING else ""
        self._log("FAILED: " + detail + hint)
