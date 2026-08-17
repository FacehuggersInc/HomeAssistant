"""
The Random Chance page, for a phone.

The panel is on a wall and the person deciding what to roll is usually not
standing at it. This is the control surface: name the question, pick what to
flip or roll, and the panel does the showing.

Plain HTML and one script. `src/webui.py` supplies the chrome, so a page that
uses `<button>`, `<section>`, `<label>` and `.card` inherits the current look
with nothing to maintain here. The only CSS below is for the three things the
chrome has no opinion about - the tab strip, the dice tray and an outcome row.

No import from the plugin, relative or otherwise.

`sibling()` loads this file under the name `__plugin_sibling__<Folder>.<n>`,
and Python works out the package for a relative import by stripping the last
component of that - so `from .dice import x` looks for a package
`__plugin_sibling__RandomChance`, which has never existed, and the endpoint
fails with "No module named" it. The documented warning is about `..`; a
single dot fails here for a different reason and just as hard.

So the page is handed what it needs to draw. It knows about HTML and nothing
else, which is a better boundary anyway.
"""

from __future__ import annotations

import json

from src.webui import page, escape

TABS = (("coin", "Coin"), ("dice", "Dice"), ("wheel", "Wheel"))

CSS = """
.tabs{display:flex;gap:8px;margin:0 0 18px}
.tabs button{flex:1;background:var(--card);border:1px solid var(--line);
  color:var(--muted);border-radius:12px;padding:12px 8px;font-size:16px}
.tabs button.on{background:var(--card2);color:var(--text);
  border-color:var(--accent)}
.pane{display:none}
.pane.on{display:block}
.dice-types{display:flex;flex-wrap:wrap;gap:8px}
.dice-types button{flex:1 1 26%;min-width:86px;padding:14px 6px;font-size:17px}
.tray{display:flex;flex-wrap:wrap;gap:10px;margin-top:12px}
.chip{display:flex;align-items:center;gap:2px;background:var(--card2);
  border:1px solid var(--line);border-radius:999px;padding:4px}
.chip b{font-size:16px;color:var(--text);min-width:62px;text-align:center}
.chip button{background:none;border:none;color:var(--text);font-size:20px;
  width:38px;height:38px;line-height:1;padding:0;border-radius:50%}
.chip button:disabled{color:var(--line)}
.chip .drop{color:var(--muted);font-size:18px}
.rule{display:flex;gap:8px;align-items:center;background:var(--card2);
  border:1px solid var(--line);border-radius:12px;padding:10px;
  margin-bottom:8px;flex-wrap:wrap}
.rule select{flex:0 0 30%}
.rule input[type=number]{flex:0 0 24%}
.rule input[type=text]{flex:1 1 100%}
.rule .drop{flex:0 0 auto;background:none;border:none;color:var(--muted);
  font-size:22px;padding:0 6px;margin-left:auto}
#say{display:none}
.tools{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.tools button{flex:1 1 30%;min-width:110px;padding:12px 6px;font-size:15px}
.witem{display:flex;gap:8px;align-items:center;background:var(--card2);
  border:1px solid var(--line);border-radius:12px;padding:8px;
  margin-bottom:8px}
.witem input[type=text]{flex:1 1 auto;min-width:0}
.witem input[type=number]{flex:0 0 74px}
.witem .pc{color:var(--muted);font-size:14px}
.witem .off input[type=text]{opacity:.45}
.witem .toggle{flex:0 0 auto;width:52px;padding:8px 0;font-size:13px;
  border-radius:999px;border:1px solid var(--line);background:var(--card);
  color:var(--muted)}
.witem .toggle.on{background:var(--accent);border-color:var(--accent);
  color:#08120c}
.witem .drop{flex:0 0 auto;background:none;border:none;color:var(--muted);
  font-size:22px;padding:0 6px}
.witem.off input{opacity:.45}
"""

