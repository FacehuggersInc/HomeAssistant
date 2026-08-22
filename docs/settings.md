# Settings

Two layers, and they are not the same thing:

* **Client settings** live in the user data directory and are described by
  `src/assets/data/new-template.json`. These are the app's own settings.
* **Plugin settings** live in each plugin's `settings.json` and appear in the
  Settings page under that plugin.

Both use the same declaration format and the same widget builders.


## Audio

Everything the panel does with sound is in one category: which devices it
uses, how it speaks, and whether it makes any noise at all. TTS moved here
from Assistant, and the two Accessibility settings - do not disturb, mute
sounds - came with them, which emptied that category entirely.

**The two devices are dropdowns of real hardware**, filled at startup by
`Client.fill_device_options()` from `AUDIO.devices()`. A list written into the
template would be whatever machine the template was made on.

Devices are held by **name**, not index. PortAudio renumbers when anything is
plugged in - exactly what a USB microphone array does - so a saved index
quietly points at something else after a reboot. `AUDIO.device_index()`
translates at the point of use, and answers `None` for a device that is not
connected, which means the system default: a working panel beats a stale
index.

**ALSA's plugins are not devices.** PortAudio reports them as if they were:
`samplerate`, `dmix`, `dsnoop`, `surround51`, `sysdefault`, `upmix`, and a
dozen more. Each is routing or format conversion, each opens without
complaining, and each then sends audio somewhere nobody chose. A dropdown full
of them looks like a list of microphones, and picking the wrong entry is how a
panel ends up hearing nothing with no error to show for it. On one Linux box
that is 19 entries reported and 5 worth offering.

They are filtered by `_HELPER_DEVICES` in `assistant/audio.py`, which the
assistant already used for its own logging and the dropdown now shares. ALSA's
own `default` goes too - it is what the `Default` entry already means, and two
entries for one thing is a dropdown that looks like it has a trick in it.

A saved device that is not plugged in right now stays in the dropdown.
Dropping it would silently rewrite the setting to whatever came first, so a
panel booted with its speaker unplugged would forget which speaker it had.

A saved name that IS a known plugin is different: it is not a device at all.
That one is reset to `Default` with a warning rather than preserved.
`AUDIO.is_helper()` draws the distinction, and an unknown name is deliberately
not a helper, because it may be hardware that is simply unplugged.

`Default` means "follow the system". That is right for most panels and wrong
for one with an array that takes the output as well as the input when it
appears - which is the reason the rest of the list exists.

> **The audio device settings are Linux only.** The output list comes from
> PipeWire or PulseAudio through `pactl`, the volume floor drives `wpctl`,
> `pactl` or `amixer`, and the microphone mute drives the same three. Without
> one of those the output list falls back to the sound card's own devices, the
> volume floor does nothing, and the microphone mute control is not offered at
> all - a button that cannot mute anything is worse than no button, because it
> looks like the microphone is off.


## Settings that move

`merge_values` matches by path, so a setting that moves category looks like
one key dropped and another added: the old value goes and the new one arrives
at its default. Somebody who had chosen a TTS voice would find it reset by an
update that only reorganised a menu.

### What counts as a change

A migration that runs only when a KEY is added or removed misses this. A new
option in an existing dropdown is neither - so the installed file keeps its
list forever, and the panel builds every dropdown from that file rather than
from the template. Adding a model to `assistant.speech.model` then does nothing on any
panel that had already been started once.

`structure_differs()` answers the wider question: has anything but a user's
own `value` changed. Options, type, description, defaults - all of that is
shipped structure and should follow the template. A settled install still
migrates nothing, and a person changing a value is not a structure change.

`MOVED_SETTINGS` in `updater.py` maps old path to new. The value is copied
into the new location in the installed tree **before** the merge, so the merge
finds it where it now expects it and needs to know nothing about any of this.

Entries stay there forever. An install can be any age, and one skipping three
versions has to make the same journey as one skipping a single version.

## Reading and writing

```python
value = self.client.setting("assistant.speech.model.value", "parakeet-v3")
```

