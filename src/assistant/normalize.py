from __future__ import annotations

import re

# Spoken numbers, in the forms Whisper actually emits.
_ONES = ("zero", "one", "two", "three", "four", "five", "six", "seven",
         "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
         "fifteen", "sixteen", "seventeen", "eighteen", "nineteen")
_TENS = ("twenty", "thirty", "forty", "fourty", "fifty", "sixty", "seventy",
         "eighty", "ninety")
_SCALE = ("hundred", "thousand", "million", "billion")

NUMBER_WORDS = _ONES + _TENS + _SCALE

_WORD_VALUE = {w: i for i, w in enumerate(_ONES)}
_WORD_VALUE.update({
    "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
})
_SCALE_VALUE = {"hundred": 100, "thousand": 1000, "million": 10**6, "billion": 10**9}

_NUM = "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))

# A run of number words joined by spaces or hyphens, optionally with "and"
# between them. Crucially the outer whitespace is NOT part of the match: the
# original pattern had \s inside the alternation, so " one " matched whole and
# collapsed to "1", turning "for one minute" into "for1minute" - a single
# token the skill matchers could never match.
_NUMBER_RUN = re.compile(
    rf"\b(?:{_NUM})(?:[\s-]+(?:and[\s-]+)?(?:{_NUM}))*\b",
    re.IGNORECASE,
)

# Units a bare article should be read as a quantity in front of. "a minute"
# means one minute; "a timer" does not mean one timer, so this is deliberately
# not applied to every noun.
_UNITS = ("second", "seconds", "sec", "secs", "minute", "minutes", "min", "mins",
          "hour", "hours", "hr", "hrs", "day", "days", "week", "weeks",
          "month", "months", "year", "years")
_UNIT = "|".join(_UNITS)

_ARTICLE_UNIT = re.compile(rf"\b(?:an?)\s+({_UNIT})\b", re.IGNORECASE)
_HALF_UNIT = re.compile(rf"\bhalf\s+(?:an?\s+)?({_UNIT})\b", re.IGNORECASE)
_QUARTER_UNIT = re.compile(rf"\b(?:a\s+)?quarter\s+(?:of\s+)?(?:an?\s+)?({_UNIT})\b",
                           re.IGNORECASE)
_COUPLE = re.compile(rf"\b(?:a\s+)?couple\s+(?:of\s+)?({_UNIT})\b", re.IGNORECASE)
_FEW = re.compile(rf"\b(?:a\s+)?few\s+({_UNIT})\b", re.IGNORECASE)

# "one and a half hours" -> "90 minutes" is over-reach; "1.5 hours" keeps the
# unit the user said and still parses as a number.
_AND_A_HALF = re.compile(rf"\b(\d+)\s+and\s+a\s+half\s+({_UNIT})\b", re.IGNORECASE)

_HALF_OF = {"second": "0.5", "minute": "30 seconds", "hour": "30 minutes",
            "day": "12 hours", "week": "3.5 days", "year": "6 months"}
_QUARTER_OF = {"minute": "15 seconds", "hour": "15 minutes", "day": "6 hours"}


def _run_to_int(phrase: str):
    """
    Convert a run of number words to an int, or None if it does not form one.

    Written out rather than delegating to word2number because that raises on
    perfectly ordinary input ("zero", trailing "and") and returns surprising
    values for others, and because a transcript is untrusted text - a
    ValueError here would drop the whole phrase.
    """
    words = [w for w in re.split(r"[\s-]+", phrase.lower()) if w and w != "and"]
    if not words:
        return None

    total = 0
    current = 0
    seen = False

    for word in words:
        if word in _SCALE_VALUE:
            scale = _SCALE_VALUE[word]
            if scale == 100:
                current = max(1, current) * 100
            else:
                total += max(1, current) * scale
                current = 0
            seen = True
        elif word in _WORD_VALUE:
            value = _WORD_VALUE[word]
            # "twenty five" -> 25, but "five twenty" is two numbers, not 520
            if current and value >= 20:
                total += current
                current = value
            elif current and current % 10 == 0 and value < 10:
                current += value
            elif current:
                total += current
                current = value
            else:
                current = value
            seen = True
        else:
            return None

    return total + current if seen else None


