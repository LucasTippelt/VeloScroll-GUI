"""Custom QTabBar that tints the tabs hosting unsaved values amber.

QTabBar cannot be styled per-tab via stylesheet (no :nth-child, no per-tab widget), so the bar paints
every tab itself through style().drawControl with a properly initialized option — selected/hover/pressed
states keep working under the active style. A dirty tab is drawn in three steps: its shape via
CE_TabBarTabShape (the normal border stays), then the interior filled flat with the amber unsaved-value
background, then the label via CE_TabBarTabLabel in the dark amber foreground (same pair as
config_tab.AMBER_STYLE, legible under light AND dark themes). The fill cannot come from a palette swap:
when QTabWidget hosts the bar, initStyleOption sets HasFrame and Fusion derives tab fills from
QPalette.Button through a lighter()/desaturation chain that washes amber out to beige; Window only feeds
the outline (which is why swapping it showed an amber border in dark mode and nothing in light)."""
from PySide6.QtGui import QColor, QPalette, QPainter
from PySide6.QtWidgets import QStyle, QStyleOptionTab, QTabBar

AMBER_BG = "#fef3c7"
AMBER_FG = "#1f2937"


class _DirtyTabBar(QTabBar):
    """A QTabBar whose tabs in `dirty` are filled with the amber unsaved-value background."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dirty = set()

    def set_dirty(self, indices):
        if indices != self._dirty:
            self._dirty = set(indices)
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        for i in range(self.count()):
            opt = QStyleOptionTab()
            self.initStyleOption(opt, i)
            if i in self._dirty:
                # shape with the normal palette (keeps the style's border), then a flat amber fill one
                # pixel inside it, then the label in the dark foreground — like the value-field tint
                self.style().drawControl(QStyle.CE_TabBarTabShape, opt, p)
                r = opt.rect
                p.fillRect(r.adjusted(1, 1, -1, -2), QColor(AMBER_BG))
                pal = opt.palette
                pal.setColor(QPalette.WindowText, QColor(AMBER_FG))
                opt.palette = pal
                self.style().drawControl(QStyle.CE_TabBarTabLabel, opt, p)
            else:
                self.style().drawControl(QStyle.CE_TabBarTab, opt, p)
        p.end()
