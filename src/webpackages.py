"""
The Packages page.

A list of things the panel can build for another machine, searchable and
downloadable. It sits under the plugins permission because a package is code
somebody will run somewhere, and it is built by plugin-supplied builders - but
it is its own place on the dashboard rather than a tab inside the plugin
manager. Nothing here installs a plugin, and a package is as likely to come
from the panel itself as from one.

Every card says what is inside before it is downloaded. "Trust me" is not a
reasonable thing to ask of an archive that is about to be executed on another
computer, and the contents are known - the registry has them.
"""

from __future__ import annotations

import html

from src.webui import core_assets, page



def _card(item: dict, token: str) -> str:
    contents = "".join(
        f'<li>{html.escape(str(name))}</li>' for name in item["contents"])
    inside = (f'<p class="label">Inside</p><ul class="inside">{contents}</ul>'
              if contents else "")
    owner = ("the panel" if item["owner"] == "client"
             else html.escape(item["owner"]))
    return f"""
<article class="package">
  <header>
    <h2>{html.escape(item['name'])}</h2>
    <span class="version">{html.escape(item['version'])}</span>
  </header>
  <p class="from">from {owner}</p>
  <p class="what">{html.escape(item['description'])}</p>
  {inside}
  <p class="row">
    <a class="btn primary"
       href="/packages/{html.escape(item['key'])}/download?token={html.escape(token)}">
      Download
    </a>
  </p>
</article>"""


def packages_page(items: list, token: str, search: str = "",
                  message: str = "", bad: bool = False) -> str:
    """Every package, or the ones a search matched."""
    if items:
        body = "".join(_card(item, token) for item in items)
    elif search:
        body = (f'<section class="empty">Nothing matches '
                f'{html.escape(search)!r}.</section>')
    else:
        body = ('<section class="empty">No packages. The panel offers one for '
                'the speech server; a plugin can add its own.</section>')

    search_box = f"""
<form class="search" method="get" action="/packages">
  <input type="hidden" name="token" value="{html.escape(token)}">
  <input type="search" name="q" value="{html.escape(search)}"
         placeholder="Search packages" autocomplete="off">
  <button class="btn" type="submit">Search</button>
</form>"""

    inline, head = core_assets().inline_or_link("packages.css")
    return page(
        title="Packages", heading="Packages",
        blurb="Built when you ask for them, with this panel's settings "
              "already filled in.",
        token=token, css=inline, head=head,
        message=message, bad=bad,
        body=search_box + body,
    )
