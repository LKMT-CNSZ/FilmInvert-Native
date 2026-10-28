"""Wide input must survive processing and export without an sRGB roundtrip."""
from pathlib import Path
import os,sys,tempfile,time,json,copy
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import native_app as ui
import desktop_model as model
import loader,colorio,core,calibration,geometry
import numpy as np
from working_color import linear
from input_color import display_working,display_pixels
from tiffio import write_tiff_rgb16
from tiffread import read_tiff
import studio_worker
from studio_archive import backup,restore
app=ui.QApplication([])
(ROOT/'build'/'tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build'/'tests',prefix='wide-') as tmp:
    folder=Path(tmp)
    codes=np.zeros((40,160,3),np.float32)
    codes[:,:80]=[.1,.65,.15];codes[:,80:]=[.65,.1,.25]
    source=folder/'wide.tif';write_tiff_rgb16(source,codes,icc_profile=colorio.icc_profile('prophoto'))
    quant=read_tiff(str(source)).astype(np.float32)/65535.
    loaded=loader.load(str(source),'prophoto','prophoto')
    assert loaded.working_space=='prophoto'
    assert np.array_equal(loaded.linear,colorio.decode(quant,'prophoto'))
    auto=loader.load(str(source),'auto','prophoto')
    assert np.max(np.abs(auto.linear-loaded.linear))<.0001
    # Wide export matches source encoding, and differs materially from an sRGB detour.
    p=core.Params(mode='positive',black=0,white=1,working_space='prophoto',output_space='prophoto')
    out=core.process(loaded.linear,p,'prophoto')
    assert np.max(np.abs(out-quant))<.00001
    srgb=core.process(loaded.linear,p)
    detour=colorio.convert(srgb,'prophoto')
    assert np.max(np.abs(out-detour))>.05
    adobe_monitor=colorio.icc_profile('adobe_rgb')
    direct=display_working(out,'prophoto',adobe_monitor)
    clipped=display_pixels((srgb*255+.5).astype(np.uint8),adobe_monitor)
    assert np.max(np.abs(direct.astype(int)-clipped.astype(int)))>5
    for mode in core.MODES:
        for target in colorio.SPACES:
            params=core.Params(mode=mode,working_space='prophoto',output_space=target,base=[.7,.7,.7],curve=[[0,0],[.4,.3],[1,1]],hsl=[[.1,0]]+[[0,0]]*7)
            actual=core.process(loaded.linear,params,target)
            assert np.isfinite(actual).all() and actual.min()>=0 and actual.max()<=1
    # Calibration references are converted from supplied sRGB to the working primaries.
    rng=np.random.default_rng(46);refs=rng.uniform(40,210,(24,3))
    reference=[{'name':str(i),'rgb':rgb.tolist()} for i,rgb in enumerate(refs)]
    samples=linear(calibration.decode(refs/255.))
    profile=calibration.fit(reference,samples.tolist(),[0]*24,[[.5,.5]]*24,working_space='prophoto')
    assert profile['working_space']=='prophoto' and np.max(np.abs(np.array(profile['matrix'])-np.eye(3)))<.0001
    assert profile['report']['cv_rmse']<.01
    try:calibration.apply(loaded.linear,{'matrix':np.eye(3).tolist()},working_space='prophoto')
    except ValueError:pass
    else:raise AssertionError('old calibration accepted in wide workspace')
    window=ui.MainWindow(folder/'profile');errors=[];window.show_error=errors.append
    def idle():
        end=time.monotonic()+60
        while time.monotonic()<end:
            app.processEvents();time.sleep(.01)
            if window.job is None and not window.preview_timer.isActive() and not any(j.isRunning() for j in window.jobs):break
        else:raise AssertionError('Qt timeout')
        assert not errors,errors
    window.settings.setValue('inputColor','prophoto');window.wide_open_action.setChecked(True)
    window.open_path(str(source));idle()
    assert window.p['working_space']=='prophoto'
    window.reset_parameters();idle();assert window.p['working_space']=='prophoto'
    window.set_parameters(dict(window.p,mode='positive',black=0.,white=1.,output_space='prophoto'));idle()
    preview=model.preview(window.p);assert 'wide' in preview
    assert np.max(np.abs(preview['wide']-quant))<.00001
    snapshot=model.project_snapshot(window.p);assert snapshot['version']==4
    project=folder/'wide.filmproj';model.save_project(project,window.p)
    window.open_project_path(str(project));idle();assert window.p['working_space']=='prophoto'
    destination=folder/'wide-output.tif';window.export_to(str(destination));idle()
    assert np.max(np.abs(read_tiff(str(destination)).astype(int)-read_tiff(str(source)).astype(int)))<=1
    recipe=json.loads(Path(str(destination)+'.json').read_text('utf-8'));assert recipe['working_space']=='prophoto'
    spec={'mode':'strip','manual':{'rotation':0,'frames':[{'id':'a','box':[0,0,80,40]},{'id':'b','box':[80,0,160,40]}]}}
    result=model.reframe(spec,window.p);window.on_opened(result);idle()
    assert model.engine.STATE['native_input']['working_space']=='prophoto'
    window.go_frame(1);idle();assert window.p['working_space']=='prophoto'
    window.save_session();store=window.studio_store;asset=store.assets()[0]['id']
    job=store.enqueue([asset],folder/'queue',True)[0]
    assert studio_worker.execute(store,job)=='completed'
    for i in range(2):
        path=Path(store.get_job(job)['destination'])/f'frame-{i+1:04d}.tif'
        assert np.max(np.abs(read_tiff(str(path)).astype(int)-read_tiff(str(source))[:,i*80:(i+1)*80].astype(int)))<=1
    archive=folder/'wide.filmbackup';backup(store,archive);restore(store,archive)
    assert store.get_asset(asset)['snapshot']['version']==4
    # Changing workspaces uses a concrete backup and resets the new document.
    choose=ui.QInputDialog.getItem;question=ui.QMessageBox.question
    ui.QInputDialog.getItem=lambda *a,**k:('sRGB 兼容工作空间',True)
    ui.QMessageBox.question=lambda *a,**k:ui.QMessageBox.StandardButton.Yes
    try:window.change_working_space();idle()
    finally:ui.QInputDialog.getItem=choose;ui.QMessageBox.question=question
    saved=list((store.folder/'snapshots').glob('before-workspace-*.filmproj'))
    assert len(saved)==1 and model.read_project(saved[0])['version']==4
    assert window.p['working_space']=='srgb'
    window.open_project_path(str(saved[0]));idle();assert window.p['working_space']=='prophoto'
    window.loaded=False;window.close();app.processEvents()
print('PASS wide: direct ProPhoto input/export within 1 TIFF level, sRGB-detour loss detected, direct wide monitor transform, all modes/outputs finite, working-space chart fit/mismatch rejection, v4 project/backup, UI reset and switching backup, manual frames and exact two-frame queue export')
