"""
Asking what day it is.
"""

from __future__ import annotations

from src.assistant.skill import Skill


def build(plugin, wake: str, key: str) -> list:
    """The skills in this group, wired to `plugin`'s handlers."""
    return [
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
                        func=plugin.tell_relative_date,
                    ),
    ]
