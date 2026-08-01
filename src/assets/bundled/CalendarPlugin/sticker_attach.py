"""
Asking whether a freshly placed sticker belongs to an event.

Asked after it has been dropped rather than before it is picked up: until it
has a day, there is no list of events to offer, and a question about "which
event" with nothing to choose from is a question that has to be asked twice.

Answering yes locks the sticker to the event's day. What that means depends on
the event, and the rules live in stickers.py:

| The event | The sticker |
|---|---|
| repeats yearly and never stops | comes back on that day every year |
| runs across several days | sits on the last of them |
| anything else | follows the event, wherever it moves to |
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.ui.dialogs import ActionSheet
from src.ui.grid_dialog import ItemGridDialog, GridItem

if TYPE_CHECKING:
    from src.main import Client


def ask_to_attach(client: "Client", page, key: str) -> None:
    """Offer to tie a sticker to something on its day."""
    store = page._sticker_store()
    sticker = store.get(key)
    if sticker is None:
        return

    day = sticker.anchor_date(store.resolve_event)
    if day is None:
        return

    events = _events_on(page, day)
    if not events:
        # Nothing to attach it to. Said rather than asked: an empty chooser is
        # a question with no answers.
        client.simple_notify(
            "mdi.sticker-emoji", "Sticker",
            f"Stuck to {day.strftime('%-d %B')}.", history=False)
        return

    client.dialog(ActionSheet(
        client,
        f"Stuck to {day.strftime('%-d %B')}",
        [
            ("Attach it to an event", lambda: _choose_event(client, page, key, day),
             "mdi.calendar-star", "primary"),
            ("Leave it on the day", lambda: None, "mdi.calendar-blank"),
        ],
    ))


def _events_on(page, day) -> list:
    api = page._calendar()
    if api is None:
        return []
    try:
        return list(api["on_day"](day, True))
    except Exception:
        return []


def _choose_event(client: "Client", page, key: str, day) -> None:
    """The day's events, to pick one for the sticker to follow."""
    store = page._sticker_store()
    events = _events_on(page, day)
    if not events:
        return

    items = []
    for entry in events:
        items.append(GridItem(
            key=getattr(entry, "key", "") or getattr(entry, "title", ""),
            label=getattr(entry, "title", "") or "Event",
            subtitle=_describes(entry),
            icon=getattr(entry, "icon", "") or "mdi.calendar",
            kind="event",
            data=entry,
        ))

    def chosen(item) -> None:
        entry = getattr(item, "data", None)
        if entry is None:
            return
        stuck = store.attach_to_event(key, entry)
        page.stickers.refresh()
        if stuck is not None:
            client.simple_notify("mdi.sticker-emoji", "Sticker",
                                 _confirms(stuck, entry), history=False)

    client.dialog(ItemGridDialog(
        client,
        title="Which event?",
        body="The sticker follows it, and stays on its day.",
        items=items,
        on_chosen=chosen,
        choose_text="Attach",
        empty_text="Nothing on this day.",
        search_hint="Search this day",
    ))


def _describes(entry) -> str:
    """A short line saying what kind of event this is."""
    repeat = str(getattr(entry, "repeat", "") or "").lower()
    until = str(getattr(entry, "repeat_until", "") or "").strip()
    spans = bool(getattr(entry, "spans_days", False))

    if repeat == "yearly" and not until:
        return "Every year"
    if repeat and not until:
        return f"Repeats {repeat}"
    if repeat:
        return f"Repeats {repeat}, until {until}"
    if spans:
        return "Runs across several days"
    return str(getattr(entry, "time", "") or "All day")


def _confirms(sticker, entry) -> str:
    title = getattr(entry, "title", "") or "that event"
    from .stickers import BY_YEAR
    if sticker.kind == BY_YEAR:
        return f"It comes back on {title} every year."
    return f"It follows {title}."
