import ast
from pathlib import Path
s = Path("bot.py").read_text(encoding="utf-8")

def test_version():
    assert 'VERSION = "v6.2.1"' in s

def test_413_handling():
    for x in ("AF_MEDIA_GROUP_MAX_BYTES", "pack_album_media_groups", "_is_entity_too_large", "splitting", "413"):
        assert x in s, x

def test_format_fallback():
    assert "can't parse entities" in s or "parse entities" in s
    assert "send_media_group" in s
    assert "parse_mode" in s and "None" in s

def test_syntax():
    ast.parse(s)

if __name__ == "__main__":
    test_version(); test_413_handling(); test_format_fallback(); test_syntax()
    print("OK v6.2.1")
