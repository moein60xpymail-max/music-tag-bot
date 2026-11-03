# Music Tag Bot — v4.6.0

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

## v4.6.0 changes
- Replaces per-file intake replies with one live progress dashboard for Album Mode, Summary intake, and Archive source A/B.
- Dashboard refreshes no more frequently than every 3 seconds and shows received, successful, skipped, failed counts, and the latest filename.
- The dashboard carries the relevant Finish button; archive dashboards become a final count message when the source is closed.
- Summary intake treats files over 20 MB as metadata-only entries instead of rejecting them.
- Progress is shown as counts rather than a percentage while the total number of incoming files is unknown; no misleading percentage is fabricated.
- No persistent progress state is stored. A host restart may interrupt an active intake, as agreed.
