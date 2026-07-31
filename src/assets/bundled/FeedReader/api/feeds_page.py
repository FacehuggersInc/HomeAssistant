"""
Adding and removing feeds from a phone.

The folder was the only way in - drop a JSON file next to the application and
restart. That is fine for the person who set the panel up and impossible for
anybody else in the house.
"""

from __future__ import annotations

from src.webui import escape, back_button, chrome_css


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Feeds</title>
<style>
__CHROME__
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--text);
      font:16px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif;padding:18px}
 h1{font-size:22px;margin:0 0 4px}
 h2{font-size:15px;margin:22px 0 8px;color:var(--muted);font-weight:600}
 p.sub{color:var(--muted);margin:0 0 18px;font-size:14px}
 section{background:var(--card);border:1px solid var(--line);
      border-radius:14px;padding:16px;margin-bottom:16px}
 label{display:block;font-size:13px;color:var(--muted);margin:12px 0 4px}
 button{width:100%;margin-top:18px;padding:15px;border:0;border-radius:10px;
      background:var(--accent);color:#10281c;font-size:17px;font-weight:600}
 ul{list-style:none;padding:0;margin:0}
 li{display:flex;align-items:center;gap:12px;padding:12px 0;
    border-bottom:1px solid var(--line)}
 li:last-child{border-bottom:0}
 .meta{flex:1;min-width:0}
 .nm{font-size:16px;font-weight:600;display:block}
 .url{color:var(--muted);font-size:12px;display:block;word-break:break-all;
      font-family:ui-monospace,monospace}
 .rm{background:#3a1f1f;color:var(--bad);border:1px solid rgba(224,138,138,.45);
     border-radius:9px;padding:9px 14px;font-size:14px;width:auto;margin:0;
     flex:0 0 auto}
 .note{background:rgba(47,240,142,.14);border:1px solid rgba(47,240,142,.5);
     border-radius:10px;padding:12px;margin-bottom:14px}
 .warn{background:rgba(224,138,138,.14);border:1px solid rgba(224,138,138,.5);
     border-radius:10px;padding:12px;margin-bottom:14px;color:var(--bad)}
 .empty{color:var(--muted);font-size:14px}
 .hint{color:var(--muted);font-size:12px;margin-top:6px}
</style></head><body>
__BACK__
<h1>Feeds</h1>
<p class="sub">Articles the panel shows while it is idle.</p>
__MESSAGE__

<section>
  <h2>Add a feed</h2>
  <form method="post" action="/public/rss_feeds?token=__TOKEN__">
    <label for="name">Name</label>
    <input id="name" name="name" placeholder="Steam deals" required
           value="__NAME__">
    <div class="hint">Only used for the filename. The feed's own title is
      what appears on the panel.</div>

    <label for="url">Address</label>
    <input id="url" name="url" type="url" placeholder="https://example.com/feed.xml"
           required value="__URL__">
    <div class="hint">Any RSS or Atom feed. A subreddit works by adding
      <code>/.rss</code> to its address.</div>

    <button type="submit">Add it</button>
  </form>
</section>

<section>
  <h2>__COUNT__ subscribed</h2>
  <ul>__FEEDS__</ul>
</section>
</body></html>"""


def render_page(token: str, feeds: list, message: str = "",
                bad: bool = False, form: dict = None) -> str:
    """
    `feeds` is a list of (name, url).
    """
    form = form or {}

    rows = []
    for name, url in feeds:
        rows.append(
            f'<li><span class="meta">'
            f'<span class="nm">{escape(name)}</span>'
            f'<span class="url">{escape(url)}</span></span>'
            f'<form method="post" action="/public/rss_feeds?token={escape(token)}" '
            f'onsubmit="return confirm(\'Remove {escape(name)}?\')">'
            f'<input type="hidden" name="remove" value="{escape(name)}">'
            f'<button class="rm" type="submit">Remove</button>'
            f'</form></li>')

    listed = "".join(rows) or '<li class="empty">No feeds yet.</li>'

    if message:
        block = (f'<div class="{"warn" if bad else "note"}">'
                 f'{escape(message)}</div>')
    else:
        block = ""

    return (PAGE
            .replace("__CHROME__", chrome_css())
            .replace("__BACK__", back_button(token))
            .replace("__TOKEN__", escape(token))
            .replace("__MESSAGE__", block)
            .replace("__FEEDS__", listed)
            .replace("__COUNT__", str(len(feeds)))
            .replace("__NAME__", escape(form.get("name") or ""))
            .replace("__URL__", escape(form.get("url") or "")))
