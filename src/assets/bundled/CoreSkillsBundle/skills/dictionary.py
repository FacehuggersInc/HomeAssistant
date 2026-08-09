"""
Looking a word up.

Both are `ask` skills, matched on frames. A word is opaque - no pattern can
match "petrichor", because no example contains it - so what a frame gives
here is a subject that comes out **exact**.

That is the whole reason these moved. A payload runs to the END of the
utterance, so "what does serendipity mean" captured `serendipity mean` and
`DictionaryAPI.clean()` had to take the verb off afterwards. A frame says
where the subject ends, so nothing is captured that has to be removed.

`define-word` and `wiki-look-like` are the pair frames exist for: "what does
{word} mean" and "what does {subject} look like" share every leading word,
and the part that decides sits after the subject. On frames neither can reach
the other's phrasing at all.
"""

from __future__ import annotations

from src.assistant.skill import Skill


def build(plugin, wake: str, key: str) -> list:
    """The skills in this group, wired to `plugin`'s handlers."""
    return [
        Skill(
            wake_word=wake, skill_key="define-word", kind="ask",
            plugin_key=key,
            # Two shapes. The trailing-verb one - "what does {word} mean" -
            # is what makes this skill specific, and is why "what does the
            # week look like" cannot reach it: that does not end in "mean".
            # The leader shapes carry their own distinguishing wording, so
            # none of them is a bare "what is".
            frames=[
                "what does {word} mean",
                "what do {word} mean",
                "whats {word} mean",
                "what does the word {word} mean",
                "what does the term {word} mean",
                "what is the definition of {word}",
                "whats the definition of {word}",
                "what is the meaning of {word}",
                "whats the meaning of {word}",
                "the definition of {word}",
                "the meaning of {word}",
                "definition of {word}",
                "meaning of {word}",
                "define {word}",
                "look up {word}",
            ],
            # The whole phrase as well as the word, because the handler has
            # always read it for context and the frame does not change that.
            wants_phrase=True,
            func=plugin.define_word,
        ),
        Skill(
            wake_word=wake, skill_key="word-synonyms", kind="ask",
            plugin_key=key,
            frames=[
                "what are other words for {word}",
                "what are some other words for {word}",
                "whats another word for {word}",
                "what is another word for {word}",
                "give me another word for {word}",
                "other words for {word}",
                "another word for {word}",
                "words that mean {word}",
                "synonyms for {word}",
                "synonym for {word}",
                "what else can i say instead of {word}",
                "instead of {word}",
            ],
            func=plugin.word_synonyms,
        ),
    ]
