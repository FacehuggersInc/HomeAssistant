"""
Wikipedia: what a thing looks like, and what it is.

Two skills, because they are two answers. "What does an axolotl look like"
wants a picture and no paragraph; "tell me about the Roman Empire" wants the
paragraph and will take a picture with it.

Both are `ask` skills, matched on frames. They are the pair that frames exist
for: `what does {subject} look like` and `what does {subject} mean` share
every leading word, and the part that decides comes AFTER the subject - which
word overlap has thrown away by the time it scores. On frames they cannot
reach each other at all, because "mean" does not close a look-like question.

Frames also make the subject exact. The old declarations could not: one
anchored on a trailing verb and read the subject back off the whole phrase,
the other used a payload that took everything to the end of the utterance.

`look up` is deliberately not a frame here. It belongs to the dictionary,
where it was first, and two skills claiming one phrase is a coin toss rather
than a decision.
"""

from __future__ import annotations

from src.assistant.skill import Skill


def build(plugin, wake: str, key: str) -> list:
    """The skills in this group, wired to `plugin`'s handlers."""
    return [
        Skill(
            wake_word=wake, skill_key="wiki-look-like", kind="ask",
            plugin_key=key,
            # Every frame ends in the thing that makes this skill what it is.
            # That trailing wording is the whole distinction from every other
            # "what does X" question on the panel.
            frames=[
                "what does {subject} look like",
                "what do {subject} look like",
                "what does a {subject} look like",
                "what does an {subject} look like",
                "what does the {subject} look like",
                "whats {subject} look like",
                "show me a picture of {subject}",
                "show me an image of {subject}",
                "show me a photo of {subject}",
                "show me what {subject} looks like",
                "find me a picture of {subject}",
            ],
            wants_phrase=True,
            func=plugin.looks_like,
        ),
        Skill(
            wake_word=wake, skill_key="wiki-search", kind="ask",
            plugin_key=key,
            # The catch-all, and it falls out of the scoring rather than
            # needing to be arranged. "What is {subject}" carries two fixed
            # words against "what does {subject} look like"'s four, so any
            # more particular reading of the same phrase outscores it - and
            # this only wins when nothing else fits, which is exactly when
            # "let me look that up" is the right answer.
            frames=[
                "what is {subject}",
                "what are {subject}",
                "what was {subject}",
                "whats {subject}",
                "who is {subject}",
                "who was {subject}",
                "who are {subject}",
                "who were {subject}",
                "tell me about {subject}",
                "read about {subject}",
                "search for {subject}",
                "search wikipedia for {subject}",
                "look up {subject} on wikipedia",
            ],
            wants_phrase=True,
            func=plugin.wiki_search,
        ),
    ]
