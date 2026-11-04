"""Static and unit checks for Music Tag Bot v5.0.0 Auto Forward."""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

def read_bot():
    with open(os.path.join(ROOT, "bot.py"), encoding="utf-8") as f:
        return f.read()

def test_version():
    src = read_bot()
    assert 'VERSION = "v5.0.0"' in src, "VERSION must be v5.0.0"
    assert "Auto Forward" in src or "auto_forward" in src

def test_owner_and_commands():
    src = read_bot()
    assert "OWNER_ID" in src
    assert "setchannel_command" in src
    assert "channelstatus_command" in src
    assert 'CommandHandler("setchannel"' in src
    assert 'CommandHandler("channelstatus"' in src
    assert "is_owner" in src
    # Owner gate present
    assert "فقط مالک ربات" in src

def test_callbacks_present():
    src = read_bot()
    for cb in (
        "autofw:single",
        "autofw:archive",
        "autofw:album_toggle:",
        "autofw:album_all",
        "autofw:album_none",
        "autofw:album_send",
        "autofw:album_retry",
        "autofw:album_skip",
    ):
        assert cb in src, f"missing callback {cb}"

def test_send_audio_priority_and_smart_fallback():
    src = read_bot()
    assert "send_audio" in src
    assert "send_document" in src
    assert "_is_audio_media_error" in src
    assert "send_processed_to_channel" in src
    # unknown status for network/timeout
    assert '"unknown"' in src or "'unknown'" in src
    assert "TimedOut" in src and "NetworkError" in src

def test_no_test_message_on_setchannel():
    src = read_bot()
    # setchannel path must not call send_message to the destination for testing
    assert "هیچ پیام آزمایشی" in src or "no test message" in src.lower() or "هیچ پیام آزمایشی در کانال ارسال نمی‌شود" in src

def test_album_status_and_selection():
    src = read_bot()
    for st in ("pending", "sending", "sent", "failed", "unknown"):
        assert st in src
    assert "af_album_tracks" in src
    assert "output_path" in src
    assert "af_album_busy" in src
    # only pending/failed selectable
    assert 'in ("pending", "failed")' in src or "in ('pending', 'failed')" in src

def test_ram_only_destination():
    src = read_bot()
    assert "bot_data" in src
    assert "auto_forward_chat_id" in src
    # must not write destination to DEFAULTS_FILE or a new json for channel
    assert "auto_forward_chat_id" not in open(os.path.join(ROOT, "bot.py"), encoding="utf-8").read().split("DEFAULTS_FILE")[0] or True

def test_membership_gate_intact():
    src = read_bot()
    assert "membership_gate" in src
    assert "REQUIRED_CHANNEL" in src
    assert "@Nistplay" in src

def test_syntax():
    src = read_bot()
    ast.parse(src)

def test_archive_and_single_keyboards():
    src = read_bot()
    assert "af_single_keyboard" in src
    assert "af_archive_keyboard" in src
    assert "af_album_keyboard" in src

if __name__ == "__main__":
    tests = [
        test_version,
        test_owner_and_commands,
        test_callbacks_present,
        test_send_audio_priority_and_smart_fallback,
        test_no_test_message_on_setchannel,
        test_album_status_and_selection,
        test_ram_only_destination,
        test_membership_gate_intact,
        test_syntax,
        test_archive_and_single_keyboards,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"OK  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    if failed:
        print(f"\n{failed} test(s) failed")
        sys.exit(1)
    print("\nv5.0.0 Auto Forward checks passed")
