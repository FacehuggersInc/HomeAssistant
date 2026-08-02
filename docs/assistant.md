# Voice assistant

Speech-to-text runs in a separate process (`src/assistant/whisper-process.py`)
talking to the client over a local socket. The client half is
`STTProcessing`; device handling is `src/assistant/audio.py`.

Settings live under **Assistant**:

| Setting | Meaning |
|---------|---------|
| `enabled` | Whether the assistant runs at all |
| `wake_word` | App-wide wake word; plugins read `client.wake_word` |
| `input_device` | Microphone name, or empty for the system default |
| `model` | Whisper model, default `tiny.en`. Downloaded on first use |
| `voice_bar` | The activity bar along the bottom of the screen |
| `wake_listen_timeout` | Seconds to keep listening after the wake word with nothing said |
| `tts_enabled` | Whether replies are spoken |

## Saying hello

When the microphone comes up the assistant greets whoever is there, rather than
reporting on itself - "STT is Listening!" under the title "Assistant: STT" is a
subsystem talking about its own startup, which is a debug line somebody left in
front of the person using it.

The notification always appears; somebody who missed the restart wants to know
it came back. Whether it is SPOKEN is `assistant.greet_on_start`, off by
default: a panel that restarts itself at four in the morning should not
announce it to the room.

The wake word it names is `client.wake_word` - the configured one. Not
`SKILLS.wake_args[0][0]`, which is every skill that declares one in load
order, so indexing it names whichever plugin registered first. A panel telling
somebody to say a word it is not listening for is worse than not naming one.

The greeting is picked at random from a short list, so a panel that restarts
twice does not say the same thing twice. The written form adds "Say Alexa when
you need me"; the spoken one does not, because it is talking to somebody who is
already there.

They are full sentences rather than two words. Speech needs a moment to be
recognised as speech, and a room reacts to "I'm awake" after it has already
finished.

## The pill on screen

Two separate things decide whether the voice bar is up: the live status, and a
hold timer for a message that should stay readable after the status has moved
on.

The status alone is not enough. `on_woke_assistant` fires from
`process_phrase` AFTER the skill has parsed, and a skill that runs in twenty
milliseconds takes the status LISTENING -> THINKING -> LIVE between two polls
of a 200ms timer - so THINKING is never drawn and the panel appears never to
have understood anything.

So a match puts up its own message, held on its own timer:

```
1. woke          Listening…
2. transcribing  Working out what you said…   (held 20s - covers a slow model)
3. understood    "turn the lights off"        (the text arrived)
4. answered      Kitchen lights off           (held, outlives the status)
```

Stage 2 is the one that matters on a big model. `small.en` takes seconds, and
nothing used to be sent during them: the child finalised the audio, the panel
stood down after the wake, and the next thing anybody saw was the finished
text. From the outside that is a pill fading and then, seconds later, an
answer out of nowhere.

The child now sends `transcribing` before the model runs, which the panel
turns into `on_transcribing_assistant`. It is held on a long timer of its own
rather than left to the status, because nothing further arrives until the
model finishes - this is the one stage that has to stay up unaided.

It shows **the phrase**, which is the panel repeating back what it heard -
the one thing that says whether it got it right. It used to read
"<wake word> - listening…", which was wrong twice: it had stopped listening by
then, and the word it named came from the matched SKILL rather than the one
the person says.
## Asking without speaking

`/process?q=...` and anything else that hands the assistant a phrase it did
not hear go through `STT.submit()`, not `pre_processing()`.

The difference is the wake word. `pre_processing` is the microphone's path and
looks for one first; a typed request has none, so it matched nothing, no skill
was found, and it was dropped in silence behind a `200 Success` - the pill
showed the query and then nothing happened.

`submit()` goes straight to the skills. A wake word is allowed and stripped,
since "alexa play something" means the same as "play something" and leaving it
in hands the parser a word that is not part of the request. A session takes the
phrase instead when one is open, because a conversation waiting on an answer
should get one however it was sent.

The endpoint now answers with what happened: `409` when the assistant was
already busy rather than `200` for a phrase nobody took.

## Playing a reply to the end

Audio is written to the output stream in short chunks so a stop can land part
way through. `write()` only queues, though - and in sounddevice's own words
`close()` discards pending buffers "as if abort() had been called", while
`stop()` "waits until all pending audio buffers have been played".

