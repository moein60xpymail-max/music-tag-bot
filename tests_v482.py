from pathlib import Path
import ast

ROOT = Path(__file__).parent
source = (ROOT / "bot.py").read_text(encoding="utf-8")
tree = ast.parse(source)
assert 'VERSION = "v4.8.2"' in source

editor_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "editor_text")
summary_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "build_album_summary_text")
overview_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "show_album_overview")
select_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "album_select_keyboard")

assert "Comment:" not in ast.get_source_segment(source, editor_fn)
assert "comment_line" not in ast.get_source_segment(source, summary_fn)
assert '💿 Album: {album}' in ast.get_source_segment(source, overview_fn)
assert 'summary_album' in ast.get_source_segment(source, overview_fn)
assert ' · {album}' in ast.get_source_segment(source, select_fn)
# The Comment field itself remains in the editable keyboard and tag model.
assert 'callback_data="field:comment"' in source
assert '"comment": "comment"' in source
print("v4.8.2 targeted checks passed")
