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

---

# Core Concepts

The entire application is built around a few core concepts.

## Plugins

Plugins provide functionality.

## Pages

Own UI systems.

## Features

Features expose extensibility for Pages and Sub-Systems.

## Mixins

Mixins rigidly extend existing behavior.

## Widgets & Tiles

Widgets and Tiles are reusable UI components. Usually added via Pages and their Features

## Registries

Registries manage and store extendable objects. Like API Endpoints, Pages, Etc. These are meant to be easily registered and unloaded for plugin use.

Three concrete registries currently exist:

* `PublicRegistry` — plugin-exposed variables and objects (`self.client.public`)
* `APIRegistry` — backend API endpoints owned by a plugin (`self.client.API_REGISTRY`)
* `PageRegistry` — pages owned by a plugin or the Client itself (`self.client.PAGES`)

All three follow the same shape: `register(owner, key, ...)` and `unregister(owner, key="")`. Registering something under your plugin's key means `PluginManager.unload_plugin()` cleans it up automatically when your plugin is unloaded or reloaded — you should not need to manually remove anything you registered this way (see Plugin Lifecycle → `unload()`).

Understanding these concepts will make understanding the rest of the application much easier.

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

Pages expose functionality through Features rather than allowing direct access to their internals.

Plugins should prefer interacting with Features whenever possible.

Think of Features as an API that Pages expose.

Examples:

```python
page.features()
```

Features may expose functionality such as:

```text
add_widgets

remove_widget

add_drawer_controls

remove_drawer_controls

add_sub_page

remove_sub_page
```

Always prefer using Features when extending existing pages.

---

# Widgets

Widgets are reusable UI components.

Widgets are not intended to be directly inserted into layouts.

Instead, they are managed by Page systems such as `WidgetFramework`.

Examples from `CoreWidgetsBundle`:

* WeatherWidget
* DateTimeWidget
* NotificationCenterWidget
* CyclingBackground

Flow:

```text
Plugin

↓

Page Feature

↓

WidgetFramework

↓

Widget
```

---

# Tiles

Tiles are lightweight interactive UI components.

Tiles are managed by Page systems such as `TileGrid`.

Like Widgets, plugins should not directly manipulate layouts.

Flow:

```text
Plugin

↓

Page Feature

↓

TileGrid

↓

Tile
```

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