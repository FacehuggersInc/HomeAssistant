"""
What "stop" means right now.

"Nevermind" and "stop" are not one instruction. Said with an answer panel open
they mean close it; said over music they mean stop the music; said with neither
they mean stop listening. And the words are not interchangeable either - "stop"
makes sense for music and "nevermind" does not, while "nevermind" is exactly
right for a question somebody has thought better of asking.

So whatever can be cancelled says so itself: which words apply to it, when it
is applicable, and what to do. The skill asks this registry rather than holding
a list of special cases that has to grow every time something new appears.
"""

from __future__ import annotations

import re
from typing import Callable, Optional


def _words(text: str) -> list:
    """
    A phrase as plain lowercase words.

    Punctuation goes: a transcriber writes "me, stop" and the comma is not
    something anybody said.
    """
    return re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower()).split()


class CancelAction:
    """One thing that can be backed out of."""

    __slots__ = ("owner", "key", "keywords", "is_active", "handler",
                 "priority", "description", "stops_listening")

    def __init__(self, owner: str, key: str, keywords: list,
                 handler: Callable, is_active: Callable = None,
                 priority: int = 0, description: str = "",
                 stops_listening: bool = True):
        self.owner = owner
        self.key = key
        # Normalised on the way in, so a caller does not have to think about
        # case or spacing.
        self.keywords = sorted({" ".join(str(k).lower().split())
                                for k in (keywords or []) if str(k).strip()})
        self.handler = handler
        self.is_active = is_active
        self.priority = int(priority)
        self.description = description or key
        # Whether backing out of this should also stand the assistant down.
        # Stopping music does not mean somebody has finished talking.
        self.stops_listening = bool(stops_listening)

    def active(self) -> bool:
        if self.is_active is None:
            return True
        try:
            return bool(self.is_active())
        except Exception:
            return False

    def matched(self, phrase: str) -> str:
        """
        Which of this action's keywords is IN the phrase, or "".

        Contained, not equal to. A registered keyword that only ever fired
        when somebody said exactly that word and nothing else would miss
        "stop the music" and "why are you still hearing me, stop".

        Whole words, in order, not a substring: "stop" must not fire on
        "stopwatch", and punctuation is dropped so a comma cannot separate
        somebody from what they asked for.

        The keyword rather than a bool, so a caller can say which word it
        acted on. `matches()` is the same question asked yes-or-no, and
        `matched_all()` gives every one that fits.
        """
        found = self.matched_all(phrase)
        return found[0] if found else ""

    def matched_all(self, phrase: str) -> list:
        """
        Every keyword of this action that is in the phrase, longest first.

        Longest first because keywords overlap: "please stop it" contains
        both `stop` and `stop it`, and the more specific one is what somebody
        said. Reported alphabetically, "stop" came back and a caller asking
        whether it was said at the END was told no - the last word is "it".

        All of them, because a caller can have a reason to reject the best
        one and still want the next.
        """
        words = _words(phrase)
        if not words:
            return []
        found = []
        for keyword in sorted(self.keywords,
                              key=lambda k: (-len(k.split()), k)):
            wanted = keyword.split()
            if not wanted:
                continue
            span = len(wanted)
            for start in range(len(words) - span + 1):
                if words[start:start + span] == wanted:
                    found.append(keyword)
                    break
        return found

    def matches(self, phrase: str) -> bool:
        """Whether any of this action's keywords is in the phrase."""
        return bool(self.matched(phrase))

    def run(self) -> bool:
        try:
            self.handler()
            return True
        except Exception:
            return False


class CancelRegistry:
    """
    `client.CANCEL`.

    Ordered by priority, highest first. Two things being cancellable at once is
    normal - an answer panel open over playing music - and the one in front is
    the one somebody means.
    """

    def __init__(self, client):
        self.client = client
        self._actions: dict = {}

    ## -- registration

    def register(self, owner: str, key: str, keywords: list,
                 handler: Callable, is_active: Callable = None,
                 priority: int = 0, description: str = "",
                 stops_listening: bool = True) -> Optional[CancelAction]:
        if not callable(handler):
            self.client.log("warning", f"[Cancel] '{key}' has no handler.")
            return None
        if not keywords:
            self.client.log("warning", f"[Cancel] '{key}' registered no "
                                       f"keywords, so nothing can trigger it.")
            return None

        action = CancelAction(owner, key, keywords, handler, is_active,
                              priority, description, stops_listening)
        self._actions[f"{owner}.{key}"] = action
        self.client.log("info", f"[Cancel] '{key}' registered by '{owner}' "
                                f"for {len(action.keywords)} phrase(s).")
        return action

    def unregister(self, owner: str, key: str = "") -> None:
        wanted = f"{owner}.{key}" if key else None
        for name in [n for n, a in self._actions.items()
                     if (n == wanted) or (wanted is None and a.owner == owner)]:
            del self._actions[name]

    def actions(self) -> list:
        """Every registered action, highest priority first."""
        return sorted(self._actions.values(),
                      key=lambda a: (-a.priority, a.key))

    ## -- use

    def keywords(self) -> set:
        """
        Every phrase any action answers to.

        Used to build the skill's examples, so a plugin adding a new word does
        not also have to edit the skill.
        """
        found = set()
        for action in self._actions.values():
            found |= set(action.keywords)
        return found

    def applicable(self, phrase: str) -> list:
        """The active actions this phrase applies to, in priority order."""
        return [a for a in self.actions() if a.active() and a.matches(phrase)]

    def run(self, phrase: str) -> Optional[CancelAction]:
        """
        Do whatever that phrase means right now.

        Returns the action taken, or None when the phrase applies to nothing -
        which is not a failure. It means there was nothing to back out of
        except the listening itself, and that is the caller's business.
        """
        for action in self.applicable(phrase):
            if action.run():
                self.client.log("info", f"[Cancel] '{phrase}' -> "
                                        f"{action.description}.")
                return action
            self.client.log("warning", f"[Cancel] '{action.key}' failed; "
                                       f"trying the next.")
        return None

    def handle(self, phrase: str) -> bool:
        """
        Back out of whatever that phrase means right now. Always handled.

        `run()` answers what applied; this answers what to DO about it, which
        is the part every caller was writing out for itself. There are three -
        the check ahead of the skills, a follow-up inside a session, and the
        nevermind skill - and a fourth would have copied it too.

        Nothing applying is not a failure. It means there was nothing in front
        to close, so backing out is the listening itself.
        """
        action = self.run(phrase)
        if action is None:
            self.client.cancel_assistant("nevermind")
            return True
        # Stopping the music does not mean somebody has finished talking, so
        # the action says whether standing down goes with it.
        if action.stops_listening:
            self.client.cancel_assistant(f"nevermind: {action.key}")
        return True
