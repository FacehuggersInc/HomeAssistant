# Music

Plays from a page nobody sees, and tells `client.PLAYER` what it is doing.

Nothing here paints. The now-playing widget reads the registry, so it has no
idea YouTube is involved and a second source would need no change to it.

## Picking a result

YouTube orders by popularity and recency. A festival recording of a song, by a
channel nobody asked for, routinely outranks the song - "OK GOODNIGHT - The
Bear @ Night Of The Prog 2024" over "The Bear by Okay Goodnight". Both carry
the title; only one is by the artist.

So results are scored before anything is queued:

|                                                    |            |
|----------------------------------------------------|------------|
| The title matches                                  | up to 1.0  |
| The artist matches, by channel **or** in the title | up to 1.4  |
| A "- Topic" channel                                | +0.6       |
| "official", "audio", "lyric"                       | +0.25      |
| "live", "cover", "remix", "reaction"...            | -0.8, once |

The artist is weighted highest because it is the part that decides it, and a
result whose TITLE carries the artist counts as well - that is how uploads are
named when the channel is not the artist.

Discouraged words are matched on **whole words**. Substring matching punished
"Live Forever" for containing "live" and "Mixtape" for containing "mix"; both
are the song rather than a video of one.

### Where a name breaks into words is not a difference

The single most common thing a speech engine gets wrong about a name is the
spacing. "okay good night" and "OK GOODNIGHT" are the same name - word by word
they are 0.62 similar, squashed together they are 0.92. Judging on the worse
reading of the same string is judging on the wrong one, so both are tried and
the better one counts.

The fuzzy score is then a curve rather than a flat multiplier:

| similar                    | old  | now      |
|----------------------------|------|----------|
| 0.92 (same name, respaced) | 0.64 | **0.85** |
| 0.75 (loosely alike)       | 0.52 | 0.55     |
| 0.60 (different)           | 0.42 | **0.27** |
| below 0.45                 | 0.32 | **0**    |

`ratio * 0.7` punished a perfectly good name as hard as a wrong one, and 0.64
was not enough to beat a stranger's upload carrying the same title. The curve
is kinder to real matches and stricter on noise at the same time - unrelated
names routinely score 0.3-0.4 against each other simply for sharing letters,
and those are now zero.

**Sorted, never filtered.** A low score is a guess about relevance, and the
queue behind the first result is the fallback for getting it wrong - somebody
who gets the wrong song presses next, which only works if the rest are still
there.

When the artist had to be dropped to find anything at all, results are scored
on the title alone: it was probably misheard, and ranking on a wrong name is
worse than not ranking on one.

## Two sources

**System audio** is the default. Every Linux media player worth having speaks
MPRIS over D-Bus — Spotify, VLC, Firefox, mpv — so the widget shows whatever
the machine is already playing without any of them knowing this panel exists.

`playerctl` is used when installed, since it already solves picking between
several players; failing that `busctl` is queried directly, and that ships with
systemd. A player that is **playing** is preferred over one that is merely
open, so a paused browser tab does not outrank music.

**YouTube takes over** the moment something is played here, and hands back when
it stops. Handing back only happens from a genuinely stopped state — otherwise
a paused Spotify would show over music that is still going.

The system is polled only while it is the active source: reading MPRIS starts a
subprocess, and doing that while this plugin plays its own music would be work
for an answer nobody reads.

## The hidden page

A `QWebEnginePage` with **no view attached** loads and runs scripts exactly as
a visible one does — it simply never paints. A page rather than a hidden
`QWebEngineView`, because a view is a widget and would want a parent, a layout
and a size, none of which mean anything here.

Three things it has to be told:

* `PlaybackRequiresUserGesture = False`, or nothing ever starts — Chromium
  wants a click before audio, and there is nothing to click.
* A real **origin**. The shell is **served over HTTP** by the panel's own
  backend at `/public/music_shell` and fetched from
  `http://127.0.0.1:<port>`, rather than handed to the page as a string.
  A document set with `setHtml` has no real origin, the embed checks one, and
  without it *every* video is refused with error **152** — including ones that
  embed perfectly elsewhere. The origin is also passed to the player as
  `origin` and `widget_referrer`.

  The endpoint is unauthenticated: the only thing that fetches it is this
  panel's own hidden page, which has no token, and it is a static shell with
  nothing in it worth protecting.
