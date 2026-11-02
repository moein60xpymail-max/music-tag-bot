from pathlib import Path
import ast
source=(Path(__file__).parent/'bot.py').read_text(encoding='utf-8')
ast.parse(source)
for item in ['callback_data="arch:previous"','callback_data="arch:report"','callback_data="action:unreleased"','def archive_report_text(queue):']:
    assert item in source, item
print('legacy archive/unreleased regression checks passed')
