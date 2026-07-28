# Home Assistant

A cross-platform, plugin-driven home assistant panel built with PyQt6, meant to
run fullscreen on a wall-mounted touchscreen.

Almost nothing is hardcoded. Pages, widgets, tiles, voice skills, settings and
API endpoints all arrive from plugins; the client coordinates rather than owns.

---

## Install

```bash
git clone https://github.com/FacehuggersInc/HomeAssistant.git
cd HomeAssistant

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
python launcher.py
```

Run `launcher.py`, not `app.py` - the launcher supervises the process and is
what applies updates, restarts after a crash and rolls back a bad update.

Full detail in [docs/installation.md](docs/installation.md).

---

## Documentation

The full documentation lives in the [`docs/`](docs/) folder as plain markdown,
so it reads fine in any editor.

The panel also serves it rendered, with a sidebar and search:

```
http://<panel-ip>:5000/docs
```

That address is shown in **Settings → Info**, with buttons to open or copy it.
No device token is needed.

| | |
|---|---|
| [Overview](docs/index.md) | Start here - the six ideas the whole thing is built on. |
| [Installation](docs/installation.md) | Getting it running. |
| [Application lifecycle](docs/lifecycle.md) | Startup, page switching, default page, shutdown. |
| [Updating](docs/updating.md) | Update checks, staging, rollback, exit codes. |
| [Architecture](docs/architecture.md) | How the client, backend and plugins fit together. |
| [Plugins](docs/plugins.md) | `plugin.toml`, `main.py`, and the full lifecycle. |
| [Bundled plugins](docs/bundled-plugins.md) | The six that ship, and what each provides. |
| [Pages](docs/pages.md) | Registering a page, a full example, sub-pages. |
| [Widgets](docs/widgets.md) | Writing and registering widgets, layout, persistence. |
| [Tiles](docs/tiles.md) | Writing and registering tiles, the grid and panel. |
| [Features](docs/features.md) | Exposing and calling page features. |
| [Registries](docs/registries.md) | API, page, public, secret and quick access registries. |
| [Users](docs/users.md) | Device approval, tokens, and identifying a caller. |
| [The web page](docs/webpage.md) | The built-in browser page and its locks. |
| [Quick settings](docs/quick-settings.md) | The global controls panel and its registry. |
| [Events](docs/events.md) | Every client event, with examples. |
| [Settings](docs/settings.md) | Declaring settings, types, migration. |
| [Threading](docs/threading.md) | `call_on_ui`, background threads, timeouts. |
| [Logging](docs/logging.md) | Levels, log files, what is worth logging. |
| [Styling](docs/styling.md) | `set_style`, fonts, colours, stylesheet conventions. |
| [Notifications, state, assets](docs/notifications.md) | Toasts, shared state, registered files. |
| [Dialogs and overlays](docs/dialogs.md) | Overlay layers, masks, dialogs, panels. |
| [On-screen keyboard](docs/keyboard.md) | The touch keyboard. |
| [Voice assistant](docs/assistant.md) | Intent matching, STT, TTS. |
| [Writing skills](docs/skills.md) | Skills, Matcher patterns, follow-up questions. |
| [Mixins](docs/mixins.md) | Extending existing methods from a plugin. |
| [Backend API](docs/api.md) | Every endpoint, and the `hactl.py` CLI. |
| [Development philosophy](docs/philosophy.md) | Why it is built the way it is. |

---

## Controlling it remotely

A Flask API runs on port 5000. `hactl.py` at the project root is a single-file
CLI for it - stdlib only, copy it anywhere that can reach the panel:

```bash
./hactl.py hosts add panel --host 192.168.1.50   # pairs with the panel
./hactl.py update --check
./hactl.py plugins list
```

See [docs/api.md](docs/api.md).

---

## The six ideas

**Plugins** provide functionality. **Pages** own UI systems. **Features** are
how a page exposes parts of itself to plugins without being imported by them.
**Widgets and tiles** are the components that live on pages. **Registries**
hold what plugins have declared. **Events** are the client's lifecycle on a bus
anything can subscribe to.

Each is a page in the docs.

---

## Requirements

Python 3.10 or newer. PyQt6, Flask, and the packages in `requirements.txt`.
The voice assistant additionally wants a working microphone and will download a
Whisper model on first use; everything else runs without it.
