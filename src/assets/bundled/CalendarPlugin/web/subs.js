/* One object from the panel - see docs/web-ui.md. */
var PAGE  = window.PAGE || {};
var token = PAGE.token || '';

function fillPeople() {
  var pick = document.getElementById('user');
  if (!pick) { return; }
  pick.innerHTML = '';
  var names = PAGE.people || [];
  if (!names.length) {
    var none = document.createElement('option');
    none.value = '';
    none.textContent = 'Nobody named yet';
    pick.appendChild(none);
    return;
  }
  names.forEach(function (name) {
    var option = document.createElement('option');
    option.value = name;
    option.textContent = name;
    pick.appendChild(option);
  });
}

/* The subscriptions, built as elements. A calendar's name and the error text
   from a feed both come from outside the panel, and one of them is a string
   a stranger's server chose. */
function fillRows() {
  var host = document.getElementById('rows');
  if (!host) { return; }
  host.innerHTML = '';
  var subs = PAGE.subscriptions || [];
  if (!subs.length) {
    var none = document.createElement('li');
    var box = document.createElement('div');
    var span = document.createElement('span');
    span.textContent = 'Nothing subscribed yet.';
    box.appendChild(span);
    none.appendChild(box);
    host.appendChild(none);
    return;
  }
  subs.forEach(function (sub) {
    var row = document.createElement('li');
    var box = document.createElement('div');

    var name = document.createElement('b');
    name.textContent = sub.name;
    box.appendChild(name);

    var owner = document.createElement('span');
    owner.textContent = sub.owner;
    box.appendChild(owner);

    var state = document.createElement('span');
    if (sub.error) {
      state.className = 'bad';
      state.textContent = sub.error;
    } else if (sub.count === 0) {
      /* Synced, and found nothing. Marked rather than reported plainly:
         a feed that works and yields nothing looks exactly like one that
         is broken until you know which it is. */
      state.className = 'bad';
      state.textContent = 'no events - synced ' + sub.synced;
    } else if (typeof sub.count === 'number' && sub.count > 0) {
      state.textContent = sub.count + ' event' + (sub.count === 1 ? '' : 's') +
                          ', synced ' + sub.synced;
    } else {
      state.textContent = 'not synced yet';
    }
    box.appendChild(state);

    /* Whether the address is one anybody holding it could read the calendar
       with. Worth saying on a page that lists them. */
    var kind = document.createElement('span');
    kind.className = 'kind';
    kind.textContent = sub.secret ? 'secret address' : 'public address';
    box.appendChild(kind);

    var drop = document.createElement('button');
    drop.dataset.remove = sub.key;
    drop.textContent = 'Remove';
    row.appendChild(box);
    row.appendChild(drop);
    host.appendChild(row);
  });
}

fillPeople();
fillRows();
function post(params) {
  params.append('token', token);
  fetch(PAGE.endpoint + '?' + params.toString(), {method: 'POST'})
    /* Reloaded rather than patched: the list is rendered by the panel, and
       rebuilding it here would be a second copy of that logic. */
    .then(function () {
      location.href = PAGE.endpoint + '?token=' +
                      encodeURIComponent(token);
    })
    .catch(function () { alert('Could not reach the panel.'); });
}
document.getElementById('f').addEventListener('submit', function (e) {
  e.preventDefault();
  var params = new URLSearchParams();
  new FormData(e.target).forEach(function (v, k) {
    if (v) { params.append(k, v); }
  });
  post(params);
});
document.querySelectorAll('button[data-remove]').forEach(function (b) {
  b.addEventListener('click', function () {
    if (!confirm('Remove this calendar and its events?')) { return; }
    post(new URLSearchParams({remove: b.dataset.remove}));
  });
});
try {
  var saved = localStorage.getItem('ha-user');
  if (saved) { document.getElementById('user').value = saved; }
  document.getElementById('user').addEventListener('change', function (e) {
    localStorage.setItem('ha-user', e.target.value);
  });
} catch (e) {}
