from __future__ import annotations
import json
from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QRect, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush

from src.ui.widgets.tile import Tile

if TYPE_CHECKING:
    from src.main import Client


##TILE GRID

class TileGrid(QWidget):

    def __init__(self, client: "Client", cols: int = 16, rows: int = 10):
        super().__init__()
        self.client  = client
        self.cols    = cols
        self.rows    = rows

        self.owning_plugin_key = "corewidgetsbundle"

        self.tiles: list[Tile] = []

        self.cell_size = 0
        self.gap_x     = 0   #horizontal spacing between cells
        self.gap_y     = 0   #vertical spacing between cells
        self.margin    = 0
        self.origin_x  = 0
        self.origin_y  = 0
        self.drawer_h  = 0   #reserved space at the bottom for the drawer handle

        self.dragging_tile: Optional[Tile] = None
        self.hover_col:     int = -1
        self.hover_row:     int = -1

        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


    def get_owning_plugin(self):
        try:
            return self.client.PLUGIN.plugins.get(self.owning_plugin_key)
        except Exception:
            return None

    def load_positions(self) -> dict:
        plugin = self.get_owning_plugin()
        if not plugin:
            return {}
        try:
            raw = plugin.settings.tiles.positions.value
            return json.loads(raw) if raw and raw != "{}" else {}
        except Exception:
            return {}

    def save_positions(self) -> None:
        plugin = self.get_owning_plugin()
        if not plugin:
            return

        positions = {t.KEY: {"col": t.grid_col, "row": t.grid_row} for t in self.tiles}

        try:
            plugin.settings.tiles.positions["value"] = json.dumps(positions)

            settings_path = plugin.config["settings"]["path"]
            with open(settings_path, "w") as f:
                json.dump(plugin.settings.to_dict(), f, indent=4)
        except Exception as e:
            self.client.log("error", f"[TileGrid] Failed to save tile positions: {e}", include_traceback=True)

    ##LAYOUT

    def set_drawer_height(self, h: int) -> None:
        self.drawer_h = h

    def recalculate(self) -> None:
        margin = int(self.client.SETTINGS.home.widget_margin.value)
        self.margin = margin

        #minimum gap before any stretching — keeps cells from touching
        base_gap = max(6, margin // 4)

        bottom_reserve = max(self.drawer_h, margin)
        available_w    = self.width()  - margin * 2
        available_h    = self.height() - margin - bottom_reserve

        #square cells: whichever axis is tighter wins the cell size
        cell_from_w = (available_w - base_gap * (self.cols - 1)) / self.cols
        cell_from_h = (available_h - base_gap * (self.rows - 1)) / self.rows
        self.cell_size = int(min(cell_from_w, cell_from_h))

        grid_w = self.cell_size * self.cols + base_gap * (self.cols - 1)
        grid_h = self.cell_size * self.rows + base_gap * (self.rows - 1)

        leftover_w = available_w - grid_w
        leftover_h = available_h - grid_h

        if leftover_w > 0 and self.cols > 1:
            self.gap_x = base_gap + leftover_w / (self.cols - 1)
        else:
            self.gap_x = base_gap

        if leftover_h > 0 and self.rows > 1:
            self.gap_y = base_gap + leftover_h / (self.rows - 1)
        else:
            self.gap_y = base_gap

        self.origin_x = margin
        self.origin_y = margin

    def cell_rect(self, col: int, row: int, span_w: int = 1, span_h: int = 1) -> QRect:
        x = self.origin_x + col * (self.cell_size + self.gap_x)
        y = self.origin_y + row * (self.cell_size + self.gap_y)
        w = span_w * self.cell_size + (span_w - 1) * self.gap_x
        h = span_h * self.cell_size + (span_h - 1) * self.gap_y
        return QRect(int(x), int(y), int(w), int(h))

    def place_tile(self, tile: Tile, col: int, row: int, animate: bool = False) -> None:
        rect = self.cell_rect(col, row, tile.grid_w, tile.grid_h)
        tile.grid_col = col
        tile.grid_row = row
        tile.resize(rect.width(), rect.height())
        if animate:
            anim = QPropertyAnimation(tile, b"pos")
            anim.setDuration(180)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.setStartValue(tile.pos())
            anim.setEndValue(rect.topLeft())
            anim.finished.connect(self.save_positions)
            anim.start()
            tile.snap_anim = anim   #keep a reference so it isn't garbage collected mid-flight
        else:
            tile.move(rect.topLeft())
            self.save_positions()


    def add_tile(self, tile: Tile, col: int = 0, row: int = 0) -> None:
        if not tile.KEY:
            raise ValueError("Tile must have a non-empty KEY")
        if any(t.KEY == tile.KEY for t in self.tiles):
            raise ValueError(f"Tile key '{tile.KEY}' already registered in this grid")

        positions = self.load_positions()
        if tile.KEY in positions:
            saved = positions[tile.KEY]
            col   = int(saved.get("col", col))
            row   = int(saved.get("row", row))

        col = max(0, min(col, self.cols - tile.grid_w))
        row = max(0, min(row, self.rows - tile.grid_h))

        tile.setParent(self)
        tile.move_requested.connect(self.on_tile_move_requested)
        self.tiles.append(tile)

        if self.cell_size > 0:
            #grid has already been laid out at least once — place immediately
            self.place_tile(tile, col, row)
        else:
            tile.grid_col = col
            tile.grid_row = row

        tile.show()

    def remove_tile(self, key: str) -> None:
        found = [t for t in self.tiles if t.KEY == key]
        for tile in found:
            tile.setParent(None)
            self.tiles.remove(tile)
        if found:
            self.save_positions()

    def get_tile(self, key: str) -> Optional[Tile]:
        found = [t for t in self.tiles if t.KEY == key]
        return found[0] if found else None

    def tick(self) -> None:
        for tile in self.tiles:
            tile.tick()


    def on_tile_move_requested(self, tile: Tile, col: int, row: int) -> None:
        col = max(0, min(col, self.cols - tile.grid_w))
        row = max(0, min(row, self.rows - tile.grid_h))
        self.hover_col     = col
        self.hover_row     = row
        self.dragging_tile = tile
        self.update()   #trigger a repaint so the guide box moves

    def snap_tile(self, tile: Tile) -> None:
        if self.hover_col >= 0 and self.hover_row >= 0:
            col = max(0, min(self.hover_col, self.cols - tile.grid_w))
            row = max(0, min(self.hover_row, self.rows - tile.grid_h))
        else:
            if self.cell_size > 0:
                col = round((tile.x() - self.origin_x) / (self.cell_size + self.gap_x))
                row = round((tile.y() - self.origin_y) / (self.cell_size + self.gap_y))
                col = max(0, min(col, self.cols - tile.grid_w))
                row = max(0, min(row, self.rows - tile.grid_h))
            else:
                col, row = tile.grid_col, tile.grid_row

        self.place_tile(tile, col, row, animate=True)
        self.dragging_tile = None
        self.hover_col     = -1
        self.hover_row     = -1
        self.update()

    def mousePressEvent(self, event) -> None:
        event.ignore()

    def mouseMoveEvent(self, event) -> None:
        if self.dragging_tile is None:
            event.ignore()
            return
        col = int((event.position().x() - self.origin_x) // (self.cell_size + self.gap_x))
        row = int((event.position().y() - self.origin_y) // (self.cell_size + self.gap_y))
        col = max(0, min(col, self.cols - self.dragging_tile.grid_w))
        row = max(0, min(row, self.rows - self.dragging_tile.grid_h))
        self.hover_col = col
        self.hover_row = row
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self.dragging_tile:
            tile = self.dragging_tile
            col  = max(0, min(self.hover_col, self.cols - tile.grid_w))
            row  = max(0, min(self.hover_row, self.rows - tile.grid_h))
            self.place_tile(tile, col, row, animate=True)
            self.dragging_tile = None
            self.hover_col     = -1
            self.hover_row     = -1
            self.update()
        else:
            event.ignore()

    ##PAINTING

    def paintEvent(self, event) -> None:
        if self.cell_size <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        #faint dots marking every cell corner across the whole grid
        p.setBrush(QBrush(QColor(255, 255, 255, 22)))
        p.setPen(Qt.GlobalColor.transparent)
        r = 2
        for col in range(self.cols + 1):
            for row in range(self.rows + 1):
                x = self.origin_x + col * (self.cell_size + self.gap_x) - self.gap_x / 2
                y = self.origin_y + row * (self.cell_size + self.gap_y) - self.gap_y / 2
                p.drawEllipse(int(x - r), int(y - r), r * 2, r * 2)

        if self.dragging_tile and self.hover_col >= 0:
            drop_rect = self.cell_rect(
                self.hover_col, self.hover_row,
                self.dragging_tile.grid_w,
                self.dragging_tile.grid_h,
            )
            p.setBrush(QBrush(QColor(255, 255, 255, 20)))
            p.setPen(QPen(QColor(255, 255, 255, 60), 1.5))
            p.drawRoundedRect(drop_rect, 10, 10)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.recalculate()
        for tile in self.tiles:
            self.place_tile(tile, tile.grid_col, tile.grid_row)
        self.update()