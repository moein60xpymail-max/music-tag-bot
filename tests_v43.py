from pathlib import Path
import ast
ROOT=Path(__file__).parent
source=(ROOT/'bot.py').read_text(encoding='utf-8')
ast.parse(source)
checks=[
 'VERSION = "v4.5.0"',
 'callback_data="album:tools"',
 'callback_data="albumtools:renumber"',
 'callback_data="albumtools:disc"',
 'callback_data="albumtools:reorder"',
 'callback_data="albumtools:filters"',
 'callback_data="albumtools:copy"',
 'albumtools:renumber_confirm',
 'albumtools_copy_source',
 'albumtools_disc_value',
 'def _album_missing_and_duplicates(tracks):',
 'def album_tools_keyboard():',
]
for item in checks:
 assert item in source, item
print('v4.3 static checks passed')
