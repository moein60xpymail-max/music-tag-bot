from pathlib import Path
import ast

ROOT = Path(__file__).parent
source = (ROOT / "bot.py").read_text(encoding="utf-8")
ast.parse(source)
assert 'VERSION = "v4.6.0"' in source
assert 'callback_data="action:unreleased"' in source
assert 'if data == "action:unreleased":' in source
assert 'set_tag(path, "album", "Unreleased Track")' in source
assert '📊 خلاصه آهنگ‌ها' in source
assert 'if data == "summary:finish":' in source
assert 'summary_tracks' in source
assert 'archive_report_text(queue)' in source
print("v4.0 compatibility checks passed")
