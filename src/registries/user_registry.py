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


#What a device may do beyond the ordinary.
#
#Approval is the door; this is what somebody may touch once inside. Kept as a
#SET of named permissions rather than a flag per capability, so the next one to
#exist is a string in this tuple and a checkbox, not another column in the
#saved file and another migration for every install that predates it.
#
#Nobody has any of these by default, including a device approved long before
#they existed. A permission that arrives switched on for everybody is not a
#permission.
PERMISSIONS = (
    ("plugins", "Manage plugins",
     "Upload, load, unload and reload plugins. Plugins are code and run with "
     "the same reach as the panel itself."),
)

PERMISSION_KEYS = frozenset(key for key, _label, _help in PERMISSIONS)


class User:
    """One approved device."""

    def __init__(self, token: str, name: str, address: str = "",
                 approved_at: float = None, last_seen: float = None,
                 note: str = "", awaiting_name: bool = False,
                 awaiting_decision: bool = False,
                 permissions=None):
        self.token = token
        self.name = name
        self.address = address
        self.approved_at = approved_at or time.time()
        self.last_seen = last_seen or self.approved_at
        self.note = note
        # Approved, but the panel handed naming to the device rather than
        # deciding for it. Until that comes back the name is a placeholder.
        self.awaiting_name = awaiting_name
        # The panel has said yes, but nobody has answered "name them, or let
        # them name themselves?" yet. The device must keep waiting through
        # this - being sent to the naming page while that question is still on
        # screen is how it ended up naming itself over the top of somebody
        # halfway through naming it.
        self.awaiting_decision = awaiting_decision
        # Only names that still exist. A permission removed from the app
        # should not linger in a saved file and come back if the name is ever
        # reused for something else.
        self.permissions = {str(p) for p in (permissions or [])
                            if str(p) in PERMISSION_KEYS}

    def may(self, permission: str) -> bool:
        return str(permission) in self.permissions

    def to_dict(self) -> dict:
        return {"token": self.token, "name": self.name, "address": self.address,
                "approved_at": self.approved_at, "last_seen": self.last_seen,
                "note": self.note, "awaiting_name": self.awaiting_name,
                "awaiting_decision": self.awaiting_decision,
                "permissions": sorted(self.permissions)}

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
            awaiting_decision=bool(raw.get("awaiting_decision", False)),
            # Absent in a file written before permissions existed, which
            # means none - see PERMISSIONS.
            permissions=raw.get("permissions") or [],
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
        if self.is_panel(token):
            return getattr(self, "_panel_user", None)
        return self.users.get((token or "").strip())

    def is_approved(self, token: str) -> bool:
        if self.is_panel(token):
            return True
        return self.get(token) is not None

    #The panel's own identity.
    #
    #It runs the backend and it calls its own routes - an action tile asking
    #/dashboard/state, a skill posting to /say. Every route wants a device
    #token and the panel is not a device, so without this there are two bad
    #options: no access at all until somebody approves a phone, or borrowing
    #an approved device's token. The second is worse than it sounds - touch()
    #would mark that person as active, /say would announce their name as the
    #sender, and revoking them would silently break the panel.
    #
    #Not written to disk and not listed among the users: it is not somebody's
    #device, it cannot be revoked, and it should not appear on a page about
    #who has access.
    PANEL_NAME = "This panel"

    def panel_token(self) -> str:
        """
        The token the panel uses to call itself.

        Made once per run and kept in memory. A new one every launch is
        correct - nothing should be able to save it, replay it, or find it in
        a file.
        """
        existing = getattr(self, "_panel_token", "")
        if existing:
            return existing

        import secrets
        self._panel_token = secrets.token_urlsafe(32)
        self._panel_user = User(self._panel_token, self.PANEL_NAME,
                                address="127.0.0.1")
        return self._panel_token

    def is_panel(self, token: str) -> bool:
        """Whether this is the panel calling itself."""
        return bool(token) and token == getattr(self, "_panel_token", "")

    def touch(self, token: str) -> Optional[User]:
        """Record that a device was seen, and return it."""
        if self.is_panel(token):
            # Answered, but not recorded. There is no "last seen" worth
            # keeping for something that is always here.
            return self._panel_user
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
                let_user_name: bool = False,
                pending_decision: bool = False) -> Optional[User]:
        request = self.pending.pop(token, None)
        if request is None:
            return None
        user = User(token=token, name=name or request.name,
                    address=request.address, awaiting_name=let_user_name,
                    awaiting_decision=pending_decision)
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
        # A name has arrived, from wherever - the device is no longer waiting,
        # and neither is the question about who names it.
        user.awaiting_name = False
        user.awaiting_decision = False
        self.save()
        self._notify()
        return True

    ## -- permissions

    def may(self, token: str, permission: str) -> bool:
        """
        Whether this device holds `permission`.

        Approval is checked here too, not assumed. A revoked device keeps its
        token, and a permission read off a user object without asking whether
        that user is still let in is a door that stays open after the lock is
        changed.
        """
        if not self.is_approved(token):
            return False
        # The panel itself is not a device with permissions. It IS the thing
        # granting them, and nothing it does goes over the network.
        if self.is_panel(token):
            return True
        user = self.get(token)
        return bool(user and user.may(permission))

    def grant(self, token: str, permission: str) -> bool:
        if str(permission) not in PERMISSION_KEYS:
            return False
        user = self.users.get(token)
        if user is None or permission in user.permissions:
            return False
        user.permissions.add(str(permission))
        self.client.log("info", f"[Users] '{user.name}' granted "
                                f"'{permission}'.")
        self.save()
        self._notify()
        return True

    def revoke_permission(self, token: str, permission: str) -> bool:
        user = self.users.get(token)
        if user is None or permission not in user.permissions:
            return False
        user.permissions.discard(str(permission))
        self.client.log("info", f"[Users] '{user.name}' no longer has "
                                f"'{permission}'.")
        self.save()
        self._notify()
        return True

    def set_permissions(self, token: str, permissions) -> bool:
        """Replace the whole set, for a form that submits every checkbox."""
        user = self.users.get(token)
        if user is None:
            return False
        wanted = {str(p) for p in (permissions or []) if str(p) in PERMISSION_KEYS}
        if wanted == user.permissions:
            return False
        user.permissions = wanted
        self.client.log("info", f"[Users] '{user.name}' permissions are now "
                                f"{sorted(wanted) or 'none'}.")
        self.save()
        self._notify()
        return True

    def needs_name(self, token: str) -> bool:
        """Whether the DEVICE should be asked - not merely that it has no name."""
        user = self.get(token)
        return bool(user and user.awaiting_name and not user.awaiting_decision)

    def awaiting_decision(self, token: str) -> bool:
        user = self.get(token)
        return bool(user and user.awaiting_decision)

    def settle_decision(self, token: str, let_user_name: bool) -> bool:
        """
        The panel answered "name them" or "let them decide".

        Until this is called an approved device stays waiting, which is what
        stops it walking off to the naming page mid-question.
        """
        user = self.users.get(token)
        if user is None:
            return False
        user.awaiting_decision = False
        user.awaiting_name = bool(let_user_name)
        self.save()
        self._notify()
        return True

    def names(self) -> list:
        """
        Who can own something, for a picker.

        A device still choosing its own name is left out - offering a
        placeholder as an owner is how a household ends up with three events
        belonging to "Browser on Linux".
        """
        return sorted({u.name for u in self.users.values()
                       if not u.awaiting_name and not u.awaiting_decision})

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
