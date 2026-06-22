# Home Assistant

A highly customizable smart home dashboard built with Python and PyQt6.

At its core, this project is essentially a giant plugin system disguised as a Home Assistant application.

The goal is to create a desktop based smart display similar to a Google Home Hub or Echo Show while remaining extremely customizable. Nearly everything visible on screen can be added, modified, or removed entirely through plugins.

The Client intentionally contains very little hardcoded functionality. Most functionality is provided by plugins which build the application at runtime.

This project is still a work in progress and will continue to evolve over time.

---

# Features

* Plugin driven architecture
* Dynamic page system
* Feature driven page extensions
* Widget framework
* Tile framework
* Mixin system
* Public registry system
* API registry system
* Page registry system
* Event system
* Plugin hot reloading with state carryover
* Flask backend API
* Optional voice assistant support

---

# Installation

## Clone the repository

```bash
git clone https://github.com/FacehuggersInc/HomeAssistant.git

cd HomeAssistant
```

## Create a virtual environment

### Windows

```bash
python -m venv .venv

.venv\Scripts\activate
```

### Linux

```bash
python3 -m venv .venv

source .venv/bin/activate
```

## Install dependencies

```bash
pip install -r requirements.txt
```

## Run the application

```bash
python app.py
```

---

# Project Overview

The project is separated into two major systems.

```text
Client Application
│
├── Plugin Loader
├── Registries
├── Pages
│   └── Features
│       └── Widgets
│       └── Tiles
└── Mixins

Flask Backend API
│
└── External communication
```

## Client Application

The Client Application is the actual PyQt application.

Its responsibility is to coordinate systems, not own them.

Most functionality should exist inside plugins.

The Client is responsible for:

* Loading plugins
* Building pages
* Coordinating features
* Managing widgets
* Managing tiles
* Managing public data
* Managing APIs
* Managing application state

## Flask Backend API

`backend.py` is a separate Flask application used for external communication.

This backend should be thought of as a server and not part of the Client Application itself.

## Core Concepts, briefly

Everything in the tree above boils down to a handful of ideas, each covered in full further down this document:

* **Plugins** provide functionality.
* **Pages** own UI systems.
* **Features** expose extensibility for Pages and sub-systems.
* **Widgets & Tiles** are reusable UI components, usually added via Pages and their Features.
* **Mixins** rigidly extend existing behavior.
* **Registries** manage and store extendable, plugin-ownable objects — see `PublicRegistry`, `APIRegistry`, and `PageRegistry` below.
* **Events** let any part of the application react to things happening elsewhere.

Keep these in mind as you read on — nearly everything else in this document is one of these six ideas in more detail.

---

# Plugins

Plugins are the primary way to extend the application.

A plugin can:

* Register pages
* Register APIs
* Expose public data
* Add widgets
* Add tiles
* Extend existing pages
* Extend existing behavior
* Add entirely new functionality

If you find yourself modifying the Client itself, consider whether it should instead exist as a plugin.

---

# Plugin Structure

Every plugin requires two files.

```text
MyPlugin/

plugin.toml

main.py
```

## plugin.toml

`plugin.toml` is required.

At minimum it must contain:

```toml
[plugin]
name = "My Plugin"
key = "myplugin"
```

And if you want editable settings that Users can interact with, in that same toml file:

```toml
[settings]
path = "/path/to/.json"
```

* `name` = Display name
* `key` = Unique identifier

* `path` = a json file

the settings path will be joined into the default settings page under plugins for Public settings.

### Load order and dependencies

Two more optional fields under `[plugin]` control the order plugins load in:

```toml
[plugin]
name = "My Addon"
key  = "myaddon"
order = 10
dependencies = ["corewidgetsbundle"]
```

* `order` — an integer. Lower loads first. Defaults to `0` if omitted. Only matters as a **tiebreaker** between plugins that have no dependency relationship to each other — a real dependency always takes priority over `order` alone.
* `dependencies` — a list of other plugins' `key` values. Every key listed here is guaranteed to load before this plugin does, as long as it actually exists and there's no circular dependency.

`PluginManager` resolves the final load order automatically at startup using these two fields together: dependencies first, `order` to break ties among everything else. A plugin with a missing or invalid `plugin.toml` doesn't block any other plugin from loading — it's just scheduled last, with a warning logged. A circular dependency is handled the same way: logged as a warning, then loaded in a best-effort order rather than refusing to start.

