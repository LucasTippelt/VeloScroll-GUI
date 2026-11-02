"""Backups tab: browse the .bak-<timestamp> backups of config.ini and presets; restore one.

The daemon's save/load commands back up the file they overwrite (pruned per MAX_BACKUPS); this tab
lists every well-formed backup in /etc/veloscroll/ and its presets/ subdir, parsed in-process (the
files are world-readable). Selecting a row shows its full key→value preview next to the current
effective values — changed keys highlighted. [Restore] confirms, then runs `priv.py preset restore`
with an explicit relative path (unambiguous even when two backups share a basename); restoring a
config.ini backup makes the daemon hot-reload it within ~2 s.
"""
import configparser
import os
import re
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
                                QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import CONFIG_PATH
from .proc import PrivError, PrivJob
from .values import _daemon_module, format_value, resolve

# Must stay in sync with veloscroll_preset.BAK_RE — the exact shape _backup() writes.
BAK_RE = re.compile(r"^(?P<orig>.+)\.bak-(?P<ts>\d{8}-\d{6}\d{6})$")

CHANGED_BG = QColor("#fef3c7")     # amber tint: value differs from the current effective one
CHANGED_FG = QColor("#1f2937")     # explicit dark text on that light tint — a bare background leaves the theme's (white) text unreadable under dark themes


def _parse_ini(path):
    """[veloscroll] raw key→value of an INI file; None when unreadable/malformed."""
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


def list_backups():
    """[(basename, relpath, target, mtime, keys|None)] newest first, from the config dir + presets/."""
    base_dir = os.path.dirname(os.path.abspath(CONFIG_PATH))
    found = []
    for d in (base_dir, os.path.join(base_dir, "presets")):
        try:
            entries = os.listdir(d)
        except OSError:
            continue
        for e in entries:
            m = BAK_RE.match(e)
            if not m or not os.path.isfile(os.path.join(d, e)):
                continue
            found.append((m.group("ts"), d, e))
    out = []
    for _ts, d, e in sorted(found, reverse=True):
        path = os.path.join(d, e)
        keys = _parse_ini(path)
        rel = os.path.relpath(path, base_dir)
        m = BAK_RE.match(e)
        orig = m.group("orig") if m else e
        target = os.path.join(os.path.dirname(rel), orig) if os.path.dirname(rel) else orig
        out.append((e, rel, target, os.path.getmtime(path), None if keys is None else len(keys)))
    return out


def _typed(mod, key, raw):
    """Parse a backup's raw value with the daemon's own parser for a typed comparison; (value|None, ok)."""
    if mod is None:
        return None, False
    try:
        return mod._parse_ini_value(key, raw.strip()), True
    except Exception:
        return None, False


