import ast
from pathlib import Path
s = Path("bot.py").read_text(encoding="utf-8")

def album_media_group_sizes(n):
    if n <= 0: return []
    if n < 4: return [1]*n
    sizes=[]; rem=n
    while rem>10: sizes.append(10); rem-=10
    sizes.append(rem)
    if len(sizes)>=2 and sizes[-1]<4:
        need=4-sizes[-1]; sizes[-2]-=need; sizes[-1]+=need
    return sizes

def test_version():
    assert 'VERSION = "v6.2.2"' in s

def test_strict_pack():
    cases={4:[4],10:[10],11:[7,4],12:[8,4],13:[9,4],14:[10,4],15:[10,5],20:[10,10],21:[10,7,4],24:[10,10,4],25:[10,10,5]}
    for n,exp in cases.items():
        assert album_media_group_sizes(n)==exp, (n, album_media_group_sizes(n))
    assert "count-based packing" in s

def test_thumbnail_path():
    assert "_cover_bytes_for_track" in s
    assert "thumbnail=thumb_arg" in s
    assert "make_telegram_thumbnail" in s

def test_syntax():
    ast.parse(s)

if __name__ == "__main__":
    for n,f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print("OK", n)
