from __future__ import annotations
from typing import TYPE_CHECKING, Callable, Optional

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QApplication
from PyQt6.QtCore import Qt, QPoint, QRect, QTimer, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QBrush, QPen, QMouseEvent

if TYPE_CHECKING:
    from src.main import Client


#TEMPORARY. Every gesture event, so a drag that misbehaves says where it went
#instead of being reasoned about. Grep the log for "[TileTrace]"; set this to
#False, or delete this block and every _trace call, to take it back out.
TRACE = True


class Tile(QWidget):

    move_requested   = pyqtSignal(object, int, int)
    resize_requested = pyqtSignal(object, int, int)
    remove_requested = pyqtSignal(object)

    DRAG_THRESHOLD = 8
    HOLD_MS        = 400     # press-and-wait before the handles appear
    #Whether anything inside this tile is meant to be pressed in its own
    #right. Off by default - see _pass_mouse_through.
    INTERACTIVE_CONTENT = False
    #How many button-less moves in a row mean the release really was lost.
    LOST_RELEASE_MOVES = 3
    #Whether this tile has a setup worth going back to. Off by default: most
    #tiles are what they are, and a pencil on one with nothing to edit is a
    #control that does nothing.
    EDITABLE = False

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
        #Consecutive moves that arrived with no button held. See
        #mouseMoveEvent - one of these is Qt talking to itself, several in a
        #row is a release that never arrived.
        self._no_button = 0
        self.drag_start: Optional[QPoint] = None
        #Whether the press was taken by a handle. See mouseReleaseEvent.
        self._handled = False
        #Where inside the tile a drag was started from, so it is positioned
        #from the pointer rather than by adding up deltas.
        self._grab: QPoint = QPoint(0, 0)
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

    #Below this many characters of a name, the elided version says nothing.
    #"Ch..." is not a shorter label, it is a worse one.
    MIN_LABEL_CHARS = 4

    def label_for(self, label, text: str, inset: int = 16) -> str:
        """
        The name to put on this tile at its current size, or nothing.

        Three answers, not two. It fits; it fits shortened; or there is no
        width at which it says anything and the tile is better off with just
        its picture.

        A 1x1 tile is always the third. Whatever the name, a square that size
        holds an icon and about three letters of it, and three letters of a
        name is not a name - it is a tile that looks broken.
        """
        from PyQt6.QtGui import QFontMetrics
        from PyQt6.QtCore import Qt

        text = str(text or "")
        if not text:
            return ""
        if self.grid_w <= 1 and self.grid_h <= 1:
            return ""

        # Measured from the TILE, not the label. A label's own width is
        # whatever the last layout pass gave it, and this runs before the
        # first one - so it reads as a handful of pixels and elides the name
        # to two letters, which then shrinks what the label asks for, which
        # cuts it down further on every resize.
        room = self.width() - inset
        if room <= 8:
            return text

        metrics = QFontMetrics(label.font())
        if metrics.horizontalAdvance(text) <= room:
            return text

        shortened = metrics.elidedText(text, Qt.TextElideMode.ElideRight, room)
        # Count what survived, not what was written: the ellipsis is not a
        # character somebody reads.
        if len(shortened.rstrip(".…").strip()) < self.MIN_LABEL_CHARS:
            return ""
        return shortened

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
            self._pass_mouse_through(widget)
            self.content_layout.addWidget(widget)

    def _pass_mouse_through(self, widget: QWidget) -> None:
        """
        Nothing inside a tile takes a press. The tile does.

        A tile is a thing on a grid: press and hold selects it, press and
        drag moves it, and a tap runs `on_click`. All three need the press to
        arrive HERE. Most content is labels, which ignore a press and let it
        through - but anything built from a control does not. A scroll area
        is the one that bites, because it looks like decoration and behaves
        like a button: `WeatherTile` at 3x3 and above fills itself with one,
        and a press anywhere on that area was swallowed by its viewport. The
        tile never learned it had been touched, so it could not be selected,
        could not be dragged, and appeared to ignore the finger entirely.

        Set on the whole subtree rather than on the one widget that swallowed
        it: a viewport is a child of the area, and a control nested three
        deep swallows a press just as well as one at the top.

        A tile that genuinely wants a control inside it sets
        `INTERACTIVE_CONTENT` and takes on the whole gesture problem itself -
        including how somebody is then meant to move it.
        """
        if self.INTERACTIVE_CONTENT:
            return
        widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        for child in widget.findChildren(QWidget):
            child.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

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

    def request_save(self) -> None:
        """
        Ask the grid to write the layout, including this tile's own state.

        A tile that changes something it reports through `tile_state()` has to
        say so. The grid saves on a drag and on a resize, which is every way
        the GRID changes a tile and no way the tile changes itself - so a
        bookmark chosen for a square was remembered until the page was rebuilt
        and then asked for again.
        """
        grid = self.parent()
        while grid is not None and not hasattr(grid, "save_positions"):
            grid = grid.parent()
        if grid is None:
            return
        try:
            grid.save_positions()
        except Exception as e:
            self.client.log("debug",
                            f"[Tiles] {self.KEY} could not save its state: {e}")

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
        # Two handles need twice their size plus a gap between them. Below
        # that they land on each other, and a tap in the overlap does
        # whichever was checked first - on a one-cell tile that was delete.
        room_across = self.width() >= size * 2 + 12

        if self.REMOVABLE:
            rects["remove"] = QRect(4, 4, size, size)
        if self.EDITABLE and room_across:
            # Top right, away from remove. A tile that holds a setup worth
            # returning to needs a way back to it, and holding to select then
            # pressing the tile itself would run the thing instead.
            #
            # Dropped rather than crowded on a tile too small for both:
            # resize is how it is made bigger, and bigger is where this
            # appears.
            rects["edit"] = QRect(self.width() - size - 4, 4, size, size)
        # Always. It is the only way to make a tile bigger, and a tile too
        # small for two handles is exactly the one somebody wants to resize.
        # It does crowd remove at one cell, which it did before this too.
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
        self._trace("HOLD-ELAPSED (selected)")
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
            elif name == "edit":
                p.setBrush(QBrush(QColor("#1c1c1c")))
                p.setPen(QPen(QColor("#e8c35a"), 2))
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
            elif name == "edit":
                # A pencil: a stroke with a tip, over its own line.
                p.drawLine(c.x() - arm, c.y() + arm - 1,
                           c.x() + arm - 2, c.y() - arm + 1)
                p.drawLine(c.x() - arm, c.y() + arm - 1, c.x() - arm + 5,
                           c.y() + arm - 1)
                p.drawLine(c.x() - arm, c.y() + arm - 1, c.x() - arm,
                           c.y() + arm - 6)
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


    def _trace(self, what: str, **facts) -> None:
        """TEMPORARY. See TRACE at the top of this module."""
        if not TRACE:
            return
        detail = " ".join(f"{k}={v}" for k, v in facts.items())
        try:
            self.client.log("info", f"[TileTrace] {self.KEY} {what} "
                                    f"span={self.grid_w}x{self.grid_h} "
                                    f"cell=({self.grid_col},{self.grid_row}) "
                                    f"drag={self.dragging} "
                                    f"resize={self.resizing} "
                                    f"start={'set' if self.drag_start else 'none'} "
                                    f"sel={self.selected} {detail}")
        except Exception:
            pass

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._trace("PRESS", button=int(event.button().value),
                    at=f"({event.position().toPoint().x()},"
                       f"{event.position().toPoint().y()})",
                    parent=type(self.parent()).__name__)
        if event.button() != Qt.MouseButton.LeftButton:
            return

        handle = self._handle_at(event.position().toPoint())
        if handle == "remove":
            self.remove_requested.emit(self)
            return
        if handle == "edit":
            # Remembered, because the release still arrives. Remove takes the
            # tile away so nothing lands afterwards, but edit leaves it here -
            # and the release then found a deselected tile with an on_click
            # and ran it, so pressing the pencil also pressed the tile.
            self._handled = True
            self.deselect()
            self.edit()
            return
        if handle:
            self._trace("PRESS-ON-HANDLE", handle=handle)
        if handle == "resize":
            self.resizing      = True
            self._resize_origin = event.globalPosition().toPoint()
            self._resize_span   = (self.grid_w, self.grid_h)
            self.raise_()
            return

        self._handled = False
        self.drag_start = event.globalPosition().toPoint()
        # Where in the tile the press landed. The drag positions the tile from
        # this rather than accumulating deltas - see mouseMoveEvent.
        self._grab = event.position().toPoint()
        self.dragging   = False
        self._selected_now = False
        self._hold.start()

    def _end_gesture(self) -> None:
        """
        Drop any in-progress drag or resize, whatever left it running.

        A drag abandoned this way is still put back on the grid. It used to
        just clear the flags, which left the tile wherever the pointer had
        got to - sitting between two cells, belonging to neither, and refusing
        to move again because `drag_start` was gone. Every way a drag can end
        has to end with the tile somewhere real.
        """
        self._trace("END-GESTURE (abandoned)")
        was_dragging = self.dragging

        self.resizing       = False
        self._resize_origin = None
        self.dragging       = False
        self.drag_start     = None
        self._hold.stop()

        if not was_dragging:
            return
        grid = self.parent()
        try:
            if grid is not None and hasattr(grid, "snap_tile"):
                grid.snap_tile(self)
        except Exception as e:
            self.client.log("warning",
                            f"[Tiles] Could not put a dropped tile back: {e}")

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        # Mouse tracking is on so the tile can highlight on hover, which means
        # moves arrive with no button held at all. Neither gesture may act on
        # those: a resize whose release went astray - the tile is re-parented
        # and re-sized mid-drag, so it can - would otherwise pick straight back
        # up the next time the pointer crossed the tile.
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            # Checked against the DEVICE, not just this event.
            #
            # An event's buttons() is a snapshot, and Qt makes events that
            # were never a real gesture: raising the tile at the start of a
            # drag re-stacks it, so Qt works out what is under the cursor
            # again and can deliver a synthetic hover move - with no button,
            # because it is not describing a press. Trusting it alone ended
            # the drag on the first move that started it, which reads as the
            # tile stopping dead under a finger that is still down.
            #
            # QApplication.mouseButtons() asks what is held right now - but
            # only about a MOUSE. A touchscreen delivers synthesised mouse
            # events, and on some platforms nothing is holding a button
            # during one, so this answers "released" all the way through a
            # drag somebody is still making with their finger.
            #
            # So the count, not the single event. Qt emits one synthetic move
            # when the raise at the start of a drag re-stacks the tile; a
            # release that genuinely went astray leaves the pointer moving
            # over the tile and produces them steadily. Waiting for a few in
            # a row tells those two apart without asking the platform a
            # question it cannot answer.
            self._trace("MOVE-NO-BUTTON",
                        app_buttons=int(QApplication.mouseButtons().value),
                        run=self._no_button)
            if QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
                self._no_button = 0
                return
            if not (self.resizing or self.dragging
                    or self.drag_start is not None):
                self._no_button = 0
                return

            self._no_button += 1
            if self._no_button < self.LOST_RELEASE_MOVES:
                return
            self.client.log("debug",
                            f"[Tiles] {self.KEY}: no button held for "
                            f"{self._no_button} moves - ending the gesture.")
            self._end_gesture()
            self.update()
            return

        self._no_button = 0

        if self.resizing:
            self._drag_resize(event.globalPosition().toPoint())
            return

        if self.drag_start is None:
            return

        delta = event.globalPosition().toPoint() - self.drag_start

        self._moves = getattr(self, "_moves", 0) + 1
        if self._moves <= 8 or self._moves % 10 == 0:
            self._trace("MOVE", n=self._moves,
                        delta=f"({delta.x()},{delta.y()})")

        if not self.dragging and max(abs(delta.x()), abs(delta.y())) >= self.DRAG_THRESHOLD:
            self._hold.stop()      # a move is a drag, not a hold
            self._trace("DRAG-BEGINS")
            self.dragging = True
            self.drag_origin = (self.grid_col, self.grid_row)
            self.raise_()
            self.update()
            grid = self.parent()
            page = grid.parent() if grid else None
            if page and hasattr(page, "notify_drag_started"):
                page.notify_drag_started()

        if self.dragging:
            # Positioned from where the pointer is now, not by adding up how
            # far it has moved since the last event.
            #
            # Deltas accumulate whatever they miss. A finger moving faster
            # than the events are delivered, a tile nudged by anything else,
            # a move that lands short - each one leaves the tile permanently
            # behind the finger, and it never catches up because every later
            # delta is measured from where it already is.
            grid = self.parent()
            here = event.globalPosition().toPoint()
            if grid is not None:
                want = grid.mapFromGlobal(here) - self._grab
                self.move(want)
                if self._moves <= 8 or self._moves % 10 == 0:
                    self._trace("MOVED", wanted=f"({want.x()},{want.y()})",
                                landed=f"({self.x()},{self.y()})")
            self.drag_start = here

            self.move_requested.emit(self, *self.screen_to_grid())

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
        self._trace("RELEASE", moves=getattr(self, "_moves", 0))
        self._moves = 0
        self._hold.stop()

        # A handle already dealt with this press. Whatever the tile does when
        # tapped is not also what was being asked for.
        if self._handled:
            self._handled = False
            return

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

    def edit(self) -> None:
        """
        Open whatever this tile is set up with.

        Overridden by tiles that set `EDITABLE`. The base does nothing rather
        than raising: a tile can turn the handle on and add this later without
        the handle being a crash in the meantime.
        """
        return None

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