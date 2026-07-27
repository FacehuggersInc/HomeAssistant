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
