import tempfile
from pathlib import Path
from sessions import SessionStore
from dupes import normalize_title, classify
from archive import QueueItem, ArchiveJournal, Tracker

def test_all():
    with tempfile.TemporaryDirectory() as d:
        s=SessionStore(d); data={"focus":"x","sessions":{"x":{"type":"single"}}}; s.save(7,data)
        assert s.load(7)["focus"] == "x"
        j=ArchiveJournal(Path(d)/"items.jsonl"); j.append(QueueItem(1,"f",status="confirmed"))
        assert Tracker(j.replay().values()).counts()["confirmed"] == 1
    assert normalize_title("كیف ۱۲۳") == normalize_title("کیف 123")
    assert classify({"title":"Intro","duration":100},{"title":"Intro","duration":101}) == "definite"
    assert classify({"title":"Intro","duration":100,"artist":"A"},{"title":"Intro","duration":100,"artist":"B"}) == "manual"
    assert classify({"title":"Track","duration":200,"size":1000},{"title":"Track","duration":240,"size":1000}) == "manual"
    assert classify({"title":"Song One","duration":180},{"title":"Song 0ne","duration":180}) == "manual"
    assert classify({"title":"Song A","duration":180},{"title":"Song B","duration":180}) == "independent"
if __name__ == "__main__":
    test_all(); print("smoke tests passed")
