# Music Tag Bot v3.9

## Changes in v3.9
- Archive report: totals for confirmed, skipped, failed, review-flagged, and remaining tracks, plus confirmed percentage.
- Archive previous-track navigation from the request prompt and the saved-track controls. Reopening a prior track makes it editable again and does not lose the current in-progress track.
- Album editor only: `Unreleased Track` button changes only the selected track's Album tag to `Unreleased Track`. It does not apply to all tracks.

## Deployment
Keep all files inside `v36work/` together in the same folder in your repository. Keep existing environment variables, including `BOT_TOKEN`, unchanged. Start command: `python bot.py`.

The package was syntax-compiled locally. Telegram/host runtime testing is still required after deployment.
