"""
The package that sets up a machine to do the speaking.

Built when it is asked for, not kept as a zip in the tree. The panel knows the
port it is configured to talk to, the voice that is chosen and its own address,
so what comes out is already pointed at the right place and the README says
exactly what to type at both ends.

`tts_protocol.py` travels with it, copied from this tree rather than written
out again. A wire format described in two files is a wire format that
disagrees with itself the first time either end changes, and the two ends here
are on different machines and get updated at different times.
"""

from __future__ import annotations

import socket
import time
from pathlib import Path

from src.assistant.tts_protocol import DEFAULT_PORT

#Bumped when what comes out changes in a way somebody would need to
#re-download for. Printed by the scripts and named in the README, so a
#copy on another machine can be told from the current one.
VERSION = "1.1"

FOLDER = "tts-server"

REQUIREMENTS = """\
# The speech model. Everything else it needs comes with it.
pocket-tts

# Audio arrives as float32 samples and is packed with numpy at this end.
numpy
"""

STARTUP_SH = """\
#!/usr/bin/env bash
# Start the speech server.
#
#     bash startup.sh
#
# `bash startup.sh` rather than `./startup.sh`, because a zip extracted by a
# file manager usually arrives without the executable bit and that is a
# confusing first thing to hit. Either works once it is set.
#
# Every step is checked and says what it wants. `set -e` on its own stops at
# the first failure and leaves whoever ran it looking at a prompt, wondering
# which line it was.

# Started with `sh startup.sh` rather than bash?
#
# Then this is dash on most Linux machines, and the redirection below is a
# bashism it refuses - one terse line about line 16, and nothing set up. Easy
# to miss and impossible to act on. Handed to bash instead, before anything
# bash-only is reached.
#
# POSIX on purpose: it has to parse under whatever is reading it.
if [ -z "${{BASH_VERSION:-}}" ]; then
    exec bash "$0" "$@"
fi

cd "$(dirname "$0")" || exit 1
LOG="setup.log"
exec > >(tee -a "$LOG") 2>&1

# Stamped when this was built, and printed first.
#
# A package is generated on demand rather than kept as a file, so the copy on
# a machine is as old as the day somebody downloaded it - and two copies look
# identical. Without this, "it does nothing" and "it does nothing any more"
# are the same sentence, and there is no way to tell whether the script being
# run is the one being discussed.
BUILT="{built}"
echo "--- $(date) --- speech server package {version}, built $BUILT"

die() {{ echo; echo "STOPPED: $*"; echo "The whole of this is in $LOG."; exit 1; }}

PY_BIN="$(command -v python3 || command -v python || true)"
[ -n "$PY_BIN" ] || die "no python3 on this machine. Install Python 3.10 or newer."
echo "Using $PY_BIN ($("$PY_BIN" --version 2>&1))"

# Anything called .venv that is not a usable environment is cleared out.
#
# A folder with no interpreter in it is worse than nothing there at all: a
# test for the folder passes, creation is skipped, and the failure lands on
# the last line pointing nowhere near the cause. A leftover FILE by that name
# is worse still - `python -m venv` refuses it, and the error is about
# permissions or paths rather than about the file being in the way.
if [ -e .venv ] && [ ! -x .venv/bin/python ]; then
    echo "There is a .venv here that is not a working environment - starting again."
    rm -rf .venv
fi

if [ ! -d .venv ]; then
    echo "Making a virtual environment. The model is downloaded after this and"
    echo "takes a few minutes the first time."
    if ! "$PY_BIN" -m venv .venv; then
        die "python could not make a virtual environment. On Debian, Ubuntu \
or Mint this is usually a missing package: sudo apt install python3-venv"
    fi
    [ -x .venv/bin/python ] || die ".venv was made but has no interpreter in it."
    .venv/bin/python -m pip install --upgrade pip \
        || die "pip would not update itself. Is this machine online?"
    .venv/bin/python -m pip install -r requirements.txt \
        || die "the requirements would not install. The reason is above."
    echo "Environment ready."
fi

[ -x .venv/bin/python ] || die ".venv/bin/python is missing. Delete .venv and \
run this again."

echo "Starting the speech server on port {port}."
exec .venv/bin/python tts-socket-process.py \\
    --host {listen} --port {port} --voice "{voice}"{language}
"""

