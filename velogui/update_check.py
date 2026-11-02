"""Startup update check: local HEAD vs origin's HEAD for both repos, read-only.

`git ls-remote origin HEAD` performs no writes to `.git` (so it works even though /opt/veloscroll is
root-owned on fresh installs) and names no branch — main or master both resolve through the remote's
HEAD symref. Any failure (offline, not a git checkout, no git binary) yields None = "unknown", which
the UI treats as "no actionable news" rather than an error: startup must never be blocked by it."""
import subprocess

from PySide6.QtCore import QThread, Signal

from . import DAEMON_DIR, GUI_DIR


def check_repo(repo_dir):
    """True = the remote's HEAD differs from local (update available), False = up to date, None = unknown.

    A local-ahead or diverged checkout also reports True: without fetching objects there is no ancestry
    check — `git pull --ff-only` then fails safely with its transcript + rollback hint."""
    try:
        p = subprocess.run(["git", "-C", repo_dir, "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    local = (p.stdout or "").strip()
    try:
        r = subprocess.run(["git", "-C", repo_dir, "ls-remote", "origin", "HEAD"],
                           capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    lines = [l for l in (r.stdout or "").splitlines() if l.strip()]
    remote = lines[0].split()[0] if lines else ""
    if not local or not remote:
        return None
    return bool(local != remote)


class UpdateCheckJob(QThread):
    """Run both repo checks sequentially off the GUI thread; one signal carries both results."""

    results = Signal(object)   # (daemon_available, gui_available), each True / False / None

    def run(self):
        try:
            daemon = check_repo(DAEMON_DIR)
        except Exception:      # a worker must never die silently — degrade to unknown instead
            daemon = None
        try:
            gui = check_repo(GUI_DIR)
        except Exception:
            gui = None
        self.results.emit((daemon, gui))
