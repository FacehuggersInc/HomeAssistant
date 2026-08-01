"""
Running an action, and reading something out of what came back.

Two jobs that belong together because the second only makes sense on the first
one's answer. Neither touches Qt: what a call returned and what a path picks
out of it are the parts worth testing, and a test that needs a screen to ask
"what does `items.0.name` resolve to" is a test nobody writes.

There is no dry run. An endpoint that posts, posts; a function that turns the
lights off turns them off. Everything here runs the real thing and says so -
the dialog above warns, and the tile is saved before anything is pressed so a
test that restarts the panel is recoverable rather than lost work.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.main import Client


#What came back, in the shapes a tile cares about.
GOT_NOTHING = "nothing"       # None, or an empty body
GOT_VALUE = "value"           # a string, number or boolean
GOT_DATA = "data"             # a dict or list to read a path out of
GOT_PAGE = "page"             # HTML, which the tile opens rather than reads
GOT_ERROR = "error"           # it raised, or answered with a failing status

#How much of a body is worth keeping to show. A page is measured in tens of
#kilobytes and none of it is read here.
PREVIEW_LIMIT = 2000


@dataclass
class Result:
    """What one run answered with."""

    kind: str = GOT_NOTHING
    #The body, normalised: parsed JSON where it was JSON, otherwise the text.
    value: Any = None
    status: int = 200
    error: str = ""
    #What it looked like, short enough to put on screen.
    preview: str = ""
    #Whether the call itself completed, however unhelpful the answer.
    ran: bool = False

    @property
    def ok(self) -> bool:
        return self.ran and self.kind != GOT_ERROR

    def describe(self) -> str:
        """One line saying what happened, for the dialog to show."""
        if not self.ran:
            return self.error or "It did not run."
        if self.kind == GOT_ERROR:
            return self.error or f"Answered {self.status}."
        if self.kind == GOT_PAGE:
            return f"Answered with a page ({self.status})."
        if self.kind == GOT_NOTHING:
            return f"Ran, and answered with nothing ({self.status})."
        if self.kind == GOT_DATA:
            size = len(self.value) if hasattr(self.value, "__len__") else 0
            what = "entries" if isinstance(self.value, list) else "fields"
            return f"Answered with {size} {what} ({self.status})."
        return f"Answered with a value ({self.status})."


def _looks_like_page(text: str) -> bool:
    start = text.lstrip()[:200].lower()
    return start.startswith("<!doctype html") or start.startswith("<html")


def _shorten(value: Any) -> str:
    try:
        if isinstance(value, (dict, list)):
            text = json.dumps(value, indent=1, default=str)
        else:
            text = str(value)
    except Exception:
        text = str(value)
    if len(text) > PREVIEW_LIMIT:
        return text[:PREVIEW_LIMIT] + "\n\u2026"
    return text


def classify(body: Any, status: int = 200) -> Result:
    """
    What a call's answer is, in the terms a tile can act on.

    The status decides first. A 500 with a helpful JSON body is still a
    failure, and a tile showing the value out of it would be reporting an
    error as a reading.
    """
    if status >= 400:
        return Result(kind=GOT_ERROR, value=body, status=status, ran=True,
                      error=f"Answered {status}.", preview=_shorten(body))

    if body is None:
        return Result(kind=GOT_NOTHING, status=status, ran=True,
                      preview="(nothing)")

    if isinstance(body, (dict, list, tuple)):
        value = list(body) if isinstance(body, tuple) else body
        return Result(kind=GOT_DATA, value=value, status=status, ran=True,
                      preview=_shorten(value))

    if isinstance(body, (bool, int, float)):
        return Result(kind=GOT_VALUE, value=body, status=status, ran=True,
                      preview=_shorten(body))

    text = str(body)
    if _looks_like_page(text):
        return Result(kind=GOT_PAGE, value=text, status=status, ran=True,
                      preview=text[:PREVIEW_LIMIT])

    # A body that is JSON in a string is data, whatever it was sent as -
    # several endpoints answer with a dumped string rather than a dict.
    stripped = text.strip()
    if stripped[:1] in ("{", "["):
        try:
            parsed = json.loads(stripped)
            return Result(kind=GOT_DATA, value=parsed, status=status, ran=True,
                          preview=_shorten(parsed))
        except ValueError:
            pass

    if not stripped:
        return Result(kind=GOT_NOTHING, status=status, ran=True,
                      preview="(empty)")
    return Result(kind=GOT_VALUE, value=text, status=status, ran=True,
                  preview=_shorten(text))


def run(client: "Client", action: dict) -> Result:
    """
    Run one action for real, and say what came back.

    Anything it raises is caught and reported rather than allowed out: this is
    called from a dialog somebody is standing in front of, and a traceback
    that closes the panel loses the work they were doing.
    """
    kind = str(action.get("kind") or "")
    name = str(action.get("name") or "")
    values = dict(action.get("values") or {})

    if not name:
        return Result(error="Nothing chosen to run.")

    try:
        if kind == "core":
            return _run_core(client, name, values)
        if kind == "endpoint":
            return _run_endpoint(client, name, values)
        return _run_public(client, name, values)
    except TypeError as e:
        # Nearly always the arguments rather than the function: a name it does
        # not take, or a required one left out.
        return Result(ran=True, kind=GOT_ERROR, status=500,
                      error=f"The arguments did not fit: {e}")
    except Exception as e:
        return Result(ran=True, kind=GOT_ERROR, status=500,
                      error=f"{type(e).__name__}: {e}")


def _run_core(client: "Client", path: str, values: dict) -> Result:
    """
    Call one of the panel's own routes.

    Through Flask's test client rather than over the network. These are
    ordinary views closed over the client, so a real request would go out to
    127.0.0.1 and come back needing a device token that this panel does not
    hold on its own behalf - and it would do it on the UI thread. The test
    client runs the same view function with none of that.
    """
    app = getattr(client, "backend", None)
    if app is None:
        return Result(error="The panel's own routes are not up yet.")

    query = {k: ("true" if v is True else "false" if v is False else str(v))
             for k, v in (values or {}).items() if v not in (None, "")}

    # The panel's own token. Not a bypass and not a borrowed one: these
    # routes are meant to be authenticated, and borrowing an approved
    # device's would mark that person as active, make /say announce their
    # name as the sender, and break the tile when they were revoked.
    try:
        query["token"] = client.USERS.panel_token()
    except Exception as e:
        return Result(error=f"The panel has no identity to call itself "
                            f"with: {e}")

    try:
        with app.test_client() as caller:
            answered = caller.get(path, query_string=query)
    except Exception as e:
        return Result(ran=True, kind=GOT_ERROR, status=500,
                      error=f"{type(e).__name__}: {e}")

    status = int(getattr(answered, "status_code", 200))
    body = None
    try:
        body = answered.get_json(silent=True)
    except Exception:
        body = None
    if body is None:
        try:
            body = answered.get_data(as_text=True)
        except Exception:
            body = ""
    return classify(body, status)


def _run_endpoint(client: "Client", name: str, values: dict) -> Result:
    registry = getattr(client, "API", None)
    if registry is None:
        return Result(error="There is no API registry to ask.")

    entry = registry.get_endpoint(name)
    endpoint = entry[1] if isinstance(entry, tuple) else entry
    if endpoint is None:
        return Result(error=f"'{name}' is no longer registered.")

    # Through the endpoint's own call(), which normalises the three shapes a
    # callback may return - body, (body, status), (body, status, headers) -
    # so this does not have to guess which it got.
    answered = endpoint.call(**values)
    if isinstance(answered, tuple) and len(answered) >= 2:
        return classify(answered[0], int(answered[1] or 200))
    return classify(answered)


def _run_public(client: "Client", name: str, values: dict) -> Result:
    surface, _, entry_name = str(name).partition(".")
    registry = getattr(client, "public", None)
    if registry is None or not entry_name:
        return Result(error=f"'{name}' cannot be reached.")

    try:
        entries = getattr(registry, surface)
        callback = entries[entry_name]
    except Exception:
        return Result(error=f"'{name}' is no longer registered.")
    if not callable(callback):
        return Result(error=f"'{name}' is not something that can be run.")

    return classify(callback(**values))


## -- reading a value out of what came back


def follow(data: Any, path: str) -> tuple:
    """
    Walk a dotted path into whatever came back.

    `weather.today.high`, `items.0.name`. A number is an index and anything
    else is a key, so the same syntax reaches into both without asking which
    is which.

    Returns `(found, value)` rather than raising or answering None: a path
    that legitimately reaches a null is a different thing from one that
    reached nothing, and a tile showing "off" needs to tell them apart.
    """
    if not path or not str(path).strip():
        return True, data

    current = data
    for step in str(path).strip().split("."):
        step = step.strip()
        if not step:
            continue
        if isinstance(current, dict):
            if step not in current:
                return False, None
            current = current[step]
            continue
        if isinstance(current, (list, tuple)):
            try:
                index = int(step)
            except ValueError:
                return False, None
            if not -len(current) <= index < len(current):
                return False, None
            current = current[index]
            continue
        return False, None
    return True, current


def paths_in(data: Any, prefix: str = "", depth: int = 0,
             limit: int = 60) -> list:
    """
    Every path worth offering, so nobody has to guess at the shape.

    Breadth first and capped: a weather response is a hundred fields deep in
    places, and a list of all of them is not a list somebody reads.
    """
    if depth > 3 or len(prefix.split(".")) > 4:
        return []

    found = []
    if isinstance(data, dict):
        for key, value in data.items():
            here = f"{prefix}.{key}" if prefix else str(key)
            found.append(here)
            found.extend(paths_in(value, here, depth + 1, limit))
            if len(found) >= limit:
                break
    elif isinstance(data, (list, tuple)):
        # The first entry only. Every entry of a list has the same shape, so
        # offering `items.0.name` through `items.99.name` says the same thing
        # a hundred times.
        if data:
            here = f"{prefix}.0" if prefix else "0"
            found.append(here)
            found.extend(paths_in(data[0], here, depth + 1, limit))
    return found[:limit]


def as_state(value: Any) -> bool:
    """
    Whether a value reads as on.

    A tile's state is a light, not a report: something is on or it is not.
    Strings that say so are honoured, because an endpoint answering "off" or
    "false" means it, and a non-empty string is otherwise true.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, (list, tuple, dict, set)):
        # Emptiness, not the text of it. `str([])` is "[]", which is not one
        # of the words below and so read as on - an endpoint answering with
        # no results would have lit the tile.
        return bool(value)
    text = str(value).strip().lower()
    if text in ("", "0", "false", "no", "off", "none", "null", "closed"):
        return False
    return True
