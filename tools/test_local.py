"""Numerical local edits, actual modal strokes, persistence and queue exports."""
from pathlib import Path
import os,sys,tempfile,copy,time,json
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import native_app as ui
import numpy as np
import local_edits as le,core,colorio,geometry,desktop_model as model,studio_worker
from input_color import display_working
from tiffio import write_tiff_rgb16
from tiffread import read_tiff
from PySide6.QtCore import Qt,QPointF
from PySide6.QtGui import QMouseEvent
app=ui.QApplication.instance() or ui.QApplication([])
def idle(window):
    end=time.monotonic()+30
    while time.monotonic()<end:
        app.processEvents();time.sleep(.005)
        if window.job is None and not window.preview_timer.isActive() and not window.preview_pending:return
    raise AssertionError('native task timeout')
# Independent clone reference: known coordinate ramp, integer offset and hard capsule.
y,x=np.mgrid[:101,:151];image=np.stack([x/200,y/150,(x+y)/300],axis=-1).astype(np.float32)
op=dict(kind='clone',points=[[.6,.3],[.6,.7]],radius=.04,feather=0,opacity=1,offset=[-.2,0])
actual=le.apply(image,[op]);expected=image.copy()
for yy in range(101):
 for xx in range(151):
  distance=((xx-90)**2+(yy-min(70,max(30,yy)))**2)**.5
  if distance<4:expected[yy,xx]=image[yy,xx-30]
assert np.max(np.abs(actual-expected))<2e-7
assert np.array_equal(image,np.stack([x/200,y/150,(x+y)/300],axis=-1).astype(np.float32))
# No feedback smear: a stroke reads immutable input despite overlapping source/destination.
over=dict(op,points=[[.4,.5],[.7,.5]],offset=[-.1,0])
a=le.apply(image,[over]);assert np.max(np.abs(a[50,60:105]-image[50,45:90]))<2e-7
assert np.max(np.abs(le.apply(image[:,::-1],[op],flip=True)-actual[:,::-1]))<2e-7
outside=dict(op,offset=[.9,0]);assert np.array_equal(le.apply(image,[outside]),image)
# Linear-light exposure endpoints, independent sRGB expression, finite tiny images.
flat=np.full((11,21,3),.25,np.float32);grad=dict(kind='gradient',points=[[0,0],[1,0]],ev=1,opacity=1)
out=le.apply(flat,[grad]);lin=((.25+.055)/1.055)**2.4
assert np.array_equal(out[:,0],flat[:,0])
assert abs(float(out[5,-1,0])-(1.055*(lin*2)**(1/2.4)-.055))<1e-7
for space in colorio.SPACES:
 assert np.isfinite(le.apply(flat,[grad],space)).all()
 assert np.isfinite(le.apply(np.full((1,1,3),.25,np.float32),[grad],space)).all()
brush=dict(kind='brush',points=[[.5,.5]],radius=.1,feather=1,opacity=.5,ev=-1)
out=le.apply(flat,[brush]);assert np.array_equal(out[:,0],flat[:,0]);assert out[5,10,0]<.25
for bad in ([dict(brush,radius=float('nan'))],[dict(brush,points=[[2,0]])],[dict(grad,points=[[0,0],[0,0]])],[dict(op,offset=[float('inf'),0])], [brush]*257):
 try:le.normalize(bad)
 except (ValueError,TypeError):pass
 else:raise AssertionError('malformed edit accepted')
