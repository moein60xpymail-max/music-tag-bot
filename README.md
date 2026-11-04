# Music Tag Bot — v4.8.2

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
