"""HDR creative/export consistency, version gates, per-display mapping and GPU routing."""
from pathlib import Path
import os,sys,tempfile,json,copy,time
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'vendor')]
import numpy as np,core,hdr_color,hdr_io,hdr_jxr,hdr_display,desktop_model as model
import calibration
from unittest.mock import patch
import native_app as ui
from native_hdr_workbench import HDRHistogram,range_colors,DisplayDialog

# Independent luminance/ratio properties, inactive identity, output headroom and SDR mapping.
x=np.geomspace(.01,4,200,dtype=np.float32)[None,:,None]*np.ones((12,1,3),np.float32)
old=core.Params(mode='positive',black=0,white=1)
legacy={k:v for k,v in old.__dict__.items() if not k.startswith(('hdr_','sdr_hdr_'))}
assert calibration.parameter_signature(old.__dict__)==calibration.parameter_signature(legacy)
assert calibration.parameter_signature(dict(old.__dict__,hdr_enabled=True,hdr_strength=3))==calibration.parameter_signature(legacy)
assert hdr_color.grade(x,old) is x
p=core.Params(mode='positive',black=0,white=1,hdr_enabled=True,hdr_strength=2,hdr_limit=3)
y=hdr_color.grade(x,p);assert np.array_equal(y[:,x[0,:,0]<=.5],x[:,x[0,:,0]<=.5])
assert np.all(np.diff(y[0,:,0])>0) and y.max()<8 and y.max()>4
color=x*np.array([1,.8,.6],np.float32);graded=hdr_color.grade(color,p)
assert np.allclose(graded[...,1]/graded[...,0],.8) and np.allclose(graded[...,2]/graded[...,0],.6)
sdr=hdr_color.sdr_rendition(y,p);assert np.isfinite(sdr).all() and sdr.max()<=1
assert np.all(np.diff(sdr[0,:,0])>0)
for space in ('srgb','prophoto'):
    q=copy.deepcopy(p);q.working_space=space
    assert np.isfinite(core.process(color,q,space)).all()

info=dict(name='display-A',monitor=1,hdr_active=True,bits=10,max_nits=600,sdr_white_nits=150)
assert np.allclose(hdr_display.display_mapping(info),(150,600,2))
assert np.allclose(hdr_display.display_mapping(info,200,800),(200,800,2))
assert hdr_display.display_mapping(dict(info,max_nits=26,sdr_white_nits=203))[1]==1000
assert hdr_display.display_mapping(info,400,300)==(300,300,0)
for white,peak in ((float('nan'),600),(79,600),(150,10001)):
    try:hdr_display.display_mapping(info,white,peak)
    except ValueError:pass
    else:raise AssertionError('Invalid display overrides accepted')
assert hdr_display.C.sizeof(hdr_display.ConfigPath)==72

# Enumerate a non-default GPU/monitor and prove unrelated references are released.
released=[];C=hdr_display.C
def fake_call(obj,index,result,args,*values):
    ident=obj.value
    if ident==1 and index==12:
        i=values[0]
        if i>1:return -2005270526
        C.cast(values[1],C.POINTER(hdr_display.P))[0]=hdr_display.P(10+i);return 0
    if ident in (10,11) and index==7:
        if values[0]>0:return -2005270526
        C.cast(values[1],C.POINTER(hdr_display.P))[0]=hdr_display.P(20+ident-10);return 0
    if ident in (20,21) and index==7:
        C.cast(values[0],C.POINTER(hdr_display.OutputBasic)).contents.Monitor=ident-19;return 0
    raise AssertionError((ident,index))
with patch.object(hdr_display,'window_monitor',return_value=2),patch.object(hdr_display,'call',side_effect=fake_call),patch.object(hdr_display,'release',side_effect=lambda obj:released.append(obj.value) if obj else None):
    adapter,output,monitor=hdr_display.select_output(hdr_display.P(1),99)
assert (adapter.value,output.value,monitor)==(11,21,2) and 10 in released and 20 in released and 11 not in released and 21 not in released

