"""
Do not disturb, the panel's own sounds, and the microphone.

Three different quiets, and saying which is which matters: do not disturb
holds notifications back, muting stops the panel making noise, and muting the
microphone stops it hearing. Every microphone example says the word, so none
of them can be mistaken for one of the others.
"""

from __future__ import annotations

from src.assistant.skill import Skill


def build(plugin, wake: str, key: str) -> list:
    """The skills in this group, wired to `plugin`'s handlers."""
    return [
        Skill(
            wake_word=wake, skill_key="quiet-on", plugin_key=key,
            examples=[
                "do not disturb", "turn on do not disturb",
                "enable do not disturb", "dont disturb me",
                "leave me alone", "hold my notifications",
            ],
            func=plugin.quiet_on,
        ),
        Skill(
            wake_word=wake, skill_key="quiet-off", plugin_key=key,
            examples=[
                "turn off do not disturb", "disable do not disturb",
                "stop do not disturb", "you can disturb me",
                "let my notifications through",
            ],
            func=plugin.quiet_off,
        ),
        Skill(
            wake_word=wake, skill_key="mute-on", plugin_key=key,
            examples=[
                "be quiet", "mute yourself", "stop making noise",
                "silence", "turn off your sounds", "mute the panel",
                "no sounds",
            ],
            func=plugin.mute_on,
        ),
        Skill(
            wake_word=wake, skill_key="mute-off", plugin_key=key,
            examples=[
                "unmute", "you can make noise", "turn your sounds back on",
                "unmute yourself", "sounds on",
            ],
            func=plugin.mute_off,
        ),
        Skill(
            wake_word=wake, skill_key="mic-mute-on", plugin_key=key,
            examples=[
                "mute the microphone", "turn the microphone off",
                "microphone off", "stop listening to me",
            ],
            func=plugin.mic_mute_on,
        ),
        Skill(
            wake_word=wake, skill_key="mic-mute-off", plugin_key=key,
            examples=[
                "unmute the microphone", "turn the microphone back on",
                "microphone on", "start listening again",
            ],
            func=plugin.mic_mute_off,
        ),
    ]
