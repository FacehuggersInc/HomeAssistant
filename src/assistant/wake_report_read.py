"""
Reading a wake report back.

The file is written for somebody to read at a terminal. This turns the last
session in it into a shape a page can draw, so the answer is legible on a
phone standing next to the panel rather than only after copying a log back to
a desk.

**The last session only.** The file accumulates across restarts, and a report
that averaged four sessions together would hide exactly the thing somebody is
looking for - that it got worse after a change. A session starts at the
banner the child writes when it opens the microphone.
"""

import os
import re

#Enough of the tail to hold several sessions without reading a file that has
#been growing for a month.
TAIL_BYTES = 400_000

_LINE = re.compile(r"^\[(\d\d:\d\d:\d\d)\]\s(.*)$")
_HEADER = re.compile(r"^\[[\d:]+\]\sWake report\s-")
_WOKE = re.compile(r"^WOKE\s+([\d.]+)\s+\(bar\s+([\d.]+)\)")
_NEAR = re.compile(r"^NEAR\s+([\d.]+)\s+\(bar\s+([\d.]+),\s+short by\s+([\d.]+)\)")
_SAID = re.compile(r"^\s+(WOKE ON|NEAR SAID)\s+(.*)$")
#Fields are read by column rather than by splitting on whitespace: the labels
#are two words as often as one, and "near misses 0" splits into a field called
#"near". The width comes from the writer so the two cannot drift apart.
from src.assistant.wake_report import WakeReport as _Writer

_FIELD_AT = 2 + _Writer.FIELD_WIDTH + 1


def _tail(path: str) -> list:
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            if size > TAIL_BYTES:
                handle.seek(size - TAIL_BYTES)
                handle.readline()          # drop the half line seeking landed in
            return handle.read().splitlines()
    except OSError:
        return []


def read(path: str, events: int = 40) -> dict:
    """
    The last session in a report.

    Answers `{"found": False}` when there is nothing yet, rather than an empty
    session - "no report" and "a report with nothing in it" mean different
    things, and only one of them is worth investigating.
    """
    lines = _tail(path)
    if not lines:
        return {"found": False}

    # Back to the last header. The rule of '=' is written twice, opening and
    # closing the device block, so searching for that lands AFTER the device
    # lines - which are the ones worth having. The header is written once.
    #
    # A session that began before the tail does is reported from wherever the
    # tail starts and marked partial, which is honest about what is on screen
    # rather than pretending it is the whole of it.
    start = 0
    partial = True
    for index in range(len(lines) - 1, -1, -1):
        if _HEADER.search(lines[index]):
            start = index
            partial = False
            break

    session = {"found": True, "partial": partial, "device": {},
               "settings": {}, "events": [], "summary": {}, "note": "",
               "started": "", "wake_word": ""}
    where = ""
    pending = None

    for raw in lines[start:]:
        match = _LINE.match(raw)
        if not match:
            continue
        when, body = match.group(1), match.group(2)

        if body.startswith("=" * 10):
            # The closing rule. The opening one sits above the header, which
            # is where reading starts, so this only ever ends the block.
            where = ""
            continue
        if body.startswith("Wake report"):
            session["started"] = when
            session["wake_word"] = body.split("-", 1)[-1].strip().strip("'")
            where = "device"
            continue
        if body == "Settings":
            where = "settings"
            continue
        if body.startswith("-" * 10):
            where = "summary" if where != "summary" else ""
            continue
        if body.startswith("Final after") or body.startswith("So far after"):
            where = "summary"
            session["summary"]["for"] = body.split("after", 1)[-1].strip()
            session["summary"]["final"] = body.startswith("Final")
            continue

        woke = _WOKE.match(body)
        near = _NEAR.match(body)
        said = _SAID.match(body)

        if woke:
            pending = {"kind": "woke", "at": when,
                       "score": float(woke.group(1)),
                       "bar": float(woke.group(2)), "said": ""}
            session["events"].append(pending)
            continue
        if near:
            pending = {"kind": "near", "at": when,
                       "score": float(near.group(1)),
                       "bar": float(near.group(2)),
                       "short": float(near.group(3)), "said": ""}
            if "not transcribed" in body:
                pending["said"] = "(not transcribed)"
            session["events"].append(pending)
            continue
        if said and pending is not None:
            text = said.group(2).strip()
            if text.startswith("'") or text.startswith('"'):
                text = text[1:-1] if len(text) > 1 else text
            pending["said"] = text
            pending = None
            continue

        if (body.startswith("  ") and len(body) > _FIELD_AT
                and where in ("device", "settings", "summary")):
            label = body[2:_FIELD_AT - 1].strip()
            value = body[_FIELD_AT:].strip()
            if label:
                session[where][label] = value
                if label == "NOTE":
                    session["note"] = value
                continue


    session["events"] = session["events"][-int(events or 40):]
    session["events"].reverse()
    return session


def verdict(session: dict) -> str:
    """
    One sentence saying what the numbers mean.

    The point of the whole exercise. Somebody standing at a panel with a
    phone wants to know whether to change a setting or move a microphone, and
    a table of numbers does not say which.
    """
    if not session.get("found"):
        return ("No report yet. It is written while the assistant runs, so "
                "give it a few minutes of the room being itself.")

    summary = session.get("summary") or {}
    events = session.get("events") or []
    fires = sum(1 for e in events if e["kind"] == "woke")
    nears = [e for e in events if e["kind"] == "near"]
    scores = summary.get("peak scores", "")

    if "none above" in str(scores) or (not fires and not nears):
        return ("Nothing has scored even faintly. That is a microphone or a "
                "channel problem rather than a sensitivity one - check the "
                "device line above before changing any setting.")

    if nears:
        closest = min(nears, key=lambda e: e.get("short", 9))
        if closest.get("short", 9) <= 0.1:
            return (f"Near misses are landing within {closest['short']:.2f} of "
                    f"the bar. Lowering the sensitivity a step is the cheapest "
                    f"thing to try - watch whether the wakes go up with it.")
        if not fires:
            return ("Peaks are well short of the bar and nothing is waking. "
                    "The word is arriving buried, so the microphone and the "
                    "room noise below are where to look, not the threshold.")

    if fires and not nears:
        return ("Waking, with nothing falling short. If it still fires when "
                "nobody spoke, read what it woke on below - that is the "
                "transcript of the moment it happened.")

    return ("Both waking and falling short. Read the transcripts below: the "
            "wakes tell you what is setting it off and the near misses tell "
            "you what it heard instead.")
