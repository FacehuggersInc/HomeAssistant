# Events

Events let any part of the application — Client, plugins, pages — react to things happening elsewhere without being directly wired together.

There are two kinds.

## Client events

A fixed set of built-in events the Client fires itself, at predictable moments:

```text
initialized
on_focus
on_un_focus
on_visit
on_leave
on_update
on_minimize
on_maximize
on_fullscreen
on_state_change
on_close
on_settings_saved
on_woke_assistant
on_assistant_transcribed
on_plugin_reloading
on_plugin_unload
on_interaction
on_fresh_interaction
on_interaction_timeout
on_collection
```

Subscribe with `subscribe_to_event` / unsubscribe with `unsubscribe_from_event`:

```python
def my_handler(event):
    ...

self.client.subscribe_to_event("on_visit", my_handler)
self.client.unsubscribe_from_event("on_visit", my_handler)
```

Each handler receives one `event` argument — its shape depends on which event fired (some pass a dict with context, some pass a single value, some pass `None`).

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

This is what the Carousel plugin subscribes to in order to dismiss whatever it's currently showing the instant the user touches the screen again.

```python
def on_resumed(event):
    # something to do right as the user comes back
    ...

self.client.subscribe_to_event("on_fresh_interaction", on_resumed)
```

### `on_interaction_timeout`

Fired once per idle period, the moment `application.interaction_timeout` (milliseconds, under Application in Settings) is crossed with no interaction anywhere in the app. Fires exactly once — it won't fire again on every subsequent tick while still idle, only on the edge where idleness was first reached. `event` is always `None`.

Does **not** fire while the Settings page is active — that page manages its own separate idle/return-home timeout, and a plugin like the Carousel popping something up over Settings would be unwelcome.

Runs on the same background thread as `on_update`, not the Qt UI thread — if your handler touches any widgets, dispatch through `client.call_on_ui(...)` the same way you would in an `on_update` handler.

```python
def on_gone_idle(event):
    self.client.call_on_ui(self.show_something)

self.client.subscribe_to_event("on_interaction_timeout", on_gone_idle)
```

### `on_collection`

Fired once an hour, right before `gc.collect()` runs as part of the update thread's own housekeeping. `event` is always `None`.

This is your plugin's chance to self-manage — clean up anything you've accumulated since the last cycle before Python's garbage collector runs. Especially relevant if your plugin runs for a long time and builds things up over and over (a panel per Carousel rotation, a cache entry per request, etc.): use this to clear it out regularly instead of letting it grow for hours unchecked.

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

`create_on_call_event` and `trigger_on_call_event_iteration` will raise if you pass one of the built-in event names — those are reserved for the Client and must be triggered through its own internal calls, not from plugin code.

---
