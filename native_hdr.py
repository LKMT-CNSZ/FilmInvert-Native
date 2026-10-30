"""Native exposure-merge workflow; no decoding or merging on the GUI thread."""
from pathlib import Path
import threading,traceback
from PySide6.QtCore import QThread,Signal,Qt,QTimer,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QTableWidget,
    QTableWidgetItem,QDoubleSpinBox,QComboBox,QCheckBox,QFileDialog,QProgressBar,QMessageBox,QWidget,QApplication)
from i18n import tr as _t,trf as _tf,ui as _ui


def show_hdr_unavailable(parent,error):
    dialog=getattr(parent,'hdr_failure_dialog',None)
    if dialog is not None and dialog.isVisible():return dialog
    text=str(error)
    system_off=any(part in text.lower() for part in ('hdr is not enabled','hdr is no longer enabled','hdr enabled','未开启 windows hdr'))
    message=_t('请先在 Windows「设置 → 系统 → 显示 → HDR」中，为软件所在的显示器开启「使用 HDR」，再返回软件重试。\n\n如果没有 HDR 开关，请检查显示器支持、连接方式及显卡驱动；多屏请确认窗口位于 HDR 显示器上。') if system_off else _t('当前无法建立 HDR 显示。请先确认 Windows「设置 → 系统 → 显示 → HDR」已开启「使用 HDR」，再检查显卡驱动和显示器连接。\n\n如果系统 HDR 已开启，此错误可能来自显示设备或驱动兼容性，请保留下方详细信息。')
    dialog=QMessageBox(QMessageBox.Icon.Warning,_t('HDR 显示暂不可用'),str(message),QMessageBox.StandardButton.Ok,parent)
    dialog.setDetailedText(text)
    button=dialog.addButton(str(_t('打开 Windows 显示设置')),QMessageBox.ButtonRole.ActionRole)
    button.clicked.connect(lambda:QDesktopServices.openUrl(QUrl('ms-settings:display')))
    dialog.setWindowModality(Qt.WindowModality.WindowModal);parent.hdr_failure_dialog=dialog
    dialog.open();return dialog


class MergeWorker(QThread):
    result=Signal(object);error=Signal(str);progress=Signal(int,int,str)
    def __init__(self,options,parent):
        super().__init__(parent);self.options=options;self.cancel=threading.Event()
    def run(self):
        import hdr_merge
        try:self.result.emit(hdr_merge.merge(**self.options,cancel=self.cancel,progress=self.progress.emit))
        except Exception:self.error.emit(traceback.format_exc())


