"""Metering excludes coherent scan bands and preserves real image dimensions."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import core,metering,viewport_render

y,x=np.mgrid[:180,:260]
inner=np.stack([.12+.15*x/260,.15+.13*y/180,.20+.1*x/260],axis=-1).astype(np.float32)
image=np.pad(inner,((8,8),(8,8),(0,0)),constant_values=.7)
image[-5:]=.0001
p=core.Params(base=[.7]*3,span=1.2,meter_mode='edges')
box,evidence=metering.bounds(image)
assert evidence and box[0]>=8 and box[1]>=8 and box[2]<=268 and box[3]<=188,(box,evidence)
a=core.process(image,p)
assert a.shape==image.shape
x0,y0,x1,y1=box
scene=core.scene_linear(image,p);lo,hi=np.quantile(core.luma(scene)[y0:y1,x0:x1],[.002,.998])
expected=core.process(image,core.Params(base=[.7]*3,span=1.2,black=float(lo),white=float(hi)))
assert np.array_equal(a,expected)
legacy=core.process(image,core.Params(base=[.7]*3,span=1.2))
assert float(a[8:188,8:268].mean())>float(legacy[8:188,8:268].mean())+.1
for mode in ('positive','color_negative','bw_negative'):
 for flip in (False,True):
  p=core.Params(mode=mode,base=[.7]*3,span=1.2,meter_mode='edges',flip=flip)
  full=core.process(image,p);roi=viewport_render.render(image,p,[22,30,140,150])[0]
  assert np.max(np.abs(roi-full[30:150,22:140]))<2e-6,(mode,flip)
for picture in (inner,np.full((120,150,3),.2,np.float32),np.tile(np.linspace(0,1,180,dtype=np.float32)[:,None,None],(1,260,3))):
 assert metering.bounds(picture)[0]==(0,0,picture.shape[1],picture.shape[0])
 assert np.array_equal(metering.region(picture,'legacy'),picture)
assert core.Params().meter_mode=='legacy'
print('PASS border metering, no-border/flat/gradient fallback, legacy identity, no output crop, all mode/flip full-vs-ROI consistency')
