"""
Finding a wake word in a transcript.

One module, because both sides of the socket need the same answer. The panel
uses it to route what it heard; the speech process uses it to decide whether a
wake it already acted on was real. A matcher described in two places disagrees
the first time either one changes, and the disagreement here would be a panel
that learns to ignore the word it just heard correctly.

Nothing in here imports anything heavy, so the speech process can use it
without dragging the assistant's model stack in behind it.
"""

from __future__ import annotations

import difflib
import re

#How wrong a heard word may be and still count as the wake word.
#
#0.8 is roughly one wrong letter in five. A wake word arrives from a
#transcriber given a second or two of audio containing one short word, which
#is close to its worst case - it comes back with "elexa" (0.800), "lexa"
#(0.889) and "a lexa" (1.000 once joined) for somebody saying it perfectly
#clearly. Demanding an exact spelling threw all of those away and the panel
#looked deaf.
#
#"alexis" scores 0.727 and does NOT pass, which is deliberate: a name spoken
#in the room scores the same as "a lexus" and no threshold separates them from
#each other. Waking on somebody else's name is worse than one more retry.
#
#Only the wake word is fuzzy. Everything after it is passed on as heard,
#because a skill's arguments are not a known short list to match against.
WAKE_RATIO = 0.8


def find_wake_fuzzy(text: str, wake: str, ratio: float = WAKE_RATIO):
    """
    The wake word, allowing for a small model mishearing it.

    Tried only after an exact match fails. A word is compared against the wake
    word on its own and joined with its neighbour, because the other common
    failure is one word arriving as two.
    """
    if not text or not wake:
        return None

    target = wake.lower()
    words = list(re.finditer(r"[A-Za-z']+", text))
    best = None
    best_score = 0.0

    for index, match in enumerate(words):
        candidates = [(match.group(0), match)]
        if index + 1 < len(words):
            # "a lexa" and "alex a" - one word heard as two.
            joined = match.group(0) + words[index + 1].group(0)
            candidates.append((joined, words[index + 1]))

        for word, ending in candidates:
            score = difflib.SequenceMatcher(None, word.lower(), target).ratio()
            if score >= ratio and score > best_score:
                best_score = score
                best = ending
    return best


def find_wake(text: str, wake: str):
    """
    Last occurrence of a wake word, case-insensitively and on word boundaries.
    Answers the match or None.

    A transcriber capitalises the first word of every transcript, so a plain
    `wake in text` test is False for essentially every real utterance -
    "alexa" is not in "Alexa, set a timer for 1 minute." Word boundaries
    matter too: a short wake word otherwise fires inside ordinary words.
    """
    if not text or not wake:
        return None

    found = None
    for match in re.finditer(rf"\b{re.escape(wake)}\b", text, re.IGNORECASE):
        found = match
    if found is not None:
        return found
    return find_wake_fuzzy(text, wake)


def heard_wake(text: str, wake: str) -> bool:
    """
    Whether the wake word appears at all, however badly.

    Deliberately generous, because of what the answer is used for: a False
    here is what lets the panel decide a wake was noise and learn to ignore
    that sound in future. A near miss counted as absent is a step towards
    ignoring the real thing, and a near miss counted as present costs one
    nuisance wake that stays in the log.
    """
    return find_wake(text, wake) is not None


def strip_wake(text: str, wake: str) -> str:
    """Everything after the wake word, or the whole phrase if it is absent."""
    match = find_wake(text, wake)
    if match is None:
        return text
    return text[match.end():].strip(" ,.;:!?-")
