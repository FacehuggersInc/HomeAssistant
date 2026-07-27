"""
Renders the files in docs/ as a browsable site.

A small markdown converter rather than a dependency: the docs are ours, the
subset of markdown they use is known, and a wall panel should not need a pip
install to explain itself. Nothing here reaches the network, so the viewer
works on a machine with no internet.

Everything is escaped before any tag is inserted. The input is trusted - these
are files shipped with the app - but the renderer is reachable without auth, so
it is written as though it were not.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Optional

from src.constants import INSTALL_ROOT

DOCS_DIR = INSTALL_ROOT / "docs"

# Order in the sidebar. Anything on disk but not listed is appended after,
# alphabetically, so a new file shows up without being registered here.
NAV_ORDER = [
    ("index.md",           "Overview"),
    ("installation.md",    "Installation"),
    ("updating.md",        "Updating"),
    ("architecture.md",    "Architecture"),
    ("plugins.md",         "Plugins"),
    ("bundled-plugins.md", "Bundled plugins"),
    ("pages.md",           "Pages"),
    ("widgets.md",         "Widgets"),
    ("features.md",        "Features"),
    ("registries.md",      "Registries"),
    ("quick-settings.md",  "Quick settings"),
    ("events.md",          "Events"),
    ("settings.md",        "Settings"),
    ("threading.md",       "Threading"),
    ("styling.md",         "Styling"),
    ("notifications.md",   "Notifications, state, assets"),
    ("dialogs.md",         "Dialogs and overlays"),
    ("keyboard.md",        "On-screen keyboard"),
    ("assistant.md",       "Voice assistant"),
    ("mixins.md",          "Mixins"),
    ("api.md",             "Backend API"),
    ("philosophy.md",      "Philosophy"),
]


## -- FILES -------------------------------------------------------------------

def available() -> bool:
    return DOCS_DIR.is_dir()


def resolve(name: str) -> Optional[Path]:
    """
    Map a URL fragment to a file inside docs/, or None.

    Resolved and then checked against the docs directory rather than filtered
    for "..": a symlink or an absolute path would walk straight past a
    blacklist, and this endpoint has no auth in front of it.
    """
    if not name:
        name = "index"
    name = name.strip().strip("/")
    if not name.endswith(".md"):
        name += ".md"

    try:
        candidate = (DOCS_DIR / name).resolve()
        root = DOCS_DIR.resolve()
    except OSError:
        return None

    if candidate == root or root not in candidate.parents:
        return None
    if not candidate.is_file():
        return None
    return candidate


def nav_entries() -> list:
    """[(slug, title, filename)] in display order."""
    if not available():
        return []

    on_disk = {p.name for p in DOCS_DIR.glob("*.md")}
    entries = []
    for filename, title in NAV_ORDER:
        if filename in on_disk:
            entries.append((filename[:-3], title, filename))
            on_disk.discard(filename)
    for filename in sorted(on_disk):
        entries.append((filename[:-3], title_of(DOCS_DIR / filename), filename))
    return entries


def title_of(path: Path) -> str:
    """The first heading, or the filename if there is not one."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    except OSError:
        pass
    return path.stem.replace("-", " ").replace("_", " ").title()


## -- MARKDOWN ----------------------------------------------------------------

_slug_strip = re.compile(r"[^a-z0-9\s-]")


def slugify(text: str) -> str:
    text = _slug_strip.sub("", text.lower()).strip()
    return re.sub(r"[\s-]+", "-", text) or "section"


_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?!\*)")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
_BARE_URL = re.compile(r"(?<![\"'=(])\bhttps?://[^\s<>\)\"']+")


