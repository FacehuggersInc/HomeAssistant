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

from src import docs_tracker as tracker
import json
import re
from pathlib import Path
from typing import Optional

from src.constants import INSTALL_ROOT

DOCS_DIR = INSTALL_ROOT / "docs"
BUNDLED_DIR = INSTALL_ROOT / "src" / "assets" / "bundled"

# Order in the sidebar. Anything on disk but not listed is appended after,
# alphabetically, so a new file shows up without being registered here.
#The nav, grouped by what somebody is trying to do.
#
#A flat list of thirty-odd pages is a list nobody reads to the end. Worse, it
#had no room for a page that arrived later: anything not named here was
#appended alphabetically at the bottom, so Wi-Fi and Bluetooth ended up filed
#under nothing at all.
#
#Groups are by purpose rather than by subsystem. "How do I add something" and
#"how does the screen work" are the questions people arrive with; which module
#a thing lives in is not.
NAV_GROUPS = [
    ("Start here", [
        ("index.md",           "Overview"),
        ("installation.md",    "Installation"),
        ("philosophy.md",      "Philosophy"),
    ]),
    ("Running it", [
        ("lifecycle.md",       "Application lifecycle"),
        ("updating.md",        "Updating"),
        ("when-it-will-not-start.md", "When it will not start"),
        ("logging.md",         "Logging"),
    ]),
    ("Building on it", [
        ("architecture.md",    "Architecture"),
        ("plugins.md",         "Plugins"),
        ("bundled-plugins.md", "Bundled plugins"),
        ("mixins.md",          "Mixins"),
        ("events.md",          "Events"),
        ("registries.md",      "Registries"),
        ("threading.md",       "Threading"),
    ]),
    ("On the screen", [
        ("pages.md",           "Pages"),
        ("widgets.md",         "Widgets"),
        ("tiles.md",           "Tiles"),
        ("dialogs.md",         "Dialogs and overlays"),
        ("quick-settings.md",  "Quick settings"),
        ("player.md",          "Media playback"),
        ("notifications.md",   "Notifications, state, assets"),
        ("styling.md",         "Styling"),
        ("keyboard.md",        "On-screen keyboard"),
    ]),
    ("Talking to it", [
        ("assistant.md",       "Voice assistant"),
        ("skills.md",          "Writing skills"),
        ("cancel.md",          "Cancelling"),
        ("api.md",             "Backend API"),
        ("users.md",           "Users"),
        ("webpage.md",         "The web page"),
    ]),
    ("The machine", [
        ("wifi.md",            "Wi-Fi"),
        ("bluetooth.md",       "Bluetooth"),
        ("backlight.md",       "Screen brightness"),
        ("settings.md",        "Settings"),
        ("features.md",        "Features"),
    ]),
]

#Flat, for anything that wants an order rather than a shape.
NAV_ORDER = [entry for _group, entries in NAV_GROUPS for entry in entries]


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


def plugin_dirs() -> list:
    """Every plugin directory, bundled or user-installed."""
    roots = [BUNDLED_DIR, INSTALL_ROOT / "plugins"]
    out = []
    for root in roots:
        if not root.is_dir():
            continue
        for directory in sorted(root.iterdir()):
            if directory.is_dir() and not directory.name.startswith((".", "__")):
                out.append(directory)
    return out


def plugin_pages(directory: Path) -> list:
    """
    [(slug, title, path)] for the .md files a plugin ships in its docs/ folder.

    A plugin that adds a subsystem should be able to document it without its
    pages being copied into the main docs tree, where the next update would
    overwrite or orphan them.
    """
    folder = directory / "docs"
    if not folder.is_dir():
        return []
    key = directory.name.lower()
    pages = []
    for file in sorted(folder.glob("*.md")):
        pages.append((f"plugin/{key}/{file.stem}", title_of(file), file))
    return pages


def plugin_docs() -> dict:
    """{plugin_slug: (display, readme_or_None, [pages])} for anything with docs."""
    found = {}
    for directory in plugin_dirs():
        readme = next((directory / n for n in ("readme.md", "README.md")
                       if (directory / n).is_file()), None)
        pages = plugin_pages(directory)
        if readme is None and not pages:
            continue
        found[directory.name.lower()] = (plugin_display(directory), readme, pages)
    return found


def resolve_plugin_page(slug: str):
    """A plugin's own docs page, by `plugin/<key>/<name>`."""
    parts = slug.strip("/").split("/")
    if len(parts) != 3 or parts[0] != "plugin":
        return None
    key, name = parts[1].lower(), parts[2]
    for directory in plugin_dirs():
        if directory.name.lower() != key:
            continue
        candidate = (directory / "docs" / f"{name}.md")
        try:
            resolved = candidate.resolve()
            root = (directory / "docs").resolve()
        except OSError:
            return None
        if resolved.is_file() and root in resolved.parents:
            return resolved
    return None


