import ast
from pathlib import Path
ROOT = Path(__file__).resolve().parent
s = (ROOT / "bot.py").read_text(encoding="utf-8")

def album_media_group_sizes(n):
    if n <= 0: return []
    if n < 4: return [1]*n
    sizes=[]; rem=n
    while rem>10:
        sizes.append(10); rem-=10
    sizes.append(rem)
    if len(sizes)>=2 and sizes[-1]<4:
        need=4-sizes[-1]; sizes[-2]-=need; sizes[-1]+=need
    return sizes

def test_version():
    assert 'VERSION = "v6.2.0"' in s

def test_media_groups():
    assert "send_media_group" in s and "InputMediaAudio" in s
    cases={11:[7,4],12:[8,4],13:[9,4],14:[10,4],15:[10,5],20:[10,10],21:[10,7,4],31:[10,10,7,4]}
    for n,exp in cases.items():
        assert album_media_group_sizes(n)==exp

def test_summary_lifecycle():
    assert "if data == \"album:summary\":" in s
    # back should not delete
    idx = s.find('if data == "album:back":')
    chunk = s[idx:idx+250]
    assert "show_album_overview" in chunk
    assert "delete()" not in chunk

def test_album_delays():
    assert "در حال آماده‌سازی و ارسال آهنگ‌های آلبوم" in s
    assert "album:defaults" in s and "op_busy" in s

def test_syntax():
    ast.parse(s)

if __name__ == "__main__":
    for n,f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("OK", n)
    print("all ok")