So the stream is stopped before it is closed, or the last chunk is thrown away
and every reply loses its final syllables. A single blocking write of the whole
buffer used to hide this, which is why it appeared when playback was split up
to be interruptible - and why a short greeting suffered most, a short phrase
being mostly end.

Skipped when somebody interrupted: they are not asking for the rest of the
buffer to play out first.

## When the microphone does the work itself

`assistant.mic_processing` is `software` or `hardware`.

`software` assumes a plain microphone and cleans the audio here. `hardware` is
for arrays that have already done it - a ReSpeaker XVF3800 runs AEC, noise
suppression, AGC and VAD on its own chip before anything reaches this side.

Running those a second time is not neutral. A second noise pass over
already-clean speech is what makes it sound underwater, two aggressive VAD
gates in series drop the quiet start of a phrase between them, and the
self-hearing guards work around an echo the hardware has already cancelled.

| | software | hardware |
|---|---|---|
| Noise reduction | on | off |
| Silence floor | -60dB | -48dB |
| Self-hearing grace | 2.5s | 0.6s |
| Interrupt settle | 0.5s | 0.2s |

**The VAD aggressiveness is 3 in both, deliberately.** Softening it for an
array that has its own VAD looks right - two aggressive gates in series clip
the quiet start of a phrase - and it is the wrong trade. A phrase is finalised
when `context_windows_end` consecutive windows are called SILENCE; a softer
gate calls fewer things silence, so that fills more slowly, and the counter
resets on any speech window. Softening it makes every phrase END later, which
is felt as the panel being slow to hear you.

An array's AGC makes it worse rather than better: sixty decibels of gain lifts
the room's noise floor, so ambient noise looks like speech to a gate already
reluctant to call anything silence.

| | to finalise |
|---|---|
| Aggressive, quiet room | ~175ms |
| Aggressive, noisy room | ~310ms |
| Soft, quiet room | ~500ms |
| Soft, noisy room with AGC | ~8700ms |

The silence floor rises because sixty decibels of AGC lifts the room's noise
along with the speech, so a threshold set for a quiet raw signal stops telling
them apart.

The grace shortens rather than disappearing. Hardware AEC cancels what it can
hear through its own reference; a speaker not wired through the array is an
echo it knows nothing about, so a little caution is kept.

**Software by default.** A plain microphone is the common case, and this
profile on one would hear far less.

Changing it **restarts the assistant**, because half of what it changes lives
in the child process and is fixed when that is spawned:

| Lives in | Read | What |
|---|---|---|
| The panel | Live, on every call | The self-hearing grace, the interrupt settle |
| The speech process | Once, at spawn | Noise reduction, VAD aggressiveness, the silence floor |

Without the restart the panel would end up half switched - the guards moving
while the audio pipeline stayed as it was - which is the worst of both and
says nothing about itself. `assistant_config()` lists it alongside the model
and the input device for that reason.

## What listens for the wake word

Two detectors, chosen by `assistant.wake_detector`.

**openWakeWord** is a model built for the job. It answers the actual question -
is the wake word in this audio - as a probability per frame. Nothing to spell,
nothing to match loosely, no window size to tune.

**Whisper** transcribes a fragment and checks the text for the word. That is a
transcription per check, and it answers with a spelling rather than an answer,
which is why everything below this section exists.

| | openWakeWord | whisper |
|---|---|---|
| Words | alexa, jarvis, mycroft, rhasspy | any |
| Cost | a few milliseconds a frame | a whole transcription |
| Tuning | a threshold | sample size, fuzzy ratio, beam size, model |

`auto` is the default: openWakeWord when there is a model for the wake word,
whisper otherwise. That is the right answer for almost everybody, and it means
setting the wake word to something unusual quietly keeps working.

It **falls back rather than failing**. Three things can stop it - the library
is not installed, there is no model for the word, the model will not load -
and none of them should leave a panel that cannot hear its own name. Asked for
by name and not available, it warns; on `auto` with an unusual word it stays
quiet, because that is the ordinary case rather than a problem.

The spotter is fed every audio window as it arrives. It is a streaming model,
so each frame builds on the last and skipping any of them describes audio that
is not adjacent - which is also why `reset_all()` clears it.

`openwakeword` is optional in `requirements.txt`. A panel without it starts
normally and says so once.

