from pathlib import Path
import ast

ROOT = Path(__file__).parent
source = (ROOT / "bot.py").read_text(encoding="utf-8")
ast.parse(source)
assert 'REQUIRED_CHANNEL = "@Nistplay"' in source
assert 'REQUIRED_CHANNEL_URL = "https://t.me/Nistplay"' in source
assert 'callback_data="membership:check"' in source
assert 'await context.bot.get_chat_member(' in source
assert 'app.add_handler(TypeHandler(Update, membership_gate), group=-1)' in source
assert 'raise ApplicationHandlerStop' in source
assert 'status == "restricted"' in source
assert 'عضویت اجباری' in source
print("v4.8.1 membership-gate compatibility checks passed")
