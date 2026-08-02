"""
Turning "play everlong" into something with an ID.

The IFrame Player API plays a video; it cannot find one. Two ways to get from
a phrase to an ID, and the plugin will use whichever it can:

* the **Data API**, with a key. Ordered, documented, and it returns the
  channel and the artwork alongside the ID.
* **scraping the results page**, with no key. It works, and it breaks whenever
  YouTube changes its markup, so it is the fallback rather than the default.

Both run on a worker: this is called from a spoken request and a network round
trip on the UI thread would freeze the panel mid-sentence.
"""

from __future__ import annotations

import difflib
import json
import re
import urllib.error
import urllib.parse
import urllib.request


API = "https://www.googleapis.com/youtube/v3/search"
RESULTS = "https://www.youtube.com/results"
#YouTube Music. Worth having as its own source rather than as a nicer YouTube:
#a track is often filed under a translated or romanised title there and only
#its original title on YouTube - "Kaiju Girl" and "\u4e59\u5973\u602a\u7363"
#are the same song, and searching the one will not find the other.
MUSIC = "https://music.youtube.com/search"

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

#a search costs 100 of the 10,000 daily quota units, so roughly a hundred
#searches a day on a free key
QUOTA_PER_SEARCH = 100


class Result:
    """One thing that could be played."""

    __slots__ = ("video_id", "title", "artist", "art_url", "duration")

    def __init__(self, video_id: str, title: str = "", artist: str = "",
                 art_url: str = "", duration: float = 0.0):
        self.video_id = str(video_id or "")
        self.title = str(title or "")
        self.artist = str(artist or "")
        self.art_url = art_url or (
            f"https://i.ytimg.com/vi/{self.video_id}/hqdefault.jpg"
            if self.video_id else "")
        self.duration = float(duration or 0)

    def __repr__(self):
        return f"Result({self.video_id}: {self.title!r} by {self.artist!r})"

    def to_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.__slots__}


def _fetch(url: str, timeout: float = 8.0, headers: dict = None) -> str:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        **(headers or {}),
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(4 * 1024 * 1024).decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        # The body says which parameter it objected to, and throwing it away
        # leaves "400 Bad Request" and nothing to act on.
        detail = ""
        try:
            payload = json.loads(error.read().decode("utf-8", "replace"))
            problem = (payload.get("error") or {})
            reasons = [d.get("reason", "") for d in problem.get("errors", [])]
            detail = f" - {problem.get('message', '')}"
            if reasons:
                detail += f" [{', '.join(r for r in reasons if r)}]"
        except Exception:
            pass
        raise OSError(f"HTTP {error.code}{detail}") from None


def _unescape(text: str) -> str:
    from html import unescape
    return unescape(str(text or "")).strip()


def search_api(query: str, key: str, limit: int = 10,
               restrict: bool = True) -> list:
    """
    The Data API. Raises on failure so the caller can fall back.

    `restrict` asks only for embeddable music. It is worth having - a result
    that cannot be embedded loads, errors, and gets skipped - but the extra
    parameters are also what a fussy key or a region rejects, so a refusal
    is retried without them rather than treated as the API being unavailable.
    """
    fields = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": max(1, min(25, int(limit))),
        "key": key,
    }
    if restrict:
        fields["videoEmbeddable"] = "true"
        # Deliberately NOT videoCategoryId: a great deal of music is not
        # filed under Music, and the restriction loses more than it saves.
        fields["videoSyndicated"] = "true"

    payload = json.loads(_fetch(f"{API}?{urllib.parse.urlencode(fields)}"))

    results = []
    for item in payload.get("items", []):
        video_id = (item.get("id") or {}).get("videoId")
        snippet = item.get("snippet") or {}
        if not video_id:
            continue
        thumbs = snippet.get("thumbnails") or {}
        art = ((thumbs.get("high") or thumbs.get("medium")
                or thumbs.get("default") or {}).get("url") or "")
        results.append(Result(video_id,
                              title=_unescape(snippet.get("title")),
                              artist=_unescape(snippet.get("channelTitle")),
                              art_url=art))
    return results


