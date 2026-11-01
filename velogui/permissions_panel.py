"""Permissions panel: four prerequisite checks, each gap with its copy-paste fix.

Checks (per plan):
 1. sudo rule   — `sudo -n helpers/priv.py ping` via proc.run_priv
 2. input group — current user in the `input` group (needed to open /dev/input/* in-process)
 3. config.ini  — /etc/veloscroll/config.ini exists
 4. unit        — unit file present AND `systemctl is-enabled veloscroll` == enabled

Chunk 1 runs all checks synchronously on [Re-check] (the ping call fails fast via -n).
"""
import getpass
import os
import subprocess

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication, QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)

from . import CONFIG_PATH, PRIV_PATH, SUDOERS_FILE, UNIT_PATH
from .proc import PrivError, run_priv, systemctl_enabled

USER = getpass.getuser()

# The rule names the helper itself (direct execution, its own shebang — same pattern as the
# documented preset rule). Listing it with a `python3` interpreter in front would not match:
# sudo then treats the script path as the single fixed argument and refuses any subcommand.
SUDOERS_FIX = (
    f"echo '{USER} ALL=(root) NOPASSWD: {PRIV_PATH}' | "
    f"sudo tee {SUDOERS_FILE} && sudo visudo -cf {SUDOERS_FILE}"
)


def check_sudo_rule():
    try:
        out = run_priv(["ping"])
        return True, f"sudo rule works (priv.py answered '{out}')", None
    except PrivError as e:
        if e.kind == PrivError.KIND_RULE_MISSING:
            return False, "scoped sudoers rule missing or not matching this user/command", SUDOERS_FIX
        if e.kind == PrivError.KIND_NOT_ROOT:
            return False, "helper ran but was not root — the rule is broken", SUDOERS_FIX
        return False, f"priv.py probe failed: {e}", None


