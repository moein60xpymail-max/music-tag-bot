# Music Tag Bot — v6.2.0

Built on v6.1.1.

## v6.2.0 changes
- Album Auto Forward: send selected tracks as Telegram media groups (max 10, last group >= 4 when possible).
- Album Summary: closes album menu on open; summary stays visible on Edit Summary and Back.
- Album Apply Default Tags / Save All: same 3s status delay pattern as Single Track.

## v6.1.1 changes
- Fix Telegram BadRequest **Message is not modified** on menu edits (e.g. Default Tags Close).
- Added  /  that only ignore this specific error.
- Callback queries still answered; unrelated BadRequest errors are not swallowed.

## v6.1.0 changes
- Default Tags **Export** / **Import** (`default_tags.json`) for the bot owner only.
- Export includes Single + Album defaults (values and Apply/Keep/Remove modes).
- Import validates JSON, shows a summary, requires confirmation, applies atomically.

## v6.0.0 changes
- Swapped main menu buttons: Album (left) / Single Track (right) with updated emojis.
- Editor: selecting a field opens direct edit (Copy / Remove / Cancel); intermediate Edit menu removed.
- Editor menu is closed when an option is chosen and restored after success or cancel.
- Show Lyrics includes Cancel; Picture opens upload with Cancel only.
- Apply Default Tags and Save show a ~3s status message before the real operation; duplicate taps blocked.
- Album Mode: real Cancel/Exit button; request album name after main artists; Album tag applied on finish.
- Album menu compacted: Edit Selected Track (+ pagination) instead of per-track buttons; Remove Artist label in English.
- Global tab navigation helpers for cleaner menu lifecycle.
- Apply-to-All / Group Edit field selection via inline buttons (not typed names).

## v5.2.1 changes
- Needs Review warnings always show the exact matched criteria from live classify_detail (Title/Artist/Duration/File Size).
- Criteria recomputed on display when missing from stored queue data (e.g. older manifests).
- Message label: «دلیل تشابه» with bullet list of real matches only.

## v5.2.0 changes
- Archive Needs Review: shows matched track name(s) and the exact shared criteria (2 of 4 signals).
- Collection summary lists track names missing Artist and/or Album tags (title or filename); long reports are split across messages.
- Duplicate classification algorithm unchanged (3+ definite, 2 manual/review, else independent).

## v5.1.0 changes
- Archive Mode: choose start point (from beginning or from a specific track number); earlier tracks are skipped for that run while real numbering is preserved.
- Default Tags: three modes per field — Apply / Keep / Remove; expanded fields (title, artist, albumartist, album, genre, year, composer, publisher, comment, lyrics, disc, track).
- Remove deletes the actual tag frame (not just empty value) without touching cover art or other tags.
- Auto Forward restricted to bot owner and `ADMIN_IDS` (comma-separated Telegram user IDs). Unauthorized users cannot send even via raw callbacks.
- Dual destination channels: `/setchannel1` (Main), `/setchannel2` (Full Archive), independent send status per channel; `/channelstatus` shows both.
- Channel config remains owner-only and RAM-only.

## v5.0.0 changes
- Adds Auto Forward to a destination channel for Single Track, Album, and Archive modes.
- Destination channel is configured only by the bot owner (`OWNER_ID`) via `/setchannel` and stored in RAM only (`application.bot_data`); it does not survive a restart.
- `/channelstatus` shows the current destination (if any).
- Sends processed audio with `send_audio` (no original sender attribution, no original caption). Falls back to `send_document` only on clear audio-media errors; network/timeout/permission errors do not auto-fallback (avoids possible duplicates) and may be marked `unknown`.
- Per-track status: `pending` / `sending` / `sent` / `failed` / `unknown`. Successful tracks are never re-sent. Failed tracks can be retried.
- Album Mode: after Save All, user selects which tracks to forward; each selection is bound to the real output file path. Only `pending` and `failed` tracks are selectable.
- Output files are kept until Auto Forward finishes; cleanup follows the existing bot lifecycle.
- No test message is posted to the channel when configuring it.

## v4.8.2 changes
- Removes Comment from the single-track editor information summary and Album Summary output; the Comment field remains editable and is still saved normally.
- Album Mode track overview now displays each track's Album tag alongside its title, artist, and generated filename.
- Track-selection buttons also include the Album tag for easier verification.

## v4.8.1 changes
- Requires every user to join the public Telegram channel `@Nistplay` before using any bot command, callback, or file-processing flow.
- Adds a Join Channel button and a “I Joined” recheck button. Membership is checked with Telegram `getChatMember` on every incoming update; users who leave are blocked on their next interaction.
- The bot must remain an administrator in `@Nistplay` so Telegram can reliably report other users’ membership. If Telegram cannot verify membership, access is denied temporarily rather than bypassing the gate.


## v4.8.0 changes
- Adds the ID3 Comment field to the single-track editor and album-track editor.
- Adds Comment to Album Mode Apply to All, selected-track Group Edit, tag copying, and Single/Album Default Tags.
- Existing saved defaults are migrated to include Comment without overwriting previously configured values.
- Album Summary displays each downloaded track's Comment when present.
- Comment remains part of normal save/send and Remove All tag handling.
- Ports the Artist-removal action from the older v4.6.1 patch into the v4.7.0 codebase without replacing v4.7.0 prompt-management fixes.
- Artist removal matches a complete artist name, previews affected tracks, asks for confirmation, and backs up files for rollback.

## v4.7.0 changes
- Adds centralized temporary-prompt tracking for interactive text-entry flows.
- A prompt is removed after the user's next response; invalid input replaces the old prompt with the new instruction.
- The Album Summary screen is removed when returning to Album Tracks.
- Summary edit screens are reused for the edit menu and removed when entering add/edit/delete input steps or confirming/canceling.
- Field detail cards are removed when the user starts editing a field, including Year.
- Persistent results, final summaries, and important error messages are not automatically deleted.

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


## v4.6.1 changes
- Automatically marks the current intake dashboard as quiet/completed after 6 seconds without a newly processed file.
- Every newly processed file restarts the 6-second inactivity timer.
- The final dashboard refresh bypasses the normal 3-second throttle.
- The 6-second quiet detection does not close Album, Summary, or Archive mode; a late file can still arrive and resume the dashboard.