def inline(text: str) -> str:
    """
    Inline markdown for one line of already-escaped text.

    Code spans are pulled out first and put back last, so a `*` or an
    `http://` inside backticks is left exactly as written instead of being
    turned into emphasis or a link.
    """
    stash: list = []

    def keep(markup: str) -> str:
        stash.append(markup)
        return f"\x00{len(stash) - 1}\x00"

    out = html.escape(text, quote=False)
    out = _INLINE_CODE.sub(lambda m: keep(f"<code>{m.group(1)}</code>"), out)
    out = _LINK.sub(
        lambda m: keep(f'<a href="{html.escape(link_target(m.group(2)), quote=True)}">'
                       f"{m.group(1)}</a>"), out)
    out = _BARE_URL.sub(lambda m: keep(bare_link(m.group(0))), out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _ITALIC.sub(r"<em>\1</em>", out)

    for index, markup in enumerate(stash):
        out = out.replace(f"\x00{index}\x00", markup)
    return out


def bare_link(url: str) -> str:
    """
    Wrap a bare URL, leaving trailing sentence punctuation outside it.

    "see https://example.org/x." ends in a full stop belonging to the sentence,
    not the address, and a link that swallows it 404s.
    """
    trailing = ""
    while url and url[-1] in ".,;:!?":
        trailing = url[-1] + trailing
        url = url[:-1]
    safe = html.escape(url, quote=True)
    return f'<a href="{safe}" rel="noreferrer">{safe}</a>{html.escape(trailing)}'


def link_target(href: str) -> str:
    """
    Rewrite links between doc files so they work as routes.

    Absolute, not relative. `/docs` and `/docs/` are both valid, and a relative
    "plugins" resolves to /plugins from the first and /docs/plugins from the
    second - so half the cross-links would 404 depending on how the reader
    arrived at the page.
    """
    if href.startswith(("http://", "https://", "#", "mailto:", "/")):
        return href
    if href.endswith(".md"):
        return f"/docs/{href[:-3]}"
    return href


def render(markdown: str) -> tuple[str, list]:
    """Returns (html_body, [(level, title, anchor)]) for the page's own TOC."""
    lines = markdown.replace("\r\n", "\n").split("\n")
    out: list = []
    toc: list = []

    list_stack: list = []       # open <ul>/<ol> tags, innermost last
    item_open: list = []        # whether an <li> is open at each of those levels
    in_code = False
    code_lang = ""
    code_buffer: list = []
    table_buffer: list = []
    paragraph: list = []

    def close_lists(to_depth: int = 0) -> None:
        while len(list_stack) > to_depth:
            # The <li> has to close before its list does. When this unwinds a
            # nested list the parent's <li> is still open on purpose - the
            # nested list belongs inside it.
            if item_open[-1]:
                out.append("</li>")
                item_open[-1] = False
            out.append(f"</{list_stack.pop()}>")
            item_open.pop()

    def flush_paragraph() -> None:
        if paragraph:
            out.append(f"<p>{inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_table() -> None:
        if not table_buffer:
            return
        rows = [r for r in table_buffer if not re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", r)]
        table_buffer.clear()
        if not rows:
            return
        out.append('<div class="table-wrap"><table>')
        for index, row in enumerate(rows):
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            tag = "th" if index == 0 else "td"
            out.append("<tr>" + "".join(
                f"<{tag}>{inline(c)}</{tag}>" for c in cells) + "</tr>")
        out.append("</table></div>")

    for raw in lines:
        line = raw.rstrip()

        # Fenced code. Checked before anything else, because inside a fence a
        # '#' is a comment and a '|' is a pipe, not a heading or a table.
        if line.lstrip().startswith("```"):
            if in_code:
                out.append(code_block("\n".join(code_buffer), code_lang))
                code_buffer.clear()
                in_code = False
                code_lang = ""
            else:
                flush_paragraph()
                flush_table()
                close_lists()
                in_code = True
                code_lang = line.lstrip()[3:].strip()
            continue
        if in_code:
            code_buffer.append(raw)
            continue

        stripped = line.strip()

        if not stripped:
            flush_paragraph()
            flush_table()
            close_lists()
            continue

        if set(stripped) <= {"-", "*", "_"} and len(stripped) >= 3:
            flush_paragraph()
            flush_table()
            close_lists()
            out.append("<hr>")
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            flush_paragraph()
            flush_table()
            close_lists()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            anchor = slugify(title)
            toc.append((level, title, anchor))
            out.append(f'<h{level} id="{anchor}">'
                       f'<a class="anchor" href="#{anchor}">#</a>'
                       f"{inline(title)}</h{level}>")
            continue

        if stripped.startswith(">"):
            flush_paragraph()
            close_lists()
            out.append(f"<blockquote>{inline(stripped.lstrip('> ').strip())}</blockquote>")
            continue

        if "|" in stripped and stripped.count("|") >= 2:
            flush_paragraph()
            close_lists()
            table_buffer.append(stripped)
            continue

        bullet = re.match(r"^(\s*)([-*+]|\d+\.)\s+(.*)$", line)
        if bullet:
            flush_paragraph()
            flush_table()
            # Two spaces per level, which is what these docs use.
            depth = len(bullet.group(1)) // 2 + 1
            tag = "ol" if bullet.group(2)[0].isdigit() else "ul"

            close_lists(depth)
            if len(list_stack) == depth and item_open[-1]:
                out.append("</li>")          # sibling item at the same level
                item_open[-1] = False
            while len(list_stack) < depth:
                # Opened without closing the parent <li>, so the nested list
                # ends up inside it rather than as its sibling.
                out.append(f"<{tag}>")
                list_stack.append(tag)
                item_open.append(False)

            out.append(f"<li>{inline(bullet.group(3))}")
            item_open[-1] = True
            continue

        flush_table()
        close_lists()
        paragraph.append(stripped)

    if in_code:
        # An unterminated fence should still show its contents rather than
        # silently swallowing the rest of the file.
        out.append(code_block("\n".join(code_buffer), code_lang))
    flush_paragraph()
    flush_table()
    close_lists()

    return "\n".join(out), toc


def code_block(code: str, language: str) -> str:
    label = html.escape(language or "text", quote=True)
    body = html.escape(code, quote=False)
    return (f'<div class="code" data-lang="{label}">'
            f'<div class="code-bar"><span>{label}</span>'
            f'<button class="copy" type="button">copy</button></div>'
            f"<pre><code>{body}</code></pre></div>")


## -- PAGE --------------------------------------------------------------------

def page(slug: str) -> Optional[str]:
    path = resolve(slug)
    if path is None:
        return None

    try:
        markdown = path.read_text(encoding="utf-8")
    except OSError as e:
        return shell("Documentation", f"<p>Could not read that page: "
                                      f"{html.escape(str(e))}</p>", "", slug)

    body, toc = render(markdown)
    return shell(title_of(path), body, toc_html(toc), path.stem)


def toc_html(toc: list) -> str:
    # h1 is the page title and is already at the top of the page; h4 and below
    # make the rail unreadable at this width.
    items = [(level, title, anchor) for level, title, anchor in toc if 2 <= level <= 3]
    if len(items) < 2:
        return ""
    rows = "".join(
        f'<a class="toc-{level}" href="#{anchor}">{html.escape(title)}</a>'
        for level, title, anchor in items
    )
    return f'<nav class="toc"><div class="toc-title">On this page</div>{rows}</nav>'


def sidebar_html(current: str) -> str:
    rows = []
    for slug, title, _ in nav_entries():
        active = ' class="active"' if slug == current else ""
        rows.append(f'<a href="/docs/{slug}"{active}>{html.escape(title)}</a>')
    return "".join(rows)


def shell(title: str, body: str, toc: str, current: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} - Home Assistant docs</title>
<style>{STYLE}</style>
</head>
<body>
<button class="menu" type="button" aria-label="Menu">&#9776;</button>
<aside class="sidebar">
  <div class="brand">Home Assistant<span>documentation</span></div>
  <input class="filter" type="search" placeholder="Filter pages" aria-label="Filter pages">
  <nav class="nav">{sidebar_html(current)}</nav>
</aside>
<main>
  <article>{body}</article>
</main>
{toc}
<script>{SCRIPT}</script>
</body>
</html>"""


STYLE = """
:root {
  --bg:#151517; --panel:#1c1c1f; --line:#2c2c31; --text:#e6e6e8;
  --muted:#9a9aa2; --accent:#2ff08e; --accent-dim:#1faf68; --code:#111114;
}
* { box-sizing:border-box; }
body {
  margin:0; background:var(--bg); color:var(--text);
  font:16px/1.65 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  display:grid; grid-template-columns:270px minmax(0,1fr) 220px;
}
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:underline; }

.sidebar {
  position:sticky; top:0; height:100vh; overflow-y:auto;
  background:var(--panel); border-right:1px solid var(--line); padding:22px 16px;
}
.brand { font-weight:600; font-size:17px; margin-bottom:16px; }
.brand span { display:block; font-weight:400; font-size:12px; color:var(--muted);
  text-transform:uppercase; letter-spacing:.09em; margin-top:2px; }
.filter {
  width:100%; padding:8px 10px; margin-bottom:14px; border-radius:7px;
  border:1px solid var(--line); background:var(--bg); color:var(--text); font-size:14px;
}
.filter:focus { outline:none; border-color:var(--accent-dim); }
.nav { display:flex; flex-direction:column; gap:1px; }
.nav a {
  color:var(--muted); padding:7px 10px; border-radius:7px; font-size:14.5px;
  border-left:2px solid transparent;
}
.nav a:hover { background:#26262b; color:var(--text); text-decoration:none; }
.nav a.active { background:#26262b; color:var(--text); border-left-color:var(--accent); }

main { min-width:0; padding:44px 52px 96px; }
article { max-width:820px; }

h1,h2,h3,h4 { line-height:1.25; scroll-margin-top:24px; }
h1 { font-size:32px; margin:0 0 6px; }
h2 { font-size:23px; margin:44px 0 12px; padding-top:20px; border-top:1px solid var(--line); }
h3 { font-size:18px; margin:28px 0 8px; color:#d2d2d6; }
.anchor { color:var(--line); margin-left:-20px; padding-right:8px; opacity:0; font-weight:400; }
h2:hover .anchor, h3:hover .anchor { opacity:1; }

p { margin:0 0 15px; }
ul,ol { margin:0 0 15px; padding-left:22px; }
li { margin:4px 0; }
hr { border:0; border-top:1px solid var(--line); margin:34px 0; }
blockquote {
  margin:0 0 15px; padding:9px 15px; border-left:3px solid var(--accent-dim);
  background:#1a1a1e; color:#c8c8cd; border-radius:0 7px 7px 0;
}
code {
  background:#26262b; padding:2px 6px; border-radius:4px; font-size:13.5px;
  font-family:"JetBrains Mono", "SF Mono", Consolas, monospace;
}

.code { margin:0 0 18px; border:1px solid var(--line); border-radius:9px; overflow:hidden; }
.code-bar {
  display:flex; justify-content:space-between; align-items:center;
  padding:6px 12px; background:#232328; border-bottom:1px solid var(--line);
  font-size:11.5px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted);
}
.copy {
  background:none; border:1px solid var(--line); color:var(--muted); cursor:pointer;
  font:inherit; font-size:11px; padding:3px 9px; border-radius:5px; letter-spacing:.05em;
}
.copy:hover { color:var(--text); border-color:var(--muted); }
.copy.done { color:var(--accent); border-color:var(--accent-dim); }
.code pre { margin:0; padding:14px 16px; overflow-x:auto; background:var(--code); }
.code pre code { background:none; padding:0; font-size:13.5px; line-height:1.55; }

.table-wrap { overflow-x:auto; margin:0 0 18px; }
table { border-collapse:collapse; width:100%; font-size:14.5px; }
th,td { text-align:left; padding:9px 13px; border:1px solid var(--line); vertical-align:top; }
th { background:#232328; font-weight:600; }
tr:nth-child(even) td { background:#1a1a1e; }

.toc {
  position:sticky; top:0; height:100vh; overflow-y:auto;
  padding:46px 20px 40px 0; font-size:13.5px;
}
.toc-title {
  color:var(--muted); text-transform:uppercase; letter-spacing:.09em;
  font-size:11px; margin-bottom:10px;
}
.toc a { display:block; color:var(--muted); padding:4px 0; }
.toc a:hover { color:var(--text); text-decoration:none; }
.toc a.toc-3 { padding-left:13px; font-size:13px; }

.menu { display:none; }

@media (max-width:1180px) { body { grid-template-columns:250px minmax(0,1fr); } .toc { display:none; } }
@media (max-width:820px) {
  body { grid-template-columns:1fr; }
  .sidebar {
    position:fixed; z-index:20; width:270px; transform:translateX(-100%);
    transition:transform .2s ease;
  }
  .sidebar.open { transform:none; }
  .menu {
    display:block; position:fixed; z-index:21; top:12px; left:12px;
    width:42px; height:42px; border-radius:9px; cursor:pointer;
    background:var(--panel); border:1px solid var(--line); color:var(--text); font-size:19px;
  }
  main { padding:70px 20px 60px; }
}
"""

SCRIPT = """
document.querySelectorAll('.copy').forEach(function (button) {
  button.addEventListener('click', function () {
    var code = button.closest('.code').querySelector('code');
    // Fall back to a hidden textarea: navigator.clipboard is unavailable over
    // plain http on anything but localhost, and this is served over http.
    var done = function () {
      button.textContent = 'copied';
      button.classList.add('done');
      setTimeout(function () {
        button.textContent = 'copy';
        button.classList.remove('done');
      }, 1400);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(code.innerText).then(done);
      return;
    }
    var scratch = document.createElement('textarea');
    scratch.value = code.innerText;
    scratch.style.position = 'fixed';
    scratch.style.opacity = '0';
    document.body.appendChild(scratch);
    scratch.select();
    try { document.execCommand('copy'); done(); } catch (e) { /* nothing to do */ }
    document.body.removeChild(scratch);
  });
});

var filter = document.querySelector('.filter');
if (filter) {
  filter.addEventListener('input', function () {
    var needle = filter.value.toLowerCase();
    document.querySelectorAll('.nav a').forEach(function (link) {
      link.style.display = link.textContent.toLowerCase().indexOf(needle) === -1 ? 'none' : '';
    });
  });
}

var menu = document.querySelector('.menu');
if (menu) {
  menu.addEventListener('click', function () {
    document.querySelector('.sidebar').classList.toggle('open');
  });
}
"""
