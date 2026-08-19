/* Everything the panel had to say arrives as one object - see
   docs/web-ui.md. The tile grid, the dropdowns and every remembered answer
   come from here rather than being interpolated into the markup. */
var PAGE = window.PAGE || {};
var Listing = window.Listing || null;

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
  /* No count here. The listing below reports one already, and it is the
     better of the two: it follows the search rather than being the size of
     the whole library regardless of what is on screen. */
}

/* How many tiles arrive at a time. The filter runs over the whole library
   either way - this only decides how much of the answer is on screen,
   because a phone scrolling four hundred images is a phone nobody waits for.

   Sixty at a time rather than two hundred, and drawn as the bottom comes
   into view rather than when a button is pressed. A batch is only ever
   sixty nodes, so growing costs the same whether it is the first or the
   twelfth. */
var BATCH = 60;
var drawn = 0;
var stopWatching = null;

function findQuery() {
  var box = document.getElementById('find');
  return box ? box.value : '';
}

function currentOrder() {
  var pick = document.getElementById('order');
  return pick ? pick.value : 'newest';
}

/* Tiles are built as elements rather than as a string of markup, so a
   sticker's own name is text and can never be read as a tag. */
/* From the top: a different filter or order is a different list. */
function fillGridTiles() {
  drawn = 0;
  if (stopWatching) { stopWatching(); stopWatching = null; }
  var host = document.getElementById('grid');
  if (host) { host.innerHTML = ''; }
  growGrid();
}

/* The next batch, appended. Nothing already on screen is touched. */
function growGrid() {
  var host = document.getElementById('grid');
  if (!host) { return; }
  var all = PAGE.stickers || [];

  if (!all.length) {
    var none = document.createElement('div');
    none.className = 'empty';
    none.textContent = 'Nothing here yet. Upload something above.';
    host.appendChild(none);
    document.getElementById('found').textContent = '';
    return;
  }

  /* Without the shared helper there is still a library to show. It is
     loaded ahead of this file, so this is the case where something went
     wrong fetching it - and a grid that draws everything unsorted is a
     working page, where a grid that throws is a blank one. */
  var result = Listing
    ? Listing.view(all, {query: findQuery(), sort: currentOrder(),
                         fields: ['label', 'name'], from: drawn,
                         window: BATCH})
    : {shown: all.slice(drawn, drawn + BATCH), drawn: drawn,
       found: all.length, total: all.length,
       hidden: Math.max(0, all.length - drawn - BATCH), filtered: false};
  drawn += result.shown.length;

  document.getElementById('found').textContent =
    Listing ? Listing.summary(result, 'sticker') : '';

  var marker = document.getElementById('more');
  if (marker) { marker.remove(); }

  result.shown.forEach(function (sticker) {
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

  /* A marker after the last tile. Scrolling towards it draws the next
     batch; pressing it does the same, for a browser with no observer and
     for anybody who would rather press something. */
  if (result.hidden) {
    var more = document.createElement('button');
    more.type = 'button';
    more.id = 'more';
    more.className = 'more';
    more.textContent = 'Show the other ' + result.hidden;
    more.addEventListener('click', growGrid);
    host.appendChild(more);
    if (stopWatching) { stopWatching(); }
    stopWatching = Listing.whenNear(more, growGrid);
  } else if (stopWatching) {
    stopWatching();
    stopWatching = null;
  }

  /* A sticker chosen and then filtered out of view is still chosen, and the
     button at the bottom still names it - so the selection is redrawn from
     what the form holds rather than assumed to survive. */
  var chosenNow = document.getElementById('sticker').value;
  if (chosenNow) {
    document.querySelectorAll('#grid .tile').forEach(function (t) {
      if (t.dataset.name === chosenNow) { t.classList.add('sel'); }
    });
  }
}

function wireFinder() {
  var box = document.getElementById('find');
  var pick = document.getElementById('order');
  if (box) {
    box.addEventListener('input', fillGridTiles);
  }
  if (pick) { pick.addEventListener('change', fillGridTiles); }
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

/* Selection is wired FIRST, and never inside a draw.
 *
 * A failure while drawing must not cost the page its controls. With the
 * listener attached after the first draw, one throw in there leaves a grid
 * that fills in only when something else redraws it AND a page where
 * pressing a sticker does nothing - two symptoms, one cause, and neither of
 * them pointing at the draw.
 *
 * One listener on the grid rather than one per tile: every tile is replaced
 * when the order or the filter changes and appended when the next batch
 * arrives, so a handler on a tile belongs to an element that is no longer on
 * the page. The grid outlives all of them. */
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

(function () {
  var grid = document.getElementById('grid');
  if (!grid) { return; }
  grid.addEventListener('click', function (ev) {
    var target = ev.target;
    /* The press lands on the image or the name, not on the tile itself. */
    var tile = target && target.closest ? target.closest('.tile') : null;
    if (tile && grid.contains(tile)) { mark(tile); }
  });
})();

fillActions();
wireFinder();
fillGridTiles();
fillFields();
fillWhere();

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
