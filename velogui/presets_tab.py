"""Presets tab (chunk 5): save the current config under a name; load key groups from saved presets.

All writes go through `priv.py preset ...` — the strict allowlist passthrough to veloscroll_preset.py
(same grammar, re-validated there). The preset list is parsed in-process (the files are world-readable);
only save/load need root. A load writes config.ini directly; the daemon hot-reloads it within ~2 s and
ConfigTab's disk watcher resyncs the edit buffer automatically — no extra wiring here.

The load panel shows one row per key group (feel / distances / buttons-keys / device / scroll) plus an
"all" shortcut; each checked row contributes `COMMAND NAME` to a single CLI invocation, applied in row
order. The exact command is shown for confirmation before anything runs, and the transcript pane keeps
the output of every operation. [Delete selected…] removes the preset picked in the list after a
confirm/cancel popup (its .bak-* backups are kept — `restore` can put it back).
"""
import configparser
import glob
import os
import re
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                                QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QTableWidget,
                                QTableWidgetItem, QVBoxLayout, QWidget)

from . import CONFIG_PATH
from .proc import PrivError, PrivJob

# Must stay in sync with veloscroll_preset.NAME_RE (priv.py re-validates every name as defense-in-depth).
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


def preset_text(name):
    """Raw INI text of one preset for the detail pane; None when unreadable."""
    path = os.path.join(PRESET_DIR, name + ".ini")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


class PresetsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._job = None

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        header = QLabel("Named presets of the daemon keys — save snapshots the current config; "
                        "loads apply one key group each (the daemon reloads within ~2 s); "
                        "delete removes the selected preset.")
        # no stylesheet: palette default text color (theme-safe black on light, white on dark)
        root.addWidget(header)

        body = QHBoxLayout()

        # -- left: preset list + detail -------------------------------------------
        left = QVBoxLayout()
        self.tbl_presets = QTableWidget(0, 3)
        self.tbl_presets.setHorizontalHeaderLabels(("preset", "modified", "keys"))
        # preset / modified / keys share the table width equally (and keep doing so on resize)
        self.tbl_presets.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_presets.verticalHeader().setVisible(False)
        self.tbl_presets.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl_presets.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_presets.itemSelectionChanged.connect(self._on_preset_selected)
        left.addWidget(self.tbl_presets, 3)

        self.txt_detail = QPlainTextEdit()
        self.txt_detail.setReadOnly(True)
        self.txt_detail.setPlaceholderText("select a preset to see its file")
        left.addWidget(self.txt_detail, 2)

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

        # -- right: load panel + transcript ----------------------------------------
        right = QVBoxLayout()
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
        right.addWidget(load_box)

        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setPlaceholderText("operation transcript (newest at the bottom)")
        right.addWidget(self.txt_log, 1)
        body.addLayout(right, 4)
        root.addLayout(body, 1)

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

    def _selected_name(self):
        rows = self.tbl_presets.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.tbl_presets.item(rows[0].row(), 0)
        return item.text() if item else None

    def _on_preset_selected(self):
        name = self._selected_name()
        text = preset_text(name) if name else None
        self.txt_detail.setPlainText(text or "(unreadable)")

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
        self._log(f"$ priv.py preset save {name}")
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
        self._log(f"$ priv.py preset delete {name}")
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
        cmdline = "priv.py preset " + " ".join(tokens)
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
        hint = " — install the scoped sudoers rule (see the Permissions panel)" \
            if kind == PrivError.KIND_RULE_MISSING else ""
        self._log("FAILED: " + detail + hint)
