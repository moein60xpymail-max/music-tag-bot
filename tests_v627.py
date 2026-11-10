"""Hotfix 6.2.7 — robust Telegram Audio/Music acceptance."""
import ast
from pathlib import Path

s = Path("bot.py").read_text(encoding="utf-8")


def test_version():
    assert 'VERSION = "v6.2.8"' in s


def test_helpers_present():
    assert "def ensure_mp3_filename" in s
    assert "def is_audio_mime" in s
    assert "def resolve_incoming_audio" in s


def test_receive_prefers_audio():
    idx = s.find("async def receive_audio")
    snippet = s[idx : idx + 600]
    assert "resolve_incoming_audio(message)" in snippet
    assert "effective_message" in snippet


def test_reject_logs_diagnostics():
    assert "reject non-mp3 media:" in s


def test_handlers():
    assert "filters.AUDIO | filters.Document.ALL, receive_audio" in s


def test_escape_fallback():
    assert "def escape_md" in s
    assert "install_telegram_resilience" in s


def test_no_forbidden():
    assert "Sequential single-message send" in s
    assert "InputMediaAudio" not in s


def test_syntax():
    ast.parse(s)


def test_behaviour():
    start = s.find("def ensure_mp3_filename")
    end = s.find("\n# ==================== پایداری", start)
    ns = {}
    exec(s[start:end], ns)
    resolve = ns["resolve_incoming_audio"]
    ensure = ns["ensure_mp3_filename"]

    class Obj:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    # Music without .mp3 extension — the reported bug
    msg = Obj(
        audio=Obj(file_id="1", file_name="Song Name", title="Song Name", mime_type="audio/mpeg"),
        document=None,
    )
    media, src, name = resolve(msg)
    assert src == "audio"
    assert name.lower().endswith(".mp3")

    # Dotted title must keep full name
    assert ensure("01. Intro") == "01. Intro.mp3"

    # Document with no extension + empty mime accepted
    msg = Obj(audio=None, document=Obj(file_id="1", file_name="Track", mime_type=None))
    media, src, name = resolve(msg)
    assert name.endswith(".mp3")

    # Non-audio rejected
    msg = Obj(audio=None, document=Obj(file_id="1", file_name="a.txt", mime_type="text/plain"))
    media, src, name = resolve(msg)
    assert not name.lower().endswith(".mp3")

    # Prefer audio when both present
    msg = Obj(
        audio=Obj(file_id="a", file_name="aud", title="T", mime_type="audio/mpeg"),
        document=Obj(file_id="d", file_name="doc.mp3", mime_type="audio/mpeg"),
    )
    media, src, name = resolve(msg)
    assert src == "audio"


if __name__ == "__main__":
    test_version(); print("OK test_version")
    test_helpers_present(); print("OK test_helpers_present")
    test_receive_prefers_audio(); print("OK test_receive_prefers_audio")
    test_reject_logs_diagnostics(); print("OK test_reject_logs_diagnostics")
    test_handlers(); print("OK test_handlers")
    test_escape_fallback(); print("OK test_escape_fallback")
    test_no_forbidden(); print("OK test_no_forbidden")
    test_syntax(); print("OK test_syntax")
    test_behaviour(); print("OK test_behaviour")
    print("ALL tests_v627 PASSED")
