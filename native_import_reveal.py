"""Disposable soft reveal on opening a negative or changing frames."""
import time,os
import numpy as np
from PySide6.QtCore import QObject,QTimer,QEvent,Qt,QRectF,QPointF,Signal
from PySide6.QtGui import QPixmap,QPainter,QImage,QLinearGradient,QColor
from PySide6.QtWidgets import QApplication,QGraphicsPixmapItem


def mixed(original,target,progress):
    """Reveal all channels together behind a 10%-wide soft moving edge."""
    if progress<=0:return original.copy()
    if progress>=1:return target.copy()
    xx=(np.arange(target.shape[1],dtype=np.float32)+.5)/target.shape[1]
    p=progress*progress*(3-2*progress)
    alpha=np.clip((p*1.1-xx)/.1,0,1);alpha=alpha*alpha*(3-2*alpha)
    alpha=alpha[None,:,None]
    values=original.astype(np.float32)*(1-alpha)+target*alpha
    return (np.floor(values+.5) if target.dtype==np.uint8 else values).astype(target.dtype)


def system_animations():
    if QApplication.platformName()!='windows':return False
    if os.name!='nt':return True
    import ctypes as C
    enabled=C.c_int(1)
    ok=C.windll.user32.SystemParametersInfoW(0x1042,0,C.byref(enabled),0)
    return bool(enabled.value) if ok else True


class SoftRevealItem(QGraphicsPixmapItem):
    """Keep the source texture; only composite the narrow feather during painting."""
    def __init__(self,parent):
        super().__init__(parent);self.progress=0.
        self.setShapeMode(QGraphicsPixmapItem.ShapeMode.BoundingRectShape)
    def set_progress(self,value):
        self.progress=value;self.update()
    def paint(self,painter,option,widget=None):
        source=self.pixmap();w=source.width();h=source.height();p=self.progress
        if p<=0:painter.drawPixmap(0,0,source);return
        if p>=1 or not w:return
        p=p*p*(3-2*p);edge=p*1.1*w;left=edge-.1*w
        start=max(0,int(np.floor(left)));end=min(w,int(np.ceil(edge)))
        if end<w:
            box=QRectF(max(0,end),0,w-max(0,end),h);painter.drawPixmap(box,source,box)
        if end<=start:return
        # A small premultiplied tile avoids a full-image NumPy mix and texture upload.
        tile=QImage(end-start,h,QImage.Format.Format_ARGB32_Premultiplied);tile.fill(0)
        blend=QPainter(tile);blend.translate(-start,0);blend.drawPixmap(0,0,source)
        blend.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        mask=QLinearGradient(left,0,edge,0)
        for i in range(17):
            t=i/16;mask.setColorAt(t,QColor(0,0,0,round(255*t*t*(3-2*t))))
        blend.fillRect(QRectF(start,0,end-start,h),mask);blend.end()
        painter.drawImage(QPointF(start,0),tile)


class ImportReveal(QObject):
    finished=Signal()
    def __init__(self,window,canvas,settings):
        super().__init__(canvas);self.window=window;self.canvas=canvas;self.settings=settings
        self.pending=None;self.active=False;self.original=self.target=None;self.hdr_original=self.hdr_target=None
        self.overlay=SoftRevealItem(canvas.item);self.overlay.setZValue(100);self.overlay.hide()
        self.hdr_future=None;self.hdr_signature=None;self.hdr_prepared=False
        self.timer=QTimer(self);self.timer.setTimerType(Qt.TimerType.PreciseTimer);self.timer.setInterval(16);self.timer.timeout.connect(self.tick)
        self.duration=1.6;self.starts=0
        QApplication.instance().installEventFilter(self)
    def arm(self,mode):
        self.cancel()
        if mode not in ('color_negative','bw_negative') or not system_animations():return
        if not self.settings.value('importRevealEnabled',True,type=bool):return
        self.pending=object()
    def cancel(self):
        self.pending=None;self.stop()
    def stop(self):
        was_active=self.active;self.active=False;self.timer.stop();self.overlay.hide();self.overlay.setPixmap(QPixmap())
        hdr=getattr(self.canvas,'hdr_controller',None)
        if self.hdr_future is not None:self.hdr_future.cancel();self.hdr_future=None
        if was_active and self.hdr_target is not None and hasattr(hdr,'end_reveal'):hdr.end_reveal()
        self.hdr_prepared=False;self.hdr_signature=None
        if was_active and self.hdr_target is not None and hdr is not None and hdr.active:hdr.set_linear(self.hdr_target)
        self.original=self.target=self.hdr_original=self.hdr_target=None
        self.canvas.clipping_item.setVisible(self.canvas.clipping and not (hdr and hdr.active))
        if was_active:self.finished.emit()
    def start(self,original,target,hdr_original=None,hdr_target=None):
        self.pending=None;self.stop()
        if original.shape!=target.shape or target.shape[0]*target.shape[1]>2_100_000:return
        if hdr_target is not None and (hdr_original is None or hdr_original.shape!=hdr_target.shape):return
        self.original=original;self.target=target;self.hdr_original=hdr_original;self.hdr_target=hdr_target
        self.active=True;self.starts+=1;self.started=time.perf_counter()
        hdr=getattr(self.canvas,'hdr_controller',None)
        if hdr_target is not None and hdr is not None and hasattr(hdr,'prepare_reveal'):
            self.hdr_future,self.hdr_signature=hdr.prepare_reveal(hdr_original,hdr_target);self.started=None
        elif hdr_target is None:
            from native_widgets import image_from_array
            self.overlay.setPixmap(QPixmap.fromImage(image_from_array(original)))
        self.canvas.clipping_item.hide();self.tick(0.)
        if self.active:self.timer.start()
    def tick(self,progress=None):
        if not self.active:return
        hdr=getattr(self.canvas,'hdr_controller',None)
        if self.hdr_future is not None:
            if not self.hdr_future.done():return
            future=self.hdr_future;self.hdr_future=None
            try:
                if not hdr or not hdr.active or not hdr.activate_reveal(future.result(),self.hdr_signature):self.stop();return
            except Exception:self.stop();return  # A disposable animation must not fail an edit.
            self.hdr_prepared=True;self.started=time.perf_counter()
        progress=(time.perf_counter()-self.started)/self.duration if progress is None else progress
        if progress>=1:self.stop();return
        if self.hdr_target is not None:
            if hdr is None or not hdr.active:self.stop();return
            if self.hdr_prepared:
                if not hdr.set_reveal_progress(progress):self.stop()
            else:hdr.set_linear(mixed(self.hdr_original,self.hdr_target,progress))
        else:
            self.overlay.set_progress(progress);self.overlay.show()
    def eventFilter(self,watched,event):
        if (self.active or self.pending) and event.type() in (QEvent.Type.MouseButtonPress,QEvent.Type.Wheel,QEvent.Type.KeyPress,QEvent.Type.TouchBegin):
            if getattr(watched,'isWidgetType',lambda:False)() and watched.window() is self.window:self.cancel()
        return False
