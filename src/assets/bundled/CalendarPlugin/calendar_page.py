from __future__ import annotations

import calendar as calendar_module
from datetime import date, datetime
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel
from PyQt6.QtCore import Qt, QRect, QTimer
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QLinearGradient

from src.ui.page import SubPageFramework
from src.ui.controls.buttons import IconButton
from src.ui.icons import icon
from src.styling import make_font, SIZES, set_style

if TYPE_CHECKING:
    from src.main import Client


SOURCE_COLOURS = {
    "local":    "#4f9de0",
    "imported": "#a97fe0",
    "holiday":  "#d8a24a",
}


class DayCell(QWidget):
    """
    One square of the month grid.

    Self-painted rather than composed from labels: a cell redraws on every
    month change and every event edit, and rebuilding a nest of QLabels each
    time is both slower and harder to keep from clipping at small sizes.
    """

    DRAG_DISTANCE = 14

    def __init__(self, page: "CalendarPage"):
        super().__init__()
        self.page   = page
        self.day: date = None
        self.column = 0
        self.events: list = []
        self.in_month  = True
        self.is_today  = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._press = None

    def set_day(self, day: date, events: list, in_month: bool,
                column: int = 0) -> None:
        self.day      = day
        self.column   = column
        # Spans first, oldest first. A multi-day event has to sit in the same
        # slot in every cell it covers or its bar steps up and down across the
        # week - and it can only do that if the ordering is stable.
        self.events   = sorted(
            events,
            key=lambda e: (0 if e.spans_days else 1,
                           e.date or day, e.key),
        )
        self.in_month = in_month
        self.is_today = (day == date.today())
        self.update()

    ## -- painting

    def paintEvent(self, event) -> None:
        if self.day is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -2, -2)

        painter.setBrush(QBrush(QColor(255, 255, 255, 20 if self.in_month else 7)))
        painter.setPen(QPen(QColor("#7ed6a6") if self.is_today
                            else QColor(255, 255, 255, 30),
                            2 if self.is_today else 1))
        painter.drawRoundedRect(rect, 8, 8)

        # A band across the top belongs to the date number. Events start below
        # it rather than beside it - the first icon was landing against the
        # number and neither was readable.
        # The number needs clear air under it, not just a band that ends where
        # it does.
        header_h = 34

        # Date number
        painter.setPen(QPen(QColor(255, 255, 255, 235 if self.in_month else 90)))
        painter.setFont(make_font(SIZES.S2, bold=self.is_today))
        painter.drawText(rect.adjusted(8, 5, -8, 0),
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
                         str(self.day.day))

        if not self.events:
            painter.end()
            return

        limit = self.page.events_per_day()
        line_h = 26
        top = rect.top() + header_h
        shown = self.events[:limit]

        for entry in shown:
            if top + line_h > rect.bottom() - 4:
                break
            colour = QColor(entry.colour or SOURCE_COLOURS.get(entry.source, "#4f9de0"))

            # One rect per line, with everything centred inside it. Drawing the
            # bar, the chip and the text from three separate offsets is what
            # made the text sit high in its own background.
            line = QRect(rect.left() + 4, top, rect.width() - 9, line_h - 3)

            # A span runs to the very edge of the cell on whichever sides it
            # continues, so consecutive days read as one bar rather than as
            # the same event listed several times.
            starts_here = (entry.date == self.day) or self.column == 0
            ends_here = (entry.last_date == self.day) or self.column == 6
            if entry.spans_days:
                left = line.left() if starts_here else self.rect().left()
                right = line.right() if ends_here else self.rect().right()
                line = QRect(left, line.top(), right - left, line.height())

            # Kind reads from the shape, not only the glyph: a holiday gets a
            # full tinted bar, an all-day event a soft one, and a timed event
            # just its chip - so the three are distinguishable at a glance even
            # where two share an icon.
            if entry.spans_days:
                painter.setBrush(QBrush(QColor(colour.red(), colour.green(),
                                               colour.blue(), 85)))
                painter.setPen(Qt.PenStyle.NoPen)
                # Rounded only where it actually begins or ends. A rounded cap
                # in the middle of a run reads as a separate event.
                painter.drawRoundedRect(line, 6, 6)
                if not starts_here:
                    painter.drawRect(line.left(), line.top(), 8, line.height())
                if not ends_here:
                    painter.drawRect(line.right() - 8, line.top(), 8, line.height())
            elif entry.source == "holiday":
                painter.setBrush(QBrush(QColor(colour.red(), colour.green(),
                                               colour.blue(), 75)))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(line, 6, 6)
            elif entry.all_day:
                painter.setBrush(QBrush(QColor(colour.red(), colour.green(),
                                               colour.blue(), 36)))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(line, 6, 6)

            # The icon belongs to the day the thing starts. Repeating it in
            # every cell of a four-day trip is four icons for one event.
            draw_chip = (not entry.spans_days) or starts_here

            glyph_size = 14
            chip = QRect(line.left() + 3,
                         line.center().y() - glyph_size // 2 - 2,
                         glyph_size + 4, glyph_size + 4)
            if draw_chip:
                painter.setBrush(QBrush(QColor(colour.red(), colour.green(),
                                               colour.blue(), 60)))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(chip, 4, 4)

                try:
                    pixmap = icon(entry.icon,
                                  color=colour.name()).pixmap(glyph_size, glyph_size)
                    painter.drawPixmap(chip.left() + 2, chip.top() + 2, pixmap)
                except Exception:
                    painter.setBrush(QBrush(colour))
                    painter.drawEllipse(chip.adjusted(4, 4, -4, -4))

            # Named once per run, and again at the start of each week so a
            # bar continuing onto a new row is still identifiable.
            if entry.spans_days and not starts_here:
                top += line_h
                continue

            painter.setPen(QPen(QColor(235, 235, 240, 235 if self.in_month else 110)))
            painter.setFont(make_font(11))
            left_edge = chip.right() + 6 if draw_chip else line.left() + 8
            text_rect = QRect(left_edge, line.top(),
                              line.right() - left_edge - 4, line.height())
            label = (entry.title if entry.all_day
                     else f"{entry.time}  {entry.title}")
            painter.drawText(
                text_rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                painter.fontMetrics().elidedText(
                    label, Qt.TextElideMode.ElideRight, text_rect.width()))
            top += line_h

        # Anything that did not fit is counted rather than dropped silently.
        hidden = len(self.events) - len(shown)
        if hidden > 0:
            painter.setPen(QPen(QColor(255, 255, 255, 150)))
            painter.setFont(make_font(10, bold=True))
            painter.drawText(rect.adjusted(0, 0, -8, -4),
                             int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom),
                             f"+{hidden}")
        painter.end()

    def mousePressEvent(self, event) -> None:
        # Ignored on purpose, so it reaches HomePage and a drag across the
        # calendar still swipes back to the widgets. Accepting it here fixed
        # the minimap opening on a tap and broke navigation doing it - the
        # hold is blocked by a flag instead, which costs nothing.
        self._press = event.globalPosition().toPoint()
        self.page.block_hold(True)
        event.ignore()

    def mouseReleaseEvent(self, event) -> None:
        self.page.block_hold(False)
        start, self._press = self._press, None
        event.ignore()

        if start is None or self.day is None:
            return
        moved = (event.globalPosition().toPoint() - start).manhattanLength()
        # A drag is a swipe and belongs to the page; only a tap opens the day.
        if moved >= self.DRAG_DISTANCE:
            return
        if not self.page.taps_open_days():
            return
        self.page.open_day(self.day)


