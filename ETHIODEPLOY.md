# EthioDeploy

1. Builder = Dockerfile
2. Start = `python bot.py`
3. Service = Background Worker (polling bot)
4. Deploy

## Persistent data
The bot stores admin registry, logs, and per-user Auto Forward destinations (`auto_forward_channels.json`) under `DATA_DIR` (default: `data/`). If the service filesystem is ephemeral, attach/configure a persistent volume in EthioDeploy and set `DATA_DIR` to its mounted directory. Otherwise managed admins and logs may be lost after a redeploy/restart.
