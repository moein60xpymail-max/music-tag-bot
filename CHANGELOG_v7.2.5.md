# Music Tag Bot v7.2.5

## Fixes
- Fixed a constructor error in the Telegram channel-picker keyboard. The previous code created `ChatAdministratorRights` with only `can_post_messages`, which is incomplete for python-telegram-bot 21.10 and could raise before the bot sent any reply to `/setchannel1` or `/setchannel2`.
- The picker now uses the native `KeyboardButtonRequestChat` channel filter without a partial administrator-rights object.
- The bottom keyboard now stays visible (`is_persistent=True`) and uses the requested `➕ افزودن کانال` button.
- Channel setup now has an explicit error fallback: if the picker cannot be built/sent, the bot replies with manual ID/username instructions and logs the exception instead of failing silently.
- Existing per-user channel storage and owner/admin authorization are preserved.

## Validation
- Python compilation and AST parsing passed.
- Live Telegram interaction was not available in this environment; verify the picker on the deployed bot after uploading this ZIP.
