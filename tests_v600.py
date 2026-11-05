"""Static checks for Music Tag Bot v6.0.0 UX redesign."""
import ast, os, sys
ROOT = os.path.dirname(os.path.abspath(__file__))

def src():
    return open(os.path.join(ROOT, "bot.py"), encoding="utf-8").read()

def test_version():
    assert 'VERSION = "v6.0.0"' in src()

def test_button_order():
    s = src()
    assert 'KeyboardButton("💿 حالت آلبوم"), KeyboardButton("🎵 حالت تکی آهنگ")' in s

def test_direct_field_edit():
    s = src()
    assert "direct_field_keyboard" in s
    assert "nav_push" in s and "safe_delete_message" in s
    assert 'callback_data=f"copy:{field}"' in s or "Copy" in s

def test_status_delays():
    s = src()
    assert "در حال اعمال تگ‌های پیش‌فرض" in s
    assert "در حال ذخیره آهنگ" in s
    assert "op_busy" in s
    assert "asyncio.sleep(1)" in s

def test_album_flow():
    s = src()
    assert "album:exit" in s
    assert 'waiting"] = "album_name"' in s or "album_name" in s
    assert "Edit Selected Track" in s
    assert "album:pick_tracks" in s
    assert "Remove Artist from All Tracks" in s
    assert "album_track_pick_keyboard" in s

def test_field_pickers():
    s = src()
    assert "field_pick_keyboard" in s
    assert "afield:" in s and "gfield:" in s

def test_lyrics_cancel():
    s = src()
    assert "lyrics_back_keyboard" in s

def test_syntax():
    ast.parse(src())

if __name__ == "__main__":
    failed = 0
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            try:
                f(); print("OK", n)
            except Exception as e:
                failed += 1; print("FAIL", n, e)
    sys.exit(1 if failed else 0)
