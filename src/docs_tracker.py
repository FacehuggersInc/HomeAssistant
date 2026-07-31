"""
Which documentation pages are new or have changed, and when.

Kept in `docs/CHANGES.json` rather than a dotfile, so it travels with the docs
it describes and is visible to anybody looking at the folder. A dotfile beside
thirty markdown files is a file nobody knows exists.

The problem it solves: a handful of pages can change across a batch of work
that never gets run, and by the time the panel is next opened there is nothing
to say which. Badges answer that, and the log behind them answers it again
later when the badges have gone.

**A badge is a reading aid, not a record.** It disappears once the page has
been opened and a day has passed, so it stops nagging - but the entry stays in
the log for good, because "what changed last week" is a question that outlives
the badge.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Optional

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
TRACKER = DOCS_DIR / "CHANGES.json"

#How long after a page is opened its badge survives. Not zero: opening a page
#and coming straight back should not erase the mark that brought you to it.
BADGE_GRACE = 24 * 60 * 60
#How long an unopened page keeps its badge. Long enough to survive a week of
#not touching the panel.
BADGE_MAX_AGE = 30 * 24 * 60 * 60
#Entries kept in the log. Old enough to cover a few months of work.
LOG_LIMIT = 400


def _now() -> float:
    return time.time()


def _empty() -> dict:
    return {"pages": {}, "log": []}


def load() -> dict:
    try:
        data = json.loads(TRACKER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    data.setdefault("pages", {})
    data.setdefault("log", [])
    return data


def save(data: dict) -> bool:
    try:
        TRACKER.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n",
                           encoding="utf-8")
        return True
    except OSError:
        return False


def _digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def scan(note: str = "") -> dict:
    """
    Compare every page against what was recorded, and record the differences.

    Content is hashed rather than trusted to a modification time: copying the
    tree, checking it out again, or touching a file all move the timestamp
    without changing a word, and a badge on a page that reads identically is a
    badge that teaches people to ignore badges.
    """
    data = load()
    pages = data["pages"]
    seen, changed = set(), []

    for path in sorted(DOCS_DIR.glob("*.md")):
        slug = path.stem
        seen.add(slug)
        digest = _digest(path)
        if not digest:
            continue
        entry = pages.get(slug)
        if entry is None:
            pages[slug] = {"hash": digest, "state": "new",
                           "at": _now(), "opened": 0}
            changed.append((slug, "new"))
        elif entry.get("hash") != digest:
            entry.update({"hash": digest, "state": "updated",
                          "at": _now(), "opened": 0})
            changed.append((slug, "updated"))

    # A page that has gone is removed from the badges but kept in the log: the
    # fact it existed and stopped existing is itself worth reading.
    for slug in [s for s in pages if s not in seen]:
        pages.pop(slug, None)
        changed.append((slug, "removed"))

    if changed:
        data["log"].insert(0, {
            "at": _now(),
            "note": note,
            "pages": [{"slug": s, "state": k} for s, k in changed],
        })
        del data["log"][LOG_LIMIT:]
        save(data)
    return data


def baseline(note: str = "", pages_noted: list = None,
             keep_log: bool = True) -> dict:
    """
    Record every page as read, so the next change is the one that stands out.

    For the point where somebody has been through the whole of the docs: a
    badge on thirty pages at once carries no information, and a reader who
    dismisses thirty learns to dismiss the next one too.

    `keep_log` decides what happens to the history behind the badges. Keeping
    it is the default, since "what changed, and when" is a question that
    outlives any badge. Clearing it starts the record here, which is what a
    tree that has just been read end to end wants - one entry saying so,
    rather than a log of work whose badges have all just been cleared.

    `pages_noted` is `[(slug, state)]` for the log entry alone. Badges come
    from the page table, never from the log, so naming pages here says what
    the baseline covers without marking any of them.

        from src import docs_tracker as t
        t.baseline("read through", keep_log=False)
    """
    data = load()
    pages = {}
    for path in sorted(DOCS_DIR.glob("*.md")):
        digest = _digest(path)
        if not digest:
            continue
        # `state` is neither new nor updated, so badge_state() answers with
        # nothing. `opened` is set as well, so a page whose state is ever
        # revived still expires on the ordinary schedule.
        pages[path.stem] = {"hash": digest, "state": "seen",
                            "at": _now(), "opened": _now()}
    data["pages"] = pages

    if not keep_log:
        data["log"] = []

    if note:
        data["log"].insert(0, {
            "at": _now(),
            "note": note,
            "pages": [{"slug": s, "state": k} for s, k in (pages_noted or [])],
        })
        del data["log"][LOG_LIMIT:]
    save(data)
    return data


def mark_opened(slug: str) -> None:
    """Note that a page has been read. The badge starts expiring from here."""
    data = load()
    entry = data["pages"].get(slug)
    if entry is None or entry.get("opened"):
        return
    entry["opened"] = _now()
    save(data)


def badge_state(slug: str, data: dict = None) -> str:
    """"new", "updated" or "" - whether this page still deserves a mark."""
    data = data if data is not None else load()
    entry = data["pages"].get(slug)
    if not entry:
        return ""
    state = entry.get("state") or ""
    if state not in ("new", "updated"):
        return ""

    age = _now() - float(entry.get("at") or 0)
    if age > BADGE_MAX_AGE:
        return ""
    opened = float(entry.get("opened") or 0)
    if opened and (_now() - opened) > BADGE_GRACE:
        return ""
    return state


def badge_for(slug: str, data: dict = None) -> str:
    state = badge_state(slug, data)
    if not state:
        return ""
    return f'<span class="doc-badge {state}">{state}</span>'


def recent_count(data: dict = None) -> int:
    data = data if data is not None else load()
    return sum(1 for slug in data["pages"] if badge_state(slug, data))


def log_entries(data: dict = None) -> list:
    data = data if data is not None else load()
    return list(data.get("log") or [])
