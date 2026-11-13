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

1. **Install dependencies**

    | Distribution  | Command |
    |---------------|---------|
    | Debian/Ubuntu | `sudo apt install python3-evdev python3-dbus python3-gi` |
    | Fedora        | `sudo dnf install python3-evdev python3-dbus python3-gobject` |
    | Arch          | `sudo pacman -S python-evdev python-dbus python-gobject` |
    | openSUSE      | `sudo zypper in python3-evdev python3-dbus-python python3-gobject` |
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

4. **Deploy the root-executed files, then install and start the systemd service**:

    > **Strongly recommended safety net — do this before enabling the service:** set up [KDE Connect](https://kdeconnect.kde.org/)  on your phone (or any similar remote-input app/VNC/cheap secondary mouse) so you can send mouse clicks/movement to your computer *should* VeloScroll ever misbehave and you lose normal mouse control (there *are* safeguards against this but better to be safe than sorry). It makes fixing whatever went wrong much easier than rebooting blind.


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

### GUI (`/opt/veloscroll-gui`)

5. **Ownership** r-owned for development:
    ```sh
    sudo chown -R $USER:$USER /opt/veloscroll-gui
    ```

   The helper's runtime copy deploys to `/usr/lib/veloscroll-gui` (root-owned 755, step 7), so the unit never executes anything in the
    repo and a `git pull` can touch any file without breaking the privilege boundary.

6. **GUI virtualenv** — the GUI runs from a project venv at `.venv`; `run.sh` execs its interpreter (one-time):

   ```sh
   cd /opt/veloscroll-gui
   python3 -m venv --system-site-packages .venv
   .venv/bin/pip install -r requirements.txt
   ```

7. **Privileged helper service**
   The GUI performs every privileged operation through a root-owned system-bus D-Bus service gated per call by polkit:

    ```sh
    sudo cp /opt/veloscroll-gui/org.veloscroll.gui.policy /usr/share/polkit-1/actions/
    sudo mkdir -p /usr/lib/veloscroll-gui
    sudo cp /opt/veloscroll-gui/helpers/helper_service.py /usr/lib/veloscroll-gui/
    sudo chown root:root /usr/lib/veloscroll-gui /usr/lib/veloscroll-gui/helper_service.py
    sudo chmod 755 /usr/lib/veloscroll-gui /usr/lib/veloscroll-gui/helper_service.py
    sudo cp /opt/veloscroll-gui/veloscroll-gui-helper.service /etc/systemd/system/
    systemctl daemon-reload && sudo systemctl enable --now veloscroll-gui-helper
    ```


    **Hardened system buses:** some systems run their bus with name ownership denied by default (each service
    then gets an explicit allow drop-in). If the unit fails to start and its journal shows
    `Request to own name refused by policy`, install the matching drop-in too:

    ```sh
    sudo cp /opt/veloscroll-gui/org.veloscroll.gui.conf /etc/dbus-1/system.d/
    sudo systemctl restart dbus && sudo systemctl reset-failed veloscroll-gui-helper && sudo systemctl start veloscroll-gui-helper
    ```

    **Security note — what this does and doesn't do.** The polkit policy (`org.veloscroll.gui.policy`) defines
    two tiers: read-only operations (ping / logs / devices) and config, preset & service management pass for any
    logged-in user without a prompt; button capture, updates, and changing TICK require an **admin
    authentication on every start**.
   
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
