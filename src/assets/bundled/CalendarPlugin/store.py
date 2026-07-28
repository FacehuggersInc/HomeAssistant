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

# Every source an event can have. A value missing from here is silently
# rewritten to "local" on load, which is not a small thing: it detaches the
# event from whatever owns it, makes it editable when it should not be, and
# hides it from the sync that would otherwise replace it.
SOURCES = ("local", "imported", "holiday", "subscribed")


## -- EVENTS -------------------------------------------------------------------

@dataclass
class Event:
    """
    One entry. `day` is always set; `time` is optional and means all-day.

    Stored as ISO strings rather than datetimes so the file is readable and a
    hand-edited entry still loads.
    """

    title:    str
    day:      str                      # "YYYY-MM-DD" — the first day
    end_day:  str = ""                 # "YYYY-MM-DD" — last day, for a span
    time:     str = ""                 # "HH:MM", empty for all-day
    end_time: str = ""                 # "HH:MM", empty for no stated end
    location: str = ""
    notes:    str = ""
    icon:     str = "mdi.calendar"
    colour:   str = ""
    source:   str = "local"

    # Recurrence. One stored event, many occurrences - storing each occurrence
    # would mean a weekly event filling the file forever and no way to change
    # "all of them" after the fact.
    repeat:          str = ""          # daily | weekly | monthly | yearly
    repeat_interval: int = 1           # every N of those
    repeat_until:    str = ""          # last day it may occur, "" for forever
    skip:            list = field(default_factory=list)   # occurrence days hidden

    key:      str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    # Set on generated occurrences, empty on the stored event they came from.
    series_key: str = ""

    # Silenced without being deleted. A holiday you never care about, or a
    # series paused indefinitely - deleting would be a different intent.
    hidden:   bool = False

    # Whose event this is. Two people can have the same thing at the same time
    # and mean two different things - one person cancelling theirs must not
    # cancel the other's, so identical events with different owners are
    # different events. Holidays have no owner; they belong to everybody.
    owner:    str = ""

    # The subscription that owns this, if any. A field rather than a prefix
    # buried in the notes: matching on notes broke the moment a feed sent a
    # description, and a user editing the notes could orphan their own event.
    subscription: str = ""

    ## -- derived

    ## -- spans
    #
    # last_date / spans_days / day_count live further down, with the rest of
    # the derived properties. They were defined twice in this class - the
    # second copy silently won, so editing the first changed nothing.

    def covers(self, when: date) -> bool:
        """Whether this event is on a given day, spans included."""
        start, end = self.date, self.last_date
        if start is None:
            return False
        return start <= when <= (end or start)

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
    def last_date(self) -> Optional[date]:
        """The final day of a span, or the only day of a single-day event."""
        if not self.end_day:
            return self.date
        try:
            end = date.fromisoformat(self.end_day)
        except (ValueError, TypeError):
            return self.date
        start = self.date
        return end if (start is None or end >= start) else start

    @property
    def spans_days(self) -> bool:
        first, last = self.date, self.last_date
        return bool(first and last and last > first)

    @property
    def day_count(self) -> int:
        first, last = self.date, self.last_date
        return 1 if not (first and last) else (last - first).days + 1

    @property
    def occurrence_span_days(self) -> int:
        """
        How far one occurrence runs past its own start day, in days.

        For a one-off this is just the span. For a **series** it is clamped so
        an occurrence cannot reach its own next occurrence: `end_day` and
        `repeat_until` are adjacent fields answering different questions, and
        putting the series' finishing date in `end_day` turns every occurrence
        into a span that long. A weekly event with a month in `end_day` then
        draws five overlapping month-long bars instead of five evenings, and
        the count climbs the further into the series you look.

        Clamping here rather than only validating on save, because the bad
        shape can already be on disk or arrive from an ICS feed.
        """
        if not self.spans_days:
            return 0
        span = (self.last_date - self.date).days
        if not self.recurring:
            return max(0, span)

        first = self.date
        following = _step(first, self.repeat, max(1, int(self.repeat_interval or 1)))
        if following is None:
            return max(0, span)
        gap = (following - first).days - 1     # last day before the next one
        return max(0, min(span, gap))

    @property
    def recurring(self) -> bool:
        return self.repeat in ("daily", "weekly", "monthly", "yearly")

    @property
    def base_key(self) -> str:
        """The stored event behind this one - itself, unless it is an occurrence."""
        return self.series_key or self.key

    def skip_dates(self) -> set:
        """The occurrence days that have been silenced, as real dates."""
        out = set()
        for raw in self.skip or []:
            try:
                out.add(date.fromisoformat(str(raw)))
            except (ValueError, TypeError):
                continue
        return out

    def occurrence_on(self, when: date) -> "Event":
        """
        A copy of this event moved to `when`.

        Its own key, so an occurrence can be told apart from its series and
        from every other occurrence - and `series_key` pointing home, so
        acting on it can find the stored event.
        """
        shift = self.occurrence_span_days
        fields = dict(self.to_dict())
        fields.update({
            "day": when.isoformat(),
            "end_day": (when + timedelta(days=shift)).isoformat() if shift else "",
            "repeat": "", "repeat_until": "", "skip": [],
            "series_key": self.key,
            "key": f"{self.key}@{when.isoformat()}",
        })
        return Event(**fields)

    @property
    def editable(self) -> bool:
        return self.source != "holiday"

    def duration(self) -> Optional[timedelta]:
        if not self.end_time or self.starts_at is None:
            return None
        return self.ends_at - self.starts_at

    def is_on(self, when: date) -> bool:
        """Alias for covers(). Kept for callers written against the old name."""
        return self.covers(when)

    ## -- recurrence

    def occurrences(self, start: date, end: date) -> list:
        """
        Copies of this event on each day it falls between start and end.

        A non-recurring event yields itself once. Occurrences carry a key of
        `<base>@<date>` so one can be referred to - to skip it, say - without
        being a stored event in its own right.
        """
        first = self.date
        if first is None:
            return []

        if not self.recurring:
            return [self] if (first <= end and (self.last_date or first) >= start) else []

        limit = None
        if self.repeat_until:
            try:
                limit = date.fromisoformat(self.repeat_until)
            except ValueError:
                limit = None

        span = self.occurrence_span_days
        step = max(1, int(self.repeat_interval or 1))
        skipped = set(self.skip or [])

        out = []
        for when in _walk(first, self.repeat, step, end, limit):
            if when + timedelta(days=span) < start:
                continue
            if when.isoformat() in skipped:
                continue
            out.append(self._as_occurrence(when, span))
            if len(out) > 400:
                # A daily event across a decade is not something anyone is
                # reading; the caller asked for a range and got a sane amount.
                break
        return out

    def _as_occurrence(self, when: date, span: int) -> "Event":
        clone = Event(**{**self.to_dict(),
                         "day": when.isoformat(),
                         "end_day": (when + timedelta(days=span)).isoformat() if span else "",
                         "key": f"{self.key}@{when.isoformat()}",
                         "series_key": self.key})
        return clone

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


