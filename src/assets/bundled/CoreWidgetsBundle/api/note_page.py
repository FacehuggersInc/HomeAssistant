"""
The form behind /public/note_add and /public/list_add.

One page for both, because they are the same act with a different shape: some
text, a colour, and a button that puts it on the wall.
"""

from __future__ import annotations

from src.webui import escape, back_button, chrome_css

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{heading}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<style>
{chrome}
 :root{{color-scheme:dark}}
 h1{{font-size:24px;font-weight:600;letter-spacing:-.02em;margin:0 0 6px}}
 .sub{{color:var(--muted);font-size:14px;margin:0 0 26px}}
 label{{display:block;font-size:12px;font-weight:600;text-transform:uppercase;
        letter-spacing:.08em;color:var(--muted);margin:18px 0 9px}}
 label:first-of-type{{margin-top:0}}
 textarea{{min-height:150px;resize:vertical;line-height:1.55}}

 /* The colours, as colours. A dropdown of hex codes is a thing to decode. */
 .swatches{{display:flex;flex-wrap:wrap;gap:10px}}
 .swatches input{{display:none}}
 .swatches label{{width:46px;height:46px;border-radius:50%;margin:0;
   cursor:pointer;border:3px solid transparent;box-sizing:border-box}}
 .swatches input:checked + label{{border-color:var(--text)}}

 .go{{margin:26px 0 0}}
 .go button{{min-height:54px;padding:0 26px;border-radius:12px;font-size:16px;
   font-weight:600;font-family:inherit;cursor:pointer;border:none;
   background:linear-gradient(135deg,var(--accent),var(--accent2));
   color:#0d1a12}}
 .said{{margin:0 0 22px;padding:13px 16px;border-radius:12px;
        border:1px solid var(--accent);background:var(--card);font-size:14px}}
 .said.bad{{border-color:var(--bad);color:#ffb3b3}}
 .hint{{color:var(--muted);font-size:12.5px;line-height:1.6;margin:20px 0 0}}
</style>
</head>
<body>
<p>{back}</p>
{said}
<h1>{heading}</h1>
<p class="sub">{blurb}</p>

<form method="post" action="{action}?token={token}">
{fields}

<label>Colour</label>
<div class="swatches">{swatches}</div>

<div class="go"><button type="submit">{submit}</button></div>
</form>
<p class="hint">{hint}</p>
</body>
</html>
"""


def render_page(token: str, colours: list, message: str = "", bad: bool = False,
                kind: str = "note", lists: list = None) -> str:
    """One page, in whichever of its two shapes was asked for."""
    lists = lists or []

    said = ""
    if message:
        said = (f'<p class="said{" bad" if bad else ""}">'
                f'{escape(message)}</p>')

    swatches = "".join(
        f'<input type="radio" name="colour" id="c{index}" '
        f'value="{escape(colour)}"{" checked" if index == 0 else ""}>'
        f'<label for="c{index}" style="background:{escape(colour)}"></label>'
        for index, colour in enumerate(colours))

    if kind == "list":
        # Choosing an existing list turns this from "make one" into "add to
        # it" - the same form, because five days apart they are the same act.
        options = "".join(
            f'<option value="{escape(key)}">{escape(title)}</option>'
            for key, title in lists)
        chooser = ""
        if options:
            chooser = (
                '<label for="target">Add to a list that is already there'
                '</label>'
                '<select name="target" id="target">'
                '<option value="">Make a new one</option>'
                f'{options}</select>')

        fields = (
            f'{chooser}'
            '<label for="title">Name</label>'
            '<input type="text" name="title" id="title" '
            'placeholder="Shopping" autocomplete="off">'
            '<label for="text">Lines</label>'
            '<textarea name="text" id="text" '
            'placeholder="Milk&#10;Bread&#10;Eggs"></textarea>')
        return PAGE.format(
            heading="Put a list on the panel", chrome=chrome_css(),
            back=back_button(token), said=said, token=escape(token),
            action="/public/list_add", fields=fields, swatches=swatches,
            submit="Put it up",
            blurb="One item per line. It appears on the home page.",
            hint="Ticking items off, renaming and removing all happen on the "
                 "panel itself.")

    fields = ('<label for="text">Note</label>'
              '<textarea name="text" id="text" '
              'placeholder="Back at six"></textarea>')
    return PAGE.format(
        heading="Put a note on the panel", chrome=chrome_css(),
        back=back_button(token), said=said, token=escape(token),
        action="/public/note_add", fields=fields, swatches=swatches,
        submit="Put it up",
        blurb="It appears on the home page, where it can be moved and resized.",
        hint="Hold a note on the panel to move, resize or recolour it.")
