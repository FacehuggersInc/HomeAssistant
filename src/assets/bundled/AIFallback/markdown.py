from __future__ import annotations

import html
import re

# Qt's rich text engine is a subset of HTML 4 - no flexbox, no CSS grid, and
# stylesheet support inside a QTextBrowser is limited to inline attributes.
# Everything below stays within what it will actually render.

CODE_BG = "#101010"
CODE_BORDER = "#3a3a3a"
INLINE_BG = "#2a2a2a"
ACCENT = "#6fa8e0"
MUTED = "#9a9a9a"

_FENCE = re.compile(r"```([A-Za-z0-9_+-]*)\n(.*?)```", re.S)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?!\*)")
_STRIKE = re.compile(r"~~([^~]+)~~")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)[^)]*\)")
_BARE_URL = re.compile(r"(?<![\"'=>])\bhttps?://[^\s<>\"')]+")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_ULIST = re.compile(r"^\s*[-*+]\s+(.*)$")
_OLIST = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_RULE = re.compile(r"^\s*([-*_])\s*\1\s*\1[\s\1]*$")

_HEADING_SIZE = {1: 20, 2: 18, 3: 16, 4: 15, 5: 14, 6: 13}


def _inline(text: str) -> str:
    """Inline spans. Escapes first, so a reply containing HTML is shown, not run."""
    out = html.escape(text, quote=False)

    placeholders = []

    def stash(markup: str) -> str:
        placeholders.append(markup)
        return f"\x00{len(placeholders) - 1}\x00"

    out = _INLINE_CODE.sub(
        lambda m: stash(
            f'<span style="background-color:{INLINE_BG}; font-family:monospace;">'
            f'&nbsp;{m.group(1)}&nbsp;</span>'
        ), out)

    out = _IMAGE.sub(
        lambda m: stash(
            f'<img src="{html.escape(m.group(2), quote=True)}" '
            f'alt="{html.escape(m.group(1), quote=True)}">'
        ), out)

    out = _LINK.sub(
        lambda m: stash(f'<a href="{m.group(2)}" style="color:{ACCENT};">{m.group(1)}</a>'), out)

    out = _BARE_URL.sub(
        lambda m: stash(f'<a href="{m.group(0)}" style="color:{ACCENT};">{m.group(0)}</a>'), out)

    out = _BOLD.sub(r"<b>\1</b>", out)
    out = _ITALIC.sub(r"<i>\1</i>", out)
    out = _STRIKE.sub(r"<s>\1</s>", out)

    for index, markup in enumerate(placeholders):
        out = out.replace(f"\x00{index}\x00", markup)
    return out


def _code_block(language: str, body: str) -> str:
    escaped = html.escape(body.rstrip("\n"), quote=False)
    label = (f'<div style="color:{MUTED}; font-size:11px;">{html.escape(language)}</div>'
             if language else "")
    return (
        f'{label}'
        f'<table width="100%" cellpadding="8" cellspacing="0" '
        f'style="background-color:{CODE_BG}; border:1px solid {CODE_BORDER};">'
        f'<tr><td><pre style="font-family:monospace; margin:0;">{escaped}</pre></td></tr>'
        f'</table>'
    )


def to_rich_text(markdown: str) -> str:
    """
    Markdown to the HTML subset Qt renders.

    Written out rather than pulling in a markdown package: this needs to cover
    what a chat reply actually contains - code, emphasis, lists, links - and
    every general-purpose converter emits CSS that Qt ignores, which looks
    worse than handling the subset directly.
    """
    if not markdown:
        return ""

    blocks = []

    # Fenced code first, so nothing inside a block is treated as markup.
    position = 0
    for match in _FENCE.finditer(markdown):
        blocks.append(("text", markdown[position:match.start()]))
        blocks.append(("code", (match.group(1), match.group(2))))
        position = match.end()
    blocks.append(("text", markdown[position:]))

    out = []
    for kind, payload in blocks:
        if kind == "code":
            out.append(_code_block(*payload))
            continue

        list_open = None
        for line in payload.splitlines():
            stripped = line.strip()

            if not stripped:
                if list_open:
                    out.append(f"</{list_open}>")
                    list_open = None
                continue

            if _RULE.match(line):
                if list_open:
                    out.append(f"</{list_open}>")
                    list_open = None
                out.append(f'<hr style="border:1px solid {CODE_BORDER};">')
                continue

            heading = _HEADING.match(line)
            if heading:
                if list_open:
                    out.append(f"</{list_open}>")
                    list_open = None
                level = len(heading.group(1))
                out.append(f'<div style="font-size:{_HEADING_SIZE[level]}px;">'
                           f'<b>{_inline(heading.group(2))}</b></div>')
                continue

            quote = _QUOTE.match(line)
            if quote:
                if list_open:
                    out.append(f"</{list_open}>")
                    list_open = None
                out.append(f'<div style="color:{MUTED};">| {_inline(quote.group(1))}</div>')
                continue

            unordered = _ULIST.match(line)
            if unordered:
                if list_open != "ul":
                    if list_open:
                        out.append(f"</{list_open}>")
                    out.append("<ul>")
                    list_open = "ul"
                out.append(f"<li>{_inline(unordered.group(1))}</li>")
                continue

            ordered = _OLIST.match(line)
            if ordered:
                if list_open != "ol":
                    if list_open:
                        out.append(f"</{list_open}>")
                    out.append("<ol>")
                    list_open = "ol"
                out.append(f"<li>{_inline(ordered.group(2))}</li>")
                continue

            if list_open:
                out.append(f"</{list_open}>")
                list_open = None
            out.append(f"<div>{_inline(stripped)}</div>")

        if list_open:
            out.append(f"</{list_open}>")

    return "".join(out)


def to_speech(markdown: str) -> str:
    """
    Plain text for TTS. Code blocks are dropped rather than read out - nobody
    wants three lines of Python spoken aloud.
    """
    if not markdown:
        return ""
    text = _FENCE.sub(" (code shown on screen) ", markdown)
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _BOLD.sub(r"\1", text)
    text = _ITALIC.sub(r"\1", text)
    text = _STRIKE.sub(r"\1", text)
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.M)
    return " ".join(text.split())