#the results page embeds its data as one JSON blob
_INITIAL = re.compile(r"var ytInitialData\s*=\s*(\{.*?\});</script>", re.S)


def search_scrape(query: str, limit: int = 10) -> list:
    """
    The results page, with no key.

    Reads the JSON the page embeds rather than its markup - the markup is
    generated class names, the blob at least has field names.
    """
    params = urllib.parse.urlencode({"search_query": query, "sp": "EgIQAQ%3D%3D"})
    html = _fetch(f"{RESULTS}?{params}")

    match = _INITIAL.search(html)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return []

    results = []

    def walk(node):
        if len(results) >= limit:
            return
        if isinstance(node, dict):
            renderer = node.get("videoRenderer")
            if isinstance(renderer, dict):
                video_id = renderer.get("videoId")
                if video_id:
                    results.append(Result(
                        video_id,
                        title=_text(renderer.get("title")),
                        artist=_text(renderer.get("ownerText"))))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return results[:limit]


#words that say nothing about which recording this is
_NOISE = {"official", "video", "audio", "lyrics", "lyric", "hd", "hq", "full",
          "music", "the", "a", "an", "by", "ft", "feat", "featuring", "with",
          "version", "remastered", "original", "soundtrack", "ost"}


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9']+", str(text or "").lower())
            if w not in _NOISE and len(w) > 1}


#Below this a result is not the thing that was asked for. A title with no
#word in common is 0; a title matching every word is 1.
MIN_TITLE_MATCH = 0.34


#Roughly: does this text use letters a Latin query could ever match?
_LATIN = re.compile(r"[a-z0-9]")
#CJK, kana, hangul, cyrillic, arabic, hebrew, thai, devanagari
_NON_LATIN = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af"
    r"\u0400-\u04ff\u0590-\u05ff\u0600-\u06ff\u0900-\u097f"
    r"\u0e00-\u0e7f]")


def script_of(text: str) -> str:
    """'latin', 'other', or 'mixed'."""
    text = str(text or "").lower()
    latin = bool(_LATIN.search(text))
    other = bool(_NON_LATIN.search(text))
    if latin and other:
        return "mixed"
    if other:
        return "other"
    return "latin"


def comparable(a: str, b: str) -> bool:
    """
    Whether comparing two titles means anything.

    A song has one title on YouTube Music and another on YouTube - "Kaiju
    Girl" is the same track as "\u4e59\u5973\u602a\u7363", and comparing them
    letter by letter says only that they are written differently. Treating
    that as "wrong" throws away the right answer.
    """
    first, second = script_of(a), script_of(b)
    if "mixed" in (first, second):
        return True
    return first == second


def title_match(wanted: set, found: set, raw_wanted: str, raw_found: str) -> float:
    """
    How much of the asked-for title a result actually contains, 0 to 1.

    Word overlap first, then a character-level comparison as a floor - so a
    title that was misheard by a letter or two still counts. "Kaiju" against
    "Kaijuu" shares no word and almost every character.
    """
    if not wanted:
        return 1.0
    overlap = len(wanted & found) / len(wanted)
    if overlap >= 1.0:
        return 1.0

    from difflib import SequenceMatcher
    close = SequenceMatcher(None, raw_wanted, raw_found).ratio()
    # The best of the two, since either being high is a good sign on its own.
    return max(overlap, close if close > 0.6 else 0.0)