app=ui.QApplication.instance() or ui.QApplication([])
folder=ROOT/'build/hdr-workbench-tests';folder.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=folder) as tmp:
    root=Path(tmp);source=root/'source.tif';hdr_io.write(source,x)
    window=ui.MainWindow(root/'profile');window.show();errors=[];window.show_error=errors.append
    def idle():
        deadline=time.monotonic()+35
        while time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
            if window.job is None and not window.preview_timer.isActive() and not window.preview_pending and not any(j.isRunning() for j in window.jobs):break
        else:raise AssertionError('HDR workbench timeout')
        assert not errors,errors
    window.open_path(str(source));idle();before=copy.deepcopy(window.p)
    window.hdr_check.setChecked(True);idle();assert window.p['hdr_enabled'] and window.rows['hdr_strength'].isEnabled()
    window.edit_parameter('hdr_strength',1.5,True);idle();params=copy.deepcopy(window.p)
    preview=model.hdr_edit_preview(params,True)['hdr_linear'];assert preview.max()>1
    model.export_hdr(root/'master.tif',params)
    from tiffread import read_tiff_linear
    master=read_tiff_linear(root/'master.tif');assert np.allclose(master,preview,rtol=2e-6,atol=2e-6)
    model.export_hdr_view(root/'view.jxr',params);decoded=hdr_jxr.read_scrgb(root/'view.jxr')
    assert np.allclose(decoded,preview*(203/80),atol=.01,rtol=.005)
    sdr_image=core.process(model.engine.STATE['full'],model.engine.prepare_params(model.params(params)))
    expected=hdr_color.encode(hdr_color.sdr_rendition(preview,model.params(params)))
    assert np.allclose(sdr_image,np.clip(expected,0,1),atol=1e-6)
    import viewport_render
    roi=viewport_render.render(model.engine.STATE['full'],model.params(params),[20,2,100,9])[0]
    assert np.allclose(roi,sdr_image[2:9,20:100],atol=1e-6)
    for space in ('srgb','prophoto'):
        q=model.params(dict(params,working_space=space,rotation=90,crop=[.1,.1,.9,.9],flip=True))
        full=core.process(model.engine.STATE['full'],q,space)
        region=viewport_render.render(model.engine.STATE['full'],q,[1,10,7,50])[0]
        assert np.array_equal(region,full[10:50,1:7])
    snap=model.project_snapshot(params);assert snap['version']==9
    from batch_engine import sync_snapshot
    reference=dict(params,hdr_strength=2.)
    # Group synchronization must retain the newer project format.
    synced=sync_snapshot(snap,snap,reference,['tone'],'absolute',0,0)
    assert synced['version']==9 and synced['frames']['0']['hdr_strength']==2.
    project=root/'edit.filmproj';model.save_project(project,params);loaded=model.read_project(project);assert loaded['frames']['0']['hdr_strength']==1.5
    recipe=model.recipe(params);assert recipe['version']==7 and model.recipe_params(recipe)['hdr_enabled']
    invalid=copy.deepcopy(recipe);invalid['version']=6
    try:model.recipe_params(invalid)
    except ValueError:pass
    else:raise AssertionError('Legacy recipe silently accepted HDR grading')
    invalid=copy.deepcopy(snap);invalid['version']=8;model.write_json(root/'old.filmproj',invalid)
    try:model.read_project(root/'old.filmproj')
    except ValueError:pass
    else:raise AssertionError('Legacy project silently accepted HDR grading')
    with patch.object(hdr_display,'inspect_display',return_value=info):
        with patch.object(hdr_display,'inspect_display',side_effect=RuntimeError('display disconnected')):
            dialog=DisplayDialog(window);assert not dialog.white_auto.isEnabled() and not dialog.peak_auto.isEnabled()
        dialog.refresh();dialog.show();app.processEvents();assert dialog.white_auto.isEnabled() and dialog.peak_auto.isEnabled()
        dialog.white_auto.setChecked(False);dialog.white.setValue(200);dialog.peak_auto.setChecked(False);dialog.peak.setValue(800)
        stored=json.loads(window.settings.value('hdrDisplayProfilesV2'))['display-A'];assert stored==dict(white=200,peak=800)
        assert window.p==params
        dialog.close()
    hist=HDRHistogram();hist.set_data(preview,2);assert hist.bins.sum()==preview.shape[0]*preview.shape[1] and hist.maximum>0
    colors=range_colors(np.array([[[.2,.2,.2],[2,1,1],[8,1,1]]],np.float32),4)
    assert np.allclose(colors[0,1],[.85,.7,0]) and np.allclose(colors[0,2],[1,.03,.03])
    window.travel(-1);idle();assert window.p['hdr_strength']!=1.5
    window.close();app.processEvents()
print('PASS HDR master/SDR/JXR, creative parameters/version gates, per-display overrides, histogram ranges and non-default GPU routing')
