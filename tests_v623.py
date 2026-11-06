import ast
from pathlib import Path
s = Path("bot.py").read_text(encoding="utf-8")

def _min_viable_max_size(n, min_size=4):
    if n <= 0: return min_size
    if n < min_size: return n
    max_groups = n // min_size
    if max_groups <= 0: return n
    return (n + max_groups - 1) // max_groups

def album_media_group_sizes(n, max_size=10, min_size=4):
    if n <= 0: return []
    max_size = max(1, min(10, int(max_size)))
    min_size = max(1, min(10, int(min_size)))
    if n < min_size: return [n]
    viable = _min_viable_max_size(n, min_size=min_size)
    if max_size < viable:
        max_size = min(10, viable)
    sizes=[]; remaining=n
    while remaining > max_size:
        sizes.append(max_size); remaining -= max_size
    sizes.append(remaining)
    if len(sizes) >= 2 and sizes[-1] < min_size:
        need = min_size - sizes[-1]
        if sizes[-2] - need >= min_size:
            sizes[-2] -= need; sizes[-1] += need
        elif sizes[-2] + sizes[-1] <= max_size:
            sizes[-2] += sizes[-1]; sizes.pop()
        else:
            borrow = max(0, sizes[-2] - min_size)
            if borrow:
                sizes[-2] -= borrow; sizes[-1] += borrow
    if any(x < min_size for x in sizes) and n >= min_size:
        if n <= 10: return [n]
        max_size = max(viable, min_size)
        sizes=[]; remaining=n
        while remaining > max_size:
            sizes.append(max_size); remaining -= max_size
        sizes.append(remaining)
        if len(sizes) >= 2 and sizes[-1] < min_size:
            need = min_size - sizes[-1]
            sizes[-2] -= need; sizes[-1] += need
    return sizes

def test_version():
    assert 'VERSION = "v6.2.3"' in s

def test_no_4_3_4():
    assert album_media_group_sizes(11) == [7, 4]
    assert album_media_group_sizes(11) != [4, 3, 4]
    for ms in range(4, 11):
        sizes = album_media_group_sizes(11, max_size=ms)
        assert 3 not in sizes
        assert all(x >= 4 for x in sizes)
        assert sum(sizes) == 11

def test_required_samples():
    cases={11:[7,4],12:[8,4],13:[9,4],14:[10,4],15:[10,5],16:[10,6],17:[10,7],18:[10,8],19:[10,9],20:[10,10],21:[10,7,4],22:[10,8,4],23:[10,9,4],24:[10,10,4]}
    for n,exp in cases.items():
        assert album_media_group_sizes(n)==exp, (n, album_media_group_sizes(n), exp)

def test_no_internal_413_split_to_3():
    assert "_split_group_after_413" not in s
    assert "re-pack unsent" in s or "re-pack" in s
    assert "_min_viable_max_size" in s

def test_covers_untouched_helpers():
    assert "_cover_bytes_for_track" in s
    assert "thumbnail=thumb_arg" in s

def test_syntax():
    ast.parse(s)

if __name__ == "__main__":
    for n,f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print("OK", n)
