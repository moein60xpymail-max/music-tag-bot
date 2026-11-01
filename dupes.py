"""Four-signal archive duplicate matching."""
import re, unicodedata
from pathlib import Path

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_TRANS = str.maketrans({"ي":"ی", "ى":"ی", "ك":"ک", "ـ":None, "\u200c":None, "\u200e":None, "\u200f":None})
def norm(s):
    s=unicodedata.normalize("NFKC",str(s or "")).casefold().translate(_DIGITS).translate(_TRANS)
    s="".join(c for c in unicodedata.normalize("NFKD",s) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]"," ",s).split())
def title(x): return norm(x.get("title") or Path(str(x.get("name") or "")).stem)
def artists(x):
    raw=str(x.get("artist") or "")
    parts=re.split(r"[,/&؛]+",raw)
    return frozenset(norm(a) for a in parts if norm(a))
def classify(a,b):
    checks=[]
    checks.append(bool(title(a) and title(a)==title(b)))
    aa,bb=artists(a),artists(b)
    checks.append(bool(aa and bb and aa==bb))
    try: checks.append(a.get("duration") is not None and b.get("duration") is not None and abs(float(a["duration"])-float(b["duration"]))<=2)
    except (TypeError,ValueError): checks.append(False)
    try: checks.append(int(a.get("size") or 0)>0 and int(a.get("size") or 0)==int(b.get("size") or 0))
    except (TypeError,ValueError): checks.append(False)
    n=sum(checks)
    return "definite" if n>=3 else "manual" if n==2 else "independent"
