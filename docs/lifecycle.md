# Application lifecycle

From launch to exit, and what runs where.

---

## Launcher first

Run `launcher.py`, not `app.py`.

`app.py` is the application. `launcher.py` is the supervisor around it: it
starts the app as a subprocess, reads the exit code, and decides what happens
next. Applying an update, restarting, restarting after a crash and rolling
back a bad update all live there — because none of them can be done by a
process that has to stop in order for them to happen.

Running `app.py` directly works, and it re-executes itself for updates and
restarts instead. What you lose is crash recovery and the update grace period.

---

## Startup

1. **`Client()` is constructed.** Settings are loaded and migrated, the client
   ID is read or generated, registries are created, the log file is opened and
   the Qt application and window exist but are not shown.

2. **Plugins are loaded.** `PluginManager` scans the bundled and user plugin
   directories, reads every `plugin.toml`, resolves dependencies into a load
   order, imports each module and calls `load()`.

3. **`build()` runs.** The window is configured and sized, fonts registered,
   the page host and overlay layer parented and shown, then quick settings, the
   dimmer, the edge gesture and the update checker are built. The window is
   shown and `BUILT` becomes `True`.

4. **`built()` is called on every plugin**, on the UI thread. UI exists by
   now, which is why anything touching a page belongs here rather than in
   `load()`.

5. **The `initialized` event fires**, once.

6. **The default page is shown.** See below.

7. **Deferred work starts.** Plugin dependency prompts at 1.2s, the voice
   assistant at 1.6s, backend services alongside. These are staggered so a
   slow subsystem does not hold up first paint.

See [Plugins](plugins.md) for the plugin side of this in detail.

---

## The default page

`client.DEFAULT_PAGE` decides where the app lands.

```python
class MyPlugin(Plugin):
    def load(self, carryover=None):
        self.client.add_page("#myhome", "My Home", MyHomePage, owner="myplugin")
        self.client.DEFAULT_PAGE = "#myhome"
```

Set it in `load()`. Navigation happens after `built()`, so `load()` is early
enough and `built()` still works.

If it is unset, or names a page nothing registered, the app falls back to
`#root` and logs which of the two happened:

```
No default page set — showing RootPage
Default page '#myhome' not registered — showing RootPage
```

`RootPage` and the settings page are registered by the client itself, not by a
plugin. That is the guarantee: **with no plugins at all, the app still starts,
still shows a page, and still reaches Settings.** A plugin that fails to load
cannot leave you with a blank screen and no way in.

The bundled `CoreWidgetsBundle` sets the default to its own home page, which
is why a normal install lands there instead.

---

## Running

The client runs a tick loop alongside the Qt event loop. On each tick it:

* calls `tick()` on the current page, if it has one
* checks whether the window has been resized and re-lays out if so
* fires `on_update`
* periodically fires `on_collection` for plugins to clean up after themselves

Keep `tick()` cheap. It runs on the UI thread, so anything slow in it is
dropped frames.

---

## Page switching

`client.goto(key)` is the only way pages change.

```python
self.client.goto("#settings")
self.client.goto("#myhome", data={"tab": "overview"})
```

In order, `goto()`:

1. returns immediately if that page is already current, unless `override=True`
2. refuses and logs if the key is not registered — it does not raise
3. sets `SWITCHING_PAGE`
4. calls `stop()` on the outgoing page if it has one
5. fires `on_leave` with both the old and new page and their data
6. hides, unparents and deletes the outgoing page
7. applies mixins to the incoming page class, constructs it, parents and sizes
   it, shows it
8. raises the overlay layer back above it
9. calls `start()` on the new page if it has one
10. clears `SWITCHING_PAGE` and fires `on_visit`

The important consequence: **the outgoing page is destroyed, not hidden.** Only
one page instance exists at a time. Anything a plugin added to a page is gone
when you navigate away, which is why widgets are registered rather than
constructed and handed over — the page rebuilds them from saved layout on the
way back in.

`data` is available to the new page as `self.data`, and is how you pass state
across a navigation without a global.

`client.is_switching_page()` is worth checking in anything that runs on a
timer and touches `client.PAGE`.

---

## Shutting down

`client.stop()` begins an orderly shutdown: plugins are unloaded, the
assistant is stopped and its process waited on, background threads are
signalled, the log is flushed and closed.

The exit code is the message to the launcher:

| Code | Means |
|---|---|
| `0` | Clean exit. Do not relaunch. |
| `42` | A staged update is waiting. Apply it, then relaunch. |
| `43` | Relaunch as-is. |
| `44` | Rollback requested. |

`client.RESTART = True` produces `EXIT_RESTART` (43). `client.UPDATE = True`
produces `EXIT_UPDATE` (42). Both then call `stop()`; the flag is what decides
what the launcher does next.

Anything else — a crash, a kill — is handled by the launcher's crash policy.
See [Updating](updating.md) for restart limits, the crash window and the
update grace period.

---

## What runs where

| Runs on | What |
|---|---|
| UI thread | `built()`, page construction, `tick()`, timeout callbacks, anything through `call_on_ui` |
| Plugin load thread | `load()`, `unload()` |
| Worker threads | Skill functions, API calls, `THREADS` entries |
| Separate process | The Whisper STT process |
| Separate thread | The Flask backend |

`load()` runs before the UI exists; `built()` runs after. That distinction is
the source of most "my widget did not appear" problems.
