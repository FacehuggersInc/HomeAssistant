"""
The spaCy model, loaded once and shared by everything that matches text.

One model and one vocabulary for the whole panel. Every `Matcher` and
`PhraseMatcher` has to be built against the same `Vocab` as the documents it
is run over, so handing them all the same model is not an optimisation - it is
the only arrangement that works.

**A model is not a package dependency.** `pip install spacy` installs the
library and no weights; the model is a separate download, and the line about
it in requirements.txt is a comment, which nothing runs. So a virtual
environment rebuilt from that file has the library and no model, and the first
`Skill` built raises `OSError` from inside plugin loading - a traceback about
`en_core_web_sm` on a stack that mentions coin flips. It is fetched here
instead, because this is the only place that knows it is missing.
"""

import importlib
import subprocess
import sys
from threading import Lock

#What every vocabulary comes from. The small model on purpose: this tags one
#short phrase at a time, and the larger ones cost startup and memory for
#accuracy nothing here reads.
MODEL_NAME = "en_core_web_sm"

#Turned off rather than merely unused. The dependency parse and the entity
#recogniser are the two most expensive stages in the pipeline, and the skill
#engine asks neither of them anything.
DISABLED = ["parser", "ner"]

#Long enough for the download on a slow connection, short enough that a panel
#is not sitting on a hung pip forever with nothing on screen.
DOWNLOAD_TIMEOUT = 600

_MODEL = None
_MATCHER = None
_LOCK = Lock()

#Where a line about the download goes. Set by the client once it has a logger;
#before that there is nothing to log through, and the download is exactly the
#kind of several-second pause that has to say what it is doing.
_log = None


class ModelMissing(RuntimeError):
    """The model is not installed and could not be fetched."""


def set_log(log) -> None:
    """Send this module's few lines through the panel's log."""
    global _log
    _log = log


def _say(level: str, message: str) -> None:
    # Printed when there is no logger yet. This runs before and during
    # startup, and a silent several-second pause is the thing being avoided.
    if callable(_log):
        try:
            _log(level, f"[NLP] {message}")
            return
        except Exception:
            pass
    print(f"[{level.upper()}][NLP] {message}")


def download() -> bool:
    """
    Fetch the model. Returns whether it worked.

    `sys.executable -m spacy` rather than a bare `spacy`, for the same reason
    `src/dependencies.py` runs pip that way: a panel with a virtual
    environment and something else on PATH installs into the wrong
    interpreter, and the symptom is identical to not having installed at all.
    """
    _say("info", f"'{MODEL_NAME}' is not installed - fetching it now. This "
                 f"happens once and takes a moment.")
    try:
        done = subprocess.run(
            [sys.executable, "-m", "spacy", "download", MODEL_NAME],
            capture_output=True, text=True, timeout=DOWNLOAD_TIMEOUT)
    except subprocess.TimeoutExpired:
        _say("warning", f"The download did not finish within "
                        f"{DOWNLOAD_TIMEOUT}s. Giving up on it.")
        return False
    except Exception as exc:
        _say("warning", f"Could not run the download: "
                        f"{type(exc).__name__}: {exc}")
        return False

    if done.returncode != 0:
        _say("warning", f"The download failed ({done.returncode}).")
        # The last few lines, not the whole of pip's output. The reason is at
        # the end of it.
        for line in (done.stderr or done.stdout or "").strip().splitlines()[-8:]:
            _say("warning", f"    {line}")
        return False

    # The package landed in a site-packages directory this process has
    # already scanned, so the import machinery does not know it is there.
    # Without this the load below fails exactly as it did before the
    # download, which reads as the download having done nothing.
    importlib.invalidate_caches()
    _say("info", f"'{MODEL_NAME}' installed.")
    return True


def _load():
    import spacy

    try:
        return spacy.load(MODEL_NAME, disable=DISABLED)
    except OSError:
        # Not there. Only this branch is worth a download - a model that is
        # present and broken is a different problem, and reinstalling it on
        # every start would hide it.
        pass

    fetched = download()
    try:
        return spacy.load(MODEL_NAME, disable=DISABLED)
    except OSError as exc:
        why = ("the download did not produce a usable one" if fetched
               else "it could not be downloaded")
        raise ModelMissing(
            f"The '{MODEL_NAME}' spaCy model is what turns a phrase into "
            f"something a skill can match, so the panel cannot understand "
            f"anything said to it without one - and {why}.\n"
            f"Install it with:\n"
            f"    {sys.executable} -m spacy download {MODEL_NAME}"
        ) from exc


def model():
    global _MODEL
    if _MODEL is None:
        with _LOCK:
            if _MODEL is None:
                _MODEL = _load()
    return _MODEL


def shared_matcher():
    global _MATCHER
    if _MATCHER is None:
        # The model FIRST, and outside the lock.
        #
        # `model()` takes this same lock and it is not reentrant, so
        # resolving the vocabulary from inside the block below deadlocks the
        # moment anything asks for the matcher before the model.
        # `Skill.__init__` asks in the safe order, which is why a panel does
        # not sit on it - but `SkillRegistry.matcher` is a property anything
        # may reach first, and it would hang the thread that did with no
        # error and nothing in the log.
        vocabulary = vocab()
        with _LOCK:
            if _MATCHER is None:
                from spacy.matcher import Matcher
                _MATCHER = Matcher(vocabulary)
    return _MATCHER


def vocab():
    return model().vocab


def new_matcher():
    from spacy.matcher import Matcher
    return Matcher(vocab())


def new_phrase_matcher(attr: str = None):
    from spacy.matcher import PhraseMatcher
    return PhraseMatcher(vocab(), attr=attr) if attr else PhraseMatcher(vocab())


def preload() -> None:
    """
    Load it now rather than at the first phrase.

    Called before the plugins, which build `Skill`s, which need it. The model
    loads on first use either way; doing it here means any download happens at
    a moment with a log line around it, rather than halfway through loading
    whichever plugin happened to declare the first skill.
    """
    model()
    shared_matcher()


def is_loaded() -> bool:
    return _MODEL is not None
