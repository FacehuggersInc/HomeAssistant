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

That runs the client directly, which is fine for development.

For anything long-running -- a wall mounted display, a kiosk, an autostart
entry -- start it through the launcher instead, so crashes and updates are
handled for you:

```bash
./startup.sh          # Linux / macOS
startup.bat           # Windows
```

Both scripts do the same two things: activate the virtualenv and hand off to
`launcher.py`. All the actual supervision logic lives in `launcher.py`, so it
behaves identically on both platforms.

---

# Updating

Updates are staged while the app runs and applied while it is stopped. The
app never overwrites its own files mid-session.

```text
app running  ->  download + extract to .update-staging/   (nothing installed is touched)
             ->  exit 42
launcher     ->  back up every file it is about to replace
             ->  apply .update-staging/ over the install
             ->  relaunch
             ->  new version starts OK  ->  discard backup
             ->  new version crashes    ->  roll back, relaunch old version
```

Trigger an update either from the API (`GET /update?id=<client id>`) or from
the command line:

```bash
python app.py update          # stage an update, then exit
python app.py apply-update    # apply a staged update in place (no launcher)
```

## What an update will not touch

`.env`, `.venv/`, `plugins/`, `logs/` and `startup.log` are never overwritten.
Your settings live outside the install directory entirely (see
`get_data_dir()` in `src/constants.py`), so they are never at risk.

Bundled plugin `settings.json` files are **merged** rather than replaced: your
existing `value` entries are kept, and any new settings the update introduces
arrive at their defaults. A setting removed by the update goes away.

Note that `startup.sh`, `startup.bat` and `launcher.py` *are* updated. If an
update replaces `launcher.py`, the launcher exits 44 and the shell wrapper
re-runs it so the new code takes effect immediately.

## Exit codes

`app.py` communicates with the launcher through its exit code:

| Code | Meaning |
|------|---------|
| 0    | Clean shutdown. Do not relaunch. |
| 42   | An update is staged. Apply it, then relaunch. |
| 43   | Relaunch as-is (`client.restart()`). |
| 44   | *(launcher -> wrapper)* `launcher.py` updated itself; re-run it. |
| any other | Crash. Handled by the crash policy below. |

Running `app.py` without the launcher still works -- it detects that nothing
is supervising it and relaunches itself instead of exiting with a code
nothing would act on.

## Crash behaviour

Under **Application -> Updates** in Settings:

* `restart_on_crash` -- whether the launcher restarts the app after a crash.
  Turn it off and a crash simply stops the app.
* `max_restart_attempts` -- consecutive restarts before giving up, so a
  genuinely broken build cannot boot loop. Backoff doubles each attempt, to
  a maximum of 30 seconds.
* `crash_window` -- if the app ran longer than this before dying, the attempt
  counter resets. A session that ran for hours and then crashed is not a boot
  loop.
* `update_grace_period` -- a freshly applied update that crashes inside this
  window is rolled back automatically.

The launcher reads these straight out of the settings JSON with the `json`
module rather than through Dynaconf, since it runs before the virtualenv is
known to be usable. A missing or malformed settings file falls back to
defaults rather than refusing to start.

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

# Where plugins live

| Location | Ships with the app | Survives an update |
|----------|--------------------|--------------------|
| `src/assets/bundled/<Name>/` | yes | replaced, but its `settings.json` is merged so your values are kept |
| `plugins/<Name>/` | no | preserved untouched |

Bundled plugins are part of the app and update with it. `plugins/` is for
your own work and is never overwritten - put anything you do not want an
update to replace there.

A bundled plugin points at its settings with a path from the install root:

```toml
[settings]
path = "src/assets/bundled/MyPlugin/settings.json"
```

Append `.DISABLED` to a folder name to stop it loading without deleting it.

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

### Setting types

`bool`, `string`, `body`, `path`, `int`, `float`, `enum`, `list`, `secret`.

`body` is multi-line — use it for anything holding a paragraph rather than a
line, such as a prompt or a template. It grows with its content between 132
and 320px, so a short value does not leave a large empty box and a long one
does not push the rest of the page away.

```json
"system_prompt": {
    "type": "body",
    "value": "You are the voice assistant for a wall-mounted display...",
    "description": "Sent to the AI before every conversation."
}
```

### Secrets

API keys and other credentials go in `.env`, never in a settings file. A
plugin declares the key **names** it needs; values are never in a toml.

