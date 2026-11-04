from pathlib import Path
import ast

ROOT = Path(__file__).parent
source = (ROOT / "bot.py").read_text(encoding="utf-8")
tree = ast.parse(source)

# Comment remains editable and supported by bulk operations.
assert '"comment": "comment"' in source
assert 'callback_data="field:comment"' in source
assert '"comment": ""' in source
assert 'if waiting == "album_remove_artist_name":' in source
assert 'if data == "album:remove_artist_confirm":' in source
assert 'callback_data="album:remove_artist"' in source
assert 'allowed = {"artist", "albumartist", "album", "genre", "composer", "publisher", "year", "comment"}' in source

# Comment must not be rendered in the track editor's information summary or Album Summary.
editor_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "editor_text")
summary_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "build_album_summary_text")
assert "Comment:" not in ast.get_source_segment(source, editor_fn)
assert "comment_line" not in ast.get_source_segment(source, summary_fn)
assert '"comment": tags.get("comment"' not in source

remove_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "remove_all_tags")
assert "MT_KEYS.items()" in ast.get_source_segment(source, remove_fn)
print("v4.8.0 compatibility checks passed")
