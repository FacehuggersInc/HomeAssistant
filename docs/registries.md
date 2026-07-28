# Registries

Registries manage and store extendable, plugin-ownable objects — things like API endpoints or pages, that a plugin registers and expects to have cleaned up automatically when it's unloaded or reloaded.

Three concrete registries currently exist. They are not all shaped the same way.

## `APIRegistry` and `PageRegistry`

These two share the same shape:

```python
registry.register(owner, key, ...)
registry.unregister(owner, key="")
```

`owner` is the plugin's key (or `"client"` for things the Client itself owns, like `#root` and `#settings`). Registering something under your plugin's key means `PluginManager.unload_plugin()` cleans it up automatically when your plugin is unloaded or reloaded — you should not need to manually remove anything you registered this way (see Plugin Lifecycle → `unload()`).

```python
# APIRegistry — self.client.API_REGISTRY
self.client.API_REGISTRY.register("myplugin", "my_endpoint", self.my_callback, False, False)
self.client.API_REGISTRY.unregister("myplugin", "my_endpoint")

# PageRegistry — self.client.PAGES (wrapped by add_page, see [Pages](pages.md))
self.client.add_page("#mypage", "My Page", MyPage, owner="myplugin")
self.client.PAGES.unregister("myplugin", "#mypage")
```

## `PublicRegistry`

This one is shaped differently — `expose` / `unexpose` rather than `register` / `unregister`, since it's not registering a discrete thing with a lifecycle so much as just making a variable or object visible to everyone else:

```python
self.client.public.expose(owner, name, value, overwrite=False)
self.client.public.unexpose(owner, name)
```

```python
# PublicRegistry — self.client.public
self.client.public.expose("myplugin", "my_shared_state", self.my_shared_state)

# elsewhere, any other plugin can read it directly:
self.client.public.my_shared_state
```

Like the other two, anything exposed under your plugin's key is cleared automatically on unload via `self.client.public.clear(owner)` — you don't need to call `unexpose` yourself during a normal teardown.

---

# Reading settings from a thread

`client.SETTINGS` is a Dynaconf object, and Dynaconf's `reload()` empties its
store before repopulating it. A read from another thread landing in that gap
raises `AttributeError: 'Settings' object has no attribute 'APPLICATION'` --
intermittent, and only when a save happens to coincide with a background read.

Saving therefore goes through `client.apply_settings(values)`, which uses
`update()` instead: it only adds and overwrites, so no section is ever
momentarily absent.

From a background thread -- `on_update`, `on_collection`, a worker of your own
-- read through the accessor rather than touching `SETTINGS` directly:

```python
margin = self.client.setting("home.widget_margin.value", 28)
```

`setting()` takes a dotted path and a default, holds the settings lock, and
returns the default rather than raising if anything on the path is missing.
Direct `client.SETTINGS.x.y` access is still fine on the UI thread.


---

## `SecretRegistry` — `self.client.SECRETS`

API keys and anything else that should not sit in a settings file. Values live
in `.env` at the install root; the registry only knows the **names**.

A plugin declares the keys it needs in `plugin.toml`, or by registering them:

```python
self.client.SECRETS.register("myplugin", "MYSERVICE_KEY",
                             label="MyService API key")
```

Reads are **scoped to the owner**. A plugin can only read a key it declared:

```python
key = self.client.SECRETS.get_for("myplugin", "MYSERVICE_KEY", "")
```

| Method | Does |
|---|---|
| `register(owner, key, label="")` | Declare a key. |
| `get_for(owner, key, default="")` | Read, scoped. |
| `set_for(owner, key, value)` | Write to `.env`. |
| `is_set(key)` | Whether it has a value. |
| `is_declared(key)` | Whether anything declared it. |
| `status(key)` | A human-readable state for the Settings page. |
| `masked(key)` | The value with most of it replaced by asterisks. |
| `keys_for(owner)` | What an owner declared. |
| `clear(key)` | Remove it from `.env`. |

Never log a secret, and never put one in a notification. Use `masked()` when
you need to show that a key is present.

Declaring a key with a `secret` setting type gives you a masked field in
Settings for free — see [Settings](settings.md).

---

## `QuickAccessRegistry` — `self.client.QUICK`

Buttons in the [quick settings](quick-settings.md) panel. Owner-scoped like
the others, and cleared automatically when a plugin unloads.

```python
self.client.QUICK.register(
    "myplugin", "porch", "Porch", Icons.LIGHTBULB,
    on_press = self.toggle_porch,
    on_state = lambda: self.porch_on,
)
```

Full detail on that page.
