# Widgets

Widgets are **registered**, not handed over as live instances - the same shape
`sub.tiles` uses for tiles. The saved layout decides what sits on the page and
what waits in the widgets panel.

> **Provided by a plugin.** Everything on this page comes from
> `corewidgetsbundle`. Disable or remove it and these features go with it — the client
> itself has no widget framework. That is deliberate: see
> [Bundled plugins](bundled-plugins.md).
```python
register = sub_home.features().register_widget

register(ConfigurationBar)                 # always placed
register(DateTimeWidget, show_date=True)
register(StickyNote, placed=False)         # starts in the panel
```

A widget declares what it supports on the class:

| Attribute | Meaning |
|-----------|---------|
| `KEY`, `NAME`, `ICON`, `DESCRIPTION` | identity, and how it appears in the panel |
| `RESIZABLE` | offers a resize handle |
| `ROTATABLE` | offers a rotate handle |
| `FLOATABLE` | stays where dropped instead of snapping to an anchor |
| `REMOVABLE` | `False` pins it to the page - no delete button appears |
| `MULTIPLE` | `True` makes it a template: it stays in the panel and each **Add** places another copy |
| `MIN_W/H`, `MAX_W/H` | resize limits |

## Placing and moving

**Hold** a widget to lift it: it rises above everything else and gets a dashed
border with handles. Drag to move, drag the corner to resize, drag the arm
above it to rotate (snapping near every 15°), and tap the green tick to
finish. A **tap** without a drag calls `on_activate()` instead - that is how
the sticky note opens its editor.

On release, a `FLOATABLE` widget stays where it was dropped. Anything else
snaps to the nearest anchor.

### Removing one

A lifted widget with `REMOVABLE` set gets a red **delete** handle on its
mid-edge, alongside the other handles. One tap takes it off the page.

It is never destroyed. The instance goes back to the widgets panel with its
state intact, so placing it again brings back its text, size and rotation. The
removal is announced rather than silent, and the worst a mis-tap costs is one
drag to put it back — which is why there is no confirmation step in the way.

The handle sits on the left mid-edge by preference and flips to the right when
the left would hang off the view. Every corner is already spoken for by a
handle that may or may not be present depending on the widget, and a widget
anchored hard against the left edge would otherwise have half its delete
button clipped away.

Everything - anchor, position, size, rotation, offset, and whether a widget is
placed at all - is saved per page to **`widget_layout.json` in the user data
directory**.

Saving is debounced: every mutation calls `schedule_save()` rather than
writing directly, so no interaction path can forget to persist and a drag
firing hundreds of move events still writes once. `hideEvent` and
`closeEvent` flush anything still pending.

Saves **merge** rather than replace. If the page is ever rebuilt - a plugin
reload, a second construction - a fresh framework with an empty registry would
otherwise write its blank state over everything already there.

Both the save and the load log at `debug` level with the file path and a
count, so a layout that is not sticking can be diagnosed from the log.

Not into the plugin's `settings.json`. That file ships with the app, so
unpacking a new build over an install replaces it and silently resets every
position. Layout is user data and belongs with it.

## Transparency

The view is made transparent with a transparent background brush and a
viewport that does not fill itself - **not** `WA_TranslucentBackground`.

That attribute is documented for top-level windows. On a child widget it makes
Qt give the widget its own backing surface, and under a Wayland compositor
that surface is created at the size the widget had when the attribute was set.
The framework is built during plugin load, before it has any real geometry, so
the surface is tiny and never grows: the scene then composites only into that
original corner, while the selection chrome - painted straight onto the
viewport - keeps drawing everywhere. Borders and handles in the right places,
widget content only near the origin.

## Sizing, and the viewport

`WidgetFramework` fills its **parent page**. Always. It is a child widget
covering the page, so it follows the same rule any other child would.

Consulting the window instead looks reasonable and is a trap. The window and
the page are sized from different places and disagree at several points during
startup; whenever they do, the framework shrinks to the window while the page
stays large, and everything lands in a smaller rect anchored top-left and
clipped to it. If the page is ever larger than the window, that is the page's
business: sub-pages already re-apply the window size when it changes.

The scene is anchored **top-left**, not centred. `QGraphicsView` defaults to
`AlignCenter`, which centres the whole scene inside the viewport whenever the
two differ in size by any amount - every widget position is then offset by
half the difference, and one placed at the page's bottom edge is pushed past
it. The positioning model here assumes scene `(0,0)` is viewport `(0,0)`, so
that is set explicitly, and the scroll offset is pinned to zero for the same
reason.

