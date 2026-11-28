"""Regression tests for v8.1.0 internet metadata and Other Album helpers."""
from __future__ import annotations
import ast
from pathlib import Path
import internet_lookup


def _bot_function(name, namespace):
    tree = ast.parse(Path("bot.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace[name]


def test_release_and_buttons():
    src = Path("bot.py").read_text(encoding="utf-8")
    assert 'VERSION = "v8.1.0"' in src
    assert 'callback_data="internet:start"' in src
    assert 'callback_data="action:other_album"' in src
    assert 'GENIUS_ACCESS_TOKEN' in src


def test_other_album_filename():
    import re
    def split_artists(value):
        if not value or value == "—": return []
        return [x.strip() for x in re.split(r"\s*[&,+/]\s*|\s+feat\.?\s+|\s+ft\.?\s+|\s+x\s+", value, flags=re.I) if x.strip()]
    def normalize_name(value): return re.sub(r"\s+", " ", value.strip().lower())
    def sanitize_filename(value): return re.sub(r'[\\/:*?"<>|]', "_", value)
    def generate_filename(artist, title): return sanitize_filename(f"{artist or 'Unknown Artist'} - {title or 'Unknown Title'}")
    fn = _bot_function("generate_other_album_filename", locals())
    assert fn("Sepehr Khalse", "Track", "Sijal") == "Sijal Ft. Sepehr Khalse - Track"
    assert fn("Sijal & Sepehr Khalse", "Track", "Sijal") == "Sijal Ft. Sepehr Khalse - Track"
    assert fn("Sepehr Khalse", "Track", "—") == "Sepehr Khalse - Track"


def test_other_album_protected_fields():
    tree = ast.parse(Path("bot.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "apply_other_album_defaults")
    source = ast.get_source_segment(Path("bot.py").read_text(encoding="utf-8"), node)
    for field in ('"album"', '"track"', '"disc"', '"albumartist"'):
        assert field in source



def test_other_album_defaults_leave_album_track_disc_and_albumartist_untouched():
    tree = ast.parse(Path("bot.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "apply_other_album_defaults")
    calls = []
    namespace = {
        "Dict": dict,
        "Any": object,
        "DEFAULT_FIELD_ORDER": ["title", "artist", "albumartist", "album", "genre", "year", "composer", "publisher", "comment", "lyrics", "disc", "track"],
        "_default_mode": lambda defaults, key: (defaults.get("_modes") or {}).get(key, "apply"),
        "set_tag": lambda path, key, value: calls.append(("set", key, value)),
        "delete_tag": lambda path, key: calls.append(("delete", key)),
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)
    namespace["apply_other_album_defaults"]("track.mp3", {
        "title": "New title", "artist": "Guest", "albumartist": "Default Owner",
        "album": "Single Track", "track": "1", "disc": "1", "year": "2018", "genre": "Rap"
    })
    keys = {entry[1] for entry in calls}
    assert "album" not in keys and "track" not in keys and "disc" not in keys and "albumartist" not in keys
    assert "year" in keys and "genre" in keys and "artist" in keys

def test_genius_song_metadata_parsing(monkeypatch):
    def fake_get_json(url, headers=None, timeout=15):
        return {"response": {"song": {
            "id": 12, "title": "Example", "artist_names": "Artist A, Artist B",
            "release_date": "2018-05-01", "url": "https://genius.com/example",
            "song_art_image_url": "https://example.com/cover.jpg",
            "album": {"name": "Album X"}
        }}}
    monkeypatch.setattr(internet_lookup, "_get_json", fake_get_json)
    result = internet_lookup.get_genius_song(12, "fake-token")
    assert result["year"] == "2018"
    assert result["artist"] == "Artist A, Artist B"
    assert result["album"] == "Album X"


def test_search_genius(monkeypatch):
    def fake_get_json(url, headers=None, timeout=15):
        return {"response": {"hits": [{"result": {
            "id": 42, "title": "Song", "artist_names": "Artist",
            "url": "https://genius.com/song", "song_art_image_url": "https://example.com/a.jpg"
        }}]}}
    monkeypatch.setattr(internet_lookup, "_get_json", fake_get_json)
    results = internet_lookup.search_genius("Song Artist", "token")
    assert len(results) == 1 and results[0]["id"] == 42


