"""Static regression checks for v7.2.4 owner/admin panel."""
from pathlib import Path

SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")

def test_version_and_owner_panel():
    assert 'VERSION = "v7.2.4"' in SOURCE
    assert 'CommandHandler("admin", admin_command)' in SOURCE
    assert 'if not is_owner(user.id if user else None)' in SOURCE

def test_managed_admins_are_persistent_and_used_for_autoforward():
    assert 'ADMIN_REGISTRY_FILE = DATA_DIR / "admin_registry.json"' in SOURCE
    assert 'return uid in ADMIN_IDS or uid in managed_admin_ids()' in SOURCE
    assert 'def add_admin_by_username' in SOURCE
    assert 'def remove_managed_admin' in SOURCE

def test_channel_setup_accepts_autoforward_admins():
    a = SOURCE.split('async def setchannel1_command', 1)[1].split('async def setchannel2_command', 1)[0]
    b = SOURCE.split('async def setchannel2_command', 1)[1].split('async def channelstatus_command', 1)[0]
    assert 'if not can_auto_forward(' in a
    assert 'if not can_auto_forward(' in b

def test_admin_callbacks_are_owner_protected():
    chunk = SOURCE.split('async def admin_callback_handler', 1)[1].split('# ==================== دستورات', 1)[0]
    assert 'is_owner(query.from_user.id' in chunk
    assert 'pattern=r"^admin:"' in SOURCE
