from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date
from threading import Thread
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QScrollArea, QSizePolicy,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap

from src.ui.overlays import BaseDialog


class _WideDialog(BaseDialog):
    """
    A dialog sized to the screen rather than to its content.

    The default dialog is a card that shrinks to what is in it, which suits a
    question. A calendar dialog is the opposite: it exists to show as much as
    it can, and a day with nine events on it should not be a scrollbar in a
    600px box.
    """

    WIDTH_RATIO  = 0.86
    HEIGHT_RATIO = 0.86
    MIN_WIDTH    = 520

    def __init__(self, client, title: str = "", body: str = "", detail: str = None):
        host = getattr(client, "OVERLAYS", None)
        width = self.WIDTH
        try:
            if host is not None and host.width() > 0:
                width = max(self.MIN_WIDTH, int(host.width() * self.WIDTH_RATIO))
                if self.HEIGHT_RATIO > 0:
                    self.MAX_HEIGHT = max(360, int(host.height() * self.HEIGHT_RATIO))
        except Exception:
            pass
        super().__init__(client, title, body, width=width, detail=detail)

        # A minimum as well as a maximum. BaseDialog.center() shrinks to the
        # size hint, so asking only for a larger cap gave a dialog that still
        # collapsed around whatever little content it had.
        if self.HEIGHT_RATIO > 0:
            self.expand_content()

        try:
            # A ratio of zero means "shrink to content" - some of these are
            # pickers, and a fixed tall dialog around two steppers looks broken.
            if host is not None and host.height() > 0 and self.HEIGHT_RATIO > 0:
                self.setMinimumHeight(max(360, int(host.height() * self.HEIGHT_RATIO)))
        except Exception:
            pass
from src.ui.controls.buttons import IconButton
from src.ui.icons import icon
from src.styling import make_font, SIZES, set_style

if TYPE_CHECKING:
    from src.main import Client


SOURCE_LABELS = {"local": "Added here", "imported": "Added remotely",
                 "holiday": "Holiday", "subscribed": "Subscribed calendar"}


def display_notes(event) -> str:
    """
    A subscribed event carries its feed key in the notes.

    That is storage, not something to read - stripped here rather than given
    its own field on every event in the store.
    """
    notes = event.notes or ""
    if event.source == "subscribed" and notes.startswith("["):
        _, _, rest = notes.partition("] ")
        return rest.strip() or notes.partition("]")[2].strip()
    return notes
SOURCE_COLOURS = {"local": "#4f9de0", "imported": "#a97fe0", "holiday": "#d8a24a"}


class EventRow(QFrame):
    """One event in the day list. Tapping it opens the full view."""

    def __init__(self, client: "Client", event, on_open,
                 on_remove=None, on_edit=None):
        super().__init__()
        self.client = client
        self.event  = event
        self.on_open = on_open

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(self, "settings", "setting-block")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(12)

        colour = event.colour or SOURCE_COLOURS.get(event.source, "#4f9de0")
        glyph = QLabel()
        try:
            glyph.setPixmap(icon(event.icon, color=colour).pixmap(26, 26))
        except Exception:
            pass
        glyph.setFixedWidth(30)
        row.addWidget(glyph)

        text = QVBoxLayout()
        text.setSpacing(1)

        title = QLabel(event.title)
        title.setFont(make_font(SIZES.S2, bold=True))
        set_style(title, "common", "text-strong")
        text.addWidget(title)

        parts = ["All day" if event.all_day else event.time]
        if event.end_time:
            parts.append(f"– {event.end_time}")
        if event.location:
            parts.append(f"· {event.location}")
        detail = QLabel("  ".join(p for p in parts if p))
        detail.setFont(make_font(SIZES.S1))
        set_style(detail, "common", "text-muted")
        text.addWidget(detail)

        row.addLayout(text, stretch=1)

        # Holidays have no delete button rather than a disabled one - there is
        # nothing to remove, they are computed.
        if event.editable and event.source != "subscribed":
            if on_edit is not None:
                row.addWidget(IconButton("mdi.pencil-outline",
                                         lambda: on_edit(event), size=18))
            if on_remove is not None:
                row.addWidget(IconButton("mdi.trash-can-outline",
                                         lambda: on_remove(event), size=18))

    def mouseReleaseEvent(self, event) -> None:
        self.on_open(self.event)


