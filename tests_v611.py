import ast
from pathlib import Path
ROOT = Path(__file__).resolve().parent
s = (ROOT / "bot.py").read_text(encoding="utf-8")

def test_version():
    assert 'VERSION = "v6.1.1"' in s

def test_safe_helpers():
    assert "def _is_message_not_modified" in s
    assert "async def safe_edit_message_text" in s
    assert "async def safe_edit_reply_markup" in s
    assert 'await query.message.edit_text(' not in s
    assert 'await safe_edit_message_text(query.message, "بسته شد.")' in s
    assert "message is not modified" in s

def test_query_answer_still_present():
    assert "await query.answer()" in s

def test_syntax():
    ast.parse(s)

if __name__ == "__main__":
    test_version(); test_safe_helpers(); test_query_answer_still_present(); test_syntax()
    print("OK all v6.1.1 checks")
