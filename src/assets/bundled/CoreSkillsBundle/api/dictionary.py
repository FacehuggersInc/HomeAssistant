"""
Definitions and synonyms, from dictionaryapi.dev.

Free and unauthenticated, which is the whole reason it was chosen: a panel
that needs a key for this is a panel where the skill starts failing the day
somebody's trial runs out, and there is no way to tell that from the outside
except by asking it something and getting an apology.

One request answers both skills. The endpoint returns definitions and
synonyms in the same document, so asking for "other words for happy" and
"what does happy mean" costs the same call and hits the same cache.
"""

from __future__ import annotations

import re
import time
from urllib.parse import quote

import requests


class DictionaryAPI:

    BASE = "https://api.dictionaryapi.dev/api/v2/entries/en/"
    TIMEOUT = 6.0

    # Long, because words do not change. The cache exists for the shape of
    # the conversation rather than for the network: "what does petrichor
    # mean" is very often followed by "what are other words for petrichor",
    # and that is two calls for one lookup.
    CACHE_SECONDS = 24 * 60 * 60
    CACHE_MAX = 200

    def __init__(self, plugin, client):
        self.plugin = plugin
        self.client = client
        self._cache: dict = {}

    # ── Lookup ────────────────────────────────────────────────────────────

    def look_up(self, word: str) -> dict | None:
        """
        Everything known about a word, or None.

        None means "no answer", and covers a word that is not in the
        dictionary as well as a network that is not there. The two are
        separated in the log, because one is the user's spelling and the
        other is the panel's problem, but the caller cannot do anything
        different about them.
        """
        word = self.clean(word)
        if not word:
            return None

        cached = self._cache.get(word)
        if cached is not None and time.time() - cached[0] < self.CACHE_SECONDS:
            return cached[1]

        try:
            response = requests.get(self.BASE + quote(word),
                                    timeout=self.TIMEOUT)
        except Exception as e:
            self.client.log("warning", f"[Dictionary] '{word}' failed: {e}")
            return None

        if response.status_code == 404:
            # A real answer, not a failure: the word is not in there. Cached
            # like any other, so a mis-transcription asked three times in a
            # row is one request.
            self.client.log("debug", f"[Dictionary] '{word}' is not a word.")
            self._remember(word, None)
            return None
        if response.status_code != 200:
            self.client.log("warning", f"[Dictionary] '{word}' returned "
                                       f"{response.status_code}.")
            return None

        try:
            entries = response.json()
        except Exception as e:
            self.client.log("warning", f"[Dictionary] '{word}' was not JSON: {e}")
            return None
        if not isinstance(entries, list) or not entries:
            return None

        parsed = self._parse(entries)
        self._remember(word, parsed)
        return parsed

    def _remember(self, word: str, value) -> None:
        if len(self._cache) >= self.CACHE_MAX:
            # Oldest out. A dictionary cache with no bound is a slow leak on
            # a panel that runs for months.
            oldest = min(self._cache, key=lambda k: self._cache[k][0])
            self._cache.pop(oldest, None)
        self._cache[word] = (time.time(), value)

    # ── Shaping ───────────────────────────────────────────────────────────

    # "what does X mean" and its contractions, with the word in the middle.
    #
    # Read off the phrase rather than captured by an argument pattern, which
    # cannot do it: `extract_args` strips leading verbs and auxiliaries off a
    # span, so "what does RUN mean" loses the only word in the question -
    # spaCy tags it VERB, and the value that comes back is empty. Every word
    # that is also a verb has that problem, which is most short words.
    _MIDDLE = re.compile(
        r"\bwhat(?:'s|s|\s+is|\s+does|\s+do)?\s+"
        r"(?:the\s+)?(?:word|term)?\s*"
        r"(.+?)\s+means?\b", re.I)

    @classmethod
    def word_from_phrase(cls, phrase: str) -> str:
        """The word out of a whole utterance, for the shapes with no leader."""
        match = cls._MIDDLE.search(phrase or "")
        return cls.clean(match.group(1)) if match else ""


    @staticmethod
    def clean(word: str) -> str:
        """
        The word on its own, from whatever the transcript carried.

        A payload runs to the end of the utterance, so "what does serendipity
        mean" arrives here as "serendipity mean" - the verb the question was
        built around is still attached to it. Stripped here rather than in
        the handler so both skills strip it the same way.
        """
        text = (word or "").strip().lower()
        for character in ".,?!;:\"'":
            text = text.replace(character, " ")
        words = [part for part in text.split() if part]

        # Leading scaffolding. "the word serendipity", "a synonym", "term".
        # "does" and "is" among them: the "what does X mean" shape is caught
        # by an argument pattern rather than an anchor, and an argument span
        # keeps the tokens it needed to anchor on.
        # "that"/"this"/"it" among them. All three are real dictionary
        # entries, so "what does that mean" with nothing before it would
        # otherwise return a definition of the word "that" - a correct
        # lookup and a useless answer. Emptied here so the handler can ask
        # which word instead.
        while words and words[0] in ("the", "a", "an", "word", "term",
                                     "meaning", "definition", "of", "for",
                                     "does", "do", "is", "'s", "s",
                                     "that", "this", "it"):
            words.pop(0)
        # Trailing scaffolding, which is where the question's verb ends up.
        while words and words[-1] in ("mean", "means", "meaning", "meant",
                                      "definition", "define", "is", "was",
                                      "about", "exactly", "again", "please"):
            words.pop()
        return " ".join(words)

    def _parse(self, entries: list) -> dict:
        """
        The response reduced to what a panel can show.

        Definitions are kept with their part of speech, because "a light" and
        "to light" are different answers to the same question and a list that
        drops which is which reads as a contradiction.
        """
        word = ""
        phonetic = ""
        senses = []
        synonyms = []
        antonyms = []

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            word = word or str(entry.get("word") or "")
            phonetic = phonetic or str(entry.get("phonetic") or "")
            for meaning in entry.get("meanings") or []:
                if not isinstance(meaning, dict):
                    continue
                part = str(meaning.get("partOfSpeech") or "")
                for term in meaning.get("synonyms") or []:
                    if term and term not in synonyms:
                        synonyms.append(str(term))
                for term in meaning.get("antonyms") or []:
                    if term and term not in antonyms:
                        antonyms.append(str(term))
                for definition in meaning.get("definitions") or []:
                    if not isinstance(definition, dict):
                        continue
                    text = str(definition.get("definition") or "").strip()
                    if not text:
                        continue
                    senses.append({
                        "part": part,
                        "text": text,
                        "example": str(definition.get("example") or "").strip(),
                    })
                    # Synonyms hang off individual definitions as well as off
                    # the meaning, and a word can have all of them in one
                    # place and none in the other.
                    for term in definition.get("synonyms") or []:
                        if term and term not in synonyms:
                            synonyms.append(str(term))

        if not senses and not synonyms:
            return None
        return {
            "word": word,
            "phonetic": phonetic,
            "senses": senses,
            "synonyms": synonyms,
            "antonyms": antonyms,
        }