There is a second, separate trap in the same area. `QGraphicsView` only
resizes its **viewport** once the whole widget chain has been shown, and the
viewport is what the scene is painted on. The framework is created, given its
geometry and filled with widgets during plugin load, so the view reports the
full page size while its viewport is still at its default - and only that
top-left corner of the scene is rendered. The framework's geometry does not
change afterwards, so no resize event arrives to correct it.

`update_geometry()` therefore resizes the viewport explicitly rather than
waiting for Qt, and `showEvent()` runs the first layout, since `setGeometry()`
before `show()` delivers no resize event at all.

Every layout pass logs its numbers at `debug` level - page, window, view,
viewport and device pixel ratio - so a layout that looks wrong can be
diagnosed from the log rather than guessed at.

## How widgets are laid out

Widgets are **ordinary child widgets**. Anchored ones sit inside an
`_AnchorZone`, a `QWidget` holding a column of row layouts, and the framework
positions each zone against the page edges. Floating ones are direct children
positioned with `move()`.

Plain widgets rather than a `QGraphicsView` scene. Wrapping each widget in a
`QGraphicsProxyWidget` carries hit-testing through a rotation, which is
tempting, but a proxy does not composite **child widgets** on the target
hardware: anything built from `QLabel`s renders blank, while self-painted
widgets and the selection chrome drawn straight onto the view are fine. That
split - own painting works, children do not - is the discriminator if you ever
see it.

### Rotation

Paint-only, and opt-in. A `QWidget` has no transform, so a widget that
rotates has to draw itself rotated:

```python
def paintEvent(self, event):
    painter = QPainter(self)
    self.apply_rotation(painter)          # provided by Widget
    content_w, content_h = self.content_size()
    ...                                   # draw at content size
```

A widget built from child widgets cannot rotate - its children would keep
painting square - which is why `ROTATABLE` is declared rather than free.
`Widget.contains_point()` inverse-transforms a hit test, so a rotated widget
is still clickable where it looks.

**Size and content size are different things once a widget rotates.** A WxH
rectangle turned by an angle spans `W|cos| + H|sin|` across, so the widget
grows to that bounding box and the content stays centred inside it at
`content_size()`. Without that the corners are clipped off by the widget's
own edges. `apply_rotation()` leaves the origin at the content, so a widget
just draws from `(0, 0)` as usual. The layout saves the **content** size, not
the rotated box.

### Placing and ordering

While a widget is being dragged, a green bar shows the slot it will drop into
and names the anchor. The label sits **beside** the bar, pointing inwards;
above the bar would put a top-anchored indicator's text off the top of the
screen, and clip a corner one either way. Position within a row is decided by the dragged
widget's centre against the centres of the widgets already there, so a widget
can be dropped to the left or right of an existing one rather than always
landing at the end.

Floating widgets are clamped to the same **page margin** the anchor zones
use, so one cannot sit flush against an edge -
while every anchored widget keeps its margin.

**Nudging.** An anchored widget gets an extra handle that offsets it from
wherever its anchor put it, and a second handle - shown only once there is an
offset - that resets it. The offset is saved with the layout.

A nudged widget cannot stay in its row: the layout would undo the move on its
next pass, and its row would clip it. It leaves a same-size placeholder
behind, so the row spacing stays correct and there is a reference point to
offset from. `clear_offset()` swaps it back.

Holding a widget only **selects** it. The lift out of the zone happens on the
first actual drag - lifting on hold disturbed the row the moment you touched
it, and left the offset handle with no slot to work from.

Anchored widgets can resize too, if they declare `RESIZABLE`. They stay in
their zone: the resize sets a **fixed** size, which is what a layout honours -
a plain `resize()` is undone on the next layout pass.

### Coordinates

Every position the framework works with goes through `_frame_pos(widget)`,
which returns the widget's top-left in **framework** coordinates.

Never use `widget.pos()` for this. An anchored widget sits inside a zone's
row, so `pos()` is row-relative and usually `(0,0)`. Mixing that with a
framework-space mouse position makes the grab offset equal the click point -
so the first drag step moves the widget to `(0,0)` and the selection border
draws somewhere else entirely.

The same applies to hit testing, the handle rects and anchor snapping.

### The selection chrome

Handles are 44px with another 12px of slop around them, sized for a finger
rather than a cursor. Which ones appear depends on what the widget declares:

