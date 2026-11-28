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
    search_url = f"{MUSICBRAINZ_API}/recording/?{urlencode({'query': query, 'fmt': 'json', 'limit': 5})}"
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
    detail_url = f"{MUSICBRAINZ_API}/recording/{best['id']}?{urlencode({'inc': 'genres', 'fmt': 'json'})}"
    detail = _get_json(detail_url, timeout=20)
    genres = detail.get("genres") or []
    genres = sorted(genres, key=lambda item: int(item.get("count") or 0), reverse=True)
    return str(genres[0].get("name") or "").strip() if genres else ""
