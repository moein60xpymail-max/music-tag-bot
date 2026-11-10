"""Persistent, low-memory audio similarity scanner for Telegram archive sources.

Audio analysis is delegated to Chromaprint's fpcalc executable.  The database
stores Telegram references and compact fingerprints, never whole audio files.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from contextlib import contextmanager
from typing import Any

FP_VERSION = 1
DEFAULT_THRESHOLD = 95.0
REVIEW_THRESHOLD = 95.0
EXACT_THRESHOLD = 99.0


def content_hash_file(path: str | Path) -> str:
    import hashlib
    path = Path(path)
    h = hashlib.sha256()
    with path.open("rb") as f:
        header = f.read(10)
        if len(header) >= 10 and header[0:3] == b"ID3":
            size = (
                ((header[6] & 0x7F) << 21)
                | ((header[7] & 0x7F) << 14)
                | ((header[8] & 0x7F) << 7)
                | (header[9] & 0x7F)
            )
            f.seek(10 + size)
        else:
            f.seek(0)
        while True:
            chunk = f.read(256 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def soft_window_fingerprint(path: str | Path, windows: int = 32) -> list[str]:
    import hashlib
    path = Path(path)
    with path.open("rb") as f:
        header = f.read(10)
        if len(header) >= 10 and header[0:3] == b"ID3":
            size = (
                ((header[6] & 0x7F) << 21)
                | ((header[7] & 0x7F) << 14)
                | ((header[8] & 0x7F) << 7)
                | (header[9] & 0x7F)
            )
            f.seek(10 + size)
        else:
            f.seek(0)
        data = f.read()
    if not data:
        return []
    n = len(data)
    if n < windows:
        return [hashlib.md5(data).hexdigest()[:12]]
    step = max(n // windows, 1)
    win = max(min(n // windows, 4096), 256)
    return [
        hashlib.md5(data[min(i * step, max(n - win, 0)): min(i * step, max(n - win, 0)) + win]).hexdigest()[:12]
        for i in range(windows)
    ]


def soft_similarity(fa: list[str], fb: list[str]) -> float:
    if not fa or not fb:
        return 0.0
    n = min(len(fa), len(fb))
    ordered = sum(1 for i in range(n) if fa[i] == fb[i]) / n
    sa, sb = set(fa), set(fb)
    jacc = len(sa & sb) / max(len(sa | sb), 1)
    return round(max(0.0, min(99.9, (0.65 * ordered + 0.35 * jacc) * 100.0)), 2)



class AudioScannerStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self):
        con = sqlite3.connect(str(self.path), timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _init_db(self):
        with self._connect() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                file_id TEXT NOT NULL,
                file_unique_id TEXT,
                name TEXT NOT NULL,
                size INTEGER NOT NULL DEFAULT 0,
                duration REAL,
                fingerprint TEXT,
                fp_duration REAL,
                fp_version INTEGER,
                status TEXT NOT NULL DEFAULT 'active',
                last_error TEXT,
                updated_at REAL NOT NULL,
                UNIQUE(user_id, chat_id, message_id)
            );
            CREATE INDEX IF NOT EXISTS idx_tracks_user_status ON tracks(user_id,status);
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                created_at REAL NOT NULL,
                total INTEGER NOT NULL DEFAULT 0,
                processed INTEGER NOT NULL DEFAULT 0,
                group_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS groups_found (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL,
                group_no INTEGER NOT NULL,
                score REAL NOT NULL,
                data_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            );
            CREATE TABLE IF NOT EXISTS quarantine (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                track_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'quarantined'
            );
            CREATE TABLE IF NOT EXISTS settings (
                user_id INTEGER PRIMARY KEY,
                threshold REAL NOT NULL DEFAULT 95.0
            );
            """)

    def register_track(self, user_id: int, source: str, chat_id: int, message_id: int,
                       file_id: str, file_unique_id: str | None, name: str,
                       size: int = 0, duration: float | None = None) -> int:
        now = time.time()
        with self._connect() as con:
            old = con.execute(
                "SELECT id,file_unique_id,size FROM tracks WHERE user_id=? AND chat_id=? AND message_id=?",
                (user_id, chat_id, message_id),
            ).fetchone()
            if old:
                changed = old["file_unique_id"] != file_unique_id or int(old["size"] or 0) != int(size or 0)
                con.execute("""UPDATE tracks SET source=?,file_id=?,file_unique_id=?,name=?,size=?,duration=?,
                    fingerprint=CASE WHEN ? THEN NULL ELSE fingerprint END,
                    fp_duration=CASE WHEN ? THEN NULL ELSE fp_duration END,
                    fp_version=CASE WHEN ? THEN NULL ELSE fp_version END,
                    status=CASE WHEN status='quarantined' THEN status ELSE 'active' END,
                    last_error=NULL,updated_at=? WHERE id=?""",
                    (source, file_id, file_unique_id, name, size or 0, duration,
                     int(changed), int(changed), int(changed), now, old["id"]))
                return int(old["id"])
            cur = con.execute("""INSERT INTO tracks
                (user_id,source,chat_id,message_id,file_id,file_unique_id,name,size,duration,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (user_id, source, chat_id, message_id, file_id, file_unique_id, name, size or 0, duration, now))
            return int(cur.lastrowid)

    def tracks(self, user_id: int):
        with self._connect() as con:
            return [dict(r) for r in con.execute(
                "SELECT * FROM tracks WHERE user_id=? AND status='active' ORDER BY source,message_id,id", (user_id,)
            ).fetchall()]

    def save_fingerprint(self, track_id: int, fingerprint: list[int], duration: float):
        with self._connect() as con:
            con.execute("UPDATE tracks SET fingerprint=?,fp_duration=?,fp_version=?,last_error=NULL,updated_at=? WHERE id=?",
                        (json.dumps(fingerprint, separators=(',', ':')), duration, FP_VERSION, time.time(), track_id))

    def save_error(self, track_id: int, error: str):
        with self._connect() as con:
            con.execute("UPDATE tracks SET last_error=?,updated_at=? WHERE id=?", (str(error)[:500], time.time(), track_id))

    def failed_tracks(self, user_id: int):
        with self._connect() as con:
            return [dict(r) for r in con.execute(
                "SELECT id,name,last_error FROM tracks WHERE user_id=? AND status='active' AND last_error IS NOT NULL ORDER BY id",
                (user_id,),
            ).fetchall()]

    def threshold(self, user_id: int) -> float:
        with self._connect() as con:
            row = con.execute("SELECT threshold FROM settings WHERE user_id=?", (user_id,)).fetchone()
            return float(row[0]) if row else DEFAULT_THRESHOLD

    def set_threshold(self, user_id: int, value: float):
        value = max(90.0, min(99.9, float(value)))
        with self._connect() as con:
            con.execute("INSERT INTO settings(user_id,threshold) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET threshold=excluded.threshold", (user_id, value))

    def create_scan(self, user_id: int, total: int, processed: int, groups: list[dict], errors: int) -> int:
        with self._connect() as con:
            cur = con.execute("INSERT INTO scans(user_id,created_at,total,processed,group_count,error_count) VALUES(?,?,?,?,?,?)",
                              (user_id, time.time(), total, processed, len(groups), errors))
            scan_id = int(cur.lastrowid)
            for no, group in enumerate(groups, 1):
                con.execute("INSERT INTO groups_found(scan_id,user_id,group_no,score,data_json) VALUES(?,?,?,?,?)",
                            (scan_id, user_id, no, float(group['score']), json.dumps(group, ensure_ascii=False)))
            return scan_id

    def latest_scan(self, user_id: int):
        with self._connect() as con:
            row = con.execute("SELECT * FROM scans WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
            return dict(row) if row else None

    def groups(self, user_id: int, pending_only: bool = False):
        with self._connect() as con:
            scan = con.execute("SELECT id FROM scans WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
            if not scan:
                return []
            sql = "SELECT * FROM groups_found WHERE scan_id=?"
            args: list[Any] = [scan["id"]]
            if pending_only:
                sql += " AND status='pending'"
            sql += " ORDER BY group_no"
            rows = con.execute(sql, args).fetchall()
            return [{**dict(r), "data": json.loads(r["data_json"])} for r in rows]

    def choose_keep(self, user_id: int, group_id: int, keep_track_id: int):
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM groups_found WHERE id=? AND user_id=?", (group_id, user_id)).fetchone()
            if not row:
                raise ValueError("گروه پیدا نشد.")
            data = json.loads(row["data_json"])
            ids = [int(x) for x in data.get("track_ids", [])]
            if keep_track_id not in ids:
                raise ValueError("این آهنگ عضو گروه انتخاب‌شده نیست.")
            if row["status"] != "pending":
                raise ValueError("برای این گروه قبلاً تصمیم ثبت شده است.")
            for tid in ids:
                if tid == keep_track_id:
                    continue
                track = con.execute("SELECT name,status FROM tracks WHERE id=? AND user_id=?", (tid, user_id)).fetchone()
                if not track or track["status"] != "active":
                    continue
                con.execute("UPDATE tracks SET status='quarantined',updated_at=? WHERE id=?", (time.time(), tid))
                con.execute("INSERT INTO quarantine(user_id,group_id,track_id,name,reason,created_at) VALUES(?,?,?,?,?,?)",
                            (user_id, group_id, tid, track["name"], f"مشابه با نسخه انتخاب‌شده؛ نگهداری ID={keep_track_id}", time.time()))
            con.execute("UPDATE groups_found SET status='resolved' WHERE id=?", (group_id,))
            con.commit()
            return len(ids) - 1

    def quarantine_items(self, user_id: int):
        with self._connect() as con:
            return [dict(r) for r in con.execute("SELECT * FROM quarantine WHERE user_id=? AND status='quarantined' ORDER BY id", (user_id,)).fetchall()]

    def restore(self, user_id: int, quarantine_id: int):
        with self._connect() as con:
            row = con.execute("SELECT track_id FROM quarantine WHERE id=? AND user_id=? AND status='quarantined'", (quarantine_id, user_id)).fetchone()
            if not row:
                return False
            con.execute("UPDATE tracks SET status='active',updated_at=? WHERE id=? AND user_id=?", (time.time(), row["track_id"], user_id))
            con.execute("UPDATE quarantine SET status='restored' WHERE id=?", (quarantine_id,))
            return True

    def remove_from_index(self, user_id: int, quarantine_id: int):
        """Forget the scanner reference only; never deletes Telegram's source message/file."""
        with self._connect() as con:
            row = con.execute("SELECT track_id FROM quarantine WHERE id=? AND user_id=? AND status='quarantined'", (quarantine_id, user_id)).fetchone()
            if not row:
                return False
            con.execute("DELETE FROM tracks WHERE id=? AND user_id=?", (row["track_id"], user_id))
            con.execute("UPDATE quarantine SET status='removed_from_index' WHERE id=?", (quarantine_id,))
            return True


def fpcalc_available() -> bool:
    return shutil.which("fpcalc") is not None


def fingerprint_file(path: str | Path, max_seconds: int = 0) -> tuple[list[int], float]:
    """Return full-track Chromaprint raw subfingerprints and decoded duration."""
    exe = shutil.which("fpcalc")
    if not exe:
        raise RuntimeError("ابزار fpcalc (Chromaprint) روی سرور نصب نیست.")
    proc = subprocess.run([exe, "-raw", "-length", str(max_seconds), str(path)],
                          capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "خطای fpcalc").strip()
        raise RuntimeError(detail[:300])
    duration = None
    raw = None
    for line in proc.stdout.splitlines():
        if line.startswith("DURATION="):
            try: duration = float(line.split("=", 1)[1])
            except ValueError: pass
        elif line.startswith("FINGERPRINT="):
            raw = line.split("=", 1)[1].strip()
    if not raw:
        raise RuntimeError("اثر انگشت صوتی از فایل استخراج نشد؛ فرمت ممکن است پشتیبانی نشود.")
    try:
        values = [int(x) & 0xFFFFFFFF for x in raw.split(",") if x]
    except ValueError as exc:
        raise RuntimeError("خروجی fpcalc نامعتبر است.") from exc
    if len(values) < 30:
        raise RuntimeError("مدت یا داده صوتی برای مقایسه کافی نیست.")
    return values, float(duration or 0.0)


def fingerprint_similarity(a: list[int], b: list[int], duration_a: float | None = None,
                            duration_b: float | None = None) -> float | None:
    """Hamming similarity of aligned Chromaprint frames (not metadata similarity)."""
    if not a or not b:
        return None
    shorter, longer = min(len(a), len(b)), max(len(a), len(b))
    if shorter < 30 or shorter / longer < 0.80:
        return None
    if duration_a and duration_b:
        ratio = min(duration_a, duration_b) / max(duration_a, duration_b)
        if ratio < 0.80:
            return None
    # Compare the shared beginning; require at least 80% of the shorter fingerprint.
    n = shorter
    if n > 2500:
        # Bound CPU per pair on very long recordings while sampling the full shared span.
        step = max(1, n // 2500)
        aa, bb = a[:n:step], b[:n:step]
    else:
        aa, bb = a[:n], b[:n]
    total = 0
    bits = 0
    for x, y in zip(aa, bb):
        total += (x ^ y).bit_count()
        bits += 32
    if not bits:
        return None
    score = 100.0 * (1.0 - total / bits)
    # A 100% prefix with materially different durations is not an exact full-file match.
    if score >= 99.999 and duration_a and duration_b:
        ratio = min(duration_a, duration_b) / max(duration_a, duration_b)
        if ratio < 0.98:
            score = 99.9
    return round(score, 2)


def build_similarity_groups(track_rows: list[dict], threshold: float = DEFAULT_THRESHOLD) -> list[dict]:
    """Build connected components from high-confidence audio fingerprint matches."""
    prepared = []
    for row in track_rows:
        try:
            fp = json.loads(row.get("fingerprint") or "[]")
        except (TypeError, ValueError):
            fp = []
        if fp:
            prepared.append((row, fp))
    parent = {int(row["id"]): int(row["id"]) for row, _ in prepared}
    scores: dict[tuple[int, int], float] = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    for i in range(len(prepared)):
        row_a, fp_a = prepared[i]
        for j in range(i + 1, len(prepared)):
            row_b, fp_b = prepared[j]
            score = fingerprint_similarity(fp_a, fp_b, row_a.get("fp_duration"), row_b.get("fp_duration"))
            if score is not None and score >= threshold:
                ida, idb = int(row_a["id"]), int(row_b["id"])
                scores[(ida, idb)] = score
                union(ida, idb)

    components: dict[int, list[int]] = {}
    for row, _ in prepared:
        tid = int(row["id"])
        root = find(tid)
        components.setdefault(root, []).append(tid)

    row_by_id = {int(row["id"]): row for row, _ in prepared}
    result = []
    for members in components.values():
        if len(members) < 2:
            continue
        pair_scores = {}
        edge_scores = []
        for i, left in enumerate(members):
            for right in members[i + 1:]:
                key = (min(left, right), max(left, right))
                if key in scores:
                    pair_scores[f"{key[0]}-{key[1]}"] = scores[key]
                    edge_scores.append(scores[key])
        result.append({
            "track_ids": members,
            "tracks": [{"id": tid, "name": row_by_id[tid].get("name") or "بدون نام",
                        "source": row_by_id[tid].get("source"), "size": row_by_id[tid].get("size") or 0,
                        "duration": row_by_id[tid].get("fp_duration") or row_by_id[tid].get("duration")}
                       for tid in members],
            "pair_scores": pair_scores,
            "score": round(min(edge_scores), 2) if edge_scores else 0.0,
        })
    result.sort(key=lambda g: (-len(g["track_ids"]), -g["score"]))
    return result


def analyze_local_file(path: str | Path) -> dict:
    path = Path(path)
    result = {
        "content_hash": None,
        "fingerprint": None,
        "soft_fp": None,
        "duration": None,
        "method": None,
        "error": None,
    }
    try:
        result["content_hash"] = content_hash_file(path)
    except Exception as exc:
        result["error"] = f"hash:{exc!r}"
        return result
    if fpcalc_available():
        try:
            fp, dur = fingerprint_file(path)
            result["fingerprint"] = fp
            result["duration"] = dur
            result["method"] = "chromaprint"
            return result
        except Exception as exc:
            result["error"] = f"fpcalc:{exc!r}"
    try:
        result["soft_fp"] = soft_window_fingerprint(path)
        result["method"] = "soft"
        try:
            from mutagen.mp3 import MP3
            audio = MP3(str(path))
            if audio.info:
                result["duration"] = float(getattr(audio.info, "length", 0) or 0)
        except Exception:
            pass
    except Exception as exc:
        result["error"] = (result.get("error") or "") + f"|soft:{exc!r}"
    return result


def similarity_between_analyses(a: dict, b: dict) -> float:
    if a.get("content_hash") and a["content_hash"] == b.get("content_hash"):
        return 100.0
    scores = []
    if a.get("fingerprint") and b.get("fingerprint"):
        s = fingerprint_similarity(
            a["fingerprint"], b["fingerprint"],
            a.get("duration"), b.get("duration"),
        )
        if s is not None:
            scores.append(float(s))
    if a.get("soft_fp") and b.get("soft_fp"):
        scores.append(soft_similarity(a["soft_fp"], b["soft_fp"]))
    if not scores:
        return 0.0
    score = max(scores)
    try:
        da, db = a.get("duration"), b.get("duration")
        if da is not None and db is not None and abs(float(da) - float(db)) > 15:
            score = min(score, 90.0)
    except (TypeError, ValueError):
        pass
    if score >= 100.0:
        score = 99.9
    return round(score, 2)
