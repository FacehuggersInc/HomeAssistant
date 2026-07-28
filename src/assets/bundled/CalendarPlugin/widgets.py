from __future__ import annotations

import calendar as calendar_module
from datetime import date
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QLinearGradient

from src.ui.widget import Widget
from src.ui.widgets.tile import Tile
from src.ui.icons import icon
from PyQt6.QtWidgets import QGraphicsDropShadowEffect

from src.styling import make_font, SIZES, set_style, add_text_shadow

if TYPE_CHECKING:
    from src.main import Client


SOURCE_COLOURS = {"local": "#4f9de0", "imported": "#a97fe0", "holiday": "#d8a24a"}


def calendar_api(client):
    """The published registry, or None when the plugin is not loaded."""
    try:
        return client.public.calendar
    except Exception:
        return None


def colour_of(event) -> str:
    return event.colour or SOURCE_COLOURS.get(event.source, "#4f9de0")


class _TintedWidget(Widget):
    """
    A widget whose background is a gradient taken from the event it is showing.

    Deliberately fainter than the page and tile gradients. A widget sits *on*
    the wallpaper rather than replacing it, and at the page's opacity a row of
    them turns the home screen into a set of coloured panels.
    """

    # Alpha at the top and bottom of the gradient, out of 255. The wallpaper
    # still shows through, but text no longer has to compete with whatever
    # photograph happens to be behind it.
    TOP_ALPHA    = 178
    BOTTOM_ALPHA = 96

    # The colour shifts as well as the opacity - lifted toward white at the
    # top and pushed toward black at the bottom. Fading one flat colour in and
    # out reads as a single tone rather than as a gradient.
    TOP_LIFT   = 0.30
    BOTTOM_DROP = 0.42

    RADIUS = 14

    def __init__(self, *args, **kwargs):
        self._tint = QColor("#4f9de0")
        self._event_key = ""
        self._press = None
        super().__init__(*args, **kwargs)

        # Ticking is a fallback for time passing; this is for the calendar
        # actually changing. A 60s timer means an event added on the panel can
        # sit invisible for most of a minute on the widget beside it.
        self.client.subscribe_to_event("on_calendar_changed", self._calendar_changed)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(26)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 150))
        self.setGraphicsEffect(shadow)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _calendar_changed(self, event=None) -> None:
        def apply():
            try:
                self.tick()
            except RuntimeError:
                # Removed between the fire and this running.
                self.teardown()
        self.client.call_on_ui(apply)

    def teardown(self) -> None:
        try:
            self.client.unsubscribe_from_event("on_calendar_changed",
                                               self._calendar_changed)
        except Exception:
            pass

    ## -- opening what it is showing

    def set_event(self, event) -> None:
        self._event_key = getattr(event, "key", "") if event is not None else ""

    DRAG_SLOP = 12

    def mousePressEvent(self, event) -> None:
        self._press = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        start, self._press = getattr(self, "_press", None), None
        super().mouseReleaseEvent(event)

        if not self._event_key or start is None:
            return

        # Measured, not assumed. The release still arrives here after a drag,
        # so trusting super() to have "claimed" it meant every reposition
        # ended by opening the event that had just been moved.
        moved = (event.globalPosition().toPoint() - start).manhattanLength()
        if moved >= self.DRAG_SLOP:
            return

        # And not while the framework has it lifted for editing - a tap to
        # deselect is not a tap to open.
        if getattr(self, "lifted", False) or getattr(self, "floating_drag", False):
            return

        self.open_event()

    def open_event(self) -> None:
        """Show the calendar page, then the event itself on top of it."""
        try:
            home = self.client.PAGES.get_entry("#cwb_home_page")
            page = getattr(home, "instance", None)
            calendar_page = (page.sub_page_dict.get("calendar")
                             if page is not None else None)
            if calendar_page is not None:
                page.jump_to_coord(tuple(calendar_page.coord))
                calendar_page.open_event(self._event_key)
        except Exception as e:
            self.client.log("warning", f"[Calendar] Could not open event: {e}")

    def set_tint(self, colour) -> None:
        colour = QColor(colour)
        if colour.isValid() and colour != self._tint:
            self._tint = colour
            self.apply_tint_to_text()
            self.update()

    def accent(self) -> str:
        """
        A light version of the tint, for secondary text.

        Pulled most of the way to white rather than used raw - the tint is a
        background colour, and text in it on a background of it is unreadable
        whichever way round you put them.
        """
        tint = self._tint
        return QColor(
            int(tint.red()   + (255 - tint.red())   * 0.62),
            int(tint.green() + (255 - tint.green()) * 0.62),
            int(tint.blue()  + (255 - tint.blue())  * 0.62),
        ).name()

    def apply_tint_to_text(self) -> None:
        """Override to recolour the labels a subclass owns."""
        pass

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        tint = self._tint
        top = QColor(
            int(tint.red()   + (255 - tint.red())   * self.TOP_LIFT),
            int(tint.green() + (255 - tint.green()) * self.TOP_LIFT),
            int(tint.blue()  + (255 - tint.blue())  * self.TOP_LIFT),
            self.TOP_ALPHA)
        bottom = QColor(
            int(tint.red()   * (1 - self.BOTTOM_DROP)),
            int(tint.green() * (1 - self.BOTTOM_DROP)),
            int(tint.blue()  * (1 - self.BOTTOM_DROP)),
            self.BOTTOM_ALPHA)

        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(1.0, bottom)
        painter.setBrush(QBrush(gradient))
        painter.setPen(QPen(QColor(self._tint.red(), self._tint.green(),
                                   self._tint.blue(), 105), 1))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1),
                                self.RADIUS, self.RADIUS)
        painter.end()
        super().paintEvent(event)