| Handle | Where | Shown when |
|---|---|---|
| **commit** — green tick, finishes editing | top-right | always |
| **resize** — diagonal arrow | bottom-right | `RESIZABLE` |
| **rotate** — arc on an arm above the widget | top-centre | `ROTATABLE` |
| **offset** — four-way arrow, nudges off the anchor | bottom-left | anchored or already offset |
| **reset** — arrow curling back | top-left | the widget has an offset |
| **delete** — red bin, returns it to the panel | left mid-edge | `REMOVABLE` |

The delete handle flips to the right mid-edge when the left would fall outside
the view. It is a bin rather than an X on purpose — an X reads as "close", and
the commit tick beside it already means done.

Child widgets paint over their parent, so the dashed border and handles
cannot be drawn in the framework's own `paintEvent` - they would sit
underneath the widget they describe. They are painted onto a raised,
mouse-transparent overlay through an event filter, which keeps them on top
without needing a class of its own.

### Re-fitting

A `QWidget` does not resize itself when its content grows; its layout
arranges children within whatever size it has. `tick_widgets()` notices and
repairs it once a second.

A row widget is shown explicitly when created. A row built while its zone is
not yet visible stays hidden otherwise, and the widget inside it only appears
once some later event forces a repaint - so the first drop after startup
appears to do nothing until the next click.

Making a new size stick takes more than `resize()`. An anchored widget lives
inside a zone's layout - the chain is zone -> row -> widget - and every layout
up that chain has to be invalidated, or the next layout pass puts the old size
straight back.

`_relayout_zone_of()` does that walk, and it stops at the zone **or at the
framework**. A floating widget has no zone ancestor, so an unbounded walk ran
off the end into the page and the window and called `adjustSize()` on both -
resizing them mid-drag. That showed up as transparent artifacts and a
glitching window while resizing a sticky note.

A widget that wants to be transparent should set `WA_NoSystemBackground`, not
`WA_TranslucentBackground`. The latter is for top-level windows; on a child it
stops the background being cleared between paints, so repeated resizing leaves
the previous frames behind.

For the grid-based equivalent that lives on `sub.tiles`, see
[Tiles](tiles.md) - tiles have the same delete and resize handles, plus size
variants that swap layout as the tile grows.

## Writing a widget

```python
from datetime import datetime

from PyQt6.QtWidgets import QVBoxLayout, QLabel
from PyQt6.QtCore import Qt

from src.ui.widget import Widget
from src.styling import make_font, SIZES, set_style


class CountdownWidget(Widget):

    KEY         = "countdown"
    NAME        = "Countdown"
    ICON        = "mdi.timer-outline"
    DESCRIPTION = "Days until a date you set."

    RESIZABLE = True
    ROTATABLE = False        # composed from child widgets, so it cannot rotate
    FLOATABLE = True
    REMOVABLE = True
    MULTIPLE  = False        # one instance; True makes it a template

    MIN_W, MIN_H = 160, 90
    MAX_W, MAX_H = 480, 260
    DEFAULT_ANCHOR = "top-right"

    def __init__(self, client, key=None, **kwargs):
        super().__init__(client=client, key=key or self.KEY,
                         width=220, height=120, **kwargs)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.days = QLabel("--")
        self.days.setFont(make_font(SIZES.L2, bold=True))
        self.days.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(self.days, "common", "text-strong")
        layout.addWidget(self.days)

        self.caption = QLabel("days to go")
        self.caption.setFont(make_font(SIZES.S1))
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_style(self.caption, "common", "text-muted")
        layout.addWidget(self.caption)

        self.start_tick(1000)     # once a second is plenty

    def tick(self) -> None:
        target = datetime(2027, 1, 1)
        self.days.setText(str(max(0, (target - datetime.now()).days)))
```

### Class attributes

The framework reads these off the **class**, so a widget can be listed in the
panel without ever being placed.

