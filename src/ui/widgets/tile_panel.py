from __future__ import annotations
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QHBoxLayout, QScrollArea, QPushButton
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QPoint, QSize, QEvent
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QMouseEvent

import qtawesome as qta

from src.ui.widgets.tile import Tile
from src.styling import COLORS, make_font, SIZES

if TYPE_CHECKING:
    from src.main import Client
    from src.ui.widgets.tile_grid import TileGrid


##TILE PANEL ITEM

class TilePanelItem(QWidget):
    """
    A single item in the tile panel: a title label above the actual
    Tile instance, rendered small (scaled down from real grid size but
    keeping the same grid_w:grid_h aspect ratio) so it looks like a true
    preview of how the tile will appear once placed — not a generic
    icon+name row like before.

    Only the Tile itself is draggable here, not the title or the item
    container — clicking/dragging the title does nothing.

    IMPORTANT: dragging is implemented via installEventFilter(self) on
    the tile (see __init__), not via this item's own mousePressEvent
    etc. Tile is a real QWidget with its own built-in drag handling for
    sitting inside a TileGrid, and it's the tile — not this item — that
    sits directly under the cursor. Qt would deliver mouse events to
    Tile first, letting its grid-drag logic run against
    preview_container as if it were a TileGrid (it isn't), silently
    eating every click. The event filter intercepts mouse events on the
    tile before Tile's own handlers ever see them — see eventFilter()
    and the _on_tile_* methods below.

    Dragging behaviour:
      - drop on the grid          -> tile is placed there (existing flow)
      - drop on the trash bin     -> tile stays in the panel (no-op, since
                                     it never left the panel to begin with)
      - drag outside the WINDOW   -> panel closes immediately, tile drops
                                     back into its spot in the list
    """

    #the preview fills the largest box it can within this max size while
    #keeping the tile's real grid_w:grid_h aspect ratio — a 1x1 tile and
    #a 4x4 tile both end up visually as large as the panel allows, just
    #shaped differently, rather than both using the same fixed
    #per-grid-unit size (which made big tiles huge and small tiles tiny)
    MAX_PREVIEW_SIZE = 220

    DRAG_THRESHOLD = 8

    def __init__(self, tile: Tile, panel: "TilePanel"):
        super().__init__()
        self.tile       = tile
        self.panel      = panel
        self.drag_start: QPoint | None = None
        self.dragging   = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        outer.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        #title sits above the tile preview, completely separate from
        #drag handling — clicking/dragging the title does nothing
        title_lbl = QLabel(tile.NAME or tile.KEY)
        title_lbl.setFont(make_font(SIZES.S2, bold=True))
        title_lbl.setStyleSheet(
            f"color: {COLORS.DARK.TEXT.IMPORTANT}; background: transparent;"
        )
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(title_lbl)

        #scale so the LARGER of width/height hits MAX_PREVIEW_SIZE,
        #keeping the tile's real aspect ratio — e.g. a 1x2 tile ends up
        #110x220, a 4x4 tile ends up 220x220, both "as big as possible"
        ratio = tile.grid_w / tile.grid_h
        if ratio >= 1:
            preview_w = self.MAX_PREVIEW_SIZE
            preview_h = int(self.MAX_PREVIEW_SIZE / ratio)
        else:
            preview_h = self.MAX_PREVIEW_SIZE
            preview_w = int(self.MAX_PREVIEW_SIZE * ratio)

        #the tile preview sits inside a fixed-size container, itself
        #centred in the item — the container does NOT use
        #MAX_PREVIEW_SIZE directly so a 1x4 tile (tall+thin) doesn't
        #leave huge empty space beside it; only the box the tile itself
        #actually needs is reserved, then the whole thing is centred by
        #outer's AlignHCenter
        self.preview_container = QWidget()
        self.preview_container.setFixedSize(preview_w, preview_h)
        self.preview_container.setStyleSheet("background: transparent;")
        outer.addWidget(self.preview_container, alignment=Qt.AlignmentFlag.AlignHCenter)

        tile.setParent(self.preview_container)
        tile.move(0, 0)
        tile.resize(preview_w, preview_h)
        tile.show()

        #Tile defines its own mousePressEvent/mouseMoveEvent/
        #mouseReleaseEvent for dragging within a TileGrid. Since the
        #Tile widget itself is what's physically under the cursor here
        #(not this TilePanelItem, which only wraps around it), Qt
        #delivers mouse events to the Tile FIRST — this item's own
        #mousePressEvent etc. below never fire at all for clicks landing
        #on the tile. Tile's grid-drag logic then runs against
        #preview_container as if it were a TileGrid, which it isn't,
        #so nothing visible happens and the click is effectively eaten.
        #
        #installEventFilter lets this item intercept those events
        #before Tile's own handlers ever see them — eventFilter() below
        #re-implements the same press/move/release logic that used to
        #live directly in this item's own mouse handlers, but now
        #actually receives the clicks.
        tile.installEventFilter(self)

        #tick once now so the preview shows current data immediately —
        #see TilePanel.tick_once() for why this isn't continuous
        try:
            tile.tick_once()
        except Exception:
            pass

    def eventFilter(self, watched, event) -> bool:
        """
        Intercepts mouse events meant for self.tile, BEFORE Tile's own
        mousePressEvent/mouseMoveEvent/mouseReleaseEvent ever run. This
        exists because Tile is a QWidget with its own built-in drag
        handling designed for sitting inside a TileGrid — when the same
        Tile instance sits inside this item's small preview_container
        instead, Qt still delivers mouse events to the Tile FIRST (it's
        what's physically under the cursor), so Tile's own handlers
        would consume the click and run grid-drag logic against
        preview_container as if it were a TileGrid. It isn't, so
        nothing happens: no drag starts, no log fires, nothing.
        Installing this filter on the tile (see __init__) means THIS
        method sees the event first and can return True to stop it from
        ever reaching Tile's own handlers.

        Returns True to swallow the event (stop further processing),
        False to let it continue normally.
        """
        if event.type() == QEvent.Type.MouseButtonPress:
            return self._on_tile_press(event)
        elif event.type() == QEvent.Type.MouseMove:
            return self._on_tile_move(event)
        elif event.type() == QEvent.Type.MouseButtonRelease:
            return self._on_tile_release(event)
        return False   #anything else (paint, resize, etc.) passes through untouched

    def _on_tile_press(self, event: QMouseEvent) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        self.drag_start = event.globalPosition().toPoint()
        self.dragging    = False
        return True   #swallow — Tile's own mousePressEvent must not also run

    def _on_tile_move(self, event: QMouseEvent) -> bool:
        if self.drag_start is None:
            return False

        delta = event.globalPosition().toPoint() - self.drag_start

        if not self.dragging and max(abs(delta.x()), abs(delta.y())) >= self.DRAG_THRESHOLD:
            #crossing the threshold for the first time — detach the tile
            #from its small preview container and reparent it to the
            #page itself so it can move freely on top of everything,
            #the same way a tile already on the grid does mid-drag
            self.dragging = True
            self.start_real_drag()

        if self.dragging:
            page = self.panel.page
            local = page.mapFromGlobal(event.globalPosition().toPoint())
            #keep the cursor roughly centred on the tile while dragging
            self.tile.move(local.x() - self.tile.width() // 2,
                           local.y() - self.tile.height() // 2)

            grid = self.panel.grid
            if hasattr(page, 'trash_bin'):
                page.trash_bin.set_hot(page.trash_bin.is_over(event.globalPosition().toPoint()))

            #drive the same green guide box TileGrid shows for tiles
            #being dragged from inside the grid itself
            grid_pos = grid.mapFromGlobal(event.globalPosition().toPoint())
            col = int((grid_pos.x() - grid.origin_x) // (grid.cell_size + grid.gap_x))
            row = int((grid_pos.y() - grid.origin_y) // (grid.cell_size + grid.gap_y))
            col = max(0, min(col, grid.cols - self.tile.grid_w))
            row = max(0, min(row, grid.rows - self.tile.grid_h))
            grid.hover_col     = col
            grid.hover_row     = row
            grid.dragging_tile = self.tile
            grid.update()

            #check whether the cursor has left the application window
            #entirely — if so, START sliding the panel closed right
            #away for instant visual feedback. Critically this does NOT
            #hide() the panel yet: TilePanelItem (this very widget) is a
            #descendant of TilePanel, and hiding an ancestor mid-drag
            #stops Qt from delivering any further mouse events to this
            #widget at all — including the eventual mouseReleaseEvent.
            #The drag would die silently: no tile under the cursor, and
            #releasing the mouse button does nothing. start_slide_out()
            #only animates the position; the panel is properly hidden
            #once mouseReleaseEvent actually completes, below.
            if self.panel.open and not self.panel.closing:
                self.panel.start_slide_out()

        return True   #swallow — Tile's own mouseMoveEvent must not also run

    def _on_tile_release(self, event: QMouseEvent) -> bool:
        was_dragging   = self.dragging
        self.dragging   = False
        self.drag_start = None

        if not was_dragging:
            return False   #wasn't a drag (just a click) — let Tile handle it normally

        gpos = event.globalPosition().toPoint()
        page = self.panel.page
        grid = self.panel.grid

        #always clear guide box / trash bin state — both only matter mid-drag
        if hasattr(page, 'trash_bin'):
            page.trash_bin.hide_after_drag()
        grid.dragging_tile = None
        grid.hover_col     = -1
        grid.hover_row     = -1
        grid.update()

        if self._cursor_outside_window(gpos):
            #dragged out of the window entirely — the panel was only
            #VISUALLY sliding closed during the drag (see
            #_on_tile_move above), so finalize that now: actually hide
            #it and flip its open flag. The tile never really left the
            #panel's data, so just put it back into its preview slot.
            self.restore_preview()
            if self.panel.closing:
                self.panel.finish_slide_out()
            return True

        #check if dropped within the grid's screen rect
        grid_global      = grid.mapToGlobal(QPoint(0, 0))
        grid_rect_global = grid.rect().translated(grid_global)

        if grid_rect_global.contains(gpos):
            grid_pos = grid.mapFromGlobal(gpos)
            col = int((grid_pos.x() - grid.origin_x) // (grid.cell_size + grid.gap_x))
            row = int((grid_pos.y() - grid.origin_y) // (grid.cell_size + grid.gap_y))
            col = max(0, min(col, grid.cols - self.tile.grid_w))
            row = max(0, min(row, grid.rows - self.tile.grid_h))
            #the tile actually leaves the panel here — see place_tile_on_grid()
            self.panel.place_tile_on_grid(self.tile, col, row)
        else:
            #missed the grid, still inside the window — snap back into
            #the panel preview rather than leaving it floating loose
            self.restore_preview()

        return True   #swallow — Tile's own mouseReleaseEvent must not also run

    def start_real_drag(self) -> None:
        """
        Detach the tile from its small fixed preview container and
        reparent it to the page so it can move freely across the whole
        screen during the drag, matching how a tile already on the grid
        behaves. Resized up to whatever size it would actually be on
        the grid right now, not the small preview size, so the user
        sees an accurate full-size tile while dragging.
        """
        page = self.panel.page
        grid = self.panel.grid

        self.tile.setParent(page)
        self.tile.raise_()

        if grid.cell_size > 0:
            #cast to int — gap_x/gap_y are floats since TileGrid
            #stretches them to fill leftover space, and Qt size setters
            #reject floats outright
            w = int(self.tile.grid_w * grid.cell_size + (self.tile.grid_w - 1) * grid.gap_x)
            h = int(self.tile.grid_h * grid.cell_size + (self.tile.grid_h - 1) * grid.gap_y)
        else:
            #grid hasn't been laid out yet — fall back to the preview
            #container's current size rather than crashing
            w = self.preview_container.width()
            h = self.preview_container.height()

        self.tile.resize(w, h)
        self.tile.show()

        page = self.panel.page
        if hasattr(page, 'notify_drag_started'):
            page.notify_drag_started()

    def restore_preview(self) -> None:
        """Put the tile back into its small preview slot at its original size."""
        self.tile.setParent(self.preview_container)
        self.tile.move(0, 0)
        self.tile.resize(self.preview_container.size())
        self.tile.show()

    def _cursor_outside_window(self, global_pos: QPoint) -> bool:
        """True if global_pos has left the application's own window bounds."""
        window = self.panel.client.window
        window_global = window.mapToGlobal(QPoint(0, 0))
        window_rect    = window.rect().translated(window_global)
        return not window_rect.contains(global_pos)


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
        self.items: dict[str, TilePanelItem] = {}   #tile.KEY -> its panel item
        self.open   = False
        #True for the window between starting a visual slide-out and
        #actually finishing it — see start_slide_out()/finish_slide_out()
        #below and TilePanelItem.mouseMoveEvent for why this exists
        self.closing = False

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

        #scrollable list of TilePanelItem widgets
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
        self.list_layout.setSpacing(16)
        self.list_layout.addStretch()   #keeps items pinned to the top as they're added

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
        if tile.KEY in self.items:
            #already listed — avoid duplicate items for the same tile
            return
        item = TilePanelItem(tile, self)
        self.items[tile.KEY] = item
        #insert before the trailing stretch so new items stack at the bottom
        self.list_layout.insertWidget(self.list_layout.count() - 1, item)

    def remove_tile(self, key: str) -> None:
        """Remove a tile's panel item — called once it's placed onto the grid."""
        if key in self.items:
            self.items[key].deleteLater()
            del self.items[key]

    def place_tile_on_grid(self, tile: Tile, col: int, row: int) -> None:
        """
        The handoff point from panel to grid. Called by TilePanelItem
        when a drag ends successfully over the grid.
        """
        #the tile is leaving the panel for good — it needs its own
        #normal Tile mouse handling back, not this item's filtered
        #version (see TilePanelItem.__init__ / eventFilter)
        tile.removeEventFilter(self.items[tile.KEY])
        self.remove_tile(tile.KEY)         #panel item no longer needed
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
        for item in self.items.values():
            try:
                item.tile.tick_once()
            except Exception:
                #a broken tile's tick() shouldn't be able to break the
                #whole panel from opening
                pass

    def toggle(self) -> None:
        """Slide the panel in if closed, or out if open."""
        if self.open:
            #normal close (e.g. the X button) — slide out AND hide once
            #the animation finishes, immediately. There's no in-progress
            #drag to worry about here, unlike start_slide_out() below.
            self.start_slide_out()
            self.anim.finished.connect(self.finish_slide_out)
            self.anim.finished.connect(lambda: self.anim.finished.disconnect())
        else:
            pw = self.page.width()
            ph = self.page.height()
            self.setFixedHeight(ph)
            self.anim.stop()
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

    def start_slide_out(self) -> None:
        """
        Begin animating the panel sliding off-screen WITHOUT hiding it
        yet. Used both by the normal close button (toggle() above,
        immediately followed by finish_slide_out()) and by
        TilePanelItem when a drag carries the cursor outside the
        window — in that second case, hiding the panel right away would
        stop Qt delivering further mouse events to the TilePanelItem
        that's still mid-drag, since it's a descendant of this panel.
        finish_slide_out() is deferred until the drag's
        mouseReleaseEvent actually fires.
        """
        pw = self.page.width()
        self.closing = True
        self.anim.stop()
        self.anim.setStartValue(self.pos())
        self.anim.setEndValue(QPoint(pw, 0))
        self.anim.start()

    def finish_slide_out(self) -> None:
        """Actually hide the panel and clear its open/closing flags, once it's safe to do so."""
        self.hide()
        self.open    = False
        self.closing = False

    def resizeEvent(self, event) -> None:
        #only height needs to track the page — width is fixed
        super().resizeEvent(event)
        self.setFixedHeight(self.page.height())