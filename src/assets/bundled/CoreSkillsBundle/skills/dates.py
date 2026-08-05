"""
Asking what day it is, and what time it is.

Both here because they are the same question at two resolutions, and because
keeping them apart is what let "what time is it" go to the timer skill: the
only example in the tree containing the word "time" was "how much time is
left".
"""

from __future__ import annotations

from src.assistant.skill import Skill


def build(plugin, wake: str, key: str) -> list:
    """The skills in this group, wired to `plugin`'s handlers."""
    return [
        Skill(
            wake_word=wake, skill_key="tell-time", plugin_key=key,
            examples=[
                "what time is it", "whats the time", "what is the time",
                "tell me the time", "do you have the time",
                "whats the time right now", "time",
                "what time is it right now", "got the time",
            ],
            func=plugin.tell_time,
        ),
        Skill(
            wake_word=wake, skill_key="tell-relative-date", plugin_key=key,
            examples=[
                "what is today", "what was yesterday",
                "what is the day before today", "what is tomorrow",
                "what is the day after today", "whats today", "what today",
                "whats tomorrow", "what tomorrow", "what before today",
                "whats before today", "what after today", "whats after today",
                # The word "date" appeared in none of the above, so the
                # commonest phrasing of all reached no skill and fell to the
                # AI. "todays" is one token and does not lemmatise to
                # "today", which is why asking for it by name is needed
                # rather than trusting the day words to cover it.
                "whats the date", "what is the date", "whats todays date",
                "what is todays date", "whats the date today",
                "what day is it", "what day is it today",
                "what is the day", "what day is tomorrow",
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
            func=plugin.tell_relative_date,
        ),
    ]
