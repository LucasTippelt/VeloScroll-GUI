# VeloScroll GUI

GUI frontend for the [VeloScroll](https://github.com/LucasTippelt/VeloScroll) daemon (`/opt/veloscroll`).
Python 3 + PySide6 (+ pyqtgraph). This file covers complete installation and setup, everything after setup — each tab, the permissions checks, and all daemon config keys — is documented in
[USAGE.md](USAGE.md).


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
   **Fallback**: pip inside a virtualenv: python3 -m venv <dir>, activate it, then pip install evdev.

2. **Clone the projects** to a stable location:

   ```sh
   sudo git clone https://github.com/LucasTippelt/VeloScroll /opt/veloscroll
   sudo git clone https://github.com/LucasTippelt/VeloScroll-GUI /opt/veloscroll-gui
   ```

### Daemon (`/opt/veloscroll`)

3. **Create the config file and copy the default presets**

   ```sh
   sudo mkdir -p /etc/veloscroll
   sudo cp /opt/veloscroll/config.ini.example /etc/veloscroll/config.ini
   sudo cp -r /opt/veloscroll/presets.example /etc/veloscroll/presets/
   ```

   The example ships a placeholder `DEVICE_MATCH` and `SCROLL_BTN = BTN_SIDE`. That's fine for first launch:
   the daemon starts, grabs nothing yet, and you set your mouse (plus `DPI`) in the GUI's Device Picker tab
   and your buttons with its [Capture…] dialogs — every change hot-reloads within ~2 s.

4. **Install and start the systemd service**:

   > **Strongly recommended safety net — do this before enabling the service:** set up [KDE Connect](https://kdeconnect.kde.org/) (or any similar remote-input app/VNC/cheap secondary mouse) on your phone so you can send mouse clicks/movement to your computer *should* VeloScroll ever misbehave and you lose normal mouse control (there are safeguards against this but better to be safe than sorry). It makes fixing whatever went wrong much easier than rebooting blind.

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

  If `python3 -m venv` fails with an ensurepip error, install your distro's venv
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

or the "VeloScroll GUI" desktop entry.

Everything after setup — each tab, the permissions checks, and all daemon config keys — is documented in
[USAGE.md](USAGE.md).

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
writing could have a measurable performance impact on lower-end hardware, so it stays opt-out.

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
