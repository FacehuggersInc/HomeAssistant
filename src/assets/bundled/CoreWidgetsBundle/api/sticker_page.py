"""
Putting a sticker on the panel from a phone.

Upload, pick one, say where it goes and how long it stays. One page rather
than a folder to drop files into, because the folder was only reachable by
whoever set the panel up.
"""

from __future__ import annotations

from src.webui import escape, page, position_grid, POSITION_SCRIPT


CSS = """
/* A link, shaped like the button it sits beside. */
a.manage{display:block;text-align:center;text-decoration:none;
         line-height:46px;border-radius:11px}

 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));
      gap:10px;margin-top:6px}
 .tile{position:relative;border:2px solid var(--line);border-radius:12px;
      overflow:hidden;background:#111114;cursor:pointer;aspect-ratio:1}
 .tile img{width:100%;height:100%;object-fit:contain;display:block}
 .tile .nm{position:absolute;left:0;right:0;bottom:0;padding:4px 6px;
      font-size:11px;background:rgba(0,0,0,.66);white-space:nowrap;
      overflow:hidden;text-overflow:ellipsis}
 .tile .vid{position:absolute;inset:0;display:flex;align-items:center;
      justify-content:center;color:var(--muted);font-size:12px;
      text-align:center;padding:6px}
 .tile.sel{border-color:var(--accent)}

 /* The upload row. The label IS the button; the input behind it is hidden.
    A bare file input renders as the browser's own control - a small grey
    "Choose file" with system text beside it - which is the one element on the
    page that does not look like the rest of it. */
 .filebtn{display:inline-flex;align-items:center;min-height:50px;padding:0 22px;
      border-radius:11px;border:1px solid var(--line);background:var(--card);
      color:var(--text);font-size:15px;font-weight:600;cursor:pointer;margin:0}
 .filebtn:active{opacity:.75}
 .picked{color:var(--muted);font-size:14px;margin:0 6px}
 .clearform{display:inline}
 .check{display:flex;align-items:center;gap:10px;margin-top:14px;
      color:var(--text);font-size:15px}
 .check input{width:22px;height:22px;flex:0 0 22px;accent-color:var(--accent)}
 .go{display:flex;flex-direction:column;gap:10px;margin-top:18px}
"""


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


SCRIPT = """
var chosen = document.getElementById('sticker').value || null;
function mark(tile) {
  document.querySelectorAll('#grid .tile').forEach(function (o) {
    o.classList.remove('sel');
  });
  tile.classList.add('sel');
  chosen = tile.dataset.name;
  document.getElementById('sticker').value = chosen;
  var go = document.getElementById('go');
  go.disabled = false;
  go.textContent = 'Place "' + tile.dataset.label + '"';
}
if (chosen) {
  var pre = document.querySelector('#grid .tile[data-name="' +
            chosen.replace(/"/g, '\\\\"') + '"]');
  if (pre) { pre.classList.add('sel'); }
}
document.querySelectorAll('#grid .tile').forEach(function (t) {
  t.addEventListener('click', function () { mark(t); });
});
document.getElementById('mode').addEventListener('change', function () {
  document.getElementById('timeoutRow').style.display =
    this.value === 'temporary' ? 'block' : 'none';
});
document.getElementById('scale').addEventListener('change', function () {
  var custom = this.value === 'custom';
  document.getElementById('sizeRow').style.display = custom ? 'block' : 'none';
  /* Disabled as well as hidden. display:none hides a field; it does not stop
     the browser submitting it, and a stray pixel value arriving alongside a
     named size is exactly what made the names look identical. */
  document.getElementById('size').disabled = !custom;
});
/* Removing lives on the shared folder page, which lists what is in here with
   thumbnails and takes the marks before it takes anything away. One picture
   picked by name off a grid is the mis-tap this page used to be able to make. */
var pick = document.getElementById('pick');
var picked = document.getElementById('picked');
if (pick && picked) {
  pick.addEventListener('change', function () {
    var n = pick.files ? pick.files.length : 0;
    picked.textContent = n === 0 ? 'None chosen'
                       : n === 1 ? pick.files[0].name
                       : n + ' files chosen';
  });
}
""" + POSITION_SCRIPT