## Hearing the wake word, the whisper way

Two things make this hard, and both are about the check rather than the
microphone.

**It reads a fraction of a second.** `wake_sample_windows` is 12 windows of
30ms, about 360ms - enough for two syllables. It was 5 (150ms), which is
barely one, and a small model handed that comes back with something adjacent
to the word rather than the word.

**And the word is matched loosely.** `find_wake` tries an exact match on word
boundaries first, then falls back to `find_wake_fuzzy`, which allows a
similarity of `WAKE_RATIO` (0.8) and also tries each word joined with its
neighbour - because the other common failure is one word arriving as two.

| Heard | |
|---|---|
| `Alexa`, `alexa,` | exact |
| `Elexa`, `alexah`, `Lexa` | fuzzy |
| `a lexa`, `Alex a` | fuzzy, joined |
| `Alexis`, `a lexus` | **refused** |

Those last two score 0.727, the same as each other. No threshold takes one and
not the other, so both are refused: a name spoken in the room waking the panel
is worse than one more retry.

Only the wake word is fuzzy. What follows it is passed on as heard, because a
skill's arguments are not a known short list to match against.

## Two models, not one

The wake word and the phrase are transcribed by different models, and they
want opposite things.

The **wake check** runs on every 150ms of speech and answers one question -
was the wake word in that. It is looking for a single known word, so a model
that mishears "weather" costs nothing, and it has to answer before the person
finishes their sentence. Small.

The **phrase** is transcribed once and read by a person. Accuracy is what
matters, and a second of latency is amortised over a whole utterance.

Sharing one model meant the accurate choice paid its cost on every wake check
too. Worse, they took the same lock, so a phrase being transcribed held up the
wake check behind it:

| | wake answered after |
|---|---|
| One model, one lock | 601ms |
| Two models, two locks | 61ms |

(with a phrase taking 300ms in both)

### Trading accuracy for time

Two knobs, and the second is the one to try first.

`assistant.beam_size` is how many candidate transcriptions the model weighs.
5 is the default and roughly three times the decode work of 1. Beam search
helps least on short clear commands, which is most of what a panel hears - so
a bigger model that hears you correctly but takes too long is often fixed by
dropping this rather than dropping the model.

Very roughly, against `tiny.en` at beam 1:

| | |
|---|---|
| `tiny.en`, beam 1 | 1x |
| `base.en`, beam 5 | ~6x |
| `small.en`, beam 1 | ~6x |
| `small.en`, beam 5 | ~19x |

The wake check always uses beam 1 whatever this says: it asks whether one
known word is present, not which of five phrasings is best.

`assistant.model` is the one that decides how long the panel takes to answer.
`assistant.wake_model` is small and separate - and ignored when the phrase
model is already `tiny`, because loading a second copy of it would be waste. A
wake model that fails to load falls back to the phrase model rather than
stopping the process: checked slowly beats not at all.

## Where the time goes

Every finalised phrase logs its own timing at `debug`:

```
[Whisper]: Finalizing - 1440ms spoken, 180ms waiting for silence.
[Whisper]: Final Transcription (12ms queued, 340ms in the model): what is the weather
```

Four numbers, and they are four different problems. **Spoken** is the person.
**Waiting for silence** is the VAD refusing to call the room quiet - the
number that grows when the aggressiveness is wrong or the room is noisy.
**Queued** is the processing thread being busy. **In the model** is the model,
and the only one a smaller model helps with.

"It feels slow" is not something anybody can act on. One of these growing is.

## Testing the microphone on its own

Settings has a **Microphone test** page. A session is started by hand, every
transcript lands in a list exactly as it was heard, and it stops when told.

Nothing is routed. No wake word is needed and no skill runs - the point is to
tell "the microphone is not working" apart from "the wake word is not
matching" apart from "the skill is not firing", which is impossible while all
three are in the way of each other.

It holds the microphone only while a session is running, and navigating away
is a stop.

A session puts the child into **passthrough** - the same mode a conversation
uses, where it stops waiting to be woken and finalises on silence - through
`STT.start_monitor()`. Watching alone would not have been enough: the child
only transcribes after the wake word fires, so a page that just read
transcripts would have been testing the wake word as much as the microphone,
which is the one thing it exists to rule out.