class MergeDialog(QDialog):
    merged=Signal(str)
    def __init__(self,parent):
        super().__init__(parent);self.worker=None;self.files=[];self.setModal(True)
        _ui(self.setWindowTitle,_t('多曝光 HDR 合成'));self.resize(820,620)
        layout=QVBoxLayout(self)
        label=_ui(QLabel,_t('选择同一画面的 2–9 个不同曝光文件，填写实际相对 EV。先合并线性数据，再进行负片反相；合成不会改写原片。'))
        label.setWordWrap(True);layout.addWidget(label)
        limit=_ui(QLabel,_t('合成区域上限 2000 万像素；对齐只处理平移。HDR 用法与限制见随包 HDR-GUIDE.md。'));limit.setWordWrap(True);layout.addWidget(limit)
        self.add=_ui(QPushButton,_t('选择曝光文件…'));self.add.clicked.connect(self.choose);layout.addWidget(self.add)
        self.table=QTableWidget(0,2);_ui(self.table.setHorizontalHeaderLabels,[_t('曝光文件'),_t('相对 EV')]);self.table.horizontalHeader().setStretchLastSection(True);self.table.setColumnWidth(0,560);layout.addWidget(self.table)
        self.kind=QComboBox();_ui(self.kind.addItem,_t('负片扫描 / 翻拍'),'negative');_ui(self.kind.addItem,_t('正像 / 场景包围曝光'),'positive');layout.addWidget(self.kind)
        self.align=_ui(QCheckBox,_t('平移对齐（不校正旋转和缩放）'));self.align.setChecked(True);layout.addWidget(self.align)
        self.ghost=_ui(QCheckBox,_t('抑制重影（与中间曝光比较）'));self.ghost.setChecked(True);layout.addWidget(self.ghost)
        self.region=_ui(QCheckBox,_t('只读取当前帧区域（同布局、16bit 无压缩线性扫描）'))
        import desktop_model as model
        self.source_region=model.source_region() if parent.loaded else None
        self.region.setEnabled(self.source_region is not None);self.region.setChecked(self.source_region is not None);layout.addWidget(self.region)
        self.message=QLabel();self.message.setWordWrap(True);layout.addWidget(self.message)
        self.progress=QProgressBar();layout.addWidget(self.progress)
        row=QHBoxLayout();self.start=_ui(QPushButton,_t('合成并保存 float32 TIFF…'));self.start.clicked.connect(self.run);row.addWidget(self.start)
        self.cancel=_ui(QPushButton,_t('关闭'));self.cancel.clicked.connect(self.close);row.addWidget(self.cancel);layout.addLayout(row)

    def choose(self):
        from native_app import IMAGE_FILTER
        files,_=_ui(QFileDialog.getOpenFileNames,self,_t('选择曝光文件'),'',IMAGE_FILTER)
        if not files:return
        if not 2<=len(files)<=9:
            _ui(self.message.setText,_t('请选择 2–9 个同画面曝光文件。'));return
        self.files=files;self.table.setRowCount(len(files))
        for row,path in enumerate(files):
            item=QTableWidgetItem(Path(path).name);item.setToolTip(path);self.table.setItem(row,0,item)
            spin=QDoubleSpinBox();spin.setRange(-8,8);spin.setSingleStep(.5);spin.setDecimals(2);self.table.setCellWidget(row,1,spin)
        _ui(self.message.setText,_t('请填写拍摄时的实际相对曝光，例如 −2、0、+2；不能把同一文件的调亮版本当作新增采集信息。'))

    def run(self):
        if self.worker is not None or not self.files:return
        evs=[self.table.cellWidget(i,1).value() for i in range(len(self.files))]
        if max(evs)-min(evs)<.5:
            _ui(self.message.setText,_t('请先填写不同的实际相对 EV。'));return
        path,_=_ui(QFileDialog.getSaveFileName,self,_t('保存 HDR 合成数据'),str(Path(self.parent().output_edit.text())/'merged-hdr.tif'),'Float32 TIFF (*.tif)')
        if not path:return
        options=dict(paths=list(self.files),evs=evs,destination=path,kind=self.kind.currentData(),
            space=self.parent().p.get('working_space','srgb'),align=self.align.isChecked(),deghost=self.ghost.isChecked())
        if self.region.isChecked() and self.source_region:
            options.update(box=self.source_region['box'],rotation=self.source_region['rotation'])
        self.worker=MergeWorker(options,self);self.worker.result.connect(self.done_merge);self.worker.error.connect(self.failed)
        self.worker.progress.connect(self.update_progress);self.worker.finished.connect(self.finished_worker)
        for widget in (self.add,self.table,self.kind,self.align,self.ghost,self.region,self.start):widget.setEnabled(False)
        _ui(self.cancel.setText,_t('取消'));self.progress.setRange(0,len(self.files));self.worker.start()

    def update_progress(self,done,total,name):
        self.progress.setRange(0,total);self.progress.setValue(done)
        _ui(self.message.setText,_tf('合成 {0}/{1} · {2}',done,total,name))

    def done_merge(self,result):
        _ui(self.message.setText,_tf('已保存 HDR 合成数据；无法恢复的端点像素占 {0:.2%}。',result['metadata']['unrecovered_fraction']))
        self.merged.emit(result['path']);self.hide()

    def failed(self,error):
        _ui(self.message.setText,error.strip().splitlines()[-1])

    def finished_worker(self):
        worker=self.worker;self.worker=None;worker.deleteLater()
        for widget in (self.add,self.table,self.kind,self.align,self.ghost,self.start):widget.setEnabled(True)
        self.region.setEnabled(self.source_region is not None);_ui(self.cancel.setText,_t('关闭'))

    def closeEvent(self,event):
        if self.worker is not None:
            self.worker.cancel.set();_ui(self.message.setText,_t('正在结束当前读取或合成…'));event.ignore()
        else:event.accept()


