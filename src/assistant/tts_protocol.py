"""
The wire between the panel and whatever is doing the speaking.

One module, imported by both ends and copied verbatim into the package that
sets up a remote machine. A protocol described in two places is a protocol
that disagrees with itself the first time either end changes.

**Text control, binary body.** Every message is one line of JSON. When audio
follows, the line says how it is framed and the frames come straight after it
on the same connection. That keeps `nc` a usable debugging tool while leaving
the audio unencoded - base64 on a few hundred kilobytes per sentence is real
work on a panel that has none to spare.

**A session key, and three separate connections.** `say` answers immediately
with a key and does not wait for the model. `stream` takes that key and stays
open for as long as the audio does. `cancel` arrives on a connection of its
own, which is the whole reason the key exists: the streaming connection is
busy carrying audio, so nothing can be said on it, and a spoken reply that
cannot be stopped until it finishes is the thing this replaces.
"""

from __future__ import annotations

import json
import socket
import struct

#The default port. Above the privileged range and clear of the panel's own
#5000 and the speech process's 65432/65433.
DEFAULT_PORT = 8770

#How a chunk is framed inside a stream: a length, then that many bytes. A
#zero length ends the audio, and the JSON line after it says how it went.
#Big-endian because a wire format that depends on the endianness of whichever
#machine happened to write it is not a wire format.
FRAME = struct.Struct(">I")

#Audio is 32-bit float, mono, at whatever rate the header declares. Float
#because that is what the models produce and what sounddevice accepts, so
#neither end has to convert.
FORMAT = "f32"

#Nothing sensible is this big, and a length prefix read from a socket is
#exactly where a wrong number turns into an allocation that ends the process.
MAX_FRAME = 4 * 1024 * 1024
MAX_LINE = 64 * 1024


class ProtocolError(Exception):
    """The other end said something this one cannot act on."""


## -- one line of JSON


def send_line(sock, payload: dict) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_LINE:
        raise ProtocolError(f"A message of {len(body)} bytes is too long.")
    sock.sendall(body + b"\n")


def read_line(sock, buffer: bytearray, timeout: float = 30.0) -> dict:
    """
    One JSON message, leaving anything after the newline in `buffer`.

    The buffer belongs to the caller and carries between calls, because a
    single recv can return part of a line, several lines, or a line followed
    by the first bytes of the audio that comes after it. A reader that
    discarded the remainder would eat the start of the stream.
    """
    sock.settimeout(timeout)
    while b"\n" not in buffer:
        if len(buffer) > MAX_LINE:
            raise ProtocolError("A message arrived without an end to it.")
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            raise ProtocolError("Nothing answered in time.")
        if not chunk:
            raise ProtocolError("The connection closed mid-message.")
        buffer.extend(chunk)

    line, _, rest = bytes(buffer).partition(b"\n")
    buffer[:] = rest
    try:
        payload = json.loads(line.decode("utf-8"))
    except Exception as exc:
        raise ProtocolError(f"Could not read the message: {exc}")
    if not isinstance(payload, dict):
        raise ProtocolError("A message has to be an object.")
    return payload


## -- audio frames


def send_frame(sock, data: bytes) -> None:
    """
    One chunk of audio.

    An empty one is refused rather than sent. Zero length is what ends the
    audio, so an empty chunk and the end of the stream would be the same
    bytes - and a producer emitting one would silently truncate its own
    sentence. Nothing wants to send no audio; the check is here so that
    mistake is loud at the point it is made.
    """
    if not data:
        raise ProtocolError("An empty chunk cannot be sent - zero length is "
                            "what ends the audio. Skip it instead.")
    if len(data) > MAX_FRAME:
        raise ProtocolError(f"A chunk of {len(data)} bytes is too big.")
    sock.sendall(FRAME.pack(len(data)) + data)


def end_frames(sock) -> None:
    """The zero-length frame that closes the audio."""
    sock.sendall(FRAME.pack(0))


def read_exactly(sock, count: int, buffer: bytearray,
                 timeout: float = 30.0) -> bytes:
    """`count` bytes, taking from the leftover buffer first."""
    sock.settimeout(timeout)
    while len(buffer) < count:
        try:
            chunk = sock.recv(min(65536, count - len(buffer)))
        except socket.timeout:
            raise ProtocolError("The audio stopped arriving.")
        if not chunk:
            raise ProtocolError("The connection closed mid-audio.")
        buffer.extend(chunk)
    out = bytes(buffer[:count])
    buffer[:] = buffer[count:]
    return out


def read_frame(sock, buffer: bytearray, timeout: float = 30.0):
    """
    One chunk, or None at the end of the audio.

    None rather than empty bytes, so a caller can loop on `is None` and never
    has to wonder. An empty chunk cannot arrive - see `send_frame`.
    """
    size = FRAME.unpack(read_exactly(sock, FRAME.size, buffer, timeout))[0]
    if size == 0:
        return None
    if size > MAX_FRAME:
        raise ProtocolError(f"A chunk claiming {size} bytes is not real.")
    return read_exactly(sock, size, buffer, timeout)


## -- what the messages mean

#Every command the server answers. Named here so both ends agree and so a
#server can refuse an unknown one with the list rather than with silence.
COMMANDS = ("ping", "status", "voices", "say", "stream", "cancel")


def failure(reason: str, **extra) -> dict:
    return {"ok": False, "reason": str(reason), **extra}


def success(**fields) -> dict:
    return {"ok": True, **fields}
