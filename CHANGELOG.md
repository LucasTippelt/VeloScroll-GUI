# Planned Features

- **Feature/Convience**: On gui startup, check git and make update field say "Update to GUI/DAEMON available"
- **Security**: make `/opt/veloscroll-gui/helpers/priv.py` root-owned -> closing possible privilege escalation vector
- **Documentation** Sudoers Example: Username fix

# v1.3.3

- Verified update functionality as described in v1.3.2. Works.

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