class HDRCanvas(QWidget):
    changed=Signal(object)
    def __init__(self,linear,parent):
        super().__init__(parent);self.linear=linear;self.surface=None;self.failed=False;self.reference_white=203.
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow);self.setMinimumSize(480,320)
        self.timer=QTimer(self);self.timer.setInterval(1000);self.timer.timeout.connect(self.update)
    def paintEngine(self):
        return None if self.surface is not None else super().paintEngine()
    def showEvent(self,event):
        super().showEvent(event);QTimer.singleShot(0,self.initialize)
    def initialize(self):
        if self.surface is not None or self.failed:return
        try:
            if QApplication.platformName()!='windows':raise RuntimeError('Native HDR preview requires the Windows platform plugin')
            from hdr_display import Surface
            ratio=self.devicePixelRatioF();self.surface=Surface(int(self.winId()),max(1,round(self.width()*ratio)),max(1,round(self.height()*ratio)))
            if not self.surface.info['hdr_active']:raise RuntimeError(str(_t('当前屏幕未开启 Windows HDR；普通修片和 float32 TIFF 导出仍可使用。')))
            self.setAttribute(Qt.WidgetAttribute.WA_PaintOnScreen);self.timer.start();self.update()
        except Exception as error:self.stop();self.failed=True;self.changed.emit(str(error));show_hdr_unavailable(self.parentWidget(),error)
    def paintEvent(self,event):
        if self.surface is None:return super().paintEvent(event)
        try:
            from hdr_display import display_mapping,scrgb_frame
            import json
            info=self.surface.capabilities()
            if info.get('monitor')!=self.surface.monitor:
                self.stop();self.failed=False;self.initialize();return
            if not info['hdr_active']:raise RuntimeError('Windows HDR is no longer enabled on this display')
            settings=self.parentWidget().parentWidget().settings
            try:profile=json.loads(settings.value('hdrDisplayProfilesV2','{}')).get(info['name'],{})
            except (ValueError,TypeError):profile={}
            self.reference_white,peak,_=display_mapping(info,profile.get('white'),profile.get('peak'))
            ratio=self.devicePixelRatioF();w=max(1,round(self.width()*ratio));h=max(1,round(self.height()*ratio))
            self.surface.present_prepared(scrgb_frame(self.linear,w,h,self.reference_white,peak),w,h)
            status=_tf('HDR 已启用 · {0} bit · 屏幕报告峰值 {1:.0f} nits · scRGB FP16',info['bits'],info['max_nits'])
            status+=_tf(' · 参考白 {0:.0f} / 映射峰值 {1:.0f} nits',self.reference_white,peak)
            self.changed.emit(status)
        except Exception as error:self.stop();self.failed=True;self.changed.emit(str(error));show_hdr_unavailable(self.parentWidget(),error)
    def stop(self):
        self.timer.stop()
        if self.surface is not None:self.surface.close();self.surface=None
        self.setAttribute(Qt.WidgetAttribute.WA_PaintOnScreen,False)


class HDRPreview(QDialog):
    def __init__(self,linear,parent):
        super().__init__(parent);_ui(self.setWindowTitle,_t('HDR 屏幕预览（实验）'));self.resize(1060,760)
        layout=QVBoxLayout(self);self.message=QLabel();self.message.setWordWrap(True);layout.addWidget(self.message)
        self.canvas=HDRCanvas(linear,self);self.canvas.changed.connect(lambda text:_ui(self.message.setText,text));layout.addWidget(self.canvas,1)
        row=QHBoxLayout();display=_ui(QPushButton,_t('HDR 显示…'));display.clicked.connect(parent.open_hdr_display);row.addWidget(display)
        button=_ui(QPushButton,_t('关闭'));button.clicked.connect(self.close);row.addWidget(button);layout.addLayout(row)
        _ui(self.message.setText,_t('正在检测当前屏幕的 Windows HDR 状态；此窗口使用线性 scRGB FP16，SDR 高光柔化不参与 HDR 呈现。'))
    def closeEvent(self,event):self.canvas.stop();event.accept()
