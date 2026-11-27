# Changelog — v7.2.2

## Fixed
- Album Mode: after artist + album name, incoming audio/MP3 tracks are received again
- Album Mode intake now has priority over Self-Scan and Archive source buckets so files are not stolen
- Explicit per-track acknowledgement + Finish Uploading keyboard available immediately
- Download failures notify the user instead of silent ignore
- Duplicate Telegram message_id ignored within an album session
- album_folder recreated if missing mid-session

## Notes
- Single Mode, Archive, Self-Scan unchanged in intent
- Entry point remains python bot.py
