"""
Wikipedia: what a thing looks like, and what it is.

Two skills, because they are two answers. "What does an axolotl look like"
wants a picture and no paragraph; "tell me about the Roman Empire" wants the
paragraph and will take a picture with it.

Neither can use an argument pattern, and only one of them can use a payload:

* **`wiki-search`** has a leader - "search for", "tell me about", "who is" -
  so the subject is everything after it and an anchor reaches it.
* **`wiki-look-like`** does not. The subject of "what does an axolotl look
  like" sits in the MIDDLE, with the question wrapped around it, and a
  payload takes everything to the END of the utterance - which here is
  "look like". It is matched on the trailing verb instead and the subject is
  read off the phrase, the same way `define-word` handles "what does X mean".

`look up` is deliberately NOT an anchor here. It belongs to the dictionary,
where it was first, and two skills claiming one phrase is a coin toss rather
than a decision.
"""

from __future__ import annotations

from src.assistant.skill import Skill


def build(plugin, wake: str, key: str) -> list:
    """The skills in this group, wired to `plugin`'s handlers."""
    return [
        Skill(
            wake_word=wake, skill_key="wiki-look-like", plugin_key=key,
            # **No subjects in the examples.** They used to name an axolotl,
            # a pangolin, a puffin, Mount Fuji and Saturn - and every one of
            # those words became this skill's vocabulary, because a
            # `wants_phrase` skill has no payload and its examples are scored
            # whole. "What is an axolotl" then matched this skill on the word
            # "axolotl" alone and went looking for a picture, while "what is
            # a black hole" went to the search skill. Which one answered
            # depended on whether the noun happened to appear in an example
            # here.
            #
            # With the subjects gone, the only thing this skill knows is the
            # SHAPE - "look like", "show me a picture" - which is the only
            # thing that actually distinguishes it.
            examples=[
                "what does it look like",
                "what do they look like",
                "what does that look like",
                "show me a picture of it",
                "show me an image of it",
                "show me a photo of it",
                "what does one look like",
            ],
            # Anchored on the trailing verb, which is what makes it specific:
            # "what does the week look like" ends in "look like" too, so the
            # weather skill has to keep beating this on score - it does,
            # because "week" is one of its own example lemmas.
            patterns=[
                [{"LOWER": {"IN": ["what", "whats"]}},
                 {"LOWER": {"IN": ["does", "do", "did", "is", "'s"]}, "OP": "?"},
                 {"LOWER": {"IN": ["a", "an", "the"]}, "OP": "?"},
                 {"IS_ALPHA": True, "OP": "{1,4}"},
                 {"LEMMA": "look"}, {"LOWER": "like"}],
                [{"LOWER": {"IN": ["show", "find"]}}, {"LOWER": "me"},
                 {"OP": "?"}, {"OP": "?"}, {"OP": "?"},
                 {"LOWER": {"IN": ["picture", "image", "photo", "photograph",
                                   "pictures", "images", "photos"]}}],
            ],
            wants_phrase=True,
            func=plugin.looks_like,
        ),
        Skill(
            wake_word=wake, skill_key="wiki-search", plugin_key=key,
            examples=[
                "search for the great barrier reef",
                "search wikipedia for marie curie",
                "tell me about the roman empire",
                "tell me about mount fuji",
                "who is ada lovelace",
                "who was marie curie",
                "read about the apollo program",
                "whats the great barrier reef",
                "look it up on wikipedia",
            ],
            payload={"subject": [
                "search wikipedia for", "look up on wikipedia",
                "tell me about", "read about", "search for", "search",
                "who is", "who was", "who are", "who were",
            ]},
            # "What is X" has no anchor worth having - a payload of "what is"
            # would leave nothing to generate a pattern from and match every
            # question on the panel. It is a shape instead, and a weak one on
            # purpose: it scores near zero, so any skill that actually knows
            # about the subject beats it, and it only wins when nothing else
            # does. Which is exactly when "let me look that up" is the right
            # answer.
            patterns=[
                [{"LOWER": {"IN": ["what", "whats"]}},
                 {"LOWER": {"IN": ["is", "are", "was", "were", "'s"]}, "OP": "?"},
                 {"LOWER": {"IN": ["a", "an", "the"]}, "OP": "?"},
                 {"IS_ALPHA": True, "OP": "{1,4}"}],
            ],
            wants_phrase=True,
            func=plugin.wiki_search,
        ),
    ]
