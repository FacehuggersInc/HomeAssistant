"""
Adding and removing feeds from a phone.

The folder was the only way in - drop a JSON file next to the application and
restart. That is fine for the person who set the panel up and impossible for
anybody else in the house.
"""

from __future__ import annotations

from src.webui import escape, page


CSS = """
 ul{list-style:none;padding:0;margin:0}
 li{display:flex;align-items:center;gap:12px;padding:12px 0;
    border-bottom:1px solid var(--line)}
 li:last-child{border-bottom:0}
 .meta{flex:1;min-width:0}
 .nm{font-size:16px;font-weight:600;display:block}
 .url{color:var(--muted);font-size:12px;display:block;word-break:break-all;
      font-family:ui-monospace,monospace}
 li form{flex:0 0 auto;margin:0}
 .go{margin-top:18px}
 .go button{width:100%}
"""


def render_page(token: str, feeds: list, message: str = "",
                bad: bool = False, form: dict = None) -> str:
    """`feeds` is a list of (name, url)."""
    form = form or {}
    action = f"/public/rss_feeds?token={escape(token)}"

    rows = []
    for name, url in feeds:
        rows.append(
            f'<li><span class="meta">'
            f'<span class="nm">{escape(name)}</span>'
            f'<span class="url">{escape(url)}</span></span>'
            f'<form method="post" action="{action}" '
            f'onsubmit="return confirm(\'Remove {escape(name)}?\')">'
            f'<input type="hidden" name="remove" value="{escape(name)}">'
            f'<button class="danger" type="submit">Remove</button>'
            f'</form></li>')

    listed = "".join(rows) or '<li class="empty">No feeds yet.</li>'

    body = f"""
<section>
  <h2>Add a feed</h2>
  <form method="post" action="{action}">
    <label for="name">Name</label>
    <input id="name" name="name" placeholder="Steam deals" required
           value="{escape(form.get('name') or '')}">
    <p class="hint">Only used for the filename. The feed's own title is what
      appears on the panel.</p>

    <label for="url">Address</label>
    <input id="url" name="url" type="url" required
           placeholder="https://example.com/feed.xml"
           value="{escape(form.get('url') or '')}">
    <p class="hint">Any RSS or Atom feed. A subreddit works by adding
      <code>/.rss</code> to its address.</p>

    <div class="go"><button type="submit">Add it</button></div>
  </form>
</section>

<section>
  <h2>{len(feeds)} subscribed</h2>
  <ul>{listed}</ul>
</section>
"""

    return page(
        title="Feeds",
        heading="Feeds",
        blurb="Articles the panel shows while it is idle.",
        token=token, message=message, bad=bad,
        css=CSS, body=body,
    )
