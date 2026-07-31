"""
The form behind /public/note_add.

Some text, a colour, and a button that puts it on the wall. Lists moved to
list_page: they started as the same page with different fields and grew into
an editor with its own state, which is not the same act any more.
"""

from __future__ import annotations

from src.webui import escape, page, position_grid, POSITION_SCRIPT

CSS = """
 textarea{min-height:150px;resize:vertical;line-height:1.55}
 .swatches{display:flex;flex-wrap:wrap;gap:10px;margin-top:6px}
 .swatches input{display:none}
 .swatches label{width:44px;height:44px;border-radius:50%;margin:0;
   cursor:pointer;border:3px solid transparent;box-sizing:border-box}
 .swatches input:checked + label{border-color:var(--text)}
 .go{margin:22px 0 0}
 .go button{width:100%}
"""


def render_page(token: str, colours: list, message: str = "", bad: bool = False,
                quadrant: str = "top-right", **_ignored) -> str:
    """One note, and where it goes."""
    swatches = "".join(
        f'<input type="radio" name="colour" id="c{index}" '
        f'value="{escape(colour)}"{" checked" if index == 0 else ""}>'
        f'<label for="c{index}" style="background:{escape(colour)}"></label>'
        for index, colour in enumerate(colours))

    body = f"""
<form method="post" action="/public/note_add?token={escape(token)}">
<section>
  <label for="text">Note</label>
  <textarea name="text" id="text" placeholder="Back at six"></textarea>
</section>

<section>
  <label>Colour</label>
  <div class="swatches">{swatches}</div>
</section>

<section>
  <label>Where it goes</label>
  {position_grid(quadrant)}
</section>

<div class="go"><button type="submit">Put it up</button></div>
</form>
<p class="hint">Hold a note on the panel to move, resize or recolour it.</p>
"""

    return page(
        title="Sticky note",
        heading="Put a note on the panel",
        blurb="It appears on the home page, where it can be moved and resized.",
        token=token, message=message, bad=bad,
        css=CSS, body=body, script=POSITION_SCRIPT,
    )
