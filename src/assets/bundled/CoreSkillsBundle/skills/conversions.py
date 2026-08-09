"""
Converting one unit into another.

The whole phrase, not arguments. A conversion carries three values - an
amount, a unit and another unit - in an order that changes with the phrasing:
"how many cups in a litre" names the target first and the source last, and
"convert a litre to cups" the other way round. Three argument patterns that
have to agree about which is which is three chances to disagree, and
`extract_args` returns the widest span per argument rather than one span per
position, so two units in one utterance is exactly the case it cannot do.

`units.parse()` reads all three out of the phrase in one pass instead, and is
plain Python that can be exercised without a panel.
"""

from __future__ import annotations

from src.assistant.skill import Skill

from . import units

_UNIT = {"LOWER": {"IN": units.PATTERN_TOKENS}}
_ANY = {"OP": "?"}          # a filler token: "a", "an", "of", "half"
_JOIN = {"LOWER": {"IN": ["in", "to", "into", "as", "per",
                          # The verb on its own is a join too: "how many
                          # inches IS 33.7 millimetres" has no preposition
                          # anywhere in it.
                          "is", "are", "equal", "equals", "make", "makes"]}}
_AMOUNT = {"LOWER": {"IN": sorted(units.WORD_NUMBERS)}}


def build(plugin, wake: str, key: str) -> list:
    """The skills in this group, wired to `plugin`'s handlers."""
    return [
        Skill(
            wake_word=wake, skill_key="convert-units", kind="act", plugin_key=key,
            examples=[
                "how many cups in a liter",
                "how many tablespoons in a cup",
                "how many ounces are in a gallon",
                "how many feet in a mile",
                "how many grams in a pound",
                "convert 5 miles to kilometers",
                "convert 2 pounds to grams",
                "whats 350 fahrenheit in celsius",
                "what is 180 celsius in fahrenheit",
                "whats 6 feet in centimeters",
                "convert 1 gallon to liters",
                "how many milliliters in a cup",
                "whats 100 kilometers per hour in miles per hour",
                # An amount on the FAR side of the question. Every example
                # above puts the number first or has no number at all, and
                # "how many inches are in 33.7 millimeters" is neither -
                # which is the shape somebody reaches for when converting a
                # measurement they are holding.
                "how many inches are in 100 millimeters",
                "how many centimeters are in 6 feet",
                "how many grams are in 3 pounds",
                "how many inches is 33 millimeters",
                "how many pounds is 5 kilograms",
                "how many mm in an inch",
                "how many inches in a foot",
                # Time units. The list covered volume, length and mass and
                # nothing else, so "how many minutes in 5 hours" reached a
                # timer skill on the strength of "minutes" alone.
                "how many minutes in an hour", "how many minutes in 5 hours",
                "how many seconds in a minute", "how many hours in a day",
                "how many days in a year", "how many weeks in a year",
            ],
            # Matched on SHAPE, not on the units named.
            #
            # The examples below can only ever list a handful of units, and
            # scoring an utterance against them punishes it for naming
            # different ones - "how many inches in a foot" against "how many
            # feet in a mile" is two shared lemmas of three, which is under
            # the threshold. Every distance conversion in the table reached
            # no skill at all while converting correctly the moment it was
            # called by hand.
            #
            # These fire on "how many <unit> ... <unit>" and "<number>
            # <unit> to <unit>" whatever the units are, which is what makes
            # the whole table reachable rather than the dozen words that
            # happen to appear in an example.
            # Four filler slots between the join and the second unit, not
            # two. A quantity is often several tokens - "33 point 7" is
            # three on its own once normalisation has been at it, and "half
            # a" is two - so a shorter run of fillers matched the shapes
            # with a bare "a" in them and missed every one with a spoken
            # decimal.
            patterns=[
                [{"LOWER": "how"}, {"LOWER": {"IN": ["many", "much"]}},
                 _UNIT, {"LOWER": {"IN": ["are", "is"]}, "OP": "?"},
                 _JOIN, _ANY, _ANY, _ANY, _ANY, _UNIT],
                [{"LIKE_NUM": True}, _ANY, _ANY, _UNIT,
                 _JOIN, _ANY, _ANY, _ANY, _UNIT],
                [_AMOUNT, _ANY, _ANY, _UNIT, _JOIN, _ANY, _ANY, _ANY, _UNIT],
                [{"LEMMA": "convert"}, _ANY, _ANY, _ANY, _UNIT,
                 _JOIN, _ANY, _ANY, _ANY, _UNIT],
            ],
            wants_phrase=True,
            func=plugin.convert_units,
        ),
    ]
