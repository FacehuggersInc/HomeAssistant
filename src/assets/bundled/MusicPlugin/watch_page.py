"""
Playing what the embed refuses.

An "owner does not allow embedding" restriction is exactly that - a restriction
on *embedding*. The same video plays perfectly on its own watch page, because a
browser visiting youtube.com is not an embed and nothing is being framed.

So when the IFrame player refuses a video, the hidden page is pointed at the
watch page instead and the HTML5 `<video>` element is driven directly. That
element is a web standard: `play`, `pause`, `volume`, `currentTime` and
`duration` mean the same thing on every page that has one, which is why this
does not depend on YouTube's own markup the way clicking buttons would.

The page is still never shown. It is the same hidden page, on a different URL.
"""

from __future__ import annotations


WATCH_URL = "https://www.youtube.com/watch?v={video_id}"

#Injected once the watch page has loaded. Everything the plugin asks for goes
#through these, so the Python side never touches YouTube's own objects.
CONTROLS = """
(function () {
  if (window.HAW) { return "already"; }

  window.HAW = {
    ready: false, errors: [], ended: false, finished: false,

    video: function () { return document.querySelector("video"); },

    // The page carries its own consent and "are you still there" overlays.
    // Dismissed by looking for a button that says so rather than by class
    // name, which changes constantly.
    dismiss: function () {
      var wanted = ["accept all", "accept the use", "i agree", "yes, i'm still",
                    "reject all", "no thanks", "dismiss", "skip ad"];
      var buttons = document.querySelectorAll("button, tp-yt-paper-button, yt-button-shape button");
      for (var i = 0; i < buttons.length; i++) {
        var label = (buttons[i].textContent || "").trim().toLowerCase();
        for (var j = 0; j < wanted.length; j++) {
          if (label.indexOf(wanted[j]) === 0) {
            try { buttons[i].click(); return wanted[j]; } catch (e) {}
          }
        }
      }
      return "";
    },

    // Whether what is playing right now is an advert rather than the song.
    //
    // The player marks itself, which is far more reliable than looking at the
    // DOM: during an advert the position and duration belong to the advert,
    // and reporting those would show a 15-second track with the wrong name.
    advert: function () {
      var player = document.getElementById("movie_player");
      if (player && player.classList) {
        if (player.classList.contains("ad-showing") ||
            player.classList.contains("ad-interrupting")) { return true; }
      }
      return !!document.querySelector(".ytp-ad-player-overlay, .ytp-ad-text");
    },

    // Pressed the moment it appears. An advert that can be skipped should be.
    skipAdvert: function () {
      var buttons = document.querySelectorAll(
        ".ytp-ad-skip-button, .ytp-ad-skip-button-modern, " +
        ".ytp-skip-ad-button, button[class*='skip']");
      for (var i = 0; i < buttons.length; i++) {
        try {
          if (buttons[i].offsetParent !== null) { buttons[i].click(); return true; }
        } catch (e) {}
      }
      return false;
    },

    snapshot: function () {
      var v = HAW.video();
      if (!v) { return {ready: false, waiting: true}; }
      HAW.ready = true;

      var advert = HAW.advert();
      if (advert) { HAW.skipAdvert(); }

      var title = "", author = "";
      try {
        var meta = document.querySelector('meta[name="title"]');
        title = meta ? meta.content : (document.title || "").replace(/ - YouTube$/, "");
        var link = document.querySelector('span[itemprop="author"] link[itemprop="name"]')
                || document.querySelector("ytd-channel-name a");
        author = link ? (link.content || link.textContent || "") : "";
      } catch (e) {}

      var errors = HAW.errors;
      HAW.errors = [];
      return {
        ready: true, advert: advert,
        // 1 playing, 2 paused, 0 ended - the same numbers the IFrame API
        // uses, so the Python side maps one set rather than two. During an
        // advert it says 3 (buffering), because the song has not started and
        // saying it is playing would be a lie.
        state: advert ? 3 : (v.ended ? 0 : (v.paused ? 2 : 1)),
        // The advert's own numbers, which are not this track's.
        duration: (!advert && isFinite(v.duration)) ? v.duration : 0,
        position: advert ? 0 : (v.currentTime || 0),
        volume: Math.round((v.volume || 0) * 100),
        muted: v.muted,
        title: (title || "").trim(),
        author: (author || "").trim(),
        errors: errors, exhausted: v.ended, watch: true
      };
    },

    // Clears the hold: an explicit press means play it again.
    play:  function () {
      HAW.finished = false;
      var v = HAW.video();
      if (v) { v.play(); }
    },
    pause: function () { var v = HAW.video(); if (v) { v.pause(); } },
    stop:  function () { var v = HAW.video(); if (v) { v.pause(); v.currentTime = 0; } },
    seek:  function (s) {
      HAW.finished = false;
      var v = HAW.video();
      if (v) { v.currentTime = s; }
    },

    volume: function (percent) {
      var v = HAW.video();
      if (!v) { return; }
      v.muted = false;
      v.volume = Math.max(0, Math.min(100, percent)) / 100;
    },

    fade: function (to, ms) {
      var v = HAW.video();
      if (!v) { return; }
      var from = (v.volume || 0) * 100;
      var steps = Math.max(1, Math.round((ms || 250) / 25));
      var step = 0;
      if (HAW.fading) { clearInterval(HAW.fading); }
      HAW.fading = setInterval(function () {
        step += 1;
        HAW.volume(from + (to - from) * (step / steps));
        if (step >= steps) { clearInterval(HAW.fading); HAW.fading = null; }
      }, 25);
    }
  };

  // Started as soon as there is something to start. A watch page loads its
  // player after the document, so the element may not exist yet.
  var waiting = setInterval(function () {
    HAW.dismiss();
    HAW.skipAdvert();
    var v = HAW.video();
    if (!v) { return; }
    clearInterval(waiting);
    v.addEventListener("error", function () {
      HAW.errors.push({code: 5, id: "", at: Date.now()});
    });
    v.addEventListener("ended", function () { HAW.finished = true; });
    // A watch page decides for itself what to play next, and autoplay is on
    // by default. Held down, because a panel that rolls into a recommendation
    // is a radio nobody asked for.
    v.addEventListener("play", function () {
      if (HAW.finished) { try { v.pause(); } catch (e) {} }
    });
    try { v.muted = false; v.play(); } catch (e) {}
  }, 400);

  return "installed";
})();
"""