## -- WIDGETS -------------------------------------------------------------------

class UpcomingEventWidget(_TintedWidget):
    """
    The next thing happening, and how long until it.

    One event, large. A wall panel is read from across a room, and the thing
    people actually want off a calendar at a glance is what is next - not a
    list they have to scan.
    """

    KEY         = "calendar_upcoming"
    NAME        = "Next event"
    ICON        = "mdi.calendar-clock"
    DESCRIPTION = "The next event, with how long until it starts."

    RESIZABLE = True
    ROTATABLE = False
    FLOATABLE = True
    REMOVABLE = True

    MIN_W, MIN_H = 220, 120
    MAX_W, MAX_H = 620, 300
    DEFAULT_ANCHOR = "top-left"

    def __init__(self, client: "Client", key: str = None, **kwargs):
        super().__init__(client=client, key=key or self.KEY,
                         width=320, height=150, **kwargs)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        top = QHBoxLayout()
        top.setSpacing(10)

        self.glyph = QLabel()
        self.glyph.setFixedWidth(38)
        top.addWidget(self.glyph)

        self.title = QLabel("Nothing coming up")
        self.title.setFont(make_font(SIZES.M1, bold=True))
        self.title.setWordWrap(True)
        set_style(self.title, "common", "text-strong")
        add_text_shadow(self.title, blur=10)
        top.addWidget(self.title, stretch=1)
        layout.addLayout(top)

        self.when = QLabel("")
        self.when.setFont(make_font(SIZES.S3, bold=True))
        add_text_shadow(self.when, blur=8)
        layout.addWidget(self.when)

        self.where = QLabel("")
        self.where.setFont(make_font(SIZES.S2))
        self.where.setWordWrap(True)
        add_text_shadow(self.where, blur=6)
        layout.addWidget(self.where)

        self.apply_tint_to_text()

        self.start_tick(30_000)   # a countdown in minutes needs no more
        self.tick()

    def apply_tint_to_text(self) -> None:
        for label in (getattr(self, "when", None), getattr(self, "where", None)):
            if label is not None:
                label.setStyleSheet(f"color: {self.accent()}; background: transparent;")

    def tick(self) -> None:
        api = calendar_api(self.client)
        if api is None:
            self.title.setText("Calendar not loaded")
            self.when.setText("")
            self.where.setText("")
            return

        try:
            event = api["next_event"]()
        except Exception:
            event = None

        if event is None:
            self.title.setText("Nothing coming up")
            self.when.setText("")
            self.where.setText("")
            self.glyph.clear()
            self.set_event(None)
            return

        self.set_tint(colour_of(event))
        self.set_event(event)
        self.title.setText(event.title)
        self.when.setText(api["describe_gap"](event).capitalize())
        self.where.setText(event.location or "")
        self.where.setVisible(bool(event.location))
        try:
            self.glyph.setPixmap(
                icon(event.icon, color=colour_of(event)).pixmap(34, 34))
        except Exception:
            self.glyph.clear()


