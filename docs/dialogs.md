# Dialogs

`Client` exposes modal dialogs directly. All of them are thread-safe -- call
them from a Flask route, a voice handler, an event callback or any background
thread and they marshal onto the Qt thread themselves, the same way
`create_panel()` does.

```python
self.client.alert("Backup complete", "Wrote 412 files.")

self.client.confirm(
    "Delete backup?", "This cannot be undone.",
    on_confirm=wipe, destructive=True,
)

self.client.prompt(
    "Rename", "New name:",
    on_submit=lambda text: rename(text),
    default="untitled",
)

self.client.choose(
    "Theme", "Pick one", ["Dark", "Light"],
    on_choose=lambda value: set_theme(value),
)
```

| Method | Purpose |
|--------|---------|
| `alert(title, body, ...)` | Message with one dismiss button |
| `confirm(title, body, on_confirm=..., destructive=False)` | Yes/no |
| `prompt(title, body, on_submit=..., numeric=False, password=False)` | Text entry |
| `choose(title, body, options, on_choose=...)` | Pick one from a list |
| `progress(title, body)` | Status line, no buttons; returns the dialog |
| `dialog(widget)` | Show any `BaseDialog` you built yourself |
| `close_dialog()` | Close the topmost dialog |

Callbacks fire **after** the dialog closes, so a callback is free to open
another dialog without fighting the one it came from.

`prompt()` raises the on-screen keyboard on focus (numpad when
`numeric=True`), since the target hardware has no physical one. `choose()`
takes plain strings, or `(value, label)` pairs when the value you want back
differs from the text shown.

`progress()` returns the dialog so a worker thread can drive it:

```python
dlg = self.client.progress("Syncing", "Talking to the server...")
dlg.set_status("42 of 300")     # safe from any thread
self.client.close_dialog()
```

## The overlay hit mask

`OverlayManager` sets a mask built from the union of its visible children's
geometry. The mask is what decides where the overlay accepts clicks at all -
outside it, events fall through to the page underneath.

**A child that sets `WA_TransparentForMouseEvents` is excluded from the
mask.** Including it would create a dead zone: the overlay claims the area,
nothing inside it is willing to handle the click, and the event never reaches
the page. The voice bar sits bottom-centre, which is exactly where page
buttons tend to be.

Those children live on `OVERLAYS.passthrough` instead - a sibling layer that
is itself `WA_TransparentForMouseEvents`, so Qt skips it entirely during hit
testing. It carries its own mask, built from where its children *can* paint,
because a mask clips painting as well as input.

If you add an overlay widget that should not receive input, set that attribute
and `OverlayManager.add()` routes it there for you.

## Layering

Dialogs are registered in the `DIALOG` overlay layer, which sits above
`TOPMOST`. This matters: `OverlayManager._enforce_z_order()` raises every
widget registered in a layer, and a widget that is only reparented onto
`OVERLAYS` without being registered is invisible to it. Anything added to a
layer afterwards - a notification toast, the voice bar - would then be raised
over the dialog, hiding it and handing the next tap to the click blocker
underneath, which closed it.

If you build an overlay widget of your own, register it with
`OVERLAYS.add(layer, widget)` rather than calling `setParent()`.

## Building your own

Subclass `BaseDialog` (`src/ui/overlays.py`) and hand it to
`client.dialog()`:

```python
from src.ui.overlays import BaseDialog

class MyDialog(BaseDialog):
    def __init__(self, client):
        super().__init__(client, "Title", "Body text")
        self.content.addWidget(my_widget)
        self.add_button("Cancel", self.close, "secondary")
        self.add_button("Go", self._go, "primary")
```

`add_button` kinds are `primary`, `secondary`, `destructive` and `disabled`.
Helpers: `make_title`, `make_body`, `make_detail` (a muted block for lists and
paths), `add_scroll`, `clear_content`, `clear_buttons`, `center`.

**A dialog is destroyed when it closes.** `DialogManager.close()` releases its
backdrop and calls `deleteLater()`, because a dialog carries a blurred snapshot
of the page the size of itself and every caller in this codebase builds a fresh
instance per open. Build a new one each time rather than keeping one around.

