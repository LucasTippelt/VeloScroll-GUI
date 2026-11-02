"""Ramp tab: the ramp options + their live curves.

The option fields are the very _FieldRow editors ConfigTab creates for all 30 keys — this tab only
places its share (RAMP_KEYS) in section tables next to the curves, so there is exactly one editor per
key in the whole window and every edit lands in the shared edit buffer as if typed on the Status &
Config page. The tables have that page's layout minus the Description column: instead, the last-clicked
field's hint is shown at the bottom left, in the same row as Cancel/Apply — clicking anywhere in a row
(the Key / Effective value cells or its editor) selects it; it mirrors errors/fallback notes live. The
second section's band is renamed live to "2. Vertical ramp" while VERTICAL_H is true. The curves replot
~100 ms after any value change; Apply/Cancel below commit or discard it like on any other tab (all bars
are driven by the ConfigTab, which owns the buffer)."""
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea,
                                QVBoxLayout, QWidget)

from .apply_bar import ApplyBar
from .config_model import RAMP_KEYS
from .curves import CurvePlots

RAMP_SECTIONS = (   # display order on this tab; must stay in sync with config_model.RAMP_KEYS.
    # The second section's band is renamed live while VERTICAL_H is true ("2. Vertical ramp", see
    # _sync_band_title) — its name here is the default one shown otherwise.
    ("Vertical ramp", ("DEADZONE_IN", "FULL_IN", "MIN_RATE", "MAX_RATE", "CURVE", "OUT_SMOOTH")),
    ("Horizontal ramp", ("DEADZONE_IN_H", "FULL_IN_H", "MIN_RATE_H", "MAX_RATE_H",
                         "CURVE_H", "OUT_SMOOTH_H")),
)
assert tuple(k for _, keys in RAMP_SECTIONS for k in keys) == RAMP_KEYS


