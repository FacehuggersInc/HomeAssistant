"""
Setting an alarm without saying anything.

The voice route exists, and a panel is a shared screen - not everybody wants
to talk to it, and not every room is quiet enough to be heard.

Shaped like `DurationPickerDialog`, which asks the neighbouring question, so
the two read the same: steppers for the numbers, a readout under them saying
what the numbers mean, and a confirm that disables itself rather than
refusing silently.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics

from src.styling import set_style, make_font, SIZES
from src.ui.overlays import BaseDialog
from src.ui.controls.stepper import Stepper

if TYPE_CHECKING:
    from src.main import Client


#How many days ahead can be chosen. A week: past that somebody wants a
#calendar entry rather than an alarm, and the day buttons stop fitting.
DAYS_AHEAD = 7


class AlarmPickerDialog(BaseDialog):
    """A time of day, a day, and whether it repeats."""

    #Wide enough for seven day buttons with room to spare. Measured widths
    #kept clipping "Tomorrow" - a styled button carries padding the font
    #metrics know nothing about - so this is simply generous, and BaseDialog
    #clamps it to the screen anyway.
    WIDTH = 860

    #One minute. A five minute step is fine for most alarms and useless for
    #the one somebody wants at 7:23, and a stepper holds to repeat - so the
    #cost of the fine step is a longer press rather than an impossible one.
    MINUTE_STEP = 1

    #Padding either side of a day name, and the gap between two of them.
    DAY_PADDING = 20
    DAY_GAP = 6

    def __init__(self, client: "Client", title: str = "New alarm",
                 when: float = None, on_chosen: Callable = None,
                 choose_text: str = "Set"):
        # Wide enough for the day names, measured rather than guessed.
        #
        # Seven buttons sharing a fixed width gives each of them about 80px,
        # and "Tomorrow" does not fit in that - a QPushButton cannot wrap, so
        # it clips with nothing to say it did. BaseDialog clamps whatever is
        # asked for to the screen, so asking for too much is safe and asking
        # for too little is not.
        # Locals, not attributes. Nothing may be set on `self` before the Qt
        # base class has been constructed.
        labels = [AlarmPickerDialog._day_name(offset)
                  for offset in range(DAYS_AHEAD)]
        metrics = QFontMetrics(make_font(SIZES.S1, bold=True))
        row_width = sum(metrics.horizontalAdvance(name) + self.DAY_PADDING
                        for name in labels)
        row_width += self.DAY_GAP * (len(labels) - 1)
        super().__init__(client, title, "",
                         width=max(self.WIDTH, row_width + 48))
        self._day_labels = labels
        self.on_chosen = on_chosen

        start = datetime.fromtimestamp(when) if when else self._default_start()
        self._offset_days = 0

        row = QHBoxLayout()
        row.setSpacing(18)
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 24 hour steppers, and the readout says it back in 12 hour with the
        # suffix. Two steppers plus an am/pm toggle is three controls for one
        # number, and the toggle is the one people get wrong.
        self.hours = Stepper("Hour", start.hour, 0, 23, wrap=True,
                             on_change=lambda _: self._update_readout())
        self.minutes = Stepper("Minute", start.minute, 0, 59, wrap=True,
                               step=self.MINUTE_STEP,
                               on_change=lambda _: self._update_readout())
        row.addWidget(self.hours)
        row.addWidget(self._colon())
        row.addWidget(self.minutes)

        holder = QWidget()
        set_style(holder, "common", "transparent")
        holder.setLayout(row)
        self.content.addWidget(holder)

        # The day, as buttons rather than a stepper. Seven of them is a row,
        # and a row of names is read at a glance where a number is not.
        self.day_buttons = []
        days = QHBoxLayout()
        days.setSpacing(self.DAY_GAP)
        for offset, name in enumerate(self._day_labels):
            button = QPushButton(name)
            button.setFont(make_font(SIZES.S1, bold=True))
            button.setFixedHeight(44)
            # Its own text, plus padding. Sharing the row equally is what
            # clips the long ones.
            button.setMinimumWidth(
                metrics.horizontalAdvance(name) + self.DAY_PADDING)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            # `offset=offset`, because a lambda closing over the loop variable
            # reads it when it fires - every button would pick the last day.
            button.clicked.connect(
                lambda _checked=False, offset=offset: self._pick_day(offset))
            days.addWidget(button, stretch=1)
            self.day_buttons.append(button)
        day_holder = QWidget()
        set_style(day_holder, "common", "transparent")
        day_holder.setLayout(days)
        self.content.addWidget(day_holder)

        self.repeat_button = QPushButton("Repeat daily")
        self.repeat_button.setFont(make_font(SIZES.S1, bold=True))
        self.repeat_button.setFixedHeight(44)
        self.repeat_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.repeat_button.clicked.connect(self._toggle_repeat)
        self._repeats = False
        # Styled now, not only on the first press. Left to the toggle, it
        # opens wearing the platform's own button look and then jumps to
        # this dialog's on the first tap - which reads as a rendering fault
        # rather than a state change.
        self._paint_repeat()
        self.content.addWidget(self.repeat_button)

        self.readout = QLabel("")
        self.readout.setFont(make_font(SIZES.S2))
        self.readout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(self.readout, "common", "text-muted")
        self.content.addWidget(self.readout)

        self.add_button("Cancel", self.close, "secondary")
        self._confirm = self.add_button(choose_text, self._choose, "primary")
        self._pick_day(0)

    ## -- pieces

    def _default_start(self) -> datetime:
        """The next round hour, which is what most alarms are near."""
        moment = datetime.now() + timedelta(hours=1)
        return moment.replace(minute=0, second=0, microsecond=0)

    def _colon(self) -> QLabel:
        colon = QLabel(":")
        colon.setFont(make_font(SIZES.L1, bold=True))
        set_style(colon, "common", "text-muted")
        return colon

    @staticmethod
    def _day_name(offset: int) -> str:
        if offset == 0:
            return "Today"
        if offset == 1:
            return "Tomorrow"
        return (datetime.now() + timedelta(days=offset)).strftime("%a")

    ## -- state

    def _pick_day(self, offset: int) -> None:
        self._offset_days = int(offset)
        for index, button in enumerate(self.day_buttons):
            set_style(button, "overlays",
                      "dialog-button-primary" if index == self._offset_days
                      else "dialog-button-secondary")
        self._update_readout()

    def _paint_repeat(self) -> None:
        set_style(self.repeat_button, "overlays",
                  "dialog-button-primary" if self._repeats
                  else "dialog-button-secondary")
        self.repeat_button.setText(
            "Repeats daily" if self._repeats else "Repeat daily")

    def _toggle_repeat(self) -> None:
        self._repeats = not self._repeats
        self._paint_repeat()
        self._update_readout()

    def chosen_when(self) -> float:
        """The epoch the steppers and the day add up to."""
        target = datetime.now().replace(hour=self.hours.value,
                                        minute=self.minutes.value,
                                        second=0, microsecond=0)
        target += timedelta(days=self._offset_days)
        return target.timestamp()

    def _update_readout(self) -> None:
        from src.assets.bundled.CoreWidgetsBundle.alarms import describe_alarm

        when = self.chosen_when()
        if when <= datetime.now().timestamp():
            # Said rather than silently rolled to tomorrow. Somebody who
            # picked Today and a time this morning meant something, and
            # guessing which is worse than asking.
            self.readout.setText("That time has already passed today.")
        else:
            said = describe_alarm(when)
            self.readout.setText(
                f"{said}, every day" if self._repeats else said)
        # Disabled says why nothing happens on a tap, where a dialog that
        # refused to close would not.
        button = getattr(self, "_confirm", None)
        if button is not None:
            button.setEnabled(when > datetime.now().timestamp())

    def _choose(self) -> None:
        when = self.chosen_when()
        if when <= datetime.now().timestamp():
            return
        self.close()
        if callable(self.on_chosen):
            self.on_chosen(when, self._repeats)
