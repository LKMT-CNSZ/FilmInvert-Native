"""Real Qt filters, editing-aware keys, export scope and non-mutating prefetch."""
from pathlib import Path
import os,sys,tempfile,time,copy,threading,struct,json
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from PIL import Image
import native_app as ui,desktop_model as model,loader
from native_efficiency import PrefetchWorker,estimate_decoded_bytes,phase
from document_cache import DocumentCache
from PySide6.QtCore import Qt,QItemSelectionModel,QEvent
from PySide6.QtGui import QKeyEvent,QFontDatabase
class Keys:
    @staticmethod
    def keyClick(widget,key,mods=Qt.KeyboardModifier.NoModifier):
        text=chr(int(key)) if 32<=int(key)<127 else ''
        for kind in (QEvent.Type.KeyPress,QEvent.Type.KeyRelease):app.sendEvent(widget,QKeyEvent(kind,int(key),mods,text))
    @staticmethod
    def keyClicks(widget,text):
        for char in text:
            for kind in (QEvent.Type.KeyPress,QEvent.Type.KeyRelease):app.sendEvent(widget,QKeyEvent(kind,ord(char.upper()),Qt.KeyboardModifier.NoModifier,char))
app=ui.QApplication.instance() or ui.QApplication([])
if os.name=='nt':
    for name in ('msyh.ttc','msyhbd.ttc','segoeui.ttf','seguisb.ttf'):
        font=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/name
        if font.is_file():QFontDatabase.addApplicationFont(str(font))
