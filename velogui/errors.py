"""Blocking error overlay and non-blocking status banner."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout)

# kind -> (background, border, text)
_COLORS = {
    "warning": ("#fef3c7", "#f59e0b", "#78350f"),
    "error":   ("#fee2e2", "#dc2626", "#7f1d1d"),
    "success": ("#dcfce7", "#16a34a", "#14532d"),
}


class Banner(QFrame):
    """Thin strip above the tabs: warnings / last-failed op.

    Auto-hides after ~8 s unless sticky (persistent conditions like flag-off stay).
    An optional action button ([Enable] in chunk 2) is supported from day one."""

    AUTOHIDE_MS = 8000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide_banner)
        self._on_action = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 6, 4)
        self.label = QLabel("")
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        btn_action = QPushButton()          # hidden by default; chunk 2 wires [Enable] here
        btn_action.clicked.connect(self._fire_action)
        btn_close = QPushButton("✕")
        btn_close.setFlat(True)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self.hide_banner)
        lay.addWidget(self.label, 1)
        lay.addWidget(btn_action)
        lay.addWidget(btn_close)
        self._btn_action = btn_action
        self.hide()                     # hidden by default (see main.py): the strip only exists while a message is shown

    def show_message(self, text, kind="warning", sticky=False, action_text=None, on_action=None):
        bg, border, fg = _COLORS.get(kind, _COLORS["warning"])
        self.setStyleSheet(
            f"Banner {{ background: {bg}; border: 1px solid {border}; }}"
            f"QLabel {{ color: {fg}; }}")
        self.label.setText(text)
        self._on_action = on_action
        if action_text:
            self._btn_action.setVisible(True)
            self._btn_action.setText(action_text)
        else:
            self._btn_action.setVisible(False)
        self.show()
        self.raise_()
        if sticky:
            self._timer.stop()
        else:
            self._timer.start(self.AUTOHIDE_MS)

    def hide_banner(self):
        self._timer.stop()
        self.hide()
        self._on_action = None

    def _fire_action(self):
        cb, self._on_action = self._on_action, None   # single-shot; a stale callback must not fire later
        if cb is not None:
            cb()


class ErrorOverlay(QDialog):
    """Modal, frameless popup centered over the main window for blocking failures."""

    def __init__(self, title, message, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 0, 16, 16)
        header = QLabel(title)
        header.setStyleSheet("background: #dc2626; color: white; padding: 8px 12px; font-weight: bold;")
        body = QLabel(message)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        btns = QHBoxLayout()
        btn_copy = QPushButton("Copy")
        btn_ok = QPushButton("OK")
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(message))
        btn_ok.clicked.connect(self.accept)
        btns.addStretch(1)
        btns.addWidget(btn_copy)
        btns.addWidget(btn_ok)
        lay.addWidget(header)
        lay.addWidget(body, 1)
        lay.addLayout(btns)

    def showEvent(self, event):
        parent = self.parent()
        if parent is not None:
            g = parent.frameGeometry()          # center over the main window, wherever it sits
            self.move(g.center() - self.rect().center())
        super().showEvent(event)


def popup_error(parent, title, message):
    """Convenience wrapper for blocking failures."""
    ErrorOverlay(title, message, parent).exec()
