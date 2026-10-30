"""Per-display preferences and scene-referred HDR range visualization."""
import json
import numpy as np
from PySide6.QtCore import Qt,QTimer,QRectF,QPointF,QUrl
from PySide6.QtGui import QPainter,QColor,QPen,QPainterPath,QDesktopServices
from PySide6.QtWidgets import QWidget,QDialog,QVBoxLayout,QHBoxLayout,QLabel,QCheckBox,QDoubleSpinBox,QPushButton
from i18n import tr as _t,trf as _tf,ui as _ui


def range_colors(linear,ceiling):
    maximum=np.maximum(np.asarray(linear).max(axis=-1),0)
    out=np.repeat(np.minimum(maximum,.6)[...,None],3,axis=-1).astype(np.float32)
    out[maximum>1]=[.85,.7,0];out[maximum>max(1,ceiling)]=[1.,.03,.03]
    return out


class HDRHistogram(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent);self.bins=None;self.ceiling=0.;self.maximum=0.;self.beyond=0.;self.setMinimumHeight(132);self.setMaximumHeight(155)
        _ui(self.setAccessibleName,_t('HDR 扩展直方图'))
    def set_data(self,linear,headroom=0):
        self.ceiling=float(headroom)
        if linear is None:self.bins=None;self.update();return
        a=np.asarray(linear)
        maximum=np.maximum(a.max(axis=-1),0)
        self.bins=np.histogram(np.clip(np.log2(np.maximum(maximum,2.**-10)),-8,6),bins=280,range=(-8,6))[0]
        self.maximum=float(np.log2(max(float(maximum.max()),1e-8)))
        self.beyond=float(np.mean(maximum>2.**self.ceiling));self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#161616'));box=QRectF(10,8,self.width()-20,self.height()-49)
        for start,end,color in ((-8,0,'#252a30'),(0,max(0,self.ceiling),'#343225'),(max(0,self.ceiling),6,'#3c2525')):
            p.fillRect(QRectF(box.left()+(start+8)/14*box.width(),box.top(),(end-start)/14*box.width(),box.height()),QColor(color))
        if self.bins is not None:
            peak=max(1,float(np.log1p(self.bins).max()));path=QPainterPath()
            for i,n in enumerate(self.bins):
                point=QPointF(box.left()+i/(len(self.bins)-1)*box.width(),box.bottom()-np.log1p(n)/peak*box.height())
                path.moveTo(point) if i==0 else path.lineTo(point)
            p.setPen(QPen(QColor('#dedede'),1.3));p.drawPath(path)
        for value,label in ((-8,'−8'),(0,'SDR 0'),(6,'+6 EV')):
            x=box.left()+(value+8)/14*box.width();p.setPen(QColor('#8b9aa9'));p.drawLine(QPointF(x,box.top()),QPointF(x,box.bottom()))
            p.drawText(QRectF(min(max(0,x-18),self.width()-52),box.bottom()+2,54,17),label)
        p.setPen(QColor('#bcbcbc'))
        text=_tf('屏幕余量 +{0:.1f} EV · 超范围 {1:.1f}%',self.ceiling,self.beyond*100) if self.bins is not None else _t('打开图像后显示 HDR 范围')
        _ui(p.drawText,QRectF(10,self.height()-22,self.width()-20,20),Qt.AlignmentFlag.AlignLeft,text)


