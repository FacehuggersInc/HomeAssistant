"""
Shared bits for the pages endpoints serve to a phone.

Each GUI endpoint writes its own HTML - they are forms with a handful of
fields, and a template engine for that is a dependency to maintain for pages
most people open twice. But the chrome around them kept drifting: one page
styled its `select`, another did not; one had a back link, another did not.
This is the part worth sharing.
"""

from __future__ import annotations

import html


def escape(text) -> str:
    return html.escape(str(text or ""), quote=True)


#The palette every page uses. Kept here so a new one starts consistent.
#The panel's own typeface, served to the browser.
#
#Poppins ships in src/assets/fonts and the panel draws with it. Every web page
#fell back to whatever the device had, which is why they never felt like part
#of the same product - a phone showed San Francisco, a desktop showed Segoe.
FONTS = """
@font-face{font-family:Poppins;font-weight:400;font-style:normal;
  font-display:swap;src:url(/font/Poppins-Regular.ttf) format("truetype")}
@font-face{font-family:Poppins;font-weight:500;font-style:normal;
  font-display:swap;src:url(/font/Poppins-Medium.ttf) format("truetype")}
@font-face{font-family:Poppins;font-weight:600;font-style:normal;
  font-display:swap;src:url(/font/Poppins-SemiBold.ttf) format("truetype")}
@font-face{font-family:Poppins;font-weight:700;font-style:normal;
  font-display:swap;src:url(/font/Poppins-Bold.ttf) format("truetype")}
@font-face{font-family:Poppins;font-weight:300;font-style:normal;
  font-display:swap;src:url(/font/Poppins-Light.ttf) format("truetype")}
"""

PALETTE = """ :root{--bg:#0e0e11;--card:#17171c;--card2:#1e1e25;
       --line:#2a2a33;--text:#f0f0f4;--muted:#8f8f9c;
       --accent:#2ff08e;--accent2:#5ac8fa;--warm:#ffb454;--bad:#ff7a7a;
       --glow:rgba(47,240,142,.16);
       /* Not optional, and here rather than on each page.
          Chromium runs with forceDarkModeEnabled so that ordinary sites come
          out dark. A page that declares itself dark is skipped; one that does
          not is inverted into a white rectangle. Six of the served pages had
          left it out, and it is the kind of line that goes missing whenever a
          page is written by copying another. */
       color-scheme:dark}
 *{font-family:Poppins,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
 body{-webkit-font-smoothing:antialiased;letter-spacing:-.011em}"""


# Fields. `select` is included deliberately: styling `input` alone left every
# dropdown as the browser's own white control on an otherwise dark page.
FIELD_CSS = """ input,textarea,select{width:100%;padding:13px;border-radius:9px;
      font-size:16px;background:#111114;color:var(--text);
      border:1px solid var(--line);appearance:none;-webkit-appearance:none}
 select{background-image:linear-gradient(45deg,transparent 50%,var(--muted) 50%),
      linear-gradient(135deg,var(--muted) 50%,transparent 50%);
      background-position:calc(100% - 20px) 22px,calc(100% - 14px) 22px;
      background-size:6px 6px,6px 6px;background-repeat:no-repeat;
      padding-right:42px}
 input:focus,textarea:focus,select:focus{outline:none;border-color:var(--accent)}
 option{background:#111114;color:var(--text)}"""


