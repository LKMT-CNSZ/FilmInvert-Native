"""Geometry acceptance: float precision, sampling coordinates and real saved TIFFs."""
from pathlib import Path
import json
import os
import sys
import tempfile
import threading
import time
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import core
import geometry
import calibration
import desktop_model as model
import native_app as ui
from native_geometry import GeometryDialog
from tiffread import read_tiff

# Independent pixel landmark oracle for quarter turns and cropping.
image=np.arange(80*120*3,dtype=np.float32).reshape(80,120,3)/(80*120*3)
for turn in (0,90,180,270):
    p={'rotation':turn,'crop':[.1,.2,.8,.9]}
    reference=np.rot90(image,-turn//90)
    h,w=reference.shape[:2]
    reference=reference[round(.2*h):round(.9*h),round(.1*w):round(.8*w)]
    assert np.array_equal(geometry.apply(image,p),reference)
    for flip in (False,True):
        p['flip']=flip
        source=image[:,::-1] if flip else image
        transformed=geometry.apply(source,p)
        h,w=transformed.shape[:2]
        for x,y in ((.1,.1),(.4,.7),(.8,.3)):
            ix,iy=min(w-1,int(x*w)),min(h-1,int(y*h))
            sx,sy=geometry.source_point((ix+.5)/w,(iy+.5)/h,image.shape,p)
            assert np.allclose(transformed[iy,ix],image[int(sy*80),int(sx*120)],atol=1e-7),(turn,flip)

# Straightening never leaks black fill into uniform images, across aspect ratios.
for h,w in ((79,113),(160,55),(39,240),(2,3)):
    for angle in (-15,-4.5,4.5,15):
        p={'rotation':90,'straighten':angle,'crop':[.03,.07,.97,.92]}
        actual=geometry.apply(np.full((h,w,3),.41357,np.float32),p)
        assert actual.dtype==np.float32 and np.isfinite(actual).all()
        assert np.allclose(actual,.41357,atol=1e-6),(h,w,angle,actual.min())

# Coordinate ramps verify arbitrary-angle inverse mapping independently of landmarks.
h,w=210,310;yy,xx=np.mgrid[:h,:w]
ramp=np.stack([(xx+.5)/w,(yy+.5)/h,np.full((h,w),.5)],axis=2).astype(np.float32)
for turns in (0,90,180,270):
    for angle in (-12.5,8.25):
        p={'rotation':turns,'straighten':angle,'crop':[.13,.09,.88,.91],'flip':True}
        actual=geometry.apply(ramp[:,::-1],p);oh,ow=actual.shape[:2]
        for x,y in ((.23,.27),(.51,.63),(.77,.52)):
            ix,iy=int(x*ow),int(y*oh)
            sx,sy=geometry.source_point((ix+.5)/ow,(iy+.5)/oh,ramp.shape,p)
            assert np.allclose(actual[iy,ix,:2],[sx,sy],atol=.003),(turns,angle,actual[iy,ix],sx,sy)
assert geometry.normalize_crop([.8,0,.2,1])==geometry.FULL
assert core.Params(rotation=float('nan'),straighten=float('inf'),crop=['bad',0,1,1]).normalized().crop==geometry.FULL
assert calibration.parameter_signature(core.Params().__dict__)==calibration.parameter_signature(core.Params(rotation=90,straighten=2,crop=[.1,.1,.9,.9]).__dict__)

app=ui.QApplication([])
(ROOT/'build'/'tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build'/'tests',prefix='geometry-') as folder:
    folder=Path(folder);window=ui.MainWindow(folder/'profile');errors=[];window.show_error=errors.append
    def idle():
        end=time.monotonic()+60
        while time.monotonic()<end:
            app.processEvents();time.sleep(.01)
            if window.job is None and not window.pending_command and not window.preview_timer.isActive() and not any(j.isRunning() for j in window.jobs):break
        else:raise AssertionError('Worker timed out')
        assert not errors,errors
    window.open_path(str(ROOT/'samples'/'pro-light-demo.tif'));idle()
    baseline=model.preview(window.p)['pixels'];original=model.engine.STATE['full'].copy()
    window.open_geometry();idle();dialog=window.geometry_dialog
    assert dialog and dialog.isModal()
    dialog.turn(90);dialog.angle_changed(4.5);dialog.refresh()
    dialog.ratios.setCurrentIndex(dialog.ratios.findData(1.5))
    assert abs(dialog.canvas.crop_rect().width()/dialog.canvas.crop_rect().height()-1.5)<.01
    dialog.apply_result();idle()
    assert window.p['rotation']==90 and window.p['straighten']==4.5
    edited=model.clone(window.p)
    assert np.array_equal(original,model.engine.STATE['full'])
    # Same spatial extent for original and processed views, sampling stays on source.
    positive=model.preview(edited)['pixels'];negative=model.preview(edited,original=True)['pixels']
    assert positive.shape==negative.shape
    point=(.32,.61);sx,sy=geometry.source_point(*point,original.shape,edited)
    measured=model.sample(edited,*point,'base')['base']
    expected=core.sample_patch(model.engine.corrected_input(original,model.params(edited)),sx,sy,10)
    assert np.allclose(measured,expected,atol=1e-6)
    window.travel(-1);idle();assert np.array_equal(model.preview(window.p)['pixels'],baseline)
    window.travel(1);idle();assert window.p['crop']==edited['crop']
    window.clipping_action.setChecked(True);window.canvas.set_clipping(True)
    assert window.canvas.clipping_item.isVisible()
    # View-only warning does not affect exported 16-bit values.
    dest=folder/'geometry.tif';window.export_to(str(dest));idle()
    exported=read_tiff(str(dest));expected=core.process(original,model.engine.prepare_params(model.params(edited)))
    assert exported.shape[:2]==geometry.output_shape(original.shape,edited)
    assert np.array_equal(exported,(np.clip(expected,0,1)*65535+.5).astype(np.uint16))
    assert json.loads(Path(str(dest)+'.json').read_text('utf-8'))['params']['crop']==edited['crop']
    project=folder/'geometry.filmproj';window.save_project_to(str(project));idle()
    window.reset_geometry();idle();window.open_project_path(str(project));idle()
    assert all(window.p[k]==edited[k] for k in ('rotation','straighten','crop'))
    assert np.array_equal(model.preview(window.p)['pixels'],positive)
    # Independent per-frame geometries survive projects and full-roll export.
    window.open_path(str(ROOT/'samples'/'pro-strip-demo.tif'));idle()
    window.keep_check.setChecked(False);window.meter_check.setChecked(False)
    window.apply_geometry({'rotation':90,'straighten':0.,'crop':[.1,.1,.9,.9]});idle()
    first=model.clone(window.p);window.go_frame(1);idle()
    window.apply_geometry({'rotation':180,'straighten':-3.,'crop':[0.,.1,1.,.9]});idle()
    second=model.clone(window.p);window.save_project_to(str(project));idle()
    window.open_project_path(str(project));idle()
    result=model.export_roll(folder,window.p,False,threading.Event(),lambda *args:None)
    rows=json.loads((Path(result['folder'])/'manifest.json').read_text('utf-8'))
    for i,p in enumerate((first,second)):
        assert all(rows[i]['params'][key]==p[key] for key in ('rotation','straighten','crop'))
        _,_,raw=__import__('strip').extract_one(model.engine.STATE['strip_img'],model.engine.STATE['strip_layout'],i)
        assert read_tiff(str(Path(result['folder'])/rows[i]['file'])).shape[:2]==geometry.output_shape(raw.shape,p)
    window.go_frame(0);idle();assert window.p['rotation']==90
    window.loaded=False;window.close();app.processEvents()
print('PASS geometry: float resampling, no fill edges, inverse coordinates, modal crop ratios, original alignment, undo/redo, source unchanged, exact 16-bit TIFF, recipe/project restore, independent roll exports and view-only clipping')
