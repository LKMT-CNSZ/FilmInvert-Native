"""Offscreen Qt integration tests; no desktop input or HTTP service."""
from pathlib import Path
import os
import sys
import tempfile
import threading
import time
import io
import json
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import native_app as ui
import desktop_model as model
import core
import numpy as np
from PIL import Image,ImageCms
from tiffread import read_tiff

(ROOT/'build'/'tests').mkdir(parents=True,exist_ok=True)
app=ui.QApplication([])
with tempfile.TemporaryDirectory(dir=ROOT/'build'/'tests',prefix='native-') as folder:
    window=ui.MainWindow(Path(folder)/'profile')
    # Simulate saved rc7 settings: navigation hidden and no new toolbar recorded.
    window.nav_dock.hide();window.removeToolBar(window.alignment_toolbar)
    window.close();app.processEvents()
    window.settings.remove('alignmentToolbarIntroduced');window.settings.sync()
    window=ui.MainWindow(Path(folder)/'profile')
    errors=[];window.show_error=lambda error:errors.append(error)
    def wait_idle():
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            app.processEvents();time.sleep(.01)
            if window.job is None and not window.pending_command and not window.preview_timer.isActive() and not any(j.isRunning() for j in window.jobs):break
        else:raise AssertionError('Qt worker did not finish')
        assert not errors,errors
    window.open_path(str(ROOT/'samples'/'pro-light-demo.tif'));wait_idle()
    assert window.loaded and window.tabs.count()==4
    # The film-edge entry stays discoverable when the navigation dock is hidden,
    # including after restoring an rc7-era layout with no alignment toolbar.
    window.show();app.processEvents()
    assert not window.nav_dock.isVisible()
    window.nav_dock.hide()
    for width,height in ((1040,700),(1440,940)):
        window.resize(width,height);app.processEvents()
        button=window.alignment_toolbar.widgetForAction(window.frame_action)
        assert button.isVisible() and button.isEnabled()
        assert window.alignment_toolbar.rect().contains(button.geometry())
        assert '片边辅助' in button.text()
        assert window.frame_action.shortcut().toString()=='Ctrl+Shift+R'
        assert window.geometry_action in window.alignment_toolbar.actions()
    window.nav_dock.show()
    assert not window.canvas.item.pixmap().isNull()
    baseline=window.p.copy();window.edit_parameter('exposure',.5,True);wait_idle()
    assert window.p['exposure']==.5 and window.history_index==1
    window.travel(-1);wait_idle();assert window.p['exposure']==baseline['exposure']
    window.travel(1);wait_idle();assert window.p['exposure']==.5
    window.set_curve([[0,0],[.25,.2],[.75,.8],[1,1]]);wait_idle()
    window.select_hsl(0);window.edit_hsl(0,-.2);window.commit_history();wait_idle()
    window.automatic_wb('gray_world');wait_idle();assert len(window.p['wb_gain'])==3
    # Direct file reference, no upload or copying source photographs.
    value=model.flat_reference(window.p,ROOT/'samples'/'pro-flat-reference.tif')
    window.set_parameters(value);wait_idle();assert window.p['flat_mode']=='reference'
    window.edit_parameter('output_space','prophoto',True)
    dest=Path(folder)/'中文成品.tif';window.export_to(str(dest));wait_idle()
    assert read_tiff(str(dest)).dtype==np.uint16
    with Image.open(dest) as im:
        assert 'ProPhoto' in ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(im.info['icc_profile'])))
    saved=json.loads(Path(str(dest)+'.json').read_text('utf-8'))
    assert saved['params']['flat_mode']=='reference' and saved['params']['hsl'][0][0]==-.2
    window.pixel_view();wait_idle();assert window.canvas.transform().m11()==1
    window.fit_view();wait_idle()
    window.open_path(str(ROOT/'samples'/'pro-strip-demo.tif'));wait_idle()
    assert window.info['strip']['frames']==6 and window.frames.count()==6
    window.edit_parameter('exposure',.25,True);window.go_frame(3);wait_idle()
    assert window.info['strip']['index']==3 and window.p['exposure']==.25
    window.apply_roll();wait_idle()
    # Cancellation occurs between completed frames and keeps a manifest.
    cancel=threading.Event()
    def progress(done,total,path):
        if done==1:cancel.set()
    result=model.export_roll(folder,window.p,True,cancel,progress)
    assert result['cancelled'] and result['completed']==1
    assert len(list(Path(result['folder']).glob('*.tif')))==1
    assert json.loads((Path(result['folder'])/'task.json').read_text())['cancelled']
    window.batch_to(folder);wait_idle()
    manifest=json.loads((Path(window.last_export)/'manifest.json').read_text('utf-8'))
    assert len(manifest)==6 and all(row['params']['exposure']==.25 for row in manifest)
    # GUI adapter's preview uses the same core pixels, without JPEG round trips.
    p=model.params(window.p)
    expected=(core.process(model.engine.STATE['preview'],p)*255+.5).astype(np.uint8)
    assert np.array_equal(model.preview(window.p)['pixels'],expected)
    window.save_session();assert (window.profile/'session.json').exists()
    window.close();app.processEvents()
    print('PASS Qt native: 4 tabs, asynchronous load/preview, history, curve/HSL, WB, reference flat, ICC TIFF, 100%, 6-frame sync/export, cancellation, session, exact core preview')
