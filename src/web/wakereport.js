var PAGE = window.PAGE || {};
var TOKEN = PAGE.token || '';

/* How many the Newest tab shows. The whole list is kept for reading a
   pattern out of an evening; this is the last few minutes, which is what
   somebody standing in the room saying the wake word is watching. */
var NEWEST = 10;

var state = { clips: [], ignored: [], tab: 'newest' };

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
    /* Opened rather than left folded away. Every score on this page is a
       score of whichever channel this line names, so a warning here is the
       thing to settle before reading any of the numbers above it. */
    document.getElementById('details').open = true;
  } else {
    channels.style.display = 'none';
  }

  document.getElementById('partial').style.display =
    session.partial ? 'block' : 'none';
}

/* Every value below came out of a transcript of a room, so textContent
   throughout - see field() above for the same reason. */
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

  /* The bar sits against the score rather than on a line of its own. A
     score means nothing without it: 0.45 and 0.02 against the same bar are
     completely different faults. */
  var bar = document.createElement('span');
  bar.className = 'bar';
  bar.textContent = 'of ' + Number(clip.bar || 0).toFixed(2);
  top.appendChild(bar);

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

  var bits = [];
  if (clip.outcome) { bits.push('ended as ' + clip.outcome); }
  /* The similarity is a tuning number, so it appears where it is the reason
     for something rather than on every row. On a suppressed clip it is why
     nothing happened; on an ignore entry it is what the rule is worth. */
  if (ignoring || clip.suppressed) {
    bits.push('closest known sound ' +
              Number(clip.similarity || 0).toFixed(3));
  }
  if (bits.length) {
    var facts = document.createElement('div');
    facts.className = 'facts';
    facts.textContent = bits.join('  \u00b7  ');
    row.appendChild(facts);
  }

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

/* -- the tabs */

function counts() {
  document.querySelectorAll('#tabs .tab').forEach(function (tab) {
    var name = tab.getAttribute('data-tab');
    var total = name === 'ignored' ? state.ignored.length
              : name === 'all' ? state.clips.length
              : Math.min(NEWEST, state.clips.length);
    tab.textContent = tab.getAttribute('data-label') + ' (' + total + ')';
    tab.classList.toggle('on', name === state.tab);
  });
}

function shown() {
  if (state.tab === 'ignored') { return state.ignored; }
  if (state.tab === 'all') { return state.clips; }
  return state.clips.slice(0, NEWEST);
}

function empty() {
  if (state.tab === 'ignored') {
    return 'Nothing is being ignored. Anything you mark as not you appears ' +
           'here, and can be undone from the same place.';
  }
  /* No figure for the cap. It lives in `MAX_CLIPS`, and a copy of it here
     would be a number that goes wrong quietly the next time that one moves. */
  return 'Nothing has woken it yet. Clips appear here once it does, oldest ' +
         'dropping off as new ones arrive.';
}

function paint() {
  var host = document.getElementById('clips');
  var hint = document.getElementById('clips-hint');
  host.innerHTML = '';
  counts();

  document.getElementById('forget-all-row').style.display =
    (state.tab === 'ignored' && state.ignored.length) ? 'flex' : 'none';

  var list = shown();
  if (!list.length) {
    hint.textContent = empty();
    return;
  }

  /* Which entries carry a rule, by key. An ignore entry is a copy of the
     clip it was learned from, so the same sound is in both lists and has to
     read the same way in either tab. */
  var ignoring = {};
  state.ignored.forEach(function (c) { ignoring[c.key] = true; });

  hint.textContent = state.clips.length + ' kept, ' +
                     state.ignored.length + ' being ignored.';
  list.forEach(function (clip) {
    host.appendChild(drawClip(clip, !!ignoring[clip.key]));
  });
}

function clips() {
  fetch(auth('/wake/clips'))
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (d.request !== 'Success') {
        document.getElementById('clips-hint').textContent =
          d.reason || 'Could not read the clips.';
        return;
      }
      state.clips = d.clips || [];
      state.ignored = d.ignored || [];
      paint();
    })
    .catch(function (e) {
      document.getElementById('clips-hint').textContent =
        'Could not reach the panel: ' + e;
    });
}

document.querySelectorAll('#tabs .tab').forEach(function (tab) {
  tab.setAttribute('data-label', tab.textContent);
  tab.addEventListener('click', function () {
    state.tab = tab.getAttribute('data-tab');
    paint();
  });
});

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
   stand in the room saying the wake word and watch what lands. The clips are
   polled with it - they are the page, and a list that only moved on a reload
   would be the wrong half staying still. Slowly: a clip takes a
   transcription to fill in, and nothing here is worth a request a second. */
var poll = null;

function tick() { load(); clips(); }

function watch() {
  if (document.hidden) {
    if (poll) { clearInterval(poll); poll = null; }
    return;
  }
  tick();
  if (!poll) { poll = setInterval(tick, 5000); }
}

document.addEventListener('visibilitychange', watch);
watch();
