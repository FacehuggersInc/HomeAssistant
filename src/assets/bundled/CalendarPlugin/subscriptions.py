"""
Read-only mirrors of calendars kept somewhere else.

One direction only: a feed updates our calendar and nothing goes back. That is
not a limitation being worked around - it is what makes this safe. Nothing
syncs both ways, so nothing can conflict, and the panel stays the authority for
what was added on the panel.

ICS is a real standard, so Google, iCloud and Outlook are all the same code
path. What differs is only where you get the URL.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from datetime import date, datetime, timedelta
from typing import Optional

from .store import Event

# Google's secret ICS feed is cached hard - changes can take hours to appear.
# Polling faster does not make it fresher, it just costs requests.
DEFAULT_INTERVAL_MINUTES = 60

# A feed that has grown to this is either a mistake or a calendar nobody wants
# on a wall panel. Read to the cap rather than into memory without a limit.
MAX_BYTES = 8 * 1024 * 1024


## -- ADDRESSES -----------------------------------------------------------------

#Google's "add this calendar" link, which is a web page and not a feed:
#  https://calendar.google.com/calendar/u/0?cid=<base64 of the address>
#It sits directly under the iCal address in Google's own settings, so it is by
#far the likeliest wrong thing to paste - and it FETCHES PERFECTLY. The panel
#gets an HTML page, finds no events in it, and reports a clean sync of nothing.
_GOOGLE_CID = re.compile(r"calendar\.google\.com/calendar/[^?]*\?.*\bcid=([^&]+)",
                         re.I)

#What a calendar always begins with. Anything without it is not one, whatever
#the server said when it handed it over.
CALENDAR_MARKER = "BEGIN:VCALENDAR"


def ical_address(url: str) -> str:
    """
    The ICS feed a pasted address is really asking for, or the address itself.

    Only Google's `cid=` form is converted, and only because the conversion is
    exact: the parameter is the calendar's own address in base64, and the feed
    for it is a fixed URL. Guessing at anything less certain would be worse
    than the error, because a wrong guess fetches something and looks like it
    worked.
    """
    import base64
    import binascii
    import urllib.parse

    found = _GOOGLE_CID.search(str(url or ""))
    if not found:
        return str(url or "").strip()

    raw = urllib.parse.unquote(found.group(1))
    try:
        # base64 without its padding, which is how Google writes it.
        address = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return str(url or "").strip()

    if "@" not in address:
        return str(url or "").strip()
    return ("https://calendar.google.com/calendar/ical/"
            f"{urllib.parse.quote(address, safe='')}/public/basic.ics")


def is_secret_address(url: str) -> bool:
    """
    Whether this address is one that should be treated as a password.

    A Google private feed carries a token in its path; iCloud's published
    links are a token and nothing else. Both let anybody holding them read
    the calendar, which is worth saying on a page that lists them.
    """
    text = str(url or "").lower()
    if "calendar.google.com" in text and "/private-" in text:
        return True
    if "icloud.com" in text and "/published/" in text:
        return True
    if "outlook" in text and "/calendar/published/" in text:
        return True
    return False


## -- PARSING -------------------------------------------------------------------

def unfold(text: str) -> list:
    """
    ICS wraps long lines and continues them with a leading space or tab.

    Undone first, because every other rule here assumes one property per line
    and a folded DESCRIPTION would otherwise parse as a property called
    something arbitrary.
    """
    out = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        else:
            out.append(raw)
    return out


def unescape(value: str) -> str:
    return (value.replace("\\n", "\n").replace("\\N", "\n")
                 .replace("\\,", ",").replace("\\;", ";")
                 .replace("\\\\", "\\"))


def split_property(line: str) -> tuple:
    """`DTSTART;TZID=America/Chicago:20260804T143000` -> (name, params, value)."""
    head, _, value = line.partition(":")
    parts = head.split(";")
    name = parts[0].upper()
    params = {}
    for item in parts[1:]:
        key, _, val = item.partition("=")
        params[key.upper()] = val
    return name, params, value


def parse_stamp(value: str, params: dict) -> tuple:
    """
    (date_string, time_string) from a DTSTART or DTEND.

    Time zones are the honest weak point. A `Z` value is UTC and is converted
    to local; a TZID value is taken at face value. A feed from another zone
    will therefore be right only while that zone's offset matches the one it
    was written in. Fixing that properly means carrying zones through the
    whole store, which is a larger change than this one.
    """
    value = value.strip()
    if params.get("VALUE") == "DATE" or len(value) == 8:
        try:
            when = datetime.strptime(value, "%Y%m%d").date()
            return when.isoformat(), ""
        except ValueError:
            return "", ""

    utc = value.endswith("Z")
    stamp = value[:-1] if utc else value
    try:
        moment = datetime.strptime(stamp, "%Y%m%dT%H%M%S")
    except ValueError:
        return "", ""

    if utc:
        offset = datetime.now() - datetime.utcnow()
        moment = moment + timedelta(seconds=round(offset.total_seconds()))
    return moment.date().isoformat(), moment.strftime("%H:%M")


WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
FREQUENCIES = {"DAILY": "daily", "WEEKLY": "weekly",
               "MONTHLY": "monthly", "YEARLY": "yearly"}


def parse_rrule(value: str) -> dict:
    rule = {}
    for part in value.split(";"):
        key, _, val = part.partition("=")
        rule[key.upper()] = val
    return rule


def parse_events(text: str) -> list:
    """Every VEVENT in a feed, as dicts of its properties."""
    events, current = [], None
    for line in unfold(text):
        stripped = line.strip()
        if stripped == "BEGIN:VEVENT":
            current = {}
            continue
        if stripped == "END:VEVENT":
            if current:
                events.append(current)
            current = None
            continue
        if current is None or ":" not in stripped:
            continue
        name, params, value = split_property(stripped)
        current[name] = (params, value)
    return events


## -- SUBSCRIPTIONS --------------------------------------------------------------

class Subscription:
    """One external calendar being mirrored."""

    def __init__(self, url: str, name: str = "", key: str = "",
                 colour: str = "", icon: str = "mdi.calendar-sync",
                 enabled: bool = True, last_sync: float = 0.0,
                 last_error: str = "", owner: str = "",
                 last_count: int = -1):
        import uuid
        self.url = url
        self.name = name or "Subscribed calendar"
        self.key = key or uuid.uuid4().hex[:10]
        self.colour = colour
        self.icon = icon
        self.enabled = enabled
        self.last_sync = last_sync
        self.last_error = last_error
        # How many events the last successful sync produced. -1 means it has
        # never run: a feed that has not synced yet and one that synced and
        # found nothing are different, and only the second is worth asking
        # about.
        self.last_count = int(last_count)
        # Whose calendar this is. Two people subscribing to the same shared
        # feed get their own copies - so one of them removing it does not take
        # the other's events with it.
        self.owner = owner

    def to_dict(self) -> dict:
        return {"url": self.url, "name": self.name, "key": self.key,
                "colour": self.colour, "icon": self.icon,
                "enabled": self.enabled, "last_sync": self.last_sync,
                "last_error": self.last_error, "owner": self.owner,
                "last_count": self.last_count}

    @classmethod
    def from_dict(cls, raw: dict) -> Optional["Subscription"]:
        if not isinstance(raw, dict) or not raw.get("url"):
            return None
        return cls(**{k: v for k, v in raw.items()
                      if k in ("url", "name", "key", "colour", "icon",
                               "enabled", "last_sync", "last_error", "owner",
                               "last_count")})

    @property
    def fetch_url(self) -> str:
        # webcal:// is what Apple hands out. It is HTTPS with a different
        # scheme and nothing else, so it is swapped rather than special-cased.
        if self.url.startswith("webcal://"):
            return "https://" + self.url[len("webcal://"):]
        return self.url


class SubscriptionManager:
    """
    Fetching, parsing and folding feeds into the store.

    Subscribed events carry `source="subscribed"`, which the editor refuses to
    change - an edit made here would be silently undone by the next sync, and
    a change that quietly disappears is worse than one that is not offered.
    """

    def __init__(self, plugin, path):
        self.plugin = plugin
        self.client = plugin.client
        self.store = plugin.store
        self.path = path
        self.subscriptions: list = []
        self.load()

    ## -- persistence

    def migrate_events(self) -> int:
        """
        Fill in `subscription` on events that predate the field.

        Run once at startup. Without it the first sync of each feed cannot
        recognise its own events, and the duplicates it creates then look like
        the sync itself is broken.
        """
        fixed = 0
        keys = {s.key for s in self.subscriptions}
        # A snapshot, and the whole repair inside one batch: this rewrites
        # fields on many events, and a reader arriving half way through would
        # see some adopted and the rest not - which is the state that makes a
        # sync create duplicates in the first place.
        with self.store.batch():
            for event in self.store.snapshot():
                if event.subscription:
                    continue

                # Source is not checked. "subscribed" was missing from SOURCES, so
                # every one of these was rewritten to "local" on load - checking
                # for it here would skip exactly the events that need repairing.
                notes = event.notes or ""
                for key in keys:
                    marker = f"[{key}]"
                    if notes.startswith(marker):
                        event.subscription = key
                        event.source = "subscribed"
                        event.notes = notes[len(marker):].strip()
                        fixed += 1
                        break

                # The key carries it too, for an event whose notes were edited or
                # were empty enough to lose the marker.
                if not event.subscription and event.key.startswith("sub:"):
                    parts = event.key.split(":")
                    if len(parts) >= 2 and parts[1] in keys:
                        event.subscription = parts[1]
                        event.source = "subscribed"
                        fixed += 1
        if fixed:
            self.store.save()
            self.client.log("info", f"[Calendar] Adopted {fixed} subscribed "
                                    f"event(s) from an older format.")
        return fixed

    def orphans(self) -> int:
        """
        Subscribed events belonging to no current feed.

        A feed removed before its events were cleaned, or a key that changed -
        either way nothing will ever refresh them, so nothing should keep them.
        """
        keys = {s.key for s in self.subscriptions}
        dropped = self.store.drop_where(
            lambda _k, e: e.source == "subscribed" and e.subscription not in keys)
        if dropped:
            self.client.log("info", f"[Calendar] Dropped {dropped} orphaned "
                                    f"subscribed event(s).")
        return dropped

    def load(self) -> None:
        self.subscriptions = []
        try:
            if not self.path.is_file():
                return
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            self.client.log("warning", f"[Calendar] Could not read subscriptions: {e}")
            return
        for raw in payload.get("subscriptions") or []:
            sub = Subscription.from_dict(raw)
            if sub is not None:
                self.subscriptions.append(sub)

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(
                {"subscriptions": [s.to_dict() for s in self.subscriptions]},
                indent=2), encoding="utf-8")
            try:
                # A Google secret ICS URL is a credential - anyone holding it
                # can read that calendar.
                self.path.chmod(0o600)
            except OSError:
                pass
        except OSError as e:
            self.client.log("warning", f"[Calendar] Could not save subscriptions: {e}")

    ## -- managing

    def add(self, url: str, name: str = "", colour: str = "",
            owner: str = "") -> Subscription:
        # Converted on the way in rather than at fetch time, so what is saved
        # is what will be fetched and the page shows the address that works.
        sub = Subscription(url=ical_address(url), name=name, colour=colour,
                           owner=owner)
        self.subscriptions.append(sub)
        self.save()
        return sub

    def remove(self, key: str) -> bool:
        before = len(self.subscriptions)
        self.subscriptions = [s for s in self.subscriptions if s.key != key]
        if len(self.subscriptions) == before:
            return False
        # Its events go with it. Leaving them would strand rows nothing owns
        # and nothing can refresh.
        self._drop_events_of(key)
        self.save()
        return True

    def all(self) -> list:
        return list(self.subscriptions)

    def _drop_events_of(self, key: str) -> int:
        """
        Everything this feed put in the store.

        Matched on the field *and* on the old notes prefix. Events written
        before `subscription` existed carry no field at all, so a field-only
        match left them behind - and since the keys had also changed, the next
        sync added a second copy of each instead of replacing them.
        """
        legacy = f"[{key}]"
        # Through the store rather than into its dictionary. This ran on the
        # sync thread while the UI read the same events on the client tick,
        # which is where "dictionary changed size during iteration" came
        # from - and reaching past the store meant its lock could not help.
        return self.store.drop_where(
            lambda _k, e: e.source == "subscribed"
            and (e.subscription == key or (e.notes or "").startswith(legacy)))

    ## -- syncing

    def reset(self, key: str = "") -> int:
        """
        Drop what a feed put here and fetch it again from nothing.

        For when a feed and the panel have drifted apart - a partial sync, a
        changed URL, events that should have gone and did not. Cheaper to
        explain than a repair, and there is nothing here worth repairing:
        every one of these events came from somewhere else.
        """
        targets = [s for s in self.subscriptions
                   if not key or s.key == key]
        dropped = 0
        for sub in targets:
            dropped += self._drop_events_of(sub.key)
            sub.last_sync = 0.0
            sub.last_error = ""
        self.save()
        # Deliberately NOT one batch around the whole reset.
        #
        # A batch holds the store's lock, and every fetch below is a network
        # round trip with a thirty second timeout. Holding it across those
        # would stop every read in the app - the tiles, the widgets, the
        # reminder check - for as long as the slowest feed takes to answer.
        #
        # Each piece is still atomic on its own: sync() replaces one feed's
        # events in a single batch, and deduplicate() is one too. The gap this
        # leaves is the feed's events being briefly absent, which is what a
        # reset is: somebody asked for these to be cleared and fetched again.
        for sub in targets:
            if sub.enabled:
                self.sync(sub)

        # After the re-fetch, not before: a reset exists to fix a store that
        # has gone wrong, and anything the fetch itself brought in twice is
        # part of what needs fixing.
        removed = self.store.deduplicate()
        if removed:
            self.client.log("info",
                            f"[Calendar] Reset also removed {removed} duplicate(s).")
        return dropped

    def sync_all(self) -> dict:
        results = {}
        for sub in self.subscriptions:
            if sub.enabled:
                results[sub.key] = self.sync(sub)
        return results

    def sync(self, sub: Subscription) -> int:
        """
        Fetch one feed and replace its events. Returns how many landed.

        A failure is written onto the subscription rather than raised: this
        runs on a timer, and the page beside the address is where somebody
        would look to find out why it is empty.
        """
        import time
        try:
            request = urllib.request.Request(
                sub.fetch_url, headers={"User-Agent": "DesktopHomeAssistant"})
            with urllib.request.urlopen(request, timeout=30) as response:
                text = response.read(MAX_BYTES).decode("utf-8", "replace")
        except Exception as e:
            sub.last_error = str(e)[:140]
            self.save()
            self.client.log("warning", f"[Calendar] '{sub.name}' fetch failed: {e}")
            return 0

        # Fetched is not the same as found. The commonest wrong address for
        # Google is its "add this calendar" page, which sits directly under
        # the iCal address in Google's own settings and returns a perfectly
        # good HTML document - so the panel used to parse no events out of it
        # and report a clean sync of nothing. A feed with no events, a feed of
        # nothing but unsupported repeats, and an address that was never a
        # calendar all read as "0 event(s)", and only one of them is the
        # user's calendar being empty.
        if CALENDAR_MARKER not in text:
            better = ical_address(sub.url)
            sub.last_error = "that address is not a calendar feed"
            if better != sub.url:
                sub.last_error += f" - try {better[:80]}"
            self.save()
            self.client.log(
                "warning",
                f"[Calendar] '{sub.name}': {sub.fetch_url} returned "
                f"{len(text)} bytes with no {CALENDAR_MARKER} in them - it is "
                f"not an ICS feed.")
            return 0

        try:
            events = self._to_events(sub, parse_events(text))
        except Exception as e:
            sub.last_error = f"could not read the feed: {e}"[:140]
            self.save()
            return 0

        # Replaced wholesale rather than merged. A feed is the authority for
        # its own events, so "what it sent" is the answer - and an event
        # deleted upstream has to disappear here too, which a merge would miss.
        # One batch: the old rows go and the new ones arrive without anything
        # being able to look in between. Reading mid-way would have shown the
        # feed's events missing entirely, which is the state this replacement
        # passes through on its way to being correct.
        #
        # The file is written once at the end rather than once per event.
        with self.store.batch():
            dropped = self._drop_events_of(sub.key)
            for event in events:
                self.store.put(event)

        # Counted, because "replaced" and "added alongside" look identical on
        # screen and are one number apart in the log.
        if dropped and dropped != len(events):
            self.client.log("debug",
                            f"[Calendar] '{sub.name}': dropped {dropped}, "
                            f"added {len(events)}.")

        sub.last_sync = time.time()
        sub.last_error = ""
        sub.last_count = len(events)
        self.save()
        self.client.log("info", f"[Calendar] '{sub.name}': {len(events)} event(s).")
        return len(events)

    def _to_events(self, sub: Subscription, raw_events: list) -> list:
        """
        Every VEVENT, with a recurring series and its exceptions reconciled.

        A calendar does not send a changed occurrence as an edit. It sends the
        master VEVENT with its RRULE *and* a second VEVENT carrying the same
        UID plus a RECURRENCE-ID naming the date it replaces. Treating those
        as unrelated events meant the series generated an occurrence on that
        date and the override added another one beside it - which is where the
        duplicates came from, and why they were the odd-looking ones.
        """
        masters, overrides = [], []
        for raw in raw_events:
            if "RECURRENCE-ID" in raw:
                overrides.append(raw)
            else:
                masters.append(raw)

        # Which dates each series must skip, so the override stands alone.
        replaced: dict = {}
        for raw in overrides:
            uid = raw.get("UID", ({}, ""))[1].strip()
            params, value = raw["RECURRENCE-ID"]
            day, _ = parse_stamp(value, params)
            if uid and day:
                replaced.setdefault(uid, set()).add(day)

        out, seen = [], set()
        for raw in masters + overrides:
            uid = raw.get("UID", ({}, ""))[1].strip()
            for event in self._one(sub, raw, skip=sorted(replaced.get(uid, ()))):
                # Belt and braces: a feed repeating a UID would otherwise
                # write two events under one key and lose one of them.
                if event.key in seen:
                    continue
                seen.add(event.key)
                out.append(event)
        return out

    def _one(self, sub: Subscription, raw: dict, skip: list = None) -> list:
        summary = unescape(raw.get("SUMMARY", ({}, ""))[1]).strip()
        if not summary:
            return []

        start_params, start_value = raw.get("DTSTART", ({}, ""))
        day, clock = parse_stamp(start_value, start_params)
        if not day:
            return []

        end_params, end_value = raw.get("DTEND", ({}, ""))
        end_day, end_clock = parse_stamp(end_value, end_params) if end_value else ("", "")

        # An all-day DTEND is exclusive: a one-day event ends on the next day.
        if end_day and not clock and not end_clock:
            try:
                end_day = (date.fromisoformat(end_day) - timedelta(days=1)).isoformat()
                if end_day <= day:
                    end_day = ""
            except ValueError:
                end_day = ""
        if end_day == day:
            end_day = ""

        uid = raw.get("UID", ({}, ""))[1].strip() or f"{summary}:{day}"
        # An override is keyed by the date it replaces as well as its UID -
        # it shares that UID with the series it belongs to.
        recurrence = raw.get("RECURRENCE-ID")
        if recurrence is not None:
            uid = f"{uid}#{parse_stamp(recurrence[1], recurrence[0])[0]}"
        location = unescape(raw.get("LOCATION", ({}, ""))[1]).strip()
        description = unescape(raw.get("DESCRIPTION", ({}, ""))[1]).strip()

        repeats = self._repeats(raw)
        if repeats is None:
            # Understood well enough to import, or not imported at all. A
            # recurrence rule silently dropped would show one occurrence of a
            # weekly meeting and look like the calendar was wrong.
            self.client.log("warning",
                            f"[Calendar] '{sub.name}': skipped '{summary}' - "
                            f"unsupported repeat rule.")
            return []

        events = []
        for index, (repeat, until, offset) in enumerate(repeats):
            first = day
            if offset:
                try:
                    first = (date.fromisoformat(day) + timedelta(days=offset)).isoformat()
                except ValueError:
                    pass
            events.append(Event(
                title=summary,
                day=first,
                end_day=end_day,
                time=clock,
                end_time=end_clock,
                location=location,
                notes=description,
                subscription=sub.key,
                icon=sub.icon,
                colour=sub.colour,
                source="subscribed",
                owner=sub.owner,
                repeat=repeat,
                repeat_until=until,
                # Only a series has occurrences to skip. On a one-off - which
                # every override is - the list would be dead weight in the file.
                skip=list(skip or []) if repeat else [],
                # sha1, not hash(). Python randomises string hashing per
                # process, so every restart rebuilt the same events under new
                # keys - which is how a reload ended up with two of everything.
                #
                # The owner is in it as well as in its own field: the same feed
                # mirrored by two people is two sets of events, and a key built
                # from the UID alone would collapse them into one.
                key="sub:{}:{}".format(sub.key, hashlib.sha1(
                    f"{sub.owner}|{uid}|{index}".encode()).hexdigest()[:12]),
            ))
        return events

    def _repeats(self, raw: dict) -> Optional[list]:
        """
        [(repeat, until, day_offset)] for this event, or None if unsupported.

        A weekly rule listing several days becomes one event per day rather
        than a rule we cannot express - which is how Google writes "every
        Monday, Wednesday and Friday", and by far the commonest thing that
        would otherwise be dropped.
        """
        rule_value = raw.get("RRULE", ({}, ""))[1]
        if not rule_value:
            return [("", "", 0)]

        rule = parse_rrule(rule_value)
        repeat = FREQUENCIES.get(rule.get("FREQ", "").upper())
        if repeat is None:
            return None
        if rule.get("INTERVAL") and rule["INTERVAL"] != "1":
            # Every other week is not something the store can express yet.
            return None
        try:
            first = date.fromisoformat(parse_stamp(
                raw.get("DTSTART", ({}, ""))[1],
                raw.get("DTSTART", ({}, {}))[0])[0])
        except ValueError:
            first = None

        # BYMONTH and BYMONTHDAY are usually redundant on a yearly or monthly
        # rule - Google writes "12 March, every year" as FREQ=YEARLY with the
        # month and day repeated back, and refusing those dropped every
        # birthday and anniversary in the feed.
        #
        # Only refused when they disagree with DTSTART, which is the case that
        # genuinely means something we cannot express.
        redundant = True
        if first is not None:
            if rule.get("BYMONTHDAY") and rule["BYMONTHDAY"] != str(first.day):
                redundant = False
            if rule.get("BYMONTH") and rule["BYMONTH"] != str(first.month):
                redundant = False
        elif rule.get("BYMONTHDAY") or rule.get("BYMONTH"):
            redundant = False

        if not redundant:
            return None
        if any(key in rule for key in ("BYSETPOS", "BYWEEKNO", "BYYEARDAY")):
            return None

        until = ""
        if rule.get("UNTIL"):
            until, _ = parse_stamp(rule["UNTIL"], {})
        elif rule.get("COUNT"):
            # Turned into an end date, since that is what the store stores.
            try:
                count = max(1, int(rule["COUNT"]))
                step = {"daily": 1, "weekly": 7, "monthly": 30, "yearly": 365}[repeat]
                until = (first + timedelta(days=step * (count - 1))).isoformat()
            except (ValueError, KeyError, TypeError, AttributeError):
                until = ""

        byday = rule.get("BYDAY", "")
        if not byday or repeat != "weekly":
            # A yearly rule may name a weekday alongside a fixed date; the
            # date is what matters and the day is a restatement of it.
            if byday and repeat not in ("weekly", "yearly", "monthly"):
                return None
            return [(repeat, until, 0)]

        days = [WEEKDAYS.get(part.strip()[-2:].upper()) for part in byday.split(",")]
        if any(d is None for d in days) or first is None:
            return None

        out = []
        for weekday in sorted(set(days)):
            offset = (weekday - first.weekday()) % 7
            out.append((repeat, until, offset))
        return out
