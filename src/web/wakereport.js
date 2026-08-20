var PAGE = window.PAGE || {};
var TOKEN = PAGE.token || '';

function say(message, bad) {
  var note = document.getElementById('note');
  if (!note) { return; }
  note.textContent = message;
  note.className = bad ? 'banner bad' : 'banner';
  note.style.display = 'block';
}

/* textContent throughout. Everything below came out of a transcript of a
   room, which is somebody else's words by definition. */
function field(host, key, value) {
  var row = document.createElement('div');
  row.className = 'field';
  var k = document.createElement('span');
  k.className = 'k';
  k.textContent = key;
  var v = document.createElement('span');
  v.className = 'v';
  v.textContent = value;
  row.appendChild(k);
  row.appendChild(v);
  host.appendChild(row);
}

function fill(id, values, order) {
  var host = document.getElementById(id);
  host.innerHTML = '';
  var keys = order || Object.keys(values || {});
  var any = false;
  keys.forEach(function (key) {
    if (values && values[key] !== undefined && key !== 'final') {
      field(host, key, String(values[key]));
      any = true;
    }
  });
  if (!any) {
    var empty = document.createElement('p');
    empty.className = 'hint';
    empty.textContent = 'Nothing recorded yet.';
    host.appendChild(empty);
  }
}

function drawEvent(event) {
  var row = document.createElement('div');
  row.className = 'event ' + (event.kind === 'woke' ? 'woke' : 'near');

  var top = document.createElement('div');
  top.className = 'top';
  var score = document.createElement('span');
  score.className = 'score';
  score.textContent = Number(event.score).toFixed(2);
  var what = document.createElement('span');
  what.className = 'what';
  what.textContent = event.kind === 'woke' ? 'woke' : 'near miss';
  var when = document.createElement('span');
  when.className = 'when';
  when.textContent = event.at || '';
  top.appendChild(score);
  top.appendChild(what);
  top.appendChild(when);
  row.appendChild(top);

  var bar = document.createElement('div');
  bar.className = 'bar';
  bar.textContent = event.kind === 'woke'
    ? 'bar ' + Number(event.bar).toFixed(2)
    : 'bar ' + Number(event.bar).toFixed(2) + ', short by ' +
      Number(event.short || 0).toFixed(2);
  row.appendChild(bar);

  var said = document.createElement('div');
  said.className = 'said';
  if (event.said) {
    said.textContent = '\u201c' + event.said + '\u201d';
  } else {
    var em = document.createElement('em');
    /* Not the same as an empty transcript. One means the model ran and heard
       nothing; the other means it was never asked. */
    em.textContent = event.kind === 'woke'
      ? 'nothing transcribable - the wake was not speech'
      : 'not transcribed';
    said.appendChild(em);
  }
  row.appendChild(said);
  return row;
}

function draw(report) {
  document.getElementById('says').textContent = report.verdict || '';

  var session = report.session || {};
  fill('device', session.device,
       ['device', 'channels', 'rate', 'native rate', 'processing']);
  fill('settings', session.settings);
  fill('summary', session.summary,
       ['for', 'woke', 'near misses', 'noise floor', 'peak scores']);

  var channels = document.getElementById('channels');
  if (session.note) {
    channels.textContent = session.note;
    channels.style.display = 'block';
  } else {
    channels.style.display = 'none';
  }

  document.getElementById('partial').style.display =
    session.partial ? 'block' : 'none';

  var host = document.getElementById('events');
  host.innerHTML = '';
  var events = session.events || [];
  if (!events.length) {
    var empty = document.createElement('p');
    empty.className = 'hint';
    empty.textContent = session.found
      ? 'Nothing has woken it and nothing has come close yet.'
      : 'No report yet. It is written while the assistant runs.';
    host.appendChild(empty);
    return;
  }
  events.forEach(function (event) { host.appendChild(drawEvent(event)); });
}

function load() {
  fetch('/wake/report?token=' + encodeURIComponent(TOKEN))
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (d.request !== 'Success') {
        say(d.reason || 'Could not read the report.', true);
        return;
      }
      draw(d);
    })
    .catch(function (e) { say('Could not reach the panel: ' + e, true); });
}

document.getElementById('download').href =
  '/logs/wake?token=' + encodeURIComponent(TOKEN);

/* Polled while the page is open, because the useful way to read this is to
   stand in the room saying the wake word and watch what lands. Slowly: a
   near miss takes a transcription to appear, and nothing here is worth a
   request a second. */
var poll = null;

function watch() {
  if (document.hidden) {
    if (poll) { clearInterval(poll); poll = null; }
    return;
  }
  load();
  if (!poll) { poll = setInterval(load, 5000); }
}

document.addEventListener('visibilitychange', watch);
watch();
