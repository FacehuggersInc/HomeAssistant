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
        try:
            import onnx_asr
            # Downloaded on first use and cached by the hub, like the Whisper
            # models. It is around 600MB, so the first launch after choosing
            # it is slow and every one after is not.
            self.log("info", f"[Parakeet]: Loading '{self.model_id}' "
                             f"(downloaded on first use)...")
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


def load(name: str, log=None) -> Optional["Parakeet"]:
    """
    A Parakeet for this model name, or None if it is not one.

    None rather than an exception: the caller uses it to decide which
    transcriber to build, and "not a Parakeet" is an ordinary answer.
    """
    if not is_parakeet(name):
        return None
    return Parakeet(name, log=log)
