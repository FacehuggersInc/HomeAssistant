# Events

Events let any part of the application — Client, plugins, pages — react to
things happening elsewhere without being directly wired together. Subscribe to
a name, and whatever fires it does not need to know you exist.

## An event has to exist before anything subscribes

`subscribe_to_event()` indexes straight into `EVENTS["on_call"]`, so a name
that was never registered raises `KeyError` at plugin load - and that is not
one plugin failing, it is the app not starting.

Two ways a name becomes real:

- **Listed in the core dict** in `main.py`. For events the panel itself fires.
- **`client.create_on_call_event("name")`** at load time. For an event a
  plugin owns - the calendar does this for `on_calendar_changed`, guarded by
  an `in` test so a reload does not clear the subscribers.

Adding a `subscribe_to_event` without doing one of those is the mistake, and
`check_events.py` catches it by comparing every name used against every name
declared.

## Client events

A fixed set of built-in events the Client fires itself, at predictable moments.

| Event                      | Fires when                                              | `event` is                      |
|----------------------------|---------------------------------------------------------|---------------------------------|
| `initialized`              | Once, after every plugin's `built()` has run.           | `None`                          |
| `on_visit`                 | A page has been shown.                                  | `{"page": {"name", "data"}}`    |
| `on_leave`                 | A page is about to be torn down.                        | `{"from": {...}, "to": {...}}`  |
| `on_update`                | Every client tick.                                      | `None`                          |
| `on_collection`            | Periodically, for plugins to clean up after themselves. | `None`                          |
| `on_focus`                 | The window gained focus.                                | The Qt event                    |
| `on_un_focus`              | The window lost focus.                                  | The Qt event                    |
| `on_minimize`              | The window was minimised.                               | The Qt event                    |
| `on_maximize`              | The window was maximised.                               | The Qt event                    |
| `on_fullscreen`            | Fullscreen was entered or left.                         | The Qt event                    |
| `on_close`                 | The window is closing.                                  | The Qt event                    |
| `on_key`                   | A key was pressed.                                      | The Qt event                    |
| `on_web_event`             | Something happened in the web page.                     | `{"kind", "url", "title", ...}` |
| `on_state_change`          | `set_state()` changed a value.                          | The state name                  |
| `on_settings_saved`        | The Settings page was saved.                            | `None`                          |
| `on_interaction`           | Any mouse or touch interaction.                         | The raw Qt event                |
| `on_fresh_interaction`     | Interaction resumed after idleness.                     | The raw Qt event                |
| `on_interaction_timeout`   | Idleness began.                                         | `None`                          |
| `on_woke_assistant`        | The wake word was heard.                                | The wake word                   |
| `on_assistant_transcribed` | A phrase was transcribed.                               | The transcript                  |
| `on_assistant_cancelled`   | The user cancelled mid-conversation.                    | `None`                          |
| `on_assistant_fallback`    | A phrase matched no skill.                              | The transcript                  |
| `on_plugin_reloading`      | A plugin is about to reload.                            | The plugin key                  |
| `on_plugin_unload`         | A plugin is unloading.                                  | The plugin key                  |

Subscribe with `subscribe_to_event` / unsubscribe with `unsubscribe_from_event`:

```python
def my_handler(event):
    ...

self.client.subscribe_to_event("on_visit", my_handler)
self.client.unsubscribe_from_event("on_visit", my_handler)
```

Each handler receives one `event` argument — its shape is in the table above.

**A handler that raises is unsubscribed.** The event bus drops it rather than
letting it throw on every future fire, so an exception in a handler silently
disables it for the rest of the session. Wrap anything that can fail.

Always unsubscribe in `unload()`. A handler pointing into an unloaded module
is an exception the first time the event fires.


## Examples

### `initialized`

Everything exists. This is the latest safe point to touch anything.

```python
def load(self, carryover=None):
    self.client.subscribe_to_event("initialized", self.on_ready)

def on_ready(self, event=None):
    self.client.log("info", f"[MyPlugin] {len(self.client.get_pages())} pages registered.")
```

### `on_visit` / `on_leave`

```python
def on_visit(self, event):
    if event["page"]["name"] == "#myhome":
        self.start_polling()

def on_leave(self, event):
    self.client.log("debug",
        f"[MyPlugin] {event['from']['name']} -> {event['to']['name']}")
    self.stop_polling()
```

`on_leave` fires *before* the old page is destroyed, so its widgets are still
valid. `on_visit` fires after the new one is up.

### `on_update`