def _in_group_on_disk(group_name):
    """Membership per /etc/group (the file, not this process's credentials).

    A process's groups are fixed at login: a usermod -aG done after the GUI started shows up here
    but NOT in `id -nG` until the process is restarted — that is the stale-process case."""
    try:
        with open("/etc/group", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split(":")
                if len(parts) >= 4 and parts[0] == group_name:
                    return USER in (parts[3].split(",") if parts[3] else [])
    except OSError:
        pass
    return False


def _process_groups():
    """This process's own group names via `id -nG` (children inherit the GUI's credentials).

    This — not /etc/group — decides whether in-process /dev/input opens succeed."""
    try:
        p = subprocess.run(["id", "-nG"], capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    return p.stdout.split()


def _other_gui_instances():
    """PIDs of other running copies of this GUI.

    The window in front of the user may belong to an OLDER instance (e.g. one from a previous login
    session that was never killed) — its stale credentials would then explain a check that looks
    impossible after a re-login."""
    me = os.getpid()
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == me:
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv0, _, rest = f.read().partition(b"\x00")
        except OSError:
            continue
        if b"python" not in argv0:
            continue
        cmd = (argv0 + b" " + rest).decode("utf-8", "replace")
        if "/opt/veloscroll-gui/main.py" in cmd or ("veloscroll-gui" in cmd and "main.py" in cmd):
            out.append(int(pid))
    return sorted(out)


def check_input_group():
    groups = _process_groups()
    if groups is None:
        return False, "could not determine current user's groups", "sudo usermod -aG input $USER   # then log out and back in"
    if "input" in groups:
        return True, f"user '{USER}' is in the input group", None
    if _in_group_on_disk("input"):
        detail = (f"user '{USER}' IS in the input group on disk (/etc/group), but this GUI process "
                  f"(PID {os.getpid()}) does not have it — its groups are: {', '.join(groups)}. "
                  f"Processes keep the groups of whatever launched them, so restarting the GUI from "
                  f"within the same session (or a terminal opened in it) does NOT pick up new membership.")
        others = _other_gui_instances()
        if others:
            detail += (f"  Other GUI instance(s) are still running (PID {', '.join(map(str, others))}) — "
                       f"you may be looking at an old one; close it and start a fresh one.")
        return (False, detail,
                "sg input /opt/veloscroll-gui/run.sh   # launch with the group right now (no re-login needed)\n"
                "# or log out AND back in — only helps if the logout actually ends this session "
                "(check: loginctl list-sessions)")
    return (False,
            f"user '{USER}' is NOT in the input group — needed to open /dev/input/* in-process",
            "sudo usermod -aG input $USER   # requires logging out and back in")


def check_config():
    if os.path.isfile(CONFIG_PATH):
        return True, f"{CONFIG_PATH} exists", None
    return (False, f"{CONFIG_PATH} is missing — the daemon itself must be installed first",
            "sudo cp /opt/veloscroll/config.ini.example /etc/veloscroll/config.ini")


def check_unit():
    if not os.path.isfile(UNIT_PATH):
        return False, f"unit file {UNIT_PATH} is missing", (
            "sudo cp /opt/veloscroll/veloscroll.service /etc/systemd/system/ && "
            "sudo systemctl daemon-reload && sudo systemctl enable veloscroll")
    if systemctl_enabled() != "enabled":
        return False, f"unit present but not enabled (`systemctl is-enabled` = {systemctl_enabled()})", (
            "sudo systemctl enable veloscroll")
    return True, "unit file present and service enabled", None


CHECKS = (
    ("sudo rule", check_sudo_rule),
    ("input group", check_input_group),
    ("config.ini", check_config),
    ("systemd unit", check_unit),
)

_MONO = QFont("monospace")


class _CheckRow(QWidget):
    def __init__(self, name, ok, detail, fix):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        top = QHBoxLayout()
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {'#16a34a' if ok else '#dc2626'}; font-size: 14px;")
        name_l = QLabel(name)
        name_font = name_l.font(); name_font.setBold(True); name_l.setFont(name_font)
        detail_l = QLabel(detail)
        detail_l.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(dot, 0, Qt.AlignVCenter)
        top.addWidget(name_l)
        top.addStretch(1)
        top.addWidget(detail_l)
        lay.addLayout(top)
        self.fix_edit = None
        if fix is not None:
            row2 = QHBoxLayout()
            edit = QPlainTextEdit(fix)
            edit.setReadOnly(True)
            edit.setMaximumHeight(46)
            edit.setFont(_MONO)
            copy = QPushButton("Copy")
            copy.clicked.connect(lambda: QApplication.clipboard().setText(edit.toPlainText()))
            row2.addWidget(edit, 1)
            row2.addWidget(copy)
            lay.addLayout(row2)
            self.fix_edit = edit


class PermissionsPanel(QGroupBox):
    """The four checks as rows (OK/gap + fix command), rerunnable via [Re-check]."""

    checked = Signal()   # emitted after a full re-run (sync in chunk 1)

    def __init__(self, parent=None):
        super().__init__("Permissions", parent)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        hint = QLabel("Privileged operations and /dev/input access prerequisites")    # no forced color (theme-safe)
        self.btn_recheck = QPushButton("Re-check")
        self.btn_recheck.clicked.connect(self.recheck)
        top.addWidget(hint, 1)
        top.addWidget(self.btn_recheck)
        lay.addLayout(top)
        self._rows_box = QVBoxLayout()
        lay.addLayout(self._rows_box)

    def recheck(self):
        """Runs all four checks synchronously and rebuilds the rows."""
        results = []
        for name, fn in CHECKS:
            try:
                ok, detail, fix = fn()
            except Exception as exc:   # a check must never take down the GUI
                ok, detail, fix = False, f"check failed: {exc}", None
            results.append((name, ok, detail, fix))
        self._rebuild(results)
        self.checked.emit()

    def _rebuild(self, results):
        while self._rows_box.count():
            item = self._rows_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for name, ok, detail, fix in results:
            row = _CheckRow(name, ok, detail, fix)
            self._rows_box.addWidget(row)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._rows_box.count():      # first paint: run the checks once
            self.recheck()
