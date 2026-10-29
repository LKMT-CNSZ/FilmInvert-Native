"""Real Qt worker merge, merged-source reopening, version gates and HDR export."""
from pathlib import Path
import os,sys,tempfile,time,copy,json
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import native_app as ui
import numpy as np
from unittest.mock import patch
from tiffio import write_tiff_rgb16
from tiffread import read_tiff_linear
import hdr_io

app=ui.QApplication.instance() or ui.QApplication([])
folder=ROOT/'build/hdr-tests';folder.mkdir(exist_ok=True,parents=True)
with tempfile.TemporaryDirectory(dir=folder) as tmp:
    root=Path(tmp);w=ui.MainWindow(root/'profile');w.show();errors=[];w.show_error=errors.append
    def idle():
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            app.processEvents();time.sleep(.01)
            merge=getattr(w,'hdr_merge_dialog',None)
            if (merge is None or merge.worker is None) and w.job is None and not w.pending_command and not w.preview_timer.isActive() and not any(j.isRunning() for j in w.jobs):break
        else:raise AssertionError('HDR UI timeout')
        assert not errors,errors
    y,x=np.mgrid[:96,:128];scene=np.stack([.03+2*x/128,.05+y/96,.1+(x+y)/120],axis=-1).astype(np.float32)
    files=[]
    for i,ev in enumerate((-2,0,2)):
        path=root/f'bracket-{i}.tif';write_tiff_rgb16(path,np.clip(scene*2.**ev,0,1),with_icc=False);files.append(str(path))
    w.open_hdr_merge();d=w.hdr_merge_dialog
    with patch.object(ui.QFileDialog,'getOpenFileNames',return_value=(files,'')):d.choose()
    assert d.table.rowCount()==3
    for i,ev in enumerate((-2,0,2)):d.table.cellWidget(i,1).setValue(ev)
    d.kind.setCurrentIndex(d.kind.findData('positive'));d.align.setChecked(False);d.ghost.setChecked(False)
    dest=root/'merged.tif'
    with patch.object(ui.QFileDialog,'getSaveFileName',return_value=(str(dest),'')):d.run()
    assert d.worker is not None and not d.start.isEnabled()
    idle();assert dest.exists() and w.loaded and w.p['scene_hdr'] and w.p['mode']=='positive'
    assert np.max(read_tiff_linear(dest))>1.5
    w.reset_parameters();idle()
    assert w.p['meter_mode']=='edges' and w.p['scene_hdr'] and w.p['mode']=='positive'
    w.edit_parameter('tone_shadows',.25,True);w.edit_parameter('local_tone',.2,True);idle()
    recipe=ui.model.recipe(w.p);assert recipe['version']==6
    assert ui.model.recipe_params(recipe)['local_tone']==.2
    snapshot=ui.model.project_snapshot(w.p);assert snapshot['version']==8
    project=root/'merged.filmproj';ui.model.write_json(project,snapshot)
    loaded=ui.model.read_project(project);assert loaded['frames']['0']['local_tone']==.2
    legacy=copy.deepcopy(snapshot);legacy['version']=7;ui.model.write_json(root/'wrong.filmproj',legacy)
    try:ui.model.read_project(root/'wrong.filmproj')
    except ValueError:pass
    else:raise AssertionError('Old project version silently accepted new HDR parameters')
    ui.model.export_hdr(root/'master.tif',w.p)
    assert read_tiff_linear(root/'master.tif').max()>1
    assert hdr_io.metadata(root/'master.tif')['sdr_rolloff_bypassed']
    # The actual toolbar and the HDR menu share the save dialog. Format selection
    # must control the encoder, even when the canvas uses the opposite display.
    before=copy.deepcopy(w.p)
    hf='HDR TIFF - Float32 (*.tif *.tiff)';sf='SDR TIFF - 16bit (*.tif *.tiff)'
    with patch.object(ui.QFileDialog,'getSaveFileName',return_value=(str(root/'toolbar-hdr'),hf)) as save:
        w.export_button.click();idle()
        assert hf in save.call_args.args[3] and save.call_args.args[4]==sf
    assert read_tiff_linear(root/'toolbar-hdr.tif').max()>1
    assert hdr_io.metadata(root/'toolbar-hdr.tif')['sdr_rolloff_bypassed']
    assert w.last_export==str(root/'toolbar-hdr.tif') and Path(w.output_edit.text())==root
    w.hdr_canvas.active=True
    try:
        with patch.object(ui.QFileDialog,'getSaveFileName',return_value=(str(root/'selected-sdr.tiff'),sf)) as save:
            w.export_dialog();idle()
            assert save.call_args.args[4]=='Windows HDR - JPEG XR (*.jxr)' and '-hdr.jxr' in save.call_args.args[2]
    finally:w.hdr_canvas.active=False
    assert read_tiff_linear(root/'selected-sdr.tiff').max()<=1
    with patch.object(ui.QFileDialog,'getSaveFileName',return_value=('',hf)),patch.object(w,'command') as command:
        w.export_dialog();assert not command.called
    with patch.object(ui.QFileDialog,'getSaveFileName',return_value=(str(root/'menu-hdr.tif'),hf)) as save:
        w.hdr_export_dialog();idle();assert save.call_args.args[4]==hf
    assert read_tiff_linear(root/'menu-hdr.tif').max()>1 and w.p==before
    with patch.object(ui.QFileDialog,'getSaveFileName',return_value=(str(root/'windows-view.tif'),'Windows HDR - JPEG XR (*.jxr)')):
        w.export_button.click();idle()
    import hdr_jxr
    import hdr_color,working_color
    p=ui.model.engine.prepare_params(ui.model.params(w.p))
    expected=working_color.linear(hdr_color.render(ui.model.engine.STATE['full'],p),p.working_space,'srgb')*(203/80)
    actual=hdr_jxr.read_scrgb(root/'windows-view.jxr')
    assert actual.shape==expected.shape and np.max(np.abs(actual-expected))<.003
    assert w.last_export==str(root/'windows-view.jxr') and w.p==before
    # Native HDR is explicitly unavailable in offscreen, never silently emulated in SDR.
    w.open_hdr_preview();idle();app.processEvents()
    assert w.hdr_preview_dialog.canvas.failed
    assert 'Windows' in w.hdr_preview_dialog.message.text()
    w.hdr_preview_dialog.close()
    # Unsaved frames inherit the document's metering generation, including old
    # projects without a roll template; both UI and frozen-queue paths agree.
    source=root/'two-frames.tif';write_tiff_rgb16(source,np.tile(np.clip(scene*.2,.01,.9),(1,2,1)),with_icc=False)
    manual={'rotation':0,'frames':[{'id':'a','box':[0,0,128,96]},{'id':'b','box':[128,0,256,96]}]}
    opened=ui.model.open_image(source,input_mode='strip',manual=manual)
    snap=ui.model.project_snapshot(opened['params']);snap['roll_params']=None
    ui.model.restore_layout(snap)
    import studio_worker
    assert studio_worker.frame_parameters(snap,1)[1].meter_mode=='edges'
    assert ui.model.change_frame(1,opened['params'],False,True)['params']['meter_mode']=='edges'
    snap['version']=2
    for frame in snap['frames'].values():frame['meter_mode']='legacy'
    ui.model.restore_layout(snap)
    assert studio_worker.frame_parameters(snap,1)[1].meter_mode=='legacy'
    assert ui.model.change_frame(1,snap['frames']['0'],False,True)['params']['meter_mode']=='legacy'
    # Syncing active tone controls into a legacy snapshot upgrades its format.
    from batch_engine import sync_snapshot
    ref=dict(snap['frames']['0'],tone_shadows=.3)
    synced=sync_snapshot(snap,snap,ref,['tone'],'absolute',0,0)
    assert synced['version']==8 and synced['frames']['0']['tone_shadows']==.3
    w.loaded=False;w.close();app.processEvents()
print('PASS Qt merge worker/EV controls, linear HDR reopen, tone/history/project/recipe gates, HDR master headroom, and honest offscreen HDR-unavailable state')
