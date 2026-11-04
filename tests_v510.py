"""Static checks for Music Tag Bot v5.1.0."""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

def src():
    with open(os.path.join(ROOT, "bot.py"), encoding="utf-8") as f:
        return f.read()

def test_version():
    s = src()
    assert 'VERSION = "v5.1.0"' in s

def test_archive_start_point():
    s = src()
    for x in ("arch:start_begin", "arch:start_pick", "arch:start_cancel", "archive_start_number"):
        assert x in s, x

def test_default_modes():
    s = src()
    assert "DEFAULT_FIELD_ORDER" in s
    assert "_default_mode" in s
    assert '"remove"' in s or "'remove'" in s
    assert "Apply" in s and "Keep" in s and "Remove" in s
    for f in ("title", "artist", "albumartist", "album", "genre", "year", "composer", "publisher", "comment", "lyrics"):
        assert f'"{f}"' in s

def test_admin_access():
    s = src()
    assert "ADMIN_IDS" in s
    assert "can_auto_forward" in s
    assert "فقط برای مالک و ادمین‌های ربات" in s

def test_dual_channels():
    s = src()
    assert "setchannel1_command" in s and "setchannel2_command" in s
    assert "auto_forward_channels" in s
    assert "autofw:single:1" in s and "autofw:single:2" in s
    assert "get_af_channel" in s

def test_no_unauthorized_send_path():
    s = src()
    # permission check before AF sends
    assert "can_auto_forward" in s
    idx = s.index("if isinstance(data, str) and data.startswith(\"autofw:\")")
    chunk = s[idx:idx+400]
    assert "can_auto_forward" in chunk

def test_membership_and_af_intact():
    s = src()
    assert "membership_gate" in s and "@Nistplay" in s
    assert "send_processed_to_channel" in s
    assert "_is_audio_media_error" in s

def test_syntax():
    ast.parse(src())

if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("OK ", name)
            except Exception as e:
                failed += 1
                print("FAIL", name, e)
    if failed:
        sys.exit(1)
    print("\nv5.1.0 checks passed")
