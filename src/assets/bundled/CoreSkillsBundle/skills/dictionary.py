"""
Looking a word up.

Both skills carry a **payload** rather than an argument pattern. A word is
opaque - no pattern can match "petrichor", because no example contains it -
so the anchor phrase is matched and everything after it is taken verbatim.

That has a consequence worth knowing: a payload runs to the END of the
utterance, so "what does serendipity mean" captures `serendipity mean`. The
question's own verb is part of the capture, and `DictionaryAPI.clean()` is
what takes it off. Both skills go through the same cleaner so they cannot
disagree about it.
"""

from __future__ import annotations

from src.assistant.skill import Skill


def build(plugin, wake: str, key: str) -> list:
    """The skills in this group, wired to `plugin`'s handlers."""
    return [
        Skill(
            wake_word=wake, skill_key="define-word", plugin_key=key,
            examples=[
                "what does serendipity mean",
                "what does the word serendipity mean",
                "what is the definition of serendipity",
                "whats the definition of serendipity",
                "define serendipity", "definition of serendipity",
                "what is the meaning of serendipity",
                "whats serendipity mean",
                "meaning of serendipity",
                "what does the term serendipity mean",
                "look up serendipity",
            ],
            # Longest anchors are tried first, so "what does the word" wins
            # over the shorter ones and the scaffolding never reaches the
            # value.
            #
            # **No bare "what does", "whats" or "what is."** An anchor is cut
            # out of the phrase before the patterns are generated from it, so
            # an anchor that IS the whole command leaves nothing behind - and
            # a pattern generated from nothing matches every question there
            # is. With "whats" in this list, "whats the weather", "whats the
            # date", "whats the forecast" and "whats the uv index" all
            # arrived here as words to look up.
            payload={"word": [
                "what does the word", "what does the term",
                "what is the definition of", "whats the definition of",
                "what is the meaning of", "whats the meaning of",
                "the definition of", "the meaning of",
                "definition of", "meaning of",
                "define", "look up",
            ]},
            # The bare "what does X mean" shape, which has no leader worth
            # anchoring on, handled by requiring the TRAILING verb instead.
            # That is what makes it specific: "what does the week look like"
            # does not end in "mean", so it is not this skill and the pattern
            # never fires on it.
            patterns=[
                [{"LOWER": {"IN": ["what", "whats"]}},
                 {"LOWER": {"IN": ["does", "do", "is", "'s"]}, "OP": "?"},
                 {"LOWER": "the", "OP": "?"},
                 {"LOWER": {"IN": ["word", "term"]}, "OP": "?"},
                 {"IS_ALPHA": True, "OP": "{1,3}"},
                 {"LEMMA": "mean"}],
            ],
            # The whole phrase, not an argument pattern. `extract_args`
            # strips leading verbs and auxiliaries off the span it matched,
            # so "what does RUN mean" comes back empty - spaCy tags `run` as
            # a VERB and it is trimmed with the "does" in front of it. Most
            # short English words are also verbs, so that is the common case
            # rather than an odd one. The handler reads the word off the
            # phrase instead.
            wants_phrase=True,
            func=plugin.define_word,
        ),
        Skill(
            wake_word=wake, skill_key="word-synonyms", plugin_key=key,
            examples=[
                "what are other words for happy",
                "whats another word for happy",
                "what is another word for happy",
                "other words for happy", "another word for happy",
                "synonyms for happy", "synonym for happy",
                "give me another word for happy",
                "what else can i say instead of happy",
                "words that mean happy",
            ],
            payload={"word": [
                "what else can i say instead of",
                "what are other words for", "what are some other words for",
                "whats another word for", "what is another word for",
                "give me another word for", "words that mean",
                "other words for", "another word for",
                "synonyms for", "synonym for", "instead of",
            ]},
            func=plugin.word_synonyms,
        ),
    ]
