"""
What an artist's name sounded like.

Speech recognition gets proper nouns wrong constantly - "METANICK" comes back
as "medanik" or "metta nick", and no amount of better ranking helps when the
word being searched for does not exist.

So the panel remembers. When a search only works after dropping the artist,
and the person confirms the result was right, the name they said is written
down against the name the result actually had. The next time they say it, it
is corrected before the search rather than after.

Stored as a plain JSON file next to the plugin: this is a handful of names,
and something a person may want to read or edit by hand.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


def normalise(name: str) -> str:
    """Lowercase, no punctuation, single spaces - for comparing only."""
    text = re.sub(r"[^a-z0-9 ]+", " ", str(name or "").lower())
    return " ".join(text.split())


def squash(name: str) -> str:
    """
    Letters only, no spaces.

    "metta nick" and "metanick" are the same word said with a gap in the
    middle, and a speech engine chooses between them more or less at random.
    """
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


class ArtistAliases:
    """
    {kind: {real name: [what it has been called, ...]}}

    Two kinds, and for different reasons.

    **artists** are misheard: "METANICK" comes back as "medanik".

    **titles** are translated. A song has one title on YouTube Music and
    another on YouTube - "Kaiju Girl" and "\u4e59\u5973\u602a\u7363" are the
    same track - so remembering the pair lets the next search use the name
    the search engine will actually match.
    """

    KINDS = ("artists", "titles")

    def __init__(self, path: Path, log=None):
        self.path = Path(path)
        self.log = log or (lambda *a, **k: None)
        self.aliases: dict = {}
        self.titles: dict = {}
        self.load()

    ## -- storage

    @staticmethod
    def _clean(section) -> dict:
        """Rebuilt rather than trusted: somebody may have edited the file."""
        if not isinstance(section, dict):
            return {}
        return {
            str(real): sorted({normalise(h) for h in heard
                               if isinstance(h, str) and normalise(h)})
            for real, heard in section.items()
            if isinstance(heard, list) and str(real).strip()
        }

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}

        if any(kind in raw for kind in self.KINDS):
            self.aliases = self._clean(raw.get("artists"))
            self.titles = self._clean(raw.get("titles"))
        else:
            # A file written before titles existed is all artists.
            self.aliases = self._clean(raw)
            self.titles = {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"artists": self.aliases, "titles": self.titles},
                           indent=4, ensure_ascii=False),
                encoding="utf-8")
        except OSError as e:
            self.log("warning", f"[Music] Could not save artist aliases: {e}")

    ## -- use

    def _store(self, kind: str) -> dict:
        return self.titles if kind == "titles" else self.aliases

    def remember_title(self, said: str, actual: str) -> bool:
        """
        Note that a song called `said` is listed as `actual`.

        Unlike an artist, the two may be entirely different words - a
        translation is not a mishearing - so the squashed-letters check that
        rejects a correct artist name does not apply.
        """
        said_key, actual_key = normalise(said), str(actual or "").strip()
        if not said_key or not actual_key:
            return False
        if said_key == normalise(actual_key):
            return False
        existing = self.titles.setdefault(actual_key, [])
        if said_key in existing:
            return False
        existing.append(said_key)
        existing.sort()
        self.save()
        self.log("info", f"[Music] '{said}' is listed as '{actual_key}'.")
        return True

    def resolve_title(self, said: str) -> str:
        said_key = normalise(said)
        if not said_key:
            return ""
        for actual, saids in self.titles.items():
            if said_key in saids or said_key == normalise(actual):
                return actual
        return ""

    def remember(self, heard: str, real: str) -> bool:
        """
        Note that `heard` meant `real`. Returns whether anything was added.

        A name that already matches is not stored: "everlong" heard correctly
        is not a mishearing, and a file full of correct spellings makes the
        lookup slower and the file harder to read.
        """
        heard_key, real_key = normalise(heard), str(real or "").strip()
        if not heard_key or not real_key:
            return False
        if squash(heard_key) == squash(real_key):
            return False

        existing = self.aliases.setdefault(real_key, [])
        if heard_key in existing:
            return False
        existing.append(heard_key)
        existing.sort()
        self.save()
        self.log("info", f"[Music] '{heard}' now means '{real_key}'.")
        return True

    def resolve(self, heard: str) -> str:
        """
        The real name for something misheard, or "" if it is not known.

        Exact match first, then the spaces-removed form - a gap in the middle
        of a name is the most common way a speech engine gets one wrong, and
        it is not worth a separate entry.
        """
        key = normalise(heard)
        if not key:
            return ""

        for real, heards in self.aliases.items():
            if key in heards or key == normalise(real):
                return real

        squashed = squash(key)
        if not squashed:
            return ""
        for real, heards in self.aliases.items():
            if squashed == squash(real):
                return real
            if any(squashed == squash(h) for h in heards):
                return real
        return ""

    def known(self) -> int:
        return (sum(len(v) for v in self.aliases.values())
                + sum(len(v) for v in self.titles.values()))
