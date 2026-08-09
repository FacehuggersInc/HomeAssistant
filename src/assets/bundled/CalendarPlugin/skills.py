"""
What the assistant can be asked about the calendar.

Every answer comes from the published registry, so a phrase and the widget
beside it can never disagree about what is next.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from src.assistant.skill import Skill
from . import store

if TYPE_CHECKING:
    from src.main import Client


def build(plugin) -> list:
    """The plugin's skills, as a list ready for SKILLS.register()."""
    wake = plugin.client.wake_word
    key = "calendar"
    answers = _Answers(plugin)

    return [
        Skill(
            wake_word=wake, plugin_key=key, skill_key="calendar-next-event", kind="act",
            owns=["calendar", "appointment", "agenda"],
            examples=[
                "what is next on my calendar",
                "what is my next event",
                "what have I got coming up",
                "what is next",
                "when is my next event",
                "what is my next appointment",
                "when is my next appointment",
                "what is next on my agenda",
            ],
            func=answers.next_event,
        ),
        Skill(
            wake_word=wake, plugin_key=key, skill_key="calendar-today", kind="act",
            owns=["calendar", "appointment", "agenda"],
            examples=[
                "what is on today",
                "what is happening today",
                "what have I got on today",
                "anything on today",
                "what is on my calendar today",
            ],
            func=answers.today,
        ),
        Skill(
            wake_word=wake, plugin_key=key, skill_key="calendar-tomorrow", kind="act",
            owns=["calendar", "appointment", "agenda"],
            examples=[
                "what is on tomorrow",
                "what is happening tomorrow",
                "anything on tomorrow",
                "what have I got on tomorrow",
                # The word this skill owns has to appear in something it
                # is scored against. Owning is a claim about vocabulary,
                # not a licence to win a phrase it matched no part of.
                "what is on my calendar tomorrow",
                "whats on my agenda tomorrow",
            ],
            func=answers.tomorrow,
        ),
        Skill(
            wake_word=wake, plugin_key=key, skill_key="calendar-this-week", kind="act",
            owns=["calendar", "appointment", "agenda"],
            examples=[
                "what is on this week",
                "what have I got this week",
                "what is happening this week",
                "anything on this week",
                "what is on my calendar this week",
                "whats on my agenda this week",
            ],
            func=answers.this_week,
        ),
        Skill(
            wake_word=wake, plugin_key=key, skill_key="calendar-next-holiday", kind="act",
            owns=["calendar", "appointment", "agenda"],
            examples=[
                "when is the next holiday",
                "what is the next holiday",
                "how long until the next holiday",
                "when is the next public holiday",
            ],
            func=answers.next_holiday,
        ),
        # Named holidays, which the skill above cannot answer: "when is the
        # next holiday" and "when is Thanksgiving" are different questions,
        # and one of them names a date the panel already knows.
        Skill(
            wake_word=wake, plugin_key=key, skill_key="calendar-named-holiday",
            kind="ask",
            # Frames, because the question WORD is the whole difference here
            # and content-word scoring throws it away: "when is christmas"
            # and "what is christmas" both reduce to {christmas}, scored an
            # exact match against the same example, and asking what
            # Christmas is returned a date.
            frames=[
                "when is {holiday}",
                "whens {holiday}",
                "when is {holiday} this year",
                "what day is {holiday}",
                "what day is {holiday} on",
                "what date is {holiday}",
                "what day of the week is {holiday}",
                "how long until {holiday}",
                "how many days until {holiday}",
                "how many days till {holiday}",
            ],
            # And typed, because the shape alone is ambiguous. "When is the
            # next holiday" and "when is my dentist appointment" fit `when is
            # {holiday}` exactly as well as "when is christmas" does - only
            # the data separates them.
            #
            # `holiday_named` is a whole-word alias lookup: no request, no
            # disk, safe to run inside matching.
            holes={"holiday": lambda text: bool(store.holiday_named(text))},
            wants_phrase=True,
            func=answers.named_holiday,
        ),
        Skill(
            wake_word=wake, plugin_key=key, skill_key="calendar-how-long", kind="act",
            owns=["calendar", "appointment", "agenda"],
            examples=[
                "how long until my next event",
                "how long until the next thing",
                "how long have I got until my next event",
            ],
            func=answers.how_long,
        ),
    ]


