"""
The page that actually plays.

A local shell rather than youtube.com. The IFrame Player API is a documented
contract - `playVideo`, `pauseVideo`, `setVolume`, `onStateChange` - whereas
the site's own markup is generated class names that change without notice, so
anything driving it by clicking would break within the week.
"""

#Injected once the API is ready. Everything the plugin asks for goes through
#these, so the Python side never touches YouTube's own objects.
def build(origin: str) -> str:
    """The shell, told which origin it is being served from."""
    return SHELL.replace("__ORIGIN__", origin or "")


SHELL = """<!doctype html><html><head><meta charset="utf-8">
<style>html,body{margin:0;background:#000;overflow:hidden}
#player{width:640px;height:360px}</style></head>
<body><div id="player"></div>
<script src="https://www.youtube.com/iframe_api"></script>
<script>
// Everything the panel reads lives here, so one runJavaScript call gets the
// whole picture rather than one per field.
var HA = {
  ready: false, player: null, state: -1, queue: [], owners: [], index: -1,
  errors: [], exhausted: false, stalled: false, finished: false,
  meta: {title: "", author: "", id: "", art: ""}
};

function onYouTubeIframeAPIReady() {
  HA.player = new YT.Player('player', {
    height: '360', width: '640',
    playerVars: {
      controls: 0, disablekb: 1, modestbranding: 1, rel: 0,
      // The page is never shown, so nothing here should try to draw
      // annotations or an end screen over it.
      iv_load_policy: 3, fs: 0, playsinline: 1,
      // The embed checks where it is being shown. Without a real origin it
      // refuses everything - every video, including ones that embed
      // perfectly elsewhere.
      enablejsapi: 1, origin: "__ORIGIN__", widget_referrer: "__ORIGIN__"
    },
    events: {
      onReady: function () { HA.ready = true; },
      onStateChange: function (e) {
        HA.state = e.data;
        HA.refreshMeta();
        // Stops at the end. A panel that rolls on to whatever the search
        // returned next is a radio nobody asked for - somebody who wants
        // another song asks for one.
        if (e.data === YT.PlayerState.ENDED) {
          HA.exhausted = true;
          HA.finished = true;
          // Paused where it stopped rather than stopVideo(). stopVideo()
          // leaves the player cued at the beginning, and a cued player is
          // one gesture away from playing the whole thing again - which is
          // exactly what it did.
          try { HA.player.pauseVideo(); } catch (err) {}
        }
        // Anything that starts it again after it has finished did not come
        // from here. Held rather than trusted: this is the same page a
        // browser would use, and it has opinions about what to play next.
        if (e.data === YT.PlayerState.PLAYING && HA.finished) {
          try { HA.player.pauseVideo(); } catch (err) {}
        }
      },
      onError: function (e) {
        // Recorded and left. What to do about it is the panel's decision:
        // an embed refusal means this song can still be played, just not
        // here, and skipping to a different song would answer a question
        // nobody asked. Anything else moves on.
        HA.errors.push({code: e.data, id: HA.queue[HA.index] || HA.meta.id,
                        at: Date.now()});
        HA.stalled = true;
      }
    }
  });
}

HA.refreshMeta = function () {
  // Frozen once the track has finished. The embed puts up its own end screen
  // when a video runs out, and getVideoData() starts answering about
  // whatever that screen is offering next - so the card quietly changed to a
  // song nobody had asked for while the player sat there stopped. HA.at()
  // clears the flag, so the next real track updates it again.
  if (HA.finished) { return; }
  try {
    var d = HA.player.getVideoData ? HA.player.getVideoData() : {};
    HA.meta = {
      title:  d.title  || "",
      author: d.author || "",
      id:     d.video_id || "",
      art:    d.video_id ? ("https://i.ytimg.com/vi/" + d.video_id +
                            "/maxresdefault.jpg") : ""
    };
  } catch (err) {}
};

HA.snapshot = function () {
  if (!HA.ready || !HA.player) { return {ready: false}; }
  HA.refreshMeta();
  var duration = 0, position = 0, volume = 100;
  try { duration = HA.player.getDuration() || 0; } catch (e) {}
  try { position = HA.player.getCurrentTime() || 0; } catch (e) {}
  try { volume = HA.player.getVolume(); } catch (e) {}
  var errors = HA.errors;
  HA.errors = [];
  var stalled = HA.stalled;
  HA.stalled = false;
  return {
    ready: true, state: HA.state, duration: duration, position: position,
    volume: volume, title: HA.meta.title, author: HA.meta.author,
    id: HA.meta.id, art: HA.meta.art,
    index: HA.index, queued: HA.queue.length,
    // Drained on read, so each is reported once.
    errors: errors, exhausted: HA.exhausted, stalled: stalled,
    muted: HA.player.isMuted()
  };
};

HA.load = function (ids, startAt, owners) {
  HA.queue = ids || [];
  HA.owners = owners || [];
  HA.index = -1;
  HA.errors = [];
  HA.exhausted = false;
  HA.stalled = false;
  HA.finished = false;
  if (HA.queue.length) { HA.at(startAt || 0); }
};

// Unmuted and audible on purpose. A player that loads, plays and reports
// PLAYING while muted looks identical to one that is working, and is the
// hardest version of "no sound" to find.
HA.wake = function (volume) {
  if (!HA.ready) { return; }
  try { HA.player.unMute(); } catch (e) {}
  try { HA.player.setVolume(volume === undefined ? 100 : volume); } catch (e) {}
};

// Pressing play. Not playVideo() straight through, because after the end
// that hands the decision to the page - and the page has an end screen with
// its own idea of what comes next. What just finished is what plays again.
HA.play = function () {
  if (!HA.ready || !HA.player) { return false; }
  if (HA.finished) { return HA.at(HA.index); }
  try { HA.player.playVideo(); } catch (e) {}
  return true;
};

HA.at = function (i) {
  if (!HA.ready || i < 0 || i >= HA.queue.length) { return false; }
  HA.index = i;
  HA.finished = false;
  // No longer at the end, so the next time it reaches one is a new event.
  // Left set, the "reached the end of the queue" line is said once ever and
  // the panel has nothing to say about the second time.
  HA.exhausted = false;
  HA.player.loadVideoById(HA.queue[i]);
  return true;
};

// Moving on after a failure. `next` is the button; this is the recovery, and
// it prefers a different uploader - a channel that blocks embedding blocks
// every one of its videos, so the next result from the same one fails too.
HA.skip = function () {
  var from = HA.owners[HA.index];
  for (var i = HA.index + 1; i < HA.queue.length; i++) {
    if (!from || HA.owners[i] !== from) { return HA.at(i); }
  }
  return HA.next();
};

HA.next = function () {
  if (HA.index + 1 < HA.queue.length) { return HA.at(HA.index + 1); }
  // The end of the queue stops rather than looping, which would leave a
  // panel playing the same album until somebody noticed.
  try { HA.player.stopVideo(); } catch (e) {}
  HA.state = 0;
  HA.exhausted = true;
  return false;
};

HA.previous = function () {
  // Restart the track first, the way every other player does, and only step
  // back when it has barely started.
  try {
    if (HA.player.getCurrentTime() > 3) { HA.player.seekTo(0, true); return true; }
  } catch (e) {}
  return HA.index > 0 ? HA.at(HA.index - 1) : false;
};

HA.fade = function (to, ms) {
  // Stepped rather than jumped: a wake word cutting the music dead is more
  // startling than the music itself.
  if (!HA.ready) { return; }
  var from = 100;
  try { from = HA.player.getVolume(); } catch (e) { return; }
  var steps = Math.max(1, Math.round((ms || 250) / 25));
  var step = 0;
  if (HA.fading) { clearInterval(HA.fading); }
  HA.fading = setInterval(function () {
    step += 1;
    var v = from + (to - from) * (step / steps);
    try { HA.player.setVolume(Math.max(0, Math.min(100, Math.round(v)))); }
    catch (e) {}
    if (step >= steps) { clearInterval(HA.fading); HA.fading = null; }
  }, 25);
};
</script></body></html>"""
