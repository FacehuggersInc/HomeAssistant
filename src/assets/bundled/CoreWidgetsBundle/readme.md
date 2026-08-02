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

## Two tiles worth knowing about

**Bookmark** and **action** are both `MULTIPLE`: the panel entry is a template
and every one placed is a copy with its own key and its own settings. Both are
`EDITABLE`, so a pencil appears on the chrome when they are selected and opens
what they were set up with rather than running them.

The action tile is the general one. It can be pointed at anything callable that
any registry knows about, which makes it powerful and makes it possible to
point it at something unsuitable — `docs/action-tile.md` says what it can and
cannot sensibly do.
