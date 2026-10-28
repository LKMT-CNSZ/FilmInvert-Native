"""Exact global repair/dust decisions and stage-aware remote clone reads for ROI."""
from collections import deque,OrderedDict
import copy
import numpy as np
import core,local_edits


class Context:
    def __init__(self,source,p,clean,light,levels,cancel=None):
        self.source=source;self.p=p;self.clean=copy.deepcopy(clean);self.light=light;self.cancel=cancel
        self.h,self.w=source.shape[:2];self.repair=None;self.selected=None
        self.cache=OrderedDict();self.bytes=0;self.calls=0;self.work=0;self.active_bytes=0
        self.clean.local_edits=[];self.clean.dust_strength=0
        self.edits=local_edits.normalize(p.local_edits)
        if self.h*self.w>64_000_000 and (p.dust_strength>0 or (p.channel_repair and p.mode=='color_negative') or p.black is None or p.white is None):raise ValueError('全图上下文统计超过 6400 万像素预算')
        if p.channel_repair and p.mode=='color_negative':self.prepare_repair()
        levels(source,p,self.clean,self.repair,self.check)
        if p.dust_strength>0:self.prepare_dust()
    def check(self):
        if self.cancel is not None and self.cancel.is_set():raise InterruptedError('区域预览已取消')
    def prepare_repair(self):
        count=np.zeros(3,np.int64);total=0
        for y in range(0,self.h,128):
            self.check();x=self.light(self.source,self.p,(0,y,self.w,min(self.h,y+128)))
            x=np.nan_to_num(x,nan=0.,posinf=1.,neginf=0.)[::2,::2]
            count+=(x<=.005).sum(axis=(0,1));total+=x.shape[0]*x.shape[1]
        near=count/total;bad=[i for i in range(3) if near[i]>.5 and near[i]>3*max(max(near[j] for j in range(3) if j!=i),1e-6)]
        self.repair={'health':{'suggest':bool(bad) and len(bad)<3,'bad':bad},'span':self.p.span}
        if not self.repair['health']['suggest']:return
        good=[i for i in range(3) if i not in bad];values=np.empty((self.h,self.w),np.float32)
        base=np.clip(np.asarray(self.p.base if self.p.base is not None else [.5]*3,np.float32),core.EPS,1.)
        for y in range(0,self.h,128):
            self.check();x=np.nan_to_num(self.light(self.source,self.p,(0,y,self.w,min(self.h,y+128))),nan=0.,posinf=1.,neginf=0.)
            rel=(core.to_density(x)-core.to_density(base))-float(self.p.d_min)
            mix=rel[...,good].mean(axis=2)
            for c in bad:rel[...,c]=mix
            values[y:y+128]=rel.mean(axis=2)
        self.repair['span']=float(np.clip(np.quantile(values,.999),.6,3.5))
    def base(self,box):
        x0,y0,x1,y1=box;out=np.empty((y1-y0,x1-x0,3),np.float32)
        for y in range(y0,y1,128):
            self.check();raw=self.light(self.source,self.p,(x0,y,x1,min(y1,y+128)))
            out[y-y0:y-y0+128]=core.process(raw,self.clean,self.p.working_space,repair_context=self.repair)
        return out
    def median(self,box):
        x0,y0,x1,y1=box;r=self.p.dust_radius;k=r*2+1
        outer=(max(0,x0-r),max(0,y0-r),min(self.w,x1+r),min(self.h,y1+r))
        raw=self.base(outer)
        raw=np.pad(raw,((max(0,r-y0),max(0,y1+r-self.h)),(max(0,r-x0),max(0,x1+r-self.w)),(0,0)),mode='reflect')
        return np.median(np.lib.stride_tricks.sliding_window_view(raw,(k,k),axis=(0,1)),axis=(-2,-1))
    def prepare_dust(self):
        r=self.p.dust_radius;k=2*r+1
        if min(self.h,self.w)<k:return
        mask=np.zeros((self.h,self.w),bool)
        for y in range(0,self.h,128):
            for x in range(0,self.w,128):
                self.check();box=(x,y,min(self.w,x+128),min(self.h,y+128));a=self.base(box);median=self.median(box)
                mask[y:y+128,x:x+128]=np.abs((a-median)@np.array([.2126,.7152,.0722],np.float32))>np.float32(.18-.15*self.p.dust_strength)
        mask[:r]=False;mask[-r:]=False;mask[:,:r]=False;mask[:,-r:]=False
        if np.mean(mask)>.08:return
        selected=np.zeros_like(mask)
        for y in range(self.h):
            self.check()
            for x in np.flatnonzero(mask[y]):
                if not mask[y,x]:continue
                todo=deque([(y,int(x))]);mask[y,x]=False;component=[];size=0
                ymin=ymax=y;xmin=xmax=int(x)
                while todo:
                    py,px=todo.popleft();size+=1
                    if size<=k*k:component.append((py,px))
                    ymin=min(ymin,py);ymax=max(ymax,py);xmin=min(xmin,px);xmax=max(xmax,px)
                    for dy in (-1,0,1):
                        for dx in (-1,0,1):
                            yy,xx=py+dy,px+dx
                            if 0<=yy<self.h and 0<=xx<self.w and mask[yy,xx]:mask[yy,xx]=False;todo.append((yy,xx))
                    if len(todo)>262144:raise ValueError('去尘连通分析超过队列预算')
                    if size%4096==0:self.check()
                bh,bw=ymax-ymin+1,xmax-xmin+1
                if size<=k*k and max(bh,bw)<=k and max(bh,bw)<=3*min(bh,bw):
                    ys,xs=zip(*component);selected[ys,xs]=True
        self.selected=selected
    def get(self,box,n=None):
        reserve=(box[2]-box[0])*(box[3]-box[1])*24
        if self.active_bytes+reserve>512*1024*1024:raise ValueError('仿制依赖临时数组超过预算')
        return self._get(box,n)
    def _get(self,box,n=None):
        self.check();n=len(self.edits) if n is None else n
        key=(tuple(box),n)
        if key in self.cache:return self.cache[key].copy()
        self.calls+=1;self.work+=(box[2]-box[0])*(box[3]-box[1])
        if self.calls>2048 or self.work>256_000_000:raise ValueError('仿制依赖超过区域计算预算，请用导出核对')
        if n:
            out=self.get(box,n-1)
            reserve=out.nbytes*2;self.active_bytes+=reserve
            try:out=local_edits.apply(out,[self.edits[n-1]],self.p.working_space,self.p.flip,origin=box[:2],shape=(self.h,self.w),source_reader=lambda b:self.get(b,n-1))
            finally:self.active_bytes-=reserve
        else:
            out=self.base(box)
            if self.selected is not None:
                selected=self.selected[box[1]:box[3],box[0]:box[2]]
                if np.any(selected):out[selected]=self.median(box)[selected]
        if out.nbytes<=16*1024*1024:
            while self.cache and self.bytes+out.nbytes>16*1024*1024:
                _,previous=self.cache.popitem(last=False);self.bytes-=previous.nbytes
            self.cache[key]=out.copy();self.bytes+=out.nbytes
        return out
