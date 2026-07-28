from __future__ import annotations

import json
import math
import pathlib
from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSizePolicy, QLabel, QPushButton,
    QScrollArea, QFrame,
)
from PyQt6.QtCore import Qt, QTimer, QPoint, QPointF, QRect, QEvent
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QTransform

from src.styling import set_style, make_font, SIZES

if TYPE_CHECKING:
    from src.main import Client


ANCHORS = (
    "top-left", "top-center", "top-right",
    "bottom-left", "bottom-center", "bottom-right",
)

TOPMOST  = "topmost"
FLOATING = "floating"

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

    ## CAPABILITIES

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

    def set_content_size(self, width: int, height: int) -> None:
        self._content_w, self._content_h = int(width), int(height)

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
        }

    def apply_layout_state(self, state: dict) -> None:
        if not isinstance(state, dict):
            return
        self.placed   = bool(state.get("placed", True))
        self.anchor   = str(state.get("anchor", self.anchor))
        self.floating = bool(state.get("floating", self.floating))
        self.float_x  = int(state.get("x", self.float_x))
        self.float_y  = int(state.get("y", self.float_y))
        self.angle    = float(state.get("angle", 0.0))
        self.offset_x = int(state.get("ox", 0))
        self.offset_y = int(state.get("oy", 0))

        if self.can_resize():
            width  = int(state.get("w", self.width()))
            height = int(state.get("h", self.height()))
            self.set_content_size(max(self.MIN_W, min(self.MAX_W, width)),
                                  max(self.MIN_H, min(self.MAX_H, height)))
            bounds_w, bounds_h = self.rotated_bounds()
            self.setFixedSize(bounds_w, bounds_h)

    ## TICK

    def start_tick(self, interval_ms: int = 1000) -> None:
        self._tick_timer.start(interval_ms)

    def stop_tick(self) -> None:
        self._tick_timer.stop()

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

        self._col = QVBoxLayout()
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(8)
        self._col.setSizeConstraint(self._col.SizeConstraint.SetFixedSize)
        self._col.setAlignment(Qt.AlignmentFlag.AlignTop if "top" in anchor_name
                               else Qt.AlignmentFlag.AlignBottom)

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

            if "right" in self.anchor_name:
                row_layout.setAlignment(Qt.AlignmentFlag.AlignRight)
            elif "center" in self.anchor_name:
                row_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            else:
                row_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

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

        self.active: Optional[Widget] = None
        self._mode = ""
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

    def _make_copy(self, template_key: str, instance_key: str = None,
                   state: dict = None):
        entry = self.templates.get(template_key)
        if entry is None:
            return None
        widget_class, args, kwargs = entry

        if instance_key is None:
            index = 1
            while f"{template_key}-{index}" in self.registry:
                index += 1
            instance_key = f"{template_key}-{index}"

        try:
            widget = widget_class(self.client, *args, **kwargs)
        except Exception as e:
            self.client.log("error",
                            f"[WidgetFramework] Could not copy '{template_key}': {e}")
            return None

        widget.KEY = instance_key
        widget.template_key = template_key
        self.registry[instance_key] = widget
        if state:
            widget.apply_layout_state(state)
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

    ## PLACEMENT

    def place(self, widget: Widget, save: bool = True) -> None:
        if widget in self._widgets:
            return

        widget.placed = True
        self._widgets.append(widget)
        widget.setParent(self)
        self._fit_to_content(widget)

        if widget.floating or widget.anchor == FLOATING:
            widget.tags = ["floating"]
            widget.move(widget.float_x, widget.float_y)
        elif widget.anchor == TOPMOST:
            widget.tags = ["topmost"]
            self._topmost.append(widget)
        else:
            widget.tags = ["anchored"]
            self._place_in_zone(widget)
            if widget.has_offset():
                self._detach_for_offset(widget)
                self._apply_offset(widget)

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

    def save_layout(self) -> None:
        data = self._read_layout_file()

        # Merge, do not replace. If this page is ever rebuilt - a plugin
        # reload, a second construction - a fresh framework with an empty or
        # partial registry would otherwise write its blank state over
        # everything that was there.
        page = dict(data.get(self.page_key, {}))
        for widget in self.registry.values():
            state = widget.layout_state()
            if widget.template_key:
                state["template"] = widget.template_key
            page[widget.KEY] = state
        data[self.page_key] = page

        try:
            path = self._layout_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=4))
            self.client.log("debug",
                            f"[WidgetFramework:{self.page_key}] saved {len(page)} "
                            f"widgets to {path}")
        except Exception as e:
            self.client.log("error", f"[WidgetFramework] Could not save layout to "
                                     f"{self._layout_path()}: {e}")

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
                break
            node = node.parent()

    def _clamp(self, widget: Widget, point: QPoint) -> QPoint:
        """
        Keep a widget inside the page margin, not merely on screen.

        The same padding the anchor zones use, so a floating widget cannot sit
        flush against an edge while every
        anchored one keeps its margin.
        """
        view_w, view_h = self.visible_size()
        pad = self.padding

        max_x = max(pad, view_w - pad - widget.width())
        max_y = max(pad, view_h - pad - widget.height())
        # A widget wider than the page between margins would be pushed off the
        # left by the clamp, so the lower bound gives way first.
        min_x = min(pad, max_x)
        min_y = min(pad, max_y)

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

        if   anchor_name == "top-left":      zone.move(pad, pad)
        elif anchor_name == "top-center":    zone.move((width - zw) // 2, pad)
        elif anchor_name == "top-right":     zone.move(width - zw - pad, pad)
        elif anchor_name == "bottom-left":   zone.move(pad, height - zh - pad)
        elif anchor_name == "bottom-center": zone.move((width - zw) // 2, height - zh - pad)
        elif anchor_name == "bottom-right":  zone.move(width - zw - pad, height - zh - pad)

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
        width, height = self.visible_size()
        horizontal = ("left" if point.x() < width / 3
                      else "right" if point.x() > width * 2 / 3 else "center")
        vertical = "top" if point.y() < height / 2 else "bottom"
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
        """
        widget = self.active
        if widget is None or not widget.REMOVABLE:
            return

        name = widget.display_name()
        self.unplace(widget)
        self.active = None
        self._mode = ""
        self._chrome.hide()
        self.save_layout()
        try:
            self.client.simple_notify("widgets", "Widgets",
                                      f"'{name}' moved to the widgets panel.")
        except Exception:
            pass

    def _handle_at(self, point: QPoint) -> str:
        for name, rect in self._handle_rects().items():
            if rect.adjusted(-HANDLE_HIT_PAD, -HANDLE_HIT_PAD,
                             HANDLE_HIT_PAD, HANDLE_HIT_PAD).contains(point):
                return name
        return ""

    def _begin_handle(self, handle: str, point: QPoint) -> None:
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
            elif name == "rotate":
                painter.drawArc(QRect(centre.x() - arm, centre.y() - arm,
                                      arm * 2, arm * 2), 0, 270 * 16)
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
        if changed:
            self.update_geometry()

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

        close = QPushButton("Close")
        close.setFixedHeight(40)
        close.setMinimumWidth(96)
        close.setFont(make_font(SIZES.S2, bold=True))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        set_style(close, "settings", "plugin-action-copy")
        close.clicked.connect(lambda: self.panel.close_panel() if self.panel else None)
        header.addWidget(close)
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
        set_style(scroll, "common", "transparent")

        inner = QWidget()
        set_style(inner, "common", "transparent")
        self._panel_items = QVBoxLayout(inner)
        self._panel_items.setContentsMargins(0, 0, 0, 0)
        self._panel_items.setSpacing(8)
        self._panel_items.addStretch()
        scroll.setWidget(inner)
        column.addWidget(scroll, stretch=1)

        self.panel = Panel(self.client, width=360, edge="right",
                           key=f"__widgets_{self.page_key}",
                           destroy_on_close=False)
        self.panel.add_content(host)

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