Every tick, on the UI thread. Do almost nothing here.

```python
def on_update(self, event=None):
    if self._dirty:
        self._dirty = False
        self.refresh_label()
```

Guard the work behind a flag. An unconditional body here runs at tick rate
forever.

### `on_settings_saved`

Fires once per save, not per field. Re-read what you care about.

```python
def on_saved(self, event=None):
    self.interval = int(self.option("general.poll_interval", 30))
    self.client.log("info", f"[MyPlugin] Interval now {self.interval}s")
```

### `on_state_change`

```python
def on_state(self, state_name):
    if state_name == "home_page_setup":
        self.attach_to_home()
```

### `on_key`

```python
from PyQt6.QtCore import Qt

def on_key(self, event):
    if event.key() == Qt.Key.Key_F5:
        self.refresh()
```

### `on_focus` / `on_un_focus` / `on_minimize` / `on_maximize` / `on_fullscreen` / `on_close`

Window state. Useful for pausing work nobody is looking at.

```python
def on_un_focus(self, event):
    self.client.THREADS.stop("myplugin_poller")

def on_focus(self, event):
    self.client.THREADS.start("myplugin_poller")
```

`on_close` fires as the window goes. Treat it as a last chance to flush, not a
place to start anything.

### `on_woke_assistant` / `on_assistant_transcribed` / `on_assistant_cancelled`

```python
def on_woke(self, wake_word):
    self.dim_music()

def on_transcribed(self, phrase):
    self.client.log("debug", f"[MyPlugin] Heard: {phrase}")

def on_cancelled(self, event=None):
    self.restore_music()
```

### `on_assistant_fallback`

Nothing matched. This is where the AI fallback plugin hooks in, and where you
would put your own catch-all.

```python
def on_fallback(self, phrase):
    if "porch" in phrase.lower():
        self.porch_on()
```

### `on_plugin_reloading` / `on_plugin_unload`

Another plugin is going away. React if you were depending on it.

```python
def on_other_unload(self, plugin_key):
    if plugin_key == "weather":
        self.reading.setText("Weather plugin unloaded.")
```

### `on_plugin_reloading`

Fired right before `PluginManager.reload_plugin()` does anything else — before `unload()` is even called on the plugin being reloaded. `event` is the **plugin key being reloaded**, as a plain string.

This exists so other plugins can react to a plugin going away before it actually does — pause something that depends on it, detach a feature it registered onto your page, show a temporary message — rather than discovering it's gone after the fact with no warning.

```python
def on_other_plugin_reloading(plugin_key: str):
    if plugin_key == "corewidgetsbundle":
        # do something before it tears itself down
        ...

self.client.subscribe_to_event("on_plugin_reloading", on_other_plugin_reloading)
```

### `on_plugin_unload`

Fired before `PluginManager.unload()` does anything else. Useful for utill or lib plugins that might want to handle plugins under their management when they unload.

This will also trigger when plugins reload because unload is triggered during a reload.

```python
def on_other_plugin_unloading(plugin_key: str):
    del self.store[plugin_key]

self.client.subscribe_to_event("on_plugin_unload", on_other_plugin_unloading)
```

### `on_interaction`

Fired on every mouse/touch interaction anywhere in the app — a mouse press, a mouse move, or a touch begin/update/end. `event` is the raw Qt event.

Subscribe to this rather than installing your own `QObject` event filter on `client.app` — watching for activity is a Client-level concern, and one filter serving every subscriber is cheaper than one per plugin.

Fires constantly while the user is active — if you only care about activity *resuming* after a period of idleness, use `on_fresh_interaction` instead.

```python
def on_any_interaction(event):
    ...

self.client.subscribe_to_event("on_interaction", on_any_interaction)
```

### `on_fresh_interaction`

Fired on the same interactions as `on_interaction`, but **only** for the first one after a period of idleness — i.e. exactly the moment activity resumes following an `on_interaction_timeout`. Every subsequent interaction during that same active stretch fires `on_interaction` only, not this.

This is what [Idle Random Triggers](bundled-plugins.md) subscribes to, so whatever it is showing is dismissed the instant the screen is touched again.

```python
def on_resumed(event):
    # something to do right as the user comes back
    ...

self.client.subscribe_to_event("on_fresh_interaction", on_resumed)
```

### `on_interaction_timeout`

Fired once per idle period, the moment `application.interaction_timeout` (milliseconds, under Application in Settings) is crossed with no interaction anywhere in the app. Fires exactly once — it won't fire again on every subsequent tick while still idle, only on the edge where idleness was first reached. `event` is always `None`.

