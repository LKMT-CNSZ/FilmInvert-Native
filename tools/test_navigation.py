"""Qt async full-frame navigation icons must never receive viewport tiles."""
from pathlib import Path
import os,sys,time,tempfile,threading
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import native_app as ui,desktop_model as model
from native_widgets import photo_icon,image_from_array
import numpy as np
from PySide6.QtGui import QIcon,QPixmap
from PySide6.QtCore import QSize
app=ui.QApplication.instance() or ui.QApplication([]);app.setStyle('Fusion')
icon=photo_icon(QPixmap.fromImage(image_from_array(np.full((95,140,3),128,np.uint8))))
for mode in (QIcon.Mode.Normal,QIcon.Mode.Selected):
    c=icon.pixmap(QSize(140,95),mode).toImage().pixelColor(50,40)
    assert (c.red(),c.green(),c.blue())==(128,128,128)
(ROOT/'build/tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build/tests',prefix='navigation-') as temporary:
    window=ui.MainWindow(Path(temporary)/'profile');errors=[];window.show_error=errors.append;window.show()
    def idle():
        end=time.monotonic()+60
        while time.monotonic()<end:
            app.processEvents();time.sleep(.01)
            if window.job is None and not window.pending_command and not window.preview_timer.isActive() and not any(j.isRunning() for j in window.jobs):break
        else:raise AssertionError('Qt preview timeout')
        assert not errors,errors
    def icons():
        return [window.frames.item(i).icon().pixmap(QSize(140,95)).toImage() for i in range(window.frames.count())]
    window.open_path(str(ROOT/'samples/pro-strip-demo.tif'));idle();before=icons()
    window.pixel_view();idle();assert window.canvas.region_mode and icons()==before
    window.canvas.horizontalScrollBar().setValue(50);window.canvas.verticalScrollBar().setValue(70);idle()
    assert icons()==before
    window.go_frame(1);idle();assert window.info['strip']['index']==1 and icons()==before
    window.fit_view();idle();assert not window.canvas.region_mode
    # A late full-frame result must not overwrite either frame when switching.
    preview=model.preview;entered=threading.Event();release=threading.Event()
    def slow(*args,**kwargs):
        result=preview(*args,**kwargs)
        entered.set();assert release.wait(10)
        return result
    model.preview=slow;before=icons()
    try:
        window.edit_parameter('exposure',1.2,True)
        end=time.monotonic()+5
        while not entered.is_set() and time.monotonic()<end:app.processEvents();time.sleep(.01)
        assert entered.is_set();window.go_frame(2)
        # Subsequent work uses normal preview. The captured old worker can finish.
        model.preview=preview;release.set();idle()
        assert window.info['strip']['index']==2 and icons()[1]==before[1]
    finally:release.set();model.preview=preview
    # Another source opened while a tile is in flight must get its own image.
    entered.clear();release.clear();model.preview=slow
    try:
        window.pixel_view();end=time.monotonic()+5
        while not entered.is_set() and time.monotonic()<end:app.processEvents();time.sleep(.01)
        assert entered.is_set();window.open_path(str(ROOT/'samples/pro-light-demo.tif'))
        model.preview=preview;release.set();idle()
        assert window.info['path'].endswith('pro-light-demo.tif')
    finally:release.set();model.preview=preview
    window.close();app.processEvents()
print('PASS navigation: exact normal/selected RGB, fit/100%/pan/frame/fit, late full preview on frame switch, late ROI on source switch')