While monitoring, nothing is routed. Every transcript reaches the listeners
and stops there - a skill firing for each sentence said in the room while this
page is open would be worse than useless. `stop_monitor()` puts it back to
waiting for the wake word.

Listeners are added with `STT.add_listener()` and are handed each transcript
before normalising or routing. A listener does not consume the phrase; outside
monitoring it reaches the panel exactly as it would have.

## Two threads, and what may not block

The speech process runs an audio thread and a processing thread, and the rule
between them is that the audio thread never waits.

It reads the microphone in 30ms windows. Anything that takes longer than a
window - de-noising a whole utterance, a socket write to a parent that is slow
to read - drops audio while it runs, and dropped audio truncates whatever is
said next. So de-noising happens in the processing loop, which is about to
spend far longer in the model anyway, and the extended-silence timeout is
reported from a thread of its own.

Two failures worth knowing about, because both were silent:

- The processing loop `return`ed on a repetitive transcript instead of
  `continue`ing. That ends the worker; nothing restarts it, and every later
  phrase queues behind a thread that has gone.
- The wake-word check cleared its gate only on the success path, and the loop
  gated on that attribute being *falsy* rather than on the thread being alive.
  One transcribe that raised, and wake detection stopped until a mode switch.

Neither logged anything. `check_whisper_process.py` asserts the shapes that
caused them are gone.

A third came from the same place. The wake check transcribes a sample on its
own thread, and on a slow one it can answer AFTER the utterance it came from
has already been finalised, sent and acted on. Waking then announces a phrase
that has already been answered - and nothing further arrives to stand the panel
down, so the pill sits reading "listening" with nobody talking to it.

Each check now carries the id of the utterance it belongs to, and one that
comes back to a different id is discarded.

The panel's own watchdog covers the rest, and it had two blind spots. It
skipped entirely while a session was open - exactly when a stuck pill lasts
longest. And it measured from `woke_at`, which the wake word sets and nothing
else does: a session taking a phrase, or a `/process` call while one is open,
both reach LISTENING with `woke_at` still zero, so the watchdog returned early
and never looked.

It anchors on entering LISTENING now, however that happened, and the anchor is
dropped wherever the status leaves it - keeping one across a stand-down would
time out the next pill the instant it went up. During a session it stands the
wake state down and leaves the session alone, since that one really is
listening.

## What the transcriber makes up

Whisper fills the pause around what was said with its own habits, so both of
these arrive as one phrase with an invented half in it:

```
i like that what is the weather
what is the weather i like that
```

`normalize.strip_hallucination()` takes known boilerplate off **either end**.
Two rules keep it from doing harm:

- Only the ends. Cutting from the middle would take real speech with it.
- Its own list (`EDGE_NOISE`), not `HALLUCINATIONS`. That one holds single
  common words - "you", "music", "right" - which are fine to reject as a whole
  utterance and ruinous to strip from the edge of one: "who are you" would
  become "who are", and "what is that music" would lose the music.

A phrase that is entirely boilerplate is still `is_hallucination`'s answer to
give; taking an edge off "i like that" would leave "i".

The test for adding to `EDGE_NOISE` is whether anybody would say it as the
first or last words of an instruction - and it is a stricter test at the front,
because "i like that idea, remind me later" is a sentence somebody could
plausibly say. The list stays short rather than clever.

## Saying what it is doing

The voice bar lives at the bottom of the SCREEN, and a full-screen panel covers
it - so while a conversation was open the one thing saying whether the panel was
listening was hidden behind it.

The conversation carries its own, in the same place and the same shape:

| State | Says |
|---|---|
| speaking | Speaking · say the wake word to interrupt |
| listening | Listening… |
| thinking | Thinking… |
| otherwise | Say the wake word to ask another |

The last two rows are the ones worth having. Somebody looking at a finished
reply has no way to know the session is still open and waiting, and somebody
listening to a long one has no way to know they can cut it short.

## Speaking over a reply

The wake word is heard while the panel is talking, and hearing it stops the
speech immediately. The session stays open, so the next question can be asked
straight away rather than after the rest of the answer has been read out.

Inside a session the wake word is stripped rather than passed on: it is not
addressing the panel there, it is interrupting it, and what follows is the
question.

| Heard, mid-reply | Given to the session |
|---|---|
| "alexa what about tuesday" | "what about tuesday" |
| "alexa" | nothing - it keeps listening |
| anything else, within a second of the stop | nothing - it is the tail |
| anything else | as it was said |

