"""Permissions panel: four prerequisite checks, each gap with its copy-paste fix.

Checks (per plan):
 1. helper service — `Ping` over the system bus via proc.run_priv; reachable means the root-owned
                     D-Bus service is running AND the polkit policy is installed (a missing policy
                     makes even Ping fail authorization). This single path covers every privileged
                     operation, including button capture and root device enumeration — no input-group
                     membership is needed. auth_admin operations additionally need a polkit agent in
                     your session (KDE/GNOME/XFCE ship one; minimal WMs: install polkit-gtk-auth)
 2. config.ini     — /etc/veloscroll/config.ini exists
 3. unit           — the veloscroll daemon's unit file present AND `systemctl is-enabled` == enabled
 4. helper         — the DEPLOYED runtime copy /usr/lib/veloscroll-gui/helper_service.py root-owned and without
                     user/group write bits (a user-writable root service script is a live
                     privilege-escalation vector; the repo's helpers/helper_service.py is plain source)

All checks run synchronously on [Re-check] (the Ping call fails fast: no owner -> ServiceUnknown,
no policy -> NotAuthorized).
"""
import getpass
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication, QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)

from . import BUS_NAME, CONFIG_PATH, GUI_DIR, HELPER_SERVICE_PATH, REPO_HELPER_SOURCE, UNIT_PATH
from .proc import PrivError, run_priv, systemctl_enabled

USER = getpass.getuser()

# Install the unit + policy and start the service (the whole privileged path in one line). If it
# fails to start, `journalctl -u veloscroll-gui-helper` (as root) shows why — usually missing
# python3-dbus / python3-gi system packages.
SERVICE_FIX = (
    f"sudo cp {GUI_DIR}/veloscroll-gui-helper.service /etc/systemd/system/ && "
    f"sudo cp {GUI_DIR}/org.veloscroll.gui.policy /usr/share/polkit-1/actions/ && "
    f"sudo systemctl daemon-reload && sudo systemctl enable --now veloscroll-gui-helper")

# The service runs but even Ping is refused: the policy file is missing (or polkitd hasn't seen it).
POLICY_FIX = f"sudo cp {GUI_DIR}/org.veloscroll.gui.policy /usr/share/polkit-1/actions/"


def check_helper_service():
    try:
        out = run_priv(["ping"])
        return True, f"helper service reachable (answered '{out}')", None
    except PrivError as e:
        if e.kind == PrivError.KIND_SERVICE_MISSING:
            detail = (f"{BUS_NAME} is not running on the system bus — install and start it "
                      "(if it fails to start, check `journalctl -u veloscroll-gui-helper` as root; "
                      "usually python3-dbus / python3-gi are missing)")
            return False, detail, SERVICE_FIX
        if e.kind == PrivError.KIND_NOT_AUTHORIZED:
            return (False,
                    f"service is running but polkit refused even the no-auth probe — the policy file "
                    f"is probably not installed ({e.stderr or str(e)})",
                    POLICY_FIX)
        return False, f"helper service probe failed: {e}", None


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


def check_helper_integrity(path=HELPER_SERVICE_PATH):
    """The root-run helper script must stay out of the user's reach — and so must its directory: it runs as
    root under systemd, so a user-writable helper_service.py is an escalation vector (edit what the service
    executes), and a user-writable directory is one too (rename-swap: replacing a file only needs write on the
    containing directory, not on the file). This inspects the DEPLOYED runtime copy the unit executes
    (/usr/lib/veloscroll-gui/helper_service.py), not the repo's helpers/helper_service.py — that one is plain
    source, user-writable by design, so a git pull can only ever touch it and can never drift the runtime
    copy. Passes only when both are root-owned with no group/other write bits — 755 (the install mode) keeps
    the exec bit and passes; owner bits are irrelevant once uid == 0."""
    try:
        st = os.stat(path)
    except OSError:
        return False, f"{path} is missing — deploy it from the repo source", \
            f"sudo mkdir -p {os.path.dirname(path)} && sudo cp {REPO_HELPER_SOURCE} {path} && " \
            f"sudo chown root:root {os.path.dirname(path)} {path} && " \
            f"sudo chmod 755 {os.path.dirname(path)} {path} && " \
            "sudo systemctl restart veloscroll-gui-helper"
    d = os.path.dirname(path)
    dst = os.stat(d)
    bad_file = st.st_uid != 0 or (st.st_mode & 0o022)
    bad_dir = dst.st_uid != 0 or (dst.st_mode & 0o022)
    if not (bad_file or bad_dir):
        return True, f"{path} and its directory are root-owned and not user/group-writable", None
    what = []
    if bad_file:
        what.append(f"{path} (uid={st.st_uid}, mode={oct(st.st_mode & 0o777)})")
    if bad_dir:
        what.append(f"its directory {d} (uid={dst.st_uid}, mode={oct(dst.st_mode & 0o777)})")
    detail = (" — a user-writable script that the root service executes is an escalation vector, and a "
              "user-writable directory lets anyone swap it via rename. The repo source "
              f"({REPO_HELPER_SOURCE}) is user-writable by design — a reference, not a trusted copy — so the "
              "fix redeploys it (restoring protection AND content); a plain re-chown would only restore "
              "protection.")
    return False, ("not root-owned and/or has user/group write bits: " + "; ".join(what)) + detail, \
        f"sudo cp {REPO_HELPER_SOURCE} {path} && sudo chown root:root {d} {path} && " \
        f"sudo chmod 755 {d} {path} && sudo systemctl restart veloscroll-gui-helper"


CHECKS = (
    ("helper service", check_helper_service),
    ("config.ini", check_config),
    ("systemd unit", check_unit),
    ("helper integrity", check_helper_integrity),
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

    checked = Signal()   # emitted after a full re-run (synchronous)

    def __init__(self, parent=None):
        super().__init__("Permissions", parent)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        hint = QLabel("Prerequisites for the GUI's privileged operations")    # no forced color (theme-safe)
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
