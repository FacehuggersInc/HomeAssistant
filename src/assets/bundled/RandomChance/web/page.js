/* Everything the panel had to tell this page arrives as one object, written
   into the document by the endpoint. Nothing in this file is substituted or
   escaped on the way out - it is served as it was written.
   
   That is the whole reason it is a file. A quote inside an attribute inside
   a JavaScript string inside a Python string passes through two rounds of
   escape processing, and one of them will eat the backslash. */
var RC = window.PAGE || {};
var wheels = RC.wheels || [];

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
  data.set('token', RC.token || '');
  return fetch(RC.endpoint, {method: 'POST', body: data})
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

function cap(){ return RC.maxDice || 60; }

/* The die buttons are built here rather than written into the markup, so the
   page stays static and the set of dice stays a fact the panel owns. */
function drawDieTypes(){
  var host = document.getElementById('dice-types');
  host.innerHTML = (RC.dieTypes || []).map(function(sides){
    return '<button type="button" onclick="addDie(' + sides + ')">d' +
           sides + '</button>';
  }).join('');
  document.getElementById('dice-cap').textContent = cap();
  document.getElementById('item-cap').textContent = RC.maxItems || 40;
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
      '<select onchange="editRule(' + i + ',\'op\',this.value)">' +
        '<option value="greater"' + (r.op === 'greater' ? ' selected' : '') +
          '>Over</option>' +
        '<option value="less"' + (r.op === 'less' ? ' selected' : '') +
          '>Under</option>' +
      '</select>' +
      '<input type="number" value="' + r.value + '" min="' + usable(r.op).low +
        '" max="' + usable(r.op).high +
        '" onchange="editRule(' + i + ',\'value\',this.value)">' +
      '<button type="button" class="drop" onclick="dropRule(' + i + ')"' +
        ' aria-label="Remove">&times;</button>' +
      '<input type="text" placeholder="Show this" value="' +
        r.text.replace(/"/g, '&quot;') +
        '" onchange="editRule(' + i + ',\'text\',this.value)">' +
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
  if(current.items.length >= (RC.maxItems || 40)){
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

drawDieTypes();
chooseWheel('');
drawPicker();
drawTray();
try{ show(localStorage.getItem('rc-tab') || 'coin'); }catch(e){ show('coin'); }