You don't need to declare `order` or `dependencies` at all unless load order actually matters for your plugin — most plugins can omit both fields entirely.

## main.py

`main.py` is the required entrypoint.

This is where your Plugin class lives.

Your plugin class needs to inherit the Plugin class from src/plugin/template

Plugins interact with the Client through `self.client`. 

They can also interact with their loaded settings via self.settings.path.to.setting. 
Due note that settings require to be set like this:
```python
self.settings['path']['to']['setting'] = new_setting
```

Example:

```python
class MyPlugin(Plugin):

    def __init__(self):
        pass

    def load(self, carryover=None):
        pass

    def built(self):
        pass

    def reload(self, carryover=None):
        pass

    def unload(self, carryover=None):
        pass
```

---

# Plugin Lifecycle

Plugins go through multiple stages during their lifetime.

Each stage has a different responsibility.

## `__init__()`

`__init__()` runs while the Client Application is initializing and plugins are being instantiated.

The application has **not been built yet**.

Use this for:

* Creating variables
* Loading JSON files
* Loading configuration files
* Loading assets
* Loading templates
* Initializing external libraries

Avoid:

* Accessing pages
* Accessing features
* Adding widgets
* Adding tiles
* Interacting with built UI

Nothing inside `__init__()` should depend on the Client already existing.

Think of this stage as preparation only.

---

## `load()`

`load()` runs once the Client Application is available.

Use this stage to register systems and connect your plugin to the application.

Typical tasks:

* Register pages
* Register APIs
* Expose public data
* Connect systems together

Anything that interacts with the application structure should happen here.

During a hot reload, `load()` receives the same `PluginCarryover` object your previous instance's `unload()` was given — use it to restore anything you stashed there. On the very first load when the application starts, `carryover` is `None`, since nothing has ever been unloaded yet.

---

## `built()`

`built()` runs once the entire application has been built.

This is where plugins should interact with live systems and built UI, though, due note you can still do some of this via page features in the load function, especially if you are just adding UI.

Examples:

* Accessing pages
* Using page features
* Adding widgets
* Adding tiles
* Modifying drawer controls
* Interacting with active interfaces

Anything that depends on UI already existing should happen here.

---

## `reload()`

Plugins can be reloaded without restarting the entire application.

Typical flow:

```text
unload()

destroy plugin

create plugin

__init__()

load()

built()

reload()
```

`reload()` receives the same `PluginCarryover` object `load()` did for this reload cycle, in case you'd rather restore state here instead of in `load()`.

---

## `unload()`

`unload()` runs before a plugin is unloaded or reloaded.

Everything manually created should be manually cleaned up.

Examples:

```python
timer.stop()

signal.disconnect(...)
```

Things added through registries do not need to be manually removed.

Only undo things that you explicitly created yourself.

### Carrying state across a reload

If you need something to survive being unloaded and reloaded — open connections, in-memory caches, runtime state that shouldn't live in `settings.json` — use the `carryover` argument:

```python
def unload(self, carryover=None):
    if carryover:
        carryover.set("cache", self.cache)
        # do NOT stop/close it — it needs to survive into the next load()

def load(self, carryover=None):
    if carryover and carryover.has("cache"):
        self.cache = carryover.get("cache")
    else:
        self.cache = {}   # first-ever load, nothing to restore
```

### Controlling navigation during a reload

By default, `PluginManager.reload_plugin()` navigates back to whichever page was on screen before the unload (or `#root` if that page no longer exists) once your plugin is reloaded. If your plugin would rather decide that for itself — for example, navigating somewhere specific from `load()`, `built()`, or `reload()` — set the reserved `handled_navigation` key to `True` from `unload()`:

```python
def unload(self, carryover=None):
    if carryover and <some condition>:
        carryover.set("handled_navigation", True)
```

`unload()` is the only lifecycle hook that runs *before* `reload_plugin()`'s own fallback navigation — setting this flag anywhere later (`load()`, `reload()`) is too late, since the fallback call will already have happened.

While your plugin is mid-reload (between `unload()` finishing and `load()` running), `#root` is shown automatically with a contextual "Reloading '\<plugin name>'…" message — distinct from the generic "no home page installed" message `#root` shows when nothing is registered at all. You can show your own custom `#root` message anywhere by passing a `data` dict:

