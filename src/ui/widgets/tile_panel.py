from __future__ import annotations
import time
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QLabel, QHBoxLayout, QScrollArea, QLayout,
)
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QPoint
from PyQt6.QtGui import QMouseEvent, QPixmap
from PyQt6 import sip

from src.ui.widgets.tile import Tile
from src.ui.widgets.tile_panel_grid import TilePanelGrid
from src.ui.overlays import Panel
from src.styling import make_font, SIZES, set_style, style_scrollbar

if TYPE_CHECKING:
    from src.main import Client
    from src.ui.widgets.tile_grid import TileGrid


##TILE PANEL ITEM

class TilePanelItem(QWidget):
    """
    One offer: a tile at one of its sizes, drawn at the size it will be.

    Nothing but the tile. Its geometry is set by `TilePanelGrid`, not by a
    layout - the panel is a grid rather than a stack, and a layout would have
    opinions about that.
    """

    DRAG_THRESHOLD = 8

    #How much more leftward than vertical a movement has to be before it is
    #read as pulling the tile out rather than scrolling the panel.
    #
    #The grid is to the LEFT of the panel, so out is one direction and only
    #one. A movement that is mostly up or down is somebody looking for
    #something further down; a movement to the right has nowhere to go at all,
    #since the panel is against that edge.
    LEFT_BIAS = 1.2

    #Held still for this long and it is a drag whatever direction it goes.
    #Somebody who has pressed and waited has said what they meant.
    HOLD_MS = 260

    def __init__(self, tile: Tile, panel: "TilePanel",
                 span: tuple = None, live: bool = True,
                 snapshot: QPixmap = None):
        super().__init__()
        self.tile       = tile
        self.panel      = panel
        self.span       = span or (tile.grid_w, tile.grid_h)
        self.live       = live
        self.snapshot   = snapshot
        # Set by the grid when this entry is too wide for the panel to show at
        # full size, which cannot happen on a panel wide enough for the grid's
        # own widest tile. See TilePanelGrid._rescue.
        self.scaled     = False
        self.drag_start: QPoint | None = None
        self.dragging   = False
        #The gesture has been given to the panel, and this entry is out of it
        #until the finger comes up.
        self.scrolling  = False
        self.pressed_at = 0.0
        self.last_point: QPoint | None = None

        # Every entry is a render, including the first. Hosting the live tile
        # in one of them meant that entry looked different from its siblings
        # and, once the tile had been borrowed for the other renders, often
        # did not paint at all.
        self.preview_label = QLabel(self)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(self.preview_label, "common", "transparent")
        self.preview_label.show()

        self.setCursor(Qt.CursorShape.OpenHandCursor)

    ## -- geometry, driven by the grid

    def apply_metrics(self, width: int, height: int) -> None:
        """Take the size the grid worked out and fill it with the render."""
        self.setFixedSize(width, height)
        self.preview_label.setGeometry(0, 0, width, height)
        self.set_snapshot(self.snapshot)

    def set_snapshot(self, pixmap) -> None:
        self.snapshot = pixmap
        label = getattr(self, "preview_label", None)
        if label is None:
            return
        if pixmap is None or pixmap.isNull():
            label.setText(f"{self.span[0]}\u00d7{self.span[1]}")
            label.setFont(make_font(SIZES.S2, bold=True))
            set_style(label, "tiles", "tile-panel-ghost")
            return

        # Back out of the ghost look. `set_style` REPLACES the sheet, so a
        # label that once had no render kept the dashed outline and the pale
        # fill behind every render it was given afterwards - a white card
        # under a tile with rounded corners.
        label.setText("")
        set_style(label, "common", "transparent")

        # 1:1 wherever it can be. The render is taken at the size the tile
        # will occupy on the grid, so on any panel wide enough to hold it the
        # pixmap already fits and scaling it would only cost sharpness. The
        # scaled path is for the rescued entry, and nothing else.
        ratio = pixmap.devicePixelRatio() or 1.0
        logical = (round(pixmap.width() / ratio), round(pixmap.height() / ratio))
        if logical == (label.width(), label.height()):
            label.setPixmap(pixmap)
            return
        label.setPixmap(pixmap.scaled(
            label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    ## -- input

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._on_tile_press(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._on_tile_move(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._on_tile_release(event)

    def _on_tile_press(self, event: QMouseEvent) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        self.drag_start = event.globalPosition().toPoint()
        self.last_point = self.drag_start
        self.pressed_at = time.monotonic()
        self.dragging    = False
        self.scrolling   = False
        return True   #swallow — Tile's own mousePressEvent must not also run

    def _on_tile_move(self, event: QMouseEvent) -> bool:
        if self.drag_start is None:
            return False

        point = event.globalPosition().toPoint()
        delta = point - self.drag_start

        # Already decided this one belongs to the list.
        if self.scrolling:
            self._scroll_by(self.last_point.y() - point.y())
            self.last_point = point
            return True

        if not self.dragging:
            held = (time.monotonic() - self.pressed_at) * 1000 >= self.HOLD_MS
            moved = max(abs(delta.x()), abs(delta.y()))
            if moved >= self.DRAG_THRESHOLD:
                # Which it is, decided once and not revisited. A gesture that
                # changes its mind halfway is worse than one that guessed
                # wrong: the tile is already out by then.
                leftward = (delta.x() < 0
                            and abs(delta.x()) >= abs(delta.y()) * self.LEFT_BIAS)
                if not held and not leftward:
                    self.scrolling = True
                    self._scroll_by(self.last_point.y() - point.y())
                    self.last_point = point
                    return True
                self.dragging = True
                self.start_real_drag()

        self.last_point = point

        if self.dragging:
            page = self.panel.page
            local = page.mapFromGlobal(event.globalPosition().toPoint())
            #keep the cursor roughly centred on the tile while dragging
            self.tile.move(local.x() - self.tile.width() // 2,
                           local.y() - self.tile.height() // 2)

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

            if self.panel.open and not self.panel.closing:
                self.panel.start_slide_out()

        return True   #swallow — Tile's own mouseMoveEvent must not also run

    def _on_tile_release(self, event: QMouseEvent) -> bool:
        if self.scrolling:
            # It was a scroll. Not a tap, not a drop - nothing happens except
            # the gesture ending.
            self.scrolling = False
            self.drag_start = None
            self.last_point = None
            return True

        was_dragging   = self.dragging
        self.dragging   = False
        self.drag_start = None

        if not was_dragging:
            return False   #wasn't a drag (just a click) — let Tile handle it normally

        gpos = event.globalPosition().toPoint()
        grid = self.panel.grid

        #always clear guide box / trash bin state — both only matter mid-drag
        grid.dragging_tile = None
        grid.hover_col     = -1
        grid.hover_row     = -1
        grid.update()

        if self._cursor_outside_window(gpos):
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
            col = max(0, min(col, grid.cols - self.span[0]))
            row = max(0, min(row, grid.rows - self.span[1]))
            #the tile actually leaves the panel here — see place_tile_on_grid()
            if not self.panel.place_tile_on_grid(self.tile, col, row, span=self.span):
                self.restore_preview()
        else:
            self.restore_preview()

        return True   #swallow — Tile's own mouseReleaseEvent must not also run

    def _scroll_by(self, pixels: int) -> None:
        """Move the panel, on behalf of the entry that was touched."""
        self.panel.scroll_by(pixels)

    def start_real_drag(self) -> None:
        page = self.panel.page
        grid = self.panel.grid

        self.tile.apply_span(*self.span, force=True)

        self.tile.setParent(page)
        self.tile.raise_()

        if grid.cell_size > 0:
            w = int(self.span[0] * grid.cell_size + (self.span[0] - 1) * grid.gap_x)
            h = int(self.span[1] * grid.cell_size + (self.span[1] - 1) * grid.gap_y)
        else:
            w = self.width()
            h = self.height()

        self.tile.resize(w, h)
        self.tile.show()

        if hasattr(page, 'notify_drag_started'):
            page.notify_drag_started()

    def restore_preview(self) -> None:
        """
        Put the tile away again after a drag that placed nothing.

        Away, not back into this entry. Every entry shows a snapshot, so a
        live tile parented into one of them is the only entry that looks
        different from its siblings - and the next snapshot pass borrows it
        straight back out again anyway.
        """
        self.tile.setParent(None)
        self.tile.hide()

    def _cursor_outside_window(self, global_pos: QPoint) -> bool:
        window = self.panel.client.window
        window_global = window.mapToGlobal(QPoint(0, 0))
        window_rect    = window.rect().translated(window_global)
        return not window_rect.contains(global_pos)


##TILE PANEL

class TilePanel(Panel):

    # A third wider than the other panels. Entries are shown at the size the
    # tile will actually be on the grid, and at the shared width the wider
    # spans had to be scaled down to fit - which is what made them look like
    # miniatures rather than previews.
    WIDTH = int(Panel.DEFAULT_WIDTH * 4 / 3)

    # ...but never more than a bit over half the screen. The width above is
    # measured against a 1920-wide panel; on a smaller one it is most of the
    # display, and a drawer that covers the grid it is filling is a drawer
    # nobody can aim from.
    MAX_SHARE = 0.55

    FALLBACK_CELL = 96   #before the grid has been laid out even once

    def __init__(self, client: "Client", page: QWidget, grid: "TileGrid"):
        width = min(self.WIDTH, max(360, int(page.width() * self.MAX_SHARE)))
        super().__init__(client, width=width, edge="right",
                         dismiss_on_outside_click=True)
        self.width_px = width
        self.page  = page
        self.grid  = grid
        self.items: dict[str, TilePanelItem] = {}        #tile.KEY -> its live item
        self.size_items: dict[str, list] = {}            #tile.KEY -> every size entry
        self.order: list[str] = []                       #the order tiles were registered
        self.closing = False

        self.page.destroyed.connect(self.deleteLater)

        self.setObjectName("tile_panel")
        self.apply_frosted_style()   #square corners — flush full-height panel

        layout = self.content_layout
        layout.setContentsMargins(16, 24, 16, 24)
        layout.setSpacing(12)

        # Just the title. A panel that closes by being tapped away from
        # needs no cross, and one that offers both teaches that the
        # cross is the way.
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)

        title = QLabel("Tiles")
        title.setFont(make_font(SIZES.M1, bold=True))
        set_style(title, "common", "text-strong")
        header_row.addWidget(title)
        header_row.addStretch()

        layout.addLayout(header_row)

        self.sub_lbl = QLabel("")
        self.sub_lbl.setFont(make_font(SIZES.S1))
        set_style(self.sub_lbl, "common", "text-muted")
        self.sub_lbl.setWordWrap(True)
        layout.addWidget(self.sub_lbl)

        #the packed grid of entries, scrolled
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        style_scrollbar(scroll)
        # Explicit: full-size entries make the grid taller than the panel, so
        # this is load-bearing rather than a default worth relying on.
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        self.panel_grid = TilePanelGrid(self)
        scroll.setWidget(self.panel_grid)
        layout.addWidget(scroll, stretch=1)
        # Held, because an item has to be able to scroll it. The items swallow
        # the press so a tile's own handlers do not also run, which means the
        # viewport never sees the gesture - so whichever item was touched
        # scrolls this on the item's behalf. See TilePanelItem._on_tile_move.
        self.scroll = scroll

        # The third argument is the PARENT. Without it the animation belongs
        # to nothing, outlives the widget it animates, and fires `finished`
        # into an object that has gone - which inside a Qt signal aborts the
        # process rather than raising.
        self.anim = QPropertyAnimation(self, b"pos", self)
        self.anim.setDuration(220)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.refresh_count()

    ## -- entries

    def scroll_by(self, pixels: int) -> None:
        """
        Move the panel by this many pixels.

        Here rather than on the entry, because the background needs it too.
        The entries swallow their presses so a tile's own handlers do not also
        run, which means the scroll area never sees a gesture that started on
        one - and the grid behind them swallows the rest for the same reason,
        so it never sees any gesture at all. Both scroll this on their own
        behalf.
        """
        try:
            bar = self.scroll.verticalScrollBar()
        except Exception:
            return
        bar.setValue(bar.value() + int(pixels))

    def refresh_count(self) -> None:
        """
        Say how many tiles are in here.

        Tiles, not entries. A tile offered at three sizes is one thing you can
        place, and counting it three times would say the panel holds twenty-two
        when it holds fourteen.
        """
        count = len(self.order)
        if not count:
            text = "Every tile is on the grid."
        elif count == 1:
            text = "1 unique tile waiting. Drag it left onto the grid to place it."
        else:
            text = (f"{count} unique tiles waiting. Drag one left onto the "
                    f"grid to place it.")
        try:
            self.sub_lbl.setText(text)
        except RuntimeError:
            pass

    def all_items(self):
        """Every entry, in the order the tiles were registered."""
        for key in self.order:
            for item in self.size_items.get(key, []):
                yield item

    def preview_size(self, span: tuple) -> tuple:
        """
        The pixel size a tile of this span occupies on the grid.

        Full size, always. An entry that cannot be shown at full size is
        dropped by the packing rather than shrunk here - a preview that has
        been squeezed is a preview of a tile that will not look like that.
        """
        grid = self.grid
        if grid is not None and getattr(grid, "cell_size", 0) > 0:
            cell, gap = grid.cell_size, grid.gap_x
        else:
            cell, gap = self.FALLBACK_CELL, 0
        width  = span[0] * cell + (span[0] - 1) * gap
        height = span[1] * cell + (span[1] - 1) * gap
        return max(1, int(width)), max(1, int(height))

    def add_tile(self, tile: Tile) -> None:
        if tile.KEY in self.items:
            #already listed — avoid duplicate items for the same tile
            return

        sizes = tile.panel_sizes() or [(tile.grid_w, tile.grid_h)]

        # Snapshots first, while the tile is still unattached. Once the live
        # item has taken it, re-parenting it around to render other sizes
        # would tear the panel apart mid-build.
        shots = {span: self._snapshot(tile, span) for span in sizes}

        made = []
        for index, span in enumerate(sizes):
            item = TilePanelItem(tile, self, span=span, live=(index == 0),
                                 snapshot=shots.get(span))
            item.setParent(self.panel_grid)
            item.show()
            made.append(item)

        self.items[tile.KEY] = made[0]
        self.size_items[tile.KEY] = made
        if tile.KEY not in self.order:
            self.order.append(tile.KEY)
        self.refresh_count()
        self.panel_grid.relayout(force=True)

    def _snapshot(self, tile: Tile, span: tuple):
        """
        Render the tile at `span` and grab it, then put it back as it was.

        Everything is restored including the variant, so the live instance is
        untouched by having been borrowed - a tile that came in at 2x2 goes
        back to 2x2 with its 2x2 layout rebuilt.
        """
        before_span   = (tile.grid_w, tile.grid_h)
        before_parent = tile.parent()
        before_geo    = tile.geometry()

        w, h = self.preview_size(span)

        pixmap = None
        try:
            tile.setParent(None)
            tile.apply_span(span[0], span[1], force=True)
            # Minimums from the tile's own layout can refuse the resize, and a
            # grab of the refused size is what made previews look stretched.
            tile.setMinimumSize(0, 0)
            if tile.layout() is not None:
                tile.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
            tile.resize(max(1, w), max(1, h))
            tile.ensurePolished()

            # render(), not grab(). `grab()` hands back an OPAQUE pixmap and
            # fills it from the palette first, so a tile with rounded corners
            # came back sitting on a near-white square - which on the panel's
            # dark grid reads as a white card with a border round every tile.
            # Rendering into a pixmap this fills itself keeps the corners
            # transparent, and DrawChildren without DrawWindowBackground keeps
            # the style from putting one back.
            ratio = tile.devicePixelRatioF() or 1.0
            pixmap = QPixmap(int(max(1, w) * ratio), int(max(1, h) * ratio))
            pixmap.setDevicePixelRatio(ratio)
            pixmap.fill(Qt.GlobalColor.transparent)
            tile.render(pixmap, QPoint(),
                        flags=QWidget.RenderFlag.DrawChildren)
        except Exception as e:
            self.client.log("warning", f"[TilePanel] Could not preview "
                                       f"{tile.KEY} at {span}: {e}")
        finally:
            try:
                if tile.layout() is not None:
                    tile.layout().setSizeConstraint(QLayout.SizeConstraint.SetDefaultConstraint)
                tile.apply_span(*before_span, force=True)
                tile.setParent(before_parent)
                tile.setGeometry(before_geo)
            except Exception:
                pass
        return pixmap

    def remove_tile(self, key: str) -> None:
        # Every size entry goes, not just the live one - the tile is on the
        # grid now, and the alternatives are offers to place it.
        for item in self.size_items.pop(key, []):
            item.setParent(None)
            item.deleteLater()
        if key in self.items:
            del self.items[key]
        if key in self.order:
            self.order.remove(key)
        self.refresh_count()
        self.panel_grid.relayout(force=True)

    def place_tile_on_grid(self, tile: Tile, col: int, row: int,
                           span: tuple = None) -> bool:
        if getattr(tile, "MULTIPLE", False):
            # The template stays; a copy goes to the grid. Its key has to be
            # unique or the grid refuses it and the saved position of the
            # original would be reused for every copy.
            copy = tile.make_copy(f"{tile.KEY}:{self.client.uuid()}", span)
            copy.setParent(self.grid)
            self.grid.add_tile(copy, col, row)
            self.toggle()
            return False   #the template is not consumed - the caller puts it back

        if span:
            # force, because the tile may already be at this span from its
            # preview and still needs the variant rebuilt at real size.
            tile.apply_span(span[0], span[1], force=True)
        self.remove_tile(tile.KEY)         #panel item no longer needed
        tile.setParent(self.grid)          #tile now belongs to TileGrid
        self.grid.add_tile(tile, col, row) #same entry point used for saved-position restoration
        self.toggle()                      #close the panel so the new tile is visible
        return True

    def tick_once(self) -> None:
        # Re-grab so a clock preview is not frozen at whatever time the panel
        # was first built - and so the first render after the grid has a real
        # cell size replaces the fallback-sized one.
        #
        # The pack comes first: an entry's snapshot is grabbed at the size the
        # pack gave it, so grabbing before laying out renders every one of
        # them at the size they had last time.
        self.panel_grid.relayout(force=True)
        for key, items in self.size_items.items():
            live = self.items.get(key)
            if live is None:
                continue
            for item in items:
                try:
                    item.set_snapshot(self._snapshot(live.tile, item.span))
                except Exception:
                    pass

    def _page_alive(self) -> bool:
        return not sip.isdeleted(self.page)

    def dismiss(self) -> None:
        """A press beside the panel. This one slides itself, so it toggles."""
        if self.open:
            self.toggle()

    def toggle(self) -> None:
        if not self._page_alive():
            return
        if self.open:
            self._release_scrim()
            self.start_slide_out()
            self.anim.finished.connect(self.finish_slide_out)
            self.anim.finished.connect(lambda: self.anim.finished.disconnect())
        else:
            pw = self.page.width()
            ph = self.page.height()
            self.setFixedHeight(ph)
            # The catcher behind it, so a press beside the panel closes it.
            # This toggle does not go through open_panel(), which is where the
            # base builds one.
            self._build_scrim()
            self.anim.stop()
            self.move(pw, 0)
            self._shown_pos = QPoint(pw - self.width_px, 0)   #for refresh_backdrop()'s rect math
            self.refresh_backdrop()
            self.show()
            self.raise_()
            self.tick_once()
            self.anim.setStartValue(QPoint(pw, 0))
            self.anim.setEndValue(QPoint(pw - self.width_px, 0))
            self.open = True
            self.anim.start()

    def start_slide_out(self) -> None:
        if not self._page_alive():
            self.finish_slide_out()
            return
        pw = self.page.width()
        self.closing = True
        self.anim.stop()
        self.anim.setStartValue(self.pos())
        self.anim.setEndValue(QPoint(pw, 0))
        self.anim.start()

    def finish_slide_out(self) -> None:
        self.hide()
        self.open    = False
        self.closing = False

    def resizeEvent(self, event) -> None:
        #only height needs to track the page — width is fixed
        super().resizeEvent(event)
        if not self._page_alive():
            return
        self.setFixedHeight(self.page.height())
        if self.open:
            self.refresh_backdrop()
