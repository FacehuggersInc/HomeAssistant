# Styling

Widgets are styled with Qt stylesheets loaded from CSS files in
`src/assets/styles/`. Every `.css` file in that folder is discovered at
startup and keyed by its filename, so adding `myplugin.css` there makes it
available as the id `"myplugin"` with no registration step.


## `set_style(widget, id, clazz, object_tag=None, override=None)`

```python
from src.styling import set_style

set_style(self.card,  "settings", "setting-block")
set_style(self.title, "common",   "text-strong")
```

`id` is the stylesheet filename without the extension. `clazz` is a class
selector in that file, written `.setting-block` there and passed without the
dot.

Qt has no class selectors, so `set_style` compiles the rule into an object
selector. A widget with no `objectName` is given a generated one, which is why
you can call this on anything without setting up ids yourself.

### `override`

Per-call tweaks without a new class:

```python
set_style(button, "buttons", "icon-button",
          override={"*": {"border-radius": "20px"}})
```

`"*"` applies to the base selector. A pseudo-state key applies only to that
state:

```python
override={"*": {"background": "#222"}, ":hover": {"background": "#333"}}
```

### `object_tag`

Pass this when the generated selector is wrong for what you need - typically
when styling a whole class of widget rather than one instance:

```python
set_style(self.window, "main", "main-window", object_tag="QMainWindow")
```


## Writing a stylesheet

```css
/* A comment. Block and line comments are both stripped. */

.setting-block {
    background: rgba(255,255,255,10);
    border: 1px solid rgba(255,255,255,22);
    border-radius: 12px;
}

.setting-block:hover {
    background: rgba(255,255,255,16);
}

/* Qt sub-controls work too */
.my-slider::handle:horizontal {
    width: 32px;
    border-radius: 16px;
}
```

Selector lists spread over several lines are supported:

```css
.text-strong,
.text-important {
    color: #e6e6e8;
}
```

### One rule that will catch you

**A declaration must end with a semicolon.** The parser reads the file line by
line and buffers until it finds one, so a value wrapped across two lines is
fine - but a missing `;` runs two declarations together and both are lost.

```css
/* Fine - the value wraps, the declaration still terminates */
.bar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 #1faf68, stop:1 #2ff08e);
}
```

Qt discards a malformed rule without a word, so the symptom is a style that
simply does not appear.

### Styling a control means styling its sub-controls

A `QRadioButton` or `QCheckBox` draws its indicator natively **until a
stylesheet touches the widget**, at which point Qt stops and expects the
stylesheet to draw it. A rule that sets only colour and padding therefore
renders the control as a bare line of text — still checkable, possibly already
checked, with nothing on screen saying so:

```css
/* Not enough - the radio circle disappears entirely */
.dialog-choice { color: #f2f2f2; padding: 6px 4px; }

/* Style the indicator too */
.dialog-choice::indicator          { width: 22px; height: 22px; ... }
.dialog-choice::indicator:checked  { background: #2d6cc0; ... }
```

The same applies to `QComboBox::drop-down`, `QScrollBar::handle` and any other
sub-control: once you stylesheet the parent, the parts you did not mention are
yours to draw. `common.css` and `buttons.css` have worked examples.

On a wall panel it is worth going further and giving the whole row a background
and a `:checked` state, so an option reads as something to tap rather than a
small circle to aim at.


## Fonts and sizes

```python
from src.styling import make_font, SIZES

label.setFont(make_font(SIZES.S2))
label.setFont(make_font(SIZES.M1, bold=True))
```

The scale, in pixels:

|         |         |         |  |
|---------|---------|---------|--|
| `S1` 16 | `S2` 18 | `S3` 20 |  |
| `M1` 25 | `M2` 28 | `M3` 31 |  |
| `L1` 35 | `L2` 45 | `L3` 60 |  |

Use the scale rather than raw numbers. The panel is read from across a room,
and a one-off `QFont("Poppins", 14)` is the thing that looks wrong on a
different screen.

Fonts ship in `src/assets/fonts/` and are registered at startup. `FONT` is
Poppins Light, `FONT_BOLD` is Poppins Medium.


## Colours

```python
from src.styling import COLORS

COLORS.PRIMARY.LIGHT          # brand green
COLORS.DARK.BG                # panel background
COLORS.DARK.BGLIGHT
COLORS.DARK.BORDER.NORMAL
COLORS.DARK.TEXT.IMPORTANT
COLORS.DARK.TEXT.MUTED
```

`STYLES.H1` through `STYLES.I4` are ready-made size/bold/colour descriptors
for headings and body text.


## Shared classes worth knowing

From `common.css`:

| Class             | Use                                    |
|-------------------|----------------------------------------|
| `text-strong`     | Primary text.                          |
| `text-muted`      | Labels, captions, secondary text.      |
| `transparent`     | A container that should paint nothing. |
| `page-background` | The standard page fill.                |


## Backgrounds that do not paint

A plain `QWidget` subclass ignores a stylesheet `background` unless it derives
from `QFrame` **or** sets `WA_StyledBackground`:

```python
self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
```

This only shows up once the widget is parented into a translucent host such as
`OVERLAYS` - standalone it looks correct, so it is easy to ship by accident.

For a child widget that should let what is behind it show through, use
`WA_NoSystemBackground`. `WA_TranslucentBackground` is a top-level window
attribute; on a child it stops the background being cleared between paints and
leaves earlier frames smeared behind the current one.

## Action buttons

`ActionButton` sizes itself to its label. `MIN_WIDTH` is a **floor**, not a
width: it pads "Join" out so a short label does not make a stub of a button
beside a long one, and anything needing more asks for more.

That distinction matters because a `QPushButton` squeezed below its text
**clips** rather than shrinking the text. Treating the floor as the width showed
"Save and Retu" on the plugin pages.

