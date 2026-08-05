# Voice assistant

**openWakeWord** spots the wake word. **Parakeet** transcribes the phrase.
Both are required; there is no fallback, and a panel missing either says which
and does not listen.

Speech-to-text runs in a separate process, `src/assistant/parakeet-process.py`,
talking to the client over two local sockets. The client half is
`STTProcessing` in `src/assistant/stt.py`.

| File                     | Holds                                               |
|--------------------------|-----------------------------------------------------|
| `parakeet-process.py`    | The child: microphone, VAD, spotter, transcriber.   |
| `stt.py`                 | The client half, routing, sessions.                 |
| `parakeet.py`            | Loading and fetching Parakeet weights.              |
| `wake_spotter.py`        | openWakeWord.                                       |
| `audio.py`               | Devices, and the model cache check.                 |
| `tts_pocket.py`          | Speaking.                                           |
| `normalize.py`, `nlp.py` | Cleaning a transcript before it is matched.         |
| `skill.py`               | The intent engine. See [Writing skills](skills.md). |

`whisper-process.py` speaks the same protocol and is kept for reference.
Nothing starts it.


## Settings

| Key                                   | Default       | Does                                                                      |
|---------------------------------------|---------------|---------------------------------------------------------------------------|
| `assistant.enabled`                   | `on`          | Whether the assistant runs at all.                                        |
| `assistant.speech.model`              | `parakeet-v3` | `parakeet-v3` (25 languages) or `parakeet-v2` (English, slightly faster). |
| `assistant.speech.parakeet_precision` | `int8`        | `int8` is ~700MB. `float32` is ~2.5GB and several times the memory.       |
| `assistant.wake.wake_word`            | `alexa`       | One of the four openWakeWord ships.                                       |
| `assistant.wake.wake_listen_timeout`  | `12` sec      | How long to wait for a phrase after waking.                               |
| `assistant.wake.session_silence`      | `800` ms      | How long a pause ends a sentence inside a conversation.                   |
| `assistant.feedback.voice_bar`        | `on`          | The activity bar along the bottom.                                        |
| `assistant.feedback.voice_bar_hold`   | `6` sec       | Minimum time a transcript stays on it.                                    |
| `assistant.feedback.greet_on_start`   | `off`         | Whether the greeting is spoken as well as shown.                          |
| `audio.devices.input_device`          | `Default`     | Which microphone.                                                         |
| `audio.devices.mic_processing`        | `software`    | `hardware` for an array that cleans its own audio.                        |
| `audio.speech.tts_enabled`            | `on`          | Whether replies are spoken.                                               |

Changing any of these restarts the assistant on save.
`Client.assistant_config()` is the snapshot that gets compared - add to it if
you add a setting the running assistant depends on.


## The wake word

openWakeWord answers "is the wake word in this audio" as a probability per
frame. It ships models for four words, and the setting is a dropdown of
exactly those:

```
alexa    hey jarvis    hey mycroft    hey rhasspy
```

Anything else needs a model trained for it. The setting is an enum rather than
free text because a word with no model produces a panel that starts, listens,
and never hears its name.

`Client.speech_stack_ready()` checks both halves before the process is
spawned, and refuses with a named reason - no `onnx-asr`, no `openwakeword`,
no model for the word - rather than letting the child start and stop again.

The spotter is fed every 30ms window in order. It is a streaming model, so a
skipped frame describes audio that is not adjacent to what follows.

**In both modes**, and while muted. A conversation runs in passthrough, and a
panel saying "say the wake word to ask something else" with no detector
running means the word has to survive being transcribed, matched as text, and
passed by every self-hearing guard first. It is skipped in one case only:
while a phrase is being captured in wake mode, where a re-fire would restart
the capture of the sentence it is already taking.


## What happens when it wakes

**The phrase has not been said yet.** A detector that fires acoustically
answers as the word completes, which is before the question starts.

|                                     |                                                              |
|-------------------------------------|--------------------------------------------------------------|
| Wake audio                          | Discarded. It is the wake word, not the phrase.              |
| The 250ms after it (`WAKE_TAIL_MS`) | Lead-in only. It cannot start a phrase.                      |
| Armed for                           | `assistant.wake.wake_listen_timeout`, from the last capture. |
| A phrase ends on                    | 700ms of silence, or 18s of speech.                          |
| Disarmed when                       | A transcript comes back **with words in it**.                |

Both ways of speaking work. Run straight on - "alexa what is the weather" -
and the words are already arriving as speech windows. Pause first and the
armed window waits.

Two things that look like details and are not:

