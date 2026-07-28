from __future__ import annotations
from typing import TYPE_CHECKING, Type

from PyQt6.QtWidgets import QWidget, QPushButton
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QPoint, QTimer
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen

import qtawesome as qta

from src.mixins import mixin_target
from src.ui.page import SubPageFramework
from src.ui.widgets.tile import Tile
from src.ui.widgets.tile_grid import TileGrid
from src.ui.widgets.tile_panel import TilePanel
from src.ui.icons import Icons
from src.styling import set_style

if TYPE_CHECKING:
    from src.main import Client


##BACKGROUND

class GridBackground(QWidget):

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


##TRASH BIN

class TrashBin(QWidget):

    SIZE = 72

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.hot = False
        self.hide()

        self.anim = QPropertyAnimation(self, b"pos")
        self.anim.setDuration(180)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def show_for_drag(self) -> None:
        pw = self.parent().width()
        ph = self.parent().height()
        x  = (pw - self.SIZE) // 2
        self.move(x, ph)
        self.show()
        self.raise_()
        self.anim.stop()
        self.anim.setStartValue(self.pos())
        self.anim.setEndValue(QPoint(x, ph - self.SIZE - 60))
        self.anim.start()

    def hide_after_drag(self) -> None:
        pw = self.parent().width()
        ph = self.parent().height()
        x  = (pw - self.SIZE) // 2
        self.anim.stop()
        self.anim.setStartValue(self.pos())
        self.anim.setEndValue(QPoint(x, ph))
        def _finish():
            self.hide()
            try: self.anim.finished.disconnect()
            except Exception: pass
        self.anim.finished.connect(_finish)
        self.anim.start()
        self.hot = False
        self.update()

    def is_over(self, global_pos: QPoint) -> bool:
        local = self.parent().mapFromGlobal(global_pos)
        return self.geometry().contains(local)

    def set_hot(self, hot: bool) -> None:
        if hot != self.hot:
            self.hot = hot
            self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg     = QColor(180, 40, 40, 200) if self.hot else QColor(60, 20, 20, 160)
        border = QColor(220, 80, 80, 200) if self.hot else QColor(120, 40, 40, 140)
        p.setBrush(QBrush(bg))
        p.setPen(QPen(border, 1.5))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 12, 12)
        try:
            import qtawesome as qta
            color = "rgba(255,180,180,255)" if self.hot else "rgba(255,120,120,180)"
            pix   = qta.icon("mdi.trash-can-outline", color=color).pixmap(36, 36)
            p.drawPixmap((self.SIZE - 36) // 2, (self.SIZE - 36) // 2, pix)
        except Exception:
            pass


class SubTilesPage(SubPageFramework):

    @mixin_target("sub.tiles.__init__")
    def __init__(self, client: "Client", page):
        super().__init__(client=client, key="tiles", coord=(1, 0))
        self.page_    = page
        self.registry: dict[str, Tile] = {}   #{key: tile_instance}

        w = int(client.SETTINGS.application.window.size.value[0])
        h = int(client.SETTINGS.application.window.size.value[1])
        self.setFixedSize(w, h)
        set_style(self, "common", "page-background")

        ## -- BACKGROUND

        self.grid_bg = GridBackground(self)
        self.grid_bg.setGeometry(0, 0, w, h)

        ## -- TILE GRID

        margin = int(client.SETTINGS.home.widget_margin.value)
        self.tile_grid = TileGrid(client, cols=16, rows=10)
        self.tile_grid.setParent(self)
        self.tile_grid.setGeometry(0, 0, w, h)
        self.tile_grid.show()

        ## -- TILE PANEL

        self.tile_panel = TilePanel(client, self, self.tile_grid)
        self.tile_panel.hide()

        ## -- TRASH BIN

        self.trash_bin = TrashBin(self)

        self.panel_btn = QPushButton()
        self.panel_btn.setFixedSize(40, 40)
        self.panel_btn.setIcon(qta.icon("mdi.view-grid-plus", color="rgba(255,255,255,120)"))
        self.panel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.panel_btn.setParent(self)
        set_style(self.panel_btn, "sub_tiles", "tiles-panel-button")
        self.panel_btn.clicked.connect(self.tile_panel.toggle)
        self.panel_btn.move(w - 56, 16)
        self.panel_btn.show()

        ## -- Z-ORDER

        self.grid_bg.lower()
        self.panel_btn.raise_()
        self.tile_panel.raise_()

        ## -- FEATURES

        self.add_features({
            "register_tile":          self.register_tile,
            "return_tile_to_panel":   self.return_tile_to_panel,
            "add_tile":               self.tile_grid.add_tile,
            "remove_tile":            self.tile_grid.remove_tile,
            "get_tile":               self.tile_grid.get_tile,
            "tile_grid":              self.tile_grid,
        })

        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self.tick)
        self.tick_timer.start(1000)   #once per second, same cadence as DateTimeWidget etc.

    def return_tile_to_panel(self, tile: Tile) -> None:
        """Called by the grid when a tile's delete handle is pressed."""
        self.tile_panel.add_tile(tile)
        try:
            self.client.simple_notify("view_module", "Tiles",
                                      f"'{tile.NAME or tile.KEY}' moved to the tile panel.")
        except Exception:
            pass

    def register_tile(self, tile_class: type[Tile], *args,
                      in_grid: bool = False, col: int = 0, row: int = 0,
                      **kwargs) -> Tile:
        if not tile_class.KEY:
            raise ValueError("Tile.KEY must be set before registering")
        if not tile_class.NAME:
            raise ValueError(f"Tile '{tile_class.KEY}' must have NAME set")

        tile = tile_class(self.client, *args, **kwargs)

        self.registry[tile.KEY] = tile

        saved = self.tile_grid.load_positions()
        if tile.KEY in saved:
            entry = saved[tile.KEY] or {}
            span_w, span_h = entry.get("w"), entry.get("h")
            if span_w and span_h:
                # Applied before placing, so the grid reserves the cells the
                # tile actually occupies rather than its default size.
                tile.apply_span(int(span_w), int(span_h), force=True)
            self.tile_grid.add_tile(tile, col, row)
        elif in_grid:
            self.tile_grid.add_tile(tile, col, row)
        else:
            self.tile_panel.add_tile(tile)

        return tile

    ##DRAG NOTIFICATIONS

    def notify_drag_started(self) -> None:
        self.trash_bin.show_for_drag()

    def notify_drag_ended(self, global_pos, tile) -> None:
        if self.trash_bin.is_over(global_pos):
            self.tile_grid.remove_tile(tile.KEY)
            self.tile_panel.add_tile(tile)
        self.trash_bin.hide_after_drag()

    def receive_tile_from_panel(self, tile, global_pos) -> None:
        self.trash_bin.hide_after_drag()

    ##TICK

    def tick(self) -> None:
        self.tile_grid.tick()

    ##RESIZE

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        self.grid_bg.setGeometry(0, 0, w, h)
        self.tile_grid.setGeometry(0, 0, w, h)
        self.panel_btn.move(w - 56, 16)
        if self.tile_panel.open:
            self.tile_panel.setGeometry(w - TilePanel.WIDTH, 0, TilePanel.WIDTH, h)
        else:
            self.tile_panel.setGeometry(w, 0, TilePanel.WIDTH, h)