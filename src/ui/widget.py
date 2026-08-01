from __future__ import annotations

import json
import math
import pathlib
from typing import TYPE_CHECKING, Optional, Callable

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSizePolicy, QLabel, QPushButton,
    QScrollArea, QFrame,
)
from PyQt6.QtCore import Qt, QTimer, QPoint, QPointF, QRect, QEvent
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QTransform

from src.styling import set_style, make_font, SIZES, get_style_sheet, style_scrollbar

if TYPE_CHECKING:
    from src.main import Client


# The nine places a widget can be asked for, as a grid read left to right and
# top to bottom. One vocabulary for every caller: an anchor zone, a random
# transient landing, a sticker dropped from a phone.
#
# The names are the anchor-style ones because those are what is already written
# into widget_layout.json. Everything else is an alias, below.
POSITIONS = (
    "top-left",    "top-center",    "top-right",
    "center-left", "center",        "center-right",
    "bottom-left", "bottom-center", "bottom-right",
)

#What each name is called on the pages that show a grid of them.
POSITION_LABELS = {
    "top-left":      "Top left",
    "top-center":    "Top",
    "top-right":     "Top right",
    "center-left":   "Left",
    "center":        "Middle",
    "center-right":  "Right",
    "bottom-left":   "Bottom left",
    "bottom-center": "Bottom",
    "bottom-right":  "Bottom right",
}

# Every other spelling that has been in a URL, a saved layout or a plugin.
#
# Kept rather than migrated: these are in bookmarks on other people's phones
# and in scripts nobody here can edit. A name that used to work still works.
POSITION_ALIASES = {
    "top":        "top-center",
    "bottom":     "bottom-center",
    "left":       "center-left",
    "right":      "center-right",
    "middle":     "center",
    "centre":     "center",
    "mid-left":   "center-left",
    "mid-right":  "center-right",
    "center-top": "top-center",
    "center-bottom": "bottom-center",
}

#The six that name an anchor zone kept their meaning exactly; ANCHORS remains
#as the old name for anything importing it.
ANCHORS = POSITIONS

TOPMOST  = "topmost"
FLOATING = "floating"


def normalise_position(value, fallback: str = "top-right") -> str:
    """
    One of POSITIONS, whatever spelling arrived.

    Anything unrecognised becomes `fallback` rather than being dropped. A
    position that cannot be honoured has to become one that can - silently
    discarding it is how a page full of place controls ended up putting
    everything in the same corner.
    """
    name = str(value or "").strip().lower().replace("_", "-").replace(" ", "-")
    name = POSITION_ALIASES.get(name, name)
    return name if name in POSITIONS else fallback


def split_position(value):
    """
    A position as (vertical, horizontal), each of top/center/bottom and
    left/center/right.

    Parsed rather than substring-tested. "center" appears in `top-center` and
    in `center-left`, and in `center` alone it is both halves - so asking
    whether the name contains it answers a different question each time.
    """
    name = normalise_position(value)
    if name == "center":
        return "center", "center"
    vertical, horizontal = name.split("-", 1)
    return vertical, horizontal

HOLD_MS = 450
# Sized for a finger. 22px was fine with a cursor and fiddly without one.
HANDLE = 44
HANDLE_HIT_PAD = 12
ROTATE_ARM = 72
SNAP_DEGREES = 15
SNAP_TOLERANCE = 4


class Widget(QWidget):
    # Class attributes declare what the widget supports. The framework reads
    # them off the class, so a widget can be listed in the panel without being
    # placed.

    KEY = ""
    NAME = ""
    ICON = ""
    DESCRIPTION = ""

    RESIZABLE = False
    #Whether a resize keeps the shape it started with. For anything showing a
    #picture: dragging a corner freely squashes it, and nobody drags a corner
    #meaning to distort what is inside.
    KEEP_ASPECT = False
    ROTATABLE = False          # requires the widget to paint itself, see below
    FLOATABLE = False
    REMOVABLE = True
    MULTIPLE = False

    MIN_W, MIN_H = 90, 60
    MAX_W, MAX_H = 1400, 1000
    DEFAULT_ANCHOR = "bottom-left"

    def __init__(
        self,
        client: "Client",
        key: str = None,
        anchor: str = None,
        width: int | None = None,
        height: int | None = None,
        floating: bool = False,
        float_x: int = 0,
        float_y: int = 0,
    ):
        super().__init__()
        self.KEY      = key or self.__class__.KEY or self.__class__.__name__.lower()
        self.client   = client
        self.anchor   = anchor or self.DEFAULT_ANCHOR
        self.floating = bool(floating)
        self.float_x  = float_x
        self.float_y  = float_y
        self.tags: list[str] = []

        self.angle: float = 0.0
        self.placed: bool = True
        self.template_key: str = ""

        # A transient widget is placed by something happening rather than by
        # the person arranging their home screen - a running timer, a sticker
        # an API call asked for. It is deliberately kept out of the saved
        # layout: a widget that only exists while its reason exists must not
        # come back as a ghost on the next launch.
        self.transient: bool = False

        # A nudge from wherever the anchor puts this widget. Anchored widgets
        # are laid out by their zone, so this is the only way to fine-tune one
        # without giving up its anchor.
        self.offset_x: int = 0
        self.offset_y: int = 0

        set_style(self, "common", "transparent")

        if width  is not None: self.setFixedWidth(width)
        if height is not None: self.setFixedHeight(height)

        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._safe_tick)
        self._tick_interval  = 1000
        self._tick_suspended = False

    ## CAPABILITIES

    #Where this sits in the stack. Higher is nearer the front.
    #
    #Saved with the rest of the layout: without it, stacking is whatever order
    #the framework happened to place things in, so a sticker deliberately put
    #in front of another was behind it again after a restart.
    z_order = 0

    def bring_to_front(self) -> int:
        """
        Put this above everything else, and say what its new z is.

        Assigned rather than swapped: the highest z in the page plus one, so
        repeatedly raising the same widget does not shuffle the others.
        """
        siblings = []
        parent = self.parent()
        if parent is not None:
            siblings = [w for w in parent.findChildren(Widget)
                        if w is not self]
        highest = max([w.z_order for w in siblings] or [0])
        self.z_order = highest + 1
        self.raise_()
        return self.z_order

    def chrome_button(self):
        """
        One extra button on the selection chrome, or None.

        Returns `(icon_name, tooltip, callable)`. Shown while this widget is
        selected, beside the handles that move and resize it - so a widget
        with something to configure has somewhere to put it that is not a
        second tap target on the face of the widget itself.

            def chrome_button(self):
                return ("mdi.palette", "Colour", self.pick_colour)

        The callable takes no arguments and runs on the UI thread.
        """
        return None

    def edge_padding(self):
        """
        How far from the page edge this widget may be dragged.

        `None` means the framework's own padding, which is what an anchored
        widget wants - a column of cards flush against the glass looks like a
        rendering fault. A widget that returns 0 may go all the way out.
        """
        return None

    def content_inset(self):
        """
        Transparent margin inside this widget, as (left, top, right, bottom).

        A sticker is a rectangle containing a shape, and the shape is usually
        smaller than the rectangle. Reporting the difference lets the clamp
        measure the edge against what can actually be seen, so a sticker with
        40px of nothing down its left side can be pushed 40px further before it
        looks like it has stopped.
        """
        return (0, 0, 0, 0)

    def wants_visible(self) -> bool:
        """
        Whether placing this widget should also show it.

        Placement shows unconditionally by default, which is right for almost
        everything - a widget that has just been dropped onto the page should
        appear. It is wrong for a widget whose whole job is to come and go: the
        now-playing card hides itself when nothing is playing, and was then
        shown again by the very placement that put it there.

        Overriding this is how a widget says "not yet". It is asked at every
        placement, so a widget that changes its mind is respected next time.
        """
        return True

    def can_resize(self) -> bool:
        return bool(self.RESIZABLE)

    def can_rotate(self) -> bool:
        return bool(self.ROTATABLE)

    def can_float(self) -> bool:
        return bool(self.FLOATABLE)

    def display_name(self) -> str:
        return self.NAME or self.KEY

    def has_offset(self) -> bool:
        return bool(self.offset_x or self.offset_y)

    ## ROTATION
    #
    # Paint-only. A QWidget has no transform, so a widget that rotates must
    # draw itself and call this at the top of its paintEvent. Widgets built
    # from child widgets cannot rotate - their children would keep painting
    # square - which is why ROTATABLE is opt-in rather than free.

    def content_size(self):
        """
        The widget's unrotated size - what it actually draws.

        Rotating a rect makes it need a bigger box: a WxH rectangle turned by
        an angle spans W|cos|+H|sin| across. The widget grows to that bounding
        box so nothing is clipped, and the content stays this size, centred
        inside it.
        """
        if not hasattr(self, "_content_w"):
            self._content_w, self._content_h = self.width(), self.height()
        return self._content_w, self._content_h

    def set_content_size(self, width: int, height: int,
                         chosen: bool = True) -> None:
        """
        Set the unrotated content size.

        `chosen` records whether this is a decision - a drag, or a restore of
        one - or a widget growing to hold what is inside it. Only a decision
        is preserved across a rebuild and written to the layout as one. A
        widget that grows itself and is marked as having been sized can never
        shrink again, because its own growth reads back as somebody's choice.
        """
        self._content_w, self._content_h = int(width), int(height)
        if chosen:
            self._sized = True

    def has_chosen_size(self) -> bool:
        """
        Whether this widget's size is a decision rather than a fallback.

        True once something has set it: a drag, or a restore from the saved
        layout. False means content_size() would be answering with whatever
        Qt happened to have laid the widget out at, which is not a size
        anybody picked and must not be preserved as though it were.
        """
        return bool(getattr(self, "_sized", False))

    def rotated_bounds(self, width: int = None, height: int = None):
        """The box needed to hold this content at the current angle."""
        content_w, content_h = self.content_size()
        width = content_w if width is None else width
        height = content_h if height is None else height
        if not self.angle:
            return int(width), int(height)
        radians = math.radians(self.angle)
        cos, sin = abs(math.cos(radians)), abs(math.sin(radians))
        return (int(round(width * cos + height * sin)),
                int(round(width * sin + height * cos)))

    def content_rect(self) -> QRect:
        """Where the unrotated content sits inside the widget."""
        content_w, content_h = self.content_size()
        return QRect(int((self.width() - content_w) / 2),
                     int((self.height() - content_h) / 2),
                     int(content_w), int(content_h))

    def apply_rotation(self, painter: QPainter) -> None:
        """
        Rotate about the widget's centre and move the origin to the content.

        After this the widget can paint at (0, 0) to content_size() and it
        lands centred and rotated, with the corners inside the widget rather
        than clipped off by it.
        """
        centre_x, centre_y = self.width() / 2, self.height() / 2
        if self.angle:
            painter.translate(centre_x, centre_y)
            painter.rotate(self.angle)
            painter.translate(-centre_x, -centre_y)
        rect = self.content_rect()
        painter.translate(rect.x(), rect.y())

    def local_transform(self) -> QTransform:
        transform = QTransform()
        if self.angle:
            transform.translate(self.width() / 2, self.height() / 2)
            transform.rotate(self.angle)
            transform.translate(-self.width() / 2, -self.height() / 2)
        rect = self.content_rect()
        transform.translate(rect.x(), rect.y())
        return transform

    def contains_point(self, point: QPoint) -> bool:
        """Hit test in the widget's own coordinates, honouring rotation."""
        content_w, content_h = self.content_size()
        box = QRect(0, 0, int(content_w), int(content_h))
        inverted, ok = self.local_transform().inverted()
        if not ok:
            return self.rect().contains(point)
        return box.contains(inverted.map(point))

    def resize_to_fit_rotation(self) -> None:
        """Grow the widget to hold its content at the current angle."""
        width, height = self.rotated_bounds()
        if (width, height) != (self.width(), self.height()):
            self.setFixedSize(width, height)

    ## HOOKS

    def on_activate(self) -> None:
        pass

    def on_transform_finished(self) -> None:
        pass

    ## LAYOUT STATE

    def layout_state(self) -> dict:
        return {
            "placed":   bool(self.placed),
            "anchor":   self.anchor,
            "floating": bool(self.floating),
            "x":        int(self.float_x),
            "y":        int(self.float_y),
            "w":        int(self.content_size()[0]),
            "h":        int(self.content_size()[1]),
            "angle":    round(float(self.angle), 2),
            "ox":       int(self.offset_x),
            "oy":       int(self.offset_y),
            "z":        int(self.z_order),
            # Whether the size was ASKED for, or is just what the content
            # happened to measure when this was written.
            "sized":    bool(self.has_chosen_size()),
        }

    def apply_layout_state(self, state: dict) -> None:
        if not isinstance(state, dict):
            return
        try:
            self.z_order = int(state.get("z", self.z_order))
        except (TypeError, ValueError):
            pass
        self.placed   = bool(state.get("placed", True))
        self.anchor   = str(state.get("anchor", self.anchor))
        self.floating = bool(state.get("floating", self.floating))
        self.float_x  = int(state.get("x", self.float_x))
        self.float_y  = int(state.get("y", self.float_y))
        self.angle    = float(state.get("angle", 0.0))
        self.offset_x = int(state.get("ox", 0))
        self.offset_y = int(state.get("oy", 0))

        # A size is restored only when it was chosen, and only when there is
        # one in the state to restore.
        #
        # set_content_size() marks a widget as having a chosen size, and
        # layout_state() writes whatever content_size() reports - so restoring
        # unconditionally turned a measurement into a decision. If the content
        # measured small at save time, that small size came back as fixed, was
        # then measured and saved again, and the widget shrank a little on
        # every launch.
        #
        # An entry written before this defaults to sized: a size already in the
        # file is honoured rather than thrown away, which stops the ratchet
        # without resetting anybody's layout.
        #
        # A state with no `w`/`h` at all is a different thing again - the API
        # builds one to carry text and items, not geometry. Falling back to
        # width() there read the widget's own current size and wrote it back as
        # a decision, which froze anything that sizes itself to its contents at
        # whatever it happened to measure first.
        has_size = "w" in state and "h" in state
        if self.can_resize() and has_size and bool(state.get("sized", True)):
            width  = int(state.get("w", self.width()))
            height = int(state.get("h", self.height()))
            self.set_content_size(max(self.MIN_W, min(self.MAX_W, width)),
                                  max(self.MIN_H, min(self.MAX_H, height)))
            bounds_w, bounds_h = self.rotated_bounds()
            self.setFixedSize(bounds_w, bounds_h)

    ## TICK

    def start_tick(self, interval_ms: int = 1000) -> None:
        self._tick_interval = int(interval_ms)
        self._tick_suspended = False
        self._tick_timer.start(self._tick_interval)

    def stop_tick(self) -> None:
        self._tick_suspended = False
        self._tick_timer.stop()

    def suspend_tick(self) -> None:
        """
        Stop ticking without forgetting the interval.

        Used when the page a widget lives on goes off screen. Distinct from
        stop_tick(), which is a permanent stop on removal - a suspended widget
        knows how to start again, and one that was never ticking stays that
        way.
        """
        if self._tick_timer.isActive():
            self._tick_suspended = True
            self._tick_timer.stop()

    def resume_tick(self) -> None:
        if getattr(self, "_tick_suspended", False):
            self._tick_suspended = False
            self._tick_timer.start(getattr(self, "_tick_interval", 1000))
            self._safe_tick()   # do not show a stale face until the next tick

    def _safe_tick(self) -> None:
        try:
            self.tick()
        except Exception:
            pass

    def tick(self) -> None:
        pass


