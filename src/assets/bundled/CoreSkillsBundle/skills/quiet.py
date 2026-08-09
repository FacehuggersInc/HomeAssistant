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
            wake_word=wake, skill_key="quiet-on", kind="act", opposite="quiet-off", plugin_key=key,
            examples=[
                "do not disturb", "turn on do not disturb",
                "enable do not disturb", "dont disturb me",
                # What people say when they do not know the panel's own
                # name for it. "Quiet mode" and "quiet hours" are the
                # phrases every other device uses.
                "turn on quiet mode", "enable quiet mode",
                "turn on quiet hours", "enable quiet hours",
                "leave me alone", "hold my notifications",
            ],
            func=plugin.quiet_on,
        ),
        Skill(
            wake_word=wake, skill_key="quiet-off", kind="act", opposite="quiet-on", plugin_key=key,
            examples=[
                "turn off do not disturb", "disable do not disturb",
                "turn off quiet mode", "disable quiet mode",
                "turn off quiet hours", "disable quiet hours",
                "stop do not disturb", "you can disturb me",
                "let my notifications through",
            ],
            func=plugin.quiet_off,
        ),
        Skill(
            wake_word=wake, skill_key="mute-on", kind="act", opposite="mute-off",
            plugin_key=key,
            examples=[
                "be quiet", "mute yourself", "stop making noise",
                "silence", "turn off your sounds", "mute the panel",
                "no sounds",
            ],
            func=plugin.mute_on,
        ),
        Skill(
            wake_word=wake, skill_key="mute-off", kind="act", opposite="mute-on",
            plugin_key=key,
            examples=[
                "unmute", "you can make noise", "turn your sounds back on",
                "unmute yourself", "sounds on",
            ],
            func=plugin.mute_off,
        ),
        Skill(
            wake_word=wake, skill_key="mic-mute-on", kind="act", opposite="mic-mute-off",
            plugin_key=key,
            examples=[
                "mute the microphone", "turn the microphone off",
                "microphone off", "stop listening to me",
            ],
            func=plugin.mic_mute_on,
        ),
        Skill(
            wake_word=wake, skill_key="mic-mute-off", kind="act", opposite="mic-mute-on",
            plugin_key=key,
            examples=[
                "unmute the microphone", "turn the microphone back on",
                "microphone on", "start listening again",
            ],
            func=plugin.mic_mute_off,
        ),
    ]
