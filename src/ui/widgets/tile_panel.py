from __future__ import annotations
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QHBoxLayout, QScrollArea, QPushButton, QLayout,
)
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QPoint, QSize, QEvent
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QMouseEvent
from PyQt6 import sip

import qtawesome as qta

from src.ui.widgets.tile import Tile
from src.ui.overlays import Panel
from src.styling import make_font, SIZES, set_style, get_style_sheet

if TYPE_CHECKING:
    from src.main import Client
    from src.ui.widgets.tile_grid import TileGrid


##TILE PANEL ITEM

class TilePanelItem(QWidget):

    DRAG_THRESHOLD = 8

    def __init__(self, tile: Tile, panel: "TilePanel",
                 span: tuple = None, live: bool = True,
                 snapshot: "QPixmap" = None):
        super().__init__()
        self.tile       = tile
        self.panel      = panel
        self.span       = span or (tile.grid_w, tile.grid_h)
        self.live       = live
        self.snapshot   = snapshot
        self.drag_start: QPoint | None = None
        self.dragging   = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        outer.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        name = tile.NAME or tile.KEY
        if len(tile.panel_sizes()) > 1:
            name = f"{name}  {self.span[0]}\u00d7{self.span[1]}"
        title_lbl = QLabel(name)
        title_lbl.setFont(make_font(SIZES.S2, bold=True))
        set_style(title_lbl, "common", "text-strong")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(title_lbl)

        preview_w, preview_h = panel.preview_size(self.span)

        self.preview_container = QWidget()
        self.preview_container.setFixedSize(preview_w, preview_h)
        set_style(self.preview_container, "common", "transparent")
        outer.addWidget(self.preview_container, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Every entry is a render, including the first. Hosting the live tile
        # in one of them meant that entry looked different from its siblings
        # and, once the tile had been borrowed for the other snapshots, often
        # did not paint at all.
        self.preview_label = QLabel(self.preview_container)
        self.preview_label.setGeometry(0, 0, preview_w, preview_h)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # NOT setScaledContents: that fills the label regardless of aspect. The
        # grab is whatever size the tile's layout would actually allow, which
        # is not always the size asked for, so it is scaled here instead.
        self.set_snapshot(snapshot)
        self.preview_label.show()

        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def refit(self) -> None:
        """
        Re-measure against the grid as it is now.

        Panel items are built during plugin load, before the grid has been laid
        out even once - so the first sizes come from FALLBACK_CELL and are
        wrong the moment a real cell size exists.
        """
        width, height = self.panel.preview_size(self.span)
        if (width, height) == (self.preview_container.width(),
                               self.preview_container.height()):
            return
        self.preview_container.setFixedSize(width, height)
        label = getattr(self, "preview_label", None)
        if label is not None:
            label.setGeometry(0, 0, width, height)

    def set_snapshot(self, pixmap) -> None:
        self.refit()
        label = getattr(self, "preview_label", None)
        if label is None:
            return
        if pixmap is None or pixmap.isNull():
            label.setText(f"{self.span[0]}\u00d7{self.span[1]}")
            label.setFont(make_font(SIZES.S2, bold=True))
            set_style(label, "tiles", "tile-panel-ghost")
            return
        label.setPixmap(pixmap.scaled(
            label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

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
        self.dragging    = False
        return True   #swallow — Tile's own mousePressEvent must not also run

    def _on_tile_move(self, event: QMouseEvent) -> bool:
        if self.drag_start is None:
            return False

        delta = event.globalPosition().toPoint() - self.drag_start

        if not self.dragging and max(abs(delta.x()), abs(delta.y())) >= self.DRAG_THRESHOLD:
            self.dragging = True
            self.start_real_drag()

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
        was_dragging   = self.dragging
        self.dragging   = False
        self.drag_start = None

        if not was_dragging:
            return False   #wasn't a drag (just a click) — let Tile handle it normally

        gpos = event.globalPosition().toPoint()
        page = self.panel.page
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
            w = self.preview_container.width()
            h = self.preview_container.height()

        self.tile.resize(w, h)
        self.tile.show()

        page = self.panel.page
        if hasattr(page, 'notify_drag_started'):
            page.notify_drag_started()

    def restore_preview(self) -> None:
        self.tile.setParent(self.preview_container)
        self.tile.move(0, 0)
        self.tile.resize(self.preview_container.size())
        self.tile.show()

    def _cursor_outside_window(self, global_pos: QPoint) -> bool:
        window = self.panel.client.window
        window_global = window.mapToGlobal(QPoint(0, 0))
        window_rect    = window.rect().translated(window_global)
        return not window_rect.contains(global_pos)


##TILE PANEL

class TilePanel(Panel):

    # A third wider than the other panels. Previews are rendered at the size
    # the tile will actually be on the grid, and at the shared width the wider
    # spans had to be scaled down to fit - which is what made them look like
    # miniatures rather than previews.
    WIDTH = int(Panel.DEFAULT_WIDTH * 4 / 3)

    # Room taken by the panel's own padding and the item margins, so a preview
    # can be measured against what is actually left for it.
    CHROME = 56

    FALLBACK_CELL = 96   #before the grid has been laid out even once

    def __init__(self, client: "Client", page: QWidget, grid: "TileGrid"):
        super().__init__(client, width=self.WIDTH, edge="right")
        self.page  = page
        self.grid  = grid
        self.items: dict[str, TilePanelItem] = {}        #tile.KEY -> its live item
        self.size_items: dict[str, list] = {}            #tile.KEY -> every size entry
        self.closing = False

        self.page.destroyed.connect(self.deleteLater)

        self.setObjectName("tile_panel")
        self.apply_frosted_style()   #square corners — flush full-height panel

        layout = self.content_layout
        layout.setContentsMargins(16, 24, 16, 24)
        layout.setSpacing(12)

        #title + close button share one row
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)

        title = QLabel("Tiles")
        title.setFont(make_font(SIZES.M1, bold=True))
        set_style(title, "common", "text-strong")

        close_btn = QPushButton("\u2715")
        close_btn.setFixedSize(32, 32)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        set_style(close_btn, "tile_panel", "tile-panel-close")
        close_btn.clicked.connect(self.toggle)   #same toggle used to open it

        header_row.addWidget(title, stretch=1)
        header_row.addWidget(close_btn)
        layout.addLayout(header_row)

        sub = QLabel("Drag a tile onto the grid to place it.")
        sub.setFont(make_font(SIZES.S1))
        set_style(sub, "common", "text-muted")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        #scrollable list of TilePanelItem widgets
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(get_style_sheet("tile_panel_scroll"))
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Explicit: full-size previews make the list far taller than the panel,
        # so this is load-bearing rather than a default worth relying on.
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.viewport().setAutoFillBackground(False)

        self.list_widget = QWidget()
        set_style(self.list_widget, "common", "transparent")
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(20)
        self.list_layout.addStretch()   #keeps items pinned to the top as they're added

        scroll.setWidget(self.list_widget)
        layout.addWidget(scroll, stretch=1)

        # The third argument is the PARENT. Without it the animation belongs
        # to nothing, outlives the widget it animates, and fires `finished`
        # into an object that has gone - which inside a Qt signal aborts the
        # process rather than raising.
        self.anim = QPropertyAnimation(self, b"pos", self)
        self.anim.setDuration(220)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def preview_size(self, span: tuple) -> tuple:
        """
        The pixel size a tile of this span will occupy on the grid.

        Full size wherever it fits. Scaled down only when a span is wider than
        the panel can show, which after the width increase is rare.
        """
        grid = self.grid
        if grid is not None and getattr(grid, "cell_size", 0) > 0:
            width  = span[0] * grid.cell_size + (span[0] - 1) * grid.gap_x
            height = span[1] * grid.cell_size + (span[1] - 1) * grid.gap_y
        else:
            width  = span[0] * self.FALLBACK_CELL
            height = span[1] * self.FALLBACK_CELL

        usable = max(80, self.width() or self.WIDTH) - self.CHROME
        if width > usable:
            scale  = usable / width
            width  = width * scale
            height = height * scale

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
            made.append(item)
            #insert before the trailing stretch so new items stack at the bottom
            self.list_layout.insertWidget(self.list_layout.count() - 1, item)

        self.items[tile.KEY] = made[0]
        self.size_items[tile.KEY] = made

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
            pixmap = tile.grab()
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

    def place_tile_on_grid(self, tile: Tile, col: int, row: int,
                           span: tuple = None) -> None:
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
        if tile.KEY in self.items:
            tile.removeEventFilter(self.items[tile.KEY])
        self.remove_tile(tile.KEY)         #panel item no longer needed
        tile.setParent(self.grid)          #tile now belongs to TileGrid
        self.grid.add_tile(tile, col, row) #same entry point used for saved-position restoration
        self.toggle()                      #close the panel so the new tile is visible
        return True

    def tick_once(self) -> None:
        for key, items in self.size_items.items():
            live = self.items.get(key)
            if live is None:
                continue
            # Re-grab so a clock preview is not frozen at whatever time the
            # panel was first built - and so the first render after the grid
            # has a real cell size replaces the fallback-sized one.
            for item in items:
                try:
                    item.refit()
                    item.set_snapshot(self._snapshot(live.tile, item.span))
                except Exception:
                    pass

    def _page_alive(self) -> bool:
        return not sip.isdeleted(self.page)

    def toggle(self) -> None:
        if not self._page_alive():
            return
        if self.open:
            self.start_slide_out()
            self.anim.finished.connect(self.finish_slide_out)
            self.anim.finished.connect(lambda: self.anim.finished.disconnect())
        else:
            pw = self.page.width()
            ph = self.page.height()
            self.setFixedHeight(ph)
            self.anim.stop()
            self.move(pw, 0)
            self._shown_pos = QPoint(pw - self.WIDTH, 0)   #for refresh_backdrop()'s rect math
            self.refresh_backdrop()
            self.show()
            self.raise_()
            self.tick_once()
            self.anim.setStartValue(QPoint(pw, 0))
            self.anim.setEndValue(QPoint(pw - self.WIDTH, 0))
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