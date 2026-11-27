"""Telegram channel repository for permanent audio storage without host downloads.

Stores only Telegram references (chat_id, message_id, file_id, metadata).
Audio files stay on Telegram servers; the bot uses copyMessage when needed.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

REPO_CHANNELS_FILE = "repo_channels.json"
REPO_DB_NAME = "channel_repo.sqlite3"


def _channels_path(data_dir: Path) -> Path:
    return Path(data_dir) / REPO_CHANNELS_FILE


def _db_path(data_dir: Path) -> Path:
    return Path(data_dir) / REPO_DB_NAME


def load_repo_channels(data_dir: Path) -> dict:
    path = _channels_path(data_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_repo_channels(data_dir: Path, data: dict) -> None:
    path = _channels_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def get_repo_channel(data_dir: Path, user_id: int, slot: str) -> Optional[dict]:
    """slot: source_a | source_b | dest"""
    all_ch = load_repo_channels(data_dir)
    user_key = str(user_id)
    return (all_ch.get(user_key) or {}).get(slot)


def set_repo_channel(
    data_dir: Path,
    user_id: int,
    slot: str,
    chat_id: int,
    title: str = "",
    username: str = "",
) -> None:
    all_ch = load_repo_channels(data_dir)
    user_key = str(user_id)
    all_ch.setdefault(user_key, {})
    all_ch[user_key][slot] = {
        "chat_id": int(chat_id),
        "title": title or "",
        "username": username or "",
        "updated_at": time.time(),
    }
    save_repo_channels(data_dir, all_ch)


def clear_repo_channel(data_dir: Path, user_id: int, slot: str) -> None:
    all_ch = load_repo_channels(data_dir)
    user_key = str(user_id)
    if user_key in all_ch and slot in all_ch[user_key]:
        del all_ch[user_key][slot]
        save_repo_channels(data_dir, all_ch)


@contextmanager
def _conn(data_dir: Path):
    path = _db_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        yield con
        con.commit()
    finally:
        con.close()


def init_db(data_dir: Path) -> None:
    with _conn(data_dir) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS repo_tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                file_id TEXT,
                file_unique_id TEXT,
                file_name TEXT,
                title TEXT,
                performer TEXT,
                duration INTEGER,
                file_size INTEGER,
                mime_type TEXT,
                status TEXT DEFAULT 'received',
                matched INTEGER DEFAULT 0,
                notes TEXT,
                created_at REAL,
                updated_at REAL,
                UNIQUE(owner_id, chat_id, message_id)
            );
            CREATE INDEX IF NOT EXISTS idx_repo_owner_source
                ON repo_tracks(owner_id, source);
            CREATE INDEX IF NOT EXISTS idx_repo_title
                ON repo_tracks(owner_id, title);
            CREATE INDEX IF NOT EXISTS idx_repo_file_unique
                ON repo_tracks(owner_id, file_unique_id);
            CREATE INDEX IF NOT EXISTS idx_repo_file_name
                ON repo_tracks(owner_id, file_name);

            CREATE TABLE IF NOT EXISTS queue_state (
                owner_id INTEGER PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at REAL
            );
            """
        )