class _AnchorZone(QWidget):
    # Restored to the original: a QWidget holding a column of row layouts.
    # Widgets are ordinary children laid out by Qt, which is what worked
    # before the graphics-scene detour.

    def __init__(self, anchor_name: str, padding: int, widget_spacing: int):
        super().__init__()
        self.anchor_name    = anchor_name
        self.padding        = padding
        self.widget_spacing = widget_spacing

        set_style(self, "common", "transparent")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        vertical, horizontal = split_position(anchor_name)
        self.vertical   = vertical
        self.horizontal = horizontal

        self._col = QVBoxLayout()
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(8)
        self._col.setSizeConstraint(self._col.SizeConstraint.SetFixedSize)
        self._col.setAlignment({
            "top":    Qt.AlignmentFlag.AlignTop,
            "center": Qt.AlignmentFlag.AlignVCenter,
            "bottom": Qt.AlignmentFlag.AlignBottom,
        }[vertical])

        self.setLayout(self._col)
        self._rows: dict[int, QHBoxLayout] = {}
        self._row_widgets: dict[int, QWidget] = {}
        self._placeholders: dict = {}

    def add_widget(self, widget: Widget, row_index: int,
                   position: int = None) -> None:
        if row_index not in self._rows:
            row_widget = QWidget()
            set_style(row_widget, "common", "transparent")
            row_widget.setSizePolicy(QSizePolicy.Policy.Preferred,
                                     QSizePolicy.Policy.Fixed)

            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(self.widget_spacing)

            row_layout.setAlignment({
                "left":   Qt.AlignmentFlag.AlignLeft,
                "center": Qt.AlignmentFlag.AlignHCenter,
                "right":  Qt.AlignmentFlag.AlignRight,
            }[self.horizontal])

            self._col.insertWidget(min(row_index, self._col.count()), row_widget)
            self._rows[row_index] = row_layout
            self._row_widgets[row_index] = row_widget
            # Explicitly. A row created while its zone is not yet visible
            # stays hidden, and the widget inside it only appeared after some
            # later event forced a repaint - which is why the first drop after
            # startup seemed to do nothing until the next click.
            row_widget.show()

        row = self._rows[row_index]
        if position is None or position >= row.count():
            row.addWidget(widget)
        else:
            row.insertWidget(max(0, position), widget)

        if widget.wants_visible():
            widget.show()
        row.activate()
        self.adjustSize()
        self.show()
        self.update()

    def remove_widget(self, widget: Widget) -> None:
        stand_in = self._placeholders.pop(widget, None)
        if stand_in is not None:
            for row in self._rows.values():
                row.removeWidget(stand_in)
            stand_in.setParent(None)
            stand_in.deleteLater()
        for row in self._rows.values():
            row.removeWidget(widget)
        self.adjustSize()

    def hold_slot(self, widget: Widget):
        """
        Swap a widget for a same-size placeholder and hand the widget back.

        An offset widget cannot stay in the layout: it is clipped by its row,
        and the layout would undo the move on its next pass. Leaving a
        placeholder keeps the row spacing correct and gives a reference point
        to offset from.
        """
        if widget in self._placeholders:
            return self._placeholders[widget]

        for index, row in self._rows.items():
            for slot in range(row.count()):
                item = row.itemAt(slot)
                if item is not None and item.widget() is widget:
                    stand_in = QWidget()
                    set_style(stand_in, "common", "transparent")
                    stand_in.setFixedSize(widget.size())
                    row.removeWidget(widget)
                    row.insertWidget(slot, stand_in)
                    stand_in.show()
                    self._placeholders[widget] = stand_in
                    self.adjustSize()
                    return stand_in
        return None

    def placeholder_for(self, widget: Widget):
        return self._placeholders.get(widget)

    def release_slot(self, widget: Widget, position: int = None) -> None:
        """Put a widget back in place of its placeholder."""
        stand_in = self._placeholders.pop(widget, None)
        if stand_in is None:
            return
        for index, row in self._rows.items():
            for slot in range(row.count()):
                item = row.itemAt(slot)
                if item is not None and item.widget() is stand_in:
                    row.removeWidget(stand_in)
                    stand_in.setParent(None)
                    stand_in.deleteLater()
                    row.insertWidget(slot if position is None else position, widget)
                    widget.show()
                    self.adjustSize()
                    return
        stand_in.setParent(None)
        stand_in.deleteLater()

    def is_empty(self) -> bool:
        return all(row.count() == 0 for row in self._rows.values())

    def widgets_in_row(self, row_index: int) -> list:
        row = self._rows.get(row_index)
        if row is None:
            return []
        out = []
        for index in range(row.count()):
            item = row.itemAt(index)
            if item is not None and item.widget() is not None:
                out.append(item.widget())
        return out

    def slot_for_x(self, row_index: int, x: int, skip=None) -> int:
        """Insertion index in a row for a point at framework x."""
        slot = 0
        for widget in self.widgets_in_row(row_index):
            if widget is skip:
                continue
            centre = widget.mapTo(self.parent(), QPoint(0, 0)).x() + widget.width() / 2
            if x > centre:
                slot += 1
        return slot


