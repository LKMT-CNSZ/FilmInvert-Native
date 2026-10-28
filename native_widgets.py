"""Qt Widgets canvas, histogram, curve editor and parameter rows."""
from i18n import tr as _t, trf as _tf, ui as _ui
import math
import numpy as np
from PySide6.QtCore import Qt,Signal,QPointF,QRectF
from PySide6.QtGui import QColor,QPainter,QPen,QPainterPath,QImage,QPixmap,QColorSpace,QBrush,QIcon
from PySide6.QtWidgets import QWidget,QGraphicsView,QGraphicsScene,QGraphicsPixmapItem,QGraphicsEllipseItem,QGraphicsSimpleTextItem,QGraphicsItem,QHBoxLayout,QLabel,QSlider,QDoubleSpinBox
import core


def photo_icon(pixmap):
    """Keep photograph colors when Qt paints a selected list item."""
    icon=QIcon(pixmap)
    icon.addPixmap(pixmap,QIcon.Mode.Selected,QIcon.State.Off)
    icon.addPixmap(pixmap,QIcon.Mode.Selected,QIcon.State.On)
    return icon

def monitor_image(image,profile=None):
    """Disposable sRGB thumbnail rendering; never change cached bytes."""
    if not profile:return image
    from input_color import profile_space
    image=image.copy();image.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
    return image.convertedToColorSpace(profile_space(profile,True))


def image_from_array(array):
    a = np.ascontiguousarray(array,dtype=np.uint8)
    image = QImage(a.data,a.shape[1],a.shape[0],a.strides[0],QImage.Format.Format_RGB888).copy()
    image.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
    return image

class ParameterRow(QWidget):
    changed = Signal(float)
    committed = Signal()
    def __init__(self,label,key,minimum,maximum,step,default=0):
        super().__init__(); self.key=key; self.step=step; self.minimum=minimum
        layout=QHBoxLayout(self); layout.setContentsMargins(0,3,0,3)
        text=_ui(QLabel, label); text.setMinimumWidth(74); layout.addWidget(text)
        self.slider=QSlider(Qt.Orientation.Horizontal); _ui(self.slider.setAccessibleName, label)
        self.slider.setObjectName('slider_'+key)
        self.slider.setRange(0,round((maximum-minimum)/step)); layout.addWidget(self.slider,1)
        self.spin=QDoubleSpinBox(); self.spin.setRange(minimum,maximum); self.spin.setSingleStep(step)
        self.spin.setDecimals(0 if step >= 1 else 2); self.spin.setFixedWidth(92)
        _ui(self.spin.setAccessibleName, label+_t('数值')); self.spin.setObjectName('value_'+key)
        self.spin.setKeyboardTracking(False); layout.addWidget(self.spin)
        self.set_value(default)
        self.slider.valueChanged.connect(self._slide)
        self.spin.valueChanged.connect(self._spin)
        self.slider.sliderReleased.connect(self.committed.emit)
        self.spin.editingFinished.connect(self.committed.emit)
    def set_value(self,value):
        self.slider.blockSignals(True); self.spin.blockSignals(True)
        self.slider.setValue(round((float(value)-self.minimum)/self.step)); self.spin.setValue(float(value))
        self.slider.blockSignals(False); self.spin.blockSignals(False)
    def _slide(self,value):
        number=self.minimum+value*self.step
        self.spin.blockSignals(True); self.spin.setValue(number); self.spin.blockSignals(False)
        self.changed.emit(float(number))
    def _spin(self,value):
        self.slider.blockSignals(True); self.slider.setValue(round((value-self.minimum)/self.step)); self.slider.blockSignals(False)
        self.changed.emit(float(value)); self.committed.emit()

def dropped_paths(mime):
    """Keep every local URL in the drop; never open a remote address."""
    return [url.toLocalFile() for url in mime.urls() if url.isLocalFile() and url.toLocalFile()]


