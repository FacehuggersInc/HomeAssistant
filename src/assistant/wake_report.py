"""
A record of why the panel woke, and why it did not.

Wake word trouble is two symptoms with one cause. Firing at the television and
missing somebody over an air conditioner look like opposite problems and are
usually the same one: the word arrived buried, and no threshold has a good
setting any more. Telling them apart needs numbers from the room they happen
in.

**A score that never fired is the half that was missing.** A wake writes a log
line already. Saying the word and getting nothing writes nothing at all, so
"it did not hear me" has never had a number attached to it - and 0.45 against
a bar of 0.5 and 0.02 against the same bar are completely different faults.
One is a setting; the other means the audio never carried the word.

Written from its own thread. The audio loop calls in every 30ms and must
never wait on a disk.
"""

import collections
import math
import os
import queue
import threading
import time


def _dbfs(pcm: bytes) -> float:
    """
    A window's level, in dBFS. Silence answers -90 rather than -inf.

    Computed by hand rather than with numpy: this runs on the audio thread
    for every window, and the array round trip costs more than the arithmetic.
    """
    if not pcm:
        return -90.0
    total = 0
    count = len(pcm) // 2
    if not count:
        return -90.0
    for index in range(0, count * 2, 2):
        sample = pcm[index] | (pcm[index + 1] << 8)
        if sample >= 32768:
            sample -= 65536
        total += sample * sample
    rms = math.sqrt(total / count)
    if rms < 1.0:
        return -90.0
    return max(-90.0, 20.0 * math.log10(rms / 32768.0))


