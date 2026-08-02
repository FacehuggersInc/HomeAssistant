"""
Transcribing with Parakeet, beside the Whisper path.

Two models with the same job and different tradeoffs, behind one small
interface so the processing loop does not have to know which it has.

Parakeet is NVIDIA's, run here through `onnx-asr` rather than NeMo. NeMo is
the usual route and it brings torch and several gigabytes with it; onnx-asr is
four megabytes and loads the same published weights. On a wall panel that
difference matters more than the last fraction of a percent of accuracy.

It is English and 24 other European languages, against Whisper's 99 - which is
why this is a choice rather than a replacement.
"""

from __future__ import annotations

from typing import Optional


#What onnx-asr calls the model. v3 is the current one; v2 is English-only and
#slightly faster, kept because a panel that only hears English may prefer it.
MODELS = {
    "parakeet-v3": "nemo-parakeet-tdt-0.6b-v3",
    "parakeet-v2": "nemo-parakeet-tdt-0.6b-v2",
}


def is_parakeet(name: str) -> bool:
    """Whether a model name belongs to this transcriber rather than Whisper."""
    return str(name or "").strip().lower() in MODELS


def available() -> tuple:
    """Whether Parakeet can be used at all. Returns (ok, reason)."""
    try:
        import onnx_asr  # noqa: F401
    except ImportError as e:
        return False, (f"onnx-asr is not installed ({e}). "
                       f"pip install 'onnx-asr[cpu,hub]'")
    except Exception as e:
        return False, f"onnx-asr could not be loaded ({e})"
    return True, ""


class Parakeet:
    """
    One loaded Parakeet model, transcribing float32 audio at 16kHz.

    The same shape the Whisper path presents: hand it audio, get text back.
    Nothing above this needs to know which one it is holding.
    """

    def __init__(self, name: str = "parakeet-v3", log=None):
        self.name = str(name or "parakeet-v3").strip().lower()
        self.model_id = MODELS.get(self.name, MODELS["parakeet-v3"])
        self.log = log or (lambda level, message: None)

        self.model = None
        self.ready = False
        self.reason = ""
        self._load()

    def _load(self) -> None:
        ok, why = available()
        if not ok:
            self.reason = why
            return
        if not cached(self.name):
            # Not on disk, and this is the speech process - which has no
            # socket yet, so a download here is minutes of silence with the
            # panel looking frozen. The panel fetches it before starting this
            # (see Client._start_assistant), so reaching here means that did
            # not work, and whisper is the answer rather than trying again
            # somewhere nobody can see.
            self.reason = (f"'{self.model_id}' is not downloaded yet")
            self.log("warning", f"[Parakeet]: {self.reason}.")
            return

        try:
            import onnx_asr
            self.log("info", f"[Parakeet]: Loading '{self.model_id}'...")
            self.model = onnx_asr.load_model(self.model_id)
            self.ready = True
            self.log("info", f"[Parakeet]: Ready.")
        except Exception as e:
            self.reason = f"Parakeet could not start: {e}"
            self.log("warning", f"[Parakeet]: {self.reason}")

    def transcribe(self, audio) -> str:
        """
        Text from float32 audio at 16kHz, or "" if there is none.

        Anything it raises is caught and reported rather than allowed out:
        this runs on the processing thread, and a raise there is a worker
        that stops transcribing with nothing said about why.
        """
        if not self.ready:
            return ""
        try:
            result = self.model.recognize(audio, sample_rate=16000)
        except Exception as e:
            self.log("warning", f"[Parakeet]: Transcription failed: {e}")
            return ""

        # A batch call answers with a list; a single one with a string. Only
        # single calls are made here, but the shape is worth handling rather
        # than assuming.
        if isinstance(result, (list, tuple)):
            result = result[0] if result else ""
        return str(result or "").strip()


def cached(name: str) -> bool:
    """
    Whether the weights are already on disk.

    Asked before starting, so the panel can say "downloading 600MB" rather
    than sitting silent for several minutes with nothing in the log.
    """
    model_id = MODELS.get(str(name or "").strip().lower())
    if not model_id:
        return False
    try:
        from huggingface_hub import snapshot_download
        snapshot_download(repo_id=_repo_for(model_id),
                          local_files_only=True)
        return True
    except Exception:
        return False


def _repo_for(model_id: str) -> str:
    """The HuggingFace repo onnx-asr pulls a model from."""
    # Hard-coded rather than read from onnx-asr: it does not expose the
    # mapping, and this is only used to ask whether the files are already
    # there. If it is wrong the answer is "not cached", which costs a
    # message rather than a failure.
    return f"istupakov/{model_id.replace('nemo-', '')}-onnx"


def fetch(name: str, log=None) -> tuple:
    """
    Make sure the weights are on disk. Returns (ok, reason).

    Done from the panel rather than the speech process, and BEFORE it is
    started. The child loads its model before its socket exists, so a
    download there is several minutes during which the panel can say nothing
    at all - it looks frozen, and the log stops mid-startup with no clue why.
    """
    log = log or (lambda level, message: None)
    ok, why = available()
    if not ok:
        return False, why

    model_id = MODELS.get(str(name or "").strip().lower())
    if not model_id:
        return False, f"'{name}' is not a Parakeet model"

    if cached(name):
        log("info", f"[Parakeet] '{model_id}' is already downloaded.")
        return True, ""

    log("info", f"[Parakeet] Downloading '{model_id}' - about 600MB, once. "
                f"The assistant starts when it finishes.")
    try:
        import onnx_asr
        # Loaded and thrown away. There is no download-only entry point, and
        # loading is what puts the files in the cache - the child then finds
        # them there and starts immediately.
        onnx_asr.load_model(model_id)
    except Exception as e:
        return False, f"could not download '{model_id}': {e}"
    log("info", f"[Parakeet] '{model_id}' downloaded.")
    return True, ""


def load(name: str, log=None) -> Optional["Parakeet"]:
    """
    A Parakeet for this model name, or None if it is not one.

    None rather than an exception: the caller uses it to decide which
    transcriber to build, and "not a Parakeet" is an ordinary answer.
    """
    if not is_parakeet(name):
        return None
    return Parakeet(name, log=log)