- The tail of the word is still arriving when the spotter fires. Without the
  guard it becomes a phrase of its own, transcribes to nothing, and the panel
  flickers from listening to thinking and back.
- The audio thread cannot tell a phrase from a cough. Only the transcript can,
  so disarming happens in the processing thread. Disarming at capture makes
  one unusable clip deafen the process while the panel still shows
  "listening" - the client holds `woke_with` until its own timeout.

The transcript therefore does **not** contain the wake word. `strip_wake()`
returns the whole phrase when it cannot find one, and `woke_with` is set from
the socket rather than from the text.

Inside a session the child transcribes everything, so the wake word does
arrive in the text and is how somebody interrupts a reply. `find_wake` matches
it on word boundaries, then falls back to `find_wake_fuzzy` at a similarity of
`WAKE_RATIO` (0.8), also trying each word joined with its neighbour:

| Heard                     |               |
|---------------------------|---------------|
| `Alexa`, `alexa,`         | exact         |
| `Elexa`, `alexah`, `Lexa` | fuzzy         |
| `a lexa`, `Alex a`        | fuzzy, joined |
| `Alexis`, `a lexus`       | **refused**   |

The last two both score 0.727. No threshold takes one and not the other, so
both are refused: a name spoken in the room waking the panel is worse than one
more retry.


## The phrase

Parakeet is NVIDIA's, run through `onnx-asr` rather than NeMo - four megabytes
against several gigabytes, loading the same published weights. It answers with
text and no segments.

### Which weights

`onnx-asr` takes the full-size export unless told otherwise, which for
`parakeet-v3` is a 2.44GB encoder plus a separate weights file.

| Precision | Encoder | Notes                                                 |
|-----------|---------|-------------------------------------------------------|
| `int8`    | ~650MB  | The default. Faster on a CPU.                         |
| `float32` | ~2.4GB  | More accurate past 20-30 seconds of continuous audio. |

A phrase caps at 18 seconds, so the two are hard to tell apart on anything
said to a panel.

The precision is sent to the child in its config: it decides which files are
downloaded, which count as downloaded, and which the child looks for.

### Downloaded, or not

`parakeet.cached()` checks for the files `onnx-asr` will look for, and
`parakeet.missing()` returns the ones that are absent:

```
encoder-model[.int8].onnx
decoder_joint-model[.int8].onnx
vocab.txt
encoder-model.onnx.data      # float32 only
```

File by file, because `snapshot_download(local_files_only=True)` finds the ref
and returns the folder without checking what is in it - an interrupted
download reads as complete. The `.data` sidecar is not in `onnx-asr`'s own
list either, so a cache missing 2.4GB passes its check and fails on load.

```
python3 hactl.py speech-model
```

Answers locally, and names the files still missing.

### Asked once

| State                    | What happens                                                            |
|--------------------------|-------------------------------------------------------------------------|
| Cached                   | Start. Any record of a decline is dropped.                              |
| Not cached, not declined | A confirm dialog naming the model and its size.                         |
| Declined                 | Recorded in `speech-models.declined`, and the assistant does not start. |
| Download failed          | Not recorded as a decline. Asked again next start.                      |

Choosing a model afresh in Settings clears its record - picking it is asking
for it.

`download_speech_model` fetches on a worker thread and finishes through
`call_on_ui`. Not in the child, which loads its model before its socket
exists; not on the UI thread, which is the same frozen panel by another route.
`fetch()` re-checks the cache afterwards rather than treating "did not raise"
as success.


## The child

Two classes. `ParakeetListener` owns the microphone, the spotter and the
transcriber; `ParakeetServer` owns the ports and the protocol.

| Constant         | Value | Is                                            |
|------------------|-------|-----------------------------------------------|
| `SAMPLE_RATE`    | 16000 | What both models want.                        |
| `WINDOW_MS`      | 30    | webrtcvad takes 10, 20 or 30.                 |
| `PRE_CONTEXT_MS` | 420   | Lead-in, so a phrase does not start mid-word. |
| `SILENCE_MS`     | 700   | Ends a phrase in wake mode.                   |
| `MIN_SPEECH_MS`  | 200   | Below this it is a cough, not a phrase.       |
| `MAX_PHRASE_MS`  | 18000 | `onnx-asr` guidance is 20-30s.                |
| `LEVEL_EVERY`    | 3     | One meter report per three speech windows.    |

Two threads, and the audio thread never waits. Anything longer than a window -
a socket write to a slow parent, a model run - drops audio, and dropped audio
truncates whatever is said next. Transcription happens on its own thread, off
a queue.

There is no noise-reduction pass. Parakeet was trained on noisy speech, and
de-noising a whole utterance costs a large fraction of a core to make clean
audio sound underwater.