def _walk(first: date, rule: str, step: int, end: date,
          limit: Optional[date]):
    """Every day this rule produces, from `first` up to `end` (and `limit`)."""
    stop = min(end, limit) if limit else end
    guard = 0
    index = 0
    when = first

    while when <= stop and guard < 2000:
        guard += 1
        yield when
        index += 1

        if rule == "daily":
            when = first + timedelta(days=step * index)
        elif rule == "weekly":
            when = first + timedelta(weeks=step * index)
        elif rule == "monthly":
            # Counted from the original day every time, not from the last
            # occurrence. Stepping from the previous one compounds the clamp:
            # the 31st becomes the 28th in February and then stays the 28th
            # for the rest of the year.
            when = _add_months(first, step * index)
        elif rule == "yearly":
            when = _add_months(first, 12 * step * index)
        else:
            return


def _add_months(when: date, months: int) -> date:
    """
    Keeps the day of the month where it exists, clamps where it does not.

    The 31st in a 30-day month has to become the 30th - rolling into the next
    month instead would drift a monthly event forward a day at a time.
    """
    month = when.month - 1 + months
    year = when.year + month // 12
    month = month % 12 + 1
    import calendar as _cal
    day = min(when.day, _cal.monthrange(year, month)[1])
    return date(year, month, day)


