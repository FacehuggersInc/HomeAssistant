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

The child and the socket reader are registered on
[`client.SERVICES`](services.md) as a process and its companion, so they start
and stop together and a child that dies is noticed.

Which recogniser starts is whoever provides `assistant.stt`. The panel provides
Parakeet; a plugin can [claim it](services.md#taking-one-over) and supply its
own, and everything above - the guards, the sessions, the routing - applies to
whatever is underneath. `STTProcessing` keeps the
half that is its own - building the command line, sending `STOP` - and the
escalation after that belongs to the registry.



## What it is doing

`client.STT.status()` is one snapshot rather than a handful of attributes -
`listening`, `processing`, `woke_at` and `process.poll()` read separately give
a different answer each depending on when they were asked.

| Key         |                                                                                              |
|-------------|----------------------------------------------------------------------------------------------|
| `state`     | One of `stopped`, `error`, `processing`, `awake`, `monitoring`, `held`, `listening`, `idle`. |
| `since`     | When that state started, or 0.                                                               |
| `for`       | How long it has been in it.                                                                  |
| `running`   | Whether the child process is alive.                                                          |
| `listeners` | Things watching transcripts without taking them.                                             |

Quick Settings shows a line for it, and **says nothing when the answer is
ordinary**. `idle` and `listening` are what a working panel looks like, so
reporting them would make the line permanent and unread. What it does report
is the states that should not last - awake with nobody following up,
transcribing, monitoring, stopped - with how long once that passes a minute,
and any transcript listener left behind by a page nobody closed.

## Settings

| Key                                   | Default       | Does                                                                      |
|---------------------------------------|---------------|---------------------------------------------------------------------------|
| `assistant.enabled`                   | `on`          | Whether the assistant runs at all.                                        |
| `assistant.speech.model`              | `parakeet-v3` | `parakeet-v3` (25 languages) or `parakeet-v2` (English, slightly faster). |
| `assistant.speech.parakeet_precision` | `int8`        | `int8` is ~700MB. `float32` is ~2.5GB and several times the memory.       |
| `assistant.wake.wake_word`            | `alexa`       | One of the four openWakeWord ships.                                       |
| `assistant.wake.wake_listen_timeout`  | `12` sec      | How long to wait for a phrase after waking.                               |
| `assistant.wake.max_phrase_seconds`   | `8` sec       | The longest one phrase may run before it is discarded.                    |
| `assistant.wake.wake_diagnostics`     | `off`         | Explain every wake in the log. See [What woke it](#what-woke-it).         |
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
| A phrase ends on                    | 700ms of silence.                                            |
| A phrase is DISCARDED at            | `assistant.wake.max_phrase_seconds` of continuous speech.    |
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


## What woke it

`assistant.wake.wake_diagnostics` explains every wake in the log. Two lines
per wake, and they answer different questions:

```
[Parakeet]: Woke on 'alexa' at 0.62 (bar 0.50).
[Parakeet]: Wake context: "so I asked her about the whole thing"
```

The score says how sure openWakeWord was and what bar it had to clear. A wake
at 0.94 and a wake at 0.51 are the same event from outside the panel and
completely different from inside it.

The transcript says what it was sure ABOUT, which the score cannot. Two
seconds of audio ending at the wake are kept in a ring and run through
Parakeet - so a wake nobody caused reads back as whatever was on television.
A wake that transcribes to nothing was not speech at all.

The ring is filled on **every** window rather than on the capture path,
because the spotter runs while muted and while disarmed. A ring filled where
audio is captured would be empty for exactly the wakes worth explaining.

Off by default: it holds two seconds of audio and runs the speech model an
extra time per wake. Turn it on, find out what is waking the panel, raise
`assistant.wake.wake_sensitivity` if the scores are marginal, and turn it off
again.

## What did not wake it

`assistant.wake.wake_report` is the other half, and it is **on by default**.
It writes `logs/wake.log`, and `/wake` renders the last session of it.

Wake word trouble is two symptoms with one cause. Firing at the television and
missing somebody over an air conditioner look like opposite problems and are
usually the same one: the word arrived buried, and no sensitivity has a good
setting any more. Raise it and the misses get worse; lower it and the false
fires do. Telling those apart needs numbers from the room they happen in.

### A near miss

A wake writes a line. Saying the word and getting nothing writes nothing at
all, so "it did not hear me" has never had a number attached to it - and 0.45
against a bar of 0.50 and 0.02 against the same bar are completely different
faults. One is a setting. The other means the audio never carried the word,
and no setting will fix it.

A **near miss** is a peak that got above `NEAR_FLOOR` and never reached the
bar:

```
NEAR   0.44 (bar 0.50, short by 0.06)  level -31 dBFS, floor -58 dBFS
  NEAR SAID  'a lexus dealership near you'
```

Transcribed from the same ring a wake uses, at most six a minute - a room with
a television in it produces far more than that, and the rest are counted
rather than run through the model.

**A fire spends its peak.** The score does not stop the moment the spotter
recognises the word; it falls away over the next several windows. Without
that, every successful wake also reported a near miss for the word that had
just worked.

### The VAD, and phrases that never end

What ends a phrase is `SILENCE_MS` of the VAD not calling speech. Constant
broadband noise - a fan, an air conditioner - keeps webrtcvad saying speech,
so that silence never arrives and every capture runs to `max_phrase_seconds`.
The wake word can be heard perfectly and the question still never gets asked.

The report counts it:

```
vad speech  94% of windows, longest unbroken run 8s
cut short   4 capture(s) hit the length limit without ending
```

and says so while it is happening, rather than only in the summary:

```
VAD    speech unbroken for 8s - a phrase cannot end while this holds
CAPPED 8010ms captured, 8010ms of it called speech - the phrase never ended on its own
```

A high ratio with short runs is a room where somebody talks a lot. A high
ratio with runs measured in whole seconds is a machine, and it is the thing
to fix.

The VAD is read for **every** window, not only while capturing, so the report
covers the stretch where a question is being lost rather than going blind
exactly then.

### Hitting the limit

A capture that reaches `max_phrase_seconds` is **transcribed and then stood
down**, not thrown away. The limit is about where a capture ends rather than
about whether the audio was meant for the panel: a wake word gated it, the
spotter is the confident part of the pipeline, and the only thing that ran
long was the room.

It stands down **without the timeout message**. That message tells the client
the turn ended with nothing, and part of that is forgetting which word woke
it - after which `routing()` scans the transcript for a wake word it cannot
contain, because the word was said before the capture began. The transcript
would be dropped by the same gate that let it through.

### The microphone line

The first block is the one worth reading:

```
device      ReSpeaker 4 Mic Array (index 3)
channels    6 available, 1 taken
rate        16000 Hz
NOTE        this device offers 6 channels and channel 0 is being read...
```

Capture opens `channels=1`, which takes the **first** channel of whatever the
device offers. On a microphone array that does its own beamforming and noise
suppression, the first channel is not always the processed one - on some
firmware it is a bare microphone with all of that bypassed. Every score in the
report is a score of whatever this line says, so it is the thing to settle
before touching a threshold.

`default` and its relatives are reported differently. ALSA answers with a
channel ceiling rather than a description - 128 on a machine with one built-in
microphone - so a channel warning there would fire on every panel, which is
the same as no warning at all. What the report says instead is that it cannot
see which hardware is being read, and that naming the input device fixes that.
Every input the machine has is listed underneath with its index and channel
count, so which one to set is answerable from the file.

### Which microphone, exactly

The panel picks a microphone by **name**, and the name is what travels to the
child. The index goes too, but only as a hint.

The two processes run separate PortAudio instances and enumerate separately,
and their lists have been observed to disagree on a real panel: a USB array
visible to one and missing from the other, with every index past that point
one lower in the child. An index chosen in the panel opened a different device
there, silently, and every score afterwards described the wrong microphone.

So the child looks the name up in its own list, and says so when the answer
surprises it:

```
asked for   reSpeaker XVF3800 4-Mic Array: USB Audio (hw:3,0)
chosen by   matched by name at index 8
```

Two failures are called out rather than quietly worked around. An index
mismatch is a **warning** naming both numbers, because it means the lists
disagree. A name that is not in the child's list at all is an **error** that
prints every input it can see - and it falls back to the system default rather
than to the hint, because a stale index is a different microphone, and a
different microphone reported as the one that was asked for is worse than an
honest fallback.

The input list in the report carries the host API for the same reason: when
two processes disagree about what the devices are, which backend each is
talking to is the first thing to compare. The panel's own list is sent across
and diffed against it, so a device offered in Settings that the speech process
cannot see is one line rather than two log files read side by side:

```
MISSING     [7] reSpeaker XVF3800 4-Mic Array: USB Audio (hw:3,0) - 6ch, ALSA
            - the panel offers this and this process cannot see it
```

A microphone that is the desktop's own default may be **absent from the
speech process entirely** while the panel can see it perfectly - claimed by
whatever routes system audio, and invisible to anything trying to open it
directly. The report says so, and setting the desktop default elsewhere while
naming the device here is what resolves it.

A name that is not found also triggers a **rescan** before it gives up.
PortAudio builds its device list once, when it starts, and the speech process
starts moments after the panel - so a USB microphone that was busy or still
settling at that instant is absent for the life of the process while the panel
that enumerated a second earlier sees it perfectly. Restarting PortAudio is the
only way to look again, it costs a fraction of a second, and it only happens
when something is already wrong.

`[Audio] input options: [...]` at startup says what the Settings dropdown was
filled with. "I set it in the app and nothing changed" has two causes that look
identical from outside - the change not saving, and the device never being
offered - and that line is what tells them apart.

### The summary

Written every five minutes and again on shutdown:

```
Final after 34 min
  woke        3 (5.3/hour)
  near misses 19 (33.5/hour), 12 transcribed, 7 not
  noise floor -58 dBFS
  peak scores 0.2-0.3: 4  0.4-0.5: 15  0.9-1.0: 3
```

The noise floor is the tenth-quietest second of the last two minutes rather
than the median. A median follows a television; the low end follows the thing
that never stops, which is what buries a wake word.

**Nothing in the distribution at all** is the answer to a different question.
It means the model is not recognising the word even faintly, which is a
microphone or a channel, not a threshold - and the summary says so rather than
leaving somebody to read an empty table.

### Reading it

`/wake` shows the last session only. The file accumulates across restarts, and
a page that averaged four sessions together would hide the thing somebody is
looking for, which is that it got worse after a change.

It leads with a sentence saying what the numbers mean, because somebody
standing at a panel with a phone wants to know whether to move a microphone or
change a number, and a table cannot say which. The whole file is a download
from there, or from `/logs/wake`.

The page polls while it is open: the useful way to read this is to stand in
the room saying the wake word and watch what lands.

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

A phrase caps at eight seconds, so the two are hard to tell apart on anything
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
| `MAX_PHRASE_MS`  | 8000  | Past this it is a television. Discarded.      |
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

### When it dies on its own

A speech process that exits without being asked leaves the panel deaf with
nothing on screen saying so: the pill reads whatever it read last and the wake
word does nothing, which from the room is a broken microphone.

`RESTART_POLICY` is `Restart(backoff=(0.0, 5.0, 30.0), window=120.0)` - now,
then in five seconds, then in thirty, then leave it down. A model that cannot
find its weights fails identically every time, so retrying faster only fills
the log; a child killed by something passing usually comes back on the first
attempt. A process that ran for longer than two minutes before dying starts the
count again.

Every attempt is logged. Giving up is an `error` and a notification, because at
that point nothing is listening and only this side knows it.

The reader is restarted with the child. `listening` therefore stays true across
the gap - it is what the reader's loop runs on, and clearing it would start a
fresh thread that reads the flag once and returns.


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
`ACTING`. It lives on [`SERVICES.STT`](services.md#listening-and-speaking)
and the client name reads it, so it survives the recogniser being restarted or
replaced underneath it. Four stages reach the voice bar:

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

### Who owns the voice

Speaking is one shared thing, and the most recent speaker owns it.
`TTS.claim()` hands out a token; `client.say()` takes one on every reply, and
`client.speech_owner()` gives it back to whoever caused it.

`TTS.stop(owner=token)` means *stop this only if it is still mine*. A holder
that has since been displaced is refused, because the voice it would cut off
belongs to whatever replaced it.

That is the whole point. An answer panel outlives its own voice: a weather
answer sits on screen, something else is asked, the new answer speaks and
opens its own panel - and then the weather panel times out and stops the
speech. An unconditional stop there cuts off a reply that was never its own.
Both the answer panel and the AI fallback's conversation therefore keep the
token they spoke under and hand it back when they close.

`TTS.stop()` with no token stops whatever is talking. That is for a person:
the wake word spoken over a reply, a finger on the action button, an explicit
cancel. A person outranks whatever the panel is in the middle of saying.

### A cancel word during the grace

`heard_itself()` is a **clock**, not a comparison: anything transcribed within
`SELF_HEARING_GRACE` of the panel finishing is treated as the tail of its own
voice. That is right for a fragment of prose and wrong for the one word
somebody is most likely to say at exactly that moment.

The wake word is already exempt, for the stated reason that the panel never
says its own wake word. A registered cancel phrase is exempt for the same
reason and one more: the panel does not speak in single words, so a transcript
that is *only* "stop" cannot be a fragment of a reply it just read.

Matched on the WHOLE phrase against `client.CANCEL.keywords()` - "stop the
timer" is a targeted cancel and goes through normal routing; "it stopped
raining" is prose. `echoed()` still runs afterwards, so a genuine echo that
happens to be short is still caught by content.

Without it, saying "stop" the moment a long reply ended did nothing, and the
panel had to be woken again before it would hear the word that means stop.

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
`SPOKEN_MEMORY` (4) replies on [`SERVICES.TTS`](services.md#listening-and-speaking). A transcript is compared against all of them:

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

### Interrupting a reply nobody has heard

The wake interrupt fires only once the reply is **audible**, not merely
`is_speaking()`.

A sentence spends a second or two being synthesised before any sound exists,
and `is_speaking()` counts that - correctly, for everything else that asks,
because an answer panel timing out over a reply still being made is worse. For
this one caller it is wrong. A wake word arriving during generation arrived
into silence, so it is the room rather than somebody talking over an answer,
and acting on it drops an answer that was never spoken.

A fan produces enough of those to swallow every reply in a row: the question
routes, the skill runs, `Stopped before it started - dropped` goes in the log,
and from the room the panel took the question and said nothing.

`TTS.is_audible()` is the narrower question. A backend that does not offer it
falls back to `is_speaking()`, which is no worse than not having asked.

### Stopping a reply that has not started

`stop()` works from the moment the text is accepted, not from the moment the
speakers begin. Generating a sentence takes seconds, and a stop raised in that
window has to be honoured - closing the AI conversation, saying "stop" and
tapping an answer all land there.

Two things make that true. `is_speaking()` counts generation as speaking,
because everything asking it wants to know whether the panel is in the middle
of saying something, and it is from the moment it agrees to. And the interrupt
flag is cleared **per request** rather than immediately before the playback
loop - clearing it there would wipe any stop raised while the sentence was
being made, which is the whole window a slow model spends.

A reply interrupted before playback is dropped rather than queued. Somebody
who interrupts is not asking for the rest of it later.

### Follow-ups have no subject

"Tell me more", "what does that mean", "can you elaborate" carry one content
word between them, and it is always a verb of asking. Scored normally, any
skill whose command reduces to that same verb matches perfectly - `tell-time`
against "tell me the time", `wiki-search` against "tell me about X" - so which
one answers depends on what happens to be installed.

`SUBJECTLESS_LEMMAS` catches them. A phrase whose content words are **all** in
that set has no topic of its own, so nothing here can be about it, and it goes
straight to `on_assistant_fallback` with the previous turn attached - which is
the only thing that can answer it.

Refused before the Matcher, not after. A hand-written pattern can match one of
these too: "what does that mean" fits the dictionary's trailing-verb pattern
and comes back with "Which word?", which is the panel asking somebody to repeat
context it was holding all along.

`GENERIC_LEMMAS` is the smaller companion rule, in the rule phase: a match
whose *entire* lexical overlap with an example is bare asking-verbs is not a
match. "Tell me a joke" and "tell me about Mount Fuji" agree about the word
"tell", which is a grammatical accident rather than a shared subject.

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


## Speaking somewhere else

`audio.speech.tts_where` is one of three:

| Value        |                                                          |
|--------------|----------------------------------------------------------|
| `local`      | The voice runs inside the panel. The default.            |
| `subprocess` | The voice runs beside the panel, on 127.0.0.1.           |
| `socket`     | The voice runs on another machine.                       |

`subprocess` and `socket` are the same server and the same client - the only
difference is the address. One implementation rather than two: a local mode
with its own code path would be a second thing to keep working.

**Why it is worth moving.** A neural voice holds a processor for a second or
two per sentence, and in this process that is a second or two where the
window, the web server and the microphone reader all wait for it - one
interpreter, one lock. Nothing can interrupt a model part way through either,
so a wake word during that gap does nothing at all.

Over a socket both problems go away. The panel does no synthesis, and stopping
a reply is a message on a second connection rather than a flag the model never
reads.

### The language setting

`audio.speech.tts_language` names the pretrained weights, and every option is
a language. It defaults to **english**, which is what the model falls back to
anyway.

An install from before that carries `default`, which was never a language -
the model went looking for weights by that name and would not load at all. Two
things handle it. `keep_valid()` in the updater drops an enum value the
shipped options no longer allow and takes the shipped default, so a settings
merge fixes it on its own. And four places translate a leftover rather than
trusting it: the local backend, the panel building the speech process's
command line, the package writing a startup script, and the server taking an
argument by hand.

The last one matters because that script is started by hand as often as it is
spawned, and `--language default` copied out of an older startup script is the
same mistake with none of the panel's code in the way.

**The options are not checked against the model.** Which languages a given
install ships depends on the package version - `french` is absent from some
while `french_24l` is present - so a language the model does not have fails at
load, with the list of what it does have in the message.

### The wire

`src/assistant/tts_protocol.py`, imported by both ends. JSON lines for
control, length-prefixed float32 frames for audio.

```
say     -> {"cmd":"say","text":"..."}      <- {"ok":true,"key":"s-7f3a12"}
stream  -> {"cmd":"stream","key":"..."}    <- header, then frames, then an end
cancel  -> {"cmd":"cancel","key":"..."}    <- on its own connection
```

**`say` answers before the model runs.** The panel is free the moment it
returns and can decide not to collect the audio at all - which is what happens
when somebody speaks again before the answer is ready.

**The session key exists so cancel has somewhere to go.** The streaming
connection is carrying audio, so nothing can be said on it. Cancel arrives on
another connection and names the session.

Three points to cancel at, and they differ. Still queued, the model never
runs. Already inside the model, the inference cannot be stopped - nothing
interrupts one - but the audio is dropped rather than spoken, and the wasted
work is on the machine with cycles to spare. Mid-transfer, the stream stops
sending and closes.

### Playback starts on the first chunk

The far end streams while it generates, so a long sentence begins being heard
before it has finished being made. The interrupt is checked **between
chunks**, which at a fifth of a second each is close enough to instant that
somebody talking over the panel hears it stop.

`is_audible()` becomes true when the first chunk reaches the output device,
not when the sentence was asked for - see
[Interrupting a reply nobody has heard](#interrupting-a-reply-nobody-has-heard).

### Speaking beside the panel

`subprocess` needs no second machine and no package. The panel spawns
`tts-socket-process.py` on the loopback through
[`client.SERVICES`](services.md), so it is supervised like anything else -
restarted if it stops, and taken down with the assistant.

The work still costs exactly what it cost. What changes is where it happens:
outside the interpreter the screen and the microphone share, so the panel
keeps drawing while a sentence is made, and a wake word during that gap can
stop it.

The process is stopped when the assistant stops. Left running it would hold
the port against the one the next start spawns, and the second would be silent
for a reason nobody would look for here.

### Waiting for it to be ready

A server on this machine spends its first seconds loading a model, and a
remote one can be restarted long after the panel was. So "cannot speak" is
**re-checked** rather than settled at startup - every few seconds, and never
while a reply is going out. A panel that had to be restarted whenever the far
end was would be the wrong way round.

That only works if the backend survives being built too early, and it is:
`RECOVERS = True` says a no from this one may become a yes without anything
being rebuilt, and `VoiceFacade.start()` **keeps** such a backend when nothing
is ready rather than discarding it. Discarding it throws away the only object
that knows how to ask again, and the panel then stays silent for the session
while reporting a backend that is missing - which was never true.

The log says which of the two it is:

```
Speech beside the panel on :8770: 127.0.0.1:8770 did not answer. The panel
  starts it here, so it is most likely still loading its model.
Speech beside the panel on :8770 is not ready yet - it will be asked again,
  and replies are silent until it answers.
```

rather than `No voice backend available`, which is reserved for the case where
nothing can work. A backend on the loopback is also told not to suggest
checking whether the server is running there: the panel started it, and being
sent to look at something the panel is responsible for sends somebody to the
wrong place.

### Setting it up

For `socket`: `host` and `port`, and the other machine needs
`tts-socket-process.py` running. [Packages](packages.md) has a download that builds it with this
panel's port and voice already in it.

The connection is checked once when the assistant starts, so an address that
is wrong says so in the log rather than the first time somebody speaks.

**There is no fallback to the local voice.** A panel set to speak elsewhere
and quietly using its own model when that machine is off would be a panel that
works, badly, for reasons nobody can see - and the local model is the thing
being moved off this hardware in the first place.

## Speech that is not for the panel

A television talks for minutes. A wake it caused is followed by whatever was
being said, and the old behaviour transcribed it: at the length cap the
buffer was finalised, so two sentences of somebody else's dialogue went to
the skill engine to be matched against. Long enough and some of it matches.

Past `assistant.wake.max_phrase_seconds` the audio is **discarded** and the
wake stands down. Nothing that long was said to the panel, so there is
nothing in the buffer worth keeping, and the next thing said starts a fresh
prompt rather than landing in the middle of an abandoned one.

The spotter is reset along with the wake state. It carries context between
frames, and context from audio that has just been thrown away describes
something no longer adjacent to what comes next.

Eight seconds is longer than any question anybody asks a wall panel and
shorter than any programme. The limit is counted in 30ms windows, so what is
set is rounded to the nearest one, and two seconds is the floor.

## Cancelling

"Stop" reaches the answer panel through `client.CANCEL`, and stopping it is
three things rather than one.

The reply stops and the card closes - both, because a voice with nothing on
screen behind it and a card nobody can dismiss by voice are each half a
cancel.

The cut is **marked as an interruption**, the same as a wake word spoken over
a reply. `tts.stop()` returns before the room does: there is still audio in
the output buffer and in the air, and the first thing captured after a cancel
is that tail. Unmarked, it is transcribed, matched against skills, and acted
on. `spoke_until` is cleared at the same time, because a reply cut mid-word
has no tail left to overhear and the grace would only suppress the next real
thing said.

And the **child is told**. This is the half that is easy to miss: clearing
the panel's wake state leaves the listener process exactly as it was.
`switch_mode()` therefore disarms and resets the spotter as well as bumping
the generation - an armed child goes on capturing whatever it hears next, so
a cancel that only cleared the panel's own state took the next sound in the
room as a phrase and put the panel back into LISTENING with nobody having
said the word. Saying "stop" again re-entered the same state, which is why it
took several.

Both callers of `switch_mode()` are deliberate transitions - opening a
conversation and leaving one - and neither wants a previous wake carried
across.

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

**A muted microphone gets a different greeting.** Every ordinary greeting
promises to be listening, which is exactly wrong when the mixer has the
microphone muted: the panel says it is ready, the wake word then does
nothing, and nothing on screen connects the two. Somebody stands there
repeating a word at a device that told them it was listening.

So `greet()` asks `client.mic_muted()` and picks from `MUTED_GREETINGS`
instead - each of which says the microphone is muted and none of which claims
to be listening. The wake word is left out of that version entirely: telling
somebody to say it while nothing can hear them is the problem being
described, not the fix for it. When it is spoken aloud, the whole sentence is
said rather than just the opening, because the explanation is the second half.


## Saying it rather than writing it

`speakable.py` runs the opposite way to `normalize.py`: an answer written for
the screen, turned into something a speech model can pronounce. It matters
most for the AI fallback, whose replies arrive as written prose.

Deliberately narrow, and shape-driven rather than word-for-word:

| Written              | Said                             |
|----------------------|----------------------------------|
| `3.14`               | 3 point 14                       |
| `The answer is 5.`   | unchanged - a full stop          |
| `(programming lang)` | `, programming lang,` - a pause  |
| `and/or`             | and slash or                     |
| `6 / 2`              | 6 divided by 2                   |
| `21°C`               | 21 degrees celsius               |
| `https://…`          | a link                           |

A period is only a decimal point with digits hard against it on both sides.
`5. Then` is two sentences and stays that way; `v1.2` is a version and reads
correctly as "one point two" anyway.

Brackets become a pause rather than a word. Nobody says "open parenthesis",
and reading straight through runs two clauses together - "Python programming
language" instead of "Python, programming language". The tidying afterwards
matters as much as the substitution: a bracket closing a sentence would
otherwise leave `,.`, and one at the end would leave a comma hanging.

Order matters. URLs and code are removed first, so a slash inside an address
is never "divided by"; units are expanded before bare symbols, so `°C` does
not become `degreesC`; and the generic slash runs after the between-numbers
rules, so `6 / 2` is division and `and/or` is not.

## Cross-platform note

Device names and the default device differ per platform, and PortAudio
renumbers when anything is plugged in - so devices are held by **name** and
translated at the point of use. A saved device that is not connected means the
system default rather than an error. See
[When it will not start](when-it-will-not-start.md) for the failures that hang
rather than raise.
