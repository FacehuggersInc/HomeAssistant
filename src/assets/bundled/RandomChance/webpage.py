"""
The Random Chance page: three files on disk, and the data they need.

The markup, the styling and the script live in `web/` as `page.html`,
`page.css` and `page.js`. This module reads them and hands them to the
chrome. It does not format them, substitute into them, or escape anything in
them.

## Why they are files

Every bug this page has had was an escaping layer. A quote inside an HTML
attribute inside a JavaScript string inside a Python string passes through
two rounds of escape processing and one of them eats the backslash - which
shipped a page that rendered as a row of tabs and nothing else, because the
script died before it could reveal a single pane. `str.format()` would be
worse still: CSS and JavaScript are full of braces and it would try to
substitute every one of them.

So there is no templating. Everything the panel has to say arrives as one
JSON object in `window.RC`, written into the document by the endpoint, and
the script reads it. The three files are served exactly as they were written,
which also means an editor highlights them, `node --check` runs on `page.js`
directly, and a formatter can be pointed at any of them.

## Inline or served

Small assets are written into the page and large ones are served from their
own endpoint, which is what `INLINE_LIMIT` decides. A couple of kilobytes of
CSS costs less inline than a second request does; sixteen kilobytes of script
is worth fetching once and caching.

A served asset carries a hash of its own contents in the URL. Without it an
update leaves every phone holding the previous script against the new panel,
which is the sort of failure that looks like the panel rather than the cache.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.webui import page

FOLDER = Path(__file__).with_name("web")

#the files this page cannot be drawn without
REQUIRED = ("page.html", "page.css", "page.js")

#bigger than this and an asset is served rather than written into the page
INLINE_LIMIT = 4096

TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8",
}


def missing() -> list:
    """
    Which required files are not here.

    Checked when the plugin loads rather than when somebody opens the page,
    so a file left out of a build is an error naming the path instead of a
    500 the first time it is wanted. `verify_siblings()` does the same for
    Python modules.
    """
    return [name for name in REQUIRED if not (FOLDER / name).is_file()]


def read(name: str) -> str:
    """One asset, by name. Refuses anything not on the list."""
    if name not in REQUIRED:
        return ""
    try:
        return (FOLDER / name).read_text(encoding="utf-8")
    except OSError:
        return ""


def fingerprint(name: str) -> str:
    """Eight characters of the file's own hash, for the URL."""
    return hashlib.sha256(read(name).encode("utf-8")).hexdigest()[:8]


def content_type(name: str) -> str:
    return TYPES.get(Path(name).suffix, "text/plain; charset=utf-8")


def render(endpoint: str, asset_endpoint: str, token: str,
           data: dict = None, message: str = "", bad: bool = False) -> str:
    """
    The page.

    `data` is written into the document as `window.RC` and is the only thing
    that crosses from the panel into the script.
    """
    gone = missing()
    if gone:
        return page(
            title="Random Chance", heading="Random Chance",
            body="<section class='card'><p class='empty'>This page is "
                 "missing " + ", ".join(gone) + ".</p></section>",
            token=token, message="The page's files are not installed.",
            bad=True)

    payload = dict(data or {})
    payload["token"] = token
    payload["endpoint"] = endpoint

    # json.dumps, not a hand-built string. `</script>` inside a value would
    # otherwise end the block early, and the separators are escaped for the
    # same reason.
    blob = json.dumps(payload).replace("<", "\\u003c").replace(">", "\\u003e")
    head = f"window.RC = {blob};"

    css = read("page.css")
    js = read("page.js")

    if len(js) <= INLINE_LIMIT:
        script = head + "\n" + js
        extra = ""
    else:
        script = head
        extra = (f'<script src="{asset_endpoint}?name=page.js'
                 f'&v={fingerprint("page.js")}" defer></script>')

    if len(css) <= INLINE_LIMIT:
        sheet = css
    else:
        sheet = ""
        extra = (f'<link rel="stylesheet" href="{asset_endpoint}'
                 f'?name=page.css&v={fingerprint("page.css")}">') + extra

    return page(
        title="Random Chance",
        heading="Random Chance",
        blurb="Flip a coin, roll dice, or spin a wheel on the panel.",
        token=token, message=message, bad=bad,
        css=sheet,
        script=script,
        body=read("page.html") + extra,
    )