* Its own **off-the-record profile**. A panel in a shared house should not be
  leaving cookies or history behind.

The page is parented to the **profile**, not to the player. A profile must
outlive every page using it, and Qt destroys children in the order they were
added — so with both parented to the player the profile would go first, while
the page was still alive. A parent destroys its children before itself, which
makes the ordering guaranteed rather than incidental.

## The shell, not the site

`shell.py` is a local page that loads the **IFrame Player API**. Every command
is a documented call — `playVideo`, `pauseVideo`, `setVolume`, `seekTo`,
`onStateChange`. Nothing reads YouTube's own markup, which is generated class
names that change without notice.

**A track that ends stops, and stays stopped.** It is *paused* where it
finished rather than stopped — `stopVideo()` leaves the player cued at the
beginning, which is one gesture away from playing the whole thing again, and
that is exactly what it did. Anything that starts it again afterwards is paused
straight back: this is the same page a browser would use and it has opinions
about what to play next. An explicit press clears the hold.

**A track the embed refuses is played anyway**, on its own watch page. See
below.

**A track that fails for any other reason moves on** — the alternatives are why
the queue exists.

A request that arrives before the API has finished loading is **held**, not
dropped, so a spoken request in the first seconds after boot still plays.

## When the embed refuses

Error **101** or **150** is a restriction on *embedding*, which is not the same
as unplayable. The same video plays perfectly on its own watch page, because a
browser visiting youtube.com is not an embed and nothing is being framed.

So the hidden page is pointed at `youtube.com/watch?v=<id>` and the HTML5
`<video>` element is driven directly. That element is a web standard — `play`,
`pause`, `volume`, `currentTime` and `duration` mean the same thing on every
page that has one — so this does not depend on YouTube's markup the way
clicking its buttons would. Consent and "are you still there" overlays are
dismissed by what a button **says** rather than what it is called.

**It plays the song that was asked for.** Skipping to a different song because
one uploader dislikes frames answers a question nobody asked.

### Adverts

A watch page serves adverts, and the `<video>` element plays them like anything
else. So the player's own `ad-showing` marker is checked — more reliable than
reading the DOM — and while an advert is up:

* a skip button is pressed the moment it appears;
* the state is reported as **buffering**, not playing, because the song has not
  started;
* the advert's position and length are **not** reported as the track's, or the
  card would show a 15-second song with the wrong name;
* the card says *Advert* rather than putting a brand where the title goes.

Logged once per advert rather than once per second.

The page is still never shown; it is the same hidden page on a different URL.
Every command checks which page is loaded, and a new request reloads the shell
and replays the queue into it.

Error **152** is deliberately not in this list: that one is the panel's own
origin being wrong, and a watch page would not fix it.

## Finding something to play

The IFrame API plays an ID; it cannot find one. `search.py` tries two sources:

|                          | Needs              | Costs                                                                 |
|--------------------------|--------------------|-----------------------------------------------------------------------|
| **Data API**             | A key in `SECRETS` | 100 of 10,000 daily units per search — about a hundred searches a day |
| **YouTube results page** | Nothing            | Breaks when YouTube changes its markup                                |
| **YouTube Music**        | Nothing            | The same, and where the translated titles are                         |

The key is tried **twice** — once asking only for embeddable, syndicated
videos, then plain. A refusal is usually one of the optional filters rather
than the key, and giving up on the API entirely would drop to scraping for the
rest of the session. Only then does it fall through to the results page, so an
exhausted quota does not mean the music stops working for the day.

A failure reports **what the API objected to**, not just the status: the body
names the parameter and the reason, and `400 Bad Request` on its own gives
nothing to act on. Both run on a worker: this arrives from a spoken request, and a
network round trip on the UI thread would freeze the panel mid-sentence.

Searches ask for `videoEmbeddable=true` — a result that cannot be embedded
loads, errors, and gets skipped, which looks like a broken queue. They do
**not** ask for the Music category: a great deal of music is not filed under
it, and the restriction loses more than it saves.

### The query

The word **"by" is dropped** before searching: an engine treats it as a term to
match and it appears in a great many unrelated titles. Both halves are kept,
because the artist is the strongest signal there is — a title alone returns
covers, live versions and anything sharing a common word.

