"""Smoke and unit tests for Music Tag Bot v8.0.0 channel repository."""
from __future__ import annotations

import ast
import tempfile
from pathlib import Path

import channel_repo


def test_version_string():
    src = Path("bot.py").read_text(encoding="utf-8")
    assert 'VERSION = "v8.1.0"' in src
    # Old multi-version changelog items should not dominate VERSION_CHANGES
    assert "سیستم مخزن کانال‌ها" in src


def test_ast_and_imports():
    for name in ("bot.py", "channel_repo.py", "audio_scanner.py", "self_scan.py"):
        tree = ast.parse(Path(name).read_text(encoding="utf-8"))
        assert tree is not None


def test_repo_channels_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        channel_repo.init_db(root)
        channel_repo.set_repo_channel(root, 42, "source_a", -100111, "Chan A", "chana")
        channel_repo.set_repo_channel(root, 42, "dest", -100222, "Dest")
        a = channel_repo.get_repo_channel(root, 42, "source_a")
        assert a and a["chat_id"] == -100111
        assert channel_repo.get_repo_channel(root, 42, "source_b") is None
        d = channel_repo.get_repo_channel(root, 42, "dest")
        assert d and d["title"] == "Dest"


def test_upsert_and_match():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        channel_repo.init_db(root)
        channel_repo.upsert_track(
            root, 1, "source_a", -1001, 5,
            file_id="fid1", title="Hello World", performer="The Band",
            duration=210, file_name="01 Hello World.mp3",
        )
        channel_repo.upsert_track(
            root, 1, "source_a", -1001, 6,
            file_id="fid2", title="Other Song", performer="Someone",
            duration=180, file_name="other.mp3",
        )
        assert channel_repo.count_tracks(root, 1, "source_a") == 2
        m, c = channel_repo.match_track(
            root, 1, title="Hello World", artist="The Band", duration=210,
        )
        assert c == "exact" and m and m["file_id"] == "fid1"
        m2, c2 = channel_repo.match_track(
            root, 1, title="Unknown", artist="Nobody",
        )
        assert c2 == "none" and m2 is None


def test_queue_state_persistence():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        channel_repo.init_db(root)
        payload = {"queue": [{"title": "A", "status": "pending"}], "cursor": 0}
        channel_repo.save_queue_state(root, 99, payload)
        loaded = channel_repo.load_queue_state(root, 99)
        assert loaded and loaded["cursor"] == 0
        assert loaded["queue"][0]["title"] == "A"


def test_archive_keyboard_has_repo():
    src = Path("bot.py").read_text(encoding="utf-8")
    assert "repo:menu" in src
    assert "مخزن کانال‌ها" in src
    assert "repo:fetch_current" in src
    assert "دریافت آهنگ برای ادیت" in src
    assert "repo:edit_copy:" in src
    assert "✏️ Edit" in src
    assert "await receive_audio(synthetic_update, context)" in src


if __name__ == "__main__":
    test_version_string()
    test_ast_and_imports()
    test_repo_channels_roundtrip()
    test_upsert_and_match()
    test_queue_state_persistence()
    test_archive_keyboard_has_repo()
    print("tests_v800: all passed")
