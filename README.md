# Music Tag Bot — v7.0.1

رفع اساسی حذف Duplicate در Archive + ساده‌سازی Audio Scanner.

## تغییرات 7.0.1
- **Duplicate قطعی خودکار حذف می‌شود** از صف نهایی آرشیو (اولویت Source A بر B).
- داخل هر منبع هم فقط یک نسخه از فایل یکسان نگه داشته می‌شود.
- موارد Similar (۹۵–۹۹٪) حذف نمی‌شوند و در «نیازمند بررسی» می‌مانند.
- دکمه «تحلیل و ساخت فهرست» حذف شد؛ همه تحلیل‌ها در Audio Scanner است.
- UI اسکنر: شروع اسکن · گزارش · نیازمند بررسی · خروجی فهرست.
- خروجی `archive_manifest.txt` با قالب archive-v1 برای «ورودی بررسی منابع».
- بدون fpcalc هم content-hash کار می‌کند؛ با fpcalc دقت بالاتر است.

# Music Tag Bot — v7.0.0

ادغام پایدار نسخه‌های 6.2.8 و 6.3.0 با بازطراحی Archive Mode.

## v7.0.0 highlights
- **پایان Source A/B**: گزارش آماری → ادامه دریافت یا تأیید نهایی (دیگر پرش مستقیم نیست).
- **Analyze با Audio Fingerprint**: جایگزینی کامل معیارهای نام/خواننده/حجم/مدت با اثرانگشت صوتی.
- **بدون حذف خودکار**: تطابق‌ها فقط علامت‌گذاری می‌شوند؛ انتخاب نسخه با کاربر است.
- **خروجی TXT** فهرست Source با حفظ `file_id` در بخش `---DATA---`.
- **Fallback**: content-hash + soft-fp وقتی `fpcalc` نصب نباشد؛ با `fpcalc` از Chromaprint استفاده می‌شود.
- Scanner مستقل آرشیو (از 6.2.8) + fallbackهای 6.3.0 حفظ شده‌اند.
- Auto Forward ترتیبی، Single/Album، تگ/کاور بدون تغییر مخرب.

### نیاز سرور
- اختیاری ولی توصیه‌شده: `chromaprint` (`fpcalc`) — در `nixpacks.toml` آمده است.
- `DATA_DIR` پایدار برای SQLite اسکنر و نشست‌ها.

### ادغام از نسخه‌ها
| منبع | چه چیزی نگه داشته شد |
|------|----------------------|
| 6.2.8 | audio_scanner SQLite، ثبت track هنگام intake، منوی ascan، quarantine منطقی، chromaprint |
| 6.3.0 | content-hash، soft fingerprint، آستانه‌های EXACT/REVIEW، ایده‌های resume/کش |

---

# Music Tag Bot — v6.2.8

Built on v6.2.7.

## v6.2.8 — Audio Similarity Scanner
- Adds a dedicated Scanner section under Archive for tracks collected from Source A and Source B.
- Uses full-track Chromaprint `fpcalc` raw audio fingerprints; titles, artists, tags, and filenames are not used to calculate similarity.
- Downloads one Telegram track at a time, fingerprints it, then deletes the temporary local copy.
- Stores compact fingerprints and Telegram file references in `DATA_DIR/audio_scanner.sqlite3`; later scans reuse cached fingerprints.
- Similar tracks are grouped and show pairwise fingerprint similarity. The percentage is a fingerprint bit-similarity score, not a probability of identity.
- The user manually chooses one track to keep in each group. Other entries move to Scanner quarantine; restore is available.
- Quarantine is logical: it removes entries from the active Scanner catalog only. It does NOT delete original Telegram messages or media from source channels.
- The Scanner requires the system executable `fpcalc` from the Chromaprint package. Nixpacks setup now includes `chromaprint` alongside `ffmpeg`.
- Telegram Bot API downloads over 20 MB are reported as unprocessable by this bot setup; their names and reasons are shown in the final report.
- Persistent scan history requires `DATA_DIR` to point to persistent storage on the hosting provider.
- Because previous releases did not persist Source A/B Telegram references, the first scan after upgrading can only see tracks newly added after this release (or still present in the current in-memory archive session); re-add the source lists once after deployment to seed the new catalog.

## v6.2.7 Hotfix
- **Telegram Music/Audio always accepted** — no dependency on `.mp3` suffix or MIME.
- Document MP3 accepted via `.mp3` name **or** audio/empty/octet-stream MIME.
- Safer filename builder (keeps titles like `01. Intro`).
- Diagnostic log when a file is rejected.
- Single/Album/Archive/Summary paths covered.

## v6.2.6 Hotfix
- Accept Telegram **Music / Audio** messages even when `file_name` has no `.mp3` suffix.
- Separate resolution for `message.audio` vs `message.document` via `resolve_incoming_audio`.
- Documents still validated by extension and/or audio MIME; non-MP3 files rejected.
- Works in Single, Album, and Archive intake paths.

## v6.2.5 Hotfix
- Fix AttributeError in receive_audio when Update has no message (use effective_message + None guard).
- Escape dynamic song/artist/album/filename text for Markdown and HTML parse modes; keep parse-entities fallback.
- More resilient polling: longer getUpdates timeout, bootstrap_retries=-1, concurrent_updates=False, clearer transient NetworkError logging.

## v6.2.4 changes
- Album Auto Forward restored to sequential single-message sends (one track = one channel message).
- Removed Media Group packing and group 413 handling.
- Light delay between tracks to reduce host pressure on large albums.

## v6.2.4 changes
- Album Auto Forward restored to **sequential single-message** sends (one track = one channel message).
- Removed Media Group packing, 4–10 split algorithm, and group 413 handling.
- Light delay between tracks to reduce host pressure on large albums.
- Cover handling remains via existing  (same as pre-6.2.0 AF).

## v6.2.3 changes
- Root fix for album AF packing: full selected list is packed once (11 → 7+4).
- Removed invalid post-413 split that produced 4+3+4.
- On HTTP 413, all unsent tracks are re-packed with a smaller viable max_size (still min 4).
- Cover/thumbnail code left unchanged.

## v6.2.2 changes
- Album Auto Forward: strict count packing restored (11 → 7+4); no preemptive size splits that orphan a middle track.
- Per-track cover/thumbnail attached to each InputMediaAudio in media groups.
- On HTTP 413 only, groups are split with a controlled algorithm; generic errors no longer force silent single sends.

## v6.2.1 changes
- Album Auto Forward media groups respect total payload size; on HTTP 413 groups are split and retried without duplicate sends.
- Stronger Telegram parse-entities fallback for messages/captions (including media group related calls).

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
