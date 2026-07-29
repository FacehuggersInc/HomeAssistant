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
PALETTE = """ :root{--bg:#151517;--card:#1c1c1f;--line:#2c2c31;--text:#e6e6e8;
       --muted:#9a9aa2;--accent:#2ff08e;--bad:#e08a8a}"""


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


def back_button(token: str, label: str = "All pages", href: str = "/") -> str:
    """A styled back control for the top of a GUI page."""
    joiner = "&" if "?" in href else "?"
    return (f'<a class="back" href="{escape(href)}{joiner}'
            f'token={escape(token)}">{_CHEVRON}<span>{escape(label)}</span></a>')


def chrome_css() -> str:
    """Everything a page needs that is not its own layout."""
    return "\n".join((PALETTE, FIELD_CSS, BACK_CSS))