(ROOT/'build/tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build/tests',prefix='efficiency-') as temporary:
    folder=Path(temporary);paths=[]
    for index in range(4):
        y,x=np.mgrid[:140,:180];path=folder/f'照片-{index}.png'
        Image.fromarray(np.stack([x+20+index*5,y+40,(x+y)//2+30],axis=-1).astype(np.uint8)).save(path);paths.append(path)
    identities={str(p):model.source_identity(p) for p in paths}
    model.open_image(paths[0],input_mode='single');active=model.engine.STATE['native_loaded'];before=copy.deepcopy(model.engine.STATE['info'])
    cache=DocumentCache(8*1024*1024);key=cache.key(identities[str(paths[0])],'legacy','srgb');cache.put(key,active)
    def worker(asset):return PrefetchWorker(asset,cache,key,active.linear.nbytes)
    def finish(task):
        task.start();assert task.wait(10000),'prefetch timed out';app.processEvents()
    asset={'path':str(paths[1]),'snapshot':None};task=worker(asset);finish(task)
    target=cache.key(identities[str(paths[1])],'legacy','srgb')
    assert task.outcome=='loaded' and cache.contains(target)
    assert model.engine.STATE['native_loaded'] is active and model.engine.STATE['info']==before
    assert cache.bytes<=cache.budget and cache.contains(key)
    tight=DocumentCache(active.linear.nbytes*2);tight.put('active',active)
    tight.put_analysis('active','single',{'preview':np.zeros((10,10,3),np.float32),'info':{},'params':{},'layout':None,'input':{}})
    assert not tight.put_prefetched('neighbor',active,'active',active.linear.nbytes,threading.Event())
    assert tight.contains('active') and tight.bytes<=tight.budget,'prefetch evicted protected analysis'
    assert estimate_decoded_bytes(paths[1])==180*140*12
    # A preloaded original is consumed by the normal, fully verified model open.
    model.DOCUMENTS=cache;load=model.load
    def forbidden(*a,**k):raise AssertionError('unexpected decoder call')
    model.load=forbidden
    try:model.open_image(paths[1],input_mode='single')
    finally:model.load=load
    assert model.engine.STATE['native_loaded'] is cache.get(target)
    # The same bytes under another input interpretation must remain separate.
    alternative={'path':str(paths[2]),'snapshot':{'source':identities[str(paths[2])],'input':{'color':'linear_srgb','working_space':'prophoto'}}}
    task=worker(alternative);finish(task)
    assert cache.contains(cache.key(identities[str(paths[2])],'linear_srgb','prophoto'))
    # Changed originals, unknown geometry and giant TIFFs never decode speculatively.
    changed=copy.deepcopy(alternative);changed['snapshot']['source']['sha256']='0'*64
    task=worker(changed);finish(task);assert task.outcome=='changed'
    giant=folder/'giant.fff';giant.write_bytes(b'II'+struct.pack('<HIH',42,8,2)+struct.pack('<HHII',256,4,1,20000)+struct.pack('<HHII',257,4,1,20000)+struct.pack('<I',0))
    raw_load=loader.load;loader.load=forbidden
    try:
        task=worker({'path':str(giant),'snapshot':None});finish(task);assert task.outcome=='budget'
    finally:loader.load=raw_load
    # Cancellation and budget changes during a decoder call discard its result.
    for reduce_budget in (False,True):
        entered=threading.Event();release=threading.Event()
        def slow(*args):entered.set();assert release.wait(10);return raw_load(*args)
        loader.load=slow;task=worker({'path':str(paths[3]),'snapshot':None});task.start();assert entered.wait(10)
        if reduce_budget:cache.set_budget(0)
        else:task.stop()
        release.set();assert task.wait(10000);loader.load=raw_load
        assert not cache.contains(cache.key(identities[str(paths[3])],'legacy','srgb'))
        if reduce_budget:assert cache.bytes==0
    print('PASS prefetch: engine untouched, verified foreground reuse, color/space identity, changed-source rejection, giant bypass, cancellation and changed budget')
    window=ui.MainWindow(folder/'profile');window.prefetch_action.setChecked(False);window.show();errors=[];window.show_error=errors.append
    def idle(exports=False):
        deadline=time.monotonic()+120;stable=0
        while time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
            busy=window.job is not None or window.pending_command or window.preview_timer.isActive() or any(j.isRunning() for j in window.jobs) or window.photo_worker is not None or window.prefetch_worker is not None or window.batch_controller.running()
            if exports:busy=busy or window.queue_controller.running() or any(j['status']=='queued' for j in window.studio_store.jobs())
            stable=stable+1 if not busy else 0
            if stable>20:break
        else:raise AssertionError('UI timeout')
        assert not errors,errors
    window.open_dropped_paths([str(p) for p in paths]);idle();identifier=window.current_workflow_asset();batch=window.current_batch_id
    original=copy.deepcopy(window.p);original['exposure']=.37;window.set_parameters(original);idle()
    window.activateWindow();window.canvas.setFocus();app.processEvents()
    Keys.keyClick(window.canvas,Qt.Key.Key_5);assert window.studio_store.get_asset(identifier)['rating']==5
    Keys.keyClick(window.canvas,Qt.Key.Key_P);assert window.studio_store.get_asset(identifier)['flag']=='pick'
    window.photo_filters.search.setFocus();window.photo_filters.search.setText('照片-');Keys.keyClicks(window.photo_filters.search,'3');app.processEvents()
    assert window.studio_store.get_asset(identifier)['rating']==5 and window.photos.count()==1
    Keys.keyClick(window.photo_filters.search,Qt.Key.Key_Left);assert window.info['path']==str(paths[0])
    window.photo_filters.search.setText('no-match');assert window.photos.count()==0 and window.info['path']==str(paths[0]) and window.p['exposure']==.37
    assert window.photo_filters.isEnabled() and window.photo_filters.isVisible() and len(window.photo_all_assets)==4
    window.photo_filters.reset();window.canvas.setFocus();Keys.keyClick(window.canvas,Qt.Key.Key_Right);idle();assert window.info['path']==str(paths[1])
    Keys.keyClick(window.canvas,Qt.Key.Key_Left);idle();assert window.info['path']==str(paths[0]) and window.p['exposure']==.37
    window.open_workflow();dialog=window.workflow_dialog;dialog.output.setText(str(folder/'output'));dialog.run('prepare');idle()
    assert all(i['valid'] and i['snapshot'] and not i['error'] for i in window.batch_store.get(batch)['items'])
    ids=[a['id'] for a in window.photo_all_assets];window.batch_store.update_items(batch,ids[:2],group='卷 A');window.batch_store.update_items(batch,ids[2:],group='卷 B');dialog.refresh()
    dialog.filters.group.setCurrentIndex(dialog.filters.group.findData('卷 A'));assert dialog.table.rowCount()==2
    dialog.table.selectRow(1);dialog.activateWindow();dialog.table.setFocus();app.processEvents()
    Keys.keyClick(dialog.table,Qt.Key.Key_4);assert window.studio_store.get_asset(ids[1])['rating']==4
    Keys.keyClick(dialog.table,Qt.Key.Key_X);assert window.studio_store.get_asset(ids[1])['flag']=='reject'
    dialog.filters.state.setCurrentIndex(dialog.filters.state.findData('skip'));assert dialog.visible_ids()==[ids[1]]
    dialog.filters.state.setCurrentIndex(0)
    dialog.filters.flag.setCurrentIndex(dialog.filters.flag.findData('pick'));assert dialog.visible_ids()==[identifier]
    dialog.filters.search.setText('absent');assert dialog.visible_ids()==[] and dialog.active_items()==[]
    old=copy.deepcopy(window.batch_store.get(batch)['items']);dialog.review();assert old==window.batch_store.get(batch)['items']
    dialog.filters.search.clear();dialog.table.selectRow(0);Keys.keyClick(dialog.table,Qt.Key.Key_R,Qt.KeyboardModifier.ShiftModifier)
    assert next(i for i in window.batch_store.get(batch)['items'] if i['asset']==identifier)['reviewed']
    old_cell=dialog.table.item(0,1);dialog.refresh();assert dialog.table.item(0,1) is old_cell,'unchanged timer refresh rebuilt table'
    dialog.check_visible();assert [i['asset'] for i in window.batch_store.get(batch)['items'] if i['selected']]==[identifier]
    dialog.enqueue();data=window.batch_store.get(batch);assert len([i for i in data['items'] if i.get('job')])==1
    dialog.start_exports();idle(True);assert window.studio_store.get_job(next(i['job'] for i in window.batch_store.get(batch)['items'] if i.get('job')))['status']=='completed'
    dialog.filters.reset();dialog.load_navigation();assert len(window.photo_assets)==4
    # Real idle scheduler respects filtered neighbors and can be disabled/cleared.
    window.prefetch_action.setChecked(True);window.clear_switch_cache();window.start_prefetch();idle()
    assert model.DOCUMENTS.contains(model.DOCUMENTS.key(model.source_identity(paths[1]),'legacy','srgb'))
    window.prefetch_action.trigger();assert not window.prefetch_action.isChecked() and not window.prefetch_timer.isActive()
    output=os.environ.get('FILM_EFFICIENCY_UI')
    if output:
        output=Path(output);output.mkdir(parents=True,exist_ok=True)
        for width,height in ((1440,940),(1040,740)):
            window.resize(width,height);app.processEvents();window.grab().save(str(output/f'main-{width}.png'))
        dialog.filters.reset();dialog.show()
        for page in range(3):
            dialog.tabs.setCurrentIndex(page)
            for width,height in ((1260,880),(1020,740)):
                dialog.resize(width,height);app.processEvents()
                assert (dialog.width(),dialog.height())==(width,height),(page,dialog.width(),dialog.height())
                dialog.grab().save(str(output/f'batch-{page+1}-{width}.png'))
    for path,identity in identities.items():assert model.source_identity(path)['sha256']==identity['sha256']
    window.loaded=False;window.close();idle();app.processEvents()
    restored=ui.MainWindow(folder/'profile');assert not restored.prefetch_action.isChecked()
    assert restored.studio_store.get_asset(identifier)['rating']==5 and restored.studio_store.get_asset(ids[1])['flag']=='reject'
    restored.loaded=False;restored.close();app.processEvents()
    print('PASS Qt filters/empty results preserve edits, text keys untouched, navigation and individual exposure, group/star/flag scope, review keys, one real export, stable refresh, idle prefetch and persisted opt-out')
print('PASS batch efficiency regression')
