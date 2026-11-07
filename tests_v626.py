"""Hotfix 6.2.6 — Telegram Audio/Music acceptance tests."""
import ast
from pathlib import Path

s = Path("bot.py").read_text(encoding="utf-8")


def test_version():
    assert 'VERSION = "v6.2.6"' in s


def test_resolve_helper_exists():
    assert "def resolve_incoming_audio" in s
    assert "ensure_mp3_name" in s


def test_receive_uses_resolve():
    idx = s.find("async def receive_audio")
    assert idx > 0
    snippet = s[idx : idx + 800]
    assert "resolve_incoming_audio(message)" in snippet
    assert "media_source" in snippet
    assert "resolved_name" in snippet


def test_audio_without_extension_accepted():
    """Core bug: Music messages often lack .mp3 in file_name."""
    # Source must treat media_source == "audio" as valid even without .mp3
    assert 'if media_source == "audio":' in s
    # Rejection only on non-audio path
    reject_idx = s.find('فقط فایل MP3 یا فایل متنی فهرست آرشیو قبول می‌کنم.')
    assert reject_idx > 0
    window = s[reject_idx - 200 : reject_idx]
    assert "media_source" in window or "elif not name.lower().endswith" in s


def test_summary_accepts_audio():
    assert 'if media_source != "audio"' in s


def test_handlers_no_conflict():
    # Single combined handler for AUDIO | Document
    assert "filters.AUDIO | filters.Document.ALL, receive_audio" in s
    # Picture handler separate
    assert "filters.PHOTO | filters.Document.IMAGE, receive_picture" in s


def test_escape_and_fallback_still_present():
    assert "def escape_md" in s
    assert "install_telegram_resilience" in s
    assert 'retry_kwargs["parse_mode"] = None' in s


def test_no_forbidden_changes():
    assert "Sequential single-message send" in s
    assert "InputMediaAudio" not in s
    assert "pack_album_media_groups" not in s


def test_syntax():
    ast.parse(s)


def test_resolve_behaviour():
    class Obj:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    # Extract and exec only resolve_incoming_audio from source
    start = s.find("def resolve_incoming_audio")
    # find next top-level def after helper
    end = s.find("\n# ==================== پایداری", start)
    code = s[start:end]
    ns = {"Path": Path}
    exec(code, ns)
    resolve = ns["resolve_incoming_audio"]

    # Music without extension
    msg = Obj(
        audio=Obj(file_id="a", file_name="Song Name", mime_type="audio/mpeg", title="Song Name"),
        document=None,
    )
    media, src, name = resolve(msg)
    assert src == "audio" and name.lower().endswith(".mp3"), (src, name)

    # Music no file_name
    msg = Obj(
        audio=Obj(file_id="a", file_name=None, mime_type=None, title="Live_Track"),
        document=None,
    )
    media, src, name = resolve(msg)
    assert src == "audio" and name.lower().endswith(".mp3")

    # Document MP3
    msg = Obj(audio=None, document=Obj(file_id="d", file_name="a.mp3", mime_type="audio/mpeg"))
    media, src, name = resolve(msg)
    assert src == "document" and name.endswith(".mp3")

    # Document audio mime, no ext
    msg = Obj(audio=None, document=Obj(file_id="d", file_name="x", mime_type="audio/mpeg"))
    media, src, name = resolve(msg)
    assert name.endswith(".mp3")

    # Reject txt
    msg = Obj(audio=None, document=Obj(file_id="d", file_name="list.txt", mime_type="text/plain"))
    media, src, name = resolve(msg)
    assert not name.lower().endswith(".mp3")

    # Empty
    msg = Obj(audio=None, document=None)
    media, src, name = resolve(msg)
    assert media is None


if __name__ == "__main__":
    test_version(); print("OK test_version")
    test_resolve_helper_exists(); print("OK test_resolve_helper_exists")
    test_receive_uses_resolve(); print("OK test_receive_uses_resolve")
    test_audio_without_extension_accepted(); print("OK test_audio_without_extension_accepted")
    test_summary_accepts_audio(); print("OK test_summary_accepts_audio")
    test_handlers_no_conflict(); print("OK test_handlers_no_conflict")
    test_escape_and_fallback_still_present(); print("OK test_escape_and_fallback_still_present")
    test_no_forbidden_changes(); print("OK test_no_forbidden_changes")
    test_syntax(); print("OK test_syntax")
    test_resolve_behaviour(); print("OK test_resolve_behaviour")
    print("ALL tests_v626 PASSED")
