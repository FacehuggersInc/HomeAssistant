"""
The wire between the panel and whatever decides who was being spoken to.

One module, imported by both ends and copied verbatim into the package that
sets up a remote machine. A protocol described in two places is a protocol
that disagrees with itself the first time either end changes.

**One line of JSON each way, and nothing else.** There is no audio here and
no session to hold open, so the whole exchange is a request and an answer on
one connection - which keeps `nc` a usable debugging tool and keeps the
process on the far end simple enough to read in one sitting.

**The answer is a KEY.** Not prose, not a probability, not a key with an
explanation after it. The panel has one decision to make and the key is that
decision, so there is nothing to parse out of a sentence and nothing to
threshold. A judge that answered "probably not, because..." would be a judge
whose output needed its own parser, and that parser would be the thing that
broke on the day the model felt chatty.
"""

from __future__ import annotations

import json
import socket

#The default port. Clear of the panel's own 5000, the speech process's
#65432/65433 and the voice server's 8770.
DEFAULT_PORT = 8771

#Nothing sensible is this big, and a length read from a socket is exactly
#where a wrong number turns into an allocation that ends the process.
MAX_LINE = 64 * 1024

#What the judge may answer, and what each one means to the panel.
#
#Two, not three. A third "unsure" reads as helpful and is not: something has
#to act on it, and the only thing it could do is fall back to the rules -
#which is what an unavailable judge already does, and which the judge can
#say by simply not answering. Every extra key is a branch at both call sites
#that has to be right in a room nobody is watching.
ANSWER = "ANSWER"
IGNORE = "IGNORE"
KEYS = (ANSWER, IGNORE)

#What the far end understands.
COMMANDS = ("ping", "status", "judge")


class ProtocolError(Exception):
    """The other end said something this one cannot act on."""


## -- one line of JSON


def send_line(sock, payload: dict) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_LINE:
        raise ProtocolError(f"A message of {len(body)} bytes is too long.")
    sock.sendall(body + b"\n")


def read_line(sock, buffer: bytearray = None) -> tuple:
    """
    One JSON line, and whatever was read past it.

    The leftover comes back rather than being dropped: TCP does not preserve
    message boundaries, so a read can return the end of this line and the
    start of the next in one go, and throwing that away loses a message that
    was already on the wire.
    """
    buffer = bytearray() if buffer is None else buffer
    while b"\n" not in buffer:
        if len(buffer) > MAX_LINE:
            raise ProtocolError("A line arrived without an end to it.")
        chunk = sock.recv(4096)
        if not chunk:
            raise ProtocolError("The connection closed mid-message.")
        buffer.extend(chunk)

    line, _, rest = bytes(buffer).partition(b"\n")
    try:
        payload = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"Could not read the message: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("A message has to be an object.")
    return payload, bytearray(rest)


## -- what a request looks like


def request(text: str, transcript: str = "", wake: str = "",
            last_query: str = "", last_answer: str = "",
            in_session: bool = False) -> dict:
    """
    Everything the far end is told about one utterance.

    Built here rather than at each call site, so the panel and the package on
    another machine cannot drift about what a field is called.

    `text` is the phrase as the panel routed it - the wake word stripped, the
    punctuation cleaned. `transcript` is what was actually heard, wake word
    and all, because how somebody addressed the panel is evidence about
    whether they addressed it at all.

    The turn before is there because a fragment is only judgeable against
    what it follows: "Tuesday" is a complete answer to a question the panel
    asked a moment ago and is nothing at all on its own.
    """
    return {
        "cmd": "judge",
        "text": str(text or ""),
        "transcript": str(transcript or ""),
        "wake": str(wake or ""),
        "last_query": str(last_query or ""),
        "last_answer": str(last_answer or ""),
        "in_session": bool(in_session),
    }


def answer(key: str) -> dict:
    """The far end's reply. A key, and whether it could answer at all."""
    key = str(key or "").strip().upper()
    if key not in KEYS:
        raise ProtocolError(f"'{key}' is not one of {', '.join(KEYS)}.")
    return {"ok": True, "key": key}


def failure(reason: str) -> dict:
    return {"ok": False, "reason": str(reason or "")}


def key_from(payload: dict) -> str:
    """
    The key in a reply, or "" when there is not one.

    Empty rather than a guess. A caller that cannot tell "it said ignore"
    from "it did not answer" is a caller that treats a broken judge as a
    judge with opinions.
    """
    if not isinstance(payload, dict) or not payload.get("ok"):
        return ""
    key = str(payload.get("key") or "").strip().upper()
    return key if key in KEYS else ""


## -- asking, from the panel side


def ask(host: str, port: int, payload: dict, timeout: float) -> dict:
    """
    One request, one answer, one connection.

    `timeout` covers the whole exchange rather than each recv, because what
    the caller cares about is how long the panel waits before giving up and
    using the rules - and a per-read timeout can be met repeatedly while the
    total runs away.
    """
    deadline = timeout
    with socket.create_connection((host, int(port)), timeout=deadline) as sock:
        sock.settimeout(deadline)
        send_line(sock, payload)
        reply, _rest = read_line(sock)
    return reply
