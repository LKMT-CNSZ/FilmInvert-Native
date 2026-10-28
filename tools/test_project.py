"""Verify full-roll persistence, relinking and failure rollback against real exports."""
from pathlib import Path
import os
import sys
import json
import tempfile
import shutil
import threading
import time
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import native_app as ui
import desktop_model as model
import numpy as np
from tiffread import read_tiff

app=ui.QApplication([])
(ROOT/'build'/'tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build'/'tests',prefix='project-') as folder:
    folder=Path(folder);profile=folder/'profile';window=ui.MainWindow(profile)
    errors=[];window.show_error=errors.append
    def idle():
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            app.processEvents();time.sleep(.01)
            if window.job is None and not window.pending_command and not window.preview_timer.isActive() and not any(j.isRunning() for j in window.jobs):break
        else:raise AssertionError('Worker timed out')
        assert not errors,errors
    def fails(fn,contains):
        full=model.engine.STATE['full'];frames=model.clone(model.engine.STATE['frame_params'])
        source=model.engine.STATE['path'];index=model.engine.STATE['frame_index']
        try:fn()
        except (ValueError,FileNotFoundError) as error:assert contains in str(error),str(error)
        else:raise AssertionError('Invalid project was accepted')
        assert model.engine.STATE['full'] is full
        assert model.engine.STATE['path']==source and model.engine.STATE['frame_index']==index
        assert model.engine.STATE['frame_params']==frames

    # Roll template and multiple independent edits, including a final UI edit whose
    # preview has not yet run when Save is requested.
    source=folder/'底片原图.tif';shutil.copy2(ROOT/'samples'/'pro-strip-demo.tif',source)
    window.open_path(str(source));idle()
    window.edit_parameter('exposure',.2,True);window.apply_roll();idle()
    window.keep_check.setChecked(False);window.meter_check.setChecked(False)
    window.edit_parameter('exposure',.55,True)
    window.go_frame(2);idle();window.set_curve([[0,0],[.3,.2],[.7,.8],[1,1]])
    window.edit_parameter('exposure',-.35,True);idle()
    window.go_frame(4);idle();window.edit_parameter('exposure',.85,True)
    project=folder/'整卷工程.filmproj';window.save_project_to(str(project));idle()
    assert not window.project_dirty
    data=model.read_project(project)
    assert data['current_frame']==4 and data['frame_count']==6
    assert data['frames']['0']['exposure']==.55 and data['frames']['2']['exposure']==-.35
    assert data['frames']['4']['exposure']==.85 and data['roll_params']['exposure']==.2
    assert data['options']=={'keep_params':False,'auto_meter':False}
    assert data['source']['relative_path']==source.name
    before=model.export_roll(folder,window.p,False,threading.Event(),lambda *a:None)
    before_manifest=json.loads((Path(before['folder'])/'manifest.json').read_text('utf-8'))
    first_thumb=model.thumbnails()[0]
    window.open_path(str(ROOT/'samples'/'pro-light-demo.tif'));idle()
    window.open_project_path(str(project));idle()
    assert not window.project_dirty
    assert window.info['strip']['index']==4 and window.p['exposure']==.85
    assert not window.keep_check.isChecked() and not window.meter_check.isChecked()
    assert model.thumbnails()[0]==first_thumb
    after=model.export_roll(folder,window.p,False,threading.Event(),lambda *a:None)
    after_manifest=json.loads((Path(after['folder'])/'manifest.json').read_text('utf-8'))
    assert [r['params'] for r in before_manifest]==[r['params'] for r in after_manifest]
    for old,new in zip(before_manifest,after_manifest):
        assert np.array_equal(read_tiff(str(Path(before['folder'])/old['file'])),
                              read_tiff(str(Path(after['folder'])/new['file'])))
    window.go_frame(0);idle();assert window.p['exposure']==.55
    assert window.project_dirty and window.windowTitle().startswith('* ')
    window.go_frame(2);idle();assert window.p['exposure']==-.35
    assert window.p['curve']==[[0.,0.],[.3,.2],[.7,.8],[1.,1.]]

    # Save/restore automatically keeps every visited frame and the roll template.
    window.save_session();window.close();app.processEvents()
    model.open_image(ROOT/'samples'/'pro-light-demo.tif')
    window=ui.MainWindow(profile);window.show_error=errors.append
    window.restore_session();idle()
    assert window.project_dirty
    assert window.info['strip']['index']==2 and window.p['exposure']==-.35
    assert model.engine.STATE['frame_params'][4]['exposure']==.85
    assert model.engine.STATE['roll_params']['exposure']==.2

    # Moving project and original together uses a relative link. Moving only the
    # original supports explicit relink, but rejects a different image.
    moved=folder/'搬家';moved.mkdir();moved_source=moved/source.name;moved_project=moved/project.name
    shutil.move(source,moved_source);shutil.move(project,moved_project)
    result=model.open_project(moved_project);assert result['info']['path']==str(moved_source.resolve())
    other=folder/'改名原图.tif';shutil.move(moved_source,other)
    fails(lambda:model.open_project(moved_project),'工程原图已移动')
    result=model.open_project(moved_project,other);assert result['params']['exposure']==.85
    fails(lambda:model.open_project(moved_project,ROOT/'samples'/'pro-light-demo.tif'),'原图与工程记录不一致')
    invalid=model.read_project(moved_project);invalid['layout']['rotation']=180
    broken=folder/'错误布局.filmproj';model.write_json(broken,invalid)
    fails(lambda:model.open_project(broken,other),'布局与工程记录不一致')
    invalid['version']=999;model.write_json(broken,invalid)
    fails(lambda:model.open_project(broken,other),'工程格式或版本')
    invalid['version']=1;invalid['current_frame']=999;model.write_json(broken,invalid)
    fails(lambda:model.open_project(broken,other),'帧数或当前帧号')
    # Single-image projects retain reference flat fields as well.
    result=model.open_image(ROOT/'samples'/'pro-light-demo.tif')
    value=model.flat_reference(result['params'],ROOT/'samples'/'pro-flat-reference.tif')
    single=folder/'单张.filmproj';model.save_project(single,value)
    model.open_image(ROOT/'samples'/'pro-strip-demo.tif')
    restored=model.open_project(single)
    assert restored['info']['strip'] is None and restored['params']['flat_field']==value['flat_field']
    # Prevent the old window from writing its GUI document over our model-only tests.
    window.loaded=False;window.close();app.processEvents()
print('PASS projects: per-frame edits, roll template, full session, exact six-frame TIFF exports, relative move, relink, wrong source/layout rollback, corrupt project rejection, single-image flat field')
