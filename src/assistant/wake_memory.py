"""
What woke the panel, kept so it can be heard and so it can be ignored.

Two things live here. The last few clips that triggered the spotter, as WAV
files somebody can play; and the vectors of the ones that turned out to be
noise, so the same sound can be recognised and passed over next time.

**The vector is openWakeWord's own.** Its pipeline is mel spectrogram ->
Google speech embedding -> a (16, 96) matrix -> a small classifier. That
matrix is what the classifier just scored, and it is already in memory, so
matching costs nothing to produce. It is also the right thing to match on: the
mel stage normalises level, so it does not care how loud the sound was, and
the embedding was trained on speech content rather than on who was speaking,
so it does not care about pitch.

The two vectors being compared are always "the sixteen frames ending at the
trigger", which means they are aligned by construction. Comparing arbitrary
clips would need time warping to allow for one being said slower; here the
trigger anchors both, and a plain cosine is enough.

**Nothing here decides on its own that a wake was false.** It is told, and the
rule that tells it is deliberately hard to satisfy - see `should_learn`.
"""

from __future__ import annotations

import base64
import json
import math
import os
import struct
import time
import uuid
import wave

#How many triggering clips to keep for listening to. A pattern in what is
#setting the panel off shows up over an evening rather than over a handful of
#wakes, and in a room with a television in it a handful is an hour.
#
#The cost is the index rather than the audio. Each clip carries a packed
#(16, 96) vector, and the whole file is rewritten on every wake, so this is
#around 430KB a write against 85KB - which is nothing on an SSD and worth
#knowing on an SD card.
MAX_CLIPS = 50

#How many learned vectors to keep. Deliberately smaller than the clip list:
#every entry here is compared against on every single wake, and it is a list
#somebody has to be able to audit by ear in one screen.
MAX_IGNORED = 10

#Audio the clips are written at.
RATE = 16000
WIDTH = 2

#How alike two sounds have to be before one is passed over.
#
#Cautious on purpose. Cosine between openWakeWord embeddings of unrelated
#speech sits well below this; the same recording heard twice sits near 1.0.
#The cost of the two mistakes is nowhere near equal - a threshold too high
#means a nuisance wake that shows up in the log, and one too low means the
#panel silently ignoring somebody saying the wake word properly.
DEFAULT_THRESHOLD = 0.93


def _now() -> float:
    return time.time()


def pack(vector) -> str:
    """A vector as base64 float32, which is a third the size of the digits."""
    flat = [float(x) for x in _flatten(vector)]
    return base64.b64encode(
        struct.pack(f"<{len(flat)}f", *flat)).decode("ascii")


def unpack(blob: str) -> list:
    raw = base64.b64decode(blob.encode("ascii"))
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


def _flatten(vector) -> list:
    """(1, 16, 96) or any nesting, as one list."""
    out = []
    stack = [vector]
    while stack:
        item = stack.pop()
        if hasattr(item, "tolist"):
            item = item.tolist()
        if isinstance(item, (list, tuple)):
            stack.extend(reversed(item))
        else:
            out.append(item)
    return out


