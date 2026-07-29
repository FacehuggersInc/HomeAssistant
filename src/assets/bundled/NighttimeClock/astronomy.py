"""
Where the sun and moon are, worked out rather than asked for.

Both are arithmetic on a date and a position, so there is no reason to spend a
network request on them. That matters more than it sounds for a panel that is
often the only thing awake in the house at 4am: this keeps working with the
router off, and it cannot be the thing that wakes the network.

The moon is the standard mean-phase approximation and the sun is the NOAA
solar position algorithm, both accurate to about a minute for this purpose -
which is a great deal more than a wall clock saying "sunrise in 2h 14m" needs.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

#a synodic month: new moon to new moon
SYNODIC = 29.530588853
#a known new moon, 2000-01-06 18:14 UTC, as a Julian day
KNOWN_NEW_MOON = 2451550.26


def julian_day(when: datetime) -> float:
    """Days since the Julian epoch, for a UTC datetime."""
    when = when.astimezone(timezone.utc) if when.tzinfo else when.replace(
        tzinfo=timezone.utc)
    year, month = when.year, when.month
    day = (when.day + when.hour / 24.0 + when.minute / 1440.0
           + when.second / 86400.0)
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + (a // 4)
    return (int(365.25 * (year + 4716)) + int(30.6001 * (month + 1))
            + day + b - 1524.5)


## ── Moon ─────────────────────────────────────────────────────────────────────

# Upper edge of each band. The four named instants get about a day and a half
# either side rather than the exact moment: a quarter moon is a specific
# instant astronomically, but a label that only appeared for forty minutes a
# fortnight would never be seen.
PHASE_NAMES = [
    (0.03, "New moon"),
    (0.22, "Waxing crescent"),
    (0.28, "First quarter"),
    (0.47, "Waxing gibbous"),
    (0.53, "Full moon"),
    (0.72, "Waning gibbous"),
    (0.78, "Last quarter"),
    (0.97, "Waning crescent"),
    (1.01, "New moon"),
]


def moon_age(when: datetime = None) -> float:
    """Days since the last new moon, 0 to ~29.53."""
    when = when or datetime.now()
    days = julian_day(when) - KNOWN_NEW_MOON
    return days % SYNODIC


def moon_phase(when: datetime = None) -> float:
    """
    0.0 new, 0.25 first quarter, 0.5 full, 0.75 last quarter.

    The mean phase, which ignores the moon's elliptical orbit. That is worth
    up to about half a day at the extremes and is invisible in a drawing of a
    crescent.
    """
    return moon_age(when) / SYNODIC


def moon_name(when: datetime = None) -> str:
    phase = moon_phase(when)
    for edge, name in PHASE_NAMES:
        if phase < edge:
            return name
    return "New moon"


def moon_illumination(when: datetime = None) -> float:
    """How much of the disc is lit, 0 to 1."""
    return (1.0 - math.cos(2.0 * math.pi * moon_phase(when))) / 2.0


def moon_waxing(when: datetime = None) -> bool:
    """Whether it is filling. Decides which side the crescent sits on."""
    return moon_phase(when) < 0.5


## ── Sun ──────────────────────────────────────────────────────────────────────

def _solar_times(day: datetime, latitude: float, longitude: float,
                 zenith: float = 90.833) -> tuple:
    """
    (sunrise, sunset) as UTC datetimes for a date, or (None, None).

    None means the sun does not cross that zenith at all where you are on that
    day - a polar summer or winter. Returning None rather than a made-up time
    is the point: "sunrise in 2h" is worse than nothing above the arctic
    circle.
    """
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return None, None

    day_of_year = day.timetuple().tm_yday
    rising_hour = longitude / 15.0
    out = []

    for rising in (True, False):
        approx = day_of_year + ((6 if rising else 18) - rising_hour) / 24.0
        mean_anomaly = (0.9856 * approx) - 3.289
        true_longitude = (mean_anomaly
                          + (1.916 * math.sin(math.radians(mean_anomaly)))
                          + (0.020 * math.sin(math.radians(2 * mean_anomaly)))
                          + 282.634) % 360.0

        right_ascension = math.degrees(math.atan(
            0.91764 * math.tan(math.radians(true_longitude)))) % 360.0
        # Into the same quadrant as the longitude, or the time is out by hours.
        right_ascension += (((true_longitude // 90) * 90)
                            - ((right_ascension // 90) * 90))
        right_ascension /= 15.0

        sin_dec = 0.39782 * math.sin(math.radians(true_longitude))
        cos_dec = math.cos(math.asin(sin_dec))

        cos_hour = ((math.cos(math.radians(zenith))
                     - (sin_dec * math.sin(math.radians(latitude))))
                    / (cos_dec * math.cos(math.radians(latitude))))
        if cos_hour > 1 or cos_hour < -1:
            return None, None      # never rises, or never sets

        hour = math.degrees(math.acos(cos_hour))
        if rising:
            hour = 360.0 - hour
        hour /= 15.0

        mean_time = hour + right_ascension - (0.06571 * approx) - 6.622
        universal = (mean_time - rising_hour) % 24.0

        midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        out.append(midnight + timedelta(hours=universal))

    rise, set_ = out
    # West of Greenwich the sun sets after midnight UTC, so both times were
    # placed on the same UTC date and the sunset came out BEFORE the sunrise -
    # a negative day length, and "sunset in -8 hours".
    if set_ <= rise:
        set_ += timedelta(days=1)
    return rise, set_


def sun_times(latitude: float, longitude: float, when: datetime = None,
              local: bool = True) -> tuple:
    """(sunrise, sunset) for the day `when` falls in."""
    when = when or datetime.now()
    reference = when if when.tzinfo else when.replace(
        tzinfo=datetime.now().astimezone().tzinfo)
    rise, set_ = _solar_times(reference.astimezone(timezone.utc),
                             latitude, longitude)
    if rise is None:
        return None, None
    if local:
        zone = reference.tzinfo
        return rise.astimezone(zone), set_.astimezone(zone)
    return rise, set_


def next_sun_event(latitude: float, longitude: float,
                   when: datetime = None) -> tuple:
    """
    ("sunrise"|"sunset", datetime, seconds_away), or (None, None, 0).

    Looks into tomorrow when both of today's are behind: at 11pm the next
    thing the sun does is rise, and it is not today.
    """
    when = when or datetime.now()
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.now().astimezone().tzinfo)

    for offset in (0, 1):
        day = when + timedelta(days=offset)
        rise, set_ = sun_times(latitude, longitude, day)
        if rise is None:
            return None, None, 0
        for name, moment in (("sunrise", rise), ("sunset", set_)):
            if moment > when:
                return name, moment, int((moment - when).total_seconds())
    return None, None, 0


def describe_wait(seconds: int) -> str:
    """'2h 14m', '46m', 'now'."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "now"
    hours, minutes = divmod(seconds // 60, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"
