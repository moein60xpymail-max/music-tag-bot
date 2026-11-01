"""Conservative music-title normalization and duplicate classification."""
import re, unicodedata
_DIGITS=str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩','01234567890123456789')
_TRANSLATE=str.maketrans({'ي':'ی','ى':'ی','ك':'ک','ـ':None,'\u200c':None,'\u200e':None,'\u200f':None})
def normalize_title(value: str) -> str:
    s=unicodedata.normalize('NFKC',str(value or '')).casefold().translate(_DIGITS).translate(_TRANSLATE)
    s=re.sub(r'@[\w.]+|https?://\S+|\b\d+\s?kbps\b',' ',s)
    s=''.join(c for c in unicodedata.normalize('NFKD',s) if not unicodedata.combining(c))
    s=re.sub(r'[^\w\s]',' ',s); return ' '.join(s.split())
def classify(a: dict,b: dict) -> str:
    if a.get('file_unique_id') and a.get('file_unique_id')==b.get('file_unique_id'): return 'definite'
    ta,tb=normalize_title(a.get('title') or a.get('name')),normalize_title(b.get('title') or b.get('name'))
    if not ta or ta!=tb: return 'independent'
    da,db=a.get('duration'),b.get('duration')
    artists_differ=bool(a.get('artist') and b.get('artist') and normalize_title(a['artist'])!=normalize_title(b['artist']))
    if da is not None and db is not None and abs(int(da)-int(db))<=2 and not artists_differ: return 'definite'
    return 'manual'
