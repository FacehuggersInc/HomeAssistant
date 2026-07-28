from __future__ import annotations

import calendar as calendar_module
from datetime import date
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel
from PyQt6.QtCore import Qt, QRect
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

    DRAG_SLOP = 14

    def __init__(self, page: "CalendarPage"):
        super().__init__()
        self.page   = page
        self.day: date = None
        self.events: list = []
        self.in_month  = True
        self.is_today  = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._press = None

    def set_day(self, day: date, events: list, in_month: bool) -> None:
        self.day      = day
        self.events   = events
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

            # Kind reads from the shape, not only the glyph: a holiday gets a
            # full tinted bar, an all-day event a soft one, and a timed event
            # just its chip - so the three are distinguishable at a glance even
            # where two share an icon.
            if entry.source == "holiday":
                painter.setBrush(QBrush(QColor(colour.red(), colour.green(),
                                               colour.blue(), 75)))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(line, 6, 6)
            elif entry.all_day:
                painter.setBrush(QBrush(QColor(colour.red(), colour.green(),
                                               colour.blue(), 36)))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(line, 6, 6)

            glyph_size = 14
            chip = QRect(line.left() + 3,
                         line.center().y() - glyph_size // 2 - 2,
                         glyph_size + 4, glyph_size + 4)
            painter.setBrush(QBrush(QColor(colour.red(), colour.green(),
                                           colour.blue(), 60)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(chip, 4, 4)

            try:
                pixmap = icon(entry.icon, color=colour.name()).pixmap(glyph_size, glyph_size)
                painter.drawPixmap(chip.left() + 2, chip.top() + 2, pixmap)
            except Exception:
                painter.setBrush(QBrush(colour))
                painter.drawEllipse(chip.adjusted(4, 4, -4, -4))

            painter.setPen(QPen(QColor(235, 235, 240, 235 if self.in_month else 110)))
            painter.setFont(make_font(11))
            text_rect = QRect(chip.right() + 6, line.top(),
                              line.right() - chip.right() - 10, line.height())
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
        if moved < self.DRAG_SLOP:
            self.page.open_day(self.day)


class CalendarPage(SubPageFramework):
    """A month at a time, with the surrounding days greyed rather than blank."""

    # Painted rather than a stylesheet: a QSS gradient on a page-sized widget
    # is re-parsed on every repaint, and this one covers the whole screen.
    TOP    = QColor("#1b2436")
    BOTTOM = QColor("#2b1f33")

    # How far the month's own colours pull the background. Enough to notice a
    # holiday-heavy month, not enough to stop the page reading as one place.
    TINT = 0.42

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

        self.add_features({
            "refresh":      self.refresh,
            "show_month":   self.show_month,
            "open_day":     self.open_day,
            "current_month": lambda: (self.year, self.month),
        })

        self.client.subscribe_to_event("on_calendar_changed", self._on_changed)
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
        return QColor(
            int(base.red()   + (other.red()   - base.red())   * amount),
            int(base.green() + (other.green() - base.green()) * amount),
            int(base.blue()  + (other.blue()  - base.blue())  * amount),
        )

    def _month_colours(self) -> tuple:
        """
        The base gradient pulled toward whatever is on this month.

        Averaged rather than taking the first: a month with one birthday in it
        should look slightly different from the one before, not entirely.
        """
        colours = getattr(self, "_tints", None)
        if not colours:
            return self.TOP, self.BOTTOM

        reds = sum(c.red() for c in colours) // len(colours)
        greens = sum(c.green() for c in colours) // len(colours)
        blues = sum(c.blue() for c in colours) // len(colours)
        average = QColor(reds, greens, blues)
        return (self._blend(self.TOP, average, self.TINT * 0.6),
                self._blend(self.BOTTOM, average, self.TINT))

    ## -- chrome

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        self.title = QLabel("")
        self.title.setFont(make_font(SIZES.M2, bold=True))
        set_style(self.title, "common", "text-strong")
        row.addWidget(self.title)
        row.addStretch()

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
        self.title.setText(f"{calendar_module.month_name[self.month]} {self.year}")

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

        # Gathered while the month is being laid out, so the background is
        # never a frame behind the grid it belongs to.
        tints = []
        for events in by_day.values():
            for entry in events:
                tints.append(QColor(entry.colour
                                    or SOURCE_COLOURS.get(entry.source, "#4f9de0")))
        self._tints = tints[:40]

        for cell, day in zip(self.cells, days[:42]):
            in_month = (day.month == self.month and day.year == self.year)
            events = by_day.get(day, []) if in_month else []
            if not in_month and api is not None and self._show_holidays():
                try:
                    events = api["on_day"](day, self._show_holidays())
                except Exception:
                    events = []
            cell.set_day(day, events, in_month)

    ## -- dialogs

    def open_day(self, day: date) -> None:
        from .dialogs import DayViewDialog
        self.client.dialog(DayViewDialog(self.client, day))

    def open_event(self, event_key: str) -> None:
        from .dialogs import EventViewDialog
        self.client.dialog(EventViewDialog(self.client, event_key))