The last two rows are why there is a settle at all. The microphone records
while the panel speaks and Whisper only transcribes on silence, so the end of
what it was saying arrives just AFTER it was stopped, looking like a question.

Everything else heard during a reply is still dropped as the panel hearing
itself - the microphone captures while it speaks, and treating a stray phrase
as an interruption would let a reply talk itself out of its own sentence.

**The wake word is never the panel hearing itself**, because the panel never
says it. So it comes through whether or not there was anything to stop - which
matters most in the two and a half seconds AFTER a reply, since that is when
somebody talks: they are answering what was just said. Answering on "was
anything stopped" instead meant the wake word was dropped exactly there, and
the panel looked deaf at the one moment it was most likely to be spoken to.

Hearing it sets the pill to listening straight away rather than waiting for the
wake pipeline, because that is the moment somebody is looking for a sign they
were heard.

Playback is written in tenth-of-a-second pieces so a stop lands where it was
asked for. One write of the whole reply blocks until every sample has played,
which is why nothing could interrupt it before: by the time anything could act
on the wake word, the sentence had finished anyway.

Saying you are finished - "stop", "nevermind", "that's all" - ends the session
AND closes the panel. That is checked inside the conversation loop rather than
left to the cancel engine, because an open session queues every phrase before
the intent engine sees it, so "stop" arrived as a question and was asked of the
model.

## The activity bar

A floating pill above the bottom edge, centred, sized to its content between
240 and 560px. It rises into place and fades out rather than blinking, and
carries a live level meter plus a line of text.

| State | Accent | Shows |
|-------|--------|-------|
| Listening | red | meter tracking voice level, "Listening…" or the wake word |
| Thinking | blue | meter sweeping on its own, "Thinking…" |
| Acting | green | whatever the skill is doing |
| Heard | grey | the transcript, quoted, held long enough to read |

Whisper only emits **finished** transcripts - it transcribes a completed
speech window, so there is no partial stream to show mid-sentence. The meter
covers "hearing something right now"; the text covers "heard this". If live
partial text is ever wanted, it needs a streaming model in
`whisper-process.py`, not a change here.

The bar is `WA_TransparentForMouseEvents`, so it never eats a tap, and its
shadow is painted by hand - a `QGraphicsEffect` cannot coexist with painting
custom alpha, and only one effect can be set on a widget at a time.

How long a transcript stays up scales with its length rather than being
fixed: `assistant.voice_bar_hold` (default 6s) is a floor, and anything
longer than that reads-in-six-seconds is held proportionally, capped at 20s.
There is also a minimum visible time, so a wake word that gets rejected a few
hundred ms later cannot flash a pill for one frame.

Turn it off with `assistant.voice_bar`.

## Settings migration

Settings added by an update are folded into your existing data file at
startup.

The template and the data file are compared on every launch. New keys arrive at
their defaults, values you have changed are kept, and keys the template no
longer has are dropped. The old file is copied to `<name>.json.bak` first,
and every added or removed path is logged.

## Startup

`Client.start_assistant()` runs shortly after `build()` -- not during plugin
load, since it needs the UI to be able to ask anything. It:

1. checks the audio stack is usable at all
2. logs every input device it can see
3. resolves the configured device name to an index, falling back to the
   default if it has gone away
4. opens the stream briefly to confirm it actually works
5. asks before downloading a Whisper model that is not already cached
6. starts the STT process

Any failure surfaces as a notification plus a dialog carrying the real
reason, and the rest of the app carries on. The assistant never takes the
app down with it.

## How skills match

A skill's `examples` are compiled into spaCy Matcher patterns. Those patterns
are **generalised**, not literal:

| In the example | In the pattern |
|----------------|----------------|
| a number (`10`) | any number |
| a determiner (`a`, `the`, `my`) | optional, interchangeable |
| politeness (`please`, `can you`, `just`) | optional |
| a pronoun (`me`, `us`) | optional |
| everything else | its lemma |

This generalisation is what lets one example cover a family of phrasings.
`"set a timer for 10 minutes"` also matches `"set the timer for 1 minute"`:
the determiner is optional, and the number is any number rather than the
literal `10`.

When the Matcher finds nothing **or is not confident**, a **rule phase** scores
the utterance's content lemmas against every skill's examples and takes the
best, provided it clears `FALLBACK_DEFAULT_RULE_SCORE`.

