from pathlib import Path
import ast

ROOT = Path(__file__).parent
source = (ROOT / "bot.py").read_text(encoding="utf-8")
tree = ast.parse(source)

assert 'VERSION = "v4.8.1"' in source
assert 'await clear_active_prompt(update, context)' in source
assert '"comment": "comment"' in source
assert 'callback_data="field:comment"' in source
assert 'f"Comment: {tags[\'comment\']}' in source
assert '"comment": ""' in source
assert 'if waiting == "album_remove_artist_name":' in source
assert 'if data == "album:remove_artist_confirm":' in source
assert 'callback_data="album:remove_artist"' in source

# Comment must be accepted by both selected-track editing and Apply to All.
assert 'allowed = {"artist", "albumartist", "album", "genre", "composer", "publisher", "year", "comment"}' in source
assert '"comment":"comment"' in source

# Summary entries carry each track's own comment and render it only when present.
assert '"comment": tags.get("comment", "—")' in source
assert 'comment_line = f"\\n   Comment: {comment}"' in source

# All standard tag deletion goes through MT_KEYS, including Comment.
remove_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "remove_all_tags")
assert "MT_KEYS.items()" in ast.get_source_segment(source, remove_fn)

# Archive mode uses the shared editor and the same Comment field.
assert '"archive_track"' in source
print("v4.8.0 static checks passed")