def words_to_numbers(text: str) -> str:
    """
    Turn spoken numbers into digits without disturbing the surrounding text.

    "set a timer for one minute"        -> "set a timer for 1 minute"
    "set a timer for twenty five mins"  -> "set a timer for 25 mins"
    "set a timer for half an hour"      -> "set a timer for 30 minutes"
    """
    if not text:
        return text

    def replace_run(match):
        value = _run_to_int(match.group())
        return str(value) if value is not None else match.group()

    out = _NUMBER_RUN.sub(replace_run, text)

    # Fractional and article forms, after the plain runs so "half an hour"
    # is not left as "half an 1 hour".
    def half(match):
        unit = match.group(1).lower().rstrip("s")
        return _HALF_OF.get(unit, f"0.5 {match.group(1)}")

    def quarter(match):
        unit = match.group(1).lower().rstrip("s")
        return _QUARTER_OF.get(unit, f"0.25 {match.group(1)}")

    out = _HALF_UNIT.sub(half, out)
    out = _QUARTER_UNIT.sub(quarter, out)
    out = _COUPLE.sub(lambda m: f"2 {m.group(1)}", out)
    out = _FEW.sub(lambda m: f"3 {m.group(1)}", out)
    out = _ARTICLE_UNIT.sub(lambda m: f"1 {m.group(1)}", out)
    out = _AND_A_HALF.sub(lambda m: f"{int(m.group(1)) + 0.5} {m.group(2)}", out)

    return out


# Whisper writes numbers and units together often enough that the skill
# matchers, which expect separate tokens, would otherwise never fire.
# \b cannot anchor inside "for1minute" - there is no boundary between a letter
# and a digit, both being word characters. Lookarounds split on the transition
# itself instead.
_LETTER_DIGIT = re.compile(r"(?<=[A-Za-z])(?=\d)")
_DIGIT_UNIT = re.compile(rf"(?<=\d)(?=(?:{_UNIT})\b)", re.IGNORECASE)


def split_glued_units(text: str) -> str:
    """'for1minute' -> 'for 1 minute'."""
    out = _LETTER_DIGIT.sub(" ", text)
    out = _DIGIT_UNIT.sub(" ", out)
    return out


# Whisper transcribes spoken abbreviations literally. Expanded to canonical
# units so skill patterns only ever have to list one form.
UNIT_ALIASES = {
    "sec": "second", "secs": "seconds", "s": "seconds",
    "min": "minute", "mins": "minutes",
    "hr": "hour", "hrs": "hours",
    "wk": "week", "wks": "weeks",
    "yr": "year", "yrs": "years",
}
_ALIAS = re.compile(rf"\b({'|'.join(sorted(UNIT_ALIASES, key=len, reverse=True))})\b",
                    re.IGNORECASE)


def expand_units(text: str) -> str:
    # Only after a number, so "s" and "min" in ordinary speech are left alone.
    def sub(match):
        return UNIT_ALIASES.get(match.group(1).lower(), match.group(1))
    return re.sub(r"(?<=\d\s)" + _ALIAS.pattern, sub, text, flags=re.IGNORECASE)


_FILLERS = re.compile(r"\b(?:um+|uh+|erm+|hmm+)\b[,\s]*", re.IGNORECASE)


def strip_fillers(text: str) -> str:
    return _FILLERS.sub("", text)


def normalize(text: str) -> str:
    """
    Full transcript clean-up, in the order the stages depend on each other.

    Whitespace is collapsed last so no earlier stage has to care about the
    spacing it leaves behind.
    """
    if not text:
        return text
    out = strip_fillers(text)
    out = split_glued_units(out)
    out = words_to_numbers(out)
    out = split_glued_units(out)
    out = expand_units(out)
    return " ".join(out.split())


# Phrases that abandon whatever the assistant is doing. Matched against the
# whole normalised utterance, not searched within it, so "never mind the
# weather" stays a weather query.
CANCEL_PHRASES = {
    "nevermind", "never mind", "no nevermind", "no never mind",
    "cancel", "cancel that", "forget it", "forget that",
    "stop", "stop it", "stop listening", "abort", "quit that",
    "nothing", "nothing nevermind", "dont worry", "don't worry",
    "dont worry about it", "don't worry about it",
    "leave it", "as you were", "disregard", "scratch that",
}


# What Whisper says when nobody is speaking.
#
# It was trained on video transcriptions, so on silence or on noise just above
# the voice-activity threshold the decoder falls back on what usually comes
# next in that training data - end-screen boilerplate and subtitle credits.
# Research on the behaviour found about 35% of all hallucinations are two
# phrases and over half come from the top ten, so a short list covers most of
# it.
#
# Matched against the WHOLE utterance and never within one: "thanks" inside a
# real sentence is a real word, and only a bare "thanks" arriving from a quiet
# room is suspect.
HALLUCINATIONS = {
    # Filler the transcriber adds to a pause. Heard often enough on this panel
    # to be worth naming: it appends them to a real question rather than
    # offering them alone, which is why they are also stripped from the end -
    # see strip_hallucination().
    "i like that", "i like it", "i love it", "that's it", "thats it",
    "okay", "ok", "alright", "all right", "right", "yeah", "yep",
    "mm hmm", "mhm", "uh huh",
    # end-screen boilerplate
    "thank you", "thank you very much", "thanks", "thanks a lot",
    "thank you for watching", "thanks for watching",
    "thank you for watching this video", "thanks for watching this video",
    "please subscribe", "subscribe to my channel",
    "please subscribe to my channel", "like and subscribe",
    "dont forget to subscribe", "don't forget to subscribe",
    "see you next time", "see you in the next video", "bye", "bye bye",
    "goodbye", "the end", "thats all", "that's all", "thats it", "that's it",
    # subtitle credits, which leak in verbatim
    "subtitles by the amara org community",
    "subtitles by the amaraorg community",
    "translated by the amara org community",
    "transcribed by otter ai", "transcription by castingwordscom",
    "amara org", "amaraorg", "otter ai",
    # what music tends to produce
    "like that", "you", "yeah", "oh", "mm", "mmm", "hmm", "uh", "um",
    "music", "applause", "laughter", "silence", "blank audio",
    "instrumental", "outro", "intro music", "background music",
}

