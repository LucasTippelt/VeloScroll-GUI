"""Update dialog: one-click daemon update per docs/UPDATING.md and one-click GUI update.

Runs `priv.py update` (daemon) or `priv.py update-gui` as root through the scoped sudoers rule in a
PrivJob worker thread: git pull --ff-only in /opt/veloscroll, service-file refresh + daemon-reload only
when the release changed veloscroll.service, then a restart and a journal tail — or just git pull
--ff-only in /opt/veloscroll-gui for the GUI (no service; after a successful pull that changes code,
the running instance spawns a fresh one and quits itself). The transcript pane shows every step; after
a run that pulled new code the previous commit hash is offered as a one-line rollback command. A clean
repo ends in an "already up to date" no-op — nothing is restarted."""

import os
import subprocess
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
                               QPushButton, QVBoxLayout)

from . import DAEMON_DIR, GUI_DIR
from .proc import PrivError, PrivJob

UPDATE_TIMEOUT_S = 120                          # a git pull over a slow link may outlive the default 30 s


def _git_head(repo_dir):
    """(short_hash, subject) of the current HEAD in repo_dir, read in-process (read-only); None when unavailable."""
    try:
        p = subprocess.run(["git", "-C", repo_dir, "log", "-1", "--format=%h %s"],
                           capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    line = (p.stdout or "").strip()
    short, _, subject = line.partition(" ")
    return (short, subject) if short else None


def _parse_markers(text):
    """(previous_commit|None, result_text|None) from the transcript's marker lines."""
    prev = result = None
    for line in (text or "").splitlines():
        if line.startswith("previous commit: "):
            prev = line[len("previous commit: "):].strip()
        elif line.startswith("result: "):
            result = line[len("result: "):]
    return prev, result


class UpdateDialog(QDialog):
    """One-click update for one target: "daemon" (/opt/veloscroll + service restart) or "gui"
    (/opt/veloscroll-gui — no service; the running instance needs a reopen to pick up new code)."""

    def __init__(self, parent=None, target="daemon"):
        super().__init__(parent)
        if target not in ("daemon", "gui"):
            raise ValueError(f"unknown update target {target!r}")
        self._target = target
        gui = target == "gui"
        repo_dir = GUI_DIR if gui else DAEMON_DIR

        self.setWindowTitle("Update VeloScroll GUI" if gui else "Update VeloScroll daemon")
        self.setMinimumSize(680, 440)
        self._job = None

        root = QVBoxLayout(self)
        head = _git_head(repo_dir)
        cur = (f"current commit: {head[0]} — {head[1]}" if head
               else f"current commit: unknown (is {repo_dir} a git checkout?)")
        if gui:
            info_text = ("Updates the GUI only (/opt/veloscroll-gui):\n\n" + cur + "\n\n"
                         "`git pull --ff-only`. The GUI is not a service — after a successful pull that "
                         "changes code, VeloScroll GUI closes & reopens itself automatically. Your config.ini "
                         "lives outside the repo and is untouched.")
        else:
            info_text = ("Updates the daemon only (/opt/veloscroll), per docs/UPDATING.md:\n\n" + cur + "\n\n"
                         "`git pull --ff-only`, refreshes veloscroll.service + daemon-reload when the release "
                         "changed it, restarts the service and tails the journal. Your config.ini lives outside "
                         "the repo and is untouched.")
        info = QLabel(info_text)
        info.setWordWrap(True)
        root.addWidget(info)

        row = QHBoxLayout()
        self.btn_run = QPushButton("Update GUI…" if gui else "Update daemon…")
        self.btn_run.setToolTip(
            f"Runs `sudo -n priv.py {'update-gui' if gui else 'update'}` as root (asks for confirmation first)")
        self.btn_run.clicked.connect(self._on_update)
        self.lbl_status = QLabel("")
        row.addWidget(self.btn_run)
        row.addWidget(self.lbl_status, 1)
        root.addLayout(row)

        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setPlaceholderText("transcript (empty until an update runs)")
        root.addWidget(self.txt_log, 1)

        self.lbl_rollback = QLabel("")
        self.lbl_rollback.setWordWrap(True)
        self.lbl_rollback.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_rollback.hide()
        root.addWidget(self.lbl_rollback)

    def _on_update(self):
        if self._job is not None and self._job.isRunning():
            return                                       # one update at a time (the transcript would interleave)
        gui = self._target == "gui"
        box = QMessageBox(self)
        box.setWindowTitle("Update VeloScroll GUI" if gui else "Update VeloScroll daemon")
        if gui:
            box.setText("Run the GUI update as root?\n\n"
                        "sudo -n priv.py update-gui\n\n"
                        "(git pull --ff-only in /opt/veloscroll-gui; restart the GUI afterwards to apply)")
        else:
            box.setText("Run the daemon update as root?\n\n"
                        "sudo -n priv.py update\n\n"
                        "(git pull --ff-only in /opt/veloscroll, service-file refresh when changed, restart)")
        box.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        if box.exec() != QMessageBox.Ok:
            return
        self.btn_run.setEnabled(False)
        self.lbl_status.setText("running… (git pull can take a while)" if gui
                                else "running… (git pull + restart can take a while)")
        job = PrivJob(["update-gui"] if gui else ["update"], self, timeout=UPDATE_TIMEOUT_S)
        job.finished_ok.connect(lambda out: self._finish(job, out))
        job.failed.connect(self._fail)
        self._job = job
        job.start()

    def _set_result_label(self, ok, text):
        # explicit state colors (the codebase's green/red pair) — legible under both themes
        color = "#16a34a" if ok else "#dc2626"
        self.lbl_status.setStyleSheet(f"color: {color};")
        self.lbl_status.setText(text)

    def _show_rollback(self, prev):
        if not prev or len(prev) < 7:                   # a full/short hash is expected; never offer an empty command
            return
        repo_dir = GUI_DIR if self._target == "gui" else DAEMON_DIR
        restart = "" if self._target == "gui" else " && sudo systemctl restart veloscroll"
        self.lbl_rollback.setText(
            f"If something breaks, roll back to the previous commit:\n"
            f"sudo git -C {repo_dir} checkout {prev}{restart}")
        self.lbl_rollback.show()

    def _finish(self, job, out):
        if job is not self._job:
            return                                       # a newer operation superseded this one
        self._job = None
        self.btn_run.setEnabled(True)
        self.txt_log.setPlainText(out or "")
        prev, result = _parse_markers(out)
        noop = bool(result and "already up to date" in result)
        if self._target == "gui" and not noop:
            # new code is on disk but this process still runs the old one — let the green status be seen,
            # then hand over (the fresh instance re-enables STATE_FILE/DISTANCE_FILE itself at startup)
            self._set_result_label(True, (result or "update finished") + " — closing & reopening now…")
            QTimer.singleShot(2000, self._relaunch_and_quit)
        else:
            self._set_result_label(True, result or "update finished")
        if not noop:
            self._show_rollback(prev)

    def _relaunch_and_quit(self):
        """Spawn a fresh GUI instance in its own session (it survives this process exiting), then quit.

        Relaunches with the very interpreter running us — guaranteed to work, since we are working."""
        try:
            with open(os.devnull, "wb") as devnull:
                subprocess.Popen([sys.executable, os.path.join(GUI_DIR, "main.py")],
                                 start_new_session=True, stdout=devnull, stderr=devnull)
        except OSError as exc:
            self._set_result_label(False, f"update pulled new code but the relaunch failed ({exc}) — "
                                          "restart VeloScroll GUI manually")
            return
        QApplication.quit()

    def _fail(self, kind, detail):
        job = self.sender()
        if job is not self._job:
            return                                       # a newer operation superseded this one
        self._job = None
        self.btn_run.setEnabled(True)
        text = (detail or "").strip()
        hint = "\n\n— install the scoped sudoers rule first (see the Permissions panel)" \
            if kind == PrivError.KIND_RULE_MISSING else ""
        self.txt_log.setPlainText(text + hint)
        prev, result = _parse_markers(text)
        self._set_result_label(False, result or "update failed — see transcript")
        self._show_rollback(prev)
