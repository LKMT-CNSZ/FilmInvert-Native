"""Byte-bounded decoded inputs and small initial-analysis views, never edit state."""
from collections import OrderedDict
import copy,json,threading
import numpy as np

class BlobCache:
    def __init__(self,budget=0):self.budget=budget;self.entries=OrderedDict();self.bytes=0;self.lock=threading.RLock();self.hits=0
    def clear(self):
        with self.lock:self.entries.clear();self.bytes=0
    def get(self,key):
        with self.lock:
            value=self.entries.pop(key,None)
            if value is not None:self.entries[key]=value;self.hits+=1;return value[0]
    def put(self,key,value):
        size=len(value)+len(key.encode('utf-8'))
        with self.lock:
            old=self.entries.pop(key,None)
            if old:self.bytes-=old[1]
            if size>self.budget:return
            while self.entries and self.bytes+size>self.budget:_,old=self.entries.popitem(last=False);self.bytes-=old[1]
            self.entries[key]=(value,size);self.bytes+=size

class DocumentCache:
    def __init__(self,budget=0):
        self.entries=OrderedDict();self.bytes=0;self.budget=int(budget);self.lock=threading.RLock()
        self.hits=0;self.misses=0;self.analysis_hits=0;self.evictions=0
    @staticmethod
    def key(identity,color,space):
        return json.dumps([identity,color,space],sort_keys=True,ensure_ascii=False)
    def statistics(self):
        with self.lock:return {'budget_bytes':self.budget,'retained_bytes':self.bytes,'entries':len(self.entries),'hits':self.hits,
                              'misses':self.misses,'analysis_hits':self.analysis_hits,'evictions':self.evictions}
    def clear(self):
        with self.lock:self.entries.clear();self.bytes=0
    def set_budget(self,budget):
        with self.lock:
            self.budget=max(0,int(budget))
            while self.entries and self.bytes>self.budget:self._evict()
    def contains(self,key):
        with self.lock:return key in self.entries
    def prefetch_fits(self,size,protected_bytes):
        with self.lock:return bool(self.budget and size+protected_bytes<=self.budget)
    def put_prefetched(self,key,loaded,protected,protected_bytes,cancel):
        with self.lock:
            active=max(protected_bytes,self.entries.get(protected,{}).get('bytes',0))
            if cancel.is_set() or not self.prefetch_fits(loaded.linear.nbytes,active):return False
            if key in self.entries:return True
            if protected in self.entries:self.entries.move_to_end(protected)
            self.put(key,loaded)
            return key in self.entries
    def _evict(self):
        _,entry=self.entries.popitem(last=False);self.bytes-=entry['bytes'];self.evictions+=1
    def get(self,key):
        with self.lock:
            entry=self.entries.pop(key,None)
            if entry is None:self.misses+=1;return None
            self.entries[key]=entry;self.hits+=1;return entry['loaded']
    def put(self,key,loaded):
        with self.lock:
            if not self.budget:return
            size=loaded.linear.nbytes
            if size>self.budget:
                # A giant scan already occupies the active document; do not
                # retain unrelated decoded photos alongside it.
                self.clear();return
            old=self.entries.pop(key,None)
            if old:self.bytes-=old['bytes']
            while self.entries and self.bytes+size>self.budget:self._evict()
            self.entries[key]={'loaded':loaded,'bytes':size,'views':OrderedDict()};self.bytes+=size
    def get_analysis(self,key,mode):
        with self.lock:
            entry=self.entries.get(key)
            if not entry or mode not in entry['views']:return None
            value=entry['views'].pop(mode);entry['views'][mode]=value;self.analysis_hits+=1
            # Only the preview array is shared; editable info/params/layout are
            # copied so restoring another project cannot modify this baseline.
            return {**copy.deepcopy({k:v for k,v in value.items() if k not in ('preview','bytes')}),'preview':value['preview']}
    def put_analysis(self,key,mode,state):
        with self.lock:
            entry=self.entries.get(key)
            if not entry:return
            size=0 if np.shares_memory(state['preview'],entry['loaded'].linear) else state['preview'].nbytes
            old=entry['views'].pop(mode,None)
            if old:entry['bytes']-=old['bytes'];self.bytes-=old['bytes']
            if entry['loaded'].linear.nbytes+size>self.budget:return
            while entry['views'] and self.bytes+size>self.budget:
                _,previous=entry['views'].popitem(last=False);entry['bytes']-=previous['bytes'];self.bytes-=previous['bytes']
            while self.bytes+size>self.budget and len(self.entries)>1:
                candidate=next(iter(self.entries))
                if candidate==key:self.entries.move_to_end(key);continue
                self._evict()
            if self.bytes+size>self.budget:return
            value=copy.deepcopy({k:v for k,v in state.items() if k!='preview'})
            value.update(preview=state['preview'],bytes=size);entry['views'][mode]=value;entry['bytes']+=size;self.bytes+=size
