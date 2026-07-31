"""
The form behind /public/list_add.

One list at a time. The chooser at the top decides WHICH list; everything
below it is that list, and submitting writes it back. A list that is not on
the panel yet is the same page with nothing chosen - it becomes an edit of a
real widget the moment it is put up.
"""

from __future__ import annotations

import json

from src.webui import escape, page, position_grid, POSITION_SCRIPT

NEW = ""        # the chooser's value for "not a list yet"


CSS = """
 .rows{display:flex;flex-direction:column;gap:8px;margin:0}
 .item{display:flex;align-items:center;gap:10px;padding:8px 10px;
   border:1px solid var(--line);border-radius:11px;background:var(--card2)}
 .item .tick{width:30px;height:30px;min-height:0;flex:none;border-radius:8px;
   border:2px solid var(--muted);background:none;color:transparent;
   padding:0;font-size:16px;line-height:1;font-weight:700}
 .item.done .tick{border-color:var(--accent);color:var(--accent)}
 .item .txt{flex:1;min-width:0;padding:9px 10px;font-size:15px;
   background:transparent;border:1px solid transparent;border-radius:8px}
 .item .txt:focus{background:#111114;border-color:var(--accent)}
 .item.done .txt{text-decoration:line-through;color:var(--muted)}
 .item .kill{width:34px;height:34px;min-height:0;flex:none;border:none;
   background:none;color:var(--muted);font-size:20px;line-height:1;padding:0;
   border-radius:8px;opacity:.55}
 .item .kill:hover{opacity:1;background:rgba(255,122,122,.16);color:#ffb3b3;
   border:none}
 .addrow{display:flex;gap:9px;margin:10px 0 0}
 .addrow input{flex:1}
 .addrow button{flex:0 0 auto}
 .swatches{display:flex;flex-wrap:wrap;gap:10px;margin-top:6px}
 .swatches input{display:none}
 .swatches label{width:44px;height:44px;border-radius:50%;margin:0;
   cursor:pointer;border:3px solid transparent;box-sizing:border-box}
 .swatches input:checked + label{border-color:var(--text)}
 .go{margin:22px 0 0}
 .go button{width:100%}
"""


SCRIPT = """
var KNOWN  = %(known)s;
var target = document.getElementById('target');
var titles = document.getElementById('title');
var rows   = document.getElementById('rows');
var hidden = document.getElementById('text');
var where  = document.getElementById('wherebox');
var verb   = document.getElementById('verb');
var items  = [];

/* The rows ARE the value. The hidden field is only what posts, written in
   the same [x] form the panel reads back, so nothing is re-guessed on the
   way in. */
function sync() {
  hidden.value = items.filter(function (i) {
    return i.text.trim() !== '';
  }).map(function (i) {
    return (i.done ? '[x] ' : '') + i.text.trim();
  }).join('\\n');
}

function draw() {
  rows.innerHTML = '';
  items.forEach(function (item, index) {
    var row = document.createElement('div');
    row.className = 'item' + (item.done ? ' done' : '');

    var tick = document.createElement('button');
    tick.type = 'button';
    tick.className = 'tick';
    tick.innerHTML = '&#10003;';
    tick.setAttribute('aria-label', 'Tick off');
    tick.addEventListener('click', function () {
      items[index].done = !items[index].done;
      draw();
    });

    /* An input, not a span. Fixing a typo on a list otherwise means deleting
       the line and typing it again. */
    var text = document.createElement('input');
    text.type = 'text';
    text.className = 'txt';
    text.value = item.text;
    /* The model is updated on every keystroke but the rows are NOT redrawn -
       rebuilding the list under somebody who is typing in it takes the focus
       and the caret with it. */
    text.addEventListener('input', function () {
      items[index].text = text.value;
      sync();
    });

    var kill = document.createElement('button');
    kill.type = 'button';
    kill.className = 'kill';
    kill.innerHTML = '&times;';
    kill.setAttribute('aria-label', 'Remove');
    kill.addEventListener('click', function () {
      items.splice(index, 1);
      draw();
    });

    row.appendChild(tick);
    row.appendChild(text);
    row.appendChild(kill);
    rows.appendChild(row);
  });
  sync();
}

function addTyped() {
  var field = document.getElementById('newitem');
  var text  = field.value.trim();
  if (!text) { return; }
  items.push({text: text, done: false});
  field.value = '';
  draw();
  field.focus();
}
document.getElementById('additem').addEventListener('click', addTyped);
document.getElementById('newitem').addEventListener('keydown', function (e) {
  if (e.key === 'Enter') { e.preventDefault(); addTyped(); }
});

/* The chooser is the source of truth for WHICH list. Once it has chosen, the
   page is the source of truth for that list's contents. */
function load() {
  var entry = target ? KNOWN[target.value] : null;
  titles.value = entry ? entry.title : '';
  items = [];
  if (entry && entry.text) {
    entry.text.split('\\n').forEach(function (line) {
      line = line.trim();
      if (!line) { return; }
      var done = line.indexOf('[x] ') === 0;
      items.push({text: done ? line.slice(4) : line, done: done});
    });
  }
  /* A list already on the panel has a place, and offering to move it here
     would be a third way to do what dragging already does. */
  if (where) { where.style.display = entry ? 'none' : ''; }
  if (verb)  { verb.textContent = entry ? 'Save changes' : 'Put it up'; }
  draw();
}
if (target) { target.addEventListener('change', load); }
load();
""" + POSITION_SCRIPT