class WidgetFramework(QWidget):
    # A plain QWidget again. Widgets are ordinary children laid out by
    # _AnchorZone, which is the arrangement that renders reliably.
    #
    # The graphics-scene version put every widget in a QGraphicsProxyWidget so
    # rotation could carry hit-testing. It did, in isolation - but a proxy did
    # not composite child widgets on the target machine, so anything built
    # from QLabels rendered blank while self-painted widgets and the selection
    # chrome, drawn straight onto the view, were fine. Rotation is now
    # paint-only and opt-in, which costs nothing that was working.

    def __init__(self, client: "Client", page_key: str,
                 padding: int = 35, widget_spacing: int = 5):
        super().__init__()
        self.client         = client
        self.page_key       = page_key
        self.padding        = padding
        self.widget_spacing = widget_spacing

        set_style(self, "common", "transparent")

        self._zones:   dict[str, _AnchorZone] = {}
        self._widgets: list = []
        self._topmost: list = []
        self.registry: dict = {}
        self.templates: dict = {}

        # Names promised to a caller that has not built its widget yet. Claimed
        # from a request thread and consumed on the UI thread, so the lock is
        # not decoration - see reserve_key.
        import threading
        self._reserved: set = set()
        self._keys_lock = threading.Lock()

        self.active: Optional[Widget] = None
        self._mode = ""
        #the shape a resize started at, for an aspect-locked drag
        self._start_w, self._start_h = 0, 0
        self._start_ratio = 1.0
        self._grab_offset = QPoint()
        self._start_angle = 0.0
        self._start_vector = 0.0
        self._moved = False
        self._pressed: Optional[Widget] = None
        self._was_anchored = ""
        self._drop_anchor = ""       # anchor a drag would land in
        self._drop_slot = 0          # position within that anchor's row
        self._offset_start = QPoint()
        self._offset_grab = QPoint()

        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.setInterval(HOLD_MS)
        self._hold.timeout.connect(self._hold_elapsed)

        # Selection chrome. Child widgets paint over their parent, so the
        # border and handles cannot be drawn in the framework's own
        # paintEvent - they would sit underneath the widget they describe.
        # A raised, mouse-transparent overlay painted through an event filter
        # keeps them on top without needing a class of its own.
        self._chrome = QWidget(self)
        self._chrome.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._chrome.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._chrome.installEventFilter(self)
        self._chrome.hide()

        self.panel = None
        self._panel_items = None

        self._refit = QTimer(self)
        self._refit.timeout.connect(self.tick_widgets)
        self._refit.start(1000)
        self._ticking = True

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self.save_layout)

    ## REGISTRATION

    def register(self, widget_class, *args, placed: bool = None, **kwargs):
        key = getattr(widget_class, "KEY", "") or widget_class.__name__.lower()
        if key in self.registry:
            return self.registry[key]

        if getattr(widget_class, "MULTIPLE", False):
            self.templates[key] = (widget_class, args, kwargs)
            for saved_key, state in self.load_layout().items():
                if state.get("template") == key and state.get("placed"):
                    self._make_copy(key, saved_key, state)
            self._build_panel()
            self._refresh_panel()
            return None

        try:
            widget = widget_class(self.client, *args, **kwargs)
        except Exception as e:
            self.client.log("error",
                            f"[WidgetFramework] Widget '{key}' failed to build: {e}")
            return None

        widget.KEY = widget.KEY or key
        self.registry[widget.KEY] = widget

        saved = self.load_layout().get(widget.KEY)
        if saved is not None:
            widget.apply_layout_state(saved)
        elif placed is not None:
            widget.placed = placed

        if not widget.REMOVABLE:
            widget.placed = True

        if widget.placed:
            self.place(widget, save=False)
        else:
            self._to_panel(widget, save=False)
        return widget

    def reserve_key(self, template_key: str, transient: bool = False) -> str:
        """
        The key the next copy of a template will have, claimed up front.

        For a caller that has to answer before the widget exists. Building one
        is the UI thread's job and call_on_ui does not report back, so a Flask
        request that creates a widget cannot otherwise say which widget it
        created - and a page that has just made a list needs to know, or it
        cannot go on to edit it.

        Reserved keys are held until the copy claims one, so two requests
        arriving together cannot be promised the same name.
        """
        shape = f"{template_key}~t{{}}" if transient else f"{template_key}-{{}}"
        with self._keys_lock:
            index = 1
            while (shape.format(index) in self.registry
                   or shape.format(index) in self._reserved):
                index += 1
            key = shape.format(index)
            self._reserved.add(key)
            return key

    def create(self, template_key: str, key: str = None, state: dict = None,
               transient: bool = False, **kwargs):
        """
        One copy of a registered template. The only way to make a widget.

        `transient` marks it as never persisted; it is the same class and the
        same registry either way. Nothing else about a widget depends on how
        it came to be on the page.
        """
        widget = self._make_copy(template_key, instance_key=key, state=state,
                                 place_now=False, **kwargs)
        if widget is not None and transient:
            widget.transient = True
        return widget

    def _make_copy(self, template_key: str, instance_key: str = None,
                   state: dict = None, place_now: bool = True, **extra):
        entry = self.templates.get(template_key)
        if entry is None:
            return None
        widget_class, args, kwargs = entry
        # A chooser's answer, merged over whatever the template was
        # registered with - see _add_copy_from_panel.
        kwargs = {**kwargs, **extra}

        if instance_key is None:
            instance_key = self.reserve_key(template_key)

        try:
            widget = widget_class(self.client, *args, **kwargs)
        except Exception as e:
            self.client.log("error",
                            f"[WidgetFramework] Could not copy '{template_key}': {e}")
            # Released on failure too, or a reserved name is never usable
            # again and every later copy skips past it.
            self._reserved.discard(instance_key)
            return None

        widget.KEY = instance_key
        widget.template_key = template_key
        self.registry[instance_key] = widget
        self._reserved.discard(instance_key)
        if state:
            widget.apply_layout_state(state)
        if place_now:
            self.place(widget)
        return widget

    def add(self, widgets: list) -> None:
        for widget in widgets:
            if widget.KEY in self.registry:
                continue
            self.registry[widget.KEY] = widget
            saved = self.load_layout().get(widget.KEY)
            if saved is not None:
                widget.apply_layout_state(saved)
            if widget.placed:
                self.place(widget, save=False)
            else:
                self._to_panel(widget, save=False)

        # Once, after the batch. place() raises as it goes, so the stack ends
        # up in construction order until this puts it back in the saved one.
        self.apply_stacking()

    ## PLACEMENT

    def place(self, widget: Widget, save: bool = True, at: str = "",
              exact=None, bundle: bool = False) -> None:
        """
        Put a widget on the page, optionally at one of the nine positions.

        `at` means the same thing whichever kind of widget this is, which is
        the point of it. An anchored widget takes it as its anchor zone; a
        floating one is given a free spot inside that region. Asking for
        "bottom-left" used to do the first and silently skip the second, so
        every floating widget placed by an API call landed in the top corner
        no matter what was chosen.

        `exact` is an (x, y) centre that overrides `at` for a floating widget.
        """
        if widget in self._widgets:
            return

        widget.placed = True
        self._widgets.append(widget)
        widget.setParent(self)
        self._fit_to_content(widget)

        wanted = normalise_position(at, "") if at else ""

        if widget.floating or widget.anchor == FLOATING:
            widget.tags = ["floating"]
            if wanted or exact is not None or bundle:
                # Measured against what is already on the page, so this has to
                # happen after the widget is parented and fitted - its size is
                # part of the question.
                #
                # `bundle` alone is enough: a transient widget with no position
                # named still wants a free spot near its siblings rather than
                # whatever float_x happens to hold.
                point = self.free_point_in(widget, center=exact, at=wanted,
                                           bundle=bundle)
                widget.float_x, widget.float_y = point
            widget.move(widget.float_x, widget.float_y)
        elif widget.anchor == TOPMOST:
            widget.tags = ["topmost"]
            self._topmost.append(widget)
        else:
            widget.tags = ["anchored"]
            if wanted:
                # The row suffix survives: "top-left:1" asked for as
                # "top-left" stays in row 1 rather than jumping to row 0.
                _name, row = self._parse_anchor(widget.anchor)
                widget.anchor = f"{wanted}:{row}" if row else wanted
            self._place_in_zone(widget)
            if widget.has_offset():
                self._detach_for_offset(widget)
                self._apply_offset(widget)

        if widget.wants_visible():
            widget.show()
        for topmost in self._topmost:
            topmost.raise_()
        self._chrome.raise_()

        self.update_geometry()
        if save:
            self.save_layout()

    def remove(self, key: str) -> None:
        widget = self.registry.get(key)
        if widget is None:
            return

        # Somewhere to unsubscribe. A widget that listens to an event outlives
        # its own removal otherwise, and the next fire calls into a deleted
        # object.
        teardown = getattr(widget, "teardown", None)
        if callable(teardown):
            try:
                teardown()
            except Exception as e:
                self.client.log("warning", f"[Widgets] {key} teardown failed: {e}")

        widget.stop_tick()
        self._detach(widget)
        self.registry.pop(key, None)

        # Out of the file too, but only for something that will not be
        # rebuilt.
        #
        # save_layout() MERGES rather than replaces, so a removed entry
        # otherwise survives and the next load restores it - which is what kept
        # bringing cleared stickers back.
        #
        # A COPY or a transient is gone for good, so its entry is noise. A
        # widget a plugin registers is re-created on every load, and deleting
        # its entry does not mean "gone" - it means "no saved state", which
        # falls back to the class default of placed. That is how removing
        # Coming Up put it straight back on the home page. Its entry has to
        # stay, saying placed: False.
        if widget.template_key or getattr(widget, "transient", False):
            self.forget_layout(key)
        else:
            self.save_layout()
        self.save_layout()

    def unplace(self, widget: Widget) -> None:
        if not widget.REMOVABLE:
            return
        self._detach(widget)
        self._to_panel(widget)

    def _detach(self, widget: Widget) -> None:
        if widget in self._widgets:
            self._widgets.remove(widget)
        if widget in self._topmost:
            self._topmost.remove(widget)
        for zone in self._zones.values():
            zone.remove_widget(widget)
        widget.hide()
        widget.setParent(None)
        if self.active is widget:
            self.active = None
            self._mode = ""
            self._chrome.hide()
        self.update_geometry()

    def _to_panel(self, widget: Widget, save: bool = True) -> None:
        widget.placed = False
        self._build_panel()
        self._refresh_panel()
        if save:
            self.save_layout()

    ## PERSISTENCE

    def _layout_path(self):
        """
        Where the layout lives: the user's data directory.

        NOT the plugin's settings.json. That file ships with the app, so
        every install or update overwrites it - the layout survived until the
        next build was unpacked over the top and then silently reset. User
        layout is user data.
        """
        from src.constants import get_data_dir, APP_NAME
        return pathlib.Path(get_data_dir(APP_NAME)) / "widget_layout.json"

    def _read_layout_file(self) -> dict:
        path = self._layout_path()
        try:
            if path.is_file():
                return json.loads(path.read_text() or "{}")
        except Exception as e:
            self.client.log("warning", f"[WidgetFramework] Could not read layout: {e}")
        return {}

    def load_layout(self) -> dict:
        page = self._read_layout_file().get(self.page_key, {})
        if not getattr(self, "_load_logged", False):
            self._load_logged = True
            sizes = ", ".join(
                f"{key}={entry.get('w')}x{entry.get('h')}"
                for key, entry in sorted(page.items()) if entry.get("w"))
            if sizes:
                # Said out loud because a size that is not restored and a size
                # that was never saved look identical on screen, and only one
                # of them is a bug in the restoring.
                self.client.log("debug",
                                f"[WidgetFramework:{self.page_key}] sizes on "
                                f"disk: {sizes}")
            self.client.log("debug",
                            f"[WidgetFramework:{self.page_key}] loaded {len(page)} "
                            f"saved widgets from {self._layout_path()}")
        return page

    def schedule_save(self) -> None:
        """
        Debounced save. Every mutation calls this rather than saving directly,
        so no interaction path can forget to persist - and a drag that fires
        hundreds of move events still writes once.
        """
        self._save_timer.start()

    def _write_layout_file(self, data: dict) -> bool:
        """The whole layout file, replaced. True when it was written."""
        try:
            path = self._layout_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=4))
            return True
        except Exception as e:
            self.client.log("error", f"[WidgetFramework] Could not save layout: {e}")
            return False

    def forget_layout(self, key: str) -> None:
        """
        Drop one widget's saved state, so it is not restored next time.

        Written straight to the file rather than left to save_layout(), which
        only ever adds: it has no way to tell a key belonging to a widget that
        has gone from one belonging to a widget not built yet.
        """
        data = self._read_layout_file()
        page = data.get(self.page_key)
        if not isinstance(page, dict) or key not in page:
            return
        page.pop(key, None)
        data[self.page_key] = page
        self._write_layout_file(data)

    def save_layout(self) -> None:
        data = self._read_layout_file()

        # Merge, do not replace. If this page is ever rebuilt - a plugin
        # reload, a second construction - a fresh framework with an empty or
        # partial registry would otherwise write its blank state over
        # everything that was there.
        page = dict(data.get(self.page_key, {}))
        for widget in self.registry.values():
            if getattr(widget, "transient", False):
                # Never written, and any entry left by an earlier run is
                # cleared. The merge above deliberately keeps keys belonging to
                # widgets this framework has not registered yet, so without
                # this a transient widget saved once would sit in the file for
                # good and be restored as a widget with nothing behind it.
                page.pop(widget.KEY, None)
                continue
            state = widget.layout_state()
            if widget.template_key:
                state["template"] = widget.template_key
            page[widget.KEY] = state
        data[self.page_key] = page

        # The helper reports its own failure, so there is nothing to catch
        # here any more.
        if self._write_layout_file(data):
            self.client.log(
                "debug", f"[WidgetFramework:{self.page_key}] saved "
                         f"{len(page)} widgets.")

    ## GEOMETRY

    def visible_size(self):
        parent = self.parent()
        if parent is not None and parent.width() > 1 and parent.height() > 1:
            return parent.width(), parent.height()
        return max(self.width(), 1), max(self.height(), 1)

    def update_geometry(self) -> None:
        width, height = self.visible_size()
        self.setGeometry(0, 0, width, height)
        self._chrome.setGeometry(0, 0, width, height)
        self._reposition_zones()
        self._reposition_floating()
        self._chrome.raise_()

        signature = (width, height, len(self._widgets))
        if signature != getattr(self, "_last_signature", None):
            self._last_signature = signature
            try:
                self.client.log(
                    "debug",
                    f"[WidgetFramework:{self.page_key}] layout {width}x{height} "
                    f"page {self.parent().width()}x{self.parent().height()} "
                    f"widgets {len(self._widgets)}")
            except Exception:
                pass

    def _fit_to_content(self, widget: Widget) -> None:
        # Release any fixed size and grow to whatever the content needs. A
        # widget pinned by its constructor to a size its labels have since
        # outgrown clips them with no way to tell from inside.
        widget.ensurePolished()
        layout = widget.layout()
        if layout is not None:
            layout.activate()

        hint = widget.sizeHint()
        minimum = widget.minimumSizeHint()

        if widget.can_resize():
            self._fit_resizable(widget, hint, minimum)
            return

        wanted_w = max(widget.width(), hint.width(), minimum.width())
        wanted_h = max(widget.height(), hint.height(), minimum.height())

        if wanted_w != widget.width() or wanted_h != widget.height():
            widget.setMinimumSize(0, 0)
            widget.setMaximumSize(16777215, 16777215)
            widget.resize(wanted_w, wanted_h)

            # An anchored widget lives inside a zone's layout, which overrides
            # resize() on its next pass. The hierarchy is zone -> row -> widget,
            # so every layout up the chain has to be told the hint changed
            # before the new size sticks.
            widget.updateGeometry()
            self._relayout_zone_of(widget)

    def _fit_resizable(self, widget: Widget, hint, minimum) -> None:
        """
        Keep a size somebody chose, while never clipping the content.

        A resizable widget's size is a decision, not a guess: it was dragged to
        that width, or restored from the saved layout of the last time it was.
        Releasing the fixed size and growing to the content hint - which is
        what a widget that cannot be resized needs - throws that decision away
        on **every page rebuild**, so leaving the home page and coming back
        made the widget a different size than it was left at.

        It is subtle because the saved file stays right the whole time.
        `content_size()` keeps the chosen number, so what is written to disk is
        the width the person picked; only what is on screen differs. Reading
        the layout file to check would have shown nothing wrong.

        The content still wins where it genuinely does not fit, which is what
        the growing was for.
        """
        chosen_w, chosen_h = widget.content_size()

        if not widget.has_chosen_size():
            # Never sized, and no saved layout to restore. The content hint is
            # the sensible starting point - the lazy fallback in
            # content_size() is Qt's default 640x480, which is not a width
            # anybody picked either.
            chosen_w = max(hint.width(), minimum.width())
            chosen_h = max(hint.height(), minimum.height())

        wanted_w = max(widget.MIN_W, min(widget.MAX_W,
                                         max(chosen_w, minimum.width())))
        wanted_h = max(widget.MIN_H, min(widget.MAX_H,
                                         max(chosen_h, minimum.height())))

        # The flag is carried, not set. This runs on every placement, so
        # marking the size here made a widget's first fit read as a decision
        # forever after - and a widget that grows to its content could then
        # never shrink back.
        widget.set_content_size(wanted_w, wanted_h,
                                chosen=widget.has_chosen_size())
        bounds_w, bounds_h = widget.rotated_bounds()
        if (widget.width(), widget.height()) != (bounds_w, bounds_h):
            widget.setFixedSize(bounds_w, bounds_h)
            widget.updateGeometry()
            self._relayout_zone_of(widget)

    def _relayout_zone_of(self, widget: Widget) -> None:
        """
        Tell the layouts between a widget and its zone that its hint changed.

        Stops at the zone, and at THIS framework if there is no zone - a
        floating widget has no zone ancestor, so an unbounded walk carried on
        into the page and the window and called adjustSize() on them. That
        resized the page and the window to their size hints mid-drag, which
        showed up as transparent artifacts and a glitching window while
        resizing a sticky note.
        """
        node = widget.parent()
        while node is not None and node is not self:
            if node.layout() is not None:
                node.layout().invalidate()
                node.layout().activate()
            node.adjustSize()
            if isinstance(node, _AnchorZone):
                # Resized, so it has to be placed again. A zone's width decides
                # where it starts: a centred one sits at (page - zone) / 2, and
                # a right-hand one at page - zone - padding. Growing a zone and
                # leaving it where it was walks its contents off to one side.
                self._reposition_zone(node.anchor_name, node)
                break
            node = node.parent()

    def _clamp(self, widget: Widget, point: QPoint) -> QPoint:
        """
        Keep a widget inside its own edge limit.

        The framework's padding by default, which is what the anchor zones use
        - a floating card sitting flush against the glass while every anchored
        one keeps its margin looks like a mistake.

        A widget can say otherwise. `edge_padding()` of 0 lets it reach the
        window edge, and `content_inset()` tells the clamp how much of the
        widget is transparent, so the limit applies to what can be seen rather
        than to the rectangle around it.
        """
        view_w, view_h = self.visible_size()

        # The widget's own limit, if it has one.
        own = widget.edge_padding()
        pad = self.padding if own is None else max(0, int(own))

        # What is actually visible inside the widget. A sticker is a rectangle
        # containing a shape; the clamp should measure the shape.
        try:
            left, top, right, bottom = widget.content_inset()
        except Exception:
            left = top = right = bottom = 0

        max_x = max(pad - left, view_w - pad - widget.width() + right)
        max_y = max(pad - top, view_h - pad - widget.height() + bottom)
        # A widget wider than the page between margins would be pushed off the
        # left by the clamp, so the lower bound gives way first.
        min_x = min(pad - left, max_x)
        min_y = min(pad - top, max_y)

        return QPoint(min(max(min_x, point.x()), max_x),
                      min(max(min_y, point.y()), max_y))

    def _parse_anchor(self, anchor: str):
        if ":" in anchor:
            name, index = anchor.split(":", 1)
            try:
                return name, int(index)
            except ValueError:
                return name, 0
        return anchor, 0

    def _get_or_create_zone(self, anchor_name: str) -> _AnchorZone:
        if anchor_name not in self._zones:
            zone = _AnchorZone(anchor_name, self.padding, self.widget_spacing)
            zone.setParent(self)
            zone.show()
            self._zones[anchor_name] = zone
            self._reposition_zone(anchor_name, zone)
        return self._zones[anchor_name]

    def _place_in_zone(self, widget: Widget, position: int = None) -> None:
        anchor_name, row_index = self._parse_anchor(widget.anchor)
        zone = self._get_or_create_zone(anchor_name)
        zone.add_widget(widget, row_index, position=position)
        zone.adjustSize()
        self._reposition_zone(anchor_name, zone)

    def _reposition_zones(self) -> None:
        for anchor_name, zone in self._zones.items():
            self._reposition_zone(anchor_name, zone)

    def _reposition_zone(self, anchor_name: str, zone: _AnchorZone) -> None:
        width, height = self.visible_size()
        pad = self.padding
        zone.adjustSize()
        zw, zh = zone.sizeHint().width(), zone.sizeHint().height()

        vertical, horizontal = split_position(anchor_name)

        # Computed from the two halves rather than listed per name: nine cases
        # written out is six of them saying the same thing twice.
        x = {"left":   pad,
             "center": (width - zw) // 2,
             "right":  width - zw - pad}[horizontal]
        y = {"top":    pad,
             "center": (height - zh) // 2,
             "bottom": height - zh - pad}[vertical]
        zone.move(x, y)

    def _reposition_floating(self) -> None:
        for widget in self._widgets:
            if "offset" in widget.tags:
                self._apply_offset(widget)
                continue
            if "floating" not in widget.tags:
                continue
            point = self._clamp(widget, QPoint(widget.float_x, widget.float_y))
            widget.float_x, widget.float_y = point.x(), point.y()
            widget.move(point)

    def nearest_anchor(self, point: QPoint) -> str:
        """
        Which of the nine a dropped widget lands in.

        Thirds both ways now. It was halves vertically, so the middle row was
        unreachable by dragging - a widget dropped dead centre went to whichever
        of top or bottom it was a pixel nearer.
        """
        width, height = self.visible_size()
        horizontal = ("left" if point.x() < width / 3
                      else "right" if point.x() > width * 2 / 3 else "center")
        vertical = ("top" if point.y() < height / 3
                    else "bottom" if point.y() > height * 2 / 3 else "center")
        if vertical == "center" and horizontal == "center":
            return "center"
        return f"{vertical}-{horizontal}"

    ## INTERACTION

    def _frame_pos(self, widget: Widget) -> QPoint:
        """
        The widget's top-left in FRAMEWORK coordinates.

        Never use widget.pos() for this. An anchored widget sits inside a
        zone's row, so pos() is row-relative and usually (0,0) - mixing it
        with a framework-space mouse position made the grab offset equal the
        click point, so the first drag step moved the widget to (0,0) and the
        selection border drew in the wrong place entirely.
        """
        if widget.parent() is None:
            return widget.pos()
        return widget.mapTo(self, QPoint(0, 0))

    def _widget_at(self, point: QPoint) -> Optional[Widget]:
        # Topmost first, then the rest in reverse placement order, so the
        # widget drawn on top is the one that gets the press.
        for widget in reversed(self._topmost + self._widgets):
            if widget.parent() is None or not widget.isVisible():
                continue
            local = point - self._frame_pos(widget)
            if widget.contains_point(local):
                return widget
        return None

    def mousePressEvent(self, event) -> None:
        point = event.pos()

        if self.active is not None:
            handle = self._handle_at(point)
            if handle:
                self._begin_handle(handle, point)
                event.accept()
                return
            if self._widget_at(point) is not self.active:
                self.commit_transform()

        widget = self._widget_at(point)
        if widget is None:
            # Not on a widget: hand it to the page so a swipe still works.
            event.ignore()
            super().mousePressEvent(event)
            return

        # Touched means in front.
        #
        # Only for a widget that floats: an anchored one sits in a zone and its
        # order there is the layout's business, not a matter of which was
        # pressed last.
        if widget.floating:
            widget.bring_to_front()
            self._chrome.raise_()

        self._pressed = widget
        self._moved = False
        self._grab_offset = point - self._frame_pos(widget)
        self._hold.start()
        event.accept()

    def _hold_elapsed(self) -> None:
        if self._pressed is not None:
            self.begin_transform(self._pressed)

    def begin_transform(self, widget: Widget) -> None:
        # Selecting only. The widget stays exactly where it is - lifting it
        # out of its zone here disturbed the layout the moment you held it,
        # and left the offset handle with no slot to offset from.
        self.active = widget
        self._mode = "move"
        self._was_anchored = widget.anchor if "anchored" in widget.tags else ""

        widget.raise_()
        self._chrome.show()
        self._chrome.raise_()
        self._chrome.update()

    def _lift_from_zone(self, widget: Widget) -> None:
        """Take an anchored widget out of its layout so it can be dragged."""
        if "anchored" not in widget.tags and "offset" not in widget.tags:
            return
        base = self._frame_pos(widget)
        for zone in self._zones.values():
            zone.remove_widget(widget)
        widget.setParent(self)
        widget.move(base)
        widget.show()
        widget.raise_()
        self._chrome.raise_()
        widget.tags = ["lifted"]

    def mouseMoveEvent(self, event) -> None:
        # Nothing selected means this drag is not ours - let it through, or
        # the page cannot be swiped to change sub-pages. The framework covers
        # the whole page, so anything it swallows is lost.
        if self.active is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            event.ignore()
            super().mouseMoveEvent(event)
            return

        point = event.pos()
        widget = self.active

        if self._mode == "move":
            if not self._moved:
                # First actual movement: now it leaves the layout.
                self._lift_from_zone(widget)
            self._moved = True
            widget.move(self._clamp(widget, point - self._grab_offset))
            widget.float_x, widget.float_y = widget.x(), widget.y()
            self._update_drop_target(widget)
            self.schedule_save()

        elif self._mode == "resize":
            origin = self._frame_pos(widget)
            width = max(widget.MIN_W, min(widget.MAX_W, point.x() - origin.x()))
            height = max(widget.MIN_H, min(widget.MAX_H, point.y() - origin.y()))

            if getattr(widget, "KEEP_ASPECT", False):
                # Driven by whichever axis was dragged further, so the corner
                # follows the finger on the axis somebody is actually pulling
                # rather than fighting them on the other one.
                ratio = self._start_ratio or 1.0
                if abs(width - self._start_w) >= abs(height - self._start_h):
                    height = width / ratio
                else:
                    width = height * ratio
                # Clamped after, and on both axes together: clamping one alone
                # is how a resize ends up out of shape at the limits.
                scale = min(1.0,
                            widget.MAX_W / max(1.0, width),
                            widget.MAX_H / max(1.0, height))
                width, height = width * scale, height * scale
                scale = max(1.0,
                            widget.MIN_W / max(1.0, width),
                            widget.MIN_H / max(1.0, height))
                width, height = width * scale, height * scale
            # setFixedSize, not resize: an anchored widget is inside a layout
            # that would otherwise put its size hint straight back. A fixed
            # size is what the layout has to honour, and it is what keeps the
            # widget inside its zone rather than escaping it.
            widget.set_content_size(int(width), int(height))
            bounds_w, bounds_h = widget.rotated_bounds()
            widget.setFixedSize(bounds_w, bounds_h)
            widget.updateGeometry()
            self._relayout_zone_of(widget)
            self._reposition_zones()
            self.schedule_save()

        elif self._mode == "offset":
            delta = point - self._offset_grab
            widget.offset_x = self._offset_start.x() + delta.x()
            widget.offset_y = self._offset_start.y() + delta.y()
            self._apply_offset(widget)
            self.schedule_save()

        elif self._mode == "rotate":
            centre = self._frame_pos(widget) + QPoint(widget.width() // 2,
                                                      widget.height() // 2)
            pointer = math.degrees(math.atan2(point.y() - centre.y(),
                                              point.x() - centre.x()))
            angle = self._start_angle + (pointer - self._start_vector)
            nearest = round(angle / SNAP_DEGREES) * SNAP_DEGREES
            if abs(angle - nearest) <= SNAP_TOLERANCE:
                angle = nearest
            widget.angle = angle % 360
            # Grow the box to hold the rotated content, or the corners are
            # clipped off by the widget's own edges.
            widget.resize_to_fit_rotation()
            widget.update()
            self.schedule_save()

        self._chrome.update()

    def mouseReleaseEvent(self, event) -> None:
        self._hold.stop()

        if self.active is None:
            widget, self._pressed = self._pressed, None
            if widget is not None and not self._moved:
                widget.on_activate()
            else:
                event.ignore()
                super().mouseReleaseEvent(event)
            return

        if self._mode in ("resize", "rotate", "offset"):
            self._mode = "move"
            self.active.on_transform_finished()
            self.save_layout()
            self._chrome.update()
            return

        if self._mode == "move" and self._moved:
            self._settle(self.active)

        self._pressed = None

    def _detach_for_offset(self, widget: Widget) -> None:
        """Float an anchored widget while keeping its slot in the row."""
        if "anchored" not in widget.tags:
            return
        anchor_name, _row = self._parse_anchor(widget.anchor)
        zone = self._zones.get(anchor_name)
        if zone is None:
            return
        base = self._frame_pos(widget)
        zone.hold_slot(widget)
        widget.setParent(self)
        widget.move(base)
        widget.show()
        widget.raise_()
        self._chrome.raise_()
        widget.tags = ["offset"]

    def _offset_base(self, widget: Widget) -> Optional[QPoint]:
        """Where the anchor would put this widget, from its placeholder."""
        anchor_name, _row = self._parse_anchor(widget.anchor)
        zone = self._zones.get(anchor_name)
        if zone is None:
            return None
        stand_in = zone.placeholder_for(widget)
        if stand_in is None:
            return None
        return stand_in.mapTo(self, QPoint(0, 0))

    def _apply_offset(self, widget: Widget) -> None:
        base = self._offset_base(widget)
        if base is None:
            return
        widget.move(self._clamp(widget, base + QPoint(widget.offset_x,
                                                      widget.offset_y)))

    def clear_offset(self, widget: Widget) -> None:
        """Put an offset widget back where its anchor wants it."""
        if widget is None:
            return
        widget.offset_x = widget.offset_y = 0
        anchor_name, _row = self._parse_anchor(widget.anchor)
        zone = self._zones.get(anchor_name)
        if zone is not None and zone.placeholder_for(widget) is not None:
            zone.release_slot(widget)
        widget.tags = ["anchored"]
        self.update_geometry()
        self.save_layout()
        self._chrome.update()

    def _update_drop_target(self, widget: Widget) -> None:
        """Where an anchored drop would land, for the indicator and the drop."""
        if widget.can_float():
            self._drop_anchor = ""
            return
        centre = self._frame_pos(widget) + QPoint(widget.width() // 2,
                                                  widget.height() // 2)
        anchor = self.nearest_anchor(centre)
        zone = self._zones.get(anchor)
        self._drop_anchor = anchor
        self._drop_slot = (zone.slot_for_x(0, centre.x(), skip=widget)
                           if zone is not None else 0)

    def _drop_indicator_rect(self) -> Optional[QRect]:
        """A vertical bar showing the slot a widget would drop into."""
        if not self._drop_anchor:
            return None
        width, height = self.visible_size()
        pad = self.padding
        zone = self._zones.get(self._drop_anchor)

        row = zone.widgets_in_row(0) if zone is not None else []
        row = [w for w in row if w is not self.active]

        bar_h = self.active.height() if self.active is not None else 80
        if "top" in self._drop_anchor:
            y = pad
        else:
            y = height - pad - bar_h

        if not row:
            if "left" in self._drop_anchor:      x = pad
            elif "right" in self._drop_anchor:   x = width - pad - 6
            else:                                x = width // 2 - 3
            return QRect(int(x), int(y), 6, int(bar_h))

        slot = min(self._drop_slot, len(row))
        if slot >= len(row):
            edge = row[-1]
            x = self._frame_pos(edge).x() + edge.width() + self.widget_spacing // 2
        else:
            edge = row[slot]
            x = self._frame_pos(edge).x() - self.widget_spacing // 2
        top = min((self._frame_pos(w).y() for w in row), default=y)
        tall = max((w.height() for w in row), default=int(bar_h))
        return QRect(int(x) - 3, int(top), 6, int(tall))

    def _settle(self, widget: Widget) -> None:
        for zone in self._zones.values():
            zone.remove_widget(widget)

        if widget.can_float():
            widget.floating = True
            widget.tags = ["floating"]
            widget.setParent(self)
            widget.move(widget.float_x, widget.float_y)
            widget.show()
            widget.raise_()
        else:
            widget.floating = False
            widget.tags = ["anchored"]
            centre = self._frame_pos(widget) + QPoint(widget.width() // 2,
                                                      widget.height() // 2)
            widget.anchor = self._drop_anchor or self.nearest_anchor(centre)
            self._place_in_zone(widget, position=self._drop_slot)
            widget.show()
        self._drop_anchor = ""

        widget.on_transform_finished()
        self.update_geometry()
        self.save_layout()
        self._chrome.raise_()
        self._chrome.update()

    def commit_transform(self) -> None:
        if self.active is None:
            return
        widget, self.active = self.active, None
        self._mode = ""

        if "lifted" in widget.tags:
            # Never dropped anywhere, so put it back where it came from.
            widget.tags = ["anchored"]
            widget.anchor = self._was_anchored or widget.anchor
            self._place_in_zone(widget)
            widget.show()

        self._was_anchored = ""
        self._chrome.hide()
        widget.on_transform_finished()
        self.update_geometry()
        self.save_layout()

    ## HANDLES

    def _handle_rects(self) -> dict:
        widget = self.active
        if widget is None or widget.parent() is None:
            return {}

        rects = {}
        half = HANDLE // 2
        origin = self._frame_pos(widget)

        if widget.can_resize():
            corner = origin + QPoint(widget.width(), widget.height())
            rects["resize"] = QRect(corner.x() - half, corner.y() - half, HANDLE, HANDLE)

        if widget.can_rotate():
            top = origin + QPoint(widget.width() // 2, -ROTATE_ARM)
            rects["rotate"] = QRect(top.x() - half, top.y() - half, HANDLE, HANDLE)

        if ({"anchored", "lifted", "offset"} & set(widget.tags)) or widget.has_offset():
            # Nudge the widget away from where its anchor put it.
            nudge = origin + QPoint(0, widget.height())
            rects["offset"] = QRect(nudge.x() - half, nudge.y() - half, HANDLE, HANDLE)
            if widget.has_offset():
                back = origin + QPoint(0, 0)
                rects["reset"] = QRect(back.x() - half, back.y() - half, HANDLE, HANDLE)

        commit = origin + QPoint(widget.width(), 0)
        rects["commit"] = QRect(commit.x() - half, commit.y() - half, HANDLE, HANDLE)

        # A widget's own button, under the commit tick.
        #
        # Below rather than beside: the top corners belong to rotate and
        # commit, the bottom to resize, and a widget that offers this is
        # usually one somebody is about to open a dialog from - which wants to
        # be near the finger that just selected it.
        if widget.chrome_button() is not None:
            spot = origin + QPoint(widget.width(), HANDLE + 8)
            rects["custom"] = QRect(spot.x() - half, spot.y() - half,
                                    HANDLE, HANDLE)

        if widget.REMOVABLE:
            # Mid-edge, because every corner is already spoken for by a handle
            # that may or may not be present depending on the widget.
            #
            # Left by preference, flipping to the right edge when the left
            # would hang off the view - a widget anchored hard against the
            # left would otherwise have half its delete button clipped away.
            view_w, view_h = self.visible_size()
            x = origin.x()
            if x - half < 0:
                x = origin.x() + widget.width()
            y = origin.y() + widget.height() // 2
            y = max(half, min(y, view_h - half))
            rects["remove"] = QRect(int(x) - half, int(y) - half, HANDLE, HANDLE)

        return rects

    def remove_active(self) -> None:
        """
        Take the selected widget off the page and put it back in the panel.

        Not a delete: unplace() keeps the instance, so a sticky note keeps its
        text and dragging it back out restores it exactly. That is why this is
        a single tap with no confirmation - the worst case is one drag to undo.

        A transient widget is the exception. It has no entry in the panel to go
        back to, and it stands for something still happening - so removing one
        has to reach whatever put it there. A timer whose widget was filed away
        while the countdown kept running would announce itself minutes later
        from nowhere.
        """
        widget = self.active
        if widget is None or not widget.REMOVABLE:
            return

        name = widget.display_name()

        if getattr(widget, "transient", False):
            key = widget.KEY
            handled = False
            hook = getattr(widget, "on_dismissed", None)
            if callable(hook):
                try:
                    handled = bool(hook())
                except Exception as e:
                    self.client.log("warning",
                                    f"[Widgets] {key} on_dismissed failed: {e}",
                                    include_traceback=True)
            self.active = None
            self._mode = ""
            self._chrome.hide()
            if not handled:
                # The hook did not take responsibility, so this is an ordinary
                # dismissal. Idempotent either way - dismiss_transient on an
                # already-removed key is a no-op.
                self.dismiss_transient(key)
            return

        self.unplace(widget)
        self.active = None
        self._mode = ""
        self._chrome.hide()
        self.save_layout()
        try:
            # Not kept.
            #
            # This is a receipt for something the person just did, and the
            # result is on screen in front of them - the widget is gone and
            # the panel it went to is one tap away. A history full of "moved
            # to the widgets panel" buries the notifications that were worth
            # keeping.
            self.client.simple_notify("widgets", "Widgets",
                                      f"'{name}' moved to the widgets panel.",
                                      history=False)
        except Exception:
            pass

    def _handle_at(self, point: QPoint) -> str:
        for name, rect in self._handle_rects().items():
            if rect.adjusted(-HANDLE_HIT_PAD, -HANDLE_HIT_PAD,
                             HANDLE_HIT_PAD, HANDLE_HIT_PAD).contains(point):
                return name
        return ""

    def _begin_handle(self, handle: str, point: QPoint) -> None:
        if handle == "custom":
            entry = self.active.chrome_button() if self.active else None
            if entry:
                try:
                    entry[2]()
                except Exception as e:
                    self.client.log("warning",
                                    f"[Widgets] {self.active.KEY} button "
                                    f"failed: {e}")
            return

        if handle == "commit":
            self.commit_transform()
            return
        if handle == "remove":
            self.remove_active()
            return
        if handle == "reset":
            self.clear_offset(self.active)
            return
        if handle == "offset":
            self._mode = "offset"
            self._offset_start = QPoint(self.active.offset_x, self.active.offset_y)
            self._offset_grab = point
            self._detach_for_offset(self.active)
            return
        if handle == "resize":
            self._mode = "resize"
            # The shape it started at, so an aspect-locked drag has something
            # to keep. Taken here, where the drag begins, rather than in the
            # hit-rect builder - that runs on hover too, and re-reading it
            # mid-drag would compound its own rounding until the sticker crept
            # square.
            self._start_w, self._start_h = self.active.content_size()
            self._start_ratio = (self._start_w / float(self._start_h)
                                 if self._start_h else 1.0)
        elif handle == "rotate":
            self._mode = "rotate"
            widget = self.active
            centre = self._frame_pos(widget) + QPoint(widget.width() // 2,
                                                      widget.height() // 2)
            self._start_angle = widget.angle
            self._start_vector = math.degrees(
                math.atan2(point.y() - centre.y(), point.x() - centre.x()))

    ## CHROME
    #
    # Painted through an event filter on a raised, mouse-transparent overlay.
    # Child widgets paint over their parent, so drawing this in the
    # framework's own paintEvent would put the border underneath the widget
    # it belongs to.

    def eventFilter(self, watched, event) -> bool:
        if watched is self._chrome and event.type() == QEvent.Type.Paint:
            self._paint_chrome()
            return True
        return super().eventFilter(watched, event)

    def _paint_chrome(self) -> None:
        widget = self.active
        if widget is None or widget.parent() is None:
            return

        painter = QPainter(self._chrome)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = QRect(self._frame_pos(widget), widget.size())
        painter.setPen(QPen(QColor("#6fa8e0"), 2, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

        if widget.can_rotate():
            top = QPoint(rect.center().x(), rect.top())
            painter.drawLine(top, QPoint(top.x(), top.y() - ROTATE_ARM))

        for name, handle_rect in self._handle_rects().items():
            if name == "commit":
                painter.setBrush(QBrush(QColor("#1f7a4d")))
                painter.setPen(QPen(QColor("#7ed6a6"), 2))
            elif name == "reset":
                painter.setBrush(QBrush(QColor("#5a4a1c")))
                painter.setPen(QPen(QColor("#e0c46f"), 2))
            elif name == "remove":
                painter.setBrush(QBrush(QColor("#7a2020")))
                painter.setPen(QPen(QColor("#e08a8a"), 2))
            else:
                painter.setBrush(QBrush(QColor("#1c1c1c")))
                painter.setPen(QPen(QColor("#6fa8e0"), 2))
            painter.drawEllipse(handle_rect)

            painter.setPen(QPen(QColor("#f2f2f2"), 3))
            centre = handle_rect.center()
            arm = HANDLE // 5          # glyphs scale with the handle

            if name == "resize":
                painter.drawLine(centre.x() - arm, centre.y() + arm,
                                 centre.x() + arm, centre.y() - arm)
                painter.drawLine(centre.x() + arm, centre.y() - arm,
                                 centre.x() + arm - 4, centre.y() - arm)
                painter.drawLine(centre.x() - arm, centre.y() + arm,
                                 centre.x() - arm, centre.y() + arm - 4)
            elif name == "custom":
                entry = widget.chrome_button()
                glyph = entry[0] if entry else ""
                if glyph:
                    # Into a rect, not at a point.
                    #
                    # QIcon.pixmap() answers in DEVICE pixels, so on a HiDPI
                    # panel width() is twice the drawn size - and centring by
                    # half of it put the glyph up and left of its own circle.
                    # A target rectangle is scaled to fit whatever comes back.
                    from src.ui.icons import icon as _icon
                    inset = 7
                    target = handle_rect.adjusted(inset, inset,
                                                  -inset, -inset)
                    painter.drawPixmap(
                        target, _icon(glyph, color="#ffffff").pixmap(
                            target.width(), target.height()))
            elif name == "rotate":
                # Three quarters of a circle, with the head on the open end.
                #
                # The previous one swept 280 degrees and put its arrowhead at
                # a fixed point, which landed mid-arc - a closed ring with a
                # spike through it. The gap is what makes this read as
                # turning; the head has to sit where the line stops.
                span = 260
                start = 100
                box = QRect(centre.x() - arm, centre.y() - arm,
                            arm * 2, arm * 2)
                painter.drawArc(box, start * 16, span * 16)

                # Where the arc ends, and which way it is going there.
                end = math.radians(start + span)
                tip = QPoint(int(centre.x() + arm * math.cos(end)),
                             int(centre.y() - arm * math.sin(end)))
                for offset in (150, 210):
                    angle = math.radians(start + span + offset)
                    painter.drawLine(
                        tip,
                        QPoint(int(tip.x() + 5 * math.cos(angle)),
                               int(tip.y() - 5 * math.sin(angle))))
            elif name == "offset":
                # A four-way move arrow.
                painter.drawLine(centre.x() - arm, centre.y(), centre.x() + arm, centre.y())
                painter.drawLine(centre.x(), centre.y() - arm, centre.x(), centre.y() + arm)
                for dx, dy in ((-arm, 0), (arm, 0)):
                    step = 3 if dx < 0 else -3
                    painter.drawLine(centre.x() + dx, centre.y(),
                                     centre.x() + dx + step, centre.y() - 3)
                    painter.drawLine(centre.x() + dx, centre.y(),
                                     centre.x() + dx + step, centre.y() + 3)
                for dy in (-arm, arm):
                    step = 3 if dy < 0 else -3
                    painter.drawLine(centre.x(), centre.y() + dy,
                                     centre.x() - 3, centre.y() + dy + step)
                    painter.drawLine(centre.x(), centre.y() + dy,
                                     centre.x() + 3, centre.y() + dy + step)
            elif name == "remove":
                # A bin: lid, body, and two slats. Not an X - that reads as
                # "close", and the commit tick next to it already means done.
                painter.drawLine(centre.x() - arm, centre.y() - arm + 2,
                                 centre.x() + arm, centre.y() - arm + 2)
                painter.drawLine(centre.x() - 3, centre.y() - arm - 1,
                                 centre.x() + 3, centre.y() - arm - 1)
                painter.drawLine(centre.x() - arm + 3, centre.y() - arm + 2,
                                 centre.x() - arm + 4, centre.y() + arm)
                painter.drawLine(centre.x() + arm - 3, centre.y() - arm + 2,
                                 centre.x() + arm - 4, centre.y() + arm)
                painter.drawLine(centre.x() - arm + 4, centre.y() + arm,
                                 centre.x() + arm - 4, centre.y() + arm)
            elif name == "reset":
                # An arrow curling back to the start.
                painter.drawArc(QRect(centre.x() - arm, centre.y() - arm,
                                      arm * 2, arm * 2), 40 * 16, 280 * 16)
                painter.drawLine(centre.x() + arm - 2, centre.y() - arm + 2,
                                 centre.x() + arm - 2, centre.y() - arm + 8)
                painter.drawLine(centre.x() + arm - 2, centre.y() - arm + 2,
                                 centre.x() + arm - 8, centre.y() - arm + 2)
            else:
                painter.drawLine(centre.x() - arm + 2, centre.y(),
                                 centre.x() - 1, centre.y() + arm - 2)
                painter.drawLine(centre.x() - 1, centre.y() + arm - 2,
                                 centre.x() + arm, centre.y() - arm + 2)

        indicator = self._drop_indicator_rect()
        if indicator is not None:
            painter.setPen(QPen(QColor("#7ed6a6"), 2))
            painter.setBrush(QBrush(QColor(126, 214, 166, 200)))
            painter.drawRoundedRect(indicator, 3, 3)

            # Beside the bar and pointing inwards, never above it. Above meant
            # a top-anchored indicator put its label off the top of the screen,
            # and a corner one had it cut off either way.
            text = self._drop_anchor.replace("-", " ")
            painter.setFont(make_font(SIZES.S1, bold=True))
            metrics = painter.fontMetrics()
            label_w = metrics.horizontalAdvance(text) + 16
            label_h = metrics.height() + 6
            gap = 10

            inward_right = "right" not in self._drop_anchor
            if inward_right:
                x = indicator.right() + gap
                align = Qt.AlignmentFlag.AlignLeft
            else:
                x = indicator.left() - gap - label_w
                align = Qt.AlignmentFlag.AlignRight

            y = indicator.center().y() - label_h // 2

            # Keep it on screen whichever way it ended up pointing.
            view_w, view_h = self.visible_size()
            x = max(4, min(x, view_w - label_w - 4))
            y = max(4, min(y, view_h - label_h - 4))

            label = QRect(int(x), int(y), int(label_w), int(label_h))
            painter.setBrush(QBrush(QColor(18, 18, 18, 215)))
            painter.setPen(QPen(QColor(126, 214, 166, 160), 1))
            painter.drawRoundedRect(label, 5, 5)
            painter.setPen(QPen(QColor("#7ed6a6")))
            painter.drawText(label.adjusted(8, 0, -8, 0),
                             int(align | Qt.AlignmentFlag.AlignVCenter), text)

        painter.end()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.update_geometry()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.update_geometry()

    def hideEvent(self, event) -> None:
        # Flush anything the debounce has not written yet. Closing the app
        # while a save was still pending would otherwise lose the last change.
        super().hideEvent(event)
        if self._save_timer.isActive():
            self._save_timer.stop()
            self.save_layout()

    def closeEvent(self, event) -> None:
        if self._save_timer.isActive():
            self._save_timer.stop()
        self.save_layout()
        super().closeEvent(event)

    ## MAINTENANCE

    def tick_widgets(self) -> None:
        changed = False
        for widget in list(self._widgets):
            if widget is self.active or widget.can_resize():
                continue
            if widget.parent() is None:
                continue
            try:
                hint = widget.sizeHint()
                if hint.width() <= widget.width() and hint.height() <= widget.height():
                    continue
                self._fit_to_content(widget)
                changed = True
            except RuntimeError:
                continue

        # Zones are placed from their own size, and a widget can change that
        # without the framework hearing about it: showing or hiding a child
        # widget resizes the zone through Qt's layout alone. The now-playing
        # card does exactly that on every track - an artist line that appears,
        # a progress bar that does not - and a resizable widget is skipped by
        # the refit above, so nothing else would put its zone back.
        #
        # A move() per zone, at most nine of them, once a second.
        self._reposition_zones()

        if changed:
            self.update_geometry()

    def set_ticking(self, state: bool) -> None:
        """
        Start or stop all periodic work on this framework.

        Covers the re-fit pass and every registered widget's own tick timer -
        including the ones sitting in the panel, which were ticking as happily
        as the placed ones despite being off screen twice over.
        """
        state = bool(state)
        if state == getattr(self, "_ticking", True):
            return
        self._ticking = state

        if state:
            self._refit.start(1000)
        else:
            self._refit.stop()

        for widget in list(self.registry.values()):
            try:
                widget.resume_tick() if state else widget.suspend_tick()
            except RuntimeError:
                continue    # deleted between the copy and here

        if state:
            # One pass now rather than up to a second from now: a widget whose
            # content changed while suspended is otherwise visibly misfitted
            # for the first second after the page comes back.
            self.tick_widgets()

    ## TRANSIENT WIDGETS

    # Fractions of the page, one per POSITION. Every entry is a band rather
    # than a point so a random pick inside it still has somewhere to go.
    #
    # Keyed on the canonical nine. The old spellings - "top", "left",
    # "middle" - still arrive from links and scripts and are folded in by
    # normalise_position before this is read.
    REGIONS = {
        "top-left":      (0.00, 0.00, 0.45, 0.45),
        "top-center":    (0.28, 0.00, 0.72, 0.40),
        "top-right":     (0.55, 0.00, 1.00, 0.45),
        "center-left":   (0.00, 0.28, 0.40, 0.72),
        "center":        (0.25, 0.25, 0.75, 0.75),
        "center-right":  (0.60, 0.28, 1.00, 0.72),
        "bottom-left":   (0.00, 0.55, 0.45, 1.00),
        "bottom-center": (0.28, 0.60, 0.72, 1.00),
        "bottom-right":  (0.55, 0.55, 1.00, 1.00),
    }

    #The old name, for anything still reaching for it.
    QUADRANTS = REGIONS

    #keep-out distance between a transient widget and anything already placed
    TRANSIENT_GAP = 12
    #how many random positions to try inside a quadrant before giving up on
    #randomness and taking the first free slot found by scanning
    TRANSIENT_TRIES = 40

    def show_transient(self, widget: Widget, center=None, quadrant: str = "",
                       timeout: float = 0, bundle: bool = True,
                       on_expired: Callable = None, at: str = "") -> Widget:
        """
        Place a widget that exists because something happened.

        A thin wrapper over place(): the only thing transience changes is that
        the widget is never written to the layout and may expire. Where it goes
        is the same question for a timer as for a note somebody put up, and it
        is answered in one place.

        `at` (or `quadrant`, its old name) is one of the nine positions;
        `center` is an exact (x, y) in page pixels and wins over it. Either way
        the result never overlaps anything already on the page - a timer that
        lands on top of the clock is worse than one a few pixels from where it
        was asked for.

        `timeout` in seconds dismisses it again. Nothing else happens on
        expiry unless `on_expired` is given: whatever asked for the widget is
        responsible for saying so, and for any tidying up that goes with it.
        """
        widget.transient = True
        widget.floating = True
        widget.anchor = FLOATING
        if widget.KEY not in self.registry:
            self.registry[widget.KEY] = widget

        where = at or quadrant

        if widget in self._widgets:
            point = self.free_point_in(widget, center=center, at=where,
                                       bundle=bundle)
            widget.float_x, widget.float_y = point
            widget.move(*point)
        else:
            self.place(widget, save=False, at=where, exact=center,
                       bundle=bundle)

        widget.raise_()
        self._chrome.raise_()

        if timeout and timeout > 0:
            key = f"transient:{self.page_key}:{widget.KEY}"

            def expire(k=widget.KEY, hook=on_expired):
                self.dismiss_transient(k)
                if callable(hook):
                    try:
                        hook()
                    except Exception as e:
                        self.client.log("warning",
                                        f"[Widgets] on_expired for '{k}' "
                                        f"failed: {e}", include_traceback=True)

            self.client.TIMEOUTS.add(float(timeout), expire, key, transient=True)
            self.client.TIMEOUTS.start(key)
        return widget

    def make_transient(self, key: str, **kwargs):
        """
        A fresh instance of a registered widget, for transient placement.

        A registered widget is a singleton under its KEY, so a transient copy
        needs its own - otherwise dismissing the copy would take the real one
        off the page with it. `MULTIPLE` templates already work this way; this
        does the same for anything else that can be built without arguments.
        """
        entry = self.templates.get(key)
        if entry is not None:
            widget_class, args, kw = entry
            merged = {**kw, **kwargs}
        else:
            existing = self.registry.get(key)
            if existing is None:
                return None
            widget_class, args, merged = existing.__class__, (), dict(kwargs)

        instance_key = self.reserve_key(key, transient=True)

        try:
            widget = widget_class(self.client, *args, **merged)
        except Exception as e:
            self.client.log("warning",
                            f"[WidgetFramework] Could not build a transient "
                            f"'{key}': {e}")
            self._reserved.discard(instance_key)
            return None

        self._reserved.discard(instance_key)
        widget.KEY = instance_key
        widget.template_key = key
        widget.transient = True
        self.registry[instance_key] = widget
        return widget

    def dismiss_transient(self, key: str) -> bool:
        widget = self.registry.get(key)
        if widget is None or not getattr(widget, "transient", False):
            return False
        try:
            self.client.TIMEOUTS.discard(f"transient:{self.page_key}:{key}")
        except Exception:
            pass
        # remove() runs teardown, stops the tick and detaches. save_layout()
        # inside it is what clears any stale entry for this key.
        self.remove(key)
        return True

    def transient_widgets(self) -> list:
        return [w for w in self._widgets if getattr(w, "transient", False)]

    def _occupied_rects(self, exclude: Widget = None) -> list:
        """Where a transient widget may not land, with its keep-out gap."""
        gap = self.TRANSIENT_GAP
        rects = []
        for other in self._widgets:
            if other is exclude:
                continue
            try:
                if not other.isVisible() and other is not exclude:
                    pass
                pos = self._frame_pos(other)
                rects.append(QRect(pos.x() - gap, pos.y() - gap,
                                   other.width() + gap * 2,
                                   other.height() + gap * 2))
            except RuntimeError:
                continue
        return rects

    def _fits(self, x: int, y: int, w: int, h: int, blocked: list,
              page_w: int, page_h: int) -> bool:
        if x < self.padding or y < self.padding:
            return False
        if x + w > page_w - self.padding or y + h > page_h - self.padding:
            return False
        candidate = QRect(x, y, w, h)
        return not any(candidate.intersects(r) for r in blocked)

    def free_point_in(self, widget: Widget, center=None, at: str = "",
                      bundle: bool = False) -> tuple:
        """
        Somewhere inside `at` that nothing already occupies.

        Used by every floating placement, not only a transient one: a sticky
        note asked for the bottom left has the same problem a timer does, which
        is that the obvious spot is often already taken.
        """
        import random

        page_w, page_h = self.visible_size()
        w, h = max(1, widget.width()), max(1, widget.height())
        blocked = self._occupied_rects(exclude=widget)

        def clamp(x, y):
            return (max(self.padding, min(int(x), page_w - w - self.padding)),
                    max(self.padding, min(int(y), page_h - h - self.padding)))

        # 1. An exact centre was asked for. Honoured if it is free, and
        #    otherwise pushed outwards in a ring until it is - moving it is
        #    better than dropping it on top of something.
        if center is not None:
            try:
                cx, cy = int(center[0]), int(center[1])
            except (TypeError, ValueError, IndexError):
                cx = cy = None
            if cx is not None:
                x, y = clamp(cx - w // 2, cy - h // 2)
                if self._fits(x, y, w, h, blocked, page_w, page_h):
                    return (x, y)
                found = self._spiral_out(x, y, w, h, blocked, page_w, page_h)
                if found:
                    return found
                return (x, y)     # nowhere free; honour what was asked for

        # 2. Bundle with the transient widgets already up, so several timers
        #    read as a group rather than scattered across the screen.
        if bundle:
            siblings = [t for t in self.transient_widgets() if t is not widget]
            if siblings:
                last = siblings[-1]
                pos = self._frame_pos(last)
                gap = self.TRANSIENT_GAP
                for dx, dy in ((0, last.height() + gap),
                               (last.width() + gap, 0),
                               (0, -(h + gap)),
                               (-(w + gap), 0)):
                    x, y = clamp(pos.x() + dx, pos.y() + dy)
                    if self._fits(x, y, w, h, blocked, page_w, page_h):
                        return (x, y)

        # 3. Random inside the region, retried until something is free.
        left, top, right, bottom = self.REGIONS[
            normalise_position(at, "bottom-right")]
        x0, y0 = int(page_w * left), int(page_h * top)
        x1, y1 = int(page_w * right) - w, int(page_h * bottom) - h

        for _ in range(self.TRANSIENT_TRIES):
            x = random.randint(min(x0, x1), max(x0, x1))
            y = random.randint(min(y0, y1), max(y0, y1))
            x, y = clamp(x, y)
            if self._fits(x, y, w, h, blocked, page_w, page_h):
                return (x, y)

        # 4. Nothing random worked. Scan for the first free slot anywhere
        #    before giving up and stacking - a busy page should still land
        #    somewhere sensible rather than on top of the clock.
        step = 24
        for y in range(self.padding, max(self.padding + 1, page_h - h), step):
            for x in range(self.padding, max(self.padding + 1, page_w - w), step):
                if self._fits(x, y, w, h, blocked, page_w, page_h):
                    return (x, y)

        return clamp(x0, y0)

    def _spiral_out(self, x: int, y: int, w: int, h: int, blocked: list,
                    page_w: int, page_h: int):
        """Nearest free position to (x, y), searched in widening rings."""
        step = 16
        for ring in range(1, 40):
            radius = ring * step
            for dx, dy in ((radius, 0), (-radius, 0), (0, radius), (0, -radius),
                           (radius, radius), (-radius, radius),
                           (radius, -radius), (-radius, -radius)):
                cx, cy = x + dx, y + dy
                if self._fits(cx, cy, w, h, blocked, page_w, page_h):
                    return (cx, cy)
        return None

    ## PANEL

    def _build_panel(self) -> None:
        if self.panel is not None:
            return
        from src.ui.overlays import Panel

        host = QWidget()
        set_style(host, "common", "transparent")
        column = QVBoxLayout(host)
        column.setContentsMargins(14, 14, 14, 14)
        column.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Widgets")
        title.setFont(make_font(SIZES.M1, bold=True))
        set_style(title, "common", "text-strong")
        header.addWidget(title)
        header.addStretch()

        column.addLayout(header)

        hint = QLabel("Place a widget on the page. Hold a placed widget to "
                      "move, resize, rotate or remove it.")
        hint.setFont(make_font(SIZES.S1))
        hint.setWordWrap(True)
        set_style(hint, "common", "text-muted")
        column.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        style_scrollbar(scroll)

        inner = QWidget()
        set_style(inner, "common", "transparent")
        self._panel_items = QVBoxLayout(inner)
        self._panel_items.setContentsMargins(0, 0, 0, 0)
        self._panel_items.setSpacing(8)
        self._panel_items.addStretch()
        scroll.setWidget(inner)
        column.addWidget(scroll, stretch=1)

        # The shared default, not a number of its own.
        #
        # 360 was narrow enough that a widget's name and its description
        # competed for the same line, and it was the only panel in the
        # application not the same width as the others.
        self.panel = Panel(self.client, width=Panel.DEFAULT_WIDTH,
                           edge="right",
                           key=f"__widgets_{self.page_key}",
                           destroy_on_close=False,
                           dismiss_on_outside_click=True)
        self.panel.add_content(host)

    def apply_stacking(self) -> None:
        """
        Raise every floating widget in saved z order.

        Placement raises as it goes, so without this the stack is whatever
        order the widgets happened to be built in - which is stable enough that
        nobody notices until they deliberately put one sticker in front of
        another and it comes back behind.
        """
        floating = [w for w in self._widgets if getattr(w, "floating", False)]
        for widget in sorted(floating, key=lambda w: w.z_order):
            try:
                widget.raise_()
            except RuntimeError:
                continue
        for topmost in self._topmost:
            try:
                topmost.raise_()
            except RuntimeError:
                continue
        try:
            self._chrome.raise_()
        except RuntimeError:
            pass

    def _refresh_panel(self) -> None:
        if self.panel is None or self._panel_items is None:
            return
        while self._panel_items.count() > 1:
            item = self._panel_items.takeAt(0)
            child = item.widget()
            if child is not None:
                child.setParent(None)
                child.deleteLater()

        for key, (widget_class, _a, _k) in sorted(self.templates.items()):
            name = getattr(widget_class, "NAME", "") or key
            self._panel_items.insertWidget(
                self._panel_items.count() - 1,
                self._panel_entry(None, name=name, template_key=key))

        for widget in sorted(self.registry.values(), key=lambda w: w.display_name()):
            if widget.placed or widget.template_key:
                continue
            self._panel_items.insertWidget(
                self._panel_items.count() - 1, self._panel_entry(widget))

    def _panel_entry(self, widget, name: str = "", template_key: str = "") -> QFrame:
        row = QFrame()
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(row, "settings", "registry-card")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        label = QLabel(name or widget.display_name())
        label.setFont(make_font(SIZES.S3, bold=True))
        set_style(label, "common", "text-strong")
        layout.addWidget(label)
        layout.addStretch()

        add = QPushButton("Add" if template_key else "Place")
        add.setFixedHeight(40)
        add.setMinimumWidth(96)
        add.setFont(make_font(SIZES.S2, bold=True))
        add.setCursor(Qt.CursorShape.PointingHandCursor)
        set_style(add, "settings", "plugin-action-reload")
        if template_key:
            add.clicked.connect(lambda _=False, k=template_key: self._add_copy_from_panel(k))
        else:
            add.clicked.connect(lambda _=False, w=widget: self._place_from_panel(w))
        layout.addWidget(add)
        return row

    def _add_copy_from_panel(self, template_key: str) -> None:
        """
        Add another copy of a template.

        A template may need to know something first - which sticker, which
        feed - so a widget class can define `choose_before_add(client, then)`
        and the copy is deferred until it calls back. Anything without it is
        added immediately, as before.
        """
        entry = self.templates.get(template_key)
        widget_class = entry[0] if entry else None
        chooser = getattr(widget_class, "choose_before_add", None)

        if callable(chooser):
            def then(**kwargs):
                widget = self._make_copy(template_key, **kwargs)
                self._refresh_panel()
                if widget is None:
                    self.client.log("warning",
                                    f"[WidgetFramework] '{template_key}' was "
                                    f"chosen but could not be built.")
            if self.panel is not None:
                self.panel.close_panel()
            try:
                chooser(self.client, then)
            except Exception as e:
                self.client.log("warning",
                                f"[WidgetFramework] '{template_key}' chooser "
                                f"failed: {e}", include_traceback=True)
            return

        self._make_copy(template_key)
        self._refresh_panel()
        if self.panel is not None:
            self.panel.close_panel()

    def _place_from_panel(self, widget: Widget) -> None:
        self.place(widget)
        self._refresh_panel()
        if self.panel is not None:
            self.panel.close_panel()

    def toggle_panel(self) -> None:
        self._build_panel()
        self._refresh_panel()
        if self.panel is not None:
            self.panel.toggle()
