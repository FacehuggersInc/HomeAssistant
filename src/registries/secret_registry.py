from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from src.constants import INSTALL_ROOT

if TYPE_CHECKING:
    from src.main import Client

ENV_PATH = INSTALL_ROOT / ".env"

# Env var names: letters, digits and underscores, not starting with a digit.
_VALID_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

MASK = "••••••••"


class SecretRegistry:
    """
    API keys and other credentials, keyed by name and stored in .env.

    Plugins declare the NAMES they need in plugin.toml; values are never in a
    toml, never in the settings JSON, and never in a log line. The settings
    file is written to disk on every save, synced by the updater's preserve
    list, and shown wholesale in the Settings UI - a credential in there
    leaks by default. .env is already excluded from updates and from git.

    A `secret` setting reads and writes through here rather than storing
    anything in its own `value`.
    """

    def __init__(self, client: "Client"):
        self.client = client
        self.store: dict[str, set[str]] = {}   # {owner: {KEY, ...}}
        self.meta: dict[str, dict] = {}        # {KEY: {"owner":..., "label":...}}
        # Told when a credential changes - see subscribe(). Each entry is
        # (owner, callback); the owner is what lets an unloading plugin's
        # callback go with it.
        self._watchers: list = []
        self.load_env()

    def load_env(self) -> int:
        """
        Read .env into the process. Returns how many names it carried.

        Nothing did this. `set()` writes the file AND the environment, so a key
        entered on the settings page worked for that run and read as unset on
        the next one - and a key put in the file by hand was never seen at all,
        because the only thing that had ever put a name in `os.environ` was
        this process writing it there.

        Existing environment variables win. A key exported by a service file is
        a deliberate choice by whoever runs the panel, and a file checked out
        beside it should not quietly replace it.
        """
        try:
            from dotenv import load_dotenv
        except Exception as e:
            self.client.log("warning",
                            f"[SecretRegistry] Could not read .env: {e}")
            return 0
        if not ENV_PATH.is_file():
            return 0
        try:
            before = set(os.environ)
            load_dotenv(ENV_PATH, override=False)
            return len(set(os.environ) - before)
        except Exception as e:
            self.client.log("warning",
                            f"[SecretRegistry] Could not read .env: {e}")
            return 0

    ## -- REGISTRATION

    def register(self, owner: str, key: str, label: str = "",
                 description: str = "") -> bool:
        key = (key or "").strip()
        if not key or not _VALID_KEY.match(key):
            self.client.log("warning",
                            f"[SecretRegistry] '{key}' is not a valid environment "
                            f"variable name - ignored (owner '{owner}')")
            return False

        existing = self.meta.get(key)
        if existing and existing["owner"] != owner:
            # Sharing is fine and sometimes intended, but it should be visible.
            self.client.log("info",
                            f"[SecretRegistry] '{key}' is also declared by "
                            f"'{existing['owner']}' - both will read the same value")

        self.store.setdefault(owner, set()).add(key)
        self.meta.setdefault(key, {"owner": owner, "label": label or key,
                                   "description": description})
        self.client.log("info", f"[SecretRegistry] '{key}' declared by '{owner}'")
        return True

    def register_many(self, owner: str, keys) -> list[str]:
        return [k for k in (keys or []) if self.register(owner, k)]

    def unregister(self, owner: str, key: str = "") -> None:
        """
        Forget a declaration. The stored VALUE is deliberately left alone -
        unloading a plugin should not delete the user's credential, since
        reloading it would silently require typing the key in again.

        Forgetting an owner entirely also forgets what it subscribed. A single
        key does not: the owner is still here and still watching its others.
        """
        if not key:
            # Above the store check on purpose. Subscribing does not require
            # declaring anything, so an owner can be watching and absent from
            # the store, and returning early would leave the callback behind.
            gone = self._drop_watchers(owner)
            if gone:
                self.client.log("debug",
                                f"[SecretRegistry] Dropped {gone} watcher(s) "
                                f"for '{owner}'")
        if owner not in self.store:
            return
        if key:
            self.store[owner].discard(key)
            if not any(key in keys for keys in self.store.values()):
                self.meta.pop(key, None)
            if not self.store[owner]:
                del self.store[owner]
        else:
            for k in self.store.pop(owner, set()):
                if not any(k in keys for keys in self.store.values()):
                    self.meta.pop(k, None)

    ## -- QUERY

    def keys_for(self, owner: str) -> list[str]:
        return sorted(self.store.get(owner, set()))

    def owner_of(self, key: str) -> Optional[str]:
        entry = self.meta.get(key)
        return entry["owner"] if entry else None

    def label_for(self, key: str) -> str:
        entry = self.meta.get(key)
        return entry["label"] if entry else key

    def is_declared(self, key: str) -> bool:
        return key in self.meta

    def is_set(self, key: str) -> bool:
        return bool(self.get(key))

    def status(self, key: str) -> str:
        return "Set" if self.is_set(key) else "Not set"

    ## -- VALUES

    def _read(self, key: str, default: str = "") -> str:
        """
        Raw read, no ownership check. Internal.

        Callers should use get_for(); this exists for the registry itself and
        for the settings UI, which is privileged by definition.

        Falls back to the file. The environment is loaded once at startup, so
        a key added to .env by hand while the panel is running is not in it -
        and "Not set" beside a key somebody just typed into the file is the
        panel calling them a liar.
        """
        found = os.environ.get(key)
        if found:
            return found
        found = self._read_env_file().get(key, "")
        if found:
            # Remembered, so the next read is not another file open.
            os.environ[key] = found
            return found
        return default

    def _read_env_file(self) -> dict:
        """Every name in .env, or an empty mapping."""
        if not ENV_PATH.is_file():
            return {}
        try:
            from dotenv import dotenv_values
            return {k: v for k, v in dotenv_values(ENV_PATH).items() if v}
        except Exception:
            return {}

    def get(self, key: str, default: str = "") -> str:
        return self._read(key, default)

    def get_for(self, owner: str, key: str, default: str = "") -> str:
        """
        Value of a key the caller owns.

        A plugin asking for a key it did not declare gets the default and a
        log line. This is not a security boundary - anything running in this
        process can read os.environ directly - it is there so a plugin cannot
        reach another's credential through the Client by accident or by
        casual intent.
        """
        if not self.is_declared(key):
            self.client.log("warning",
                            f"[SecretRegistry] '{owner}' asked for undeclared secret '{key}'")
            return default

        keys = self.store.get(owner, set())
        if key not in keys:
            self.client.log("warning",
                            f"[SecretRegistry] '{owner}' asked for '{key}', which belongs "
                            f"to '{self.owner_of(key)}' - refused")
            return default

        return self._read(key, default)

    def set_for(self, owner: str, key: str, value: str) -> bool:
        """Write a key the caller owns. Same rule as get_for()."""
        if key not in self.store.get(owner, set()):
            self.client.log("warning",
                            f"[SecretRegistry] '{owner}' tried to write '{key}', "
                            f"which it does not own - refused")
            return False
        return self.set(key, value)

    def set(self, key: str, value: str) -> bool:
        """
        Write to .env and to the running process.

        Returns False rather than raising: a failed credential write should
        surface as a message, not take the settings page down.
        """
        key = (key or "").strip()
        if not _VALID_KEY.match(key or ""):
            return False

        value = "" if value is None else str(value)
        try:
            self._ensure_env_file()
            from dotenv import set_key
            set_key(str(ENV_PATH), key, value)
        except Exception as e:
            # Never include the value in the message.
            self.client.log("error", f"[SecretRegistry] Could not write '{key}' to .env: {e}")
            return False

        os.environ[key] = value
        self.client.log("info", f"[SecretRegistry] '{key}' updated ({self.status(key)})")
        # Told on every successful write, including one that writes the same
        # value again: pasting a key is somebody saying to try again, and a
        # backend that stopped asking has no other way to hear it.
        self._notify(key)
        return True

    def clear(self, key: str) -> bool:
        ok = self.set(key, "")
        os.environ.pop(key, None)
        return ok

    ## -- CHANGE NOTIFICATION

    def subscribe(self, callback: Callable, owner: str = "client") -> None:
        """
        Be told when a credential is written, with the key as the argument.

        A credential never appears in the settings file, so nothing watching
        settings sees one arrive. Anything that cannot start without a key -
        a voice backend, an API client - needs telling directly or it stays
        exactly as broken as it was when the key was pasted in.

        Pass the plugin key as `owner`, so unloading takes the callback with
        it. A callback left behind by an unloaded plugin holds its module
        alive and is only dropped the next time it throws, which is a write
        that logged a failure nobody caused.
        """
        # Compared by equality, not identity. `self._on_key` builds a new
        # bound method object every time it is read, so an identity test
        # would let the same method subscribe as many times as it is passed
        # and call it once per copy.
        if not any(cb == callback for _owner, cb in self._watchers):
            self._watchers.append((owner, callback))

    def unsubscribe(self, callback: Callable) -> None:
        self._watchers = [entry for entry in self._watchers
                          if entry[1] != callback]

    def _drop_watchers(self, owner: str) -> int:
        """Forget everything an owner subscribed. Returns how many went."""
        keep = [entry for entry in self._watchers if entry[0] != owner]
        gone = len(self._watchers) - len(keep)
        self._watchers = keep
        return gone

    def _notify(self, key: str) -> None:
        for entry in list(self._watchers):
            owner, callback = entry
            try:
                callback(key)
            except Exception as e:
                # Dropped rather than left to throw on every future write.
                # Never include the value; the key name alone is safe.
                if entry in self._watchers:
                    self._watchers.remove(entry)
                self.client.log("warning",
                                f"[SecretRegistry] Watcher for '{owner}' failed "
                                f"and was removed: {e}")

    ## -- FILE

    def _ensure_env_file(self) -> None:
        if not ENV_PATH.exists():
            ENV_PATH.touch()
        if os.name != "nt":
            # Owner read/write only. A credentials file should not be
            # world-readable just because the umask was loose.
            try:
                ENV_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass

    def env_path(self) -> Path:
        return ENV_PATH

    ## -- DISPLAY

    def masked(self, key: str) -> str:
        """Never returns the value. For logs and the registrations card."""
        return MASK if self.is_set(key) else ""

    def all_keys(self) -> list[str]:
        return sorted(self.meta.keys())
