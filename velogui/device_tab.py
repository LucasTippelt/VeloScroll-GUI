"""Device Picker tab (chunk 3) + the device & button fields moved here from Status/Config.

The fields on the left are the very _FieldRow editors ConfigTab creates for DEVICE_KEYS — one editor
per key in the whole window, exactly like the Ramp tab does with RAMP_KEYS: every edit lands in the
shared edit buffer as if typed anywhere else. They live in a section table with the same layout as the
Status & Config page's main table (Key | Effective value | Edit | Description, uniform 34 px rows). The
three button rows carry their chunk-4 [Capture…] buttons in-cell (press the physical button, click its
code in the dialog). Selecting a device on the right writes DEVICE_MATCH immediately; Apply/Cancel below
commit or discard everything on this page like any other tab (all bars are driven by the ConfigTab,
which owns the buffer)."""
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QVBoxLayout, QWidget)

from .apply_bar import ApplyBar
from .config_model import DEVICE_KEYS
from .devices import DevicePicker

DEVICE_SECTIONS = (   # must stay in sync with config_model.DEVICE_KEYS
    ("Device & buttons", DEVICE_KEYS),
)
assert tuple(k for _, keys in DEVICE_SECTIONS for k in keys) == DEVICE_KEYS


class DeviceTab(QWidget):
    def __init__(self, watcher, config_tab, parent=None):
        super().__init__()
        self.config_tab = config_tab

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        # no stylesheet: palette default text color (theme-safe black on light, white on dark)
        header = QLabel("Select the mouse veloscroll should grab (writes DEVICE_MATCH into the shared "
                        "edit buffer) and assign its buttons — press Apply to make anything stick.")
        root.addWidget(header)

        body = QHBoxLayout()

        # the device & button fields in a section table with the same layout as the Status & Config
        # page's main table (Key | Effective value | Edit | Description, 34 px rows — uniform padding
        # for every row, buttons included); built by the ConfigTab, which owns the shared _FieldRow
        # widgets and keeps the effective-value cells live through its refresh cycle. The button rows'
        # [Capture…] buttons sit in their Edit cell like any other editor.
        self.tbl_device, _bands = config_tab.make_section_table(DEVICE_SECTIONS)
        # the table's fixed columns (Key/Effective/Edit ≈ 720 px) need most of the row — give it the wider
        # share so its Description column keeps real width instead of being squeezed by the picker side
        body.addWidget(self.tbl_device, 6)

        self.picker = DevicePicker(config_tab.model, watcher, config_tab.set_key_raw)
        body.addWidget(self.picker, 4)
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
