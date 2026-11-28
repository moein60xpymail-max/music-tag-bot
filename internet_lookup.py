"""Metadata-only online lookup for Music Tag Bot.

Genius is used for song identity, artwork, artists, album and release date.
MusicBrainz is queried for a recording genre when one is explicitly tagged.
No lyrics are downloaded or stored by this module.
"""
from __future__ import annotations

import json
import re
import time
from difflib import SequenceMatcher
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

GENIUS_API = "https://api.genius.com"
MUSICBRAINZ_API = "https://musicbrainz.org/ws/2"
USER_AGENT = "MusicTagBot/8.1.0 (https://github.com/moein60xpymail-max/music-tag-bot)"


def _get_json(url: str, headers: dict | None = None, timeout: int = 15) -> dict:
    req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    req = Request(url, headers=req_headers)
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _norm(value: str) -> str:
    value = (value or "").casefold()
    value = re.sub(r"\b(feat(?:uring)?|ft)\.?\b", " ", value)
    value = re.sub(r"[^\w\u0600-\u06ff]+", " ", value)
    return " ".join(value.split())


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def search_genius(query: str, token: str, limit: int = 5) -> list[dict]:
    if not token:
        raise ValueError("GENIUS_ACCESS_TOKEN تنظیم نشده است.")
    url = f"{GENIUS_API}/search?{urlencode({'q': query})}"
    payload = _get_json(url, {"Authorization": f"Bearer {token}"})
    hits = (payload.get("response") or {}).get("hits") or []
    results = []
    for hit in hits:
        song = hit.get("result") or {}
        if not song.get("id"):
            continue
        results.append({
            "id": int(song["id"]),
            "title": song.get("title") or "",
            "full_title": song.get("full_title") or "",
            "artist": song.get("artist_names") or "",
            "url": song.get("url") or "",
            "image": song.get("song_art_image_url") or song.get("header_image_url") or "",
            "album": (song.get("album") or {}).get("name") or "",
            "release_date": song.get("release_date") or "",
            "release_date_for_display": song.get("release_date_for_display") or "",
        })
    return results[:max(1, min(limit, 5))]


def get_genius_song(song_id: int, token: str) -> dict:
    if not token:
        raise ValueError("GENIUS_ACCESS_TOKEN تنظیم نشده است.")
    url = f"{GENIUS_API}/songs/{int(song_id)}"
    payload = _get_json(url, {"Authorization": f"Bearer {token}"})
    song = (payload.get("response") or {}).get("song") or {}
    if not song:
        raise LookupError("اطلاعات این آهنگ در Genius پیدا نشد.")
    primary = (song.get("primary_artist") or {}).get("name") or ""
    featured = [x.get("name", "") for x in (song.get("featured_artists") or []) if x.get("name")]
    artists = song.get("artist_names") or ", ".join([primary] + featured).strip(", ")
    release_date = song.get("release_date") or ""
    if not release_date:
        release_date = song.get("release_date_for_display") or ""
    year_match = re.search(r"\b(19|20)\d{2}\b", release_date)
    album = (song.get("album") or {}).get("name") or ""
    return {
        "id": int(song.get("id") or song_id),
        "title": song.get("title") or "",
        "artist": artists,
        "album": album,
        "year": year_match.group(0) if year_match else "",
        "url": song.get("url") or "",
        "image": song.get("song_art_image_url") or song.get("header_image_url") or "",
        "release_date": release_date,
    }


def lookup_musicbrainz_genre(title: str, artist: str) -> str:
    """Return the top explicit MusicBrainz genre, or empty string if absent."""
    if not title or not artist:
        return ""
    query = f'recording:"{title}" AND artist:"{artist}"'
    url = MUSICBRAINZ_API + "/recording/?" + urlencode({"query": query, "fmt": "json", "limit": 10, "inc": "releases"})
    payload = _get_json(search_url, timeout=20)
    records = payload.get("recordings") or []
    if not records:
        return ""
    # Require a close title and artist match; do not guess a genre from a weak match.
    best = None
    best_score = 0.0
    for record in records:
        title_score = _similarity(title, record.get("title", ""))
        credits = " ".join(
            (entry.get("name") or ((entry.get("artist") or {}).get("name", "")))
            for entry in (record.get("artist-credit") or []) if isinstance(entry, dict)
        )
        artist_score = _similarity(artist, credits)
        score = title_score * 0.65 + artist_score * 0.35
        if score > best_score:
            best, best_score = record, score
    if not best or best_score < 0.72 or not best.get("id"):
        return ""
    time.sleep(1.05)  # MusicBrainz asks clients to stay around one request/second.
    detail_url = MUSICBRAINZ_API + f'/recording/{best["id"]}?' + urlencode({"inc": "releases+genres", "fmt": "json"})
    detail = _get_json(detail_url, timeout=20)
    genres = detail.get("genres") or []
    genres = sorted(genres, key=lambda item: int(item.get("count") or 0), reverse=True)
    return str(genres[0].get("name") or "").strip() if genres else ""


