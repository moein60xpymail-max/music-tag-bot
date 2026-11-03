# Music Tag Bot v4.4

## Changes in v4.4
- Album Summary now displays Album Name below Album Artist(s). If multiple distinct album names exist in the received tracks, all are shown.
- Failed audio intake now reports the filename (or attempt number fallback) and a categorized likely cause: Telegram/network delivery, unavailable file reference, invalid/corrupt MP3, or local storage/read failure.
- Album intake attempt numbers advance even when a file fails, so a failure can be identified without confusing it with the count of successfully received tracks.

Run with `python bot.py`. Keep existing environment variables unchanged.
