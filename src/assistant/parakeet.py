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

from pathlib import Path
from typing import Optional


#What onnx-asr calls the model. v3 is the current one; v2 is English-only and
#slightly faster, kept because a panel that only hears English may prefer it.
MODELS = {
    "parakeet-v3": "nemo-parakeet-tdt-0.6b-v3",
    "parakeet-v2": "nemo-parakeet-tdt-0.6b-v2",
}


#Which weights to fetch.
#
#onnx-asr defaults to the full-size ones, and for these that is a 2.4GB
#encoder plus a separate file of weights beside it - about 2.5GB, over a
#panel's network onto a panel's SD card, and the reason a download that was
#described as "about 600MB" never seemed to finish. The int8 encoder is
#650MB, runs faster on a machine with no GPU behind it, and loses accuracy
#only on audio far longer than anything said to a wall panel.
DEFAULT_PRECISION = "int8"

#What each costs on disk, for the message shown before it starts.
SIZES = {"int8": "~700 MB", "": "~2.5 GB"}


def is_parakeet(name: str) -> bool:
    """Whether a model name belongs to this transcriber rather than Whisper."""
    return str(name or "").strip().lower() in MODELS


def quantization(precision: str = "") -> Optional[str]:
    """
    onnx-asr's word for a precision, or None for the full-size weights.

    None and "int8" are the two that matter here; anything else is passed
    through, so a repo that grows another variant needs no change.
    """
    value = str(precision or DEFAULT_PRECISION).strip().lower()
    if value in ("", "none", "full", "fp32", "float32"):
        return None
    return value


def size_hint(precision: str = "") -> str:
    return SIZES.get(quantization(precision) or "", "a few hundred MB")


def _required_files(quant: Optional[str]) -> tuple:
    """
    The files onnx-asr will look for, and refuse to load without.

    Mirrors `NemoConformerTdt._get_model_files` - both models here are TDT -
    with the sidecar the full-size encoder carries its weights in. That last
    one is NOT in onnx-asr's own list: it globs for the graph and finds it,
    then onnxruntime opens the graph and looks for the weights beside it. A
    half-downloaded cache passes its check and fails on load.
    """
    suffix = f".{quant}" if quant else ""
    files = [f"encoder-model{suffix}.onnx",
             f"decoder_joint-model{suffix}.onnx",
             "vocab.txt"]
    if not quant:
        files.append("encoder-model.onnx.data")
    return tuple(files)


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

    def __init__(self, name: str = "parakeet-v3", log=None, precision: str = ""):
        self.name = str(name or "parakeet-v3").strip().lower()
        self.model_id = MODELS.get(self.name, MODELS["parakeet-v3"])
        self.log = log or (lambda level, message: None)
        self.precision = precision
        self.quantization = quantization(precision)

        self.model = None
        self.ready = False
        self.reason = ""
        self._load()

    def _load(self) -> None:
        ok, why = available()
        if not ok:
            self.reason = why
            return
        if not cached(self.name, self.precision):
            # Not on disk, and this is the speech process - which has no
            # socket yet, so a download here is minutes of silence with the
            # panel looking frozen. The panel fetches it before starting this
            # (see Client._start_assistant), so reaching here means that did
            # not work, and whisper is the answer rather than trying again
            # somewhere nobody can see.
            absent = missing(self.name, self.precision)
            self.reason = (f"'{self.model_id}' is not downloaded yet "
                           f"(missing {', '.join(absent)})")
            self.log("warning", f"[Parakeet]: {self.reason}.")
            return

        try:
            import onnx_asr
            self.log("info", f"[Parakeet]: Loading '{self.model_id}' "
                             f"({self.quantization or 'full precision'})...")
            self.model = onnx_asr.load_model(self.model_id,
                                             quantization=self.quantization)
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


def cached(name: str, precision: str = "") -> bool:
    """
    Whether the weights are already on disk, and all of them.

    Asked before starting, so the panel can say "downloading" rather than
    sitting silent for several minutes with nothing in the log.

    File by file, because `snapshot_download(local_files_only=True)` answers
    a different question than it looks like: it finds the ref, returns the
    snapshot folder, and never checks what is in it. An interrupted download
    leaves that folder there with half its files, so the panel read "already
    downloaded", skipped the fetch, and handed the speech process a cache it
    could not load - which then downloaded the rest itself, before its socket
    existed, silently, every single start.
    """
    return not missing(name, precision)


def missing(name: str, precision: str = "") -> list:
    """Which of the needed files are not on disk. Empty means ready."""
    quant = quantization(precision)
    folder = _snapshot_folder(name)
    if folder is None:
        return list(_required_files(quant))

    absent = []
    for filename in _required_files(quant):
        path = folder / filename
        try:
            # is_file() follows the symlink into the blob store, so a name
            # left behind by a download that did not finish reads as absent
            # rather than as present.
            if not path.is_file() or path.stat().st_size == 0:
                absent.append(filename)
        except OSError:
            absent.append(filename)
    return absent


def _snapshot_folder(name: str) -> Optional[Path]:
    """Where these weights live on disk, or None if nothing was fetched."""
    model_id = MODELS.get(str(name or "").strip().lower())
    if not model_id:
        return None
    try:
        from huggingface_hub import snapshot_download
        return Path(snapshot_download(repo_id=_repo_for(model_id),
                                      local_files_only=True))
    except Exception:
        return None


def _repo_for(model_id: str) -> str:
    """The HuggingFace repo onnx-asr pulls a model from."""
    # Hard-coded rather than read from onnx-asr: it does not expose the
    # mapping, and this is only used to ask whether the files are already
    # there. If it is wrong the answer is "not cached", which costs a
    # message rather than a failure.
    return f"istupakov/{model_id.replace('nemo-', '')}-onnx"


def fetch(name: str, log=None, precision: str = "") -> tuple:
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

    quant = quantization(precision)
    absent = missing(name, precision)
    if not absent:
        log("info", f"[Parakeet] '{model_id}' is already downloaded.")
        return True, ""

    log("info", f"[Parakeet] Downloading '{model_id}' - {size_hint(precision)}, "
                f"once. The assistant starts when it finishes.")
    log("debug", f"[Parakeet] Fetching: {', '.join(absent)}")
    try:
        import onnx_asr
        # Loaded and thrown away. There is no download-only entry point, and
        # loading is what puts the files in the cache - the child then finds
        # them there and starts immediately.
        onnx_asr.load_model(model_id, quantization=quant)
    except Exception as e:
        return False, f"could not download '{model_id}': {e}"

    # Asked again rather than assumed. `load_model` returning is evidence
    # that this process could load it, not that the cache is complete - and
    # the whole point of the fetch is what the NEXT process will find.
    absent = missing(name, precision)
    if absent:
        return False, (f"'{model_id}' downloaded but is still incomplete "
                       f"({', '.join(absent)})")
    log("info", f"[Parakeet] '{model_id}' downloaded.")
    return True, ""


def load(name: str, log=None, precision: str = "") -> Optional["Parakeet"]:
    """
    A Parakeet for this model name, or None if it is not one.

    None rather than an exception: the caller uses it to decide which
    transcriber to build, and "not a Parakeet" is an ordinary answer.
    """
    if not is_parakeet(name):
        return None
    return Parakeet(name, log=log, precision=precision)
