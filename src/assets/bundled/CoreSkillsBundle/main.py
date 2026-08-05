from __future__ import annotations

import random
import time as _time
from datetime import date, datetime, timedelta

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
from PyQt6.QtCore import Qt

from src.plugin.template import Plugin
from src.assistant.skill import Skill
from src.styling import make_font, SIZES, set_style

from .voice_bar import VoiceBar


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
ALARM_AFTER_PATTERNS = [
    [{"LIKE_NUM": True}, {"LEMMA": {"IN": DURATION_UNITS}},
     {"LOWER": {"IN": DURATION_JOINERS}, "OP": "?"},
     {"LIKE_NUM": True, "OP": "?"},
     {"LEMMA": {"IN": DURATION_UNITS}, "OP": "?"},
     {"LOWER": {"IN": ["from", "in"]}, "OP": "?"},
     {"LOWER": {"IN": ["now", "time"]}, "OP": "?"}],
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


class CoreSkills(Plugin):

    def _describe(self, seconds) -> str:
        """
        A duration as a noun phrase, from whoever owns timers.

        Through the public registry rather than by importing the module it
        lives in. `plugin.toml` makes `corewidgetsbundle` a dependency, so an
        import would load - but a dependency is what makes an import legal,
        not what makes it right: everything else about timers already comes
        through `client.public.timers`, and one function arriving by a
        different route is one more thing to keep in step.
        """
        try:
            return self.client.public.timers["describe"](seconds)
        except Exception:
            return f"{int(seconds)} seconds"


    ## CORE

    def __init__(self):
        self.skills = []
        self.voice_bar = None
        self._last_state = (None, None)

    def load(self, carryover=None):
        self.load_skills()
        self.client.subscribe_to_event("on_update", self.update_assistant)
        self.client.subscribe_to_event("on_assistant_transcribed", self.on_routed)
        self.client.subscribe_to_event("on_transcribing_assistant",
                                       self.on_transcribing)
        self.client.subscribe_to_event("on_transcribed_assistant",
                                       self.on_transcribed)
        self.client.subscribe_to_event("on_heard_assistant", self.on_heard)
        self.client.subscribe_to_event("on_woke_assistant", self.on_woke)
        self.client.subscribe_to_event("on_assistant_cancelled", self.on_cancelled)

    def built(self):
        self.load_voice_bar()

    def unload(self, carryover=None):
        self.client.unsubscribe_from_event("on_update", self.update_assistant)
        self.client.unsubscribe_from_event("on_assistant_transcribed", self.on_routed)
        self.client.unsubscribe_from_event("on_transcribing_assistant",
                                           self.on_transcribing)
        self.client.unsubscribe_from_event("on_transcribed_assistant",
                                           self.on_transcribed)
        self.client.unsubscribe_from_event("on_heard_assistant", self.on_heard)
        self.client.unsubscribe_from_event("on_woke_assistant", self.on_woke)
        self.client.unsubscribe_from_event("on_assistant_cancelled", self.on_cancelled)
        if self.voice_bar is not None:
            try:
                self.client.OVERLAYS.remove("TOPMOST", self.voice_bar)
                self.voice_bar.setParent(None)
                self.voice_bar.deleteLater()
            except Exception:
                pass
            self.voice_bar = None

    ## WAKE WORD

    def wake_word(self) -> str:
        # Plugin setting wins if set, otherwise the app-wide one so a user
        # changing it in Assistant settings does not have to change it twice.
        try:
            own = str(self.settings.general.wake_word.value).strip()
        except Exception:
            own = ""
        return own.lower() or self.client.wake_word

    ## VOICE BAR

    def load_voice_bar(self):
        try:
            if not self.client.SETTINGS.assistant.feedback.voice_bar.value:
                return
        except Exception:
            pass

        self.voice_bar = VoiceBar(self.client)
        self.client.OVERLAYS.add("TOPMOST", self.voice_bar)
        self.voice_bar.anchor(self.client.OVERLAYS)
        self.client.subscribe_to_event("on_state_change", self._reanchor)

    def _reanchor(self, event=None):
        if self.voice_bar is not None:
            self.client.call_on_ui(lambda: self.voice_bar.anchor(self.client.OVERLAYS))

    def on_routed(self, event):
        """
        The transcript, after normalising and before it is acted on.

        Named apart from `on_transcribed` deliberately: both were called
        that, in this class, subscribed to two DIFFERENT events - so the
        second definition replaced the first and `on_assistant_transcribed`
        ran the wrong body. The bar cleared its waiting message where it was
        meant to show the words.

        The transcriber only emits finished transcripts, so there is no
        partial stream to show while somebody is talking. The meter covers
        "hearing something"; this is "heard this".
        """
        if self.voice_bar is None:
            return
        text = event if isinstance(event, str) else str(event or "")
        self.client.call_on_ui(lambda: self.voice_bar.show_heard(text))

    def on_cancelled(self, event=None):
        if self.voice_bar is None:
            return
        self.client.call_on_ui(lambda: self.voice_bar.show_cancelled())

    def on_transcribing(self, event=None):
        """
        Audio captured, the model is working on it.

        The stage that covers the wait. On a big model this is seconds, and
        nothing used to be shown during them - the pill from the wake faded
        and the answer arrived later out of nowhere, which reads as the panel
        having missed you and then changed its mind.
        """
        if self.voice_bar is None:
            return
        self.client.call_on_ui(self.voice_bar.show_transcribing)

    def on_transcribed(self, event=None):
        """
        The model finished, whatever it decided.

        Only clears the WAITING message. If a phrase came back, `on_heard`
        has already replaced this with the words - and taking that down here
        would remove the one thing worth reading a moment after it appeared.
        """
        if self.voice_bar is None:
            return
        self.client.call_on_ui(self.voice_bar.done_transcribing)

    def on_heard(self, event):
        """
        A phrase arrived and is being looked up. Show it.

        The middle of the three stages. It fires before the skill search, so
        a phrase that matches nothing still leaves the person knowing what
        the panel heard - which is the difference between "it misheard me"
        and "there is no skill for that".
        """
        if self.voice_bar is None:
            return
        said = str(event or "").strip()
        self.client.call_on_ui(lambda: self.voice_bar.show_understood(said))

    def on_woke(self, event):
        """
        A skill matched. Say what was understood.

        The event carries `(skill, phrase)` - it fires from `process_phrase`
        AFTER parsing, so by the time this runs the panel is acting rather
        than listening. It used to show "<wake word> - listening…", which was
        wrong twice: it had stopped listening, and the word it named was the
        matched SKILL's own wake word rather than the one the person says.

        The phrase is what is worth showing. It is the panel repeating back
        what it heard, which is the one thing that says whether it got it
        right.
        """
        if self.voice_bar is None:
            return

        said = ""
        if isinstance(event, (tuple, list)) and len(event) >= 2:
            said = str(event[1] or "").strip()
        elif isinstance(event, str):
            said = event.strip()

        # Without a phrase there is still something worth saying: it
        # understood, even if this does not know what.
        shown = said or "Got it\u2026"
        self.client.call_on_ui(lambda: self.voice_bar.show_matched(shown))

    def update_assistant(self, event):
        # on_update runs on the update thread; widgets must be touched on the
        # Qt thread, and only when something actually changed.
        if self.voice_bar is None:
            return

        state = (self.client.ASSIST_STATUS, round(self.client.ASSIST_VOICE_ACTIVITY_LEVEL, 2))
        if state == self._last_state:
            # Nothing moved. The bar is still asked to check itself, because
            # what it is SHOWING can be wrong while the status is right: a
            # phrase that arrives already answered goes LIVE -> THINKING ->
            # LIVE between two polls, so this sees no change at all, and the
            # pill that was put up in the middle of it is never taken down.
            self.client.call_on_ui(self.voice_bar.check_still_wanted)
            return
        self._last_state = state

        status, level = state
        self.client.call_on_ui(lambda: self.voice_bar.apply_state(status, level))

    ## SKILLS

    def load_skills(self):
        wake = self.wake_word()
        key = self.config["plugin"]["key"]

        self.skills = [
            Skill(
                wake_word=wake, skill_key="tell-relative-date", plugin_key=key,
                examples=[
                    "what is today", "what was yesterday",
                    "what is the day before today", "what is tomorrow",
                    "what is the day after today", "whats today", "what today",
                    "whats tomorrow", "what tomorrow", "what before today",
                    "whats before today", "what after today", "whats after today",
                ],
                arguments={
                    "given_date": [
                        # The day word alone, first. The runs below are broken
                        # by an apostrophe: "what's today" tokenises as
                        # what / 's / today, and 's is not alpha - so the two
                        # commonest phrasings of this skill captured nothing
                        # at all and the day never reached the function.
                        [{"LOWER": {"IN": ["today", "tomorrow", "yesterday"]}}],
                        [{"LOWER": {"IN": ["before", "after"]}},
                         {"LOWER": "today"}],
                        [{"IS_ALPHA": True, "OP": "{2,3}"}],
                        [{"LOWER": {"IN": ["is", "was"]}}, {"IS_ALPHA": True, "OP": "{1,4}"}],
                    ]
                },
                func=self.tell_relative_date,
            ),
            Skill(
                wake_word=wake, skill_key="notifications-open", plugin_key=key,
                examples=[
                    "open my notifications", "show me my notifications",
                    "check notifications", "display notifications",
                    "read my notifications", "bring up notifications",
                    "what are my notifications", "open notifications",
                ],
                func=self.open_notifications,
            ),
            Skill(
                wake_word=wake, skill_key="notifications-empty", plugin_key=key,
                examples=[
                    "empty notifications", "empty my notifications",
                    "empty all of my notifications", "please empty my notifications",
                    "empty notifications all", "clear my notifications",
                    "clear notifications", "clear all notifications",
                    "delete my notifications", "delete all notifications",
                    "dismiss my notifications", "remove my notifications",
                ],
                func=self.empty_notifications,
            ),
            Skill(
                wake_word=wake, skill_key="weather-update", plugin_key=key,
                examples=[
                    "whats the weather", "weather outside",
                    "what is the weather today", "can you tell me the weather",
                    "can you tell me the weather today", "weather today", "the weather",
                    "hows the weather", "whats the weather like",
                    "what is it like outside", "is it cold outside",
                    "is it warm outside", "is it raining", "whats the temperature",
                ],
                func=self.weather_update,
            ),
            Skill(
                wake_word=wake, skill_key="set-timer", plugin_key=key,
                examples=[
                    "set a timer for 10 minutes", "can you create a timer for 5 minutes",
                    "make a timer for 11 minutes", "start a timer for 5 minutes",
                    "start a 5 minute timer", "make a new 2 minute timer",
                    "create a timer for 5 minute",
                    "can you make a timer called Cooking for 5 minutes",
                    "start a new timer for 10 minutes and call it Eggs",
                    "make a timer named Spaghetti for 5 minutes",
                    # The name in front of the word, which is how people
                    # actually say it - "an eggs timer", not "a timer called
                    # eggs".
                    "create an eggs timer for 5 minutes",
                    "set a spaghetti timer for 1 hour",
                    "start a laundry timer for 40 minutes",
                    "make a bread timer for 25 minutes",
                    "put a coffee timer on for 4 minutes",
                    # Seconds. The pattern and the parser have always taken
                    # them; without an example saying so, the phrase scored
                    # against a list that only ever said "minutes" and the
                    # skill was not the one that matched.
                    "set a timer for 30 seconds", "set a timer for 5 seconds",
                    "start a 45 second timer", "make a timer for 90 seconds",
                    "set an eggs timer for 30 seconds",
                    "set a timer for 1 minute and 30 seconds",
                ],
                arguments={
                    # LEMMA, not LOWER: one entry covers singular and plural.
                    # Abbreviations ("mins", "secs") are expanded upstream by
                    # normalize.expand_units before this ever sees them.
                    # A compound duration is ONE span, not two.
                    #
                    # `[number, unit]` alone matches "1 hour" and "48 minutes"
                    # separately in "1 hour and 48 minutes", and the extractor
                    # keeps the widest - which has to exist to be kept. Three
                    # parts is more than anybody says out loud.
                    "time": [
                        [{"LIKE_NUM": True},
                         {"LEMMA": {"IN": DURATION_UNITS}},
                         {"LOWER": {"IN": DURATION_JOINERS}, "OP": "?"},
                         {"LIKE_NUM": True, "OP": "?"},
                         {"LEMMA": {"IN": DURATION_UNITS}, "OP": "?"},
                         {"LOWER": {"IN": DURATION_JOINERS}, "OP": "?"},
                         {"LIKE_NUM": True, "OP": "?"},
                         {"LEMMA": {"IN": DURATION_UNITS}, "OP": "?"}],
                    ],
                    "name": [
                        # "call it Eggs" is in the examples but was not in the
                        # pattern, so that phrasing never produced a name.
                        [{"LOWER": {"IN": ["call", "called", "naming", "named", "name"]}},
                         {"LOWER": "it", "OP": "?"},
                         {"POS": "DET", "OP": "?"},
                         {"IS_ALPHA": True, "IS_STOP": False}],
                        # "an eggs timer" - the word immediately before
                        # "timer". Units and quantifiers are excluded or
                        # "a 5 minute timer" would come back named "minute",
                        # and determiners are stop words so "a timer" is safe.
                        [{"IS_ALPHA": True, "IS_STOP": False,
                          "LOWER": {"NOT_IN": TIMER_NAME_STOPWORDS}},
                         {"LOWER": {"IN": ["timer", "timers"]}}],
                    ],
                },
                func=self.start_timer,
            ),
            Skill(
                wake_word=wake, skill_key="cancel-timer", plugin_key=key,
                examples=[
                    # All of them
                    "cancel my timers", "stop all timers", "clear my timers",
                    "cancel all of my timers", "turn off my timers",
                    "stop all my timers", "cancel the timer",
                    "stop the timer", "end the timer", "kill the timer",
                    "get rid of the timer", "remove the timer",
                    # One, by name
                    "cancel the eggs timer", "stop the pasta timer",
                    "cancel the timer called eggs",
                    "stop the timer named laundry",
                    "end the bread timer",
                    # One, by how long it was set for. Determiners vary, and a
                    # pattern compiles the one it was given - so the shapes
                    # differ rather than repeating "the" three times.
                    "cancel the 5 minute timer", "stop the 10 minute timer",
                    "cancel my 30 second timer", "stop the 30 second timer",
                    "cancel the 1 hour timer",
                    "cancel the 1 hour and 10 minute timer",
                    "stop the 2 hour 30 minute timer",
                ],
                arguments={
                    # The same shape as start-timer's: a compound duration is
                    # ONE span. "cancel the 1 hour and 10 minutes timer"
                    # otherwise matches "1 hour" and "10 minutes" separately,
                    # and cancels whichever the extractor kept.
                    "time": [
                        [{"LIKE_NUM": True},
                         {"LEMMA": {"IN": DURATION_UNITS}},
                         {"LOWER": {"IN": DURATION_JOINERS}, "OP": "?"},
                         {"LIKE_NUM": True, "OP": "?"},
                         {"LEMMA": {"IN": DURATION_UNITS}, "OP": "?"},
                         {"LOWER": {"IN": DURATION_JOINERS}, "OP": "?"},
                         {"LIKE_NUM": True, "OP": "?"},
                         {"LEMMA": {"IN": DURATION_UNITS}, "OP": "?"}],
                    ],
                    "name": [
                        # "called eggs" / "named laundry"
                        [{"LOWER": {"IN": ["call", "called", "name", "named"]}},
                         {"LOWER": "it", "OP": "?"},
                         {"POS": "DET", "OP": "?"},
                         {"IS_ALPHA": True, "IS_STOP": False}],
                        # "the eggs timer" - the word immediately before
                        # "timer", as long as it is not a unit or a quantifier.
                        # Without the exclusion "the five minute timer" would
                        # hand back a timer named "minute".
                        [{"IS_ALPHA": True, "IS_STOP": False,
                          "LOWER": {"NOT_IN": TIMER_NAME_STOPWORDS}},
                         {"LOWER": {"IN": ["timer", "timers"]}}],
                    ],
                },
                func=self.cancel_timers,
            ),
            Skill(
                wake_word=wake, skill_key="check-timers", plugin_key=key,
                examples=[
                    "how long is left on my timer", "how much time is left",
                    "check my timers", "what timers are running",
                    "how long until my timer is done", "how long on the timer",
                ],
                func=self.check_timers,
            ),
            Skill(
                wake_word=wake, skill_key="set-alarm", plugin_key=key,
                examples=[
                    # A clock time
                    "set an alarm at 4:40 PM", "set an alarm for 7 am",
                    "set an alarm at 6:30", "wake me up at 6:30",
                    "wake me at 7 in the morning",
                    "set an alarm for half past 7",
                    "set an alarm at 7 o'clock",
                    "set an alarm for noon", "set an alarm for midnight",
                    # A day as well
                    "set an alarm tomorrow at 8 AM",
                    "set an alarm at 8 tomorrow",
                    "wake me up tomorrow at 6:30",
                    "set an alarm for friday at 9 am",
                    "set an alarm for monday morning at 7",
                    # Relative
                    "set an alarm 10 minutes from now",
                    "set an alarm for 25 minutes from now",
                    "set an alarm in 20 minutes",
                    "set an alarm in an hour and a half",
                    "wake me up in 45 minutes",
                    # Named
                    "set an alarm called laundry for 6 pm",
                    "set a bread alarm for 7 am",
                    # Repeating
                    "set a daily alarm for 7 am",
                    "wake me up every day at 6:30",
                ],
                arguments={
                    "time":   ALARM_TIME_PATTERNS,
                    "after":  ALARM_AFTER_PATTERNS,
                    "day":    ALARM_DAY_PATTERNS,
                    "part":   ALARM_PART_PATTERNS,
                    "repeat": ALARM_REPEAT_PATTERNS,
                    "name": [
                        [{"LOWER": {"IN": ["call", "called", "name", "named"]}},
                         {"LOWER": "it", "OP": "?"},
                         {"POS": "DET", "OP": "?"},
                         {"IS_ALPHA": True, "IS_STOP": False}],
                        [{"IS_ALPHA": True, "IS_STOP": False,
                          "LOWER": {"NOT_IN": ALARM_NAME_STOPWORDS}},
                         {"LOWER": {"IN": ["alarm", "alarms"]}}],
                    ],
                },
                func=self.set_alarm,
            ),
            Skill(
                wake_word=wake, skill_key="cancel-alarm", plugin_key=key,
                examples=[
                    # All of them
                    "cancel my alarms", "cancel all my alarms",
                    "clear my alarms", "delete all alarms",
                    "turn off my alarms", "remove all of my alarms",
                    # One, by time
                    "cancel the alarm at 4:40 PM", "cancel the 7 am alarm",
                    "cancel the alarm at 8 tomorrow",
                    "delete the alarm for tomorrow at 6:30",
                    "cancel the alarm for noon",
                    # One, relative - the way it was asked for
                    "cancel the alarm 10 minutes from now",
                    "cancel the alarm in 20 minutes",
                    # One, by name
                    "cancel the laundry alarm",
                    "cancel the alarm called bread",
                    # The repeating one
                    "cancel the daily alarm", "stop the daily alarm",
                    "cancel my everyday alarm",
                    "turn off the every day alarm",
                    "cancel the repeating alarm",
                    "stop waking me up every day",
                ],
                arguments={
                    "time":   ALARM_TIME_PATTERNS,
                    "after":  ALARM_AFTER_PATTERNS,
                    "day":    ALARM_DAY_PATTERNS,
                    "part":   ALARM_PART_PATTERNS,
                    "repeat": ALARM_REPEAT_PATTERNS,
                    "name": [
                        [{"LOWER": {"IN": ["call", "called", "name", "named"]}},
                         {"LOWER": "it", "OP": "?"},
                         {"POS": "DET", "OP": "?"},
                         {"IS_ALPHA": True, "IS_STOP": False}],
                        [{"IS_ALPHA": True, "IS_STOP": False,
                          "LOWER": {"NOT_IN": ALARM_NAME_STOPWORDS}},
                         {"LOWER": {"IN": ["alarm", "alarms"]}}],
                    ],
                },
                func=self.cancel_alarms,
            ),
            Skill(
                wake_word=wake, skill_key="check-alarms", plugin_key=key,
                examples=[
                    "what alarms do i have", "what alarms are set",
                    "when is my alarm", "when is my next alarm",
                    "do i have an alarm set", "list my alarms",
                    "check my alarms", "what time is my alarm",
                ],
                func=self.check_alarms,
            ),
            # The calendar skills live in the Calendar plugin, against its own
            # registry. Two skills claiming "what is my next event" is the
            # intent matcher choosing between them on wording, which is not a
            # decision anybody wants it making.
            Skill(
                wake_word=wake, skill_key="quiet-on", plugin_key=key,
                examples=[
                    "do not disturb", "turn on do not disturb",
                    "enable do not disturb", "dont disturb me",
                    "leave me alone", "hold my notifications",
                ],
                func=self.quiet_on,
            ),
            Skill(
                wake_word=wake, skill_key="quiet-off", plugin_key=key,
                examples=[
                    "turn off do not disturb", "disable do not disturb",
                    "stop do not disturb", "you can disturb me",
                    "let my notifications through",
                ],
                func=self.quiet_off,
            ),
            Skill(
                wake_word=wake, skill_key="mute-on", plugin_key=key,
                examples=[
                    "be quiet", "mute yourself", "stop making noise",
                    "silence", "turn off your sounds", "mute the panel",
                    "no sounds",
                ],
                func=self.mute_on,
            ),
            Skill(
                wake_word=wake, skill_key="mute-off", plugin_key=key,
                examples=[
                    "unmute", "you can make noise", "turn your sounds back on",
                    "unmute yourself", "sounds on",
                ],
                func=self.mute_off,
            ),
            Skill(
                wake_word=wake, skill_key="go-to-page", plugin_key=key,
                examples=[
                    "show the calendar", "open the calendar", "go to settings",
                    "show me the home page", "open settings", "go home",
                    "take me home", "show the clock", "open the web page",
                ],
                arguments={
                    "page_name": [
                        [{"LOWER": {"IN": ["the", "me", "to"]}, "OP": "*"},
                         {"IS_ALPHA": True, "OP": "{1,3}"}],
                    ]
                },
                func=self.go_to_page,
            ),
            Skill(
                wake_word=wake, skill_key="open-bookmark", plugin_key=key,
                examples=[
                    "open scryfall", "open my scryfall bookmark",
                    "go to scryfall", "open the bookmark for scryfall",
                    "bring up scryfall", "open bookmark scryfall",
                ],
                arguments={
                    # Everything after the verb, however long.
                    #
                    # A bookmark is named by whoever saved it and the title
                    # comes from the page, so it can be anything - "Scryfall"
                    # or "Advanced Search - Scryfall". Anchoring on the verb
                    # and taking the rest is the only shape that works; the
                    # matching happens in the handler, against the list.
                    "wanted": [
                        [{"LOWER": {"IN": ["open", "goto", "launch"]}},
                         {"IS_ALPHA": True, "OP": "+"}],
                        [{"LOWER": "go"}, {"LOWER": "to"},
                         {"IS_ALPHA": True, "OP": "+"}],
                    ]
                },
                func=self.open_bookmark,
            ),
            Skill(
                wake_word=wake, skill_key="nevermind", plugin_key=key,
                examples=[
                    "nevermind", "never mind", "cancel", "cancel that",
                    "forget it", "forget that", "stop listening",
                    "stop", "abort", "nothing", "leave it",
                    "disregard that", "scratch that", "dont worry about it",
                ],
                func=self.nevermind,
                # Which word was said decides what it does, so the whole
                # utterance is needed rather than an argument out of it.
                wants_phrase=True,
            ),
            Skill(
                wake_word=wake, skill_key="quit-application", plugin_key=key,
                examples=[
                    "quit the application", "quit application",
                    "close the application", "close application",
                    "exit the application", "exit application",
                    "close down the application", "shut down the application",
                    "shut down application", "shut the application down",
                    "close the client", "exit the client", "quit the client",
                    "close client", "exit client", "quit client",
                    "close the app", "quit the app", "exit the app",
                    "close the program", "exit the program", "quit the program",
                ],
                func=self.quit_application,
            ),
        ]
        self.client.SKILLS.register(key, self.skills)

    ## SETTINGS PAGE

    def settings_blocks(self) -> list:
        """
        Cards for this plugin's settings page, between the registry summary
        and the settings themselves. Rendered by SettingsPage via the
        settings_blocks() hook - any plugin can do this.
        """
        if not self.skills:
            return []

        container = QWidget()
        set_style(container, "common", "transparent")
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 8)
        outer.setSpacing(10)

        heading = QLabel(
            f"Skills  ·  {len(self.skills)} registered to \u201c{self.wake_word()}\u201d"
        )
        heading.setFont(make_font(SIZES.S2, bold=True))
        set_style(heading, "common", "text-muted")
        outer.addWidget(heading)

        for skill in sorted(self.skills, key=lambda s: getattr(s, "key", "")):
            outer.addWidget(self._skill_card(skill))

        return [container]

    def _skill_card(self, skill) -> QFrame:
        card = QFrame()
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        set_style(card, "settings", "registry-card")

        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(5)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)

        title = QLabel(getattr(skill, "key", "unnamed"))
        title.setFont(make_font(SIZES.S3, bold=True))
        set_style(title, "common", "text-strong")
        top.addWidget(title)

        examples = list(getattr(skill, "examples", []) or [])
        badge = QLabel(f"{len(examples)} phrase" + ("s" if len(examples) != 1 else ""))
        badge.setFont(make_font(SIZES.S1, bold=True))
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(badge, "settings", "registry-count")
        top.addWidget(badge)

        arguments = list((getattr(skill, "arguments", None) or {}).keys())
        if arguments:
            args_badge = QLabel(", ".join(arguments))
            args_badge.setFont(make_font(SIZES.S1, bold=True))
            args_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            set_style(args_badge, "settings", "skill-args")
            top.addWidget(args_badge)

        top.addStretch()
        layout.addLayout(top)

        wake = getattr(skill, "wake", "") or self.wake_word()
        word_min = getattr(skill, "word_min", None)
        word_max = getattr(skill, "word_max", None)
        meta = f"say \u201c{wake} …\u201d"
        if word_min is not None and word_max is not None:
            meta += f"   ·   {word_min}-{word_max} words"
        sub = QLabel(meta)
        sub.setFont(make_font(SIZES.S1))
        set_style(sub, "common", "text-muted")
        layout.addWidget(sub)

        # A couple of real phrases are worth more than the whole list, which
        # runs to sixteen entries on some skills.
        for phrase in examples[:3]:
            row = QLabel(f"\u201c{phrase}\u201d")
            row.setFont(make_font(SIZES.S1))
            row.setWordWrap(True)
            row.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            set_style(row, "settings", "registry-entry")
            layout.addWidget(row)

        if len(examples) > 3:
            more = QLabel(f"+{len(examples) - 3} more")
            more.setFont(make_font(SIZES.S1))
            set_style(more, "common", "text-muted")
            layout.addWidget(more)

        return card

    ## SKILL BODIES

    def nevermind(self, phrase: str = "", **_ignored):
        """
        Back out of whatever is in front.

        What that means depends on what is going on: an answer panel should
        close, music should stop, and with neither of those the assistant
        should simply stand down. And the words are not interchangeable -
        "stop" fits music where "nevermind" does not - so each thing that can
        be cancelled registers its own words and its own condition on
        `client.CANCEL`, and this asks rather than holding a list of cases that
        would grow every time something new appeared.
        """
        action = self.client.CANCEL.run(phrase)

        if action is None:
            # Nothing was in front. Standing down is the whole instruction.
            self.client.cancel_assistant("nevermind")
            return

        if action.stops_listening:
            self.client.cancel_assistant(f"nevermind: {action.key}")
    def quit_application(self):
        try:
            confirm = bool(self.settings.general.confirm_quit.value)
        except Exception:
            confirm = True

        # Spoken confirmation needs both ends of the voice loop. Without TTS
        # the user would be answering a question they never heard, so fall
        # back to an on-screen confirm instead.
        if not confirm or self.client.STT is None or not self.client.say(
                "Are you sure you want to quit the application? Please say Yes or No.",
                thread=False):
            self.client.confirm(
                "Quit application?", "Asked by voice command.",
                confirm_text="Quit", destructive=True,
                on_confirm=lambda: self.client.call_on_ui(self.client.stop),
            )
            return

        session = self.client.STT.new_session()
        reprompt = None
        with session:
            while True:
                phrase = session.wait_for_phrase()
                if phrase is None:
                    break
                phrase = phrase.lower().strip()
                if phrase in ("yes", "yeah", "yup", "sure", "absolutely", "correct"):
                    self.client.call_on_ui(self.client.stop)
                    break
                if phrase in ("no", "nope", "nah", "negative", "incorrect"):
                    break
                if reprompt is None:
                    reprompt = True
                    self.client.say("I didn't quite get that. Please say Yes or No.",
                                    thread=False)

    def tell_relative_date(self, given_date: str = ""):
        def ordinal(n: int) -> str:
            if 10 <= n % 100 <= 20:
                suffix = "th"
            else:
                suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
            return f"{n}{suffix}"

        def readable(d: date) -> str:
            """For speech. The strftime already ends in a space."""
            return f"{d.strftime('%B, %A the')} {ordinal(d.day)}"

        def heading(d: date) -> str:
            """For the panel. A heading, not a sentence read aloud."""
            return f"{d.strftime('%A')} {ordinal(d.day)} {d.strftime('%B')}"

        today = date.today()
        given_date = (given_date or "").lower()

        if "yesterday" in given_date or "before today" in given_date:
            when, spoken = today - timedelta(days=1), "Yesterday was,"
        elif "tomorrow" in given_date or "after today" in given_date:
            when, spoken = today + timedelta(days=1), "Tomorrow is,"
        elif "today" in given_date or not given_date:
            # Nothing captured means the phrasing got past the patterns but
            # not into them. Every example of this skill asks about a day, and
            # of those today is the one somebody asks for without saying which
            # - so it is a better answer than a refusal.
            when, spoken = today, "Today is,"
        else:
            self._respond("I don't know how to answer that.")
            return

        answer = f"{spoken} {readable(when)}"
        # Shown as well as said. A date is a thing to read off a wall rather
        # than catch as it goes past, and the day somebody just asked about
        # is the day they are about to ask what is on.
        self.client.answer("mdi.calendar", heading(when),
                           self._day_lines(when), tint="#4f9de0", speak=answer)

    def _day_lines(self, when: date) -> list:
        """
        What is on that day, one line each.

        Empty when there is no calendar, or nothing on. The panel is worth
        showing either way - the date is the answer, and the events are what
        the question was usually leading to.
        """
        try:
            if not self.client.public.has("calendar"):
                return []
            api = self.client.public.calendar
            events = api["on_day"](when)
        except Exception:
            return []

        lines = []
        for event in events or []:
            if getattr(event, "all_day", False):
                lines.append(f"All day  -  {event.title}")
                continue
            starts = getattr(event, "starts_at", None)
            clock = ""
            if starts is not None:
                pattern = "%I:%M %p" if starts.minute else "%I %p"
                clock = starts.strftime(pattern).lstrip("0")
            lines.append(f"{clock}  -  {event.title}" if clock else event.title)
        return lines or ["Nothing on."]

    def empty_notifications(self):
        if not self.client.public.has("notification_history"):
            self._respond("There is no notification history to clear.")
            return

        history = self.client.public.notification_history
        try:
            count = len(history.items)
        except Exception:
            count = -1
        history.clear()

        # Answered, like every other skill here.
        #
        # This cleared the list and said nothing - spoken or shown - so from
        # the outside a working clear and a skill that failed to match looked
        # identical.
        if count < 0:
            self._respond("Notifications cleared.")
        elif count == 0:
            self._respond("There was nothing to clear.")
        else:
            self._respond(f"Cleared {count} "
                          f"notification{'s' if count != 1 else ''}.")

    def open_notifications(self):
        if not self.client.public.has("notification_history"):
            self._respond("There is no notification history.")
            return
        # Through what the history exposes, rather than reaching for the
        # widget behind it. The manager lives on the home page, so it can be
        # absent - which is what asking for this from another page does.
        if not self.client.public.notification_history.open():
            self._respond("The notification list is only on the home screen.")
            return
        # Not spoken on success: the panel opening IS the answer, and reading
        # it out over the list somebody is now looking at helps nobody.
    def weather_update(self):
        api = self.client.API.get("weather")
        if api is None:
            self._respond("The weather plugin is not loaded.")
            return
        try:
            data = api.get_current_weather()
            # Nested quotes inside an f-string only became legal in 3.12, and
            # this plugin should stay readable regardless.
            temperature = int(data["temperature_2m"])
        except Exception as e:
            self.client.log("warning", f"[CoreSkills] Weather lookup failed: {e}")
            self._respond("I couldn't get the weather right now.")
            return

        # A panel, not a notification. The weather has more to say than one
        # number, and a toast is the wrong shape for four lines - it goes
        # past before anybody has read the second one.
        lines = []
        for label, key, unit in (("Feels like", "apparent_temperature", "\u00b0"),
                                 ("Cloud cover", "cloud_cover", "%"),
                                 ("Wind", "wind_speed_10m", " mph"),
                                 ("Gusts", "wind_gusts_10m", " mph"),
                                 ("Humidity", "relative_humidity_2m", "%")):
            value = data.get(key)
            if value is None:
                continue
            lines.append(f"{label}: {int(value)}{unit}")

        rain = data.get("precipitation")
        if rain:
            lines.append(f"Precipitation: {float(rain):.2f} in")

        is_day = bool(data.get("is_day", 1))
        glyph = "mdi.weather-sunny" if is_day else "mdi.weather-night"
        try:
            glyph = api.get_icon(data) or glyph
        except Exception:
            pass

        self.client.answer(glyph, f"{temperature} degrees", lines,
                           tint="#3f7fbf" if is_day else "#3a2159",
                           speak=self._weather_spoken(temperature, data))

    #What the sky is doing, in the order somebody would mention it. First hit
    #wins, so snow beats rain beats cloud.
    WEATHER_WORDS = (
        ("snowfall",     0,  "snowing"),
        ("showers",      0,  "showery"),
        ("rain",         0,  "raining"),
        ("cloud_cover", 80,  "overcast"),
        ("cloud_cover", 40,  "cloudy"),
    )

    def _weather_spoken(self, temperature, data: dict) -> str:
        """
        The weather as a sentence rather than a reading.

        "72 degrees." is two words: a speech model is finished with it before
        a room has noticed anybody is talking. The answer is not padded with
        silence - the words somebody misses are the ones at the front, so the
        front is given something worth missing.

        What is added is real: the sky, and how it feels if that differs from
        the number. A sentence made of filler is the same problem in more
        syllables.
        """
        sky = ""
        for key, above, word in self.WEATHER_WORDS:
            try:
                if float(data.get(key) or 0) > above:
                    sky = word
                    break
            except (TypeError, ValueError):
                continue
        if not sky:
            sky = "clear" if data.get("is_day", 1) else "clear out"

        said = f"It's {temperature} degrees and {sky}"

        # Only when it disagrees with the thermometer by enough to be worth
        # saying. "72 degrees, feels like 72" is noise.
        try:
            feels = int(float(data.get("apparent_temperature")))
            if abs(feels - int(float(temperature))) >= 3:
                said += f", though it feels more like {feels}"
        except (TypeError, ValueError):
            pass
        return said + "."

    def start_timer(self, time: str = None, name: str = None):
        """
        "set a timer for 10 minutes", "make a timer called Eggs for 5 minutes".

        `time` arrives as spoken text - "10 minutes", "1 hour" - because a
        transcript is untrusted and normalisation converts most spoken numbers
        but is not a guarantee. Parsed here rather than trusted.
        """
        seconds = _spoken_duration(time)
        if not seconds:
            self._respond("I did not catch how long for. Try 'set a timer for "
                          "five minutes'.")
            return

        if not self.client.public.has("timers"):
            # Provided by corewidgetsbundle. Disable that and timers go with
            # it, so this says so rather than failing silently.
            self._respond("Timers need the Core Widgets plugin, which is not "
                          "loaded.")
            return

        timer = self.client.public.timers["start"](
            seconds, name=_clean_label(name))
        if timer is None:
            self._respond("I could not start that timer.")
            return

        label = f" for {name}" if name else ""
        self._respond(f"{self._describe(seconds)}{label}, starting now.")

    def cancel_timers(self, time: str = None, name: str = None):
        """
        "cancel my timers", "cancel the eggs timer", "cancel the 5 minute timer".

        With neither argument this means all of them. With either, it means the
        ones that match - and saying so when nothing does, rather than silently
        cancelling everything, which is the failure that would actually cost
        somebody their dinner.
        """
        if not self.client.public.has("timers"):
            self._respond("There are no timers running.")
            return

        api = self.client.public.timers
        running = api["running"]()
        if not running:
            self._respond("There are no timers running.")
            return

        seconds = _spoken_duration(time) if time else 0
        wanted = _clean_label(name)

        # Nothing to narrow by: all of them.
        if not seconds and not wanted:
            stopped = api["cancel_all"]()
            self._respond(f"Stopped {stopped} timer" + ("s." if stopped != 1 else "."))
            return

        matched = api["cancel_matching"](name=wanted, seconds=seconds)

        if not matched:
            self._respond(f"I could not find that timer. {self._running_summary(running)}")
            return

        if len(matched) == 1:
            timer = matched[0]
            if timer.name:
                self._respond(f"Stopped the {timer.name} timer.")
            else:
                # "the 30 minutes timer" reads wrong - describe() gives a
                # noun phrase, not an adjective, so it goes after the noun.
                self._respond(
                    f"Stopped the timer set for "
                    f"{self._describe(timer.duration)}.")
            return

        self._respond(f"Stopped {len(matched)} timers.")

    def _running_summary(self, running: list) -> str:
        """What is actually on, for when a request matched nothing."""
        if not running:
            return "Nothing is running."
        names = []
        for timer in running:
            names.append(timer.name if timer.name
                         else self._describe(timer.duration))
        if len(names) == 1:
            return f"The only one running is {names[0]}."
        return "Running: " + ", ".join(names) + "."

    def check_timers(self):
        if not self.client.public.has("timers"):
            self._respond("There are no timers running.")
            return
        running = self.client.public.timers["running"]()
        if not running:
            self._respond("There are no timers running.")
            return

        lines = [f"{t.label()}: {self._describe(t.remaining())} left"
                 for t in running]
        spoken = "; ".join(lines)
        self.client.answer("mdi.timer-outline",
                           f"{len(running)} timer" + ("s" if len(running) != 1 else ""),
                           lines, tint="#3f7fbf", speak=spoken)

    ## ALARMS

    def _alarms(self):
        """The alarm service, or None if whoever owns it is not loaded."""
        if not self.client.public.has("alarms"):
            return None
        return self.client.public.alarms

    def _alarm_when(self, time_text, after_text, day, part) -> float:
        """
        The epoch an alarm was asked for, however it was asked for.

        Relative beats absolute, always. "10 minutes from now" contains a
        number that also reads as a clock time, so both arguments match - and
        the one somebody said is the one with a unit on it.
        """
        seconds = _spoken_duration(after_text) if after_text else 0
        if seconds:
            return _time.time() + seconds
        return _alarm_epoch(time_text or "", day or "", part or "")

    def set_alarm(self, time: str = None, after: str = None, day: str = None,
                  part: str = None, repeat: str = None, name: str = None):
        """
        "set an alarm at 4:40 PM", "set an alarm 10 minutes from now".

        Everything ambiguous is resolved in `_alarm_epoch` - which 8 o'clock,
        which day, and what a bare hour means with a day named on it.
        """
        api = self._alarms()
        if api is None:
            self._respond("Alarms are not available right now.")
            return

        when = self._alarm_when(time, after, day, part)
        if not when:
            self._respond("I did not catch what time. Try 'set an alarm for "
                          "seven in the morning'.")
            return

        alarm = api["schedule"](when, name=_clean_label(name),
                                repeats=bool(repeat))
        if alarm is None:
            # The only way schedule() refuses: a time already gone. Everything
            # else has had a day rolled onto it by now.
            self._respond("That time has already passed.")
            return

        said = api["describe"](alarm.when)
        every = " every day" if alarm.repeats else ""
        called = f", called {alarm.name}" if alarm.name else ""
        lines = [f"Set for {said}."]
        if alarm.repeats:
            lines.append("Repeats daily.")
        self.client.answer("mdi.alarm",
                           f"Alarm {api['clock_text'](alarm.when)}",
                           lines, tint="#c0603f",
                           speak=f"Alarm set for {said}{every}{called}.")

    def cancel_alarms(self, time: str = None, after: str = None,
                      day: str = None, part: str = None, repeat: str = None,
                      name: str = None):
        """
        "cancel my alarms", "cancel the alarm at 4:40 PM",
        "cancel the alarm 10 minutes from now".

        The relative form is how it was ASKED for rather than what it reads
        as now, and both find the same one: "10 minutes from now" resolves to
        a clock time, and the match is against that to the nearest minute.
        """
        api = self._alarms()
        if api is None:
            self._respond("Alarms are not available right now.")
            return

        scheduled = api["scheduled"]()
        if not scheduled:
            self._respond("There are no alarms set.")
            return

        when = self._alarm_when(time, after, day, part)
        wanted = _clean_label(name)
        # "the daily alarm" narrows to the repeating ones. On its own that is
        # usually enough to say which; with a time as well it is both.
        only_repeating = True if repeat else None

        if not when and not wanted and only_repeating is None:
            stopped = api["cancel_all"]()
            self._respond(f"Cleared {stopped} alarm"
                          + ("s." if stopped != 1 else "."))
            return

        matched = api["cancel_matching"](when=when, name=wanted,
                                         repeats=only_repeating)
        if not matched:
            self._respond("I could not find that alarm. "
                          + self._alarm_summary(scheduled))
            return
        if len(matched) == 1:
            alarm = matched[0]
            said = (alarm.name if alarm.name
                    else f"the {api['clock_text'](alarm.when)} alarm")
            if alarm.repeats:
                said = f"the daily {said}" if not alarm.name else said
            self._respond(f"Cancelled {said}.")
            return
        self._respond(f"Cancelled {len(matched)} alarms.")

    def check_alarms(self):
        """What is set, soonest first."""
        api = self._alarms()
        if api is None:
            self._respond("Alarms are not available right now.")
            return

        scheduled = api["scheduled"]()
        if not scheduled:
            self._respond("There are no alarms set.")
            return

        lines = []
        for alarm in scheduled:
            said = api["describe"](alarm.when)
            lines.append(f"{alarm.name}: {said}" if alarm.name else said)
        self.client.answer(
            "mdi.alarm",
            f"{len(scheduled)} alarm" + ("s" if len(scheduled) != 1 else ""),
            lines, tint="#c0603f", speak=self._alarm_summary(scheduled))

    def _alarm_summary(self, scheduled: list) -> str:
        """What is set, for when a request matched nothing."""
        api = self._alarms()
        if api is None or not scheduled:
            return "There are no alarms set."
        said = [(f"{a.name} at {api['describe'](a.when)}" if a.name
                 else api["describe"](a.when)) for a in scheduled]
        if len(said) == 1:
            return f"The only one set is {said[0]}."
        return "Set: " + ", ".join(said) + "."

    ## HELPERS

    ## -- quiet

    #Skill handlers run on the assistant's worker.
    #
    #Anything below that reaches Qt - a page rebuild, a settings apply that
    #redraws, a panel - has to be handed over. Doing it inline produced
    #"Timers cannot be stopped from another thread" and a page torn down
    #underneath its own widgets.
    def quiet_on(self):
        self.client.call_on_ui(lambda: self.client.set_do_not_disturb(True))
        # Said before it takes effect, or the confirmation is the first thing
        # the mode silences.
        self._respond("Do not disturb is on.")

    def quiet_off(self):
        # Spoken FIRST for the opposite reason: while it is still on, this
        # would not be heard.
        self.client.call_on_ui(lambda: self.client.set_do_not_disturb(False))
        self._respond("Do not disturb is off.")

    def mute_on(self):
        self._respond("Going quiet.")
        self.client.call_on_ui(lambda: self.client.set_sounds_muted(True))

    def mute_off(self):
        self.client.call_on_ui(lambda: self.client.set_sounds_muted(False))
        self._respond("Sounds are back on.")

    ## -- getting about

    def go_to_page(self, page_name: str = ""):
        """
        Open a registered page by name.

        Matched against what the pages call themselves rather than a list kept
        here, so a plugin adding a page is reachable without this knowing it
        exists.
        """
        wanted = _clean_words(page_name, drop=PAGE_VERBS)
        if not wanted:
            self._respond("Which page?")
            return

        best, score = None, 0.0
        for label, target in self._page_candidates():
            hit = _overlap(wanted, label)
            if hit > score:
                best, score = target, hit

        # A page nobody meant is worse than admitting the miss: this navigates,
        # so a wrong guess takes the screen away from whatever was on it.
        if best is None or score < 0.5:
            self._respond(f"I do not have a page called {wanted}.")
            return

        entry_key, coord = best
        if coord is None:
            self.client.call_on_ui(
                lambda target=entry_key: self.client.goto(target, override=True))
            return

        def travel(target=entry_key, where=coord):
            self.client.goto(target, override=True)
            instance = getattr(self.client.PAGES.get_entry(target),
                               "instance", None)
            jump = getattr(instance, "jump_to_coord", None)
            if callable(jump):
                jump(tuple(where))
        self.client.call_on_ui(travel)

    def _page_candidates(self) -> list:
        """
        Every page somebody could ask for: the registered ones, and the
        sub-pages inside them.

        A sub-page is reached through its parent rather than by name, so it is
        not in `PAGES` and a search of that alone cannot find it - "go to
        calendar" matched the skill and then reported no such page, because
        the calendar is a sub-page of the home page.

        Found by shape rather than by naming a plugin: any page instance
        offering `sub_page_dict` and `jump_to_coord` joins in, so this does
        not have to know which plugin owns the framework.
        """
        found = []
        for entry_key in self.client.PAGES.keys():
            label = str(entry_key).lstrip("#").replace("_", " ")
            label = label.replace("cwb ", "").replace(" page", "")
            found.append((label, (entry_key, None)))

            instance = getattr(self.client.PAGES.get_entry(entry_key),
                               "instance", None)
            subs = getattr(instance, "sub_page_dict", None)
            if not isinstance(subs, dict) \
                    or not callable(getattr(instance, "jump_to_coord", None)):
                continue
            for name, sub in subs.items():
                coord = getattr(sub, "coord", None)
                if coord is None:
                    continue
                spoken = str(getattr(sub, "NAME", "") or name)
                spoken = spoken.replace("_", " ").lower()
                found.append((spoken, (entry_key, coord)))
        return found

    def open_bookmark(self, wanted: str = ""):
        """
        Open a saved page by roughly its name.

        Anchored on the verb and matched loosely, because a bookmark's title
        comes from the page rather than from whoever saved it - "Advanced
        Search - Scryfall" is a name somebody would say as "scryfall".
        """
        wanted = _clean_words(wanted, drop=("open", "go", "to", "goto",
                                            "launch", "bookmark", "my", "the",
                                            "for", "bring", "up"))
        if not wanted:
            self._respond("Which bookmark?")
            return

        marks = []
        try:
            marks = self.client.BOOKMARKS.all()
        except Exception:
            marks = []
        if not marks:
            self._respond("There are no bookmarks saved.")
            return

        best, score = None, 0.0
        for mark in marks:
            hit = max(_overlap(wanted, mark.label), _overlap(wanted, mark.host))
            if hit > score:
                best, score = mark, hit

        if best is None or score < 0.4:
            self._respond(f"I have no bookmark like {wanted}.")
            return

        from urllib.parse import urlparse
        base = ""
        try:
            parts = urlparse(best.url)
            if parts.scheme and parts.netloc:
                base = f"{parts.scheme}://{parts.netloc}"
        except Exception:
            base = ""
        self.client.call_on_ui(lambda: self.client.goto("#webpage", data={
            "url": best.url, "lock_base": base, "lock_address": True,
        }, override=True))

    def _respond(self, text: str):
        # Speak when possible, otherwise show it - a skill that silently does
        # nothing because TTS is unconfigured is indistinguishable from a
        # broken one.
        if not self.client.say(text, thread=False):
            self.client.simple_notify("assistant", "Assistant", text)


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