class ImageCanvas(QGraphicsView):
    sampled=Signal(float,float)
    pathDropped=Signal(str)
    pathsDropped=Signal(object)
    zoomChanged=Signal(object)
    viewportChanged=Signal()
    def __init__(self):
        super().__init__(); self.setObjectName('imageCanvas'); _ui(self.setAccessibleName, _t('胶片预览画布'))
        self.scene_=QGraphicsScene(self); self.setScene(self.scene_)
        self.item=QGraphicsPixmapItem(); _ui(self.scene_.addItem, self.item)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor('#111111')); self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag); self.setAcceptDrops(True)
        self.fit_mode=True; self.sampling=False
        self.region_mode=False;self.region_shape=None;self.setting_region=False
        self.marker_items=[]
        self.clipping=False;self.pixels=None
        self.clipping_item=QGraphicsPixmapItem(self.item);self.clipping_item.setZValue(1)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.horizontalScrollBar().valueChanged.connect(self.region_changed)
        self.verticalScrollBar().valueChanged.connect(self.region_changed)
    def region_changed(self,*args):
        if self.region_mode and not self.setting_region:self.viewportChanged.emit()
    def begin_region(self,shape):
        old=self.sceneRect();point=self.mapToScene(self.viewport().rect().center())
        center=(point.x()/max(1,old.width()),point.y()/max(1,old.height()))
        self.setting_region=True;self.region_mode=True;self.region_shape=shape;self.fit_mode=False
        self.item.setPixmap(QPixmap());self.clipping_item.setPixmap(QPixmap());self.item.setPos(0,0)
        self.scene_.setSceneRect(0,0,shape[1],shape[0]);self.resetTransform()
        self.centerOn(center[0]*shape[1],center[1]*shape[0]);self.setting_region=False
        self.zoomChanged.emit(_t('100% · 可见区域'))
    def region_box(self):
        rect=self.mapToScene(self.viewport().rect()).boundingRect();h,w=self.region_shape
        return (max(0,int(math.floor(rect.left()))-64),max(0,int(math.floor(rect.top()))-64),
                min(w,int(math.ceil(rect.right()))+64),min(h,int(math.ceil(rect.bottom()))+64))
    def set_region_pixels(self,array,box,shape,display=None,diagnostics=None):
        self.setting_region=True;self.region_mode=True;self.region_shape=shape;self.fit_mode=False
        self.pixels=array;self.diagnostics=diagnostics;self.item.setPos(box[0],box[1])
        self.item.setPixmap(QPixmap.fromImage(image_from_array(array if display is None else display)))
        self.scene_.setSceneRect(0,0,shape[1],shape[0]);self.update_clipping();self.setting_region=False
    def end_region(self):
        self.setting_region=True;self.region_mode=False;self.region_shape=None
        self.item.setPos(0,0);self.setting_region=False
    def set_pixels(self,array,full=False,display=None,diagnostics=None):
        self.end_region()
        self.diagnostics=diagnostics
        self.pixels=array
        self.item.setPixmap(QPixmap.fromImage(image_from_array(array if display is None else display)))
        self.update_clipping()
        self.scene_.setSceneRect(self.item.boundingRect())
        if self.fit_mode:
            self.fit_image()
        elif full:
            self.resetTransform(); self.zoomChanged.emit('100%')
    def clear(self):
        self.end_region()
        self.pixels=None;self.item.setPixmap(QPixmap());self.clipping_item.setPixmap(QPixmap())
        for item in self.marker_items:self.scene_.removeItem(item)
        self.marker_items=[];self.scene_.setSceneRect(self.item.boundingRect())
    def set_clipping(self,on):
        self.clipping=on;self.update_clipping()
    def update_clipping(self):
        self.clipping_item.setVisible(self.clipping)
        if not self.clipping or self.pixels is None:return
        if getattr(self,'diagnostics',None) is None:
            self.clipping_item.setPixmap(QPixmap());return
        high=self.diagnostics['high'];low=self.diagnostics['low']
        overlay=np.zeros((*high.shape,4),np.uint8)
        overlay[low]=[45,120,255,190];overlay[high]=[255,65,40,200];overlay[high&low]=[230,60,235,200]
        im=QImage(overlay.data,overlay.shape[1],overlay.shape[0],overlay.strides[0],QImage.Format.Format_RGBA8888).copy()
        self.clipping_item.setPixmap(QPixmap.fromImage(im))
    def paintEvent(self,event):
        super().paintEvent(event)
        if self.item.pixmap().isNull():
            painter=QPainter(self.viewport());painter.setPen(QColor('#b8b8b8'))
            font=painter.font();font.setPointSize(18);painter.setFont(font)
            _ui(painter.drawText, self.viewport().rect(),Qt.AlignmentFlag.AlignCenter,_t('拖入一张开始修片，多张显示在底部照片导航\n\nCtrl+O 打开 RAW / TIFF / PNG / JPEG'))
    def fit_image(self):
        self.fit_mode=True
        if not self.item.pixmap().isNull():
            self.fitInView(self.item,Qt.AspectRatioMode.KeepAspectRatio)
        self.zoomChanged.emit(_t('适应窗口'))
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self.fit_mode:self.fit_image()
        elif self.region_mode:self.region_changed()
    def set_sampling(self,on):
        self.sampling=on
        self.setDragMode(QGraphicsView.DragMode.NoDrag if on else QGraphicsView.DragMode.ScrollHandDrag)
        self.viewport().setCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.OpenHandCursor)
    def set_markers(self,points):
        for item in self.marker_items:self.scene_.removeItem(item)
        self.marker_items=[];rect=self.item.boundingRect()
        for i,point in enumerate(points):
            if point is None:continue
            marker=QGraphicsEllipseItem(-5,-5,10,10)
            marker.setPen(QPen(QColor('#ededed'),2));marker.setBrush(QBrush(QColor(16,16,16,160)))
            marker.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            marker.setPos(point[0]*rect.width(),point[1]*rect.height());marker.setZValue(10)
            label=_ui(QGraphicsSimpleTextItem, str(i+1),marker);label.setBrush(QBrush(QColor('#ededed')));label.setPos(7,-9)
            _ui(self.scene_.addItem, marker);self.marker_items.append(marker)
    def mousePressEvent(self,event):
        if self.sampling and event.button()==Qt.MouseButton.LeftButton and not self.item.pixmap().isNull():
            point=self.mapToScene(event.position().toPoint()); rect=self.sceneRect() if self.region_mode else self.item.boundingRect()
            if rect.contains(point):self.sampled.emit(point.x()/rect.width(),point.y()/rect.height())
            return
        super().mousePressEvent(event)
    def wheelEvent(self,event):
        if self.region_mode:
            self.verticalScrollBar().setValue(self.verticalScrollBar().value()-event.angleDelta().y()*2)
            event.accept();return
        self.fit_mode=False
        scale=1.2 if event.angleDelta().y()>0 else 1/1.2
        value=self.transform().m11()*scale
        if .02<value<30:self.scale(scale,scale)
        self.zoomChanged.emit(_tf('{0:.0f}%（当前预览）', self.transform().m11()*100))
    def dragEnterEvent(self,event):
        if dropped_paths(event.mimeData()):event.acceptProposedAction()
        else:event.ignore()
    def dragMoveEvent(self,event):self.dragEnterEvent(event)
    def dropEvent(self,event):
        paths=dropped_paths(event.mimeData())
        if paths:
            if len(paths)==1:self.pathDropped.emit(paths[0])
            self.pathsDropped.emit(paths);event.acceptProposedAction()
        else:event.ignore()

