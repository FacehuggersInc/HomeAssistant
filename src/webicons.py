"""
Icons for pages served to a browser.

A phone has no icon font, and shipping one for twenty glyphs is a megabyte for
nothing - so these are paths on a 24-unit grid, stroked with currentColor.

An unknown name falls back to a dot: a missing picture should not be a broken
tile.
"""

from __future__ import annotations

#Stroke paths, drawn at 24x24. Named after the Material icon they stand in for,
#so a plugin can pass the same name it would use on the panel.
PATHS = {
    "book-open-variant": "M12 6.5C10 5 7.5 4.5 4 5v13c3.5-.5 6 0 8 1.5 "
                         "2-1.5 4.5-2 8-1.5V5c-3.5-.5-6 0-8 1.5zm0 0v13",
    "upload":            "M12 16V4m0 0L7 9m5-5l5 5M4 20h16",
    "arrow-right-bold":  "M4 12h13m0 0l-5-5m5 5l-5 5",
    "clipboard-text":    "M9 4h6v3H9zM7 5H5v15h14V5h-2M8 11h8M8 15h5",
    "timer-sand":        "M7 3h10M7 21h10M8 3v4l4 4 4-4V3M8 21v-4l4-4 4 4v4",
    "sticker-emoji":     "M12 3a9 9 0 100 18 9 9 0 000-18zM9 10h.01M15 10h.01"
                         "M8.5 14.5a4 4 0 007 0",
    "rss":               "M5 19a1 1 0 100-2 1 1 0 000 2zM5 12a7 7 0 017 7"
                         "M5 5a14 14 0 0114 14",
    "calendar":          "M4 6h16v14H4zM4 10h16M8 3v4M16 3v4",
    "music":             "M9 18V6l10-2v12M9 18a2.5 2.5 0 11-5 0 2.5 2.5 0 015 0z"
                         "M19 16a2.5 2.5 0 11-5 0 2.5 2.5 0 015 0z",
    "volume-high":       "M4 9v6h4l5 4V5L8 9H4zm12-1a5 5 0 010 8",
    "cog":               "M12 9a3 3 0 100 6 3 3 0 000-6z"
                         "M12 3l1.5 2.6 3-.4.4 3L19.5 10l-1.3 2.7 1.3 2.7-2.6 1.8"
                         "-.4 3-3-.4L12 21l-1.5-2.2-3 .4-.4-3L4.5 15.4 5.8 12.7"
                         "4.5 10l2.6-1.8.4-3 3 .4z",
    "account-multiple":  "M8 11a3 3 0 100-6 3 3 0 000 6zM2 20a6 6 0 0112 0"
                         "M16 6a3 3 0 010 6M17 20a6 6 0 00-2-4.5",
    # A ring with a break and a head, not a circle that failed to close.
    "restart":           "M20 12a8 8 0 11-2.3-5.7M20 4v4h-4",
    "sync":              "M4 12a8 8 0 0113.7-5.7M20 12a8 8 0 01-13.7 5.7"
                         "M18 3v4h-4M6 21v-4h4",
    "refresh":           "M20 12a8 8 0 11-2.3-5.7M20 4v4h-4",
    "rotate":            "M12 5a7 7 0 107 7M12 5V2M12 5l3.5 3.5",
    "power":             "M12 4v8M7 6a7 7 0 109 0",
    "download":          "M12 4v12m0 0l-5-5m5 5l5-5M4 20h16",
    "check-network":     "M4 6h16v9H4zM9 19h6M12 15v4",
    "bookmark":          "M7 4h10v16l-5-4-5 4z",
    "web":               "M12 3a9 9 0 100 18 9 9 0 000-18zM3 12h18"
                         "M12 3c2.5 3 2.5 15 0 18M12 3c-2.5 3-2.5 15 0 18",
    "tune":              "M4 7h10M18 7h2M4 17h4M12 17h8M14 4v6M8 14v6",
    "image-multiple":    "M8 4h12v10H8zM4 8v12h12M11 11l2-2 3 3",
    "message-text":      "M4 5h16v11H9l-5 4z",
    "bell":              "M12 3a5 5 0 015 5v4l2 3H5l2-3V8a5 5 0 015-5z"
                         "M10 19a2 2 0 004 0",
    "puzzle":            "M10 4h4v2a2 2 0 104 0V4h2v4h-2a2 2 0 100 4h2v8h-6v-2"
                         "a2 2 0 10-4 0v2H4v-6H2a2 2 0 100-4h2V4h6z",
    "plus-box":          "M4 4h16v16H4zM12 8v8M8 12h8",
    "folder-plus":       "M4 6h5l2 2h9v10H4zM12 12v4M10 14h4",
    "playlist-check":    "M4 7h10M4 12h10M4 17h6M15 15l2 2 4-4",
    "play":              "M8 5l11 7-11 7z",
    "stop":              "M6 6h12v12H6z",
    "file-document":     "M7 3h7l4 4v14H7zM14 3v4h4M10 12h6M10 16h6",
    "shield-key":        "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z"
                         "M12 10a2 2 0 100 4 2 2 0 000-4zM12 14v3",
    "alert":             "M12 4l9 16H3zM12 10v4M12 17h.01",
    # A die with a second one behind it. Pips are zero-length segments, the
    # same trick sticker-emoji uses for eyes - a stroked path has no fill to
    # make a dot with.
    "dice-multiple":     "M4 10h10v10H4zM10 4h10v10h-6"
                         "M7 13h.01M11 13h.01M7 17h.01M11 17h.01M9 15h.01",
}
#Everything else.
FALLBACK = "M12 8a4 4 0 100 8 4 4 0 000-8z"


def svg(name: str, size: int = 22) -> str:
    """
    One icon, as an <svg> element.

    Stroked rather than filled so a single path reads at any size and inherits
    the surrounding colour - which means the same markup works on a card, a
    button and a danger button without three copies of it.
    """
    key = str(name or "").strip().lower()
    # `mdi.timer-sand` and `timer-sand` are the same request. The panel writes
    # the prefix and a plugin should not have to remember which side it is on.
    if key.startswith("mdi."):
        key = key[4:]
    path = PATHS.get(key, FALLBACK)
    return (f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" '
            f'fill="none" stroke="currentColor" stroke-width="1.7" '
            f'stroke-linecap="round" stroke-linejoin="round" '
            f'aria-hidden="true"><path d="{path}"/></svg>')


def known() -> list:
    """Every name this set draws. For the docs, and for a checker."""
    return sorted(PATHS)
