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

Everything in the tree above boils down to a handful of ideas, each covered in full on its own page:

* **Plugins** provide functionality.
* **Pages** own UI systems.
* **Features** expose extensibility for Pages and sub-systems.
* **Widgets & Tiles** are reusable UI components, usually added via Pages and their Features.
* **Mixins** rigidly extend existing behavior.
* **Events** let any part of the application react to things happening elsewhere.
* **Registries** manage and store extendable, plugin-ownable objects. There are
  nine, each with its own section under [Registries](registries.md):

| Registry              | Reached by       | Holds                                                                             |
|-----------------------|------------------|-----------------------------------------------------------------------------------|
| `APIRegistry`         | `client.API`     | HTTP endpoints a plugin serves                                                    |
| `PageRegistry`        | `client.PAGES`   | Full-screen pages. A sub-page is reached through its parent's entry, not by name. |
| `PublicRegistry`      | `client.public`  | Data one plugin offers to the others                                              |
| `SecretRegistry`      | `client.SECRETS` | API keys, kept out of the settings file                                           |
| `QuickAccessRegistry` | `client.QUICK`   | Buttons on the quick settings panel                                               |
| `UserRegistry`        | `client.USERS`   | Approved devices and their tokens                                                 |
| `AudioRegistry`       | `client.AUDIO`   | Sounds by name, and where their files live                                        |
| `PlayerRegistry`      | `client.PLAYER`  | Whatever is playing, from any source                                              |
| `StatusRegistry`      | `client.STATUS`  | What the panel is busy with, as icons                                             |
| `CancelRegistry`      | `client.CANCEL`  | What "stop" means right now                                                       |

Keep these in mind as you read on — nearly everything else in these docs is one of these seven ideas in more detail.

---

## Library plugins

A plugin that registers no page, no widget and no skill, and exists so more
than one plugin can share something. `AstronomyLibrary` is the first.

A library declares no dependencies of its own, which is what lets everything
else depend on it. Sun-and-moon arithmetic is wanted by both Core Widgets and
Nighttime Clock; Core Widgets loads first, so neither can own it without the
other depending upwards, and a dependency in that direction is a cycle. A
library sits under both.

Why a plugin rather than `src/`: nothing in the client needs to know where
the moon is. `src/` is the panel's own machinery, and a thing that can be
uninstalled without the panel noticing is not that. The rule is not "shared
code goes to core" - it is **what does the client itself depend on**.

Shape:

- No dependencies, so anything can depend on it.
- `load()` exposes its surface on the public registry and nothing else.
- No state, no timers, no widgets - so `unload()` has nothing to undo beyond
  the automatic `public.clear(key)`.
- Callers declare it in `dependencies` and reach it through
  `client.public.<name>`, never by importing it.
