from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QPainter, QColor, QPen, QFontMetrics

from src.ui.widget import Widget
from src.styling import make_font

if TYPE_CHECKING:
    from src.main import Client


class DateTimeWidget(Widget):
    """
    The time, with the day under it.

    Painted as one block rather than stacked labels. Two QLabels in a column
    each carried their own font metrics, their own drop shadow and their own
    baseline, and the gap between them was whatever the layout decided - so it
    read as two widgets that happened to be near each other rather than one
    thing. Painting both lines against a shared baseline grid is what makes it
    look deliberate.

    No background and no border by design: it sits on the wallpaper and the
    wallpaper is the background.
    """

    KEY         = "datetimewidget"
    NAME        = "Date and time"
    ICON        = "mdi.clock-outline"
    DESCRIPTION = "The time, with the full date beneath it."

    RESIZABLE = False
    ROTATABLE = True        # nothing but painting, so it can
    FLOATABLE = True
    REMOVABLE = True

    DEFAULT_ANCHOR = "bottom-left:0"

    # Pixels of clear space between the bottom of the time's glyphs and the
    # top of the date's. Measured against the ink, not the font box: ascent
    # and descent carry a lot of leading, so laying the two lines out from
    # them put about 50px of nothing between a 96px time and its date.
    LINE_GAP = 5
    #room for the painted shadow, which is not in any metric
    SHADOW_PAD = 3

    def __init__(
        self,
        client:    "Client",
        show_date: bool = True,
        show_time: bool = True,
        time_size: int = 96,
        date_size: int = 27,
        time_font: str = "poppins-light",
        date_font: str = "poppins-light",
        anchor:    str = None,
        **kwargs,
    ):
        kwargs.pop("width", None)
        kwargs.pop("height", None)
        super().__init__(
            client = client,
            key    = self.KEY,
            anchor = anchor or self.DEFAULT_ANCHOR,
            width  = None,
            height = None,
            **kwargs,
        )

        self._show_time = show_time
        self._show_date = show_date
        self._time_size = int(time_size)
        self._date_size = int(date_size)
        self._time_font = time_font
        self._date_font = date_font

        self._time_qfont = make_font(self._time_size, bold=False,
                                     family=self._time_font)
        self._date_qfont = make_font(self._date_size, bold=False,
                                     family=self._date_font)

        self._time_fmt = str(client.setting("home.clock.time_format.value", "%I:%M %p"))
        self.client.subscribe_to_event("on_settings_saved", self._on_settings_saved)

        self._time_text = ""
        self._date_text = ""
        self._last = None

        self._recompute()
        self._resize_to_content()
        self.start_tick(1000)

    ## -- text

    def _on_settings_saved(self, event=None) -> None:
        try:
            self._time_fmt = str(
                self.client.setting("home.clock.time_format.value", "%I:%M %p"))
            self._recompute()
            self._resize_to_content()
            self.update()
        except RuntimeError:
            pass

    def _long_date(self, now: datetime) -> str:
        """
        "Tuesday, July 28" - the full day, the month, the day number.

        Written out rather than taken from home.clock.date_format, which defaults to
        the abbreviated "%a, %b %d". The setting still drives the clock tile
        and anything else reading it; this widget is deliberately the long
        form, because it is the one with room for it.
        """
        return f"{now.strftime('%A')}, {now.strftime('%B')} {now.day}"

    @staticmethod
    def _trim_leading_zero(text: str) -> str:
        """
        "01:30 PM" -> "1:30 PM", without eating midnight.

        lstrip("0") removes *every* leading zero, so "00:30" became ":30".
        One is the most that is ever a leading hour zero.
        """
        if len(text) > 1 and text[0] == "0" and text[1].isdigit():
            return text[1:]
        return text

    def _recompute(self) -> None:
        now = datetime.now()
        if self._show_time:
            self._time_text = self._trim_leading_zero(now.strftime(self._time_fmt))
        else:
            self._time_text = ""
        self._date_text = self._long_date(now) if self._show_date else ""

    ## -- geometry

    def _metrics(self):
        time_m = QFontMetrics(self._time_qfont)
        date_m = QFontMetrics(self._date_qfont)
        return time_m, date_m

    def _layout(self):
        """
        Baselines and size, measured from where the glyphs actually are.

        `tightBoundingRect` gives the ink: its top is negative (how far the
        tallest glyph reaches above the baseline) and its bottom is how far the
        lowest reaches below. Digits and a capitalised date have almost no
        descender, so stacking on the ink puts exactly LINE_GAP between them -
        where ascent/descent would have added the font's leading at both ends.

        Returns (time_baseline, date_baseline, width, height).
        """
        time_m, date_m = self._metrics()
        pad = self.SHADOW_PAD

        show_time = bool(self._show_time and self._time_text)
        show_date = bool(self._show_date and self._date_text)

        t_ink = time_m.tightBoundingRect(self._time_text) if show_time else None
        d_ink = date_m.tightBoundingRect(self._date_text) if show_date else None

        width = 0
        if show_time:
            width = max(width, time_m.horizontalAdvance(self._time_text))
        if show_date:
            width = max(width, date_m.horizontalAdvance(self._date_text))

        time_baseline = date_baseline = 0.0
        y = float(pad)

        if show_time:
            time_baseline = y - t_ink.top()          # top is negative
            y = time_baseline + max(0, t_ink.bottom())

        if show_date:
            if show_time:
                y += self.LINE_GAP
            date_baseline = y - d_ink.top()
            y = date_baseline + max(0, d_ink.bottom())

        height = y + pad
        return time_baseline, date_baseline, max(1, width + pad * 2), max(1, int(height))

    def _content_size(self):
        _, _, width, height = self._layout()
        return width, height

    def _resize_to_content(self) -> None:
        width, height = self._content_size()
        if (width, height) == (self.width(), self.height()):
            return
        self.set_content_size(width, height)
        bounds_w, bounds_h = self.rotated_bounds(width, height)
        self.setFixedSize(bounds_w, bounds_h)

    ## -- painting

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.apply_rotation(painter)

        content_w, _ = self.content_size()
        time_baseline, date_baseline, _, _ = self._layout()

        if self._show_time and self._time_text:
            self._draw_line(painter, self._time_qfont, self._time_text,
                            content_w, time_baseline, QColor(255, 255, 255, 248))

        if self._show_date and self._date_text:
            # Softer than the time, so the pair reads as one block with a
            # heading rather than two competing lines.
            self._draw_line(painter, self._date_qfont, self._date_text,
                            content_w, date_baseline, QColor(255, 255, 255, 190))

        painter.end()

    def _draw_line(self, painter: QPainter, font, text: str,
                   width: float, baseline: float, colour: QColor) -> None:
        painter.setFont(font)
        # Drawn at the baseline point, not bottom-aligned inside a rect.
        # AlignBottom aligns the FONT BOX bottom, which sits a descent below
        # the baseline - so the glyphs were lifted by that much and the top
        # third of a 96px time was clipped off the widget.
        painter.setPen(QPen(QColor(0, 0, 0, 120)))
        painter.drawText(QPointF(1.5, baseline + 1.5), text)
        painter.setPen(QPen(colour))
        painter.drawText(QPointF(0.0, baseline), text)

    ## -- lifecycle

    def tick(self) -> None:
        self._recompute()
        state = (self._time_text, self._date_text)
        if state == self._last:
            return
        self._last = state
        self._resize_to_content()
        self.update()

    def layout_state(self) -> dict:
        state = super().layout_state()
        state["show_date"] = self._show_date
        state["show_time"] = self._show_time
        return state

    def apply_layout_state(self, state: dict) -> None:
        super().apply_layout_state(state)
        self._show_date = bool(state.get("show_date", self._show_date))
        self._show_time = bool(state.get("show_time", self._show_time))
        self._recompute()
        self._resize_to_content()

    def teardown(self) -> None:
        try:
            self.client.unsubscribe_from_event("on_settings_saved",
                                               self._on_settings_saved)
        except Exception:
            pass
        self.stop_tick()