def bundled_plugins() -> list:
    """
    [(slug, display, path)] for bundled plugins shipping a readme.

    Read off disk rather than listed here, so a plugin added to the bundle
    appears in the sidebar without anyone remembering to register it.
    """
    out = []
    if not BUNDLED_DIR.is_dir():
        return out
    for directory in sorted(BUNDLED_DIR.iterdir()):
        if not directory.is_dir() or directory.name.startswith((".", "__")):
            continue
        readme = next((directory / n for n in ("readme.md", "README.md")
                       if (directory / n).is_file()), None)
        if readme is None:
            continue
        out.append((directory.name.lower(), plugin_display(directory), readme))
    return out


def plugin_display(directory: Path) -> str:
    """The name from plugin.toml, falling back to the folder."""
    toml = directory / "plugin.toml"
    if toml.is_file():
        try:
            for line in toml.read_text(encoding="utf-8").splitlines():
                match = re.match(r'\s*name\s*=\s*"([^"]+)"', line)
                if match:
                    return match.group(1)
        except OSError:
            pass
    return directory.name


def resolve_plugin(slug: str) -> Optional[Path]:
    for candidate, _, readme in bundled_plugins():
        if candidate == slug.strip().strip("/").lower():
            return readme
    return None


def nav_entries() -> list:
    """[(slug, title, filename)] in display order, flat."""
    return [entry for _group, entries in nav_groups() for entry in entries]


def nav_groups() -> list:
    """
    [(group title, [(slug, title, filename)])] in display order.

    A page on disk that no group names goes into a final "Everything else"
    rather than being dropped. Silently omitting it would mean a page nobody
    can reach; putting it somewhere visible means the omission gets noticed and
    filed properly.
    """
    if not available():
        return []

    on_disk = {p.name for p in DOCS_DIR.glob("*.md")}
    groups = []
    for title, entries in NAV_GROUPS:
        rows = []
        for filename, label in entries:
            if filename in on_disk:
                rows.append((filename[:-3], label, filename))
                on_disk.discard(filename)
        if rows:
            groups.append((title, rows))

    leftover = [(f[:-3], title_of(DOCS_DIR / f), f) for f in sorted(on_disk)]
    if leftover:
        groups.append(("Everything else", leftover))
    return groups


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
    pending_blank = False       # a blank line seen but not yet acted on
    quote: list = []            # consecutive "> " lines, joined into one block
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

    def flush_quote() -> None:
        # Consecutive quote lines are one blockquote. Emitting one per line
        # stacks four bordered boxes where the author wrote a single note.
        if quote:
            out.append(f"<blockquote>{inline(' '.join(quote))}</blockquote>")
            quote.clear()

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
            flush_quote()
            flush_table()
            # Lists are NOT closed here. A blank line between items is a loose
            # list, still one list - closing on the blank started a fresh <ol>
            # per item and every one of them numbered itself 1.
            pending_blank = True
            continue

        if set(stripped) <= {"-", "*", "_"} and len(stripped) >= 3:
            flush_paragraph()
            flush_table()
            close_lists()
            out.append("<hr>")
            pending_blank = False
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
            pending_blank = False
            continue

        if stripped.startswith(">"):
            flush_paragraph()
            close_lists()
            quote.append(stripped.lstrip("> ").strip())
            pending_blank = False
            continue
        flush_quote()

        if "|" in stripped and stripped.count("|") >= 2:
            flush_paragraph()
            close_lists()
            table_buffer.append(stripped)
            pending_blank = False
            continue

        # A wrapped line under a list item: indented, no marker of its own,
        # and no blank line between it and the item it belongs to. Without
        # this it closed the list and became a paragraph, which is what split
        # a numbered list into one <ol> per item.
        indent = len(line) - len(line.lstrip())
        if (list_stack and item_open and item_open[-1]
                and indent >= 2 and not pending_blank
                and not re.match(r"^\s*([-*+]|\d+\.)\s", line)):
            out.append(" " + inline(stripped))
            pending_blank = False
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
            pending_blank = False
            continue

        flush_table()
        close_lists()
        paragraph.append(stripped)
        pending_blank = False

    if in_code:
        # An unterminated fence should still show its contents rather than
        # silently swallowing the rest of the file.
        out.append(code_block("\n".join(code_buffer), code_lang))
    flush_quote()
    flush_paragraph()
    flush_table()
    close_lists()

    return "\n".join(out), toc


## -- SYNTAX HIGHLIGHTING ------------------------------------------------------

# Deliberately small. These are our own docs, in five known languages, and the
# alternative is shipping a JS highlighter from a CDN - which fails on a
# machine with no internet, which is exactly the machine reading local docs.
#
# Each spec is an ordered list of (token class, pattern). Order matters:
# comments and strings come first so a '#' inside a string is not a comment
# and a keyword inside a comment is not a keyword. An unknown language falls
# through to plain escaped text rather than guessing.

