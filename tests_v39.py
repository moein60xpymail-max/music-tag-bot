from pathlib import Path
import ast

ROOT = Path(__file__).parent
source = (ROOT / "bot.py").read_text(encoding="utf-8")
ast.parse(source)
assert 'VERSION = "v3.9"' in source
assert 'callback_data="arch:previous"' in source
assert 'callback_data="arch:report"' in source
assert 'callback_data="album:unreleased"' in source
assert 'set_tag(path, "album", "Unreleased Track")' in source
assert 'def archive_report_text(queue):' in source
print("v3.9 static checks passed")