class NextEventsWidget(_TintedWidget):
    """A short list of what is coming, for people who want the shape of a week."""

    KEY         = "calendar_list"
    NAME        = "Coming up"
    ICON        = "mdi.format-list-bulleted"
    DESCRIPTION = "The next few events, in order."

    RESIZABLE = True
    ROTATABLE = False
    FLOATABLE = True
    REMOVABLE = True

    MIN_W, MIN_H = 260, 160
    MAX_W, MAX_H = 620, 460
    DEFAULT_ANCHOR = "right"

    ROW_H = 34

    def __init__(self, client: "Client", key: str = None, **kwargs):
        super().__init__(client=client, key=key or self.KEY,
                         width=340, height=260, **kwargs)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(4)

        self.heading = QLabel("Coming up")
        self.heading.setFont(make_font(SIZES.S1, bold=True))
        add_text_shadow(self.heading, blur=6)
        layout.addWidget(self.heading)

        self.rows = QVBoxLayout()
        self.rows.setSpacing(2)
        layout.addLayout(self.rows)
        layout.addStretch()

        self.start_tick(60_000)
        self.apply_tint_to_text()
        self.tick()

    def apply_tint_to_text(self) -> None:
        heading = getattr(self, "heading", None)
        if heading is not None:
            heading.setStyleSheet(f"color: {self.accent()}; background: transparent;")

    def _capacity(self) -> int:
        """However many fit. A fixed count either overflows or wastes the space."""
        return max(1, (self.height() - 46) // self.ROW_H)

    def tick(self) -> None:
        while self.rows.count():
            item = self.rows.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        api = calendar_api(self.client)
        events = []
        if api is not None:
            try:
                events = api["upcoming"](self._capacity())
            except Exception:
                events = []

        if not events:
            empty = QLabel("Nothing coming up")
            empty.setFont(make_font(SIZES.S2))
            empty.setStyleSheet(f"color: {self.accent()}; background: transparent;")
            add_text_shadow(empty, blur=6)
            self.rows.addWidget(empty)
            self.set_tint("#4f9de0")
            self.set_event(None)
            return

        # The first event, not an average. This widget is a queue and its
        # colour should be whatever is at the front of it.
        self.set_tint(colour_of(events[0]))
        self.set_event(events[0])
        for event in events:
            self.rows.addWidget(self._row(event, api))

    def _row(self, event, api) -> QWidget:
        host = QWidget()
        set_style(host, "common", "transparent")
        host.setFixedHeight(self.ROW_H)

        line = QHBoxLayout(host)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)

        glyph = QLabel()
        try:
            glyph.setPixmap(icon(event.icon, color=colour_of(event)).pixmap(18, 18))
        except Exception:
            pass
        glyph.setFixedWidth(22)
        line.addWidget(glyph)

        title = QLabel(event.title)
        title.setFont(make_font(SIZES.S2))
        set_style(title, "common", "text-strong")
        add_text_shadow(title, blur=6)
        line.addWidget(title, stretch=1)

        when = QLabel(api["describe_gap"](event) if api else "")
        when.setFont(make_font(SIZES.S1, bold=True))
        when.setStyleSheet(f"color: {self.accent()}; background: transparent;")
        add_text_shadow(when, blur=6)
        line.addWidget(when)
        return host


## -- TILE ----------------------------------------------------------------------

