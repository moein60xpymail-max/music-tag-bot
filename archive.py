"""Archive queue data model and progress accounting (no eager media downloads)."""
from dataclasses import dataclass, asdict
from pathlib import Path
import json, os
STATUSES={'pending','in_progress','delivered','confirmed','skipped','unsupported','too_large','failed'}
@dataclass
class QueueItem:
    number:int; file_id:str; file_unique_id:str|None=None; name:str=''; source:str='A'; status:str='pending'; merged_into:int|None=None; defaults_applied:bool=False
class ArchiveJournal:
    def __init__(self,path:Path|str): self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
    def append(self,item:QueueItem):
        row=asdict(item)
        with self.path.open('a',encoding='utf-8') as f: f.write(json.dumps(row,ensure_ascii=False)+'\n'); f.flush(); os.fsync(f.fileno())
    def replay(self)->dict[int,QueueItem]:
        latest={}
        if not self.path.exists(): return latest
        with self.path.open(encoding='utf-8') as f:
            for line in f:
                try:
                    d=json.loads(line); obj=QueueItem(**d); latest[obj.number]=obj
                except (ValueError,TypeError,KeyError): continue
        return latest
class Tracker:
    def __init__(self,items): self.items=[x for x in items if x.merged_into is None]
    def counts(self):
        keys=('confirmed','skipped','unsupported','failed','too_large','remaining')
        c={k:0 for k in keys}
        for x in self.items:
            if x.status in ('confirmed','skipped','unsupported','failed','too_large'): c[x.status]+=1
            else: c['remaining']+=1
        c['total']=len(self.items); c['percent']=round(100*c['confirmed']/c['total']) if c['total'] else 0
        return c