Running it only when the Matcher found *nothing* was too narrow. A pattern can
only ever match words somebody wrote into an example, so a phrase carrying an
arbitrary name reaches the Matcher as a weak partial hit at best - "stop the
eggs timer" matched only the nevermind skill's one-word "stop", which then won
uncontested and the rule phase never ran. A Matcher hit below
`MATCHER_CONFIDENT_SCORE` now has to beat the rule phase rather than being
taken on its own. The threshold matters: scoring every
skill against every phrase will always produce a nearest match, and "nothing
matched" has to stay a possible answer.

Write several examples per skill regardless - they define the vocabulary the
rule phase scores against - but they do not need to enumerate every
determiner and number.

### Scoring

**Both phases score on two coverages, not one.** How much of the example the
utterance covers, and how much of the utterance the example accounts for,
combined as a harmonic mean.

Measuring only the first is what let a one-word example win any utterance
containing that word: `"cancel the 5 minute timer"` scored a perfect 1.0
against the `nevermind` skill's `"cancel"`, tied with the timer skill's own
example, and took the tie by appearing earlier in the sentence. Every targeted
cancel was eaten by backing out of the assistant.

An example only wins now if it explains most of what was said, so a short
catch-all cannot outrank a specific phrase - while a bare "cancel" still
reaches `nevermind`, because there the example accounts for the whole thing.

The rule phase weights words by how discriminating they are: a lemma used by
one skill counts for more than one every skill shares. Weighting every word
equally scores "clear all notifications" identically against
`notifications-open` and `notifications-empty`, since both contain
"notification" and the word that actually decides carries no more weight than
the noise.

Score is the harmonic mean of two coverages: how much of the example the
utterance covers, and how much of the utterance the example accounts for.
Recall alone let a long rambling phrase match a tiny example on one shared
word.

Token comparison is fuzzy above four characters, which absorbs the
mishearings a better phrase list cannot: "notifcations", "aplication",
"minuets" for "minutes". Short tokens are compared exactly, since at three or
four characters nearly everything is close to everything.

`FALLBACK_DEFAULT_RULE_SCORE` is a swept value, not a guessed one. Lower
thresholds score better overall but start letting out-of-domain phrases
through - at 0.50, "tell me a joke" answers with the weather. A miss costs the
user a repeat; a misfire makes the assistant do something nobody asked for, so
the right choice is the highest threshold with **zero**
misfires wins.

### Writing good examples

Determiners, numbers, politeness, plurals, capitalisation and small
mishearings are handled for you. Vocabulary is not: *"close the app"* will not
match a skill whose examples only ever say *"application"*. Cover the **words**
people use, not their grammar.

[Writing skills](skills.md) has the rest.

### Wake words in a transcript

Whisper capitalises the first word of every transcript, so wake matching is
case-insensitive and anchored on word boundaries
(`STTProcessing.find_wake` / `strip_wake`). A plain substring test does not
work here - "alexa" is not in "Alexa, set a timer for 1 minute." The same
match also splits the command off the wake word, so a miss would pass the
wake word through as part of the command.

Boundaries matter too: a short wake word otherwise fires inside ordinary
words ("Alexander").

### Units

`normalize.expand_units()` turns spoken abbreviations into canonical units,
but only directly after a number, so ordinary speech is untouched:

```text
"3 mins"  -> "3 minutes"      "the min temperature" -> unchanged
"30 secs" -> "30 seconds"     "press s to continue" -> unchanged
"2 hrs"   -> "2 hours"
```

That means argument patterns only ever need to list the canonical form, and
using `LEMMA` rather than `LOWER` covers singular and plural in one entry:

```python
"time": [[{"LIKE_NUM": True},
          {"LEMMA": {"IN": ["second", "minute", "hour", "day"]}}]]
```

### Arguments

A matched phrase can carry values out of the transcript - a name, a number, a
duration. The anchor words that locate a value are stripped before it reaches
the skill, so *"call it Eggs"* arrives as `name="Eggs"`.

