"""Advanced ROI equals whole pipeline, with remote clone stages and global decisions."""
from pathlib import Path
import os,sys,threading
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import native_app,core,viewport_render as vr,numpy as np
rng=np.random.default_rng(410)
edits=[{'kind':'brush','points':[[.15,.3],[.4,.5]],'radius':.12,'feather':.6,'ev':.8},
 {'kind':'gradient','points':[[.2,.2],[.85,.8]],'ev':-.4},
 {'kind':'clone','points':[[.7,.4],[.8,.6]],'radius':.09,'offset':[-.5,-.1]},
 {'kind':'clone','points':[[.35,.7]],'radius':.13,'offset':[.4,-.2]},
 {'kind':'clone','points':[[.05,.02]],'radius':.15,'offset':[-.2,.01]}]
count=0
for space in ('srgb','prophoto'):
 for flip in (False,True):
  for turn in (0,90,270):
   a=rng.uniform(.03,.8,(83,117,3)).astype(np.float32)
   p=core.Params(mode='positive',black=0,white=1,flip=flip,rotation=turn,working_space=space,local_edits=edits)
   whole=core.process(a,p,space);h,w=whole.shape[:2]
   for box in ((0,0,25,30),(w//3,h//3,w-4,h-3)):
    got=vr.render(a,p,box)[0];expected=whole[box[1]:box[3],box[0]:box[2]]
    assert np.max(np.abs(got-expected))<2e-6,(space,flip,turn,np.max(np.abs(got-expected)))
    count+=1
# Global bad-channel health/span must not be recomputed on a locally healthy ROI.
for auto in (False,True):
 a=rng.uniform(.05,.8,(169,231,3)).astype(np.float32);a[...,2]=.001;a[70:110,80:140,2]=.2
 p=core.Params(mode='color_negative',channel_repair=True,black=None if auto else 0,white=None if auto else 1,base=[.8,.7,.6],flip=True,vignette=.2)
 whole=core.process(a,p);box=(70,60,160,120);got=vr.render(a,p,box)[0]
 assert np.allclose(got,whole[60:120,70:160],atol=1e-7),np.max(np.abs(got-whole[60:120,70:160]))
# Small isolated dust, connected candidates crossing tile boundaries and >8% gate.
for dense in (False,True):
 a=np.full((259,277,3),.25,np.float32)
 if dense:a[::2,::2]=.9
 else:
  a[127:129,127:129]=.95;a[60,80]=.001;a[185,185:199]=.9
 p=core.Params(mode='positive',black=0,white=1,dust_strength=.7,dust_radius=2,local_edits=edits[:2])
 whole=core.process(a,p)
 for box in ((110,110,150,150),(20,20,200,200),(0,0,25,25)):
  got=vr.render(a,p,box)[0];assert np.allclose(got,whole[box[1]:box[3],box[0]:box[2]],atol=1e-7)
# Remote sources in an 18MP image use bounded regional processing.
large=np.broadcast_to(np.array([[[.2,.3,.4]]],np.float32),(3464,5202,3))
p=core.Params(mode='positive',black=0,white=1,local_edits=edits)
original=core.process;seen=[]
def bounded(a,*args,**kwargs):
 assert a.shape[0]<=128;seen.append(a.shape);return original(a,*args,**kwargs)
core.process=bounded
try:assert vr.render(large,p,(2000,1300,2500,1800))[0].shape==(500,500,3)
finally:core.process=original
assert seen
stop=threading.Event();stop.set();vr.clear_context()
try:vr.render(large,core.Params(mode='positive',black=0,white=1,dust_strength=.5),(100,100,200,200),cancel=stop)
except InterruptedError:pass
else:raise AssertionError('cancel ignored')
vr.clear_context()
# 4K output does not require one giant core processing call.
p=core.Params(mode='positive',black=0,white=1)
wide=vr.render(large,p,(0,0,3840,2160))[0]
assert wide.shape==(2160,3840,3) and np.allclose(wide[::100,::100],core.process(large[:1,:1],p))
del wide;vr.clear_context()
print(f'PASS advanced region: {count} clone/brush/gradient workspace/flip/rotation comparisons, global repair/auto levels, dust connectivity and 8% gate, bounded 18MP and cancellation')
