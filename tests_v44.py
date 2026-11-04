from pathlib import Path
import ast
s=(Path(__file__).parent/'bot.py').read_text(encoding='utf-8')
ast.parse(s)
checks=[
 'VERSION = "v4.8.1"',
 'Album Name:',
 'def explain_media_error(exc, stage):',
 'def failed_media_message(label, exc, stage):',
 'context.user_data.get("album_receive_attempt", 0) + 1',
 'failed_media_message(label, e, stage)',
 'failed_media_message(label, exc, stage)',
]
for x in checks: assert x in s, x
print('v4.4 static checks passed')