Does **not** fire while a dialog is open, or on any page that sets
`blocks_idle = True` — see [The web page](webpage.md). Also does
**not** fire while the Settings page is active — that page manages its own idle/return-home timeout, and an idle plugin popping something up over Settings would be unwelcome.

Runs on the same background thread as `on_update`, not the Qt UI thread — if your handler touches any widgets, dispatch through `client.call_on_ui(...)` the same way you would in an `on_update` handler.

```python
def on_gone_idle(event):
    self.client.call_on_ui(self.show_something)

self.client.subscribe_to_event("on_interaction_timeout", on_gone_idle)
```

### Resetting the clock without an interaction

`client.reset_interaction_timeout()` treats now as the last interaction, for
something the panel did that a person is expected to look at - a timer
finishing, an alarm. Nobody has touched the screen, but going to sleep over the
answer it just produced is not what anyone wants.

By default it also brings the panel **out** of idle if it is already there,
firing `on_fresh_interaction` so an idle plugin dismisses whatever it was
showing. Pass `wake=False` to push the clock without waking anything.

Safe from any thread - the event is marshalled, because `on_fresh_interaction`
subscribers close panels and touch widgets.

### `on_collection`

Fired once an hour, right before `gc.collect()` runs as part of the update thread's own housekeeping. `event` is always `None`.

This is your plugin's chance to self-manage — clean up anything accumulated since the last cycle before Python's garbage collector runs. Especially relevant for a plugin that runs for a long time and builds things up over and over: a panel per idle rotation, a cache entry per request. Clear it out here rather than letting it grow for hours unchecked.

If you're holding onto any PyQt6 objects (a `Panel`, a widget, anything `QObject`-based), make sure they're actually cleaned up here if nothing else already does it for you — just dropping the Python reference isn't enough on its own.

Runs on the same background thread as `on_update`/`on_interaction_timeout`, not the Qt UI thread — dispatch through `client.call_on_ui(...)` if your handler needs to touch a widget.

```python
def on_hourly_collection(event):
    self._stale_cache.clear()

self.client.subscribe_to_event("on_collection", on_hourly_collection)
```

## Custom events

Plugins can also define and fire their own event names — anything not in the built-in list above.

```python
self.client.create_on_call_event("my_custom_event")

self.client.trigger_on_call_event_iteration("my_custom_event", some_data)
```

Other plugins subscribe to a custom event exactly the same way as a built-in one, via `subscribe_to_event`.

**Declare it before anything subscribes.** `subscribe_to_event` indexes
straight into the event table, so subscribing to a name that has not been
created is a `KeyError`, not a quietly ignored subscription. Create it in your
plugin's `load()`, which runs before any page that might listen is built.

Guard the creation if your plugin can be reloaded — `create_on_call_event`
resets the subscriber list, so re-creating an event that another plugin is
already listening to silently drops them:

```python
if "my_custom_event" not in self.client.EVENTS["on_call"]:
    self.client.create_on_call_event("my_custom_event")
```

`create_on_call_event` and `trigger_on_call_event_iteration` will raise if you pass one of the built-in event names — those are reserved for the Client and must be triggered through its own internal calls, not from plugin code.

---

## `on_web_event`

One event for everything the web page does, rather than one event per thing.
A subscriber wanting two of them would otherwise register twice, and a new kind
later would be a new event name that nothing is listening for.

```python
self.client.subscribe_to_event("on_web_event", self._on_web_event)

def _on_web_event(self, payload=None):
    if not isinstance(payload, dict):
        return
    if payload.get("kind") != "bookmarked":
        return
    url = payload.get("url", "")
```

| `kind`         | Sent when                                             |
|----------------|-------------------------------------------------------|
| `loaded`       | A page finished loading. `ok` says whether it worked. |
| `error`        | A page failed to load.                                |
| `changed`      | The address changed, including from a link.           |
| `home`         | The home button was pressed.                          |
| `refreshed`    | The page was reloaded.                                |
| `bookmarked`   | The star was pressed on a page that was not saved.    |
| `unbookmarked` | The star was pressed on one that was.                 |

Every payload carries `kind`, `url` and `title`. A kind not in
`Client.WEB_EVENTS` is refused rather than delivered, so a typo is a log line
rather than a subscriber that silently never fires.

Fired by the client, not a plugin: the web page belongs to the client, and a
plugin unloading should not take the event with it.