# One script, and the reason the page does not reload.
#
# The dice and the outcome rules live in arrays here rather than in form
# fields. A normal submit navigates, the answer arrives as a fresh document,
# and everything picked is gone - which on a phone means re-tapping a handful
# of dice to roll the same thing twice. So the form is posted with fetch and
# the page never goes anywhere; only the banner changes.
SCRIPT = """
function show(name){
  document.querySelectorAll('.pane').forEach(function(p){
    p.classList.toggle('on', p.dataset.pane === name);});
  document.querySelectorAll('.tabs button').forEach(function(b){
    b.classList.toggle('on', b.dataset.tab === name);});
  try{ localStorage.setItem('rc-tab', name); }catch(e){}
}

function banner(text, bad){
  var box = document.getElementById('say');
  box.textContent = text || '';
  box.className = bad ? 'banner bad' : 'banner';
  box.style.display = text ? 'block' : 'none';
}

/* One place the page talks to the panel. Everything posts here and nothing
   navigates: the dice, the outcome rules and the wheel being edited all live
   in arrays on this page, and a reply that replaced the document would throw
   them away. */
function post(fields){
  var data = new FormData();
  Object.keys(fields).forEach(function(key){ data.set(key, fields[key]); });
  data.set('fmt', 'json');
  data.set('token', document.getElementById('token').value);
  return fetch(document.getElementById('endpoint').value,
               {method: 'POST', body: data})
    .then(function(r){ return r.json(); })
    .then(function(answer){
      banner(answer.message, answer.bad);
      if(answer.wheels){ wheels = answer.wheels; drawPicker(); }
      return answer;
    })
    .catch(function(){
      banner('Could not reach the panel.', true);
      return {bad: true};
    });
}

function busy(on){
  document.querySelectorAll('button[type=submit], .tools button, #save, #drop')
    .forEach(function(b){ b.disabled = on; });
  if(!on){ drawTray(); drawWheel(); }
}

function send(form){
  var fields = {};
  new FormData(form).forEach(function(value, key){ fields[key] = value; });
  busy(true);
  post(fields).then(function(){ busy(false); });
  return false;
}

/* Dice picked so far, in the order they were added. */
var dice = [];

function cap(){
  return parseInt(document.getElementById('tray').dataset.cap || '60', 10);
}

function addDie(sides){
  if(bounds().low >= cap()){ return; }
  var found = dice.find(function(d){ return d.sides === sides; });
  if(found){ found.count += 1; } else { dice.push({sides: sides, count: 1}); }
  drawTray();
}

function nudge(sides, by){
  var found = dice.find(function(d){ return d.sides === sides; });
  if(!found){ return; }
  if(by > 0 && bounds().low >= cap()){ return; }
  found.count += by;
  if(found.count < 1){ dropDie(sides); return; }
  drawTray();
}

function dropDie(sides){
  dice = dice.filter(function(d){ return d.sides !== sides; });
  drawTray();
}

/* The lowest and highest total the picked dice can actually produce. Every
   die shows at least 1, so the floor is simply how many there are. */
function bounds(){
  var low = 0, high = 0;
  dice.forEach(function(d){ low += d.count; high += d.count * d.sides; });
  return {low: low, high: high};
}

/* The thresholds that mean something, which is NOT the same range for both
   comparisons. On 2d6 the total runs 2 to 12, but "over 12" can never hold
   and "over 1" holds every time - so a usable "over" is 2 to 11, and
   "under" is its mirror at 3 to 12. The server drops anything outside
   these, so the page had better not offer them. */
function usable(op){
  var span = bounds();
  if(!span.low){ return {low: 0, high: 0}; }
  if(op === 'greater'){
    return {low: span.low, high: Math.max(span.low, span.high - 1)};
  }
  return {low: Math.min(span.high, span.low + 1), high: span.high};
}

function drawTray(){
  var tray = document.getElementById('tray');
  var span = bounds();
  var full = span.low >= cap();
  if(!dice.length){
    tray.innerHTML = '<p class="empty">Nothing picked yet.</p>';
  } else {
    tray.innerHTML = dice.map(function(d){
      return '<span class="chip">' +
        '<button type="button" onclick="nudge(' + d.sides + ',-1)"' +
          ' aria-label="One fewer">&minus;</button>' +
        '<b>' + d.count + 'd' + d.sides + '</b>' +
        '<button type="button" onclick="nudge(' + d.sides + ',1)"' +
          (full ? ' disabled' : '') + ' aria-label="One more">+</button>' +
        '<button type="button" class="drop" onclick="dropDie(' + d.sides +
          ')" aria-label="Remove">&times;</button></span>';
    }).join('');
  }
  document.querySelectorAll('.dice-types button').forEach(function(b){
    b.disabled = full;
  });
  document.getElementById('groups').value = JSON.stringify(dice);
  document.getElementById('roll').disabled = span.low === 0;
  /* Just the count against the cap. The label beside it already says
     "Dice", and repeating the noun there read as "Dice 5 dice". */
  document.getElementById('count').textContent = span.low
    ? span.low + ' of ' + cap()
    : '';
  drawRules();
}

/* Outcome rules. Order is priority - the first that matches is the one shown. */
var rules = [];

function addRule(){
  var span = usable('greater');
  rules.push({op: 'greater',
              value: Math.round((span.low + span.high) / 2),
              text: ''});
  drawRules();
}

function dropRule(index){ rules.splice(index, 1); drawRules(); }

function editRule(index, field, value){
  if(field === 'value'){
    var span = usable(rules[index].op);
    var n = parseInt(value, 10);
    if(isNaN(n)){ n = span.low; }
    rules[index].value = Math.min(span.high, Math.max(span.low, n));
  } else {
    rules[index][field] = value;
  }
  drawRules();
}

function drawRules(){
  var host = document.getElementById('rules');
  var span = bounds();

  /* A threshold outside what the dice can actually roll is not a rule.
     Over the highest can never happen and under the lowest can never happen;
     the other way round they happen every single time, which quietly kills
     every rule below them. Clamped rather than refused, so the number moves
     to the nearest one that means something instead of the row going red. */
  rules.forEach(function(r){
    var fits = usable(r.op);
    r.value = Math.min(fits.high, Math.max(fits.low, r.value));
  });

  document.getElementById('add-rule').disabled = span.low === 0;
  document.getElementById('rule-note').textContent = span.low
    ? 'These dice total ' + span.low + ' to ' + span.high + '.'
    : 'Pick some dice first.';

  host.innerHTML = rules.map(function(r, i){
    return '<div class="rule">' +
      '<select onchange="editRule(' + i + ',\\'op\\',this.value)">' +
        '<option value="greater"' + (r.op === 'greater' ? ' selected' : '') +
          '>Over</option>' +
        '<option value="less"' + (r.op === 'less' ? ' selected' : '') +
          '>Under</option>' +
      '</select>' +
      '<input type="number" value="' + r.value + '" min="' + usable(r.op).low +
        '" max="' + usable(r.op).high +
        '" onchange="editRule(' + i + ',\\'value\\',this.value)">' +
      '<button type="button" class="drop" onclick="dropRule(' + i + ')"' +
        ' aria-label="Remove">&times;</button>' +
      '<input type="text" placeholder="Show this" value="' +
        r.text.replace(/"/g, '&quot;') +
        '" onchange="editRule(' + i + ',\\'text\\',this.value)">' +
    '</div>';
  }).join('');
  document.getElementById('outcomes').value = JSON.stringify(rules);
}

/* ── Wheels ────────────────────────────────────────────────────────────── */

/* Saved wheels, and the one being edited. `current` is a working copy, so
   backing out of an edit is a matter of choosing the wheel again rather than
   of undoing anything. */
var current = null;

function liveItems(){
  return (current ? current.items : []).filter(function(i){ return i.on; });
}

/* Shares are percentages and the enabled ones come to exactly 100. Whole
   numbers with the remainder handed out largest-first, the same rule the
   panel uses - a column that reads 99 is the sort of thing nobody notices
   until they are staring at a wheel wondering why the arrow is off. */
function renorm(){
  var live = liveItems();
  if(!live.length){ return; }
  var total = live.reduce(function(n, i){ return n + Math.max(0, i.share); }, 0);
  if(total <= 0){
    live.forEach(function(i){ i.share = 100 / live.length; });
    total = 100;
  }
  var exact = live.map(function(i){ return Math.max(0, i.share) * 100 / total; });
  var whole = exact.map(function(v){ return Math.floor(v); });
  var left = 100 - whole.reduce(function(a, b){ return a + b; }, 0);
  var order = exact.map(function(v, i){ return i; }).sort(function(a, b){
    return (exact[b] - whole[b]) - (exact[a] - whole[a]);
  });
  for(var n = 0; n < left; n++){ whole[order[n % order.length]] += 1; }
  live.forEach(function(i, n){ i.share = whole[n]; });
}

/* Setting one item to a number has to move the others, because there is only
   ever 100 to go round. What is left is shared out in proportion to what
   they already had, so the shape of the rest of the wheel survives. */
function setShare(index, value){
  var item = current.items[index];
  if(!item || !item.on){ return; }
  var wanted = Math.min(100, Math.max(0, parseInt(value, 10) || 0));
  var others = liveItems().filter(function(i){ return i !== item; });
  item.share = wanted;
  var spare = 100 - wanted;
  var pool = others.reduce(function(n, i){ return n + Math.max(0, i.share); }, 0);
  others.forEach(function(i){
    i.share = pool > 0 ? Math.max(0, i.share) * spare / pool
                       : spare / others.length;
  });
  renorm();
  drawWheel();
}

/* An item joining the wheel takes an equal share OF THE NEW TOTAL, and the
   others are scaled down to make room for it.

   Giving it a share and letting renorm() sort it out is not the same thing:
   that scales everything proportionally, so the items already there keep
   their head start and three items added one after another come out 50/25/25
   rather than equal. Scaling the others to fit means a fresh wheel stays
   even, and a wheel somebody has deliberately weighted keeps its shape. */
function giveShare(item){
  var others = liveItems().filter(function(i){ return i !== item; });
  var share = 100 / (others.length + 1);
  var spare = 100 - share;
  var pool = others.reduce(function(n, i){ return n + Math.max(0, i.share); },
                           0);
  others.forEach(function(i){
    i.share = pool > 0 ? Math.max(0, i.share) * spare / pool
                       : spare / others.length;
  });
  item.share = share;
}

function evenShares(){
  var live = liveItems();
  live.forEach(function(i){ i.share = 100 / live.length; });
  renorm();
  drawWheel();
}

function enableAll(on){
  (current ? current.items : []).forEach(function(i){ i.on = on; });
  renorm();
  drawWheel();
}

function toggleItem(index){
  var item = current.items[index];
  if(!item){ return; }
  item.on = !item.on;
  if(item.on && !item.share){
    /* Back on with nothing is a slice that cannot be landed on, which is not
       what turning something back on means. */
    giveShare(item);
  }
  renorm();
  drawWheel();
}

function renameItem(index, value){
  var item = current.items[index];
  if(item){ item.label = value; }
  document.getElementById('wheel-json').value = JSON.stringify(packed());
}

function dropItem(index){
  current.items.splice(index, 1);
  renorm();
  drawWheel();
}

function addItem(){
  var box = document.getElementById('new-item');
  var label = (box.value || '').trim();
  if(!current || !label){ return; }
  if(current.items.length >= 40){
    banner('Forty items is as many as a wheel takes.', true);
    return;
  }
  var item = {id: 'i' + Date.now() + Math.floor(Math.random() * 900),
              label: label, on: true, share: 0};
  current.items.push(item);
  giveShare(item);
  box.value = '';
  renorm();
  drawWheel();
  box.focus();
}

function blankWheel(){
  return {id: 'w' + Date.now() + Math.floor(Math.random() * 900),
          name: '', items: []};
}

/* The panel stores `enabled`; the rows here read `on`. Converted at the two
   edges rather than everywhere in between. */
function unpack(wheel){
  return {
    id: wheel.id,
    name: wheel.name || '',
    items: (wheel.items || []).map(function(i){
      return {id: i.id, label: i.label,
              on: i.enabled !== false, share: Number(i.share) || 0};
    })
  };
}

function chooseWheel(id){
  if(!id){ current = blankWheel(); }
  else {
    var found = wheels.find(function(w){ return w.id === id; });
    current = found ? unpack(found) : blankWheel();
  }
  document.getElementById('wheel-name').value = current.name || '';
  renorm();
  drawWheel();
}

/* What goes to the panel: the shape wheels.py reads, not the shape the page
   finds convenient. */
function packed(){
  if(!current){ return null; }
  return {
    id: current.id,
    name: document.getElementById('wheel-name').value || current.name || '',
    items: current.items.map(function(i){
      return {id: i.id, label: i.label, enabled: i.on, share: i.share};
    })
  };
}

function drawPicker(){
  var pick = document.getElementById('wheel-pick');
  var chosen = current ? current.id : '';
  pick.innerHTML = '<option value="">New wheel</option>' +
    wheels.map(function(w){
      return '<option value="' + w.id + '"' +
        (w.id === chosen ? ' selected' : '') + '>' +
        (w.name || 'Untitled').replace(/</g, '&lt;') + '</option>';
    }).join('');
}

function drawWheel(){
  var host = document.getElementById('wheel-items');
  var live = liveItems();

  if(!current || !current.items.length){
    host.innerHTML = '<p class="empty">Nothing on this wheel yet.</p>';
  } else {
    /* Handlers take the row's index, not its id. An id has to be quoted
       inside an attribute inside a JavaScript string inside a Python string,
       and one of those layers will eat the escape - this one did. A number
       needs no quoting at all, and the rows are redrawn after every change,
       so an index is never stale by the time it is used. */
    host.innerHTML = current.items.map(function(i, n){
      return '<div class="witem' + (i.on ? '' : ' off') + '">' +
        '<button type="button" class="toggle' + (i.on ? ' on' : '') +
          '" onclick="toggleItem(' + n + ')">' +
          (i.on ? 'On' : 'Off') + '</button>' +
        '<input type="text" value="' + (i.label || '').replace(/"/g, '&quot;') +
          '" onchange="renameItem(' + n + ',this.value)">' +
        (i.on
          ? '<input type="number" min="0" max="100" value="' +
              Math.round(i.share) + '" onchange="setShare(' + n +
              ',this.value)"><span class="pc">%</span>'
          : '<span class="pc">off</span>') +
        '<button type="button" class="drop" onclick="dropItem(' + n +
          ')" aria-label="Remove">&times;</button>' +
      '</div>';
    }).join('');
  }

  document.getElementById('wheel-count').textContent =
    current && current.items.length
      ? live.length + ' of ' + current.items.length + ' on'
      : '';
  document.getElementById('spin').disabled = live.length < 2;
  document.getElementById('drop').disabled =
    !current || !wheels.some(function(w){ return w.id === current.id; });
  document.getElementById('wheel-json').value = JSON.stringify(packed());
}

function saveWheel(){
  if(!current){ return; }
  busy(true);
  post({what: 'wheel_save', wheel: JSON.stringify(packed())})
    .then(function(){ busy(false); });
}

function deleteWheel(){
  if(!current){ return; }
  busy(true);
  post({what: 'wheel_delete', wheel_id: current.id}).then(function(answer){
    if(!answer.bad){ chooseWheel(''); }
    busy(false);
  });
}

chooseWheel('');
drawPicker();
drawTray();
try{ show(localStorage.getItem('rc-tab') || 'coin'); }catch(e){ show('coin'); }
"""


