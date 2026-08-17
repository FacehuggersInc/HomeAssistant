/* Everything the panel had to say arrives as one object - see
   docs/web-ui.md. The presets, the running list, the field values and the
   form's action all come from here rather than being interpolated into the
   markup. */
var PAGE = window.PAGE || {};

function fillForm() {
  var form = document.getElementById('timerform');
  if (form) {
    form.action = PAGE.endpoint + '?token=' +
                  encodeURIComponent(PAGE.token || '');
  }
  var values = PAGE.form || {};
  ['hours', 'minutes', 'seconds', 'name'].forEach(function (key) {
    var field = document.getElementById(key);
    if (field && values[key] !== undefined) { field.value = values[key]; }
  });
}

function fillPresets() {
  var host = document.getElementById('presets');
  if (!host) { return; }
  host.innerHTML = (PAGE.presets || []).map(function (minutes) {
    return '<button type="button" data-min="' + minutes + '">' +
           minutes + 'm</button>';
  }).join('');
}

/* Built here rather than written as markup, so a timer's name is text and
   can never be read as a tag. */
function fillRunning() {
  var host = document.getElementById('running');
  if (!host) { return; }
  var timers = PAGE.running || [];
  host.innerHTML = '';
  if (!timers.length) {
    var none = document.createElement('li');
    none.className = 'empty';
    none.textContent = 'Nothing running.';
    host.appendChild(none);
    return;
  }
  timers.forEach(function (timer) {
    var row = document.createElement('li');
    var name = document.createElement('b');
    name.textContent = timer.name || 'Timer';
    var left = document.createElement('span');
    left.textContent = timer.left + ' left';
    row.appendChild(name);
    row.appendChild(left);
    host.appendChild(row);
  });
}

/* The grid is a hidden field and a row of buttons, so the choice is set in
   both - POSITION_SCRIPT keeps them together after this. Blank is a real
   answer: a timer with nowhere named goes wherever there is room. */
function fillGrid() {
  var wanted = PAGE.quadrant || '';
  var field = document.getElementById('quadrant');
  if (field) { field.value = wanted; }
  document.querySelectorAll('.where button[data-q]').forEach(function (b) {
    b.classList.toggle('on', b.dataset.q === wanted);
  });
}

fillForm();
fillPresets();
fillRunning();
fillGrid();

/* A preset fills the fields rather than submitting, so it can be adjusted
   before it starts - which is the whole reason this is a page and not a
   button that fired a fixed five minutes. */
document.querySelectorAll('#presets button').forEach(function (b) {
  b.addEventListener('click', function (e) {
    e.preventDefault();
    document.querySelectorAll('#presets button').forEach(function (o) {
      o.classList.remove('on');
    });
    b.classList.add('on');
    var m = parseInt(b.dataset.min, 10);
    document.getElementById('hours').value = Math.floor(m / 60);
    document.getElementById('minutes').value = m % 60;
    document.getElementById('seconds').value = 0;
  });
});


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