_PY_KEYWORDS = (
    r"\b(?:def|class|return|if|elif|else|for|while|in|not|and|or|import|from|"
    r"as|with|try|except|finally|raise|pass|lambda|yield|global|nonlocal|"
    r"assert|del|is|await|async|break|continue)\b"
)
_PY_CONSTANTS = r"\b(?:None|True|False|self|cls)\b"
_PY_BUILTINS = (
    r"\b(?:print|len|str|int|float|bool|dict|list|set|tuple|range|open|"
    r"isinstance|super|property|staticmethod|classmethod|Exception|enumerate|"
    r"sorted|any|all|min|max|abs|round|type|getattr|setattr|hasattr)\b"
)

SYNTAX = {
    "python": [
        ("com", r"#[^\n]*"),
        ("str", r"(?:[frbFRB]{0,2})(?:\"\"\".*?\"\"\"|\'\'\'.*?\'\'\'|\"(?:\\\\.|[^\"\\\\\n])*\"|\'(?:\\\\.|[^\'\\\\\n])*\')"),
        ("dec", r"@[\w.]+"),
        ("kw",  _PY_KEYWORDS),
        ("con", _PY_CONSTANTS),
        ("bui", _PY_BUILTINS),
        ("num", r"\b\d+(?:\.\d+)?\b"),
        ("fn",  r"\b[A-Za-z_]\w*(?=\()"),
    ],
    "bash": [
        ("com", r"#[^\n]*"),
        ("str", r"\"(?:\\\\.|[^\"\\\\])*\"|\'[^\']*\'"),
        ("flg", r"(?<=\s)--?[A-Za-z][\w-]*"),
        ("kw",  r"\b(?:if|then|else|fi|for|do|done|while|case|esac|function|export|source|cd|sudo)\b"),
        ("fn",  r"^\s*(?:\./)?[\w./-]+(?=\s|$)"),
        ("num", r"\b\d+\b"),
    ],
    "json": [
        ("key", r"\"(?:\\\\.|[^\"\\\\])*\"(?=\s*:)"),
        ("str", r"\"(?:\\\\.|[^\"\\\\])*\""),
        ("con", r"\b(?:true|false|null)\b"),
        ("num", r"-?\b\d+(?:\.\d+)?\b"),
    ],
    "toml": [
        ("com", r"#[^\n]*"),
        ("dec", r"^\s*\[[^\]\n]+\]"),
        ("str", r"\"(?:\\\\.|[^\"\\\\])*\"|\'[^\']*\'"),
        ("key", r"^\s*[\w.-]+(?=\s*=)"),
        ("con", r"\b(?:true|false)\b"),
        ("num", r"-?\b\d+(?:\.\d+)?\b"),
    ],
    "css": [
        ("com", r"/\*.*?\*/"),
        ("dec", r"^\s*[.#][\w-]+(?:::?[\w-]+)*"),
        ("key", r"[\w-]+(?=\s*:)"),
        ("str", r"\"(?:\\\\.|[^\"\\\\])*\"|\'[^\']*\'"),
        ("num", r"-?\b\d+(?:\.\d+)?(?:px|em|rem|%|s|ms)?\b"),
        ("fn",  r"\b[a-z-]+(?=\()"),
    ],
}
SYNTAX["py"] = SYNTAX["python"]
SYNTAX["sh"] = SYNTAX["shell"] = SYNTAX["bash"]

_COMPILED = {
    lang: re.compile(
        "|".join(f"(?P<{cls}{i}>{pattern})" for i, (cls, pattern) in enumerate(spec)),
        # S so a triple-quoted string or a /* */ comment can span lines;
        # M so the line-anchored patterns match every line, not just the first.
        # Both have to be flags here rather than inline (?ms) groups, which
        # Python rejects anywhere but the start of a pattern.
        re.S | re.M,
    )
    for lang, spec in SYNTAX.items()
}
_CLASS_OF = {
    lang: {f"{cls}{i}": cls for i, (cls, _) in enumerate(spec)}
    for lang, spec in SYNTAX.items()
}


def highlight(code: str, language: str) -> str:
    """Escaped HTML for a code block, with spans where we recognise the language."""
    lang = (language or "").strip().lower()
    pattern = _COMPILED.get(lang)
    if pattern is None:
        return html.escape(code, quote=False)

    classes = _CLASS_OF[lang]
    out = []
    cursor = 0
    for match in pattern.finditer(code):
        if match.start() > cursor:
            out.append(html.escape(code[cursor:match.start()], quote=False))
        name = match.lastgroup
        out.append(f'<span class="t-{classes[name]}">'
                   f"{html.escape(match.group(), quote=False)}</span>")
        cursor = match.end()
    out.append(html.escape(code[cursor:], quote=False))
    return "".join(out)


def code_block(code: str, language: str) -> str:
    label = html.escape(language or "text", quote=True)
    body = highlight(code, language)
    return (f'<div class="code" data-lang="{label}">'
            f'<div class="code-bar"><span>{label}</span>'
            f'<button class="copy" type="button">copy</button></div>'
            f"<pre><code>{body}</code></pre></div>")


