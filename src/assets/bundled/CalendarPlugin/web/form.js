/* Everything the panel had to say arrives as one object - see
   docs/web-ui.md. The token used to be spliced into this file with a
   __TOKEN__ replace, which is a substitution step and one more thing that
   can go wrong quietly. */
var PAGE = window.PAGE || {};

/* Whoever the panel knows, as options. A free text field here meant "Chris",
   "chris" and "Chris " were three people who each owned some of the same
   events. Built as elements, so a name is text rather than markup. */
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

function fillUpcoming() {
  var host = document.getElementById('upcoming');
  if (!host) { return; }
  host.innerHTML = '';
  var events = PAGE.upcoming || [];
  if (!events.length) {
    var none = document.createElement('li');
    var span = document.createElement('span');
    span.textContent = 'Nothing coming up.';
    none.appendChild(span);
    host.appendChild(none);
    return;
  }
  events.forEach(function (event) {
    var row = document.createElement('li');
    var title = document.createElement('b');
    title.textContent = event.title;
    var when = document.createElement('span');
    when.textContent = event.when;
    row.appendChild(title);
    row.appendChild(when);
    host.appendChild(row);
  });
}

fillPeople();
fillUpcoming();

document.getElementById('day').valueAsDate = new Date();
/* Remembered, so a phone asks once rather than on every event. */
try {
  var saved = localStorage.getItem('ha-user');
  if (saved) { document.getElementById('user').value = saved; }
  document.getElementById('user').addEventListener('change', function (e) {
    localStorage.setItem('ha-user', e.target.value);
  });
} catch (e) {}
document.getElementById('f').addEventListener('submit', function (event) {
  event.preventDefault();
  var params = new URLSearchParams({token: PAGE.token || ''});
  new FormData(event.target).forEach(function (value, key) {
    if (value) { params.append(key, value); }
  });
  fetch(PAGE.addEndpoint + '?' + params.toString(), {method: 'POST'})
    .then(function (r) { return r.json(); })
    .then(function (body) {
      if (body.request !== 'Success') {
        alert(body.reason || 'Could not add that.');
        return;
      }
      document.getElementById('ok').style.display = 'block';
      /* Reloaded rather than patched in: the list below is rendered by the
         panel, and rebuilding it here would be a second copy of that logic. */
      setTimeout(function () { location.reload(); }, 700);
    })
    .catch(function () { alert('Could not reach the panel.'); });
});
