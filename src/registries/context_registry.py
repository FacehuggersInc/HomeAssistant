"""
What was last asked, and what the panel answered.

A wall panel is talked to in fragments. "What does petrichor mean" is followed
by "where does that come from", which names nothing and matches nothing - and
without the turn before it, that second question is unanswerable by anything.
This is where the turn before it lives.

**The client owns this, and only the client writes to it.** Plugins read it.
The alternative - every plugin keeping its own idea of what was just said -
gives as many histories as there are plugins, all subtly disagreeing about
which one was most recent.

Nothing here is persisted. A conversation is a thing that is happening, and a
panel restarted an hour later resuming "that" from before the restart would be
answering a question nobody remembers asking.
"""

from __future__ import annotations

import time
from threading import RLock


class ContextEntry:
    """One turn: what was asked, what came back, and what produced it."""

    __slots__ = ("skill", "query", "answer", "spoken", "data", "at")

    def __init__(self, skill: str, query: str):
        self.skill  = skill or ""
        self.query  = (query or "").strip()
        self.answer = ""
        self.spoken = ""
        self.data: dict = {}
        self.at     = time.time()

    @property
    def age(self) -> float:
        return time.time() - self.at

    def summary(self, limit: int = 400) -> str:
        """
        The turn as one line of prose, for handing to a model.

        Deliberately not JSON. Whatever reads this is a language model being
        told what happened a moment ago, and a serialised object spends its
        tokens on punctuation.
        """
        parts = []
        if self.query:
            parts.append(f"They asked: {self.query}")
        if self.answer:
            parts.append(f"The panel answered: {self.answer}")
        text = ". ".join(parts)
        return text[:limit] + "\u2026" if len(text) > limit else text

    def __repr__(self) -> str:
        return f"<ContextEntry {self.skill!r} {self.query!r}>"


class ContextRegistry:
    """
    The last few things asked, newest last.

    Written by the client at two moments: the intent engine opens an entry
    when a skill is about to run, and `answer()` fills in what was shown. A
    skill therefore gets context for free, and one that wants to record
    something specific calls `note()`.
    """

    #Turns kept. Enough that "the one before that" works, small enough that a
    #panel running for a month is not carrying a month of conversation.
    LIMIT = 12

    #How old a turn can be and still be what "that" refers to. Somebody
    #returning to the panel after lunch is starting a new conversation, and
    #answering them out of the last one is worse than having no memory at all.
    RELEVANT_FOR = 300.0

    def __init__(self, client):
        self.client = client
        self._lock = RLock()
        self._entries: list = []
        self._open: ContextEntry | None = None

    ## -- writing, client only

    def begin(self, skill: str, query: str) -> ContextEntry:
        """
        A skill is about to run. Open a turn for it.

        Opened rather than appended, because the answer does not exist yet.
        An entry that never gets one is still worth keeping - "what did I
        ask" is answerable from the question alone.
        """
        with self._lock:
            entry = ContextEntry(skill, query)
            self._open = entry
            self._entries.append(entry)
            del self._entries[:-self.LIMIT]
            return entry

    def record_answer(self, answer: str = "", spoken: str = "") -> None:
        """What the panel showed, against the turn that is open."""
        with self._lock:
            if self._open is None:
                return
            if answer:
                self._open.answer = str(answer)
            if spoken:
                self._open.spoken = str(spoken)

    def note(self, **data) -> None:
        """
        Anything a skill wants remembered beyond the text.

        The dictionary skill puts the word here. "Where does that come from"
        is then answerable, where it is not from the panel's own prose - the
        answer says what the word means and never repeats the word.
        """
        with self._lock:
            if self._open is not None:
                self._open.data.update(data)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._open = None

    ## -- reading, anyone

    @property
    def last(self) -> ContextEntry | None:
        """The most recent turn, or None if it is too old to mean anything."""
        with self._lock:
            if not self._entries:
                return None
            entry = self._entries[-1]
            return entry if entry.age <= self.RELEVANT_FOR else None

    def history(self, count: int = 3) -> list:
        """The most recent turns, oldest first."""
        with self._lock:
            return list(self._entries[-max(0, int(count)):])

    def summary(self, count: int = 1, limit: int = 400) -> str:
        """The recent turns as prose, or "" if there is nothing recent."""
        turns = [entry for entry in self.history(count)
                 if entry.age <= self.RELEVANT_FOR and entry.summary()]
        return "\n".join(turn.summary(limit) for turn in turns)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
