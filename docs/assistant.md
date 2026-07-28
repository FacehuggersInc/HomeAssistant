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
| `tts_enabled` | Whether replies are spoken |
| `elevenlabs_key` | ElevenLabs API key, stored in `.env` (see [Registries](registries.md)) |

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

When the Matcher finds nothing, a **rule phase** scores the utterance's
content lemmas against every skill's examples and takes the best, provided it
clears `FALLBACK_DEFAULT_RULE_SCORE`. The threshold matters: scoring every
skill against every phrase will always produce a nearest match, and "nothing
matched" has to stay a possible answer.

Write several examples per skill regardless - they define the vocabulary the
rule phase scores against - but they do not need to enumerate every
determiner and number.

### Scoring

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

The engine now handles determiners, numbers, politeness, plurals,
capitalisation and small mishearings. What it cannot invent is vocabulary:
"close the app" will not match a skill whose examples only ever say
"application". Cover the *words* people use, not their grammar - one example
per distinct phrasing, not per determiner.

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

`arguments` patterns often need an anchor word to find the value:

```python
"name": [[{"LOWER": {"IN": ["call", "called", "named"]}},
          {"LOWER": "it", "OP": "?"},
          {"IS_ALPHA": True, "IS_STOP": False}]]
```

The anchor is stripped before the value reaches your skill, so
`"call it Eggs"` arrives as `name="Eggs"` rather than `name="call it Eggs"`.

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

`say()` returns whether anything was actually said. TTS needs an ElevenLabs
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
