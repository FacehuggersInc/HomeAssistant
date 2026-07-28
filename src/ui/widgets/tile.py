from __future__ import annotations
from typing import TYPE_CHECKING, Callable, Optional

from PyQt6.QtWidgets import QWidget, QVBoxLayout
from PyQt6.QtCore import Qt, QPoint, QRect, QTimer, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QMouseEvent

if TYPE_CHECKING:
    from src.main import Client


class Tile(QWidget):

    move_requested   = pyqtSignal(object, int, int)
    resize_requested = pyqtSignal(object, int, int)
    remove_requested = pyqtSignal(object)

    DRAG_THRESHOLD = 8
    HOLD_MS        = 400     # press-and-wait before the handles appear
    HANDLE         = 40      # finger-sized, same reasoning as the widget chrome
    HANDLE_PAD     = 10

    KEY  : str = ""
    NAME : str = ""
    ICON : str = ""

    RESIZABLE = True
    REMOVABLE = True

    MIN_GRID_W, MIN_GRID_H = 1, 1
    MAX_GRID_W, MAX_GRID_H = 8, 8

    # Sizes offered as separate entries in the tile panel. Empty means "derive
    # them from the variants", and a tile with many variants can set this to a
    # shorter list rather than advertising every one of them.
    PANEL_SIZES: list = []

    # A template stays in the panel and each drag-out places another copy,
    # the same way a MULTIPLE widget works. The copies get their own keys, so
    # they save and restore their positions independently.
    MULTIPLE = False

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
        # Where a drag started, so a drop onto an occupied block can put the
        # tile back exactly where it was picked up from.
        self.drag_origin: tuple[int, int] = (0, 0)

        self.bg_color  = QColor(bg_color)
        self.radius    = 10
        self.dragging  = False
        self.drag_start: Optional[QPoint] = None
        self.hovered   = False

        self.template_key: str = ""
        self.selected  = False
        self.resizing  = False
        # Whether THIS press is the one that selected the tile. Without it the
        # release that follows a successful hold immediately deselects again,
        # so the border appears and vanishes as the finger lifts.
        self._selected_now = False
        self._resize_origin: Optional[QPoint] = None
        self._resize_span:   tuple[int, int] = (grid_w, grid_h)

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMouseTracking(True)

        self.content_layout = QVBoxLayout(self)
        self.content_layout.setContentsMargins(12, 12, 12, 12)
        self.content_layout.setSpacing(6)

        # Size variants. Keys are (min_w, min_h) thresholds rather than exact
        # spans, so a tile does not need an entry for every size it could be
        # dragged to - 3x3 and 5x4 both land on the same "at least 3x3" entry.
        self._variants: dict[tuple[int, int], Callable] = {}
        self._variant_key: Optional[tuple[int, int]] = None
        self._content: Optional[QWidget] = None

        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.setInterval(self.HOLD_MS)
        self._hold.timeout.connect(self._select)

        self.build_variants()
        self.apply_span(grid_w, grid_h, force=True)

    ##VARIANTS

    def build_variants(self) -> None:
        """
        Override to register size variants with add_variant().

        A tile with no variants keeps whatever its constructor put in
        content_layout and never swaps it, which is the simple case.
        """
        pass

    def add_variant(self, min_w: int, min_h: int, builder: Callable) -> None:
        """
        builder() -> QWidget, shown once the tile is at least min_w x min_h.

        Thresholds, not exact sizes. The most demanding entry a tile satisfies
        wins, so registering (1,1) and (3,3) gives you a small layout that is
        replaced the moment the tile is dragged to three cells either way.
        """
        self._variants[(int(min_w), int(min_h))] = builder

    def variant_for(self, w: int, h: int) -> Optional[tuple[int, int]]:
        best = None
        best_score = None
        for (min_w, min_h) in self._variants:
            if w < min_w or h < min_h:
                continue
            # Area first, then the larger single dimension - so (3,3) beats
            # (1,1), and (2,3) beats (3,1) at 3x3 where both fit.
            score = (min_w * min_h, max(min_w, min_h), min_w)
            if best_score is None or score > best_score:
                best, best_score = (min_w, min_h), score
        return best

    def apply_span(self, w: int, h: int, force: bool = False) -> bool:
        """
        Set the tile's span and swap in the variant that fits.

        Returns whether the variant changed. The grid calls this live during a
        resize drag, so it has to be cheap when nothing has changed - which is
        most frames.
        """
        w = max(self.MIN_GRID_W, min(int(w), self.MAX_GRID_W))
        h = max(self.MIN_GRID_H, min(int(h), self.MAX_GRID_H))
        self.grid_w, self.grid_h = w, h

        key = self.variant_for(w, h)
        if key == self._variant_key and not force:
            return False

        self._variant_key = key
        if key is not None:
            self._swap_content(self._variants[key]())

        # After the swap, not before: a variant that has just been built has
        # nothing in it yet, and tick_once is what fills it.
        self.tick_once()
        return True

    def _swap_content(self, widget: Optional[QWidget]) -> None:
        if self._content is not None:
            self.content_layout.removeWidget(self._content)
            self._content.setParent(None)
            self._content.deleteLater()
            self._content = None
        if widget is not None:
            self._content = widget
            self.content_layout.addWidget(widget)

    def variant_key(self) -> Optional[tuple[int, int]]:
        return self._variant_key

    def make_copy(self, key: str, span: tuple = None):
        """A fresh instance of this tile for MULTIPLE templates."""
        span = span or (self.grid_w, self.grid_h)
        copy = type(self)(self.client, grid_w=span[0], grid_h=span[1])
        copy.KEY = key
        copy.template_key = self.KEY
        return copy

    def panel_sizes(self) -> list:
        """Which starting sizes the tile panel should offer for this tile."""
        if self.PANEL_SIZES:
            return [(int(w), int(h)) for w, h in self.PANEL_SIZES]
        if self._variants:
            return sorted(self._variants.keys())
        return [(self.grid_w, self.grid_h)]

    ##TICK

    def tick(self) -> None:
        pass

    def tick_once(self) -> None:
        self.tick()

    ##APPEARANCE

    def set_bg_color(self, color: str) -> None:
        self.bg_color = QColor(color)
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Fixed 12px margins leave a one-cell tile with almost nothing inside
        # them, so its content overflows and the preview looks clipped. Scale
        # them with the tile instead.
        margin = max(3, min(12, min(self.width(), self.height()) // 12))
        self.content_layout.setContentsMargins(margin, margin, margin, margin)

    ##HANDLES

    def _handle_rects(self) -> dict:
        """Only while selected. An always-live delete corner is a mis-tap away."""
        if not self.selected or self.dragging:
            return {}
        rects = {}
        size = self.HANDLE
        if self.REMOVABLE:
            rects["remove"] = QRect(4, 4, size, size)
        if self.RESIZABLE:
            rects["resize"] = QRect(self.width() - size - 4,
                                    self.height() - size - 4, size, size)
        return rects

    def _handle_at(self, point: QPoint) -> str:
        for name, rect in self._handle_rects().items():
            if rect.adjusted(-self.HANDLE_PAD, -self.HANDLE_PAD,
                             self.HANDLE_PAD, self.HANDLE_PAD).contains(point):
                return name
        return ""

    def _select(self) -> None:
        self.selected = True
        self._selected_now = True
        grid = self.parent()
        if grid is not None and hasattr(grid, "deselect_all"):
            grid.deselect_all(except_tile=self)
        self.update()

    def deselect(self) -> None:
        # The handles are what a resize is started from, so losing them has to
        # end one that is somehow still running.
        self._end_gesture()
        if self.selected:
            self.selected = False
            self.update()

    def _paint_handles(self, p: QPainter) -> None:
        for name, rect in self._handle_rects().items():
            if name == "remove":
                p.setBrush(QBrush(QColor("#7a2020")))
                p.setPen(QPen(QColor("#e08a8a"), 2))
            else:
                p.setBrush(QBrush(QColor("#1c1c1c")))
                p.setPen(QPen(QColor("#6fa8e0"), 2))
            p.drawEllipse(rect)

            p.setPen(QPen(QColor("#f2f2f2"), 3))
            c = rect.center()
            arm = self.HANDLE // 5

            if name == "remove":
                # A bin, matching the widget chrome - an X reads as "close".
                p.drawLine(c.x() - arm, c.y() - arm + 2, c.x() + arm, c.y() - arm + 2)
                p.drawLine(c.x() - 3, c.y() - arm - 1, c.x() + 3, c.y() - arm - 1)
                p.drawLine(c.x() - arm + 3, c.y() - arm + 2, c.x() - arm + 4, c.y() + arm)
                p.drawLine(c.x() + arm - 3, c.y() - arm + 2, c.x() + arm - 4, c.y() + arm)
                p.drawLine(c.x() - arm + 4, c.y() + arm, c.x() + arm - 4, c.y() + arm)
            else:
                p.drawLine(c.x() - arm, c.y() + arm, c.x() + arm, c.y() - arm)
                p.drawLine(c.x() + arm, c.y() - arm, c.x() + arm - 4, c.y() - arm)
                p.drawLine(c.x() - arm, c.y() + arm, c.x() - arm, c.y() + arm - 4)

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

        if self.selected and not self.dragging:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor("#6fa8e0"), 2, Qt.PenStyle.DashLine))
            p.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2),
                              self.radius, self.radius)
            self._paint_handles(p)


    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return

        handle = self._handle_at(event.position().toPoint())
        if handle == "remove":
            self.remove_requested.emit(self)
            return
        if handle == "resize":
            self.resizing      = True
            self._resize_origin = event.globalPosition().toPoint()
            self._resize_span   = (self.grid_w, self.grid_h)
            self.raise_()
            return

        self.drag_start = event.globalPosition().toPoint()
        self.dragging   = False
        self._selected_now = False
        self._hold.start()

    def _end_gesture(self) -> None:
        """Drop any in-progress drag or resize, whatever left it running."""
        self.resizing       = False
        self._resize_origin = None
        self.dragging       = False
        self.drag_start     = None
        self._hold.stop()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        # Mouse tracking is on so the tile can highlight on hover, which means
        # moves arrive with no button held at all. Neither gesture may act on
        # those: a resize whose release went astray - the tile is re-parented
        # and re-sized mid-drag, so it can - would otherwise pick straight back
        # up the next time the pointer crossed the tile.
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            if self.resizing or self.dragging or self.drag_start is not None:
                self._end_gesture()
                self.update()
            return

        if self.resizing:
            self._drag_resize(event.globalPosition().toPoint())
            return

        if self.drag_start is None:
            return

        delta = event.globalPosition().toPoint() - self.drag_start

        if not self.dragging and max(abs(delta.x()), abs(delta.y())) >= self.DRAG_THRESHOLD:
            self._hold.stop()      # a move is a drag, not a hold
            self.dragging = True
            self.drag_origin = (self.grid_col, self.grid_row)
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

    def _drag_resize(self, global_point: QPoint) -> None:
        """Turn pointer travel into a cell span, and swap variants as it changes."""
        grid = self.parent()
        if grid is None or not hasattr(grid, "cell_size") or grid.cell_size <= 0:
            return

        step_x = grid.cell_size + grid.gap_x
        step_y = grid.cell_size + grid.gap_y
        delta  = global_point - self._resize_origin

        want_w = self._resize_span[0] + round(delta.x() / step_x)
        want_h = self._resize_span[1] + round(delta.y() / step_y)

        want_w = max(self.MIN_GRID_W, min(want_w, self.MAX_GRID_W, grid.cols - self.grid_col))
        want_h = max(self.MIN_GRID_H, min(want_h, self.MAX_GRID_H, grid.rows - self.grid_row))

        if (want_w, want_h) == (self.grid_w, self.grid_h):
            return

        # Growing into another tile is refused rather than clamped to the
        # largest free size - a resize that stops dead at the neighbour is
        # easier to understand than one that silently picks its own limit.
        if hasattr(grid, "can_resize_to") and not grid.can_resize_to(self, want_w, want_h):
            return

        # Live, not on release: the variant swaps as the tile crosses each
        # threshold, so the size being chosen is the size being previewed.
        self.apply_span(want_w, want_h)
        self.resize_requested.emit(self, want_w, want_h)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._hold.stop()

        if self.resizing:
            self.resizing = False
            self._resize_origin = None
            grid = self.parent()
            if grid is not None and hasattr(grid, "place_tile"):
                grid.place_tile(self, self.grid_col, self.grid_row)
            if grid is not None and hasattr(grid, "save_positions"):
                grid.save_positions()
            self.update()
            return

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
        elif self._selected_now:
            # The release that completed the hold. Selection stays.
            self._selected_now = False
        elif self.selected:
            # A later tap on an already-selected tile puts the handles away
            # rather than firing its action - the same tap would otherwise
            # both dismiss and activate.
            self.deselect()
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
        if not (self.resizing or self.dragging):
            # Leaving without a button held means whatever was in progress is
            # over; leaving mid-drag is normal and must not cancel it.
            self._end_gesture()
        self.hovered = False
        self.update()
        self.setCursor(Qt.CursorShape.ArrowCursor)