class Histogram(QWidget):
    def __init__(self):
        super().__init__();self.bins=None;self.setMinimumHeight(116);self.setMaximumHeight(140)
        _ui(self.setAccessibleName, _t('RGB 对数直方图'))
    def set_bins(self,bins):self.bins=bins;self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#161616'));p.setRenderHint(QPainter.RenderHint.Antialiasing)
        box=QRectF(9,9,self.width()-18,self.height()-32)
        p.setPen(QPen(QColor('#363636'),1))
        for i in range(5):
            x=box.left()+box.width()*i/4;p.drawLine(QPointF(x,box.top()),QPointF(x,box.bottom()))
        if self.bins is None:
            p.setPen(QColor('#a8a8a8'));_ui(p.drawText, self.rect(),Qt.AlignmentFlag.AlignCenter,_t('打开图像后显示 RGB 直方图'));return
        peak=max(1,max(float(np.log1p(b).max()) for b in self.bins))
        for bins,color in zip(self.bins,('#e27878','#78bd90','#7ba4db')):
            path=QPainterPath()
            for i,v in enumerate(bins):
                point=QPointF(box.left()+i/255*box.width(),box.bottom()-float(np.log1p(v))/peak*box.height())
                path.moveTo(point) if i==0 else path.lineTo(point)
            p.setPen(QPen(QColor(color),1.2));p.drawPath(path)
        p.setPen(QColor('#a8a8a8'));labels=QRectF(9,self.height()-24,self.width()-18,18)
        _ui(p.drawText, labels,Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,'0')
        _ui(p.drawText, labels,Qt.AlignmentFlag.AlignCenter,_t('RGB · 对数纵轴'))
        _ui(p.drawText, labels,Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,'255')

