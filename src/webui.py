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
         nav: str = "") -> str:
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
    )
