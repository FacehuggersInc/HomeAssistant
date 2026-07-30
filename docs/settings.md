# Settings

Two layers, and they are not the same thing:

* **Client settings** live in the user data directory and are described by
  `src/assets/data/new-template.json`. These are the app's own settings.
* **Plugin settings** live in each plugin's `settings.json` and appear in the
  Settings page under that plugin.

Both use the same declaration format and the same widget builders.


## Reading and writing

```python
value = self.client.setting("assistant.model.value", "tiny.en")
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

| Type | Widget |
|---|---|
| `bool` | Toggle. |
| `int`, `float` | Numeric field, opens the numpad. |
| `string` | Text field, opens the QWERTY keyboard. |
| `body` | Multi-line text field. |
| `enum` | Dropdown. Requires an `options` list. |
| `path` | Text field with a Browse button. |
| `secret` | Masked field backed by `.env`. Requires an `env` key. |
| `list[int]`, `list[float]` | Comma-separated numeric field. |

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

```json
"model": {
  "type": "enum",
  "default": "tiny.en",
  "value": "tiny.en",
  "options": ["tiny.en", "base.en", "small.en"],
  "description": "Larger is more accurate and slower."
}
```

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

### `accessibility`

| Key | Type | Default | What it does |
|---|---|---|---|
| `accessibility.handles_open_on_touch` | bool | `off` | Drawer Handles instead of needing a swiping action, require just a touch to open |

### `application`

| Key | Type | Default | What it does |
|---|---|---|---|
| `application.interaction_timeout` | int | `60000` ms | Time of inactivity (no clicks/touches anywhere) before Client fires on_interaction_timeout. Used in the settings page to go home when no interaction has happened for a while. |
| `application.updates.check_interval` | int | `6` hrs | How often to ask GitHub whether a newer version exists. This is a single small request, not a download - nothing is fetched until you choose to update. Set to 0 to never check automatically. |
| `application.updates.crash_window` | int | `120` sec | If the app ran longer than this before crashing, the restart counter resets. A long-lived session that later dies is not a boot loop. |
| `application.updates.max_restart_attempts` | int | `5` | How many times in a row the launcher will restart after a crash before giving up. Prevents a boot loop when something is genuinely broken. |
| `application.updates.restart_on_crash` | bool | `on` | Whether the launcher automatically restarts the app if it crashes. Turn this off to have a crash simply stop the app. |
| `application.updates.update_grace_period` | int | `60` sec | If a freshly-applied update crashes within this window, the launcher rolls back to the previous version and relaunches it. |
| `application.window.auto_lock` | bool | `on` | Whether the app will automatically on startup, position at X and Y, then fullscreen |
| `application.window.position` | list[int] | `1074 x 1701` ['px', 'px'] | Where the window will position itself on startup |
| `application.window.size` | list[float] | `1920.0 x 1080.0` ['px', 'px'] | Set the size of the window. NOTE: this doesn't effect the actual size, its a reference |

### `assistant`

| Key | Type | Default | What it does |
|---|---|---|---|
| `assistant.enabled` | bool | `on` | Whether the voice assistant runs at all. Turn this off to stop the speech-to-text process entirely. |
| `assistant.input_device` | string | `(empty)` | Name of the microphone to listen on. Leave empty to use the system default. Available devices are listed in the Assistant section of the log at startup. |
| `assistant.model` | enum | `tiny.en` | Whisper model used for transcription. Larger is more accurate and slower; the '.en' variants are English-only and faster. Downloaded on first use. |
| `assistant.session_silence` | int | `800` ms | How long a pause ends your sentence once the assistant is in a conversation. Lower reacts faster; too low and a breath mid-sentence is treated as the end, which splits one question into several and sends each of them separately. 800ms suits normal speech. |
| `assistant.tts_enabled` | bool | `on` | Whether the assistant speaks its replies. Requires a voice backend in your .env file; skills stay usable without it, they just do not talk back. |
| `assistant.voice_bar` | bool | `on` | Show the thin activity bar along the bottom of the screen while the assistant is listening. |
| `assistant.voice_bar_hold` | int | `6` sec | Minimum time the activity bar keeps a transcript on screen. Longer transcripts are held longer than this automatically. |
| `assistant.wake_listen_timeout` | int | `12` sec | How long the panel keeps listening after the wake word before giving up. Too short and it stops mid-thought; too long and it sits listening to the room. |
| `assistant.wake_word` | string | `alexa` | The word that wakes the assistant. Plugins use this as the default wake word for their skills. Requires a restart of the assistant. |

### `home`

| Key | Type | Default | What it does |
|---|---|---|---|
| `home.background_cycle_interval` | int | `75` sec | The amount of time in seconds before the background cycles to a new Wallpaper |
| `home.background_fade_duration` | int | `1200` ms | How long the fade animation should be when a Wallpaper cycles |
| `home.date_format` | string | `%a, %b %d` | - |
| `home.images` | path | `C:\Home\Images` | Home Background's Path for Cycling Images |
| `home.media_player_position` | enum | `bottom-right` | Where should the media controls / player be positioned |
| `home.pinned` | path | ` ` | The Path to your Pinned Image for the Home Background |
| `home.show_normal_media_player` | bool | `off` | If the normal Media Player should show (The black box with Play / Pause, Next, and Previous) |
| `home.show_whats_playing` | bool | `off` | If the title of whats playing should show. This will be positioned above or below the media controls and will show if the media controls aren't showing |
| `home.time_format` | string | `%I:%M %p` | - |
| `home.widget_margin` | int | `28` | The Outer Margin of Widgets on the Home Page |

### `notifications`

| Key | Type | Default | What it does |
|---|---|---|---|
| `notifications.notification_duration` | float | `4.5` sec | How long a notification stays on screen |
| `notifications.notification_position` | enum | `bottom-right` | Where should notifications appear |
| `notifications.notification_queue_delay` | float | `0.4` sec | The delay in seconds between notifications in queue |

### `plugins`

| Key | Type | Default | What it does |
|---|---|---|---|
| `plugins.media.username` | string | `colin.a.bond` | Your Username for your music API |
| `plugins.weather.latitude` | float | `41.2619` deg. | The Latitude of your city |
| `plugins.weather.longitude` | float | `-95.8608` deg. | The Longitude of your city |
| `plugins.weather.timezone` | string | `America/Chicago` | The timezone you're in |

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

| | |
|---|---|
| `primary` | the thing this row is for — Join, Connect, Save |
| `secondary` | a reasonable alternative — Disconnect, Rename |
| `destructive` | loses something — Forget, Revoke |
| `quiet` | navigation and toggles that change nothing |

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
declaring fields. See [Features](features.md) for `new_category`,
`insert_block` and `new_settings_list`.
