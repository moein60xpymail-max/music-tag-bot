"""Four-signal archive duplicate matching."""
import re, unicodedata
from pathlib import Path

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_TRANS = str.maketrans({"ي":"ی", "ى":"ی", "ك":"ک", "ـ":None, "\u200c":None, "\u200e":None, "\u200f":None})

# Stable labels for review reporting (order matches check order)
CRITERION_LABELS = (
    "نام آهنگ (Title)",
    "هنرمند (Artist)",
    "مدت‌زمان (Duration)",
    "حجم فایل (File Size)",
)

def norm(s):
    s=unicodedata.normalize("NFKC",str(s or "")).casefold().translate(_DIGITS).translate(_TRANS)
    s="".join(c for c in unicodedata.normalize("NFKD",s) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]"," ",s).split())
def title(x): return norm(x.get("title") or Path(str(x.get("name") or "")).stem)
def artists(x):
    raw=str(x.get("artist") or "")
    parts=re.split(r"[,/&؛]+",raw)
    return frozenset(norm(a) for a in parts if norm(a))

def match_checks(a, b):
    """Return list of 4 booleans: title, artist, duration, size."""
    checks=[]
    checks.append(bool(title(a) and title(a)==title(b)))
    aa,bb=artists(a),artists(b)
    checks.append(bool(aa and bb and aa==bb))
    try: checks.append(a.get("duration") is not None and b.get("duration") is not None and abs(float(a["duration"])-float(b["duration"]))<=2)
    except (TypeError,ValueError): checks.append(False)
    try: checks.append(int(a.get("size") or 0)>0 and int(a.get("size") or 0)==int(b.get("size") or 0))
    except (TypeError,ValueError): checks.append(False)
    return checks

def matched_criteria(a, b):
    """Labels of criteria that matched between a and b."""
    checks = match_checks(a, b)
    return [CRITERION_LABELS[i] for i, ok in enumerate(checks) if ok]

def classify_detail(a, b):
    """Return (level, matched_labels) where level is definite|manual|independent."""
    checks = match_checks(a, b)
    n = sum(checks)
    labels = [CRITERION_LABELS[i] for i, ok in enumerate(checks) if ok]
    if n >= 3:
        return "definite", labels
    if n == 2:
        return "manual", labels
    return "independent", labels

def classify(a,b):
    level, _ = classify_detail(a, b)
    return level