class WakeReport:
    """
    One session of wake word behaviour, as a file somebody can read later.

    Every method is safe to call from the audio thread: they append to an
    in-memory queue and return. A daemon thread does the writing.
    """

    #A peak below this is room noise rather than a word that nearly made it.
    #Low enough to catch a badly buried "alexa", high enough that a quiet room
    #does not fill the file with nothing.
    NEAR_FLOOR = 0.15

    #A peak is over when the score has been under the floor for this long.
    #Without it one utterance reports as five near misses, because the score
    #wobbles either side of the floor while the word is being said.
    PEAK_SETTLE = 0.5

    #Transcribing costs a model run. A near miss is worth one; a noisy room
    #producing forty a minute is worth six, and the rest counted.
    TRANSCRIBE_PER_MINUTE = 6

    #How often the running totals are written out.
    SUMMARY_EVERY = 300.0

    #A run of unbroken VAD speech longer than this is worth a line of its own
    #while it is happening. Above the phrase limit would be too late to be a
    #warning and below a few seconds is ordinary speech.
    RUN_WORTH_SAYING = 4.0

    #Level is summarised once a second rather than 33 times.
    LEVEL_EVERY = 1.0

    #Indented fields are written as a label padded to this, then a space,
    #then the value. Named rather than repeated, because the reader takes the
    #value by column: labels here are two words as often as one, and anything
    #splitting on whitespace turns "near misses 0" into a field called "near".
    FIELD_WIDTH = 11

    #The file is capped and rolled once. A panel runs for months, and a report
    #that grows without limit is one that fills a disk on the quietest night
    #of the year.
    MAX_BYTES = 4_000_000

    def __init__(self, path, wake_word: str = "", log=None):
        self.path = str(path)
        self.wake_word = str(wake_word or "")
        self.log = log or (lambda level, message: None)

        self._lines = queue.Queue(maxsize=4096)
        self._stop = threading.Event()
        self._writer = None

        # -- counts, for the summary
        self.started_at = time.time()
        self.fires = 0
        self.near_misses = 0
        self.transcribed = 0
        self.skipped = 0
        #Peak scores, bucketed by tenth. Cheaper than keeping every score and
        #enough to see whether the model is scoring at all.
        self.buckets = [0] * 10

        # -- rolling level
        self._level_at = 0.0
        self._level_peak = -90.0
        self._level_sum = 0.0
        self._level_count = 0
        self._floor = collections.deque(maxlen=120)

        # -- the VAD
        #
        # The thing that actually decides whether a question ends. Constant
        # broadband noise keeps it saying speech, and then the silence that
        # finishes a phrase never arrives - so a wake word heard perfectly
        # still produces nothing. A ratio and a longest run say that in two
        # numbers.
        self.vad_speech = 0
        self.vad_windows = 0
        self._run_from = 0.0
        self._run_said = 0.0
        self.longest_run = 0.0
        # Named apart from capped() below: an instance attribute of the same
        # name shadows the method, and the call site would raise rather than
        # record the one event this whole file was added to catch.
        self.cut_short = 0
        #Wakes passed over because they matched something learned as noise.
        #Counted so a quiet panel can be told from a deaf one.
        self.suppressed = 0

        # -- peak tracking
        self._peak = 0.0
        self._peak_level = -90.0
        self._under_since = 0.0
        self._in_peak = False
        #Set by a fire, cleared once the room goes quiet again. The word does
        #not stop being said because the spotter recognised it: the score
        #falls away over the next several windows, and without this that tail
        #starts a fresh peak and is reported as a near miss for the very
        #utterance that just woke the panel.
        self._spent = False
        self._minute = 0.0
        self._this_minute = 0

        self._last_summary = time.time()

    ## -- lifecycle

    def start(self) -> None:
        try:
            folder = os.path.dirname(self.path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            self._roll_if_large()
        except OSError as exc:
            self.log("warning", f"[Wake] Report could not be opened: {exc}")
            return
        self._writer = threading.Thread(target=self._write_loop,
                                        name="__wake_report", daemon=True)
        self._writer.start()

    def stop(self) -> None:
        """
        Finish the report and wait for it to be on disk.

        The sentinel rather than the stop event, and joined afterwards. A
        writer that checked a flag first would exit with the final summary
        still in the queue - which is the one part of the file somebody
        actually reads.
        """
        if self._writer is None:
            return
        self.summary(final=True)
        try:
            self._lines.put(None, timeout=1.0)
        except queue.Full:
            self._stop.set()
        self._writer.join(timeout=3.0)
        self._writer = None

    ## -- writing

    def _say(self, line: str) -> None:
        if self._writer is None:
            return
        try:
            self._lines.put_nowait(line)
        except queue.Full:
            # Dropped rather than waited on. This is called from the audio
            # thread, and a full queue means the disk is slower than the
            # microphone - which is a reason to lose a report line, never a
            # reason to lose audio.
            pass

    def _write_loop(self) -> None:
        """
        Drain until the sentinel, with the file held open.

        Reopening per line costs a syscall pair on every window of a busy
        room. Flushed per line instead, so a panel that is pulled from the
        wall still has everything up to that moment.
        """
        handle = None
        try:
            handle = open(self.path, "a", encoding="utf-8")
        except OSError:
            # Nowhere useful to report this: complaining about the report file
            # through the log the report exists to supplement would be the
            # loudest possible way to say nothing.
            return
        try:
            while True:
                try:
                    line = self._lines.get(timeout=0.5)
                except queue.Empty:
                    if self._stop.is_set():
                        break
                    continue
                if line is None:
                    break
                try:
                    handle.write(f"[{time.strftime('%H:%M:%S')}] {line}\n")
                    handle.flush()
                except OSError:
                    break
        finally:
            try:
                handle.close()
            except OSError:
                pass

    def _roll_if_large(self) -> None:
        try:
            if os.path.getsize(self.path) < self.MAX_BYTES:
                return
        except OSError:
            return
        try:
            os.replace(self.path, self.path + ".1")
        except OSError:
            pass

    ## -- the opening block

    #ALSA's `default` and `pulse` devices answer with a channel count that is
    #a ceiling rather than a description - 128 on a laptop with one built-in
    #microphone. A warning that fires on that is a warning on every Linux
    #panel, which is the same as no warning at all.
    VIRTUAL_CHANNELS = 32
    VIRTUAL_NAMES = ("default", "sysdefault", "pulse", "pipewire", "jack")

    def virtual(self, name: str, channels: int) -> bool:
        """Whether this is a routing device rather than a microphone."""
        return (str(name or "").strip().lower().split(":")[0]
                in self.VIRTUAL_NAMES) or channels >= self.VIRTUAL_CHANNELS

    def opened(self, device, name: str, channels: int, rate: int,
               taken: int = 1, extra: dict = None, others: list = None,
               missing: list = None) -> None:
        """
        What the microphone is, and what was actually taken from it.

        The most consequential block in the file. A four-microphone array with
        on-board beamforming presents several channels, and taking one of them
        is not the same as taking the right one: on some the first channel is
        the processed output and on others it is a bare microphone with the
        array's own noise suppression bypassed. Every score below is a score
        of whatever this says.

        `others` is every input the machine has, so which index to set is
        answerable from the file rather than from a shell on the panel.

        `missing` is what the panel could see and this process cannot. That
        difference is a fault on its own - a microphone offered in Settings
        and unopenable by the thing that has to open it - and it is worth
        more than everything else in this block put together.
        """
        self._say("=" * 68)
        self._say(f"Wake report - '{self.wake_word}'")
        self._field("device", f"{name or device} (index {device})")
        self._field("channels", f"{channels} available, {taken} taken")
        self._field("rate", f"{rate} Hz")
        for key, value in (extra or {}).items():
            self._field(key, value)

        if self.virtual(name, channels):
            # Not a note about channels. Whatever this is routing to is the
            # thing the scores describe, and the report cannot see through it.
            self._field("NOTE", "this is the system default rather than a "
                                "named microphone, so the report cannot say "
                                "which hardware is being read. Set the input "
                                "device to name it.")
        elif channels and channels > taken:
            self._field("NOTE", f"this device offers {channels} channels and "
                                f"channel 0 is being read. On a microphone "
                                f"array that does its own processing, check "
                                f"which channel carries the processed output.")

        for line in (others or []):
            self._field("input", line)
        for line in (missing or []):
            self._field("MISSING", f"{line} - the panel offers this and this "
                                   f"process cannot see it, so it cannot be "
                                   f"opened from here")
        self._say("=" * 68)

    def settings(self, **values) -> None:
        """The numbers every score below should be read against."""
        self._say("Settings")
        for key, value in values.items():
            self._field(key, value)

    ## -- every window

    def window(self, score: float, pcm: bytes, speech: bool,
               bar: float, scored: bool = True) -> str:
        """
        One 30ms window. Answers `""`, `"fire"` or `"near"`.

        Called from the audio loop, so it does arithmetic and appends to a
        queue and nothing else. The peak logic lives here rather than at the
        call site because a peak spans windows and the loop has no memory.

        `scored=False` while a phrase is being captured, where the spotter is
        not run and the last score describes some earlier moment. The level
        and the VAD still count - that stretch is exactly when the question
        is being lost, and a report blind to it would miss the fault.
        """
        now = time.time()
        level = _dbfs(pcm)
        self._vad(bool(speech), now)

        # -- level, summarised once a second
        self._level_peak = max(self._level_peak, level)
        self._level_sum += level
        self._level_count += 1
        if not self._level_at:
            self._level_at = now
        elif now - self._level_at >= self.LEVEL_EVERY:
            average = self._level_sum / max(1, self._level_count)
            self._floor.append(average)
            self._level_at = now
            self._level_peak = -90.0
            self._level_sum = 0.0
            self._level_count = 0

        if not scored:
            return ""

        # -- the peak this score belongs to
        score = float(score or 0.0)

        if self._spent:
            # Waiting out the tail of something that already fired.
            if score >= self.NEAR_FLOOR:
                self._under_since = 0.0
                return ""
            if not self._under_since:
                self._under_since = now
                return ""
            if now - self._under_since >= self.PEAK_SETTLE:
                self._spent = False
                self._under_since = 0.0
            return ""

        if score >= self.NEAR_FLOOR:
            if not self._in_peak:
                self._in_peak = True
                self._peak = score
                self._peak_level = level
            else:
                if score > self._peak:
                    self._peak = score
                    self._peak_level = level
            self._under_since = 0.0
            return ""

        if not self._in_peak:
            return ""
        if not self._under_since:
            self._under_since = now
            return ""
        if now - self._under_since < self.PEAK_SETTLE:
            return ""

        # The peak is over. A fire has already been reported by the spotter,
        # so anything reaching here without one is a near miss.
        peak, level_at = self._peak, self._peak_level
        self._in_peak = False
        self._peak = 0.0
        self._under_since = 0.0
        if peak >= bar:
            return ""
        return self._near_miss(peak, bar, level_at, now)

    def _near_miss(self, peak: float, bar: float, level: float,
                   now: float) -> str:
        self.near_misses += 1
        self.buckets[min(9, int(peak * 10))] += 1

        if now - self._minute >= 60.0:
            self._minute = now
            self._this_minute = 0

        short = bar - peak
        floor = self.noise_floor()
        if self._this_minute < self.TRANSCRIBE_PER_MINUTE:
            self._this_minute += 1
            self.transcribed += 1
            self._say(f"NEAR   {peak:.2f} (bar {bar:.2f}, short by "
                      f"{short:.2f})  level {level:.0f} dBFS, "
                      f"floor {floor:.0f} dBFS")
            return "near"

        self.skipped += 1
        self._say(f"NEAR   {peak:.2f} (bar {bar:.2f}, short by "
                  f"{short:.2f})  level {level:.0f} dBFS "
                  f"- not transcribed, {self.TRANSCRIBE_PER_MINUTE}/min reached")
        return ""

    def _vad(self, speech: bool, now: float) -> None:
        """
        Count what the VAD said, and time how long it has been saying it.

        A ratio alone would not separate a room where somebody talks half the
        time from one where a fan never stops - and it is the unbroken run
        that ends a capture, not the average.
        """
        self.vad_windows += 1
        if speech:
            self.vad_speech += 1
            if not self._run_from:
                self._run_from = now
                self._run_said = 0.0
            running = now - self._run_from
            self.longest_run = max(self.longest_run, running)
            if (running >= self.RUN_WORTH_SAYING
                    and running - self._run_said >= self.RUN_WORTH_SAYING):
                self._run_said = running
                self._say(f"VAD    speech unbroken for {running:.0f}s "
                          f"- a phrase cannot end while this holds")
            return
        self._run_from = 0.0
        self._run_said = 0.0

    def speech_ratio(self) -> float:
        if not self.vad_windows:
            return 0.0
        return self.vad_speech / float(self.vad_windows)

    ## -- events

    def capped(self, ran_ms: int, spoken_ms: int) -> None:
        """
        A capture hit the length limit.

        The failure this report exists to catch: the word was heard, and then
        the room kept the VAD alive until the limit cut the question off.
        """
        self.cut_short += 1
        self._say(f"CAPPED {ran_ms}ms captured, {spoken_ms}ms of it called "
                  f"speech - the phrase never ended on its own")

    def ignored(self, score: float, bar: float, similarity: float,
                matched: str) -> None:
        """
        A wake that was recognised as a sound already known to be noise.

        Recorded rather than passed over silently. A panel that stops waking
        has to be able to say why it stopped, and the only alternative is a
        quiet one that looks broken.
        """
        self.suppressed += 1
        self._say(f"IGNORE {score:.2f} (bar {bar:.2f}) - matches {matched} at "
                  f"{similarity:.3f}, learned as noise")

    def judged(self, score: float, transcript: str, outcome: str,
               learned: bool, why: str) -> None:
        """What a wake turned out to be, once its turn had ended."""
        said = transcript.strip() or "nothing"
        self._say(f"  JUDGED    {score:.2f} ended as {outcome!r}, heard "
                  f"{said[:60]!r}")
        self._say(f"  {'LEARNED' if learned else 'KEPT':<9} {why}")

    def stood_down(self, why: str) -> None:
        self._say(f"DOWN   {why}")

    def fired(self, score: float, bar: float) -> None:
        self.fires += 1
        self.buckets[min(9, int(float(score or 0) * 10))] += 1
        # The peak is spent, and stays spent until the room is quiet. Without
        # this the same utterance is reported as a fire and then, as its score
        # falls away, as a near miss for the word that just worked.
        self._in_peak = False
        self._peak = 0.0
        self._under_since = 0.0
        self._spent = True
        self._say(f"WOKE   {float(score):.2f} (bar {bar:.2f})  "
                  f"floor {self.noise_floor():.0f} dBFS")

    def heard(self, kind: str, text: str) -> None:
        """What the audio around a fire or a near miss actually said."""
        text = str(text or "").strip()
        label = "WOKE ON" if kind == "fire" else "NEAR SAID"
        self._say(f"  {label:<10} {text!r}" if text
                  else f"  {label:<10} (nothing transcribable)")

    def _field(self, label: str, value: str) -> None:
        self._say(f"  {label:<{self.FIELD_WIDTH}} {value}")

    def note(self, message: str) -> None:
        self._say(str(message))

    ## -- summarising

    def noise_floor(self) -> float:
        """
        The quiet level of the room, as the tenth quietest second in two
        minutes. A median would follow a television; the low end follows the
        thing that never stops, which is what buries a wake word.
        """
        if not self._floor:
            return -90.0
        ordered = sorted(self._floor)
        return ordered[max(0, len(ordered) // 10)]

    def due(self) -> bool:
        return (time.time() - self._last_summary) >= self.SUMMARY_EVERY

    def summary(self, final: bool = False) -> None:
        self._last_summary = time.time()
        minutes = max(1.0, (time.time() - self.started_at) / 60.0)
        scored = sum(self.buckets)

        self._say("-" * 68)
        self._say(f"{'Final' if final else 'So far'} after "
                  f"{minutes:.0f} min")
        self._field("woke", f"{self.fires} "
                    f"({self.fires / minutes * 60:.1f}/hour)")
        self._field("near misses", f"{self.near_misses} "
                    f"({self.near_misses / minutes * 60:.1f}/hour), "
                    f"{self.transcribed} transcribed, {self.skipped} not")
        self._field("noise floor", f"{self.noise_floor():.0f} dBFS")
        self._field("vad speech", f"{self.speech_ratio() * 100:.0f}% of "
                                  f"windows, longest unbroken run "
                                  f"{self.longest_run:.0f}s")
        if self.cut_short:
            self._field("cut short", f"{self.cut_short} capture(s) hit the "
                                     f"length limit without ending")
        if self.suppressed:
            self._field("ignored", f"{self.suppressed} wake(s) matched a sound "
                                   f"learned as noise and were passed over")
        if scored:
            rows = []
            for index, count in enumerate(self.buckets):
                if count:
                    rows.append(f"{index / 10:.1f}-{(index + 1) / 10:.1f}: "
                                f"{count}")
            self._field("peak scores", "  ".join(rows))
        else:
            self._field("peak scores",
                        f"none above {self.NEAR_FLOOR} - nothing the model "
                        f"recognised even faintly. Check the microphone "
                        f"before the threshold.")
        self._say("-" * 68)
