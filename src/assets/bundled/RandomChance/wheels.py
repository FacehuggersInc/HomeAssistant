"""
Wheels: what is on them, what each one's share is, and where they live.

No Qt and no drawing. This is the part that can be reasoned about on its own -
a wheel is a name and a list of items, each of which is enabled or not and
holds a share of the wheel.

## Shares are percentages

A share is a percentage of the wheel, and the percentages of the enabled
items always come to 100. That is a choice with a consequence: setting one
item to 40 has to move the others, because there is only ever 100 to go
round. Weights would not have that behaviour, and would also mean nobody
could say "this one, four times in ten" without doing arithmetic first.

The redistribution itself happens where the editing does - in the page - and
this module only ever normalises what it is given. Two reasons: an edit needs
to answer immediately under a finger, and a wheel arriving from anywhere at
all still has to be made to add up before it can be spun.

## Where they live

The user data directory, not the plugin's settings.json. That file ships with
the app and is replaced by an update, which would take somebody's wheels with
it - the same reason widget layout is user data.
"""

from __future__ import annotations

import json
import random
import threading
import time
import zlib
from pathlib import Path
from typing import Callable, Optional

_rng = random.SystemRandom()

MAX_WHEELS = 40
MAX_ITEMS = 40
MAX_LABEL = 60
MAX_NAME = 60
#the smallest share an item can be given and still be on the wheel at all
MIN_SHARE = 0


def colour_for(label: str) -> str:
    """
    A slice colour, worked out from the label rather than stored.

    Deterministic, so a wheel looks like itself every time it is opened, and
    derived from the item rather than its position, so deleting one item does
    not reshuffle the colour of every item after it.

    `crc32` rather than `hash`: Python randomises string hashing per process,
    so a stored-nowhere colour built on `hash` would change on every restart -
    which is the one thing this is meant to prevent.
    """
    hue = zlib.crc32(str(label).strip().lower().encode("utf-8")) % 360
    return f"hsl({hue}, 62%, 56%)"


def _fingerprint(label: str) -> int:
    return zlib.crc32(str(label).strip().lower().encode("utf-8"))


def hue_for(label: str) -> int:
    """The same colour as a hue, for anything painting rather than styling."""
    return _fingerprint(label) % 360


