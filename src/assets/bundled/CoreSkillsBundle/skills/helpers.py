"""
What more than one skill group needs.

The patterns, the stop-word lists and the parsers - anything two groups would
otherwise each carry a copy of. Nothing in here knows about a Skill or about
the plugin: text in, values out, which is what makes it the part of this
bundle that can be exercised without a panel.
"""

from __future__ import annotations

import time as _time

import re
from datetime import datetime, timedelta


# Units a duration is counted in, as LEMMAs so one entry covers the plural.
# Abbreviations ("mins", "secs") are expanded upstream by
# normalize.expand_units before a skill sees them.
DURATION_UNITS = ["second", "minute", "hour", "day"]

# What sits between two halves of one duration. "an hour AND a half";
# "two hours THIRTY" has nothing between them at all, which is why every part
# after the first is optional rather than joined.
DURATION_JOINERS = ["and", "plus", "&"]

# A clock time as one token. "4:40" is a single NUM token whose `like_num` is
# FALSE, so LIKE_NUM never matches it - the shape has to be asked for.
_CLOCK = {"TEXT": {"REGEX": r"^\d{1,2}([:.]\d{2})?$"}}
_MERIDIEM = {"LOWER": {"IN": ["am", "pm", "a.m.", "p.m.", "am.", "pm."]}}

# Longest first, so the widest match wins on the ones that overlap.
ALARM_TIME_PATTERNS = [
    [{"LOWER": {"IN": ["half", "quarter"]}},
     {"LOWER": {"IN": ["past", "to"]}}, _CLOCK, dict(_MERIDIEM, OP="?")],
    [_CLOCK, _MERIDIEM],
    [_CLOCK, {"LOWER": "o'clock", "OP": "?"}],
    [{"LOWER": {"IN": ["noon", "midday", "midnight"]}}],
]

# "in 20 minutes", "10 minutes from now", "an hour and a half from now".
#"an hour" is a number said as an article. `LIKE_NUM` is False for "a" and
#"an", so a pattern built only on it misses the commonest way of saying one
#of something - "wake me up in an hour" extracted nothing at all.
_A_NUMBER = {"IN": ["a", "an", "one", "half"]}

ALARM_AFTER_PATTERNS = [
    [{"LIKE_NUM": True}, {"LEMMA": {"IN": DURATION_UNITS}},
     {"LOWER": {"IN": DURATION_JOINERS}, "OP": "?"},
     {"LIKE_NUM": True, "OP": "?"},
     {"LEMMA": {"IN": DURATION_UNITS}, "OP": "?"},
     {"LOWER": {"IN": ["from", "in"]}, "OP": "?"},
     {"LOWER": {"IN": ["now", "time"]}, "OP": "?"}],
    [{"LOWER": _A_NUMBER}, {"LEMMA": {"IN": DURATION_UNITS}},
     {"LOWER": {"IN": DURATION_JOINERS}, "OP": "?"},
     {"LOWER": _A_NUMBER, "OP": "?"},
     {"LIKE_NUM": True, "OP": "?"},
     # "half" closes "an hour and a half", which otherwise ends at the "a"
     # and comes out as an hour and one minute.
     {"LOWER": "half", "OP": "?"},
     {"LEMMA": {"IN": DURATION_UNITS}, "OP": "?"}],
]

ALARM_DAY_PATTERNS = [
    [{"LOWER": {"IN": ["today", "tomorrow", "tonight"]}}],
    [{"LOWER": {"IN": ["monday", "tuesday", "wednesday", "thursday",
                       "friday", "saturday", "sunday"]}}],
]

ALARM_PART_PATTERNS = [
    [{"LOWER": "in", "OP": "?"}, {"LOWER": "the", "OP": "?"},
     {"LOWER": {"IN": ["morning", "afternoon", "evening", "night",
                       "tonight"]}}],
]

ALARM_REPEAT_PATTERNS = [
    [{"LOWER": {"IN": ["daily", "everyday", "repeating"]}}],
    [{"LOWER": "every"}, {"LOWER": {"IN": ["day", "morning", "night"]}}],
]