def acceptance_score(result, query: str) -> tuple:
    """
    (score, comparable) for one result, for deciding whether to play it.

    Named apart from `ranking_score` deliberately. Both were called
    `score_result`, in this one module, with different signatures and
    different return types - so the second definition replaced the first and
    `usable()` called it with a query where an artist was expected. Every
    search raised `missing 1 required positional argument: 'artist'`, which
    the caller logged as "Search failed" and turned into "Nothing found": a
    working search engine, results in hand, and nothing ever played.

    `comparable` is False when the two titles are written in different
    scripts, which is not a low score - it is no score at all. The caller
    decides what to do with an unknown, and treating it as zero would reject
    every Japanese title somebody asked for in English.

    Separate from rank() because ordering and *accepting* are different
    questions: sorting always produces a first result, however wrong, and
    something has to decide that the first result is not an answer.
    """
    title_part, artist_part = split_request(query)
    wanted_title = _words(title_part)
    wanted_artist = _words(artist_part)

    found_title = _words(result.title)
    found_artist = _words(result.artist)

    artist = 0.0
    if wanted_artist:
        artist = max(
            len(wanted_artist & found_artist) / len(wanted_artist),
            len(wanted_artist & found_title) / len(wanted_artist),
        )

    if not comparable(title_part, result.title):
        # Nothing can be said about the title. The artist is all there is.
        return artist, False

    title = title_match(wanted_title, found_title | found_artist,
                        title_part.lower(), str(result.title or "").lower())
    if not wanted_artist:
        return title, True

    # The title carries it. An artist heard wrongly is common - a title heard
    # wrongly enough to share nothing is not the same song.
    return title * 0.75 + artist * 0.25, True


def rank(results: list, query: str) -> list:
    """
    Put the closest match first.

    Search engines answer the words, not the request: "the bear by casey lee
    williams" returns anything with a bear in it. The phrase already says what
    matters - a title, and after "by", who by - so the results are re-ordered
    against both rather than the first one being taken on trust.
    """
    if not results:
        return results

    title_part, artist_part = split_request(query)
    wanted_title = _words(title_part)
    wanted_artist = _words(artist_part)

    def score(index_and_result) -> tuple:
        index, result = index_and_result
        title = _words(result.title)
        artist = _words(result.artist)

        points = 0.0
        if wanted_title:
            # How much of the asked-for title this result actually contains.
            points += 2.0 * len(wanted_title & title) / len(wanted_title)
        if wanted_artist:
            # The channel first. Somebody naming an artist wants that artist's
            # upload, not a cover with their name in the title.
            on_channel = len(wanted_artist & artist) / len(wanted_artist)
            in_title = len(wanted_artist & title) / len(wanted_artist)
            points += 4.0 * on_channel + 1.5 * in_title
            # An exact channel match settles it. "Ok Goodnight" the channel
            # beats "Ok Goodnight cover by someone else" every time.
            if on_channel >= 1.0:
                points += 1.5
        # Ties keep the order the search returned, which is its own ranking
        # and better than nothing.
        return (-points, index)

    return [result for _, result in
            sorted(enumerate(results), key=score)]


def _text(node) -> str:
    """YouTube writes text as {runs:[{text}]} or {simpleText}."""
    if not isinstance(node, dict):
        return ""
    if node.get("simpleText"):
        return _unescape(node["simpleText"])
    runs = node.get("runs")
    if isinstance(runs, list):
        return _unescape("".join(run.get("text", "") for run in runs
                                 if isinstance(run, dict)))
    return ""


def split_request(phrase: str) -> tuple:
    """
    ("the bear", "okay goodnight") out of "the bear by okay goodnight".

    The last "by" wins, since a title may contain one - "Death by Glamour by
    Toby Fox" is a title and an artist, not two artists.
    """
    text = " ".join(str(phrase or "").split())
    lowered = text.lower()
    at = lowered.rfind(" by ")
    if at == -1:
        return text, ""
    return text[:at].strip(), text[at + 4:].strip()