STARTUP_BAT = """\
@echo off
REM Start the speech server. Run startup.bat from this folder.
cd /d "%~dp0"
set LOG=setup.log
echo Speech server package {version}, built {built}

where python >nul 2>nul
if errorlevel 1 (
    echo STOPPED: no python on this machine. Install Python 3.10 or newer and
    echo tick "Add python.exe to PATH" in the installer.
    pause
    exit /b 1
)

if exist .venv\\Scripts\\python.exe goto ready
if exist .venv (
    echo There is a .venv here with no interpreter in it - starting again.
    rmdir /s /q .venv
)

echo Making a virtual environment. The model is downloaded after this and
echo takes a few minutes the first time.
python -m venv .venv >>%LOG% 2>&1
if errorlevel 1 (
    echo STOPPED: python could not make a virtual environment. See %LOG%.
    pause
    exit /b 1
)
.venv\\Scripts\\python -m pip install --upgrade pip >>%LOG% 2>&1
.venv\\Scripts\\python -m pip install -r requirements.txt >>%LOG% 2>&1
if errorlevel 1 (
    echo STOPPED: the requirements would not install. See %LOG%.
    pause
    exit /b 1
)
echo Environment ready.

:ready
echo Starting the speech server on port {port}.
.venv\\Scripts\\python tts-socket-process.py --host {listen} --port {port} ^
    --voice "{voice}"{language}
"""

README = """\
# Speech for {panel}

*Package {version}, built {built}. Downloaded again from Packages on the
panel, this file is rebuilt with whatever the settings say at that moment - so
if something here does not match the panel, this copy is the older of the
two.*

This runs the voice on this machine instead of on the panel. The panel sends
text and takes back audio; nothing about the model runs there.

## Why

A neural voice holds a processor for a second or two per sentence. On the
panel that is a second or two where the screen, the web page and the
microphone all wait for it - one interpreter, one lock. Nothing can interrupt
a model part way through either, so a wake word during that gap does nothing
at all.

Here, both problems go away. The panel does no synthesis, and stopping a reply
is a message rather than a flag the model never reads.

## Setting it up

    bash startup.sh

or on Windows:

    startup.bat

`bash startup.sh` rather than `./startup.sh`: a zip extracted by a file
manager usually arrives without the executable bit, and that is a confusing
first thing to hit. Either works once it is set.

Every step is checked and says what it wants, and the whole of it is written
to `setup.log` beside the script.

The first run makes a virtual environment and downloads the model, which takes
a few minutes. After that it starts in seconds.

It listens on **port {port}**. Leave it running.

## Then, on the panel

Settings -> Audio -> Speech:

| Setting | Set it to |
|---------|-----------|
| where   | `socket` |
| host    | `{address}` |
| port    | `{port}` |

{address_note}

The panel checks the connection when the assistant starts and says what it
found in the log. If it cannot reach this machine it will say so there rather
than going quiet.

## Checking it by hand

    printf '{{"cmd":"status"}}\\n' | nc {address} {port}

A healthy answer has `"ready": true` in it. `"ready": false` comes with the
reason - almost always that the model has not finished downloading yet.

## What is in here

| File | |
|------|--|
| `tts-socket-process.py` | The server. |
| `tts_protocol.py` | The wire format, shared with the panel. |
| `requirements.txt` | What to install. |
| `startup.sh` / `startup.bat` | Make the environment, then run it. |

`tts_protocol.py` is a copy of the panel's own. If you update the panel,
download this package again rather than editing either copy - the two ends
have to agree about the wire, and that is easiest when neither is hand-edited.

## Using a different model

`Voice` in `tts-socket-process.py` is the only class that knows about a model.
It loads one, lists its voices and turns text into float32 samples, in about
thirty lines. Everything else - the sessions, the streaming, the cancelling -
is model-agnostic.
"""


