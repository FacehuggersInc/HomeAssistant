"""
The form behind /public/note_add.

Some text, a colour, a size, and a button that puts it on the wall. Lists
moved to list_page: they started as the same page with different fields and
grew into an editor with its own state, which is not the same act any more.

The page is three files in `web/` - `note.html`, `note.css` and `note.js` -
read by `WebAssets`. See docs/web-ui.md.
"""

from __future__ import annotations

# Absolute, not relative: this module is loaded by path and a relative import
# needs a package it does not have. See check_siblings.py.
from src.assets.bundled.CoreWidgetsBundle import ASSETS

PATH = "/public/note_add"


def render_page(token: str, colours: list, message: str = "",
                bad: bool = False, quadrant: str = "top-right",
                font_sizes: list = None, font_size: int = 0,
                colour: str = "", notes: list = None, target: str = "",
                **_ignored) -> str:
    """
    One note, and how it should look.

    `notes` is (key, label, text, colour, size) for every note on the panel,
    and `target` is the one the chooser opens on. Same shape as the checklist
    editor, because it is the same act.

    The size options are sent as numbers and each is drawn at the size it
    names. A list reading 14 / 17 / 20 in one size tells you the numbers and
    nothing about what is being chosen, which is the only question here.
    """
    sizes = [int(size) for size in (font_sizes or [])]
    notes = notes or []

    return ASSETS.page(
        title="Sticky note",
        heading="Put a note on the panel",
        blurb="It appears on the home page, where it can be moved and resized.",
        token=token, endpoint=PATH, message=message, bad=bad,
        body_file="note.html", css_file="note.css", script_file="note.js",
        data={
            "colours": list(colours or []),
            "colour": str(colour or ""),
            "fontSizes": sizes,
            "fontSize": int(font_size or 0),
            # What a note starts as, when nothing was chosen. Sent rather
            # than guessed at by the page, so the two cannot drift.
            "defaultFontSize": sizes[len(sizes) // 2] if sizes else 20,
            "quadrant": str(quadrant or "top-right"),
            "target": str(target or ""),
            # In order, for the chooser.
            "notes": [{"key": key, "label": label}
                      for key, label, _text, _tint, _size in notes],
            # And every note's contents, so switching between them is instant
            # rather than a round trip for something already known.
            "known": {key: {"text": text, "colour": tint, "size": size}
                      for key, _label, text, tint, size in notes},
        },
    )
