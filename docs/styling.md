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

`SIZES.S1` needs 24px on this panel. A guess of 18 costs the label its
descenders, and a two-line one loses half its second row.

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

Everything a phone sees comes from `src/webui.py`, and it has a page of its
own: **[Web UI](web-ui.md)**. It covers `page()`, `WebAssets` for a page
kept as files, the shared helpers, icons and templates.

The one rule worth repeating here, because it is a styling rule rather than a
plumbing one:

### Style plain elements, not classes

A page that uses `<button>`, `<h1>`, `<h2>`, `<section>`, `<label>` and
`.card` inherits the current look with nothing to edit. That is how every
served page stays consistent when the look changes.

`chrome_css()` styles `select` and `option` as well as `input`. Styling only
`input` is why one page's dropdown was the browser's own white control on an
otherwise dark page.

### The documentation viewer

`src/docs.py` carries its own stylesheet and its own palette. It is a reading
surface on a desktop rather than a control surface on a phone, and it is the
one page that does not take the chrome.