#A phrase saying the same short thing over and over is the other shape a
#hallucination takes - "thank you thank you thank you".
_REPEAT_LIMIT = 3


#What may be taken off either END of a real phrase.
#
#Both ends, because the transcriber puts it at either: it fills the pause
#before somebody starts speaking as readily as the one after they stop, so
#"i like that what is the weather" and "what is the weather i like that" are
#the same mistake in two places.
#
#Deliberately NOT the whole hallucination set. That one holds single common
#words - "you", "music", "right" - which are fine to reject as a whole
#utterance and ruinous to strip from the edge of one: "who are you" would
#become "who are", and "what is that music" would lose the music.
#
#Everything here is something nobody says as the first or last words of an
#instruction to a panel. That is the whole test for adding to it, and it is a
#stricter one for the front: "i like that idea, remind me" is a sentence
#somebody could say, so the list stays short rather than clever.
EDGE_NOISE = frozenset({
    "i like that", "i like it", "i love it", "like that",
    "thank you", "thank you very much", "thanks", "thanks a lot",
    "thank you for watching", "thanks for watching",
    "please subscribe", "don't forget to subscribe",
    "dont forget to subscribe", "subscribe to my channel",
    "bye bye", "the end", "amara org", "amaraorg", "otter ai",
    "background music", "intro music", "outro music", "blank audio",
})


def strip_hallucination(text: str) -> str:
    """
    Take known boilerplate off either end of a transcript.

    `is_hallucination` only answers about a whole utterance, and the
    transcriber does not always give it one: it fills the pause around what
    was actually said with its own habits, so both

        "i like that what is the weather"
        "what is the weather i like that"

    arrive as one phrase with an invented half that gets asked along with the
    real one.

    Only the ends, and only while something is left. Cutting from the middle
    would take real speech with it, and a phrase that IS one of these is
    `is_hallucination`'s answer to give rather than this one's.
    """
    words = str(text or "").split()
    if not words:
        return ""
    if is_hallucination(text):
        # The whole thing is boilerplate. Taking an edge off "i like that"
        # leaves "i", which is worse than either dropping it or keeping it.
        return ""

    # Longest first, so "thank you very much" is not left as "very much".
    sizes = sorted({len(phrase.split()) for phrase in EDGE_NOISE},
                   reverse=True)

    def edge(seq) -> str:
        return " ".join(seq).strip(" .!?,").lower()

    changed = True
    while changed and words:
        changed = False
        for size in sizes:
            if size >= len(words):
                # Never take the whole phrase; that is the guard above.
                continue
            if edge(words[:size]) in EDGE_NOISE:
                words = words[size:]
                changed = True
                break
            if edge(words[-size:]) in EDGE_NOISE:
                words = words[:-size]
                changed = True
                break
    return " ".join(words)


def flatten(text: str) -> str:
    """
    Lower case, letters and digits and single spaces, nothing else.

    For comparing two pieces of text that went different ways round - one
    written, one through a microphone and a transcriber - where punctuation
    and casing say nothing about whether they are the same words.
    """
    stripped = re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower())
    return " ".join(stripped.split())


def is_hallucination(text: str) -> bool:
    """
    Whether an utterance is the transcriber inventing something.

    Nothing here is a command this panel answers to, so dropping a real
    "thanks" costs nothing - while acting on one costs the panel doing
    something nobody asked for, in the middle of a song.
    """
    stripped = " ".join(str(text or "").lower().split())
    stripped = stripped.strip(" .!?,")
    if not stripped:
        return True
    if stripped in HALLUCINATIONS:
        return True

    # The same word or short phrase repeated. Real speech to a panel does not
    # look like this.
    words = stripped.split()
    if len(words) >= _REPEAT_LIMIT and len(set(words)) == 1:
        return True
    for size in (2, 3):
        if len(words) >= size * _REPEAT_LIMIT and len(words) % size == 0:
            chunks = {" ".join(words[i:i + size])
                      for i in range(0, len(words), size)}
            if len(chunks) == 1:
                return True
    return False


def is_cancel(text: str) -> bool:
    """Whether an utterance is the user backing out."""
    if not text:
        return False
    stripped = " ".join(str(text).lower().split()).strip(".,!?\u2026 ")
    return stripped in CANCEL_PHRASES
