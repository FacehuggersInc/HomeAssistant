/* Everything the panel had to say arrives as one object - see
   docs/web-ui.md. */
var PAGE = window.PAGE || {};

function fillForm() {
  var action = PAGE.endpoint + '?token=' +
               encodeURIComponent(PAGE.token || '');
  ['noteform', 'removeform'].forEach(function (id) {
    var form = document.getElementById(id);
    if (form) { form.action = action; }
  });
}

/* Which note is being edited, if any. Empty means a new one. */
var target = PAGE.target || '';

function drawChooser() {
  var pick = document.getElementById('target');
  if (!pick) { return; }
  pick.innerHTML = '';

  var blank = document.createElement('option');
  blank.value = '';
  blank.textContent = 'Make a new one';
  pick.appendChild(blank);

  /* Appended rather than built as a string: a note's first line stands in for
     its name, and that line is whatever somebody typed. */
  (PAGE.notes || []).forEach(function (entry) {
    var option = document.createElement('option');
    option.value = entry.key;
    option.textContent = entry.label;
    if (entry.key === target) { option.selected = true; }
    pick.appendChild(option);
  });
  pick.value = target;

  document.getElementById('targetfield').value = target;
  document.getElementById('removefield').value = target;
  document.getElementById('drop').disabled = !target;
  document.getElementById('verb').textContent =
    target ? 'Save this note' : 'Put it up';
}

function chooseNote(key) {
  target = key || '';
  var known = (PAGE.known || {})[target];
  document.getElementById('text').value = known ? (known.text || '') : '';
  if (known && known.colour) { PAGE.colour = known.colour; }
  if (known && known.size) { PAGE.fontSize = known.size; }
  drawChooser();
  fillSwatches();
  fillSizes();
}

function fillSwatches() {
  var host = document.getElementById('swatches');
  if (!host) { return; }
  var chosen = PAGE.colour || (PAGE.colours || [])[0];
  host.innerHTML = '';
  (PAGE.colours || []).forEach(function (colour, index) {
    var id = 'c' + index;
    var radio = document.createElement('input');
    radio.type = 'radio';
    radio.name = 'colour';
    radio.id = id;
    radio.value = colour;
    radio.checked = colour === chosen;

    var label = document.createElement('label');
    label.htmlFor = id;
    label.setAttribute('for', id);
    label.style.background = colour;
    label.title = colour;

    host.appendChild(radio);
    host.appendChild(label);
  });
}

/* Each option is drawn at the size it names. A list reading 14 / 17 / 20 in
   one size tells you the numbers and nothing about what you are choosing,
   which is the only question being asked here. */
function fillSizes() {
  var host = document.getElementById('sizes');
  if (!host) { return; }
  var chosen = PAGE.fontSize || PAGE.defaultFontSize;
  host.innerHTML = '';
  (PAGE.fontSizes || []).forEach(function (size, index) {
    var id = 's' + index;
    var radio = document.createElement('input');
    radio.type = 'radio';
    radio.name = 'font_size';
    radio.id = id;
    radio.value = size;
    radio.checked = Number(size) === Number(chosen);

    var label = document.createElement('label');
    label.setAttribute('for', id);
    label.style.fontSize = size + 'px';
    label.textContent = 'Aa';

    var note = document.createElement('span');
    note.style.fontSize = '11px';
    note.style.display = 'block';
    note.style.opacity = '0.7';
    note.textContent = size + 'pt';
    label.appendChild(note);

    host.appendChild(radio);
    host.appendChild(label);
  });
}

/* The grid is a hidden field and a row of buttons - POSITION_SCRIPT keeps
   them together after this. */
function fillWhere() {
  var wanted = PAGE.quadrant || 'top-right';
  var field = document.getElementById('quadrant');
  if (field) { field.value = wanted; }
  document.querySelectorAll('.where button[data-q]').forEach(function (b) {
    b.classList.toggle('on', b.dataset.q === wanted);
  });
}

fillForm();
drawChooser();
chooseNote(target);
fillWhere();


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
