# Planned Features

- polishing GUI layout and feel

# v1.3.4

**GUI-only changes, parity with Daemon v1.3.X**.
- **Update dialog commit links** — the update dialog's "current commit:" line is now a clickable link to the GitHub commit page (refreshed to the new HEAD after a successful pull), and after an update it shows linked prev → new hashes plus a compare link for multi-commit pulls; the rollback command's previous hash is linked too. The URL base is read from each repo's own `remote.origin.url`, so non-GitHub remotes (other hosts, local paths) degrade to today's plain text with no broken links.
- **Live TICK description** — the TICK row's hint now shows the Hz figure and period computed from the actually active tickrate on every refresh ("ticker period in seconds (~125 Hz at 0.008)") instead of the fixed "~250 Hz at 0.004" example; unapplied edits don't change it until applied, matching the Effective column's semantics.
- **Check for updates button** — new [Check for updates…] below the two update buttons on the Daemon group re-runs the read-only local-vs-origin comparison on demand (it previously only ran at startup and after an update dialog): the indicator row shows "checking for updates…" while it runs, an available update appears in the existing amber note, and a clean check flashes a status-bar confirmation ("no updates available", or an inconclusive note when offline / non-git) instead of silently hiding.

# v1.3.3

**GUI-only changes, parity with Daemon v1.3.X**.
- Verified update functionality as described in v1.3.2.
- **Startup update indicator** — the Daemon group now compares local HEAD against `origin`'s for both repos at startup (read-only `git ls-remote`, no writes to `.git`, so it works on fresh root-owned installs; no hardcoded branch name): an amber "Update to DAEMON/GUI available" note appears below the two update buttons when a repo is behind, and re-checks after each update dialog. Hidden when up to date or unknown (offline / non-git install) — it shows only actionable news.
- **apply-config hardening** — `priv.py apply-config` now reads the whole config text from stdin instead of opening a caller-chosen path: the root helper can no longer read an arbitrary file, and its error echoes can only ever contain text the GUI itself generated (the old form leaked the first line of e.g. `/etc/shadow` on parse failure and copied the entire file into `config.ini` on success). A 1 MiB cap bounds the input.
- **priv.py stays root-owned** — README step 5 now re-chowns `helpers/priv.py` to `root:root` after the repo-wide user chown (a user-writable sudo-run helper is a live privilege-escalation vector), and the Permissions panel gains a fifth check row that surfaces any ownership drift (e.g. from a manual `git pull`) with the copy-paste fix.
- The README's sudoers example now uses `$USER` instead of a hardcoded username, matching the permissions panel's own fix text.

# v1.3.2

**GUI-only changes, parity with Daemon v1.3.X**.
- **Tray icon** — a runtime-drawn dot in the system tray: green while a scroll session is active (state
  `active`, re-read at 4 Hz), gray when idle; a click toggles the main window. No icon file needed, and it
  degrades gracefully on sessions without a tray.
- **One-click daemon update** — new [Update daemon…] button on the Daemon group plus `priv.py update`:
  `git pull --ff-only` in `/opt/veloscroll`, service-file refresh + daemon-reload when the release changed
  `veloscroll.service`, restart, and a journal tail of only the new entries. The dialog shows a transcript of
  every step; a clean repo ends in an "already up to date" no-op (nothing restarted), and after a real update
  it offers the previous commit as a one-line rollback command. Success is confirmed by the service coming
  back active with no startup errors — and by a fresh `config loaded` line when `LOG_LEVEL >= 1`.
- **One-click GUI update** — new [Update GUI…] button directly below [Update daemon…] on the Daemon group,
  plus `priv.py update-gui`: `git pull --ff-only` in `/opt/veloscroll-gui`. The GUI is not a service, so there
  is nothing to restart — after a successful pull that changes code, the running instance closes & reopens
  itself automatically (an "already up to date" no-op leaves it untouched). Same transcript and one-line
  rollback command as the daemon update. After this release ships, the GitHub version gets bumped so the
  button can be tested end-to-end against a real pull.
- **GUI-only show/hide toggle** — a new GUI-owned key on the Status & Config page (GUI section, daemon ignores it):
  `TRAY_LED`, a checkbox that shows/hides the tray LED. It applies live (no Apply round-trip; Cancel reverts) and
  persists in `config.ini` once applied — unset means on.

# v1.3.1

**GUI-only changes, parity with Daemon v1.3.0** 
- **Scroll/Invert tab** — the Feel (`INVERT`, `INVERT_H`, `TICK`) and Scroll mode sections moved out of
  Status & Config into their own tab, with its own Apply/Cancel bar over the shared edit buffer.
- **Startup tab key** — new GUI-only config key `STARTUP_TAB` (combo on the Status & Config page) picks
  which tab opens on startup; an unset or unknown value falls back to the first tab. The daemon ignores
  the key, so it never affects reloads or presets.
- The distance watcher (GUI side) now follows the daemon's actual write rate instead of a fixed ~33 Hz.

# v1.3.0 — Initial Release

The first public release of the VeloScroll GUI — a PySide6 + pyqtgraph frontend
for the [VeloScroll](https://github.com/LucasTippelt/VeloScroll) velocity-scrolling
daemon. The version number is kept in lockstep with the daemon (1.3.0 / 1.3.0);
both are always updated together.

### What's included

- **Dashboard** — live daemon state from `/run/veloscroll/state` and service
  status, with an automatic in-process fallback when the state file is off
- **Config editor** — all 30 daemon keys grouped by function, one-click Apply;
  changes hot-reload in ~2 s, no restart needed
- **Ramp tab** — live curve previews of your speed ramp, plus per-axis distance
  markers driven by the ~250 Hz `/run/veloscroll/distance` feed (opt-out checkbox)
- **Device Picker** — USB-ID based device selection that's stable across
  reboots and ports, with Capture… dialogs for assigning scroll buttons
- **Presets** — save/load/share config presets, with timestamped `.bak-*`
  backups and a restore view
- **Permissions panel** — checks the scoped sudoers rule, `input` group
  membership, config file and systemd unit; every gap comes with a copy-paste
  fix and [Re-check]

### Security model

The GUI runs unprivileged as your desktop user. Every privileged operation goes
through one allowlisted helper (`helpers/priv.py`) under a single scoped
NOPASSWD sudoers rule — no broad sudo, no shell anywhere.

### Quick start

Full one-time setup (daemon + GUI) is in the [README](https://github.com/LucasTippelt/VeloScroll-GUI#one-time-setup).
In short: install your distro's `python-evdev` package, clone both repos to
`/opt`, create the project venv (`--system-site-packages`), install the scoped
sudoers rule — then launch with `/opt/veloscroll-gui/run.sh` or the "VeloScroll
GUI" desktop entry.

### Requirements

- Linux desktop (Debian/Ubuntu, Fedora, Arch and openSUSE covered in the README)
- Python 3 + system `python-evdev`
- GUI deps: PySide6, pyqtgraph (installed into the project venv)
