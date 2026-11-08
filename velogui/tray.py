"""Tray icon: a QSystemTrayIcon with runtime-drawn pixmaps — no icon file needed.

Red = scroll bypass latched (state `bypass`, daemon >= 2.1.0), green = an active scroll session
(state `active`), gray = idle. The flags are re-read at 4 Hz, the same rate as the state-file
watcher; since the daemon rewrites the state on every held transition and latch flip, the dot lags
a press by at most one write (~250 ms). The pixmap is only redrawn when a flag flips. A single
left click toggles the main window (show/raise or hide). When no system tray is available the icon
is simply not created — nothing else in the GUI depends on it."""

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QSystemTrayIcon

from . import APP_NAME

ACTIVE_COLOR = "#16a34a"
BYPASS_COLOR = "#dc2626"   # same red as the service "failed" dot — an attention state that wins over green
IDLE_COLOR = "#9ca3af"


def _pixmap(color):
    """A status-LED dot with a small vertical bar (the scroll motif); drawn at 64 px, the tray scales it down."""
    size = 64
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    m = size * 0.12
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(m, m, size - 2 * m, size - 2 * m, size * 0.30, size * 0.30)
    bw = size * 0.14                            # inner notch: a short vertical bar in white
    x = (size - bw) / 2
    p.setBrush(QColor("white"))
    p.drawRoundedRect(x, m + size * 0.28, bw, size * 0.44, bw / 2, bw / 2)
    p.end()
    return pm


class TrayIcon(QObject):
    def __init__(self, window, watcher, enabled_getter=None):
        super().__init__()
        self._window = window
        self._watcher = watcher
        # TRAY_LED (GUI-only key): the getter returns the edit-buffer value; polled at 20 Hz so an edit or
        # Cancel takes effect without any other wiring. None -> no polling (tests / standalone use).
        self._enabled_getter = enabled_getter
        # None = "not yet applied": QSystemTrayIcon is hidden until shown, and start() calls set_enabled
        # with the file value — if _enabled started True that call would early-return and the LED would
        # never appear for default users (no TRAY_LED key). The first set_enabled always takes effect.
        self._enabled = None
        self.icon = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return                              # no tray on this session — the GUI works without it
        # Qt 6's QSystemTrayIcon takes a QIcon (setPixmap was removed with the Qt5 API)
        self.icon = QSystemTrayIcon(QIcon(_pixmap(IDLE_COLOR)))
        self.icon.setToolTip(APP_NAME)
        self.icon.activated.connect(self._on_activated)
        self._state = "idle"                    # "bypass" / "active" / "idle" — the pixmap only redraws on a flip
        t = QTimer(self)
        t.setInterval(50)                      # 20 Hz (see module docstring for why this is enough)
        t.timeout.connect(self._tick)
        t.start()

    def set_enabled(self, on):
        """Show/hide the LED icon (TRAY_LED). Idempotent; a no-op when the session has no tray."""
        on = bool(on)
        if self._enabled is not None and on == self._enabled:
            return                                # _enabled None -> first call always applies (see __init__)
        self._enabled = on
        if self.icon is not None:
            if on:
                self.icon.show()
            else:
                self.icon.hide()

    def _tick(self):
        if self._enabled_getter is not None:
            try:
                self.set_enabled(self._enabled_getter())
            except Exception:
                pass                            # a getter failure must never kill the 20 Hz tick
        p = self._watcher.payload
        active = bool(p.get("active")) if isinstance(p, dict) else False
        bypass = bool(p.get("bypass")) if isinstance(p, dict) else False   # additive field (daemon >= 2.1.0); older daemons never set it -> gray/green as before
        state = "bypass" if bypass else ("active" if active else "idle")   # red wins: while latched no session can be engaged anyway
        if state != self._state:
            self._state = state
            color = BYPASS_COLOR if bypass else (ACTIVE_COLOR if active else IDLE_COLOR)
            self.icon.setIcon(QIcon(_pixmap(color)))
            tip = {"bypass": f"{APP_NAME} — scroll bypass latched",
                   "active": f"{APP_NAME} — scrolling active"}.get(state, APP_NAME)
            self.icon.setToolTip(tip)

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:   # single left click toggles; there is no context menu in v1
            w = self._window
            if w.isVisible():
                w.hide()
            else:
                w.show()
                w.raise_()
                w.activateWindow()

    def show(self):
        if self.icon is not None:
            self.icon.show()
