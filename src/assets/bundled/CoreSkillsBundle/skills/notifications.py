"""
The notification history, opened and emptied by voice.
"""

from __future__ import annotations

from src.assistant.skill import Skill


def build(plugin, wake: str, key: str) -> list:
    """The skills in this group, wired to `plugin`'s handlers."""
    return [
        Skill(
                        wake_word=wake, skill_key="notifications-open", kind="act", plugin_key=key,
                        examples=[
                            "open my notifications", "show me my notifications",
                            "check notifications", "display notifications",
                            "read my notifications", "bring up notifications",
                            "what are my notifications", "open notifications",
                        ],
                        func=plugin.open_notifications,
                    ),
        Skill(
                        wake_word=wake, skill_key="notifications-empty", kind="act", plugin_key=key,
                        examples=[
                            "empty notifications", "empty my notifications",
                            "empty all of my notifications", "please empty my notifications",
                            "empty notifications all", "clear my notifications",
                            "clear notifications", "clear all notifications",
                            "delete my notifications", "delete all notifications",
                            "dismiss my notifications", "remove my notifications",
                        ],
                        func=plugin.empty_notifications,
                    ),
    ]
