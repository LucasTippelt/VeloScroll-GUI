# VeloScroll GUI

GUI frontend for the [VeloScroll](https://github.com/LucasTippelt/VeloScroll) daemon (`/opt/veloscroll`).
Python 3 + PySide6 (+ pyqtgraph). This file covers complete installation and setup, everything after setup — each tab, the permissions checks, and all daemon config keys — is documented in
[USAGE.md](USAGE.md).


## Prerequisites

- A Linux desktop with `git`.
- (Optional) Systemd
- Everything else — the daemon's dependencies, this GUI's environment, and the polkit-gated helper
  service that performs its privileged operations — is installed by [One-time setup](#one-time-setup) below.

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

    The GUI's privileged helper service (step 7) runs on the *system* Python and additionally needs your
    distro's `dbus-python` + PyGObject packages — Debian/Ubuntu: `sudo apt install python3-dbus python3-gi`,
    Fedora: `python3-dbus python3-gobject`, Arch: `python-dbus python-gobject`.

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

4. **Deploy the root-executed files, then install and start the systemd service**:

    > **Strongly recommended safety net — do this before enabling the service:** set up [KDE Connect](https://kdeconnect.kde.org/) (or any similar remote-input app/VNC/cheap secondary mouse) on your phone so you can send mouse clicks/movement to your computer *should* VeloScroll ever misbehave and you lose normal mouse control (there are safeguards against this but better to be safe than sorry). It makes fixing whatever went wrong much easier than rebooting blind.

    The unit executes a *deployed* copy in `/usr/lib/veloscroll` (root-owned 755), never the repo — so make
    the checkout user-owned source and deploy it before `enable --now`, otherwise the unit fails to start:

    ```sh
    sudo chown -R $USER:$USER /opt/veloscroll
    sudo mkdir -p /usr/lib/veloscroll/helpers
    sudo cp /opt/veloscroll/veloscroll.py /opt/veloscroll/veloscroll_preset.py /usr/lib/veloscroll/
    sudo cp /opt/veloscroll/helpers/ListDeviceNames.py \
            /opt/veloscroll/helpers/ListButtonPresses.py \
            /opt/veloscroll/helpers/veloscroll_tuning_helper.py /usr/lib/veloscroll/helpers/
    sudo chown -R root:root /usr/lib/veloscroll
    sudo chmod 755 /usr/lib/veloscroll /usr/lib/veloscroll/*.py \
                   /usr/lib/veloscroll/helpers /usr/lib/veloscroll/helpers/*.py
    sudo cp /opt/veloscroll/veloscroll.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now veloscroll
    ```

    The deploy block is idempotent — the one-click **Update daemon…** button re-runs it whenever a release
    changes any of those files. `helpers/veloscroll_seed.py` stays
    user-run from the repo, never elevated.

### GUI (`/opt/veloscroll-gui`)

5. **Ownership** — this project home starts out root-owned; make it user-owned for development:

    ```sh
    sudo chown -R $USER:$USER /opt/veloscroll-gui
    ```

    The repo is uniformly user-writable source — nothing root-executed lives in it. The helper's runtime copy
    deploys to `/usr/lib/veloscroll-gui` (root-owned 755, step 7), so the unit never executes anything in the
    repo and a `git pull` can touch any file without breaking the privilege boundary.

6. **GUI virtualenv** — the GUI runs from a project venv at `.venv`; `run.sh` execs its interpreter (one-time):

   ```sh
   cd /opt/veloscroll-gui
   python3 -m venv --system-site-packages .venv
   .venv/bin/pip install -r requirements.txt
   ```

  If `python3 -m venv` fails with an ensurepip error, install your distro's venv
   package first (Debian/Ubuntu: `sudo apt install python3-venv`).

7. **Privileged helper service** (one-time, needs your password). The GUI performs every privileged operation
   through a root-owned system-bus D-Bus service gated per call by polkit — no sudo at all:

    The unit executes the *deployed* copy of the helper (`/usr/lib/veloscroll-gui/helper_service.py`), so
    deploy it before `enable --now` — otherwise the unit fails to start:

    ```sh
    sudo cp /opt/veloscroll-gui/org.veloscroll.gui.policy /usr/share/polkit-1/actions/
    sudo mkdir -p /usr/lib/veloscroll-gui
    sudo cp /opt/veloscroll-gui/helpers/helper_service.py /usr/lib/veloscroll-gui/
    sudo chown root:root /usr/lib/veloscroll-gui /usr/lib/veloscroll-gui/helper_service.py
    sudo chmod 755 /usr/lib/veloscroll-gui /usr/lib/veloscroll-gui/helper_service.py
    sudo cp /opt/veloscroll-gui/veloscroll-gui-helper.service /etc/systemd/system/
    systemctl daemon-reload && sudo systemctl enable --now veloscroll-gui-helper
    ```

    No further setup is needed for the [Update daemon…]/[Update GUI…] buttons: their `git pull` step runs as
    **the user who owns each checkout** (the service drops privileges per git call), so there is no "dubious
    ownership" problem and nothing to add to any git config. Only the restart / service-file parts of a daemon
    update run as root, behind the polkit prompt.

    **Hardened system buses:** some systems run their bus with name ownership denied by default (each service
    then gets an explicit allow drop-in). If the unit fails to start and its journal shows
    `Request to own name refused by policy`, install the matching drop-in too:

    ```sh
    sudo cp /opt/veloscroll-gui/org.veloscroll.gui.conf /etc/dbus-1/system.d/
    sudo systemctl restart dbus && sudo systemctl reset-failed veloscroll-gui-helper && sudo systemctl start veloscroll-gui-helper
    ```

    **Security note — what this does and doesn't do.** The polkit policy (`org.veloscroll.gui.policy`) defines
    two tiers: read-only operations (ping / logs / devices) and config, preset & service management pass for any
    logged-in user without a prompt; button capture and both updates require an **admin authentication on every
    start** — the native polkit prompt appears in place of the old passwordless sudo. Button capture opens input
    nodes **read-only** by design, so nothing can *inject* events through it. The same copy-deploy model covers
    the daemon itself: its unit executes `/usr/lib/veloscroll/veloscroll.py` and this helper runs the preset CLI
    from there too — while both checkouts are user-owned source, so a `git pull` in either repo can never reach
    root on its own. Input-group membership is therefore NOT required — if you added it during an earlier setup, remove it:

    ```sh
    sudo gpasswd -d $USER input    # optional cleanup; the GUI works without it (and with it)
    ```

 8. **Desktop entry**:

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
  opted-out key is left exactly as configured), through one normal Apply path — the helper service from
  step 7 must be installed first.
- **On close** it writes both back to `False` — so these files exist exactly while a GUI is open.

STATE_FILE has no opt-out because its cost is negligible: the daemon writes the state file only on
events (startup, config reloads, device grab/release, scroll session start/stop). The distance feed, by
contrast, is rewritten on every ticker tick (~250 Hz) for as long as a button is held — that continuous
writing could have a measurable performance impact on lower-end hardware, so it stays opt-out.

The daemon hot-reloads within ~2 s of startup, the state file appears, and the dashboard connects
automatically within ~3 s. If the startup write cannot run (e.g. the helper service is not running), the
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
