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

from typing import Callable, Optional


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

    def matches(self, phrase: str) -> bool:
        return " ".join(str(phrase or "").lower().split()) in self.keywords

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
