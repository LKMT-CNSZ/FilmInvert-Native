"""Independent projective geometry, bounded ROI, Qt handles and persisted exports."""
from pathlib import Path
import os,sys,math,copy,tempfile,time,json,io
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import perspective,geometry,core,viewport_render as vr,desktop_model as model,studio_worker
import native_app as ui,calibration,project_transfer,studio_archive
from native_geometry import GeometryDialog
from studio_store import StudioStore
from tiffread import read_tiff
from tiffio import write_tiff_rgb16
from PySide6.QtCore import Qt,QPointF,QEvent
from PySide6.QtGui import QMouseEvent
from PIL import Image,ImageCms

known=np.array([[.78,.08,.06],[.05,.76,.07],[.15,-.05,1.]])
points=[]
for u,v in perspective.FULL:
    x,y,z=known@np.array([u,v,1.]);points.append([x/z,y/z])
spec={'points':points,'ratio':None}
assert np.allclose(perspective.matrix(points),known,atol=1e-12)
assert perspective.normalize({'points':perspective.FULL,'ratio':None}) is None
for bad in ({'points':points[::-1]}, {'points':[points[0],points[2],points[1],points[3]]},
            {'points':[[.1,.1]]*4},{'points':[[0,0],[1,0],[1,1],[float('nan'),1]]},
            {'points':[[-.1,0],[1,0],[1,1],[0,1]]},dict(spec,ratio=0)):
    try:perspective.normalize(bad)
    except ValueError:pass
    else:raise AssertionError('invalid quad accepted')

