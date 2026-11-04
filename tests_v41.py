from pathlib import Path
import ast

ROOT = Path(__file__).parent
source = (ROOT / "bot.py").read_text(encoding="utf-8")
ast.parse(source)
checks = [
    'VERSION = "v4.8.1"',
    'VERSION_CHANGES = [',
    'UNRELEASED_COVER_FILE = DATA_DIR / "unreleased_track_cover.jpg"',
    'نسخه فعلی ربات شما:',
    '🖼️ مدیریت کاور Unreleased Track',
    'BotCommand("unreleasedcover", "🖼️ مدیریت کاور Unreleased Track")',
    'CommandHandler("unreleasedcover", unreleased_cover_command)',
    'waiting == "unreleased_default_cover"',
    'callback_data="unrel:replace"',
    'callback_data="unrel:keep"',
    'callback_data="unrel:cancel"',
    'update_album_track_cover(context, path, image_data)',
    'if data == "action:unreleased":',
]
for item in checks:
    assert item in source, item
assert 'KeyboardButton("🖼️ مدیریت کاور Unreleased Track")' not in source
print("v4.1.1 compatibility checks passed")
