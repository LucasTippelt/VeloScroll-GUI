"""VeloScroll GUI — shared constants."""
import os

APP_NAME = "VeloScroll"
VERSION = "1.3.3"   #initial release was 1.3.0 to match daemon version, 1.3.X releases on both sides are compatible

DAEMON_DIR = "/opt/veloscroll"
CONFIG_PATH = "/etc/veloscroll/config.ini"
STATE_PATH = "/run/veloscroll/state"
DISTANCE_PATH = "/run/veloscroll/distance"   # high-rate live per-axis distance feed (daemon key DISTANCE_FILE)
SERVICE = "veloscroll"
UNIT_PATH = "/etc/systemd/system/veloscroll.service"

GUI_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # /opt/veloscroll-gui
ICON_PATH = os.path.join(GUI_DIR, "velogui", "icon.png")
PRIV_PATH = os.path.join(GUI_DIR, "helpers", "priv.py")
SUDOERS_FILE = "/etc/sudoers.d/veloscroll-gui"

# the tab pages in display order — main.py adds them to the QTabWidget in this order and STARTUP_TAB
# (a GUI-only config key) stores one of these exact labels for the tab opened on startup
TABS = ("Status & Config", "Scroll/Invert", "Ramp", "Device Picker", "Presets", "Backups")