The transcript IS cleaned before it is sent - `__clean()` drops an invented
utterance and trims invented edges off a real one, through `normalize`. Doing
it here as well as on the client is the point: a transcript that reaches the
panel has already been drawn on the voice bar and handed to every listener,
and only then does routing decide it was nothing. A phrase dropped in the
child was never said.

`normalize` is imported defensively. Without it the panel still hears, it just
sends the occasional invented phrase for the client to drop.

### Points worth knowing if you change it

- **It is spawned as a script**, so `sys.path[0]` is `src/assistant/` and the
  project root is nowhere. `_add_project_root()` puts it there and must stay
  above everything that imports `src.*`. Get this wrong and both imports
  raise, both are caught, and the panel starts with both features off.
- **The log sink is set first.** `send_log` falls back to `print` when it has
  none, so anything reporting before it is assigned never reaches the panel.
- **Every message is newline-terminated and the client buffers.** `recv`
  returns bytes, not messages. Two `sendall` calls a moment apart arrive as
  one string, and a reader that splits once per read swallows the second into
  the first's payload.
- **The processing loop returns only for the shutdown sentinel.** Returning
  early ends the worker, and nothing restarts it.
- **Every pass that announced itself says when it finished**, or the panel
  reads "thinking" until something else moves it.
- **Nothing half-starts.** `prepare()` returns a reason, runs after the
  sockets are up, and sleeps either side of reporting so the reason arrives.

### The protocol

Two ports: `65432` for commands in, `65433` for events out. Messages are
`host:<event>:<payload>\n`.

| Message                     | Means                                      |
|-----------------------------|--------------------------------------------|
| `host:notify:Ready!`        | The child is up.                           |
| `host:woke:<word>`          | The wake word fired.                       |
| `host:voice_activity:<0-1>` | Input level, while capturing.              |
| `host:transcribing:1`       | Audio captured, the model is running.      |
| `host:transcribe:<text>`    | A finished transcript.                     |
| `host:transcribed:1`        | The model finished, whatever it decided.   |
| `host:wait:<kind>`          | Woke, and nothing was said.                |
| `host:audio_error:<text>`   | Microphone trouble. Empty means recovered. |
| `host:log:<level>:<text>`   | Anything the child would have printed.     |

Commands are `server:<name>`:

| Command             | Does                                        |
|---------------------|---------------------------------------------|
| `STOP`              | Shut the process down.                      |
| `START_WAKE`        | Wait for the wake word again.               |
| `START_PASSTHROUGH` | Transcribe everything.                      |
| `MUTE`              | Capture nothing. The spotter keeps running. |
| `UNMUTE`            | Capture again.                              |

**Passthrough** transcribes everything with no wake word. A session uses it,
and so does the microphone test page.

A mode switch bumps a **generation** and drains the queue. A phrase carries
the generation it was captured under, and one from before a switch is dropped
before it is announced. Otherwise closing a conversation leaves whatever was
already finalised to arrive afterwards - each announcing `transcribing`, the
panel flashing THINKING with nothing woken, then standing back down.


## Where the time goes

```
[Parakeet]: Woke on 'alexa'.
[Parakeet]: Finalising - 1440ms spoken, 1980ms captured.
[Parakeet]: Final transcription (12ms queued, 340ms in the model): what is the weather
```

**Spoken** is the person. **Captured** is that plus the lead-in and the
silence that ended it; the gap between them grows in a noisy room. **Queued**
is the processing thread being busy. **In the model** is the model.

Three endings tell you where to look:

| Line                                     | Means                        |
|------------------------------------------|------------------------------|
| `Ignored Nms - too short to be a phrase` | Never reached the model.     |
| `Nothing transcribed - still listening`  | Reached it, came back empty. |
| `Final transcription`                    | Worked.                      |

A `Woke` with no `Finalising` after it is a wake nobody followed up on.


## What the panel shows

`client.ASSIST_STATUS` is one of `DORMANT`, `LIVE`, `LISTENING`, `THINKING`,
`ACTING`. Four stages reach the voice bar:

```
1. listening     Listening…                    (wake fired)
2. transcribing  Working out what you said…    (the model is running)
3. understood    "what is the weather"         (a phrase arrived)
4. answered      Kitchen lights off            (held, outlives the status)
```

Stage 2 has to stay up unaided - nothing else arrives while the model runs -
so the child sends `transcribing` before it and `transcribed` after, rather
than leaving the bar to infer it from the status.

The status alone is not enough for the rest either. A skill that runs in
twenty milliseconds takes LISTENING → THINKING → LIVE between two polls of a
200ms timer, so THINKING is never drawn and the panel appears never to have
understood anything. Hence the events, and hence the hold timer that keeps a
message readable after the status has moved on.

