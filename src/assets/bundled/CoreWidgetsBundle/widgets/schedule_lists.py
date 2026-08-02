"""
Everything that is set, in one place.

The voice route answers "what timers are running" and the home page shows a
widget per timer, and neither is a way to CANCEL the third one down without
saying its name. These are: a row each, a cancel on every row, and a button to
add another.

Two dialogs rather than one with tabs. A timer and an alarm are different
questions - "how long left" against "what time" - and somebody opening this
already knows which they meant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QScrollArea,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer

from src.styling import set_style, make_font, SIZES, style_scrollbar
from src.ui.overlays import BaseDialog
from src.ui.controls.buttons import IconButton, ActionButton

if TYPE_CHECKING:
    from src.main import Client


class _Row(QFrame):
    """One thing that is set, and a way to stop it."""

    HEIGHT = 62

    def __init__(self, client: "Client", title: str, detail: str,
                 tint: str, on_cancel: Callable):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(self, "settings", "setting-block")
        self.setMinimumHeight(self.HEIGHT)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 10, 8)
        row.setSpacing(12)

        column = QVBoxLayout()
        column.setSpacing(2)

        self.title = QLabel(title)
        self.title.setFont(make_font(SIZES.S2, bold=True))
        self.title.setWordWrap(True)
        set_style(self.title, "common", "text-strong")
        column.addWidget(self.title)

        self.detail = QLabel(detail)
        self.detail.setFont(make_font(SIZES.S1))
        self.detail.setWordWrap(True)
        set_style(self.detail, "common", "text-muted")
        column.addWidget(self.detail)
        row.addLayout(column, stretch=1)

        # A tinted dot, so two rows are told apart at a glance the way the
        # widgets on the home page are.
        dot = QLabel()
        dot.setFixedSize(12, 12)
        dot.setStyleSheet(f"background:{tint};border-radius:6px;")
        row.addWidget(dot, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.cancel_button = IconButton("mdi.close", on_cancel, size=20)
        self.cancel_button.setFixedSize(40, 40)
        row.addWidget(self.cancel_button,
                      alignment=Qt.AlignmentFlag.AlignVCenter)

    def update_detail(self, text: str) -> None:
        if text != self.detail.text():
            self.detail.setText(text)


class _ListDialog(BaseDialog):
    """
    The shape both of these share: a scrolling list, an add, a close.

    Rebuilt rather than diffed when something is cancelled. The list is short
    by definition - a panel with forty timers on it is not a case worth
    optimising for - and rebuilding cannot leave a row pointing at something
    that has gone.
    """

    WIDTH = 760
    MAX_HEIGHT = 900
    #How often the remaining times are refreshed. A second: these are
    #countdowns, and one that does not move looks broken.
    TICK_MS = 1000

    def __init__(self, client: "Client", title: str, body: str,
                 add_text: str, add_icon: str):
        super().__init__(client, title, body)

        self.list_host = QWidget()
        set_style(self.list_host, "common", "transparent")
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # No floor. Giving up height is what a scroll area is for.
        scroll.setMinimumHeight(0)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Expanding)
        scroll.setWidget(self.list_host)
        style_scrollbar(scroll)
        self.content.addWidget(scroll, stretch=1)

        self.rows: dict = {}
        self.add_button(add_text, self._add, "primary")
        self.add_button("Close", self.close, "secondary")
        self.refresh()

        self._ticker = QTimer(self)
        self._ticker.timeout.connect(self._tick)
        self._ticker.start(self.TICK_MS)

    ## -- for a subclass

    def entries(self) -> list:
        """`(key, title, detail, tint)` for everything to show."""
        raise NotImplementedError

    def cancel_entry(self, key: str) -> None:
        raise NotImplementedError

    def add_entry(self) -> None:
        raise NotImplementedError

    def empty_text(self) -> str:
        return "Nothing set."

    ## -- the list

    def refresh(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.rows = {}

        try:
            entries = self.entries()
        except Exception as e:
            self.client.log("warning", f"[Lists] Could not read: {e}")
            entries = []

        if not entries:
            empty = QLabel(self.empty_text())
            empty.setFont(make_font(SIZES.S2))
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            set_style(empty, "common", "text-muted")
            self.list_layout.addWidget(empty)
            self.list_layout.addStretch()
            return

        for key, title, detail, tint in entries:
            # `key=key`, because a lambda closing over the loop variable reads
            # it when it fires - every row would cancel the last one.
            row = _Row(self.client, title, detail, tint,
                       lambda key=key: self._cancel(key))
            self.rows[key] = row
            self.list_layout.addWidget(row)
        self.list_layout.addStretch()

    def _tick(self) -> None:
        """
        The details only, without rebuilding.

        A rebuild once a second would take the row out from under a finger
        on its way to the cancel button.
        """
        try:
            entries = self.entries()
        except Exception:
            return
        if {key for key, *_ in entries} != set(self.rows):
            # Something appeared or went while this was open.
            self.refresh()
            return
        for key, _title, detail, _tint in entries:
            row = self.rows.get(key)
            if row is not None:
                row.update_detail(detail)

    def _cancel(self, key: str) -> None:
        try:
            self.cancel_entry(key)
        except Exception as e:
            self.client.log("warning", f"[Lists] Could not cancel: {e}")
        self.refresh()

    def _add(self) -> None:
        self.close()
        self.add_entry()


class TimersDialog(_ListDialog):
    """Every running timer, with a cancel each."""

    def __init__(self, client: "Client"):
        super().__init__(client, "Timers",
                         "Everything counting down right now.",
                         "New timer", "mdi.timer-plus-outline")

    def _api(self):
        return self.client.public.timers

    def entries(self) -> list:
        api = self._api()
        out = []
        for timer in api["running"]():
            left = api["describe"](timer.remaining())
            out.append((timer.key, timer.label(), f"{left} left",
                        getattr(timer, "colour", "#3f7fbf")))
        return out

    def cancel_entry(self, key: str) -> None:
        self._api()["cancel"](key)

    def add_entry(self) -> None:
        from src.ui.dialogs import DurationPickerDialog
        self.client.dialog(DurationPickerDialog(
            self.client, title="New timer", seconds=300,
            on_chosen=lambda seconds: self._api()["start"](float(seconds)),
            choose_text="Start"))

    def empty_text(self) -> str:
        return "No timers running."


class AlarmsDialog(_ListDialog):
    """Every scheduled alarm, with a cancel each."""

    def __init__(self, client: "Client"):
        super().__init__(client, "Alarms",
                         "Everything set to go off.",
                         "New alarm", "mdi.alarm-plus")

    def _api(self):
        return self.client.public.alarms

    def entries(self) -> list:
        api = self._api()
        out = []
        for alarm in api["scheduled"]():
            detail = api["describe"](alarm.when)
            if alarm.repeats:
                detail += "  ·  every day"
            title = alarm.name or api["clock_text"](alarm.when)
            out.append((alarm.key, title, detail,
                        getattr(alarm, "colour", "#c0603f")))
        return out

    def cancel_entry(self, key: str) -> None:
        self._api()["cancel"](key)

    def add_entry(self) -> None:
        from .alarm_picker import AlarmPickerDialog
        self.client.dialog(AlarmPickerDialog(
            self.client, title="New alarm",
            on_chosen=lambda when, repeats=False:
                self._api()["schedule"](when, repeats=bool(repeats))))

    def empty_text(self) -> str:
        return "No alarms set."
