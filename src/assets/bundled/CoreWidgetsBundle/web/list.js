/* Everything the panel had to say arrives as one object - see
   docs/web-ui.md. Nothing in this file is substituted on the way out, which
   is also why the newline below is a plain one: it used to be doubled to
   survive %-formatting on the Python side. */
var PAGE   = window.PAGE || {};
var KNOWN  = PAGE.known || {};
function fillChooser() {
  var pick = document.getElementById('target');
  if (!pick) { return; }
  var chosen = PAGE.target || '';
  pick.innerHTML = '';

  var blank = document.createElement('option');
  blank.value = '';
  blank.textContent = 'Make a new one';
  pick.appendChild(blank);

  /* Appended, not serialised back into innerHTML. Building elements and then
     round-tripping them through outerHTML relies on the browser escaping on
     the way out, which it does - but it is a reliance with no purpose, and
     it is one more place a title has to survive being turned into text. */
  (PAGE.lists || []).forEach(function (entry) {
    var opt = document.createElement('option');
    opt.value = entry.key;
    opt.textContent = entry.title;
    if (entry.key === chosen) { opt.selected = true; }
    pick.appendChild(opt);
  });
  pick.value = chosen;
}

function fillSwatches() {
  var host = document.getElementById('swatches');
  if (!host) { return; }
  host.innerHTML = (PAGE.colours || []).map(function (colour, index) {
    var id = 'c' + index;
    return '<input type="radio" name="colour" id="' + id + '" value="' +
           colour + '"' + (index === 0 ? ' checked' : '') + '>' +
           '<label for="' + id + '" style="background:' + colour +
           '"></label>';
  }).join('');
}

/* The grid's markup is fixed; only which cell is chosen is not. It is a
   hidden field and a row of buttons rather than radios, so the choice has to
   be set in both places - POSITION_SCRIPT keeps them together after this. */
function fillGrid() {
  var wanted = PAGE.quadrant || 'top-right';
  var field = document.getElementById('quadrant');
  if (field) { field.value = wanted; }
  document.querySelectorAll('.where button[data-q]').forEach(function (b) {
    b.classList.toggle('on', b.dataset.q === wanted);
  });
}

function fillForm() {
  var form = document.getElementById('listform');
  if (form) { form.action = PAGE.endpoint + '?token=' +
                            encodeURIComponent(PAGE.token || ''); }
}

fillForm();
fillChooser();
fillSwatches();
fillGrid();

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
  }).join('\n');
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
    entry.text.split('\n').forEach(function (line) {
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


Array.prototype.forEach.call(document.querySelectorAll('.where'),
  function (grid) {
    var field = document.getElementById(grid.dataset.for);
    Array.prototype.forEach.call(grid.querySelectorAll('button'),
      function (b) {
        b.addEventListener('click', function (e) {
          e.preventDefault();
          Array.prototype.forEach.call(grid.querySelectorAll('button'),
            function (o) { o.classList.remove('on'); });
          b.classList.add('on');
          if (field) { field.value = b.dataset.q; }
        });
      });
  });
