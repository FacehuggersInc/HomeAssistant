"""
What has been played.

Storage only, and deliberately free of any Qt import: a plugin's data should be
readable and testable without a display, and the panel that draws it is a
separate concern that happens to read this.
"""

from __future__ import annotations

import json
from pathlib import Path


class History:
    """The list, on disk."""

    #enough to be useful, few enough to scroll
    LIMIT = 40

    def __init__(self, path: Path, log=None):
        self.path = Path(path)
        self.log = log or (lambda *a, **k: None)
        self.items: list = []
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.items = []
            return
        if not isinstance(raw, list):
            self.items = []
            return
        # Rebuilt rather than trusted - a file somebody may have edited.
        self.items = [
            {
                "video_id": str(e.get("video_id") or ""),
                "title":    str(e.get("title") or ""),
                "artist":   str(e.get("artist") or ""),
                "art_url":  str(e.get("art_url") or ""),
                "asked":    str(e.get("asked") or ""),
            }
            for e in raw
            if isinstance(e, dict) and e.get("video_id")
        ][:self.LIMIT]

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.items, indent=4,
                                            ensure_ascii=False),
                                 encoding="utf-8")
        except OSError as e:
            self.log("warning", f"[Music] Could not save history: {e}")

    def remember(self, video_id: str, title: str = "", artist: str = "",
                 art_url: str = "", asked: str = "") -> None:
        video_id = str(video_id or "")
        if not video_id:
            return

        # Moved to the front rather than appended. The same song twice is one
        # entry, or a history of one album is forty rows of the same names.
        self.items = [e for e in self.items if e["video_id"] != video_id]
        self.items.insert(0, {
            "video_id": video_id,
            "title": title or video_id,
            "artist": artist,
            "art_url": art_url,
            "asked": asked,
        })
        del self.items[self.LIMIT:]
        self.save()

    def clear(self) -> None:
        self.items = []
        self.save()
