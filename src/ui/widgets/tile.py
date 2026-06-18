from __future__ import annotations
from typing import TYPE_CHECKING, Callable, Optional

from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtCore import Qt, QPoint, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QMouseEvent

if TYPE_CHECKING:
    from src.main import Client


##TILE

class Tile(QWidget):
    """
    Base class for all tile widgets placed in a TileGrid.

    Subclasses MUST define:
        KEY  : str  — unique identifier
        NAME : str  — display name shown in the tile panel
        ICON : str  — icon string for the Icons system (e.g. "mdi.clock")

    grid_w / grid_h are in grid units. Add content to self.content_layout.
    tick() is called by the grid each update cycle while placed.
    """

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
        """Called each update cycle while tile is placed in the grid. Override to update content."""
        pass

    def tick_once(self) -> None:
        """Called once when the tile panel opens so panel previews are up to date."""
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

    ##MOUSE

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
            #notify page so trash bin appears
            page = self.parent().parent() if self.parent() else None
            if page and hasattr(page, 'notify_drag_started'):
                page.notify_drag_started()
        if self.dragging:
            new_pos = self.pos() + event.globalPosition().toPoint() - self.drag_start
            self.move(new_pos)
            self.drag_start = event.globalPosition().toPoint()
            self.move_requested.emit(self, *self.screen_to_grid())
            #update trash bin hot state
            page = self.parent().parent() if self.parent() else None
            if page and hasattr(page, 'trash_bin'):
                page.trash_bin.set_hot(page.trash_bin.is_over(event.globalPosition().toPoint()))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        was_dragging  = self.dragging
        self.dragging  = False
        self.drag_start = None
        self.update()

        if was_dragging:
            parent = self.parent()
            if parent and hasattr(parent, "snap_tile"):
                parent.snap_tile(self)
            elif parent and hasattr(parent, "receive_tile_from_panel"):
                #dropped onto the tiles page from the panel
                parent.receive_tile_from_panel(self, event.globalPosition().toPoint())
        elif self.on_click:
            self.on_click()

    def screen_to_grid(self) -> tuple[int, int]:
        parent = self.parent()
        if parent and hasattr(parent, "_cell_size") and parent._cell_size > 0:
            cs  = parent._cell_size
            gap = parent._gap
            col = round((self.x() - parent._origin_x) / (cs + gap))
            row = round((self.y() - parent._origin_y) / (cs + gap))
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