## -- PAGE --------------------------------------------------------------------

def changes_page() -> str:
    """
    What has changed in these docs, newest first.

    Reachable at all times from the top of the nav rather than only when
    something is badged, because "what changed" is also asked long after the
    badges have expired - and a button that comes and goes is one nobody learns
    is there.
    """
    import datetime as _dt

    data = tracker.scan()
    entries = tracker.log_entries(data)
    titles = {slug: title for slug, title, _f in nav_entries()}

    if not entries:
        body = "<p>Nothing recorded yet.</p>"
        return shell("What changed", body, "", "changes")

    parts = []
    for entry in entries:
        when = _dt.datetime.fromtimestamp(entry.get("at", 0))
        note = html.escape(str(entry.get("note") or ""))
        parts.append('<div class="change-entry">')
        parts.append(f'<div class="change-when">{when:%d %b %Y, %H:%M}</div>')
        if note:
            parts.append(f'<div class="change-note">{note}</div>')
        parts.append('<ul class="change-pages">')
        for item in entry.get("pages", []):
            slug = str(item.get("slug", ""))
            state = str(item.get("state", ""))
            label = html.escape(titles.get(slug, slug))
            if state == "removed":
                parts.append(f'<li><span class="doc-badge removed">removed'
                             f'</span>{label}</li>')
            else:
                parts.append(f'<li><span class="doc-badge {state}">{state}'
                             f'</span><a href="/docs/{slug}">{label}</a></li>')
        parts.append("</ul></div>")

    return shell("What changed", "".join(parts), "", "changes")


def page(slug: str) -> Optional[str]:
    if slug == "changes":
        return changes_page()

    # Noted as read, so its badge starts expiring. Opening a page and coming
    # straight back should not erase the mark that brought you to it, so the
    # badge survives a further day rather than going at once.
    tracker.mark_opened(slug)

    if slug.startswith("plugin/"):
        # A three-part slug is a plugin's own docs page; two parts is its
        # readme.
        extra = resolve_plugin_page(slug)
        if extra is not None:
            return _render_file(extra, slug,
                                "Shipped with this plugin - it arrives and "
                                "leaves with it.")
        return plugin_page(slug[len("plugin/"):])

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


def _render_file(path: Path, current: str, note: str) -> Optional[str]:
    try:
        markdown = path.read_text(encoding="utf-8")
    except OSError:
        return None
    body, toc = render(markdown)
    intro = (f'<p class="note">{html.escape(note)} See '
             f'<a href="/docs/bundled-plugins">Bundled plugins</a>.</p>')
    return shell(title_of(path), intro + body, toc_html(toc), current)


def plugin_page(slug: str) -> Optional[str]:
    path = resolve_plugin(slug)
    if path is None:
        return None
    try:
        markdown = path.read_text(encoding="utf-8")
    except OSError as e:
        return None

    body, toc = render(markdown)
    # A note above the readme, because a plugin readme is written by whoever
    # wrote the plugin and does not necessarily follow the house style.
    intro = ('<p class="note">Shipped readme for this bundled plugin. '
             'See <a href="/docs/bundled-plugins">Bundled plugins</a> for how '
             'it fits with the rest.</p>')
    return shell(title_of(path), intro + body, toc_html(toc), f"plugin/{slug}")


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


def neighbours(current: str) -> dict:
    """
    What comes before and after this page, two ways.

    `prev`/`next` step through everything including a plugin's sub-pages -
    which is what reading straight through wants. `prev_top`/`next_top` skip to
    the next thing that is not a sub-page, for somebody who has finished with a
    plugin and does not want its four sub-pages one at a time.

    Both are returned rather than one being chosen here: which is wanted is a
    property of the button pressed, not of the page.
    """
    flat, tops = [], []
    for slug, title, _ in nav_entries():
        flat.append((slug, title))
        tops.append((slug, title))

    for plugin_slug, (display, readme, pages) in sorted(
            plugin_docs().items(), key=lambda item: item[1][0].lower()):
        if readme is not None:
            flat.append((f"plugin/{plugin_slug}", display))
            tops.append((f"plugin/{plugin_slug}", display))
        for page_slug, title, _path in pages:
            # In the straight-through order but not the skipping one.
            flat.append((page_slug, title))

    def around(sequence):
        for index, (slug, _title) in enumerate(sequence):
            if slug == current:
                before = sequence[index - 1] if index > 0 else None
                after = (sequence[index + 1]
                         if index + 1 < len(sequence) else None)
                return before, after
        return None, None

    prev_one, next_one = around(flat)
    prev_top, next_top = around(tops)
    # A sub-page is not in `tops`, so it has no skipping neighbours of its own.
    # Its parent's do instead, which is what "skip the rest of this plugin"
    # means from inside one.
    if prev_top is None and next_top is None and "/" in current:
        parent = current.rsplit("/", 1)[0]
        prev_top, next_top = around(tops) if parent == current else (None, None)
        for index, (slug, _t) in enumerate(tops):
            if current.startswith(slug + "/") or slug == parent:
                prev_top = tops[index - 1] if index > 0 else None
                next_top = (tops[index + 1]
                            if index + 1 < len(tops) else None)
                break

    return {"prev": prev_one, "next": next_one,
            "prev_top": prev_top, "next_top": next_top}