# A known independent homography + pixel ramps test all spatial inverses.
h,w=260,390;yy,xx=np.mgrid[:h,:w]
ramp=np.stack([(xx+.5)/w,(yy+.5)/h,np.full((h,w),.5)],axis=-1).astype(np.float32)
for rotation in (0,90,180,270):
 for angle in (0,7.3,-9.1):
  for flip in (False,True):
    p={'rotation':rotation,'straighten':angle,'crop':[.07,.11,.93,.89],'perspective':spec,'flip':flip}
    actual=geometry.apply(ramp[:,::-1] if flip else ramp,p);oh,ow=actual.shape[:2]
    assert (oh,ow)==geometry.output_shape(ramp.shape,p)
    rh,rw=(w,h) if rotation%180 else (h,w)
    co,si=math.cos(math.radians(angle)),math.sin(math.radians(angle))
    scale=min((rw-4)/(abs(co)*rw+abs(si)*rh),(rh-4)/(abs(si)*rw+abs(co)*rh),1.) if angle else 1.
    sw,sh=(max(1,int(rw*scale)),max(1,int(rh*scale))) if angle else (rw,rh)
    left,top=(rw-sw)//2,(rh-sh)//2
    q=np.array(points)*[sw,sh];lengths=np.linalg.norm(np.roll(q,-1,axis=0)-q,axis=1)
    bw,bh=(lengths[0]+lengths[2])/2,(lengths[1]+lengths[3])/2
    s=min(1.,sw/bw,sh/bh);bw,bh=max(1,int(bw*s)),max(1,int(bh*s))
    for ix,iy in ((ow//4,oh//3),(ow//2,oh//2),(ow*3//4,oh*2//3)):
        u,v=(round(.07*bw)+ix+.5)/bw,(round(.11*bh)+iy+.5)/bh
        a,b,z=known@np.array([u,v,1.]);a,b=left+a/z*sw,top+b/z*sh
        a,b=(co*(a-rw/2)+si*(b-rh/2)+rw/2,-si*(a-rw/2)+co*(b-rh/2)+rh/2)
        x,y=a/rw,b/rh
        if rotation==90:x,y=y,1-x
        elif rotation==180:x,y=1-x,1-y
        elif rotation==270:x,y=1-y,x
        if flip:x=1-x
        assert np.allclose(actual[iy,ix,:2],[x,y],atol=.003),(rotation,angle,flip,actual[iy,ix],x,y)
        assert np.allclose(geometry.source_point((ix+.5)/ow,(iy+.5)/oh,ramp.shape,p),[x,y],atol=1e-10)
    uniform=geometry.apply(np.full_like(ramp,.41357),p)
    assert np.allclose(uniform,.41357,atol=1e-6)

for ratio in (1.,1.5,2/3):
    shape=geometry.output_shape(ramp.shape,{'perspective':dict(spec,ratio=ratio)})
    assert abs(shape[1]/shape[0]-ratio)<.02 and shape[0]*shape[1]<=h*w
assert calibration.parameter_signature(core.Params().__dict__)==calibration.parameter_signature(core.Params(perspective=spec).__dict__)

# Full / ROI parity, including advanced global/local contexts and wide/original.
rng=np.random.default_rng(128);source=rng.uniform(.05,.8,(180,260,3)).astype(np.float32)
for space in ('srgb','prophoto'):
 for mode in ('positive','color_negative','bw_negative'):
  for advanced in (False,True):
    p=core.Params(mode=mode,working_space=space,base=[.8,.7,.6],black=0,white=1,
        rotation=90,straighten=5.1,crop=[.04,.03,.96,.97],flip=True,perspective=spec)
    if advanced:
        p.local_edits=[{'kind':'brush','points':[[.45,.5],[.6,.6]],'radius':.15,'ev':.3}];p.vignette=.2
    expected=core.process(source,p,space);oh,ow=expected.shape[:2];box=(5,7,ow-5,oh-7)
    wide,bounds,shape,_=vr.render(source,p,box)
    assert bounds==box and shape==(oh,ow)
    assert np.allclose(wide,expected[7:oh-7,5:ow-5],atol=2e-5),(space,mode,advanced,np.max(np.abs(wide-expected[7:oh-7,5:ow-5])))
    import colorio
    raw=source[:,::-1];raw=core.srgb_encode(raw) if space=='srgb' else colorio.encode(raw,space)
    original=geometry.apply(raw,p.__dict__)
    assert np.allclose(vr.render(source,p,box,True)[0],original[7:oh-7,5:ow-5],atol=2e-5)
large=np.broadcast_to(source[0:1,0:1],(3464,5202,3));process=core.process;seen=[]
def bounded(a,*args,**kwargs):
    seen.append(a.shape);assert a.shape[0]*a.shape[1]<=vr.MAX_PIXELS*2
    return process(a,*args,**kwargs)
core.process=bounded
try:assert vr.render(large,core.Params(mode='positive',black=0,white=1,perspective=spec),(1500,1000,2100,1400))[0].shape==(400,600,3)
finally:core.process=process
assert seen and all(s[0]*s[1]<2_000_000 for s in seen)

app=ui.QApplication.instance() or ui.QApplication([])
preview={'pixels':np.full((260,390,3),128,np.uint8),'source_shape':(1040,1560)}
dialog=GeometryDialog(preview,{'rotation':0,'straighten':2.,'crop':geometry.FULL})
dialog.show();app.processEvents();dialog.perspective_button.setChecked(True);app.processEvents();canvas=dialog.canvas
def drag(a,b):
    rect=canvas.item.boundingRect()
    for kind,p,button,buttons in ((QEvent.Type.MouseButtonPress,a,Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton),
       (QEvent.Type.MouseMove,b,Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton),
       (QEvent.Type.MouseButtonRelease,b,Qt.MouseButton.LeftButton,Qt.MouseButton.NoButton)):
        local=QPointF(canvas.mapFromScene(QPointF(p[0]*rect.width(),p[1]*rect.height())))
        ui.QApplication.sendEvent(canvas.viewport(),QMouseEvent(kind,local,local,button,buttons,Qt.KeyboardModifier.NoModifier));app.processEvents()
for i in range(4):drag(canvas.quad[i],points[i])
assert dialog.value.get('perspective') and dialog.apply_button.isEnabled()
before=copy.deepcopy(dialog.value);drag(canvas.quad[0],(.98,.98))
assert canvas.quad_invalid and not dialog.apply_button.isEnabled() and dialog.value==before
drag(canvas.quad[0],points[0]);assert not canvas.quad_invalid
dialog.perspective_ratio.setCurrentIndex(dialog.perspective_ratio.findData(1.5));dialog.refresh()
assert dialog.value['perspective']['ratio']==1.5
dialog.line_button.setChecked(True);assert not canvas.perspective_mode and canvas.line_mode
dialog.perspective_button.setChecked(True);assert canvas.perspective_mode and not canvas.line_mode
captured=[];dialog.applied.connect(captured.append);dialog.apply_result();assert captured[0]['perspective']
dialog=GeometryDialog(preview,captured[0]);assert dialog.perspective_button.isChecked()
dialog.clear_perspective();assert 'perspective' not in dialog.value
dialog.quad_changed(points);dialog.turn(90);assert 'perspective' not in dialog.value
dialog.reset_geometry();assert 'perspective' not in dialog.value and dialog.value['straighten']==0
dialog.reject()

(ROOT/'build/tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build/tests',prefix='perspective-') as temporary:
    folder=Path(temporary);window=ui.MainWindow(folder/'ui');errors=[];window.show_error=errors.append;window.show()
    def idle():
        deadline=time.monotonic()+50
        while time.monotonic()<deadline:
            app.processEvents();time.sleep(.01)
            if window.job is None and not window.pending_command and not window.preview_timer.isActive() and not any(j.isRunning() for j in window.jobs):break
        else:raise AssertionError('worker timeout')
        assert not errors,errors
    file=folder/'source.tif';write_tiff_rgb16(file,ramp)
    window.open_mode.setCurrentIndex(window.open_mode.findData('single'));window.open_path(str(file));idle()
    original=model.engine.STATE['full'].copy();initial=copy.deepcopy(window.p)
    window.apply_geometry({'rotation':90,'straighten':3.2,'crop':[.05,.05,.95,.95],'perspective':spec});idle()
    edited=copy.deepcopy(window.p);assert edited['perspective']==spec
    window.travel(-1);idle();assert window.p==initial
    window.travel(1);idle();assert window.p==edited and np.array_equal(model.engine.STATE['full'],original)
    window.pixel_view();idle();assert window.canvas.region_mode
    window.fit_view();idle();assert not window.canvas.item.pixmap().isNull()
    p=model.params(edited);sx,sy=geometry.source_point(.3,.6,model.engine.STATE['preview'].shape,edited)
    assert np.allclose(model.sample(edited,.3,.6,'base')['base'],core.sample_patch(model.engine.corrected_input(original,p),sx,sy,10),atol=1e-6)
    project=folder/'perspective.filmproj';window.save_project_to(str(project));idle()
    saved=model.read_project(project);assert saved['version']==8
    bad=copy.deepcopy(saved);bad['version']=6;model.write_json(folder/'old-version.filmproj',bad)
    before=model.project_signature(model.project_snapshot(edited))
    try:model.open_project(folder/'old-version.filmproj')
    except ValueError:pass
    else:raise AssertionError('perspective silently accepted in old project')
    assert model.project_signature(model.project_snapshot(edited))==before
    window.reset_geometry();idle();assert window.p['perspective'] is None
    window.open_project_path(str(project));idle();assert window.p['perspective']==spec
    recipe=model.recipe(window.p);assert recipe['version']==6 and model.recipe_params(recipe)['perspective']==spec
    bad=copy.deepcopy(recipe);bad['version']=4
    try:model.recipe_params(bad)
    except ValueError:pass
    else:raise AssertionError('perspective silently accepted in old recipe')
    snapshot=model.project_snapshot(window.p);store=StudioStore(folder/'studio');asset=store.capture(snapshot)
    job=store.enqueue([asset],folder/'output',False)[0];assert studio_worker.execute(store,job)=='completed'
    output=Path(store.get_job(job)['destination'])/'frame-0001.tif'
    expected=(np.clip(core.process(original,model.params(edited)),0,1)*65535+.5).astype(np.uint16)
    assert np.array_equal(read_tiff(output),expected)
    with Image.open(output) as im:assert im.info.get('icc_profile')
    assert json.loads(Path(str(output)+'.json').read_text('utf-8'))['version']==6
    project_transfer.pack(snapshot,folder/'perspective.filmtransfer')
    moved=project_transfer.unpack(folder/'perspective.filmtransfer',folder/'moved')
    assert model.open_project(moved['project'])['params']['perspective']==spec
    studio_archive.backup(store,folder/'perspective.filmbackup');studio_archive.restore(store,folder/'perspective.filmbackup')
    # Local/chart tools receive their pre-geometry image; B input edits clear C.
    assert model.local_preview(edited)['working_pixels'].shape[:2]==model.engine.STATE['preview'].shape[:2]
    layout={'mode':'strip','manual':{'rotation':0,'frames':[{'id':'one','box':[2,2,w-2,h-2]}]}}
    assert model.reframe(layout,edited)['params']['perspective'] is None
    window.loaded=False;window.close();app.processEvents()
for widget in app.topLevelWidgets():widget.close();widget.deleteLater()
app.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()
print('PASS perspective independent homography/ramp inverse/rotation/flip/crop, no fill, bounded exact ROI/wide/original/local, Qt four corners/invalid/reset, history/v7/v5 gates, sample coordinates, exact queued ICC TIFF, transfer/backup and B clearing')
