"""
Asking about the sun and the moon.

The arithmetic is not here. It lives in the AstronomyLibrary plugin, which
registers no page, no widget and no skill on purpose - so the skills that use
it live with the other skills, and the library stays a library. `plugin.toml`
declares the dependency, which is what makes the load order right; every use
is still guarded, so a panel with the library uninstalled says so rather than
failing.
"""

from __future__ import annotations

from src.assistant.skill import Skill


def build(plugin, wake: str, key: str) -> list:
    """The skills in this group, wired to `plugin`'s handlers."""
    return [
        # One skill for both ends of the day. Sunrise and sunset are the same
        # calculation and the same card, and which one was asked for is a
        # word in the phrase rather than a different question - so the
        # handler reads it, the way the rain and snow one does.
        Skill(
            wake_word=wake, skill_key="sun-times", plugin_key=key,
            examples=[
                "when is sunset", "when is sunrise", "what time is sunset",
                "what time is sunrise", "when does the sun set",
                "when does the sun rise", "what time does the sun go down",
                "what time does the sun come up", "when does it get dark",
                "when does it get light", "how long until sunset",
                "how long until sunrise", "when is sundown",
                "how much daylight is left", "what time is sundown",
            ],
            wants_phrase=True,
            func=plugin.sun_times,
        ),
        Skill(
            wake_word=wake, skill_key="moon-phase", plugin_key=key,
            examples=[
                "what phase is the moon", "whats the moon phase",
                "is it a full moon", "what is the moon doing",
                "hows the moon", "what phase is the moon in",
                "is the moon full", "is the moon waxing",
                "how full is the moon", "whats the moon tonight",
            ],
            func=plugin.moon_phase,
        ),
    ]
