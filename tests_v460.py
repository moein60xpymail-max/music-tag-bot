from pathlib import Path
import ast
s=Path('bot.py').read_text(encoding='utf-8')
ast.parse(s)
checks=[
 'VERSION = "v4.6.1"',
 'PROGRESS_UPDATE_INTERVAL = 3.0',
 'PROGRESS_IDLE_TIMEOUT = 6.0',
 'async def _progress_idle_finish(context, operation, state):',
 'asyncio.create_task(',
 'تشخیص خودکار پایان ارسال فایل‌ها پس از ۶ ثانیه سکوت',
 'async def update_progress_dashboard(',
 'async def finish_progress_dashboard(',
 'await update_progress_dashboard(message, context, "album"',
 'await update_progress_dashboard(message, context, "summary"',
 'await update_progress_dashboard(message, context, op,',
 'if outcome and not final:',
 'await update_progress_dashboard(_MessageRef(), context, operation, final=True)',
]
for item in checks: assert item in s, item
print('v4.6.1 static checks passed')
