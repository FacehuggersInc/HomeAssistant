"""
The calendar's data layer.

Everything the pages, widgets, tiles, panels and skills read comes from here,
and none of it needs Qt - which is deliberate. The UI can be rebuilt without
touching any of this, and this can be tested without a display.

Events come from three places and are kept apart by `source`:

* `local`    - made in the app
* `imported` - posted to the API by something else
* `holiday`  - computed, not stored, and not editable
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from typing import Callable, Optional

SOURCES = ("local", "imported", "holiday")


## -- EVENTS -------------------------------------------------------------------

@dataclass
class Event:
    """
    One entry. `day` is always set; `time` is optional and means all-day.

    Stored as ISO strings rather than datetimes so the file is readable and a
    hand-edited entry still loads.
    """

    title:    str
    day:      str                      # "YYYY-MM-DD"
    time:     str = ""                 # "HH:MM", empty for all-day
    end_time: str = ""                 # "HH:MM", empty for no stated end
    location: str = ""
    notes:    str = ""
    icon:     str = "mdi.calendar"
    colour:   str = ""
    source:   str = "local"
    key:      str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    ## -- derived

    @property
    def date(self) -> Optional[date]:
        try:
            return date.fromisoformat(self.day)
        except (ValueError, TypeError):
            return None

    @property
    def starts_at(self) -> Optional[datetime]:
        """When it begins. An all-day event begins at midnight."""
        day = self.date
        if day is None:
            return None
        hour, minute = _parse_clock(self.time)
        return datetime(day.year, day.month, day.day, hour, minute)

    @property
    def ends_at(self) -> Optional[datetime]:
        day = self.date
        if day is None:
            return None
        if not self.end_time:
            # No stated end. All-day runs to midnight; a timed event is
            # treated as an instant rather than being given an invented length.
            if not self.time:
                return datetime(day.year, day.month, day.day, 23, 59)
            return self.starts_at
        hour, minute = _parse_clock(self.end_time)
        end = datetime(day.year, day.month, day.day, hour, minute)
        if self.starts_at and end < self.starts_at:
            end += timedelta(days=1)      # crosses midnight
        return end

    @property
    def all_day(self) -> bool:
        return not self.time

    @property
    def editable(self) -> bool:
        return self.source != "holiday"

    def duration(self) -> Optional[timedelta]:
        if not self.end_time or self.starts_at is None:
            return None
        return self.ends_at - self.starts_at

    def is_on(self, when: date) -> bool:
        return self.date == when

    ## -- serialising

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> Optional["Event"]:
        """None for anything unusable, so one bad entry cannot lose the file."""
        if not isinstance(raw, dict):
            return None
        title = str(raw.get("title") or "").strip()
        day   = str(raw.get("day") or "").strip()
        if not title or not day:
            return None
        try:
            date.fromisoformat(day)
        except ValueError:
            return None

        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in raw.items() if k in known}
        clean["title"] = title
        clean["day"]   = day
        if clean.get("source") not in SOURCES:
            clean["source"] = "local"
        return cls(**clean)


def _parse_clock(text: str) -> tuple:
    try:
        hour, _, minute = str(text).partition(":")
        return max(0, min(23, int(hour))), max(0, min(59, int(minute or 0)))
    except (ValueError, TypeError):
        return 0, 0


## -- HOLIDAYS -----------------------------------------------------------------
#
# Computed rather than fetched. A wall panel is frequently offline, an API key
# for something this static is a poor trade, and the rules do not change.

def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    """nth (1-based) `weekday` of a month; nth=-1 means the last one."""
    if nth > 0:
        first = date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return first + timedelta(days=offset + 7 * (nth - 1))
    nxt = date(year + (month == 12), (month % 12) + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def holidays_for(year: int) -> list:
    """Every holiday in a year, as unsaved Event objects."""
    easter = _easter(year)
    entries = [
        ("New Year's Day",     date(year, 1, 1),                    "mdi.firework"),
        ("Martin Luther King Jr. Day", _nth_weekday(year, 1, 0, 3), "mdi.account-star"),
        ("Groundhog Day",      date(year, 2, 2),                    "mdi.rodent"),
        ("Valentine's Day",    date(year, 2, 14),                   "mdi.heart"),
        ("Presidents' Day",    _nth_weekday(year, 2, 0, 3),         "mdi.bank"),
        ("St. Patrick's Day",  date(year, 3, 17),                   "mdi.clover"),
        ("Good Friday",        easter - timedelta(days=2),          "mdi.cross"),
        ("Easter",             easter,                              "mdi.egg-easter"),
        ("Cinco de Mayo",      date(year, 5, 5),                    "mdi.party-popper"),
        ("Mother's Day",       _nth_weekday(year, 5, 6, 2),         "mdi.flower"),
        ("Memorial Day",       _nth_weekday(year, 5, 0, -1),        "mdi.flag"),
        ("Juneteenth",         date(year, 6, 19),                   "mdi.flag-variant"),
        ("Father's Day",       _nth_weekday(year, 6, 6, 3),         "mdi.tie"),
        ("Independence Day",   date(year, 7, 4),                    "mdi.firework"),
        ("Labor Day",          _nth_weekday(year, 9, 0, 1),         "mdi.hammer-wrench"),
        ("Halloween",          date(year, 10, 31),                  "mdi.halloween"),
        ("Veterans Day",       date(year, 11, 11),                  "mdi.medal"),
        ("Thanksgiving",       _nth_weekday(year, 11, 3, 4),        "mdi.food-turkey"),
        ("Christmas Eve",      date(year, 12, 24),                  "mdi.pine-tree"),
        ("Christmas Day",      date(year, 12, 25),                  "mdi.gift"),
        ("New Year's Eve",     date(year, 12, 31),                  "mdi.glass-flute"),
    ]
    return [
        Event(title=title, day=when.isoformat(), icon=icon,
              source="holiday", colour="#c08a3e",
              key=f"holiday:{year}:{title.lower().replace(' ', '-').replace(chr(39), '')}")
        for title, when, icon in entries
    ]


## -- STORE --------------------------------------------------------------------

class CalendarStore:
    """
    Events on disk, plus every question the rest of the plugin asks of them.

    Holidays are merged in on read rather than stored - they are derivable, and
    saving them would mean a file that goes stale a year at a time.
    """

    def __init__(self, path, log: Callable = None):
        self.path = path
        self.log = log or (lambda *a, **k: None)
        self.events: dict = {}          # key -> Event
        self._holiday_cache: dict = {}  # year -> [Event]
        self.load()

    ## -- persistence

    def load(self) -> None:
        self.events = {}
        try:
            if not self.path.is_file():
                return
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            self.log("warning", f"[Calendar] Could not read events: {e}")
            return

        for item in (raw.get("events") if isinstance(raw, dict) else raw) or []:
            event = Event.from_dict(item)
            if event is not None:
                self.events[event.key] = event
        self.log("info", f"[Calendar] Loaded {len(self.events)} events.")

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"events": [e.to_dict() for e in self.events.values()]}
            self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as e:
            self.log("warning", f"[Calendar] Could not save events: {e}")

    ## -- mutation

    def add(self, event: Event) -> Event:
        if event.source == "holiday":
            raise ValueError("Holidays are computed, not stored.")
        self.events[event.key] = event
        self.save()
        return event

    def remove(self, key: str) -> bool:
        if key in self.events:
            del self.events[key]
            self.save()
            return True
        return False

    def update(self, key: str, **changes) -> Optional[Event]:
        event = self.events.get(key)
        if event is None:
            return None
        for name, value in changes.items():
            if name in Event.__dataclass_fields__ and name != "key":
                setattr(event, name, value)
        self.save()
        return event

    def get(self, key: str) -> Optional[Event]:
        if key.startswith("holiday:"):
            try:
                year = int(key.split(":")[1])
            except (IndexError, ValueError):
                return None
            return next((h for h in self.holidays(year) if h.key == key), None)
        return self.events.get(key)

    ## -- reading

    def holidays(self, year: int) -> list:
        if year not in self._holiday_cache:
            self._holiday_cache[year] = holidays_for(year)
        return self._holiday_cache[year]

    def all_events(self, include_holidays: bool = True,
                   years: tuple = None) -> list:
        out = list(self.events.values())
        if include_holidays:
            span = years or self._year_span()
            for year in span:
                out.extend(self.holidays(year))
        return sorted(out, key=_sort_key)

    def _year_span(self) -> range:
        """Years worth computing holidays for: this year, and any with events."""
        today = date.today().year
        years = {today, today + 1}
        for event in self.events.values():
            when = event.date
            if when:
                years.add(when.year)
        return range(min(years), max(years) + 1)

    def on_day(self, when: date, include_holidays: bool = True) -> list:
        return [e for e in self.all_events(include_holidays, years=(when.year,))
                if e.is_on(when)]

    def in_month(self, year: int, month: int,
                 include_holidays: bool = True) -> dict:
        """{date: [events]} for one month - what the calendar grid draws from."""
        out: dict = {}
        for event in self.all_events(include_holidays, years=(year,)):
            when = event.date
            if when and when.year == year and when.month == month:
                out.setdefault(when, []).append(event)
        return out

    def between(self, start: date, end: date,
                include_holidays: bool = True) -> list:
        years = tuple(range(start.year, end.year + 1))
        return [e for e in self.all_events(include_holidays, years=years)
                if e.date and start <= e.date <= end]

    ## -- relative helpers
    #
    # The questions everything else actually asks. Kept here rather than spread
    # across the widgets, tiles and skills that need them.

    def upcoming(self, count: int = 5, source: str = None,
                 now: datetime = None) -> list:
        now = now or datetime.now()
        out = []
        for event in self.all_events():
            if source and event.source != source:
                continue
            end = event.ends_at
            if end is None or end < now:
                continue
            out.append(event)
        return out[:count]

    def next_event(self, source: str = None, now: datetime = None) -> Optional[Event]:
        found = self.upcoming(1, source=source, now=now)
        return found[0] if found else None

    def next_holiday(self, now: datetime = None) -> Optional[Event]:
        return self.next_event(source="holiday", now=now)

    def next_user_event(self, now: datetime = None) -> Optional[Event]:
        """Anything a person put there, whether locally or over the API."""
        now = now or datetime.now()
        candidates = [e for e in self.upcoming(50, now=now) if e.source != "holiday"]
        return candidates[0] if candidates else None

    def previous_event(self, source: str = None, now: datetime = None) -> Optional[Event]:
        now = now or datetime.now()
        past = [e for e in self.all_events()
                if (not source or e.source == source)
                and e.ends_at and e.ends_at < now]
        return past[-1] if past else None

    def current_event(self, now: datetime = None) -> Optional[Event]:
        """Something happening right now, if anything is."""
        now = now or datetime.now()
        for event in self.all_events():
            start, end = event.starts_at, event.ends_at
            if start and end and start <= now <= end and not event.all_day:
                return event
        return None

    def time_until(self, event: Event, now: datetime = None) -> Optional[timedelta]:
        now = now or datetime.now()
        return None if event.starts_at is None else event.starts_at - now

    def days_until(self, event: Event, today: date = None) -> Optional[int]:
        today = today or date.today()
        return None if event.date is None else (event.date - today).days

    def describe_gap(self, event: Event, now: datetime = None) -> str:
        """
        Human phrasing for how far off something is.

        Days for anything more than a day out, because "in 37 hours" is not how
        anyone thinks about Thursday.
        """
        now = now or datetime.now()
        days = self.days_until(event, now.date())
        if days is None:
            return "sometime"
        if days > 1:
            return f"in {days} days"
        if days == 1:
            return "tomorrow"
        if days < -1:
            return f"{abs(days)} days ago"
        if days == -1:
            return "yesterday"

        gap = self.time_until(event, now)
        if gap is None:
            return "today"
        seconds = int(gap.total_seconds())
        if seconds < -60:
            return "earlier today"
        if seconds < 60:
            return "now"
        if seconds < 3600:
            minutes = seconds // 60
            return f"in {minutes} minute{'s' if minutes != 1 else ''}"
        hours = seconds // 3600
        return f"in {hours} hour{'s' if hours != 1 else ''}"

    def describe_duration(self, event: Event) -> str:
        length = event.duration()
        if length is None:
            return "all day" if event.all_day else "no set length"
        minutes = int(length.total_seconds() // 60)
        hours, minutes = divmod(minutes, 60)
        if hours and minutes:
            return f"{hours}h {minutes}m"
        if hours:
            return f"{hours} hour{'s' if hours != 1 else ''}"
        return f"{minutes} minute{'s' if minutes != 1 else ''}"


def _sort_key(event: Event):
    start = event.starts_at
    return (start or datetime.max, event.title.lower())
