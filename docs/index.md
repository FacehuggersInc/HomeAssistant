# Home Assistant

A cross-platform, plugin-driven home assistant panel built with PyQt6, meant to
run fullscreen on a wall-mounted touchscreen.

Almost nothing here is hardcoded. Pages, widgets, tiles, skills, settings and
API endpoints all arrive from plugins, and the client is a coordinator rather
than an owner.

---

## Start here

If you are setting the panel up for the first time, read
[Installation](installation.md), then [Updating](updating.md).

If you are writing a plugin, read [Architecture](architecture.md) for the shape
of the thing, then [Plugins](plugins.md) for the lifecycle, then whichever of
[Widgets](widgets.md), [Tiles](tiles.md), [Pages](pages.md) or
[Features](features.md) matches
what you are building. [Application lifecycle](lifecycle.md) explains when
each of your hooks runs, and [Threading](threading.md) is worth reading before
you write any background work - it is the rule most easily broken by accident.

[Bundled plugins](bundled-plugins.md) describes the six that ship. They are
ordinary plugins with no special privileges, so they are the worked examples
for everything above.

If you want to drive the panel from another machine, everything is in
[Backend API](api.md).

---

## The six ideas

Understand these and the rest follows.

**Plugins** provide functionality. They are directories with a `plugin.toml`
and a `main.py`, loaded in dependency order at startup, and can be reloaded
without restarting the app.

**Pages** own UI systems. A page builds its own interface and exposes the parts
plugins are allowed to touch.

**Features** are how a page exposes those parts - a string-keyed map of
callables, so a plugin can ask a page to do something without importing it.

```python
self.client.action("sub.home.register_widget", MyWidget)
```

**Widgets and tiles** are the reusable components that live on pages. Widgets
are registered rather than constructed, and the saved layout decides what is on
screen and what waits in the panel.

**Registries** hold what plugins have declared: pages, API endpoints, secrets,
quick access buttons, and a free-form public registry for anything else.

**Events** are the client's own lifecycle, published on a bus that any plugin
can subscribe to.

---

## Contents

| Page | What is in it |
|---|---|
| [Installation](installation.md) | Getting it running. |
| [Application lifecycle](lifecycle.md) | Startup, page switching, default page, shutdown. |
| [Updating](updating.md) | Update checks, staging, rollback, exit codes. |
| [Architecture](architecture.md) | How the client, backend and plugins fit together. |
| [Plugins](plugins.md) | `plugin.toml`, `main.py`, and the full lifecycle. |
| [Bundled plugins](bundled-plugins.md) | The seven that ship, and what each provides. |
| [Pages](pages.md) | Registering a page, a full example, sub-pages. |
| [Widgets](widgets.md) | Writing and registering widgets, layout, persistence. |
| [Tiles](tiles.md) | Writing and registering tiles, the grid and panel. |
| [Features](features.md) | Exposing and calling page features. |
| [Registries](registries.md) | API, page, public, secret and quick access registries. |
| [Users](users.md) | Device approval, tokens, and identifying a caller. |
| [The web page](webpage.md) | The built-in browser page and its locks. |
| [Quick settings](quick-settings.md) | The global controls panel and its registry. |
| [Screen brightness](backlight.md) | Real backlight control, and the overlay fallback. |
| [Events](events.md) | Every client event, with examples. |
| [Settings](settings.md) | Declaring settings, types, migration. |
| [Threading](threading.md) | `call_on_ui`, background threads, timeouts. |
| [Logging](logging.md) | Levels, log files, what is worth logging. |
| [Styling](styling.md) | `set_style`, fonts, colours, stylesheet conventions. |
| [Notifications, state, assets](notifications.md) | Toasts, shared state, registered files. |
| [Dialogs and overlays](dialogs.md) | Overlay layers, masks, dialogs, panels. |
| [On-screen keyboard](keyboard.md) | The touch keyboard. |
| [Voice assistant](assistant.md) | Intent matching, STT, TTS. |
| [Writing skills](skills.md) | Skills, Matcher patterns, follow-up questions. |
| [Mixins](mixins.md) | Extending existing methods from a plugin. |
| [Backend API](api.md) | Every endpoint, and the `hactl.py` CLI. |
| [Development philosophy](philosophy.md) | Why it is built the way it is. |

Pages a bundled plugin ships with itself, listed under **Plugins** in the
sidebar rather than here:

| Page | From |
|---|---|
| [Transient widgets and timers](/docs/plugin/corewidgetsbundle/transient-widgets) | Core Widgets |
| [Stickers](/docs/plugin/corewidgetsbundle/stickers) | Core Widgets |
| [Nighttime Clock](/docs/plugin/nighttimeclock/nighttime) | Nighttime Clock |
| [The calendar registry](/docs/plugin/calendarplugin/registry) | Calendar |

---

## Reading these

These files are plain markdown and read fine in any editor. The panel also
serves them rendered, with a sidebar and navigation, at:

```
http://<panel-ip>:5000/docs
```

That address is in **Settings → Info**, along with a button to copy it.
