"""Crash-tolerant per-user session persistence primitives."""
from __future__ import annotations
import json, os, shutil, time
from pathlib import Path
from typing import Any

class SessionStore:
    def __init__(self, root: Path | str):
        self.root = Path(root) / "sessions"
        self.root.mkdir(parents=True, exist_ok=True)
    def _path(self, uid: int) -> Path: return self.root / f"{int(uid)}.json"
    def load(self, uid: int) -> dict[str, Any]:
        p=self._path(uid)
        for candidate in (p, p.with_suffix('.json.bak')):
            try:
                data=json.loads(candidate.read_text(encoding='utf-8'))
                if isinstance(data,dict) and data.get('schema') == 1: return data
            except (OSError, ValueError, TypeError): pass
        return {"schema":1,"focus":None,"sessions":{}}
    def save(self, uid: int, data: dict[str, Any]) -> None:
        p=self._path(uid); p.parent.mkdir(parents=True,exist_ok=True)
        data=dict(data); data['schema']=1; data['updated']=time.time()
        tmp=p.with_suffix('.json.tmp')
        with tmp.open('w',encoding='utf-8') as f:
            json.dump(data,f,ensure_ascii=False,separators=(',',':')); f.flush(); os.fsync(f.fileno())
        if p.exists(): shutil.copy2(p,p.with_suffix('.json.bak'))
        os.replace(tmp,p)
    def delete(self, uid: int) -> None:
        p=self._path(uid)
        for x in (p,p.with_suffix('.json.bak'),p.with_suffix('.json.tmp')):
            try: x.unlink()
            except FileNotFoundError: pass