class CurveEditor(QWidget):
    changed=Signal(object);committed=Signal()
    def __init__(self):
        super().__init__();self.points=[[0.,0.],[1.,1.]];self.active=-1;self.dragging=False
        self.setMinimumHeight(195);self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        _ui(self.setAccessibleName, _t('影调曲线：双击加点或删除，拖动调整'))
    def area(self):return QRectF(14,14,self.width()-28,self.height()-28)
    def coordinate(self,point):
        r=self.area();return [float(np.clip((point.x()-r.left())/r.width(),0,1)),float(np.clip(1-(point.y()-r.top())/r.height(),0,1))]
    def position(self,row):
        r=self.area();return QPointF(r.left()+row[0]*r.width(),r.bottom()-row[1]*r.height())
    def nearest(self,point):
        best=-1;distance=13
        for i,row in enumerate(self.points):
            q=self.position(row);d=math.hypot(q.x()-point.x(),q.y()-point.y())
            if d<distance:best,distance=i,d
        return best
    def set_points(self,points):self.points=core.normalize_curve(points);self.active=-1;self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#161616'));p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r=self.area();p.setPen(QPen(QColor('#383838'),1))
        for i in range(5):
            x=r.left()+r.width()*i/4;y=r.top()+r.height()*i/4
            p.drawLine(QPointF(x,r.top()),QPointF(x,r.bottom()));p.drawLine(QPointF(r.left(),y),QPointF(r.right(),y))
        p.setPen(QPen(QColor('#666666'),1,Qt.PenStyle.DashLine));p.drawLine(r.bottomLeft(),r.topRight())
        lut=core.curve_lut(self.points,256);path=QPainterPath()
        for i,v in enumerate(lut):
            point=self.position([i/255,float(v)])
            path.moveTo(point) if i==0 else path.lineTo(point)
        p.setPen(QPen(QColor('#eeeeee'),2));p.drawPath(path)
        for i,row in enumerate(self.points):
            p.setBrush(QColor('#eeeeee' if i==self.active else '#242424'));p.drawEllipse(self.position(row),5,5)
    def mousePressEvent(self,event):
        self.active=self.nearest(event.position());self.dragging=self.active>=0;self.setFocus();self.update()
    def mouseMoveEvent(self,event):
        if not self.dragging:return
        x,y=self.coordinate(event.position());i=self.active;pts=self.points
        x=pts[i][0] if i in (0,len(pts)-1) else max(pts[i-1][0]+.005,min(pts[i+1][0]-.005,x))
        y=max(pts[i-1][1] if i else 0,min(pts[i+1][1] if i<len(pts)-1 else 1,y))
        pts[i]=[x,y];self.changed.emit([row[:] for row in pts]);self.update()
    def mouseReleaseEvent(self,event):
        if self.dragging:self.dragging=False;self.committed.emit()
    def mouseDoubleClickEvent(self,event):
        i=self.nearest(event.position())
        if 0<i<len(self.points)-1:self.points.pop(i);self.active=-1
        elif i<0 and len(self.points)<16:
            x,y=self.coordinate(event.position());i=next((i for i,p in enumerate(self.points) if p[0]>x),-1)
            if i<=0 or x-self.points[i-1][0]<.005 or self.points[i][0]-x<.005:return
            y=max(self.points[i-1][1],min(self.points[i][1],y));self.points.insert(i,[x,y]);self.active=i
        else:return
        self.changed.emit([row[:] for row in self.points]);self.committed.emit();self.update()
    def keyPressEvent(self,event):
        if event.key() in (Qt.Key.Key_Delete,Qt.Key.Key_Backspace) and 0<self.active<len(self.points)-1:
            self.points.pop(self.active);self.active=-1;self.changed.emit([row[:] for row in self.points]);self.committed.emit();self.update()
        else:super().keyPressEvent(event)
