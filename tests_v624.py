import ast
from pathlib import Path
s = Path("bot.py").read_text(encoding="utf-8")

def test_version():
    assert 'VERSION = "v6.2.4"' in s

def test_no_media_group_send_path():
    assert "InputMediaAudio" not in s
    assert "pack_album_media_groups" not in s
    assert "album_media_group_sizes" not in s
    assert "Sequential single-message send" in s

def test_sequential_uses_send_processed():
    assert "await asyncio.sleep(0.35)" in s
    idx = s.find("Sequential single-message send")
    assert idx > 0
    assert "send_processed_to_channel" in s[idx:idx+2000]

def test_syntax():
    ast.parse(s)

if __name__ == "__main__":
    test_version(); print("OK test_version")
    test_no_media_group_send_path(); print("OK test_no_media_group_send_path")
    test_sequential_uses_send_processed(); print("OK test_sequential_uses_send_processed")
    test_syntax(); print("OK test_syntax")