def _options(choices, chosen: str) -> str:
    return "".join(
        '<option value="{v}"{sel}>{label}</option>'.format(
            v=escape(value),
            sel=" selected" if str(value) == str(chosen) else "",
            label=escape(label))
        for value, label in choices)


def render_page(token: str, stickers: list, message: str = "",
                bad: bool = False, form: dict = None) -> str:
    """
    The page, rendered from whatever was last submitted.

    `form` carries the previous answers back in. Re-rendering from defaults
    meant every placement reset the position, the size and the duration - so
    putting three stickers in the same corner meant setting the same three
    controls three times.
    """
    from src.ui.widget import normalise_position

    form = form or {}
    position = normalise_position(form.get("quadrant"), "center")
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
        src = f"/asset/stickers/{escape(sticker.name)}?token={escape(token)}"
        if sticker.kind == "video":
            # No poster frame without a decoder, so it is named rather than
            # shown - a tile drawn as nothing reads as broken.
            inner = f'<div class="vid">{escape(sticker.label)}<br>(video)</div>'
        else:
            inner = (f'<img src="{src}" alt="{escape(sticker.label)}" '
                     f'loading="lazy">')
        tiles.append(
            f'<div class="tile" data-name="{escape(sticker.name)}" '
            f'data-label="{escape(sticker.label)}">{inner}'
            f'<div class="nm">{escape(sticker.label)}</div></div>')

    grid = "".join(tiles) or (
        '<div class="empty">Nothing here yet. Upload something above.</div>')

    label = next((s.label for s in stickers if s.name == chosen), "")
    disabled = "" if chosen else " disabled"
    action = f"/public/sticker_add?token={escape(token)}"

    body = f"""
<section>
  <h2>Upload a new one</h2>
  <form method="post" enctype="multipart/form-data" action="{action}">
    <input type="file" name="file" id="pick" multiple
           accept="image/*,video/mp4,video/webm" required hidden>
    <label class="filebtn" for="pick">Choose files</label>
    <span class="picked" id="picked">None chosen</span>
    <button type="submit">Upload</button>
  </form>
</section>

<section>
  <h2>{len(stickers)} in your library</h2>
  <form method="post" action="{action}" class="clearform"
        onsubmit="return confirm('Take every sticker off the home page? They stay in your library.');">
    <input type="hidden" name="clear_placed" value="1">
    <button type="submit" class="danger">Clear the home page</button>
  </form>

  <form method="post" action="{action}" id="place">
    <input type="hidden" name="sticker" id="sticker" value="{escape(chosen)}">
    <div class="grid" id="grid">{grid}</div>

    <label>Where should it go?</label>
    {position_grid(position)}

    <label for="mode">How long should it stay?</label>
    <select id="mode" name="mode">{_options(MODES, mode)}</select>

    <div id="timeoutRow" style="display:{"block" if mode == "temporary" else "none"}">
      <label for="timeout">Gone after (seconds)</label>
      <input id="timeout" name="timeout" type="number" min="1" max="86400"
             value="{escape(timeout)}">
      <p class="hint">1 second is allowed - useful for testing placement.</p>
      <label class="check">
        <input type="checkbox" name="delete_after" value="1"
               {"checked" if str(form.get("delete_after") or "").lower()
                in ("1", "true", "on", "yes") else ""}>
        <span>Delete the file too, when it goes</span>
      </label>
    </div>

    <label for="scale">How big?</label>
    <select id="scale" name="scale">{_options(SCALES, scale)}</select>
    <div id="sizeRow" style="display:{"block" if scale == "custom" else "none"}">
      <label for="size">Longest side (pixels)</label>
      <input id="size" name="size" type="number" min="32" max="1200"
             value="{escape(size)}"{"" if scale == "custom" else " disabled"}>
    </div>

    <div class="go">
      <button type="submit" id="go"{disabled}>
        {f'Place "{escape(label)}"' if chosen else "Choose a sticker first"}</button>
      <a class="danger manage" href="/upload/stickers?token={escape(token)}">
        Manage the library</a>
    </div>
  </form>
</section>
"""

    return page(
        title="Stickers",
        heading="Stickers",
        blurb="Put something on the panel.",
        token=token, message=message, bad=bad,
        css=CSS, body=body, script=SCRIPT,
    )
