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

Position comes from `notifications.notification_position` in Settings, so do
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

### Which to use

A notification **reports**; an answer panel **answers**. The test is whether
there is anything to read.

| Use a panel | Use a notification |
|---|---|
| The reply has parts — a time, a place, a length | The reply is one fact |
| A list: today's events, the next few hours | Something happened in the background |
| Somebody asked a question and is standing there | Nothing is waiting on it |

The weather skill is a panel: six lines, and a toast goes past before anyone
has read the second one. Clearing notifications is a notification: the action
*is* the answer, and there is nothing to read afterwards.

### Speaking

`speak=` says the text as well as showing it. Text-to-speech needs an
the voice backend key and a panel without one is a normal install, so an answer that
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

| | |
|---|---|
| `accessibility.do_not_disturb` | No notifications, no sounds, no speech |
| `accessibility.mute_sounds` | No sounds and no speech; notifications still appear |

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