class DisplayDialog(QDialog):
    def __init__(self,window):
        super().__init__(window);self.window=window;self.info={};self.loading=False
        _ui(self.setWindowTitle,_t('HDR 显示适配'));self.resize(540,360);layout=QVBoxLayout(self)
        self.status=QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        self.white_auto=_ui(QCheckBox,_t('参考白跟随 Windows'));layout.addWidget(self.white_auto)
        self.white=QDoubleSpinBox();self.white.setRange(80,1000);self.white.setSuffix(' nits');layout.addWidget(self.white)
        self.peak_auto=_ui(QCheckBox,_t('峰值跟随屏幕报告'));layout.addWidget(self.peak_auto)
        self.peak=QDoubleSpinBox();self.peak.setRange(80,10000);self.peak.setSuffix(' nits');layout.addWidget(self.peak)
        note=_ui(QLabel,_t('这里只适配当前显示器，不写入照片和导出。报告不可靠时请使用校准结果手动设置；估计值不是实测值。'));note.setWordWrap(True);layout.addWidget(note)
        row=QHBoxLayout();settings=_ui(QPushButton,_t('打开 Windows 显示设置'));settings.clicked.connect(lambda:QDesktopServices.openUrl(QUrl('ms-settings:display')));row.addWidget(settings)
        refresh=_ui(QPushButton,_t('重新检测'));refresh.clicked.connect(self.refresh);row.addWidget(refresh)
        close=_ui(QPushButton,_t('关闭'));close.clicked.connect(self.close);row.addWidget(close);layout.addLayout(row)
        for control in (self.white_auto,self.peak_auto):control.toggled.connect(self.save)
        self.white.valueChanged.connect(self.save);self.peak.valueChanged.connect(self.save)
        self.timer=QTimer(self);self.timer.setInterval(1500);self.timer.timeout.connect(self.poll);self.timer.start();self.refresh()
    def poll(self):
        if self.isVisible() and self.window.hdr_canvas.active:
            info=self.window.hdr_canvas.info
            if info!=self.info:self.refresh()
    def refresh(self):
        try:
            if self.window.hdr_canvas.active:self.window.hdr_canvas.check_display()
            if self.window.hdr_canvas.active:info=dict(self.window.hdr_canvas.info)
            else:
                from hdr_display import inspect_display
                info=inspect_display(int(self.window.canvas.viewport().winId()))
            self.info=info
            from hdr_display import display_mapping
            white,peak,headroom=display_mapping(info)
            profiles=json.loads(self.window.settings.value('hdrDisplayProfilesV2','{}'));profile=profiles.get(info['name'],{})
            self.loading=True
            self.white_auto.setEnabled(True);self.peak_auto.setEnabled(True)
            self.white_auto.setChecked(profile.get('white') is None);self.peak_auto.setChecked(profile.get('peak') is None)
            self.white.setValue(profile.get('white') or white);self.peak.setValue(profile.get('peak') or peak)
            self.white.setEnabled(not self.white_auto.isChecked());self.peak.setEnabled(not self.peak_auto.isChecked())
            text=_tf('屏幕：{0}\nWindows HDR：{1} · {2} bit\n系统参考白：{3:.0f} nits · 屏幕报告峰值：{4:.0f} nits',info['name'],_t('已开启') if info['hdr_active'] else _t('未开启'),info.get('bits',0),info.get('sdr_white_nits',203),info.get('max_nits',0))
            if info.get('white_estimated') or info.get('peak_estimated'):text+=_t('\n存在缺失或异常报告，当前使用明确标注的估计值。')
            _ui(self.status.setText,text)
        except Exception as error:
            _ui(self.status.setText,_t('无法读取当前屏幕：')+str(error));self.info={}
            for control in (self.white_auto,self.white,self.peak_auto,self.peak):control.setEnabled(False)
        finally:self.loading=False
    def save(self,*args):
        if self.loading or not self.info:return
        try:profiles=json.loads(self.window.settings.value('hdrDisplayProfilesV2','{}'))
        except (ValueError,TypeError):profiles={}
        profiles[self.info['name']]={'white':None if self.white_auto.isChecked() else self.white.value(),'peak':None if self.peak_auto.isChecked() else self.peak.value()}
        self.window.settings.setValue('hdrDisplayProfilesV2',json.dumps(profiles))
        self.white.setEnabled(not self.white_auto.isChecked());self.peak.setEnabled(not self.peak_auto.isChecked())
        if self.window.hdr_canvas.active:
            self.window.import_reveal.stop();self.window.hdr_canvas.adapt_display();self.window.canvas.viewport().update();self.window.refresh_hdr_scope()
    def closeEvent(self,event):self.timer.stop();event.accept()
