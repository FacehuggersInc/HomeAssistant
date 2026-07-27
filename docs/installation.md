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
