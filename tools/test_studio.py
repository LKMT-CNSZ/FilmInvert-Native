"""Persistent library, immutable queue, verified resume and isolated process tests."""
from pathlib import Path
import copy
import json
import os
import sys
import tempfile
import time
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import core
import native_app as ui
import desktop_model as model
from studio_store import StudioStore,queue_lock,QueueBusy,sha256
import studio_worker as worker
from tiffread import read_tiff

app=ui.QApplication([])
(ROOT/'build'/'tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build'/'tests',prefix='studio-') as folder:
    folder=Path(folder);profile=folder/'profile';window=ui.MainWindow(profile);errors=[];window.show_error=errors.append
    def idle():
        end=time.monotonic()+90
        while time.monotonic()<end:
            app.processEvents();time.sleep(.01)
            if window.job is None and not window.pending_command and not window.preview_timer.isActive() and not any(j.isRunning() for j in window.jobs):break
        else:raise AssertionError('Qt worker timeout')
        assert not errors,errors
    window.open_path(str(ROOT/'samples'/'pro-light-demo.tif'));idle();window.edit_parameter('exposure',.25,True);idle()
    store=window.studio_store;first=store.assets()[0]['id']
    store.metadata(first,4,'pick','卷 A')
    window.open_path(str(ROOT/'samples'/'pro-strip-demo.tif'));idle()
    window.keep_check.setChecked(False);window.meter_check.setChecked(False)
    window.edit_parameter('exposure',.45,True);idle();window.go_frame(2);idle();window.edit_parameter('exposure',-.3,True);idle()
    second=[row for row in store.assets() if row['id']!=first][0]['id']
    assert len(store.assets('卷 A',4))==1 and len(store.assets('卷 A',5))==0
    window.open_library_asset(first);idle();assert window.p['exposure']==.25
    window.open_library_asset(second);idle();assert window.info['strip']['index']==2 and window.p['exposure']==-.3
    assert model.engine.STATE['frame_params'][0]['exposure']==.45
    # Reopening the persistent store retains metadata, edits, and task snapshots.
    fresh=StudioStore(profile);assert fresh.get_asset(first)['rating']==4
    job=fresh.enqueue([second],folder/'成品',True)[0]
    captured=fresh.get_job(job)['snapshot'];assert captured['frames']['0']['exposure']==.45
    window.edit_parameter('exposure',1.,True);idle()
    assert fresh.get_job(job)['snapshot']['frames']['2']['exposure']==-.3
    window.open_studio();app.processEvents();dialog=window.studio_dialog
    assert dialog.library.rowCount()==2 and dialog.queue.rowCount()==1
    dialog.search.setText('卷 A');assert dialog.library.rowCount()==1
    dialog.search.clear();dialog.hide()
    window.loaded=False  # Pure worker fixtures below own the in-process model.
    # Pause exactly after one committed frame; reopening does not rerender it.
    def pause(done,total,path):
        if done==1:fresh.request_pause(job)
    assert worker.execute(fresh,job,pause)=='paused'
    checkpoint=fresh.get_job(job);assert checkpoint['status']=='paused' and len(checkpoint['outputs'])==1
    output=Path(checkpoint['destination'])/'frame-0001.tif';first_hash=sha256(output);stamp=output.stat().st_mtime_ns
    fresh.retry([job]);assert worker.execute(fresh,job)=='completed'
    assert sha256(output)==first_hash and output.stat().st_mtime_ns==stamp
    manifest=json.loads((output.parent/'manifest.json').read_text('utf-8'));assert len(manifest['frames'])==6
    # Independently reproduce a saved frame and compare all uint16 pixels.
    model.open_project(store.materialize(captured,'acceptance'))
    array,p=worker.frame_parameters(captured,2)
    expected=(core.process(array,p)*65535+.5).astype(np.uint16)
    assert np.array_equal(read_tiff(str(output.parent/'frame-0003.tif')),expected)
    # Prepared publication interrupted between TIFF and JSON is recoverable.
    partial=fresh.enqueue([first],folder/'成品',False)[0]
    original_publish=worker.publish;calls=[]
    def interrupted(stage,destination,expected):
        calls.append(1)
        if len(calls)==2:raise OSError('simulated interruption after TIFF publication')
        return original_publish(stage,destination,expected)
    worker.publish=interrupted
    try:
        try:worker.execute(fresh,partial)
        except OSError:pass
        else:raise AssertionError('Interruption fixture did not execute')
    finally:worker.publish=original_publish
    assert fresh.get_job(partial)['status']=='failed'
    fresh.retry([partial]);worker.execute(fresh,partial)
    assert fresh.get_job(partial)['status']=='completed'
    # Altering a completed output stops retry and preserves the altered bytes.
    corrupt=fresh.enqueue([second],folder/'成品',True)[0]
    worker.execute(fresh,corrupt,lambda done,*args:fresh.request_pause(corrupt) if done==1 else None)
    victim=Path(fresh.get_job(corrupt)['destination'])/'frame-0001.tif';victim.write_bytes(b'user-modified-output')
    fresh.retry([corrupt])
    try:worker.execute(fresh,corrupt)
    except ValueError as error:assert '修改' in str(error)
    else:raise AssertionError('Changed output was overwritten')
    assert victim.read_bytes()==b'user-modified-output'
    # Process death becomes paused only when the exclusive worker lock is free.
    dead=fresh.enqueue([first],folder/'成品',False)[0];fresh.claim(dead)
    with queue_lock(fresh.folder):
        assert fresh.recover() is False
        try:
            with queue_lock(fresh.folder):pass
        except QueueBusy:pass
        else:raise AssertionError('Concurrent workers could take the same queue')
    assert fresh.recover() and fresh.get_job(dead)['status']=='paused'
    # Missing source produces a retryable failure. Source restored -> retry succeeds.
    source=folder/'queue-source.tif';source.write_bytes((ROOT/'samples'/'pro-light-demo.tif').read_bytes())
    result=model.open_image(source);snap=model.project_snapshot(result['params']);third=fresh.capture(snap)
    missing=fresh.enqueue([third],folder/'成品',False)[0];moved=source.with_suffix('.held');source.rename(moved)
    try:worker.execute(fresh,missing)
    except FileNotFoundError:pass
    else:raise AssertionError('Missing source accepted')
    moved.rename(source);fresh.retry([missing]);worker.execute(fresh,missing)
    changed=fresh.enqueue([third],folder/'成品',False)[0];original_bytes=source.read_bytes();source.write_bytes(original_bytes+b'changed')
    try:worker.execute(fresh,changed)
    except ValueError as error:assert '不一致' in str(error)
    else:raise AssertionError('Changed original was accepted')
    source.write_bytes(original_bytes);fresh.retry([changed]);worker.execute(fresh,changed)
    obsolete=fresh.enqueue([third],folder/'成品',False)[0]
    bad=fresh.get_job(obsolete)['snapshot'];bad['_queue_engine']='old-engine'
    with fresh.connection() as db:db.execute('UPDATE jobs SET snapshot=? WHERE id=?',(json.dumps(bad),obsolete))
    try:worker.execute(fresh,obsolete)
    except ValueError as error:assert '引擎' in str(error)
    else:raise AssertionError('Changed processing engine was accepted')
    # A real QProcess exports another photograph while the editor stays on its own.
    window.open_library_asset(second);idle();window.edit_parameter('exposure',.8,True);idle()
    editor_signature=model.project_signature(model.project_snapshot(window.p,window.project_options()))
    process_job=fresh.enqueue([first],folder/'隔离输出',False)[0]
    # Previously paused/failed fixtures should remain paused, not be scheduled here.
    with fresh.connection() as db:db.execute("UPDATE jobs SET status='paused' WHERE id<>? AND status='queued'",(process_job,))
    window.queue_controller.start()
    end=time.monotonic()+90
    while time.monotonic()<end:
        app.processEvents();time.sleep(.02)
        if not window.queue_controller.running() and fresh.get_job(process_job)['status']=='completed':break
    else:raise AssertionError(fresh.get_job(process_job))
    assert model.project_signature(model.project_snapshot(window.p,window.project_options()))==editor_signature
    assert window.p['exposure']==.8
    # Failed and completed jobs persist after a fresh store instance.
    assert StudioStore(profile).get_job(corrupt)['status']=='failed'
    window.loaded=False;window.close();app.processEvents()
print('PASS studio: multi-photo autosave/restore, metadata/filtering, frozen queue parameters, pause/resume without rewrites, exact TIFF, prepared-output recovery, modified-file protection, exclusive worker/restart recovery, source retry and real process isolation')
