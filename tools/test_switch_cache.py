"""Decoded/analysis/view cache correctness, isolation, invalidation and GUI controls."""
from pathlib import Path
import os,sys,tempfile,copy,json,subprocess
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from PIL import Image
from document_cache import DocumentCache,BlobCache
from loader import Loaded
import desktop_model as model,native_app as ui
def loaded(value,side=256):
    a=np.full((side,side,3),value,np.float32);return Loaded(a,'fixture',side,side,False)
cache=DocumentCache(1024*1024);a=loaded(.2);b=loaded(.7)
cache.put('a',a);cache.put('b',b);assert cache.get('a') is None and cache.get('b') is b
state={'preview':np.ones((64,64,3),np.float32),'info':{'base':[.7,.5,.3]},'layout':None,'params':{'exposure':0},'input':{'mode':'single'}}
cache.put_analysis('b','mode',state);first=cache.get_analysis('b','mode');first['info']['base'][0]=0
assert cache.get_analysis('b','mode')['info']['base'][0]==.7
for i in range(20):cache.put_analysis('b',str(i),state)
assert cache.bytes<=cache.budget and cache.bytes>=b.linear.nbytes
cache.put('giant',loaded(.4,512));assert not cache.entries and cache.bytes==0
cache.put('a',a);cache.set_budget(0);assert cache.bytes==0
blobs=BlobCache(200);blobs.put('甲',b'a'*160);blobs.put('乙',b'b'*160)
assert blobs.get('甲') is None and blobs.get('乙')==b'b'*160 and blobs.bytes<=200
print('PASS decoded/analysis cache LRU, bounded retained bytes, independent metadata, oversized bypass and disable')
app=ui.QApplication.instance() or ui.QApplication([])
(ROOT/'build/tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build/tests',prefix='switch-cache-') as temporary:
    folder=Path(temporary);paths=[folder/'A.png',folder/'B.png']
    for i,path in enumerate(paths):
        y,x=np.mgrid[:70,:100];Image.fromarray(np.stack([x+i*20+40,y+50,(x+y)//2+30],axis=-1).astype(np.uint8)).save(path)
    model.clear_navigation_caches();model.DOCUMENTS.set_budget(32*1024*1024)
    p=model.params({'mode':'positive','black':0,'white':1}).__dict__
    model.open_image(paths[0],input_mode='single');expected=model.preview(p)['pixels'].copy()
    model.open_image(paths[1],input_mode='single');other=model.preview(p)['pixels'].copy();assert not np.array_equal(expected,other)
    load=model.load;install=model.engine.install_loaded;process=model.core.process
    def forbidden(*a,**k):raise AssertionError('warm switch repeated decode/analysis/render')
    model.load=model.engine.install_loaded=model.core.process=forbidden
    try:
        model.open_image(paths[0],input_mode='single');warm=model.preview(p)
        assert np.array_equal(warm['pixels'],expected)
    finally:model.load=load;model.engine.install_loaded=install;model.core.process=process
    # Input color and working space must not collide after cross-document preview retention.
    model.open_image(paths[0],input_mode='single',input_color='linear_srgb');linear=model.preview(p)['pixels'].copy()
    model.open_image(paths[0],input_mode='single',input_color='srgb');encoded=model.preview(p)['pixels'].copy()
    assert not np.array_equal(linear,encoded)
    model.open_image(paths[0],input_mode='single',working_space='prophoto');assert model.engine.STATE['native_loaded'].working_space=='prophoto'
    model.open_image(paths[0],input_mode='single');assert np.array_equal(model.preview(p)['pixels'],expected)
    # Full SHA verification must invalidate even same-size/same-time replacement.
    source=folder/'same.bmp';Image.new('RGB',(32,24),(80,70,60)).save(source)
    model.open_image(source,input_mode='single');old=model.engine.STATE['native_loaded'];stat=source.stat()
    Image.new('RGB',(32,24),(100,90,80)).save(source);assert source.stat().st_size==stat.st_size
    os.utime(source,ns=(stat.st_atime_ns,stat.st_mtime_ns));model.open_image(source,input_mode='single')
    assert model.engine.STATE['native_loaded'] is not old and not np.array_equal(old.linear,model.engine.STATE['native_loaded'].linear)
    # Cached input layouts do not collide; invalid input still rolls back.
    model.open_image(paths[0],input_mode='strip',manual={'rotation':0,'frames':[{'id':'left','box':[0,0,50,70]}]})
    assert model.engine.STATE['full'].shape==(70,50,3)
    model.NAV_THUMBS.budget=1024*1024;model.preview(p);thumb=model.thumbnails()[0]
    renderer=model.engine.do_thumb;model.engine.do_thumb=forbidden
    try:assert model.thumbnails()[0]==thumb
    finally:model.engine.do_thumb=renderer
    changed=dict(p,exposure=.8);model.preview(changed);updated=model.thumbnails()[0];assert updated!=thumb
    model.engine.THUMB_CACHE.clear();assert renderer(0)==updated
    before=model.engine.STATE['native_loaded'];info=copy.deepcopy(model.engine.STATE['info'])
    try:model.open_image(paths[1],input_mode='wrong')
    except ValueError:pass
    else:raise AssertionError('invalid mode accepted')
    assert model.engine.STATE['native_loaded'] is before and model.engine.STATE['info']==info
    model.open_image(paths[0],input_mode='single');assert model.engine.STATE['full'].shape==(70,100,3)
    print('PASS warm exact pixels without decode/analysis/render, color/space/layout isolation, SHA invalidation despite same stat, failed-open rollback')
    # GUI cache choices persist; clearing must never discard the active original/edit.
    window=ui.MainWindow(folder/'profile');window.show();errors=[];window.show_error=errors.append
    import time
    window.open_path(str(paths[0]));deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        app.processEvents();time.sleep(.005)
        if window.job is None and not window.preview_timer.isActive() and not any(j.isRunning() for j in window.jobs):break
    assert not errors,errors
    window.cache_actions[512].trigger();assert model.DOCUMENTS.budget==512*1024*1024
    original=model.engine.STATE['native_loaded'];params=copy.deepcopy(window.p);window.clear_switch_cache()
    assert not model.DOCUMENTS.entries and not model.PREVIEWS.entries and model.engine.STATE['native_loaded'] is original and window.p==params
    window.loaded=False;window.close();app.processEvents()
    window=ui.MainWindow(folder/'profile');assert window.cache_actions[512].isChecked();window.loaded=False;window.close();app.processEvents()
    child=subprocess.run([sys.executable,'-I','-X','utf8','-c','import desktop_model as m;assert m.DOCUMENTS.budget==0;print("PASS worker cache disabled")'],capture_output=True,encoding='utf-8',errors='replace')
    assert child.returncode==0,child.stdout+child.stderr
    print('PASS GUI budget choices/clear/persistence, active edit preserved and independent workers default uncached')
print('PASS source-switch cache regressions')
