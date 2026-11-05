"""Checks for Music Tag Bot v5.2.0."""
import ast, os, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

def bot_src():
    with open(os.path.join(ROOT, "bot.py"), encoding="utf-8") as f:
        return f.read()

def test_version():
    assert 'VERSION = "v5.2.0"' in bot_src()

def test_dupes_detail():
    from dupes import classify, classify_detail, matched_criteria
    a = {"title": "Hello", "artist": "X", "duration": 120, "size": 1000, "name": "a.mp3"}
    b = {"title": "Hello", "artist": "Y", "duration": 120, "size": 2000, "name": "b.mp3"}
    level, labels = classify_detail(a, b)
    assert level == "manual"
    assert len(labels) == 2
    assert "نام آهنگ (Title)" in labels
    assert "مدت‌زمان (Duration)" in labels
    c = {"title": "Hello", "artist": "X", "duration": 120, "size": 1000, "name": "c.mp3"}
    assert classify(a, c) == "definite"
    d = {"title": "Other", "artist": "Z", "duration": 50, "size": 9, "name": "d.mp3"}
    assert classify(a, d) == "independent"

def test_multi_manual_matches_stored():
    s = bot_src()
    assert "review_matches" in s
    assert "classify_detail" in s
    assert "format_review_warning" in s
    assert "معیارهای تطبیق" in s

def test_summary_lists_missing_tags():
    s = bot_src()
    assert "missing_artist_tracks" in s
    assert "missing_album_tracks" in s
    assert "track_display_name" in s

def test_syntax():
    ast.parse(bot_src())
    import dupes
    ast.parse(open(os.path.join(ROOT, "dupes.py"), encoding="utf-8").read())

if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("OK ", name)
            except Exception as e:
                failed += 1; print("FAIL", name, e)
    if failed:
        sys.exit(1)
    print("\nv5.2.0 checks passed")
