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
* Plugin hot reloading
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

[settings]
path = "/path/to/.json
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

    def load(self):
        pass

    def built(self):
        pass

    def reload(self):
        pass

    def unload(self):
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
```

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

---

# Development Philosophy

This project intentionally favors modularity over simplicity.

The Client should remain relatively small while plugins provide most functionality.

Pages own UI.

Features expose extensibility.

Mixins extend behavior.

Plugins build functionality.

Everything should be as modular as possible.
