"""Shared configuration for Music Tag Bot v3."""
import os
from pathlib import Path
VERSION = "3.0.0"
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
MAX_FILE_SIZE = 20 * 1024 * 1024
ARCHIVE_FIELDS = ("artist", "albumartist", "album", "genre", "composer", "publisher", "year")
