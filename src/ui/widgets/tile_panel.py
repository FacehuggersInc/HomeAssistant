from __future__ import annotations
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QHBoxLayout, QScrollArea, QPushButton
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QPoint, QSize
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QMouseEvent

import qtawesome as qta

from src.ui.widgets.tile import Tile
from src.styling import COLORS, make_font, SIZES

if TYPE_CHECKING:
    from src.main import Client
    from src.ui.widgets.tile_grid import TileGrid


##TILE PANEL ROW

class TilePanelRow(QWidget):
    """
    A single row in the tile panel representing a registered-but-unplaced
    tile. Dragging this row out and over the grid places the real tile.

    The tile widget itself is NOT parented to TileGrid while it sits in
    a row — only a temporary "ghost" preview follows the cursor during
    the drag. The real Tile only gets attached to TileGrid once it's
    actually dropped onto it, via place_tile_on_grid().
    """

    DRAG_THRESHOLD = 8

    def __init__(self, tile: Tile, panel: "TilePanel"):
        super().__init__()
        self.tile       = tile
        self.panel      = panel
        self.drag_start: QPoint | None = None
        self.dragging   = False
        self.ghost:      QWidget | None = None   #floating preview, created on drag start

        self.setFixedHeight(64)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            QWidget {{
                background: rgba(255,255,255,8);
                border-radius: 8px;
                border: 1px solid rgba(255,255,255,10);
            }}
        """)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(12)

        #icon — falls back to a generic puzzle piece if ICON is missing
        #or isn't a valid qtawesome name
        icon_lbl = QLabel()
        icon_lbl.setFixedSize(32, 32)
        icon_lbl.setStyleSheet("background: transparent; border: none;")
        try:
            icon_lbl.setPixmap(qta.icon(tile.ICON or "mdi.puzzle", color="white").pixmap(28, 28))
        except Exception:
            icon_lbl.setPixmap(qta.icon("mdi.puzzle", color="white").pixmap(28, 28))

        #name + grid size, stacked vertically next to the icon
        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        text_col.setContentsMargins(0, 0, 0, 0)

        name_lbl = QLabel(tile.NAME or tile.KEY)
        name_lbl.setFont(make_font(SIZES.S2, bold=True))
        name_lbl.setStyleSheet(f"color: {COLORS.DARK.TEXT.IMPORTANT}; background: transparent; border: none;")

        size_lbl = QLabel(f"{tile.grid_w} × {tile.grid_h}")
        size_lbl.setFont(make_font(SIZES.S1))
        size_lbl.setStyleSheet(f"color: {COLORS.DARK.TEXT.MUTED}; background: transparent; border: none;")

        text_col.addWidget(name_lbl)
        text_col.addWidget(size_lbl)

        row.addWidget(icon_lbl)
        row.addLayout(text_col, stretch=1)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        #just record where the press started — dragging only begins
        #once the cursor moves past DRAG_THRESHOLD in mouseMoveEvent
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start = event.globalPosition().toPoint()
            self.dragging   = False

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.drag_start is None:
            return

        delta = event.globalPosition().toPoint() - self.drag_start

        if not self.dragging and max(abs(delta.x()), abs(delta.y())) >= self.DRAG_THRESHOLD:
            #threshold crossed for the first time this drag — start
            #the ghost preview and tell the page to show the trash bin
            #(same trash bin used when dragging a tile already on the grid)
            self.dragging = True
            self.create_ghost()
            page = self.panel.page
            if hasattr(page, 'notify_drag_started'):
                page.notify_drag_started()

        if self.dragging and self.ghost:
            #move the ghost to follow the cursor, centred under it
            page = self.panel.page
            if hasattr(page, 'trash_bin'):
                page.trash_bin.set_hot(page.trash_bin.is_over(event.globalPosition().toPoint()))
            local = page.mapFromGlobal(event.globalPosition().toPoint())
            self.ghost.move(local.x() - self.ghost.width() // 2,
                            local.y() - self.ghost.height() // 2)

            #also compute which grid cell the cursor is over right now,
            #so TileGrid can draw the same green guide box it shows for
            #tiles being dragged from within the grid itself
            grid = self.panel.grid
            grid_pos = grid.mapFromGlobal(event.globalPosition().toPoint())
            col = int((grid_pos.x() - grid.origin_x) // (grid.cell_size + grid.gap_x))
            row = int((grid_pos.y() - grid.origin_y) // (grid.cell_size + grid.gap_y))
            col = max(0, min(col, grid.cols - self.tile.grid_w))
            row = max(0, min(row, grid.rows - self.tile.grid_h))
            grid.hover_col     = col
            grid.hover_row     = row
            grid.dragging_tile = self.tile
            grid.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        was_dragging    = self.dragging
        self.dragging    = False
        self.drag_start  = None

        #ghost preview is only ever needed mid-drag — clean it up
        #unconditionally on release
        if self.ghost:
            self.ghost.deleteLater()
            self.ghost = None

        if was_dragging:
            page = self.panel.page
            gpos = event.globalPosition().toPoint()

            #hide the trash bin and clear the grid's guide box regardless
            #of where the tile was dropped — both only matter mid-drag
            if hasattr(page, 'trash_bin'):
                page.trash_bin.hide_after_drag()
            self.panel.grid.dragging_tile = None
            self.panel.grid.hover_col     = -1
            self.panel.grid.hover_row     = -1
            self.panel.grid.update()

            #only place the tile if it was actually dropped within the
            #grid's screen rect — otherwise it just stays in the panel
            grid              = self.panel.grid
            grid_global       = grid.mapToGlobal(QPoint(0, 0))
            drop_global       = event.globalPosition().toPoint()
            grid_rect_global  = grid.rect().translated(grid_global)

            if grid_rect_global.contains(drop_global):
                grid_pos = grid.mapFromGlobal(drop_global)
                col = int((grid_pos.x() - grid.origin_x) // (grid.cell_size + grid.gap_x))
                row = int((grid_pos.y() - grid.origin_y) // (grid.cell_size + grid.gap_y))
                col = max(0, min(col, grid.cols - self.tile.grid_w))
                row = max(0, min(row, grid.rows - self.tile.grid_h))
                #this is the moment the tile actually leaves the panel
                #and becomes a real grid tile — see place_tile_on_grid()
                self.panel.place_tile_on_grid(self.tile, col, row)

    def create_ghost(self) -> None:
        """
        Build the floating dashed-border preview that follows the cursor
        while dragging this row. Sized to roughly match what the tile
        would look like once placed, using the grid's current cell_size
        so the preview isn't wildly off from the real thing.
        """
        page = self.panel.page
        tile = self.tile
        grid = self.panel.grid

        if grid.cell_size > 0:
            #cast to int — gap_x/gap_y are floats since TileGrid
            #stretches them to fill leftover space, and Qt size setters
            #reject floats outright
            w = int(tile.grid_w * grid.cell_size + (tile.grid_w - 1) * grid.gap_x)
            h = int(tile.grid_h * grid.cell_size + (tile.grid_h - 1) * grid.gap_y)
        else:
            #grid hasn't been laid out yet (cell_size still 0) — use a
            #reasonable placeholder size instead of crashing
            w, h = 120, 120

        self.ghost = QWidget(page)
        self.ghost.setFixedSize(w, h)
        self.ghost.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.ghost.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.ghost.setStyleSheet(
            "background: rgba(255,255,255,15);"
            "border: 2px dashed rgba(255,255,255,60);"
            "border-radius: 10px;"
        )
        self.ghost.show()
        self.ghost.raise_()


##TILE PANEL

class TilePanel(QWidget):
    """
    Slide-in panel on the right side of the tiles page, listing every
    tile that's registered but not currently placed on the grid.

    Lives as a child of SubTilesPage. Slides in/out via toggle(), driven
    by the subtle button SubTilesPage adds in its top-right corner.
    """

    WIDTH = 280

    def __init__(self, client: "Client", page: QWidget, grid: "TileGrid"):
        super().__init__(page)
        self.client = client
        self.page   = page
        self.grid   = grid
        self.rows:  dict[str, TilePanelRow] = {}   #tile.KEY -> its row widget
        self.open   = False

        #start fully off-screen to the right, same width as the panel —
        #toggle() animates it sliding in from here
        ph = page.height()
        self.setGeometry(page.width(), 0, self.WIDTH, ph)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            QWidget {{
                background: {COLORS.DARK.BG};
                border-left: 1px solid rgba(255,255,255,12);
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 24, 16, 24)
        layout.setSpacing(12)

        #title + close button share one row
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)

        title = QLabel("Tiles")
        title.setFont(make_font(SIZES.M1, bold=True))
        title.setStyleSheet(f"color: {COLORS.DARK.TEXT.IMPORTANT}; background: transparent;")

        close_btn = QPushButton("\u2715")
        close_btn.setFixedSize(32, 32)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,8); color: rgba(255,255,255,120);"
            " border: 1px solid rgba(255,255,255,12); border-radius: 6px; font-size: 14px; }"
            "QPushButton:hover { background: rgba(255,255,255,18); color: white; }"
        )
        close_btn.clicked.connect(self.toggle)   #same toggle used to open it

        header_row.addWidget(title, stretch=1)
        header_row.addWidget(close_btn)
        layout.addLayout(header_row)

        sub = QLabel("Drag a tile onto the grid to place it.")
        sub.setFont(make_font(SIZES.S1))
        sub.setStyleSheet(f"color: {COLORS.DARK.TEXT.MUTED}; background: transparent;")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        #scrollable list of TilePanelRow widgets
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }"
                             "QScrollBar:vertical { width: 4px; background: transparent; }"
                             "QScrollBar::handle:vertical { background: rgba(255,255,255,30); border-radius: 2px; }")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.list_widget = QWidget()
        self.list_widget.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)
        self.list_layout.addStretch()   #keeps rows pinned to the top as they're added

        scroll.setWidget(self.list_widget)
        layout.addWidget(scroll, stretch=1)

        #drives the slide in/out — see toggle()
        self.anim = QPropertyAnimation(self, b"pos")
        self.anim.setDuration(220)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def add_tile(self, tile: Tile) -> None:
        """
        Register a tile in the panel — called by SubTilesPage.register_tile()
        when a tile has no saved grid position, and again whenever a
        tile is dragged off the grid into the trash bin (it lands back
        here instead of being destroyed).
        """
        if tile.KEY in self.rows:
            #already listed — avoid duplicate rows for the same tile
            return
        row = TilePanelRow(tile, self)
        self.rows[tile.KEY] = row
        #insert before the trailing stretch so new rows stack at the bottom
        self.list_layout.insertWidget(self.list_layout.count() - 1, row)

    def remove_tile(self, key: str) -> None:
        """Remove a tile's row from the panel — called once it's placed onto the grid."""
        if key in self.rows:
            self.rows[key].deleteLater()
            del self.rows[key]

    def place_tile_on_grid(self, tile: Tile, col: int, row: int) -> None:
        """
        The handoff point from panel to grid. Called by TilePanelRow
        when a drag ends successfully over the grid.
        """
        self.remove_tile(tile.KEY)         #row no longer needed
        tile.setParent(self.grid)          #tile now belongs to TileGrid
        self.grid.add_tile(tile, col, row) #same entry point used for saved-position restoration
        self.toggle()                      #close the panel so the new tile is visible

    def tick_once(self) -> None:
        """
        Tick every panel tile exactly once. Called only when the panel
        opens (see toggle() below) so any tile that shows live data
        (like a clock) looks current the moment it's visible — tiles
        sitting in the panel are NOT ticked continuously the way placed
        tiles are by TileGrid.tick(), since there's no reason to update
        a preview nobody is looking at.
        """
        for tile in [r.tile for r in self.rows.values()]:
            try:
                tile.tick_once()
            except Exception:
                #a broken tile's tick() shouldn't be able to break the
                #whole panel from opening
                pass

    def toggle(self) -> None:
        """Slide the panel in if closed, or out if open."""
        pw = self.page.width()
        ph = self.page.height()
        self.setFixedHeight(ph)
        self.anim.stop()

        if self.open:
            #slide back out to fully off-screen, then hide once finished
            #(hiding immediately would cut the animation short)
            self.anim.setStartValue(self.pos())
            self.anim.setEndValue(QPoint(pw, 0))
            self.anim.finished.connect(self.hide)
            self.anim.finished.connect(lambda: self.anim.finished.disconnect())
            self.open = False
        else:
            #must be visible before/while animating in, and ticked once
            #so previews are current right as they become visible
            self.move(pw, 0)
            self.show()
            self.raise_()
            self.tick_once()
            self.anim.setStartValue(QPoint(pw, 0))
            self.anim.setEndValue(QPoint(pw - self.WIDTH, 0))
            self.open = True

        self.anim.start()

    def resizeEvent(self, event) -> None:
        #only height needs to track the page — width is fixed
        super().resizeEvent(event)
        self.setFixedHeight(self.page.height())