def _nav_button(entry, direction: str, skip: bool) -> str:
    if not entry:
        return '<span class="page-nav-gap"></span>'
    slug, title = entry
    arrow = "\u2190" if direction == "prev" else "\u2192"
    label = html.escape(title)
    hint = "Skip to" if skip else ("Previous" if direction == "prev" else "Next")
    inner = (f'<span class="page-nav-hint">{arrow} {hint}</span>'
             f'<span class="page-nav-title">{label}</span>')
    if direction == "next":
        inner = (f'<span class="page-nav-hint">{hint} {arrow}</span>'
                 f'<span class="page-nav-title">{label}</span>')
    classes = "page-nav-link" + (" skip" if skip else "")
    return f'<a class="{classes}" href="/docs/{slug}">{inner}</a>'


def page_nav_html(current: str, position: str) -> str:
    """
    The previous/next row. `position` is "top" or "bottom".

    At the top only the way back, at the bottom only the way on. A page that
    opens with a Next button invites skipping it before it has been read, and
    one that ends with a Back button is offering to undo what somebody just
    did.
    """
    around = neighbours(current)
    if position == "top":
        parts = [_nav_button(around["prev"], "prev", False)]
        if around["prev_top"] and around["prev_top"] != around["prev"]:
            parts.append(_nav_button(around["prev_top"], "prev", True))
    else:
        parts = []
        if around["next_top"] and around["next_top"] != around["next"]:
            parts.append(_nav_button(around["next_top"], "next", True))
        parts.append(_nav_button(around["next"], "next", False))
    if not [p for p in parts if "page-nav-link" in p]:
        return ""
    return f'<nav class="page-nav {position}">' + "".join(parts) + "</nav>"


def sidebar_html(current: str) -> str:
    rows = []
    changes = tracker.load()

    # What changed, before anything else. It is the page somebody wants after
    # being handed a new build, and hunting for it in a group defeats that.
    recent = tracker.recent_count(changes)
    badge = f'<span class="nav-count">{recent}</span>' if recent else ""
    active = ' class="active"' if current == "changes" else ""
    rows.append(f'<a href="/docs/changes"{active}>What changed{badge}</a>')

    for group, entries in nav_groups():
        rows.append(f'<div class="nav-divider">{html.escape(group)}</div>')
        for slug, title, _ in entries:
            active = ' class="active"' if slug == current else ""
            mark = tracker.badge_for(slug, changes)
            rows.append(f'<a href="/docs/{slug}"{active}>'
                        f'{html.escape(title)}{mark}</a>')

    # Plugins get their own section at the end, one heading each.
    #
    # They used to hang off the Bundled plugins page, which only worked for
    # plugins that were bundled - a user plugin in plugins/ had nowhere to go.
    # Down here they are all equal, and the core pages above stay a fixed list
    # that does not change shape with whatever happens to be installed.
    plugins = plugin_docs()
    if plugins:
        rows.append('<div class="nav-divider">Plugins</div>')
        for plugin_slug, (display, readme, pages) in sorted(
                plugins.items(), key=lambda item: item[1][0].lower()):

            # The name is the link to the readme, not a label above a link
            # called "Overview". One less row, and it reads the same way the
            # core entries above it do.
            if readme is not None:
                target = f"plugin/{plugin_slug}"
                active = ' class="active"' if target == current else ""
                rows.append(f'<a href="/docs/{target}"{active}>'
                            f"{html.escape(display)}</a>")
            else:
                # Nothing to link to, so it stays a heading over its pages.
                rows.append(f'<div class="nav-plugin">{html.escape(display)}</div>')

            for page_slug, title, _path in pages:
                sub_active = " active" if page_slug == current else ""
                rows.append(f'<a class="sub{sub_active}" '
                            f'href="/docs/{page_slug}">{html.escape(title)}</a>')
    return "".join(rows)


_SEARCH_CACHE: dict = {"stamp": None, "data": None}


#Underscore is deliberately NOT in here. It is markdown emphasis, but it is
#also half the identifiers in this codebase - KEEP_ASPECT, has_chosen_size -
#and those are the exact strings somebody searches for. Stripping it made them
#unfindable, which is worse than the occasional stray underscore in a result.
_MD_NOISE = re.compile(r"[`*>#|\[\]]+")
_SPACES = re.compile(r"\s+")


