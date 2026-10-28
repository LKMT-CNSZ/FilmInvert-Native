"""Viewport pixels vs full processing, global metering, pan/stale/error recovery."""
from pathlib import Path
import os,sys,time,tempfile,copy
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import native_app as ui,desktop_model as model,viewport_render as vr,core,colorio,geometry
import numpy as np
app=ui.QApplication.instance() or ui.QApplication([])
rng=np.random.default_rng(870);image=rng.uniform(.03,.85,(123,169,3)).astype(np.float32)
cases=0
for space in ('srgb','prophoto'):
 for mode in ('positive','color_negative','bw_negative'):
  for settings in ({},{'flip':True,'rotation':90,'crop':[.1,.03,.9,.95]},
                   {'rotation':270,'straighten':7.2,'crop':[.08,.09,.97,.94]},
                   {'flip':True,'rotation':180,'vignette':.35,'flat_mode':'reference','flat_strength':.4,'flat_field':[[[.8,1.1,1.2],[1.2,.9,.8]],[[1.1,.85,1.1],[.9,1.2,.95]]]}):
   p=core.Params(mode=mode,working_space=space,base=[.8,.7,.6],temp=.15,tint=-.08,exposure=.25,**settings)
   if not settings:p.black=0;p.white=1
   expected=core.process(image,p,space);h,w=expected.shape[:2]
   box=(7,9,w-8,h-11);wide,actual_box,shape,method=vr.render(image,p,box)
   difference=float(np.max(np.abs(wide-expected[9:h-11,7:w-8])))
   assert difference<2e-5,(space,mode,settings,difference)
   assert shape==(h,w) and actual_box==box
   raw=image[:,::-1] if p.flip else image
   raw=core.srgb_encode(raw).astype(np.float32) if space=='srgb' else colorio.encode(raw,space)
   reference=geometry.apply(raw,p.__dict__)
   original=vr.render(image,p,box,True)[0]
   assert np.allclose(original,reference[9:h-11,7:w-8],atol=2e-5)
   cases+=1
# 18MP read-only/broadcast source: processing arrays are limited to requested pixels.
large=np.broadcast_to(image[0:1,0:1],(3464,5202,3));p=core.Params(mode='positive',black=0,white=1)
process=core.process;seen=[]
def bounded(a,*args,**kwargs):
 seen.append(a.shape);assert a.shape[0]*a.shape[1]<=vr.MAX_PIXELS*2;return process(a,*args,**kwargs)
core.process=bounded
try:
 wide,_,_,_=vr.render(large,p,(2200,1300,3000,1900));assert wide.shape==(600,800,3)
finally:core.process=process
assert seen==[(600,800,3)]
(ROOT/'build/tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build/tests',prefix='viewport-') as tmp:
 window=ui.MainWindow(Path(tmp)/'profile');errors=[];window.show_error=errors.append;window.show()
 def idle():
  end=time.monotonic()+45
  while time.monotonic()<end:
   app.processEvents();time.sleep(.01)
   if window.job is None and not window.pending_command and not window.preview_timer.isActive() and not any(j.isRunning() for j in window.jobs):break
  else:raise AssertionError('worker timeout')
  assert not errors,errors
 window.open_path(str(ROOT/'samples/calibration-demo.tif'));idle()
 # Install a huge immutable source without a huge processing allocation.
 previous=model.engine.STATE['full'];model.engine.STATE['full']=large
 window.set_parameters(p.__dict__);idle();window.pixel_view();idle()
 assert window.full_resolution and window.canvas.region_mode
 assert '100%' in window.zoom_label.text() and window.canvas.sceneRect().width()==5202
 first=window.canvas.item.pos();window.canvas.horizontalScrollBar().setValue(800);idle()
 assert window.canvas.item.pos()!=first
 size=window.size();window.resize(3840,2160);idle()
 assert window.full_resolution and window.canvas.region_mode and window.canvas.pixels.shape[1]>3000
 window.resize(size);idle()
 # Rapid parameter/pan changes must leave the final revision, not a stale image.
 window.edit_parameter('exposure',-.7);window.canvas.verticalScrollBar().setValue(500)
 window.edit_parameter('exposure',.6);idle()
 box=window.canvas.region_box();result=model.preview(window.p,True,region=box)
 assert np.array_equal(window.canvas.pixels,result['pixels'])
 # Sampling is normalized over the whole scene, not the tile's local rectangle.
 assert window.canvas.sceneRect().width()>window.canvas.item.boundingRect().width()
 # A bounded request failure falls back once and later edits work.
 budget=vr.MAX_PIXELS;vr.MAX_PIXELS=10
 window.edit_parameter('exposure',.25);idle()
 vr.MAX_PIXELS=budget
 assert not window.full_resolution and not window.canvas.region_mode and window.canvas.fit_mode
 assert '已退回适应窗口' in window.statusBar().currentMessage()
 window.edit_parameter('exposure',.2);idle();assert not window.full_resolution
 # A worker failure (not just a budget rejection) must also recover.
 window.edit_parameter('dust_strength',0);idle();preview=model.preview
 def fail(value,full=False,*args,**kwargs):
  if full:raise MemoryError('injected viewport allocation failure')
  return preview(value,full,*args,**kwargs)
 model.preview=fail
 try:window.pixel_view();idle();assert not window.full_resolution and not window.canvas.region_mode
 finally:model.preview=preview
 # A foreground command cancels an obsolete viewport calculation then resumes it.
 calls=[];completed=[]
 def cancel_for_command(value,full=False,*args,**kwargs):
  if full and not calls:
   calls.append(True);end=time.monotonic()+3
   while not kwargs['cancel'].is_set() and time.monotonic()<end:time.sleep(.01)
   assert kwargs['cancel'].is_set();raise InterruptedError('obsolete preview canceled for command')
  return preview(value,full,*args,**kwargs)
 model.preview=cancel_for_command
 try:
  window.pixel_view();end=time.monotonic()+5
  while not calls and time.monotonic()<end:app.processEvents();time.sleep(.01)
  assert calls
  window.command(lambda c,p:True,completed.append,'test queued command');idle()
  assert completed==[True] and window.full_resolution
 finally:model.preview=preview
 # Leaving 100% while its worker fails must discard the obsolete error as well.
 def late_fail(value,full=False,*args,**kwargs):
  if full:time.sleep(.15);raise MemoryError('obsolete viewport failure')
  return preview(value,full,*args,**kwargs)
 model.preview=late_fail
 try:
  window.pixel_view()
  end=time.monotonic()+5
  while window.job is None and time.monotonic()<end:app.processEvents();time.sleep(.01)
  assert window.job is not None;window.fit_view();idle()
  assert not window.full_resolution and not window.canvas.region_mode
 finally:model.preview=preview
 model.engine.STATE['full']=previous;window.close();app.processEvents()
print(f'PASS viewport: {cases} full/ROI cases, original geometry, global auto levels/light coordinates, bounded 18MP processing, Qt pan/revision, full-context refusal and failure fallback')