# The same idea as TIMER_NAME_STOPWORDS: what may sit before "alarm" without
# being its name. Clock words as well as units, or "the 7 am alarm" comes back
# as an alarm called "am".
ALARM_NAME_STOPWORDS = [
    "alarm", "alarms", "repeating",
    "second", "seconds", "minute", "minutes", "hour", "hours", "day", "days",
    "am", "pm", "a.m.", "p.m.", "o'clock", "noon", "midday", "midnight",
    "morning", "afternoon", "evening", "night", "tonight",
    "today", "tomorrow", "daily", "everyday",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday",
    "all", "every", "next", "new", "another", "other", "scheduled",
    "and", "plus",
]


# Words that may sit immediately before "timer" without being its name.
# Shared by set-timer and cancel-timer: two copies would drift, and the whole
# point is that "a 5 minute timer" is not a timer called "minute".
TIMER_NAME_STOPWORDS = [
    "timer", "timers",
    "second", "seconds", "minute", "minutes", "hour", "hours", "day", "days",
    "all", "every", "running", "remaining", "new", "another", "other",
    # Joiners inside a compound duration. Without these, "the 1 hour and 10
    # minute timer" hands back a timer named "and" when the unit before
    # "timer" is skipped.
    "and", "plus",
]






#Words that carry no meaning in a request and only dilute a match.
FILLER = ("the", "a", "an", "my", "me", "please", "to", "up", "for")

#Words that ask for a page without naming one. Dropped before matching, or
#"show me the home page" is measured as three words against one and scores a
#third of what it should.
PAGE_VERBS = ("show", "open", "go", "goto", "take", "bring", "display",
              "switch", "page", "screen")



def _clean_words(text: str, drop: tuple = ()) -> str:
    """The words worth matching on, in order."""
    words = str(text or "").lower().split()
    skip = set(FILLER) | set(drop)
    return " ".join(w for w in words if w and w not in skip).strip()


def _overlap(said: str, candidate: str) -> float:
    """
    How much of what was said appears in the candidate, 0 to 1.

    Words rather than characters, and only whether each word is present rather
    than where. "scryfall" against "Advanced Search - Scryfall" scores 1: the
    part somebody says is the part they remember, and it is rarely the whole
    title.
    """
    said_words = [w for w in _clean_words(said).split() if w]
    if not said_words:
        return 0.0
    hay = str(candidate or "").lower()
    hits = sum(1 for w in said_words if w in hay)
    return hits / len(said_words)


#Words that name a time without a number on them.
NAMED_TIMES = {
    "noon": (12, 0), "midday": (12, 0), "midnight": (0, 0),
}

#What half of the day a phrase puts the hour in, when no am/pm was said.
DAY_PARTS = {
    "morning": (5, 11), "afternoon": (12, 17), "evening": (17, 21),
    "night": (21, 23), "tonight": (18, 23),
}

#Which day. Anything not here is "the next one of these there is".
DAY_WORDS = {"today": 0, "tonight": 0, "tomorrow": 1}

WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6}