def lookup_musicbrainz_metadata(title: str, artist: str, album_hint: str = "") -> dict:
    """Find a close MusicBrainz recording and enrich it with release/track metadata."""
    empty = {"album": "", "albumartist": "", "track": "", "genre": "", "cover_url": "", "year": ""}
    if not title or not artist:
        return empty
    query = f'recording:"{title}" AND artist:"{artist}"'
    url = MUSICBRAINZ_API + "/recording/?" + urlencode({"query": query, "fmt": "json", "limit": 10, "inc": "releases"})
    payload = _get_json(url, timeout=20)
    records = payload.get("recordings") or []
    best, best_score = None, 0.0
    for record in records:
        rec_artists = " ".join(
            entry.get("name") or (entry.get("artist") or {}).get("name", "")
            for entry in (record.get("artist-credit") or []) if isinstance(entry, dict)
        )
        score = _similarity(title, record.get("title", "")) * .65 + _similarity(artist, rec_artists) * .35
        if album_hint:
            releases = record.get("releases") or []
            if any(_similarity(album_hint, rel.get("title", "")) > .8 for rel in releases):
                score += .08
        if score > best_score:
            best, best_score = record, score
    if not best or best_score < .72:
        return empty
    time.sleep(1.05)
    detail_url = MUSICBRAINZ_API + f'/recording/{best["id"]}?' + urlencode({"inc": "releases+genres", "fmt": "json"})
    detail = _get_json(detail_url, timeout=20)
    genres = sorted(detail.get("genres") or [], key=lambda x: int(x.get("count") or 0), reverse=True)
    result = dict(empty)
    result["genre"] = (genres[0].get("name") or "") if genres else ""
    releases = detail.get("releases") or []
    if not releases:
        return result
    if album_hint:
        releases.sort(key=lambda r: _similarity(album_hint, r.get("title", "")), reverse=True)
    else:
        releases.sort(key=lambda r: bool(r.get("date")), reverse=True)
    release = releases[0]
    release_id = release.get("id")
    result["album"] = release.get("title") or ""
    result["year"] = (release.get("date") or "")[:4]
    credit = release.get("artist-credit") or []
    result["albumartist"] = "".join(
        (x.get("name") or (x.get("artist") or {}).get("name", "")) + (x.get("joinphrase") or "")
        for x in credit if isinstance(x, dict)
    ).strip()
    if release_id:
        time.sleep(1.05)
        rel_url = MUSICBRAINZ_API + f'/release/{release_id}?' + urlencode({"inc": "recordings+artists", "fmt": "json"})
        rel_detail = _get_json(rel_url, timeout=20)
        if not result["albumartist"]:
            credit = rel_detail.get("artist-credit") or []
            result["albumartist"] = "".join(
                (x.get("name") or (x.get("artist") or {}).get("name", "")) + (x.get("joinphrase") or "")
                for x in credit if isinstance(x, dict)
            ).strip()
        wanted_id = best.get("id")
        for medium in rel_detail.get("media") or []:
            for position, track in enumerate(medium.get("tracks") or [], 1):
                recording = track.get("recording") or {}
                if recording.get("id") == wanted_id:
                    result["track"] = str(track.get("position") or track.get("number") or position)
                    break
            if result["track"]:
                break
        result["cover_url"] = f"https://coverartarchive.org/release/{release_id}/front-500"
    return result


def download_cover(url: str) -> bytes:
    if not url or not url.startswith(("https://coverartarchive.org/", "https://images.genius.com/", "https://assets.genius.com/")):
        raise ValueError("Unsupported artwork host")
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=20) as response:
        data = response.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("Cover image is too large")
    return data
