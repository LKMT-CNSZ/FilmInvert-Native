"""High precision ICC input, legacy projects, display-only transforms and queued export."""
from pathlib import Path
import os,sys,tempfile,time,json
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import native_app as ui
import desktop_model as model
import loader,colorio,core
import numpy as np
from PIL import Image
from input_color import managed_load,named_linear,icc_linear,display_pixels,profile_space
from tiffio import write_tiff_rgb16
from tiffread import read_tiff
from studio_store import StudioStore
import studio_worker
app=ui.QApplication([])
(ROOT/'build'/'tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build'/'tests',prefix='input-color-') as tmp:
    folder=Path(tmp);ramp=np.linspace(.0005,.8,2048,dtype=np.float32)
    pixels=np.repeat(ramp[None,:,None],3,axis=2);pixels=np.repeat(pixels,3,axis=0)
    for space in colorio.SPACES:
        path=folder/(space+'.tif');write_tiff_rgb16(path,pixels,icc_profile=colorio.icc_profile(space))
        codes=read_tiff(str(path)).astype(np.float32)/65535.
        actual=loader.load(str(path),'auto');expected=np.clip(named_linear(codes,space),0,1)
        assert np.max(np.abs(actual.linear-expected))<.00015,(space,np.max(np.abs(actual.linear-expected)))
        assert len(np.unique(actual.linear[0,:,0]))>2000,'16-bit detail reduced'
        manual=loader.load(str(path),space);assert np.array_equal(manual.linear,expected)
        assert actual.color_info['icc_sha256'] and actual.color=='auto'
    # Manual selection overrides a misleading embedded tag, including linear scans.
    source=folder/'srgb.tif';linear=loader.load(str(source),'linear_srgb')
    assert np.array_equal(linear.linear,read_tiff(str(source)).astype(np.float32)/65535.)
    assert np.max(np.abs(linear.linear-loader.load(str(source),'auto').linear))>.2
    png=folder/'tagged.png';Image.new('RGB',(21,17),(60,110,140)).save(png,icc_profile=colorio.icc_profile('adobe_rgb'))
    managed=loader.load(str(png),'auto');assert managed.color_info['icc_sha256']
    plain=folder/'untagged.tif';write_tiff_rgb16(plain,pixels,with_icc=False)
    assert np.array_equal(loader.load(str(plain),'auto').linear,loader.load(str(plain)).linear)
    assert '假定' in loader.load(str(plain),'auto').color_info['encoding']
    saturated=folder/'prophoto-blue.tif';blue=np.zeros((12,15,3),np.float32);blue[...,2]=1
    write_tiff_rgb16(saturated,blue,icc_profile=colorio.icc_profile('prophoto'))
    assert loader.load(str(saturated),'auto').color_info['clipped_fraction']==1
    window=ui.MainWindow(folder/'profile');errors=[];window.show_error=errors.append
    def idle():
        end=time.monotonic()+60
        while time.monotonic()<end:
            app.processEvents();time.sleep(.01)
            if window.job is None and not window.preview_timer.isActive() and not any(j.isRunning() for j in window.jobs):break
        else:raise AssertionError('Qt timeout')
        assert not errors,errors
    window.settings.setValue('inputColor','auto');window.open_path(str(source));idle()
    snapshot=model.project_snapshot(window.p);assert snapshot['version']==8 and snapshot['input']['color']=='auto'
    project=folder/'managed.filmproj';model.save_project(project,window.p)
    before=model.preview(window.p)['pixels'].copy();restored=model.open_project(project)
    assert np.array_equal(before,model.preview(restored['params'])['pixels'])
    # Reframing must retain the decoding choice and project v3.
    model.reframe({'mode':'single','manual':None},window.p)
    assert model.engine.STATE['native_input']['color']=='auto'
    model.restore_layout(snapshot);assert model.engine.STATE['native_input']['color']=='auto'
    # Invalid embedded ICC must leave the current document intact.
    bad=folder/'bad.tif';write_tiff_rgb16(bad,pixels,icc_profile=b'broken')
    signature=model.project_signature(model.project_snapshot(window.p))
    try:model.open_image(str(bad),input_color='auto')
    except ValueError:pass
    else:raise AssertionError('bad ICC silently accepted')
    assert model.project_signature(model.project_snapshot(window.p))==signature
    try:profile_space(b'broken',True)
    except ValueError:pass
    else:raise AssertionError('bad display profile accepted')
    # Old v1/v2 projects deliberately preserve their legacy interpretation.
    old=model.open_image(str(source),input_color='legacy',input_mode='single')
    old['params']['meter_mode']='legacy'
    for saved in model.engine.STATE['frame_params'].values():saved['meter_mode']='legacy'
    if model.engine.STATE['roll_params']:model.engine.STATE['roll_params']['meter_mode']='legacy'
    oldproject=folder/'old.filmproj';model.save_project(oldproject,old['params'])
    assert model.read_project(oldproject)['version']==2
    model.open_project(oldproject);assert np.array_equal(model.engine.STATE['full'],linear.linear)
    window.settings.setValue('inputColor','auto');window.open_path(str(source));idle()
    signature=model.project_signature(model.project_snapshot(window.p));raw=window.canvas.pixels.copy()
    icc=folder/'display.icc';icc.write_bytes(colorio.icc_profile('adobe_rgb'))
    original_dialog=ui.QFileDialog.getOpenFileName
    ui.QFileDialog.getOpenFileName=lambda *a,**k:(str(icc),'')
    try:window.choose_monitor();idle()
    finally:ui.QFileDialog.getOpenFileName=original_dialog
    assert window.monitor_profile and np.array_equal(raw,window.canvas.pixels)
    window.proof_action.setChecked(True);window.request_preview();idle()
    assert model.project_signature(model.project_snapshot(window.p))==signature
    assert np.array_equal(raw,window.canvas.pixels)
    window.clear_monitor();idle()
    # Queue uses the saved input decoding in a separate processing context.
    window.save_session();store=window.studio_store;identifier=store.assets()[0]['id']
    job=store.enqueue([identifier],folder/'exports',False)[0]
    expected=(core.process(model.engine.STATE['full'],model.params(window.p))*65535+.5).astype(np.uint16)
    assert studio_worker.execute(store,job)=='completed'
    output=Path(store.get_job(job)['destination'])/'frame-0001.tif'
    assert np.array_equal(read_tiff(str(output)),expected)
    recipe=json.loads(Path(str(output)+'.json').read_text('utf-8'))
    assert recipe['input_color']=='auto' and recipe['input_color_report']['icc_sha256']
    # Actual reinterpret command backs up the old document before resetting edits.
    choose=ui.QInputDialog.getItem;question=ui.QMessageBox.question
    from input_color import CHOICES
    ui.QInputDialog.getItem=lambda *a,**k:(CHOICES['linear_srgb'],True)
    ui.QMessageBox.question=lambda *a,**k:ui.QMessageBox.StandardButton.Yes
    try:window.change_input_color();idle()
    finally:ui.QInputDialog.getItem=choose;ui.QMessageBox.question=question
    backups=list((store.folder/'snapshots').glob('before-input-color-*.filmproj'))
    assert len(backups)==1 and model.engine.STATE['native_input']['color']=='linear_srgb'
    assert model.read_project(backups[0])['input']['color']=='auto'
    window.open_project_path(str(backups[0]));idle()
    assert model.engine.STATE['native_input']['color']=='auto'
    window.loaded=False;window.close();app.processEvents()
print('PASS input color: sRGB/Adobe/ProPhoto ICC and explicit encoding, >2000 distinct 16-bit levels, untagged legacy fallback, gamut report, invalid ICC rollback, v3 project/reframe/queue propagation, v1/v2 compatibility, native monitor/proof view leaves exports unchanged, exact queued TIFF')
