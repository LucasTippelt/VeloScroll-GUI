"""Ramp tab (split out of chunk 3's old Preview tab): the ramp options + their live curves.

The option fields are the very _FieldRow editors ConfigTab creates for all 30 keys — this tab only
places its share (RAMP_KEYS) in a form next to the curves, so there is exactly one editor per key in
the whole window and every edit lands in the shared edit buffer as if typed on the Status & Config
page. The curves replot ~100 ms after any value change; Apply/Cancel below commit or discard it like
on any other tab (all bars are driven by the ConfigTab, which owns the buffer)."""
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea,
                               QVBoxLayout, QWidget)

from .apply_bar import ApplyBar
from .config_model import RAMP_KEYS
from .curves import CurvePlots

RAMP_SECTIONS = (   # display order on this tab; must stay in sync with config_model.RAMP_KEYS
    ("Vertical ramp", ("DPI", "DEADZONE_IN", "FULL_IN", "MIN_RATE", "MAX_RATE", "CURVE", "OUT_SMOOTH")),
    ("Horizontal overrides", ("DEADZONE_IN_H", "FULL_IN_H", "MIN_RATE_H", "MAX_RATE_H",
                              "CURVE_H", "OUT_SMOOTH_H")),
)
assert tuple(k for _, keys in RAMP_SECTIONS for k in keys) == RAMP_KEYS


class RampTab(QWidget):
    def __init__(self, config_tab, watcher=None, parent=None):
        super().__init__(parent)
        self.config_tab = config_tab

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        header = QLabel("Ramp options and their live curves — edits update the shared edit buffer; "
                        "nothing is applied until you press Apply.")
        header.setStyleSheet("color: gray;")
        root.addWidget(header)

        body = QHBoxLayout()

        form = QWidget()
        self._grid = QGridLayout(form)
        grid = self._grid
        r = 0
        for name, keys in RAMP_SECTIONS:
            sec = QLabel(name)
            f = sec.font(); f.setBold(True); sec.setFont(f)
            # explicit dark-on-light colors (not a bare lightGray background): under a dark theme the
            # palette's white text on forced lightGray is unreadable; this pair stays legible in both.
            # Tight padding keeps the band compact instead of a big washed-out block.
            sec.setStyleSheet("background: #d1d5db; color: #1f2937; padding: 1px 6px;")
            grid.addWidget(sec, r, 0, 1, 4)        # spans the trailing spacer column too, so the band reaches the edge
            r += 1
            for key in keys:
                row = config_tab.rows[key]       # ConfigTab owns the editor + hint pair (see _FieldRow there)
                k = QLabel(key)
                grid.addWidget(k, r, 0)
                grid.addWidget(row.editor, r, 1)
                grid.addWidget(row.hint, r, 2)
                r += 1
        grid.setColumnMinimumWidth(0, 140)
        # no stretch on the field columns: editors and hints keep a consistent width instead of growing
        # with the window — an empty trailing column absorbs all extra space (left-aligned form).
        grid.setColumnStretch(3, 1)

        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(form)
        body.addWidget(scroll, 5)

        self.curves = CurvePlots(config_tab.model, watcher=watcher)
        body.addWidget(self.curves, 7)
        root.addLayout(body, 1)

        self.bar = ApplyBar(config_tab.apply, config_tab.cancel)
        config_tab.register_bar(self.bar)
        root.addWidget(self.bar)

        # the ramp rows were created parentless in ConfigTab's init — sync their editors from the buffer now
        config_tab.reset_rows()

    def distance_markers_enabled(self):
        """The opt-out checkbox for DISTANCE_FILE (checked by default) — read once at startup to decide
        whether the GUI forces that key on; it is always forced off again at exit."""
        return self.curves.chk_distance.isChecked()

    def refresh(self):
        """Main window's 2 s cycle: re-plot both curves from the edit buffer."""
        self.curves.refresh()
