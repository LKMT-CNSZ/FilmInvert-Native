"""Frame-local clone strokes and exposure masks; compute/display are separated."""
from i18n import tr as _t, trf as _tf, ui as _ui
import copy
import numpy as np
from PySide6.QtCore import Qt,Signal,QPointF
from PySide6.QtGui import QPainter,QPen,QColor,QKeySequence,QShortcut
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,QDoubleSpinBox,QCheckBox
from native_widgets import ImageCanvas
import local_edits
from input_color import display_working

class LocalCanvas(ImageCanvas):
    stroke=Signal(object)
    sourcePicked=Signal(object)
    def __init__(self):
        super().__init__();self.setAcceptDrops(False);self.setDragMode(self.DragMode.NoDrag)
        self.path=[];self.source=None;self.radius=.025;self.tool='clone';self.cursor_point=None
        self.setMouseTracking(True)
    def point(self,event):
        pos=self.mapToScene(event.position().toPoint());r=self.item.boundingRect()
        if not r.contains(pos):return None
        return [pos.x()/max(1,r.width()),pos.y()/max(1,r.height())]
    def mousePressEvent(self,event):
        if event.button()!=Qt.MouseButton.LeftButton:return super().mousePressEvent(event)
        p=self.point(event)
        if p is None:return
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            self.source=p;self.sourcePicked.emit(p);self.viewport().update();return
        self.path=[p];self.viewport().update()
    def mouseMoveEvent(self,event):
        p=self.point(event);self.cursor_point=p
        if p is not None and self.path and len(self.path)<local_edits.MAX_POINTS:
            if np.linalg.norm(np.array(p)-self.path[-1])>.001:self.path.append(p)
        self.viewport().update()
    def mouseReleaseEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and self.path:
            p=self.point(event)
            if p is not None and len(self.path)<local_edits.MAX_POINTS:self.path.append(p)
            points=self.path;self.path=[];self.stroke.emit(points);self.viewport().update()
        else:super().mouseReleaseEvent(event)
    def paintEvent(self,event):
        super().paintEvent(event)
        painter=QPainter(self.viewport());painter.setPen(QPen(QColor('#ededed'),1.5))
        r=self.item.boundingRect()
        def mapped(p):return self.mapFromScene(QPointF(p[0]*r.width(),p[1]*r.height()))
        if self.source:
            p=mapped(self.source);painter.drawLine(p.x()-7,p.y(),p.x()+7,p.y());painter.drawLine(p.x(),p.y()-7,p.x(),p.y()+7)
        if self.cursor_point and self.tool!='gradient':
            radius=self.radius*min(r.width(),r.height())*self.transform().m11();painter.drawEllipse(mapped(self.cursor_point),int(radius),int(radius))
        if self.path:
            points=self.path if self.tool!='gradient' else [self.path[0],self.path[-1]]
            for a,b in zip(points,points[1:]):painter.drawLine(mapped(a),mapped(b))

