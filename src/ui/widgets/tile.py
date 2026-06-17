from __future__ import annotations
from typing import TYPE_CHECKING, Callable, Optional

from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtCore import Qt, QPoint, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QMouseEvent

if TYPE_CHECKING:
    from src.main import Client


class Tile(QWidget):
    """
    Base class for all tile widgets placed in a TileGrid.

    grid_w / grid_h are in grid units, not pixels.
    Subclass and add children to self._content_layout.
    """

    move_requested = pyqtSignal(object, int, int)  # (tile, col, row)
    DRAG_THRESHOLD  = 8

    def __init__(
        self,
        client:   "Client",
        key:      str,
        grid_w:   int = 1,
        grid_h:   int = 1,
        bg_color: str = "#2a2a2a",
        on_click: Optional[Callable] = None,
        on_drag:  Optional[Callable] = None,
    ):
        super().__init__()
        self.client   = client
        self.KEY      = key
        self.grid_w   = grid_w
        self.grid_h   = grid_h
        self.on_click = on_click
        self.on_drag  = on_drag

        self.grid_col = 0
        self.grid_row = 0

        self._bg_color  = QColor(bg_color)
        self._radius    = 10
        self._dragging  = False
        self._drag_start: Optional[QPoint] = None
        self._hovered   = False

        # Required for paintEvent to actually fire on a plain QWidget
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMouseTracking(True)

        self._content_layout = QVBoxLayout(self)
        self._content_layout.setContentsMargins(12, 12, 12, 12)
        self._content_layout.setSpacing(6)

    def set_bg_color(self, color: str) -> None:
        self._bg_color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(self._bg_color)
        if self._hovered and not self._dragging:
            bg = bg.lighter(115)
        if self._dragging:
            bg.setAlphaF(0.75)
        p.setBrush(QBrush(bg))
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), self._radius, self._radius)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start  = event.globalPosition().toPoint()
            self._dragging    = False

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_start is None:
            return
        delta = event.globalPosition().toPoint() - self._drag_start
        if not self._dragging and max(abs(delta.x()), abs(delta.y())) >= self.DRAG_THRESHOLD:
            self._dragging = True
            self.raise_()
            self.update()
        if self._dragging:
            new_pos = self.pos() + event.globalPosition().toPoint() - self._drag_start
            self.move(new_pos)
            self._drag_start = event.globalPosition().toPoint()
            self.move_requested.emit(self, *self._screen_to_grid())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        was_dragging = self._dragging
        self._dragging   = False
        self._drag_start = None
        self.update()

        if was_dragging:
            # Ask parent grid to snap us to the nearest cell
            parent = self.parent()
            if parent and hasattr(parent, "snap_tile"):
                parent.snap_tile(self)
        elif self.on_click:
            self.on_click()

    def _screen_to_grid(self) -> tuple[int, int]:
        parent = self.parent()
        if parent and hasattr(parent, "_cell_size") and parent._cell_size > 0:
            cs  = parent._cell_size
            gap = parent._gap
            col = round((self.x() - parent._origin_x) / (cs + gap))
            row = round((self.y() - parent._origin_y) / (cs + gap))
            return max(0, col), max(0, row)
        return self.grid_col, self.grid_row

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        self.setCursor(Qt.CursorShape.ArrowCursor)