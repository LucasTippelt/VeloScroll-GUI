"""Privileged-command runner and systemctl helpers (no shell anywhere).

`run_priv` builds [sudo -n priv.py] + args — the helper is executed directly (it carries its own
shebang), which is what the scoped NOPASSWD sudoers rule names. Captures output with a 30 s
timeout, and classifies failures so the GUI can route RULE_MISSING to the permissions-panel
guidance instead of a generic error overlay.
"""
import subprocess

from PySide6.QtCore import QThread, Signal

from . import PRIV_PATH, SERVICE

TIMEOUT_S = 30


class PrivJob(QThread):
    """Run an allowlisted privileged command off the GUI thread (chunks 2+)."""

    finished_ok = Signal(str)      # priv.py stdout on success
    failed = Signal(str, str)      # (PrivError.kind, detail incl. captured stderr)

    def __init__(self, args, parent=None):
        super().__init__(parent)
        self._args = [str(a) for a in args]

    def run(self):
        try:
            out = run_priv(self._args)
        except PrivError as e:
            detail = (e.stderr or str(e)).strip()
            self.failed.emit(e.kind, detail)
        except Exception as exc:   # a worker must never die silently
            self.failed.emit(PrivError.KIND_COMMAND_FAILED, f"unexpected error: {exc}")
        else:
            self.finished_ok.emit(out)


class PrivError(Exception):
    KIND_RULE_MISSING = "rule_missing"      # sudo refused: no matching NOPASSWD rule (or password required)
    KIND_NOT_ROOT = "not_root"              # helper ran but euid != 0 (broken rule / direct invocation)
    KIND_COMMAND_FAILED = "command_failed"  # any other non-zero exit; .stderr holds the captured output

    def __init__(self, kind, message, stderr=""):
        super().__init__(message)
        self.kind = kind
        self.stderr = stderr


def run_priv(args, timeout=TIMEOUT_S):
    """Run the allowlisted helper as root. Returns stripped stdout on success."""
    cmd = ["sudo", "-n", PRIV_PATH] + [str(a) for a in args]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise PrivError(PrivError.KIND_COMMAND_FAILED, f"could not start sudo: {exc}")
    except subprocess.TimeoutExpired:
        raise PrivError(PrivError.KIND_COMMAND_FAILED,
                        f"'sudo -n {PRIV_PATH}' timed out after {timeout} s")

    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if p.returncode == 0:
        return out
    low = f"{err}\n{out}".lower()
    if ("a password is required" in low or "interactive authentication is required" in low
            or "not allowed to run" in low):
        raise PrivError(PrivError.KIND_RULE_MISSING, err or out or "sudo refused the command")
    if p.returncode == 2:
        raise PrivError(PrivError.KIND_NOT_ROOT, "helper ran but was not root (check the sudoers rule)")
    raise PrivError(PrivError.KIND_COMMAND_FAILED, f"command failed with exit code {p.returncode}", err)


def _systemctl(action):
    try:
        p = subprocess.run(["systemctl", action, SERVICE], capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "unknown"
    return (p.stdout or "").strip() or "unknown"


def systemctl_state():
    """Printed state string from `systemctl is-active` ('active', 'inactive', 'failed', ...)."""
    return _systemctl("is-active")


def systemctl_enabled():
    """Printed state string from `systemctl is-enabled` ('enabled', 'disabled', ...)."""
    return _systemctl("is-enabled")