```toml
[secrets]
keys = ["WORDNIK_KEY", "SPOTIFY_CLIENT_ID"]
```

Then add a `secret` setting for each, naming the env key it maps to:

```json
"credentials": {
    "wordnik_key": {
        "type": "secret",
        "env": "WORDNIK_KEY",
        "value": "",
        "description": "Wordnik API key. Stored in .env, not here."
    }
}
```

The field renders masked, shows **Set** or **Not set** rather than the value,
and has its own Save and Clear buttons - it writes straight through to `.env`
on save rather than waiting for the page save, because the value never enters
the settings object at all.

Read it back from inside the plugin:

```python
key = self.secret("WORDNIK_KEY")
```

Use that rather than `os.getenv`, so a key edited in Settings takes effect
without a restart.

### Ownership

`self.secret()` only returns keys **your** plugin declared. Asking for
another plugin's key returns the default and logs a refusal, and the same
applies to `self.set_secret()`. `client.secret()` is scoped the same way to
keys the Client itself owns, so reaching through the Client is not a way
around it.

This is not a security boundary and is not meant to be - anything running in
this process can read `os.environ` directly. It is there so one plugin cannot
pick up another's credential through the Client by accident or by casual
intent. A plugin that genuinely needs a shared key should declare the same
name; the registry logs when two plugins declare one, and both then read the
same value.

| Call | Returns |
|------|---------|
| `self.secret(key)` | keys this plugin declared |
| `self.set_secret(key, value)` | writes keys this plugin declared |
| `client.secret(key)` | keys the Client declared (`CORE_SECRETS`) |
| `SECRETS.is_set(key)` / `status(key)` | unrestricted - metadata, never the value |

**Why not just store it in settings.json.** That file is written to disk on
every save, rendered wholesale in the Settings UI, and carried across updates
by the preserve list. A credential in there leaks by default. `.env` is
already excluded from updates and from version control, and the registry
chmods it to owner-only on POSIX.

Three separate paths could otherwise write a value into a settings file - the
settings page save, the plugin settings save on unload, and the template
migration - so all three run `scrub_secrets()`, which empties the `value` of
anything typed `secret`. A plugin author who ships a key in their
settings.json gets it stripped rather than persisted.

Unloading a plugin forgets the *declaration* but keeps the stored value, so
reloading does not silently require typing the key in again.

### Python package requirements

If your plugin needs pip packages the app does not already ship, declare them:

```toml
[plugin]
name = "Weather Radar"
key  = "weatherradar"

[requirements]
pip = ["Pillow", "requests>=2.28"]
```

`[plugin] requirements = [...]` works identically if you prefer it in one
section.

Nothing is ever installed silently. At startup the requirements are checked
during the `plugin.toml` pre-scan -- before any plugin code is imported --
and a plugin with unmet requirements is **held back**: its `main.py` is never
imported, since a module doing `import PIL` at the top cannot be imported at
all when PIL is absent.

Once the UI is up, a dialog lists every held-back plugin and exactly which
packages would be installed, and where. Approving it runs pip, then loads the
plugin normally (`load()` -> mixins -> `built()`) without a restart.

Declining leaves the plugin listed in Settings under Plugins, greyed out and
badged `NOT INSTALLED`, with its own **Install** button. Nothing is lost and
nothing is retried behind your back.

Requirement checks use `importlib.metadata` against the *distribution* name,
so `Pillow` and `PyYAML` resolve correctly even though they import as `PIL`
and `yaml`. Version specifiers are honoured when `packaging` is importable
and treated as satisfied when it is not, so an unverifiable specifier never
causes a repeat prompt for something already installed.

### Uninstalling packages

Every plugin that declares requirements gets an **Uninstall** button in
Settings, next to Reload and Unload. It removes the plugin's pip packages and
then unloads the plugin -- leaving it running against packages that no longer
exist would only defer the crash.

The plugin's own files are untouched. It reappears in the list, greyed out,
with an Install button, exactly as if its packages had never been installed.

A package is only removed when nothing else needs it. The confirm dialog
lists what will be removed and what will be kept, with a reason for each:

```text
Will be removed:
  some-niche-lib

Will be kept:
  requests   — required by the app itself
  Pillow     — required by another installed plugin
```

Anything in the app's own `requirements.txt` is permanently protected. A
plugin declaring `requests` cannot take the Flask backend down with it.

