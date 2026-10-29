"""Scene-linear tonal controls preserve chromatic ratios and source detail."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import core,tone_mapping,viewport_render

x=np.geomspace(.001,4,256,dtype=np.float32)[None,:,None]*np.ones((8,1,3),np.float32)
assert tone_mapping.apply(x,core.Params()) is x
shadow=tone_mapping.apply(x,core.Params(tone_shadows=.5))
assert shadow[0,0,0]>x[0,0,0] and shadow[0,-1,0]/x[0,-1,0]<1.01
highlight=tone_mapping.apply(x,core.Params(tone_highlights=-.8,tone_rolloff=.8))
assert highlight.max()<1 and np.unique(highlight[0,:,0]).size==256
assert np.all(np.diff(highlight[0,:,0])>0)
colored=x*np.array([1,.8,.5],np.float32)
out=tone_mapping.apply(colored,core.Params(tone_rolloff=.8))
assert np.max(np.abs(out[...,1]/out[...,0]-.8))<2e-6
assert np.isfinite(tone_mapping.apply(np.zeros((2,3,3),np.float32),core.Params(tone_blacks=.5))).all()
rng=np.random.default_rng(20);image=rng.uniform(.05,.7,(96,130,3)).astype(np.float32)
for space in ('srgb','prophoto'):
 for mode in core.MODES:
  for flip in (False,True):
   p=core.Params(mode=mode,working_space=space,base=[.7]*3,flip=flip,tone_shadows=.3,tone_highlights=-.4,tone_whites=.2,tone_rolloff=.6)
   full=core.process(image,p,target_space=space)
   roi=viewport_render.render(image,p,[12,15,90,70])[0]
   assert np.max(np.abs(full[15:70,12:90]-roi))<2e-6,(space,mode,flip)
print('PASS neutral identity, shadow selectivity, untruncated highlight gradients, monotonic shoulder, chromatic ratios, finite blacks and all mode/workspace/flip ROI equality')
for mode in core.MODES:
 for flip in (False,True):
  for vignette in (0,.2):
   p=core.Params(mode=mode,base=[.7]*3,flip=flip,vignette=vignette,local_tone=.5,local_detail=.2)
   full=core.process(image,p)
   roi=viewport_render.render(image,p,[12,15,90,70])[0]
   assert np.max(np.abs(full[15:70,12:90]-roi))<3e-6,(mode,flip,vignette,np.max(np.abs(full[15:70,12:90]-roi)))
flat=np.full((100,120,3),.25,np.float32)
p=core.Params(mode='positive',black=0,white=1,local_tone=.7,local_detail=.2)
assert np.max(np.abs(core.process(flat,p)-core.process(flat,core.Params(mode='positive',black=0,white=1))))<2e-6
print('PASS local tone/detail full-vs-tiled ROI with flip/illumination, neutral flat fields and bounded context')
