from __future__ import annotations

import calendar as calendar_module
import time as clock
from datetime import date, timedelta
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel
from PyQt6.QtCore import Qt, QRectF, QTimer
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QLinearGradient, QPainterPath)

from src.ui.widget import Widget, HOLD_MS
from src.ui.widgets.tile import Tile
from src.ui.icons import icon
from PyQt6.QtWidgets import QGraphicsDropShadowEffect

from src.styling import make_font, SIZES, set_style, add_text_shadow

if TYPE_CHECKING:
    from src.main import Client


SOURCE_COLOURS = {"local": "#4f9de0", "imported": "#a97fe0", "holiday": "#d8a24a"}


def clock_12h(moment) -> str:
    """
    A datetime as `2:05 PM`, or `3 PM` on the hour.

    Taken from `starts_at`/`ends_at` rather than from the stored `time`
    string, which is kept as a 24-hour clock and is what a wall panel should
    never show: the panel is read at a glance from across a room, and "14:05"
    is a number to convert before it is a time.

    The minutes go when there are none. "3 PM" is what somebody would say.
    """
    if moment is None:
        return ""
    pattern = "%I:%M %p" if moment.minute else "%I %p"
    return moment.strftime(pattern).lstrip("0")


def when_text(event, span: bool = False) -> str:
    """
    When an event is, in words: `All day`, `2:05 PM`, or `2:05 PM - 3:30 PM`.

    `span` asks for the frame where there is one. Off by default because a
    row in a list has one line to say it in, and a start time is the part
    somebody is actually looking for.
    """
    if event is None:
        return ""
    if getattr(event, "all_day", False):
        return "All day"

    start = clock_12h(getattr(event, "starts_at", None))
    if not span:
        return start

    end = getattr(event, "ends_at", None)
    # ends_at answers with the start when nothing else was stated, rather
    # than inventing a length - so a frame is only shown when there is one.
    if not getattr(event, "end_time", "") or end is None \
            or end == getattr(event, "starts_at", None):
        return start
    return f"{start} - {clock_12h(end)}"


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

    DRAG_DISTANCE = 12

    def mousePressEvent(self, event) -> None:
        self._press = event.globalPosition().toPoint()
        self._press_at = clock.monotonic()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        start, self._press = getattr(self, "_press", None), None
        began = getattr(self, "_press_at", 0.0)
        super().mouseReleaseEvent(event)

        if not self._event_key or start is None:
            return

        # Measured, not assumed. The release still arrives here after a drag,
        # so trusting super() to have "claimed" it meant every reposition
        # ended by opening the event that had just been moved.
        moved = (event.globalPosition().toPoint() - start).manhattanLength()
        if moved >= self.DRAG_DISTANCE:
            return

        # And not a hold. Distance alone cannot tell a tap from somebody
        # pressing and waiting for the handles, because a finger held still
        # travels nothing - so every attempt to pick the widget up ended on
        # the calendar page instead. The framework's own threshold, so the
        # moment this stops counting as a tap is the moment the handles
        # appear rather than some other number nearby.
        if began and (clock.monotonic() - began) * 1000 >= HOLD_MS:
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
    # One event, large - what is next, read from across a room.
    # Events starting close together are cycled through, with a line counting them.

    KEY = "calendar_upcoming"
    NAME = "Next event"
    ICON = "mdi.calendar-clock"
    DESCRIPTION = "The next event, with how long until it starts. Events close together take turns."

    RESIZABLE = True
    ROTATABLE = False
    FLOATABLE = True
    REMOVABLE = True

    MIN_W, MIN_H = 220, 120
    MAX_W, MAX_H = 620, 300
    DEFAULT_ANCHOR = "top-left"

    TICK_MS = 1000
    REFRESH_SECONDS = 30
    LOOK_AHEAD = 20

    def __init__(self, client: "Client", key: str = None, **kwargs):
        self._group = []
        self._index = 0
        self._refreshed_at = 0.0
        self._shown_at = 0.0
        super().__init__(client=client, key=key or self.KEY,
                         width=320, height=170, **kwargs)

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
        self.when.setWordWrap(True)
        add_text_shadow(self.when, blur=8)
        layout.addWidget(self.when)

        self.where = QLabel("")
        self.where.setFont(make_font(SIZES.S2))
        self.where.setWordWrap(True)
        add_text_shadow(self.where, blur=6)
        layout.addWidget(self.where)

        self.count = QLabel("")
        self.count.setFont(make_font(SIZES.S1))
        self.count.setStyleSheet("color: rgba(255,255,255,150); background: transparent;")
        add_text_shadow(self.count, blur=6)
        self.count.setVisible(False)
        layout.addWidget(self.count)

        self.apply_tint_to_text()

        # Ticks every second so the cycle can turn; the calendar itself is only re-read every REFRESH_SECONDS
        self.start_tick(self.TICK_MS)
        self.tick()

    def apply_tint_to_text(self) -> None:
        for label in (getattr(self, "when", None), getattr(self, "where", None)):
            if label is not None:
                label.setStyleSheet(f"color: {self.accent()}; background: transparent;")

    def _calendar_changed(self, event=None) -> None:
        self._refreshed_at = 0.0
        super()._calendar_changed(event)

    ## -- settings

    def _option(self, path: str, default):
        api = calendar_api(self.client)
        if api is None:
            return default
        try:
            return api["option"](path, default)
        except Exception:
            return default

    def window_hours(self) -> float:
        try:
            return max(0.0, float(self._option("widgets.next_event_window_hours", 3)))
        except (TypeError, ValueError):
            return 3.0

    def cycle_seconds(self) -> float:
        try:
            return max(2.0, float(self._option("widgets.next_event_cycle_seconds", 8)))
        except (TypeError, ValueError):
            return 8.0

    ## -- which events take turns

    @staticmethod
    def group_of(events: list, window_hours: float) -> list:
        if not events:
            return []
        first = events[0]
        if window_hours <= 0:
            return [first]
        if getattr(first, "all_day", False):
            return [e for e in events
                    if getattr(e, "all_day", False) and e.date == first.date]
        start = getattr(first, "starts_at", None)
        if start is None:
            return [first]
        reach = timedelta(hours=window_hours)
        return [e for e in events
                if not getattr(e, "all_day", False)
                and getattr(e, "starts_at", None) is not None
                and timedelta(0) <= e.starts_at - start <= reach]

    @staticmethod
    def count_text(index: int, total: int, window_hours: float, all_day: bool) -> str:
        if total <= 1:
            return ""
        if all_day:
            return f"{index + 1} of {total} all day"
        hours = f"{window_hours:g}"
        unit = "hour" if hours == "1" else "hours"
        return f"{index + 1} of {total} within {hours} {unit}"

    ## -- content

    def tick(self) -> None:
        api = calendar_api(self.client)
        if api is None:
            self._group = []
            self._show(None, api)
            self.title.setText("Calendar not loaded")
            return

        now = clock.monotonic()
        if now - self._refreshed_at >= self.REFRESH_SECONDS:
            before = getattr(self._current(), "key", None)
            self._refresh(api)
            self._refreshed_at = now
            # A refresh that lands on the same event keeps its turn, or a long cycle would never advance
            if getattr(self._current(), "key", None) != before:
                self._shown_at = now
            self._show(self._current(), api)
            return

        if len(self._group) > 1 and now - self._shown_at >= self.cycle_seconds():
            self._index = (self._index + 1) % len(self._group)
            self._shown_at = now
            self._show(self._current(), api)

    def _refresh(self, api) -> None:
        try:
            events = list(api["upcoming"](self.LOOK_AHEAD))
        except Exception:
            try:
                single = api["next_event"]()
                events = [single] if single is not None else []
            except Exception:
                events = []

        showing = self._current()
        showing_key = getattr(showing, "key", None)
        self._group = self.group_of(events, self.window_hours())
        # Stay on the same event across a refresh rather than jumping back to the first
        keys = [getattr(e, "key", None) for e in self._group]
        self._index = keys.index(showing_key) if showing_key in keys else 0

    def _current(self):
        if not self._group:
            return None
        return self._group[self._index % len(self._group)]

    def _show(self, event, api) -> None:
        if event is None:
            self.title.setText("Nothing coming up")
            self.when.setText("")
            self.where.setText("")
            self.where.setVisible(False)
            self.count.setVisible(False)
            self.glyph.clear()
            self.set_event(None)
            return

        self.set_tint(colour_of(event))
        self.set_event(event)
        self.title.setText(event.title)
        # The day and the time, not the day alone: "Tomorrow" still leaves nine or four to guess
        try:
            gap = api["describe_gap"](event, short=True)
        except TypeError:
            gap = api["describe_gap"](event)
        gap = gap.capitalize()
        clock_part = when_text(event, span=True)
        self.when.setText(f"{gap}  \u00b7  {clock_part}" if clock_part else gap)
        self.where.setText(event.location or "")
        self.where.setVisible(bool(event.location))

        text = self.count_text(self._index % max(1, len(self._group)), len(self._group),
                               self.window_hours(), bool(getattr(event, "all_day", False)))
        self.count.setText(text)
        self.count.setVisible(bool(text))
        try:
            self.glyph.setPixmap(
                icon(event.icon, color=colour_of(event)).pixmap(34, 34))
        except Exception:
            self.glyph.clear()


