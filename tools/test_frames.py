"""Frame-layout acceptance using cached originals, projects and exact TIFF exports."""
from pathlib import Path
import copy
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
import strip
import desktop_model as model
import native_app as ui
from tiffio import write_tiff_rgb16
from tiffread import read_tiff

app=ui.QApplication([])
(ROOT/'build'/'tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build'/'tests',prefix='frames-') as folder:
    folder=Path(folder);window=ui.MainWindow(folder/'profile');errors=[];window.show_error=errors.append
    def idle():
        end=time.monotonic()+60
        while time.monotonic()<end:
            app.processEvents();time.sleep(.01)
            if window.job is None and not window.pending_command and not window.preview_timer.isActive() and not any(j.isRunning() for j in window.jobs):break
        else:raise AssertionError('Worker timed out')
        assert not errors,errors
    # Preserve two independent edits and reset geometry only on a changed source box.
    window.open_path(str(ROOT/'samples'/'pro-strip-demo.tif'));idle()
    source=model.engine.STATE['native_loaded'];identity=copy.deepcopy(model.engine.STATE['native_source'])
    assert window.info['strip']['frames']==6
    window.keep_check.setChecked(False);window.meter_check.setChecked(False)
    window.edit_parameter('exposure',.35,True);window.apply_geometry({'rotation':90,'straighten':1.,'crop':[.1,.1,.9,.9]});idle()
    window.go_frame(2);idle();window.edit_parameter('exposure',-.4,True);idle()
    original=model.project_snapshot(window.p,window.project_options())
    old_signature=model.project_signature(original)
    preview=model.layout_editor();rows=copy.deepcopy(preview['frames']);rows[0]['box'][0]+=7
    rows=[rows[2],rows[0],rows[1],rows[3],rows[4]]
    extra=copy.deepcopy(rows[-1]);extra['id']='extra-frame';extra['box'][1]+=10;rows.append(extra)
    spec={'mode':'strip','manual':{'rotation':preview['rotation'],'frames':rows}}
    window.apply_frames(spec);idle()
    assert model.engine.STATE['native_loaded'] is source and model.engine.STATE['native_source']==identity
    assert window.info['strip']['manual'] and window.info['strip']['index']==0
    assert window.p['exposure']==-.4 and model.engine.STATE['frame_params'][1]['exposure']==.35
    assert model.engine.STATE['frame_params'][1]['crop']==[0.,0.,1.,1.] and model.engine.STATE['frame_params'][1]['rotation']==0
    assert window.layout_undo_action.isEnabled()
    project=folder/'手动工程.filmproj';window.save_project_to(str(project));idle()
    data=model.read_project(project);assert data['version']==8 and data['input']==spec and not window.project_dirty
    before=model.export_roll(folder,window.p,False,threading.Event(),lambda *args:None)
    manifest=json.loads((Path(before['folder'])/'manifest.json').read_text('utf-8'))
    raw=source.linear
    for i,row in enumerate(manifest):
        box=rows[i]['box'];x0,y0,x1,y1=box
        assert row['source_region']=={'id':rows[i]['id'],'box':box,'rotation':preview['rotation']}
        cut=raw[y0:y1,x0:x1]
        cut=np.rot90(cut,-preview['rotation']//90) if preview['rotation'] else cut
        expected=core.process(cut,model.params(row['params']))
        actual=read_tiff(str(Path(before['folder'])/row['file']))
        assert np.array_equal(actual,(expected*65535+.5).astype(np.uint16)),i
        recipe=json.loads(Path(str(Path(before['folder'])/row['file'])+'.json').read_text('utf-8'))
        assert recipe['source_region']==row['source_region']
    window.restore_frames();idle()
    assert model.project_signature(model.project_snapshot(window.p,window.project_options()))==old_signature
    assert model.engine.STATE['frame_params'][0]['crop']==[.1,.1,.9,.9]
    window.open_project_path(str(project));idle()
    assert model.engine.STATE['native_input']==spec and not window.project_dirty
    restored=model.export_roll(folder,window.p,False,threading.Event(),lambda *args:None)
    again=json.loads((Path(restored['folder'])/'manifest.json').read_text('utf-8'))
    for a,b in zip(manifest,again):
        assert a==b
        assert np.array_equal(read_tiff(str(Path(before['folder'])/a['file'])),read_tiff(str(Path(restored['folder'])/b['file'])))

    # Automatic session restores manual layout, order, frame and all parameters.
    window.save_session();window.close();app.processEvents()
    window=ui.MainWindow(folder/'profile');window.show_error=errors.append;window.restore_session();idle()
    assert model.engine.STATE['native_input']==spec and window.p['exposure']==-.4
    assert model.engine.STATE['frame_params'][1]['exposure']==.35
    def rejected(fn):
        full=model.engine.STATE['full'];snapshot=model.project_snapshot(window.p,window.project_options())
        try:fn()
        except ValueError:pass
        else:raise AssertionError('Invalid manual layout was accepted')
        assert model.engine.STATE['full'] is full
        assert model.project_signature(model.project_snapshot(window.p,window.project_options()))==model.project_signature(snapshot)
    bad=copy.deepcopy(spec);bad['manual']['frames'][0]['box'][2]=raw.shape[1]+1
    rejected(lambda:model.reframe(bad,window.p))
    broken=copy.deepcopy(data);broken['input']=bad;invalid=folder/'越界工程.filmproj';model.write_json(invalid,broken)
    rejected(lambda:model.open_project(invalid))
    for frames in ([{'id':'same','box':[0,0,10,10]}]*2,[{'id':'n','box':[0,0,1,4]}],
                   [{'id':'n','box':[0,0,float('nan'),4]}]):
        rejected(lambda:strip.normalize_manual({'rotation':0,'frames':frames}))

    # Workbench: edit, add/remove, split, reorder, undo, mode switch and cancel.
    window.open_frames();idle();dialog=window.frame_dialog;assert dialog.isModal()
    initial=copy.deepcopy(dialog.rows);dialog.add_frame();assert len(dialog.rows)==7
    dialog.remove_frame();assert dialog.rows==initial
    dialog.grid_rows.setValue(1);dialog.grid_cols.setValue(2);dialog.divide();assert len(dialog.rows)==7
    dialog.travel(-1);assert dialog.rows==initial
    assert dialog.frames.currentRow()==1  # Undo restores the selected frame with its reference set.
    dialog.frames.setCurrentRow(0)
    dialog.move_frame(1);assert dialog.rows[1]['id']==initial[0]['id']
    manual=copy.deepcopy(dialog.rows);dialog.mode.setCurrentIndex(dialog.mode.findData('single'))
    dialog.mode.setCurrentIndex(dialog.mode.findData('auto'));dialog.mode.setCurrentIndex(dialog.mode.findData('manual'))
    assert dialog.rows==manual
    signature=model.project_signature(model.project_snapshot(window.p,window.project_options()))
    dialog.reject();app.processEvents()
    assert model.project_signature(model.project_snapshot(window.p,window.project_options()))==signature

    # Explicit single mode overrides auto-detection and survives v2 projects.
    window.open_mode.setCurrentIndex(window.open_mode.findData('single'))
    window.open_path(str(ROOT/'samples'/'pro-strip-demo.tif'));idle()
    assert window.info['strip'] is None and model.engine.STATE['full'].shape==source.linear.shape
    window.save_project_to(str(project));idle();assert model.read_project(project)['version']==8
    window.open_mode.setCurrentIndex(window.open_mode.findData('auto'))
    window.open_path(str(ROOT/'samples'/'pro-light-demo.tif'));idle();window.open_project_path(str(project));idle()
    assert window.info['strip'] is None and model.engine.STATE['native_input']['mode']=='single'

    # A whole-frame manual box can keep geometry but cannot inherit a reference
    # flat field into a strip pipeline. Restoring the previous state recovers it.
    window.open_mode.setCurrentIndex(window.open_mode.findData('single'))
    window.open_path(str(ROOT/'samples'/'pro-light-demo.tif'));idle()
    window.set_parameters(model.flat_reference(window.p,ROOT/'samples'/'pro-flat-reference.tif'));idle()
    before_flat=copy.deepcopy(window.p['flat_field'])
    h,w=model.engine.STATE['full'].shape[:2]
    window.apply_frames({'mode':'strip','manual':{'rotation':0,'frames':[{'id':'frame-0','box':[0,0,w,h]}]}});idle()
    assert window.p['flat_mode']=='off'
    assert np.allclose(window.p['base'],core.auto_base(model.engine.STATE['native_loaded'].linear))
    window.restore_frames();idle();assert window.p['flat_mode']=='reference' and window.p['flat_field']==before_flat

    # Horizontal scan with two manually split regions; exact full-source pixels.
    yy,xx=np.mgrid[:96,:280];synthetic=np.stack([.12+.3*xx/280,.08+.2*yy/96,.03+.1*xx/280],axis=2).astype(np.float32)
    horizontal=folder/'横向扫描.tif';write_tiff_rgb16(str(horizontal),synthetic)
    window.open_mode.setCurrentIndex(window.open_mode.findData('strip'));window.open_path(str(horizontal));idle()
    assert window.info['strip'] and window.info['strip']['frames']>=1
    window.open_frames();idle();dialog=window.frame_dialog
    dialog.mode.setCurrentIndex(dialog.mode.findData('manual'));dialog.rotation.setCurrentIndex(dialog.rotation.findData(0))
    dialog.grid_rows.setValue(1);dialog.grid_cols.setValue(2);dialog.gap.setValue(8);dialog.divide();dialog.apply_result();idle()
    assert window.info['strip']['frames']==2
    rows=model.engine.STATE['native_input']['manual']['frames']
    assert rows[0]['box']==[0,0,136,96] and rows[1]['box']==[144,0,280,96]
    assert model.engine.STATE['full'].shape==(96,136,3)
    before_signature=model.calibration_preview(window.p)['parameter_signature']
    small=copy.deepcopy(model.engine.STATE['native_input']);small['manual']['frames'][0]['box'][0]=2
    window.apply_frames(small);idle()
    assert model.calibration_preview(window.p)['parameter_signature']!=before_signature
    # Cached source survives all layout edits; the physical original file is intact.
    assert model.source_identity(horizontal)['sha256']==model.engine.STATE['native_source']['sha256']
    window.loaded=False;window.close();app.processEvents()
print('PASS frames: explicit modes, cached originals, stable-id edit retention, changed-box geometry reset, layout undo, v2 project/session, exact ordered TIFF and source-region recipes, rollback, modal editing/grid/reorder/cancel, horizontal scans and calibration provenance')
