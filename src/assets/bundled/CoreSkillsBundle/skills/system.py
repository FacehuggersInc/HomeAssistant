"""
Backing out, and shutting down.
"""

from __future__ import annotations

from src.assistant.skill import Skill


def build(plugin, wake: str, key: str) -> list:
    """The skills in this group, wired to `plugin`'s handlers."""
    return [
        Skill(
                        wake_word=wake, skill_key="nevermind", kind="act", plugin_key=key,
                        examples=[
                            "nevermind", "never mind", "cancel", "cancel that",
                            "forget it", "forget that", "stop listening",
                            "stop", "abort", "nothing", "leave it",
                            "disregard that", "scratch that", "dont worry about it",
                            # Said to a panel that is still listening when it
                            # should not be. Complaining about it IS the
                            # instruction, and the words nobody thinks of when
                            # writing the polite ones down.
                            "why are you still listening", "stop hearing me",
                            "why are you still hearing me", "you can stop now",
                            "that is enough", "thats enough", "enough",
                            "stop it", "quit listening", "go away",
                        ],
                        func=plugin.nevermind,
                        # Which word was said decides what it does, so the whole
                        # utterance is needed rather than an argument out of it.
                        wants_phrase=True,
                    ),
        Skill(
                        wake_word=wake, skill_key="quit-application", kind="act", plugin_key=key,
                        examples=[
                            "quit the application", "quit application",
                            "close the application", "close application",
                            "exit the application", "exit application",
                            "close down the application", "shut down the application",
                            "shut down application", "shut the application down",
                            "close the client", "exit the client", "quit the client",
                            "close client", "exit client", "quit client",
                            "close the app", "quit the app", "exit the app",
                            "close the program", "exit the program", "quit the program",
                        ],
                        func=plugin.quit_application,
                    ),
    ]
