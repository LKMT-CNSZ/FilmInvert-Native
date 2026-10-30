"""HDR editor data, view mapping and real Qt interaction with a fake display sink."""
from pathlib import Path
import os,sys,tempfile,time
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import native_app as ui
import numpy as np,hdr_io,hdr_display,geometry
from native_hdr_canvas import visible_frame,PreparedReveal
from unittest.mock import patch
from PySide6.QtCore import QPoint,Qt,QPointF,QEvent
from PySide6.QtGui import QMouseEvent

# Independent pixel-center mapping, padding and fractional pan checks.
y,x=np.mgrid[:6,:8];ramp=np.stack([x,y,x+y],axis=-1).astype(np.float32)
assert np.array_equal(visible_frame(ramp,(0,0,8,6),8,6),ramp)
assert np.array_equal(visible_frame(ramp,(2,1,6,5),4,4),ramp[1:5,2:6])
assert np.allclose(visible_frame(ramp,(2.5,1.5,6.5,5.5),4,4),ramp[1:5,2:6]+[.5,.5,1])
assert np.all(visible_frame(ramp,(-2,-2,10,8),12,10)[:2]==0)

app=ui.QApplication([]);folder=ROOT/'build/hdr-canvas-tests';folder.mkdir(parents=True,exist_ok=True)
class Display:
    hdr=True;closed=0;frames=[];prepared=[];monitor=1;white=203.
    def __init__(self,*args):pass
    def capabilities(self):return dict(hdr_active=self.hdr,max_nits=1000,name='fake HDR display',monitor=self.monitor,sdr_white_nits=self.white)
    def present(self,frame,w,h,white):self.frames.append(frame.copy())
    def present_prepared(self,frame,w,h):self.prepared.append(frame.copy())
    def close(self):Display.closed+=1

