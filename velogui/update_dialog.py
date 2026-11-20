"""Update dialog: one-click daemon update per docs/UPDATING.md and one-click GUI update.

Runs the helper service's Update (daemon) or UpdateGui as root — polkit auth_admin, so a native
prompt asks for confirmation first — in a PrivJob worker thread: for the daemon, git pull --ff-only in
/opt/veloscroll on the branch the GUI's C++-daemon checkbox selected (migrating the checkout when it
sits on the other branch), the branch's deploy (the five Python files, or a CMake build + install of the
five C++ binaries), service-file refresh + daemon-reload only when the release changed veloscroll.service,
then a restart and a journal tail — or just git pull --ff-only in /opt/veloscroll-gui for the GUI (no
service; after a successful pull that changes code, the running instance spawns a fresh one and quits
itself). The transcript pane shows every step; after a run that pulled new code the previous commit hash
is offered as a one-line rollback command. A clean repo ends in an "already up to date" no-op — nothing
is restarted."""

import html
import json
import os
import re
import subprocess
import sys
import urllib.request

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
                               QPushButton, QVBoxLayout)

from . import DAEMON_DIR, GUI_DIR
from .proc import PrivError, PrivJob

UPDATE_TIMEOUT_S = 180                          # advisory only: the helper enforces its own per-command caps (git 60 s)
UPDATE_TIMEOUT_CPP_S = 600                      # advisory: the cpp branch adds a CMake build on top of the pull
DAEMON_BRANCHES = ("main", "cpp")               # must match the helper's allowlist (helpers/helper_service.py)


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