def _panel_address(client) -> tuple:
    """
    This machine's address, as something to type on another one.

    Best effort. The answer goes in a README as an example rather than into
    anything that depends on it, so a guess that turns out to be the wrong
    interface costs a sentence somebody has to correct rather than a setup
    that does not work.
    """
    try:
        name = socket.gethostname()
    except Exception:
        name = ""
    address = ""
    try:
        # Nothing is sent. Asking a UDP socket to route somewhere tells the
        # kernel to pick a source address, which is the one another machine
        # on this network would reach.
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("10.255.255.255", 1))
            address = probe.getsockname()[0]
        finally:
            probe.close()
    except Exception:
        address = ""
    return name, address


def build_for(client) -> tuple:
    """
    (folder, {relpath: bytes}) for the speech server, as configured now.
    """
    port = client.setting("audio.speech.tts_port.value", DEFAULT_PORT)
    try:
        port = int(port or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    voice = str(client.setting("audio.speech.tts_voice.value", "")
                or "anna").strip() or "anna"
    # Always a real language. `default` was an option here once and was never
    # one of the model's; baked into a startup script it becomes a machine
    # that will not speak and a file somebody has to open to find out why.
    language = str(client.setting("audio.speech.tts_language.value", "")
                   or "").strip().lower()
    if language in ("", "default", "auto", "none"):
        language = "english"
    host = str(client.setting("audio.speech.tts_host.value", "") or "").strip()

    panel = "the panel"
    try:
        panel = client.panel_name()
    except Exception:
        pass

    name, mine = _panel_address(client)
    if host:
        # Already configured, so the README can name the machine rather than
        # guessing at it.
        address = host
        note = (f"`{host}` is what the panel is set to now, so if this is the "
                f"machine you are reading this on, it is already correct.")
    else:
        address = name or mine or "this-machine"
        note = ("The panel does not have an address set yet. Use this "
                "machine's address on your network - `hostname -I` on Linux, "
                "`ipconfig` on Windows.")

    # When this was built, to the minute. Local time rather than UTC: it is
    # read beside a log somebody is looking at on the same machine.
    built = time.strftime("%Y-%m-%d %H:%M")

    here = Path(__file__).resolve().parent
    files: dict = {}

    server = (here / "tts-socket-process.py").read_bytes()
    files["tts-socket-process.py"] = server
    # Copied rather than regenerated. Both ends have to agree about the wire,
    # and the only way to be sure of that is for one of them to be a copy.
    files["tts_protocol.py"] = (here / "tts_protocol.py").read_bytes()
    files["requirements.txt"] = REQUIREMENTS.encode("utf-8")
    language_flag = f' --language "{language}"' 
    files["startup.sh"] = STARTUP_SH.format(
        listen="0.0.0.0", port=port, voice=voice,
        language=language_flag, version=VERSION,
        built=built).encode("utf-8")
    files["startup.bat"] = STARTUP_BAT.format(
        listen="0.0.0.0", port=port, voice=voice,
        language=language_flag, version=VERSION,
        built=built).encode("utf-8")
    files["README.md"] = README.format(
        panel=panel, port=port, address=address, version=VERSION,
        built=built, address_note=note).encode("utf-8")
    return FOLDER, files


CONTENTS = ("tts-socket-process.py", "tts_protocol.py", "requirements.txt",
            "startup.sh", "startup.bat", "README.md")

DESCRIPTION = (
    "Everything another machine needs to do the speaking for this panel. The "
    "server, the wire format, a requirements file and a startup script that "
    "makes its own environment on the first run. Built with this panel's port "
    "and voice already filled in, and a README that names the settings to "
    "change at both ends.")


def register(client) -> None:
    """Offer it. Called once, while the Client is being built."""
    client.PACKAGES.register(
        "client", "tts-server", "Speech server",
        lambda: build_for(client),
        description=DESCRIPTION, version=VERSION, contents=CONTENTS)
