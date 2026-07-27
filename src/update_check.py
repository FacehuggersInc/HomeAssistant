"""
Knowing whether an update exists, without downloading one.

`updater.stage()` pulls the whole branch zip, which is far too heavy to run on
a timer. This asks GitHub for the head commit of the tracked branch instead -
one small JSON reply - and compares it against a marker written when an update
was last applied.

The repo, owner and branch are all derived from REPO_ZIP_URL rather than
written out again, so there is still exactly one place that says where this
install comes from.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.constants import INSTALL_ROOT, REPO_ZIP_URL

# Written at the root of the install, beside .update-staging, and preserved
# across updates the same way (see UPDATE_PRESERVE).
VERSION_FILE = INSTALL_ROOT / ".update-version.json"

USER_AGENT = "HomeAssistant-UpdateCheck"
TIMEOUT = 10

_ZIP_URL_PATTERN = re.compile(
    r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/archive/refs/heads/(?P<branch>.+)\.zip$"
)


class UpdateCheckError(Exception):
    pass


def repo_parts(url: str = REPO_ZIP_URL) -> tuple[str, str, str]:
    match = _ZIP_URL_PATTERN.search(url)
    if not match:
        raise UpdateCheckError(f"Cannot read owner/repo/branch out of '{url}'")
    return match.group("owner"), match.group("repo"), match.group("branch")


def commits_api_url(url: str = REPO_ZIP_URL) -> str:
    owner, repo, branch = repo_parts(url)
    return f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}"


class Commit:
    """The head of the tracked branch, as far as GitHub is concerned."""

    def __init__(self, payload: dict):
        self.sha: str = str(payload.get("sha") or "")
        commit = payload.get("commit") or {}
        author = commit.get("author") or {}

        self.message: str = str(commit.get("message") or "").strip()
        self.author: str = str(author.get("name") or "unknown")
        self.date_raw: str = str(author.get("date") or "")
        self.url: str = str(payload.get("html_url") or "")

    @property
    def short(self) -> str:
        return self.sha[:7]

    @property
    def summary(self) -> str:
        """First line only. Commit bodies can run for paragraphs."""
        return self.message.splitlines()[0] if self.message else "(no message)"

    @property
    def date(self) -> Optional[datetime]:
        if not self.date_raw:
            return None
        try:
            return datetime.fromisoformat(self.date_raw.replace("Z", "+00:00"))
        except ValueError:
            return None

    def age(self) -> str:
        when = self.date
        if when is None:
            return "unknown"
        delta = datetime.now(timezone.utc) - when
        seconds = int(delta.total_seconds())
        if seconds < 3600:
            return f"{max(1, seconds // 60)} minutes ago"
        if seconds < 86400:
            hours = seconds // 3600
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        days = seconds // 86400
        return f"{days} day{'s' if days != 1 else ''} ago"

    def as_dict(self) -> dict:
        return {"sha": self.sha, "message": self.summary,
                "author": self.author, "date": self.date_raw, "url": self.url}


## -- REMOTE ------------------------------------------------------------------

def latest_commit(timeout: int = TIMEOUT) -> Commit:
    request = urllib.request.Request(
        commits_api_url(),
        headers={"User-Agent": USER_AGENT,
                 "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code == 403:
            # Unauthenticated calls are limited to 60 an hour per IP. Worth
            # naming, because it looks identical to being offline otherwise.
            raise UpdateCheckError("GitHub rate limit reached; try again later") from e
        raise UpdateCheckError(f"GitHub returned {e.code}") from e
    except urllib.error.URLError as e:
        raise UpdateCheckError(f"Could not reach GitHub: {e.reason}") from e
    except (ValueError, OSError) as e:
        raise UpdateCheckError(f"Unreadable reply from GitHub: {e}") from e

    commit = Commit(payload if isinstance(payload, dict) else {})
    if not commit.sha:
        raise UpdateCheckError("GitHub reply contained no commit sha")
    return commit


## -- LOCAL MARKER ------------------------------------------------------------

def read_marker() -> Optional[dict]:
    try:
        return json.loads(VERSION_FILE.read_text())
    except (OSError, ValueError):
        return None


def installed_sha() -> Optional[str]:
    marker = read_marker()
    return (marker or {}).get("sha") or None


def write_marker(commit: Commit, note: str = "") -> None:
    payload = commit.as_dict()
    payload["recorded_at"] = datetime.now(timezone.utc).isoformat()
    if note:
        payload["note"] = note
    try:
        VERSION_FILE.write_text(json.dumps(payload, indent=2))
    except OSError:
        # Not fatal. Losing the marker means the next check re-baselines, which
        # is a missed notification rather than a broken install.
        pass


## -- THE CHECK ---------------------------------------------------------------

def check(timeout: int = TIMEOUT) -> tuple[bool, Optional[Commit], str]:
    """
    Returns (update_available, latest_commit, explanation).

    On an install that has never recorded a version there is nothing to compare
    against, so the current head is written as the baseline and the answer is
    "up to date". Reporting an update instead would mean every fresh install
    started by nagging about one it already has.
    """
    commit = latest_commit(timeout=timeout)
    known = installed_sha()

    if not known:
        write_marker(commit, note="baseline recorded on first check")
        return False, commit, "First check - recorded the current version as the baseline."

    if known == commit.sha:
        return False, commit, "Up to date."

    return True, commit, f"{commit.short} is newer than the installed {known[:7]}."
