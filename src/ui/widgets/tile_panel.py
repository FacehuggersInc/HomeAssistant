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
    A single row in the tile panel representing a registered tile.
    Drag this row onto the grid to place the tile.
    """

    DRAG_THRESHOLD = 8

    def __init__(self, tile: Tile, panel: "TilePanel"):
        super().__init__()
        self.tile       = tile
        self.panel      = panel
        self.drag_start: QPoint | None = None
        self.dragging   = False
        self.ghost:      QWidget | None = None

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

        #icon
        icon_lbl = QLabel()
        icon_lbl.setFixedSize(32, 32)
        icon_lbl.setStyleSheet("background: transparent; border: none;")
        try:
            icon_lbl.setPixmap(qta.icon(tile.ICON or "mdi.puzzle", color="white").pixmap(28, 28))
        except Exception:
            icon_lbl.setPixmap(qta.icon("mdi.puzzle", color="white").pixmap(28, 28))

        #text
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
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start = event.globalPosition().toPoint()
            self.dragging   = False

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.drag_start is None:
            return
        delta = event.globalPosition().toPoint() - self.drag_start
        if not self.dragging and max(abs(delta.x()), abs(delta.y())) >= self.DRAG_THRESHOLD:
            self.dragging = True
            self.create_ghost()
            page = self.panel.page
            if hasattr(page, 'notify_drag_started'):
                page.notify_drag_started()

        if self.dragging and self.ghost:
            #map global pos to the page (overlay parent)
            page = self.panel.page
            if hasattr(page, 'trash_bin'):
                page.trash_bin.set_hot(page.trash_bin.is_over(event.globalPosition().toPoint()))
            local = page.mapFromGlobal(event.globalPosition().toPoint())
            self.ghost.move(local.x() - self.ghost.width() // 2,
                            local.y() - self.ghost.height() // 2)
            #forward to grid for guide box
            grid = self.panel.grid
            grid_pos = grid.mapFromGlobal(event.globalPosition().toPoint())
            col = int((grid_pos.x() - grid.origin_x) // (grid.cell_size + grid.gap))
            row = int((grid_pos.y() - grid.origin_y) // (grid.cell_size + grid.gap))
            col = max(0, min(col, grid.cols - self.tile.grid_w))
            row = max(0, min(row, grid.rows - self.tile.grid_h))
            grid.hover_col     = col
            grid.hover_row     = row
            grid.dragging_tile = self.tile
            grid.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        was_dragging = self.dragging
        self.dragging   = False
        self.drag_start = None

        if self.ghost:
            self.ghost.deleteLater()
            self.ghost = None

        if was_dragging:
            page = self.panel.page
            gpos = event.globalPosition().toPoint()
            if hasattr(page, 'trash_bin'):
                page.trash_bin.hide_after_drag()
            self.panel.grid.dragging_tile = None
            self.panel.grid.hover_col     = -1
            self.panel.grid.hover_row     = -1
            self.panel.grid.update()

            #check if dropped on the grid
            grid = self.panel.grid
            grid_global = grid.mapToGlobal(QPoint(0, 0))
            drop_global = event.globalPosition().toPoint()
            grid_rect_global = grid.rect().translated(grid_global)

            if grid_rect_global.contains(drop_global):
                grid_pos = grid.mapFromGlobal(drop_global)
                col = int((grid_pos.x() - grid.origin_x) // (grid.cell_size + grid.gap))
                row = int((grid_pos.y() - grid.origin_y) // (grid.cell_size + grid.gap))
                col = max(0, min(col, grid.cols - self.tile.grid_w))
                row = max(0, min(row, grid.rows - self.tile.grid_h))
                self.panel.place_tile_on_grid(self.tile, col, row)

    def create_ghost(self) -> None:
        """Floating semi-transparent preview that follows the cursor."""
        page  = self.panel.page
        tile  = self.tile
        grid  = self.panel.grid

        #estimate pixel size based on grid cell size
        if grid.cell_size > 0:
            w = tile.grid_w * grid.cell_size + (tile.grid_w - 1) * grid.gap
            h = tile.grid_h * grid.cell_size + (tile.grid_h - 1) * grid.gap
        else:
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
    Slide-in panel on the right side of the tiles page.
    Lists all registered-but-unplaced tiles.
    Tiles are dragged from here onto the grid.
    """

    WIDTH = 280

    def __init__(self, client: "Client", page: QWidget, grid: "TileGrid"):
        super().__init__(page)
        self.client   = client
        self.page     = page
        self.grid     = grid
        self.rows:    dict[str, TilePanelRow] = {}
        self.open     = False

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
        close_btn.clicked.connect(self.toggle)

        header_row.addWidget(title, stretch=1)
        header_row.addWidget(close_btn)
        layout.addLayout(header_row)

        sub = QLabel("Drag a tile onto the grid to place it.")
        sub.setFont(make_font(SIZES.S1))
        sub.setStyleSheet(f"color: {COLORS.DARK.TEXT.MUTED}; background: transparent;")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        #scrollable list
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
        self.list_layout.addStretch()

        scroll.setWidget(self.list_widget)
        layout.addWidget(scroll, stretch=1)

        #slide animation
        self.anim = QPropertyAnimation(self, b"pos")
        self.anim.setDuration(220)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def add_tile(self, tile: Tile) -> None:
        """Register a tile in the panel (not yet on the grid)."""
        if tile.KEY in self.rows:
            return
        row = TilePanelRow(tile, self)
        self.rows[tile.KEY] = row
        #insert before the stretch
        self.list_layout.insertWidget(self.list_layout.count() - 1, row)

    def remove_tile(self, key: str) -> None:
        if key in self.rows:
            self.rows[key].deleteLater()
            del self.rows[key]

    def place_tile_on_grid(self, tile: Tile, col: int, row: int) -> None:
        """Move tile from panel onto the grid at the given position."""
        self.remove_tile(tile.KEY)
        tile.setParent(self.grid)
        self.grid.add_tile(tile, col, row)
        self.toggle()   #close panel after placing

    def tick_once(self) -> None:
        """Tick all panel tiles once so previews are current."""
        for tile in [r.tile for r in self.rows.values()]:
            try:
                tile.tick_once()
            except Exception:
                pass

    def toggle(self) -> None:
        pw = self.page.width()
        ph = self.page.height()
        self.setFixedHeight(ph)
        self.anim.stop()
        if self.open:
            self.anim.setStartValue(self.pos())
            self.anim.setEndValue(QPoint(pw, 0))
            self.anim.finished.connect(self.hide)
            self.anim.finished.connect(lambda: self.anim.finished.disconnect())
            self.open = False
        else:
            self.move(pw, 0)
            self.show()
            self.raise_()
            self.tick_once()
            self.anim.setStartValue(QPoint(pw, 0))
            self.anim.setEndValue(QPoint(pw - self.WIDTH, 0))
            self.open = True
        self.anim.start()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.setFixedHeight(self.page.height())