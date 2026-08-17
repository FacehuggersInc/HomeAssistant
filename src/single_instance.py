"""
Is a panel already running here.

Two panels on one machine is not a harmless duplicate. Only the first to
start binds port 5000, so the phone's requests all reach THAT one while the
window somebody is looking at belongs to the second - stickers are placed,
timers start and notes appear, on a copy of the app nobody can see. Nothing
errors, and every symptom points at the feature rather than at the panel.

The check asks the port, rather than looking for a lock file or another
process by name:

  * A lock file outlives the process that wrote it. A panel killed with -9
    leaves one behind and the next launch refuses to start for no reason
    anybody can see.
  * A process list finds the wrong things - an editor with app.py open, this
    very check - and cannot tell a panel from a copy of the tree being
    updated.
  * The port IS the resource being contended. If something answers on it,
    starting a second panel cannot work, whatever the process list says.
"""

from __future__ import annotations

import json
import socket
from urllib.error import URLError, HTTPError
from urllib.request import urlopen

from src.backend import PORT

#Localhost only. ADDRESS is 0.0.0.0 - what the server binds - which is not an
#address to connect TO, and asking the machine's outward address would find a
#panel on another machine and refuse to start because of it.
HOST = "127.0.0.1"

#Short. This runs before the window opens, on a loopback connection to
#something that either answers at once or is not there.
TIMEOUT = 1.5


def _port_open() -> bool:
    """Whether anything at all is listening. Cheap, and usually the answer."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(TIMEOUT)
        return probe.connect_ex((HOST, PORT)) == 0


def _ping() -> dict | None:
    """
    /ping's reply, or None if nothing recognisable answered.

    Unauthenticated, so an approved panel answers 401 rather than 200. That
    is a perfectly good answer for this purpose: the reply is the panel's own
    JSON, which is what identifies it. Asking for a token here would mean the
    launcher holding a device credential to find out whether to start.
    """
    try:
        with urlopen(f"http://{HOST}:{PORT}/ping", timeout=TIMEOUT) as reply:
            body = reply.read()
    except HTTPError as e:
        # 401 is the expected case - a running panel, refusing to say more.
        try:
            body = e.read()
        except Exception:
            return None
    except (URLError, OSError):
        return None

    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except (ValueError, AttributeError):
        return None
    return data if isinstance(data, dict) else None


def already_running() -> tuple[bool, str]:
    """
    (stand down, why).

    Three outcomes, and they are not the same thing:

      * Nothing on the port          -> start.
      * The panel answering          -> stand down.
      * Something else on the port   -> stand down, and say so plainly.

    The third is worth separating. A panel cannot serve its phone pages
    without that port, so starting anyway produces the same invisible
    split-brain as a duplicate - but the cause is a different program, and a
    message about "another panel" would send somebody hunting for one that
    is not there.
    """
    if not _port_open():
        return False, ""

    reply = _ping()
    if reply is None:
        return True, (f"Something is already listening on port {PORT}, and it "
                      f"is not this panel - it did not answer /ping. The panel "
                      f"cannot serve its pages without that port.")

    # Its own reply shape, from the route in backend.py.
    if reply.get("alive") or "request" in reply:
        app = reply.get("app") or "the panel"
        page = reply.get("page")
        where = f", showing '{page}'" if page else ""
        return True, (f"{app} is already running on this machine{where}. "
                      f"Two would share port {PORT}: the phone would reach one "
                      f"of them and the screen would show the other.")

    return True, (f"Port {PORT} is taken by something that answered "
                  f"unexpectedly. Not starting a second panel.")
