from __future__ import annotations

import calendar as calendar_module
from datetime import date, timedelta
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QLinearGradient, QPainterPath)

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

    # A dark surface carrying a hint of the event's colour, rather than the
    # colour itself at partial opacity.
    #
    # Text sits on this. Lifting the tint toward white at the top and dropping
    # the alpha to a third at the bottom leaves white words competing with
    # whatever photograph the wallpaper happens to be showing through - the
    # card reads as coloured glass rather than as something written on.
    #
    # The colour is not lost: it is what the surface is tinted with, and it is
    # the bar down the left edge at full strength.
    SURFACE = QColor("#101014")
    #How much of the event's colour is mixed into the surface.
    TINT_TOP    = 0.24
    TINT_BOTTOM = 0.12
    #A tint lighter than this is darkened before it is mixed in. Without it a
    #pale event colour - a yellow, a mint - washes the card out and the words
    #on it stop reading, while a deep one stays perfectly legible. Capping the
    #lightness rather than the mix keeps every colour at the same contrast
    #instead of tuning for the palest one and losing the rest.
    TINT_CAP = 0.42
    #Out of 255. High enough to read on, low enough that the wallpaper is
    #still there behind it.
    TOP_ALPHA    = 232
    BOTTOM_ALPHA = 214

    #The event's colour, at full strength, down one edge.
    EDGE = 5

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
        self._sticker = None
        self._sticker_name = ""
        if event is None:
            self.update()
            return
        from .sticker_layer import sticker_for_event, load_sticker
        name = sticker_for_event(self.client, event)
        if name:
            self._sticker_name = name
            self._sticker = load_sticker(self.client, name)
        self.update()

    def paint_sticker(self, painter) -> None:
        """
        Draw whatever is stuck to the event this widget is showing.

        Sized against the widget rather than against a day box: the scale a
        sticker carries is a share of a calendar cell, and a cell is nothing
        like the shape of a card that has an event's name across it.
        """
        pixmap = getattr(self, "_sticker", None)
        if pixmap is None:
            return
        from .sticker_layer import draw_beside
        draw_beside(painter, pixmap, self.rect())

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

    @classmethod
    def _deepened(cls, tint: QColor) -> QColor:
        """A tint dark enough to write white on, keeping its hue."""
        lightness = (0.2126 * tint.red() + 0.7152 * tint.green()
                     + 0.0722 * tint.blue()) / 255
        if lightness <= cls.TINT_CAP:
            return tint
        factor = cls.TINT_CAP / max(lightness, 0.001)
        return QColor(int(tint.red() * factor), int(tint.green() * factor),
                      int(tint.blue() * factor))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        tint = self._deepened(self._tint)

        def mixed(amount: float, alpha: int) -> QColor:
            base = self.SURFACE
            return QColor(
                int(base.red()   + (tint.red()   - base.red())   * amount),
                int(base.green() + (tint.green() - base.green()) * amount),
                int(base.blue()  + (tint.blue()  - base.blue())  * amount),
                alpha)

        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, mixed(self.TINT_TOP, self.TOP_ALPHA))
        gradient.setColorAt(1.0, mixed(self.TINT_BOTTOM, self.BOTTOM_ALPHA))
        painter.setBrush(QBrush(gradient))
        painter.setPen(QPen(QColor(tint.red(), tint.green(), tint.blue(), 120), 1))
        body = self.rect().adjusted(0, 0, -1, -1)
        painter.drawRoundedRect(body, self.RADIUS, self.RADIUS)

        # The event's own colour, at full strength, where nothing is written.
        painter.save()
        path = QPainterPath()
        path.addRoundedRect(QRectF(body), self.RADIUS, self.RADIUS)
        painter.setClipPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        # The edge keeps the colour as it was given, not as it was deepened:
        # nothing is written on it, so it has nothing to be legible against.
        painter.setBrush(QBrush(self._tint))
        painter.drawRect(0, 0, self.EDGE, self.height())
        painter.restore()
        # Before end(), and before the child labels are drawn by the base:
        # a sticker belongs on the card, not over the words.
        self.paint_sticker(painter)
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
    """
    Today and the two days after it, with what is on each.

    A flat list of the next few events answers "what is next" - which the
    other widget already answers, larger. What this is for is the shape of the
    next few days, and that needs the days themselves: an empty tomorrow is
    information, and a list that skips to the day after hides it.
    """

    KEY         = "calendar_list"
    NAME        = "Coming up"
    ICON        = "mdi.format-list-bulleted"
    DESCRIPTION = "Today and the next two days, with what is on each."

    RESIZABLE = True
    ROTATABLE = False
    FLOATABLE = True
    REMOVABLE = True

    MIN_W, MIN_H = 260, 170
    MAX_W, MAX_H = 620, 520
    DEFAULT_ANCHOR = "center-right"

    #How many days it covers. Today, tomorrow, and the day after.
    DAYS = 3
    ROW_H = 30
    HEAD_H = 26

    def __init__(self, client: "Client", key: str = None, **kwargs):
        super().__init__(client=client, key=key or self.KEY,
                         width=340, height=300, **kwargs)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(6)

        self.days = QVBoxLayout()
        self.days.setSpacing(4)
        layout.addLayout(self.days)
        layout.addStretch()

        self.start_tick(60_000)
        self.tick()

    def apply_tint_to_text(self) -> None:
        # Every label is rebuilt on each tick, so there is nothing standing to
        # recolour. Kept because the base calls it when the tint changes.
        pass

    ## -- how much fits

    def _capacity(self) -> int:
        """
        How many lines fit in the widget, headings included.

        One budget for the whole list rather than a share each. Dividing the
        height between three days gives an empty tomorrow the same room as a
        full today, and a busy day says "+4 more" beside two blank rows.
        """
        return max(self.DAYS, int((self.height() - 22) // self.ROW_H))

    ## -- content

    def tick(self) -> None:
        while self.days.count():
            item = self.days.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        api = calendar_api(self.client)
        today = date.today()
        days = [today + timedelta(days=offset) for offset in range(self.DAYS)]

        by_day = {}
        for day in days:
            found = []
            if api is not None:
                try:
                    found = list(api["on_day"](day))
                except Exception:
                    found = []
            by_day[day] = found

        # The colour of the next thing that actually happens, not of today.
        # A day with nothing on it has no colour to lend.
        leading = next((entry for day in days for entry in by_day[day]), None)
        self.set_tint(colour_of(leading) if leading is not None else "#4f9de0")
        self.set_event(leading)

        for widget in self._lay_out(days, by_day, api):
            self.days.addWidget(widget)

    def _lay_out(self, days: list, by_day: dict, api) -> list:
        """
        The list, filled from the top until the room runs out.

        Every day gets its heading and at least one line, so a day with
        nothing on it still says so - an empty tomorrow is information, and a
        list that skips it hides that. What is left over goes to the days with
        the most on them, in order, which is where it is worth having.
        """
        budget = self._capacity()
        # A heading and one line each, reserved before anything is handed out.
        budget -= self.DAYS * 2
        shown = {day: 0 for day in days}

        for day in days:
            if not by_day[day]:
                continue
            shown[day] = 1

        # The rest, a line at a time, to whichever day still has the most
        # waiting. Round robin rather than first-come, so a packed today does
        # not swallow every spare line before Wednesday is looked at.
        while budget > 0:
            hungriest = max(
                days, key=lambda d: len(by_day[d]) - shown[d])
            if len(by_day[hungriest]) - shown[hungriest] <= 0:
                break
            shown[hungriest] += 1
            budget -= 1

        blocks = []
        for day in days:
            blocks.append(self._day_block(day, by_day[day], shown[day], api))
        return blocks

    def _day_block(self, day, events: list, capacity: int, api) -> QWidget:
        host = QWidget()
        set_style(host, "common", "transparent")
        column = QVBoxLayout(host)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(1)

        heading = QLabel(self._day_name(day))
        heading.setFont(make_font(SIZES.S1, bold=True))
        heading.setFixedHeight(self.HEAD_H)
        heading.setStyleSheet(f"color: {self.accent()}; background: transparent;")
        add_text_shadow(heading, blur=6)
        column.addWidget(heading)

        if not events:
            empty = QLabel("Nothing on")
            empty.setFont(make_font(SIZES.S1))
            empty.setFixedHeight(self.ROW_H)
            empty.setStyleSheet(
                "color: rgba(255,255,255,110); background: transparent;")
            column.addWidget(empty)
            return host

        shown = events[:max(0, capacity)]
        for entry in shown:
            column.addWidget(self._row(entry, api))

        remaining = len(events) - len(shown)
        if remaining > 0:
            more = QLabel(f"+{remaining} more")
            more.setFont(make_font(SIZES.S1))
            more.setFixedHeight(self.ROW_H)
            more.setStyleSheet(
                "color: rgba(255,255,255,140); background: transparent;")
            column.addWidget(more)
        return host

    @staticmethod
    def _day_name(day) -> str:
        today = date.today()
        if day == today:
            return "Today"
        if day == today + timedelta(days=1):
            return "Tomorrow"
        return day.strftime("%A")

    def _row(self, event, api) -> QWidget:
        host = QWidget()
        set_style(host, "common", "transparent")
        host.setFixedHeight(self.ROW_H)

        line = QHBoxLayout(host)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)

        glyph = QLabel()
        try:
            glyph.setPixmap(icon(event.icon, color=colour_of(event)).pixmap(16, 16))
        except Exception:
            pass
        glyph.setFixedWidth(20)
        line.addWidget(glyph)

        title = QLabel(event.title)
        title.setFont(make_font(SIZES.S2))
        set_style(title, "common", "text-strong")
        add_text_shadow(title, blur=6)
        line.addWidget(title, stretch=1)

        when = QLabel(str(getattr(event, "time", "") or "All day"))
        when.setFont(make_font(SIZES.S1))
        when.setStyleSheet(
            "color: rgba(255,255,255,170); background: transparent;")
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