def _clock_parts(text: str):
    """
    `(hour, minute, meridiem)` from "4:40 pm", "8", "half past 7", "noon".

    `meridiem` is "am", "pm" or "" - empty meaning nobody said, which is the
    case the caller has to resolve rather than guess at silently.
    """
    import re

    said = " ".join(str(text or "").lower().split())
    if not said:
        return None

    for word, (hour, minute) in NAMED_TIMES.items():
        if word in said:
            return hour, minute, "am" if hour < 12 else "pm"

    meridiem = ""
    match = re.search(r"\b([ap])\.?\s?m\.?\b", said)
    if match:
        meridiem = match.group(1) + "m"

    # "half past seven", "quarter to eight" - said before the number is read,
    # because they change what the number means.
    offset = 0
    if re.search(r"\bhalf\s+past\b", said):
        offset = 30
    elif re.search(r"\b(a\s+)?quarter\s+past\b", said):
        offset = 15
    elif re.search(r"\b(a\s+)?quarter\s+to\b", said):
        offset = -15
    elif re.search(r"\bhalf\s+to\b", said):
        offset = -30

    clock = re.search(r"\b(\d{1,2})\s*[:.]\s*(\d{2})\b", said)
    if clock:
        hour, minute = int(clock.group(1)), int(clock.group(2))
    else:
        bare = re.search(r"\b(\d{1,2})\b", said)
        if not bare:
            return None
        hour, minute = int(bare.group(1)), 0

    if offset:
        total = hour * 60 + minute + offset
        hour, minute = (total // 60) % 24, total % 60

    if hour > 23 or minute > 59:
        return None
    return hour, minute, meridiem


def _alarm_epoch(time_text: str = "", day_text: str = "",
                 part_text: str = "", now: float = None) -> float:
    """
    Seconds since the epoch for a spoken clock time, or 0.

    Everything ambiguous is resolved the way somebody standing in the room
    would resolve it:

    | Said                     | Means                                      |
    |--------------------------|--------------------------------------------|
    | a time already past      | tomorrow                                   |
    | `8` with no am/pm        | whichever 8 comes first from now           |
    | `8` with "in the morning"| 8 am, today or tomorrow                    |
    | `8 tomorrow`             | 8 am tomorrow, because that is what a bare |
    |                          | hour with a day on it means                |
    """
    from datetime import datetime, timedelta

    parts = _clock_parts(time_text)
    if parts is None:
        return 0.0
    hour, minute, meridiem = parts

    now = _time.time() if now is None else now
    current = datetime.fromtimestamp(now)

    said_day = " ".join(str(day_text or "").lower().split())
    said_part = " ".join(str(part_text or "").lower().split())

    # Which day, if one was named at all.
    offset_days = None
    for word, days in DAY_WORDS.items():
        if word in said_day or word in said_part:
            offset_days = days
            break
    if offset_days is None:
        for word, index in WEEKDAYS.items():
            if word in said_day:
                ahead = (index - current.weekday()) % 7
                offset_days = ahead or 7
                break

    # 12 hour to 24 hour.
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    elif not meridiem and hour <= 12:
        window = None
        for word, span in DAY_PARTS.items():
            if word in said_part or word in said_day:
                window = span
                break
        if window is not None:
            low, high = window
            if not (low <= hour <= high):
                if hour + 12 <= high:
                    hour += 12
                elif hour == 12 and low == 0:
                    hour = 0
        elif offset_days is not None:
            # A bare hour with a day on it is the MORNING, and left alone.
            # "8 tomorrow" is eight in the morning; so is "6 tomorrow".
            # Somebody meaning the evening says so, and an alarm that is
            # mostly a wake-up should not guess otherwise.
            pass
        else:
            # Whichever comes first. "set an alarm at 8" at nine in the
            # morning means eight in the evening, not eight tomorrow.
            morning = current.replace(hour=hour % 12, minute=minute,
                                      second=0, microsecond=0)
            evening = current.replace(hour=(hour % 12) + 12, minute=minute,
                                      second=0, microsecond=0)
            if morning.timestamp() <= now < evening.timestamp():
                hour = (hour % 12) + 12
            elif evening.timestamp() <= now:
                hour = hour % 12

    target = current.replace(hour=hour % 24, minute=minute,
                             second=0, microsecond=0)
    if offset_days:
        target += timedelta(days=offset_days)
    # Already gone, and no day was named: the next one there is.
    if offset_days is None and target.timestamp() <= now:
        target += timedelta(days=1)
    return float(target.timestamp())


#What a name span may carry that is not part of the name. The Matcher hands
#back the whole span it matched - "called laundry", "bread alarm" - and the
#trigger word is how it was found rather than what it is called.
_NAME_LEADERS = ("call it", "called it", "name it", "named it",
                 "call", "called", "name", "named")


def _clean_label(text: str, nouns=("timer", "timers", "alarm", "alarms")) -> str:
    """
    The name out of a name span.

    "call it Eggs" is Eggs; "a bread alarm" is bread. Left alone, a timer
    started this way is called "laundry timer" and announces itself as "the
    laundry timer timer".
    """
    said = " ".join(str(text or "").split())
    if not said:
        return ""

    lowered = said.lower()
    for lead in _NAME_LEADERS:
        if lowered.startswith(lead + " "):
            said = said[len(lead) + 1:]
            lowered = said.lower()
            break

    words = said.split()
    while words and words[-1].lower().strip(".,") in nouns:
        words.pop()
    # Determiners left at the front by a lead-in - "call it the eggs one".
    while words and words[0].lower() in ("a", "an", "the", "my"):
        words.pop(0)
    return " ".join(words).strip()


def _spoken_duration(text: str) -> float:
    """
    Seconds from a phrase like "10 minutes" or "1 hour 30 minutes".

    Transcript normalisation turns most spoken numbers into digits before this
    sees them, but not all of it - so a small word list covers what is left
    rather than trusting that every "five" arrived as a "5".
    """
    import re

    if not text:
        return 0.0

    # "a"/"an" are a soft one: they only count when nothing else is pending,
    # or "half an hour" reads as "half", then "an" overwriting it with 1, then
    # an hour - and a thirty minute timer becomes a sixty minute one.
    soft = {"a", "an", "the"}
    words = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
        "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
        "forty-five": 45, "fortyfive": 45, "fifty": 50, "sixty": 60,
        "half": 0.5, "quarter": 0.25,
    }
    units = {
        "second": 1, "seconds": 1, "sec": 1, "secs": 1,
        "minute": 60, "minutes": 60, "min": 60, "mins": 60,
        "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
    }

    tokens = re.findall(r"[a-z0-9\-\.]+", str(text).lower())
    total = 0.0
    pending = None
    #The last unit counted in, so a number left over at the end has something
    #to be measured against. See the tail below.
    last_unit = None

    for token in tokens:
        if token in units:
            # A unit with no number in front of it means one of them:
            # "set a timer for an hour" arrives here as just "hour".
            total += (1.0 if pending is None else pending) * units[token]
            last_unit = units[token]
            pending = None
            continue
        try:
            pending = float(token)
            continue
        except ValueError:
            pass
        if token in soft:
            if pending is None:
                pending = 1
            continue
        if token in words:
            value = words[token]
            # "twenty five" is one number, not two. normalize.py usually joins
            # compounds before a skill sees them, but not always, and losing
            # the tens turns a 25 minute timer into a 5 minute one.
            if (pending is not None and pending >= 20
                    and pending % 10 == 0 and value < 10):
                pending += value
            else:
                pending = value

    # "set a timer for 10" with no unit at all - minutes is what people mean.
    if total == 0 and pending:
        return float(pending * 60)

    # A number left over AFTER a unit. Two readings, and the size of it says
    # which:
    #
    #   "an hour and a half"  - a fraction, so the same unit again.
    #   "an hour thirty"      - a whole number, so the next unit down. Nobody
    #                           means thirty hours.
    if pending and last_unit:
        if pending < 1:
            total += pending * last_unit
        else:
            smaller = {3600: 60, 60: 1}.get(last_unit)
            if smaller:
                total += pending * smaller
    return float(total)


def _clock(moment) -> str:
    """
    A time somebody would say out loud: "3 PM", "3:20 PM".

    Twelve hour, no leading zero, and the minutes only when there are any -
    "3:00 PM" is one more thing to read than "3 PM" and says nothing extra.
    Speech gets the same string as the panel, so what is heard and what is
    shown cannot disagree.
    """
    try:
        pattern = "%I:%M %p" if moment.minute else "%I %p"
        return moment.strftime(pattern).lstrip("0")
    except Exception:
        return str(moment)


def _spoken_wait(seconds) -> str:
    """
    A gap as somebody would say it: "3 hours and 33 minutes", "46 minutes".

    `astronomy.describe_wait` gives "3h 33m", which is right for a label on a
    widget and wrong for a voice - a speech model reads it as "three em
    thirty-three em", or as nothing at all. The compact form still goes on
    the panel; this is what gets said.
    """
    try:
        seconds = max(0, int(seconds))
    except (TypeError, ValueError):
        return ""
    if seconds < 60:
        return "less than a minute"

    hours, minutes = divmod(seconds // 60, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return " and ".join(parts)