def cosine(a: list, b: list) -> float:
    """
    How alike two vectors point, ignoring how long they are.

    Written out rather than reached for from numpy: this runs a handful of
    times per wake on 1536 numbers, which is nothing, and the speech process
    should not gain a dependency for arithmetic this size.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = top = bottom = 0.0
    for x, y in zip(a, b):
        dot += x * y
        top += x * x
        bottom += y * y
    if top <= 0.0 or bottom <= 0.0:
        return 0.0
    return dot / math.sqrt(top * bottom)


class Clip:
    """One trigger: what it scored, what it sounded like, what came of it."""

    def __init__(self, key: str = "", at: float = 0.0, score: float = 0.0,
                 bar: float = 0.0, wav: str = "", transcript: str = "",
                 outcome: str = "", vector: str = "", ignored: bool = False,
                 similarity: float = 0.0, suppressed: bool = False,
                 matched: str = ""):
        self.key = key or f"w-{uuid.uuid4().hex[:8]}"
        self.at = at or _now()
        self.score = float(score)
        self.bar = float(bar)
        self.wav = wav
        self.transcript = transcript
        # What the turn came to: "answered", "nothing", "timeout", "capped".
        self.outcome = outcome
        self.vector = vector
        self.ignored = bool(ignored)
        # The best similarity against the ignore list at the moment it fired,
        # recorded whether or not anything was suppressed. Without it there is
        # no way to choose a threshold except by guessing.
        self.similarity = float(similarity)
        self.suppressed = bool(suppressed)
        self.matched = matched

    def as_dict(self) -> dict:
        return {"key": self.key, "at": self.at, "score": self.score,
                "bar": self.bar, "wav": self.wav,
                "transcript": self.transcript, "outcome": self.outcome,
                "vector": self.vector, "ignored": self.ignored,
                "similarity": self.similarity, "suppressed": self.suppressed,
                "matched": self.matched}

    @classmethod
    def from_dict(cls, data: dict) -> "Clip":
        return cls(**{k: data.get(k) for k in
                      ("key", "at", "score", "bar", "wav", "transcript",
                       "outcome", "vector", "ignored", "similarity",
                       "suppressed", "matched")
                      if data.get(k) is not None})

    def when(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.at))


#Outcomes that say the turn came to nothing. A wake followed by a question
#that was answered is a wake that worked, whatever it transcribed as.
BAD_OUTCOMES = ("nothing", "timeout", "capped")


def should_learn(transcript: str, outcome: str, heard_wake: bool) -> tuple:
    """
    Whether a trigger was noise, and why. Answers (learn, reason).

    All three have to agree, and the first is the one that matters most.

    **There has to be a transcript at all.** Without it, somebody saying the
    wake word and then changing their mind produces no text, no wake word in
    that text, and a timeout - every condition satisfied, and the panel
    learns the real wake word as noise. Requiring evidence to exist before
    acting on it costs nothing and closes that.

    Then: the wake word is not in what was heard, tested generously, so a
    mishearing counts as present and blocks learning. And the turn came to
    nothing, which is a far better signal than the transcript on its own -
    a real wake is nearly always followed by a question that gets answered.
    """
    said = str(transcript or "").strip()
    if not said:
        return False, "nothing was transcribed, so there is no evidence either way"
    if heard_wake:
        return False, "the wake word is in what was heard"
    if outcome not in BAD_OUTCOMES:
        return False, f"the turn ended as '{outcome}' rather than coming to nothing"
    return True, f"heard {said[:40]!r} with no wake word in it, and it {outcome}"


class WakeMemory:
    """
    The clips and the ignore list, on disk.

    Kept in the folder the speech process is given. Both halves are small: ten
    clips of a second or two, and ten vectors of six kilobytes.
    """

    def __init__(self, folder: str, threshold: float = DEFAULT_THRESHOLD,
                 log=None):
        self.folder = str(folder)
        self.threshold = float(threshold)
        self.log = log or (lambda level, message: None)
        self.clips: list = []
        self.ignored: list = []
        self._loaded = False

    ## -- where things live

    @property
    def index_path(self) -> str:
        return os.path.join(self.folder, "wake-memory.json")

    def clip_dir(self, ignored: bool = False) -> str:
        return os.path.join(self.folder, "ignored" if ignored else "clips")

    def path_for(self, clip: Clip) -> str:
        return os.path.join(self.clip_dir(clip.ignored), clip.wav)

    ## -- reading and writing

    def load(self) -> None:
        self._loaded = True
        try:
            with open(self.index_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return
        self.clips = [Clip.from_dict(c) for c in data.get("clips", [])]
        self.ignored = [Clip.from_dict(c) for c in data.get("ignored", [])]

    def save(self) -> None:
        try:
            os.makedirs(self.folder, exist_ok=True)
            tmp = self.index_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump({"clips": [c.as_dict() for c in self.clips],
                           "ignored": [c.as_dict() for c in self.ignored]},
                          handle, indent=1)
            # Replaced rather than written over. A panel pulled from the wall
            # mid-write would otherwise leave a half file that reads as no
            # memory at all.
            os.replace(tmp, self.index_path)
        except OSError as exc:
            self.log("warning", f"[Wake] Could not save the memory: {exc}")

    ## -- matching

    def best_match(self, vector) -> tuple:
        """
        The closest thing on the ignore list, and how close. (clip, score).

        Answers the closest whatever it scored, so the caller can record a
        near miss as well as a hit - which is the only way to tell whether the
        threshold is set anywhere near right.
        """
        flat = _flatten(vector)
        # Starts below anything cosine can answer, not at zero. Two unrelated
        # sounds routinely score slightly negative, and starting at zero threw
        # those away - the closest entry came back as None and the similarity
        # as 0.0, which is a number that was never measured. The whole point
        # of recording it is to see the spread of real ones against real
        # false ones before choosing where to draw the line.
        best, best_score = None, -1.1
        for entry in self.ignored:
            score = cosine(flat, unpack(entry.vector))
            if score > best_score:
                best, best_score = entry, score
        return best, (best_score if best is not None else 0.0)

    def should_suppress(self, vector) -> tuple:
        """(suppress, matched clip, similarity)."""
        match, score = self.best_match(vector)
        return (score >= self.threshold and match is not None), match, score

    ## -- remembering

    def add_clip(self, audio: bytes, vector, score: float, bar: float,
                 similarity: float = 0.0, suppressed: bool = False,
                 matched: str = "") -> Clip:
        """Keep a trigger for listening to. Oldest out at the cap."""
        clip = Clip(score=score, bar=bar, vector=pack(vector),
                    similarity=similarity, suppressed=suppressed,
                    matched=matched)
        clip.wav = f"{clip.key}.wav"
        if self._write_wav(self.clip_dir(False), clip.wav, audio):
            self.clips.insert(0, clip)
            self._evict()
            self.save()
        return clip

    def learn(self, clip: Clip, reason: str = "") -> bool:
        """
        Put a clip on the ignore list.

        Its audio is copied rather than referenced. A clip falls off the
        listening list once `MAX_CLIPS` more have arrived, and an ignore entry
        whose sound had been deleted would be a rule nobody could check by ear.
        """
        if any(entry.key == clip.key for entry in self.ignored):
            return False
        if not clip.vector:
            return False

        kept = Clip.from_dict(clip.as_dict())
        kept.ignored = True
        try:
            source = os.path.join(self.clip_dir(False), clip.wav)
            with open(source, "rb") as handle:
                body = handle.read()
            os.makedirs(self.clip_dir(True), exist_ok=True)
            with open(os.path.join(self.clip_dir(True), kept.wav), "wb") as out:
                out.write(body)
        except OSError as exc:
            self.log("warning", f"[Wake] Could not keep the audio for "
                                f"{clip.key}: {exc}")

        self.ignored.insert(0, kept)
        while len(self.ignored) > MAX_IGNORED:
            self._drop(self.ignored.pop())
        clip.ignored = True
        self.log("info", f"[Wake] Ignoring sounds like {clip.key} from now on"
                         f"{' - ' + reason if reason else ''}.")
        self.save()
        return True

    def forget(self, key: str) -> bool:
        """Take one off the ignore list. The way back from a wrong call."""
        for index, entry in enumerate(self.ignored):
            if entry.key == key:
                self._drop(self.ignored.pop(index))
                for clip in self.clips:
                    if clip.key == key:
                        clip.ignored = False
                self.log("info", f"[Wake] No longer ignoring {key}.")
                self.save()
                return True
        return False

    def clear(self) -> int:
        gone = len(self.ignored)
        while self.ignored:
            self._drop(self.ignored.pop())
        for clip in self.clips:
            clip.ignored = False
        self.save()
        return gone

    ## -- housekeeping

    def _evict(self) -> None:
        while len(self.clips) > MAX_CLIPS:
            old = self.clips.pop()
            if old.ignored:
                # The listening copy only. `learn()` took its own copy into
                # the ignored folder and that one outlives this list, so
                # dropping both here would delete the audio behind a rule
                # that is still in force - and a rule nobody can check by ear
                # is the thing the copy exists to prevent.
                self._drop_one(old, False)
            else:
                self._drop(old)

    def _drop_one(self, clip: Clip, ignored: bool) -> None:
        try:
            os.remove(os.path.join(self.clip_dir(ignored), clip.wav))
        except OSError:
            pass

    def _drop(self, clip: Clip) -> None:
        for ignored in (True, False):
            self._drop_one(clip, ignored)

    def _write_wav(self, folder: str, name: str, audio: bytes) -> bool:
        try:
            os.makedirs(folder, exist_ok=True)
            with wave.open(os.path.join(folder, name), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(WIDTH)
                handle.setframerate(RATE)
                handle.writeframes(audio)
            return True
        except (OSError, wave.Error) as exc:
            self.log("warning", f"[Wake] Could not save the clip: {exc}")
            return False

    ## -- for a page to draw

    def snapshot(self) -> dict:
        return {"threshold": self.threshold,
                "clips": [c.as_dict() for c in self.clips],
                "ignored": [c.as_dict() for c in self.ignored]}