def _current_branch(repo_dir):
    """Name of the branch the checkout is on, read-only; "" when detached HEAD / unavailable."""
    try:
        p = subprocess.run(["git", "-C", repo_dir, "branch", "--show-current"],
                           capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if p.returncode != 0:
        return ""
    return (p.stdout or "").strip()


def _origin_head(repo_dir, ref="HEAD"):
    """Short hash of origin's `ref` via read-only `git ls-remote` (no .git writes); None when offline /
    not a git checkout. ref is "HEAD" (the remote's default branch — main or master both resolve through
    the HEAD symref) or a branch ref like "refs/heads/cpp"."""
    try:
        p = subprocess.run(["git", "-C", repo_dir, "ls-remote", "origin", ref],
                           capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    lines = [l for l in (p.stdout or "").splitlines() if l.strip()]
    full = lines[0].split()[0] if lines else ""
    return full[:9] if full else None


def _origin_subject(web_base, sha):
    """First line of a remote commit's message via the GitHub REST API; None when unavailable.

    ls-remote carries no subjects and fetching would write to .git (forbidden here), so this is a
    read-only HTTP GET — GitHub remotes only (web_base None -> hash without message, like every other
    non-GitHub degradation in this dialog). Offline / rate-limited / malformed responses degrade to None."""
    if not web_base or not sha:
        return None
    url = web_base.replace("https://github.com/", "https://api.github.com/repos/") + f"/commits/{sha}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "veloscroll-gui"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    msg = (data.get("commit") or {}).get("message") or ""
    first = msg.splitlines()[0].strip() if msg else ""
    return first or None


def _repo_web_base(repo_dir):
    """Web base (https://github.com/<owner>/<repo>) for commit/compare links, read from the repo's own
    remote.origin.url; None when it is not a GitHub checkout (no origin / other host -> plain text)."""
    try:
        p = subprocess.run(["git", "-C", repo_dir, "config", "--get", "remote.origin.url"],
                           capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    url = (p.stdout or "").strip()
    path = None
    for prefix in ("https://github.com/", "git@github.com:", "ssh://git@github.com/"):
        if url.startswith(prefix):
            path = url[len(prefix):]
            break
    if path is None:
        return None
    path = path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-len(".git")]
    parts = path.split("/")
    if len(parts) != 2 or not all(parts):
        return None
    return f"https://github.com/{parts[0]}/{parts[1]}"


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
    (/opt/veloscroll-gui — no service; the running instance needs a reopen to pick up new code).
    For the daemon target, `branch` selects the branch the update works on (main = the Python daemon,
    cpp = the C++ core) — taken from the GUI's C++-daemon checkbox; the checkout is migrated when it
    sits on the other branch."""

    def __init__(self, parent=None, target="daemon", branch="main", apply_text=None):
        super().__init__(parent)
        if target not in ("daemon", "gui"):
            raise ValueError(f"unknown update target {target!r}")
        if branch not in DAEMON_BRANCHES:
            raise ValueError(f"unknown daemon branch {branch!r}")
        if target != "daemon" and apply_text is not None:
            raise ValueError("apply_text is only for the daemon target (the branch-flip Apply)")
        self._target = target
        self._branch = branch
        self._apply_text = apply_text
        self.result = None            # ("ok", transcript) / ("failed", detail) — the caller reads it after exec()
        gui = target == "gui"
        repo_dir = GUI_DIR if gui else DAEMON_DIR
        # static for the dialog's lifetime; None (non-GitHub remote / no origin) -> plain text everywhere
        self._web_base = _repo_web_base(repo_dir)

        if apply_text is not None:
            self.setWindowTitle("Switching the daemon branch")
        else:
            self.setWindowTitle("Update VeloScroll GUI" if gui else "Update VeloScroll daemon")
        self.setMinimumSize(680, 440)
        self._job = None

        root = QVBoxLayout(self)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self._set_info()
        root.addWidget(self.info)

        row = QHBoxLayout()
        self.btn_run = QPushButton(
            "Apply + update…" if apply_text is not None else ("Update GUI…" if gui else "Update daemon…"))
        self.btn_run.setToolTip(
            f"Runs the helper service's {'UpdateGui' if gui else ('ApplyAndUpdate' if apply_text is not None else 'Update')} as root "
            "(polkit asks for confirmation first)")
        self.btn_run.clicked.connect(self._on_update)
        self.lbl_status = QLabel("")
        row.addWidget(self.btn_run)
        row.addWidget(self.lbl_status, 1)
        root.addLayout(row)

        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setPlaceholderText("transcript (empty until an update runs)")
        root.addWidget(self.txt_log, 1)

        # post-update line: linked prev → new hashes (+ compare for multi-commit pulls); hidden until a real pull
        self.lbl_updated = QLabel("")
        self.lbl_updated.setWordWrap(True)
        self.lbl_updated.setTextFormat(Qt.RichText)
        self.lbl_updated.setOpenExternalLinks(True)
        self.lbl_updated.hide()
        root.addWidget(self.lbl_updated)

        self.lbl_rollback = QLabel("")
        self.lbl_rollback.setWordWrap(True)
        self.lbl_rollback.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_rollback.hide()
        root.addWidget(self.lbl_rollback)

        if self._apply_text is not None:
            # branch-flip Apply: the concise confirmation already happened in the tab — the combined job
            # (config write + update behind ONE polkit prompt) starts as soon as the dialog is shown
            QTimer.singleShot(0, self._on_update)

    def _set_info(self):
        """(Re)render the top info label for the current HEAD. `current commit` is the LOCAL head (what is
        running), always plain text — deliberately no link: an unpushed local commit has nothing to point at,
        so a GitHub link could mislead about what an update would actually pull. While origin is ahead an
        extra `latest on origin:` line shows that (real remote) commit with a clickable GitHub link when
        available — omitted when up to date or the remote is unknown. For the daemon target the comparison
        ref is the branch the C++-daemon checkbox selected (origin/cpp, or the remote's HEAD for main), and
        a checkout sitting on the other branch gets a migration note."""
        gui = self._target == "gui"
        repo_dir = GUI_DIR if gui else DAEMON_DIR
        head = _git_head(repo_dir)
        cpp = not gui and self._branch == "cpp"
        ref = "refs/heads/cpp" if cpp else "HEAD"
        remote = _origin_head(repo_dir, ref=ref)
        base = self._web_base
        cur = (f"current commit: {head[0]} — {head[1]}" if head
               else f"current commit: unknown (is {repo_dir} a git checkout?)")
        if not gui:
            on = _current_branch(repo_dir)
            if on and on != self._branch:
                cur += (f"\nbranch: {on} — the C++-daemon checkbox selects '{self._branch}'; "
                        f"the update will migrate the checkout first")
        latest = None
        if remote and (not head or remote != head[0]):
            subject = _origin_subject(base, remote)   # GitHub only; None -> hash without message
            suffix = f" — {html.escape(subject)}" if subject else ""
            label = "latest on origin/cpp" if cpp else "latest on origin"
            latest = (f'{label}: <a href="{base}/commit/{remote}">{remote}</a>{suffix}' if base
                      else f"{label}: {remote}")
        rich = bool(latest and base)                  # only the origin line ever carries a link
        if rich:
            cur = html.escape(cur).replace("\n", "<br>")   # the subject may carry HTML characters; \n collapses in rich text
        mid = ("\n" + latest) if latest else ""
        if gui:
            body = ("Updates the GUI only (/opt/veloscroll-gui):\n\n" + cur + mid + "\n\n"
                    "`git pull --ff-only`. The GUI is not a service — after a successful pull that "
                    "changes code, VeloScroll GUI closes & reopens itself automatically. Your config.ini "
                    "lives outside the repo and is untouched.")
        elif cpp:
            body = ("Updates the daemon only (/opt/veloscroll, cpp branch — the C++ core), "
                    "per docs/UPDATING.md:\n\n" + cur + mid + "\n\n"
                    "migrates the checkout to the cpp branch when needed, `git pull --ff-only origin cpp`, "
                    "builds the five C++ binaries with CMake and installs them to /usr/lib/veloscroll, "
                    "refreshes veloscroll.service + daemon-reload when the release changed it, restarts "
                    "the service and tails the journal. Your config.ini lives outside the repo and is "
                    "untouched.")
        else:
            body = ("Updates the daemon only (/opt/veloscroll, main branch — the Python daemon), "
                    "per docs/UPDATING.md:\n\n" + cur + mid + "\n\n"
                    "migrates the checkout to the main branch when needed, `git pull --ff-only`, "
                    "re-deploys the changed daemon files, refreshes veloscroll.service + daemon-reload "
                    "when the release changed it, restarts the service and tails the journal. Your "
                    "config.ini lives outside the repo and is untouched.")
        if self._apply_text is not None:
            body += ("\n\nThis run also saves your config first — ONE password prompt covers the save "
                     "and the update.")
        if rich:
            self.info.setTextFormat(Qt.RichText)      # \n collapses in rich text — convert to <br>
            self.info.setOpenExternalLinks(True)
            self.info.setText(body.replace("\n", "<br>"))
        else:
            self.info.setTextFormat(Qt.PlainText)
            self.info.setText(body)

    def _on_update(self):
        if self._job is not None and self._job.isRunning():
            return                                       # one update at a time (the transcript would interleave)
        gui = self._target == "gui"
        if self._apply_text is None:
            # the branch-flip Apply skips this box: the tab already showed its concise popup, and the
            # combined job's single polkit prompt is the one confirmation
            box = QMessageBox(self)
            box.setWindowTitle("Update VeloScroll GUI" if gui else "Update VeloScroll daemon")
            if gui:
                box.setText("Run the GUI update as root?\n\n"
                            "helper service: UpdateGui (polkit will ask for confirmation)\n\n"
                            "(git pull --ff-only in /opt/veloscroll-gui; restart the GUI afterwards to apply)")
            elif self._branch == "cpp":
                box.setText("Run the daemon update as root?\n\n"
                            "helper service: Update (polkit will ask for confirmation)\n\n"
                            "(migrate the checkout to the cpp branch when needed, git pull --ff-only origin cpp,\n"
                            "CMake build of the five C++ binaries + install, service-file refresh when changed, restart)")
            else:
                box.setText("Run the daemon update as root?\n\n"
                            "helper service: Update (polkit will ask for confirmation)\n\n"
                            "(migrate the checkout to the main branch when needed, git pull --ff-only,\n"
                            "re-deploy the changed daemon files, service-file refresh when changed, restart)")
            if not gui:
                on = _current_branch(DAEMON_DIR)
                if on and on != self._branch:
                    box.setText(box.text() + f"\n\nThe checkout is currently on branch '{on}' — "
                                             f"it will be migrated to '{self._branch}'.")
            box.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
            if box.exec() != QMessageBox.Ok:
                return
        self.btn_run.setEnabled(False)
        if gui:
            self.lbl_status.setText("running… (git pull can take a while)")
        elif self._branch == "cpp":
            self.lbl_status.setText("running… (git pull + CMake build can take a few minutes)")
        else:
            self.lbl_status.setText("running… (git pull + restart can take a while)")
        timeout = UPDATE_TIMEOUT_CPP_S if (not gui and self._branch == "cpp") else UPDATE_TIMEOUT_S
        if self._apply_text is not None:
            # branch-flip Apply: the config write and the full update travel as ONE polkit-gated call
            job = PrivJob(["apply-and-update", self._branch], self, input_text=self._apply_text, timeout=timeout)
        else:
            job = PrivJob(["update-gui"] if gui else ["update", self._branch], self, timeout=timeout)
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
        gui = self._target == "gui"
        repo_dir = GUI_DIR if gui else DAEMON_DIR
        cpp = not gui and self._branch == "cpp"
        # cpp: a checkout alone does not rebuild the installed binary — the note says what else a rollback needs
        restart = "" if gui else (" && sudo systemctl restart veloscroll" if not cpp else "")
        note = ("   (C++ branch: a checkout does not rebuild the installed binary — re-run the build + install "
                "block from docs/INSTALL.md, or flip the C++ checkbox back and run [Update daemon…] again, "
                "then sudo systemctl restart veloscroll)" if cpp else "")
        base = self._web_base
        if base:
            # rich text with a clickable previous hash; the command stays selectable (TextSelectableByMouse)
            self.lbl_rollback.setTextFormat(Qt.RichText)
            self.lbl_rollback.setOpenExternalLinks(True)
            self.lbl_rollback.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
            self.lbl_rollback.setText(
                f"If something breaks, roll back to the previous commit:<br>"
                f"sudo git -C {repo_dir} checkout <a href=\"{base}/commit/{prev}\">{prev}</a>{restart}{note}")
        else:                                           # non-GitHub remote — today's plain text
            self.lbl_rollback.setTextFormat(Qt.PlainText)
            self.lbl_rollback.setText(
                f"If something breaks, roll back to the previous commit:\n"
                f"sudo git -C {repo_dir} checkout {prev}{restart}{note}")
        self.lbl_rollback.show()

    def _show_updated(self, prev, out):
        """Post-update line: linked prev → new hashes plus a compare link for multi-commit pulls; plain
        text (no links) when the web base is unknown. `out` is the transcript carrying the helper's
        `updated <prev9> → <new9> (N new commit(s))` line."""
        if not prev or len(prev) < 7:
            return
        head = _git_head(GUI_DIR if self._target == "gui" else DAEMON_DIR)
        if head is None:
            return
        new = head[0]
        m = re.search(r"updated [0-9a-f]+ → [0-9a-f]+ \((\d+) new commit\(s\)\)", out or "")
        n = int(m.group(1)) if m else None
        base = self._web_base
        prev_txt = f'<a href="{base}/commit/{prev}">{prev[:9]}</a>' if base else prev[:9]
        new_txt = f'<a href="{base}/commit/{new}">{new}</a>' if base else new
        text = f"Updated {prev_txt} → {new_txt}"
        if n is not None and n >= 2:                    # compare only for multi-commit pulls, with a known base
            text += f' · <a href="{base}/compare/{prev}...{new}">compare</a>'
        self.lbl_updated.setText(text)
        self.lbl_updated.show()

    def _finish(self, job, out):
        if job is not self._job:
            return                                       # a newer operation superseded this one
        self._job = None
        self.result = ("ok", out)
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
            self._show_updated(prev, out)
            self._set_info()      # the top line must point at the new HEAD (the dialog stays open for daemon targets)
        else:
            self.lbl_updated.hide()   # a no-op leaves any earlier post-update line stale — clear it

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
        self.result = ("failed", detail)
        self.btn_run.setEnabled(True)
        text = (detail or "").strip()
        hint = "\n\n— install and start the helper service first (see the Permissions panel)" \
            if kind == PrivError.KIND_SERVICE_MISSING else ""
        self.txt_log.setPlainText(text + hint)
        prev, result = _parse_markers(text)
        self._set_result_label(False, result or "update failed — see transcript")
        self.lbl_updated.hide()   # a failed run supersedes any earlier post-update line
        self._show_rollback(prev)