The **last** "by" splits it, so *"Death by Glamour by Toby Fox"* is a title and
an artist rather than two artists.

### Ranking, and rejecting

Results are re-ordered against both halves of the request. The **channel**
carries the most weight and an exact channel match settles it: naming an artist
means that artist's upload, not a cover with their name in the title.

Ordering and **accepting** are different questions. Sorting always produces a
first result however wrong it is, so `usable()` throws away anything scoring
below a floor — *"kaiju girl by metta nick"* returning a short film about a
corporate monster scores zero, and it only played because zero was the highest
score there was. When nothing clears the floor the panel says it could not find
anything rather than playing something unrelated.

The **title carries the score**. An artist name heard wrongly is common; a
title sharing no word with what was asked for is a different song. Character
similarity sits under word overlap as a floor, so *"kaiju"* against *"kaijuu"*
still counts.

### Two titles for one song

A track is often filed under a **translated or romanised** title on YouTube
Music and only its original title on YouTube. *Kaiju Girl* and *乙女怪獣* are
the same song, so searching for one will not find the other — which is why
YouTube Music is its own source rather than a nicer YouTube. Both are tried and
whichever produces something plausible wins.

Comparing titles across scripts says only that they are written differently, so
`comparable()` refuses to try. An uncomparable title is **not a low score** —
it is no score at all, and treating it as zero would reject every Japanese
title somebody asked for in English.

What happens instead:

* **The artist agrees** → accepted, since that is real evidence.
* **Nothing else matched** → the search's own top result is trusted, and only
  that one. Queueing nine things nobody can vouch for is worse than one.
* **Something comparable did match** → that wins, and the uncomparable ones are
  left out.

A **mixed** title like *乙女怪獣 (Kaiju Girl)* has something to compare, so it is
scored normally.

### A misheard artist

Speech recognition gets proper nouns wrong constantly, and no amount of ranking
helps when the word being searched for does not exist.

When a search finds nothing, there is **one retry with the title alone** — a
title is usually heard correctly and a name usually is not, so the name is the
part worth giving up on.

If that retry works, the panel asks: *"Is this the right song?"*, showing what
was heard and who the result is actually by. Answering yes writes the
mishearing down against the real name, and the next request for it is corrected
**before** searching rather than after.

Asked rather than assumed: the panel found that result by throwing away part of
what was said, and a guess recorded as fact would make every future search for
that name worse.

Names live in `music_artist_aliases.json` in the data directory — a handful of
entries, in a file somebody may want to read or edit. A name already spelled
correctly is not recorded, and a gap in the middle (*"metta nick"* against
*"metanick"*) resolves without its own entry.

## When nothing comes out

The player reports what it is doing, so silence is diagnosable:

* **A video that refuses to play** is logged with the reason — *removed or
  private*, *the owner does not allow embedding*, *the embed rejected this
  page's origin*. Each is reported once.
* **A whole queue failing the same way** is one problem, not ten. When three or
  more fail identically it says so once at error level, with what to do about
  it — ten identical warnings leave somebody to notice the codes match.
* **Running out of queue** says so.
* **A muted player** says so. It is also unmuted and set to the wanted volume
  on every load, because a muted player plays perfectly and silently, which is
  the hardest kind of "it does not work" to find.

## Which thread everything is on

**Every call into the page is marshalled**, whatever thread it comes from.
`WebPlayer._run` and `_load` wrap themselves in `client.call_on_ui` rather than
trusting the caller.

That is not defensive tidiness. A `QWebEnginePage` may only be touched from the
UI thread, and none of the callers are on it:

* `on_woke_assistant` fires from a thread the STT spawns per phrase.
* `on_update` fires from the update loop.
* A skill runs on its own thread, so `PLAYER.next()` arrives from there.

Ducking is triggered from the first two, so **every volume change reached
`runJavaScript` from the wrong thread**. Qt does not raise for that — it aborts
the process, which is why it only ever showed as a crash while music was
playing, and never as a traceback.

## What the card shows

The page is asked what is playing, and **what the search found is the
fallback**. A page does not always report an artist — the watch page has to be
scraped for one, and a scrape that misses leaves a title with nothing under it —
while the search already knew, so throwing it away was the only reason the card
was ever blank.

