from pathlib import Path
import ast
s=Path('bot.py').read_text(encoding='utf-8')
ast.parse(s)
checks=[
 'VERSION = "v4.7.0"',
 'file_size',
 'downloaded',
 'async def send_album_summary(query, context):',
 'async def show_album_summary_draft(message, context):',
 'albumsummary:add_position', 'albumsummary:edit_position',
 'albumsummary:delete_position', 'albumsummary:confirm',
 'def build_album_summary_text(entries, main_artists, album_name):',
 '"downloaded": False',
]
for item in checks: assert item in s, item
print('v4.5.0 static checks passed')
