var PAGE = window.PAGE || {};
var TOKEN = PAGE.token || '';

function say(message, bad) {
  var note = document.getElementById('note');
  if (!note) { return; }
  note.textContent = message;
  note.className = bad ? 'banner bad' : 'banner';
  note.style.display = 'block';
}

function auth(path, extra) {
  return path + '?token=' + encodeURIComponent(TOKEN) + (extra || '');
}

/* -- what the panel is showing --------------------------------------------- */

function drawState(state) {
  document.body.classList.remove('elsewhere');
  document.getElementById('title').textContent =
    state.title || state.url || 'A blank page';
  document.getElementById('addr').textContent = state.url || '';
  document.getElementById('now').className = 'now' +
    (state.bookmarked ? ' saved' : '');

  var keys = document.getElementById('keys');
  keys.querySelector('[data-cmd="back"]').disabled = !state.can_back;
  keys.querySelector('[data-cmd="forward"]').disabled = !state.can_forward;
  var star = keys.querySelector('[data-cmd="bookmark"]');
  star.className = 'key' + (state.bookmarked ? ' on' : '');
  document.getElementById('k-star').innerHTML =
    state.bookmarked ? '&#9733;' : '&#9734;';

  /* A locked page refuses a typed address at the engine, and the refusal is a
     notification on the panel - which is the one screen whoever is holding
     this is not looking at. Say it here instead of letting them find out. */
  var field = document.getElementById('url');
  field.disabled = !!state.lock_address;
  field.placeholder = state.lock_address
    ? 'The panel\u2019s address bar is locked'
    : (state.lock_base ? state.lock_base + '\u2026' : 'https://example.com');
}

function drawElsewhere(reason) {
  document.body.classList.add('elsewhere');
  document.getElementById('title').textContent =
    reason || 'The panel is not on the browser.';
  document.getElementById('addr').textContent = '';
  document.getElementById('now').className = 'now';
}

function state() {
  return fetch(auth('/browser/state'))
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (d.request === 'Success') { drawState(d); }
      else { drawElsewhere(d.reason); }
    })
    .catch(function () { drawElsewhere('Could not reach the panel.'); });
}

/* -- the controls ---------------------------------------------------------- */

function press(command) {
  fetch(auth('/browser/' + encodeURIComponent(command)))
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (d.request !== 'Success') {
        say(d.reason || 'The panel refused that.', true);
        if (d.reason) { drawElsewhere(d.reason); }
        return;
      }
      /* `moved` false is a success that did nothing - an empty history, a
         page that cannot be saved. Said plainly rather than as a failure. */
      if (!d.moved) { say(d.reason || 'Nothing to do.'); }
      else if (d.result === 'bookmarked') { say('Saved to bookmarks.'); marks(); }
      else if (d.result === 'unbookmarked') { say('Removed from bookmarks.'); marks(); }
      else { say('Done.'); }
      state();
    })
    .catch(function (e) { say('Could not reach the panel: ' + e, true); });
}

Array.prototype.forEach.call(document.querySelectorAll('.key'),
  function (button) {
    button.addEventListener('click', function () {
      press(button.getAttribute('data-cmd'));
    });
  });

/* -- sending it somewhere -------------------------------------------------- */

function openOnPanel(address) {
  /* The '#' has to be percent-encoded or the server never sees what follows
     it - a fragment is not sent. */
  fetch(auth('/goto/' + encodeURIComponent('#webpage'),
             '&url=' + encodeURIComponent(address)))
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (d.request !== 'Success') {
        say(d.reason || 'Could not open that.', true);
        return;
      }
      say('Opening ' + address + ' on the panel.');
      /* The page is built and the load started after this answers, so a state
         read now would describe the page being replaced. */
      setTimeout(state, 700);
    })
    .catch(function (e) { say('Could not reach the panel: ' + e, true); });
}

document.getElementById('openurl').addEventListener('click', function () {
  var value = document.getElementById('url').value.trim();
  if (!value) { say('Type an address first.', true); return; }
  if (!/^https?:\/\//i.test(value)) { value = 'https://' + value; }
  openOnPanel(value);
});

/* -- bookmarks ------------------------------------------------------------- */

function forget(url) {
  /* Not authed, the same as the panel's own home page - one route for
     forgetting means the two pages cannot disagree about what it does. */
  fetch('/bookmark/forget?url=' + encodeURIComponent(url))
    .then(function (r) { return r.json(); })
    .then(function (d) {
      var gone = d.request === 'Forgotten';
      say(gone ? 'Forgotten.' : (d.reason || 'It was not saved.'), !gone);
      marks();
      state();
    })
    .catch(function (e) { say('Could not reach the panel: ' + e, true); });
}

function drawMark(mark) {
  var row = document.createElement('div');
  row.className = 'mark';

  var open = document.createElement('button');
  open.type = 'button';
  open.className = 'open';

  if (mark.icon) {
    var img = document.createElement('img');
    img.src = '/bookmark-icon/' + encodeURIComponent(mark.icon);
    img.alt = '';
    open.appendChild(img);
  } else {
    var letter = document.createElement('span');
    letter.className = 'letter';
    /* textContent, not innerHTML. Every value on this row came out of a page
       title somebody else wrote. */
    letter.textContent = mark.initial || '?';
    open.appendChild(letter);
  }

  var what = document.createElement('div');
  what.className = 'what';
  var name = document.createElement('div');
  name.className = 'name';
  name.textContent = mark.label || mark.url;
  var host = document.createElement('div');
  host.className = 'host';
  host.textContent = mark.host || '';
  what.appendChild(name);
  what.appendChild(host);
  open.appendChild(what);
  open.addEventListener('click', function () { openOnPanel(mark.url); });

  var drop = document.createElement('button');
  drop.type = 'button';
  drop.className = 'drop';
  drop.title = 'Forget this bookmark';
  drop.innerHTML = '&times;';
  drop.addEventListener('click', function () { forget(mark.url); });

  row.appendChild(open);
  row.appendChild(drop);
  return row;
}

function marks() {
  fetch(auth('/bookmarks'))
    .then(function (r) { return r.json(); })
    .then(function (d) {
      var host = document.getElementById('marks');
      host.innerHTML = '';
      if (d.request !== 'Success') {
        say(d.reason || 'Could not list the bookmarks.', true);
        return;
      }
      if (!d.bookmarks.length) {
        var empty = document.createElement('p');
        empty.className = 'hint';
        empty.textContent = 'Nothing saved yet. Open a page and press ' +
                            'Bookmark above, or the star on the panel.';
        host.appendChild(empty);
        return;
      }
      d.bookmarks.forEach(function (mark) {
        host.appendChild(drawMark(mark));
      });
    })
    .catch(function (e) { say('Could not reach the panel: ' + e, true); });
}

/* The panel changes without this page being told, so what is drawn here goes
   stale on its own. Polled only while the page is being looked at - a phone
   in a pocket asking every few seconds is a request per person per day for
   nobody. */
var poll = null;

function watch() {
  if (document.hidden) {
    if (poll) { clearInterval(poll); poll = null; }
    return;
  }
  state();
  if (!poll) { poll = setInterval(state, 6000); }
}

document.addEventListener('visibilitychange', watch);
watch();
marks();
