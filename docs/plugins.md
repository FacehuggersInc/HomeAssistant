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

**A plugin with a `settings.json` needs a `[settings]` block.** Without it the
file beside it is never read, the plugin has no settings at all, and every
option silently falls back to its default - which looks exactly like the
settings page having lost them.

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
        # Three things arrive from the loader before load() runs. They are
        # declared here rather than assigned - the loader sets them, and
        # writing over one is how a plugin loses its own settings.
        #
        #   self.client    the Client. Registries, pages, events, dialogs,
        #                  logging - everything the plugin reaches out to.
        #
        #   self.settings  this plugin's settings.json, editable by anyone
        #                  from the Settings page. Public.
        #
        #   self.config    this plugin's plugin.toml. Its key, name, version,
        #                  dependencies, declared secrets. Private and not
        #                  edited from the panel.
        #
        # Nothing on the client is ready yet - no pages, no widgets, no other
        # plugins. Set up your own state and stop there.
        self.entries = []

    def load(self, carryover=None):
        # Registration goes here: pages, API endpoints, skills, quick access,
        # events. `carryover` is whatever a previous instance handed over on
        # reload, and None on a first load.
        pass

    def built(self):
        # The application exists now. Anything that needs a page, a widget or
        # another plugin belongs here rather than in load().
        pass

    def reload(self, carryover=None):
        pass

    def unload(self, carryover=None):
        # Give back anything that should survive, and unsubscribe from
        # anything still holding a reference to this instance.
        pass
```

Three helpers come from the base class, and are the reason to inherit it rather
than duplicate them:

| | |
|---|---|
| `self.plugin_key()` | This plugin's key from `plugin.toml`. |
| `self.secret(name)` | A secret **this** plugin declared. Another plugin's returns the default. |
| `self.set_secret(name, value)` | Writes one. Refused for anything not declared here. |

Use `self.secret()` rather than `client.SECRETS` or `os.getenv`, so a key edited
in Settings takes effect without a restart.

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


## `built()`

`built()` runs once the entire application has been built.

This is where plugins should interact with live systems and built UI, though, due note you can still do some of this via page features in the load function, especially if you are just adding UI.

Examples:

* Accessing pages
* Using page features
* Adding widgets
* Adding tiles
* Registering quick access buttons
* Interacting with active interfaces

Anything that depends on UI already existing should happen here.


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


## Shipping documentation with a plugin

A plugin's `readme.md` appears in the docs viewer, in the **Plugins** section
at the bottom of the sidebar. A plugin that adds a subsystem can go further and
ship a `docs/` folder:

```
MyPlugin/
    plugin.toml
    main.py
    readme.md            shown under Bundled plugins
    docs/
        registry.md      its own page in the sidebar
        skills.md
```

Every `.md` in that folder becomes a page, titled from its first heading.

Plugins appear in their own **Plugins** section at the bottom of the sidebar.
The plugin's name is the link to its readme, with each `docs/` page listed
beneath it.
Not nested under Bundled plugins — a plugin installed into `plugins/` is not
bundled and would have nowhere to go. It also keeps the core pages above a
fixed list that does not change shape with whatever happens to be installed.

The contents are indexed by the docs search alongside everything else, so a
heading in a plugin's page is findable the same way a core one is.

**Why not add pages to the main `docs/` tree.** They would be overwritten or
orphaned by the next update, and a plugin that is removed would leave its
documentation behind describing something that is no longer there. Shipping
them with the plugin means they arrive and leave with it.

Write them the same way as the core pages: present tense, and say plainly that
the feature comes from a plugin. Someone reading about your registry needs to
know it disappears when the plugin does.

User plugins in `plugins/` are scanned exactly the same way as bundled ones.