def tone_for(label: str) -> int:
    """
    A lightness step, 0 to 4, decided separately from the hue.

    Two labels landing on neighbouring hues is common enough to see - six
    items and two of them come out as the same blue. Taking the tone from a
    different part of the same fingerprint means two slices close on the
    colour wheel are still told apart, where deriving it FROM the hue would
    give them the same lightness as well.
    """
    return (_fingerprint(label) // 360) % 5


def new_item(label: str, share: float = 0.0, enabled: bool = True) -> dict:
    return {
        "id": f"i{int(time.time() * 1000)}{_rng.randint(100, 999)}",
        "label": str(label or "").strip()[:MAX_LABEL],
        "enabled": bool(enabled),
        "share": float(share),
    }


def new_wheel(name: str = "") -> dict:
    return {
        "id": f"w{int(time.time() * 1000)}{_rng.randint(100, 999)}",
        "name": str(name or "").strip()[:MAX_NAME] or "New wheel",
        "items": [],
    }


def enabled_items(items: list) -> list:
    return [item for item in items or [] if item.get("enabled", True)]


def normalise(items: list) -> list:
    """
    The enabled items, with integer percentages that come to exactly 100.

    Whole numbers, and the remainder handed out largest-first. Percentages
    that come to 99 or 101 are the sort of thing nobody notices until they
    are staring at a wheel wondering why the arrow is off - and a spin
    weighted by numbers that do not add up is quietly not the wheel that was
    shown.

    An empty wheel gives an empty list. A wheel whose enabled items are all
    at zero is read as equal shares, since it is what somebody means by
    turning everything down rather than a wheel that cannot be spun.
    """
    live = [dict(item) for item in enabled_items(items)]
    if not live:
        return []

    total = sum(max(0.0, float(item.get("share", 0.0))) for item in live)
    if total <= 0:
        share = 100.0 / len(live)
        for item in live:
            item["share"] = share
        total = 100.0

    exact = [max(0.0, float(item["share"])) * 100.0 / total for item in live]
    whole = [int(value) for value in exact]
    left = 100 - sum(whole)

    order = sorted(range(len(live)), key=lambda i: exact[i] - whole[i],
                   reverse=True)
    for index in order[:max(0, left)]:
        whole[index] += 1

    for item, percent in zip(live, whole):
        item["share"] = percent
    return live


def spread_evenly(items: list) -> list:
    """Every enabled item on the same share. The reset button's whole job."""
    out = [dict(item) for item in items or []]
    live = [item for item in out if item.get("enabled", True)]
    if live:
        share = 100.0 / len(live)
        for item in live:
            item["share"] = share
    return out


def pick(items: list) -> Optional[dict]:
    """
    One item, chosen by its share. The only place a winner is decided.

    Over the normalised percentages rather than the raw shares, so what is
    drawn on the wheel and what can actually win are the same numbers. An
    item on 0% is on the wheel's list and not on the wheel - it cannot be
    landed on, which is what setting it to zero means.
    """
    live = normalise(items)
    live = [item for item in live if item["share"] > 0]
    if not live:
        return None
    roll = _rng.uniform(0.0, sum(item["share"] for item in live))
    running = 0.0
    for item in live:
        running += item["share"]
        if roll <= running:
            return item
    return live[-1]


def clean_wheel(raw) -> Optional[dict]:
    """
    A wheel from anywhere, made safe to keep.

    This arrives from a page anybody on the network can post to, so a
    malformed item costs that item rather than the wheel, and a wheel with no
    usable name or id is refused outright rather than saved as a nameless
    thing nobody can find again.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "null")
        except ValueError:
            return None
    if not isinstance(raw, dict):
        return None

    wheel_id = str(raw.get("id", "")).strip()[:60]
    name = str(raw.get("name", "")).strip()[:MAX_NAME]
    if not wheel_id:
        return None

    items = []
    for entry in list(raw.get("items") or [])[:MAX_ITEMS]:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label", "")).strip()[:MAX_LABEL]
        if not label:
            continue
        try:
            share = max(0.0, float(entry.get("share", 0.0)))
        except (TypeError, ValueError):
            share = 0.0
        items.append({
            "id": str(entry.get("id", "")).strip()[:60] or new_item(label)["id"],
            "label": label,
            "enabled": bool(entry.get("enabled", True)),
            "share": share,
        })

    return {"id": wheel_id, "name": name or "New wheel", "items": items}


class WheelStore:
    """
    Wheels on disk, under one lock.

    Everything here is locked for the same reason `CalendarStore` is: a wheel
    is saved from a Flask worker while the stage reads one on the UI thread,
    and the two meeting in the middle gives a read that sees half of somebody
    else's change. Reentrant, because these methods call each other - `put()`
    ends in `save()`.
    """

    def __init__(self, path: Path, log: Callable = None):
        self.path = Path(path)
        self._log = log or (lambda *a, **k: None)
        self._lock = threading.RLock()
        self._wheels: dict = {}
        self.load()

    # ── Disk ─────────────────────────────────────────────────────────────────

    def load(self) -> None:
        with self._lock:
            self._wheels = {}
            if not self.path.is_file():
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                self._log("warning",
                          f"[RandomChance] Could not read {self.path}: {e}. "
                          f"Starting with no wheels rather than replacing the "
                          f"file - it is somebody's list and may be "
                          f"recoverable by hand.")
                return
            for entry in list(raw.get("wheels") or [])[:MAX_WHEELS]:
                wheel = clean_wheel(entry)
                if wheel:
                    self._wheels[wheel["id"]] = wheel

    def save(self) -> None:
        with self._lock:
            payload = {"wheels": list(self._wheels.values())}
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                # Written beside and moved into place, so a crash mid-write
                # leaves the previous file rather than half of this one.
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(json.dumps(payload, indent=2),
                                     encoding="utf-8")
                temporary.replace(self.path)
            except OSError as e:
                self._log("error",
                          f"[RandomChance] Could not save wheels to "
                          f"{self.path}: {e}")

    # ── Reading ──────────────────────────────────────────────────────────────

    def all(self) -> list:
        """
        Every wheel, as copies.

        Copies because the caller is usually about to render them on another
        thread, and handing out the live objects is how a list gets iterated
        while it is being replaced.
        """
        with self._lock:
            return [json.loads(json.dumps(wheel))
                    for wheel in self._wheels.values()]

    def get(self, wheel_id: str) -> Optional[dict]:
        with self._lock:
            found = self._wheels.get(str(wheel_id))
            return json.loads(json.dumps(found)) if found else None

    def count(self) -> int:
        with self._lock:
            return len(self._wheels)

    # ── Writing ──────────────────────────────────────────────────────────────

    def put(self, raw) -> Optional[dict]:
        """Add or replace one wheel. Returns what was kept."""
        wheel = clean_wheel(raw)
        if wheel is None:
            return None
        with self._lock:
            if (wheel["id"] not in self._wheels
                    and len(self._wheels) >= MAX_WHEELS):
                self._log("warning",
                          f"[RandomChance] Refusing a {MAX_WHEELS + 1}th "
                          f"wheel - delete one first.")
                return None
            self._wheels[wheel["id"]] = wheel
            self.save()
            return json.loads(json.dumps(wheel))

    def drop(self, wheel_id: str) -> bool:
        with self._lock:
            if str(wheel_id) not in self._wheels:
                return False
            del self._wheels[str(wheel_id)]
            self.save()
            return True
