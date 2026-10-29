"""Native HDR presentation in the existing editable QGraphicsView viewport."""
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import Qt,QTimer,Signal,QObject
from PySide6.QtWidgets import QWidget,QApplication

REVEAL_POOL=ThreadPoolExecutor(max_workers=1,thread_name_prefix='HDR-reveal')


class PreparedReveal:
    """Display-linear FP16 endpoints, prepared once off the Qt event thread."""
    def __init__(self,original,target,bounds,width,height,white,peak):
        from hdr_display import scrgb_frame
        if width*height>8_388_608:raise ValueError('HDR animation exceeds the 8 MP transient budget')
        self.original=scrgb_frame(visible_frame(original,bounds,width,height),width,height,white,peak)
        self.target=scrgb_frame(visible_frame(target,bounds,width,height),width,height,white,peak)
        self.out=self.original.copy();self.last_progress=0.;self.last_lo=0
        self.delta=np.subtract(self.target,self.original,dtype=np.float32)
        band=min(width,max(1,int(np.ceil(.1*original.shape[1]*width/(bounds[2]-bounds[0])))+2))
        self.scratch=np.empty((height,band,4),np.float32)
        self.xx=(bounds[0]+(np.arange(width,dtype=np.float32)+.5)*(bounds[2]-bounds[0])/width)/original.shape[1]
    def compose(self,progress):
        if progress<=0:return self.original
        if progress>=1:return self.target
        p=progress*progress*(3-2*progress);edge=p*1.1
        lo=int(np.searchsorted(self.xx,edge-.1));hi=int(np.searchsorted(self.xx,edge))
        if progress<self.last_progress:self.out[:]=self.original;self.last_lo=0
        self.out[:,self.last_lo:lo]=self.target[:,self.last_lo:lo]
        alpha=np.clip((edge-self.xx[lo:hi])/.1,0,1);alpha=(alpha*alpha*(3-2*alpha))[None,:,None]
        scratch=self.scratch[:,:hi-lo]
        np.multiply(self.delta[:,lo:hi],alpha,out=scratch)
        np.add(self.original[:,lo:hi],scratch,out=scratch);self.out[:,lo:hi]=scratch
        self.last_progress=progress;self.last_lo=lo
        return self.out


def visible_frame(linear,rect,width,height):
    """Sample the current scene transform; black padding never stretches the image."""
    left,top,right,bottom=rect;h,w=linear.shape[:2]
    xx=left+(np.arange(width,dtype=np.float32)+.5)*(right-left)/width-.5
    yy=top+(np.arange(height,dtype=np.float32)+.5)*(bottom-top)/height-.5
    valid=(yy[:,None]>=-.5)&(yy[:,None]<h-.5)&(xx[None,:]>=-.5)&(xx[None,:]<w-.5)
    xx=np.clip(xx,0,w-1);yy=np.clip(yy,0,h-1)
    ix=xx.astype(np.int32);iy=yy.astype(np.int32);fx=(xx-ix)[None,:,None];fy=(yy-iy)[:,None,None]
    jx=np.minimum(ix+1,w-1);jy=np.minimum(iy+1,h-1)
    out=(linear[iy[:,None],ix]*(1-fx)+linear[iy[:,None],jx]*fx)*(1-fy)
    out+=(linear[jy[:,None],ix]*(1-fx)+linear[jy[:,None],jx]*fx)*fy
    out[~valid]=0
    return out.astype(np.float32)


class NativeViewport(QWidget):
    def paintEngine(self):
        owner=getattr(self,'hdr_owner',None)
        return None if owner is not None and owner.active else super().paintEngine()


