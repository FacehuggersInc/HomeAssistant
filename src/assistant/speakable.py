"""
Text as it should be said, rather than as it is written.

`normalize.py` runs the other way - it turns what a person said into something
a matcher can compare. This is the return trip: an answer written for the
screen, turned into something a speech model can pronounce.

It matters most for the AI fallback, whose replies come back as written prose.
"5 x 3 = 15" is unambiguous on screen and unreadable aloud: a TTS model says
"five ex three" and stops, because `=` is not a word and `x` is a letter.

Deliberately narrow. Only the symbols that appear in ordinary answers are
expanded, and only where they are being used as symbols - `x` between two
numbers is multiplication, `x` anywhere else is a letter.
"""

from __future__ import annotations

import re

#Symbols that are always read the same way, whatever surrounds them.
ALWAYS = [
    ("\u00d7", " times "),          # ×
    ("\u00f7", " divided by "),     # ÷
    ("\u2260", " does not equal "),
    ("\u2264", " is less than or equal to "),
    ("\u2265", " is greater than or equal to "),
    ("\u00b1", " plus or minus "),
    ("\u2192", " to "),             # →
    ("\u2013", " to "),             # – en dash, a range
    ("\u2014", ", "),               # — em dash, a pause
    ("\u2026", ", "),               # …
    ("&", " and "),
    ("%", " percent"),
    ("\u00b0", " degrees"),
    ("\u20ac", " euros"),
    ("\u00a3", " pounds"),
    ("\u00a5", " yen"),
]

#Symbols only expanded when they sit between two numbers, because elsewhere
#they are punctuation or a letter.
BETWEEN_NUMBERS = [
    (r"(?<=\d)\s*=\s*(?=[-\d])",  " equals "),
    (r"(?<=\d)\s*\+\s*(?=\d)",    " plus "),
    (r"(?<=\d)\s*[x\u00d7]\s*(?=\d)", " times "),
    (r"(?<=\d)\s*/\s*(?=\d)",     " divided by "),
    (r"(?<=\d)\s*>\s*(?=\d)",     " is greater than "),
    (r"(?<=\d)\s*<\s*(?=\d)",     " is less than "),
    # Minus, not hyphen: "5 - 3" is a sum, "well-known" is a word and
    # "2026-01-13" is a date.
    (r"(?<=\d)\s+-\s+(?=\d)",     " minus "),
]

#Read as words rather than spelled out.
UNITS = [
    (r"(?<=\d)\s*°C\b", " degrees celsius"),
    (r"(?<=\d)\s*°F\b", " degrees fahrenheit"),
    (r"(?<=\d)\s*km/h\b", " kilometres per hour"),
    (r"(?<=\d)\s*mph\b", " miles per hour"),
    (r"(?<=\d)\s*kg\b", " kilograms"),
    (r"(?<=\d)\s*cm\b", " centimetres"),
    (r"(?<=\d)\s*mm\b", " millimetres"),
    (r"(?<=\d)\s*km\b", " kilometres"),
    (r"(?<=\d)\s*ms\b", " milliseconds"),
    (r"(?<=\d)\s*GB\b", " gigabytes"),
    (r"(?<=\d)\s*MB\b", " megabytes"),
    (r"(?<=\d)\s*KB\b", " kilobytes"),
]

_URL = re.compile(r"https?://\S+|\bwww\.\S+")
_CODE = re.compile(r"`[^`]*`")
_MARKDOWN = re.compile(r"[*_#>|]+")
_SPACES = re.compile(r"[ \t]+")


def _dollars(text: str) -> str:
    """$5 -> 5 dollars. Written before the number, said after it."""
    return re.sub(r"\$\s*(\d[\d,]*(?:\.\d+)?)", r"\1 dollars", text)


#A short answer, given the shape of a sentence.
#
#"72 degrees." is two words and a full stop. A speech model reads it in about
#three quarters of a second, which is less time than a room takes to notice
#somebody is talking - so the answer is over before anybody has turned round,
#and it sounds curt into the bargain.
#
#Silence padded either side does not fix that: the words are still gone before
#they were listened to. A lead-in does, because the part somebody misses is
#the part that carries no information.
#Deliberately few, and all of them neutral about what follows. "That would be"
#and "I make it" read as answers to a question, which is wrong the moment a
#skill is confirming something it just did.
LEAD_INS = (
    "It's {answer}",
    "Right now, {answer}",
    "Looks like {answer}",
)

#Under this many words, an answer gets one. Four is "seventy two degrees out";
#five is a sentence already.
FLAVOUR_UNDER = 4


def flavour(text: str, under: int = FLAVOUR_UNDER) -> str:
    """
    A spoken answer with enough words to be heard as one.

    Left alone if it is already a sentence, or if it starts with something
    that is clearly one - a lead-in on "There are no timers running" would
    read as a stammer.
    """
    import random

    answer = " ".join(str(text or "").split())
    if not answer:
        return answer
    stripped = answer.rstrip(".!?")
    if len(stripped.split()) >= under:
        return answer

    # Sentence case is the answer's own; a lead-in puts it mid-sentence.
    first, _, rest = stripped.partition(" ")
    if first[:1].isupper() and not first.isupper():
        stripped = first.lower() + (" " + rest if rest else "")

    return random.choice(LEAD_INS).format(answer=stripped) + "."


def speakable(text: str) -> str:
    """
    An answer, rewritten to be read aloud.

    Order matters: URLs and code go first, since what is inside them must not
    be expanded - a slash in a URL is not "divided by".
    """
    said = str(text or "")
    if not said.strip():
        return ""

    # A spoken address is unusable anyway, and reading one out takes longer
    # than the answer it belongs to.
    said = _URL.sub("a link", said)
    # Code is on screen to be read, not heard.
    said = _CODE.sub("this", said)

    said = _dollars(said)

    # Units BEFORE the bare symbols. "°C" is a unit; replacing the "°" first
    # left "21 degreesC", because by then there was no "°C" to match.
    for pattern, spoken in UNITS:
        said = re.sub(pattern, spoken, said)
    for symbol, spoken in ALWAYS:
        said = said.replace(symbol, spoken)
    for pattern, spoken in BETWEEN_NUMBERS:
        said = re.sub(pattern, spoken, said)

    # Markdown emphasis is invisible when spoken and confuses the model's own
    # text preparation, which capitalises and punctuates what it is given.
    said = _MARKDOWN.sub(" ", said)

    said = _SPACES.sub(" ", said)
    said = re.sub(r"\s+([,.;:!?])", r"\1", said)
    return said.strip()
