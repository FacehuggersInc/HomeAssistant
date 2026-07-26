from __future__ import annotations
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QHBoxLayout, QScrollArea, QPushButton
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

        title_lbl = QLabel(tile.NAME or tile.KEY)
        title_lbl.setFont(make_font(SIZES.S2, bold=True))
        set_style(title_lbl, "common", "text-strong")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(title_lbl)

        ratio = tile.grid_w / tile.grid_h
        if ratio >= 1:
            preview_w = self.MAX_PREVIEW_SIZE
            preview_h = int(self.MAX_PREVIEW_SIZE / ratio)
        else:
            preview_h = self.MAX_PREVIEW_SIZE
            preview_w = int(self.MAX_PREVIEW_SIZE * ratio)

        self.preview_container = QWidget()
        self.preview_container.setFixedSize(preview_w, preview_h)
        set_style(self.preview_container, "common", "transparent")
        outer.addWidget(self.preview_container, alignment=Qt.AlignmentFlag.AlignHCenter)

        tile.setParent(self.preview_container)
        tile.move(0, 0)
        tile.resize(preview_w, preview_h)
        tile.show()
        tile.update()

        tile.installEventFilter(self)

        try:
            tile.tick_once()
        except Exception:
            pass

    def eventFilter(self, watched, event) -> bool:
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
        if hasattr(page, 'trash_bin'):
            page.trash_bin.hide_after_drag()
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
            col = max(0, min(col, grid.cols - self.tile.grid_w))
            row = max(0, min(row, grid.rows - self.tile.grid_h))
            #the tile actually leaves the panel here — see place_tile_on_grid()
            self.panel.place_tile_on_grid(self.tile, col, row)
        else:
            self.restore_preview()

        return True   #swallow — Tile's own mouseReleaseEvent must not also run

    def start_real_drag(self) -> None:
        page = self.panel.page
        grid = self.panel.grid

        self.tile.setParent(page)
        self.tile.raise_()

        if grid.cell_size > 0:
            w = int(self.tile.grid_w * grid.cell_size + (self.tile.grid_w - 1) * grid.gap_x)
            h = int(self.tile.grid_h * grid.cell_size + (self.tile.grid_h - 1) * grid.gap_y)
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

    WIDTH = Panel.DEFAULT_WIDTH   #shared by every panel — see Panel.apply_frosted_style()

    def __init__(self, client: "Client", page: QWidget, grid: "TileGrid"):
        super().__init__(client, width=self.WIDTH, edge="right")
        self.page  = page
        self.grid  = grid
        self.items: dict[str, TilePanelItem] = {}   #tile.KEY -> its panel item
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
        scroll.viewport().setAutoFillBackground(False)

        self.list_widget = QWidget()
        set_style(self.list_widget, "common", "transparent")
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(16)
        self.list_layout.addStretch()   #keeps items pinned to the top as they're added

        scroll.setWidget(self.list_widget)
        layout.addWidget(scroll, stretch=1)

        self.anim = QPropertyAnimation(self, b"pos")
        self.anim.setDuration(220)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def add_tile(self, tile: Tile) -> None:
        if tile.KEY in self.items:
            #already listed — avoid duplicate items for the same tile
            return
        item = TilePanelItem(tile, self)
        self.items[tile.KEY] = item
        #insert before the trailing stretch so new items stack at the bottom
        self.list_layout.insertWidget(self.list_layout.count() - 1, item)

    def remove_tile(self, key: str) -> None:
        if key in self.items:
            self.items[key].deleteLater()
            del self.items[key]

    def place_tile_on_grid(self, tile: Tile, col: int, row: int) -> None:
        tile.removeEventFilter(self.items[tile.KEY])
        self.remove_tile(tile.KEY)         #panel item no longer needed
        tile.setParent(self.grid)          #tile now belongs to TileGrid
        self.grid.add_tile(tile, col, row) #same entry point used for saved-position restoration
        self.toggle()                      #close the panel so the new tile is visible

    def tick_once(self) -> None:
        for item in self.items.values():
            try:
                item.tile.tick_once()
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