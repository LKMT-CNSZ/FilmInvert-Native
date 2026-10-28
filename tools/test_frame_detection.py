"""Private-photo-free regressions for camera framing and safe base sampling."""
from pathlib import Path
import copy,os,sys,tempfile,time
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import strip,core,desktop_model as model,native_app as ui
from tiffio import write_tiff_rgb16

def camera_fixture():
    rng=np.random.default_rng(4626)
    a=np.full((600,900,3),.006,np.float32);base=np.array([.7,.3,.12],np.float32)
    a[15:585,100:750]=base
    a[30:570,230:720]=base*(.15+.5*rng.random((540,490,1)))
    a[240:330,230:720]=base*.75 # broad, bright image content, not a frame gap
    a[:,0:70]=base*.3 # incomplete neighbouring frame
    return a

image=camera_fixture()
for a in (image,np.rot90(image),np.repeat(np.repeat(image,3,axis=0),3,axis=1)):
    layout=strip.detect_layout(a)
    assert layout.kind=='window' and layout.frame_count==1
    assert layout.rotation==0 # film format cannot establish the subject's up direction
    _,_,x0,x1,y0,y1=strip.frame_boxes(layout)[0]
    assert (x1-x0)*(y1-y0)>.40*a.shape[0]*a.shape[1]
    assert (x1-x0)*(y1-y0)<.65*a.shape[0]*a.shape[1]

# A broad horizontal bright subject in an unframed image is not an inter-frame gap.
photo=np.full((1000,900,3),.25,np.float32);photo[440:560]=.8
assert strip.detect_layout(photo).kind=='single'
assert strip.detect_layout(np.full((300,400,3),.4,np.float32)).kind=='single'

app=ui.QApplication.instance() or ui.QApplication([])
(ROOT/'build/tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build/tests',prefix='detection-') as folder:
    folder=Path(folder);path=folder/'camera.tif';write_tiff_rgb16(path,image)
    opened=model.open_image(path)
    assert opened['info']['strip']['frames']==1
    region=model.source_region();assert region and region['box'][0]>220 and region['box'][2]<735
    # Auto mode in the layout editor must show the same one-window ROI as loading.
    dialog=ui.FrameLayoutDialog(model.layout_editor())
    dialog.mode.setCurrentIndex(dialog.mode.findData('single'))
    dialog.mode.setCurrentIndex(dialog.mode.findData('auto'))
    assert dialog.rows[0]['box']==region['box'];dialog.reject()
    project=folder/'auto.filmproj';model.save_project(project,opened['params'])
    restored=model.open_project(project);assert model.source_region()==region

    # Old automatic layouts retain their exact boxes and per-frame processing.
    old_path=folder/'legacy-camera.tif'
    write_tiff_rgb16(old_path,image[np.arange(800)*600//800])
    model.open_image(old_path)
    ld=model.engine.STATE['native_loaded']
    model.engine.install_loaded(str(old_path),ld,legacy_detection=True)
    old_identity=model.layout_identity();assert old_identity!=model.read_project(project)['layout']
    assert old_identity is not None and model.engine.STATE['strip_layout'].frame_count>1
    old=model.engine.STATE['params'].__dict__;old['exposure']=.27
    old_project=folder/'old.filmproj';model.save_project(old_project,old)
    original_snapshot=model.read_project(old_project)
    model.open_image(path);model.open_project(old_project)
    assert model.layout_identity()==old_identity
    assert model.project_signature(model.project_snapshot(old))==model.project_signature(original_snapshot)
    saved_full=model.engine.STATE['full'];saved_params=model.clone(model.engine.STATE['params'].__dict__)
    damaged=copy.deepcopy(original_snapshot);damaged['layout']['rotation']=180
    model.write_json(folder/'damaged.filmproj',damaged)
    try:model.open_project(folder/'damaged.filmproj')
    except ValueError:pass
    else:raise AssertionError('damaged legacy layout accepted')
    assert model.engine.STATE['full'] is saved_full
    assert model.engine.STATE['params'].__dict__==saved_params

    # Endpoint validation reads original transmission and leaves failed edits intact.
    clipped=image.copy();clipped[100:160,300:360]=[1,.2,1]
    path2=folder/'endpoint.tif';write_tiff_rgb16(path2,clipped)
    opened=model.open_image(path2,input_mode='single');value=opened['params']
    before=copy.deepcopy(model.engine.STATE['params'].__dict__)
    try:model.sample(value,330/900,130/600,'base',True)
    except ValueError as error:assert '未应用' in str(error) and 'ProPhoto' in str(error)
    else:raise AssertionError('clipped base accepted')
    assert model.engine.STATE['params'].__dict__==before
    value=model.sample(value,150/900,.5,'base',True)
    assert not core.base_clipped_channels(value['base'])
    assert core.base_clipped_channels([1.,.2,0.])==[0,2]
    assert not core.base_clipped_channels([1.2,.4,.2]) # corrected illumination can exceed 1
    window=ui.MainWindow(folder/'profile');window.p=value;window.sync_ui()
    assert '解码端点' not in window.base_label.text()
    window.p['base']=[1.,.2,1.];window.sync_ui();assert '解码端点' in window.base_label.text()
    window.p['base']=[.7,.3,.12];window.sync_ui();assert '解码端点' not in window.base_label.text()
    window.loaded=False;window.close();app.processEvents()
print('PASS camera windows, rotated/scaled framing, broad-subject rejection, auto editor consistency, auto project roundtrip, legacy layout preservation and corrupt rollback, atomic endpoint rejection, persistent base warning')
