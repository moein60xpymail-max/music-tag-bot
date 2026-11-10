"""v7.0.0 merge & archive fingerprint workflow tests."""
import ast
import tempfile
from pathlib import Path

from audio_scanner import (
    content_hash_file,
    analyze_local_file,
    similarity_between_analyses,
    EXACT_THRESHOLD,
    REVIEW_THRESHOLD,
)

s = Path("bot.py").read_text(encoding="utf-8")


def test_version():
    assert 'VERSION = "v7.0.0"' in s


def test_source_finish_flow():
    for key in (
        "arch:finish_a",
        "arch:continue_a",
        "arch:confirm_a",
        "arch:confirm_a_yes",
        "arch:continue_b",
        "arch:confirm_b",
        "arch:confirm_b_yes",
        "arch:export_a",
        "arch:export_b",
        "format_source_intake_report",
        "source_finish_keyboard",
        "export_source_txt",
    ):
        assert key in s, key


def test_analyze_uses_fingerprint_not_four_criteria():
    assert "similarity_between_analyses" in s
    assert "analyze_local_file" in s
    # Old 4-criteria call must not remain inside arch:analyze path
    idx = s.find('if data == "arch:analyze":')
    assert idx > 0
    # next major handler
    nxt = s.find('if data == "arch:list":', idx)
    block = s[idx:nxt]
    assert "classify_detail" not in block
    assert "Audio Fingerprint" in block or "fingerprint_score" in block


def test_core_features_preserved():
    assert "async def receive_audio" in s
    assert "resolve_incoming_audio" in s
    assert "Sequential single-message send" in s or "send_processed_to_channel" in s
    assert "AudioScannerStore" in s
    assert "ascan:scan" in s


def test_syntax():
    ast.parse(s)
    ast.parse(Path("audio_scanner.py").read_text(encoding="utf-8"))


def _fake_mp3(path: Path, payload: bytes, id3: bool = True):
    data = b""
    if id3:
        body = b"TAGX" * 8
        size = len(body)
        ss = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
        data += b"ID3\x03\x00\x00" + ss + body
    data += payload
    path.write_bytes(data)


def test_content_hash_and_similarity():
    td = Path(tempfile.mkdtemp())
    p1, p2, p3 = td / "a.mp3", td / "b.mp3", td / "c.mp3"
    payload = b"\xff\xfb\x90\x00" + bytes([i % 256 for i in range(10000)])
    other = b"\xff\xfb\x90\x00" + bytes([(i * 7) % 256 for i in range(10000)])
    _fake_mp3(p1, payload, True)
    _fake_mp3(p2, payload, False)
    _fake_mp3(p3, other, True)
    assert content_hash_file(p1) == content_hash_file(p2)
    a1, a2, a3 = analyze_local_file(p1), analyze_local_file(p2), analyze_local_file(p3)
    assert similarity_between_analyses(a1, a2) == 100.0
    assert similarity_between_analyses(a1, a3) < REVIEW_THRESHOLD


if __name__ == "__main__":
    test_version(); print("OK test_version")
    test_source_finish_flow(); print("OK test_source_finish_flow")
    test_analyze_uses_fingerprint_not_four_criteria(); print("OK test_analyze_fp")
    test_core_features_preserved(); print("OK test_core_features")
    test_syntax(); print("OK test_syntax")
    test_content_hash_and_similarity(); print("OK test_content_hash")
    print("ALL tests_v700 PASSED")
