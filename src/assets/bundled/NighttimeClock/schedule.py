"""
When it is night, and how bright the panel should be.

No Qt and no client: the whole schedule is arithmetic on a clock, and the
awkward parts - a window that crosses midnight, a fade that starts on the
previous day, times entered wrong - are much easier to be sure of when they
can be tested directly.
"""

from __future__ import annotations

from datetime import datetime

#Phases, in the order a day passes through them
DAY = "day"
DIMMING = "dimming"
NIGHT = "night"

MINUTES_PER_DAY = 24 * 60


def parse_time(text: str, fallback: str = "00:00") -> int:
    """
    "21:00" -> 1260, as minutes past midnight.

    Falls back rather than raising: this comes from a settings field somebody
    typed into, and a panel that refuses to start because of "9pm" in the
    wrong box is worse than one that uses its default and says so.
    """
    for candidate in (text, fallback):
        try:
            hours, _, minutes = str(candidate or "").strip().partition(":")
            hour, minute = int(hours), int(minutes or 0)
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return hour * 60 + minute
        except (TypeError, ValueError):
            continue
    return 0


def now_minutes(when: datetime = None) -> int:
    when = when or datetime.now()
    return when.hour * 60 + when.minute


def in_window(minute: int, start: int, end: int) -> bool:
    """
    Whether a time falls inside a window that may cross midnight.

    21:00 to 07:00 is not `start <= minute < end` - that comparison is false
    for every minute of it. Night is the normal case here, so this is the
    normal case too.
    """
    if start == end:
        return False
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def minutes_until(minute: int, target: int) -> int:
    """How far ahead `target` is, wrapping past midnight."""
    return (target - minute) % MINUTES_PER_DAY


class Schedule:
    """Where in the day/night cycle the panel is, and how bright to be."""

    def __init__(self, night: str = "21:00", day: str = "07:00",
                 lead_minutes: int = 60, night_brightness: int = 12,
                 dim_enabled: bool = True):
        self.night = parse_time(night, "21:00")
        self.day = parse_time(day, "07:00")
        self.lead = max(0, int(lead_minutes or 0))
        self.night_brightness = max(1, min(100, int(night_brightness or 1)))
        self.dim_enabled = bool(dim_enabled)

    ## -- phase

    def is_night(self, minute: int) -> bool:
        return in_window(minute, self.night, self.day)

    def phase(self, minute: int) -> str:
        if self.is_night(minute):
            return NIGHT
        if self.dim_enabled and self.lead and \
                minutes_until(minute, self.night) <= self.lead:
            return DIMMING
        return DAY

    def is_dimming(self, minute: int) -> bool:
        """In the lead window: not night yet, and on the way down."""
        return self.phase(minute) == DIMMING

    ## -- brightness

    def brightness(self, minute: int, start: int = 100) -> int:
        """
        What the level should be, with nobody about.

        The fade runs from `start` down to the night level across the lead
        time, so the last hour before bed gets gradually dimmer rather than
        the room changing all at once at nine o'clock.

        `start` is where the fade begins, and it is the caller's business
        because only the caller knows what the screen is actually at. Fading
        from a fixed 100 means a screen somebody had already turned down to
        75 jumps UP to full the moment the lead window opens, and then dims
        from there - brighter than it was, in the hour before bed, which is
        the opposite of the point.

        A `start` already at or below the night level leaves it alone: it is
        darker than the night setting asks for, and somebody put it there.
        """
        start = max(1, min(100, int(start or 100)))
        if not self.dim_enabled:
            return self.night_brightness if self.is_night(minute) else start

        if self.is_night(minute):
            return self.night_brightness

        remaining = minutes_until(minute, self.night)
        if not self.lead or remaining > self.lead:
            return start

        if start <= self.night_brightness:
            return start

        # remaining == lead -> start, remaining == 0 -> night level
        progress = 1.0 - (remaining / float(self.lead))
        span = start - self.night_brightness
        return int(round(start - span * progress))

    ## -- transitions

    def crossed_into_night(self, previous: int, minute: int) -> bool:
        return self.is_night(minute) and not self.is_night(previous)

    def crossed_into_day(self, previous: int, minute: int) -> bool:
        return self.is_night(previous) and not self.is_night(minute)

    def describe(self, minute: int) -> str:
        phase = self.phase(minute)
        if phase == NIGHT:
            return f"Night until {self.clock(self.day)}."
        if phase == DIMMING:
            left = minutes_until(minute, self.night)
            return f"Dimming, {left} minute{'' if left == 1 else 's'} to night."
        return f"Day until {self.clock(self.night)}."

    @staticmethod
    def clock(minute: int) -> str:
        return f"{minute // 60:02d}:{minute % 60:02d}"
