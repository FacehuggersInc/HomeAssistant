var PAGE = window.PAGE || {};
var TOKEN = PAGE.token || '';

function auth(path, extra) {
  return path + '?token=' + encodeURIComponent(TOKEN) + (extra || '');
}

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

/* Every value below came out of a transcript of a room, so textContent
   throughout - see drawEvent above for the same reason. */
function drawClip(clip, ignoring) {
  var row = document.createElement('div');
  row.className = 'clip' + (ignoring ? ' ignoring' : '') +
                  (clip.suppressed ? ' was-ignored' : '');

  var top = document.createElement('div');
  top.className = 'top';
  var score = document.createElement('span');
  score.className = 'score';
  score.textContent = Number(clip.score).toFixed(2);
  top.appendChild(score);

  var tag = document.createElement('span');
  tag.className = 'tag' + (ignoring ? ' on' : '');
  tag.textContent = ignoring ? 'ignoring sounds like this'
                             : (clip.suppressed ? 'was ignored' : 'woke it');
  top.appendChild(tag);

  var when = document.createElement('span');
  when.className = 'when';
  when.textContent = new Date((clip.at || 0) * 1000)
    .toTimeString().slice(0, 8);
  top.appendChild(when);
  row.appendChild(top);

  var player = document.createElement('audio');
  player.controls = true;
  player.preload = 'none';
  player.src = '/wake/clip/' + encodeURIComponent(clip.key) +
               '?token=' + encodeURIComponent(TOKEN);
  row.appendChild(player);

  var said = document.createElement('div');
  said.className = 'said';
  if (clip.transcript) {
    said.textContent = '\u201c' + clip.transcript + '\u201d';
  } else {
    var em = document.createElement('em');
    em.textContent = 'nothing transcribable';
    said.appendChild(em);
  }
  row.appendChild(said);

  var facts = document.createElement('div');
  facts.className = 'facts';
  var bits = ['bar ' + Number(clip.bar || 0).toFixed(2)];
  if (clip.outcome) { bits.push('ended as ' + clip.outcome); }
  /* Shown for every clip, not only the ones that matched. The spread of
     near misses against real hits is the only way to tell whether the
     similarity setting is anywhere near right. */
  bits.push('closest known sound ' + Number(clip.similarity || 0).toFixed(3));
  facts.textContent = bits.join('  \u00b7  ');
  row.appendChild(facts);

  var actions = document.createElement('div');
  actions.className = 'row';
  var button = document.createElement('button');
  button.type = 'button';
  button.className = 'btn';
  button.textContent = ignoring ? 'Stop ignoring this' : 'That was not me';
  button.addEventListener('click', function () {
    button.disabled = true;
    remember(ignoring ? 'forget' : 'ignore', clip.key);
  });
  actions.appendChild(button);
  row.appendChild(actions);
  return row;
}

function remember(what, key) {
  /* forget-all takes no key, and a trailing slash is a different route that
     does not exist. */
  var where = key ? '/wake/' + what + '/' + encodeURIComponent(key)
                  : '/wake/' + what;
  fetch(auth(where))
    .then(function (r) { return r.json(); })
    .then(function (d) {
      say(d.reason || 'Done.', d.request !== 'Success');
      /* The speech process writes the file, so reading it straight back
         races the write. */
      setTimeout(clips, 600);
    })
    .catch(function (e) { say('Could not reach the panel: ' + e, true); });
}

function clips() {
  fetch(auth('/wake/clips'))
    .then(function (r) { return r.json(); })
    .then(function (d) {
      var host = document.getElementById('clips');
      var hint = document.getElementById('clips-hint');
      host.innerHTML = '';
      if (d.request !== 'Success') {
        hint.textContent = d.reason || 'Could not read the clips.';
        return;
      }
      var ignoring = {};
      (d.ignored || []).forEach(function (c) { ignoring[c.key] = true; });
      var list = d.clips || [];
      document.getElementById('forget-all-row').style.display =
        (d.ignored || []).length ? 'flex' : 'none';
      if (!list.length) {
        hint.textContent = 'Nothing has woken it yet. Clips appear here once ' +
                           'it does, up to the last ten.';
        return;
      }
      hint.textContent = list.length + ' kept, ' +
                         (d.ignored || []).length + ' being ignored.';
      list.forEach(function (clip) {
        host.appendChild(drawClip(clip, !!ignoring[clip.key]));
      });
    })
    .catch(function (e) {
      document.getElementById('clips-hint').textContent =
        'Could not reach the panel: ' + e;
    });
}

document.getElementById('forget-all').addEventListener('click', function () {
  remember('forget-all', '');
});

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
  clips();
  if (!poll) { poll = setInterval(load, 5000); }
}

document.addEventListener('visibilitychange', watch);
watch();