```python
self.client.goto("#root", data={
    "title": "Custom title",
    "body": "Custom body text.",
    "hint": "Optional monospace hint line",
    "show_hint": False,   # hide the hint line entirely
}, override=True)
```

`carryover` is only ever non-`None` during a hot reload triggered through `PluginManager.reload_plugin()`. It is `None` when the whole application is shutting down, since there is no future `load()` to hand anything to in that case.

---

# Pages

Pages own UI systems and features to interact with them.

Pages should be responsible for organizing and displaying content.

Pages often expose Features that plugins can interact with.

Examples from `CoreWidgetsBundle`:

```text
HomePage

SubHomePage

SubTilesPage
```

Pages may own systems such as:

* WidgetFramework
* TileGrid
* TilePanel
* Drawer controls
* Sub page navigation

Pages own UI.

Plugins extend Pages.

---

# Features

Features are one of the primary extension systems of the application.

Pages expose functionality through Features rather than allowing direct access to their internals. A plugin should never need to reach into `sub_home.widget_manager` directly — it should call whatever Feature that page chose to expose for that purpose.

Think of Features as an API that a Page exposes. The Page decides what's exposed and under what name; the plugin only ever sees the names the Page chose to give it.

## How a Page exposes Features

Every page gets `add_features(dict)`, `has_feature(key)`, and `features(key=None, *args, **kwargs)` for free from `PageFramework` / `SubPageFramework`. A page calls `add_features` once, near the end of its own `__init__`, after everything it wants to expose already exists:

```python
self.add_features({
    "add_widgets":   self.widget_manager.add,
    "remove_widget": self.widget_manager.remove,
})
```

The dict values can be **bound methods** (the common case — calling the feature calls straight through to the real method) or a **raw object reference**, exposing an entire sub-system directly rather than one method at a time:

```python
self.add_features({
    "tile_grid": self.tile_grid,   # the whole TileGrid instance, not a method
})
```

## How a plugin calls a Feature

```python
page.features().add_widgets([MyWidget(client)])
```

`page.features()` with no arguments returns the whole feature container; calling `.add_widgets(...)` on it resolves to whatever was registered under that name and calls it normally. You can also call `page.features("add_widgets", [MyWidget(client)])` — passing the key and args directly — though the attribute-style call above is more common and more readable.

Always check `has_feature` first if a Feature might not exist (e.g. a page from a plugin that may not be loaded):

```python
if page.has_feature("add_widgets"):
    page.features().add_widgets([...])
```

## Example: `WidgetFramework`

Widgets are reusable UI components — not intended to be directly inserted into layouts, but managed by a Page system like `WidgetFramework`. `WidgetFramework` is the system behind anchored widgets like `DateTimeWidget` or `WeatherWidget`. A page that wants widgets constructs one, parents it, and exposes a couple of its methods as Features — see `SubHomePage`:

```python
self.widget_manager = WidgetFramework(
    client   = client,
    page_key = "sub.home",
    padding  = client.SETTINGS.home.widget_margin.value,
)
self.widget_manager.setParent(self)
self.widget_manager.setGeometry(0, 0, w, h)
self.widget_manager.show()

# ... later, once everything else on the page exists ...

self.add_features({
    "add_widgets":   self.widget_manager.add,
    "remove_widget": self.widget_manager.remove,
})
```

A plugin then adds widgets to that page without ever touching `WidgetFramework` itself:

```python
@mixin("sub.home.__init__", "myplugin", "after")
def _inject_widgets(self, sub_home, *args):
    sub_home.features().add_widgets([
        DateTimeWidget(self.client, show_date=True, show_time=True),
    ])
```

Flow, end to end:

```text
Plugin → Page Feature → WidgetFramework → Widget
```

Examples from `CoreWidgetsBundle`: `WeatherWidget`, `DateTimeWidget`, `NotificationCenterWidget`, `CyclingBackground`.

## Example: `TileGrid`

Tiles are lightweight interactive UI components, managed the same way — a Page system (`TileGrid`) owns them, plugins never manipulate layouts directly. `SubTilesPage` constructs `TileGrid` the same way `SubHomePage` constructs `WidgetFramework`, but exposes a richer set of Features — several individual methods, **and** the raw `TileGrid` instance itself:

```python
self.tile_grid = TileGrid(client, cols=16, rows=10)
self.tile_grid.setParent(self)
self.tile_grid.setGeometry(0, 0, w, h)
self.tile_grid.show()

# ... later ...

self.add_features({
    "register_tile": self.register_tile,     # SubTilesPage's own method
    "add_tile":       self.tile_grid.add_tile,
    "remove_tile":    self.tile_grid.remove_tile,
    "get_tile":       self.tile_grid.get_tile,
    "tile_grid":      self.tile_grid,          # raw instance, for anything not covered above
})
```

A plugin registers a tile **class** (not an instance — the page constructs it):

```python
@mixin("sub.tiles.__init__", "myplugin", "after")
def _inject_tile(self, sub_tiles, *args):
    sub_tiles.features().register_tile(MyTile, in_grid=False)
```

Notice `register_tile` here is `SubTilesPage`'s **own** method, not `TileGrid`'s — the page wraps `TileGrid.add_tile` with extra logic (checking for a saved position, deciding panel vs. grid) before deciding what to call. This is the pattern to follow when a Feature needs to do more than just forward straight through to the underlying system: write the logic as a method on the Page itself, and expose *that* instead of the raw sub-system method.

Flow, end to end:

```text
Plugin → Page Feature → TileGrid → Tile
```

## General guidance

* Expose the smallest, most specific set of methods a typical plugin actually needs.
* Only expose a raw object reference (like `"tile_grid": self.tile_grid`) when plugins genuinely need capabilities you haven't wrapped yet — prefer specific named methods otherwise, since they're easier to keep stable across refactors.
* Call `add_features` once your page's sub-systems already exist — Features exposing something that doesn't exist yet will simply error when called.
* Always prefer using Features when extending existing pages, rather than reaching into a page's internals directly.

---

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

# PageRegistry — self.client.PAGES (wrapped by add_page, see Pages below)
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

## Custom events

Plugins can also define and fire their own event names — anything not in the built-in list above.

```python
self.client.create_on_call_event("my_custom_event")

self.client.trigger_on_call_event_iteration("my_custom_event", some_data)
```

Other plugins subscribe to a custom event exactly the same way as a built-in one, via `subscribe_to_event`.

`create_on_call_event` and `trigger_on_call_event_iteration` will raise if you pass one of the built-in event names — those are reserved for the Client and must be triggered through its own internal calls, not from plugin code.

---

# Mixins

Mixins are one of the core extension systems of the application.

They allow plugins to inject functionality into existing systems without modifying the original source code.

Mixins work by wrapping functions before or after they execute.

---

## `@mixin_target()`

`mixin_target()` marks a function as available for plugins to hook into.

Example:

```python
@mixin_target("refresh_weather")
def refresh_weather(self):

    ...
```

---

## `mixin()`

`mixin()` attaches functionality to an existing mixin target.

```python
mixin(
    key="refresh_weather",
    plugin="mypluginkey,
    when="before"
)

mixin(
    key="refresh_weather",
    plugin="mypluginkey,
    when="after"
)
```

* `before` runs before the original function
* `after` runs after the original function

Use Mixins whenever you need to extend existing behavior.

Avoid directly modifying another system whenever possible.

Or feel free to directly add mixin_targets to functions you feel do not need new source code, but you want to extend.

Mixins have a args layout that needs to be followed.
```python
@mixin("refresh_weather", "mypluginkey", "before")
def function_thats_mixing(self, self_obj_from_class, *args_from_mixed_func):
    pass
```

you get 3+ args from the mixin wrapper.
* `self`: this is your plugin instance
* `self_obj_from_class`: this is the Class Instance from the function that mixin refers too
```python
class DummyClass:
    @mixin_target("mixin_key")
    def targeted_func(dummy_class_self, arg1, arg2):
        pass

... inside your plugin

class Plugin:
    @mixin("mixin_key", "mypluginkey", "before")
    def new_mixin(self, dummy_class_self, (arg1, arg2)):
        pass
```
* `*args`: the given args to that targeted mixin function

---

# Development Philosophy

This project intentionally favors modularity over simplicity.

The Client should remain relatively small while plugins provide most functionality.

Pages own UI.

Features expose extensibility.

Mixins extend behavior.

Plugins build functionality.

Everything should be as modular as possible.