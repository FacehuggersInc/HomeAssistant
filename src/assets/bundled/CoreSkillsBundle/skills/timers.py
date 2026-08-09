"""
Countdowns: starting them, stopping them, asking after them.
"""

from __future__ import annotations

from src.assistant.skill import Skill
from .helpers import (
    DURATION_JOINERS,
    DURATION_UNITS,
    TIMER_NAME_STOPWORDS,
)


def build(plugin, wake: str, key: str) -> list:
    """The skills in this group, wired to `plugin`'s handlers."""
    return [
        Skill(
                        wake_word=wake, skill_key="set-timer", kind="act", plugin_key=key,
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
                            # Durations said as words rather than digits.
                            # `_spoken_duration` reads these correctly - "an
                            # hour and a half" is 5400 seconds to it - but no
                            # example said so, and the phrase scored against a
                            # list that only ever counted in digits.
                            "set a timer for an hour",
                            "set a timer for half an hour",
                            "set a timer for an hour and a half",
                            "set a timer for a minute",
                            "set a timer for a quarter of an hour",
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
                                # An article where a number would be.
                                # `LIKE_NUM` is False for "a" and "an", so
                                # "an hour and a half" matched none of the
                                # patterns below and came back with no time
                                # at all - the same assumption that broke the
                                # date skills.
                                [{"LOWER": {"IN": ["a", "an", "one"]}},
                                 {"LEMMA": {"IN": DURATION_UNITS}},
                                 {"LOWER": {"IN": DURATION_JOINERS}, "OP": "?"},
                                 {"LOWER": {"IN": ["a", "an"]}, "OP": "?"},
                                 {"LOWER": {"IN": ["half", "quarter"]}, "OP": "?"},
                                 {"LIKE_NUM": True, "OP": "?"},
                                 {"LEMMA": {"IN": DURATION_UNITS}, "OP": "?"}],
                                # "half an hour", "a quarter of an hour".
                                [{"LOWER": {"IN": ["half", "quarter"]}},
                                 {"LOWER": "of", "OP": "?"},
                                 {"LOWER": {"IN": ["a", "an"]}, "OP": "?"},
                                 {"LEMMA": {"IN": DURATION_UNITS}}],
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
                        # The whole phrase too, to catch a "half" or
                        # "quarter" that argument extraction trims off
                        # the front of the duration it captured.
                        wants_phrase=True,
                        func=plugin.start_timer,
                    ),
        Skill(
                        wake_word=wake, skill_key="cancel-timer", kind="act", plugin_key=key,
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
                        func=plugin.cancel_timers,
                    ),
        Skill(
                        wake_word=wake, skill_key="check-timers", kind="act", plugin_key=key,
                        examples=[
                            "how long is left on my timer", "how much time is left",
                            "check my timers", "what timers are running",
                "list my timers", "how many timers do i have",
                "what timers do i have", "are there any timers running",
                            "how long until my timer is done", "how long on the timer",
                        ],
                        func=plugin.check_timers,
                    ),
    ]