class BackupsTab(QWidget):
    def __init__(self, watcher, parent=None):
        super().__init__(parent)
        self.watcher = watcher
        self._job = None

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        header = QLabel("Backups made before every config overwrite (pruned per MAX_BACKUPS). "
                        "Select one to preview it against the current values; [Restore] puts it back.")
        # no stylesheet: palette default text color (theme-safe black on light, white on dark)
        root.addWidget(header)

        body = QHBoxLayout()

        left = QVBoxLayout()
        self.tbl_backups = QTableWidget(0, 4)
        self.tbl_backups.setHorizontalHeaderLabels(("backup", "target", "modified", "keys"))
        # backup names are long (.bak-<timestamp>) and modified is a fixed date+time — give both real room;
        # keys (a small number) stays narrow instead of stretching to fill the table
        self.tbl_backups.setColumnWidth(0, 280)
        self.tbl_backups.setColumnWidth(1, 120)
        self.tbl_backups.setColumnWidth(2, 145)
        self.tbl_backups.setColumnWidth(3, 60)
        self.tbl_backups.verticalHeader().setVisible(False)
        self.tbl_backups.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl_backups.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_backups.itemSelectionChanged.connect(self._on_selected)
        left.addWidget(self.tbl_backups, 1)

        btns = QHBoxLayout()
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self.refresh)
        self.btn_restore = QPushButton("Restore selected…")
        self.btn_restore.setEnabled(False)
        self.btn_restore.setToolTip("Puts the backup back over its original file (daemon reloads within ~2 s)")
        self.btn_restore.clicked.connect(self._on_restore)
        btns.addWidget(btn_refresh)
        btns.addStretch(1)
        btns.addWidget(self.btn_restore)
        left.addLayout(btns)
        body.addLayout(left, 5)

        right = QVBoxLayout()
        # no forced color (theme-safe): only the size is set — palette default text color applies
        lbl_detail = QLabel("preview — amber rows differ from the current effective value")
        lbl_detail.setStyleSheet("font-size: small;")
        right.addWidget(lbl_detail)
        self.tbl_diff = QTableWidget(0, 3)
        self.tbl_diff.setHorizontalHeaderLabels(("key", "in backup", "current"))
        # key / in backup / current share the table width equally (and keep doing so on resize)
        self.tbl_diff.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_diff.verticalHeader().setVisible(False)
        self.tbl_diff.setEditTriggers(QTableWidget.NoEditTriggers)
        right.addWidget(self.tbl_diff, 1)

        self.lbl_status = QLabel("not scanned yet")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("font-size: small;")    # no forced color (theme-safe)
        self.lbl_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right.addWidget(self.lbl_status)
        body.addLayout(right, 6)
        root.addLayout(body, 1)

        # re-diff against the new current values when the state file changes (e.g. after a daemon reload)
        self.watcher.state_changed.connect(self._on_state)

    def _on_state(self, payload):
        if self._selected_rel() is not None:
            self._on_selected()

    # -- list -----------------------------------------------------------------------

    def refresh(self):
        """(Re)scan both backup locations and refill the table (newest first).

        Runs on the main window's 2 s cycle as well as from [Refresh], so it must never wipe a
        live preview: when a row was selected, its selection is restored AND the preview +
        Restore button are rebuilt explicitly — selectRow() alone does not re-fire
        itemSelectionChanged when the selection is unchanged (the common case: with the same
        backup count setRowCount is a no-op and the row stays selected through the refill)."""
        backups = list_backups()
        self._backups = backups
        sel = self._selected_rel()
        self.tbl_backups.setRowCount(len(backups))
        for i, (base, rel, target, mtime, keys) in enumerate(backups):
            self.tbl_backups.setItem(i, 0, QTableWidgetItem(base))
            self.tbl_backups.item(i, 0).setData(Qt.UserRole, rel)
            self.tbl_backups.setItem(i, 1, QTableWidgetItem(target))
            self.tbl_backups.setItem(i, 2, QTableWidgetItem(
                time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))))
            self.tbl_backups.setItem(i, 3, QTableWidgetItem("—" if keys is None else str(keys)))
        self.btn_restore.setEnabled(False)
        self.tbl_diff.setRowCount(0)
        if sel:
            found = False
            for i in range(self.tbl_backups.rowCount()):
                if self.tbl_backups.item(i, 0).data(Qt.UserRole) == sel:
                    self.tbl_backups.selectRow(i)
                    found = True
                    break
            if found:
                self._on_selected()                       # rebuild preview + re-enable [Restore]
            else:                                         # pruned away between scans — nothing left to preview
                self.lbl_status.setText(f"{len(backups)} backup(s) found — the previously selected one no longer exists")
        else:
            self.lbl_status.setText(f"{len(backups)} backup(s) found")

    def _selected_rel(self):
        rows = self.tbl_backups.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.tbl_backups.item(rows[0].row(), 0)
        return item.data(Qt.UserRole) if item else None

    # -- preview / diff ---------------------------------------------------------------

    def _on_selected(self):
        rel = self._selected_rel()
        base_dir = os.path.dirname(os.path.abspath(CONFIG_PATH))
        path = os.path.join(base_dir, rel) if rel else None
        keys = _parse_ini(path) if path else None
        self.btn_restore.setEnabled(keys is not None)
        if keys is None:
            self.tbl_diff.setRowCount(0)
            self.lbl_status.setText("selected backup is unreadable or has no [veloscroll] section")
            return
        values, _ds, _errs = resolve(getattr(self.watcher, "payload", None))
        mod = _daemon_module()
        self.tbl_diff.setRowCount(len(keys))
        n_changed = 0
        for i, (key, raw) in enumerate(sorted(keys.items())):
            typed, ok = _typed(mod, key, raw) if values is not None else (None, False)
            cur_text = format_value(key, values.get(key)) if values is not None else "—"
            changed = ok and values is not None and typed != values.get(key)
            self.tbl_diff.setItem(i, 0, QTableWidgetItem(key))
            item_b = QTableWidgetItem(raw.strip())
            if not ok:
                item_b.setToolTip(f"could not parse {raw!r} as a {key} value — compared as text")
            self.tbl_diff.setItem(i, 1, item_b)
            self.tbl_diff.setItem(i, 2, QTableWidgetItem(cur_text))
            if changed:
                n_changed += 1
                for c in range(3):
                    item = self.tbl_diff.item(i, c)
                    item.setBackground(QBrush(CHANGED_BG))
                    item.setForeground(QBrush(CHANGED_FG))   # dark-on-amber stays legible under light AND dark themes
        self.lbl_status.setText(f"{len(keys)} key(s) in this backup — {n_changed} differ from the current "
                                f"effective value (amber); restoring changes exactly those")

    # -- restore ----------------------------------------------------------------------

    def _on_restore(self):
        rel = self._selected_rel()
        if not rel:
            return
        base = os.path.basename(rel)
        box = QMessageBox(self)
        box.setWindowTitle("Restore backup")
        box.setText(f"Put {base} back over its original file?\n\n"
                    f"The daemon hot-reloads config changes within ~2 s; earlier backups of the same "
                    f"file are kept (pruned per MAX_BACKUPS).")
        box.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        if box.exec() != QMessageBox.Ok:
            return
        self.lbl_status.setText("restoring…")
        job = PrivJob(["preset", "restore", rel], self)      # explicit relative path — unambiguous across both dirs
        job.finished_ok.connect(lambda out: self._finish(job, base, out))
        job.failed.connect(self._fail)
        self._job = job
        job.start()

    def _finish(self, job, base, out):
        if job is not self._job:
            return
        self._job = None
        detail = (out or "").strip().replace("\n", " · ")
        self.lbl_status.setText(f"restored {base} — {detail}")
        self.refresh()

    def _fail(self, kind, detail):
        job = self.sender()
        if job is not self._job:
            return                                       # a newer operation superseded this one
        self._job = None
        hint = " — install the scoped sudoers rule (see the Permissions panel)" \
            if kind == PrivError.KIND_RULE_MISSING else ""
        self.lbl_status.setText(f"restore failed: {detail}{hint}")
