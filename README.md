# VeloScroll GUI

GUI frontend for the [VeloScroll](https://github.com/LucasTippelt/VeloScroll) daemon (`/opt/veloscroll`).
Python 3 + PySide6 (+ pyqtgraph). The app runs as your desktop user; every privileged
operation goes through one allowlisted helper binary under a single scoped NOPASSWD sudoers rule.


## Prerequisites

- A Linux desktop with `git`.
- (Optional) Systemd
- Everything else — the daemon's dependencies, this GUI's environment, the scoped sudoers rule — is
  installed by [One-time setup](#one-time-setup) below.

## One-time setup

Installs both the [VeloScroll](https://github.com/LucasTippelt/VeloScroll) daemon and this GUI in one pass;
every step runs once. The daemon steps follow the daemon's own installation guide, trimmed to what a fresh
install needs — device and button selection happen in the GUI after first launch.

### General

1. **Install dependencies** — Python 3 + evdev for the daemon (this GUI's own packages — PySide6, pyqtgraph,
   numpy — are pip-installed into a project venv in step 6):

   | Distribution  | Command |
   |---------------|---------|
   | Debian/Ubuntu | `sudo apt install python3-evdev` |
   | Fedora        | `sudo dnf install python3-evdev` |
   | Arch          | `sudo pacman -S python-evdev` |
   | openSUSE      | `sudo zypper in python3-evdev` |

2. **Clone the projects** to a stable location:

   ```sh
   sudo git clone https://github.com/LucasTippelt/VeloScroll /opt/veloscroll
   sudo git clone https://github.com/LucasTippelt/VeloScroll-GUI /opt/veloscroll-gui
   ```

### Daemon (`/opt/veloscroll`)

3. **Create the config file**

   ```sh
   sudo mkdir -p /etc/veloscroll
   sudo cp /opt/veloscroll/config.ini.example /etc/veloscroll/config.ini
   ```

   The example ships a placeholder `DEVICE_MATCH` and `SCROLL_BTN = BTN_SIDE`. That's fine for first launch:
   the daemon starts, grabs nothing yet, and you set your mouse (plus `DPI`) in the GUI's Device Picker tab
   and your buttons with its [Capture…] dialogs — every change hot-reloads within ~2 s.

4. **Install and start the systemd service**:

> **Strongly recommended safety net — do this before enabling the service:** set up [KDE Connect](https://kdeconnect.kde.org/) (or any similar remote-input app) on your phone so you can send mouse clicks/movement over your local metwork if VeloScroll ever misbehaves and you lose normal mouse control (there are safeguards against this but better to be safe than sorry). It makes fixing whatever went wrong much easier than rebooting blind — see the [README's safety-net section](../README.md#safety-net-keep-a-second-way-to-drive-your-mouse-strongly-recommended).

   ```sh
   sudo cp /opt/veloscroll/veloscroll.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now veloscroll
   ```

### GUI (`/opt/veloscroll-gui`)

5. **Ownership** — this project home starts out root-owned:

   ```sh
   sudo chown -R $USER:$USER /opt/veloscroll-gui
   ```

6. **GUI virtualenv** — the GUI runs from a project venv at `.venv`; `run.sh` execs its interpreter (one-time):

   ```sh
   cd /opt/veloscroll-gui
   python3 -m venv --system-site-packages .venv
   .venv/bin/pip install -r requirements.txt
   ```

   `--system-site-packages` is required, not optional: the GUI reads `/dev/input/*` through the system's
   `python-evdev`, which lives in the system site-packages and is deliberately not pinned in
   `requirements.txt`. If `python3 -m venv` fails with an ensurepip error, install your distro's venv
   package first (Debian/Ubuntu: `sudo apt install python3-venv`).

7. **Scoped sudoers rule** (one-time, needs your password). Install it and validate with `visudo -cf` in the
   same command — if validation fails, fix before relying on the GUI's privileged operations:

   ```sh
   chmod +x /opt/veloscroll-gui/helpers/priv.py    # direct execution (own shebang), like the preset rule
   echo 'lucas ALL=(root) NOPASSWD: /opt/veloscroll-gui/helpers/priv.py' \
     | sudo tee /etc/sudoers.d/veloscroll-gui && sudo visudo -cf /etc/sudoers.d/veloscroll-gui
   ```

   One command, any subcommand — same trade-off as the documented preset rule. The rule names `priv.py`
   itself (executed directly via its shebang), NOT `/usr/bin/python3 .../priv.py`: with an interpreter in
   front, sudo treats the script path as the single fixed argument and refuses every real call that adds a
   subcommand (`sudo -n python3 priv.py ping` fails to match). `helpers/priv.py` keeps a strict allowlist
   and re-validates every write with the daemon's own parser; it refuses to run unprivileged.

8. **input group** (needed for in-process `/dev/input/*` access):

   ```sh
   sudo usermod -aG input $USER    # requires logging out and back in
   ```

9. **Desktop entry**:

   ```sh
   mkdir -p ~/.local/share/applications
   cp /opt/veloscroll-gui/veloscroll-gui.desktop ~/.local/share/applications/
   update-desktop-database ~/.local/share/applications 2>/dev/null || true
   ```

## Running

```sh
/opt/veloscroll-gui/run.sh
```

or the "VeloScroll GUI" desktop entry. No custom icon in v1.

## GUI-managed lifecycle keys (STATE_FILE, DISTANCE_FILE)

The dashboard reads live daemon data from `/run/veloscroll/state`, and the Ramp tab's live curve
markers read `/run/veloscroll/distance`. The daemon writes each file only while its key is on — both
default off, so there is zero overhead without a GUI. The GUI owns both keys: there are no selector
rows for either in the values table.

- **On startup** it forces `STATE_FILE = True` (always) and `DISTANCE_FILE = True` unless you opt out
  with the "live position markers (DISTANCE_FILE)" checkbox on the Ramp tab (checked by default; an
  opted-out key is left exactly as configured), through one normal Apply path — the scoped sudoers rule
  from step 7 must be installed first.
- **On close** it writes both back to `False` — so these files exist exactly while a GUI is open.

STATE_FILE has no opt-out because its cost is negligible: the daemon writes the state file only on
events (startup, config reloads, device grab/release, scroll session start/stop). The distance feed, by
contrast, is rewritten on every ticker tick (~250 Hz) for as long as a button is held — that continuous
writing can have a measurable performance impact on lower-end hardware, so it stays opt-out.

The daemon hot-reloads within ~2 s of startup, the state file appears, and the dashboard connects
automatically within ~3 s. If the startup write cannot run (e.g. the sudoers rule is missing), the
status bar shows "state file disabled", after ~5 s of service-active-without-state-file a banner warns
that `STATE_FILE` may be off — its [Enable] action retries the same Apply path — and until then the
values table falls back to computing effective values in-process (config.ini + built-in defaults),
which is correct but not live.

Manual equivalent:

```sh
echo 'STATE_FILE = True' | sudo tee -a /etc/veloscroll/config.ini
echo 'DISTANCE_FILE = True' | sudo tee -a /etc/veloscroll/config.ini
```

Note: both keys are per-machine operational state — presets never capture or apply them.

## Permissions panel

The page's Permissions group (below the values table) checks, with a copy-paste fix for each gap (and [Re-check]):

1. the scoped sudoers rule (`sudo -n ... priv.py ping`),
2. current user in the `input` group,
3. `/etc/veloscroll/config.ini` exists,
4. unit file present at `/etc/systemd/system/veloscroll.service` and service enabled.

## Config keys (hot-reloaded)

All 30 daemon keys, grouped as in the GUI — the ramp keys (Feel / Horizontal overrides) are edited on the
Ramp tab next to their live curves, Device & buttons on the Device Picker tab, and INVERT/INVERT_H/TICK with
Scroll mode, Logging and Misc in the Status & Config table. A non-empty value in `/etc/veloscroll/config.ini`
overrides the built-in default; a missing or empty key falls back to it. Edits hot-reload within ~2 s — no
restart needed (the GUI applies them with one click). Keys with an `_H` suffix are horizontal variants; where
noted, unset or `none` falls back to the vertical value.

### Feel

| Key | Meaning / units | Tuning hint |
|-----|-----------------|-------------|
| `DPI` | Your mouse's current DPI (counts/inch). Converts inches to raw counts. | Set it to your mouse's current DPI setting, not its maximum. |
| `DEADZONE_IN` | Distance from the press point (inches) below which nothing scrolls. | Raise if scrolling starts before you've moved the mouse at all. |
| `FULL_IN` | Distance (inches) from the press point where `MAX_RATE` is reached. Must be > `DEADZONE_IN`. | The span over which speed ramps up. |
| `MAX_RATE` | Scroll rate in notches/second at `FULL_IN` and beyond. | Your top scrolling speed. |
| `MIN_RATE` | Scroll rate in notches/second just past the deadzone. | The floor of the ramp; keep above 0. |
| `CURVE` | Shape of the speed ramp: `>1` gentle near the start, `=1` linear, `<1` steep near the start. | Tune feel: `<1` reacts fast to small movements, `>1` is more forgiving. |
| `OUT_SMOOTH` | `0..1`: how quickly the rate follows the target. Lower = smoother, higher = snappier. | Lower it if scrolling feels steppy or jumpy. |
| `INVERT` | `True`/`False`. Flip scroll direction. | Set `true` if your DE has "natural scrolling" enabled. |
| `INVERT_H` | Horizontal variant of `INVERT` for `SCROLL_BTN_H`. Unset or `none` falls back to the vertical value. | Only if the H axis should be inverted differently than vertical (e.g. natural scrolling on one axis only). |
| `TICK` | Ticker loop period in seconds; `0.004` s ≈ 250 Hz — how often the scroll rate is recomputed and hi-res events are emitted. | Not a feel setting (that's `OUT_SMOOTH`/`MAX_RATE`). Only lower it if scrolling still looks steppy, at the cost of more CPU. |

### Horizontal overrides

Used only while `SCROLL_BTN_H` is held; unset or `none` falls back to the vertical value.

| Key | Meaning / units | Tuning hint |
|-----|-----------------|-------------|
| `DEADZONE_IN_H` | Horizontal variant of `DEADZONE_IN` (inches). | Only if horizontal scrolling should start at a different distance than vertical. |
| `FULL_IN_H` | Horizontal variant of `FULL_IN`. Must be greater than the effective horizontal deadzone. | The span over which horizontal speed ramps up. |
| `MIN_RATE_H` | Horizontal variant of `MIN_RATE` (notches/second). | The floor of the horizontal ramp; keep > 0. |
| `MAX_RATE_H` | Horizontal variant of `MAX_RATE`. Must be >= the effective horizontal `MIN_RATE`. | Your top horizontal scrolling speed. |
| `CURVE_H` | Horizontal variant of `CURVE` (ramp shape). | Same semantics as `CURVE`. |
| `OUT_SMOOTH_H` | Horizontal variant of `OUT_SMOOTH`. | How quickly the horizontal rate follows its target. |

### Device & buttons

| Key | Meaning / units | Tuning hint |
|-----|-----------------|-------------|
| `NAME_MATCH` | Case-insensitive substring of your mouse's name that selects it. Used only when `DEVICE_MATCH` is unset (fallback). | Use a short unique fragment from the device list; if any other device shares it, prefer `DEVICE_MATCH`. Recommended for piping another virtual device into VeloScroll. |
| `DEVICE_MATCH` | Recommended precise selector for a physical device; overrides `NAME_MATCH`. An exact USB ID pair (`0x46d, 0xc084`) or a stable path string (e.g. `/dev/input/by-id/...`) for two identical mice. | Set it in the GUI's Device Picker tab (its USB ID pair): stable across reboots/ports, and lets per-device pointer rules apply automatically. |
| `SCROLL_BTN` | Key code of the button to hold for scrolling (evdev name like `BTN_SIDE`, or decimal code, e.g. `275`). | Use the GUI's [Capture…] dialog (or `ListButtonPresses.py` from the daemon repo). Names are `evdev.ecodes.BTN_*`. |
| `SCROLL_BTN_H` | Optional second button to hold for velocity scrolling. By default horizontal, driven by mouse X movement (emits `REL_HWHEEL`/`REL_HWHEEL_HI_RES`), using the vertical ramp unless overridden by the `_H` keys. With `VERTICAL_H = true` it emits vertical wheel events from up/down movement instead, at the same `_H`-key values. Evdev name or decimal code, or `none` to disable (default). | Must differ from `SCROLL_BTN` and `DISABLED_BTN`. `INVERT` flips both axes (`INVERT_H` overrides it for the H axis). |
| `DISABLED_BTN` | Key code that is swallowed and does nothing, or `none` to disable this feature. | Set `none` if you don't want to disable a button. |

### Scroll mode

| Key | Meaning / units | Tuning hint |
|-----|-----------------|-------------|
| `TOGGLE_SCROLL` | How `SCROLL_BTN` engages velocity scrolling. `False` (default) = hold-to-scroll, the button must stay pressed. `True` = tap to toggle on, tap again to toggle off (releasing does nothing while toggled). | While toggled on, movement is captured exactly as in hold mode (cursor stays put, same ramp from the tap point). Set `true` if you prefer a latched scroll mode over holding. |
| `TOGGLE_SCROLL_H` | Horizontal variant of `TOGGLE_SCROLL` for `SCROLL_BTN_H`. Unset or `none` falls back to the vertical value. | Only if horizontal scrolling should use a different engagement mode than vertical (e.g. hold vertically, toggle horizontally). |
| `VERTICAL_H` | `True`/`False`. With `true`, holding `SCROLL_BTN_H` becomes a second vertical velocity scroll instead of horizontal: mouse up/down movement drives `REL_WHEEL`/`REL_WHEEL_HI_RES` using the horizontal ramp values (`*_H` keys), and left/right movement does nothing. `false` (default) = horizontal behavior. | Only takes effect when `SCROLL_BTN_H` is set. Flipped by `INVERT_H` when set, else by `INVERT`, like every other axis. Flipping it live mid-hold ends any in-progress session. |
| `LEFT_CLICK_RESET` | `True`/`False`. What releasing the left mouse button does while a scroll session is active: `true` (default) = fresh origin — the next movement scrolls from zero again; `false` = keep the current distance, so scrolling continues at its old speed. | Set `false` if you prefer to click mid-session without losing your scroll position/speed. Velocity output is paused while left is down either way. |

### Logging

| Key | Meaning / units | Tuning hint |
|-----|-----------------|-------------|
| `LOG_LEVEL` | What the daemon writes to its log (`journalctl -u veloscroll`). Integer `0..3`, higher = more verbose: `0` errors only, `1` + events (`grabbed …`), `2` + config detail (default: unknown keys ignored, reload rejected, config reloaded old→new), `3` + debug. | Set `0` on a healthy system for silence; raise to `3` temporarily when debugging device selection. Lowering below `2` also hides the `config reloaded:` confirmation line itself. |
| `LATENCY_LOG` | `True`/`False`. Per-second measured latency summary lines plus a computed model line at startup/config change. Independent of `LOG_LEVEL`. | Debugging only: set `true`, watch `journalctl -u veloscroll`, then leave it `false`. |

### Misc

| Key | Meaning / units | Tuning hint |
|-----|-----------------|-------------|
| `MAX_BACKUPS` | How many `.bak-<timestamp>` backups the preset helper and seed script keep per file family. `-1` = unlimited, `N ≥ 1` = keep only the newest N after each run that writes a backup, `0` = backups disabled entirely (existing ones stay untouched). | Hot-reloaded like every other key — each helper run reads the current value. Set `0` if you don't want any `.bak-*` files at all. |
| `STATE_FILE` | `True`/`False`. When true, the daemon writes `/run/veloscroll/state`: a JSON snapshot of all effective values, the grabbed devices and scroll activity (plus reload health) for GUI frontends to read. Off by default — zero overhead when no GUI runs. | Per-machine operational state like `MAX_BACKUPS` — never part of presets; managed by the GUI itself (forced on while it is open, off at exit — no selector row). The shipped unit creates `/run/veloscroll` (`RuntimeDirectory=veloscroll`). See [GUI-managed lifecycle keys](#gui-managed-lifecycle-keys-state_file-distance_file). |
| `DISTANCE_FILE` | `True`/`False`. When true, the daemon writes a second file, `/run/veloscroll/distance`: the live per-axis distances (vertical + horizontal) and smoothed rate of the active scroll session, rewritten on every ticker tick while a button is held (~250 Hz). This is what drives the Ramp tab's live curve markers — with it off they only follow your mouse. Off by default — zero overhead without a consumer. | Per-machine operational state like `STATE_FILE` — never part of presets; managed by the GUI itself (forced on while open unless opted out via the Ramp tab checkbox, always off at exit — no selector row). The same unit creates `/run/veloscroll` for this file too. See [GUI-managed lifecycle keys](#gui-managed-lifecycle-keys-state_file-distance_file). |