### A note on trust

`plugin.toml` is arbitrary text from wherever you got the plugin, so
installing from it is equivalent to running an installer. That is why it is
always an explicit, itemised prompt rather than something that happens during
load, and why package names are shown verbatim before anything runs.

Everything targets `sys.executable -m pip`, which inside a virtualenv is that
virtualenv's pip. The app refuses to install or uninstall at all when
`sys.prefix == sys.base_prefix`, so a launch outside the venv cannot quietly
modify system Python.

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

### Settings page presence: readme and icon

Two more optional fields under `[plugin]` control how your plugin shows up on its own page in Settings (every plugin gets one, nested under "Plugins" in the sidebar):

```toml
[plugin]
name = "My Addon"
key  = "myaddon"
icon = "extension"
readme = "README.md"
```

* `icon` — either a name from the icon system (`src/ui/icons.py` — a registered name like `"extension"`, or a raw `mdi.*` name) **or** a path to your own image file. Shown next to your plugin's nav button in the Settings sidebar, and next to its title in its own page's header. A path is resolved relative to your plugin's own directory, same as `settings.path` above — `"assets/icon.png"` means `assets/icon.png` inside your plugin's folder, not the app's root. If you give a path and the file doesn't exist, the icon just doesn't render rather than showing something broken. Omit this entirely and it defaults to `"extension"` (a generic puzzle-piece icon) — every plugin gets *some* icon either way.
* `readme` — a path to a markdown file, resolved the same way. Rendered as actual markdown (headers, bold/italic, lists, links) at the very bottom of your plugin's settings header, underneath everything else there (title, key, dependency info). Omit this and a `README.md` (or `readme.md`/`Readme.md`/`README.MD`) sitting in your plugin's own folder gets picked up automatically if one exists. Missing/empty file either way → nothing renders, no error.

Neither field is required — a plugin with neither still gets its own settings page, just without an icon or the extra markdown section.

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

# Adding your own cards to a settings page

A plugin can contribute widgets to its own page in Settings, between the
registry summary and its settings, by defining `settings_blocks()`:

```python
def settings_blocks(self) -> list[QWidget]:
    return [my_card]
```

Return any widgets you like; they render in order and are not affected by the
sort toolbar, since they are static content rather than sortable settings. A
plugin raising here is logged and skipped rather than blanking its own page.

`CoreSkillsBundle` uses this to list its voice skills - each with its trigger
phrases, argument names and word-count range - which is a better fit there
than in a generic registry card.

# What a plugin has registered

Every plugin's own page in Settings shows what it currently owns, between its
description and its settings. One card per registry, each with a count and
the entries themselves:

```text
Registered  ·  9 items across 6 registries

  Pages            2      API Endpoints    2
    #weather  Weather Page   /public/forecast
    #radar                   /public/alerts

  Public Registry  1      Skills           2
    weather_state           weather-forecast
                            weather-update

  Mixins           1      Pip Packages     1
    sub.home.__init__ (after)   requests>=2.28
```

Empty registries are omitted rather than shown as zero. A plugin that has
registered nothing says so.

The data comes from `PluginManager.registrations(plugin_key)`, which returns
`[(registry_name, [entries])]` already formatted for display. Each registry is
read independently and a failing one is skipped, so a registry raising cannot
take the whole page down.

Adding a new registry to this view means adding one `add(...)` call there. If
your registry does not already expose a per-owner listing, give it one -
`APIRegistry.endpoints_for()`, `PublicRegistry.names_for()` and
`MixinManager.mixins_for()` were added for exactly this.


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

