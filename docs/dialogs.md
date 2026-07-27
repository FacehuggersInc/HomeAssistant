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

**One thing to know if you build dialog widgets by hand.** `BaseDialog`
derives from `QFrame` *and* sets `WA_StyledBackground`, and both matter. A
plain `QWidget` subclass does not paint a stylesheet `background` without that
attribute -- but only once it is parented into `OVERLAYS`, which is
translucent. Rendered standalone it looks completely fine, so this is easy to
ship by accident and the symptom is floating text over the page. Anything you
parent into the overlay layer wants one or the other.