# Buttons and cards, for every page rather than for the ones written since.
#
# The dashboard grew a gradient primary, lit borders and rounded cards, and the
# pages written before it kept flat grey ones - so the same panel looked like
# two products depending on which button somebody pressed. Styling the plain
# elements here means a page gets it by using them, with no page to edit.
# A labelled button is a FLEX row, not a line of text with an svg dropped into
# it. An inline <svg> sits on the text baseline, which puts a 16px icon a few
# pixels low beside a 15px label - on every button in the app that has one,
# which is most of them now.
CONTROL_CSS = """ button,.btn{min-height:50px;padding:0 22px;border-radius:11px;
      font-family:inherit;font-size:15px;font-weight:600;cursor:pointer;
      border:1px solid var(--line);background:var(--card);color:var(--text);
      display:inline-flex;align-items:center;justify-content:center;gap:9px}
 button svg,.btn svg{flex:none}
 button:hover,.btn:hover{border-color:var(--accent);color:var(--accent)}
 button:active,.btn:active{transform:scale(.99)}
 button[type=submit],.btn.primary{border:none;color:#0d1a12;
      background:linear-gradient(135deg,var(--accent),var(--accent2));
      box-shadow:0 6px 22px var(--glow)}
 button[type=submit]:hover,.btn.primary:hover{color:#0d1a12;filter:brightness(1.06)}
 button.danger,.btn.danger{border-color:rgba(255,122,122,.4);color:#ffb3b3;
      background:var(--card)}
 button.danger:hover{border-color:var(--bad);color:var(--bad)}
 .card{border:1px solid var(--line);border-radius:14px;background:var(--card);
      padding:16px}
 /* A square button with a glyph and no words. The rule above carries the
    padding a labelled button needs, and under border-box that padding eats a
    small fixed width from both sides - so an icon button resets it and states
    its own size. */
 button.icon,.btn.icon{min-height:0;padding:0;width:46px;height:46px;
      display:inline-flex;align-items:center;justify-content:center}
 h1{font-size:24px;font-weight:600;letter-spacing:-.02em}
 h2{font-size:12px;font-weight:600;text-transform:uppercase;
      letter-spacing:.1em;color:var(--muted)}
 a{color:var(--accent2)}"""


# The page itself. Every served page set its own body rule, and each one used
# the `font:` shorthand - which resets font-family, so the page that had just
# loaded Poppins rendered its body in the system font.
#
# Set here, and set as separate properties rather than the shorthand.
LAYOUT_CSS = """ *{box-sizing:border-box}
 body{margin:0 auto;padding:18px;background:var(--bg);color:var(--text);
      font-size:16px;line-height:1.5;max-width:820px}
 h1{margin:0 0 4px}
 h2{margin:22px 0 8px}
 p.sub{color:var(--muted);margin:0 0 18px;font-size:14px}
 section{background:var(--card);border:1px solid var(--line);
      border-radius:14px;padding:16px;margin-bottom:16px}
 label{display:block;font-size:13px;color:var(--muted);margin:12px 0 4px}
 .row{display:flex;gap:10px}
 .row>div{flex:1}
 .hint{color:var(--muted);font-size:12.5px;margin-top:6px;line-height:1.55}
 .empty{color:var(--muted);font-size:14px;padding:10px 0}"""


# One banner, not four. The same "it worked" strip was `.note`/`.warn` on three
# pages, `.said`/`.said.bad` on a fourth and `.badge` on a fifth, so a person
# moving between them saw the same message styled three ways.
BANNER_CSS = """ .banner{border-radius:11px;padding:13px 16px;margin:0 0 16px;
      font-size:14px;border:1px solid var(--accent);
      background:rgba(47,240,142,.12)}
 .banner.bad{border-color:var(--bad);background:rgba(255,122,122,.12);
      color:#ffb3b3}"""


# The nine positions, drawn as the shape of the screen. Three pages ask this
# question and each drew its own grid; a dropdown reading "bottom-right" is a
# word to translate into a place.
POSITION_CSS = """ .where{display:grid;grid-template-columns:repeat(3,1fr);
      gap:6px;max-width:320px;aspect-ratio:16/9;margin-top:6px}
 .where button{min-height:0;padding:0;font-size:11.5px;font-weight:500;
      border-radius:9px;background:var(--card);color:var(--muted)}
 .where button.on{border-color:var(--accent);color:var(--accent);
      background:linear-gradient(150deg,rgba(47,240,142,.14),var(--card) 70%)}"""