def _read(path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _searchable(text: str, limit: int = 1400) -> str:
    """Markdown stripped to words, capped so the payload stays sane."""
    cleaned = _SPACES.sub(" ", _MD_NOISE.sub(" ", text)).strip()
    return cleaned[:limit]


def search_index() -> list:
    """
    Every section of every page, as
    [page_slug, page_title, heading, anchor, body_text].

    The body is included so a search finds the **words in a page**, not only
    the words somebody happened to put in a heading. Thirty pages in, the thing
    being looked for is usually a snippet or a term buried in a paragraph, and
    remembering which heading it lived under is exactly what nobody can do.

    Markdown syntax is stripped and whitespace collapsed before indexing:
    matching on backticks and pipe characters finds nothing useful and makes
    the payload larger for it.

    Cached against the newest mtime in docs/, so editing a file during
    development still refreshes it without a restart, and a normal run builds
    it once rather than re-reading 26 files on every page load.
    """
    try:
        stamp = max((p.stat().st_mtime for p in DOCS_DIR.glob("*.md")), default=0)
    except OSError:
        stamp = 0
    if _SEARCH_CACHE["stamp"] == stamp and _SEARCH_CACHE["data"] is not None:
        return _SEARCH_CACHE["data"]

    rows = []
    for slug, page_title, filename in nav_entries():
        try:
            text = (DOCS_DIR / filename).read_text(encoding="utf-8")
        except OSError:
            continue
        heading, anchor, body = page_title, "", []

        def flush():
            rows.append([slug, page_title, heading, anchor,
                         _searchable(" ".join(body))])

        in_code = False
        for line in text.splitlines():
            fence = line.lstrip().startswith("```")
            if fence:
                in_code = not in_code
                # Code is indexed too. A snippet is one of the commonest things
                # to be hunting for, and the fence markers are all that need
                # dropping.
                continue
            match = None if in_code else re.match(r"^(#{2,4})\s+(.*)$",
                                                  line.strip())
            if match:
                flush()
                heading = match.group(2).strip().replace("`", "")
                anchor = slugify(match.group(2).strip())
                body = []
                continue
            if line.strip():
                body.append(line.strip())
        flush()

    # Plugin docs go in the same shape, body and all. They were headings only,
    # which meant the one place a plugin explains itself was the one place a
    # search could not reach.
    for plugin_slug, (display, readme, pages) in plugin_docs().items():
        if readme is not None:
            rows.append([f"plugin/{plugin_slug}", "Plugins", display, "",
                         _searchable(_read(readme))])
        for page_slug, title, path in pages:
            text = _read(path)
            rows.append([page_slug, display, title, "", _searchable(text)])
            heading, anchor, body = title, "", []
            for line in text.splitlines():
                match = re.match(r"^(#{2,4})\s+(.*)$", line.strip())
                if match:
                    if body:
                        rows.append([page_slug, display, heading, anchor,
                                     _searchable(" ".join(body))])
                    heading = match.group(2).strip().replace("`", "")
                    anchor = slugify(match.group(2).strip())
                    body = []
                elif line.strip():
                    body.append(line.strip())
            if body:
                rows.append([page_slug, display, heading, anchor,
                             _searchable(" ".join(body))])

    _SEARCH_CACHE.update(stamp=stamp, data=rows)
    return rows


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
  <input class="filter" type="search" placeholder="Search docs" aria-label="Search docs">
  <nav class="nav">{sidebar_html(current)}</nav>
  <div class="results" hidden></div>
</aside>
<main>
  {page_nav_html(current, "top")}
  <article>{body}</article>
  {page_nav_html(current, "bottom")}
</main>
{toc}
<script type="application/json" id="search-index">{json.dumps(search_index())}</script>
<script>{SCRIPT}</script>
</body>
</html>"""


STYLE = """
:root {
  /* Chromium runs with forceDarkModeEnabled so ordinary sites come out dark.
     A page declaring itself dark is skipped; one that does not is inverted
     into a white rectangle, and this viewer is read on the panel itself. */
  color-scheme:dark;
  --bg:#151517; --panel:#1c1c1f; --line:#2c2c31; --text:#e6e6e8;
  --muted:#9a9aa2; --accent:#2ff08e; --accent-dim:#1faf68; --code:#111114;
  --inline:#7fe0b0;
  /* Syntax tokens. Kept away from the accent green so a keyword is never
     mistaken for a link. */
  --t-kw:#8ab4ff; --t-str:#e3b673; --t-num:#d7a3ea; --t-com:#6f6f78;
  --t-bui:#5ed4a8; --t-fn:#f0d67a; --t-dec:#c69cf0; --t-con:#e88f8f;
  --t-key:#8ab4ff; --t-flg:#e3b673;
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

/* -- What changed --------------------------------------------------------- */
.nav-count {
  float:right; background:var(--accent); color:#10281c; border-radius:9px;
  padding:0 7px; font-size:11px; font-weight:700; line-height:18px;
  min-width:18px; text-align:center;
}
.doc-badge {
  display:inline-block; margin-left:8px; padding:1px 7px; border-radius:8px;
  font-size:10px; font-weight:700; text-transform:uppercase;
  letter-spacing:.04em; vertical-align:middle;
}
.doc-badge.new     { background:rgba(47,240,142,.18); color:#7ef0b4; }
.doc-badge.updated { background:rgba(120,170,255,.18); color:#9dc0ff; }
.doc-badge.removed { background:rgba(224,138,138,.18); color:#e8a6a6; }

.change-entry { border-left:2px solid var(--line); padding:2px 0 2px 16px;
  margin:0 0 22px; }
.change-when { color:var(--muted); font-size:13px; }
.change-note { margin:4px 0 8px; font-size:15px; }
.change-pages { list-style:none; padding:0; margin:0; }
.change-pages li { padding:3px 0; font-size:15px; }
.change-pages .doc-badge { margin:0 8px 0 0; }

/* -- Previous / next ------------------------------------------------------ */
.page-nav { display:flex; gap:10px; flex-wrap:wrap; margin:0 0 22px; }
.page-nav.bottom { margin:34px 0 0; }
.page-nav-link {
  flex:1 1 220px; min-width:0; display:block; padding:10px 14px;
  border:1px solid var(--line); border-radius:10px; background:var(--card);
  text-decoration:none;
}
.page-nav-link:hover { border-color:var(--accent); text-decoration:none; }
/* The skipping one steps back, so the ordinary next page stays the obvious
 * thing to press and the shortcut is there when it is wanted. */
.page-nav-link.skip { flex:0 1 auto; background:transparent; opacity:.72; }
.page-nav-hint { display:block; color:var(--muted); font-size:12px; }
.page-nav-title { display:block; color:var(--text); font-size:15px;
  font-weight:600; overflow:hidden; text-overflow:ellipsis;
  white-space:nowrap; }
.page-nav-gap { flex:1 1 220px; }

.results a small {
  display:block; color:var(--muted); font-size:12px; line-height:1.5;
  margin-top:3px; overflow:hidden;
}
.results a mark { background:rgba(47,240,142,.22); color:var(--text);
  border-radius:3px; padding:0 2px; }
.nav a.active { background:#26262b; color:var(--text); border-left-color:var(--accent); }
.nav a.sub {
  font-size:13.5px; padding-left:22px; color:#7f7f88;
  border-left:2px solid var(--line); margin-left:9px; border-radius:0 7px 7px 0;
}
.nav a.sub:hover { color:var(--text); }
.nav a.sub.active { color:var(--text); border-left-color:var(--accent); background:#26262b; }
.nav-divider {
  color:var(--muted); text-transform:uppercase; letter-spacing:.09em;
  font-size:10.5px; margin:20px 0 4px; padding:0 10px;
  border-top:1px solid var(--line); padding-top:14px;
}
.nav-plugin {
  color:var(--text); font-size:14px; font-weight:600;
  margin:10px 0 2px; padding:0 10px;
}

.results { display:flex; flex-direction:column; gap:1px; margin-top:4px; }
.results-title {
  color:var(--muted); text-transform:uppercase; letter-spacing:.09em;
  font-size:10.5px; margin:12px 0 6px; padding-left:10px;
}
.results a {
  display:flex; flex-direction:column; gap:1px; padding:6px 10px;
  border-radius:7px; border-left:2px solid transparent; color:var(--muted);
}
.results a:hover { background:#26262b; text-decoration:none; }
.results a span { color:var(--text); font-size:14px; }
.results a em { font-style:normal; font-size:11.5px; color:#6f6f78; }
.results .empty { color:var(--muted); font-size:13.5px; padding:10px; }

.note {
  padding:9px 14px; margin:0 0 20px; border-radius:8px;
  background:#1a1a1e; border:1px solid var(--line); color:var(--muted);
  font-size:14px;
}

main { min-width:0; padding:44px 52px 96px; }
article { max-width:820px; }

h1,h2,h3,h4 { line-height:1.25; scroll-margin-top:24px; }
h1 { font-size:32px; margin:0 0 6px; }
h2 { font-size:23px; margin:44px 0 12px; padding-top:20px; border-top:1px solid var(--line); }
/* Sections separated by an explicit "---" already have a rule above them; the
   heading must not draw a second one directly under it. */
hr + h2 { border-top:none; padding-top:0; margin-top:34px; }
h3 { font-size:18px; margin:28px 0 8px; color:#d2d2d6; }
.anchor { color:var(--line); margin-left:-20px; padding-right:8px; opacity:0; font-weight:400; }
h2:hover .anchor, h3:hover .anchor { opacity:1; }

p { margin:0 0 15px; }
strong { color:#f2f2f4; font-weight:600; }
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
  color:var(--inline); border:1px solid #303036;
}
/* Inside a link the link colour wins, or an inline-code link stops looking
   clickable. Same in headings, where the heading colour carries the weight. */
a code { color:inherit; border-color:transparent; }
h1 code, h2 code, h3 code { color:inherit; font-size:0.92em; }
th code { color:var(--inline); }

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
.code pre code {
  background:none; padding:0; border:none; color:var(--text);
  font-size:13.5px; line-height:1.55;
}
.t-kw  { color:var(--t-kw); }
.t-str { color:var(--t-str); }
.t-num { color:var(--t-num); }
.t-com { color:var(--t-com); font-style:italic; }
.t-bui { color:var(--t-bui); }
.t-fn  { color:var(--t-fn); }
.t-dec { color:var(--t-dec); }
.t-con { color:var(--t-con); }
.t-key { color:var(--t-key); }
.t-flg { color:var(--t-flg); }

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

/* The panel's built-in browser injects these too, but the docs are read from
   a desktop browser just as often and a white scrollbar on this palette is the
   brightest thing on the page. */
::-webkit-scrollbar { width:12px; height:12px; }
::-webkit-scrollbar-track { background:rgba(0,0,0,.25); }
::-webkit-scrollbar-thumb {
  background:rgba(255,255,255,.2); border-radius:6px;
  border:3px solid transparent; background-clip:content-box;
}
::-webkit-scrollbar-thumb:hover { background-color:rgba(255,255,255,.32);
  background-clip:content-box; }
::-webkit-scrollbar-corner { background:transparent; }
* { scrollbar-color: rgba(255,255,255,.22) rgba(0,0,0,.25); scrollbar-width: thin; }

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
var results = document.querySelector('.results');
var nav = document.querySelector('.nav');
var indexNode = document.getElementById('search-index');
var index = indexNode ? JSON.parse(indexNode.textContent) : [];

if (filter) {
  filter.addEventListener('input', function () {
    var needle = filter.value.trim().toLowerCase();

    if (!needle) {
      nav.hidden = false;
      results.hidden = true;
      document.querySelectorAll('.nav a').forEach(function (link) {
        link.style.display = '';
      });
      return;
    }

    // Page titles first: an exact page is almost always what was wanted, and
    // burying it under twenty section matches would be worse than no search.
    var pageHits = 0;
    document.querySelectorAll('.nav a').forEach(function (link) {
      var hit = link.textContent.toLowerCase().indexOf(needle) !== -1;
      link.style.display = hit ? '' : 'none';
      if (hit) { pageHits++; }
    });
    nav.hidden = pageHits === 0;

    // A heading match outranks a body match. Somebody typing "threading"
    // wants the section called that, not the twelve paragraphs mentioning it.
    var headingRows = [], bodyRows = [];
    index.forEach(function (row) {
      if (row[2].toLowerCase().indexOf(needle) !== -1) {
        headingRows.push(row);
      } else if ((row[4] || '').toLowerCase().indexOf(needle) !== -1) {
        bodyRows.push(row);
      }
    });
    var rows = headingRows.concat(bodyRows).slice(0, 50);

    if (!rows.length) {
      results.hidden = pageHits > 0;
      results.innerHTML = pageHits > 0 ? '' : '<div class="empty">Nothing found.</div>';
      return;
    }

    function escapeHtml(text) {
      return text.replace(/[&<>"]/g, function (c) {
        return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c];
      });
    }

    // The words around the match, so a result can be judged without opening
    // it. A list of section names is not enough once the search reaches into
    // the body - the heading may say nothing about why it matched.
    function snippet(text, term) {
      var at = text.toLowerCase().indexOf(term);
      if (at === -1) { return ''; }
      var from = Math.max(0, at - 48);
      var piece = text.slice(from, at + term.length + 90);
      var out = escapeHtml(piece);
      var marked = escapeHtml(text.substr(at, term.length));
      out = out.replace(marked, '<mark>' + marked + '</mark>');
      return (from > 0 ? '&hellip;' : '') + out + '&hellip;';
    }

    var html = '<div class="results-title">' + rows.length +
               ' match' + (rows.length === 1 ? '' : 'es') + '</div>';
    rows.forEach(function (row) {
      var href = '/docs/' + row[0] + (row[3] ? '#' + row[3] : '');
      var body = snippet(row[4] || '', needle);
      html += '<a href="' + href + '"><span>' + escapeHtml(row[2]) +
              '</span><em>' + escapeHtml(row[1]) + '</em>' +
              (body ? '<small>' + body + '</small>' : '') + '</a>';
    });
    results.innerHTML = html;
    results.hidden = false;
  });

  // Escape clears, so the sidebar can be got back without reaching for the
  // mouse or selecting the text.
  filter.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      filter.value = '';
      filter.dispatchEvent(new Event('input'));
    }
  });
}

var menu = document.querySelector('.menu');
if (menu) {
  menu.addEventListener('click', function () {
    document.querySelector('.sidebar').classList.toggle('open');
  });
}
"""
