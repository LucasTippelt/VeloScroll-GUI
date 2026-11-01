"""Per-tab Apply/Cancel bar over the shared edit buffer.

Every tab page (Status & Config / Scroll/Invert / Ramp / Device Picker) carries its own bar so a change can be
committed or discarded without switching pages. All bars are registered with the ConfigTab, which
owns the buffer and drives every bar's error text, dirty mark, and busy state from one place."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget


class ApplyBar(QWidget):
    def __init__(self, on_apply, on_cancel, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.lbl_error = QLabel("")
        self.lbl_error.setStyleSheet("color: #dc2626;")
        self.lbl_error.setWordWrap(True)
        self.lbl_dirty = QLabel("")    # no stylesheet: palette default text color (theme-safe)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_apply = QPushButton("Apply")
        self.btn_apply.setDefault(True)
        self.btn_cancel.clicked.connect(on_cancel)
        self.btn_apply.clicked.connect(on_apply)
        lay.addWidget(self.lbl_error, 1)
        lay.addWidget(self.lbl_dirty, 0, Qt.AlignVCenter)
        lay.addStretch(0)
        lay.addWidget(self.btn_cancel)
        lay.addWidget(self.btn_apply)

    def set_state(self, error="", dirty=False):
        """Shared validation state — the buffer is one, so every bar shows the same text."""
        self.lbl_error.setText(error)
        self.lbl_dirty.setText("unsaved changes" if dirty else "")

    def set_busy(self, busy, label=""):
        self.btn_apply.setEnabled(not busy)
        self.btn_cancel.setEnabled(not busy)
        self.btn_apply.setText((label or "Working…") if busy else "Apply")