class DayViewDialog(_WideDialog):
    """Everything on one day, with add and remove."""

    WIDTH = 760

    def __init__(self, client: "Client", day: date):
        super().__init__(client, day.strftime("%A"), day.strftime("%d %B %Y"))
        self.day = day

        self.list_host = QWidget()
        set_style(self.list_host, "common", "transparent")
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # No fixed cap. The dialog is already bounded by the screen, and a
        # second limit inside it just wastes the room the dialog asked for.
        scroll.setMinimumHeight(240)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        set_style(scroll, "common", "transparent")
        scroll.setWidget(self.list_host)
        self.content.addWidget(scroll, stretch=1)

        self.add_button("Add event", self._add, "primary")
        self.add_button("Close", self.close, "secondary")
        self.refresh()

    def _calendar(self):
        try:
            return self.client.public.calendar
        except Exception:
            return None

    def refresh(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        api = self._calendar()
        events = []
        if api is not None:
            try:
                events = api["on_day"](self.day)
            except Exception as e:
                self.client.log("warning", f"[Calendar] Could not read day: {e}")

        if not events:
            empty = QLabel("Nothing on this day.")
            empty.setFont(make_font(SIZES.S2))
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            set_style(empty, "common", "text-muted")
            self.list_layout.addWidget(empty)
            return

        for event in events:
            self.list_layout.addWidget(
                EventRow(self.client, event, self._open,
                         self._confirm_remove, self._edit))
        self.list_layout.addStretch()

    def _open(self, event) -> None:
        self.client.dialog(EventViewDialog(self.client, event.key))

    def _confirm_remove(self, event) -> None:
        # Confirmed, because a mis-tap here loses something the user typed and
        # there is no undo behind it.
        self.client.confirm(
            "Remove event",
            f"Remove '{event.title}'?",
            on_confirm   = lambda: self._remove(event),
            confirm_text = "Remove",
            cancel_text  = "Keep",
            destructive  = True,
        )

    def _remove(self, event) -> None:
        api = self._calendar()
        if api is None:
            return
        try:
            # remove_event announces the change itself now, so firing here
            # too would refresh everything twice for one deletion.
            api["remove_event"](event.key)
        except Exception as e:
            self.client.log("warning", f"[Calendar] Could not remove: {e}")
        self.refresh()

    def _edit(self, event) -> None:
        from .event_editor import EventEditorDialog
        self.client.dialog(EventEditorDialog(self.client, day=self.day,
                                             on_saved=self.refresh, event=event))

    def _add(self) -> None:
        from .event_editor import EventEditorDialog
        self.client.dialog(EventEditorDialog(self.client, day=self.day,
                                             on_saved=self.refresh))


class SubscriptionsDialog(_WideDialog):
    """
    Subscribed calendars, on the panel.

    The same job the phone page does, for the times somebody is standing at
    the panel with the URL already on the screen beside them.
    """

    WIDTH_RATIO  = 0.62
    HEIGHT_RATIO = 0.7

    def __init__(self, client: "Client"):
        super().__init__(client, "Subscribed calendars",
                         "Mirrored onto this panel, one way. Nothing is sent back.")

        self.list_host = QWidget()
        set_style(self.list_host, "common", "transparent")
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        set_style(scroll, "common", "transparent")
        scroll.setWidget(self.list_host)
        self.content.addWidget(scroll, stretch=1)

        self.add_button("Add a calendar", self._add, "primary")
        self.add_button("Sync now", self._sync, "secondary")
        self.add_button("Close", self.close, "secondary")
        self.refresh()

    def _api(self):
        try:
            return self.client.public.calendar
        except Exception:
            return None

    def refresh(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        api = self._api()
        feeds = api["subscriptions"]() if api else []

        if not feeds:
            empty = QLabel("Nothing subscribed yet.\n\n"
                           "Google: Settings for that calendar, then the secret "
                           "address in iCal format. Apple and Outlook: publish "
                           "the calendar and copy the link.")
            empty.setFont(make_font(SIZES.S2))
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            set_style(empty, "common", "text-muted")
            self.list_layout.addWidget(empty)
            return

        from .subscription_editor import subscription_row
        for feed in feeds:
            self.list_layout.addWidget(
                subscription_row(self.client, feed, on_changed=self.refresh))
        self.list_layout.addStretch()

    def _add(self) -> None:
        from .subscription_editor import SubscriptionEditorDialog
        self.client.dialog(SubscriptionEditorDialog(self.client,
                                                    on_saved=self.refresh))

    def _sync(self) -> None:
        api = self._api()
        if api is None:
            return
        api["sync_subscriptions"]()
        self.client.simple_notify("mdi.calendar-sync", "Calendar",
                                  "Refreshing subscribed calendars.")
        # Queued: the sync is on a thread, and redrawing immediately would
        # show the same "last synced" it already had.
        self.client.TIMEOUTS.add(6, self.refresh, "calendar_subs_refresh")
        self.client.TIMEOUTS.start("calendar_subs_refresh")

    def _confirm_remove(self, feed) -> None:
        self.client.confirm(
            "Remove calendar",
            f"Stop mirroring '{feed.name}'?",
            detail="Its events are removed from this panel. The calendar "
                   "itself is not touched.",
            on_confirm=lambda: self._remove(feed),
            confirm_text="Remove", cancel_text="Keep", destructive=True,
        )

    def _remove(self, feed) -> None:
        api = self._api()
        if api is not None:
            api["remove_subscription"](feed.key)
            self.client.trigger_on_call_event_iteration("on_calendar_changed", None)
        self.refresh()


class EventViewDialog(_WideDialog):
    """
    One event in full, with a map when it has somewhere to be.

    Separate from the day view on purpose: the day is a list you scan, and this
    is the thing you actually read.
    """

    WIDTH = 720
    MAP_H = 460

    def __init__(self, client: "Client", event_key: str):
        self.event_key = event_key
        event = None
        try:
            event = client.public.calendar["get_event"](event_key)
        except Exception:
            pass

        if event is None:
            super().__init__(client, "Event", "That event no longer exists.")
            self.add_button("Close", self.close, "primary")
            return

        api = client.public.calendar
        when = api["describe_gap"](event)
        super().__init__(client, event.title, when)
        self.event = event

        body = QVBoxLayout()
        body.setSpacing(6)

        for label, value in (
            ("When",     event.day if event.all_day
                         else f"{event.day}  {event.time}"
                              + (f" – {event.end_time}" if event.end_time else "")),
            ("Length",   api["describe_duration"](event)),
            ("Where",    event.location),
            ("Source",   SOURCE_LABELS.get(event.source, event.source)),
            # Holidays belong to everybody, so there is nobody to name.
            ("For",      "" if event.source == "holiday" else (event.owner or "")),
            ("Repeats",  {"daily": "Every day", "weekly": "Every week",
                          "monthly": "Every month", "yearly": "Every year"}
                         .get(event.repeat, "")),
        ):
            if not value:
                continue
            line = QHBoxLayout()
            key = QLabel(label)
            key.setFont(make_font(SIZES.S1))
            key.setFixedWidth(80)
            set_style(key, "common", "text-muted")
            val = QLabel(str(value))
            val.setFont(make_font(SIZES.S2))
            val.setWordWrap(True)
            set_style(val, "common", "text-strong")
            line.addWidget(key)
            line.addWidget(val, stretch=1)
            body.addLayout(line)

        readable = display_notes(event)
        if readable:
            notes = QLabel(readable)
            notes.setFont(make_font(SIZES.S2))
            notes.setWordWrap(True)
            set_style(notes, "common", "text-muted")
            body.addWidget(notes)

        holder = QWidget()
        set_style(holder, "common", "transparent")
        holder.setLayout(body)
        # No stretch on the detail block, so every spare pixel goes to the map
        # rather than being shared with a handful of one-line rows.
        holder.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.content.addWidget(holder)

        if event.location:
            # The same MapView the picker uses. This dialog kept its own copy
            # of the old static-tile code, which is why it showed nothing at
            # all once the picker moved on.
            from .pickers import MapView
            self.map = MapView(self.MAP_H, client=client)
            self.content.addWidget(self.map, stretch=1)
            self._load_map(event.location)
        else:
            self.content.addStretch()

        # Holidays are computed, so there is nothing to edit - no button
        # rather than a disabled one.
        # A subscribed event is owned by its feed - an edit here would be
        # undone by the next sync. Said rather than left as a missing button,
        # because a button that is simply absent looks like a bug.
        if event.source == "subscribed":
            note = QLabel("This comes from a subscribed calendar, so it is "
                          "read-only here. Change it where it lives and it "
                          "will update on the next sync.")
            note.setFont(make_font(SIZES.S1))
            note.setWordWrap(True)
            set_style(note, "common", "text-muted")
            self.content.addWidget(note)
        elif event.editable:
            self.add_button("Edit", self._edit, "primary")
        elif event.source == "subscribed":
            # Said, not silently absent. A missing Edit button is a question;
            # a sentence saying where the event comes from is an answer.
            note = QLabel("Kept in sync with a subscribed calendar, so it "
                          "cannot be changed here.")
            note.setFont(make_font(SIZES.S1))
            note.setWordWrap(True)
            set_style(note, "common", "text-muted")
            self.content.addWidget(note)
        self.add_button("Close", self.close, "primary")

    def _edit(self) -> None:
        from datetime import date as _date
        from .event_editor import EventEditorDialog
        try:
            day = _date.fromisoformat(self.event.day)
        except (ValueError, TypeError):
            day = None
        self.close()
        self.client.dialog(EventEditorDialog(self.client, day=day,
                                             event=self.event))

    ## -- map

    def _load_map(self, location: str) -> None:
        """Geocode the saved address, then hand the point to the map."""
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
                self.client.log("debug", f"[Calendar] No map for '{location}': {e}")

            def apply():
                try:
                    if point is None:
                        self.map.set_message(location)
                        return
                    # Closer than the picker's default. The picker is choosing
                    # between places and wants their surroundings; this is
                    # showing one address that has already been chosen.
                    self.map.show_point(point[0], point[1], self.client, zoom=18)
                except RuntimeError:
                    pass      # dialog closed while the request was in flight
            self.client.call_on_ui(apply)

        Thread(target=work, name="__calendar_map", daemon=True).start()
