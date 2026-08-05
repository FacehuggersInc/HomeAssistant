# Notifications, state and assets

Three small client-level systems that plugins reach for constantly.


## Notifications

```python
self.client.simple_notify("check", "Timer", "10 minutes, starting now.")
```

`simple_notify(icon, title, body, history=True)`. The icon is an `Icons`
constant or any `mdi.` name. Toasts queue, so several in a row appear one
after another rather than on top of each other.

`history=False` shows the toast without recording it. Use it for things that
are noise in a list - progress ticks, repeated status - and leave it `True`
for anything worth finding again later.

History keeps the most recent **50** entries. The panel renders the list in
full every time it opens, so an uncapped history is both memory that never
comes back and a build on the UI thread that grows with the uptime of the
panel.

Position comes from `notifications.toasts.notification_position` in Settings, so do
not assume a corner.

Safe to call from any thread.

### When to use one

A notification is the right answer when something happened that the person did
not ask for and would want to know about: an update arrived, a request failed,
a plugin could not start.

It is the wrong answer for something they *did* ask for and are watching - use
a dialog, or update the thing in front of them. A panel that toasts on every
interaction trains people to ignore the toasts.

For anything needing a response, use `client.alert()` or `client.confirm()`
instead. See [Dialogs and overlays](dialogs.md).


## Answer panels

```python
self.client.answer(
    "mdi.weather-sunny", "72 degrees",
    ["Feels like: 74\u00b0", "Wind: 7 mph", "Humidity: 41%"],
    tint="#3f7fbf",
    speak="72 degrees.",
)
```

A panel with an icon, a headline and however many detail lines the answer has.
Closes itself after 30 seconds, and on a tap.

**It grows to hold what it is given.** The headline wraps, so a long event
title makes the card taller — and wider first, since wrapping is what makes a
card tall and the same title needs half the height in a wider one. It stops at
the screen; an answer that still does not fit is trimmed and says so in the
log rather than quietly losing its last lines.

**Only one is ever up.** Every answer is the same card in the same corner, so
a second one would land on top of the first rather than beside it — and
asking two things in a row is ordinary rather than a race, since the panel
stands for thirty seconds and nothing about it suggests waiting. Opening an
answer takes down whichever one was there, and that panel's `on_closed` fires
the same as if it had been tapped. Only answers: a conversation panel or the
notification centre is a different thing in a different place, and an answer
arriving is no reason to take it away.

### Which to use

A notification **reports**; an answer panel **answers**. The test is whether
there is anything to read.

| Use a panel                                     | Use a notification                   |
|-------------------------------------------------|--------------------------------------|
| The reply has parts — a time, a place, a length | The reply is one fact                |
| A list: today's events, the next few hours      | Something happened in the background |
| Somebody asked a question and is standing there | Nothing is waiting on it             |

The weather skill is a panel: six lines, and a toast goes past before anyone
has read the second one. Clearing notifications is a notification: the action
*is* the answer, and there is nothing to read afterwards.

### Speaking

`speak=` says the text as well as showing it. Replies can be turned off in
Settings and a backend can fail to load, so an answer that
is only spoken is an answer half the time — every skill should show as well as
say.

If the panel cannot be built, `answer()` falls back to a notification rather
than losing the reply.

`tint` colours the gradient, `timeout=0` keeps it open until it is tapped.


## State

A flat dictionary on the client for cross-plugin flags that are not settings -
runtime facts, not configuration, and not persisted.

```python
self.client.set_state("myplugin_ready", True)

if self.client.get_state("home_page_setup"):
    ...
```

`get_state()` returns `None` for anything unset, so treat that as false rather
than assuming a key exists.

Prefix keys with your plugin key. The dictionary is global and there is no
ownership, so two plugins choosing `"ready"` will overwrite each other.

Use this for "has X happened yet" coordination. For anything a plugin needs to
call, expose a Feature or use the [public registry](registries.md) - those
carry behaviour, where state only carries a value.


## Assets

An asset is a file or folder a plugin registers so the rest of the app - and
the API - can reach it by key.

```python
self.client.register_asset("myplugin_sounds", path, forced_type="FOLDER")
```

Assets are bucketed by type. A file is filed under its extension (`PNG`,
`JSON`, `MP3`), a folder under `FOLDER`, and `forced_type` overrides both.

```python
asset = self.client.asset("PNG", "myplugin_logo")
```

`asset(type, key)` returns `None` when nothing matches, so check before use.

### Over the API

Registered assets are reachable from the network, which is the point of
registering them rather than keeping a path:

```
GET  /asset/<key>                 list what is under a key
GET  /asset/<key>/<filename>      download one
GET  /upload/<key>                an upload form for that key
POST /upload/<key>                receive a file
```

That is how a phone puts a wallpaper on the panel without a file share. Full
detail in [Backend API](api.md).

**Do not register a folder you would not hand to anyone on the network.** The
upload and asset endpoints are authenticated, but a registered folder is a
folder the API can read from and write into.

### Where to put files

User data belongs in `get_data_dir(APP_NAME)`, not the app tree. Anything
written inside the install is wiped when an update is unpacked over it -
`UPDATE_PRESERVE` covers `plugins/`, `.env` and logs, and nothing else.

```python
from src.constants import get_data_dir, APP_NAME

path = get_data_dir(APP_NAME) / "myplugin"
path.mkdir(parents=True, exist_ok=True)
```

## Quiet modes

Two, and they are not the same thing.

|                              |                                                     |
|------------------------------|-----------------------------------------------------|
| `audio.quiet.do_not_disturb` | No notifications, no sounds, no speech              |
| `audio.quiet.mute_sounds`    | No sounds and no speech; notifications still appear |

Do not disturb implies silence. Silence does not imply do not disturb — a quiet
panel that still shows what happened is the common case on a desk.

**Nothing is lost.** A notification held back by do not disturb is still written
to the history: the point is not to be interrupted, not to forget. A caller can
pass `urgent=True` to be shown anyway, for anything that genuinely cannot wait.

Both are read from the setting rather than held on the client, so the quick
settings buttons and the settings page cannot disagree. Turning either on stops
whatever is currently playing, because silence that starts after the current
sound finishes is not silence.

The Silence button reads as **on** while do not disturb is holding it — it is
silent, and a button saying otherwise beside a quiet panel is the button being
wrong. Pressing it then explains why nothing changed instead of appearing to do
nothing.

### Asking, and setting

```python
client.do_not_disturb()          # notifications and sounds are held back
client.sounds_muted()            # no sound or speech; true if DND is on
client.set_do_not_disturb(True)
client.set_sounds_muted(True)
```

Both read the setting rather than a flag on the client, so the quick settings
buttons and the settings page cannot disagree. Turning either on stops whatever
is currently playing — silence that begins after the current sound finishes is
not silence.

`sounds_muted()` is the one to check before making a noise; it already accounts
for do-not-disturb, so a caller does not have to ask twice.

**`say()` reports whether a person heard it**, not whether the backend was
called — muted returns `False`, the same as no voice installed. Anything that
falls back to showing a message reads that answer, so the fallback fires when
the panel is silent:

```python
if not client.say(text):
    client.simple_notify("assistant", "Assistant", text)
```
