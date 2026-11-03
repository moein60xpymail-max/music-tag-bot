# Music Tag Bot — v4.5.0

## v4.5.0 changes
- Album Mode can register tracks larger than 20 MB using Telegram-provided metadata without downloading the media file.
- A failed/omitted download for one large album track no longer prevents the album summary from being generated.
- Album Summary includes a text-only editor to insert, edit, or delete tracks.
- Inserting a track at a chosen position automatically renumbers the list.
- Track count, total duration, and unique artist count are recalculated from the edited summary.
- Summary edits are confirmed separately and do not modify MP3 files or tags.
- The version screen lists only the current release's changes.

## Notes
- The 20 MB download limit remains for Single and Archive workflows. The Album Mode exception is metadata-only; large media is not downloaded.
- Telegram metadata may not include title, performer, or duration for document uploads. Missing values can be completed in Album Summary Editor.
- Album Summary edits affect only the summary text/data, not the original MP3 tags or the files sent by Save All.
- Existing environment variables are unchanged. Start command remains `python bot.py`.
