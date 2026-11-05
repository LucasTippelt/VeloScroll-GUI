"""Scroll/Invert tab: the Feel and Scroll mode sections moved here from Status & Config.

The fields are the very _FieldRow editors ConfigTab creates for every key — this tab only places its
share (SCROLL_INVERT_KEYS) in section tables with the same layout as the Status & Config page's main
table (Key | Effective value | Edit | Description, uniform 34 px rows), so there is exactly one editor
per key in the whole window and every edit lands in the shared edit buffer as if typed anywhere else.
Apply/Cancel below commit or discard it like on any other tab (all bars are driven by the ConfigTab,
which owns the buffer)."""
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .apply_bar import ApplyBar
from .config_model import SCROLL_INVERT_KEYS

SCROLL_SECTIONS = (   # must stay in sync with config_model.SCROLL_INVERT_KEYS
    ("Feel", ("INVERT", "INVERT_H")),
    ("Scroll mode", ("TOGGLE_SCROLL", "TOGGLE_SCROLL_H", "VERTICAL_H", "LEFT_CLICK_RESET")),
)
assert tuple(k for _, keys in SCROLL_SECTIONS for k in keys) == SCROLL_INVERT_KEYS


class ScrollTab(QWidget):
    def __init__(self, config_tab, parent=None):
        super().__init__()
        self.config_tab = config_tab

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        # no stylesheet: palette default text color (theme-safe black on light, white on dark)
        header = QLabel("Scroll direction and engagement mode — edits update the shared edit buffer; "
                        "nothing is applied until you press Apply.")
        root.addWidget(header)

        # the feel & scroll-mode fields in section tables with the same layout as the Status & Config
        # page's main table (Key | Effective value | Edit | Description, 34 px rows); built by the
        # ConfigTab, which owns the shared _FieldRow widgets and keeps the effective-value cells live
        # through its refresh cycle
        self.tbl_scroll, _bands = config_tab.make_section_table(SCROLL_SECTIONS)
        root.addWidget(self.tbl_scroll, 1)

        self.bar = ApplyBar(config_tab.apply, config_tab.cancel)
        config_tab.register_bar(self.bar)
        root.addWidget(self.bar)

        # the scroll rows were created parentless in ConfigTab's init — sync their editors from the buffer now
        config_tab.reset_rows()

    def refresh(self):
        """Main window's 2 s cycle: no live widgets of its own — the effective-value cells are already
        re-rendered by ConfigTab.refresh (this tab's table joined its _eff_items at build time)."""