Always pass a default. A read that misses returns it rather than raising,
which means a setting that never arrived degrades to a working default instead
of a crash - and also means a **missing setting is silent**, so check the
Settings page if something appears to do nothing.

Attribute access works too, and is what most of the codebase uses:

```python
self.client.SETTINGS.application.window.size.value
self.client.SETTINGS.home.widget_margin.value
```

Writing is the same path in reverse:

```python
self.client.SETTINGS.home.pinned.value = "/path/to/image.png"
```

From a plugin, prefer `self.option("general.enabled", True)` - it reads from
your own `settings.json` and keeps you out of the client's tree.

Writes are not thread-safe. Do them on the UI thread, or hold
`client.SETTINGS_LOCK`. See [Threading](threading.md).


## Sub-headings

A key whose value is a dict of *other settings* rather than a
`{"type", "value"}` leaf becomes a heading on the page, with its children
under it:

```json
"assistant": {
  "enabled": { "type": "bool", "default": true, "value": true },
  "speech": {
    "model":              { "type": "enum", "...": "..." },
    "parakeet_precision": { "type": "enum", "...": "..." }
  }
}
```

That renders as a bare toggle, then a **SPEECH** rule with two settings under
it. Nest as deep as you like; anything left at the top of a category appears
above the first heading.

