from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Optional

# What the panel can actually show, split by how it has to be drawn.
#
# STILL   - one frame, drawn as a pixmap.
# ANIMATED- many frames, driven by QMovie. GIF is the only animated format Qt
#           is guaranteed to have a plugin for; WebP animation depends on the
#           qwebp plugin in the build, so it is probed at runtime rather than
#           assumed (see StickerStore.animated_formats).
# VIDEO   - not an image format at all. Accepted and stored so the library is
#           not lossy, but it needs QtMultimedia to play and is shown as a
#           placeholder until that exists.
STILL_SUFFIXES    = {".png", ".jpg", ".jpeg", ".bmp"}
ANIMATED_SUFFIXES = {".gif", ".webp"}
VIDEO_SUFFIXES    = {".mp4", ".webm", ".mov", ".m4v"}

ALL_SUFFIXES = STILL_SUFFIXES | ANIMATED_SUFFIXES | VIDEO_SUFFIXES

#a sticker is a decoration, not a media library
MAX_BYTES = 12 * 1024 * 1024


def kind_of(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in ANIMATED_SUFFIXES:
        return "animated"
    if suffix in STILL_SUFFIXES:
        return "still"
    return ""


def safe_name(name: str) -> str:
    """
    A filename that cannot escape the sticker folder or collide with the OS.

    Uploads arrive from the network, so the name is untrusted: a path
    separator or a leading dot in one would write outside the directory this
    is supposed to own.
    """
    name = (name or "").strip().replace("\\", "/").split("/")[-1]
    stem = Path(name).stem
    suffix = Path(name).suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9 _\-\.]+", "", stem).strip(" .-_")
    stem = re.sub(r"\s+", " ", stem)[:64]
    if not stem:
        stem = f"sticker-{int(time.time())}"
    return f"{stem}{suffix}"


def label_of(path: Path) -> str:
    """'happy-cat_01.gif' -> 'Happy Cat 01'. What a person would search for."""
    words = re.split(r"[-_\s]+", path.stem.strip())
    words = [w for w in words if w]
    return " ".join(w[:1].upper() + w[1:] for w in words) or path.stem


class Sticker:
    """One file in the sticker folder."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.name = self.path.name
        self.label = label_of(self.path)
        self.kind = kind_of(self.path)

    @property
    def key(self) -> str:
        return self.name

    def exists(self) -> bool:
        try:
            return self.path.is_file()
        except OSError:
            return False

    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def modified(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0

    def as_dict(self) -> dict:
        return {
            "key":   self.key,
            "name":  self.name,
            "label": self.label,
            "kind":  self.kind,
            "size":  self.size(),
        }

    def __repr__(self) -> str:
        return f"<Sticker {self.name!r} {self.kind}>"


class StickerStore:
    """
    The sticker folder, listed and searched.

    No Qt import, so the scanning, filtering and upload rules are testable
    without a display - which is most of the behaviour worth testing.

    Lives in the user data directory rather than the install tree: anything
    written inside the install is wiped when an update is unpacked over it.
    """

    def __init__(self, directory: Path, log=None):
        self.directory = Path(directory)
        self.log = log or (lambda *a, **k: None)
        self._cache: Optional[list] = None
        self._stamp = None
        self.ensure()

    def ensure(self) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.log("warning", f"[Stickers] Could not create {self.directory}: {e}")

    ## -- listing

    def _folder_stamp(self):
        try:
            return self.directory.stat().st_mtime
        except OSError:
            return None

    def all_stickers(self, refresh: bool = False) -> list:
        """
        Everything in the folder, cached against its mtime.

        The grid asks for this every time it opens and on every keystroke of
        a search; a directory walk per keystroke is the kind of thing that
        makes a touch keyboard feel broken.
        """
        stamp = self._folder_stamp()
        if not refresh and self._cache is not None and stamp == self._stamp:
            return self._cache

        found = []
        try:
            for entry in sorted(self.directory.iterdir()):
                if not entry.is_file():
                    continue
                if entry.suffix.lower() not in ALL_SUFFIXES:
                    continue
                found.append(Sticker(entry))
        except OSError as e:
            self.log("warning", f"[Stickers] Could not read {self.directory}: {e}")

        self._cache = found
        self._stamp = stamp
        return found

    def get(self, key: str) -> Optional[Sticker]:
        """One sticker by filename. Never resolves outside the folder."""
        name = safe_name(key)
        candidate = self.directory / name
        try:
            if candidate.is_file() and candidate.parent == self.directory:
                return Sticker(candidate)
        except OSError:
            pass
        return None

    def search(self, text: str = "", kinds: tuple = ()) -> list:
        """
        Match on the label, the filename and the kind.

        Every word has to appear somewhere, in any order, so "cat happy"
        finds "happy-cat.gif" - matching the whole string against the
        filename only finds what somebody named exactly right.
        """
        items = self.all_stickers()
        if kinds:
            items = [s for s in items if s.kind in kinds]

        words = [w for w in re.split(r"\s+", (text or "").strip().lower()) if w]
        if not words:
            return items

        def matches(sticker: Sticker) -> bool:
            hay = f"{sticker.label} {sticker.name} {sticker.kind}".lower()
            return all(word in hay for word in words)

        return [s for s in items if matches(s)]

    ## -- writing

    def accepts(self, filename: str, size: int = 0) -> tuple:
        """(ok, reason) for an upload, before anything is written."""
        suffix = Path(filename or "").suffix.lower()
        if not suffix:
            return False, "That file has no extension, so its type is unknown."
        if suffix not in ALL_SUFFIXES:
            allowed = ", ".join(sorted(s.lstrip(".") for s in ALL_SUFFIXES))
            return False, f"'{suffix}' is not a sticker. Allowed: {allowed}."
        if size and size > MAX_BYTES:
            return False, (f"That is {size / 1024 / 1024:.1f}MB. "
                           f"The limit is {MAX_BYTES // 1024 // 1024}MB.")
        return True, ""

    def unique_path(self, filename: str) -> Path:
        """
        A path that does not overwrite an existing sticker.

        Uploading a second "cat.gif" should not silently replace the first -
        somebody has that one on their home screen.
        """
        name = safe_name(filename)
        candidate = self.directory / name
        if not candidate.exists():
            return candidate
        stem, suffix = Path(name).stem, Path(name).suffix
        index = 2
        while True:
            candidate = self.directory / f"{stem}-{index}{suffix}"
            if not candidate.exists():
                return candidate
            index += 1

    def add_bytes(self, filename: str, data: bytes) -> tuple:
        """Write an upload. Returns (Sticker, "") or (None, reason)."""
        ok, reason = self.accepts(filename, len(data or b""))
        if not ok:
            return None, reason
        path = self.unique_path(filename)
        try:
            path.write_bytes(data)
        except OSError as e:
            return None, f"Could not save it: {e}"
        self._cache = None
        return Sticker(path), ""

    def add_file(self, source: Path) -> tuple:
        source = Path(source)
        try:
            size = source.stat().st_size
        except OSError as e:
            return None, f"Could not read it: {e}"
        ok, reason = self.accepts(source.name, size)
        if not ok:
            return None, reason
        path = self.unique_path(source.name)
        try:
            shutil.copy2(source, path)
        except OSError as e:
            return None, f"Could not copy it: {e}"
        self._cache = None
        return Sticker(path), ""

    def remove(self, key: str) -> bool:
        sticker = self.get(key)
        if sticker is None:
            return False
        try:
            sticker.path.unlink()
        except OSError as e:
            self.log("warning", f"[Stickers] Could not remove {key}: {e}")
            return False
        self._cache = None
        return True
