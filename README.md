# Music Tag Bot v3.1

## Deploy
- Python start command: `python bot.py`
- Required environment variable: `BOT_TOKEN`
- Optional: `DATA_DIR` (defaults to `data`)
- Dependencies: `pip install -r requirements.txt`

## Archive (basic integrated workflow)
Use **📚 آرشیو موسیقی**, add MP3 files to source A and source B, finish each source, analyze to build a queue, inspect the list, and open pending tracks for editing. Similar filenames are flagged, never automatically deleted. Opening a track downloads it; saving sends the edited MP3 and marks it confirmed.

Archive session state currently lives in Telegram bot process memory (`context.user_data`). It is not durable across process restarts/redeploys. Duplicate detection is filename-based, not audio-fingerprint or full metadata analysis. This is an initial integrated archive workflow, not the full persistent/recovery specification.