The heading becomes part of the path, so `assistant.model.value` above is
`assistant.speech.model.value`. Moving an existing setting under a heading is
a **move**, and needs an entry in `MOVED_SETTINGS` like any other - see
[Settings that move](#settings-that-move).

## Declaring a setting

```json
{
  "general": {
    "enabled": {
      "type": "bool",
      "default": true,
      "value": true,
      "description": "Whether the plugin does anything at all."
    },
    "poll_interval": {
      "type": "int",
      "default": 30,
      "value": 30,
      "suffix": "sec",
      "description": "How often to refresh. Lower costs more requests."
    }
  }
}
```

`default` and `value` are both required. `default` is what Reset returns to;
`value` is what is in force. The top-level keys become sections on the page.

`description` is not optional in spirit - it is the only explanation the person
using the panel will ever see, and it should say what the setting *does to
them*, not what it sets.

### Types

| Type                       | Widget                                                |
|----------------------------|-------------------------------------------------------|
| `bool`                     | Toggle.                                               |
| `int`, `float`             | Numeric field, opens the numpad.                      |
| `string`                   | Text field, opens the QWERTY keyboard.                |
| `body`                     | Multi-line text field.                                |
| `enum`                     | Dropdown. Requires an `options` list.                 |
| `group`                    | Dropdown that chooses which other settings show.      |
| `path`                     | Text field with a Browse button.                      |
| `secret`                   | Masked field backed by `.env`. Requires an `env` key. |
| `list[int]`, `list[float]` | Comma-separated numeric field.                        |

`hidden: true` stores a value without rendering a field for it:

```json
"default_location": {
  "type": "string", "default": "", "value": "", "hidden": true,
  "description": "Set with the Choose on a map button above."
}
```

For a plugin that draws its own control and would otherwise show a raw text box
beside a proper picker. The value still saves, migrates and reads back exactly
as any other — it simply has no field. Pair it with a block added through
`insert_plugin_block()` so there is something to set it with.

`suffix` adds a unit label (`"sec"`, `"ms"`, `"hrs"`, `"px"`).

### `enum`

An enum's `options` are shipped structure, not a user value, so they follow
the template on every update. A saved value that the new list does not offer
falls back to the shipped `default` - see `keep_valid()` in `updater.py`.
Carrying it across regardless is how a panel ends up displaying a setting it
will not let you pick again, with code reading a string no branch handles.

```json
"model": {
  "type": "enum",
  "default": "parakeet-v3",
  "value": "parakeet-v3",
  "options": ["parakeet-v3", "parakeet-v2"],
  "description": "v2 is English-only and slightly faster."
}
```

### `group`

A dropdown that decides which **other** settings in its block are worth
showing.

It exists because a block full of settings that only matter in one
configuration reads as a block full of settings. `audio.speech` carries a host
and a port that mean nothing unless the voice is on another machine, and
nothing on the page said so — somebody sets them, nothing changes, and the
setting looks broken rather than inapplicable.

```json
"tts_where": {
  "type": "group",
  "value": "socket",
  "groups": {
    "local":      ["tts_voice", "tts_language", "tts_voice_file"],
    "subprocess": ["tts_voice", "tts_language", "tts_voice_file", "tts_port"],
    "socket":     ["tts_host", "tts_port"]
  },
  "description": "Where the speaking happens."
}
```

**No `options`, and no `default`.** The choices are the keys of `groups`, so
there is no second list to disagree with the first — one that did would offer
a choice that shows nothing. And the value *is* the selection; a group setting
with nothing selected is a dropdown with nothing in it. A value naming a group
that no longer exists falls back to the first, which is what a plugin update
leaves behind.

**Sharing is a key in more than one group.** `tts_voice` is the same choice
whether the model runs here or beside the panel; `tts_port` is the same port
on the loopback or on another machine. It is one setting, so switching to
another group that also names it finds the value that was already there.

**Hiding is all it does.** Every setting keeps its value and stays readable
and writable by name whatever is selected — `client.setting()` does not know
groups exist. A setting left behind in a group nobody is looking at keeps its
value and finds it again when that group comes back. Nothing is reset and
nothing is dropped from the file.

Members are drawn **inside** the selector's own block, indented behind a rule
down the left, so where the group starts and where it ends is visible without
reading anything. A chip on each row would say that every one of them belongs
to something; it would not say they belong to *that*, nor where they stop.

The chip stays only on settings shared between several groups, because which
group is the one thing containment cannot say.

### Groups stack

A member can itself be a selector. `tts_backend` chooses whether there is a
voice at all; `tts_where` chooses where it runs, which only means anything
once the first has said yes — so `where` is a member of `backend`, and carries
its own members inside it.

```json
"tts_backend": {
  "type": "group",
  "value": "auto",
  "groups": { "auto": ["tts_where"], "pocket": ["tts_where"], "off": [] }
}
```

Choosing `off` names nothing, so the whole run below it disappears and the
block is left with the enable toggle and the two settings that apply whatever
speaks.

**Each level of nesting gets its own colour** down the left edge. Two runs can
share an edge once groups stack, and one colour for both leaves nothing to say
where the outer ends and the inner begins. Three levels are defined, which is
as many as stay apart at a glance on a panel across a room —
`MAX_GROUP_DEPTH` refuses a fourth and logs which selector asked for it, so a
schema that nests itself in a circle stops rather than recursing for ever.

Everything not named by any group stays exactly where it was written. A member naming a setting that is not in the block
is logged as a warning and skipped — a control somebody expected and does not
get, with nothing to say why, is worth a line.

**Changing the dropdown rebuilds nothing.** Every member is built once,
whichever group names it, and switching shows and hides them. So the scroll
position holds, an open keyboard stays open, and a value typed into a setting
that is then hidden is still there when its group comes back. The cost is a
handful of extra widgets on one page.

### `secret`

The value is **not** stored in `settings.json`. It goes in `.env`, and the
declaration only names the variable:

```json
"openai_key": {
  "type": "secret",
  "env": "OPENAI_API_KEY",
  "value": "",
  "description": "Stored in your .env file, not here."
}
```

See [Registries](registries.md) for how the scoping works - a plugin can only
read secrets it declared.


## What the client itself declares

Generated from `src/assets/data/new-template.json`. A plugin's own
settings live in its `settings.json` and are documented with the plugin.

### `application`

| Key                                        | Type        | Default                        | What it does                                                                                                                                                                                   |
|--------------------------------------------|-------------|--------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `application.window.size`                  | list[float] | `1920.0 x 1080.0` ['px', 'px'] | Set the size of the window. NOTE: this doesn't effect the actual size, its a reference                                                                                                         |
| `application.window.position`              | list[int]   | `1074 x 1701` ['px', 'px']     | Where the window will position itself on startup                                                                                                                                               |
| `application.window.auto_lock`             | bool        | `on`                           | Whether the app will automatically on startup, position at X and Y, then fullscreen                                                                                                            |
| `application.interaction_timeout`          | int         | `60000` ms                     | Time of inactivity (no clicks/touches anywhere) before Client fires on_interaction_timeout. Used in the settings page to go home when no interaction has happened for a while.                 |
| `application.updates.check_interval`       | int         | `6` hrs                        | How often to ask GitHub whether a newer version exists. This is a single small request, not a download - nothing is fetched until you choose to update. Set to 0 to never check automatically. |
| `application.updates.restart_on_crash`     | bool        | `on`                           | Whether the launcher automatically restarts the app if it crashes. Turn this off to have a crash simply stop the app.                                                                          |
| `application.updates.max_restart_attempts` | int         | `5`                            | How many times in a row the launcher will restart after a crash before giving up. Prevents a boot loop when something is genuinely broken.                                                     |
| `application.updates.crash_window`         | int         | `120` sec                      | If the app ran longer than this before crashing, the restart counter resets. A long-lived session that later dies is not a boot loop.                                                          |
| `application.updates.update_grace_period`  | int         | `60` sec                       | If a freshly-applied update crashes within this window, the launcher rolls back to the previous version and relaunches it.                                                                     |

### `assistant`

| Key                                   | Type | Default       | What it does                                                                                                                                                                                                                                                                                              |
|---------------------------------------|------|---------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `assistant.enabled`                   | bool | `on`          | Whether the voice assistant runs at all. Turn this off to stop the speech-to-text process entirely.                                                                                                                                                                                                       |
| `assistant.speech.model`              | enum | `parakeet-v3` | Which Parakeet transcribes the phrase, run through `onnx-asr`. `parakeet-v3` covers 25 European languages; `parakeet-v2` is English-only and slightly faster. Downloaded on first use, after being asked.                                                                                                 |
| `assistant.speech.parakeet_precision` | enum | `int8`        | Which Parakeet weights to fetch and load. `int8` is about 700MB; `float32` is the full-size export at about 2.5GB and several times the memory, and is only more accurate on audio far longer than anything said to a panel. Changing it downloads the other set.                                         |
| `assistant.wake.wake_word`            | enum | `alexa`       | The word that wakes the assistant, spotted by openWakeWord. One of `alexa`, `hey jarvis`, `hey mycroft`, `hey rhasspy` - the words it ships models for; anything else would need a model trained for it. Plugins use this as the default wake word for their skills. Requires a restart of the assistant. |
| `assistant.wake.wake_listen_timeout`  | int  | `12` sec      | How long the panel keeps listening after the wake word before giving up. Too short and it stops mid-thought; too long and it sits listening to the room.                                                                                                                                                  |
| `assistant.wake.wake_sensitivity`     | float| `0.5`         | How sure openWakeWord has to be before it counts as the wake word. Lower hears it through more noise - a fan, music, a room away - and also fires on more things that were not it. Worth moving in steps of 0.05; the assistant restarts itself on save.                                                  |
| `assistant.wake.wake_sensitivity_speaking`| float| `0.0`         | The same bar for the moment the panel is talking; 0 uses the ordinary one. A reply being read out puts the panel's own voice in the microphone alongside yours, and the value that suits the room is often too high for that. Going lower costs less here - a false fire only interrupts a sentence it|
| `assistant.wake.session_silence`      | int  | `800` ms      | How long a pause ends your sentence once the assistant is in a conversation. Lower reacts faster; too low and a breath mid-sentence is treated as the end, which splits one question into several and sends each of them separately. 800ms suits normal speech.                                           |
| `assistant.feedback.voice_bar`        | bool | `on`          | Show the thin activity bar along the bottom of the screen while the assistant is listening.                                                                                                                                                                                                               |
| `assistant.feedback.voice_bar_hold`   | int  | `6` sec       | Minimum time the activity bar keeps a transcript on screen. Longer transcripts are held longer than this automatically.                                                                                                                                                                                   |

### `audio`

Everything the panel does with sound, in one category: which devices it uses,
how it speaks, and whether it makes any noise at all.

| Key                            | Type   | Default    | What it does                                                                                                                                                                                |
|--------------------------------|--------|------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `audio.devices.output_device`  | enum   | `Default`  | Which output speech and sounds are played through. The list is the system's own outputs, read from PipeWire or PulseAudio - the same ones the desktop's sound settings show. Only the panel'|
| `audio.devices.input_device`   | enum   | `Default`  | Which microphone to listen on. A dropdown of real hardware, filled at startup; `Default` follows the system.                                                                                |
| `audio.devices.mic_processing` | enum   | `software` | Whether the microphone cleans its own audio. `hardware` for an array that runs its own AEC and noise suppression, which shortens the self-hearing guards and turns off a second noise pass. |
| `audio.devices.minimum_volume` | int    | `0`        | The quietest the system volume may be; 0 turns it off. A machine that picks its own output on boot inherits whatever volume that device was left at, and a reply nobody can hear looks broke|
| `audio.speech.tts_enabled`     | bool   | `on`       | Whether the assistant speaks its replies. Skills stay usable without it; they just do not talk back.                                                                                        |
| `audio.speech.tts_backend`     | enum   | `auto`     | Which voice backend speaks, or `off`.                                                                                                                                                       |
| `audio.speech.tts_voice`       | enum   | `alba`     | Which Pocket TTS voice speaks.                                                                                                                                                              |
| `audio.speech.tts_language`    | enum   | `default`  | Which language weights Pocket TTS loads.                                                                                                                                                    |
| `audio.speech.tts_voice_file`  | string | `(empty)`  | A wav to clone instead of a listed voice.                                                                                                                                                   |
| `audio.speech.tts_padding_ms`  | int    | `140` ms   | Silence added either side of a spoken reply.                                                                                                                                                |
| `audio.speech.tts_rate`        | float  | `1.0`      | Playback speed, which also moves the pitch.                                                                                                                                                 |
| `audio.quiet.do_not_disturb`   | bool   | `off`      | Hold notifications back.                                                                                                                                                                    |
| `audio.quiet.mute_sounds`      | bool   | `off`      | Silence the panel's own sounds.                                                                                                                                                             |

### `home`

| Key                                         | Type   | Default          | What it does                                                                  |
|---------------------------------------------|--------|------------------|-------------------------------------------------------------------------------|
| `home.layout.widget_margin`                 | int    | `28`             | The Outer Margin of Widgets on the Home Page                                  |
| `home.layout.pinned`                        | path   | ` `              | The Path to your Pinned Image for the Home Background                         |
| `home.clock.time_format`                    | string | `%I:%M %p`       | -                                                                             |
| `home.clock.date_format`                    | string | `%a, %b %d`      | -                                                                             |
| `home.background.images`                    | path   | `C:\Home\Images` | Home Background's Path for Cycling Images                                     |
| `home.background.background_cycle_interval` | int    | `75` sec         | The amount of time in seconds before the background cycles to a new Wallpaper |
| `home.background.background_fade_duration`  | int    | `1200` ms        | How long the fade animation should be when a Wallpaper cycles                 |

### `notifications`

| Key                                             | Type  | Default        | What it does                                        |
|-------------------------------------------------|-------|----------------|-----------------------------------------------------|
| `notifications.toasts.notification_duration`    | float | `4.5` sec      | How long a notification stays on screen             |
| `notifications.toasts.notification_queue_delay` | float | `0.4` sec      | The delay in seconds between notifications in queue |
| `notifications.toasts.notification_position`    | enum  | `bottom-right` | Where should notifications appear                   |

### `plugins`

| Key                         | Type   | Default           | What it does                     |
|-----------------------------|--------|-------------------|----------------------------------|
| `plugins.media.username`    | string | `colin.a.bond`    | Your Username for your music API |
| `plugins.weather.timezone`  | string | `America/Chicago` | The timezone you're in           |
| `plugins.weather.latitude`  | float  | `41.2619` deg.    | The Latitude of your city        |
| `plugins.weather.longitude` | float  | `-95.8608` deg.   | The Longitude of your city       |

## Buttons

Every labelled button is an `ActionButton` (`src/ui/controls/buttons.py`): one
height, one icon size, one shared minimum width so that "Join" and "Disconnect"
in the same row come out the same size.

It exists because each page was making its own. A `QPushButton` with
`setFixedHeight(38)` written out at the call site drifts — one page used 38, the
next 44, a third left it to the layout — and a row of them ended up uneven for no
reason anybody chose. None of them carried an icon either, so a page of buttons
read as a wall of similar words.

A row in a **list** gets `row_menu()` — one glyph opening an
[action sheet](dialogs.md) — while a page devoted to a single thing keeps its
buttons.

Labels are the widest part of a button, so in a list they are what gets cut off
first on a narrow panel. In the sheet there is room for them, and room to say
what the action does to *this* item.

The Plugins overview is a list and uses the menu; a specific plugin's own page
keeps its buttons.

A page with a fixed set of actions uses `action_column()` — a tray that is always the same
number of slots wide, whatever it holds, padded on the left.

Rows in a list do not all carry the same actions: a saved network has Forget
beside Join, a new one only has Join. Right-aligning a varying count puts the
last button in a different place on every row and the column zigzags down the
page. **Every button being the same width does not fix that** — it is the count
that differs, not the size.

`kind` picks the **meaning**, and the palette follows from it:

|               |                                                 |
|---------------|-------------------------------------------------|
| `primary`     | the thing this row is for — Join, Connect, Save |
| `secondary`   | a reasonable alternative — Disconnect, Rename   |
| `destructive` | loses something — Forget, Revoke                |
| `quiet`       | navigation and toggles that change nothing      |

Turning something off is `secondary`, not `destructive`: it is reversible.

## A rebuild comes back to the section it was on

Revoking a user, unloading a plugin and saving a calendar all rebuild the whole
page with `goto("#settings", override=True)` and no data, because what they
changed is on the page.

The section is remembered on the **client** — `goto()` destroys the page, so
anything kept on `self` goes with it, and it is the rebuild that needs to know.
When no `section` is asked for, the page resumes there; a caller naming one still
goes where it says, which is how the quick panel's Wi-Fi and Bluetooth buttons
work.

Checked against the nav buttons that were actually built, so a section whose
plugin has since failed to load lands somewhere real rather than on a blank page.

## A section that fills the page

The content layout ends in a stretch, so every block takes its natural height
and the spare goes to the bottom. That is right for a column of setting cards
and wrong for a single view.

A widget with `fills_height = True` is inserted with a stretch factor instead.
The Logs section uses it — a log occupying 200px with empty space under it shows
about eight lines.

## A setting that renders as a picker

The page dispatches on **`type`**. `options` is read by `EnumComponent` and by
nothing else, so a setting declared `"str"` with a list of options renders as a
plain text box — the list is never consulted, and the box appears empty.

```json
"tts_voice": {
  "type": "enum",
  "default": "alba",
  "value": "alba",
  "options": ["alba", "anna", "vera"]
}
```

Two things a picker needs beyond that:

* **`value` and `default` must be in `options`.** The combo box selects by
  matching the value against the list; one that is not there leaves the box on
  whichever option is first, and saving the page then writes that one.
* **No blank option.** `format_name("")` is an empty string, so it renders as a
  row with nothing in it.

Option labels come from `format_name()`, so `bill_boerst` shows as
"Bill Boerst". The stored value is the raw option.

An enum cannot hold free text. Where both are wanted — a list to pick from *and*
a path to type — that is two settings, with the text one taking precedence when
it is set.

## Heights are measured

`styling.line_height(size, bold)` gives what a line of that font actually needs.
Anywhere a label is pinned to stop it stretching, the number comes from there.

A height picked by eye clips the descenders of anything larger than it was
chosen for, and from outside that does not look like a layout bug — it looks like
the font is wrong. The registry title is S3 bold, which needs 31px; it was pinned
at 28.

## Naming the panel

`application.panel_name`, edited in **Info**. `client.panel_name()` is the only
reader — empty falls back to the application name, so a panel nobody has named
still reads as something rather than as a blank heading.

The setting is marked `hidden`, so the generated Application section does not
show a raw text field beside the proper control. The control writes into the
page's **working copy** like every other control here: saved by the Save button,
discarded by leaving without one. Writing to the live settings instead would be
overwritten by the next Save anyway.

The control indexes the working copy **through the Settings object**, the way
`builder()` resolves a live setting — `working["application"]["panel_name"]`, not
`working.to_dict()[...]`. `to_dict()` rebuilds a fresh dict on every call, so a
control that mutates what it returns is writing into a throwaway: the value
looks accepted on screen and is gone at Save.

The name is collapsed to single spaces and capped at 64 characters, because it
lands in a window title and an HTML heading and a name with newlines in it is
neither.

Every page the panel serves gets it through a Flask **context processor** rather
than a keyword on each `render_template()`. There are five pages and adding a
sixth is how a heading ends up saying "Home Assistant" on a panel somebody named
something else.

## The sort toolbar

Shown only where the content carries at least two sort labels — the same test
`_sorted_content()` uses to do the sorting, so the toolbar cannot appear above
content it would not reorder.

Two rather than one: reordering a single card does nothing, and offering a
control that appears to have no effect invites the question of why.

The test is not `system=True`. Plugins is a system page and does sort; Wi-Fi,
Info and Users are live views whose cards carry no sort label at all.

## Plugin readmes

A plugin's readme appears on its settings page **folded away**, with a header
saying how many lines are in there. They run to pages, and a page that opens
with one expanded has pushed the settings somebody came for off the bottom of
the screen.

## The navigation

Two sections.

**System** holds the pages that exist in their own right — Users, Plugins,
Info. They are live views of a registry whose buttons act the moment you press
them.

**Settings** holds everything generated from the settings file, plus each
plugin's own section. Those are lists of values to change and save.

The two behave differently enough to be worth telling apart, and mixing them in
one list gives no clue which is which. `new_category(..., system=True)` puts an
entry in the first; the default is the second, so a plugin adding a category
lands where a reader expects.

### Debug mode

`debug.enabled` is one flag the whole app reads, rather than each plugin
inventing its own. Anything that wants a developer-only control, an extra log
line, or a way to force a state it would otherwise have to wait for should gate
it on this:

```python
if client.debug_mode():
    client.QUICK.register(...)
```

It is a method rather than an attribute so it follows the setting without
anything having to be told the setting changed. The Nighttime Clock uses it to
add environment switches — forcing rain or snow beats waiting for weather.

## Migration

Settings added by an update are folded into the existing data file at startup.
The template and the data file are compared on every launch: new keys arrive at
their defaults, values you have changed are kept, and keys the template no
longer declares are dropped.

The same applies to bundled plugin `settings.json` files, which are merged
rather than replaced when an update is applied - so your configured values
survive, and a new setting arrives at its default.

**This matters when copying files over an install by hand.** A hand-copied
`settings.json` replaces the file outright, including any `value` you had
changed.


## Reacting to changes

```python
def load(self, carryover=None):
    self.client.subscribe_to_event("on_settings_saved", self.on_saved)

def on_saved(self, event=None):
    self.interval = int(self.option("general.poll_interval", 30))
```

Fires once when the Settings page is saved, not per field. See
[Events](events.md).

Some client settings restart a subsystem on save rather than being re-read -
changing the assistant's model, microphone or wake word restarts the assistant,
and changing the update check interval restarts the checker.


## Adding cards to the Settings page

A plugin can add its own content to a settings category rather than only
declaring fields. The Settings page exposes these:

| Feature                                        | Does                                                                                            |
|------------------------------------------------|-------------------------------------------------------------------------------------------------|
| `new_category(name, ...)`                      | A top-level category. `system=True` puts it with the panel's own rather than among the plugins. |
| `new_subcategory(parent, name, controls, ...)` | A subcategory under one that already exists. Warns and does nothing if the parent is not there. |
| `insert_block(...)`                            | A card of your own inside a category.                                                           |
| `insert_plugin_block(...)`                     | The same, keyed to a plugin so it goes when the plugin does.                                    |
| `new_settings_list(...)`                       | The builder, for declaring fields from a list.                                                  |

```python
settings = client.PAGES.get_entry("#settings").instance
settings.features("new_subcategory")("plugins", "my_plugin", controls)
```