#Words a video has that a song does not. A result carrying one of these is
#somebody's recording of the thing rather than the thing.
NOT_THE_SONG = (
    "live", "cover", "remix", "reaction", "reacts", "review", "karaoke",
    "instrumental", "tutorial", "lesson", "how to play", "backing track",
    "8 bit", "8-bit", "nightcore", "sped up", "slowed", "loop", "1 hour",
    "full album", "mix", "compilation", "medley", "concert", "festival",
    "tour", "rehearsal", "soundcheck", "behind the scenes", "trailer",
)

#And words that say it IS the thing.
IS_THE_SONG = (
    "official", "official video", "official audio", "audio", "lyric",
    "lyrics", "music video", "topic", "visualizer", "hd",
)


#How similar two strings must be before they are a match at all. Below this
#is noise: unrelated names routinely score 0.3-0.4 against each other simply
#for sharing letters.
FUZZY_FLOOR = 0.45


def _plain(text: str) -> str:
    """A string reduced to the letters and digits somebody actually said."""
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower()).split())


def _closeness(heard: str, found: str) -> float:
    """
    How much one string is the other, 0 to 1.

    Both directions matter and they are not the same question. A title that
    CONTAINS what was asked for is a good match - "The Bear (Official Video)"
    is "the bear". A title the asked-for string contains is usually a worse
    one, because it means most of what was said is missing.
    """
    want, got = _plain(heard), _plain(found)
    if not want or not got:
        return 0.0
    if want == got:
        return 1.0
    if want in got:
        # Scaled by how much of the result is the thing asked for, so a
        # title with the song buried in a sentence scores below a clean one.
        return 0.75 + 0.25 * (len(want) / len(got))
    if got in want:
        return 0.6 * (len(got) / len(want))

    # Compared with the spaces taken out as well.
    #
    # Where a name breaks into words is the single most common thing a
    # speech engine gets wrong, and it is not a real difference: "okay good
    # night" and "OK GOODNIGHT" are the same name, and word-by-word they
    # score 0.62 while squashed they score 0.92. Judging on the worse of two
    # readings of the same string is judging on the wrong one.
    squashed_want, squashed_got = want.replace(" ", ""), got.replace(" ", "")
    if squashed_want == squashed_got:
        return 0.95
    if squashed_want in squashed_got:
        return 0.72 + 0.23 * (len(squashed_want) / len(squashed_got))
    if squashed_got in squashed_want:
        return 0.58 * (len(squashed_got) / len(squashed_want))

    loose = max(
        difflib.SequenceMatcher(None, want, got).ratio(),
        difflib.SequenceMatcher(None, squashed_want, squashed_got).ratio(),
    )
    # A curve, not a flat multiplier.
    #
    # `ratio * 0.7` punished a name that is the same but spaced differently
    # exactly as hard as one that is genuinely different - "okay good night"
    # against "OK GOODNIGHT" is 0.92 similar and scored 0.64, which is not
    # enough to beat a stranger's upload carrying the same title.
    #
    # Below FUZZY_FLOOR is not a match at all; above it, scaled up to 1. That
    # is both kinder to real matches and stricter on noise than the flat
    # version was.
    if loose <= FUZZY_FLOOR:
        return 0.0
    return (loose - FUZZY_FLOOR) / (1.0 - FUZZY_FLOOR)


