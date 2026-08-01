"""
Stickers stuck to the calendar.

A sticker sits inside a day box rather than on the page, so it comes back on
the same day when the month is paged away and back, and after a restart. What
"the same day" means is the anchor, and there are three kinds:

| Anchor | Comes back on |
|---|---|
| `date` | one date, and only that one |
| `yearly` | that month and day, every year |
| `event` | wherever the event is, and on its last day when it spans |

Kept in the calendar's own file rather than in `widget_layout.json`: this is
calendar data keyed to a day or an event, not a record of where widgets sit on
the home screen, and it has to survive independently of either.

Nothing here touches Qt. The rules are the part worth testing, and a store that
needs a running application to answer a question about a date is a store nobody
tests.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Callable, Optional

#Anchor kinds.
BY_DATE = "date"
BY_YEAR = "yearly"
BY_EVENT = "event"
#A holiday, which is computed per year rather than stored. Its key carries the
#year - `holiday:2026:labor-day` - so following the key would follow one year
#only. And many of them move: Labor Day is the first Monday in September, so a
#month-and-day anchor lands a day or two out. The slug is what stays the same.
BY_HOLIDAY = "holiday"

#Where a sticker sits inside its day box, and how big, as fractions of the box.
#Fractions rather than pixels so a sticker two thirds across a Tuesday is two
#thirds across it on any screen, in any month, however many weeks that month
#happens to need.
DEFAULT_X = 0.5
DEFAULT_Y = 0.5
DEFAULT_SCALE = 0.45

#A sticker may not be dragged so far that it stops reading as belonging to its
#day. These are the centre's limits inside the box.
MIN_POS, MAX_POS = 0.08, 0.92
MIN_SCALE, MAX_SCALE = 0.12, 0.95
#How far it may be turned. Enough to look placed by hand rather than printed;
#a sticker upside down on a calendar is a sticker nobody can read.
MAX_ANGLE = 35.0


@dataclass
class Sticker:
    """One sticker, its picture, and what it is stuck to."""

    image: str                              # a filename in the sticker library
    kind:  str = BY_DATE
    day:   str = ""                         # "YYYY-MM-DD", for BY_DATE
    month: int = 0                          # for BY_YEAR
    date_of_month: int = 0                  # for BY_YEAR
    event: str = ""                         # event key, for BY_EVENT
    # Where an event-anchored sticker goes when its event no longer exists.
    # Without it, deleting an event silently deletes the sticker with it.
    fallback: str = ""

    x: float = DEFAULT_X
    y: float = DEFAULT_Y
    scale: float = DEFAULT_SCALE
    #Degrees, clockwise. A sticker put on by hand is rarely square to the page,
    #and one that cannot be turned reads as printed rather than stuck on.
    angle: float = 0.0

    # Whether this sticker is settled in its box.
    #
    # True from the moment it is put down. A sticker belongs to a day, and
    # dragging it across to another one turns a decision somebody made into an
    # accident of where their finger stopped. Moving it inside its own box is
    # always allowed; leaving the box needs the unlock control, which says so.
    held: bool = True

    key: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    ## -- geometry

    def clamped(self) -> "Sticker":
        """This sticker with its position and size brought inside the limits."""
        self.x = max(MIN_POS, min(MAX_POS, float(self.x)))
        self.y = max(MIN_POS, min(MAX_POS, float(self.y)))
        self.scale = max(MIN_SCALE, min(MAX_SCALE, float(self.scale)))
        self.angle = max(-MAX_ANGLE, min(MAX_ANGLE, float(self.angle or 0.0)))
        return self

    ## -- locked to an event

    @property
    def attached(self) -> bool:
        """Whether this sticker is following an event rather than a date."""
        return bool(self.event) and self.kind in (BY_EVENT, BY_YEAR, BY_HOLIDAY)

    @property
    def locked(self) -> bool:
        """
        Whether this sticker may leave its day box.

        Held by default, and always held while it follows an event: the event
        decides which day that is, so dragging it elsewhere would be a claim
        the event contradicts on its next occurrence.

        It still moves freely inside the box it is in. Leaving the box takes
        the unlock control, which also detaches it from any event - the two are
        one act, which is why the control says one thing and does both.
        """
        return bool(self.held) or self.attached

    ## -- what it is stuck to

    def anchor_date(self, resolve_event: Callable = None) -> Optional[date]:
        """
        The one date this sticker names, or None when it names many.

        A yearly or holiday sticker has no single date - both answer
        `shows_on` instead.
        """
        if self.kind == BY_HOLIDAY:
            return None
        if self.kind == BY_DATE:
            return _parse(self.day)

        if self.kind == BY_EVENT:
            entry = resolve_event(self.event) if resolve_event else None
            if entry is not None:
                # The last day of a span. A sticker put on something that runs
                # from Monday to Friday belongs on the Friday: that is the day
                # the thing happens, and the four before it are the run-up.
                landing = getattr(entry, "last_date", None)
                if callable(landing):
                    landing = landing()
                if isinstance(landing, date):
                    return landing
                first = getattr(entry, "date", None)
                if isinstance(first, date):
                    return first
            return _parse(self.fallback)

        return None

    def shows_on(self, day: date, resolve_event: Callable = None,
                 resolve_holiday: Callable = None) -> bool:
        """Whether this sticker belongs on `day`."""
        if self.kind == BY_YEAR:
            return (day.month == int(self.month or 0)
                    and day.day == int(self.date_of_month or 0))
        if self.kind == BY_HOLIDAY:
            if resolve_holiday is None:
                return False
            when = resolve_holiday(self.event, day.year)
            return when == day
        anchored = self.anchor_date(resolve_event)
        return anchored is not None and anchored == day

    ## -- storage

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> Optional["Sticker"]:
        if not isinstance(raw, dict) or not raw.get("image"):
            return None
        allowed = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in raw.items() if k in allowed}
        try:
            return cls(**clean).clamped()
        except (TypeError, ValueError):
            return None


def _parse(text) -> Optional[date]:
    try:
        return date.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None


class StickerStore:
    """
    Every sticker on the calendar, and which day each one belongs to.

    `resolve_event` is handed in rather than imported: this store answers
    questions about dates, and the calendar's own store answers questions about
    events. Passing one into the other keeps this file free of both Qt and the
    event model.
    """

    def __init__(self, path, resolve_event: Callable = None,
                 resolve_holiday: Callable = None, log: Callable = None):
        from pathlib import Path
        self.path = Path(path)
        self.resolve_event = resolve_event
        #`(slug, year) -> date | None`. Holidays are computed per year, so
        #this is the only way to ask where one falls in a year nobody has
        #looked at yet.
        self.resolve_holiday = resolve_holiday
        self.log = log or (lambda *a, **k: None)
        self.stickers: dict = {}
        self.load()

    ## -- persistence

    def load(self) -> None:
        self.stickers = {}
        try:
            if not self.path.is_file():
                return
            raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError) as e:
            self.log("warning", f"[CalendarStickers] Could not read: {e}")
            return

        for entry in (raw.get("stickers") or []):
            sticker = Sticker.from_dict(entry)
            if sticker is not None:
                self.stickers[sticker.key] = sticker

    def save(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"stickers": [s.to_dict() for s in self.stickers.values()]}
            self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return True
        except OSError as e:
            self.log("warning", f"[CalendarStickers] Could not save: {e}")
            return False

    ## -- reading

    def all_stickers(self) -> list:
        return list(self.stickers.values())

    def get(self, key: str) -> Optional[Sticker]:
        return self.stickers.get(key)

    def on_day(self, day: date) -> list:
        """Every sticker belonging to one day, oldest first."""
        return [s for s in self.stickers.values()
                if s.shows_on(day, self.resolve_event, self.resolve_holiday)]

    def for_days(self, days) -> dict:
        """
        `{date: [sticker]}` for a whole month grid, in one pass.

        A month is forty-two boxes and a sticker answers for each of them, so
        the page asks once rather than forty-two times.
        """
        found = {}
        days = list(days)
        for sticker in self.stickers.values():
            if sticker.kind == BY_YEAR:
                month, number = int(sticker.month or 0), int(sticker.date_of_month or 0)
                for day in days:
                    if day.month == month and day.day == number:
                        found.setdefault(day, []).append(sticker)
                continue
            if sticker.kind == BY_HOLIDAY:
                # Asked once per year on screen rather than once per day: a
                # month grid spans two at most.
                for year in {day.year for day in days}:
                    when = (self.resolve_holiday(sticker.event, year)
                            if self.resolve_holiday else None)
                    if when in days:
                        found.setdefault(when, []).append(sticker)
                continue
            anchored = sticker.anchor_date(self.resolve_event)
            if anchored is not None and anchored in days:
                found.setdefault(anchored, []).append(sticker)
        return found

    def for_event(self, event_key: str) -> list:
        """
        Every sticker attached to one event.

        What the widgets and the reminder panel ask: they have an event and
        want to know whether anything is stuck to it. Both kinds of attachment
        answer - a yearly anchor came from an event too, and keeps its key.
        """
        if not event_key:
            return []
        return [s for s in self.stickers.values()
                if s.attached and s.event == event_key]

    ## -- writing

    def add(self, image: str, day: date, x: float = DEFAULT_X,
            y: float = DEFAULT_Y, scale: float = DEFAULT_SCALE,
            angle: float = 0.0, held: bool = True) -> Sticker:
        """Stick one to a single date. The plainest anchor, and the default."""
        sticker = Sticker(image=str(image), kind=BY_DATE, day=day.isoformat(),
                          x=x, y=y, scale=scale, angle=angle,
                          held=held).clamped()
        self.stickers[sticker.key] = sticker
        self.save()
        return sticker

    def attach_to_event(self, key: str, entry) -> Optional[Sticker]:
        """
        Point a sticker at an event instead of at a date.

        A series that repeats yearly with no end becomes a yearly anchor rather
        than an event one: the sticker is wanted on that day every year, and an
        anchor that follows the event would put it on whichever occurrence the
        store happened to answer with.
        """
        sticker = self.stickers.get(key)
        if sticker is None or entry is None:
            return None

        landing = getattr(entry, "last_date", None)
        if callable(landing):
            landing = landing()
        if not isinstance(landing, date):
            landing = getattr(entry, "date", None)

        slug = _holiday_slug(entry)
        if slug:
            # A holiday recurs by definition, and moves. Following its slug is
            # the only anchor that is right in both respects.
            sticker.kind = BY_HOLIDAY
            sticker.event = slug
            sticker.fallback = (landing.isoformat()
                                if isinstance(landing, date) else "")
        elif _is_forever_yearly(entry) and isinstance(landing, date):
            sticker.kind = BY_YEAR
            sticker.month = landing.month
            sticker.date_of_month = landing.day
            sticker.event = getattr(entry, "key", "") or ""
        else:
            sticker.kind = BY_EVENT
            sticker.event = getattr(entry, "key", "") or ""
            sticker.fallback = landing.isoformat() if isinstance(landing, date) else ""

        self.save()
        return sticker

    def detach(self, key: str) -> Optional[Sticker]:
        """Put a sticker back on a plain date, wherever it currently shows."""
        sticker = self.stickers.get(key)
        if sticker is None:
            return None
        landing = sticker.anchor_date(self.resolve_event)
        if landing is None and sticker.kind == BY_YEAR:
            today = date.today()
            landing = date(today.year, int(sticker.month or 1),
                           int(sticker.date_of_month or 1))
        if landing is None and sticker.kind == BY_HOLIDAY:
            landing = (self.resolve_holiday(sticker.event, date.today().year)
                       if self.resolve_holiday else None)
            if landing is None:
                landing = _parse(sticker.fallback)
        sticker.kind = BY_DATE
        sticker.day = landing.isoformat() if landing else ""
        sticker.event = ""
        sticker.fallback = ""
        # Freed as well as detached. Unlocking is what somebody presses when
        # they want to move it, and leaving it held would answer that with
        # nothing visible happening.
        sticker.held = False
        self.save()
        return sticker

    def move(self, key: str, day: date = None, x: float = None,
             y: float = None, scale: float = None,
             angle: float = None) -> Optional[Sticker]:
        """Move, resize or turn one inside its box, or send it to another day."""
        sticker = self.stickers.get(key)
        if sticker is None:
            return None
        if x is not None:
            sticker.x = float(x)
        if y is not None:
            sticker.y = float(y)
        if scale is not None:
            sticker.scale = float(scale)
        if angle is not None:
            sticker.angle = float(angle)
        if day is not None and not sticker.locked:
            # Dropping a sticker on a different day is a decision about where
            # it lives, so it stops following whatever it followed before -
            # and settles again on the day it landed on.
            sticker.kind = BY_DATE
            sticker.day = day.isoformat()
            sticker.event = ""
            sticker.fallback = ""
            sticker.held = True
        sticker.clamped()
        self.save()
        return sticker

    def unlock(self, key: str) -> Optional[Sticker]:
        """
        Free a sticker from its event, leaving it where it currently shows.

        What the chrome button on a locked sticker does. Detaching and
        unlocking are one act: the sticker stops following the event, and stops
        being held to the day the event chose.
        """
        sticker = self.stickers.get(key)
        if sticker is None:
            return None
        if sticker.attached:
            # Detaching frees it as well - see detach().
            return self.detach(key)
        # Settled on a day but following nothing. There is no event to come
        # off; it just stops being held.
        sticker.held = False
        self.save()
        return sticker

    def remove(self, key: str) -> bool:
        if key not in self.stickers:
            return False
        self.stickers.pop(key, None)
        self.save()
        return True

    def remove_for_event(self, event_key: str) -> int:
        """
        Unstick anything following an event that has gone.

        The stickers stay - on the date they were last shown. A sticker is
        something somebody put there, and deleting an event is not a statement
        about it.
        """
        moved = 0
        for sticker in list(self.stickers.values()):
            if sticker.kind == BY_EVENT and sticker.event == event_key:
                self.detach(sticker.key)
                moved += 1
        return moved


def _holiday_slug(entry) -> str:
    """
    The part of a holiday's key that does not change with the year.

    Keys are `holiday:2026:labor-day`, so the year has to come out or the
    sticker follows one year and disappears in the next.
    """
    if str(getattr(entry, "source", "") or "") != "holiday":
        return ""
    key = str(getattr(entry, "key", "") or "")
    parts = key.split(":")
    return parts[-1] if len(parts) >= 3 else ""


def _is_forever_yearly(entry) -> bool:
    """A series that repeats every year and never stops."""
    repeat = str(getattr(entry, "repeat", "") or "").lower()
    if repeat != "yearly":
        return False
    return not str(getattr(entry, "repeat_until", "") or "").strip()
