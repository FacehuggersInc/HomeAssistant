/* Everything the panel had to say arrives as one object - see
   docs/web-ui.md. The tile grid, the dropdowns and every remembered answer
   come from here rather than being interpolated into the markup. */
var PAGE = window.PAGE || {};

function fillActions() {
  var action = PAGE.endpoint + '?token=' +
               encodeURIComponent(PAGE.token || '');
  document.querySelectorAll('form[data-action]').forEach(function (f) {
    f.action = action;
  });
  var manage = document.getElementById('manage');
  if (manage) {
    manage.href = '/upload/stickers?token=' +
                  encodeURIComponent(PAGE.token || '');
  }
  var count = document.getElementById('count');
  if (count) {
    count.textContent = (PAGE.stickers || []).length + ' in your library';
  }
}

/* Tiles are built as elements rather than as a string of markup, so a
   sticker's own name is text and can never be read as a tag. */
function fillGridTiles() {
  var host = document.getElementById('grid');
  if (!host) { return; }
  var stickers = PAGE.stickers || [];
  host.innerHTML = '';
  if (!stickers.length) {
    var none = document.createElement('div');
    none.className = 'empty';
    none.textContent = 'Nothing here yet. Upload something above.';
    host.appendChild(none);
    return;
  }
  stickers.forEach(function (sticker) {
    var tile = document.createElement('div');
    tile.className = 'tile';
    tile.dataset.name = sticker.name;
    tile.dataset.label = sticker.label;

    if (sticker.kind === 'video') {
      /* No poster frame without a decoder, so it is named rather than shown
         - a tile drawn as nothing reads as broken. */
      var vid = document.createElement('div');
      vid.className = 'vid';
      vid.textContent = sticker.label;
      vid.appendChild(document.createElement('br'));
      vid.appendChild(document.createTextNode('(video)'));
      tile.appendChild(vid);
    } else {
      var img = document.createElement('img');
      img.src = sticker.src;
      img.alt = sticker.label;
      img.loading = 'lazy';
      tile.appendChild(img);
    }

    var name = document.createElement('div');
    name.className = 'nm';
    name.textContent = sticker.label;
    tile.appendChild(name);
    host.appendChild(tile);
  });
}

function fillChoices(id, choices, chosen) {
  var pick = document.getElementById(id);
  if (!pick) { return; }
  pick.innerHTML = '';
  (choices || []).forEach(function (entry) {
    var option = document.createElement('option');
    option.value = entry[0];
    option.textContent = entry[1];
    if (String(entry[0]) === String(chosen)) { option.selected = true; }
    pick.appendChild(option);
  });
  pick.value = chosen;
}

function fillFields() {
  var form = PAGE.form || {};
  document.getElementById('sticker').value = form.sticker || '';
  document.getElementById('timeout').value = form.timeout || '300';
  document.getElementById('size').value = form.size || '180';
  document.getElementById('delete_after').checked = !!form.delete_after;

  fillChoices('mode', PAGE.modes, form.mode || 'permanent');
  fillChoices('scale', PAGE.scales, form.scale || 'normal');

  document.getElementById('timeoutRow').style.display =
    form.mode === 'temporary' ? 'block' : 'none';
  var custom = form.scale === 'custom';
  document.getElementById('sizeRow').style.display = custom ? 'block' : 'none';
  document.getElementById('size').disabled = !custom;

  var go = document.getElementById('go');
  if (form.sticker) {
    go.disabled = false;
    go.textContent = 'Place "' + (PAGE.chosenLabel || '') + '"';
  } else {
    go.disabled = true;
    go.textContent = 'Choose a sticker first';
  }
}

/* The grid is a hidden field and a row of buttons - POSITION_SCRIPT keeps
   them together after this. */
function fillWhere() {
  var wanted = PAGE.quadrant || 'center';
  var field = document.getElementById('quadrant');
  if (field) { field.value = wanted; }
  document.querySelectorAll('.where button[data-q]').forEach(function (b) {
    b.classList.toggle('on', b.dataset.q === wanted);
  });
}

fillActions();
fillGridTiles();
fillFields();
fillWhere();

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
  /* Matched by comparing values rather than by building an attribute
     selector out of a filename. A name with a quote in it had to be escaped
     into the selector, which is one more escaping layer than this needs. */
  document.querySelectorAll('#grid .tile').forEach(function (t) {
    if (t.dataset.name === chosen) { t.classList.add('sel'); }
  });
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
