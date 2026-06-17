from __future__ import annotations
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor, QBrush

from src.mixins import mixin_target
from src.ui.page import SubPageFramework
from src.ui.widget import WidgetFramework
from src.ui.widgets.tile_grid import TileGrid
from src.ui.controls.drawer import Drawer
from src.ui.controls.buttons import IconButton
from src.ui.icons import Icons
from src.styling import COLORS

if TYPE_CHECKING:
    from src.main import Client


class _GridBackground(QWidget):
    """Dot-grid background matching settings/root pages."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(QColor(255, 255, 255, 18)))
        p.setPen(Qt.GlobalColor.transparent)
        s, r = 32, 1
        for x in range(s, self.width(), s):
            for y in range(s, self.height(), s):
                p.drawEllipse(x - r, y - r, r * 2, r * 2)


class SubTilesPage(SubPageFramework):

    @mixin_target("sub.tiles.__init__")
    def __init__(self, client: "Client", page):
        super().__init__(client=client, key="tiles", coord=(1, 0))
        self.page_ = page

        w = int(client.SETTINGS.application.window.size.value[0])
        h = int(client.SETTINGS.application.window.size.value[1])
        self.setFixedSize(w, h)
        self.setStyleSheet(f"background-color: {COLORS.DARK.BGDARK};")

        # ── Dot grid background ───────────────────────────────────────────────
        self._grid_bg = _GridBackground(self)
        self._grid_bg.setGeometry(0, 0, w, h)

        # ── Tile grid ─────────────────────────────────────────────────────────
        margin = int(client.SETTINGS.home.widget_margin.value)
        # plugin is injected by CoreWidgetsBundle mixin after __init__
        self.tile_grid = TileGrid(client, cols=16, rows=10)
        self.tile_grid.setParent(self)
        self.tile_grid.setGeometry(0, 0, w, h)
        self.tile_grid.show()

        # ── Widget manager (for anchored overlay widgets) ─────────────────────
        self.widget_manager = WidgetFramework(
            client   = client,
            page_key = "#tiles",
            padding  = margin,
        )
        self.widget_manager.setParent(self)
        self.widget_manager.setGeometry(0, 0, w, h)
        self.widget_manager.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.widget_manager.show()

        # ── Drawer ────────────────────────────────────────────────────────────
        self.drawer = Drawer(client, position="bottom")
        self.drawer.setParent(self)
        self.drawer.place_on_page()

        self.drawer.add_controls([
            IconButton(Icons.SETTINGS,   lambda: client.goto("#settings")),
            IconButton(Icons.FULLSCREEN, client.toggle_fullscreen),
            IconButton(Icons.CLOSE,      client.stop),
        ])

        # Z-order: grid_bg < tile_grid < widget_manager < drawer
        self._grid_bg.lower()
        self.widget_manager.raise_()
        self.drawer.raise_()

        # ── Features ──────────────────────────────────────────────────────────
        self.add_features({
            "add_widgets":            self.widget_manager.add,
            "remove_widget":          self.widget_manager.remove,
            "add_drawer_controls":    self.drawer.insert_controls,
            "remove_drawer_controls": self.drawer.remove_controls,
            "add_tile":               self.tile_grid.add_tile,
            "remove_tile":            self.tile_grid.remove_tile,
            "get_tile":               self.tile_grid.get_tile,
            "tile_grid":              self.tile_grid,
            "set_tile_plugin":        self._set_tile_plugin,
        })

    def _set_tile_plugin(self, plugin) -> None:
        """Called by CoreWidgetsBundle after mixin fires to wire persistence."""
        self.tile_grid._plugin = plugin
        # Now that plugin is set, tell the grid how tall the drawer handle is
        # so top/bottom padding match
        self.tile_grid.set_drawer_height(self.drawer.handle.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        self._grid_bg.setGeometry(0, 0, w, h)
        self.tile_grid.setGeometry(0, 0, w, h)
        self.widget_manager.setGeometry(0, 0, w, h)
        self.widget_manager.update_geometry()
        self.drawer.apply_parent_width()

    def tick(self) -> None:
        self.widget_manager.tick_widgets()
        self.drawer.tick()