def upsert_track(
    data_dir: Path,
    owner_id: int,
    source: str,
    chat_id: int,
    message_id: int,
    *,
    file_id: str = "",
    file_unique_id: str = "",
    file_name: str = "",
    title: str = "",
    performer: str = "",
    duration: int = 0,
    file_size: int = 0,
    mime_type: str = "",
    status: str = "received",
) -> int:
    now = time.time()
    with _conn(data_dir) as con:
        con.execute(
            """
            INSERT INTO repo_tracks (
                owner_id, source, chat_id, message_id,
                file_id, file_unique_id, file_name, title, performer,
                duration, file_size, mime_type, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_id, chat_id, message_id) DO UPDATE SET
                file_id=excluded.file_id,
                file_unique_id=excluded.file_unique_id,
                file_name=excluded.file_name,
                title=excluded.title,
                performer=excluded.performer,
                duration=excluded.duration,
                file_size=excluded.file_size,
                mime_type=excluded.mime_type,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (
                owner_id, source, chat_id, message_id,
                file_id, file_unique_id, file_name, title, performer,
                duration or 0, file_size or 0, mime_type, status, now, now,
            ),
        )
        row = con.execute(
            "SELECT id FROM repo_tracks WHERE owner_id=? AND chat_id=? AND message_id=?",
            (owner_id, chat_id, message_id),
        ).fetchone()
        return int(row["id"]) if row else 0


def count_tracks(data_dir: Path, owner_id: int, source: Optional[str] = None) -> int:
    with _conn(data_dir) as con:
        if source:
            row = con.execute(
                "SELECT COUNT(*) AS c FROM repo_tracks WHERE owner_id=? AND source=?",
                (owner_id, source),
            ).fetchone()
        else:
            row = con.execute(
                "SELECT COUNT(*) AS c FROM repo_tracks WHERE owner_id=?",
                (owner_id,),
            ).fetchone()
        return int(row["c"]) if row else 0


def list_tracks(
    data_dir: Path,
    owner_id: int,
    source: Optional[str] = None,
    limit: int = 5000,
) -> list[dict]:
    with _conn(data_dir) as con:
        if source:
            rows = con.execute(
                "SELECT * FROM repo_tracks WHERE owner_id=? AND source=? ORDER BY id LIMIT ?",
                (owner_id, source, limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM repo_tracks WHERE owner_id=? ORDER BY id LIMIT ?",
                (owner_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]


def update_track_status(data_dir: Path, track_id: int, status: str, notes: str = "") -> None:
    with _conn(data_dir) as con:
        con.execute(
            "UPDATE repo_tracks SET status=?, notes=?, updated_at=? WHERE id=?",
            (status, notes, time.time(), track_id),
        )


def _norm(s: str) -> str:
    if not s:
        return ""
    s = s.lower().strip()
    for ch in "[](){}-_./\\":
        s = s.replace(ch, " ")
    return " ".join(s.split())


def match_track(
    data_dir: Path,
    owner_id: int,
    *,
    title: str = "",
    artist: str = "",
    file_name: str = "",
    duration: int = 0,
    preferred_source: Optional[str] = None,
) -> tuple[Optional[dict], str]:
    """Return (best_match_or_None, confidence).

    confidence: exact | high | ambiguous | none
    Never auto-picks when multiple similar candidates exist.
    """
    tracks = list_tracks(data_dir, owner_id, preferred_source)
    if preferred_source is None:
        tracks = list_tracks(data_dir, owner_id)

    if not tracks:
        return None, "none"

    nt = _norm(title)
    na = _norm(artist)
    nf = _norm(file_name)
    stem = _norm(Path(file_name).stem) if file_name else ""

    exact = []
    high = []
    soft = []

    for t in tracks:
        tt = _norm(t.get("title") or "")
        tp = _norm(t.get("performer") or "")
        tn = _norm(t.get("file_name") or "")
        tstem = _norm(Path(t.get("file_name") or "").stem)
        td = int(t.get("duration") or 0)

        score = 0
        if nt and tt and nt == tt:
            score += 50
        elif nt and tt and (nt in tt or tt in nt):
            score += 25
        if na and tp and na == tp:
            score += 30
        elif na and tp and (na in tp or tp in na):
            score += 15
        if nf and tn and nf == tn:
            score += 40
        elif stem and tstem and stem == tstem:
            score += 35
        elif stem and tstem and (stem in tstem or tstem in stem):
            score += 15
        if duration and td and abs(duration - td) <= 2:
            score += 10
        elif duration and td and abs(duration - td) <= 5:
            score += 5

        if score >= 80:
            exact.append((score, t))
        elif score >= 55:
            high.append((score, t))
        elif score >= 35:
            soft.append((score, t))

    exact.sort(key=lambda x: -x[0])
    high.sort(key=lambda x: -x[0])

    if len(exact) == 1:
        return exact[0][1], "exact"
    if len(exact) > 1:
        # Multiple exact-ish: ambiguous unless one clearly higher
        if exact[0][0] - exact[1][0] >= 15:
            return exact[0][1], "high"
        return None, "ambiguous"
    if len(high) == 1:
        return high[0][1], "high"
    if len(high) > 1:
        if high[0][0] - high[1][0] >= 20:
            return high[0][1], "high"
        return None, "ambiguous"
    return None, "none"


def save_queue_state(data_dir: Path, owner_id: int, payload: dict) -> None:
    with _conn(data_dir) as con:
        con.execute(
            """
            INSERT INTO queue_state (owner_id, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(owner_id) DO UPDATE SET
                payload=excluded.payload, updated_at=excluded.updated_at
            """,
            (owner_id, json.dumps(payload, ensure_ascii=False), time.time()),
        )


def load_queue_state(data_dir: Path, owner_id: int) -> Optional[dict]:
    with _conn(data_dir) as con:
        row = con.execute(
            "SELECT payload FROM queue_state WHERE owner_id=?", (owner_id,)
        ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["payload"])
        except Exception:
            return None


def clear_queue_state(data_dir: Path, owner_id: int) -> None:
    with _conn(data_dir) as con:
        con.execute("DELETE FROM queue_state WHERE owner_id=?", (owner_id,))
