"""
Approved devices, instead of one shared secret.

The client ID was a single string every caller sent. It could not be revoked
without changing it for everything, it said nothing about who was asking, and
anything that learnt it was indistinguishable from the person who owned the
panel.

A user here is a device that asked for access and was allowed it. Each has its
own token, can be revoked on its own, and arrives with a name - so an endpoint
can tell who is calling and act on it.
"""

from __future__ import annotations

import json
import secrets
import time
from typing import Callable, Optional

# How long an unapproved request keeps its place in the queue. A device that
# asked and walked away should not sit in the dialog stack for the rest of the
# day.
PENDING_TTL = 300


class User:
    """One approved device."""

    def __init__(self, token: str, name: str, address: str = "",
                 approved_at: float = None, last_seen: float = None,
                 note: str = "", awaiting_name: bool = False):
        self.token = token
        self.name = name
        self.address = address
        self.approved_at = approved_at or time.time()
        self.last_seen = last_seen or self.approved_at
        self.note = note
        # Approved, but the panel handed naming to the device rather than
        # deciding for it. Until that comes back the name is a placeholder.
        self.awaiting_name = awaiting_name

    def to_dict(self) -> dict:
        return {"token": self.token, "name": self.name, "address": self.address,
                "approved_at": self.approved_at, "last_seen": self.last_seen,
                "note": self.note, "awaiting_name": self.awaiting_name}

    @classmethod
    def from_dict(cls, raw: dict) -> Optional["User"]:
        if not isinstance(raw, dict) or not raw.get("token"):
            return None
        return cls(
            token=str(raw["token"]),
            name=str(raw.get("name") or "Unnamed device"),
            address=str(raw.get("address") or ""),
            approved_at=raw.get("approved_at"),
            last_seen=raw.get("last_seen"),
            note=str(raw.get("note") or ""),
            awaiting_name=bool(raw.get("awaiting_name", False)),
        )


class PendingRequest:
    """A device waiting to be let in."""

    def __init__(self, token: str, name: str, address: str):
        self.token = token
        self.name = name
        self.address = address
        self.asked_at = time.time()
        self.decided: Optional[bool] = None

    @property
    def expired(self) -> bool:
        return (time.time() - self.asked_at) > PENDING_TTL


class UserRegistry:
    """
    Who is allowed to talk to this panel.

    Lives in the user data directory rather than in settings: it is state the
    person builds up by approving things, not configuration they edit, and an
    update unpacking over the app tree must not take it.
    """

    def __init__(self, client, path):
        self.client = client
        self.path = path
        self.users: dict = {}        # token -> User
        self.pending: dict = {}      # token -> PendingRequest
        self._listeners: list = []
        self.load()

    ## -- persistence

    def load(self) -> None:
        self.users = {}
        try:
            if not self.path.is_file():
                return
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            self.client.log("warning", f"[Users] Could not read users: {e}")
            return

        for raw in (payload.get("users") if isinstance(payload, dict) else payload) or []:
            user = User.from_dict(raw)
            if user is not None:
                self.users[user.token] = user
        self.client.log("info", f"[Users] {len(self.users)} approved device(s).")

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"users": [u.to_dict() for u in self.users.values()]},
                           indent=2),
                encoding="utf-8")
            if hasattr(self.path, "chmod"):
                try:
                    # Tokens. Same reasoning as the CLI's host file.
                    self.path.chmod(0o600)
                except OSError:
                    pass
        except OSError as e:
            self.client.log("warning", f"[Users] Could not save users: {e}")

    ## -- lookups

    def get(self, token: str) -> Optional[User]:
        return self.users.get((token or "").strip())

    def is_approved(self, token: str) -> bool:
        return self.get(token) is not None

    def touch(self, token: str) -> Optional[User]:
        """Record that a device was seen, and return it."""
        user = self.get(token)
        if user is not None:
            user.last_seen = time.time()
        return user

    def all_users(self) -> list:
        return sorted(self.users.values(), key=lambda u: u.name.lower())

    ## -- the approval flow

    def request_access(self, name: str, address: str = "") -> PendingRequest:
        """
        A new device asking to be let in.

        The token is generated here rather than sent by the device. A device
        choosing its own would let anything claim a token it had seen
        somewhere, and there would be no moment at which the panel decided.
        """
        token = secrets.token_urlsafe(24)
        request = PendingRequest(token, name or "Unnamed device", address)
        self.pending[token] = request
        self.client.log("info", f"[Users] '{request.name}' at {address} asked for access.")
        self._notify()
        return request

    def state_of(self, token: str) -> str:
        """approved | pending | denied | unknown"""
        if self.is_approved(token):
            return "approved"
        request = self.pending.get(token)
        if request is None:
            return "unknown"
        if request.expired:
            del self.pending[token]
            return "unknown"
        if request.decided is True:
            return "approved"
        if request.decided is False:
            return "denied"
        return "pending"

    def approve(self, token: str, name: str = "",
                let_user_name: bool = False) -> Optional[User]:
        request = self.pending.pop(token, None)
        if request is None:
            return None
        user = User(token=token, name=name or request.name,
                    address=request.address, awaiting_name=let_user_name)
        self.users[token] = user
        self.save()
        self.client.log("info", f"[Users] Approved '{user.name}'.")
        self._notify()
        return user

    def deny(self, token: str) -> None:
        request = self.pending.get(token)
        if request is not None:
            # Kept, briefly. The device is still polling and should be told no
            # rather than left to time out as though nobody was home.
            request.decided = False
        self._notify()

    def revoke(self, token: str) -> bool:
        user = self.users.pop(token, None)
        if user is None:
            return False
        self.save()
        self.client.log("info", f"[Users] Revoked '{user.name}'.")
        self._notify()
        return True

    def rename(self, token: str, name: str) -> bool:
        user = self.users.get(token)
        if user is None or not name.strip():
            return False
        user.name = name.strip()
        # A name has arrived, from wherever - the device is no longer waiting.
        user.awaiting_name = False
        self.save()
        self._notify()
        return True

    def needs_name(self, token: str) -> bool:
        user = self.get(token)
        return bool(user and user.awaiting_name)

    def names(self) -> list:
        """
        Who can own something, for a picker.

        A device still choosing its own name is left out - offering a
        placeholder as an owner is how a household ends up with three events
        belonging to "Browser on Linux".
        """
        return sorted({u.name for u in self.users.values() if not u.awaiting_name})

    def waiting(self) -> list:
        """Undecided requests, oldest first, with expired ones dropped."""
        for token in [t for t, r in self.pending.items() if r.expired]:
            del self.pending[token]
        return sorted((r for r in self.pending.values() if r.decided is None),
                      key=lambda r: r.asked_at)

    ## -- change notification

    def subscribe(self, callback: Callable) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def unsubscribe(self, callback: Callable) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _notify(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception as e:
                self._listeners.remove(callback)
                self.client.log("warning", f"[Users] Listener removed after error: {e}")