If you genuinely need to keep and reopen the same instance, set `REUSABLE = True`
on the class and it will only be hidden and unparented.

**One thing to know if you build dialog widgets by hand.** `BaseDialog`
derives from `QFrame` *and* sets `WA_StyledBackground`, and both matter. A
plain `QWidget` subclass does not paint a stylesheet `background` without that
attribute -- but only once it is parented into `OVERLAYS`, which is
translucent. Rendered standalone it looks completely fine, so this is easy to
ship by accident and the symptom is floating text over the page. Anything you
parent into the overlay layer wants one or the other.


## Action sheets

`ActionSheet` is the row-actions dialog behind `row_menu()`: a title and a
column of full-width rows, **one tap each**.

Use this rather than a `QMenu`: a menu's items are one line of text tall and it
expects a press-drag-release, neither of which a finger performs.

Rows are 56px and full width. A destructive row is coloured as one. There is no
Select step - a tap acts.

The sheet closes **before** it calls the action, so an action that opens its own
dialog (Forget asks for confirmation) is not fighting this one on the way out.

## Refusing to close

A dialog can veto its own dismissal by defining `can_close()`:

```python
class MyDialog(BaseDialog):
    def can_close(self) -> bool:
        return self.form_is_valid()
```

`DialogManager.close()` asks before closing anything. That is deliberately the
check's home rather than the widget's own `close()`: the manager is the single
path every dismissal funnels through — the buttons, the click blocker behind
the dialog, and any plugin closing it directly. A guard on the widget only
covers the first of those, and a tap outside would sail past it.

Returning `False` should be accompanied by something on screen saying why. The
minimap disables its Done button and shows a line of text while its origin slot
is empty, so a refused tap is explained rather than simply ignored.


## Frosting

Dialogs and panels share the same treatment: a blurred snapshot of the page
behind them, with a dark wash over it so text stays readable on a bright
wallpaper.

`BaseDialog.refresh_backdrop()` is called from `showEvent` and `resizeEvent`,
both queued through `QTimer.singleShot(0, …)`. A dialog has no final position
until the overlay layer has centred it, and grabbing before that snapshots the
wrong region of the page.

`BLUR_RADIUS` on the class controls the strength.

**Idle does not run behind a dialog.** A dialog is a question waiting for an
answer, and `on_interaction_timeout` firing behind one lets an idle plugin
cover it or dismiss the page underneath while it is still being read. The
client treats an open dialog as continuous interaction, so the idle clock
restarts when the dialog closes.


## Panels

A `Panel` is a frosted sliding surface, used for the notification history, the
tile panel, the AI chat panel and [quick settings](quick-settings.md).

```python
from src.ui.overlays import Panel

panel = Panel(client, edge="right", width=Panel.DEFAULT_WIDTH, key="mypanel")
panel.add_content(my_widget)
panel.open_panel()
```

| Argument | Default | Meaning |
|---|---|---|
| `edge` | `"right"` | `"left"`, `"right"`, `"top"` or `"bottom"`. |
| `width` | 680 for left/right | `None` fills the axis less the margin. |
| `height` | `None` | Set it for a panel that does not reach the far edge. |
| `margin` | `0` | Inset from the screen edges. Non-zero makes it float. |
| `radius` | `None` | CSS border-radius. Floating panels default to `14px`. |
| `blur_radius` | 28 | Backdrop blur strength. |
| `animation_speed` | 220 | Slide duration in ms. |
| `key` | `None` | Identifier, for `client.create_panel()`. |
| `destroy_on_close` | `False` | Whether closing deletes it. |

With `margin=0` a panel is flush to its edge and spans the full cross axis.
With a margin it floats as a card, and gets a border all the way round rather
than a single seam.

Use `Panel.DEFAULT_WIDTH` rather than a number when you want to match the
other side panels — several places share it, and a literal will drift.

`open_panel()`, `close_panel()` and `toggle()` drive it. The frosted effect is
a blurred snapshot of the page behind, refreshed on open and on resize, so it
costs nothing while the panel is closed.

### Shared controls

`src/ui/controls/stepper.py` is a big number with up and down, sized for a
finger rather than a spinbox. A value chosen on it cannot be out of range,
so nothing downstream has to validate one.

