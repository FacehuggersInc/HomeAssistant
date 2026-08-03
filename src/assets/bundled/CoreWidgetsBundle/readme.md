### A bundle of widgets and other UI objects
- Handles the home page (sub home and sub tiles pages)
- Gives *Widgets* and *Tiles* of out the box
- Handles all of the *core* experience 

## Docs in this folder

| File                        | About                                                                                                 |
|-----------------------------|-------------------------------------------------------------------------------------------------------|
| `docs/action-tile.md`       | The action tile — pointing one at anything registered, and deciding how it looks from what came back. |
| `docs/stickers.md`          | The sticker widget and its library.                                                                   |
| `docs/transient-widgets.md` | Widgets placed by something happening rather than by somebody arranging their screen.                 |

For the frameworks these sit in rather than these particular tiles, see the
panel's own `docs/widgets.md` and `docs/tiles.md`.


## What it exposes

Under the key `corewidgetsbundle`, on the public registry:

| Name                           | Is                     | For                                                                                                                                                          |
|--------------------------------|------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `timers`                       | A dict of callables    | `start`, `cancel`, `cancel_all`, `cancel_matching`, `find`, `get`, `running`, `all`, and `describe` for saying a duration aloud.                             |
| `alarms`                       | A dict of callables    | `schedule`, `cancel`, `cancel_all`, `cancel_matching`, `find`, `get`, `scheduled`, `ringing`, `silence`, and `clock_text` / `describe` for saying one aloud. |
| `stickers`                     | A dict of callables    | Placing and listing stickers. See `docs/stickers.md`.                                                                                                        |
| `notification_history`         | The history manager    | Reading and reopening past notifications.                                                                                                                    |
| `cwb_widgets`, `cwb_sub_pages` | Registration helpers   | How other plugins add to the home page.                                                                                                                      |
| `cwb_wallpaper`                | The wallpaper controls | Setting what the home page sits on.                                                                                                                          |

Reach them through `client.public`, guarded with `public.has(name)`, and
declare `corewidgetsbundle` in your `dependencies`. See "Reaching another
plugin" in the panel's own `docs/plugins.md`.

## Alarms

A wall clock time rather than a countdown. `alarms.py` reads beside
`timers.py` on purpose; three things differ.

|                    | Timer      | Alarm                 |
|--------------------|------------|-----------------------|
| Set for            | A duration | A time of day         |
| Survives a restart | No         | Yes, in `alarms.json` |
| Home widget        | Yes        | No                    |
| Repeats            | Never      | Daily, if asked       |

A timer is a thing happening in the room over the next few minutes, and a
panel that rebooted has already failed to count it. An alarm for seven
tomorrow morning set at ten at night has to still be there in the morning.

**So it is on disk.** `alarms.json` in the data directory, written on every
change - set, cancelled, or a repeating one rolling to its next day - and read
back by `start_watching()`. There is no save on shutdown to miss, which is the
point: a crash and a clean exit leave the same file.

| Situation                           | What happens                                                               |
|-------------------------------------|----------------------------------------------------------------------------|
| Restarted a minute later            | Everything comes back, same keys                                           |
| Off overnight, one-off already past | Dropped. Six alarms at lunchtime is worse than none                        |
| Off overnight, repeating            | Moved to its next occurrence                                               |
| Crash during the write              | The old file is intact - written beside and renamed over with `os.replace` |
| File unreadable                     | Said in the log, moved to `alarms.json.bad`, starts empty                  |

That last one matters more than it looks: overwriting an unreadable file with
an empty one on the next save destroys the evidence, and somebody was relying
on what was in it.

**When one goes off** it resets the idle timeout (so a night clock is
dismissed rather than left sitting over it), sounds `timer_alarm`, and opens
an answer panel. `AUDIO.play` answers to `sounds_muted()` itself, which do not
disturb implies - so a silent panel stays silent and still shows the panel.

**Three ways to stop it**, all reaching `_dismissed`:

- Tapping the panel, which closes it.
- Saying "stop" - a `CANCEL` registry entry, active only while one is
  ringing, at a higher priority than the music. Whatever is demanding
  attention is what "stop" means.
- Nobody answering for `alerts.alarm_give_up_after`.

