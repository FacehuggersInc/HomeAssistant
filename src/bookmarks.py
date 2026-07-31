"""
Saved web pages, and the icons that identify them.

Client-owned: the web page and its toolbar are the client's, so a bookmark
saved there survives a plugin being unloaded.

Icons come from the browser engine, which has already downloaded the favicon
to draw its own tab - fetching it again would need the network up at the exact
moment somebody presses the button.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse

if TYPE_CHECKING:
    from src.main import Client

#How many to keep. Past this the grid stops being something to glance at.
LIMIT = 60

#Icons are written here, one per bookmark, named from the address.
ICON_DIRNAME = "bookmark-icons"

#What an icon is saved as. PNG because the engine hands over a QIcon and that
#is what it renders to without a second decision.
ICON_SIZE = 64


@dataclass
class Bookmark:
    url: str
    title: str = ""
    icon: str = ""            #filename inside the icon folder, not a path
    added: float = field(default_factory=time.time)

    @property
    def host(self) -> str:
        try:
            return urlparse(self.url).netloc.replace("www.", "")
        except Exception:
            return ""

    @property
    def label(self) -> str:
        """What to show. A title if there is one, otherwise the address."""
        return (self.title or "").strip() or self.host or self.url

    @property
    def initial(self) -> str:
        """One letter, for when there is no icon."""
        source = self.label.strip()
        return source[0].upper() if source else "?"

    def as_dict(self) -> dict:
        return {"url": self.url, "title": self.title, "icon": self.icon,
                "added": self.added}


def key_for(url: str) -> str:
    """
    A stable id for an address.

    Hashed rather than sanitised: an address can contain anything, and a
    filename derived by replacing the awkward characters collides the moment
    two pages differ only in one of them.
    """
    return hashlib.sha256(str(url or "").encode("utf-8")).hexdigest()[:16]


class BookmarkStore:
    """Bookmarks, on disk, in the user's data directory."""

    def __init__(self, client: "Client"):
        self.client = client
        self._lock = Lock()
        self._items: list = []
        self._loaded = False

    ## -- where things live

    def path(self) -> Path:
        return Path(self.client.DATAPATH) / "bookmarks.json"

    def icon_dir(self) -> Path:
        return Path(self.client.DATAPATH) / ICON_DIRNAME

    def icon_path(self, bookmark: Bookmark) -> Optional[Path]:
        if not bookmark.icon:
            return None
        candidate = self.icon_dir() / bookmark.icon
        try:
            return candidate if candidate.is_file() else None
        except OSError:
            return None

    ## -- reading and writing

    def load(self, force: bool = False) -> list:
        with self._lock:
            if self._loaded and not force:
                return list(self._items)
            self._items = []
            try:
                raw = json.loads(self.path().read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = []
            if isinstance(raw, list):
                for entry in raw:
                    if not isinstance(entry, dict) or not entry.get("url"):
                        continue
                    self._items.append(Bookmark(
                        url=str(entry.get("url")),
                        title=str(entry.get("title") or ""),
                        icon=str(entry.get("icon") or ""),
                        added=float(entry.get("added") or 0)))
            self._loaded = True
            return list(self._items)

    def save(self) -> bool:
        try:
            path = self.path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps([b.as_dict() for b in self._items], indent=1),
                encoding="utf-8")
            return True
        except OSError as e:
            self.client.log("warning", f"[Bookmarks] Could not save: {e}")
            return False

    ## -- the list

    def all(self) -> list:
        """Newest first. The one just saved is the one being looked for."""
        return sorted(self.load(), key=lambda b: b.added, reverse=True)

    def get(self, url: str) -> Optional[Bookmark]:
        wanted = str(url or "").strip()
        for bookmark in self.load():
            if bookmark.url == wanted:
                return bookmark
        return None

    def has(self, url: str) -> bool:
        return self.get(url) is not None

    def add(self, url: str, title: str = "", icon=None) -> Optional[Bookmark]:
        """
        Save one. Returns it, or None when the address is unusable.

        Saving the same address twice updates the title and icon rather than
        making a second entry - pressing the button again on a page whose title
        has since loaded is a correction, not a duplicate.
        """
        url = str(url or "").strip()
        if not url or url.startswith("about:"):
            return None

        self.load()
        with self._lock:
            existing = None
            for bookmark in self._items:
                if bookmark.url == url:
                    existing = bookmark
                    break

            if existing is None:
                existing = Bookmark(url=url, title=str(title or "").strip())
                self._items.append(existing)
            elif title:
                existing.title = str(title).strip()

            # Oldest out. Sorted by when they were added rather than by use:
            # a list this size is browsed, not searched, and "recently added"
            # is the order somebody already has in their head.
            if len(self._items) > LIMIT:
                self._items.sort(key=lambda b: b.added)
                del self._items[:len(self._items) - LIMIT]

        if icon is not None:
            saved = self._write_icon(existing, icon)
            if saved:
                existing.icon = saved

        self.save()
        return existing

    def remove(self, url: str) -> bool:
        self.load()
        with self._lock:
            before = len(self._items)
            gone = [b for b in self._items if b.url == str(url or "").strip()]
            self._items = [b for b in self._items
                           if b.url != str(url or "").strip()]
            changed = len(self._items) != before

        for bookmark in gone:
            path = self.icon_path(bookmark)
            if path is not None:
                try:
                    path.unlink()
                except OSError:
                    pass

        if changed:
            self.save()
        return changed

    def clear(self) -> int:
        self.load()
        count = len(self._items)
        for bookmark in list(self._items):
            self.remove(bookmark.url)
        return count

    ## -- icons

    def _write_icon(self, bookmark: Bookmark, icon) -> str:
        """
        A QIcon from the engine, written as a PNG. Returns the filename.

        Failing here is not failing to bookmark. An address with no picture is
        still an address, and the grid draws a letter instead.
        """
        try:
            if icon is None or icon.isNull():
                return ""
            from PyQt6.QtCore import QSize
            pixmap = icon.pixmap(QSize(ICON_SIZE, ICON_SIZE))
            if pixmap.isNull():
                return ""

            folder = self.icon_dir()
            folder.mkdir(parents=True, exist_ok=True)
            name = f"{key_for(bookmark.url)}.png"
            if not pixmap.save(str(folder / name), "PNG"):
                return ""
            return name
        except Exception as e:
            self.client.log("debug", f"[Bookmarks] No icon saved: {e}")
            return ""