def render_page(token: str, colours: list, message: str = "", bad: bool = False,
                lists: list = None, target: str = "",
                quadrant: str = "top-right") -> str:
    """
    The list editor.

    `lists` is (key, title, text) for every checklist on the panel, `text`
    written in the same [x] form the widget parses. `target` is the one the
    chooser opens on - the key just created, when something was just put up.
    """
    lists = lists or []

    options = "".join(
        f'<option value="{escape(key)}"'
        f'{" selected" if key == target else ""}>{escape(title)}</option>'
        for key, title, _text in lists)

    swatches = "".join(
        f'<input type="radio" name="colour" id="c{index}" '
        f'value="{escape(colour)}"{" checked" if index == 0 else ""}>'
        f'<label for="c{index}" style="background:{escape(colour)}"></label>'
        for index, colour in enumerate(colours))

    # Every list's contents, so switching between them is instant rather than
    # a round trip for something already known.
    known = {key: {"title": title, "text": text} for key, title, text in lists}

    editing = bool(target)
    body = f"""
<form method="post" action="/public/list_add?token={escape(token)}">
<section>
  <label for="target">Which list</label>
  <select name="target" id="target">
    <option value="{NEW}"{"" if editing else " selected"}>Make a new one</option>
    {options}
  </select>

  <label for="title">Name</label>
  <input type="text" name="title" id="title" placeholder="Shopping"
         autocomplete="off">
</section>

<section>
  <label>Items</label>
  <div class="rows" id="rows"></div>
  <div class="addrow">
    <input type="text" id="newitem" placeholder="Milk" autocomplete="off">
    <button type="button" id="additem">Add</button>
  </div>
  <textarea name="text" id="text" hidden></textarea>
  <p class="hint">Tap the box to tick something off, the name to change it,
    and the cross to take it away. Empty lines are dropped.</p>
</section>

<section>
  <label>Colour</label>
  <div class="swatches">{swatches}</div>
</section>

<section id="wherebox">
  <label>Where it goes</label>
  {position_grid(quadrant)}
</section>

<div class="go">
  <button type="submit" id="verb">
    {"Save changes" if editing else "Put it up"}</button>
</div>
</form>
"""

    return page(
        title="Checklist",
        heading="Checklist",
        blurb="Pick a list to edit, or make a new one.",
        token=token, message=message, bad=bad,
        css=CSS, body=body,
        script=SCRIPT % {"known": json.dumps(known)},
    )