A repeating alarm is rescheduled for the next day rather than removed. "Set a
daily alarm for 7 am" and "wake me up every day at 6:30" set one; "cancel the
daily alarm" takes it away. `find()` and `cancel_matching()` take a `repeats`
filter for that - on its own it is usually enough to say which alarm is meant,
since there are rarely two.

**Cancelling one that is ringing** deals with all three things on screen: the
noise stops, the panel offering to silence it is taken down, and a short
"stopped" panel says what went. Left alone, that first panel is still offering
to silence something that no longer exists. `client.answer()` takes an
`on_built` callback for this - the service keeps the panel so it can close it
from elsewhere.

Both panels time out on their own: 120 seconds for the ringing one, which is
asking to be answered, and 8 for the confirmation, which is only reporting.

**Seeing what is set** is two Quick Settings entries, **Timers** and
**Alarms**, each opening a list with a cancel on every row and a button to add
another. Two dialogs rather than one with tabs: a timer and an alarm answer
different questions - "how long left" against "what time" - and somebody
opening one already knows which they meant.

Both share `_ListDialog` in `widgets/schedule_lists.py`. It rebuilds the list
when something is cancelled rather than diffing it - the list is short by
definition, and rebuilding cannot leave a row pointing at something that has
gone - but refreshes only the DETAIL text on its one-second tick, because a
rebuild every second would take a row out from under a finger on its way to
the cancel button.

**Setting one by hand** is the alarm button on the Configuration Bar, beside
the timer one. `AlarmPickerDialog` is shaped like `DurationPickerDialog` -
steppers, a readout saying the numbers back, a confirm that disables itself -
with a row of day buttons and a repeat toggle. Hours are 24 hour and the
readout says it back in 12 hour with the suffix: an am/pm toggle is a third
control for one number, and the one people get wrong.

Skills for setting, cancelling and listing them are in **Core Skills**, which
owns the phrasing; this plugin owns the clock, the panel and the picker.

## The whiteboard

`mdi.draw` on the Configuration Bar opens a near-fullscreen canvas: colours,
four brush widths, an eraser, undo, clear. Save writes a **transparent PNG**
into the sticker folder and puts it on the home screen.

A producer for the sticker system rather than a second one. The folder, the
library, placement and persistence already exist - this only had to make the
image and hand it over, through `stickers["add"]` and `stickers["place"]`.

Four decisions worth knowing:

- **Strokes are objects, not pixels.** Each is a `QPainterPath` with its own
  colour and width. Undo is then free and the drawing never goes soft.
- **The eraser is a stroke** with `CompositionMode_Clear`, so it takes ink
  away rather than painting over it - which is what a transparent sticker
  needs, since background-coloured paint would land as an opaque smear on the
  wallpaper. It also undoes like everything else, rather than being a hole
  nothing can take back.
- **The ink is composed offscreen**, then drawn onto the widget. `Clear`
  needs an alpha channel to clear TO and a widget has none, so painting the
  strokes straight onto it made the eraser a black pen. It also means the
  preview is rendered by the same code as the saved file - a preview drawn
  differently from the thing being saved is a preview of nothing. Finished
  strokes are cached; only the one under the finger is redrawn.
- **Saved cropped to the ink**, and put up at the size it was drawn. The
  canvas is most of a screen and a drawing is usually a corner of it; whole,
  every sticker would be a screen-sized mostly-empty PNG that is awkward to
  place and slow to paint.

  The crop margin is **half** the pen width plus a pixel for the antialiased
  edge, because a path's bounding box is its centre line and the pen paints
  half either side. The whole width leaves a 36 pixel border around a 36
  pixel brush, which nearly doubles a small drawing.

  Placement passes that measured size rather than the default share of the
  panel width - the canvas is nearly the screen, so pixels on it are close to
  pixels on the home page, and "it appeared the size I drew it" is the least
  surprising answer. `StickerWidget` clamps to 48-1600 either way.
- **The board tint is not saved.** There is a faint fill under the canvas so
  ink can be seen while drawing; the image is filled transparent instead.

Black is deliberately not in the palette. A transparent sticker lands on
whatever wallpaper is underneath, and black on a dark panel is invisible.

