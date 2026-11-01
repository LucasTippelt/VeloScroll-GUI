# Planned Features/Ideas

- A way for the graphs to signal if scrolling is up/down or left/right (Change the arrow between Inches and Notches based on the context)
- Ghost cursor?

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