class HDRCanvasController(QObject):
    failed=Signal(str)
    def __init__(self,canvas):
        super().__init__(canvas);self.canvas=canvas;self.surface=None;self.linear=None;self.active=False
        self.reference_white=203.;self.canvas.viewport().hdr_owner=self
        self.reveal=None;self.reveal_signature=None;self.reveal_progress=0.;self.reveal_waiting=False
        self.timer=QTimer(self);self.timer.setInterval(1000);self.timer.timeout.connect(self.check_display)
    def start(self):
        self.stop()
        try:
            if QApplication.platformName()!='windows':raise RuntimeError('Native HDR editing requires the Windows platform plugin')
            from hdr_display import Surface
            viewport=self.canvas.viewport();ratio=viewport.devicePixelRatioF()
            self.surface=Surface(int(viewport.winId()),max(1,round(viewport.width()*ratio)),max(1,round(viewport.height()*ratio)))
            if not self.surface.capabilities()['hdr_active']:raise RuntimeError('Windows HDR is not enabled on this display')
            self.info=self.surface.capabilities()
            self.active=True;viewport.setAttribute(Qt.WidgetAttribute.WA_PaintOnScreen,True);self.timer.start();viewport.update()
        except Exception:
            self.stop();raise
    def stop(self):
        self.end_reveal()
        self.active=False;self.linear=None;self.timer.stop()
        if self.surface is not None:self.surface.close();self.surface=None
        self.canvas.viewport().setAttribute(Qt.WidgetAttribute.WA_PaintOnScreen,False);self.canvas.viewport().update()
    def check_display(self):
        if not self.active:return
        try:
            info=self.surface.capabilities()
            if not info['hdr_active']:raise RuntimeError('Windows HDR is no longer enabled on this display')
            if info!=self.info:self.info=info;self.canvas.viewport().update()
        except Exception as error:self.stop();self.failed.emit(str(error))
    def set_linear(self,linear):
        self.end_reveal();self.linear=linear;self.canvas.viewport().update()
    def view_signature(self):
        viewport=self.canvas.viewport();ratio=viewport.devicePixelRatioF()
        width=max(1,round(viewport.width()*ratio));height=max(1,round(viewport.height()*ratio))
        rect=self.canvas.mapToScene(viewport.rect()).boundingRect();pos=self.canvas.item.pos()
        bounds=(rect.left()-pos.x(),rect.top()-pos.y(),rect.left()-pos.x()+rect.width(),rect.top()-pos.y()+rect.height())
        from hdr_display import display_peak
        return (bounds,width,height,self.reference_white,display_peak(self.info.get('max_nits',1000)))
    def prepare_reveal(self,original,target):
        signature=self.view_signature();self.reveal_waiting=True
        return REVEAL_POOL.submit(PreparedReveal,original,target,*signature),signature
    def activate_reveal(self,frames,signature):
        if not self.active or signature!=self.view_signature():return False
        self.reveal_waiting=False;self.reveal=frames;self.reveal_signature=signature;self.reveal_progress=0.;return True
    def set_reveal_progress(self,progress):
        if self.reveal is None or self.reveal_signature!=self.view_signature():self.end_reveal();return False
        self.reveal_progress=progress;self.canvas.viewport().update();return True
    def end_reveal(self):
        self.reveal=None;self.reveal_signature=None;self.reveal_waiting=False
    def paint(self):
        if not self.active:return False
        try:
            if self.reveal_waiting:return True  # Keep the last backbuffer until preparation completes.
            viewport=self.canvas.viewport();ratio=viewport.devicePixelRatioF()
            width=max(1,round(viewport.width()*ratio));height=max(1,round(viewport.height()*ratio))
            if width*height>16_000_000:raise ValueError('HDR viewport exceeds the 16 MP display budget')
            if self.reveal is not None:
                if self.reveal_signature==self.view_signature():
                    self.surface.present_prepared(self.reveal.compose(self.reveal_progress),width,height);return True
                self.end_reveal()
            rect=self.canvas.mapToScene(viewport.rect()).boundingRect();pos=self.canvas.item.pos()
            bounds=(rect.left()-pos.x(),rect.top()-pos.y(),rect.left()-pos.x()+rect.width(),rect.top()-pos.y()+rect.height())
            if self.linear is None:frame=np.zeros((height,width,3),np.float32)
            else:frame=visible_frame(self.linear,bounds,width,height)
            self.surface.present(frame,width,height,self.reference_white)
            return True
        except Exception as error:self.stop();self.failed.emit(str(error));return False
