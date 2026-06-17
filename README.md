# Desktop Home Assistant

A fullscreen desktop assistant application built with Python and PyQt6. Acts as a smart home display similar to Google Home Hub or Amazon Echo Show — cycling wallpapers, clock, weather, word of the day, notifications, and a voice assistant pipeline.

---

## Requirements

- Python 3.11 or newer
- pip
- A display (X11 or Wayland on Linux, or Windows)

---

## Installation

### Windows

**1. Install Python**

Download and install Python 3.11+ from [python.org](https://www.python.org/downloads/).
During installation, check **"Add Python to PATH"**.

**2. Install system dependencies**

Install [Chocolatey](https://chocolatey.org/install) if not already installed, then:

```powershell
choco install ffmpeg
```

**3. Clone or download the project**

```powershell
cd C:\Projects
git clone <repo-url> HomeAssistant
cd HomeAssistant
```

**4. Create and activate a virtual environment**

```powershell
python -m venv .venv
.venv\Scripts\activate
```

**5. Install Python dependencies**

```powershell
pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

**6. Run**

```powershell
python app.py
```

---

### Arch Linux

**1. Install system packages**

```bash
sudo pacman -S --needed \
  python \
  python-pip \
  base-devel \
  portaudio \
  ffmpeg \
  qt6-base \
  git
```

- `base-devel` — required to build `webrtcvad` (C extension)
- `portaudio` — required by `sounddevice`
- `ffmpeg` — required by `faster-whisper` and audio processing
- `qt6-base` — Qt6 runtime libraries

**2. Clone or download the project**

```bash
git clone <repo-url> ~/HomeAssistant
cd ~/HomeAssistant
```

**3. Create and activate a virtual environment**

In VSCode: open the project folder, press `Ctrl+Shift+P` → `Python: Create Environment` → `Venv`.

Or from the terminal:

```bash
python -m venv .venv
source .venv/bin/activate
```

**4. Install Python dependencies**

```bash
pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

**5. Run**

```bash
python app.py
```

**Wayland note (Hyprland, Sway, etc.):**

```bash
export QT_QPA_PLATFORM=wayland
python app.py
```

Or add it to your compositor's autostart.

---

### Linux Mint

Linux Mint ships Python 3 but may not have a recent enough version. Check first:

```bash
python3 --version
```

If it's below 3.11, install a newer version:

```bash
sudo apt install software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install python3.12 python3.12-venv python3.12-dev
```

**1. Install system packages**

```bash
sudo apt update
sudo apt install -y \
  python3-pip \
  python3-venv \
  build-essential \
  portaudio19-dev \
  ffmpeg \
  libxcb-xinerama0 \
  libxcb-cursor0 \
  git
```

- `portaudio19-dev` — required by `sounddevice`
- `ffmpeg` — required by `faster-whisper`
- `libxcb-xinerama0` and `libxcb-cursor0` — required by Qt6 on X11
- `build-essential` — required to build `webrtcvad`

**2. Clone or download the project**

```bash
git clone <repo-url> ~/HomeAssistant
cd ~/HomeAssistant
```

**3. Create and activate a virtual environment**

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

**4. Install Python dependencies**

```bash
pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

**5. Run**

```bash
python app.py
```

---

## Configuration

Settings are stored at:

| Platform | Path |
|---|---|
| Windows | `%LOCALAPPDATA%\DesktopHomeAssistant\DesktopHomeAssistant.json` |
| Linux | `~/.local/share/DesktopHomeAssistant/DesktopHomeAssistant.json` |

The settings file is created automatically on first run from the template at `src/assets/data/new-template.json`. Edit it directly or use the settings page in the app.

Key settings to configure after first run:

- `home.images.value` — path to a folder of wallpaper images
- `notifications.notification_position.value` — where notifications appear (`top-right` recommended)
- `weather.latitude.value` and `weather.longitude.value` — your location for weather data
- `weather.timezone.value` — your timezone string (e.g. `America/New_York`)

---

## Plugin Development

Plugins live in the `plugins/` folder at the project root. Each plugin is a folder containing:

```
plugins/
└── MyPlugin/
    ├── plugin.toml       # name and key
    ├── main.py           # Plugin subclass
    └── widgets/          # optional widget files
        └── my_widget.py
```

**plugin.toml**

```toml
[plugin]
name = "My Plugin"
key  = "myplugin"
```

**main.py**

```python
from src.plugin.template import Plugin
from src.mixins import mixin
from src.ui.icons import Icons
from .widgets.my_widget import MyWidget

class MyPlugin(Plugin):

    def load(self):
        self.client.log("info", "[MyPlugin] Loaded.")

    @mixin("sub.home.__init__", "myplugin", "after")
    def inject_widgets(self, sub_home, *args):
        sub_home.features().add_widgets([MyWidget(self.client)])

    def unload(self):
        pass
```

Plugins can also register pages:

```python
def load(self):
    from .pages.my_page import MyPage
    self.client.add_page("#mypage", "My Page", MyPage)
```

And register custom icons:

```python
from src.ui.icons import register
register("my-icon", "mdi.rocket")
```

---

## Enabling Voice Assistant

STT and TTS are disabled by default. To enable them, open `src/main.py` and find the assistant block in `__init__`:

```python
self.STT = None   # STTProcessing(self)  — disabled
self.TTS = None   # TTSProcessing(self)   — disabled
```

Replace with:

```python
self.STT = STTProcessing(self)
self.TTS = TTSProcessing(self)
```

Voice requires an ElevenLabs API key (for TTS) set in your `.env` file at the project root:

```
ELEVENLABS_API_KEY=your_key_here
```

---

## Suppressing the Kvantum style warning

On systems with Kvantum installed as the default Qt theme, you may see:

```
QApplication: invalid style override 'kvantum' passed, ignoring it.
```

This is harmless. To suppress it, add to the top of `app.py`:

```python
import os
os.environ["QT_STYLE_OVERRIDE"] = ""
```