class NextEventsWidget(_TintedWidget):
    # The days ahead and what is on each - an empty tomorrow is information, so every day is listed.
    # The first MIN_DAYS are always there and are filled first; height past that adds more days.

    KEY = "calendar_list"
    NAME = "Coming up"
    ICON = "mdi.format-list-bulleted"
    DESCRIPTION = "The days ahead, with what is on each. Make it taller to see more days."

    RESIZABLE = True
    ROTATABLE = False
    FLOATABLE = True
    REMOVABLE = True

    # Tall enough for MIN_DAYS at a heading and one line each
    MIN_W, MIN_H = 260, 210
    MAX_W, MAX_H = 620, 760
    DEFAULT_ANCHOR = "center-right"

    MIN_DAYS = 3
    MAX_DAYS = 14
    # Lines one day may take, its "+N more" included, so one busy day cannot push the rest off
    DAY_CAP = 5
    ROW_H = 30
    HEAD_H = 26
    ROW_GAP = 1
    DAY_GAP = 4
    MARGIN_TOP = 10
    MARGIN_BOTTOM = 12
    RELAYOUT_MS = 150

    def __init__(self, client: "Client", key: str = None, **kwargs):
        super().__init__(client=client, key=key or self.KEY,
                         width=340, height=340, **kwargs)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, self.MARGIN_TOP, 14, self.MARGIN_BOTTOM)
        layout.setSpacing(0)

        self.days = QVBoxLayout()
        self.days.setSpacing(self.DAY_GAP)
        layout.addLayout(self.days)
        layout.addStretch()

        # Rebuilt once a resize settles, not on every step of a drag
        self._relayout = QTimer(self)
        self._relayout.setSingleShot(True)
        self._relayout.timeout.connect(self._safe_tick)
        self._last_height = self.height()

        self.start_tick(60_000)
        self.tick()

    def apply_tint_to_text(self) -> None:
        # Every label is rebuilt on each tick, so there is nothing standing to recolour
        pass

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        relayout = getattr(self, "_relayout", None)
        if relayout is not None and self.height() != self._last_height:
            self._last_height = self.height()
            relayout.start(self.RELAYOUT_MS)

    ## -- how much fits

    def _usable(self) -> int:
        return max(0, self.height() - self.MARGIN_TOP - self.MARGIN_BOTTOM)

    def _line_h(self) -> int:
        return self.ROW_H + self.ROW_GAP

    def _day_h(self, lines: int) -> int:
        return self.HEAD_H + self.ROW_GAP + lines * self._line_h()

    def plan(self, events_by_day: list) -> list:
        # [(day, lines)] in order: MIN_DAYS at one line each, filled busiest first, then whole days while they fit
        line_h, gap = self._line_h(), self.DAY_GAP
        remaining = self._usable() + gap

        def need(events) -> int:
            return max(1, min(len(events), self.DAY_CAP))

        lines = []
        for day, events in events_by_day[:self.MIN_DAYS]:
            cost = self._day_h(1) + gap
            if remaining < cost and lines:
                break
            lines.append([day, 1, need(events)])
            remaining -= cost

        while remaining >= line_h:
            hungriest = max(lines, key=lambda item: item[2] - item[1])
            if hungriest[2] - hungriest[1] <= 0:
                break
            hungriest[1] += 1
            remaining -= line_h

        for day, events in events_by_day[len(lines):]:
            cost = self._day_h(1) + gap
            if remaining < cost:
                break
            extra = min(need(events) - 1, (remaining - cost) // line_h)
            lines.append([day, 1 + extra, need(events)])
            remaining -= cost + extra * line_h

        return [(day, count) for day, count, _ in lines]

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
        candidates = []
        for offset in range(self.MAX_DAYS):
            day = today + timedelta(days=offset)
            found = []
            if api is not None:
                try:
                    found = list(api["on_day"](day))
                except Exception:
                    found = []
            candidates.append((day, found))

        planned = self.plan(candidates)
        by_day = dict(candidates)

        # The colour of the next thing that actually happens - a day with nothing on it has none to lend
        leading = next((entry for day, _ in planned for entry in by_day[day]), None)
        self.set_tint(colour_of(leading) if leading is not None else "#4f9de0")
        self.set_event(leading)

        for day, lines in planned:
            self.days.addWidget(self._day_block(day, by_day[day], lines, api))

    def _day_block(self, day, events: list, lines: int, api) -> QWidget:
        host = QWidget()
        set_style(host, "common", "transparent")
        column = QVBoxLayout(host)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(self.ROW_GAP)

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

        # The "+N more" line takes a line of its own, except when a day has only the one
        if len(events) <= lines:
            shown, hidden = events, 0
        elif lines == 1:
            shown, hidden = events[:1], len(events) - 1
        else:
            shown, hidden = events[:lines - 1], len(events) - (lines - 1)

        for index, entry in enumerate(shown):
            extra = hidden if (lines == 1 and index == 0) else 0
            column.addWidget(self._row(entry, api, extra))

        if hidden and lines > 1:
            more = QLabel(f"+{hidden} more")
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
        if (day - today).days < 7:
            return day.strftime("%A")
        # A week out the weekday names repeat, so the date comes with it
        return f"{day.strftime('%A')}, {day.strftime('%b')} {day.day}"

    def _row(self, event, api, more: int = 0) -> QWidget:
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

        when_label = when_text(event)
        if more:
            when_label = f"{when_label}  +{more}" if when_label else f"+{more}"
        when = QLabel(when_label)
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