def _tabs() -> str:
    buttons = "".join(
        f'<button type="button" data-tab="{key}" onclick="show(\'{key}\')">'
        f'{escape(label)}</button>' for key, label in TABS)
    return f'<div class="tabs">{buttons}</div>'


def _coin_pane(action: str, token: str) -> str:
    return f"""
<div class="pane" data-pane="coin">
  <form method="post" action="{escape(action)}" onsubmit="return send(this)">
    <input type="hidden" name="token" value="{escape(token)}">
    <section class="card">
      <label for="coin-title">What is being decided</label>
      <input id="coin-title" name="title" placeholder="Who goes first?">
      <p class="hint">Shown before the coin, if you give one.</p>
    </section>
    <section class="card">
      <label>What the sides stand for</label>
      <div class="row">
        <input name="heads" placeholder="Heads">
        <input name="tails" placeholder="Tails">
      </div>
      <p class="hint">The crown side is the first one. Leave both empty for
        heads and tails.</p>
    </section>
    <input type="hidden" name="what" value="coin">
    <button type="submit">Flip the coin</button>
  </form>
</div>
"""


def _dice_pane(action: str, token: str, die_types: tuple,
               max_dice: int) -> str:
    types = "".join(
        f'<button type="button" onclick="addDie({sides})">d{sides}</button>'
        for sides in die_types)
    return f"""
<div class="pane" data-pane="dice">
  <form method="post" action="{escape(action)}" onsubmit="return send(this)">
    <input type="hidden" name="token" value="{escape(token)}">
    <section class="card">
      <label for="dice-title">What is being decided</label>
      <input id="dice-title" name="title" placeholder="Who buys lunch?">
    </section>

    <section class="card">
      <label>Dice <span class="hint" id="count"></span></label>
      <div class="dice-types">{types}</div>
      <div class="tray" id="tray" data-cap="{max_dice}"></div>
      <p class="hint">Tap a die to add one, then &minus; and + to change how
        many of it. Up to {max_dice} in a roll, and all of them are
        drawn.</p>
    </section>

    <section class="card">
      <label>Outcomes</label>
      <p class="hint">Checked against the total, in order &mdash; the first
        one that matches is the one shown. They last as long as this page is
        open.</p>
      <p class="hint" id="rule-note"></p>
      <div id="rules"></div>
      <button type="button" id="add-rule" onclick="addRule()">Add an
        outcome</button>
    </section>

    <input type="hidden" name="what" value="dice">
    <input type="hidden" name="groups" id="groups" value="[]">
    <input type="hidden" name="outcomes" id="outcomes" value="[]">
    <button type="submit" id="roll" disabled>Roll</button>
  </form>
</div>
"""


