"""2.6 cache correctness, paged UI, content-verified relink and reversible backup."""
from pathlib import Path
import os,sys,tempfile,time,zipfile,json,shutil
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import native_app as ui
import desktop_model as model
from preview_cache import PreviewCache,ThumbnailCache
from studio_store import StudioStore,queue_lock,QueueBusy,sha256
from studio_archive import backup,restore
from PIL import Image

app=ui.QApplication([])
from PySide6.QtGui import QFontDatabase
font=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/'msyh.ttc'
if font.is_file():QFontDatabase.addApplicationFont(str(font))
app.setStyle('Fusion');app.setStyleSheet(ui.STYLE)
(ROOT/'build'/'tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build'/'tests',prefix='workflow-') as tmp:
    folder=Path(tmp);window=ui.MainWindow(folder/'profile');errors=[];window.show_error=errors.append
    def idle():
        end=time.monotonic()+60
        while time.monotonic()<end:
            app.processEvents();time.sleep(.01)
            dialog=window.studio_dialog
            if window.job is None and not window.preview_timer.isActive() and not any(j.isRunning() for j in window.jobs) and (dialog is None or dialog.thumb_worker is None):break
        else:raise AssertionError('worker timeout')
        assert not errors,errors
    source=folder/'original.tif';shutil.copy2(ROOT/'samples'/'pro-light-demo.tif',source)
    window.open_path(str(source));idle();window.edit_parameter('exposure',.6,True);idle()
    store=window.studio_store;asset=store.assets()[0];identifier=asset['id']
    store.metadata(identifier,5,'pick','备份胶卷')
    # A real processing call is reused on a hit and changes on new parameters.
    model.PREVIEWS.entries.clear();model.PREVIEWS.bytes=0
    process=model.core.process;calls=[]
    def counted(*args,**kwargs):calls.append(1);return process(*args,**kwargs)
    model.core.process=counted
    try:
        first=model.preview(window.p);second=model.preview(window.p)
        assert len(calls)==1 and np.array_equal(first['pixels'],second['pixels'])
        altered=dict(window.p,exposure=-.4);model.preview(altered);assert len(calls)==2
    finally:model.core.process=process
    cache=PreviewCache(1000);pixels=np.zeros((10,20,3),np.uint8);hist=[np.zeros(2,np.int64)]
    cache.put('a',pixels,hist);cache.put('b',pixels+1,hist)
    assert cache.get('a') is None and cache.get('b') is not None and cache.bytes<=1000
    # Protect full-size interactive rendering before allocating full output arrays.
    original=model.engine.STATE['full'];model.engine.STATE['full']=np.broadcast_to(np.zeros((1,1,3),np.float32),(5000,5000,3))
    try:
        try:model.preview(window.p,True)
        except ValueError as error:assert '768 MB' in str(error)
        else:raise AssertionError('unbounded full preview')
    finally:model.engine.STATE['full']=original
    window.save_session();store.save_thumbnail(str(source),first['pixels'])
    path,label=store.thumbnails.original(store.get_asset(identifier));assert label=='已修片' and Path(path).is_file()
    tiny=ThumbnailCache(folder/'tiny-cache',1000)
    for i in range(6):tiny.save(str(i),Image.new('RGB',(120,100),(i*30,40,60)))
    assert sum(p.stat().st_size for p in tiny.folder.glob('*.jpg'))<=1000
    # Paging bounds all QPixmap icons, even with hundreds of library records.
    library=folder/'many';library.mkdir()
    for i in range(204):Image.new('RGB',(8,8),(30,40,50)).save(library/f'{i}.png')
    store.import_paths(library.glob('*.png'));window.open_studio();dialog=window.studio_dialog
    assert dialog.library.rowCount()==200
    dialog.change_page(1);assert dialog.library.rowCount()==5
    dialog.search.setText('备份胶卷');assert dialog.library.rowCount()==1
    dialog.library.selectRow(0);dialog.view.setCurrentIndex(1);idle()
    assert dialog.wall.count()==1 and dialog.selected_assets()==[identifier]
    assert not dialog.wall.item(0).icon().isNull()
    dialog.grab().save(str(ROOT/'build'/'workflow-preview.png'))
    dialog.view.setCurrentIndex(0);assert dialog.selected_assets()==[identifier]
    # Backup includes immutable job snapshots and metadata, without original files.
    job=store.enqueue([identifier],folder/'output',False)[0]
    archive=folder/'studio.filmbackup';backup(store,archive)
    with zipfile.ZipFile(archive) as z:assert sorted(z.namelist())==['library.sqlite3','manifest.json']
    store.metadata(identifier,1,'reject','changed')
    before=folder/'before-bad.filmbackup';backup(store,before)
    bad=folder/'bad.filmbackup'
    with zipfile.ZipFile(archive) as z,zipfile.ZipFile(bad,'w') as out:
        out.writestr('manifest.json',z.read('manifest.json'));out.writestr('library.sqlite3',b'broken')
    try:restore(store,bad)
    except ValueError:pass
    else:raise AssertionError('corrupt backup accepted')
    assert store.get_asset(identifier)['rating']==1
    with queue_lock(store.folder):
        try:restore(store,archive)
        except QueueBusy:pass
        else:raise AssertionError('restore during export accepted')
    recovery=restore(store,archive)
    assert Path(recovery).is_file() and store.get_asset(identifier)['rating']==5
    assert store.get_job(job)['status']=='paused'
    restore(store,recovery);assert store.get_asset(identifier)['rating']==1
    restore(store,archive)
    # Relinking accepts only a single same-name candidate with identical contents.
    moved=folder/'moved';moved.mkdir();source.rename(moved/source.name)
    wrong=moved/'wrong';wrong.mkdir();(wrong/source.name).write_bytes(b'wrong')
    frozen=store.get_job(job)['snapshot']
    result=store.relink([identifier],moved);assert len(result['changed'])==1 and not result['errors']
    assert store.get_asset(identifier)['snapshot']['source']['path']==str((moved/source.name).resolve())
    assert store.get_job(job)['snapshot']==frozen
    window.library_relinked(result['changed']);assert window.info['path']==str((moved/source.name).resolve())
    window.open_library_asset(identifier);idle();assert window.p['exposure']==.6
    duplicate=folder/'duplicate';duplicate.mkdir();shutil.copy2(moved/source.name,duplicate/source.name)
    shutil.copy2(moved/source.name,wrong/source.name);(moved/source.name).unlink()
    result=store.relink([identifier],folder);assert result['errors'] and not result['changed']
    # Exercise the actual restore UI command, including automatic current-edit capture.
    get_open=ui.QFileDialog.getOpenFileName;question=ui.QMessageBox.question
    ui.QFileDialog.getOpenFileName=lambda *args,**kwargs:(str(archive),'')
    ui.QMessageBox.question=lambda *args,**kwargs:ui.QMessageBox.StandardButton.Yes
    try:dialog.restore_library();idle()
    finally:ui.QFileDialog.getOpenFileName=get_open;ui.QMessageBox.question=question
    assert not window.loaded and window.canvas.pixels is None and dialog.isEnabled()
    window.close();app.processEvents()
print('PASS workflow: exact cache hits/invalidation, byte limits, full-preview guard, paged thumbnail wall/selection, saved positive previews, checksummed SQLite backup, corrupt/busy restore protection, automatic rollback backup, paused jobs, SHA256 relink/ambiguity rejection and preserved queue snapshots')
