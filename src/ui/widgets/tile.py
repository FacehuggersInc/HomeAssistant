from __future__ import annotations
from typing import TYPE_CHECKING, Callable, Optional

from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtCore import Qt, QPoint, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QMouseEvent

if TYPE_CHECKING:
    from src.main import Client


##TILE
# A single draggable tile. See TileGrid (tile_grid.py) for how placed
# tiles are tracked, and TilePanel (tile_panel.py) for how unplaced
# tiles wait to be dragged out. SubTilesPage owns and connects both.

class Tile(QWidget):
    """
    Base class for all tile widgets placed in a TileGrid.

    Subclasses MUST define:
        KEY  : str  — unique identifier, used for persistence and lookup
        NAME : str  — display name shown in the tile panel
        ICON : str  — icon string for the Icons system (e.g. "mdi.clock")

    grid_w / grid_h are in grid units. Add content to self.content_layout.
    tick() is called by TileGrid each update cycle while the tile is placed.
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

        #child widgets added here (buttons, labels, etc.) receive their
        #own mouse events normally — Qt delivers press/release to
        #whichever widget is directly under the cursor, so a QPushButton
        #placed in here intercepts clicks within its own bounds before
        #Tile's own mousePressEvent/mouseReleaseEvent ever see them.
        #Tile's drag handling only kicks in for clicks that land on
        #empty space within the tile, not on a child widget.
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
    #drag handling for a tile that is already parented to TileGrid.
    #see the module-level comment block above for the full drag flow.

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
            #parent is TileGrid, parent.parent() is SubTilesPage —
            #tell the page so it can show the trash bin
            grid = self.parent()
            page = grid.parent() if grid else None
            if page and hasattr(page, "notify_drag_started"):
                page.notify_drag_started()

        if self.dragging:
            new_pos = self.pos() + event.globalPosition().toPoint() - self.drag_start
            self.move(new_pos)
            self.drag_start = event.globalPosition().toPoint()

            #recompute grid col/row from current pixel position and tell
            #TileGrid — this is what drives the green guide box
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

            #let the page check the trash bin first — if the tile is
            #dropped there it gets removed from the grid and sent back
            #to the panel before snap_tile() runs
            if page and hasattr(page, "notify_drag_ended"):
                page.notify_drag_ended(gpos, self)

            #if the tile is still parented to the grid (i.e. it wasn't
            #just removed by the trash bin above) snap it into place
            if self.parent() is grid and grid and hasattr(grid, "snap_tile"):
                grid.snap_tile(self)
        elif self.on_click:
            self.on_click()

    def screen_to_grid(self) -> tuple[int, int]:
        """
        Convert this tile's current pixel position into a grid col/row,
        using the parent TileGrid's own layout numbers.

        IMPORTANT: attribute names here (cell_size, gap_x, gap_y,
        origin_x, origin_y) must match TileGrid exactly — no leading
        underscores anywhere in this codebase. If TileGrid renames any
        of these, this silently breaks: it falls back to returning the
        tile's last known grid_col/grid_row, which looks like "the tile
        won't move" since every drag frame reports the same stale spot.
        """
        parent = self.parent()
        if parent and hasattr(parent, "cell_size") and parent.cell_size > 0:
            #gap_x and gap_y can differ — TileGrid stretches whichever
            #axis has leftover space so the grid fills edge to edge
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