`action_column()` follows the same rule. Its tray is at least `slots` buttons
wide so right edges line up down a list, and wider when what it holds needs it.
Rows in one list usually carry the same labels and still line up; a row that
genuinely needs more gets more, because a readable row slightly out of line
beats a tidy column of cut-off words.

## Fixed heights

A label's height comes from its font, never from a guess:

```python
label.setFont(make_font(SIZES.S1))
label.setFixedHeight(QFontMetrics(label.font()).height())
```

`SIZES.S1` needs 24px on this panel, and the guesses beside it were all 18 -
so every one of those labels lost its descenders, and a two-line one lost half
its second row. `check_text_fits.py` reads every `setFixedHeight` next to a
`make_font` and compares the two.

The same shape of mistake sizes a tile: a fixed icon height in a grid cell
around sixty pixels square leaves negative room for the text under it. Give the
text what it needs and let the picture take the rest.

## Scroll areas

Every scrolling surface the panel draws itself takes one sheet:

```python
from src.styling import style_scrollbar

scroll = QScrollArea()
scroll.setWidgetResizable(True)
set_style(scroll.viewport(), "common", "transparent")
style_scrollbar(scroll)
```

`style_scrollbar()` **appends**. `setStyleSheet` replaces, and `set_style()`
uses it - so a scroll area given the sheet and then styled for anything else
loses the scrollbar again and shows the platform's own, which on this palette
is a bright grey slab. Appending means the order does not matter.

A surface that hides its bar and drags instead needs neither:

```python
scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
QScroller.grabGesture(scroll.viewport(),
                      QScroller.ScrollerGestureType.LeftMouseButtonGesture)
```

Web pages are separate - Chromium draws its own, restyled in `webpage.py` and
`docs.py`.

## The web UI

Everything a phone sees comes from `src/webui.py`. `page()` builds a whole
document and the caller supplies only its content, so a served page carries no
copy of the parts every page shares.

```python
from src.webui import page, position_grid, POSITION_SCRIPT

def render_page(token, message=""):
    body = """
<section>
  <label for="name">Name</label>
  <input id="name" name="name">
</section>
"""
    return page(
        title="Something",
        heading="Something",
        blurb="What this page is for.",
        token=token, message=message,
        css=" .mine{color:var(--accent)}",
        body=body,
    )
```

| Argument                            | Meaning                                               |
|-------------------------------------|-------------------------------------------------------|
| `title`                             | The browser tab.                                      |
| `body`                              | The page's own markup.                                |
| `token`                             | The caller's token, for the back control.             |
| `heading` / `blurb`                 | The `h1` and the line under it.                       |
| `message` / `bad`                   | The status banner, and whether it reads as a failure. |
| `css`                               | Rules this page needs that the chrome does not carry. |
| `script`                            | JavaScript, placed at the end of the body.            |
| `back` / `back_label` / `back_href` | The back control, on by default.                      |

### Style plain elements, not classes

A page that uses `<button>`, `<h1>`, `<h2>`, `<section>`, `<label>` and
`.card` inherits the current look with nothing to edit. That is how every
served page stays consistent when the look changes.

`button[type=submit]` is the gradient primary and `button.danger` is the
destructive one. Neither needs a class of its own. A page's `css` is for what
is genuinely its own — a grid of sticker tiles, a row of duration presets. A
rule that restates `body`, `section`, `input` or the back control is a second
copy of a decision made once.

| Class     | Use                                         |
|-----------|---------------------------------------------|
| `.banner` | The status strip. Add `.bad` for a failure. |
| `.card`   | A bordered block.                           |
| `.hint`   | Small muted text under a field.             |
| `.empty`  | What a list says when it has nothing in it. |
| `.row`    | Fields side by side.                        |
| `.where`  | The nine-position picker — see below.       |

### The palette

|                           |                                             |
|---------------------------|---------------------------------------------|
| `--bg` `--card` `--card2` | Backgrounds, darkest first                  |
| `--line`                  | Borders                                     |
| `--text` `--muted`        | Foreground                                  |
| `--accent` `--accent2`    | Green and blue; gradients run between them  |
| `--warm` `--bad`          | Amber for a held state, red for destructive |
| `--glow`                  | The shadow under a primary button           |

`color-scheme: dark` is declared in the palette, so any page carrying the
chrome has it. Chromium runs with `forceDarkModeEnabled` so that ordinary
sites come out dark: a page that declares itself dark is skipped, and one that
does not is inverted into a white rectangle.

### Asking where something goes

`position_grid(selected)` draws the nine positions as the shape of the screen
and writes the choice to a hidden field. Include `POSITION_SCRIPT` once on any
page that uses it. The options come from `POSITIONS`, so a page cannot offer a
tenth or leave one out.

```python
body = f"""
<section>
  <label>Where it goes</label>
  {position_grid("top-right")}
</section>
"""
return page(title="Place it", body=body, token=token, script=POSITION_SCRIPT)
```

### Templates

A page rendered from `src/templates/` gets `chrome` and `back_button` from a
context processor, so a route passes neither.

```html
<style>
{{ chrome|safe }}
 .mine{color:var(--accent)}
</style>
...
<p>{{ back_button(token)|safe }}</p>
```

### Icons

Inline SVG, from `src/webicons.py`. A phone has no icon font and shipping one
for twenty glyphs is a megabyte for nothing. `mdi.rss` and `rss` are the same
request; an unknown name becomes a dot rather than a gap. Add a path to
`PATHS` when one is missing.

### The documentation viewer

`src/docs.py` carries its own stylesheet and its own palette. It is a reading
surface on a desktop rather than a control surface on a phone, and it is the
one page that does not take the chrome.