YouTube's own pages log a steady stream of console warnings: unused preloads,
unrecognised permissions policies. None of it is actionable and all of it went
to stderr, so a page nobody can see is no longer allowed to narrate. Genuine
JavaScript errors are still kept, at debug.

## History

`music_history.json` in the data directory keeps the last 40 things played, and
**Music** in quick settings opens them as a list of rows. Pressing one plays it
and closes the panel.

Played **by id**, with no search at all — the id already answered that
question, and asking again could answer it differently. Which is the point of
the list: asking for a song by name works until the name is the hard part, and
a title in another script or an artist a speech engine mangles is easier to
press than to say again.

The same song twice is **one entry moved to the front**, not two. A history of
one album would otherwise be forty rows of the same names.

**Written from what plays, not from what was searched for.** The two differ more
often than they look: a queue skips past a video that refuses, the watch-page
fallback opens a different URL for the same track, and a replay does not search
at all. Watching the player is the only place all of those agree — so an entry
appears once a track has actually started, and a video that turned out to be
unplayable is never offered as something to press again. Adverts are excluded.

The panel closes before the track loads, so the press feels like it did
something while the page is still fetching.

## Stopping

`stop`, `shut up`, `turn it off` and similar are registered on
[`client.CANCEL`](../../../../docs/cancel.md). **Not `nevermind`** — that word
belongs to a question somebody has thought better of asking, and music is not a
question.

Priority 20, below an answer panel's 50, so with a panel open over music "stop"
closes the panel. And `stops_listening=False`: somebody who stops the music may
be about to ask for something else.

Stopping means **stop**, not pause. Leaving it paused mid-track with the card
still showing a song is not what was asked for.

## Skills

| Say                                                | Skill           |
|----------------------------------------------------|-----------------|
| *play &lt;anything&gt;*, *put on &lt;anything&gt;* | `play-music`    |
| *pause the music*                                  | `pause-music`   |
| *keep playing*                                     | `resume-music`  |
| *skip this song*                                   | `skip-music`    |
| *what is playing*                                  | `whats-playing` |

`play-music` declares a [payload](../../../../docs/skills.md), so a title of
any length arrives whole and does not drag the skill's score down.

## Ducking

These skills answer to the **panel's own wake word**, not one of their own. A
second wake word means the assistant listens for both and half the skills
answer to each, which is indistinguishable from it not hearing you.

The music drops to `duck_volume` when the assistant starts listening and comes
back when it has finished — **faded**, not cut, since a wake word killing the
music dead is more startling than the music.

**5% by default, which is lower than it sounds like it should be.** The
microphone hears the speakers: music quiet enough for a person to ignore is
still loud enough to be transcribed as speech, and a lyric arriving as a
request is worse than a request being missed. `pause_on_wake` stops it outright
instead, which is surer than any volume at the cost of losing your place for a
moment. Only music the assistant paused is resumed by it — somebody who pressed
pause during a request meant it.

Driven by `ASSIST_STATUS` rather than by events. `on_woke_assistant` only
fires once a skill has been **recognised**, so the wake word alone would never
quieten anything — and the status passes through `LIVE` on the way to
`LISTENING`, so unducking the moment it reads `LIVE` undoes the duck within a
frame of making it.

Settling is therefore a **duration**, not a value: `LIVE` has to hold for a
second or so. Every way a request can end — answered, cancelled, timed out,
fallen through — comes back to `LIVE` and stays there.

## Settings

| Key             | Default | Meaning                                   |
|-----------------|---------|-------------------------------------------|
| `volume`        | 80%     | Playback volume.                          |
| `duck_on_wake`  | on      | Quieten while the assistant is listening. |
| `duck_volume`   | 5%      | How quiet to go while listening.          |
| `pause_on_wake` | off     | Pause outright instead of quietening.     |

The Data API key goes in `SECRETS` under `musicplugin` / `youtube_api_key`.
Without one, search falls back to scraping.

## When nothing is playing

A poll that finds **no MPRIS player at all** publishes a stopped state rather
than publishing nothing. Every player having closed is not the same as one being
paused, and staying quiet leaves the registry holding whatever was last true —
so the card sits on the wallpaper showing a track that stopped existing.

A read that *errored* is treated differently and stays quiet. That is not
knowing, and the last state is a better guess than claiming silence.
