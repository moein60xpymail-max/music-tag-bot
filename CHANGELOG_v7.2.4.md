# Changelog — v7.2.4

## Added
- Telegram native channel picker for `/setchannel1` and `/setchannel2` using `KeyboardButtonRequestChat`.
- Manual numeric ID / username entry remains available as a fallback.
- Per-user Auto Forward destinations persisted in `DATA_DIR/auto_forward_channels.json`.

## Changed
- Each authorized Telegram user now has an independent Main and Full Archive channel pair.
- All Auto Forward send buttons, album flows, status views, and admin channel status read the acting user's own destinations.
- Selected channels are checked for bot administrator status and `Post Messages` permission before saving.

## Important
- The channel picker is available in the private chat with the bot.
- The selecting user must have posting rights in the channel. The bot itself must already be an administrator with permission to post.
- Configure a persistent `DATA_DIR` on EthioDeploy to preserve settings across redeploys.