class CalendarPage(SubPageFramework):
    """A month at a time, with the surrounding days greyed rather than blank."""

    # Painted rather than a stylesheet: a QSS gradient on a page-sized widget
    # is re-parsed on every repaint, and this one covers the whole screen.
    #
    # The background follows the time of day, not the month. Pulling it toward
    # whatever events a month happens to contain means the page changes colour
    # as somebody pages through it, while the controls on top of it - labels,
    # icon buttons, the weekday row - keep the fixed colours the rest of the
    # application uses. Every control then looks correct on some months and
    # wrong on others, and there is no colour for them that is right on all.
    #
    # An event's own colour stays where it belongs: on the event, in its bar in
    # the day cell, and in the widgets that show it.
    #
    # Keys are hours. The gradient is interpolated between the two nearest, so
    # the page drifts through the day rather than stepping at each boundary.
    SKY = {
        0:  ("#0d1220", "#141326"),   # night
        5:  ("#111a2e", "#1d1b33"),   # first light
        7:  ("#1b2c46", "#2a2540"),   # morning
        10: ("#20364f", "#2c2b45"),   # daylight
        14: ("#22384f", "#2e2c46"),   # afternoon
        17: ("#2a3149", "#3a2740"),   # low sun
        19: ("#1f2439", "#2e1f36"),   # dusk
        21: ("#131a2b", "#1c162a"),   # evening
        24: ("#0d1220", "#141326"),   # back to night
    }

    #How often the background is re-measured against the clock. The gradient
    #moves slowly enough that a minute is invisible, and this runs on a page
    #that is often left open for hours.
    SKY_REFRESH_MS = 60 * 1000

    def __init__(self, client: "Client", page):
        super().__init__(client=client, key="sub.calendar", coord=(0, 1))

        today = date.today()
        self.year  = today.year
        self.month = today.month
        self.cells: list = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 18)
        outer.setSpacing(10)

        outer.addLayout(self._build_header())
        outer.addLayout(self._build_weekdays())

        self.grid = QGridLayout()
        self.grid.setSpacing(6)
        for index in range(42):          # six weeks covers every month layout
            cell = DayCell(self)
            self.cells.append(cell)
            self.grid.addWidget(cell, index // 7, index % 7)
        outer.addLayout(self.grid, stretch=1)

        # Over the grid rather than inside a cell: a sticker is dragged from
        # one day to another, and a child widget cannot cross into its sibling.
        from .sticker_layer import StickerLayer
        self.stickers = StickerLayer(self, self._sticker_store(), client)
        self.stickers.setGeometry(self.rect())
        self.stickers.show()
        self.stickers.raise_()

        self.add_features({
            "refresh":      self.refresh,
            "show_month":   self.show_month,
            "open_day":     self.open_day,
            "open_month":   self.open_month,
            "open_jump":    self.open_jump,
            "add_sticker":  self.add_sticker,
            "edit_stickers": self.edit_stickers,
            "sticker_store": lambda: self._sticker_store(),
            "current_month": lambda: (self.year, self.month),
        })

        self.client.subscribe_to_event("on_calendar_changed", self._on_changed)

        # The gradient drifts through the day, so it is re-measured while the
        # page sits open. A repaint only - nothing is rebuilt.
        self._sky_timer = QTimer(self)
        self._sky_timer.timeout.connect(self.update)
        self._sky_timer.start(self.SKY_REFRESH_MS)

        self.refresh()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        top, bottom = self._month_colours()
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(1.0, bottom)
        painter.fillRect(self.rect(), QBrush(gradient))
        painter.end()

    @staticmethod
    def _blend(base: QColor, other: QColor, amount: float) -> QColor:
        amount = max(0.0, min(1.0, amount))
        return QColor(
            int(round(base.red()   + (other.red()   - base.red())   * amount)),
            int(round(base.green() + (other.green() - base.green()) * amount)),
            int(round(base.blue()  + (other.blue()  - base.blue())  * amount)),
        )

    def _sky_hour(self) -> float:
        """The clock as a fraction of the day. Split out so a test can move it."""
        now = datetime.now()
        return now.hour + now.minute / 60.0

    def _month_colours(self) -> tuple:
        """
        The background for the time of day.

        Named for what every caller wants of it - the two ends of the page
        gradient - rather than for what it reads to answer that.
        """
        hour = self._sky_hour()
        keys = sorted(self.SKY)

        lower = max(k for k in keys if k <= hour)
        upper = min((k for k in keys if k > hour), default=keys[-1])
        span = (upper - lower) or 1
        amount = (hour - lower) / span

        top_from, bottom_from = (QColor(c) for c in self.SKY[lower])
        top_to, bottom_to = (QColor(c) for c in self.SKY[upper])
        return (self._blend(top_from, top_to, amount),
                self._blend(bottom_from, bottom_to, amount))

    ## -- chrome

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        self.title = QLabel("")
        self.title.setFont(make_font(SIZES.M2, bold=True))
        set_style(self.title, "common", "text-strong")
        row.addWidget(self.title)
        row.addStretch()

        self.month_btn = IconButton("mdi.format-list-bulleted", self.open_month,
                                    size=24)
        row.addWidget(self.month_btn)
        self.jump_btn = IconButton("mdi.calendar-search", self.open_jump,
                                   size=24)
        row.addWidget(self.jump_btn)
        self.sticker_btn = IconButton("mdi.sticker-emoji", self.add_sticker,
                                      size=24)
        row.addWidget(self.sticker_btn)
        # Arranging what is already there, without adding another.
        self.arrange_btn = IconButton("mdi.cursor-move", self.edit_stickers,
                                      size=24)
        row.addWidget(self.arrange_btn)
        self.subs_btn = IconButton("mdi.calendar-sync", self.open_subscriptions, size=24)
        row.addWidget(self.subs_btn)
        self.today_btn = IconButton("mdi.calendar-today", self.go_today, size=24)
        row.addWidget(self.today_btn)
        row.addWidget(IconButton("mdi.chevron-left", self.previous_month, size=28))
        row.addWidget(IconButton("mdi.chevron-right", self.next_month, size=28))
        return row

    def _build_weekdays(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        self.weekday_labels = []
        for _ in range(7):
            label = QLabel("")
            label.setFont(make_font(SIZES.S1, bold=True))
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            set_style(label, "common", "text-muted")
            self.weekday_labels.append(label)
            row.addWidget(label)
        return row

    def taps_open_days(self) -> bool:
        """
        Whether a tap on a cell should open its day right now.

        A press that was doing something else entirely still ends in a release
        on whatever is underneath. Dismissing the quick settings panel and
        swiping to the next sub-page both land on a day cell, and both opened
        the day list on the way out.

        Asked at the release rather than blocked at the press, because at the
        press none of this is known yet - the panel is still open and the
        swipe has not happened.
        """
        # A panel over the page took that press. Closing it IS what the press
        # did; the cell underneath was never the target.
        try:
            host = self.client.OVERLAYS
            if host is not None:
                from src.ui.overlays import Panel
                for panel in host.findChildren(Panel):
                    if panel.isVisible():
                        return False
        except Exception:
            pass

        # A dialog is in front, so nothing behind it is being tapped.
        try:
            if self.client.DIALOG.get() is not None:
                return False
        except Exception:
            pass

        # The page moved under the finger. The release belongs to the
        # navigation, not to whichever cell happened to end up beneath it.
        #
        # Read from the home page's coordinate rather than an animation flag:
        # a swipe changes it before the slide starts, so this is true from the
        # moment the gesture is recognised rather than only while it moves.
        if not self.isVisible():
            return False
        home = self.parent()
        here = getattr(self, "coord", None)
        showing = getattr(home, "_current_coord", None)
        if here is not None and showing is not None:
            if tuple(showing) != tuple(here):
                return False
        return True

    def block_hold(self, blocked: bool) -> None:
        """Stop the home page's hold gesture while a day is being pressed."""
        home = self.parent()
        if home is not None:
            setattr(home, "_hold_blocked", bool(blocked))

    ## -- settings

    def _option(self, path: str, default):
        api = self._calendar()
        if api is None:
            return default
        try:
            return api["option"](path, default)
        except Exception:
            return default

    def events_per_day(self) -> int:
        try:
            return max(1, int(self._option("general.events_per_day", 4)))
        except (TypeError, ValueError):
            return 4

    def _monday_first(self) -> bool:
        return bool(self._option("general.week_starts_monday", False))

    def _show_holidays(self) -> bool:
        return bool(self._option("general.show_holidays", True))

    ## -- navigation

    def show_month(self, year: int, month: int) -> None:
        self.year, self.month = int(year), int(month)
        self.refresh()

    def next_month(self, event=None) -> None:
        self.show_month(self.year + (self.month == 12), (self.month % 12) + 1)

    def previous_month(self, event=None) -> None:
        month = self.month - 1
        self.show_month(self.year - (month < 1), 12 if month < 1 else month)

    def go_today(self, event=None) -> None:
        today = date.today()
        self.show_month(today.year, today.month)

    ## -- data

    def _calendar(self):
        try:
            return self.client.public.calendar
        except Exception:
            return None

    def _on_changed(self, event=None) -> None:
        # Queued, and guarded: a sub-page is destroyed when its plugin unloads,
        # and a handler that raises is dropped from the bus entirely.
        def apply():
            try:
                self.refresh()
            except RuntimeError:
                pass
        self.client.call_on_ui(apply)

    def teardown(self) -> None:
        self.client.unsubscribe_from_event("on_calendar_changed", self._on_changed)

    def refresh(self) -> None:
        # The month being looked at, and today. A panel showing a calendar is
        # also the thing somebody glances at to find out what day it is, and
        # a heading of "August 2026" answers that only if you already know.
        # Today is said in full only while it is on screen - on any other
        # month it would read as the month's own date.
        today = date.today()
        heading = f"{calendar_module.month_name[self.month]} {self.year}"
        if (today.year, today.month) == (self.year, self.month):
            heading = f"{today.strftime('%A')} {today.day} \u00b7 {heading}"
        self.title.setText(heading)

        first_weekday = 0 if self._monday_first() else 6
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        order = [(first_weekday + i) % 7 for i in range(7)]
        for label, index in zip(self.weekday_labels, order):
            label.setText(names[index])

        api = self._calendar()
        by_day = {}
        if api is not None:
            try:
                by_day = api["in_month"](self.year, self.month, self._show_holidays())
            except Exception as e:
                self.client.log("warning", f"[Calendar] Could not read month: {e}")

        # Neighbouring days are shown greyed rather than left blank, so the
        # grid always reads as a continuous month rather than a ragged block.
        weeks = calendar_module.Calendar(firstweekday=first_weekday).monthdatescalendar(
            self.year, self.month)
        days = [day for week in weeks for day in week]
        while len(days) < 42:
            days.append(days[-1] + (days[-1] - days[-2]))

        for index, (cell, day) in enumerate(zip(self.cells, days[:42])):
            in_month = (day.month == self.month and day.year == self.year)
            events = by_day.get(day, []) if in_month else []
            if not in_month and api is not None and self._show_holidays():
                try:
                    events = api["on_day"](day, self._show_holidays())
                except Exception:
                    events = []
            cell.set_day(day, events, in_month, column=index % 7)

        # The month on screen decides which stickers are on it. The layer is
        # re-measured as well: a month needing six weeks is a taller grid than
        # one needing five, and the day boxes move with it.
        layer = getattr(self, "stickers", None)
        if layer is not None:
            layer.setGeometry(self.rect())
            layer.raise_()
            layer.refresh()

    ## -- stickers

    def _sticker_store(self):
        """
        The calendar's own sticker file, made once and shared.

        On the page rather than the plugin because the page is what draws them,
        and a page that outlives a plugin reload would otherwise be holding a
        store belonging to something that has gone.
        """
        api = self._calendar()
        if api is not None:
            store = api.get("stickers") if hasattr(api, "get") else None
            if store is not None:
                return store

        # The plugin is not up yet. An empty store keeps the page drawable
        # rather than making every caller check for None.
        existing = getattr(self, "_stickers", None)
        if existing is not None:
            return existing

        from src.constants import get_data_dir, APP_NAME
        from .stickers import StickerStore
        self._stickers = StickerStore(
            get_data_dir(APP_NAME) / "calendar" / "stickers.json",
            log=self.client.log)
        return self._stickers

    def edit_stickers(self, event=None) -> None:
        """
        Arrange the stickers already on the month.

        Separate from adding one, because the layer takes the mouse for the
        whole page while it is on: the toolbar and the day boxes are underneath
        it and cannot be pressed. Done, on the layer itself, gives them back.
        """
        layer = getattr(self, "stickers", None)
        if layer is None:
            return
        layer.set_editing(not layer.editing)

    def add_sticker(self, event=None) -> None:
        """
        Pick one from the library, then drop it on a day.

        The picker is the bundle's own, so the calendar offers exactly the
        stickers the home screen does rather than keeping a second library.
        """
        library = None
        try:
            library = self.client.public.stickers
        except Exception:
            library = None
        if library is None:
            self.client.simple_notify(
                "mdi.sticker-emoji", "Stickers",
                "The sticker library is not loaded.")
            return

        entries = library["list"](refresh=True)
        if not entries:
            self.client.simple_notify(
                "mdi.sticker-emoji", "Stickers",
                "There are no stickers yet. Upload one from a phone.")
            return

        from .sticker_picker import choose_sticker
        choose_sticker(self.client, entries, self._place_sticker)

    def _place_sticker(self, name: str) -> None:
        started = self.stickers.begin_placing(
            name, on_placed=self._sticker_placed)
        if not started:
            return
        self.client.simple_notify(
            "mdi.sticker-emoji", "Stickers",
            "Drag it onto a day, then press Done.", history=False)

    def _sticker_placed(self, key: str) -> None:
        """Ask whether it belongs to an event, once it has a day."""
        from .sticker_attach import ask_to_attach
        ask_to_attach(self.client, self, key)

    def open_jump(self, event=None) -> None:
        """A year, then a month. Two taps to anywhere."""
        from .jump_dialog import JumpToMonthDialog
        self.client.dialog(JumpToMonthDialog(self.client, self))

    def open_month(self, event=None) -> None:
        """Everything on this month, in one list."""
        from .month_dialog import MonthEventsDialog
        self.client.dialog(MonthEventsDialog(self.client, self,
                                             self.year, self.month))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        layer = getattr(self, "stickers", None)
        if layer is not None:
            layer.setGeometry(self.rect())
            layer.raise_()

    def open_subscriptions(self, event=None) -> None:
        from .dialogs import SubscriptionsDialog
        self.client.dialog(SubscriptionsDialog(self.client))

    def open_day(self, day: date) -> None:
        from .dialogs import DayViewDialog
        self.client.dialog(DayViewDialog(self.client, day))

    def open_event(self, event_key: str) -> None:
        from .dialogs import EventViewDialog
        self.client.dialog(EventViewDialog(self.client, event_key))
