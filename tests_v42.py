from pathlib import Path
import ast
ROOT = Path(__file__).parent
source = (ROOT / "bot.py").read_text(encoding="utf-8")
ast.parse(source)
assert 'VERSION = "v4.3"' in source
assert 'sort_album_tracks(tracks)' in source
assert 'def sort_album_tracks(tracks):' in source
assert 'callback_data="album:select_menu"' in source
assert 'callback_data=f"album:select:{idx}"' in source
assert 'callback_data="album:select_done"' in source
assert 'waiting == "album_selected_value"' in source
assert 'مرتب‌سازی آلبوم بر اساس شماره ترک پس از پایان دریافت فایل‌ها' in source
print("v4.3 compatibility checks passed")