## Sun & moon

`SunTile` counts down to the next sunrise or sunset, and after dark draws the
moon at its actual phase.

Nothing is fetched. The **Astronomy** library plugin is arithmetic on a date
and a position, so it works with the router off and is never the thing that
wakes the network - reached through `client.public.astronomy`, with
`astronomy` declared as a dependency. The position comes from the weather API's `coordinates()`, not
from another plugin's settings file.

**Two faces, one tile.** Daylight puts the sun on an arc at how far through
the day it is - a dot near the left has just come up, one near the right is
nearly down, and that is read before any word on the tile. Night draws the
moon as a lit disc with a shadow offset across it, which gives every phase
rather than eight named pictures.

Three things that were wrong and are worth not repeating:

- `sun_times` answers in **UTC with a timezone attached**, and comparing one
  of those to `datetime.now()` raises rather than returning False. The
  day/night test threw, the handler swallowed it, and the tile showed the day
  face at midnight while claiming it had no location. Everything is converted
  to local naive time on the way in.
- `%-I` is a **glibc extension**. Windows raises on it, and a format string is
  a poor place to lose a whole platform.
- The failure branch resets **everything**, not just the times. Half-reset
  state is a tile confidently showing the wrong face with a stale countdown.

## The weather tile

Three variants: a glance at one cell, an hourly strip from 2x3, a full
readout at 3x3 and above.

**The background is the same painter the weather-event tile uses**, so two of
these side by side agree about the sky rather than one showing a gradient and
the other a storm.

**There is no icon.** The background draws the weather, so a glyph on top of
a picture of the same thing is the same fact twice - in the space the
temperature wants. What the icon used to say, the sky says. A scrim is laid under the readout on anything larger than
1x1 - white-on-cloud is the one combination that stops being readable, and
the drawing is behind numbers now.

**The stats are meters, not rows.** Anything with a scale worth drawing gets
a bar under its value, so the tile answers "a lot or a little" before a
number is read:

| Reading                | Ceiling                         |
|------------------------|---------------------------------|
| Humidity, cloud cover  | 100%                            |
| Wind                   | 40 mph                          |
| Gusts                  | 60 mph                          |
| Feels like, rain, snow | no bar - inches have no ceiling |

A fixed ceiling rather than the day's maximum. A bar that rescales itself
looks like the weather changed when only the range did.

Feels-like and humidity are new: the API was already fetching both and the
tile was throwing them away. A reading that is missing or zero drops its row
entirely - "Snowfall 0.00 in" in July is a row spending space to say nothing.

## Weather event

`WeatherEventTile` and `WeatherEventWidget`: the sky drawn, and one word for
what it is doing. No numbers - the weather tile beside them has those, and a
temperature is a thing you read where "raining" is a thing you see from the
far side of a room.

Both are frames around `paint_condition()`, a painter rather than a widget,
which is what lets a 1x1 tile and a home-page widget be the same picture at
very different sizes without one owning the other.

`condition_of()` picks the word in the order somebody would say it: storm
beats snow beats rain beats cloud. If the sky is doing two things, the one
worth mentioning is the worse one.

The raindrops are placed from a seed rather than at random. Re-rolled on
every repaint they crawl, which reads as a rendering fault rather than as
rain.

The widget paints its word in `paintEvent`; the tile puts one in a label from
its variant builder. That is not inconsistency - a `Widget` has no `build()`
hook and a `Tile` does take builders, so each does what its base class asks
for.

The widget declares `FLOATABLE = True`. Without it a widget is pinned to an
anchor zone and `DEFAULT_ANCHOR` is `bottom-left` - so it can be picked up
and never put down anywhere else. Anything meant to be arranged has to say
so.

## Two tiles worth knowing about

**Bookmark** and **action** are both `MULTIPLE`: the panel entry is a template
and every one placed is a copy with its own key and its own settings. Both are
`EDITABLE`, so a pencil appears on the chrome when they are selected and opens
what they were set up with rather than running them.

The action tile is the general one. It can be pointed at anything callable that
any registry knows about, which makes it powerful and makes it possible to
point it at something unsuitable — `docs/action-tile.md` says what it can and
cannot sensibly do.
