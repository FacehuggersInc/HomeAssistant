from __future__ import annotations
import json
from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QPoint, QRect, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush

from src.ui.widgets.tile import Tile

if TYPE_CHECKING:
    from src.main import Client


class TileGrid(QWidget):
    """
    A grid container for Tile widgets.

    Cell size is derived from available area divided by cols/rows.
    Cells are square — sized by whichever axis is more constrained.
    Grid dots mark cell corners. A drop-zone highlight tracks the drag.

    Tile positions are saved to plugin settings (key → {col, row})
    and restored when add_tile is called with a registered key.
    """

    def __init__(
        self,
        client:   "Client",
        cols:     int = 16,
        rows:     int = 10,
        plugin    = None,   # plugin instance for settings persistence
    ):
        super().__init__()
        self.client  = client
        self._cols   = cols
        self._rows   = rows
        self._plugin = plugin
        self._tiles: list[Tile] = []

        self._cell_size = 0
        self._gap       = 0
        self._margin    = 0
        self._origin_x  = 0
        self._origin_y  = 0
        self._drawer_h  = 0   # reserved at bottom for drawer

        self._dragging_tile: Optional[Tile] = None
        self._hover_col:     int = -1
        self._hover_row:     int = -1

        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load_positions(self) -> dict:
        """Return saved {key: {col, row}} from plugin settings."""
        try:
            raw = self._plugin.settings.tiles.positions.value
            return json.loads(raw) if raw and raw != "{}" else {}
        except Exception:
            return {}

    def _save_positions(self) -> None:
        """Persist all tile positions to plugin settings."""
        if not self._plugin:
            return
        positions = {t.KEY: {"col": t.grid_col, "row": t.grid_row} for t in self._tiles}
        try:
            self._plugin.settings.tiles.positions.value = json.dumps(positions)
        except Exception:
            pass

    # ── Layout ────────────────────────────────────────────────────────────────

    def set_drawer_height(self, h: int) -> None:
        """Call from SubTilesPage with the drawer's handle height so the
        grid leaves matching breathing room at the top and bottom."""
        self._drawer_h = h

    def _recalculate(self) -> None:
        margin = int(self.client.SETTINGS.home.widget_margin.value)
        self._margin = margin
        self._gap    = max(6, margin // 4)

        # Reserve drawer handle height at bottom to match top margin visually
        bottom_reserve = max(self._drawer_h, margin)

        available_w = self.width()  - margin * 2
        available_h = self.height() - margin - bottom_reserve

        cell_from_w = (available_w - self._gap * (self._cols - 1)) / self._cols
        cell_from_h = (available_h - self._gap * (self._rows - 1)) / self._rows
        self._cell_size = int(min(cell_from_w, cell_from_h))

        # Centre the resulting grid within available area
        grid_w = self._cell_size * self._cols + self._gap * (self._cols - 1)
        grid_h = self._cell_size * self._rows + self._gap * (self._rows - 1)
        self._origin_x = (self.width()  - grid_w) // 2
        self._origin_y = margin + (available_h - grid_h) // 2

    def _cell_rect(self, col: int, row: int, span_w: int = 1, span_h: int = 1) -> QRect:
        x = self._origin_x + col * (self._cell_size + self._gap)
        y = self._origin_y + row * (self._cell_size + self._gap)
        w = span_w * self._cell_size + (span_w - 1) * self._gap
        h = span_h * self._cell_size + (span_h - 1) * self._gap
        return QRect(x, y, w, h)

    def _place_tile(self, tile: Tile, col: int, row: int, animate: bool = False) -> None:
        rect = self._cell_rect(col, row, tile.grid_w, tile.grid_h)
        tile.grid_col = col
        tile.grid_row = row
        tile.resize(rect.width(), rect.height())
        if animate:
            anim = QPropertyAnimation(tile, b"pos")
            anim.setDuration(180)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.setStartValue(tile.pos())
            anim.setEndValue(rect.topLeft())
            anim.finished.connect(lambda: self._save_positions())
            anim.start()
            tile._snap_anim = anim   # keep reference
        else:
            tile.move(rect.topLeft())

    # ── Public API ────────────────────────────────────────────────────────────

    def add_tile(self, tile: Tile, col: int = 0, row: int = 0) -> None:
        """
        Place a tile. If a saved position exists for tile.KEY it overrides
        the given col/row defaults.
        Raises ValueError if tile.KEY is already registered.
        """
        if not tile.KEY:
            raise ValueError("Tile must have a non-empty KEY")
        if any(t.KEY == tile.KEY for t in self._tiles):
            raise ValueError(f"Tile key '{tile.KEY}' already registered in this grid")

        # Restore saved position if available
        positions = self._load_positions()
        if tile.KEY in positions:
            saved = positions[tile.KEY]
            col = int(saved.get("col", col))
            row = int(saved.get("row", row))

        # Clamp to grid bounds
        col = max(0, min(col, self._cols - tile.grid_w))
        row = max(0, min(row, self._rows - tile.grid_h))

        tile.setParent(self)
        tile.move_requested.connect(self._on_tile_move_requested)
        self._tiles.append(tile)

        if self._cell_size > 0:
            self._place_tile(tile, col, row)
        else:
            tile.grid_col = col
            tile.grid_row = row

        tile.show()

    def remove_tile(self, key: str) -> None:
        found = [t for t in self._tiles if t.KEY == key]
        for tile in found:
            tile.setParent(None)
            self._tiles.remove(tile)
        if found:
            self._save_positions()

    def get_tile(self, key: str) -> Optional[Tile]:
        found = [t for t in self._tiles if t.KEY == key]
        return found[0] if found else None

    # ── Drag / snap ───────────────────────────────────────────────────────────

    def _on_tile_move_requested(self, tile: Tile, col: int, row: int) -> None:
        col = max(0, min(col, self._cols - tile.grid_w))
        row = max(0, min(row, self._rows - tile.grid_h))
        self._hover_col     = col
        self._hover_row     = row
        self._dragging_tile = tile
        self.update()

    def snap_tile(self, tile: Tile) -> None:
        """Called by Tile on release — snap to guide box position."""
        if self._hover_col >= 0 and self._hover_row >= 0:
            col = max(0, min(self._hover_col, self._cols - tile.grid_w))
            row = max(0, min(self._hover_row, self._rows - tile.grid_h))
        else:
            if self._cell_size > 0:
                cs, gap = self._cell_size, self._gap
                col = max(0, min(round((tile.x() - self._origin_x) / (cs + gap)), self._cols - tile.grid_w))
                row = max(0, min(round((tile.y() - self._origin_y) / (cs + gap)), self._rows - tile.grid_h))
            else:
                col, row = tile.grid_col, tile.grid_row

        self._place_tile(tile, col, row, animate=True)
        self._dragging_tile = None
        self._hover_col     = -1
        self._hover_row     = -1
        self.update()

    def mousePressEvent(self, event) -> None:
        event.ignore()

    def mouseMoveEvent(self, event) -> None:
        if self._dragging_tile is None:
            event.ignore()
            return
        col = int((event.position().x() - self._origin_x) // (self._cell_size + self._gap))
        row = int((event.position().y() - self._origin_y) // (self._cell_size + self._gap))
        col = max(0, min(col, self._cols - self._dragging_tile.grid_w))
        row = max(0, min(row, self._rows - self._dragging_tile.grid_h))
        self._hover_col = col
        self._hover_row = row
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging_tile:
            tile = self._dragging_tile
            col  = max(0, min(self._hover_col, self._cols - tile.grid_w))
            row  = max(0, min(self._hover_row, self._rows - tile.grid_h))
            self._place_tile(tile, col, row, animate=True)
            self._dragging_tile = None
            self._hover_col     = -1
            self._hover_row     = -1
            self.update()
        else:
            event.ignore()

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        if self._cell_size <= 0:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        p.setBrush(QBrush(QColor(255, 255, 255, 22)))
        p.setPen(Qt.GlobalColor.transparent)
        r = 2
        for col in range(self._cols + 1):
            for row in range(self._rows + 1):
                x = self._origin_x + col * (self._cell_size + self._gap) - self._gap // 2
                y = self._origin_y + row * (self._cell_size + self._gap) - self._gap // 2
                p.drawEllipse(x - r, y - r, r * 2, r * 2)

        if self._dragging_tile and self._hover_col >= 0:
            drop_rect = self._cell_rect(
                self._hover_col, self._hover_row,
                self._dragging_tile.grid_w,
                self._dragging_tile.grid_h,
            )
            p.setBrush(QBrush(QColor(255, 255, 255, 20)))
            p.setPen(QPen(QColor(255, 255, 255, 60), 1.5))
            p.drawRoundedRect(drop_rect, 10, 10)

    # ── Resize ────────────────────────────────────────────────────────────────

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._recalculate()
        for tile in self._tiles:
            self._place_tile(tile, tile.grid_col, tile.grid_row)
        self.update()