# A button, not a link with an arrow glyph in it. It is the control people
# reach for most on a phone and it was the least tappable thing on the page.
BACK_CSS = """ a.back{display:inline-flex;align-items:center;gap:8px;
      text-decoration:none;background:var(--card);border:1px solid var(--line);
      color:var(--text);border-radius:10px;padding:11px 16px;font-size:15px;
      font-weight:600;margin-bottom:14px}
 a.back:active{background:#26262b}
 a.back svg{width:16px;height:16px;fill:none;stroke:currentColor;
      stroke-width:2.4;stroke-linecap:round;stroke-linejoin:round}
 .backrow{display:flex;gap:10px;flex-wrap:wrap;align-items:center}"""


_CHEVRON = ('<svg viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M15 5l-7 7 7 7"/></svg>')


def back_button(token: str, label: str = "Dashboard", href: str = "/") -> str:
    """A styled back control for the top of a GUI page."""
    joiner = "&" if "?" in href else "?"
    return (f'<a class="back" href="{escape(href)}{joiner}'
            f'token={escape(token)}">{_CHEVRON}<span>{escape(label)}</span></a>')


# A sticky row of sibling pages, for a section with more than one.
#
# Sticky because these are long pages - a list of files, a form - and a set of
# tabs that scrolls away is a set of tabs somebody scrolls back up to reach.
# It sits above the content rather than beside it: at phone width there is no
# beside.
SUBNAV_CSS = """ .subnav{position:sticky;top:0;z-index:20;display:flex;
      margin:0 -18px 16px;padding:10px 18px;background:var(--bg);
      border-bottom:1px solid var(--line);overflow-x:auto;
      -webkit-overflow-scrolling:touch}
 /* Centred by an inner row rather than by justify-content on the scroller.
    A flex container that both centres AND scrolls clips the overflow at the
    START in Chromium, and what is cut off cannot be scrolled back to - so
    the first tab would vanish on a narrow phone. `margin:auto` centres while
    there is room and gives up cleanly when there is not. */
 .subnav .row{display:flex;gap:6px;margin:0 auto}
 .subnav a{display:inline-flex;align-items:center;gap:8px;flex:0 0 auto;
      text-decoration:none;color:var(--muted);background:var(--card);
      border:1px solid var(--line);border-radius:10px;padding:10px 14px;
      font-size:14.5px;font-weight:600;white-space:nowrap}
 .subnav a:active{background:#26262b}
 .subnav a.on{color:var(--accent);border-color:var(--accent);
      background:linear-gradient(150deg,rgba(47,240,142,.14),var(--card) 70%)}
 .subnav a svg{flex:none;opacity:.85}"""


def subnav(items, current: str = "", token: str = "") -> str:
    """
    The sibling pages of a section, as a sticky row.

    `items` is a list of (href, label, icon). The current page is still a link
    rather than a dead span - a tab that does nothing when tapped reads as
    broken, and reloading the page you are on is a harmless thing for it to
    do.

    Include SUBNAV_CSS on any page using this.
    """
    from src.webicons import svg

    parts = []
    for href, label, icon in items:
        joiner = "&" if "?" in href else "?"
        target = f"{escape(href)}{joiner}token={escape(token)}" if token \
            else escape(href)
        on = " class=\"on\"" if href == current else ""
        parts.append(f'<a href="{target}"{on}>{svg(icon, 18)}'
                     f'<span>{escape(label)}</span></a>')
    return f'<nav class="subnav"><div class="row">{"".join(parts)}</div></nav>'


def chrome_css() -> str:
    """Everything a page needs that is not its own layout."""
    return "\n".join((FONTS, PALETTE, LAYOUT_CSS, FIELD_CSS, BACK_CSS,
                      CONTROL_CSS, BANNER_CSS, POSITION_CSS, SUBNAV_CSS))


def banner(message: str, bad: bool = False) -> str:
    """The one status strip. Empty when there is nothing to say."""
    if not message:
        return ""
    return f'<p class="banner{" bad" if bad else ""}">{escape(message)}</p>'


