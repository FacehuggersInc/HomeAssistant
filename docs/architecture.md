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
* **Registries** manage and store extendable, plugin-ownable objects. There are
  eight, each with its own page section under [Registries](registries.md):
* **Events** let any part of the application react to things happening elsewhere.

| Registry | Reached by | Holds |
|---|---|---|
| `APIRegistry` | `client.API` | HTTP endpoints a plugin serves |
| `PageRegistry` | `client.PAGES` | Full-screen pages |
| `PublicRegistry` | `client.public` | Data one plugin offers to the others |
| `SecretRegistry` | `client.SECRETS` | API keys, kept out of the settings file |
| `QuickAccessRegistry` | `client.QUICK` | Buttons on the quick settings panel |
| `UserRegistry` | `client.USERS` | Approved devices and their tokens |
| `PlayerRegistry` | `client.PLAYER` | Whatever is playing, from any source |
| `CancelRegistry` | `client.CANCEL` | What "stop" means right now |

Keep these in mind as you read on — nearly everything else in this document is one of these six ideas in more detail.

---
