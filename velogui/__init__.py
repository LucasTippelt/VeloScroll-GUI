"""VeloScroll GUI — shared constants."""
import os

APP_NAME = "VeloScroll"
VERSION = "2.1.1"   #new scheme since v2.0.0 (see VERSIONING.md): X parity break, Y feature/behavior change in either component, Z bugfix/docs

DAEMON_DIR = "/opt/veloscroll"
CONFIG_PATH = "/etc/veloscroll/config.ini"
STATE_PATH = "/run/veloscroll/state"
DISTANCE_PATH = "/run/veloscroll/distance"   # high-rate live per-axis distance feed (daemon key DISTANCE_FILE)
SERVICE = "veloscroll"
UNIT_PATH = "/etc/systemd/system/veloscroll.service"

GUI_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # /opt/veloscroll-gui
ICON_PATH = os.path.join(GUI_DIR, "velogui", "icon.png")

# privileged helper service — the unit executes the DEPLOYED copy;
# helpers/helper_service.py in the repo is source only
HELPER_SERVICE_PATH = "/usr/lib/veloscroll-gui/helper_service.py"   # runtime copy — must stay root-owned 755
REPO_HELPER_SOURCE = os.path.join(GUI_DIR, "helpers", "helper_service.py")   # user-owned source; redeployed by Update GUI… when a release changes it
BUS_NAME = "org.veloscroll.gui"          # system-bus name owned by the service (veloscroll-gui-helper.service)
HELPER_OBJECT_PATH = "/org/veloscroll/gui/Helper"
HELPER_IFACE = "org.veloscroll.gui.Helper"
# install locations referenced by the permissions-panel fix text:
HELPER_UNIT_PATH = "/etc/systemd/system/veloscroll-gui-helper.service"
POLICY_FILE_PATH = "/usr/share/polkit-1/actions/org.veloscroll.gui.policy"

# the tab pages in display order — main.py adds them to the QTabWidget in this order and STARTUP_TAB
# (a GUI-only config key) stores one of these exact labels for the tab opened on startup
TABS = ("Status & Config", "Scroll/Invert", "Ramp", "Device Picker", "Presets", "Backups")
