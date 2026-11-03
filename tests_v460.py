from pathlib import Path
import ast
s=Path('bot.py').read_text(encoding='utf-8')
ast.parse(s)
checks=[
 'VERSION = "v4.6.0"',
 'PROGRESS_UPDATE_INTERVAL = 3.0',
 'async def update_progress_dashboard(',
 'async def finish_progress_dashboard(',
 'await update_progress_dashboard(message, context, "album"',
 'await update_progress_dashboard(message, context, "summary"',
 'await update_progress_dashboard(message, context, op,',
 'داشبورد پیشرفت تک‌پیامی',
]
for item in checks: assert item in s, item
print('v4.6.0 static checks passed')