def position_grid(selected: str = "", name: str = "quadrant",
                  field_id: str = "quadrant") -> str:
    """
    The nine positions as the shape of the screen, plus the field they set.

    The framework's own list, so a page cannot offer a tenth or miss one out.
    Include POSITION_SCRIPT once on any page that uses this.
    """
    from src.ui.widget import POSITIONS, POSITION_LABELS

    buttons = "".join(
        '<button type="button" data-q="{key}"{on}>{label}</button>'.format(
            key=escape(key),
            on=' class="on"' if key == selected else "",
            label=escape(POSITION_LABELS[key]))
        for key in POSITIONS)
    return (f'<input type="hidden" name="{escape(name)}" '
            f'id="{escape(field_id)}" value="{escape(selected)}">'
            f'<div class="where" data-for="{escape(field_id)}">{buttons}</div>')


# Wires every grid on the page to its own hidden field, so a page may carry
# more than one without them fighting over the same element id.
POSITION_SCRIPT = """
Array.prototype.forEach.call(document.querySelectorAll('.where'),
  function (grid) {
    var field = document.getElementById(grid.dataset.for);
    Array.prototype.forEach.call(grid.querySelectorAll('button'),
      function (b) {
        b.addEventListener('click', function (e) {
          e.preventDefault();
          Array.prototype.forEach.call(grid.querySelectorAll('button'),
            function (o) { o.classList.remove('on'); });
          b.classList.add('on');
          if (field) { field.value = b.dataset.q; }
        });
      });
  });
"""


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<style>
{chrome}
{css}
</style>
{head}
</head>
<body>
{back}{nav}{heading}{blurb}{banner}
{body}
{script}
</body>
</html>
"""


def page(title: str, body: str, token: str = "", heading: str = "",
         blurb: str = "", message: str = "", bad: bool = False,
         css: str = "", script: str = "", back: bool = True,
         back_label: str = "Dashboard", back_href: str = "/",
         nav: str = "", head: str = "") -> str:
    """
    A whole served page.

    Everything above the content is the same on every page and is assembled
    here: the doctype, the viewport, the chrome, the back control, the
    heading and the one status banner.

    `color-scheme: dark` is not optional and so is not a parameter. Chromium
    runs with forceDarkModeEnabled, so a page that does not declare itself
    dark is inverted into a white rectangle - and it was the easiest line to
    leave out of a page written by copying another one.
    """
    return PAGE.format(
        title=escape(title),
        chrome=chrome_css(),
        css=css,
        back=back_button(token, back_label, back_href) if back else "",
        # Above the heading, not under it. A section's tabs are how somebody
        # moves between its pages, and putting them below the title makes the
        # title look like it belongs to the tabs rather than to the page they
        # chose. Sticky as well, so this is also the only position where what
        # sticks to the top is the row of controls rather than a stray
        # heading.
        nav=nav,
        heading=f"<h1>{escape(heading)}</h1>" if heading else "",
        blurb=f'<p class="sub">{escape(blurb)}</p>' if blurb else "",
        banner=banner(message, bad),
        body=body,
        script=f"<script>{script}</script>" if script else "",
        # Tags that belong in <head> - a stylesheet link for an asset too
        # big to inline. A <link> placed in the body works, but it is read
        # after the page has already been laid out once.
        head=head,
    )


# ── Pages that live in files ────────────────────────────────────────────────

import hashlib as _hashlib
import json as _json
from pathlib import Path as _Path

#anything bigger than this is served from its own URL rather than written
#into the page
INLINE_LIMIT = 4096

#what a web folder may hold. A folder is not an allowlist by itself: globbing
#it would publish an editor backup or a stray notes file the moment somebody
#saved one next to the page.
WEB_SUFFIXES = (".html", ".css", ".js")

_WEB_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8",
}


#The panel's own pages - the documentation viewer, the plugin manager - kept
#in src/web/ for the same reason a plugin's are kept in its own web/ folder.
#Declared here so the route that serves them and the pages that link them are
#looking at one object rather than each building their own.
CORE_ASSETS = None       # set by core_assets(), below


def core_assets():
    """
    The panel's own web folder, made once.

    Late rather than at import, because `WebAssets` is defined below this and
    because a module that builds a page should not have to care whether it
    was the first one to ask.
    """
    global CORE_ASSETS
    if CORE_ASSETS is None:
        CORE_ASSETS = WebAssets(_Path(__file__).with_name("web"),
                                required=("docs.css", "docs.js",
                                          "plugins.css", "listing.js"))
        CORE_ASSETS.endpoint = "/web"
    return CORE_ASSETS


class WebAssets:
    """
    A page kept as files, rather than as strings inside Python.

    Point it at a folder of `.html`, `.css` and `.js`, and it reads them,
    serves the large ones, inlines the small ones and hands the whole thing to
    `page()`. Nothing is formatted, substituted into or escaped on the way
    out: what the panel has to tell the page arrives as one JSON object in
    `window.PAGE`, and the script reads it.

    That is the point rather than a detail. A quote inside an HTML attribute
    inside a JavaScript string inside a Python string passes through two
    rounds of escape processing, and one of them eats the backslash - which
    has already shipped a page that rendered as a row of tabs and nothing
    else, because the script died before it could reveal a single pane.
    `str.format()` is worse again: CSS and JavaScript are full of braces and
    it would try to substitute every one.

    Being files also means an editor highlights them, `node --check` runs on
    the script directly, and a formatter can be pointed at any of them.

        ASSETS = WebAssets(Path(__file__).with_name("web"))

        def load(self):
            self.client.API.register(self.KEY, "thing_page", self.api_page,
                                     requires_auth=True, gui="Thing")
            ASSETS.register(self.client, self.KEY)

        def api_page(self, **kwargs):
            return ASSETS.page(title="Thing", token=token,
                               endpoint="/public/thing_page",
                               data={"things": [...]})
    """

    #the object the data is written into, the same on every page so a script
    #never has to be told what its own plugin called it
    GLOBAL = "PAGE"

    def __init__(self, folder, required=("page.html",),
                 inline_limit: int = INLINE_LIMIT):
        self.folder = _Path(folder)
        self.required = tuple(required)
        self.inline_limit = int(inline_limit)
        self.endpoint = ""

    # ── Reading ─────────────────────────────────────────────────────────────

    def names(self) -> list:
        """Every asset in the folder, in a fixed order."""
        return sorted(entry.name for entry in self.folder.glob("*")
                      if entry.is_file() and entry.suffix in WEB_SUFFIXES)

    def missing(self) -> list:
        """
        Which required files are not here.

        Meant to be called when a plugin loads rather than when somebody opens
        the page, so a file left out of a build is a line in the log with the
        path in it instead of a 500 the first time it is wanted -
        `verify_siblings()` does the same for Python modules.
        """
        return [name for name in self.required
                if not (self.folder / name).is_file()]

    def read(self, name: str) -> str:
        """
        One asset, by name.

        Checked against what is actually in the folder rather than joined onto
        it. `read("../main.py")` is a request this has to be able to refuse,
        and comparing against a listing refuses it without any thinking about
        separators, symlinks or encodings.
        """
        if str(name) not in self.names():
            return ""
        try:
            return (self.folder / str(name)).read_text(encoding="utf-8")
        except OSError:
            return ""

    def fingerprint(self, name: str) -> str:
        """Eight characters of the file's own hash, for its URL."""
        return _hashlib.sha256(
            self.read(name).encode("utf-8")).hexdigest()[:8]

    def content_type(self, name: str) -> str:
        return _WEB_TYPES.get(_Path(str(name)).suffix,
                              "text/plain; charset=utf-8")

    # ── Serving ─────────────────────────────────────────────────────────────

    def register(self, client, plugin_key: str, key: str = "") -> str:
        """
        Wire up the endpoint that serves the large files, and remember it.

        Done here rather than by the caller because the URL has to appear in
        two places - the handler that serves it and the markup that links it -
        and two places kept in step by hand is one page linking a script
        nobody serves, which is silent until somebody opens it.
        """
        key = key or f"{plugin_key}_asset"
        client.API.register(plugin_key, key, self.serve, requires_auth=True)
        self.endpoint = f"/public/{key}"
        return self.endpoint

    def serve(self, name: str = "", **_ignored):
        """The endpoint. Returns what a plugin endpoint returns."""
        body = self.read(str(name))
        if not body:
            return "Not found", 404, {"Content-Type": "text/plain"}
        return body, 200, {
            "Content-Type": self.content_type(name),
            # The URL carries a hash of the contents, so a given URL can never
            # go stale and this can be cached for as long as it likes.
            "Cache-Control": "public, max-age=31536000, immutable",
        }

    def link(self, name: str) -> str:
        """
        The URL for one asset, with its own hash on it.

        Public because a caller that builds its own document rather than
        using `page()` - the documentation viewer has a sidebar and a table
        of contents and belongs to no plugin - still wants the file served
        and cached the same way.
        """
        return f"{self.endpoint}?name={name}&v={self.fingerprint(name)}"

    def tag(self, name: str) -> str:
        """A `<link>` or `<script>` for one asset, whichever it needs."""
        if name.endswith(".css"):
            return f'<link rel="stylesheet" href="{self.link(name)}">'
        return f'<script src="{self.link(name)}" defer></script>'

    def inline_or_link(self, name: str) -> tuple:
        """
        One asset as `(inline_text, tag)` - exactly one of them filled.

        The same rule `page()` applies, for a document built by hand.
        """
        body = self.read(name)
        if body and len(body) > self.inline_limit and self.endpoint:
            return "", self.tag(name)
        return body, ""

    # ── The page ────────────────────────────────────────────────────────────

    def data_block(self, data: dict) -> str:
        """
        The one thing that crosses from the panel into the script.

        `json.dumps`, and then the angle brackets escaped: a value containing
        `</script>` would otherwise end the block early and whatever followed
        it would run.
        """
        blob = _json.dumps(dict(data or {}))
        blob = blob.replace("<", "\\u003c").replace(">", "\\u003e")
        return f"window.{self.GLOBAL} = {blob};"

    def page(self, title: str, token: str = "", endpoint: str = "",
             data: dict = None, heading: str = "", blurb: str = "",
             message: str = "", bad: bool = False, body_file: str = "page.html",
             css_file: str = "page.css", script_file: str = "page.js",
             also: tuple = ()) -> str:
        """
        The whole document, from the folder.

        `also` names core assets from `src/web/` to load first - the shared
        listing helper, for a page with more items on it than fit. They are
        linked with their own fingerprints, so a page that shares one cannot
        go stale against it.
        """
        gone = self.missing()
        if gone:
            return page(
                title=title, heading=heading or title,
                body="<section class=\"card\"><p class=\"empty\">This page is "
                     "missing " + escape(", ".join(gone)) + ".</p></section>",
                token=token,
                message="The page's files are not installed.", bad=True)

        payload = dict(data or {})
        payload.setdefault("token", token)
        payload.setdefault("endpoint", endpoint)

        css = self.read(css_file)
        script = self.read(script_file)
        data = self.data_block(payload)
        extra = ""

        if script and len(script) > self.inline_limit and self.endpoint:
            extra += self.tag(script_file)
        else:
            data = data + "\n" + script

        head = "".join(core_assets().tag(name) for name in (also or ())
                       if core_assets().read(name))
        if css and len(css) > self.inline_limit and self.endpoint:
            head = self.tag(css_file) + head
            css = ""

        return page(
            title=title, heading=heading or title, blurb=blurb,
            token=token, message=message, bad=bad,
            css=css, script=data,
            body=self.read(body_file) + extra,
            head=head,
        )