class MiniCalendarTile(Tile):
    """
    A month at a glance, with days that have something on them marked.

    Five by three is the floor: below that the seven columns are narrower than
    a two-digit date and the grid stops being a calendar.
    """

    KEY  = "calendar_mini"
    NAME = "Calendar"
    ICON = "mdi.calendar-month"

    MIN_GRID_W, MIN_GRID_H = 5, 3
    MAX_GRID_W, MAX_GRID_H = 8, 6
    PANEL_SIZES = [(5, 3), (6, 4)]

    # Opaque. A tile is a card on a grid of cards, and one that lets the
    # wallpaper through breaks the row it sits in.
    BASE_TOP    = QColor("#1b2436")
    BASE_BOTTOM = QColor("#2b1f33")
    TINT        = 0.4

    def __init__(self, client: "Client", grid_w: int = 5, grid_h: int = 3):
        self._marked: set = set()
        self._tints: list = []
        super().__init__(client, grid_w=grid_w, grid_h=grid_h, bg_color="#1b2436")
        self.client.subscribe_to_event("on_calendar_changed", self._calendar_changed)

    def _calendar_changed(self, event=None) -> None:
        def apply():
            try:
                self.tick()
            except RuntimeError:
                self.teardown()
        self.client.call_on_ui(apply)

    def teardown(self) -> None:
        try:
            self.client.unsubscribe_from_event("on_calendar_changed",
                                               self._calendar_changed)
        except Exception:
            pass

    def tick(self) -> None:
        api = calendar_api(self.client)
        today = date.today()
        marked = set()
        if api is not None:
            try:
                marked = {
                    day for day, events
                    in api["in_month"](today.year, today.month).items() if events
                }
            except Exception:
                marked = set()
        tints = []
        if api is not None:
            try:
                for events in api["in_month"](today.year, today.month).values():
                    for entry in events:
                        tints.append(QColor(colour_of(entry)))
            except Exception:
                tints = []

        if marked != self._marked or tints != self._tints:
            self._marked = marked
            self._tints = tints[:40]
            self.update()

    def paintEvent(self, event) -> None:
        today = date.today()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        top, bottom = self.BASE_TOP, self.BASE_BOTTOM
        if self._tints:
            average = QColor(
                sum(c.red() for c in self._tints) // len(self._tints),
                sum(c.green() for c in self._tints) // len(self._tints),
                sum(c.blue() for c in self._tints) // len(self._tints),
            )
            def blend(base, amount):
                return QColor(
                    int(base.red()   + (average.red()   - base.red())   * amount),
                    int(base.green() + (average.green() - base.green()) * amount),
                    int(base.blue()  + (average.blue()  - base.blue())  * amount),
                )
            top, bottom = blend(top, self.TINT * 0.6), blend(bottom, self.TINT)

        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(1.0, bottom)
        painter.setBrush(QBrush(gradient))
        painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1),
                                self.radius, self.radius)

        if self.selected and not self.dragging:
            painter.setPen(QPen(QColor("#6fa8e0"), 2, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2),
                                    self.radius, self.radius)
            self._paint_handles(painter)

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        margin = 10
        rect = self.rect().adjusted(margin, margin, -margin, -margin)

        painter.setPen(QPen(QColor(255, 255, 255, 220)))
        painter.setFont(make_font(SIZES.S1, bold=True))
        header = f"{calendar_module.month_name[today.month]} {today.year}"
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignHCenter
                                   | Qt.AlignmentFlag.AlignTop), header)

        weeks = calendar_module.Calendar(firstweekday=6).monthdatescalendar(
            today.year, today.month)
        top = rect.top() + 22
        cell_w = rect.width() / 7
        cell_h = max(10.0, (rect.bottom() - top) / max(1, len(weeks)))

        painter.setFont(make_font(11))
        for row, week in enumerate(weeks):
            for column, day in enumerate(week):
                x = rect.left() + column * cell_w
                y = top + row * cell_h
                centre_x = int(x + cell_w / 2)
                centre_y = int(y + cell_h / 2)

                if day == today:
                    painter.setBrush(QBrush(QColor("#2ff08e")))
                    painter.setPen(Qt.PenStyle.NoPen)
                    size = int(min(cell_w, cell_h)) - 3
                    painter.drawEllipse(centre_x - size // 2,
                                        centre_y - size // 2, size, size)

                in_month = day.month == today.month
                if day == today:
                    painter.setPen(QPen(QColor("#11331f")))
                else:
                    painter.setPen(QPen(QColor(255, 255, 255,
                                               225 if in_month else 70)))
                painter.drawText(int(x), int(y), int(cell_w), int(cell_h),
                                 int(Qt.AlignmentFlag.AlignCenter), str(day.day))

                # A dot under a day that has something on it. Deliberately not
                # the event colour - at this size several dots of different
                # colours read as noise rather than as information.
                if in_month and day in self._marked and day != today:
                    painter.setBrush(QBrush(QColor("#7ed6a6")))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawEllipse(centre_x - 2,
                                        int(y + cell_h) - 5, 4, 4)
        painter.end()
