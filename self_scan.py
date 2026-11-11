"""Archive Self-Scan: find duplicates inside one archive (no auto-delete).

Independent from Source A/B Audio Scanner flow.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from audio_scanner import (
    EXACT_THRESHOLD,
    REVIEW_THRESHOLD,
    similarity_between_analyses,
)


def empty_state() -> dict[str, Any]:
    return {
        "phase": "menu",
        "tracks": [],
        "results": None,
        "analyses": {},
    }


def cache_path(data_dir: Path, user_id: int) -> Path:
    d = Path(data_dir) / "self_scan"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{int(user_id)}.json"


def save_state(data_dir: Path, user_id: int, st: dict[str, Any]) -> None:
    payload = {
        "schema": 1,
        "phase": st.get("phase"),
        "tracks": st.get("tracks") or [],
        "results": st.get("results"),
        "updated": time.time(),
        "analyses": {},
    }
    for k, an in (st.get("analyses") or {}).items():
        if not isinstance(an, dict):
            continue
        payload["analyses"][k] = {
            "content_hash": an.get("content_hash"),
            "method": an.get("method"),
            "duration": an.get("duration"),
            "error": an.get("error"),
            "fingerprint": an.get("fingerprint"),
            "soft_fp": an.get("soft_fp"),
        }
    path = cache_path(data_dir, user_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_state(data_dir: Path, user_id: int) -> dict[str, Any]:
    path = cache_path(data_dir, user_id)
    st = empty_state()
    if not path.exists():
        return st
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != 1:
            return st
        st["tracks"] = list(data.get("tracks") or [])
        st["results"] = data.get("results")
        st["analyses"] = dict(data.get("analyses") or {})
        st["phase"] = data.get("phase") or "menu"
    except Exception as exc:
        print("self_scan load error:", repr(exc))
    return st


def format_duration_ss(sec: Any) -> str:
    try:
        s = int(float(sec or 0))
    except (TypeError, ValueError):
        return "—"
    return f"{s // 60:02d}:{s % 60:02d}"


def track_cache_key(file_unique_id: str | None, file_id: str | None, size: int) -> str:
    if file_unique_id:
        return f"uid:{file_unique_id}"
    if file_id:
        return f"fid:{file_id}:{int(size or 0)}"
    return f"unknown:{time.time_ns()}"


def build_groups(tracks: list[dict], analyses: dict) -> dict:
    """All-pairs fingerprint compare. Returns stats, exact groups, review pairs."""
    n = len(tracks)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    exact_pairs: list[tuple[int, int, float]] = []
    review_pairs: list[tuple[int, int, float]] = []

    for i in range(n):
        key_i = tracks[i].get("cache_key") or ""
        ai = analyses.get(key_i)
        if not ai:
            continue
        for j in range(i + 1, n):
            key_j = tracks[j].get("cache_key") or ""
            aj = analyses.get(key_j)
            if not aj:
                continue
            score = float(similarity_between_analyses(ai, aj))
            if score >= EXACT_THRESHOLD:
                exact_pairs.append((i, j, score))
                union(i, j)
            elif score >= REVIEW_THRESHOLD:
                review_pairs.append((i, j, score))

    buckets: dict[int, list[int]] = {}
    for i in range(n):
        buckets.setdefault(find(i), []).append(i)

    groups: list[dict] = []
    in_exact: set[int] = set()
    for members in buckets.values():
        if len(members) < 2:
            continue
        scores: list[float] = []
        member_set = set(members)
        for i, j, sc in exact_pairs:
            if i in member_set and j in member_set:
                scores.append(sc)
        groups.append({
            "members": members,
            "min_score": round(min(scores), 2) if scores else 100.0,
        })
        in_exact.update(members)

    groups.sort(key=lambda g: (-len(g["members"]), -g["min_score"]))
    analyzed = sum(1 for tr in tracks if analyses.get(tr.get("cache_key") or ""))
    definite_extras = sum(max(0, len(g["members"]) - 1) for g in groups)

    return {
        "stats": {
            "total": n,
            "analyzed": analyzed,
            "errors": max(0, n - analyzed),
            "definite_tracks_in_groups": len(in_exact),
            "definite_extra_copies": definite_extras,
            "group_count": len(groups),
            "review_pair_count": len(review_pairs),
            "unique_no_dup": n - len(in_exact),
        },
        "groups": groups,
        "review_pairs": review_pairs,
        "created_at": time.time(),
    }


def format_report(st: dict) -> str:
    res = st.get("results") or {}
    stats = res.get("stats") or {}
    tracks = st.get("tracks") or []
    return "\n".join([
        "📋 گزارش Archive Self-Scan",
        "",
        f"• تعداد کل آهنگ‌ها: {stats.get('total', len(tracks))}",
        f"• تعداد آهنگ‌های تحلیل‌شده: {stats.get('analyzed', 0)}",
        f"• آهنگ‌های داخل گروه‌های تکراری قطعی: {stats.get('definite_tracks_in_groups', 0)}",
        f"• نسخه‌های اضافی قطعی (جمع len-1 گروه‌ها): {stats.get('definite_extra_copies', 0)}",
        f"• تعداد گروه‌های تکراری: {stats.get('group_count', 0)}",
        f"• جفت‌های نیازمند بررسی: {stats.get('review_pair_count', 0)}",
        f"• بدون تکراری قطعی: {stats.get('unique_no_dup', 0)}",
        f"• خطا / غیرقابل تحلیل: {stats.get('errors', 0)}",
        "",
        "هیچ فایلی به‌صورت خودکار حذف نشده است.",
    ])


def format_groups(st: dict, limit: int = 15) -> str:
    res = st.get("results") or {}
    groups = res.get("groups") or []
    tracks = st.get("tracks") or []
    if not groups:
        return "گروه تکراری قطعی‌ای پیدا نشد."
    lines = [f"🔁 گروه‌های تکراری قطعی ({len(groups)} گروه):\n"]
    for gi, g in enumerate(groups[:limit], 1):
        lines.append(f"Duplicate #{gi}  (شباهت ≥ {g.get('min_score', '?')}%)")
        for mi, idx in enumerate(g.get("members") or [], 1):
            if idx < 0 or idx >= len(tracks):
                continue
            tr = tracks[idx]
            name = tr.get("title") or Path(str(tr.get("name") or "?")).stem
            artist = tr.get("artist") or "—"
            lines.append(
                f"  {mi}) #{tr.get('number', idx + 1)} | {name} | {artist} | "
                f"{format_duration_ss(tr.get('duration'))}"
            )
        lines.append("  نتیجه: محتوای صوتی این نسخه‌ها یکسان/تقریباً یکسان تشخیص داده شد.\n")
    if len(groups) > limit:
        lines.append(f"… و {len(groups) - limit} گروه دیگر در گزارش کامل")
    return "\n".join(lines)[:3900]


def format_review(st: dict, limit: int = 20) -> str:
    res = st.get("results") or {}
    pairs = res.get("review_pairs") or []
    tracks = st.get("tracks") or []
    if not pairs:
        return "مورد نیازمند بررسی‌ای نیست."
    lines = [f"🔎 نیازمند بررسی ({len(pairs)} جفت):\n"]
    for i, triple in enumerate(pairs[:limit], 1):
        a, b, score = triple
        ta = tracks[a] if 0 <= a < len(tracks) else {}
        tb = tracks[b] if 0 <= b < len(tracks) else {}
        na = ta.get("title") or Path(str(ta.get("name") or "?")).stem
        nb = tb.get("title") or Path(str(tb.get("name") or "?")).stem
        lines.append(
            f"{i}) #{ta.get('number', a + 1)} {na}  ↔  #{tb.get('number', b + 1)} {nb}\n"
            f"   شباهت: {score}% — مشکوک (حذف خودکار نشد)"
        )
    if len(pairs) > limit:
        lines.append(f"… و {len(pairs) - limit} جفت دیگر")
    return "\n".join(lines)[:3900]


def export_report_bytes(st: dict) -> bytes:
    res = st.get("results") or {}
    tracks = st.get("tracks") or []
    groups = res.get("groups") or []
    pairs = res.get("review_pairs") or []
    lines = [format_report(st), "", "=== GROUPS ===", ""]
    for gi, g in enumerate(groups, 1):
        lines.append(f"Duplicate #{gi} score>={g.get('min_score')}")
        for idx in g.get("members") or []:
            if 0 <= idx < len(tracks):
                tr = tracks[idx]
                lines.append(
                    f"  #{tr.get('number', idx + 1)} | {tr.get('title') or tr.get('name')} | "
                    f"{tr.get('artist') or '—'} | {tr.get('file_id', '')[:24]}"
                )
        lines.append("")
    lines.append("=== REVIEW ===")
    for a, b, score in pairs:
        ta = tracks[a] if 0 <= a < len(tracks) else {}
        tb = tracks[b] if 0 <= b < len(tracks) else {}
        lines.append(
            f"#{ta.get('number', a + 1)} ↔ #{tb.get('number', b + 1)} | {score}%"
        )
    payload = {
        "format": "self-scan-v1",
        "stats": res.get("stats"),
        "groups": groups,
        "review_pairs": pairs,
        "tracks": [
            {k: tr.get(k) for k in (
                "number", "name", "title", "artist", "duration", "size",
                "file_id", "file_unique_id", "cache_key",
            )}
            for tr in tracks
        ],
    }
    lines += ["", "---DATA---", json.dumps(payload, ensure_ascii=False, separators=(",", ":"))]
    return ("\n".join(lines) + "\n").encode("utf-8")