Declaring them is a skill-writing job rather than an assistant one, and is
covered on [Writing skills](skills.md#arguments).

## Transcript normalisation

`src/assistant/normalize.py` cleans a transcript before it reaches the intent
engine. Skill patterns match on tokens, so spoken numbers have to end up as
separate number and unit tokens or the argument never extracts:

```text
"set a timer for one minute"      -> "set a timer for 1 minute"
"set a timer for1minute"          -> "set a timer for 1 minute"
"set a timer for half an hour"    -> "set a timer for 30 minutes"
"set a timer for a couple of mins"-> "set a timer for 2 mins"
```

It handles compound numbers ("twenty-five", "one hundred and twenty"),
articles before a unit ("a minute" -> 1, but "a timer" is left alone),
fractions, filler words, and digits glued to words. Number conversion is
written out rather than delegated to `word2number`, which raised on ordinary
input like "zero" or a trailing "and" - a transcript is untrusted text and an
exception there dropped the whole phrase.

## When nothing matches

If `SkillIntentEngine` finds no skill, the client fires
**`on_assistant_fallback`** with the phrase. Skills always win - a subscriber
only ever sees what nothing else claimed. It fires on the real input path
only, so a `use_skill=False` probe stays side-effect free.

`src/assets/bundled/AIFallback` uses it to answer the question with an AI and show the
reply in a chat panel. Nothing in the client depends on that plugin: remove
it and unmatched phrases go back to being ignored.

```python
def load(self, carryover=None):
    self.client.subscribe_to_event("on_assistant_fallback", self.on_fallback)

def on_fallback(self, event):
    phrase = event          # the phrase nothing understood
```

Handle it **off the event thread**. It fires from inside the intent engine,
so blocking there stalls the whole STT pipeline.

### The AI fallback plugin

Needs an OpenAI key, entered under the plugin's own settings and stored in
`.env` (see [Registries](registries.md)). Without one it stays quiet.

**OpenAI has no free tier** - the account needs credit on it. An
`insufficient_quota` error is a billing limit, not a rate limit, so waiting
will not help.

Pinned to `gpt-5.4-mini`. Add more entries to the `model` options in the
plugin's `settings.json` if you want the choice back.

Configurable: model, token ceiling, how many previous turns to send, the
system prompt, whether replies are spoken, and the panel timeout.

Two details worth knowing:

**A Session opens before the first API call.** That is what serialises the
conversation. While a request is in flight, anything else the user says lands
in the session queue rather than being treated as a fresh command, and is
only picked up once a reply has come back. Without it a second question fired
mid-request would race the first.

**Replies are markdown**, rendered to the HTML subset Qt actually supports -
headings, emphasis, lists, links, images, blockquotes and fenced code blocks.
Written out in `markdown.py` rather than pulling in a markdown package: every
general-purpose converter emits CSS that Qt ignores, which renders worse than
handling the subset directly. Replies are HTML-escaped before any markup is
added, so a reply containing a `<script>` tag is displayed rather than
interpreted. A separate `to_speech()` strips markup and drops code blocks
before anything is read aloud.

The panel is reused across turns and carries its own long timeout, so a
conversation is not cut off mid-thought by the ordinary interaction timeout.

**Failures never open the panel.** A chat panel containing nothing but an
error implies a conversation started; the error goes to a dialog instead,
with the summary as the body and OpenAI's own message as the detail. If a
panel is already open the note is added there too, so the transcript does not
end on an unanswered question.

Errors are also classed as fatal or not. A rejected key, an account with no
credit, or a model this account cannot use ends the conversation - there is
no point holding a session open that will fail again on the next question.
Rate limits and network errors leave it open so a follow-up can retry.

## A wake nobody followed up on

Saying the wake word and then not saying anything must not leave the panel
stuck in `LISTENING`.

Two things prevent it. A repeated wake **refreshes** the wake rather than being
ignored - saying it again is exactly what a person does when it looks like the
panel did not hear them. And `assistant.wake_listen_timeout` (12 seconds by
default, floored at 3) stands the wake down on the client's own clock. The STT
process has its own reset; this is the panel not depending on it.

A wake word with nothing after it clears the wake immediately rather than
leaving it armed, and an open session is never interrupted by the timeout - a
skill holding a conversation is waiting on purpose.

## Backing out

Saying "nevermind", "cancel", "forget it", "stop" and similar abandons
whatever the assistant is doing and returns it to waiting for the wake word.

This is handled in two places on purpose:

* `STTProcessing.start_skill_parse()` checks for a cancel phrase **before**
  intent matching, so backing out works even with no cancel skill registered.
* `CoreSkillsBundle` also registers a `nevermind` skill, so it appears in the
  skills list in Settings and the activity bar acknowledges it.

`client.cancel_assistant(reason)` does the same thing from code, and fires
`on_assistant_cancelled`.

Cancel phrases are matched against the **whole** utterance, never searched
within it - "never mind the weather" stays a weather query.

### Sessions

A skill holding a conversation (`STT.new_session()`) gets the same escape.
`wait_for_phrase()` returns `None` when the user cancels, the session times
out, or it is closed - so a prompt loop should break on `None` rather than
asking again:

```python
with session:
    while True:
        phrase = session.wait_for_phrase()
        if phrase is None:
            break            # cancelled, timed out, or closed
        ...
```

`wait_for_phrase()` always returns. It takes a timeout, and a cancel or a
close pushes a sentinel that releases the waiter - so an answer that never
comes cannot leave a skill thread blocked for the life of the process.

## The STT process

`whisper-process.py` runs detached and talks over a socket. Points worth
knowing if you change it:

* **VAD gets raw audio.** Denoising every 30ms frame costs about 76% of a core
  continuously - roughly 5000x the cost of the VAD call it feeds - and on
  slower hardware the loop cannot keep up, drops input and truncates phrases.
  Noise reduction runs once on the complete utterance before transcription,
  which is where it helps.
* **`beam_size`, not `best_of`.** `best_of` only applies when sampling, and is
  inert at `temperature=0`.
* **`condition_on_previous_text=False`.** Carrying context between windows
  makes short isolated commands loop and hallucinate.
* **Hallucinations are filtered.** Whisper emits "Thank you.", "you",
  "Thanks for watching!" and repeated single words from silence, confidently.
  These are not transcription errors better audio would fix.
* **The model is locked.** The wake-word check transcribes on its own thread
  alongside the processing loop, and `WhisperModel` is not documented as
  thread-safe.
* **Overflows are logged.** `stream.read()` reports dropped input. Discarding
  that report makes truncated phrases look like model errors.

## Changing settings while running

Changing the model, microphone, wake word, `enabled` or `tts_enabled` in
Settings restarts the assistant on save -- including the download prompt if
you switch to a model that is not cached yet. Nothing needs a relaunch.

`Client.assistant_config()` is the snapshot that gets compared; add to it if
you add a setting the running assistant depends on.

## Devices

`input_device` is stored as a **name**, not an index, because PortAudio
indices shift whenever devices are added or removed -- a pinned index
silently becomes the wrong microphone. A configured name that is no longer
present falls back to the system default and says so, rather than refusing
to start.

ALSA advertises its rate-conversion and channel-mixing plugins (`lavrate`,
`samplerate`, `speexrate`, `upmix`, `vdownmix`) as capture devices. They are
not microphones, so they are hidden from the listing -- but still resolvable
by name if you deliberately want one. Real backends (`pulse`, `pipewire`,
`default`, `sysdefault`, `hw:*`) are always listed.

Microphone problems detected while running (unplugged mid-session, another
app claiming the device) are reported back over the socket and shown once,
not on every retry.

## Speaking

Skills should call `client.say(text)` rather than `client.TTS.play(...)`:

```python
if not self.client.say("Twenty two degrees."):
    self.client.simple_notify("assistant", "Assistant", "Twenty two degrees.")
```

`say()` returns whether anything was actually said. TTS needs an the voice backend
key, set under **Assistant** in Settings (it is stored in `.env`, not in the
settings file - see [Registries](registries.md)). Without one the app still runs and skills still
work, they just do not talk back. Entering a key restarts the assistant, so
speech comes up without relaunching. `TTSProcessing` exposes `.available` and `.error`
for the specific reason.

## Wake words

`client.wake_word` is the app-wide setting. A plugin may override it for its
own skills, but should default to inheriting:

```python
own = str(self.settings.general.wake_word.value).strip()
wake = own.lower() or self.client.wake_word
```

## Cross-platform note

`import sounddevice` raises **OSError**, not ImportError, when PortAudio is
missing -- which is the normal state of a fresh Windows install without audio
drivers, or a minimal Linux container. Anything touching the audio stack must
catch `Exception`, not `ImportError`. `audio.available()` already does, and
returns a reason worth showing a user.
