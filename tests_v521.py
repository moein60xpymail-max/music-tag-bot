"""v5.2.1: exact Needs Review criteria display."""
import ast, os, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

def test_version():
    s = open(os.path.join(ROOT, "bot.py"), encoding="utf-8").read()
    assert 'VERSION = "v5.2.1"' in s
    assert "دلیل تشابه" in s
    assert "classify_detail(other, item)" in s

def test_criteria_pairs():
    from dupes import classify_detail
    cases = [
        ({"title":"T","artist":"A1","duration":100,"size":5000},
         {"title":"T","artist":"B2","duration":200,"size":5000},
         "manual", {"نام آهنگ (Title)", "حجم فایل (File Size)"}),
        ({"title":"X","artist":"Same","duration":120,"size":1},
         {"title":"Y","artist":"Same","duration":121,"size":2},
         "manual", {"هنرمند (Artist)", "مدت‌زمان (Duration)"}),
        ({"title":"Hit","artist":"Band","duration":10,"size":10},
         {"title":"Hit","artist":"Band","duration":99,"size":99},
         "manual", {"نام آهنگ (Title)", "هنرمند (Artist)"}),
        ({"title":"Hit","artist":"Band","duration":120,"size":10},
         {"title":"Hit","artist":"Band","duration":121,"size":99},
         "definite", {"نام آهنگ (Title)", "هنرمند (Artist)", "مدت‌زمان (Duration)"}),
    ]
    for a, b, level, labels in cases:
        got_level, got = classify_detail(a, b)
        assert got_level == level, (got_level, got)
        assert set(got) == labels, (got, labels)

def test_syntax():
    ast.parse(open(os.path.join(ROOT, "bot.py"), encoding="utf-8").read())

if __name__ == "__main__":
    failed = 0
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            try:
                f(); print("OK", n)
            except Exception as e:
                failed += 1; print("FAIL", n, e)
    if failed:
        sys.exit(1)
    print("\nv5.2.1 checks passed")