with tempfile.TemporaryDirectory(dir=folder) as temporary:
    root=Path(temporary);window=ui.MainWindow(root/'profile');window.show();errors=[];window.show_error=errors.append
    def idle():
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            app.processEvents();time.sleep(.01)
            if window.job is None and not window.preview_timer.isActive() and not window.preview_pending and not any(j.isRunning() for j in window.jobs):break
        else:raise AssertionError('HDR editor timeout')
        assert not errors,errors
    source=root/'source.tif';y,x=np.mgrid[:80,:120];linear=np.stack([.05+x/40,.1+y/40,.2+(x+y)/100],-1).astype(np.float32)
    hdr_io.write(source,linear);window.open_path(str(source));idle()
    before=ui.model.clone(window.p);window.hdr_canvas_action.trigger();idle()
    assert not window.hdr_canvas.active and not window.hdr_canvas_action.isChecked()
    assert window.p==before
    dialog=window.hdr_failure_dialog;assert dialog.isVisible() and dialog.detailedText()
    dialog.accept();app.processEvents()
    from native_hdr import show_hdr_unavailable
    with patch('native_hdr.QDesktopServices.openUrl',return_value=True) as settings:
        dialog=show_hdr_unavailable(window,'Windows HDR is not enabled on this display')
        assert '使用 HDR' in dialog.text() and dialog.detailedText().startswith('Windows HDR')
        assert show_hdr_unavailable(window,'duplicate') is dialog
        next(b for b in dialog.buttons() if 'Windows' in b.text()).click()
        assert settings.call_args[0][0].toString()=='ms-settings:display'
        dialog.accept();app.processEvents()
    with patch.object(ui.QApplication,'platformName',return_value='windows'),patch.object(hdr_display,'Surface',Display):
        window.hdr_canvas_action.trigger();idle();assert window.hdr_canvas.active
        assert window.hdr_canvas.linear.max()>1 and window.p==before
        window.hdr_canvas.paint();assert Display.prepared[-1].max()>1
        preserved=window.hdr_canvas.linear.copy();recipe=ui.model.clone(window.p)
        window.sdr_compare.setChecked(True);assert not window.hdr_canvas.paint() and window.p==recipe
        window.sdr_compare.setChecked(False);assert window.hdr_canvas.paint() and np.array_equal(window.hdr_canvas.linear,preserved)
        Display.monitor=2;Display.white=160.;window.hdr_canvas.check_display()
        assert window.hdr_canvas.active and window.hdr_canvas.reference_white==160 and np.array_equal(window.hdr_canvas.linear,preserved)
        window.edit_parameter('exposure',1,True);idle();assert window.hdr_canvas.linear.max()>linear.max()*1.99
        window.travel(-1);idle();assert window.p['exposure']==0
        # HDR endpoint work runs in the background; playback never resamples/remaps.
        import native_import_reveal,native_hdr_canvas,hdr_display
        from concurrent.futures import Future
        sink=window.hdr_canvas;target=sink.linear;original=target*.25
        window.import_reveal.start(np.zeros(target.shape,np.uint8),np.ones(target.shape,np.uint8),original,target)
        deadline=time.monotonic()+10
        while not window.import_reveal.hdr_prepared and time.monotonic()<deadline:app.processEvents();time.sleep(.005)
        assert window.import_reveal.hdr_prepared and window.import_reveal.active and sink.linear is target
        cached=sink.reveal
        for progress in (.2,.7,.35,.9):
            actual=cached.compose(progress).copy();p=progress*progress*(3-2*progress)
            alpha=np.clip((p*1.1-cached.xx)/.1,0,1);alpha=(alpha*alpha*(3-2*alpha))[None,:,None]
            expected=(cached.original.astype(np.float32)*(1-alpha)+cached.target.astype(np.float32)*alpha).astype(np.float16)
            assert np.allclose(actual,expected,atol=.004,rtol=.001)
        for progress in (0,.5,1):
            pixels=cached.compose(progress)
            assert pixels.dtype==np.float16 and np.isfinite(pixels).all() and pixels.max()>1
        assert np.array_equal(cached.compose(0),cached.original) and np.array_equal(cached.compose(1),cached.target)
        with patch.object(native_hdr_canvas,'visible_frame',side_effect=AssertionError('Repeated resampling')),patch.object(hdr_display,'scrgb_frame',side_effect=AssertionError('Repeated mapping')):
            window.import_reveal.tick(.5);assert sink.paint() and Display.prepared
        signature=sink.reveal_signature
        with patch.object(sink,'view_signature',return_value=('changed viewport',)):
            assert not sink.set_reveal_progress(.6) and sink.reveal is None
        window.import_reveal.stop();assert sink.linear is target and not sink.reveal_waiting
        waiting=Future()
        def pending(*args):sink.reveal_waiting=True;return waiting,signature
        with patch.object(sink,'prepare_reveal',side_effect=pending):
            window.import_reveal.start(np.zeros(target.shape,np.uint8),np.ones(target.shape,np.uint8),original,target)
            assert window.import_reveal.started is None and sink.reveal_waiting
            window.import_reveal.cancel();assert waiting.cancelled() and not sink.reveal_waiting and sink.linear is target
        # Full view/region uses the same unbounded master and output geometry.
        value=dict(window.p,crop=[.1,.1,.9,.9],rotation=90)
        full=ui.model.hdr_edit_preview(value,True)
        region=ui.model.hdr_edit_preview(value,True,region=(2,3,30,32))
        a,b,c,d=region['region'];assert np.array_equal(region['hdr_linear'],full['hdr_linear'][b:d,a:c])
        # Click sampling uses the existing scene coordinates, not the HDR backbuffer.
        window.fit_view();idle();window.choose_sample('gray',True);points=[];window.canvas.sampled.connect(lambda a,b:points.append((a,b)))
        point=window.canvas.mapFromScene(60,40);ui.QApplication.sendEvent(window.canvas.viewport(),QMouseEvent(QEvent.Type.MouseButtonPress,QPointF(point),QPointF(point),Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier));idle()
        assert points and np.allclose(points[-1],[.5,.5],atol=.02)
        window.choose_sample('gray',False)
        other=root/'other.tif';hdr_io.write(other,linear*.4);window.open_path(str(other));idle()
        assert np.max(np.abs(window.hdr_canvas.linear-linear*.4))<2e-6
        window.pixel_view();idle();assert window.canvas.region_mode and window.hdr_canvas.linear is not None
        Display.hdr=False;window.hdr_canvas.check_display();idle()
        assert not window.hdr_canvas.active and not window.hdr_canvas_action.isChecked()
        assert window.hdr_canvas.headroom==0
        assert window.proof_action.isEnabled() and window.clipping_action.isEnabled()
        assert window.hdr_failure_dialog.isVisible()
        window.hdr_failure_dialog.accept();app.processEvents()
    window.close();app.processEvents();assert Display.closed>=1
print('PASS HDR editor float headroom/live exposure/undo, exact region geometry, pan/padding, shared sampling, SDR refusal and display-loss recovery (mock HDR sink; no hardware visual claim)')
