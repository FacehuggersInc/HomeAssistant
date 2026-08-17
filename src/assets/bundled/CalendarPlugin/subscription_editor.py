from __future__ import annotations
from typing import TYPE_CHECKING, Callable

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt

from src.styling import make_font, SIZES, set_style

from .dialogs import _WideDialog
from .event_editor import _Field

if TYPE_CHECKING:
    from src.main import Client


class SubscriptionEditorDialog(_WideDialog):
    """Three fields and a warning about what the address is."""

    WIDTH_RATIO  = 0.6
    HEIGHT_RATIO = 0.0

    def __init__(self, client: "Client", on_saved: Callable = None):
        super().__init__(client, "Add a calendar",
                         "Paste the ICS address of a calendar to mirror.")
        self.on_saved = on_saved

        body = QVBoxLayout()
        body.setSpacing(10)

        from .event_editor import _OwnerPicker
        self.owner_field = _OwnerPicker(client)
        self.name_field  = _Field(client, "Name", placeholder="Work, Family, Bins",
                                  editor=self)
        self.url_field   = _Field(client, "Address",
                                  placeholder="https://... or webcal://...",
                                  editor=self)
        for field in (self.owner_field, self.name_field, self.url_field):
            body.addWidget(field)

        hint = QLabel(
            "Google: Settings for that calendar, then the secret address in "
            "iCal format. Apple and Outlook: publish the calendar and copy the "
            "link.\n\nTreat it as a password - anyone holding it can read that "
            "calendar. Google caches the feed for hours, so a change made on a "
            "phone will not appear here straight away."
        )
        hint.setFont(make_font(SIZES.S1))
        hint.setWordWrap(True)
        set_style(hint, "common", "text-muted")
        body.addWidget(hint)

        holder = QWidget()
        set_style(holder, "common", "transparent")
        holder.setLayout(body)
        self.content.addWidget(holder)

        self.error = QLabel("")
        self.error.setFont(make_font(SIZES.S2, bold=True))
        self.error.setWordWrap(True)
        self.error.setStyleSheet(
            "color:#f0a0a0;background:rgba(176,52,52,60);"
            "border:1px solid rgba(224,138,138,120);border-radius:8px;padding:8px 12px;")
        self.error.hide()
        self.content.addWidget(self.error)

        self.add_button("Subscribe", self._save, "primary")
        self.add_button("Cancel", self.close, "secondary")

    def on_field_changed(self) -> None:
        self.error.hide()

    def _save(self) -> None:
        url = self.url_field.value()
        owner = self.owner_field.chosen

        if not url:
            self._complain("Paste the calendar's address.")
            return
        if not (url.startswith("http://") or url.startswith("https://")
                or url.startswith("webcal://")):
            # Checked here rather than at fetch time: a typo is easier to fix
            # while the person is still looking at the field.
            self._complain("That does not look like a calendar address.")
            return
        if not owner:
            self._complain("Nobody is named yet - approve a device first, or "
                           "name one under Settings, Users.")
            return

        try:
            api = self.client.public.calendar
            # Owner passed in, not set afterwards: reaching back for "the one
            # I just added" assumes the list order, and it is the key the
            # events are built from.
            api["add_subscription"](url, self.name_field.value(), "", owner)
            api["sync_subscriptions"]()
        except Exception as e:
            self.client.log("warning", f"[Calendar] Could not subscribe: {e}")
            self._complain("Could not add that calendar.")
            return

        self.client.simple_notify("mdi.calendar-sync", "Calendar",
                                  "Subscribed. Fetching it now.")
        if callable(self.on_saved):
            try:
                self.on_saved()
            except Exception:
                pass
        self.close()

    def _complain(self, message: str) -> None:
        self.error.setText(message)
        self.error.show()


def subscription_row(client: "Client", feed, on_changed: Callable = None) -> QWidget:
    """
    One subscribed calendar, with everything you can do to it.

    Shared, because this list appeared in two places - the settings section and
    the dialog on the calendar page - and the two had drifted to the point that
    one offered four actions and the other only Remove. Which controls you got
    depended on where you happened to open it from.
    """
    import datetime as _dt
    from PyQt6.QtWidgets import QFrame, QHBoxLayout, QPushButton

    def api():
        try:
            return client.public.calendar
        except Exception:
            return None

    def changed():
        try:
            client.trigger_on_call_event_iteration("on_calendar_changed", None)
        except Exception:
            pass
        if callable(on_changed):
            on_changed()

    card = QFrame()
    card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    set_style(card, "settings", "setting-block")

    row = QHBoxLayout(card)
    row.setContentsMargins(14, 10, 14, 10)
    row.setSpacing(10)

    column = QVBoxLayout()
    column.setSpacing(1)

    name = QLabel(feed.name)
    name.setFont(make_font(SIZES.S2, bold=True))
    set_style(name, "common", "text-strong")
    column.addWidget(name)

    # Whose it is, then how it is doing. An error replaces the count rather
    # than sitting beside it - a stale count next to a failure reads as though
    # the failure did not matter.
    if feed.last_error:
        detail = f"{feed.owner or 'unassigned'} · last sync failed: {feed.last_error}"
    elif feed.last_sync:
        when = _dt.datetime.fromtimestamp(feed.last_sync).strftime("%d %b, %H:%M")
        count = 0
        try:
            store = client.public.calendar["store"]
            count = sum(1 for e in store.snapshot()
                        if e.subscription == feed.key)
        except Exception:
            pass
        detail = f"{feed.owner or 'unassigned'} · {count} event(s) · synced {when}"
    else:
        detail = f"{feed.owner or 'unassigned'} · not synced yet"

    info = QLabel(detail)
    info.setFont(make_font(SIZES.S1))
    info.setWordWrap(True)
    set_style(info, "common", "text-muted")
    column.addWidget(info)
    row.addLayout(column, stretch=1)

    def button(label: str, handler: Callable, kind: str = "secondary"):
        widget = QPushButton(label)
        widget.setFont(make_font(SIZES.S1, bold=True))
        widget.setFixedHeight(38)
        widget.setCursor(Qt.CursorShape.PointingHandCursor)
        set_style(widget, "overlays", f"dialog-button-{kind}")
        widget.clicked.connect(lambda: handler())
        return widget

    def sync():
        service = api()
        if service:
            service["sync_subscriptions"]()
        client.simple_notify("mdi.calendar-sync", "Calendar",
                             f"Fetching '{feed.name}'.")

    def tidy():
        service = api()
        removed = service["deduplicate"]() if service else 0
        changed()
        client.simple_notify(
            "mdi.broom", "Calendar",
            f"Removed {removed} duplicate event(s)." if removed
            else "No duplicates found.")

    def reset():
        client.confirm(
            "Re-sync from scratch",
            f"Clear every event from '{feed.name}' and fetch them again?",
            on_confirm   = lambda: (api() or {}).get(
                "reset_subscriptions", lambda *_: None)(feed.key),
            confirm_text = "Re-sync",
            cancel_text  = "Cancel",
            detail       = ("Nothing added on the panel is touched - only what "
                            "this calendar put here."),
        )

    def remove():
        def go():
            service = api()
            if service:
                service["remove_subscription"](feed.key)
            changed()

        client.confirm(
            "Stop syncing",
            f"Remove '{feed.name}' and delete its events?",
            on_confirm   = go,
            confirm_text = "Remove",
            cancel_text  = "Keep",
            destructive  = True,
        )

    row.addWidget(button("Sync", sync))
    row.addWidget(button("Tidy", tidy))
    row.addWidget(button("Reset", reset))
    row.addWidget(button("Remove", remove, "destructive"))
    return card
