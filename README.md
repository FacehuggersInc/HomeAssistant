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
No client ID is needed.

| | |
|---|---|
| [Overview](docs/index.md) | Start here - the six ideas the whole thing is built on. |
| [Installation](docs/installation.md) | Getting it running. |
| [Updating](docs/updating.md) | Update checks, staging, rollback, exit codes. |
| [Architecture](docs/architecture.md) | How the client, backend and plugins fit together. |
| [Plugins](docs/plugins.md) | `plugin.toml`, `main.py`, and the full lifecycle. |
| [Widgets](docs/widgets.md) | The widget framework, layout, dragging, persistence. |
| [Pages](docs/pages.md) | What a page owns. |
| [Features](docs/features.md) | How pages expose behaviour to plugins. |
| [Registries](docs/registries.md) | API, page, public and secret registries. |
| [Quick settings](docs/quick-settings.md) | The global controls panel and its registry. |
| [Events](docs/events.md) | Client events and custom events. |
| [Voice assistant](docs/assistant.md) | Skills, intent matching, STT, TTS. |
| [On-screen keyboard](docs/keyboard.md) | The touch keyboard. |
| [Dialogs and overlays](docs/dialogs.md) | Overlay layers, masks, dialogs. |
| [Mixins](docs/mixins.md) | Extending existing methods from a plugin. |
| [Backend API](docs/api.md) | Every endpoint, and the `hactl.py` CLI. |
| [Philosophy](docs/philosophy.md) | Why it is built the way it is. |

---

## Controlling it remotely

A Flask API runs on port 5000. `hactl.py` at the project root is a single-file
CLI for it - stdlib only, copy it anywhere that can reach the panel:

```bash
./hactl.py hosts add panel --host 192.168.1.50   # prompts for the client ID
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