def _step(when: date, repeat: str, index: int) -> Optional[date]:
    """The `index`-th occurrence after `when`, or None for an unknown rule."""
    if repeat == "daily":
        return when + timedelta(days=index)
    if repeat == "weekly":
        return when + timedelta(weeks=index)
    if repeat == "monthly":
        month = when.month - 1 + index
        year = when.year + month // 12
        month = month % 12 + 1
        # Clamped, so the 31st of a 30-day month lands on the 30th rather
        # than being skipped or rolling into the next one.
        import calendar as _calendar
        day = min(when.day, _calendar.monthrange(year, month)[1])
        return date(year, month, day)
    if repeat == "yearly":
        try:
            return when.replace(year=when.year + index)
        except ValueError:
            # 29 February in a year that has none.
            return date(when.year + index, 2, 28)
    return None


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
        self.hidden_holidays: set = set()   # holiday keys never to show
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

        if isinstance(raw, dict):
            # Holidays are computed, so hiding one cannot be a flag on the
            # event - there is no stored event to flag.
            self.hidden_holidays = set(raw.get("hidden_holidays") or [])
        self.log("info", f"[Calendar] Loaded {len(self.events)} events.")

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"events": [e.to_dict() for e in self.events.values()],
                       "hidden_holidays": sorted(self.hidden_holidays)}
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

    def resolve_key(self, key: str) -> str:
        """
        The stored event behind a key, which may be an occurrence's.

        Everything a caller sees from on_day(), in_month() or upcoming() for a
        recurring event is a generated occurrence keyed `<stored>@<date>`, and
        that key is not in `self.events`. remove() and update() matched on it
        directly, found nothing, and returned a falsy value that every caller
        discarded - so deleting an occurrence of a series looked like nothing
        happening at all.

        Split from the right and only accepted when the result is genuinely a
        stored key, so an event whose own key contained an `@` is unaffected.
        """
        key = str(key or "")
        if key in self.events:
            return key
        if "@" in key:
            base = key.rsplit("@", 1)[0]
            if base in self.events:
                return base
        return key

    def remove(self, key: str) -> bool:
        key = self.resolve_key(key)
        if key in self.events:
            del self.events[key]
            self.save()
            return True
        return False

    def update(self, key: str, **changes) -> Optional[Event]:
        event = self.events.get(self.resolve_key(key))
        if event is None:
            return None
        for name, value in changes.items():
            if name in Event.__dataclass_fields__ and name != "key":
                setattr(event, name, value)
        self.save()
        return event

    def get(self, key: str) -> Optional[Event]:
        # `series@date` is one occurrence of a repeating event. Nothing stored
        # has that key, so looking it up straight found nothing and the event
        # view reported that the event no longer existed.
        if "@" in key:
            series_key, _, stamp = key.rpartition("@")
            series = self.events.get(series_key)
            if series is not None:
                try:
                    return series.occurrence_on(date.fromisoformat(stamp))
                except (ValueError, TypeError):
                    return None

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
                   years: tuple = None, expand_from: date = None,
                   expand_to: date = None) -> list:
        """
        Every event, with series expanded across the window being asked about.

        A recurring event is one stored row; what a caller wants is the
        occurrences that fall in the range it cares about. Without a range,
        a year either side of today is enough for every question asked here.
        """
        today = date.today()
        start = expand_from or (today - timedelta(days=365))
        end = expand_to or (today + timedelta(days=365))

        out = []
        for event in self.events.values():
            if event.hidden:
                continue
            if not event.recurring:
                out.append(event)
                continue
            out.extend(self.expand(event, start, end))

        if include_holidays:
            for year in (years or self._year_span()):
                out.extend(h for h in self.holidays(year)
                           if h.key not in self.hidden_holidays)

        return sorted(self._collapse(out), key=_sort_key)

    @staticmethod
    def _collapse(events: list) -> list:
        """
        One entry per real event, after expansion.

        deduplicate() cannot help here: it only sees what is *stored*, and the
        commonest duplicate is a stored one-off sitting on the same day as an
        occurrence generated from a series. A calendar that moves one week of
        a repeating event often sends the moved copy as a separate entry with
        no link back, so nothing ties the two together at import.

        Where both exist, the stored one wins - it is the specific answer, and
        the generated one is the rule it was an exception to.
        """
        best: dict = {}
        order: list = []

        for event in events:
            if event.source == "holiday":
                order.append(event)
                continue

            fingerprint = (
                event.owner, event.title.strip().lower(),
                event.day, event.end_day, event.time, event.end_time,
            )
            previous = best.get(fingerprint)
            if previous is None:
                best[fingerprint] = event
                order.append(event)
                continue

            # A generated occurrence carries series_key; a stored event does
            # not. Prefer the stored one, in place, so ordering is untouched.
            if previous.series_key and not event.series_key:
                order[order.index(previous)] = event
                best[fingerprint] = event

        return order

    def expand(self, event: Event, start: date, end: date,
               limit: int = 400) -> list:
        """
        The occurrences of a series between two dates.

        Bounded by `limit` as well as by the window: a daily event with no end
        and a wide range would otherwise generate as many rows as the range
        has days, and something has to stop a mistake becoming a hang.
        """
        first = event.date
        if first is None or not event.recurring:
            return []

        until = None
        if event.repeat_until:
            try:
                until = date.fromisoformat(event.repeat_until)
            except (ValueError, TypeError):
                until = None

        # How far each occurrence runs past its own start day. Matched against
        # the window below, because an occurrence that BEGINS before the window
        # can still cover days inside it - a three-day event starting on the
        # Sunday is on the Monday being asked about. Filtering on the start day
        # alone is why on_day() found nothing for a span that in_month() was
        # quite happily drawing across the whole week.
        span = max(0, event.occurrence_span_days)

        skips = event.skip_dates()
        out = []
        index = 0
        while index < limit:
            when = _step(first, event.repeat, index)
            index += 1
            if when is None or when > end:
                break
            if until is not None and when > until:
                break
            if when + timedelta(days=span) < start or when in skips:
                continue
            out.append(event.occurrence_on(when))
        return out

    def _year_span(self) -> range:
        """Years worth computing holidays for: this year, and any with events."""
        today = date.today().year
        years = {today, today + 1}
        for event in self.events.values():
            when = event.date
            if when:
                years.add(when.year)
        return range(min(years), max(years) + 1)

    def by_owner(self, owner: str, include_holidays: bool = False) -> list:
        """Everything belonging to one person. Holidays are nobody's."""
        return [e for e in self.all_events(include_holidays)
                if e.source == "holiday" or (e.owner or "") == owner]

    def owners(self) -> list:
        """Every name that owns something, for a filter or a summary."""
        return sorted({e.owner for e in self.events.values()
                       if e.owner and e.source != "holiday"})

    def on_day(self, when: date, include_holidays: bool = True) -> list:
        out, seen = [], set()
        for event in self.all_events(include_holidays, years=(when.year,),
                                     expand_from=when, expand_to=when):
            if event.covers(when) and event.key not in seen:
                seen.add(event.key)
                out.append(event)
        return out

    def in_month(self, year: int, month: int,
                 include_holidays: bool = True) -> dict:
        """{date: [events]} for one month - what the calendar grid draws from."""
        import calendar as _calendar
        first = date(year, month, 1)
        last = date(year, month, _calendar.monthrange(year, month)[1])

        out: dict = {}
        for event in self.all_events(include_holidays, years=(year,),
                                     expand_from=first, expand_to=last):
            start, end = event.date, event.last_date
            if start is None:
                continue
            # A span appears on every day it covers, not only its first - the
            # grid draws it in each cell it runs through.
            cursor = max(start, first)
            finish = min(end or start, last)
            while cursor <= finish:
                day_list = out.setdefault(cursor, [])
                # By key, because a span is deliberately added to every day it
                # covers and any path that reaches the same day twice would
                # otherwise draw the event twice in one cell.
                if not any(existing.key == event.key for existing in day_list):
                    day_list.append(event)
                cursor += timedelta(days=1)
        return out

    def between(self, start: date, end: date,
                include_holidays: bool = True) -> list:
        years = tuple(range(start.year, end.year + 1))
        return [e for e in self.all_events(include_holidays, years=years,
                                           expand_from=start, expand_to=end)
                if e.date and start <= e.date <= end]

    ## -- hiding and pruning

    def skip_occurrence(self, series_key: str, when: date) -> bool:
        """Hide one occurrence of a series, leaving the rest alone."""
        event = self.events.get(series_key)
        if event is None or not event.recurring:
            return False
        stamp = when.isoformat()
        if stamp not in event.skip:
            event.skip.append(stamp)
            self.save()
        return True

    def unskip_occurrence(self, series_key: str, when: date) -> bool:
        event = self.events.get(series_key)
        if event is None:
            return False
        stamp = when.isoformat()
        if stamp in event.skip:
            event.skip.remove(stamp)
            self.save()
            return True
        return False

    def skip_next(self, series_key: str, count: int = 1,
                  now: date = None) -> int:
        """
        Hide the next `count` occurrences.

        "Not for the next three Wednesdays" is one call rather than three
        trips into the calendar to find them.
        """
        event = self.events.get(series_key)
        if event is None or not event.recurring:
            return 0
        now = now or date.today()
        upcoming = [o.date for o in self.expand(event, now, now + timedelta(days=800))]
        hidden = 0
        for when in upcoming[:max(0, count)]:
            if self.skip_occurrence(series_key, when):
                hidden += 1
        return hidden

    def set_hidden(self, key: str, hidden: bool = True) -> bool:
        """Silence a whole event or series without deleting it."""
        if key.startswith("holiday:"):
            if hidden:
                self.hidden_holidays.add(key)
            else:
                self.hidden_holidays.discard(key)
            self.save()
            return True
        event = self.events.get(key)
        if event is None:
            return False
        event.hidden = bool(hidden)
        self.save()
        return True

    def hidden_keys(self) -> list:
        return sorted(self.hidden_holidays) + \
               sorted(k for k, e in self.events.items() if e.hidden)

    def looks_like(self, event: Event, ignore_day: bool = True) -> list:
        """
        Every stored event that is recognisably the same thing as `event`.

        Deliberately looser than deduplicate()'s fingerprint, which includes
        `day` and so treats a weekly series starting on the 28th and an
        identical one starting on the 4th as two different events. They are
        two different *rows*, but to somebody looking at a calendar they are
        the same thing appearing twice, and "remove this" means remove both.

        Holidays are never matched: they are computed, not stored, and
        removing one is what `set_hidden` is for.
        """
        target = (
            (event.owner or ""),
            event.title.strip().lower(),
            event.time or "",
            event.end_time or "",
        )
        out = []
        for stored in self.events.values():
            if stored.source == "holiday":
                continue
            candidate = (
                (stored.owner or ""),
                stored.title.strip().lower(),
                stored.time or "",
                stored.end_time or "",
            )
            if candidate != target:
                continue
            if not ignore_day and stored.day != event.day:
                continue
            out.append(stored)
        return out

    def remove_matching(self, event: Event, ignore_day: bool = True) -> int:
        """Remove every stored copy of `event`. Returns how many went."""
        doomed = [e.key for e in self.looks_like(event, ignore_day=ignore_day)]
        for key in doomed:
            self.events.pop(key, None)
        if doomed:
            self.save()
        return len(doomed)

    def deduplicate(self) -> int:
        """
        Drop events that are the same thing stored twice.

        Same owner, same source, same title, same span and time - which is one
        event however it got in. Keys are deliberately not part of that: two
        rows with different keys and identical content is exactly the state
        this exists to clear, and comparing keys would never find it.

        Holidays are excluded; they are computed and cannot be duplicated.
        """
        seen, doomed = {}, []
        for key, event in self.events.items():
            if event.source == "holiday":
                continue
            # Source is deliberately not part of this. The same event stored
            # twice under two different sources is exactly what a source that
            # failed to round-trip produces, and matching on it would declare
            # the pair different and keep both.
            fingerprint = (
                event.owner, event.title.strip().lower(),
                event.day, event.end_day, event.time, event.end_time,
                event.repeat, event.repeat_until,
            )
            if fingerprint in seen:
                other = self.events[seen[fingerprint]]
                # Keep whichever one a feed still owns: that is the copy the
                # next sync will replace, and dropping it would strand the
                # other beyond anything's reach.
                if event.subscription and not other.subscription:
                    doomed.append(seen[fingerprint])
                    seen[fingerprint] = key
                else:
                    doomed.append(key)
            else:
                seen[fingerprint] = key

        for key in doomed:
            del self.events[key]
        if doomed:
            self.save()
        return len(doomed)

    def prune(self, older_than_days: int = 365, now: date = None) -> int:
        """
        Drop finished one-off events past a cutoff.

        Series are never pruned - a weekly thing that started two years ago is
        still the same weekly thing, and its first date is not its last.
        """
        now = now or date.today()
        cutoff = now - timedelta(days=max(1, older_than_days))
        doomed = [
            key for key, event in self.events.items()
            if not event.recurring
            and event.last_date is not None
            and event.last_date < cutoff
        ]
        for key in doomed:
            del self.events[key]
        if doomed:
            self.save()
        return len(doomed)

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


def owner_of(event: Event) -> str:
    """Blank for a holiday, so nothing tries to attribute one."""
    return "" if event.source == "holiday" else (event.owner or "")


def _sort_key(event: Event):
    start = event.starts_at
    return (start or datetime.max, event.title.lower())
