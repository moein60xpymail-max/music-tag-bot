# Music Tag Bot v3.4

## Archive duplicate detection update
- Compares Telegram audio title metadata first; falls back to filename without extension.
- Compares duration when Telegram provides it. Exact normalized title + duration difference <= 2 seconds is marked as a strong duplicate unless both artist values conflict.
- Compares artist/performer metadata when available. Conflicting artists send the pair to manual review.
- Similar titles are flagged for manual review only; they are never auto-merged.
- File size is only a weak supporting signal and is never used alone to declare a duplicate.
- No track is automatically deleted or removed from the queue.

For MP3s sent as Telegram documents, Telegram may not expose embedded title/artist/duration metadata. In that case the bot falls back to the filename and marks uncertain matches for review. Audio files sent as Telegram Audio provide richer metadata.

## Deploy
Keep Start Command: `python bot.py`. Keep `BOT_TOKEN` and `OWNER_ID` in environment variables; do not commit secrets.

## v3.7 archive request display
- Shows archive progress percentage on each requested track.
- Adds a Telegram copy-text button for the current track title.
- Changes are limited to the archive track prompt.