assert le.apply(image,[]) is image
print('PASS independent capsule clone, immutable stroke source, source-edge exclusion, flip anchoring, linear EV gradient/brush, tiny images, invalid edits and no-op identity')
(ROOT/'build/tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build/tests',prefix='local-') as tmp:
 folder=Path(tmp);source=folder/'strip.tif'
 scan=np.tile(image,(1,2,1));write_tiff_rgb16(source,scan,icc_profile=colorio.icc_profile('prophoto'))
 manual={'rotation':0,'frames':[{'id':'a','box':[0,0,151,101]},{'id':'b','box':[151,0,302,101]}]}
 opened=model.open_image(source,input_mode='strip',manual=manual,input_color='prophoto',working_space='prophoto')
 p=opened['params'];p.update(mode='positive',black=0,white=1,local_edits=[op,brush,grad],flip=True,rotation=90,straighten=1,crop=[.1,.1,.9,.9],output_space='prophoto')
 lin=model.engine.STATE['full'].copy();base=core.process(lin,model.params(dict(p,local_edits=[],rotation=0,straighten=0,crop=geometry.FULL)),'prophoto')
 expected=geometry.apply(le.apply(base,p['local_edits'],'prophoto',True),p)
 assert np.array_equal(core.process(lin,model.params(p),'prophoto'),expected)
 dest=folder/'result.tif';model.export_image(dest,p)
 assert np.array_equal(read_tiff(str(dest)),(np.clip(expected,0,1)*65535+.5).astype(np.uint16))
 assert json.loads(Path(str(dest)+'.json').read_text('utf-8'))['version']==3
 saved=folder/'local.filmproj';model.save_project(saved,p);assert model.read_project(saved)['version']==5
 restored=model.open_project(saved);assert restored['params']['local_edits']==le.normalize(p['local_edits'])
 assert np.array_equal(core.process(model.engine.STATE['full'],model.params(restored['params']),'prophoto'),expected)
 other=model.change_frame(1,p,True,False)['params'];assert other['local_edits']==[]
 other.update(mode='positive',black=0,white=1,local_edits=[brush]);model.preview(other)
 back=model.change_frame(0,other,True,False)['params'];assert back['local_edits']==le.normalize(p['local_edits'])
 model.apply_all(back,False)
 assert model.engine.STATE['roll_params']['local_edits']==[]
 assert model.engine.STATE['frame_params'][1]['local_edits']==le.normalize([brush])
 # Queue parameters remain a frozen copy of all frame-local operations.
 model.preview(back)
 snapshot=model.project_snapshot(back);window=ui.MainWindow(folder/'profile')
 store=window.studio_store;asset=store.capture(snapshot)
 job=store.enqueue([asset],folder/'queue',True)[0]
 expected_frames={}
 for i in range(2):
  fp=model.params(snapshot['frames'].get(str(i),snapshot['roll_params']));arr=model.engine.STATE['native_loaded'].linear[:,i*151:(i+1)*151]
  expected_frames[i]=(np.clip(core.process(arr,fp,fp.output_space),0,1)*65535+.5).astype(np.uint16)
 assert studio_worker.execute(store,job)=='completed'
 for i in range(2):assert np.array_equal(read_tiff(str(Path(store.get_job(job)['destination'])/f'frame-{i+1:04d}.tif')),expected_frames[i])
 # Changing source ROI clears affected local edits; layout undo restores them.
 changed=copy.deepcopy(manual);changed['frames'][0]['box']=[1,0,151,101]
 result=model.reframe({'mode':'strip','manual':changed},back)
 assert result['params']['local_edits']==[]
 model.restore_layout(result['previous']);assert model.engine.STATE['params'].local_edits==le.normalize(back['local_edits'])
 print('PASS pre-geometry core order, exact wide TIFF/recipe v3, v5 project, per-frame isolation/apply-all, frozen two-frame queue, reframe clearing and restore')
 # Actual native modal; deliver viewport mouse events rather than calling stroke algorithm.
 window.on_opened(model.open_project(saved));idle(window);window.open_local();idle(window);d=window.local_dialog;app.processEvents()
 assert d is not None;initial=copy.deepcopy(d.edits);d.clear();d.tool.setCurrentIndex(1)
 def send(kind,norm,button,buttons,mods=Qt.KeyboardModifier.NoModifier):
  rect=d.canvas.item.boundingRect();pos=d.canvas.mapFromScene(QPointF(norm[0]*rect.width(),norm[1]*rect.height()))
  event=QMouseEvent(kind,QPointF(pos),QPointF(d.canvas.viewport().mapToGlobal(pos)),button,buttons,mods)
  app.sendEvent(d.canvas.viewport(),event);app.processEvents()
 send(QMouseEvent.Type.MouseButtonPress,[.3,.3],Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton)
 send(QMouseEvent.Type.MouseMove,[.5,.4],Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton)
 send(QMouseEvent.Type.MouseButtonRelease,[.6,.5],Qt.MouseButton.LeftButton,Qt.MouseButton.NoButton)
 assert len(d.edits)==1 and d.edits[0]['kind']=='brush'
 d.travel(-1);assert d.edits==[];d.travel(1);assert len(d.edits)==1
 before=d.preview['working_pixels'].copy();edits=copy.deepcopy(d.edits)
 d.preview['monitor']=colorio.icc_profile('adobe_rgb');d.refresh()
 assert np.array_equal(before,d.preview['working_pixels']) and d.edits==edits
 d.applied.emit(copy.deepcopy(d.edits));d.accept();idle(window)
 assert window.p['local_edits']==edits
 window.commit_history();window.travel(-1);idle(window);assert window.p['local_edits']==initial
 window.travel(1);idle(window);assert window.p['local_edits']==edits
 window.open_local();idle(window);d=window.local_dialog;d.clear();d.reject();app.processEvents();assert window.p['local_edits']==edits
 window.loaded=False;window.preview_timer.stop();window.history_timer.stop();window.close();app.processEvents()
 print('PASS native mouse stroke, modal undo/redo, monitor compute separation, apply/main undo/redo and cancel')
print('PASS local editing regressions')

