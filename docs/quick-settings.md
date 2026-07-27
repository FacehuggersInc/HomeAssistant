# Quick settings

One global controls surface, reachable from every page by swiping down from
the top edge of the screen.

It is registered once against the client rather than built per page, so a
control appears everywhere or nowhere - there is no page that quietly lacks
"quit the app" because nobody added it there.

---

## Opening it

A thin strip along the top of the screen (`src/ui/controls/edge_swipe.py`,
20px) watches for a downward drag of 45px or more. It fires mid-drag rather
than on release, because waiting for the finger to lift makes a pull-down feel
like it did not register.

That strip consumes presses. An ignored press goes to the widget's parent -
the overlay layer - not to the page underneath, so there is no way to watch
the gesture and still let it through. The top 20px is a deliberate dead band;
keep interactive widgets clear of it.

`client.toggle_quick_settings()` opens it without the gesture.

---

## Layout

```
+------------------------------------------------------------------+
| Quick Settings                    [update][wp][pin][fs][set][x]   |
| Monday  14:32                                                     |
+------------------------------------------------------------------+
| +----------------------------+  +-----------------------------+  |
| | Quick Access               |  | System                      |  |
| | [btn] [btn] [btn] [btn]    |  | Brightness  [=========]     |  |
| | [btn] [btn]                |  | Volume      [======    ]    |  |
| +----------------------------+  +-----------------------------+  |
+------------------------------------------------------------------+
```

The panel is a `Panel` with `edge="top"`, a height of a third of the screen and
an 18px margin, which makes it float as a card rather than sit flush. Both
cards scroll internally so the panel keeps its proportion instead of growing to
fit whatever happens to be registered.

Header controls, left to right: update, wallpaper cycle, wallpaper pin,
fullscreen, settings, quit. The two wallpaper buttons are hidden off `sub.home`
- they act on the cycling background, and the publication they call only exists
while that page is built.

---

## Registering a quick access button

```python
from src.ui.icons import Icons

class MyPlugin(Plugin):
    def load(self, carryover=None):
        self.client.QUICK.register(
            "myplugin", "porch_light", "Porch", Icons.LIGHTBULB,
            on_press = self.toggle_porch,
            on_state = lambda: self.porch_on,   # omit for a momentary button
            order    = 20,
        )
```

| Argument | Meaning |
|---|---|
| `owner` | Your plugin key. Everything under it is dropped when the plugin unloads. |
| `key` | Unique within the owner. |
| `label` | Shown under the icon. Keep it to one or two words. |
| `icon` | An `Icons` constant, or any `mdi.` name. |
| `on_press` | Called when tapped. |
| `on_state` | Optional. Returns `True` when the button should read as "on". |
| `order` | Sort order, lower first. Defaults to 100. |

`on_state` is what makes a button a toggle: the tile picks up the `quick-tile-on`
style when it returns `True`, and is re-read after every press.

Entries are **descriptions, not widgets**. The panel builds a button from each
one every time it opens, so an entry registered while the panel is closed still
appears next time, and an entry whose owner has unloaded simply stops being
built. Nothing has to hand a live widget between pages, and nothing keeps a
reference to one that has been torn down.

### Removing

```python
self.client.QUICK.unregister("myplugin", "porch_light")   # one entry
self.client.QUICK.unregister("myplugin")                  # everything you own
```

The plugin loader already calls the second form on unload, so a plugin that
registers and never unregisters is still correct.

### Reacting to changes

```python
self.client.QUICK.subscribe(callback)     # called on every register/unregister
self.client.QUICK.unsubscribe(callback)
```

A listener that raises is dropped rather than left to raise on every future
registration.

---

## System controls

**Brightness** is a black wash over the window, not a backlight change. Real
DDC/CI control needs a channel the display may not expose, root on most Linux
setups, and a different API per platform. Painting over the window gets the
same result for a wall panel in a dark room with nothing to fail at runtime.

It lives on `OVERLAYS.passthrough` - that layer is
`WA_TransparentForMouseEvents`, which Qt skips entirely during hit testing, so
a full-screen dim cannot swallow a touch. It caps at 200/255 alpha: at full
black the only control that could undo it would be invisible.

The level resets to full brightness on every launch. It is deliberately not a
setting - a panel that boots dark looks broken.

**Volume** talks to whatever the machine has, tried in order: `wpctl`,
`pactl`, `amixer`, then `pycaw` on Windows. The slider is hidden entirely when
none of them answers, because on a wall panel there is no console to check why
a control does nothing. The chosen backend is logged at startup.

---

## The update button

Always present. Its colour is the signal: white when nothing is known, brand
green once an update is waiting.

Pressing it opens the update dialog straight away if a check has already found
something. If not, it checks there and then, and either opens the dialog or
notifies that this is the latest version. A failed check reports the reason
rather than claiming there is no update - the truth is that nobody could look.

See [Updating](updating.md) for how checks and the version marker work.
