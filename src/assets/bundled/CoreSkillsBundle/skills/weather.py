"""
The forecast, on demand.

Four skills rather than one, because they are four different answers. Asking
about the air is not asking about the temperature, and a single skill covering
everything outdoors answers "is the air clean" with a wind speed.
"""

from __future__ import annotations

from src.assistant.skill import Skill


def build(plugin, wake: str, key: str) -> list:
    """The skills in this group, wired to `plugin`'s handlers."""
    return [
        Skill(
            wake_word=wake, skill_key="weather-update", kind="act", plugin_key=key,
            examples=[
                "whats the weather", "weather outside",
                "what is the weather today", "can you tell me the weather",
                "can you tell me the weather today", "weather today",
                "the weather", "hows the weather", "whats the weather like",
                "what is it like outside", "is it cold outside",
                "is it warm outside",
                "is it warm out", "is it cold out", "is it nice out", "whats the temperature",
                "how hot is it", "how cold is it",
            ],
            func=plugin.weather_update,
        ),
        # "is it raining" used to sit in weather-update, which answered it
        # with a temperature and four other readings. It is a precipitation
        # question and it belongs here - and leaving it in both is the two
        # skills competing over the same phrase.
        Skill(
            wake_word=wake, skill_key="weather-precipitation", kind="act", plugin_key=key,
            examples=[
                "is it raining", "is it going to rain", "will it rain",
                "will it rain today", "will it rain later",
                "whats the chance of rain", "what are the chances of rain",
                "how likely is rain", "do i need an umbrella",
                "should i take an umbrella", "is rain expected",
                "is there rain coming", "how much rain",
                # Snow is the same skill, not a rival one. Two skills would
                # put "will it rain or snow" in a competition between them,
                # and the handler takes the phrase so it can tell which was
                # asked.
                "is it snowing", "is it going to snow", "will it snow",
                "will it snow today", "will it snow tonight",
                "whats the chance of snow", "how much snow",
                "is snow expected", "are we getting snow",
            ],
            wants_phrase=True,
            func=plugin.precipitation_update,
        ),
        Skill(
            wake_word=wake, skill_key="weather-wind", kind="act", plugin_key=key,
            examples=[
                "how windy is it", "is it windy", "is it windy outside",
                "whats the wind", "whats the wind speed",
                "how strong is the wind", "which way is the wind blowing",
                "what direction is the wind", "hows the wind",
                "are there gusts",
            ],
            func=plugin.wind_update,
        ),
        Skill(
            wake_word=wake, skill_key="weather-uv", kind="act", plugin_key=key,
            examples=[
                "whats the uv index", "hows the uv", "what is the uv",
                "do i need sunscreen", "is the sun strong",
                "how strong is the sun today", "whats the uv like",
                "will i burn today",
            ],
            func=plugin.uv_update,
        ),
        Skill(
            wake_word=wake, skill_key="weather-week", kind="act", plugin_key=key,
            examples=[
                "whats the forecast for the week", "whats the forecast",
                "whats the weekly forecast", "hows the week looking",
                "what does the week look like", "forecast for the week",
                "whats the rest of the week look like",
                "whats the weather this week", "give me the weekly forecast",
                "seven day forecast", "whats the extended forecast",
                "weather for the next few days",
                "whats the weather for the next few days",
                "forecast for the next few days",
            ],
            func=plugin.forecast_week,
        ),
        Skill(
            wake_word=wake, skill_key="weather-humidity", kind="act", plugin_key=key,
            examples=[
                "whats the humidity", "how humid is it", "is it humid",
                "how humid is it outside", "whats the humidity outside",
                "is it muggy", "is it sticky out", "humidity",
                "hows the humidity",
            ],
            func=plugin.humidity_update,
        ),
        Skill(
            wake_word=wake, skill_key="weather-air-quality", kind="act", plugin_key=key,
            examples=[
                "hows the air quality", "whats the air quality",
                "air quality", "is the air clean", "is the air bad",
                "whats the aqi", "hows the air", "is it smoky outside",
                "is there smoke outside", "is the air safe to breathe",
                "hows the pollution", "is the pollution bad",
            ],
            func=plugin.air_quality_update,
        ),
    ]
