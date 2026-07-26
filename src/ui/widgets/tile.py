from __future__ import annotations
from typing import TYPE_CHECKING, Callable, Optional

from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtCore import Qt, QPoint, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QMouseEvent

if TYPE_CHECKING:
    from src.main import Client


class Tile(QWidget):

    move_requested = pyqtSignal(object, int, int)
    DRAG_THRESHOLD  = 8

    KEY  : str = ""
    NAME : str = ""
    ICON : str = ""

    def __init__(
        self,
        client:   "Client",
        grid_w:   int = 2,
        grid_h:   int = 2,
        bg_color: str = "#2a2a2a",
        on_click: Optional[Callable] = None,
    ):
        super().__init__()
        self.client   = client
        self.grid_w   = grid_w
        self.grid_h   = grid_h
        self.on_click = on_click

        #current grid position — kept in sync by TileGrid.place_tile()
        self.grid_col = 0
        self.grid_row = 0

        self.bg_color  = QColor(bg_color)
        self.radius    = 10
        self.dragging  = False
        self.drag_start: Optional[QPoint] = None
        self.hovered   = False

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMouseTracking(True)

        self.content_layout = QVBoxLayout(self)
        self.content_layout.setContentsMargins(12, 12, 12, 12)
        self.content_layout.setSpacing(6)

    ##TICK

    def tick(self) -> None:
        pass

    def tick_once(self) -> None:
        self.tick()

    ##APPEARANCE

    def set_bg_color(self, color: str) -> None:
        self.bg_color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(self.bg_color)
        if self.hovered and not self.dragging:
            bg = bg.lighter(115)
        if self.dragging:
            bg.setAlphaF(0.75)
        p.setBrush(QBrush(bg))
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), self.radius, self.radius)


    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start = event.globalPosition().toPoint()
            self.dragging   = False

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.drag_start is None:
            return

        delta = event.globalPosition().toPoint() - self.drag_start

        if not self.dragging and max(abs(delta.x()), abs(delta.y())) >= self.DRAG_THRESHOLD:
            self.dragging = True
            self.raise_()
            self.update()
            grid = self.parent()
            page = grid.parent() if grid else None
            if page and hasattr(page, "notify_drag_started"):
                page.notify_drag_started()

        if self.dragging:
            new_pos = self.pos() + event.globalPosition().toPoint() - self.drag_start
            self.move(new_pos)
            self.drag_start = event.globalPosition().toPoint()

            self.move_requested.emit(self, *self.screen_to_grid())

            #update trash bin hot state (red highlight when hovering over it)
            grid = self.parent()
            page = grid.parent() if grid else None
            if page and hasattr(page, "trash_bin"):
                page.trash_bin.set_hot(page.trash_bin.is_over(event.globalPosition().toPoint()))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        was_dragging   = self.dragging
        self.dragging   = False
        self.drag_start = None
        self.update()

        if was_dragging:
            gpos = event.globalPosition().toPoint()
            grid = self.parent()
            page = grid.parent() if grid else None

            if page and hasattr(page, "notify_drag_ended"):
                page.notify_drag_ended(gpos, self)

            if self.parent() is grid and grid and hasattr(grid, "snap_tile"):
                grid.snap_tile(self)
        elif self.on_click:
            self.on_click()

    def screen_to_grid(self) -> tuple[int, int]:
        parent = self.parent()
        if parent and hasattr(parent, "cell_size") and parent.cell_size > 0:
            col = round((self.x() - parent.origin_x) / (parent.cell_size + parent.gap_x))
            row = round((self.y() - parent.origin_y) / (parent.cell_size + parent.gap_y))
            return max(0, col), max(0, row)
        return self.grid_col, self.grid_row

    def enterEvent(self, event) -> None:
        self.hovered = True
        self.update()
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def leaveEvent(self, event) -> None:
        self.hovered = False
        self.update()
        self.setCursor(Qt.CursorShape.ArrowCursor)