`DurationPickerDialog` is built on it, for anything that needs a length of
time. `ItemGridDialog` (`src/ui/grid_dialog.py`) is the searchable,
sortable grid of things to pick one of - see
[Stickers](/docs/plugin/corewidgetsbundle/stickers) for the full argument list.

### Enabling a button after it exists

`add_button()` picks its style at construction. Toggling with `setEnabled()`
alone leaves a button looking primary or destructive while refusing every tap,
which reads as broken rather than unavailable.

`set_button_state(button, enabled, kind)` sets both the state and the style,
and is what to use for anything that becomes available once a selection is
made.

### What a slide costs

Two things made opening a panel slower than it looks, and both are worth
knowing if you add another animated overlay.

**The blur is done at a third scale.** A gaussian blur costs roughly its pixel
count, and a full-width panel on a 1080p screen is about 640,000 pixels - paid
on the UI thread before the panel can appear. `_blur_pixmap()` shrinks the
snapshot, blurs that, and scales it back: nine times less work, and the detail
it throws away is exactly what the blur exists to destroy.

**The hit mask is held for the length of the slide.** A `QPropertyAnimation` on
`pos` emits a Move event every frame, and each one scheduled a full mask
recompute - `findChildren` across the overlay, a `QRegion` union, and `setMask`
on a full-screen widget, which forces everything above the page to repaint.
Thirteen times across a 220ms slide.

`OVERLAYS.hold_mask(sweep)` / `release_mask()` suppress that, and `Panel`
already brackets its own animations with them.

**Pass the swept rect.** A mask clips painting as well as input, so freezing
the mask at the panel's starting position masks the panel out of every frame it
moves through - it slides in drawing nothing and appears only when the mask
catches up at the end. `sweep` is the union of where the widget is and where it
is going; it is added to the mask before updates stop, so the whole path stays
paintable.

Holds are counted, so overlapping panels behave, and a watchdog releases a hold
whose owner never did - a lost release would otherwise freeze painting and hit
testing for the life of the process.

If you animate something of your own on the overlay layer, bracket it the same
way and pass the ground it covers.

## Dismissing a panel by pressing beside it

```python
client.create_panel(content, dismiss_on_outside_click=True)
```

A panel with no close button and nothing else to press cannot be got rid of at
all, so anything opened deliberately should set this.

**It cannot be done with an event filter on the overlay.** The overlay masks
itself to where its children can paint, and a `QWidget` mask clips **input** as
well as painting — so a press beside the panel never reaches the overlay, it
goes straight to the page underneath. What catches it is a `_PanelScrim`: a
sibling widget covering the whole overlay, stacked under the panel, which is
inside that mask by definition because the mask is built from its children's
geometry.

The scrim is faintly darkened, so it is visible that presses are going
somewhere. The press is
**accepted**, not passed on — it meant "get rid of this", and letting it
through would also hit whatever is underneath.

Built on `open_panel()` rather than in the constructor, so a panel that is never
opened leaves nothing behind, and released on both exits.

**Off by default.** A transient panel put up by the idle rotation is dismissed
on its own schedule, and closing it on the first touch would swallow the very
tap that woke the screen to read it. The AI answer panel does not need it
either — it already ends its conversation on any interaction.

## What an open dialog holds back

Two separate things measure "nothing has happened", and both are held while a
dialog is up.

**`on_interaction_timeout`** — `_check_interaction_timeout` returns early
whenever `DIALOG.dialog_stack` is non-empty, before it asks the page's own
opinion.

**`TIMEOUTS`** — a registration made with `idle=True` has its deadline pushed
out on every pass while a dialog is open, so the countdown restarts from when
the dialog closes.

```python
client.TIMEOUTS.add(20, self._back_to_night, key,
                    transient=True, idle=True)
```

`idle` is opt-in because not every timeout measures idleness. A transient
widget's few seconds is a **display duration** and keeps counting; a periodic
sync is work on a schedule. The ones marked are the night clock's settle timer,
the quick panel closing itself, an answer panel timing out, and a reminder
dismissing — all of which take something away from somebody who is still
looking at the screen.

Both matter. The night clock switches pages through `TIMEOUTS` rather than
through `on_interaction_timeout`, so a guard on one of them leaves the other
free to take the page away.
