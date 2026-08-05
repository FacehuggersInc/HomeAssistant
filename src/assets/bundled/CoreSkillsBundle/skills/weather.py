"""
The forecast, on demand.
"""

from __future__ import annotations

from src.assistant.skill import Skill


def build(plugin, wake: str, key: str) -> list:
    """The skills in this group, wired to `plugin`'s handlers."""
    return [
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
                        func=plugin.weather_update,
                    ),
    ]
