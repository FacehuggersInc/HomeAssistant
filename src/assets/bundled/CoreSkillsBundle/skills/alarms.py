"""
Wall-clock alarms, which are a different thing from timers -
an alarm is a time of day and a timer is a length.
"""

from __future__ import annotations

from src.assistant.skill import Skill
from .helpers import (
    ALARM_AFTER_PATTERNS,
    ALARM_DAY_PATTERNS,
    ALARM_NAME_STOPWORDS,
    ALARM_PART_PATTERNS,
    ALARM_REPEAT_PATTERNS,
    ALARM_TIME_PATTERNS,
)


def build(plugin, wake: str, key: str) -> list:
    """The skills in this group, wired to `plugin`'s handlers."""
    return [
        Skill(
                        wake_word=wake, skill_key="set-alarm", kind="act", plugin_key=key,
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
                            "wake me up in two hours", "wake me up in an hour",
                            "set an alarm in 2 hours",
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
                        func=plugin.set_alarm,
                    ),
        Skill(
                        wake_word=wake, skill_key="cancel-alarm", kind="act", plugin_key=key,
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
                        func=plugin.cancel_alarms,
                    ),
        Skill(
                        wake_word=wake, skill_key="check-alarms", kind="act", plugin_key=key,
                        examples=[
                            "when is my alarm", "when is my next alarm",
                            "do i have an alarm set",
                "are there any alarms set", "are any alarms set", "list my alarms",
                            "check my alarms", "what time is my alarm",
                        ],
                        func=plugin.check_alarms,
                    ),
    ]