def ranking_score(result, title: str, artist: str) -> float:
    """
    How well one search result answers what was asked for, for ordering.

    The artist is the part that decides it. YouTube ranks by popularity and
    recency, so a festival recording of a song by a channel nobody asked for
    routinely outranks the song - "OK GOODNIGHT - The Bear @ Night Of The
    Prog 2024 6/8 by Himpel Pimpf" over "The Bear by Okay Goodnight". Both
    contain the title; only one is by the artist.

    So the artist is weighted heavily when one was given, and a result whose
    TITLE carries the artist name counts too, because that is how uploads are
    named when the channel is not the artist.
    """
    found_title = str(getattr(result, "title", "") or "")
    found_artist = str(getattr(result, "artist", "") or "")

    score = _closeness(title, found_title) * 1.0

    if artist:
        # Either the channel is the artist, or the title says who it is by.
        by_channel = _closeness(artist, found_artist)
        by_title = 1.0 if _plain(artist) in _plain(found_title) else 0.0
        score += max(by_channel, by_title) * 1.4

    # Whole words, padded so a phrase can be matched the same way. Substring
    # matching punished "Live Forever" for containing "live" and "Mixtape"
    # for containing "mix" - both are the song, not a recording of one.
    lowered = f" {_plain(found_title)} "
    for word in NOT_THE_SONG:
        if f" {word} " in lowered:
            # Once, not once per word: a title reading "Live (Official
            # Video)" should not be punished twice for one live recording.
            score -= 0.8
            break
    for word in IS_THE_SONG:
        if f" {word} " in lowered:
            score += 0.25
            break
    # A "- Topic" channel is YouTube's own upload of a release. Nothing is a
    # stronger signal that this is the record rather than a video of it.
    if found_artist.strip().lower().endswith("- topic"):
        score += 0.6
    return score


def best_match(results: list, title: str, artist: str) -> list:
    """
    The same results, best first.

    Sorted rather than filtered. A low score is a guess about relevance and
    the queue behind the first result is still worth having - somebody who
    asked for a song and got the wrong one presses next, which only works if
    the rest are still there.
    """
    if not results:
        return results
    ranked = sorted(results,
                    key=lambda r: ranking_score(r, title, artist),
                    reverse=True)
    return ranked


def build_query(phrase: str) -> str:
    """
    What to actually ask for.

    The word "by" is dropped: a search engine treats it as a term to match,
    and it appears in a great many unrelated titles. Both halves are kept,
    because the artist is the strongest signal there is - the title alone
    returns covers, live versions and anything sharing a common word.
    """
    title, artist = split_request(phrase)
    if not artist:
        return title
    return f"{title} {artist}".strip()


def usable(results: list, query: str, floor: float = MIN_TITLE_MATCH,
           log=None) -> list:
    """
    Only the results that are plausibly what was asked for.

    Ranking sorts; this rejects. Without it the best of a bad set is still
    played - "kaiju girl by metta nick" returning a short film about a
    corporate monster is a score of zero, and it played because zero was the
    highest score there was.
    """
    _title, wanted_artist = split_request(query)

    kept, unknown = [], []
    for index, result in enumerate(results or []):
        score, could_compare = acceptance_score(result, query)

        if not could_compare:
            # A title in another script. Kept if the artist agrees, and
            # otherwise held back - the search engine put it here for a
            # reason, so the first one is worth trusting when there is
            # nothing else to go on.
            if wanted_artist and score >= 0.5:
                kept.append((score, result))
            else:
                unknown.append((index, result))
            continue

        if score >= floor:
            kept.append((score, result))
        elif log:
            log("debug", f"[Music] Rejected {result.title!r} "
                         f"(match {score:.2f} < {floor:.2f})")

    kept.sort(key=lambda pair: -pair[0])
    ordered = [result for _score, result in kept]

    if unknown and not ordered:
        # Nothing comparable matched, so fall back to what the search itself
        # ranked first. Only the top one: trusting the whole list would queue
        # up nine things nobody can vouch for.
        unknown.sort(key=lambda pair: pair[0])
        best = unknown[0][1]
        if log:
            log("info", f"[Music] {best.title!r} is written in another script "
                        f"- trusting the search's own ranking.")
        return [best]

    return ordered


def _unescape_js(blob: str) -> str:
    """
    Undo the backslash escaping in a JavaScript string literal.

    `unicode_escape` alone is wrong here: it decodes bytes as Latin-1, so a
    UTF-8 title comes back as one mangled character per byte - "\u4e59\u5973\u602a\u7363"
    arrives as gibberish, and the bullet separating a row's fields stops being
    a bullet, which breaks the parsing that depends on it.

    Unescaped, then put back through Latin-1 and read as the UTF-8 it always
    was.
    """
    text = blob.encode("utf-8", "surrogatepass").decode("unicode_escape")
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        # Already clean, or not UTF-8 underneath. Better than nothing.
        return text


