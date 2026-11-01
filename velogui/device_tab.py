"""Device Picker tab (chunk 3) + the device & button fields moved here from Status/Config.

The form on the left is the very _FieldRow editors ConfigTab creates for DEVICE_KEYS — one editor
per key in the whole window, exactly like the Ramp tab does with RAMP_KEYS: every edit lands in
the shared edit buffer as if typed anywhere else. The three button rows carry their chunk-4
[Capture…] buttons (press the physical button, click its code in the dialog). Selecting a device
on the right writes DEVICE_MATCH immediately; Apply/Cancel below commit or discard everything on
this page like any other tab (all bars are driven by the ConfigTab, which owns the buffer)."""
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea,
                               QVBoxLayout, QWidget)

from .apply_bar import ApplyBar
from .config_model import DEVICE_KEYS
from .devices import DevicePicker

DEVICE_SECTIONS = (   # must stay in sync with config_model.DEVICE_KEYS
    ("Device & buttons", DEVICE_KEYS),
)
assert tuple(k for _, keys in DEVICE_SECTIONS for k in keys) == DEVICE_KEYS


class DeviceTab(QWidget):
    def __init__(self, watcher, config_tab, parent=None):
        super().__init__(parent)
        self.config_tab = config_tab

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        header = QLabel("Select the mouse veloscroll should grab (writes DEVICE_MATCH into the shared "
                        "edit buffer) and assign its buttons — press Apply to make anything stick.")
        header.setStyleSheet("color: gray;")
        root.addWidget(header)

        body = QHBoxLayout()

        form = QWidget()
        grid = QGridLayout(form)
        r = 0
        for name, keys in DEVICE_SECTIONS:
            sec = QLabel(name)
            f = sec.font(); f.setBold(True); sec.setFont(f)
            # explicit colors (not a bare lightGray background): readable under dark themes too
            sec.setStyleSheet("background: #d1d5db; color: #1f2937; padding: 1px 6px;")
            grid.addWidget(sec, r, 0, 1, 3)
            r += 1
            for key in keys:
                row = config_tab.rows[key]       # ConfigTab owns the editor (+ Capture button) + hint pair
                k = QLabel(key)
                grid.addWidget(k, r, 0)
                grid.addWidget(row.container, r, 1)
                grid.addWidget(row.hint, r, 2)
                r += 1
        grid.setColumnMinimumWidth(0, 140)
        grid.setColumnStretch(1, 3)
        grid.setColumnStretch(2, 4)

        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(form)
        body.addWidget(scroll, 5)

        self.picker = DevicePicker(config_tab.model, watcher, config_tab.set_key_raw)
        body.addWidget(self.picker, 7)
        root.addLayout(body, 1)

        self.bar = ApplyBar(config_tab.apply, config_tab.cancel)
        config_tab.register_bar(self.bar)
        root.addWidget(self.bar)

        # the device rows were created parentless in ConfigTab's init — sync their editors from the buffer now
        config_tab.reset_rows()

    def refresh(self):
        """Main window's 2 s cycle: resync the selection and re-mark the grabbed device."""
        self.picker.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_scanned", False):       # first paint: enumerate the devices once
            self._scanned = True
            self.picker.rescan()
