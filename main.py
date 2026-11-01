#!/usr/bin/env python3
"""VeloScroll GUI — entry point (chunk 1: bootstrap + dashboard; chunk 2: Config editor;
chunk 3: curve previews + device picker).

Status and Config are merged into one page: a per-key table with the live effective
value, an edit field, and the description, plus the daemon/permissions groups. The Ramp tab
holds the ramp options next to their live curves; the Device Picker tab holds the mouse
selector. Every tab carries its own Apply/Cancel bar over the shared edit buffer; Presets+Backups
[chunk 5] joins them.
"""
import os
import sys
import time

# PyQt6 is installed system-wide: force pyqtgraph (later chunks) onto PySide6 before any Qt import.
os.environ.setdefault("QT_API", "pyside")

from PySide6.QtCore import QTimer                # noqa: E402
from PySide6.QtWidgets import (QApplication, QMainWindow, QTabWidget, QVBoxLayout, QWidget)  # noqa: E402

from velogui import APP_NAME                     # noqa: E402
from velogui.backups_tab import BackupsTab       # noqa: E402
from velogui.config_tab import ConfigTab         # noqa: E402
from velogui.device_tab import DeviceTab         # noqa: E402
from velogui.errors import Banner                # noqa: E402
from velogui.permissions_panel import PermissionsPanel  # noqa: E402
from velogui.presets_tab import PresetsTab       # noqa: E402
from velogui.ramp_tab import RampTab             # noqa: E402
from velogui.state_watcher import StateWatcher   # noqa: E402

FLASH_MS = 5000        # how long a success note stays in the status bar before the base text returns


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1200, 800)

        self.banner = Banner()                 # hidden by default; strip above the page
        self.watcher = StateWatcher()
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self.banner)

        # merged Status+Config page + Ramp (options + curves) + Device Picker + Presets/Backups (chunk 5)
        self.tab_config = ConfigTab(self.watcher)
        self.tab_ramp = RampTab(self.tab_config, self.watcher)
        self.tab_device = DeviceTab(self.watcher, self.tab_config)
        self.tab_presets = PresetsTab()
        self.tab_backups = BackupsTab(self.watcher)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.tab_config, "Status & Config")
        self.tabs.addTab(self.tab_ramp, "Ramp")
        self.tabs.addTab(self.tab_device, "Device Picker")
        self.tabs.addTab(self.tab_presets, "Presets")
        self.tabs.addTab(self.tab_backups, "Backups")
        lay.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        self.tab_config.dirty_changed.connect(self._on_dirty)

        # the Config tab routes its banners through the main window's banner strip — except success
        # notes and transient info (e.g. "waiting for the daemon to reload"), which flash in the bottom
        # status bar instead of occupying the strip (5 s, then the base "state file ..." text comes back)
        def _config_banner(text, kind="warning", sticky=False):
            if kind in ("success", "info"):
                self._status_flash(text)
                return
            self.banner.show_message(text, kind=kind, sticky=sticky)
        self.config_banner = _config_banner

        self._status_base = "state file disabled"
        self.statusBar().showMessage(self._status_base)
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(lambda: self.statusBar().showMessage(self._status_base))

        self._refresh_timer = QTimer(self)     # steady re-render (also picks up config.ini edits while no state file exists)
        self._refresh_timer.setInterval(2000)
        self._refresh_timer.timeout.connect(self.refresh)

        self._flag_off_text = None            # banner text of the sticky flag-off warning (cleared when a state file appears)
        self.watcher.state_changed.connect(self._on_state_payload)
        self.watcher.service_changed.connect(self._on_service)
        self.watcher.flag_off_suspected.connect(self._flag_off_banner)
        # transient watch problems (e.g. "state file unreadable or malformed") flash in the bottom status
        # bar like other non-sticky notes — they are rate-limited and self-clearing, so they never occupy
        # the banner strip
        self.watcher.state_warning.connect(self._status_flash)

    def _status_flash(self, text):
        """Transient note in the status bar (success/info flashes and watch warnings); a newer flash
        restarts the timer."""
        self._flash_timer.start(FLASH_MS)
        self.statusBar().showMessage(text)

    def start(self):
        self.watcher.start()
        # STATE_FILE / DISTANCE_FILE have no selector rows: the GUI owns them — on while open (forced through
        # one combined Apply when they differ; DISTANCE_FILE only unless opted out on the Ramp tab), off again
        # at exit (ConfigTab._on_app_quit)
        self.tab_config.auto_set_startup_keys(distance_on=self.tab_ramp.distance_markers_enabled())
        self.refresh()
        self._refresh_timer.start()

    def refresh(self):
        self.tab_config.refresh()
        self.tab_ramp.refresh()
        self.tab_device.refresh()
        self.tab_presets.refresh()      # cheap: two small dir globs; picks up saves/loads from anywhere
        self.tab_backups.refresh()
        w = self.watcher
        if w.last_read_ts is not None:
            ts = time.strftime("%H:%M:%S", time.localtime(w.last_read_ts))
            self._status_base = f"state file last updated {ts}"
        else:
            self._status_base = "state file disabled"
        if not self._flash_timer.isActive():          # an active flash keeps the bar until it expires
            self.statusBar().showMessage(self._status_base)

    def _on_state_payload(self, p):
        # a state file just appeared: clear the sticky flag-off warning (only if it is still what's shown —
        # never clobber a newer message such as an Apply confirmation)
        if self._flag_off_text is not None and self.banner.label.text() == self._flag_off_text \
                and isinstance(p, dict):
            self._flag_off_text = None
            self.banner.hide_banner()
        self.refresh()

    def _flag_off_banner(self):
        # sticky: the condition persists until a state file appears or the service stops;
        # [Enable] (chunk 2) writes STATE_FILE = True through the Config tab's Apply path
        text = ("Service is running but no state file was found — STATE_FILE may be off. "
                "Enable it to connect this dashboard.")
        self._flag_off_text = text
        self.banner.show_message(text, kind="warning", sticky=True,
                                 action_text="Enable", on_action=self._enable_state_file)

    def _enable_state_file(self):
        self.tab_config.enable_state_file()       # everything is on one page now — nothing to switch to

    def _on_dirty(self, dirty):
        self.setWindowTitle(APP_NAME + (" — modified" if dirty else ""))

    def _on_service(self, s):
        if s != "active":
            self.banner.hide_banner()          # flag-off condition can no longer hold
            self._flag_off_text = None
        self.refresh()


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    win.start()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
