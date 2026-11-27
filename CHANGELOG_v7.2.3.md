# Changelog — v7.2.3

## Added
- Owner-only `/admin` panel.
- Add Auto Forward admins by Telegram username, using usernames previously observed by the bot.
- Remove managed admins and view the managed-admin list.
- Bot status, configured Auto Forward channels, filesystem storage usage, recent error log, and admin activity log views.
- Persistent admin registry under `DATA_DIR/admin_registry.json`.

## Changed
- `/setchannel1` and `/setchannel2` now allow the owner and Auto Forward admins.
- Managed admins receive Auto Forward permissions only; they cannot open `/admin` or use owner-only settings.
- Existing `ADMIN_IDS` environment entries remain supported for backward compatibility. They must still be removed from the host environment if you want to revoke those legacy permissions.

## Important
- Adding by username requires the target user to have interacted with the bot at least once so the bot can map the username to a Telegram numeric ID.
- Admin registry and logs are stored under `DATA_DIR`; configure a persistent volume/path on EthioDeploy if the host filesystem is ephemeral.
