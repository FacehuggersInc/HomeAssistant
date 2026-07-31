"""
A JSON answer, as a page.

Every endpoint here returns JSON, which is right for a script and wrong for
somebody who arrived by pressing a card on the dashboard - a browser shows them
a wall of braces and a Back button.

`answered()` decides which they wanted. A request that says it accepts HTML and
did not ask for JSON is a person; anything else is a script and gets the JSON
untouched.
"""

from __future__ import annotations

from src.webui import escape, page

CSS = """
 body{display:flex;flex-direction:column;justify-content:center;
      min-height:100vh;padding:40px 24px}
 .badge{display:inline-flex;align-items:center;gap:9px;align-self:flex-start;
   padding:7px 14px;border-radius:999px;font-size:12px;font-weight:600;
   letter-spacing:.04em;text-transform:uppercase;margin:0 0 18px}
 .badge.ok{background:rgba(47,240,142,.15);color:var(--accent)}
 .badge.bad{background:rgba(255,122,122,.15);color:var(--bad)}
 h1{font-size:26px;margin:0 0 22px;line-height:1.25}
 dl{margin:0;border:1px solid var(--line);border-radius:14px;
     background:var(--card);overflow:hidden}
 .pair{display:flex;gap:16px;padding:14px 17px;
        border-top:1px solid var(--line)}
 .pair:first-child{border-top:none}
 dt{flex:none;width:34%;max-width:190px;color:var(--muted);font-size:13px;
     text-transform:capitalize}
 dd{margin:0;font-size:14px;word-break:break-word;line-height:1.5}
"""


def wants_page(request) -> bool:
    """
    Whether this came from a person in a browser.

    Accepts HTML and did not ask for JSON. A `fetch()` from the dashboard sets
    neither, so the pages that read these answers themselves keep getting the
    data - only a link somebody followed gets a page.
    """
    accept = str(request.headers.get("Accept", ""))
    if "application/json" in accept:
        return False
    if str(request.args.get("format", "")).lower() == "json":
        return False
    return "text/html" in accept


def render(payload: dict, token: str = "", status: int = 200) -> str:
    """One JSON answer, laid out."""
    payload = payload if isinstance(payload, dict) else {"result": payload}

    ok = 200 <= int(status) < 400
    # The endpoints here answer with `request` and `reason`, so those become
    # the headline rather than another row saying what the badge already says.
    headline = str(payload.get("what")
                   or payload.get("reason")
                   or payload.get("request")
                   or ("Done" if ok else "That did not work"))

    rows = []
    for key, value in payload.items():
        if key in ("request", "what") or value in ("", None):
            continue
        label = str(key).replace("_", " ")
        rows.append(f'<div class="pair"><dt>{escape(label)}</dt>'
                    f'<dd>{escape(str(value))}</dd></div>')

    kind = "ok" if ok else "bad"
    status = escape(str(payload.get("request", "OK" if ok else "Failed")))
    pairs = "".join(rows) or '<div class="pair"><dd>Nothing to show.</dd></div>'

    body = (f'<span class="badge {kind}">{status}</span>'
            f'<h1>{escape(headline)}</h1>'
            f'<dl>{pairs}</dl>')

    return page(title=headline, body=body, token=token, css=CSS)
