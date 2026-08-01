from __future__ import annotations

import json
import urllib.parse
import urllib.request
import time
from datetime import datetime
from threading import Thread
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QToolButton,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPainter, QColor, QBrush, QLinearGradient

from src.ui.overlays import Panel
from src.ui.icons import icon
from src.styling import make_font, SIZES, set_style, add_text_shadow

if TYPE_CHECKING:
    from src.main import Client


SOURCE_COLOURS = {"local": "#4f9de0", "imported": "#a97fe0", "holiday": "#d8a24a"}


class ReminderPanel(Panel):
    """
    A full-height card that appears when something is about to start.

    A notification is a line of text that has already gone by the time anyone
    looks up. This is the opposite: everything about the event, a map of where
    it is, and the two things a person actually wants to do next - see it in
    the calendar, or change it.
    """

    WIDTH_RATIO = 0.48
    MIN_WIDTH   = 520
    MAP_H       = 300

    def __init__(self, client: "Client", event, on_closed=None, on_snoozed=None):
        width = self.MIN_WIDTH
        try:
            host = client.OVERLAYS
            if host is not None and host.width() > 0:
                width = max(self.MIN_WIDTH, int(host.width() * self.WIDTH_RATIO))
        except Exception:
            pass

        super().__init__(client, width=width, edge="right",
                         key=f"__reminder_{getattr(event, 'key', '')}",
                         destroy_on_close=True)
        self.event = event
        self.on_closed = on_closed
        self.on_snoozed = on_snoozed
        self.tint = QColor(event.colour or SOURCE_COLOURS.get(event.source, "#4f9de0"))

        body = QWidget()
        set_style(body, "common", "transparent")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(10)

        # Centred when there is nothing to fill the height with.
        #
        # An event with no location is a title, a time and four buttons; pinned
        # to the top of a full-height panel that is a paragraph of text with a
        # screen of nothing under it. A stretch on each side puts the whole
        # thing where the eye already is.
        if not event.location:
            layout.addStretch()

        layout.addLayout(self._header())
        layout.addLayout(self._details())

        if event.location:
            from .pickers import MapView
            self.map = MapView(self.MAP_H, client=client)
            layout.addWidget(self.map, stretch=1)
            self._load_map(event.location)
        else:
            self.map = None
            layout.addStretch()

        layout.addLayout(self._buttons())
        self.add_content(body)

    ## -- painting

    def paintEvent(self, event) -> None:
        # Over the frosted backdrop the panel already draws, so the event's
        # colour reads without losing the blur behind it.
        super().paintEvent(event)
        painter = QPainter(self)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, QColor(self.tint.red(), self.tint.green(),
                                        self.tint.blue(), 96))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 40))
        painter.fillRect(self.rect(), QBrush(gradient))
        painter.end()

    ## -- content

    def _header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(14)

        glyph = QLabel()
        try:
            glyph.setPixmap(icon(self.event.icon, color="#ffffff").pixmap(52, 52))
        except Exception:
            pass
        glyph.setFixedWidth(58)
        glyph.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(glyph)

        column = QVBoxLayout()
        column.setSpacing(2)

        title = QLabel(self.event.title)
        title.setFont(make_font(SIZES.L1, bold=True))
        title.setWordWrap(True)
        title.setStyleSheet("color: #ffffff; background: transparent;")
        add_text_shadow(title, blur=14)
        column.addWidget(title)

        api = self._api()
        when = QLabel(api["describe_gap"](self.event).capitalize() if api else "")
        when.setFont(make_font(SIZES.M1, bold=True))
        when.setStyleSheet("color: rgba(255,255,255,230); background: transparent;")
        add_text_shadow(when, blur=10)
        column.addWidget(when)

        row.addLayout(column, stretch=1)

        # Anything stuck to this event, beside the words rather than over
        # them. A label in the header rather than a painted corner: the panel
        # has buttons along its bottom, and painting into that corner would put
        # a picture under a control.
        sticker = self._sticker_label()
        if sticker is not None:
            row.addWidget(sticker, alignment=Qt.AlignmentFlag.AlignTop)
        return row

    #Big enough to recognise across a room, small enough that the event's name
    #is still the thing being read.
    STICKER_SIDE = 64

    def _sticker_label(self):
        """A label holding this event's sticker, or None when it has none."""
        from .sticker_layer import sticker_for_event, load_sticker

        name = sticker_for_event(self.client, self.event)
        if not name:
            return None
        pixmap = load_sticker(self.client, name)
        if pixmap is None:
            return None

        label = QLabel()
        label.setFixedSize(self.STICKER_SIDE, self.STICKER_SIDE)
        label.setStyleSheet("background: transparent;")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setPixmap(pixmap.scaled(
            self.STICKER_SIDE, self.STICKER_SIDE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        return label

    def _details(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(4)

        api = self._api()
        rows = [
            ("mdi.clock-outline",
             "All day" if self.event.all_day
             else self.event.time + (f" – {self.event.end_time}"
                                     if self.event.end_time else "")),
            ("mdi.timer-outline", api["describe_duration"](self.event) if api else ""),
            ("mdi.map-marker-outline", self.event.location),
            ("mdi.note-text-outline", self.event.notes),
        ]

        for glyph_name, value in rows:
            if not value:
                continue
            line = QHBoxLayout()
            line.setSpacing(10)

            mark = QLabel()
            try:
                mark.setPixmap(icon(glyph_name, color="#ffffff").pixmap(17, 17))
            except Exception:
                pass
            mark.setFixedWidth(20)
            mark.setAlignment(Qt.AlignmentFlag.AlignTop)
            line.addWidget(mark)

            text = QLabel(str(value))
            text.setFont(make_font(SIZES.S2))
            text.setWordWrap(True)
            text.setStyleSheet("color: rgba(255,255,255,225); background: transparent;")
            add_text_shadow(text, blur=8)
            line.addWidget(text, stretch=1)
            column.addLayout(line)
        return column

    def _buttons(self) -> QHBoxLayout:
        """
        The four actions, as icons with their word underneath.

        Four labelled buttons stretched across the panel gave each one a wide
        strip of colour with a small word floating in it - the text was the
        only part carrying meaning and the least visible thing in the row. An
        icon reads from across the room, which is the distance this panel is
        seen from, and the word underneath is there for the one time somebody
        has not met the icon before.
        """
        from src.ui.icons import Icons

        row = QHBoxLayout()
        row.setSpacing(12)
        row.setContentsMargins(0, 6, 0, 0)

        def _glyph(name: str):
            # icon(), which this module already uses for the header. The
            # parameter below is called `icon` and would shadow it, so the
            # lookup happens here where it does not.
            from src.ui.icons import icon as make
            return make(name, color="#e8ecf4")

        def button(icon: str, text: str, handler, kind: str = "secondary"):
            widget = QToolButton()
            widget.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            widget.setIcon(_glyph(icon))
            widget.setIconSize(QSize(30, 30))
            widget.setText(text)
            widget.setFont(make_font(SIZES.S1, bold=True))
            widget.setFixedHeight(78)
            widget.setSizePolicy(QSizePolicy.Policy.Expanding,
                                 QSizePolicy.Policy.Fixed)
            widget.setCursor(Qt.CursorShape.PointingHandCursor)
            set_style(widget, "overlays", f"dialog-button-{kind}")
            widget.clicked.connect(lambda: handler())
            return widget

        row.addWidget(button(Icons.OPEN_IN_NEW, "Open", self._open, "primary"))
        if self.event.editable:
            row.addWidget(button(Icons.PENCIL, "Edit", self._edit))
        row.addWidget(button(Icons.ALARM_SNOOZE, "Snooze", self._snooze))
        row.addWidget(button(Icons.CLOSE, "Dismiss", self._dismiss))
        return row

    ## -- actions

    def _api(self):
        try:
            return self.client.public.calendar
        except Exception:
            return None

    def _open(self) -> None:
        """Show the event where it lives, rather than only in this card."""
        self._dismiss()
        try:
            home = self.client.PAGES.get_entry("#cwb_home_page")
            page = getattr(home, "instance", None)
            calendar_page = (page.sub_page_dict.get("calendar")
                             if page is not None else None)
            if calendar_page is not None:
                page.jump_to_coord(tuple(calendar_page.coord))
                calendar_page.open_event(self.event.key)
        except Exception as e:
            self.client.log("warning", f"[Calendar] Could not open event: {e}")

    def _edit(self) -> None:
        from datetime import date as _date
        from .event_editor import EventEditorDialog
        self._dismiss()
        try:
            day = _date.fromisoformat(self.event.day)
        except (ValueError, TypeError):
            day = None
        self.client.dialog(EventEditorDialog(self.client, day=day, event=self.event))

    def _snooze(self) -> None:
        """Put it off rather than dismissing it - a reminder you cannot act on
        yet is not one you are finished with."""
        if callable(self.on_snoozed):
            try:
                self.on_snoozed(self.event)
            except Exception:
                pass
        self.close_panel()

    def _dismiss(self) -> None:
        if callable(self.on_closed):
            try:
                self.on_closed(self.event)
            except Exception:
                pass
        self.close_panel()

    ## -- map

    def _load_map(self, location: str) -> None:
        def work():
            point = None
            try:
                query = urllib.parse.urlencode(
                    {"q": location, "format": "json", "limit": 1})
                request = urllib.request.Request(
                    f"https://nominatim.openstreetmap.org/search?{query}",
                    headers={"User-Agent": "DesktopHomeAssistant"})
                with urllib.request.urlopen(request, timeout=8) as response:
                    found = json.loads(response.read().decode())
                if found:
                    point = (found[0]["lat"], found[0]["lon"])
            except Exception as e:
                self.client.log("debug", f"[Calendar] Reminder map failed: {e}")

            def apply():
                try:
                    if point is None:
                        self.map.set_message(location)
                    else:
                        self.map.show_point(point[0], point[1], self.client, zoom=17)
                except RuntimeError:
                    pass      # dismissed while the request was in flight
            self.client.call_on_ui(apply)

        Thread(target=work, name="__reminder_map", daemon=True).start()


class ReminderWatcher:
    """
    Decides when a reminder is due, and makes sure it is shown exactly once.

    Runs off the client tick rather than a timer of its own - the tick is
    already there, and one more repeating timer is one more thing to stop on
    unload.
    """

    def __init__(self, plugin):
        self.plugin = plugin
        self.client = plugin.client
        self.shown: set = set()
        self.panel = None
        self._timeout_id = "__calendar_reminder"
        self._complained = ""
        self._opening = False       # a panel is being built on the UI thread
        self._snoozed_until: dict = {}

    ## -- lifecycle

    def start(self) -> None:
        self.client.subscribe_to_event("on_update", self.check)

    def stop(self) -> None:
        try:
            self.client.unsubscribe_from_event("on_update", self.check)
        except Exception:
            pass
        self.dismiss()

    ## -- the check

    def check(self, event=None) -> None:
        try:
            if self.plugin.store is None:
                return
            if not self.plugin.option("reminders.enabled", True):
                return
            self._complained = ""
            if self.panel is not None or self._opening:
                # _opening as well as panel: the panel is now built on the UI
                # thread, so there is a gap between deciding to show one and it
                # existing. check() runs on every client tick, and without this
                # it would pick the next candidate and queue that one too.
                return

            lead = int(self.plugin.option("reminders.lead_minutes", 15))
            upcoming = self.plugin.store.upcoming(6)
            now = datetime.now()

            for candidate in upcoming:
                if self._snoozed_until.get(candidate.key, 0) > time.time():
                    continue
                if candidate.all_day:
                    # All-day events have no moment to be reminded about, and
                    # a reminder for one would fire at midnight.
                    continue
                start = candidate.starts_at
                if start is None:
                    continue

                minutes = (start - now).total_seconds() / 60

                # Two reminders, not one.
                #
                # A single one at `lead` minutes before told somebody an event
                # was coming and then nothing happened when it arrived -
                # `shown` held the key, so the moment the event actually
                # started passed in silence. The warning and the event are
                # different things to be told.
                if -self.START_GRACE <= minutes <= self.START_WINDOW:
                    if (candidate.key, "start") not in self.shown:
                        self.show(candidate, kind="start")
                        return
                elif 0 < minutes <= lead:
                    if (candidate.key, "lead") not in self.shown:
                        self.show(candidate, kind="lead")
                        return
        except Exception as e:
            # Once per distinct fault. This runs on the client tick, so a
            # persistent failure otherwise writes a line every frame and
            # buries everything else in the log.
            message = str(e)
            if message != self._complained:
                self._complained = message
                self.client.log("warning", f"[Calendar] Reminder check failed: {e}")

    #How wide the "it is starting" window is, in minutes.
    #
    #Not a single instant: check() runs on the client tick, and an event whose
    #start falls between two ticks would be missed entirely. A little either
    #side means it cannot be, and the panel is the same panel whether it opens
    #at the minute or a few seconds after it.
    START_WINDOW = 0.5
    START_GRACE = 2.0

    def show(self, event, kind: str = "lead") -> None:
        # A different sound for a different thing.
        #
        # The warning and the event are not the same news: one is "get ready"
        # and the other is "now", and a panel that makes the same noise for
        # both has said nothing the second time.
        try:
            self.client.AUDIO.play(
                "event_now" if kind == "start" else "notify")
        except Exception as e:
            self.client.log("debug", f"[Calendar] Reminder sound: {e}")

        self.shown.add((event.key, kind))
        # The lead reminder is not worth showing once the event has started.
        if kind == "start":
            self.shown.add((event.key, "lead"))
        self._opening = True

        # Built on the UI thread, not merely opened there. check() runs on
        # on_update, which is a background thread - so constructing the panel
        # here gave every QObject inside it (its position animation, and the
        # map's web view) background-thread affinity. open_panel() then called
        # _anim.start() from the UI thread against an object owned by another,
        # which Qt refuses with
        #     QObject::startTimer: Timers cannot be started from another thread
        # and which took the process down with SIGTRAP.
        def build_and_open():
            try:
                self.panel = ReminderPanel(self.client, event,
                                           on_closed=self._closed,
                                           on_snoozed=self._snoozed)
                self.panel.open_panel()
            except Exception as e:
                self.panel = None
                self.client.log("warning",
                                f"[Calendar] Could not show reminder: {e}",
                                include_traceback=True)
            finally:
                self._opening = False

        self.client.call_on_ui(build_and_open)

        seconds = int(self.plugin.option("reminders.dismiss_seconds", 45))
        if seconds > 0:
            # Closes itself, because a panel covering half the screen until
            # somebody walks past it is worse than no reminder.
            # idle: a reminder should not dismiss itself while somebody has
            # a dialog open over it.
            self.client.TIMEOUTS.add(seconds, self.dismiss, self._timeout_id,
                                     idle=True)
            self.client.TIMEOUTS.start(self._timeout_id)

    def dismiss(self, event=None) -> None:
        try:
            self.client.TIMEOUTS.cancel(self._timeout_id)
        except Exception:
            pass
        self._opening = False
        panel, self.panel = self.panel, None
        if panel is None:
            return

        # Marshalled, because stop() reaches this from the plugin unload
        # thread as well as the timeout reaching it from the UI thread.
        def close():
            try:
                panel.close_panel()
            except RuntimeError:
                pass
        self.client.call_on_ui(close)

    def _snoozed(self, event) -> None:
        """Forget that it was shown, and hold it off for a while."""
        self.panel = None
        self._opening = False
        self.shown.discard((event.key, "lead"))
        self.shown.discard((event.key, "start"))
        minutes = int(self.plugin.option("reminders.snooze_minutes", 5))
        self._snoozed_until[event.key] = time.time() + max(1, minutes) * 60

    def _closed(self, event) -> None:
        self.panel = None
        self._opening = False
