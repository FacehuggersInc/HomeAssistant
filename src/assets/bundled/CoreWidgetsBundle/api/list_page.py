"""
The form behind /public/list_add.

One list at a time. The chooser at the top decides WHICH list; everything
below it is that list, and submitting writes it back. A list that is not on
the panel yet is the same page with nothing chosen - it becomes an edit of a
real widget the moment it is put up.

The page itself is three files in `web/` - `list.html`, `list.css` and
`list.js` - read by `WebAssets`. See docs/web-ui.md. Nothing here formats or
substitutes into them: what the panel has to say goes into one JSON object
and the script reads it.
"""

from __future__ import annotations

# Absolute, not relative. This module is loaded through `sibling()`, which
# gives it a name whose package has never existed - `from .. import ASSETS`
# resolves fine as an ordinary import and fails on the panel. See
# check_siblings.py.
from src.assets.bundled.CoreWidgetsBundle import ASSETS

NEW = ""        # the chooser's value for "not a list yet"

PATH = "/public/list_add"


def render_page(token: str, colours: list, message: str = "",
                bad: bool = False, lists: list = None, target: str = "",
                quadrant: str = "top-right") -> str:
    """
    The list editor.

    `lists` is (key, title, text) for every checklist on the panel, `text`
    written in the same [x] form the widget parses. `target` is the one the
    chooser opens on - the key just created, when something was just put up.
    """
    lists = lists or []

    return ASSETS.page(
        title="Checklist",
        heading="Checklist",
        blurb="Pick a list to edit, or make a new one.",
        token=token, endpoint=PATH, message=message, bad=bad,
        body_file="list.html", css_file="list.css", script_file="list.js",
        data={
            "target": target,
            "quadrant": quadrant,
            "colours": list(colours or []),
            # In order, for the chooser.
            "lists": [{"key": key, "title": title}
                      for key, title, _text in lists],
            # And every list's contents, so switching between them is instant
            # rather than a round trip for something already known.
            "known": {key: {"title": title, "text": text}
                      for key, title, text in lists},
        },
    )