class _Answers:
    """
    The handlers, sharing one way of phrasing things.

    Kept together rather than as loose functions so a change to how a list of
    events is read out lands in every answer that reads one.
    """

    # Spoken, not printed. Six events in a row is a wall of speech nobody
    # follows - the rest is a count.
    MAX_SPOKEN = 3

    def __init__(self, plugin):
        self.plugin = plugin
        self.client = plugin.client

    ## -- helpers

    def _api(self):
        try:
            return self.client.public.calendar
        except Exception:
            return None

    def _say(self, text: str, lines: list = None,
             icon: str = "mdi.calendar", tint: str = "#4f9de0") -> None:
        """
        Spoken and shown. Spoken replies can be turned off and a backend can
        fail to load, and a panel that never speaks should still answer the
        question - and an answer with three events in it is a list to read,
        not a line to catch as it goes past.
        """
        self.client.answer(icon, text, lines or [], tint=tint, speak=text)

    def _describe(self, event, api, with_when: bool = True) -> str:
        parts = [event.title]
        if with_when:
            parts.append(api["describe_gap"](event))
        if not event.all_day:
            parts.append(f"at {self._spoken_time(event.time)}")
        return " ".join(p for p in parts if p)

    @staticmethod
    def _spoken_time(clock: str) -> str:
        """14:30 reads badly out loud; half past two reads as a person would."""
        try:
            hour, _, minute = clock.partition(":")
            hour, minute = int(hour), int(minute or 0)
        except (ValueError, TypeError):
            return clock
        suffix = "in the morning" if hour < 12 else "in the evening" if hour >= 18 else "in the afternoon"
        display = hour % 12 or 12
        if minute == 0:
            return f"{display} {suffix}"
        return f"{display} {minute:02d} {suffix}"

    def _list(self, events, api, when_label: str) -> str:
        if not events:
            return f"Nothing on {when_label}."

        spoken = events[:self.MAX_SPOKEN]
        names = [self._describe(e, api, with_when=False) for e in spoken]
        if len(names) == 1:
            body = names[0]
        else:
            body = ", ".join(names[:-1]) + f" and {names[-1]}"

        rest = len(events) - len(spoken)
        tail = f", and {rest} more" if rest > 0 else ""
        return f"On {when_label}: {body}{tail}."

    ## -- answers

    def next_event(self) -> None:
        api = self._api()
        event = api["next_event"]() if api else None
        if event is None:
            self._say("Nothing coming up.")
            return
        self._say(f"Next is {self._describe(event, api)}.",
                  lines=[l for l in (event.location,
                                     api["describe_duration"](event),
                                     event.notes) if l],
                  icon=event.icon, tint=event.colour or "#4f9de0")

    def named_holiday(self, holiday: str = "", phrase: str = "") -> None:
        # `holiday` is the frame's hole - the subject, extracted exactly.
        # The lookup still runs over the whole phrase, since `find_holiday`
        # is a whole-word alias search and the subject is inside it either
        # way. The parameter has to be accepted regardless: an ask skill is
        # called with its hole as a keyword argument.
        api = self._api()
        found = api["find_holiday"](phrase) if api else None
        if found is None:
            # Matched the shape of the question but not a holiday it knows.
            # Naming what it does know would be a list of twenty-one read
            # aloud, so it says what kind of thing it wants instead.
            self._say("I don't know that one. Try a holiday like "
                      "Thanksgiving or the fourth of July.",
                      icon="mdi.calendar-question", tint="#8a8a8a")
            return

        self._say(f"{found.title} is {api['describe_gap'](found)}.",
                  lines=[self._full_date(found.day)],
                  icon=found.icon, tint="#d8a24a")

    @staticmethod
    def _full_date(day: str) -> str:
        """
        "Monday, September 7, 2026" from an ISO day.

        The weekday is most of why anybody asks: "Christmas is in 47 days"
        answers a different question from "Christmas is on a Thursday". And
        `2026-09-07` on a card is a field out of a database - it is the value
        the panel stores, not the answer somebody wanted.
        """
        try:
            when = date.fromisoformat(day)
        except (TypeError, ValueError):
            return day or ""
        return f"{when.strftime('%A, %B')} {when.day}, {when.year}"

    def how_long(self) -> None:
        api = self._api()
        event = api["next_event"]() if api else None
        if event is None:
            self._say("Nothing coming up.")
            return
        # The same shape as every other calendar answer. A bare line said
        # "in about two hours" and showed nothing about which thing, when, or
        # where - which is the rest of what somebody asking is about to want.
        when = "All day" if event.all_day else self._clock(event)
        self._say(f"{event.title} is {api['describe_gap'](event)}.",
                  lines=[l for l in (when, event.location,
                                     api["describe_duration"](event)) if l],
                  icon=event.icon, tint=event.colour or "#4f9de0")

    @staticmethod
    def _clock(event) -> str:
        """The start on a 12-hour clock, for a panel rather than for speech."""
        starts = getattr(event, "starts_at", None)
        if starts is None:
            return ""
        pattern = "%I:%M %p" if starts.minute else "%I %p"
        return starts.strftime(pattern).lstrip("0")

    def today(self) -> None:
        api = self._api()
        if api is None:
            self._say("The calendar is not loaded.")
            return
        events = api["on_day"](date.today())
        self._say(self._list(events, api, "today"),
                  lines=[self._describe(e, api, with_when=False) for e in events],
                  icon="mdi.calendar-today")

    def tomorrow(self) -> None:
        api = self._api()
        if api is None:
            self._say("The calendar is not loaded.")
            return
        when = date.today() + timedelta(days=1)
        events = api["on_day"](when)
        self._say(self._list(events, api, "tomorrow"),
                  lines=[self._describe(e, api, with_when=False) for e in events],
                  icon="mdi.calendar-arrow-right")

    def this_week(self) -> None:
        api = self._api()
        if api is None:
            self._say("The calendar is not loaded.")
            return
        today = date.today()
        # To the end of the week, not seven days out - "this week" ends on
        # Sunday, whatever day it is asked on.
        end = today + timedelta(days=6 - today.weekday())
        events = api["between"](today, end)
        self._say(self._list(events, api, "this week"),
                  lines=[f"{e.day}  {self._describe(e, api, with_when=False)}"
                         for e in events],
                  icon="mdi.calendar-week")

    def next_holiday(self) -> None:
        api = self._api()
        holiday = api["next_holiday"]() if api else None
        if holiday is None:
            self._say("No holidays coming up.")
            return
        self._say(f"{holiday.title} is {api['describe_gap'](holiday)}.",
                  lines=[self._full_date(holiday.day)],
                  icon=holiday.icon, tint="#d8a24a")
