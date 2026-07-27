# Notifications, state and assets

Three small client-level systems that plugins reach for constantly.

---

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

---

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

---

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
