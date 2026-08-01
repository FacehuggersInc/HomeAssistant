"""
The layer stickers live on, over the month grid.

Not a WidgetFramework. That one places widgets against the edges of a page and
saves where they sit; a calendar sticker belongs to a day box, and the box it
belongs to is somewhere different every month. What they share is the idea, not
the machinery.

One widget over the whole grid rather than a child of each day cell: a sticker
may be dragged from one day to another, and a child cannot cross into its
sibling. The layer also draws above the cells without being repainted by them.

The geometry is module-level and takes plain rectangles, so where a sticker
lands is answerable without a running page - see check_calendar_stickers.py.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Optional, TYPE_CHECKING

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QPoint, QRect, QSize
from PyQt6.QtGui import QPainter, QPixmap, QMovie, QColor, QPen, QBrush

from .stickers import DEFAULT_SCALE

if TYPE_CHECKING:
    from src.main import Client

#How big the delete and unlock controls are, and how far from the sticker.
CHROME = 34
CHROME_GAP = 6
#How far a press has to travel before it counts as a drag rather than a tap.
DRAG_SLOP = 6
#How far outside the Done control still counts as pressing it.
DONE_PAD = 14


def _distance(a: QPoint, b: QPoint) -> float:
    return math.hypot(b.x() - a.x(), b.y() - a.y())


def _bearing(centre: QPoint, point: QPoint) -> float:
    """Degrees clockwise from centre to point."""
    return math.degrees(math.atan2(point.y() - centre.y(),
                                   point.x() - centre.x()))


## -- geometry ----------------------------------------------------------------
#
# Plain rectangles in, plain answers out. Nothing here needs a page.

def cell_under(point: QPoint, boxes: dict):
    """The key whose box contains `point`, or None."""
    for key, box in boxes.items():
        if box.contains(point):
            return key
    return None


def nearest_cell(point: QPoint, boxes: dict):
    """
    The key whose box is nearest `point`.

    A sticker dropped in the gap between two cells, or just off the edge of the
    grid, belongs to the day it is closest to rather than nowhere. Measured
    centre to centre, which is what "the day it looks like it is on" means when
    a sticker is straddling a gutter.
    """
    if not boxes:
        return None
    inside = cell_under(point, boxes)
    if inside is not None:
        return inside

    def distance(item):
        centre = item[1].center()
        return (centre.x() - point.x()) ** 2 + (centre.y() - point.y()) ** 2

    return min(boxes.items(), key=distance)[0]


def to_fraction(point: QPoint, box: QRect):
    """A point inside a box, as a fraction of it."""
    width = max(1, box.width())
    height = max(1, box.height())
    return ((point.x() - box.x()) / width,
            (point.y() - box.y()) / height)


def to_point(fraction_x: float, fraction_y: float, box: QRect) -> QPoint:
    """The centre a fraction names, back in pixels."""
    return QPoint(int(round(box.x() + fraction_x * box.width())),
                  int(round(box.y() + fraction_y * box.height())))


def sticker_rect(fraction_x: float, fraction_y: float, scale: float,
                 box: QRect) -> QRect:
    """
    Where a sticker is drawn inside a day box.

    Sized against the box's shorter side so a sticker is the same size on a
    month with five weeks in it as on one with six, and centred on its point
    rather than hung from a corner - a fraction names where the middle of the
    picture is, which is what somebody dragging it is aiming with.
    """
    side = max(8, int(round(min(box.width(), box.height()) * scale)))
    centre = to_point(fraction_x, fraction_y, box)
    return QRect(centre.x() - side // 2, centre.y() - side // 2, side, side)


class StickerLayer(QWidget):
    """Draws and moves the stickers stuck to the month on screen."""

    def __init__(self, page, store, client: "Client"):
        super().__init__(page)
        self.page = page
        self.store = store
        self.client = client

        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setMouseTracking(True)

        # Transparent to the mouse until somebody says they are arranging
        # stickers.
        #
        # This covers the whole page, so while it takes mouse events nothing
        # underneath receives any - not the toolbar, not a day box. A press it
        # does not want cannot be passed down either: ignoring an event sends
        # it to the PARENT, and the buttons below are siblings.
        #
        # So it is only in the way while it needs to be. The toolbar button
        # turns it on, and the Done control on the layer turns it off.
        self.editing = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._images: dict = {}          # filename -> QPixmap or QMovie
        self._visible: list = []         # (sticker, day) on screen now
        self.selected: Optional[str] = None

        self._press: Optional[QPoint] = None
        self._dragging = False
        self._drag_key: Optional[str] = None
        #Where a free sticker would land if the drag ended now, and where it
        #is being held while the drag is in progress.
        self._pending_day: Optional[date] = None
        self._drag_pos: Optional[QPoint] = None
        #Where the press landed relative to the sticker it picked up, so a
        #drag moves it rather than snapping its middle to the finger.
        self._grab = QPoint(0, 0)

        #A resize or a turn in progress. `_handle_key` is None while the one
        #being placed is the subject, since it is not in the store yet.
        self._handle: Optional[str] = None
        self._handle_key: Optional[str] = None
        self._handle_centre = QPoint()
        self._handle_start = QPoint()
        self._handle_scale = DEFAULT_SCALE
        self._handle_angle = 0.0
        self._handle_reach = 1.0
        # A sticker being placed for the first time, before it is committed
        # to anything: `{"image": name, "pos": QPoint}` in layer coordinates.
        #
        # Held here rather than in the store, because until Done is pressed it
        # is not on a day. Writing it to a day the moment it appears takes the
        # choice away and then asks about the event as though one had been
        # made.
        self.pending: Optional[dict] = None
        self._on_placed = None

    ## -- editing

    #The Done control, top centre, while stickers are being arranged.
    DONE_W, DONE_H, DONE_TOP = 120, 38, 8

    def set_editing(self, state: bool) -> None:
        """Take or release the mouse for the whole page."""
        state = bool(state)
        if state == self.editing:
            return
        self.editing = state
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                          not state)
        if not state:
            self.selected = None
            self.pending = None
        self.update()

    def done_rect(self) -> QRect:
        return QRect((self.width() - self.DONE_W) // 2, self.DONE_TOP,
                     self.DONE_W, self.DONE_H)

    ## -- what is on screen

    def day_boxes(self) -> dict:
        """`{date: QRect}` for every day cell, in this layer's coordinates."""
        boxes = {}
        for cell in getattr(self.page, "cells", []):
            day = getattr(cell, "day", None)
            if day is None or not cell.isVisible():
                continue
            # Through global coordinates, not mapTo(self). A day cell is a
            # SIBLING of this layer - both are children of the page - and
            # mapTo needs its target to be an ancestor. Given a sibling it
            # refuses, warns, and answers with a point that is not on screen.
            top_left = self.mapFromGlobal(cell.mapToGlobal(QPoint(0, 0)))
            boxes[day] = QRect(top_left, cell.size())
        return boxes

    def refresh(self) -> None:
        """Re-read the store for the month now on screen."""
        boxes = self.day_boxes()
        found = self.store.for_days(list(boxes))
        self._visible = [(sticker, day)
                         for day, stickers in found.items()
                         for sticker in stickers]
        # A sticker whose day left the screen cannot stay selected.
        keys = {s.key for s, _d in self._visible}
        if self.selected not in keys:
            self.selected = None
        self.update()

    ## -- images

    def _image(self, name: str):
        if name in self._images:
            return self._images[name]

        entry = None
        try:
            library = self.client.public.stickers
            entry = library["get"](name)
        except Exception:
            entry = None
        if entry is None:
            self._images[name] = None
            return None

        path = str(entry.path)
        loaded = None
        # "animated", which is what the library calls it - see kind_of() in
        # CoreWidgetsBundle/stickers.py. Asking for "gif" matched nothing, so
        # every animation drew as its first frame and never moved.
        if getattr(entry, "kind", "") == "animated":
            movie = QMovie(path, parent=self)
            if movie.isValid():
                movie.frameChanged.connect(self.update)
                movie.start()
                loaded = movie
        if loaded is None:
            pixmap = QPixmap(path)
            loaded = pixmap if not pixmap.isNull() else None

        self._images[name] = loaded
        return loaded

    @staticmethod
    def _frame(image) -> Optional[QPixmap]:
        if image is None:
            return None
        if isinstance(image, QMovie):
            return image.currentPixmap()
        return image

    ## -- painting

    def _draw(self, painter: QPainter, image: str, rect: QRect,
              angle: float = 0.0) -> None:
        """Draw one sticker in `rect`, turned by `angle` about its middle."""
        frame = self._frame(self._image(image))
        if frame is None or frame.isNull():
            return
        scaled = frame.scaled(rect.size(),
                              Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
        painter.save()
        if angle:
            centre = rect.center()
            painter.translate(centre)
            painter.rotate(angle)
            painter.translate(-centre)
        painter.drawPixmap(
            rect.x() + (rect.width() - scaled.width()) // 2,
            rect.y() + (rect.height() - scaled.height()) // 2,
            scaled)
        painter.restore()

    def paintEvent(self, event) -> None:
        if not self._visible and not self.editing:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        boxes = self.day_boxes()
        for sticker, day in self._visible:
            rect = self.rect_of(sticker, day, boxes)
            if rect is None:
                continue

            # The one being dragged loose is outlined where it would land.
            if (sticker.key == self._drag_key and self._drag_pos is not None
                    and self._pending_day is not None):
                landing = boxes.get(self._pending_day)
                if landing is not None:
                    painter.setBrush(QBrush(QColor(47, 240, 142, 26)))
                    painter.setPen(QPen(QColor(47, 240, 142, 200), 2,
                                        Qt.PenStyle.DashLine))
                    painter.drawRoundedRect(landing.adjusted(1, 1, -1, -1), 8, 8)

            self._draw(painter, sticker.image, rect, sticker.angle)
            if sticker.key == self.selected:
                self._paint_chrome(painter, sticker, rect)

        if self.pending is not None:
            self._paint_pending(painter)

        if self.editing:
            self._paint_done(painter)
        painter.end()

    def rect_of(self, sticker, day, boxes: dict) -> Optional[QRect]:
        """
        Where a sticker is drawn now.

        Its box, or the pointer while it is being dragged loose - a free
        sticker mid-drag is not in any box yet.
        """
        if (sticker.key == self._drag_key and self._drag_pos is not None
                and not sticker.locked):
            reference = boxes.get(self._pending_day) or next(iter(boxes.values()), None)
            if reference is None:
                return None
            side = max(8, int(round(min(reference.width(), reference.height())
                                    * sticker.scale)))
            point = self._drag_pos
            return QRect(point.x() - side // 2, point.y() - side // 2,
                         side, side)

        box = boxes.get(day)
        if box is None:
            return None
        return sticker_rect(sticker.x, sticker.y, sticker.scale, box)

    def _paint_pending(self, painter: QPainter) -> None:
        """
        The sticker being placed, and the box it would land in.

        The box is outlined as it moves, so the answer to "which day is this
        going on" is on screen before Done is pressed rather than after.
        """
        boxes = self.day_boxes()
        landing = nearest_cell(self.pending["pos"], boxes)
        if landing is not None:
            box = boxes[landing]
            painter.setBrush(QBrush(QColor(47, 240, 142, 26)))
            painter.setPen(QPen(QColor(47, 240, 142, 200), 2,
                                Qt.PenStyle.DashLine))
            painter.drawRoundedRect(box.adjusted(1, 1, -1, -1), 8, 8)

        rect = self.pending_rect()
        self._draw(painter, self.pending["image"], rect,
                   self.pending.get("angle", 0.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#2ff08e"), 2, Qt.PenStyle.DashLine))
        painter.drawRoundedRect(rect.adjusted(-3, -3, 3, 3), 8, 8)
        self._paint_handles(painter, rect)

    def pending_rect(self) -> QRect:
        """Where the sticker being placed is drawn."""
        boxes = self.day_boxes()
        landing = nearest_cell(self.pending["pos"], boxes)
        side = self._pending_side(boxes.get(landing))
        centre = self.pending["pos"]
        return QRect(centre.x() - side // 2, centre.y() - side // 2, side, side)

    def _pending_side(self, box) -> int:
        """How big to draw the one being placed, before it has a box."""
        scale = self.pending.get("scale") if self.pending else None
        from .stickers import DEFAULT_SCALE
        scale = DEFAULT_SCALE if scale is None else scale
        if box is not None:
            return max(8, int(round(min(box.width(), box.height()) * scale)))
        return max(8, int(round(120 * scale)))

    def _paint_done(self, painter: QPainter) -> None:
        """
        The way out of sticker mode, and the sign that you are in it.

        On the layer rather than in the toolbar: the toolbar is underneath and
        cannot be pressed while this is up, so the control that ends the mode
        has to be part of what is holding it.
        """
        box = self.done_rect()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(47, 240, 142, 235)))
        painter.drawRoundedRect(box, box.height() // 2, box.height() // 2)
        painter.setPen(QPen(QColor("#0d1a12")))
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, "Done")

    def _paint_chrome(self, painter: QPainter, sticker, rect: QRect) -> None:
        """A border, and the controls for the selected sticker."""
        painter.setBrush(Qt.BrushStyle.NoBrush)
        colour = QColor("#ffb454") if sticker.locked else QColor("#6fa8e0")
        painter.setPen(QPen(colour, 2, Qt.PenStyle.DashLine))
        painter.drawRoundedRect(rect.adjusted(-3, -3, 3, 3), 8, 8)

        self._paint_handles(painter, rect)
        for name, box in self.chrome_rects(sticker, rect).items():
            if name in ("scale", "rotate"):
                continue
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(20, 20, 24, 220)))
            painter.drawEllipse(box)
            painter.setPen(QPen(QColor("#f0f0f4"), 2))
            middle = box.center()
            if name == "remove":
                offset = box.width() // 5
                painter.drawLine(middle.x() - offset, middle.y() - offset,
                                 middle.x() + offset, middle.y() + offset)
                painter.drawLine(middle.x() + offset, middle.y() - offset,
                                 middle.x() - offset, middle.y() + offset)
            elif name == "unlock":
                # An open shackle: the sticker comes off the event.
                painter.setPen(QPen(QColor("#ffb454"), 2))
                body = QRect(middle.x() - 6, middle.y() - 1, 12, 9)
                painter.drawRoundedRect(body, 2, 2)
                painter.drawArc(QRect(middle.x() - 1, middle.y() - 10, 10, 11),
                                0, 180 * 16)

    def chrome_rects(self, sticker, rect: QRect) -> dict:
        """The controls on a selected sticker, in layer coordinates."""
        controls = dict(self.handle_rects(rect))
        top = rect.top() - CHROME - CHROME_GAP
        if top < 0:
            top = rect.bottom() + CHROME_GAP
        controls["remove"] = QRect(rect.right() - CHROME // 2, top,
                                   CHROME, CHROME)
        if sticker.locked:
            controls["unlock"] = QRect(rect.left() - CHROME // 2, top,
                                       CHROME, CHROME)
        return controls

    def handle_rects(self, rect: QRect) -> dict:
        """
        Size and turn, just outside the two bottom corners.

        Outside, not straddling. A sticker in a day box is about forty pixels
        across and a control is thirty-four, so two of them centred on its
        corners cover the whole picture - there is then nothing left to press
        that is the sticker itself, and dragging it becomes impossible.

        Present while placing as well as while editing: a sticker is sized and
        angled as it is put down, and having to place it, press Done and then
        pick it up again to turn it is three steps for one decision.
        """
        controls = {
            "scale": QRect(rect.right() + CHROME_GAP,
                           rect.bottom() - CHROME // 2, CHROME, CHROME),
            "rotate": QRect(rect.left() - CHROME - CHROME_GAP,
                            rect.bottom() - CHROME // 2, CHROME, CHROME),
        }
        # Brought back inside the page. A sticker against the right-hand edge
        # of the month would otherwise put its size control off the screen.
        for name, box in controls.items():
            moved = QRect(box)
            if moved.right() > self.width():
                moved.moveRight(self.width() - 2)
            if moved.left() < 0:
                moved.moveLeft(2)
            if moved.bottom() > self.height():
                moved.moveBottom(self.height() - 2)
            controls[name] = moved
        return controls

    def _paint_handles(self, painter: QPainter, rect: QRect) -> None:
        """The size and turn controls, drawn the same in both modes."""
        for name, box in self.handle_rects(rect).items():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(20, 20, 24, 220)))
            painter.drawEllipse(box)
            painter.setPen(QPen(QColor("#f0f0f4"), 2))
            middle = box.center()
            if name == "scale":
                # A diagonal with a head at each end.
                painter.drawLine(middle.x() - 6, middle.y() + 6,
                                 middle.x() + 6, middle.y() - 6)
                painter.drawLine(middle.x() + 6, middle.y() - 6,
                                 middle.x() + 1, middle.y() - 6)
                painter.drawLine(middle.x() - 6, middle.y() + 6,
                                 middle.x() - 1, middle.y() + 6)
            else:
                painter.drawArc(QRect(middle.x() - 7, middle.y() - 7, 14, 14),
                                30 * 16, 280 * 16)
                painter.drawLine(middle.x() + 5, middle.y() - 7,
                                 middle.x() + 7, middle.y() - 1)

    ## -- interaction

    def sticker_at(self, point: QPoint):
        """The topmost sticker under a point, or None. Last drawn wins."""
        boxes = self.day_boxes()
        for sticker, day in reversed(self._visible):
            box = boxes.get(day)
            if box is None:
                continue
            if sticker_rect(sticker.x, sticker.y, sticker.scale,
                            box).contains(point):
                return sticker
        return None

    def mousePressEvent(self, event) -> None:
        point = event.position().toPoint()

        # Padded. It is the one control that ends the mode, and a near miss
        # moves a sticker instead of pressing it.
        if self.editing and self.done_rect().adjusted(
                -DONE_PAD, -DONE_PAD, DONE_PAD, DONE_PAD).contains(point):
            placed = self.commit_pending()
            self.set_editing(False)
            event.accept()
            if placed and callable(self._on_placed):
                # Asked once it has a day, which is the first moment the
                # question means anything.
                self._on_placed(placed)
            return

        # A control on the selected sticker comes first: it sits partly over
        # the sticker, and a press there is not a press on the picture.
        current = self.store.get(self.selected) if self.selected else None
        if current is not None:
            boxes = self.day_boxes()
            day = current.anchor_date(self.store.resolve_event)
            box = boxes.get(day) or (self._box_for(current, boxes))
            if box is not None:
                rect = sticker_rect(current.x, current.y, current.scale, box)
                for name, control in self.chrome_rects(current, rect).items():
                    if not control.contains(point):
                        continue
                    if name in ("scale", "rotate"):
                        # These are drags, not taps. Handing them to
                        # _chrome_pressed - which only knows remove and
                        # unlock - swallowed the press and did nothing.
                        self._begin_handle(name, rect.center(), point,
                                           key=current.key)
                    else:
                        self._chrome_pressed(name, current)
                    event.accept()
                    return

        if self.pending is not None:
            # Its own handles first: a press on one of those is a resize or a
            # turn, not a move.
            rect = self.pending_rect()
            for name, box in self.handle_rects(rect).items():
                if box.contains(point):
                    self._begin_handle(name, rect.center(), point)
                    event.accept()
                    return
            # A press picks it up; it moves when the press moves.
            #
            # Teleporting it to wherever the screen was touched means a press
            # that just misses Done drags the sticker under Done - and the
            # next press hits it, which reads as needing two presses to
            # confirm.
            self._press = point
            self._grab = self.pending["pos"] - point
            self._drag_key = None
            self._dragging = False
            event.accept()
            return

        hit = self.sticker_at(point)
        if hit is None:
            # The handles sit on the sticker's edge and reach past it, so a
            # press just outside is still a press on a control.
            if current is not None:
                boxes = self.day_boxes()
                rect = self.rect_of(current, current.anchor_date(
                    self.store.resolve_event), boxes) or self._box_for(current, boxes)
                if rect is not None:
                    for name, box in self.handle_rects(rect).items():
                        if box.contains(point):
                            self._begin_handle(name, rect.center(), point,
                                               key=current.key)
                            event.accept()
                            return
            # Deselects rather than falling through. While this layer is taking
            # the mouse the page is deliberately out of reach, and a press on
            # empty space is how somebody puts a sticker down.
            self.selected = None
            self.update()
            event.accept()
            return

        self.selected = hit.key
        self._press = point
        self._drag_key = hit.key
        self._dragging = False
        self.update()
        event.accept()

    ## -- sizing and turning

    def _begin_handle(self, name: str, centre: QPoint, point: QPoint,
                      key: str = None) -> None:
        """Start a resize or a turn, measured from the sticker's middle."""
        self._handle = name
        self._handle_key = key
        self._handle_centre = centre
        self._handle_start = point
        source = self.pending if key is None else self.store.get(key)
        if source is None:
            self._handle = None
            return
        getter = (source.get if isinstance(source, dict)
                  else lambda n, d=None: getattr(source, n, d))
        self._handle_scale = float(getter("scale", DEFAULT_SCALE) or DEFAULT_SCALE)
        self._handle_angle = float(getter("angle", 0.0) or 0.0)
        self._handle_reach = max(8.0, _distance(centre, point))

    def _drag_handle(self, point: QPoint) -> None:
        """Resize or turn from where the press began."""
        from .stickers import MIN_SCALE, MAX_SCALE, MAX_ANGLE

        if self._handle == "scale":
            reach = max(1.0, _distance(self._handle_centre, point))
            scale = self._handle_scale * (reach / self._handle_reach)
            scale = max(MIN_SCALE, min(MAX_SCALE, scale))
            self._apply_handle(scale=scale)
            return

        turned = (_bearing(self._handle_centre, point)
                  - _bearing(self._handle_centre, self._handle_start))
        angle = self._handle_angle + turned
        # Snapped near square, so a sticker somebody wanted straight is
        # straight rather than one degree off it.
        if abs(angle) < 4:
            angle = 0.0
        angle = max(-MAX_ANGLE, min(MAX_ANGLE, angle))
        self._apply_handle(angle=angle)

    def _apply_handle(self, scale: float = None, angle: float = None) -> None:
        if self._handle_key is None:
            if self.pending is None:
                return
            if scale is not None:
                self.pending["scale"] = scale
            if angle is not None:
                self.pending["angle"] = angle
            self.update()
            return
        self.store.move(self._handle_key, scale=scale, angle=angle)
        self.refresh()

    def _end_handle(self) -> None:
        self._handle = None
        self._handle_key = None

    def _box_for(self, sticker, boxes: dict) -> Optional[QRect]:
        for candidate, day in self._visible:
            if candidate.key == sticker.key:
                return boxes.get(day)
        return None

    def mouseMoveEvent(self, event) -> None:
        point = event.position().toPoint()

        if self._handle:
            self._drag_handle(point)
            return

        if self.pending is not None:
            if self._press is not None:
                self.pending["pos"] = point + self._grab
                self.update()
            return

        if self._press is None or self._drag_key is None:
            return
        if not self._dragging:
            travelled = (point - self._press).manhattanLength()
            if travelled < DRAG_SLOP:
                return
            self._dragging = True

        sticker = self.store.get(self._drag_key)
        if sticker is None:
            return

        boxes = self.day_boxes()
        if sticker.locked:
            # Held to its box. The fraction is measured against that box, so
            # dragging past its edge stops at the edge.
            box = self._box_for(sticker, boxes)
            if box is None:
                return
            fraction_x, fraction_y = to_fraction(point, box)
            self.store.move(sticker.key, x=fraction_x, y=fraction_y)
            self.refresh()
            return

        # Free, so it follows the pointer rather than being redrawn inside the
        # box it has not left yet.
        #
        # Writing the fraction against the box it is heading FOR while it is
        # still anchored to the one it is leaving puts it at that fraction of
        # the wrong box - so it springs back and sits somewhere it was never
        # dragged. It lands on release instead.
        self._drag_pos = point
        self._pending_day = nearest_cell(point, boxes)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._handle:
            self._end_handle()
            self._press = None
            event.accept()
            return

        if self.pending is not None:
            # Nothing is committed by letting go. Done is what puts it down.
            self._press = None
            self._dragging = False
            event.accept()
            return

        was_dragging = self._dragging
        key = self._drag_key
        self._press = None
        self._dragging = False
        self._drag_key = None

        sticker = self.store.get(key) if key else None
        if sticker is not None and was_dragging and not sticker.locked:
            landing = self._pending_day
            point = self._drag_pos
            if landing is not None and point is not None:
                box = self.day_boxes().get(landing)
                if box is not None:
                    fraction_x, fraction_y = to_fraction(point, box)
                    self.store.move(sticker.key, x=fraction_x, y=fraction_y)
                # Settles on the day it landed on - see StickerStore.move.
                self.store.move(sticker.key, day=landing)
        self._pending_day = None
        self._drag_pos = None

        self.refresh()
        event.accept()

    def _chrome_pressed(self, name: str, sticker) -> None:
        if name == "remove":
            self.store.remove(sticker.key)
            self.selected = None
        elif name == "unlock":
            self.store.unlock(sticker.key)
        self.refresh()

    ## -- placing a new one

    def begin_placing(self, image: str, on_placed=None) -> bool:
        """
        Start placing a new sticker. It follows the drag and lands on Done.

        Nothing is written until then: a sticker put straight onto today has
        been placed by the application rather than by the person, and the
        question about attaching it to an event is then a question about a
        choice nobody made.

        `on_placed` is called with the new key once it lands.
        """
        boxes = self.day_boxes()
        if not boxes:
            return False

        self.set_editing(True)
        self.selected = None
        self._on_placed = on_placed
        self.pending = {"image": str(image),
                        "pos": QPoint(self.width() // 2, self.height() // 2)}
        self.update()
        return True

    def commit_pending(self) -> Optional[str]:
        """
        Put the sticker being placed onto the day it is over.

        Nearest rather than only the one underneath: something dropped in a
        gutter or just off the edge of the grid belongs to the day it is
        closest to, rather than nowhere.
        """
        pending, self.pending = self.pending, None
        if pending is None:
            return None

        boxes = self.day_boxes()
        landing = nearest_cell(pending["pos"], boxes)
        if landing is None:
            self.update()
            return None

        from .stickers import DEFAULT_SCALE as _DEFAULT
        fraction_x, fraction_y = to_fraction(pending["pos"], boxes[landing])
        # Carried over, not defaulted. A sticker sized and turned on the way
        # down that lands square is a sticker that ignored what was asked.
        sticker = self.store.add(
            pending["image"], landing, x=fraction_x, y=fraction_y,
            scale=float(pending.get("scale", _DEFAULT) or _DEFAULT),
            angle=float(pending.get("angle", 0.0) or 0.0))
        self.selected = sticker.key
        self.refresh()
        return sticker.key


## -- drawing one somewhere that is not the calendar -----------------------
#
# The next-event widget and the reminder panel both show an event, and a
# sticker stuck to that event belongs on both. Neither is a day box, so the
# size comes from the surface rather than from the sticker's own scale: a
# sticker sized for a calendar cell is a picture covering half a reminder.

#The share of the shorter side a sticker takes on a surface that is not a day
#box. Small enough to sit beside the words rather than compete with them.
BESIDE_SHARE = 0.42
#And the most it may ever be, whatever the surface. A reminder panel is wide,
#and a share of it would be a poster.
BESIDE_MAX = 72
BESIDE_MIN = 24


def sticker_for_event(client, event) -> Optional[str]:
    """The filename of the sticker stuck to an event, or None."""
    key = getattr(event, "key", "") or ""
    series = getattr(event, "series_key", "") or ""
    try:
        lookup = client.public.calendar["stickers_for"]
    except Exception:
        return None
    for candidate in (key, series):
        if not candidate:
            continue
        try:
            found = lookup(candidate)
        except Exception:
            found = []
        if found:
            return found[0].image
    return None


def load_sticker(client, name: str) -> Optional[QPixmap]:
    """One frame of a sticker from the library, or None."""
    if not name:
        return None
    try:
        entry = client.public.stickers["get"](name)
    except Exception:
        entry = None
    if entry is None:
        return None
    pixmap = QPixmap(str(entry.path))
    return pixmap if not pixmap.isNull() else None


def beside_rect(area: QRect, margin: int = 8) -> QRect:
    """
    Where a sticker goes on a surface showing one event.

    Bottom right, against the corner furthest from the text: the title starts
    top left and grows down, so this is the one corner that stays empty as an
    event's name gets longer.
    """
    side = int(round(min(area.width(), area.height()) * BESIDE_SHARE))
    side = max(BESIDE_MIN, min(BESIDE_MAX, side))
    side = min(side, max(1, area.width() - margin * 2),
               max(1, area.height() - margin * 2))
    return QRect(area.right() - side - margin,
                 area.bottom() - side - margin, side, side)


def draw_beside(painter: QPainter, pixmap: QPixmap, area: QRect,
                margin: int = 8) -> QRect:
    """Draw a sticker in the corner of `area`, and say where it went."""
    if pixmap is None or pixmap.isNull():
        return QRect()
    box = beside_rect(area, margin)
    scaled = pixmap.scaled(box.size(),
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
    painter.drawPixmap(box.x() + (box.width() - scaled.width()) // 2,
                       box.y() + (box.height() - scaled.height()) // 2,
                       scaled)
    return box
