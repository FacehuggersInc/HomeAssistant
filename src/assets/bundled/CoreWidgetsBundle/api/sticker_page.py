"""
The sticker library over the API.

One page a phone can do the whole job from: upload something, or pick what is
already there, choose where it lands and whether it stays. Served rather than
shipped as a file, because the device token has to be in the form for the
POST to authenticate and it is not known until runtime.
"""

from __future__ import annotations

import html


def _escape(text) -> str:
    return html.escape(str(text or ""), quote=True)


QUADRANTS = [
    ("top-left", "Top left"), ("top", "Top"), ("top-right", "Top right"),
    ("left", "Left"), ("center", "Middle"), ("right", "Right"),
    ("bottom-left", "Bottom left"), ("bottom", "Bottom"),
    ("bottom-right", "Bottom right"),
]


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stickers</title>
<style>
 :root{--bg:#151517;--card:#1c1c1f;--line:#2c2c31;--text:#e6e6e8;
       --muted:#9a9aa2;--accent:#2ff08e;--bad:#e08a8a}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--text);
      font:16px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif;padding:18px}
 h1{font-size:22px;margin:0 0 4px}
 h2{font-size:15px;margin:22px 0 8px;color:var(--muted);font-weight:600}
 p.sub{color:var(--muted);margin:0 0 18px;font-size:14px}
 section{background:var(--card);border:1px solid var(--line);
      border-radius:14px;padding:16px;margin-bottom:16px}
 label{display:block;font-size:13px;color:var(--muted);margin:12px 0 4px}
 input,select{width:100%;padding:13px;border-radius:9px;font-size:16px;
      background:#111114;color:var(--text);border:1px solid var(--line)}
 button{width:100%;margin-top:16px;padding:15px;border:0;border-radius:10px;
      background:var(--accent);color:#10281c;font-size:17px;font-weight:600}
 button.ghost{background:#26262b;color:var(--text)}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));
      gap:10px;margin-top:6px}
 .tile{position:relative;border:2px solid transparent;border-radius:12px;
      overflow:hidden;background:#111114;cursor:pointer;aspect-ratio:1}
 .tile img{width:100%;height:100%;object-fit:contain;display:block}
 .tile .nm{position:absolute;left:0;right:0;bottom:0;padding:4px 6px;
      font-size:11px;background:rgba(0,0,0,.66);white-space:nowrap;
      overflow:hidden;text-overflow:ellipsis}
 .tile .vid{position:absolute;inset:0;display:flex;align-items:center;
      justify-content:center;color:var(--muted);font-size:12px;text-align:center;
      padding:6px}
 .tile.sel{border-color:var(--accent)}
 .quads{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:6px}
 .quads button{margin:0;padding:14px 6px;font-size:13px;background:#26262b;
      color:var(--text);border:1px solid var(--line)}
 .quads button.on{background:var(--accent);color:#10281c;border-color:var(--accent)}
 .row{display:flex;gap:10px}.row>div{flex:1}
 .note{background:rgba(47,240,142,.14);border:1px solid rgba(47,240,142,.5);
     border-radius:10px;padding:12px;margin-bottom:14px}
 .warn{background:rgba(224,138,138,.14);border:1px solid rgba(224,138,138,.5);
     border-radius:10px;padding:12px;margin-bottom:14px;color:var(--bad)}
 .empty{color:var(--muted);font-size:14px;padding:10px 0}
 a.back{display:inline-flex;align-items:center;gap:8px;
      text-decoration:none;background:var(--card);border:1px solid var(--line);
      color:var(--text);border-radius:10px;padding:11px 16px;font-size:15px;
      font-weight:600;margin-bottom:14px}
 a.back:active{background:#26262b}
 a.back svg{width:16px;height:16px;fill:none;stroke:currentColor;
      stroke-width:2.4;stroke-linecap:round;stroke-linejoin:round}
 .hint{color:var(--muted);font-size:12px;margin-top:6px}
 .check{display:flex;align-items:center;gap:10px;margin-top:14px;
      color:var(--text);font-size:15px}
 .check input{width:22px;height:22px;flex:0 0 22px;accent-color:var(--accent)}
 button.danger{background:#3a1f1f;color:var(--bad);
      border:1px solid rgba(224,138,138,.45);margin-top:10px}
 button:disabled{opacity:.45}
</style></head><body>
<a class="back" href="/?token=__TOKEN__"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg><span>All pages</span></a>
<h1>Stickers</h1>
<p class="sub">Put something on the panel.</p>
__MESSAGE__

<section>
  <h2>Upload a new one</h2>
  <form method="post" enctype="multipart/form-data"
        action="/public/sticker_add?token=__TOKEN__">
    <input type="file" name="file" accept="image/*,video/mp4,video/webm" required>
    <button type="submit">Upload</button>
  </form>
</section>

<section>
  <h2>__COUNT__ in your library</h2>
  <form method="post" action="/public/sticker_add?token=__TOKEN__" id="place">
    <input type="hidden" name="sticker" id="sticker" value="__STICKER__">
    <input type="hidden" name="quadrant" id="quadrant" value="__QUADRANT__">
    <div class="grid" id="grid">__TILES__</div>

    <label>Where should it go?</label>
    <div class="quads" id="quads">__QUADS__</div>

    <label for="mode">How long should it stay?</label>
    <select id="mode" name="mode">__MODE__</select>

    <div id="timeoutRow" style="display:__TIMEOUT_ROW__">
      <label for="timeout">Gone after (seconds)</label>
      <input id="timeout" name="timeout" type="number" min="1" max="86400"
             value="__TIMEOUT__">
      <div class="hint">1 second is allowed - useful for testing placement.</div>
      <label class="check">
        <input type="checkbox" name="delete_after" value="1" __DELETE_AFTER__>
        <span>Delete the file too, when it goes</span>
      </label>
    </div>

    <label for="scale">How big?</label>
    <select id="scale" name="scale">__SCALE__</select>
    <div id="sizeRow" style="display:__SIZE_ROW__">
      <label for="size">Longest side (pixels)</label>
      <input id="size" name="size" type="number" min="32" max="1200"
             value="__SIZE__"__SIZE_OFF__>
    </div>

    <button type="submit" id="go" __GO_STATE__>__GO_LABEL__</button>
    <button type="submit" name="remove" id="rm" class="danger"
            value="__STICKER__" formnovalidate __GO_STATE__>__RM_LABEL__</button>
  </form>
</section>

<script>
 var chosen = document.getElementById('sticker').value || null;
 if (chosen) {
   var pre = document.querySelector('#grid .tile[data-name="' +
             chosen.replace(/"/g, '\\"') + '"]');
   if (pre) { pre.classList.add('sel'); }
 }
 document.querySelectorAll('#grid .tile').forEach(function(t){
   t.addEventListener('click', function(){
     document.querySelectorAll('#grid .tile').forEach(function(o){
       o.classList.remove('sel');
     });
     t.classList.add('sel');
     chosen = t.dataset.name;
     document.getElementById('sticker').value = chosen;
     var go = document.getElementById('go');
     go.disabled = false;
     go.textContent = 'Place "' + t.dataset.label + '"';
     var rm = document.getElementById('rm');
     rm.disabled = false;
     rm.value = chosen;
     rm.textContent = 'Delete "' + t.dataset.label + '" from the library';
   });
 });
 document.querySelectorAll('#quads button').forEach(function(b){
   b.addEventListener('click', function(e){
     e.preventDefault();
     document.querySelectorAll('#quads button').forEach(function(o){
       o.classList.remove('on');
     });
     b.classList.add('on');
     document.getElementById('quadrant').value = b.dataset.q;
   });
 });
 document.getElementById('mode').addEventListener('change', function(){
   document.getElementById('timeoutRow').style.display =
     this.value === 'temporary' ? 'block' : 'none';
 });
 document.getElementById('scale').addEventListener('change', function(){
   var custom = this.value === 'custom';
   document.getElementById('sizeRow').style.display = custom ? 'block' : 'none';
   // Disabled as well as hidden. display:none hides a field; it does not stop
   // the browser submitting it, and a stray pixel value arriving alongside a
   // named size is exactly what made the names look identical.
   document.getElementById('size').disabled = !custom;
 });
 // Asked first. This removes a file, and the grid is where a mis-tap lands.
 document.getElementById('rm').addEventListener('click', function(e){
   var label = document.querySelector('#grid .tile.sel');
   var name = label ? label.dataset.label : 'this sticker';
   if (!confirm('Delete "' + name + '" from the library? This cannot be undone.')) {
     e.preventDefault();
   }
 });
</script>
</body></html>"""


MODES = [("permanent", "Permanently, until I remove it"),
         ("temporary", "Temporarily")]

#Named, not numbered.
#
#Each name is a share of the panel's width rather than a multiplier on a fixed
#number: "huge" as 2x180px was 360px, which on a 2560px panel is a seventh of
#the width - the same size "large" looks, and nothing like the word.
#
#Names rather than the fractions themselves because the two ranges overlap. The
#old values were 0.5 to 2 and a share is 0.02 to 0.95, so "0.5" could mean
#either the old "small" or half the screen. A word cannot be mistaken for
#either, and a link still passing a number keeps its old meaning exactly.
SCALES = [("small", "Small"), ("normal", "Normal"), ("large", "Large"),
          ("huge", "Huge"), ("enormous", "Enormous"),
          ("custom", "Exact size\u2026")]


def _options(choices, chosen: str) -> str:
    out = []
    for value, label in choices:
        selected = " selected" if str(value) == str(chosen) else ""
        out.append(f'<option value="{_escape(value)}"{selected}>'
                   f'{_escape(label)}</option>')
    return "".join(out)


def render_page(token: str, stickers: list, message: str = "",
                bad: bool = False, form: dict = None) -> str:
    """
    The page, rendered from whatever was last submitted.

    `form` carries the previous answers back in. Re-rendering from defaults
    meant every placement reset the quadrant, the size and the duration - so
    putting three stickers in the same corner meant setting the same three
    controls three times.
    """
    form = form or {}
    quadrant = str(form.get("quadrant") or "center")
    mode = str(form.get("mode") or "permanent")
    scale = str(form.get("scale") or "normal")
    timeout = str(form.get("timeout") or "300")
    size = str(form.get("size") or "180")
    chosen = str(form.get("sticker") or "")

    # A sticker that has since been deleted must not stay selected.
    if chosen and not any(s.name == chosen for s in stickers):
        chosen = ""

    tiles = []
    for sticker in stickers:
        src = f"/asset/stickers/{_escape(sticker.name)}?token={_escape(token)}"
        if sticker.kind == "video":
            # No poster frame without a decoder, so it is named rather than
            # shown - a tile drawn as nothing reads as broken.
            inner = (f'<div class="vid">{_escape(sticker.label)}<br>(video)</div>')
        else:
            inner = f'<img src="{src}" alt="{_escape(sticker.label)}" loading="lazy">'
        tiles.append(
            f'<div class="tile" data-name="{_escape(sticker.name)}" '
            f'data-label="{_escape(sticker.label)}">{inner}'
            f'<div class="nm">{_escape(sticker.label)}</div></div>')

    quads = "".join(
        f'<button data-q="{key}" class="{"on" if key == quadrant else ""}">'
        f'{_escape(label)}</button>'
        for key, label in QUADRANTS)

    if message:
        block = (f'<div class="{"warn" if bad else "note"}">'
                 f'{_escape(message)}</div>')
    else:
        block = ""

    body = "".join(tiles) or (
        '<div class="empty">Nothing here yet. Upload something above.</div>')

    label = next((s.label for s in stickers if s.name == chosen), "")

    return (PAGE
            .replace("__TOKEN__", _escape(token))
            .replace("__MESSAGE__", block)
            .replace("__TILES__", body)
            .replace("__QUADS__", quads)
            .replace("__COUNT__", str(len(stickers)))
            .replace("__STICKER__", _escape(chosen))
            .replace("__QUADRANT__", _escape(quadrant))
            .replace("__MODE__", _options(MODES, mode))
            .replace("__SCALE__", _options(SCALES, scale))
            .replace("__TIMEOUT__", _escape(timeout))
            .replace("__SIZE__", _escape(size))
            .replace("__TIMEOUT_ROW__", "block" if mode == "temporary" else "none")
            .replace("__SIZE_ROW__", "block" if scale == "custom" else "none")
            .replace("__SIZE_OFF__", "" if scale == "custom" else " disabled")
            .replace("__GO_STATE__", "" if chosen else "disabled")
            .replace("__GO_LABEL__",
                     f'Place "{_escape(label)}"' if chosen
                     else "Choose a sticker first")
            .replace("__RM_LABEL__",
                     f'Delete "{_escape(label)}" from the library' if chosen
                     else "Delete")
            .replace("__DELETE_AFTER__",
                     "checked" if str(form.get("delete_after") or "").lower()
                     in ("1", "true", "on", "yes") else ""))