| Attribute | Meaning |
|---|---|
| `KEY` | Unique. How the layout is saved. |
| `NAME` | Shown in the widgets panel. |
| `ICON` | An `mdi.` name for the panel entry. |
| `DESCRIPTION` | One line, shown in the panel. |
| `RESIZABLE` | Whether the resize handles appear. |
| `ROTATABLE` | Requires the widget to paint itself — see [Rotation](#rotation). |
| `FLOATABLE` | Whether it can be dropped anywhere rather than snapped to an anchor. |
| `REMOVABLE` | Whether a delete handle is offered. |
| `MULTIPLE` | Template: stays in the panel, and Add makes another copy. |
| `MIN_W`/`MIN_H`, `MAX_W`/`MAX_H` | Resize bounds. |
| `DEFAULT_ANCHOR` | Where it lands the first time. |

`MULTIPLE = True` is what makes a widget a template rather than a singleton.
`register()` returns `None` for one, because no instance is created — the panel
offers it and each Add builds a copy with its own key and its own saved state.
`StickyNote` is the worked example.

### Composed or self-painted

A widget built from child widgets (`QLabel`, `QPushButton`) is the easy case
and cannot rotate — children keep painting square. A widget that draws itself
in `paintEvent` can, which is why `ROTATABLE` is opt-in.

Self-painting also avoids child hit targets falling out of alignment when the
widget is scaled. `StickyNote` is self-painted for exactly these two reasons.

### Ticking

`start_tick(ms)` starts a timer that calls your `tick()`. It runs on the UI
thread, so it must be cheap — anything slow goes on a thread and comes back
through `call_on_ui`. See [Threading](threading.md).

The framework calls `stop_tick()` for you when the widget is removed.

It also **suspends** every widget's tick when `sub.home` goes off screen, and
resumes it when the page comes back — see
[Off-screen sub-pages](pages.md#off-screen-sub-pages). `suspend_tick()` remembers
the interval and `resume_tick()` restores it plus one immediate tick, so nothing
shows a stale face after a swipe. A widget that was never ticking stays that
way, so there is nothing to opt into: write `tick()` as normal and it simply
stops running while nobody can see it.

Anything a widget does on a timer of its **own** — a `QTimer` it constructed
rather than `start_tick()` — is outside that and keeps running. Prefer
`start_tick()`.

---

## Registering a widget

Widgets are **registered, not constructed and added**. The saved layout decides
what is on the page and what waits in the panel — so registration is a
declaration that the widget exists, not an instruction to place it.

From a mixin on the sub-page's own `__init__`, which is what the bundled
plugin does:

```python
from src.mixins import mixin

class MyPlugin(Plugin):

    @mixin("sub.home.__init__", "myplugin", "after")
    def _add_widgets(self, sub_home, *args):
        register = sub_home.features().register_widget
        register(CountdownWidget)
```

Or from `built()`, reaching the page through the registry:

```python
def built(self):
    entry = self.client.PAGES.get_entry("#cwb_home_page")
    if entry and entry.instance:
        sub_home = entry.instance.sub_page_dict.get("home")
        if sub_home and sub_home.has_feature("register_widget"):
            sub_home.features().register_widget(CountdownWidget)
```

The mixin is preferable — it runs exactly when the page is built, whether that
is at startup or after a reload. See [Mixins](mixins.md).

### Features on `sub.home`

| Feature | Does |
|---|---|
| `register_widget(cls)` | Declare it. Returns the instance, or `None` for a `MULTIPLE` template. |
| `add_widgets(...)` | Place an already-constructed widget. |
| `remove_widget(key)` | Take it off and out of the panel. |
| `toggle_widget_panel()` | Open or close the panel. |
| `widget_framework` | The `WidgetFramework` itself. |

### Cleaning up

```python
def unload(self, carryover=None):
    entry = self.client.PAGES.get_entry("#cwb_home_page")
    if entry and entry.instance:
        sub_home = entry.instance.sub_page_dict.get("home")
        if sub_home and sub_home.has_feature("remove_widget"):
            sub_home.features().remove_widget("countdown")
```

The page outlives your plugin. A widget left on it after unload keeps painting
and keeps ticking, from a module that is gone.

---

## Transient widgets

A widget placed by something happening rather than by the person arranging
their screen - a running timer, a note an API call asked for. They are placed
at an exact point or at random inside a quadrant, never overlap anything
already there, can dismiss themselves on a timeout, and are **never written to
`widget_layout.json`**: a widget that exists only while its reason exists must
not come back on the next launch.

```python
widget = framework.make_transient("sticky-note", text="Back at 6")
sub_home.features().show_transient(widget, quadrant="top-left", timeout=600)
```

The delete handle on one does not file it in the widgets panel - there is no
entry there to go back to. It calls `on_dismissed()` on the widget instead, so
whatever placed it can react; a timer uses that to stop the real countdown.

Provided by `corewidgetsbundle`. Full detail, and the timer service built on
it, are in that plugin's own documentation.

## Asking something before a widget is added

A `MULTIPLE` template may need to know something before a copy can be built -
which sticker, which feed. A widget class can define a chooser, and the panel
defers building the copy until it calls back:

```python
@classmethod
def choose_before_add(cls, client, then):
    client.dialog(MyDialog(client, on_chosen=lambda value: then(thing=value)))
```

Those keywords go to the widget's constructor. Cancelling simply never calls
back, so nothing is placed - which is the point of doing it this way rather
than placing an empty widget and expecting it to be edited afterwards.

`StickerWidget` uses this to open the sticker library; anything without a
chooser is added immediately, as before.

## Masks and hit testing

A `QWidget` mask clips **painting** as well as input. Mask a widget to only
its currently visible content and it will slide open drawing nothing.

The rule is that a widget claims the area it *can* paint in, not the area it
happens to be visible in right now. The overlay layer, the quick settings
passthrough layer and the widget chrome all follow it.

See [Dialogs and overlays](dialogs.md) for how the overlay mask decides where
clicks land.

## Events the framework does not want

`WidgetFramework` covers the entire page, so anything it consumes is lost -
including the swipe that changes sub-page. Press, move and release all call
`event.ignore()` and defer to the base class unless the gesture is actually
the framework's: a press that landed on a widget, or a drag with a widget
already selected.

## The configuration bar

`ConfigurationBar` carries the notification centre and the widgets-panel
button. It is `REMOVABLE = False` so it cannot be thrown away - otherwise
removing it would leave no way back into the panel - but it is `FLOATABLE`, so
it can be moved anywhere. It embeds the real `NotificationCenterWidget` rather
than reimplementing it, so history, the unread dot and the panel keep working.

## The sticky note

`StickyNote` is `MULTIPLE`, so it stays in the panel and every **Add** creates
another note with its own key (`sticky-note-1`, `sticky-note-2`, ...). Each is
saved separately with a `template` field recording where it came from, so they
all come back after a restart.

It resizes, rotates and floats, and is painted entirely in
`paintEvent` rather than composed from child widgets - so a rotated note stays
legible with no child hit targets to fall out of alignment. Tapping it opens
the keyboard dialog in body mode. Its text and colour ride along in
`layout_state()`, which is the pattern for any widget that needs to persist
more than geometry.

## Anything that emits needs a parent

A Qt object that emits on its own — `QMovie`, `QTimer`, `QPropertyAnimation` —
**must not outlive the widget it calls into.** A widget's C++ object can be
deleted while its Python wrapper is still alive, and an emitter held only by a
Python attribute keeps going: the callback then touches a deleted object, and
because that happens inside a Qt signal handler it **aborts the process**
rather than raising something catchable.

```python
movie = QMovie(path, parent=self)                       # dies with the widget
anim  = QPropertyAnimation(self, b"pos", self)          # third argument!
timer = QTimer(self)
```

`QPropertyAnimation` is the trap: its first argument is the **target**, not a
parent, so `QPropertyAnimation(tile, b"pos")` looks parented and is not.

Parenting is the fix; guarding the callback with `except RuntimeError` is the
belt to its braces, for a signal already queued when the widget went.

## And only from the UI thread

Qt objects may only be touched from the thread that made them. **No event
handler is on that thread**: `on_update` comes from the update loop and
`on_woke_assistant` from a thread the STT spawns, so anything reached from an
event, a worker or a skill has to go through `client.call_on_ui`.

Qt does not raise when this is got wrong — it **aborts the process**. There is
no traceback and nothing to catch, so a function that both spawns work and
touches a widget is worth reading twice.

## A size somebody chose

`place()` calls `_fit_to_content()`, which releases a widget's fixed size and
grows it to whatever its content needs. That is right for a widget pinned by its
constructor to a size its labels have since outgrown — it would otherwise clip
them with no way to tell from inside.

It is wrong for a **resizable** widget. That size is a decision: it was dragged
to, or restored from the layout saved the last time it was. And `place()` runs on
every page rebuild, so leaving the home page and coming back would hand the
widget a different width than it was left at.

`has_chosen_size()` tells the two apart. It is true once something has set the
size — a drag, or `apply_layout_state()` — and false while `content_size()` would
only be answering with whatever Qt happened to have laid the widget out at.
Qt's default is 640x480, which is not a width anybody picked and must not be
preserved as though it were.

So a resizable widget keeps its chosen size, growing only where the content
genuinely does not fit, and never past `MAX_W`/`MAX_H`. A widget that cannot be
resized behaves exactly as before.

**This is worth knowing because the saved file stays correct the whole time.**
`content_size()` holds the chosen number, so what is written to disk is the width
the person picked; only what is on screen differs. Checking the layout file to
diagnose it would have shown nothing wrong.
