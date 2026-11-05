import ast, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent

def src():
    return (ROOT / "bot.py").read_text(encoding="utf-8")

def test_version():
    assert 'VERSION = "v6.1.0"' in src()

def test_feature_present():
    s = src()
    for x in ("tag:export", "tag:import", "tag:import_confirm", "tag:import_cancel",
              "Export Default Tags", "Import Default Tags", "defaults_import_file",
              "build_defaults_export_payload", "parse_defaults_import_payload",
              "apply_defaults_import", "default_tags.json", "music-tag-bot-defaults-v1"):
        assert x in s, x
    assert "فقط مالک ربات می‌تواند Export" in s
    assert "فقط مالک ربات می‌تواند Import" in s

def test_syntax():
    ast.parse(src())

if __name__ == "__main__":
    failed = 0
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            try:
                f(); print("OK", n)
            except Exception as e:
                failed += 1; print("FAIL", n, e)
    sys.exit(1 if failed else 0)
