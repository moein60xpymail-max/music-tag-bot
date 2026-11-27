from pathlib import Path

SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")

def test_version_and_native_picker():
    assert 'VERSION = "v7.2.4"' in SOURCE
    assert 'KeyboardButtonRequestChat' in SOURCE
    assert 'filters.StatusUpdate.CHAT_SHARED' in SOURCE
    assert 'async def receive_chat_shared' in SOURCE

def test_per_user_persistent_channels():
    assert 'AF_CHANNELS_FILE = DATA_DIR / "auto_forward_channels.json"' in SOURCE
    assert 'def load_af_channels_data' in SOURCE
    assert 'def save_af_channels_data' in SOURCE
    assert 'str(int(user_id))' in SOURCE

def test_send_flow_reads_user_scoped_channels():
    assert 'get_af_channel(context, slot, uid)' in SOURCE
    assert 'get_af_channel(context, "1", user_id)' in SOURCE
    assert 'get_af_channel(context, "2", user_id)' in SOURCE

def test_picker_validates_bot_permissions():
    block = SOURCE.split('async def receive_chat_shared', 1)[1].split('# Backward-compat helpers', 1)[0]
    assert 'get_chat_member' in block
    assert 'can_post_messages' in block
    assert 'set_af_channel(context, slot, chat.id, title, user.id)' in block
