"""
The package that sets up a machine to do the judging.

Built when it is asked for, not kept as a zip in the tree. The panel knows
the port it is configured to talk to and its own address, so what comes out
is already pointed at the right place and the README says exactly what to
type at both ends.

`judge_protocol.py` and `judge_prompt.py` travel with it, copied from this
tree rather than written out again. A wire format described in two files is a
wire format that disagrees with itself the first time either end changes, and
a prompt described in two files is two behaviours - the two ends here are on
different machines and get updated at different times.
"""

from __future__ import annotations

import socket
import time
from pathlib import Path

from src.assistant.judge_protocol import DEFAULT_PORT

#Bumped when what comes out changes in a way somebody would need to
#re-download for.
VERSION = "1.0"

FOLDER = "judge-server"

#fp16 rather than the panel's int8. A machine reached over a socket is not
#holding a screen and a microphone, and the larger build judges a little
#better for memory nobody is short of.
REMOTE_FILE = "onnx/model_fp16.onnx"

REQUIREMENTS = """\
# The model runs on onnxruntime rather than torch. A judgement is one forward
# pass, and this is a few megabytes against a few hundred.
onnxruntime

# Fetches the model on the first run and caches it.
huggingface_hub

# The tokenizer, without the rest of transformers.
tokenizers

# The logits come back as an array.
numpy
"""

STARTUP_SH = """\
#!/usr/bin/env bash
# Start the judge.
#
#     bash startup.sh
#
# `bash startup.sh` rather than `./startup.sh`, because a zip extracted by a
# file manager usually arrives without the executable bit and that is a
# confusing first thing to go wrong.
#
# Built {built} from {panel}, version {version}.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "Making an environment. The first run downloads the model, which is"
    echo "a few hundred megabytes."
    python3 -m venv .venv
    ./.venv/bin/pip install --upgrade pip
    ./.venv/bin/pip install -r requirements.txt
fi

exec ./.venv/bin/python judge-socket-process.py \\
    --host {listen} --port {port} \\
    --model "{model}" --file "{file}"
"""

STARTUP_BAT = """\
@echo off
REM Start the judge.
REM
REM     startup.bat
REM
REM Built {built} from {panel}, version {version}.
cd /d "%~dp0"

if not exist .venv (
    echo Making an environment. The first run downloads the model, which is
    echo a few hundred megabytes.
    python -m venv .venv
    .venv\\Scripts\\python -m pip install --upgrade pip
    .venv\\Scripts\\pip install -r requirements.txt
)

.venv\\Scripts\\python judge-socket-process.py ^
    --host {listen} --port {port} ^
    --model "{model}" --file "{file}"
"""

README = """\
# Judging for {panel}

This machine decides whether somebody was talking to the panel, or whether
the microphone picked up a television.

Built {built}, version {version}.

## Start it

    bash startup.sh          # Linux, macOS
    startup.bat              # Windows

The first run makes an environment and downloads the model — a few hundred
megabytes. After that it starts in a few seconds.

It listens on port **{port}**, on every address of this machine.

## Point the panel at it

On {panel}, under **Assistant → Wake**:

| Setting | Set it to |
|---------|-----------|
| `judge_backend` | `qwen` |
| `judge_where` | `socket` |
| `judge_host` | `{address}` |
| `judge_port` | `{port}` |

{address_note}

## What it is for

The panel decides by sentence shape whether an utterance was meant for it.
That works for "what time is it" and not for "what did she tell you", which
is a question by every measure and was asked of somebody on television.

This answers the same question with a small language model. It replies with
one word — `ANSWER` or `IGNORE` — and nothing else: not prose, not a score.
The panel cannot receive an explanation from it because there is nowhere in
the wire format to put one.

**If this machine is off, the panel is not broken.** Every utterance falls
back to the rules, which is exactly how the panel behaves with the judge
turned off. You can stop this at any time and the only difference is that
the television gets answered a little more often.

## Checking it

    curl -s --max-time 3 telnet://{address}:{port} <<< '{{"cmd":"status"}}'

Or from the panel's log: every judgement is written there with the key and
how long it took.

## The timeout

The panel gives up after `judge_timeout` seconds and uses the rules. Somebody
is standing in front of it while this runs, so a judge that has gone slow has
to stop being consulted rather than hold up the answer. If the log shows
judgements being abandoned, either the network or this machine is the reason —
the timeout is not the thing to raise first.
"""


def _panel_address(client) -> tuple:
    """This machine's name and address, for the README to suggest."""
    name = ""
    address = ""
    try:
        name = socket.gethostname()
    except Exception:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Never sent. Connecting a UDP socket picks the interface that
            # would be used to reach the internet, which is the address
            # another machine on the network can reach.
            probe.connect(("10.255.255.255", 1))
            address = probe.getsockname()[0]
        finally:
            probe.close()
    except Exception:
        pass
    return name, address


def build_for(client) -> tuple:
    """(folder, {relpath: bytes}) for the judge server, as configured now."""
    port = client.setting("assistant.wake.judge_port.value", DEFAULT_PORT)
    try:
        port = int(port or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT

    model = str(client.setting("assistant.wake.judge_model.value", "")
                or "onnx-community/Qwen3-1.7B-ONNX").strip()
    host = str(client.setting("assistant.wake.judge_host.value", "")
               or "").strip()

    panel = "the panel"
    try:
        panel = client.panel_name()
    except Exception:
        pass

    name, mine = _panel_address(client)
    if host:
        address = host
        note = (f"`{host}` is what the panel is set to now, so if this is the "
                f"machine you are reading this on, it is already correct.")
    else:
        address = name or mine or "this-machine"
        note = ("The panel does not have an address set yet. Use this "
                "machine's address on your network — `hostname -I` on Linux, "
                "`ipconfig` on Windows.")

    built = time.strftime("%Y-%m-%d %H:%M")
    here = Path(__file__).resolve().parent
    files: dict = {}

    files["judge-socket-process.py"] = (here / "judge-socket-process.py").read_bytes()
    # Copied rather than regenerated. Both ends have to agree about the wire
    # and about the question, and the only way to be sure is for one of them
    # to be a copy.
    files["judge_protocol.py"] = (here / "judge_protocol.py").read_bytes()
    files["judge_prompt.py"] = (here / "judge_prompt.py").read_bytes()
    files["requirements.txt"] = REQUIREMENTS.encode("utf-8")

    fields = dict(listen="0.0.0.0", port=port, model=model, file=REMOTE_FILE,
                  version=VERSION, built=built, panel=panel)
    files["startup.sh"] = STARTUP_SH.format(**fields).encode("utf-8")
    files["startup.bat"] = STARTUP_BAT.format(**fields).encode("utf-8")
    files["README.md"] = README.format(panel=panel, port=port, address=address,
                                       version=VERSION, built=built,
                                       address_note=note).encode("utf-8")
    return FOLDER, files


CONTENTS = ("judge-socket-process.py", "judge_protocol.py", "judge_prompt.py",
            "requirements.txt", "startup.sh", "startup.bat", "README.md")

DESCRIPTION = (
    "Everything another machine needs to decide whether somebody was talking "
    "to this panel. The server, the wire format, the prompt, a requirements "
    "file and a startup script that makes its own environment on the first "
    "run. Built with this panel's port already filled in, and a README that "
    "names the settings to change at both ends.")


def register(client) -> None:
    """Offer it. Called once, while the Client is being built."""
    client.PACKAGES.register(
        "client", "judge-server", "Judge server",
        lambda: build_for(client),
        description=DESCRIPTION, version=VERSION, contents=CONTENTS)
