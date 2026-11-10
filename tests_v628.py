"""Tests for v6.2.8 Audio Similarity Scanner."""
import json
import os
import tempfile
from pathlib import Path

from audio_scanner import (
    AudioScannerStore, fingerprint_file, fingerprint_similarity, build_similarity_groups,
    DEFAULT_THRESHOLD,
)


def test_similarity_is_audio_fingerprint_based():
    fp = [0x12345678 + i for i in range(80)]
    assert fingerprint_similarity(fp, list(fp), 100, 100) == 100.0
    altered = list(fp)
    altered[20] ^= 0x0000000F
    score = fingerprint_similarity(fp, altered, 100, 100)
    assert score is not None and 90 < score < 100
    assert fingerprint_similarity(fp, fp[:20], 100, 25) is None


def test_fpcalc_output_parser():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        executable = root / "fpcalc"
        executable.write_text("#!/bin/sh\necho DURATION=123.5\necho FINGERPRINT=1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32\n", encoding="utf-8")
        executable.chmod(0o755)
        audio = root / "sample.mp3"
        audio.write_bytes(b"fake")
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(root) + os.pathsep + old_path
        try:
            fp, duration = fingerprint_file(audio)
        finally:
            os.environ["PATH"] = old_path
        assert len(fp) == 32
        assert duration == 123.5


def test_group_builder():
    base = [0x12345678 + i for i in range(100)]
    similar = list(base)
    similar[5] ^= 1
    different = [0xFFFFFFFF - i for i in range(100)]
    rows = [
        {"id": 1, "name": "one.mp3", "source": "A", "size": 100, "fp_duration": 120,
         "fingerprint": json.dumps(base)},
        {"id": 2, "name": "two.mp3", "source": "B", "size": 110, "fp_duration": 120,
         "fingerprint": json.dumps(similar)},
        {"id": 3, "name": "other.mp3", "source": "B", "size": 100, "fp_duration": 120,
         "fingerprint": json.dumps(different)},
    ]
    groups = build_similarity_groups(rows, 95.0)
    assert len(groups) == 1
    assert set(groups[0]["track_ids"]) == {1, 2}
    assert groups[0]["score"] >= 95


def test_store_selection_quarantine_restore():
    with tempfile.TemporaryDirectory() as td:
        store = AudioScannerStore(Path(td) / "scanner.sqlite3")
        one = store.register_track(7, "A", 7, 101, "file-a", "unique-a", "a.mp3", 1000, 120)
        two = store.register_track(7, "B", 7, 102, "file-b", "unique-b", "b.mp3", 1000, 120)
        store.save_fingerprint(one, [1] * 80, 120)
        store.save_fingerprint(two, [1] * 80, 120)
        rows = store.tracks(7)
        groups = build_similarity_groups(rows, DEFAULT_THRESHOLD)
        scan_id = store.create_scan(7, 2, 2, groups, 0)
        group = store.groups(7)[0]
        moved = store.choose_keep(7, int(group["id"]), one)
        assert moved == 1
        assert [x["id"] for x in store.tracks(7)] == [one]
        quarantined = store.quarantine_items(7)
        assert len(quarantined) == 1
        assert store.restore(7, quarantined[0]["id"])
        assert {x["id"] for x in store.tracks(7)} == {one, two}
        assert store.latest_scan(7)["id"] == scan_id


def test_version_and_ui_wiring():
    bot = Path("bot.py").read_text(encoding="utf-8")
    assert 'VERSION = "v6.2.8"' in bot
    assert 'callback_data="ascan:menu"' in bot
    assert 'register_track(' in bot
    assert 'audio_scanner_worker' in bot


if __name__ == "__main__":
    test_similarity_is_audio_fingerprint_based()
    print("OK test_similarity_is_audio_fingerprint_based")
    test_fpcalc_output_parser()
    print("OK test_fpcalc_output_parser")
    test_group_builder()
    print("OK test_group_builder")
    test_store_selection_quarantine_restore()
    print("OK test_store_selection_quarantine_restore")
    test_version_and_ui_wiring()
    print("OK test_version_and_ui_wiring")
    print("ALL tests_v628 PASSED")