class LocalDialog(QDialog):
    applied=Signal(object)
    def __init__(self,preview,edits,parent=None):
        super().__init__(parent);_ui(self.setWindowTitle, _t('局部精修 · 仿制 / 曝光 / 渐变'));self.setModal(True);self.resize(1120,820)
        self.preview=preview;self.edits=local_edits.normalize(edits);self.history=[copy.deepcopy(self.edits)];self.index=0
        layout=QVBoxLayout(self)
        info=_ui(QLabel, _t('裁切前、未翻转的当前帧。仿制：Alt+单击选来源，再拖画覆盖尘点 / 划痕；曝光：拖画；渐变：从不调整端拖向完整调整端。\n滚轮缩放，适应按钮复位。笔画不自动跨帧复制；修复为手动仿制，来源越出图像的部分不处理。'))
        info.setWordWrap(True);layout.addWidget(info)
        row=QHBoxLayout();layout.addLayout(row);self.tool=QComboBox()
        for label,key in [(_t('仿制修复'),'clone'),(_t('曝光笔刷'),'brush'),(_t('线性渐变'),'gradient')]:_ui(self.tool.addItem, label,key)
        row.addWidget(self.tool)
        def spin(label,low,high,step,value):
            row.addWidget(_ui(QLabel, label));widget=QDoubleSpinBox();widget.setRange(low,high);widget.setSingleStep(step);widget.setValue(value);row.addWidget(widget);return widget
        self.radius=spin(_t('半径 / 短边 %'),.1,25,.5,2.5);self.feather=spin(_t('羽化 %'),0,100,5,50);self.opacity=spin(_t('强度 %'),0,100,5,100);self.ev=spin(_t('曝光 EV'),-4,4,.1,.5)
        self.tool.currentIndexChanged.connect(self.tool_changed);self.radius.valueChanged.connect(lambda v:setattr(self.canvas,'radius',v/100))
        self.canvas=LocalCanvas();self.canvas.stroke.connect(self.add_stroke);layout.addWidget(self.canvas,1)
        self.message=_ui(QLabel);layout.addWidget(self.message)
        buttons=QHBoxLayout();layout.addLayout(buttons)
        self.undo=_ui(QPushButton, _t('撤销笔画'));self.redo=_ui(QPushButton, _t('重做笔画'));clear=_ui(QPushButton, _t('清空局部编辑'));fit=_ui(QPushButton, _t('适应窗口'))
        for button in (self.undo,self.redo,clear,fit):buttons.addWidget(button)
        self.undo.clicked.connect(lambda:self.travel(-1));self.redo.clicked.connect(lambda:self.travel(1));clear.clicked.connect(self.clear)
        fit.clicked.connect(self.canvas.fit_image);self.compare=_ui(QCheckBox, _t('显示局部编辑'));self.compare.setChecked(True);self.compare.toggled.connect(self.refresh);buttons.addWidget(self.compare);buttons.addStretch()
        apply=_ui(QPushButton, _t('应用到当前帧'));apply.setProperty('role','primary');cancel=_ui(QPushButton, _t('取消'));buttons.addWidget(apply);buttons.addWidget(cancel)
        apply.clicked.connect(lambda:(self.applied.emit(copy.deepcopy(self.edits)),self.accept()));cancel.clicked.connect(self.reject)
        QShortcut(QKeySequence('Ctrl+Z'),self,activated=lambda:self.travel(-1));QShortcut(QKeySequence('Ctrl+Shift+Z'),self,activated=lambda:self.travel(1))
        self.tool_changed();self.refresh()
    def tool_changed(self):
        self.canvas.tool=self.tool.currentData();gradient=self.canvas.tool=='gradient'
        self.radius.setEnabled(not gradient);self.feather.setEnabled(not gradient);self.ev.setEnabled(self.canvas.tool!='clone')
    def add_stroke(self,points):
        if len(self.edits)>=local_edits.MAX_EDITS:_ui(self.message.setText, _t('已达 256 笔上限，请撤销或清空部分笔画。'));return
        kind=self.tool.currentData();op={'kind':kind,'points':points,'opacity':self.opacity.value()/100}
        if kind=='gradient':
            op['points']=[points[0],points[-1]];op['ev']=self.ev.value()
            if np.linalg.norm(np.array(points[0])-points[-1])<1e-5:_ui(self.message.setText, _t('渐变端点不能重合。'));return
        else:
            op.update(radius=self.radius.value()/100,feather=self.feather.value()/100)
            if kind=='clone':
                if self.canvas.source is None:_ui(self.message.setText, _t('先 Alt+单击选择仿制来源。'));return
                op['offset']=(np.array(self.canvas.source)-points[0]).tolist()
            else:op['ev']=self.ev.value()
        self.edits=local_edits.normalize(self.edits+[op]);self.commit()
    def commit(self):
        self.history=self.history[:self.index+1]+[copy.deepcopy(self.edits)];self.index+=1
        # Keep at most 51 reversible states in this modal session.
        if len(self.history)>51:self.history.pop(0);self.index-=1
        self.refresh()
    def clear(self):self.edits=[];self.commit()
    def travel(self,delta):
        target=self.index+delta
        if 0<=target<len(self.history):self.index=target;self.edits=copy.deepcopy(self.history[target]);self.refresh()
    def refresh(self):
        base=self.preview['working_pixels'];space=self.preview['working_space']
        out=local_edits.apply(base,self.edits,space) if self.compare.isChecked() else base
        self.canvas.set_pixels(display_working(out,space),display=display_working(out,space,self.preview.get('monitor')))
        self.undo.setEnabled(self.index>0);self.redo.setEnabled(self.index<len(self.history)-1)
        _ui(self.message.setText, _tf('{0} 笔 · 当前为适应预览；精细尘点请应用后用原图 100% 或全尺寸导出复核。', len(self.edits)))