def _wheel_pane(action: str, token: str) -> str:
    return f"""
<div class="pane" data-pane="wheel">
  <section class="card">
    <label for="wheel-pick">Wheel</label>
    <select id="wheel-pick" onchange="chooseWheel(this.value)"></select>
    <p class="hint">Saved wheels stay on the panel and can be used again.</p>
  </section>

  <form method="post" action="{escape(action)}" onsubmit="return send(this)">
    <input type="hidden" name="token" value="{escape(token)}">
    <section class="card">
      <label for="wheel-name">Name</label>
      <input id="wheel-name" placeholder="Who buys lunch?">
    </section>

    <section class="card">
      <label>Items <span class="hint" id="wheel-count"></span></label>
      <div id="wheel-items"></div>
      <div class="row">
        <input id="new-item" placeholder="Add an item">
        <button type="button" onclick="addItem()">Add</button>
      </div>
      <div class="tools">
        <button type="button" onclick="enableAll(true)">Enable all</button>
        <button type="button" onclick="enableAll(false)">Disable all</button>
        <button type="button" onclick="evenShares()">Equal chances</button>
      </div>
      <p class="hint">A percentage is a share of the wheel, so changing one
        moves the others &mdash; there is only ever 100 to go round. An item
        at 0% is on the list and not on the wheel.</p>
    </section>

    <input type="hidden" name="what" value="wheel_spin">
    <input type="hidden" name="wheel" id="wheel-json">
    <button type="submit" id="spin" disabled>Spin the wheel</button>
  </form>

  <div class="row">
    <button type="button" id="save" onclick="saveWheel()">Save</button>
    <button type="button" id="drop" class="danger"
            onclick="deleteWheel()">Delete</button>
  </div>
</div>
"""


def render(token: str, action: str, die_types: tuple, max_dice: int,
           wheels: list = None, message: str = "", bad: bool = False) -> str:
    """The whole page."""
    body = ('<div id="say" class="banner"></div>'
            # The token and the endpoint, once, for everything that posts.
            f'<input type="hidden" id="token" value="{escape(token)}">'
            f'<input type="hidden" id="endpoint" value="{escape(action)}">'
            + _tabs()
            + _coin_pane(action, token)
            + _dice_pane(action, token, die_types, max_dice)
            + _wheel_pane(action, token))
    return page(
        title="Random Chance",
        heading="Random Chance",
        blurb="Flip a coin or roll dice on the panel.",
        token=token, message=message, bad=bad,
        css=CSS,
        # The saved wheels are written into the page rather than fetched
        # after it loads: the list is small, it is needed before anything can
        # be drawn, and a second request is a second thing to fail.
        script=("var wheels = " + json.dumps(list(wheels or [])) + ";\n"
                + SCRIPT),
        body=body,
    )