class RampTab(QWidget):
    def __init__(self, config_tab, watcher=None, parent=None):
        super().__init__()
        self.config_tab = config_tab

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        # no stylesheet: the palette's default text color (theme-safe black on light, white on dark) — a
        # forced gray was hard to read
        header = QLabel("Ramp options and their live curves — edits update the shared edit buffer; "
                        "nothing is applied until you press Apply.")
        root.addWidget(header)

        # Body grid: row 0 = "Vertical ramp" table | vertical graph,
        # row 1 = "Horizontal ramp" table | horizontal graph,
        # row 2 (column 1 only) = fallback note under the horizontal graph (hidden while empty),
        # rows 3-4 (spanning both columns) = separator + live position markers checkbox. Sharing the
        # first two rows is what pins the second section's table to exactly the same height as the
        # horizontal graph's top edge at any window size — a side-by-side HBox can't do that, because
        # where plot_h begins on the right is stretch-driven by everything above it.
        body = QGridLayout()
        self._body = body

        # the option fields live in section tables with the Status & Config page's layout minus the
        # Description column (the focused field's hint moves to the bottom bar row instead) — built by
        # the ConfigTab, which owns the shared _FieldRow widgets and keeps every effective-value cell
        # live through its refresh cycle
        self.tbl_v, _bands_v = config_tab.make_section_table((RAMP_SECTIONS[0],), description=False)
        self.tbl_h, bands_h = config_tab.make_section_table((RAMP_SECTIONS[1],), description=False)
        self._band_h = bands_h[RAMP_SECTIONS[1][0]]   # renamed live with VERTICAL_H (see _sync_band_title)

        self.curves = CurvePlots(config_tab.model, watcher=watcher)
        body.addWidget(self.tbl_v, 0, 0)
        body.addWidget(self.curves.top, 0, 1)
        body.addWidget(self.tbl_h, 1, 0)
        body.addWidget(self.curves.bottom, 1, 1)

        # the fallback note (created by CurvePlots) gets its own grid row directly under the horizontal
        # graph — column 1 only, so it never steals height from either graph and both graphs stay exactly
        # as tall as their tables; hidden while empty. Below it: the live position markers checkbox with a
        # separator above, spanning both columns so they sit under table AND graph.
        body.addWidget(self.curves.lbl_fallback, 2, 1)
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        body.addWidget(sep, 3, 0, 1, 2)
        body.addWidget(self.curves.chk_distance, 4, 0, 1, 2)
        body.setColumnStretch(0, 5)               # the old form:curves width ratio
        body.setColumnStretch(1, 7)
        body.setRowStretch(0, 1)                  # mirror the two plots' equal stretch
        body.setRowStretch(1, 1)

        container = QWidget()
        cl = QVBoxLayout(container)
        cl.addLayout(body)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        root.addWidget(scroll, 1)

        self.bar = ApplyBar(config_tab.apply, config_tab.cancel)
        config_tab.register_bar(self.bar)
        # the tables above have no Description column: the last-clicked field's hint (any cell of a row —
        # Key / Effective value via cellClicked, the editor via its FocusIn) sits at the bottom left
        # instead, in the same row as Cancel/Apply. The bar keeps its own sizeHint on the right; the label
        # takes the rest of the width.
        bottom = QHBoxLayout()
        self.lbl_desc = QLabel("")    # no stylesheet: palette default text color (theme-safe), red only on error
        bottom.addWidget(self.lbl_desc, 1)
        bottom.addWidget(self.bar)
        root.addLayout(bottom)

        # editor widget -> its _FieldRow for every ramp key; a filter on each editor itself catches its
        # FocusIn (Qt object filters do not see descendant events through the parent chain, so one per
        # editor — clicking/Tabbing into any of them updates lbl_desc with that field's hint)
        self._focus_rows = {}
        for _, keys in RAMP_SECTIONS:
            for k in keys:
                row = config_tab.rows[k]
                self._focus_rows[id(row.editor)] = row
                row.editor.installEventFilter(self)
        # clicking the Key / Effective value cells (plain items — they never focus an editor, and the table
        # has NoSelection) must update lbl_desc too: map each table's rows to their keys via cellClicked
        for tbl, (_, keys) in ((self.tbl_v, RAMP_SECTIONS[0]), (self.tbl_h, RAMP_SECTIONS[1])):
            row_to_key = {i + 1: k for i, k in enumerate(keys)}    # row 0 is the section band — no key
            tbl.cellClicked.connect(lambda r, _c, m=row_to_key: self._on_cell_clicked(m.get(r)))
        self._focused_row = None

        # the ramp rows were created parentless in ConfigTab's init — sync their editors from the buffer now
        config_tab.reset_rows()

        # VERTICAL_H renames the second section live (it then emits vertical wheel events, not horizontal);
        # refresh() re-syncs as well so buffer resets that don't emit changed (Cancel/Apply) catch up too.
        # The bottom hint follows both: edits change hints via set_state (changed signal), and Apply/Cancel
        # /disk reloads are picked up by the 2 s refresh cycle.
        config_tab.model.changed.connect(self._sync_band_title)
        config_tab.model.changed.connect(self._sync_desc_label)
        self._sync_band_title()

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.FocusIn and id(obj) in self._focus_rows:
            self._focused_row = self._focus_rows[id(obj)]
            self._sync_desc_label()
        return super().eventFilter(obj, ev)

    def _on_cell_clicked(self, key):
        """cellClicked for the Key / Effective value cells (band rows map to None and are ignored)."""
        if key is not None:
            self._focused_row = self.config_tab.rows[key]
            self._sync_desc_label()

    def _sync_desc_label(self):
        """Mirror the focused field's hint — static description, unset-fallback note, or error text.
        Normal hints use the palette's default text color (theme-safe black/white) instead of the table's
        muted gray; only errors keep their red."""
        if self._focused_row is None:
            return
        ss = self._focused_row.hint.styleSheet()
        self.lbl_desc.setText(self._focused_row.hint.text())
        self.lbl_desc.setStyleSheet(ss if "#dc2626" in ss else "")

    def _sync_band_title(self):
        """VERTICAL_H true: the second ramp is a second VERTICAL one — rename its band accordingly."""
        vh = bool(self.config_tab.model.values.get("VERTICAL_H", False))
        text = "2. Vertical ramp" if vh else RAMP_SECTIONS[1][0]
        if self._band_h.text() != text:
            self._band_h.setText(text)

    def distance_markers_enabled(self):
        """The opt-out checkbox for DISTANCE_FILE (checked by default) — read once at startup to decide
        whether the GUI forces that key on; it is always forced off again at exit."""
        return self.curves.chk_distance.isChecked()

    def refresh(self):
        """Main window's 2 s cycle: re-plot both curves from the edit buffer, re-sync the band title and
        the focused field's bottom hint (catches Apply/Cancel/disk-reload resets that don't emit changed)."""
        self.curves.refresh()
        self._sync_band_title()
        self._sync_desc_label()
