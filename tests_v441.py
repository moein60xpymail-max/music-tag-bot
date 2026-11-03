from pathlib import Path
import ast
s=(Path(__file__).parent/"bot.py").read_text(encoding="utf-8")
ast.parse(s)
checks=[
 'VERSION = "v4.6.0"',
 'def install_telegram_resilience():',
 'def download_to_drive_retry(',
 'def download_as_bytearray_retry(',
 'read_timeout=90',
 'Telegram formatting fallback:',
 'Callback acknowledgement warning:',
 'traceback.print_exception',
 'Album Name:',
]
for item in checks:
    assert item in s, item
assert 'await tg_file.download_to_drive(' not in s.replace('return await tg_file.download_to_drive(custom_path)', '')
print("v4.4.1 static checks passed")
