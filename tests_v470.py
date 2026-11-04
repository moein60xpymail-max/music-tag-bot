from pathlib import Path
import ast

ROOT = Path(__file__).parent
source = (ROOT / "bot.py").read_text(encoding="utf-8")
ast.parse(source)
assert 'VERSION = "v4.8.0"' in source
assert "async def send_temporary_prompt" in source
assert "async def clear_active_prompt" in source
assert "active_prompt_message_id" in source
assert "await clear_active_prompt(update, context)" in source
assert 'if data == "album:back":' in source
assert 'await query.message.delete()' in source
assert 'if action == "menu":' in source and "await query.message.edit_text" in source
print("v4.7.0 static checks passed")
