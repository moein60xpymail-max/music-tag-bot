"""Multi-signal duplicate detection for the archive workflow."""
import re
import unicodedata
from difflib import SequenceMatcher

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_TRANSLATE = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "ـ": None,
                           "\u200c": None, "\u200e": None, "\u200f": None})

def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = value.translate(_DIGITS).translate(_TRANSLATE)
    value = re.sub(r"@[^\s]+|https?://\S+|\b\d+\s?kbps\b", " ", value)
    value = "".join(c for c in unicodedata.normalize("NFKD", value)
                   if not unicodedata.combining(c))
    value = re.sub(r"[^\w\s]", " ", value)
    return " ".join(value.split())

def _title(item: dict) -> str:
    # Prefer embedded Telegram audio title; otherwise use the filename without extension.
    title = item.get("title")
    if not title:
        from pathlib import Path
        title = Path(str(item.get("name") or "")).stem
    return normalize_title(title)

def classify(a: dict, b: dict) -> str:
    """Return definite, manual, or independent. File size is never decisive."""
    if a.get("file_unique_id") and a.get("file_unique_id") == b.get("file_unique_id"):
        return "definite"

    ta, tb = _title(a), _title(b)
    if not ta or not tb:
        return "independent"

    aa, ab = normalize_title(a.get("artist")), normalize_title(b.get("artist"))
    artists_conflict = bool(aa and ab and aa != ab)
    da, db = a.get("duration"), b.get("duration")
    try:
        duration_delta = abs(float(da) - float(db)) if da is not None and db is not None else None
    except (TypeError, ValueError):
        duration_delta = None

    # Exact title plus matching duration is strong evidence, unless artists conflict.
    if ta == tb:
        if duration_delta is not None and duration_delta <= 2 and not artists_conflict:
            return "definite"
        return "manual"

    # Near-identical title is only a review candidate, never an automatic duplicate.
    similarity = SequenceMatcher(None, ta, tb).ratio()
    if similarity >= 0.85 and duration_delta is not None and duration_delta <= 2:
        return "manual"

    # Size can support a manual review, but different encodings/bitrates make it unreliable.
    sa, sb = a.get("size"), b.get("size")
    try:
        sa, sb = int(sa or 0), int(sb or 0)
        if similarity >= 0.84 and duration_delta is not None and duration_delta <= 2 and sa and sb:
            if abs(sa - sb) / max(sa, sb) <= 0.03:
                return "manual"
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return "independent"
