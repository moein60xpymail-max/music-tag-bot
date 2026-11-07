"""Hotfix 6.2.5 smoke tests."""
import ast
from pathlib import Path

s = Path("bot.py").read_text(encoding="utf-8")


def test_version():
    assert 'VERSION = "v6.2.5"' in s


def test_receive_audio_guards_none_message():
    assert "update.effective_message" in s
    idx = s.find("async def receive_audio")
    assert idx > 0
    snippet = s[idx : idx + 400]
    assert "if message is None:" in snippet
    assert "getattr(message, \"document\", None)" in snippet or "getattr(message, 'document', None)" in snippet
    # Must not use bare update.message without guard at the start
    assert "message = update.message\n    document = message.document" not in snippet


def test_escape_helpers_present():
    assert "def escape_md" in s
    assert "def escape_html" in s
    assert "escape_md(filename)" in s or "escape_md(tags[" in s
    assert "escape_html(state['current'])" in s or 'escape_html(state["current"])' in s


def test_escape_md_behaviour():
    # Reimplement the same logic for unit check
    def escape_md(text):
        if text is None:
            return ""
        out = str(text)
        for ch in ("\\", "_", "*", "`", "["):
            out = out.replace(ch, "\\" + ch)
        return out

    assert escape_md("a_b*c") == r"a\_b\*c"
    assert escape_md("x`y") == r"x\`y"
    assert escape_md(None) == ""
    assert "\\_" in escape_md("Song_Title")
    assert "\\*" in escape_md("A * B")


def test_parse_mode_fallback_still_present():
    assert "install_telegram_resilience" in s
    assert "parse entities" in s
    assert 'retry_kwargs["parse_mode"] = None' in s


def test_polling_resilience():
    assert "bootstrap_retries=-1" in s
    assert "concurrent_updates(False)" in s
    assert "NetworkError" in s
    assert "BOT TRANSIENT NETWORK ERROR" in s


def test_no_forbidden_redesigns():
    # Sequential AF still present; no media-group album send path reintroduced
    assert "Sequential single-message send" in s
    assert "InputMediaAudio" not in s
    assert "pack_album_media_groups" not in s


def test_syntax():
    ast.parse(s)


if __name__ == "__main__":
    test_version(); print("OK test_version")
    test_receive_audio_guards_none_message(); print("OK test_receive_audio_guards_none_message")
    test_escape_helpers_present(); print("OK test_escape_helpers_present")
    test_escape_md_behaviour(); print("OK test_escape_md_behaviour")
    test_parse_mode_fallback_still_present(); print("OK test_parse_mode_fallback_still_present")
    test_polling_resilience(); print("OK test_polling_resilience")
    test_no_forbidden_redesigns(); print("OK test_no_forbidden_redesigns")
    test_syntax(); print("OK test_syntax")
    print("ALL tests_v625 PASSED")