def _walk_for(node, key: str, found: list, limit: int) -> None:
    """Every dict under `node` holding `key`, depth first."""
    if len(found) >= limit:
        return
    if isinstance(node, dict):
        if key in node and isinstance(node[key], dict):
            found.append(node[key])
        for value in node.values():
            _walk_for(value, key, found, limit)
    elif isinstance(node, list):
        for value in node:
            _walk_for(value, key, found, limit)


def search_music(query: str, limit: int = 10) -> list:
    """
    YouTube Music, which is where the translated titles are.

    Read out of the page's embedded JSON like the YouTube results page, but
    a different shape: a row is a `musicResponsiveListItemRenderer` whose
    columns hold the title, then the kind, artist, album and length.
    """
    params = urllib.parse.urlencode({"q": query})
    html = _fetch(f"{MUSIC}?{params}", headers={
        # Without this the page is a consent wall in much of the world, and
        # the wall has no results in it.
        "Cookie": "CONSENT=YES+cb; SOCS=CAI",
    })

    blobs = re.findall(r"initialData\.push\(\{path:.*?data:\s*'(.*?)'\}\)", html, re.S)
    payloads = []
    for blob in blobs:
        try:
            payloads.append(json.loads(_unescape_js(blob)))
        except (ValueError, UnicodeDecodeError):
            continue
    if not payloads:
        match = _INITIAL.search(html)
        if match:
            try:
                payloads.append(json.loads(match.group(1)))
            except ValueError:
                pass
    if not payloads:
        return []

    rows = []
    for payload in payloads:
        _walk_for(payload, "musicResponsiveListItemRenderer", rows, limit * 3)

    results, seen = [], set()
    for row in rows:
        video_id = (((row.get("playlistItemData") or {}).get("videoId"))
                    or _music_video_id(row))
        if not video_id or video_id in seen:
            continue

        columns = []
        for column in row.get("flexColumns") or []:
            renderer = column.get(
                "musicResponsiveListItemFlexColumnRenderer") or {}
            columns.append(_text(renderer.get("text")))
        if not columns or not columns[0]:
            continue

        seen.add(video_id)
        results.append(Result(video_id,
                              title=columns[0],
                              artist=_music_artist(columns)))
        if len(results) >= limit:
            break
    return results


def _music_video_id(row: dict) -> str:
    """The id off whichever nested endpoint carries it."""
    found = []
    _walk_for(row, "watchEndpoint", found, 3)
    for endpoint in found:
        if endpoint.get("videoId"):
            return str(endpoint["videoId"])
    return ""


def _music_artist(columns: list) -> str:
    """
    The artist out of "Song \u2022 Artist \u2022 Album \u2022 3:41".

    The second column is a list of facts separated by bullets, and which
    position the artist sits in depends on the row - so the kind and anything
    that looks like a duration are dropped and the first of what is left is
    taken.
    """
    if len(columns) < 2:
        return ""
    # Split on a bullet however it arrives - and on a vertical bar, which some
    # rows use instead.
    parts = [p.strip() for p in
             re.split(r"\s*(?:[\u2022\u00b7\u30fb|]|\u00e2\u0080\u00a2)\s*", columns[1])
             if p.strip()]

    kinds = {"song", "video", "album", "single", "ep", "playlist", "artist",
             "podcast", "episode"}
    cleaned = []
    for part in parts:
        # A leading kind word with no separator after it: "Song METANICK".
        # Stripped as well as filtered, so a missing bullet does not leave the
        # word glued to the artist's name.
        for kind in kinds:
            if part.lower().startswith(kind + " "):
                part = part[len(kind) + 1:].strip()
                break
        if not part or part.lower() in kinds:
            continue
        if re.fullmatch(r"[\d:]+", part):
            continue
        # "1.2M views", "3.4K plays" - a count, not a name.
        if re.fullmatch(r"[\d.,]+[KMB]?\s+\w+", part):
            continue
        cleaned.append(part)
    return cleaned[0] if cleaned else ""


