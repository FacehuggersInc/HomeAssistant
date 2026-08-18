"""
Putting a sticker on the panel from a phone.

Upload, pick one, say where it goes and how long it stays. One page rather
than a folder to drop files into, because the folder was only reachable by
whoever set the panel up.

The page is three files in `web/` - `sticker.html`, `sticker.css` and
`sticker.js` - read by `WebAssets`. See docs/web-ui.md.
"""

from __future__ import annotations

# Absolute, not relative: this module is loaded by path, and a relative import
# needs a package it does not have. See check_siblings.py.
from pathlib import Path

from src.assets.bundled.CoreWidgetsBundle import ASSETS

MODES = [("permanent", "Permanently, until I remove it"),
         ("temporary", "Temporarily")]

#A word rather than a number. "180" meant nothing on its own - it could be
#either the old "small" or half the screen. A word cannot be mistaken for
#either, and a link still passing a number keeps its old meaning exactly.
SCALES = [("small", "Small"), ("normal", "Normal"), ("large", "Large"),
          ("huge", "Huge"), ("enormous", "Enormous"),
          ("custom", "Exact size\u2026")]

PATH = "/public/sticker_add"


def _modified(entry) -> int:
    """When a sticker's file was last written, or 0."""
    try:
        return int(Path(entry.path).stat().st_mtime)
    except Exception:
        return 0


def _size(entry) -> int:
    try:
        return int(Path(entry.path).stat().st_size)
    except Exception:
        return 0

TRUTHY = ("1", "true", "on", "yes")


def render_page(token: str, stickers: list, message: str = "",
                bad: bool = False, form: dict = None) -> str:
    """
    The page, rendered from whatever was last submitted.

    `form` carries the previous answers back in. Re-rendering from defaults
    meant every placement reset the position, the size and the duration - so
    putting three stickers in the same corner meant setting the same three
    controls three times.

    The library is sent as data and the tiles are built by the script, so a
    sticker's own name is text on the page rather than markup in it.
    """
    from src.ui.widget import normalise_position

    form = form or {}
    chosen = str(form.get("sticker") or "")

    # A sticker that has since been deleted must not stay selected.
    if chosen and not any(entry.name == chosen for entry in stickers):
        chosen = ""

    return ASSETS.page(
        title="Stickers",
        heading="Stickers",
        blurb="Put something on the panel.",
        token=token, endpoint=PATH, message=message, bad=bad,
        body_file="sticker.html", css_file="sticker.css",
        script_file="sticker.js",
        # Search, sort and the draw cap, shared with the upload page so the
        # two cannot drift.
        also=("listing.js",),
        data={
            "stickers": [{
                "name": entry.name,
                "label": entry.label,
                "kind": entry.kind,
                "src": f"/asset/stickers/{entry.name}?token={token}",
                # For sorting. Read here rather than in the page, which has
                # no way to ask the disk anything.
                "modified": _modified(entry),
                "size_bytes": _size(entry),
            } for entry in stickers or []],
            "modes": [list(entry) for entry in MODES],
            "scales": [list(entry) for entry in SCALES],
            "quadrant": normalise_position(form.get("quadrant"), "center"),
            "chosenLabel": next((entry.label for entry in stickers
                                 if entry.name == chosen), ""),
            "form": {
                "sticker": chosen,
                "mode": str(form.get("mode") or "permanent"),
                "scale": str(form.get("scale") or "normal"),
                "timeout": str(form.get("timeout") or "300"),
                "size": str(form.get("size") or "180"),
                "delete_after": (str(form.get("delete_after") or "").lower()
                                 in TRUTHY),
            },
        },
    )