This replaces installing your own `QObject` event filter on `client.app` just to watch for activity — that used to be something each plugin had to set up for itself (e.g. the Carousel plugin's old `InteractionEventWatcher`); now it's a Client-level concern any plugin can subscribe to directly.

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

# Voice assistant

Speech-to-text runs in a separate process (`src/assistant/whisper-process.py`)
talking to the client over a local socket. The client half is
`STTProcessing`; device handling is `src/assistant/audio.py`.

Settings live under **Assistant**:

| Setting | Meaning |
|---------|---------|
| `enabled` | Whether the assistant runs at all |
| `wake_word` | App-wide wake word; plugins read `client.wake_word` |
| `input_device` | Microphone name, or empty for the system default |
| `model` | Whisper model, default `tiny.en`. Downloaded on first use |
| `voice_bar` | The activity bar along the bottom of the screen |
| `tts_enabled` | Whether replies are spoken |
| `elevenlabs_key` | ElevenLabs API key, stored in `.env` (see Secrets) |

## The activity bar

A floating pill above the bottom edge, centred, sized to its content between
240 and 560px. It rises into place and fades out rather than blinking, and
carries a live level meter plus a line of text.

| State | Accent | Shows |
|-------|--------|-------|
| Listening | red | meter tracking voice level, "Listening…" or the wake word |
| Thinking | blue | meter sweeping on its own, "Thinking…" |
| Acting | green | whatever the skill is doing |
| Heard | grey | the transcript, quoted, held long enough to read |

Whisper only emits **finished** transcripts - it transcribes a completed
speech window, so there is no partial stream to show mid-sentence. The meter
covers "hearing something right now"; the text covers "heard this". If live
partial text is ever wanted, it needs a streaming model in
`whisper-process.py`, not a change here.

The bar is `WA_TransparentForMouseEvents`, so it never eats a tap, and its
shadow is painted by hand - a `QGraphicsEffect` cannot coexist with painting
custom alpha, and only one effect can be set on a widget at a time.

How long a transcript stays up scales with its length rather than being
fixed: `assistant.voice_bar_hold` (default 6s) is a floor, and anything
longer than that reads-in-six-seconds is held proportionally, capped at 20s.
There is also a minimum visible time, so a wake word that gets rejected a few
hundred ms later cannot flash a pill for one frame.

Turn it off with `assistant.voice_bar`.

## Settings migration

Settings added by an update are folded into your existing data file at
startup. `create_user_data_files()` used to copy the template only when the
file did not exist, so a new setting never reached an existing install: not
in the file, not in Settings (the page builds its categories from that data),
and not readable by the code that added it. Because every read is guarded
with a default, the symptom was a feature that silently did nothing.

On startup the template and the data file are compared. New keys arrive at
their defaults, values you have changed are kept, and keys the template no
longer has are dropped. The old file is copied to `<name>.json.bak` first,
and every added or removed path is logged.

## Startup

`Client.start_assistant()` runs shortly after `build()` -- not during plugin
load, since it needs the UI to be able to ask anything. It:

1. checks the audio stack is usable at all
2. logs every input device it can see
3. resolves the configured device name to an index, falling back to the
   default if it has gone away
4. opens the stream briefly to confirm it actually works
5. asks before downloading a Whisper model that is not already cached
6. starts the STT process

Any failure surfaces as a notification plus a dialog carrying the real
reason, and the rest of the app carries on. The assistant never takes the
app down with it.

## How skills match

A skill's `examples` are compiled into spaCy Matcher patterns. Those patterns
are **generalised**, not literal:

| In the example | In the pattern |
|----------------|----------------|
| a number (`10`) | any number |
| a determiner (`a`, `the`, `my`) | optional, interchangeable |
| politeness (`please`, `can you`, `just`) | optional |
| a pronoun (`me`, `us`) | optional |
| everything else | its lemma |

This matters more than it sounds. Patterns used to be one literal lemma per
token, so `"set a timer for 10 minutes"` matched only that exact sequence -
`"set the timer for 1 minute"` failed on both the determiner and the number,
and `"set a timer for 1 minute"` failed too, because the `10` was compiled in.
Effectively only the verbatim examples ever matched.

When the Matcher finds nothing, a **rule phase** scores the utterance's
content lemmas against every skill's examples and takes the best, provided it
clears `FALLBACK_DEFAULT_RULE_SCORE`. `phases` had listed `"rule"` since the
engine was written but nothing ever ran it. The threshold matters: scoring
every skill against every phrase will always produce a nearest match, and
"nothing matched" has to stay a possible answer.

You still want several examples per skill - they define the vocabulary the
rule phase scores against - but they no longer need to enumerate every
determiner and number.

### Scoring

The rule phase weights words by how discriminating they are. A lemma used by
one skill counts for more than one every skill shares - without that, "clear
all notifications" scored identically against `notifications-open` and
`notifications-empty`, since both contain "notification" and the word that
actually decides it was worth no more than the noise.

Score is the harmonic mean of two coverages: how much of the example the
utterance covers, and how much of the utterance the example accounts for.
Recall alone let a long rambling phrase match a tiny example on one shared
word.

Token comparison is fuzzy above four characters, which absorbs the
mishearings a better phrase list cannot: "notifcations", "aplication",
"minuets" for "minutes". Short tokens are compared exactly, since at three or
four characters nearly everything is close to everything.

`FALLBACK_DEFAULT_RULE_SCORE` was tuned by sweeping a labelled corpus rather
than picked. Lower thresholds score better overall but start letting
out-of-domain phrases through - at 0.50, "tell me a joke" answers with the
weather. A miss costs the user a repeat; a misfire makes the assistant do
something it was never asked to do, so the highest threshold with **zero**
misfires wins.

### Writing good examples

The engine now handles determiners, numbers, politeness, plurals,
capitalisation and small mishearings. What it cannot invent is vocabulary:
"close the app" will not match a skill whose examples only ever say
"application". Cover the *words* people use, not their grammar - one example
per distinct phrasing, not per determiner.

### Wake words in a transcript

Whisper capitalises the first word of every transcript, so wake matching is
case-insensitive and anchored on word boundaries
(`STTProcessing.find_wake` / `strip_wake`). It used to be a plain
`wake in processed` substring test, which was False for essentially every
real utterance - "alexa" is not in "Alexa, set a timer for 1 minute." - and
the same test split the command off the wake word, so when it failed the wake
word was passed through as part of the command.

Boundaries matter too: a short wake word otherwise fires inside ordinary
words ("Alexander").

### Units

`normalize.expand_units()` turns spoken abbreviations into canonical units,
but only directly after a number, so ordinary speech is untouched:

```text
"3 mins"  -> "3 minutes"      "the min temperature" -> unchanged
"30 secs" -> "30 seconds"     "press s to continue" -> unchanged
"2 hrs"   -> "2 hours"
```

That means argument patterns only ever need to list the canonical form, and
using `LEMMA` rather than `LOWER` covers singular and plural in one entry:

```python
"time": [[{"LIKE_NUM": True},
          {"LEMMA": {"IN": ["second", "minute", "hour", "day"]}}]]
```

### Arguments

`arguments` patterns often need an anchor word to find the value:

```python
"name": [[{"LOWER": {"IN": ["call", "called", "named"]}},
          {"LOWER": "it", "OP": "?"},
          {"IS_ALPHA": True, "IS_STOP": False}]]
```

The anchor is stripped before the value reaches your skill, so
`"call it Eggs"` arrives as `name="Eggs"` rather than `name="call it Eggs"`.

## Transcript normalisation

`src/assistant/normalize.py` cleans a transcript before it reaches the intent
engine. Skill patterns match on tokens, so spoken numbers have to end up as
separate number and unit tokens or the argument never extracts:

```text
"set a timer for one minute"      -> "set a timer for 1 minute"
"set a timer for1minute"          -> "set a timer for 1 minute"
"set a timer for half an hour"    -> "set a timer for 30 minutes"
"set a timer for a couple of mins"-> "set a timer for 2 mins"
```

It handles compound numbers ("twenty-five", "one hundred and twenty"),
articles before a unit ("a minute" -> 1, but "a timer" is left alone),
fractions, filler words, and digits glued to words. Number conversion is
written out rather than delegated to `word2number`, which raised on ordinary
input like "zero" or a trailing "and" - a transcript is untrusted text and an
exception there dropped the whole phrase.

## When nothing matches

If `SkillIntentEngine` finds no skill, the client fires
**`on_assistant_fallback`** with the phrase. Skills always win - a subscriber
only ever sees what nothing else claimed. It fires on the real input path
only, so a `use_skill=False` probe stays side-effect free.

`src/assets/bundled/AIFallback` uses it to answer the question with an AI and show the
reply in a chat panel. Nothing in the client depends on that plugin: remove
it and unmatched phrases go back to being ignored.

```python
def load(self, carryover=None):
    self.client.subscribe_to_event("on_assistant_fallback", self.on_fallback)

def on_fallback(self, event):
    phrase = event          # the phrase nothing understood
```

Handle it **off the event thread**. It fires from inside the intent engine,
so blocking there stalls the whole STT pipeline.

### The AI fallback plugin

Needs an OpenAI key, entered under the plugin's own settings and stored in
`.env` (see Secrets). Without one it stays quiet.

**OpenAI has no free tier** - the account needs credit on it. An
`insufficient_quota` error is a billing limit, not a rate limit, so waiting
will not help.

Defaults to `gpt-5.6-luna`, the cost-sensitive tier, which suits the short
spoken replies this plugin asks for. The model list is fixed at release; if
OpenAI ships new models it needs updating.

Configurable: model, token ceiling, how many previous turns to send, the
system prompt, whether replies are spoken, and the panel timeout.

Two details worth knowing:

**A Session opens before the first API call.** That is what serialises the
conversation. While a request is in flight, anything else the user says lands
in the session queue rather than being treated as a fresh command, and is
only picked up once a reply has come back. Without it a second question fired
mid-request would race the first.

**Replies are markdown**, rendered to the HTML subset Qt actually supports -
headings, emphasis, lists, links, images, blockquotes and fenced code blocks.
Written out in `markdown.py` rather than pulling in a markdown package: every
general-purpose converter emits CSS that Qt ignores, which renders worse than
handling the subset directly. Replies are HTML-escaped before any markup is
added, so a reply containing a `<script>` tag is displayed rather than
interpreted. A separate `to_speech()` strips markup and drops code blocks
before anything is read aloud.

The panel is reused across turns and carries its own long timeout, so a
conversation is not cut off mid-thought by the ordinary interaction timeout.

**Failures never open the panel.** A chat panel containing nothing but an
error implies a conversation started; the error goes to a dialog instead,
with the summary as the body and OpenAI's own message as the detail. If a
panel is already open the note is added there too, so the transcript does not
end on an unanswered question.

Errors are also classed as fatal or not. A rejected key, an account with no
credit, or a model this account cannot use ends the conversation - there is
no point holding a session open that will fail again on the next question.
Rate limits and network errors leave it open so a follow-up can retry.

## Backing out

Saying "nevermind", "cancel", "forget it", "stop" and similar abandons
whatever the assistant is doing and returns it to waiting for the wake word.

This is handled in two places on purpose:

* `STTProcessing.start_skill_parse()` checks for a cancel phrase **before**
  intent matching, so backing out works even with no cancel skill registered.
* `CoreSkillsBundle` also registers a `nevermind` skill, so it appears in the
  skills list in Settings and the activity bar acknowledges it.

`client.cancel_assistant(reason)` does the same thing from code, and fires
`on_assistant_cancelled`.

Cancel phrases are matched against the **whole** utterance, never searched
within it - "never mind the weather" stays a weather query.

### Sessions

A skill holding a conversation (`STT.new_session()`) gets the same escape.
`wait_for_phrase()` returns `None` when the user cancels, the session times
out, or it is closed - so a prompt loop should break on `None` rather than
asking again:

```python
with session:
    while True:
        phrase = session.wait_for_phrase()
        if phrase is None:
            break            # cancelled, timed out, or closed
        ...
```

This previously had no way out. `wait_for_phrase()` was a blocking `get()`
with no timeout and no sentinel, so anything that was not an expected answer
re-prompted forever, and when the five-minute timeout fired it reset the STT
without ever releasing the waiter - leaving that skill thread blocked for the
life of the process.

## The STT process

`whisper-process.py` runs detached and talks over a socket. Points worth
knowing if you change it:

* **VAD gets raw audio.** It used to spectral-gate the whole 420ms context
  buffer on every 30ms frame and keep only the last 30ms - about 76% of a core
  continuously, and 5000x the cost of the VAD call it fed. On slower hardware
  the loop cannot keep up, drops input, and truncates phrases. Noise reduction
  still runs once on the complete utterance before transcription, which is
  where it helps.
* **`beam_size`, not `best_of`.** `best_of` only applies when sampling; at
  `temperature=0` it was inert.
* **`condition_on_previous_text=False`.** Carrying context between windows
  makes short isolated commands loop and hallucinate.
* **Hallucinations are filtered.** Whisper emits "Thank you.", "you",
  "Thanks for watching!" and repeated single words from silence, confidently.
  These are not transcription errors better audio would fix.
* **The model is locked.** The wake-word check transcribes on its own thread
  alongside the processing loop, and `WhisperModel` is not documented as
  thread-safe.
* **Overflows are logged.** `stream.read()` reports dropped input; it used to
  be discarded, which made truncated phrases look like model errors.

## Changing settings while running

Changing the model, microphone, wake word, `enabled` or `tts_enabled` in
Settings restarts the assistant on save -- including the download prompt if
you switch to a model that is not cached yet. Nothing needs a relaunch.

`Client.assistant_config()` is the snapshot that gets compared; add to it if
you add a setting the running assistant depends on.

## Devices

`input_device` is stored as a **name**, not an index, because PortAudio
indices shift whenever devices are added or removed -- a pinned index
silently becomes the wrong microphone. A configured name that is no longer
present falls back to the system default and says so, rather than refusing
to start.

ALSA advertises its rate-conversion and channel-mixing plugins (`lavrate`,
`samplerate`, `speexrate`, `upmix`, `vdownmix`) as capture devices. They are
not microphones, so they are hidden from the listing -- but still resolvable
by name if you deliberately want one. Real backends (`pulse`, `pipewire`,
`default`, `sysdefault`, `hw:*`) are always listed.

Microphone problems detected while running (unplugged mid-session, another
app claiming the device) are reported back over the socket and shown once,
not on every retry.

## Speaking

Skills should call `client.say(text)` rather than `client.TTS.play(...)`:

```python
if not self.client.say("Twenty two degrees."):
    self.client.simple_notify("assistant", "Assistant", "Twenty two degrees.")
```

`say()` returns whether anything was actually said. TTS needs an ElevenLabs
key, set under **Assistant** in Settings (it is stored in `.env`, not in the
settings file - see Secrets). Without one the app still runs and skills still
work, they just do not talk back. Entering a key restarts the assistant, so
speech comes up without relaunching. `TTSProcessing` exposes `.available` and `.error`
for the specific reason.

## Wake words

`client.wake_word` is the app-wide setting. A plugin may override it for its
own skills, but should default to inheriting:

```python
own = str(self.settings.general.wake_word.value).strip()
wake = own.lower() or self.client.wake_word
```

## Cross-platform note

`import sounddevice` raises **OSError**, not ImportError, when PortAudio is
missing -- which is the normal state of a fresh Windows install without audio
drivers, or a minimal Linux container. Anything touching the audio stack must
catch `Exception`, not `ImportError`. `audio.available()` already does, and
returns a reason worth showing a user.


# The on-screen keyboard

Any text field opens a keyboard dialog rather than a strip sliding up from
the bottom. The dialog shows what you are editing, its description, the
current value, and the keys.

The old popup covered the very field it was editing on a short screen, and
had no room to say which setting was in play.

* Rows are **staggered** like a physical keyboard - the home row sits half a
  key right of the number row, and the one below it nine tenths. Aligned
  columns look tidy and defeat muscle memory.
* Keys scale to the panel - roughly 81px wide on a 1024 screen, capped at
  112px, so the dialog uses about 90% of the available width instead of a
  fixed 750px. On a panel shorter than 660px key height shrinks toward a 44px
  floor and the description is dropped first, so it still fits an 800x480
  screen rather than running off the bottom.
* Shift is one-shot, the way a phone behaves - capitalise one letter and drop
  back rather than staying locked.
* `?123` switches to a symbols layer. Numeric settings get a numpad with a
  sign toggle instead.
* Nothing is written to the field until **Done**. Cancel leaves it untouched.
* A `body` setting gets a **multi-line preview** rather than a single line,
  and the keys drop to their touch floor so the text gets the room instead.
  The dialog then measures itself and trims the preview until it fits the
  panel, so it never runs off the bottom.
* Only **one** keyboard can be open at a time. A tap fires `mousePressEvent`
  on the field and, unaccepted, on its parent, plus `focusInEvent` besides -
  which used to stack two or three identical dialogs. Closing the top one
  revealed the next, so buttons appeared to need clicking twice.

Numeric settings (`int`, `float`, `numeric`, `list[int]`, `list[float]`) get a
numpad with a sign toggle and no letters. Everything else gets the full
QWERTY board.

`make_keyboard(client, target, setting_type, label=..., description=...)`
builds it. The target may be a `QLineEdit`, `QTextEdit` or `QPlainTextEdit`.

## Fields are displays

Every editable field is **read-only** and opens the keyboard dialog on tap.
There is no physical keyboard on the target hardware, so a field that accepts
direct input only ever shows a caret nothing can type into. All editing goes
through the dialog, and the value is written back on Done.

If you add a field type, bind `mousePressEvent` as well as `focusInEvent` -
focus alone means a second tap on an already-focused field does nothing.

Pass `client` and `setting_type` in explicitly. An earlier version read both
off the setting object, which carries neither, so every call raised
`AttributeError` into a bare `except Exception: pass` and the keyboard never
opened at all.

# Dialogs

`Client` exposes modal dialogs directly. All of them are thread-safe -- call
them from a Flask route, a voice handler, an event callback or any background
thread and they marshal onto the Qt thread themselves, the same way
`create_panel()` does.

```python
self.client.alert("Backup complete", "Wrote 412 files.")

self.client.confirm(
    "Delete backup?", "This cannot be undone.",
    on_confirm=wipe, destructive=True,
)

self.client.prompt(
    "Rename", "New name:",
    on_submit=lambda text: rename(text),
    default="untitled",
)

self.client.choose(
    "Theme", "Pick one", ["Dark", "Light"],
    on_choose=lambda value: set_theme(value),
)
```

| Method | Purpose |
|--------|---------|
| `alert(title, body, ...)` | Message with one dismiss button |
| `confirm(title, body, on_confirm=..., destructive=False)` | Yes/no |
| `prompt(title, body, on_submit=..., numeric=False, password=False)` | Text entry |
| `choose(title, body, options, on_choose=...)` | Pick one from a list |
| `progress(title, body)` | Status line, no buttons; returns the dialog |
| `dialog(widget)` | Show any `BaseDialog` you built yourself |
| `close_dialog()` | Close the topmost dialog |

Callbacks fire **after** the dialog closes, so a callback is free to open
another dialog without fighting the one it came from.

`prompt()` raises the on-screen keyboard on focus (numpad when
`numeric=True`), since the target hardware has no physical one. `choose()`
takes plain strings, or `(value, label)` pairs when the value you want back
differs from the text shown.

`progress()` returns the dialog so a worker thread can drive it:

```python
dlg = self.client.progress("Syncing", "Talking to the server...")
dlg.set_status("42 of 300")     # safe from any thread
self.client.close_dialog()
```

## The overlay hit mask

`OverlayManager` sets a mask built from the union of its visible children's
geometry. The mask is what decides where the overlay accepts clicks at all -
outside it, events fall through to the page underneath.

**A child that sets `WA_TransparentForMouseEvents` is excluded from the
mask.** Including it created dead zones: the overlay claimed the area, nothing
inside it was willing to handle the click, and the event never reached the
page. The voice bar sits bottom-centre, which is exactly where page buttons
tend to be - the symptom was buttons that only worked if you moved and tried
again a few pixels away.

If you add an overlay widget that should not receive input, set that
attribute and the mask will leave the area clickable.

## Layering

Dialogs are registered in the `DIALOG` overlay layer, which sits above
`TOPMOST`. This matters: `OverlayManager._enforce_z_order()` raises every
widget registered in a layer, and a widget that is only reparented onto
`OVERLAYS` without being registered is invisible to it. Anything added to a
layer afterwards - a notification toast, the voice bar - would then be raised
over the dialog, hiding it and handing the next tap to the click blocker
underneath, which closed it.

If you build an overlay widget of your own, register it with
`OVERLAYS.add(layer, widget)` rather than calling `setParent()`.

## Building your own

Subclass `BaseDialog` (`src/ui/overlays.py`) and hand it to
`client.dialog()`:

```python
from src.ui.overlays import BaseDialog

class MyDialog(BaseDialog):
    def __init__(self, client):
        super().__init__(client, "Title", "Body text")
        self.content.addWidget(my_widget)
        self.add_button("Cancel", self.close, "secondary")
        self.add_button("Go", self._go, "primary")
```

`add_button` kinds are `primary`, `secondary`, `destructive` and `disabled`.
Helpers: `make_title`, `make_body`, `make_detail` (a muted block for lists and
paths), `add_scroll`, `clear_content`, `clear_buttons`, `center`.

**One thing to know if you build dialog widgets by hand.** `BaseDialog`
derives from `QFrame` *and* sets `WA_StyledBackground`, and both matter. A
plain `QWidget` subclass does not paint a stylesheet `background` without
that attribute -- but only once it is parented into `OVERLAYS`, which is
translucent. Rendered standalone it looks completely fine, which makes this
easy to ship by accident: the dialogs here originally did exactly that and
came out as floating text over the page. Anything you parent into the overlay
layer wants one or the other.


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