#Keys the API has already refused as invalid, for this session.
#
#An exhausted quota is worth retrying - it comes back tomorrow, and giving up
#on the API for the rest of the day over one busy hour would be wrong. A key
#the API calls INVALID is different: it fails identically on every search
#forever, and each search pays two round trips and two warnings before it
#reaches the scrapers that were going to answer anyway.
_REFUSED_KEYS = set()

#What the Data API says when the key itself is the problem, rather than one
#of the optional filters. Deliberately not "badRequest", which is also what a
#rejected filter returns - and retrying without filters is the whole reason
#that path exists.
_KEY_REFUSALS = ("api key not valid", "keyinvalid", "api key expired",
                 "api_key_invalid")


def _key_was_refused(problem) -> bool:
    lowered = str(problem).lower()
    return any(reason in lowered for reason in _KEY_REFUSALS)


def search(query: str, key: str = "", limit: int = 10, log=None) -> list:
    """
    Whatever works. Returns [] rather than raising.

    The key is tried first when there is one, and a failure falls through to
    scraping rather than leaving somebody with silence - an exhausted quota
    should not mean the music stops working for the rest of the day.
    """
    query = str(query or "").strip()
    if not query:
        return []

    # Asked for without the "by", ranked against both halves of what was said.
    asked = build_query(query)
    if log and asked != query:
        log("debug", f"[Music] Searching for {asked!r}")

    if key and key in _REFUSED_KEYS:
        # Already established, once, out loud. Saying it again on every
        # search would bury the log in a problem nobody is going to fix from
        # a warning they have read forty times.
        key = ""

    if key:
        # Restricted first, then plain. A refusal is usually one of the
        # optional filters rather than the key, and giving up on the API
        # entirely would drop to scraping for the rest of the session.
        for restrict in (True, False):
            try:
                results = search_api(asked, key, limit, restrict=restrict)
                if results:
                    good = usable(results, query, log=log)
                    if good:
                        return good
                    if log:
                        log("debug", "[Music] Nothing the API returned matched "
                                     "closely enough.")
                if log:
                    log("debug", f"[Music] The Data API returned nothing"
                                 f"{' with filters' if restrict else ''}.")
            except Exception as e:
                if _key_was_refused(e):
                    # Not the filters, and not going to get better. Retrying
                    # unfiltered would fail the same way, and so would every
                    # search after this one.
                    _REFUSED_KEYS.add(key)
                    if log:
                        log("warning",
                            f"[Music] The YouTube Data API key is not valid "
                            f"({e}). Searching YouTube directly instead, and "
                            f"not asking the API again until restart.")
                    break
                if log:
                    log("warning", f"[Music] Data API search failed ({e})"
                                   f"{' - retrying unfiltered' if restrict else ''}.")

    # YouTube first, then YouTube Music.
    #
    # Both, rather than one: a track is often filed under a translated or
    # romanised title on Music and only its original title on YouTube, so a
    # search for the English name finds nothing on one and everything on the
    # other. Whichever produces something plausible wins.
    for name, finder in (("YouTube", search_scrape),
                         ("YouTube Music", search_music)):
        try:
            results = finder(asked, limit)
        except Exception as e:
            if log:
                log("warning", f"[Music] {name} search failed: {e}")
            continue

        good = usable(results, query, log=log)
        if good:
            if log and name != "YouTube":
                log("info", f"[Music] Found on {name}: {good[0].title!r}")
            return good
        if log and results:
            log("debug", f"[Music] {name} returned {len(results)} results, "
                         f"none matching.")

    return []