`client.thinking("why")` holds the pill at THINKING while something slow runs.
It is counted rather than a flag, and restores whatever the status was before
the first hold.

The AI fallback plugin has its own pill inside its panel, because a
full-screen card covers the bar. See
[Bundled plugins](bundled-plugins.md#the-pill-in-the-panel).


## Speaking

```python
client.say("Kitchen lights off")
```

Returns whether anything was said. Pocket TTS is the default backend, needs no
key and runs locally; `audio.speech.tts_enabled` turns replies off without
affecting skills. `PocketTTSProcessing` exposes `.available` and `.error` for
the specific reason when there is none.

Playback is written in tenth-of-a-second pieces so a stop lands where it was
asked for. The stream is **stopped** before it is closed - closing directly
discards pending buffers and every reply loses its final syllables.

### Hearing itself

**Nothing is captured while the panel is speaking.** `client.say()` sends
`MUTE` before playing and `note_speech_ended()` sends `UNMUTE` when the
backend reports it has finished. While muted the child runs the spotter and
captures no phrases, so a reply read into a live microphone is never
transcribed and never routed.

That is the guard that holds. The two below are text comparisons and can be
argued with - a reply the voice cut short, a transcriber that heard it
differently - so they are the fallback for when the command does not arrive.

The mute lifts itself after `MUTE_DEADLINE` (120s). Releasing it depends on a
message from another process, and a client that died mid-reply would
otherwise leave a panel that never records anything again.

`heard_itself()` asks **was the panel talking**. It covers audio captured
during speech, and holds for `self_hearing_grace()` afterwards.

`echoed()` asks **is this what I just said**: a session holds the microphone
open while a long reply is read into it, and the fragments are finalised on
the pauses and transcribed *after* speech ends - so the first guard has
already expired.

`client.say()` records the text before speaking it, keeping the last
`SPOKEN_MEMORY` (4) replies. A transcript is compared against all of them:

|                 |                                                                       |
|-----------------|-----------------------------------------------------------------------|
| Window          | `ECHO_WINDOW`, 20s from when speech ENDED, or any time it is speaking |
| Shortest judged | `ECHO_MIN_WORDS`, 3 - below that it is "yes" or "stop"                |
| Match           | `ECHO_RATIO`, 0.8, on WORDS against a same-length window of the reply |
| Never an echo   | Anything containing the wake word                                     |

Three details, each of which lets a loop through on its own:

- **From when speech ended, not when it started.** A forty-second reply has
  used a window measured from the start before its last phrase is finalised -
  and that last phrase is the one that gets through, because everything
  before it was still being spoken and `heard_itself()` had already caught it.
- **Several replies, not one.** Once a loop is running the panel is answering
  itself, so a fragment of the previous reply arrives after the next one has
  been spoken. A single slot has been overwritten by then.
- **Words, not characters.** On characters "turn the bedroom lights off"
  scores 0.87 against "turned the kitchen lights off", so asking about a
  different room reads as the panel repeating itself.

The wake word is never an echo, because the panel never says it. That is also
the way out of the case this gets wrong: the reply suggests something, the
person asks for exactly that, and the words match because they were the
panel's words first. Saying the wake word first always gets through.

A near miss - 0.5 or better and still refused - is logged at `debug` with its
score, so the threshold can be read off a real transcript rather than guessed.

### Speaking over a reply

The wake word interrupts. It is never the panel hearing itself, because the
panel never says it, so it comes through whether or not there was anything to
stop - which matters most in the seconds after a reply, when somebody is
answering what was just said.

**An interrupted reply leaves no grace behind.** The grace covers the panel
overhearing the tail of its own voice, and a reply cut off mid-word has no
tail - what is in the room instead is the person who interrupted, mid-sentence,
saying the thing the wake word was said in order to ask. Both interrupt paths
clear `spoke_until`, and clearing it is not enough on its own: `tts.stop()`
returns before the playback thread has noticed, so that thread reaches
`note_speech_ended()` a moment later and stamps the grace straight back over
the clear. `note_interrupted()` is what makes the clear stick. From the log the
symptom is a wake word interrupting correctly, the microphone reopening
correctly, and the question that followed dropped as an echo two seconds
afterwards.

Everything else heard while the panel speaks is dropped as self-hearing, for
`self_hearing_grace()` after it finishes. A microphone array that runs its own
AEC shortens that: `audio.devices.mic_processing = hardware` uses
`HARDWARE_GRACE` (0.6s) instead.

`INTERRUPT_SETTLE` covers the tail. The microphone was recording while the
panel spoke, and the transcriber only runs on silence, so the last thing it
was saying arrives just after it was stopped, looking like a question.


## Routing a transcript

```
transcript -> pre_processing -> normalize -> routing -> skill
```

`pre_processing()` is the microphone's path: it checks self-hearing, drops
known noise, normalises, and routes. `submit()` is for anything handed a
phrase it did not hear - `/process?q=...` and the API - and goes straight to
the skills, because a typed request has no wake word to find.

Inside a **session** the route changes: `for_session()` strips the wake word
and hands what follows to whatever is waiting, rather than searching skills.
A session is opened by a skill expecting a follow-up, and switches the child
to passthrough.

`normalize.py` cleans a transcript before the intent engine sees it - numbers
written as words, filler at the edges, spacing.

**Clock times are settled before the number pass.** A transcriber writes
"eleven fifty am", and `words_to_numbers` reads a run of number words as ONE
number and sums it - so that arrives as `61 am` and the minutes are gone
before any skill sees it. `meridiem()` collapses every spelling of the suffix
(`a m`, `a.m.`, `A.M.`) to one token, then `spoken_clock()` turns an hour
followed by a spoken minute into digits:

| Said                | Becomes                  |
|---------------------|--------------------------|
| eleven fifty am     | `11:50 am`               |
| four forty p m      | `4:40 pm`                |
| ten oh five am      | `10:05 am`               |
| seven thirty        | `7:30`                   |
| twenty five minutes | `25 minutes` - unchanged |

Narrow on purpose. An hour is 1-12; a minute is a tens word, a teen, or "oh"
and a digit. "four five" stays two numbers, because that is somebody counting
rather than saying a time.

**And the colon has to survive the trip.** `STTProcessing.clean_text()` runs
after normalising and before the Matcher. Stripping every punctuation
character turns `11:46` into `1146` and `o'clock` into `oclock`, so a clock
time stops being one on its way to the skill that asked for it. Punctuation
is dropped except where it is inside a number - `11:46`, `3.5` - or inside a
word - `o'clock`, `don't`. See
[Writing skills](skills.md) for how matching works from there.

When nothing matches, `on_assistant_fallback` fires. The AI fallback plugin
subscribes to it.

### What the transcriber makes up

A transcriber handed room tone rather than speech writes boilerplate - sign-
offs, subtitle credits, one word repeated - confidently. It also appends its
habits to the end of a real phrase asked with a pause after it, so both of
these arrive as one utterance with an invented half in it:

```
i like that what is the weather
what is the weather thanks for watching
```

Two functions, two questions:

|                             |                                                  |
|-----------------------------|--------------------------------------------------|
| `is_hallucination(text)`    | Is the WHOLE utterance invented. Drops it.       |
| `strip_hallucination(text)` | Takes known filler off either END of a real one. |

Both run in the speech process before a transcript is sent, and again in
`pre_processing()` for anything that did not come from the microphone.

Two rules keep the trim from doing harm:

- **Only the ends.** Cutting from the middle would take real speech with it.
- **Its own list** (`EDGE_NOISE`), not `HALLUCINATIONS`. That one holds single
  common words - "you", "music", "right" - fine to reject as a whole
  utterance and ruinous at the edge of one: "who are you" becomes "who are",
  and "what is that music" loses the music.

A phrase that is entirely boilerplate is `is_hallucination`'s answer to give;
taking an edge off "i like that" would leave "i". The test for adding to
`EDGE_NOISE` is whether anybody would say it as the first or last words of an
instruction, and it is stricter at the front - "i like that idea, remind me
later" is a sentence somebody could plausibly say.


## Testing the microphone on its own

Settings → **Microphone test**. A session is started by hand, every transcript
lands in a list as it was heard, and it stops when told. Nothing is routed.

The point is to tell "the microphone is not working" apart from "the wake word
is not matching" apart from "the skill is not firing", which is impossible
while all three are in the way of each other. It puts the child into
passthrough through `STT.start_monitor()`, so no wake word is needed.

`STT.add_listener()` adds a watcher that sees every transcript before
normalising or routing, without consuming it.


## Saying hello

The notification always appears when the microphone comes up; whether it is
spoken is `assistant.feedback.greet_on_start`, off by default. It names
`client.wake_word` - the configured one, not whichever skill registered first.


## Cross-platform note

Device names and the default device differ per platform, and PortAudio
renumbers when anything is plugged in - so devices are held by **name** and
translated at the point of use. A saved device that is not connected means the
system default rather than an error. See
[When it will not start](when-it-will-not-start.md) for the failures that hang
rather than raise.
