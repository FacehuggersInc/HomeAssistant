# Quick settings

One global controls surface, reachable from every page by swiping down from
the top edge of the screen.

> **Part of the client.** Quick settings, the registry behind it
> and the top-edge gesture are built into the client, not a plugin —
> so they are there whatever else is loaded. The buttons *inside* it
> come from plugins and go when those do.
It is registered once against the client rather than built per page, so a
control appears everywhere or nowhere - there is no page that quietly lacks
"quit the app" because nobody added it there.


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

Header controls, left to right: update, fullscreen, docs, settings, quit.

Nothing here acts on one page in particular. A control that only means
something on `sub.home` - the wallpaper, for instance - belongs on that page
rather than in a panel reachable from every other one, where it would be
hidden almost everywhere it could be reached from. See the
[configuration bar](widgets.md#the-configuration-bar).


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

| Argument   | Meaning                                                                  |
|------------|--------------------------------------------------------------------------|
| `owner`    | Your plugin key. Everything under it is dropped when the plugin unloads. |
| `key`      | Unique within the owner.                                                 |
| `label`    | Shown under the icon. Keep it to one or two words.                       |
| `icon`     | An `Icons` constant, or any `mdi.` name.                                 |
| `on_press` | Called when tapped.                                                      |
| `on_state` | Optional. Returns `True` when the button should read as "on".            |
| `order`    | Sort order, lower first. Defaults to 100.                                |

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


## The update button

Always present. Its colour is the signal: white when nothing is known, brand
green once an update is waiting.

Pressing it opens the update dialog straight away if a check has already found
something. If not, it checks there and then, and either opens the dialog or
notifies that this is the latest version. A failed check reports the reason
rather than claiming there is no update - the truth is that nobody could look.

See [Updating](updating.md) for how checks and the version marker work.


## The tiles

Each entry is drawn as a tile with a glyph and a caption, and **the whole tile
takes the press** — on a touch screen, a control that only responds over its
20px glyph is a decoration rather than a button.

The glyph and caption are mouse-transparent so every press lands on the tile,
and a press that travels more than a few pixels is treated as a scroll of the
card rather than a tap. Four visual states: on, off, pressed and disabled.

**The grid fills the panel.** Column count comes from the available width, so
a wide screen gets more across rather than four in the left third. Tiles are
fixed height and flexible width, capped at 168px so three entries on a 2560px
panel do not become three 700px slabs, and a row that cannot fill the width is
centred rather than hugging one edge. The grid re-lays out when the panel is
resized, but only when the column count actually changes.

### Closing on press

`closes_panel=True` shuts the panel after the entry has run. Off by default,
because most entries are toggles and watching one flip is the confirmation that
it worked — but anything that **navigates or opens something else** should set
it, or the first thing you do afterwards is dismiss the panel covering what you
just asked for.

```python
client.QUICK.register(
    "myplugin", "my_page", "My page", "mdi.page-next",
    on_press=lambda: client.goto("#my_page"),
    closes_panel=True,
)
```

The panel closes even if the press raised, so a broken entry cannot leave it
stuck open. Both bundled navigating entries use it: *Night clock* and
*Widgets*.

## How tall it opens

`home.layout.quick_settings_height` is a **share of the window**, not a pixel count, so
one value suits any display. Clamped to 0.15–0.9; a third is tight on 1080, and
0.45 gives the System side room without scrolling.

## A short screen

Below 900px of panel height the System side lays out **compact**: the slider's
label and its track share one line instead of stacking, and the card gives up
some padding.

The track keeps its full 38px either way. What is being squeezed is empty space
and a line break — not the thing somebody has to hit with a finger, and not the
text they have to read. That returns roughly 66px, which is enough for the
controls to fit without a scrollbar.

The threshold is measured against the height the panel actually has rather than a
device name: the card grows with whatever is registered in it, so a 1080-tall
screen with four controls has less room than a 1440 one with two.

> **Some of these are Linux only.** The Wi-Fi and Bluetooth buttons,
> the volume slider and the media keys all read Linux services. Where
> one is missing the control says what it needs rather than
> disappearing. Brightness and the quick-access grid work everywhere.

## The two radios

Wi-Fi and Bluetooth sit above the sliders, as state buttons.

Each shows its state in its **icon** as well as its label: signal bars for
Wi-Fi, a struck-through symbol when Bluetooth is off, the connected device and
its charge when it is on.

Pressing one opens its section in Settings. Pressing one that cannot work
explains what is missing instead.

## Controls that cannot work yet

A control whose tooling is absent is **shown greyed and says what it needs when
pressed**, rather than being hidden. There is no console on a wall panel, so a
control that is simply absent gives nobody a way to find out why.

`src/system/requirements.py` holds what each capability needs: the tool, what it
is for, and a starting point for installing it. The install line is a guess at a
package name rather than a promise - the panel may not be on the distribution it
was written for.

## Volume

The slider **follows the system volume** while the panel is open, re-read once a
second. A media key, another application or a mixer can move it, and a slider
showing a level the machine is not at is worse than no slider.

Read on a worker, since reading it shells out, and left alone while it is being
dragged — overwriting the handle under somebody's finger fights them.

## Media controls

Previous, play/pause and next, under the sliders on the System side. These are
the **media keys a keyboard sends**, so they reach whatever the desktop
considers to be playing — a browser tab, a music player, anything that
registered for them. The panel's own player has its own controls on the
now-playing card; this is for everything else.

`playerctl` is used when installed, since it speaks MPRIS directly. Failing
that the keys are synthesised with `xdotool` or `ydotool`, which is harder on
Wayland than on X11 and may not be possible at all. **The row is hidden when no
tool can send them** — on a wall panel there is no console to check why a button
does nothing.

Sending happens off the UI thread, because it shells out.

## Brightness

The slider drives `client.DIMMER`, which uses **real backlight control** where
the machine allows it - sysfs, systemd-logind, brightnessctl/light or DDC/CI -
and falls back to a black wash over the window where it does not. See
[Screen brightness](backlight.md) for the routes, the setup each needs, and
`hactl.py backlight --survey` for finding out which one your panel got.

`set_brightness(percent)` snaps to a level. `animate_brightness(percent,
duration_ms)` eases to one, which is what anything changing the level *on its
own* should use: a wall panel dimming by itself is startling as a step change
and unremarkable as a fade. Setting it directly cancels an animation in flight.

It never goes fully black. At 0% the wash is alpha 200, not 255, so the screen
stays readable enough to find the control that undims it.

## Order in the constructor

Every piece of state is initialised **before** the methods that fill it in.

`_build_cards()` creates the Wi-Fi and Bluetooth buttons and assigns them to
`_wifi_button` and `_bt_button`. Setting those to `None` after it runs leaves
the buttons on screen with nothing referring to them — so the ticks that update
them see nothing to paint, for the life of the process. The panel looked right
and never changed.

## Which Bluetooth device the button shows

`connected_devices()` orders by **what a device is for**, matched against
BlueZ's own `Icon` hint rather than guessed from its name:

|   |                                     |
|---|-------------------------------------|
| 0 | headset, headphones, audio, speaker |
| 1 | phone                               |
| 2 | computer                            |
| 3 | gaming, joystick                    |
| 4 | keyboard, mouse, input              |
| 5 | anything else                       |

Audio first because it is the one you are currently hearing — a headset that has
dropped to 8% is worth knowing about, while a controller sitting on the table is
not. Input last: a keyboard is either working or obviously not.

Whether it reports a charge, and then its name, only break ties. The name keeps
the answer stable as BlueZ reorders its object tree, so the button does not flip
between two equivalent devices.

`snapshot()` uses the same key rather than a second copy of it — the two drifted
apart once already.

### Saying there are others

A **badge** in the corner, painted rather than written into the label. The label
already holds a name and a charge — `Buds  90%` — and a third thing in it is read
word by word, which is not how this button is used: it is glanced at from across
a room. A dot is seen without being read.

`+1` through `+9`, then `9+`. The exact number stops mattering and the badge
stops fitting at about the same point.
