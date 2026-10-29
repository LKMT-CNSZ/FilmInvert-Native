"""FilmInvert Native: Qt Widgets desktop application, no web view/server."""
from pathlib import Path
import argparse
import copy
import json
import os
import sys
import threading
import time
import traceback
import sqlite3

ROOT=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
APP_DIR=Path(sys.executable).parent if getattr(sys,'frozen',False) else ROOT
if not getattr(sys,'frozen',False) and (ROOT.parent/'portable-layout.json').is_file():
    APP_DIR=ROOT.parent
from app_paths import select_profile, default_output
if (ROOT/'vendor').exists():sys.path.insert(0,str(ROOT/'vendor'))

import numpy as np
from PySide6.QtCore import Qt,QThread,Signal,Slot,QTimer,QSettings,QSize,QUrl
from PySide6.QtGui import QAction,QActionGroup,QKeySequence,QIcon,QDesktopServices,QPixmap,QPainter,QColor,QPen
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,
    QLabel,QPushButton,QComboBox,QCheckBox,QScrollArea,QTabWidget,QDockWidget,QListWidget,
    QListWidgetItem,QAbstractItemView,QFileDialog,QMessageBox,QProgressBar,QToolBar,QLineEdit,QFrame,QInputDialog,QListView,QMenu,QToolButton,QSizePolicy,QStyledItemDelegate,QStyle,QStyleOptionViewItem)

from i18n import tr as _t, trf as _tf, ui as _ui
import i18n
import desktop_model as model
import core
import calibration
import geometry
from file_io import io_path
from native_widgets import photo_icon,dropped_paths
from native_calibration import CalibrationDialog
from native_local import LocalDialog
from native_geometry import GeometryDialog
from native_frames import FrameLayoutDialog
from studio_store import StudioStore
from native_studio import QueueController,StudioDialog,ThumbnailWorker
from films import FILMS
from native_widgets import ParameterRow,ImageCanvas,Histogram,CurveEditor,image_from_array
from native_theme import STYLE,apply as apply_theme,tool_icon
from batch_store import BatchStore
from native_batch import BatchController,BatchDialog
from native_efficiency import PhotoFilters,EditingKeys,PrefetchWorker,catalog,phase,job_states

APP_NAME='FilmInvert Native'
IMAGE_FILTER=_t('胶片图像 (*.tif *.tiff *.png *.jpg *.jpeg *.cr2 *.cr3 *.nef *.arw *.dng *.raf *.orf *.rw2 *.fff *.3fr *.pef *.srw *.iiq);;所有文件 (*)')

def app_icon():
    logo=ROOT/'assets/filminvert-native-856209a72631.ico'
    if logo.is_file():return QIcon(str(logo))
    pix=QPixmap(64,64);pix.fill(Qt.GlobalColor.transparent)
    p=QPainter(pix);p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor('#1b1b1b'));p.setPen(QPen(QColor('#ededed'),2));p.drawRoundedRect(3,3,58,58,5,5)
    p.setBrush(QColor('#ededed'));p.drawRect(16,20,32,24)
    p.setBrush(QColor('#1b1b1b'))
    for x in (20,30,40):p.drawRect(x,22,5,4);p.drawRect(x,38,5,4)
    p.end();return QIcon(pix)

class FilmstripDelegate(QStyledItemDelegate):
    def sizeHint(self,option,index):
        view=self.parent();grid=view.gridSize()
        if grid.isValid():return QSize(max(1,grid.width()-2*view.spacing()),max(1,grid.height()-2*view.spacing()))
        return super().sizeHint(option,index)
    def paint(self,painter,option,index):
        # Fixed cell geometry and an explicit clip prevent selected text/updated
        # icons from enlarging a card into its neighbour's grid slot.
        option=QStyleOptionViewItem(option);self.initStyleOption(option,index)
        painter.save();painter.setClipRect(option.rect,Qt.ClipOperation.IntersectClip)
        rect=option.rect.adjusted(3,2,-3,-2)
        selected=bool(option.state & QStyle.StateFlag.State_Selected)
        hovered=bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.setPen(QPen(QColor('#ededed') if selected else Qt.GlobalColor.transparent,1))
        painter.setBrush(QColor('#303030' if selected else '#292929' if hovered else '#191919'))
        painter.drawRoundedRect(rect,2,2)
        height=option.fontMetrics.height()
        image=rect.adjusted(4,4,-4,-height-7)
        icon=index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(icon,QIcon) and image.width()>0 and image.height()>0:
            icon.paint(painter,image,Qt.AlignmentFlag.AlignCenter,QIcon.Mode.Normal,QIcon.State.Off)
        text=rect.adjusted(4,max(0,rect.height()-height-4),-4,-2)
        painter.setFont(option.font);painter.setPen(QColor('#ffffff' if selected else '#ededed'))
        title=option.fontMetrics.elidedText(str(index.data(Qt.ItemDataRole.DisplayRole) or ''),Qt.TextElideMode.ElideMiddle,max(0,text.width()))
        painter.drawText(text,Qt.AlignmentFlag.AlignHCenter|Qt.AlignmentFlag.AlignVCenter,title)
        painter.restore()

class FilmstripList(QListWidget):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.setItemDelegate(FilmstripDelegate(self))
        self.setUniformItemSizes(True);self.setWordWrap(False)
    def wheelEvent(self,event):
        bar=self.horizontalScrollBar()
        if bar.maximum()>0 and event.angleDelta().y():
            bar.setValue(bar.value()-event.angleDelta().y());event.accept();return
        super().wheelEvent(event)


class Job(QThread):
    result=Signal(object);error=Signal(str);progress=Signal(int,int,str)
    def __init__(self,fn):
        super().__init__();self.fn=fn;self.cancel=threading.Event()
    def run(self):
        try:self.result.emit(self.fn(self.cancel,self.progress.emit))
        except Exception:
            self.error.emit(traceback.format_exc())

class MainWindow(QMainWindow):
    def __init__(self,profile):
        apply_theme(QApplication.instance())
        super().__init__();self.profile=Path(profile);io_path(self.profile).mkdir(parents=True,exist_ok=True)
        self.settings=QSettings(str(io_path(self.profile/'settings.ini')),QSettings.Format.IniFormat)
        i18n.set_language(self.settings.value('language','zh_CN'))
        self.decoded_cache_mib=self.settings.value('decodedCacheMiB',1024,type=int)
        if self.decoded_cache_mib not in (0,512,1024,2048):self.decoded_cache_mib=1024
        model.DOCUMENTS.set_budget(self.decoded_cache_mib*1024*1024)
        model.NAV_THUMBS.budget=32*1024*1024
        self.settings_version='darkroomLayoutVersion'
        self.p=core.Params().__dict__;self.info={};self.loaded=False;self.syncing=False
        self.rows={};self.combos={};self.hsl_index=0;self.sample_kind=None
        self.history=[];self.history_index=-1;self.revision=0;self.full_resolution=False;self.reset_zoom=False
        self.job=None;self.jobs=[];self.pending_command=None;self.preview_pending=False;self.closing=False
        self.callback=None;self.task_kind='';self.last_export='';self.restore_params=None;self.project_path=''
        self.saved_project_signature=None;self.project_dirty=False
        self.local_dialog=None;self.chart_dialog=None;self.geometry_dialog=None;self.frame_dialog=None;self.layout_previous=None
        self.monitor_profile=None;self.monitor_name=_t('sRGB 默认显示')
        self.studio_store=StudioStore(self.profile);self.studio_dialog=None
        self.batch_store=BatchStore(self.studio_store);self.current_batch_id=None;self.batch_busy=False;self.workflow_reloading=False;self.workflow_dialog=None
        self.photo_assets=[];self.photo_all_assets=[];self.photo_images={};self.photo_rendered=set();self.photo_generation=0;self.photo_worker=None
        self.prefetch_worker=None;self.prefetch_direction=1;self.prefetch_attempted=set();self.prefetch_pending=False
        self.prefetch_timer=QTimer(self);self.prefetch_timer.setSingleShot(True);self.prefetch_timer.setInterval(800);self.prefetch_timer.timeout.connect(self.start_prefetch)
        self.queue_controller=QueueController(self.studio_store,ROOT,self)
        self.queue_controller.stopped.connect(self.queue_stopped)
        self.queue_controller.changed.connect(lambda:self.stop_prefetch() if self.queue_controller.running() else None)
        self.batch_controller=BatchController(self)
        _ui(self.setWindowTitle, APP_NAME+_t(' · Qt 原生桌面版'));self.setWindowIcon(app_icon());self.resize(1440,940)
        self.setMinimumSize(1040,700);self.setDockOptions(QMainWindow.DockOption.AnimatedDocks|QMainWindow.DockOption.AllowNestedDocks)
        self.setAcceptDrops(True)
        self.canvas=ImageCanvas();self.setCentralWidget(self.canvas)
        from native_hdr_canvas import HDRCanvasController
        self.hdr_canvas=HDRCanvasController(self.canvas);self.canvas.hdr_controller=self.hdr_canvas
        self.hdr_canvas.failed.connect(self.hdr_canvas_failed)
        from native_import_reveal import ImportReveal
        self.import_reveal=ImportReveal(self,self.canvas,self.settings);self.canvas.import_reveal=self.import_reveal
        self.developed_frames=set(self.settings.value('developedFramesV1',[],type=list));self.reveal_finish=None
        self.import_reveal.finished.connect(self.finish_frame_reveal)
        self.canvas.pathsDropped.connect(self.open_dropped_paths);self.canvas.sampled.connect(self.sample_at)
        self.canvas.zoomChanged.connect(lambda s:_ui(self.zoom_label.setText, s))
        self.canvas.viewportChanged.connect(self.viewport_moved)
        self.preview_timer=QTimer(self);self.preview_timer.setSingleShot(True);self.preview_timer.setInterval(100)
        self.preview_timer.timeout.connect(self.render_preview)
        self.history_timer=QTimer(self);self.history_timer.setSingleShot(True);self.history_timer.setInterval(350)
        self.history_timer.timeout.connect(self.commit_history)
        self._build_menus();self._build_toolbar();self._build_navigation();self._build_controls();self._build_status()
        self.editing_keys=EditingKeys(self,self.editing_shortcut)
        self.setCorner(Qt.Corner.BottomLeftCorner,Qt.DockWidgetArea.BottomDockWidgetArea)
        self.setCorner(Qt.Corner.BottomRightCorner,Qt.DockWidgetArea.BottomDockWidgetArea)
        self.sync_ui();self._history_state();self._enable_document(False)
        geometry=self.settings.value('geometry')
        if geometry:self.restoreGeometry(geometry)
        state=self.settings.value('dockState')
        if state:self.restoreState(state)
        if not self.settings.value('alignmentToolbarIntroduced',False,type=bool):
            self.removeToolBar(self.alignment_toolbar)
            self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
            self.addToolBar(Qt.ToolBarArea.TopToolBarArea,self.alignment_toolbar)
            self.alignment_toolbar.show()
            self.settings.setValue('alignmentToolbarIntroduced',True)
        if self.settings.value(self.settings_version,0,type=int)<1:self.restore_darkroom_layout()
        _ui(self.statusBar().showMessage, _t('打开或拖入底片 · 本地离线处理 · Qt 原生界面'))

    def action(self,text,fn,key=None,checkable=False):
        action=_ui(QAction, text,self);action.setCheckable(checkable)
        if key:action.setShortcut(QKeySequence(key))
        action.triggered.connect(fn);return action
    def _build_menus(self):
        file=_ui(self.menuBar().addMenu, _t('文件(&F)'))
        file.addAction(self.action(_t('解包迁移工程…'),self.unpack_dialog))
        file.addAction(self.action(_t('保存本机验收报告…'),self.acceptance_dialog))
        self.open_action=self.action(_t('打开图像…'),self.open_dialog,'Ctrl+O');file.addAction(self.open_action)
        file.addAction(self.action(_t('多曝光 HDR 合成…'),self.open_hdr_merge))
        self.hdr_export_action=self.action(_t('导出线性 HDR TIFF…'),self.hdr_export_dialog);file.addAction(self.hdr_export_action)
        self.hdr_preview_action=self.action(_t('HDR 屏幕预览…'),self.open_hdr_preview);file.addAction(self.hdr_preview_action)
        from input_color import CHOICES
        inputs=_ui(file.addMenu, _t('下一张图像的输入色彩'));group=QActionGroup(self);group.setExclusive(True)
        for key,title in CHOICES.items():
            action=self.action(_t(title),lambda checked,k=key:self.settings.setValue('inputColor',k) if checked else None,checkable=True)
            group.addAction(action);inputs.addAction(action);action.setChecked(key==self.settings.value('inputColor','legacy'))
        self.wide_open_action=self.action(_t('下一张图像使用 ProPhoto 工作空间'),lambda checked:self.settings.setValue('wideWorking',checked),checkable=True)
        self.wide_open_action.setChecked(str(self.settings.value('wideWorking',False)).lower()=='true');file.addAction(self.wide_open_action)
        self.studio_action=self.action(_t('照片库与导出队列…'),self.open_studio,'Ctrl+I');file.addAction(self.studio_action)
        self.workflow_action=self.action(_t('批量工作流…'),self.open_workflow,'Ctrl+Shift+B');file.addAction(self.workflow_action)
        self.recent_menu=_ui(file.addMenu, _t('最近打开'));self.update_recent()
        file.addAction(self.action(_t('恢复上次会话'),self.restore_session));file.addSeparator()
        file.addAction(self.action(_t('打开整卷工程…'),self.open_project_dialog,'Ctrl+Shift+O'))
        self.project_save_action=self.action(_t('保存工程'),self.save_project_dialog,'Ctrl+Alt+S');file.addAction(self.project_save_action)
        self.project_as_action=self.action(_t('工程另存为…'),lambda:self.save_project_dialog(True));file.addAction(self.project_as_action)
        file.addSeparator()
        self.export_action=self.action(_t('导出 TIFF / HDR…'),self.export_dialog,'Ctrl+Shift+S');file.addAction(self.export_action)
        self.roll_action=self.action(_t('整卷导出…'),self.batch_dialog);file.addAction(self.roll_action)
        file.addSeparator();self.save_action=self.action(_t('保存参数配方…'),self.save_recipe,'Ctrl+S');file.addAction(self.save_action)
        self.load_action=self.action(_t('载入参数配方…'),self.load_recipe,'Ctrl+L');file.addAction(self.load_action)
        self.full_recipe_action=self.action(_t('载入时包含片基、测光和平场'),lambda _:None,checkable=True);file.addAction(self.full_recipe_action)
        file.addSeparator();file.addAction(self.action(_t('退出'),self.close,'Ctrl+Q'))
        edit=_ui(self.menuBar().addMenu, _t('编辑(&E)'))
        self.undo_action=self.action(_t('撤销'),lambda:self.travel(-1),'Ctrl+Z');edit.addAction(self.undo_action)
        self.redo_action=self.action(_t('重做'),lambda:self.travel(1),'Ctrl+Shift+Z');edit.addAction(self.redo_action)
        self.addAction(self.action(_t('重做'),lambda:self.travel(1),'Ctrl+Y'))
        self.reset_action=self.action(_t('重置当前帧'),self.reset_parameters);edit.addAction(self.reset_action)
        self.local_action=self.action(_t('局部精修…'),self.open_local,'Ctrl+J');edit.addAction(self.local_action)
        self.geometry_action=self.action(_t('裁切…'),lambda:self.open_geometry('crop'),'Ctrl+R');edit.addAction(self.geometry_action)
        self.straighten_action=self.action(_t('画线校直…'),lambda:self.open_geometry('straighten'));edit.addAction(self.straighten_action)
        self.perspective_action=self.action(_t('四角透视…'),lambda:self.open_geometry('perspective'));edit.addAction(self.perspective_action)
        self.frame_action=self.action(_t('输入与切帧 / 片边辅助…'),self.open_frames,'Ctrl+Shift+R');edit.addAction(self.frame_action)
        _ui(self.frame_action.setToolTip, _t('查看原始扫描的片框与片边倾角建议，或沿片边画线校直（Ctrl+Shift+R）'))
        self.input_color_action=self.action(_t('更改当前输入色彩…'),self.change_input_color);edit.addAction(self.input_color_action)
        self.working_action=self.action(_t('更改当前工作空间…'),self.change_working_space);edit.addAction(self.working_action)
        edit.addAction(self.action(_t('查看当前输入色彩信息…'),self.show_input_color))
        self.layout_undo_action=self.action(_t('恢复切帧前状态'),self.restore_frames,'Ctrl+Alt+Z');self.layout_undo_action.setEnabled(False);edit.addAction(self.layout_undo_action)
        self.chart_action=self.action(_t('色卡标定…'),self.open_calibration,'Ctrl+K');edit.addAction(self.chart_action)
        self.chart_import_action=self.action(_t('导入色卡校正配置…'),self.import_calibration);edit.addAction(self.chart_import_action)
        view=_ui(self.menuBar().addMenu, _t('视图(&V)'))
        self.fit_action=self.action(_t('适应窗口'),self.fit_view,'F');view.addAction(self.fit_action)
        self.pixel_action=self.action(_t('原图 100%'),self.pixel_view,'Ctrl+1');view.addAction(self.pixel_action)
        self.hdr_canvas_action=self.action(_t('主画布 HDR'),self.toggle_hdr_canvas,checkable=True);view.addAction(self.hdr_canvas_action)
        self.import_reveal_action=self.action(_t('首次查看显影动画'),self.toggle_import_reveal,checkable=True)
        self.import_reveal_action.setChecked(self.settings.value('importRevealEnabled',True,type=bool));view.addAction(self.import_reveal_action)
        self.original_action=self.action(_t('查看原负片'),lambda _:self.request_preview(),checkable=True);view.addAction(self.original_action)
        self.clipping_action=self.action(_t('目标输出越界（红上界 / 蓝下界）'),self.canvas.set_clipping,'J',checkable=True);view.addAction(self.clipping_action)
        view.addSeparator();view.addAction(self.action(_t('选择显示器 RGB ICC…'),self.choose_monitor))
        view.addAction(self.action(_t('恢复默认 sRGB 显示'),self.clear_monitor))
        self.proof_action=self.action(_t('输出 RGB 色域预览'),lambda _:self.request_preview(),checkable=True);view.addAction(self.proof_action)
        self.view_menu=view
        languages=_ui(view.addMenu, 'Language / 语言');language_group=QActionGroup(self);language_group.setExclusive(True)
        self.language_actions={}
        for code,title in (('zh_CN','简体中文'),('en','English')):
            action=self.action(title,lambda checked,c=code:self.switch_language(c) if checked else None,checkable=True)
            language_group.addAction(action);languages.addAction(action);action.setChecked(i18n.language()==code);self.language_actions[code]=action
        view.addAction(self.action(_t('恢复暗房布局'),self.restore_darkroom_layout))
        cache_menu=_ui(view.addMenu, _t('切图缓存'));cache_group=QActionGroup(self);cache_group.setExclusive(True)
        self.cache_actions={}
        for size,title in ((0,_t('关闭原图缓存')),(512,'512 MiB'),(1024,_t('1 GiB（默认）')),(2048,'2 GiB')):
            action=self.action(title,lambda checked,n=size:self.set_switch_cache(n) if checked else None,checkable=True)
            cache_group.addAction(action);cache_menu.addAction(action);action.setChecked(size==self.decoded_cache_mib);self.cache_actions[size]=action
        cache_menu.addSeparator();cache_menu.addAction(self.action(_t('清空切图缓存'),self.clear_switch_cache))
        self.prefetch_action=self.action(_t('空闲时预加载相邻照片'),self.toggle_prefetch,checkable=True)
        self.prefetch_action.setChecked(self.settings.value('adjacentPrefetch',True,type=bool));cache_menu.addAction(self.prefetch_action)
        help_menu=_ui(self.menuBar().addMenu, _t('帮助(&H)'))
        help_menu.addAction(self.action(_t('打开使用说明'),self.show_help))
        help_menu.addAction(self.action(_t('批量操作与快捷键…'),self.show_efficiency_help))
        help_menu.addAction(self.action(_t('打开用户数据目录'),lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.profile.resolve())))))
        help_menu.addAction(self.action(_t('关于'),lambda:_ui(QMessageBox.about, self,APP_NAME,
            _tf('FilmInvert Native {0} 功能预览版\n照片库 · 参数快照队列 · 片边辅助 · 色卡标定\n官方 Python 运行时与应用文件分开分发。', model.VERSION.removesuffix("-native")))))
    def _build_toolbar(self):
        bar=_ui(QToolBar, _t('主工具栏'),self);bar.setObjectName('mainToolbar');bar.setMovable(False);self.addToolBar(bar)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon);bar.setIconSize(QSize(16,16))
        brand=_ui(QLabel, 'FilmInvert');brand.setProperty('role','brand');bar.addWidget(brand)
        for action,kind in ((self.open_action,'open'),(self.studio_action,'library'),(self.export_action,'export'),
                (self.frame_action,'frames'),(self.geometry_action,'crop'),(self.straighten_action,'straighten'),
                (self.perspective_action,'perspective'),(self.fit_action,'fit'),(self.pixel_action,'pixel')):
            action.setIcon(tool_icon(kind))
        _ui(self.open_action.setText, _t('打开胶片…'));_ui(self.studio_action.setText, _t('照片库 / 队列…'))
        _ui(self.frame_action.setText, _t('片边辅助 / 切帧…'))
        bar.addAction(self.open_action);bar.addAction(self.studio_action);bar.addSeparator()
        self.language_combo=QComboBox();self.language_combo.setAccessibleName('Language / 语言')
        self.language_combo.addItem('简体中文','zh_CN');self.language_combo.addItem('English','en')
        self.language_combo.setCurrentIndex(max(0,self.language_combo.findData(i18n.language())))
        self.language_combo.currentIndexChanged.connect(lambda _:self.switch_language(self.language_combo.currentData()))
        bar.addWidget(self.language_combo)
        bar.addWidget(_ui(QLabel, _t('打开方式')))
        self.open_mode=QComboBox();_ui(self.open_mode.setAccessibleName, _t('下一张图像的打开方式'))
        self.open_mode.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        for title,key in [(_t('自动识别'),'auto'),(_t('单张'),'single'),(_t('整条'),'strip')]:_ui(self.open_mode.addItem, title,key)
        self.open_mode.setCurrentIndex(max(0,self.open_mode.findData(self.settings.value('inputMode','auto'))))
        _ui(self.open_mode.setToolTip, _t('用于下一次打开图像；当前图像通过“输入与切帧”更改。'))
        self.open_mode.currentIndexChanged.connect(lambda _:self.settings.setValue('inputMode',self.open_mode.currentData()));bar.addWidget(self.open_mode)
        spring=QWidget();spring.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred);bar.addWidget(spring)
        bar.addAction(self.hdr_canvas_action)
        bar.addAction(self.project_save_action)
        self.export_button=_ui(QPushButton, _t('导出 TIFF / HDR…'));self.export_button.setProperty('role','primary')
        self.export_button.clicked.connect(self.export_dialog);bar.addWidget(self.export_button);bar.addAction(self.roll_action)
        self.sample_actions={}
        for key,title in (('base',_t('点片基')),('anchor',_t('点黑场')),('gray',_t('点中性灰'))):
            action=self.action(title,lambda checked,k=key:self.choose_sample(k,checked),checkable=True)
            self.sample_actions[key]=action
        self.toolbar=bar
        self.addToolBarBreak()
        tools=_ui(QToolBar, _t('校直与片边辅助'),self);tools.setObjectName('alignmentToolbar');tools.setMovable(False)
        tools.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon);tools.setIconSize(QSize(16,16))
        for action in (self.frame_action,self.geometry_action,self.straighten_action,self.perspective_action):tools.addAction(action)
        tools.addSeparator()
        for action in (self.fit_action,self.pixel_action,self.original_action):tools.addAction(action)
        tools.addSeparator();self.sample_button=_ui(QToolButton);_ui(self.sample_button.setText, _t('取样'));self.sample_button.setIcon(tool_icon('sample'))
        self.sample_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.sample_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        sample_menu=_ui(QMenu, self.sample_button)
        for action in self.sample_actions.values():sample_menu.addAction(action)
        self.sample_button.setMenu(sample_menu);tools.addWidget(self.sample_button)
        self.addToolBar(tools);self.alignment_toolbar=tools
        self.view_menu.addAction(tools.toggleViewAction())
    def _build_navigation(self):
        dock=_ui(QDockWidget, _t('整卷 / 帧导航'),self);dock.setObjectName('frameDock');dock.setMinimumHeight(94);dock.setMaximumHeight(112)
        self.filmstrip_active=False
        dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        box=QWidget();box.setObjectName('filmstripBody');layout=QVBoxLayout(box);layout.setContentsMargins(12,3,12,6);layout.setSpacing(3)
        line=QHBoxLayout();self.source_label=_ui(QLabel, _t('未打开图像'));self.source_label.setMinimumWidth(80)
        self.source_label.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred);self.source_label.setProperty('role','subtle');line.addWidget(self.source_label,1)
        self.frame_label=_ui(QLabel, _t('单张图像'));line.addWidget(self.frame_label)
        self.navigation_mode=QComboBox();_ui(self.navigation_mode.addItems, [_t('本次照片'),_t('当前胶片帧')]);self.navigation_mode.hide()
        self.navigation_mode.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.navigation_mode.currentIndexChanged.connect(self.update_navigation);line.addWidget(self.navigation_mode)
        self.prev_button=_ui(QPushButton, _t('‹ 上一帧'));self.next_button=_ui(QPushButton, _t('下一帧 ›'))
        self.prev_button.clicked.connect(lambda:self.navigate_relative(-1))
        self.next_button.clicked.connect(lambda:self.navigate_relative(1))
        line.addWidget(self.prev_button);line.addWidget(self.next_button);layout.addLayout(line)
        self.photo_filters=PhotoFilters(self);self.photo_filters.hide();self.photo_filters.changed.connect(self.refresh_photo_filter);layout.addWidget(self.photo_filters)
        self.frames=FilmstripList();self.frames.setObjectName('frameList');self.frames.setIconSize(QSize(112,76))
        _ui(self.frames.setAccessibleName, _t('整卷胶片条：左右切帧，滚轮横向浏览'));self.frames.hide()
        self.frames.setViewMode(QListView.ViewMode.IconMode);self.frames.setFlow(QListView.Flow.LeftToRight)
        self.frames.setWrapping(False);self.frames.setResizeMode(QListView.ResizeMode.Adjust)
        self.frames.setMovement(QListView.Movement.Static);self.frames.setGridSize(QSize(128,106));self.frames.setSpacing(5);self.frames.setMinimumHeight(118)
        self.frames.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.frames.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.frames.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.frames.currentRowChanged.connect(self.go_frame);layout.addWidget(self.frames,1)
        self.photos=FilmstripList();self.photos.setObjectName('photoList');_ui(self.photos.setAccessibleName, _t('本次拖入的照片：点击切换，分别保存编辑'))
        self.photos.setIconSize(QSize(112,76));self.photos.setViewMode(QListView.ViewMode.IconMode)
        self.photos.setFlow(QListView.Flow.LeftToRight);self.photos.setWrapping(False);self.photos.setResizeMode(QListView.ResizeMode.Adjust)
        self.photos.setMovement(QListView.Movement.Static);self.photos.setGridSize(QSize(148,106));self.photos.setSpacing(5);self.photos.setMinimumHeight(118)
        self.photos.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel);self.photos.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.photos.currentRowChanged.connect(self.go_photo);self.photos.hide();layout.addWidget(self.photos,1)
        line=QHBoxLayout();self.keep_check=_ui(QCheckBox, _t('切帧沿用当前调色'));self.keep_check.setChecked(True);line.addWidget(self.keep_check)
        _ui(self.keep_check.setToolTip, _t('关闭后，切回已修帧时恢复它的独立参数。'))
        self.meter_check=_ui(QCheckBox, _t('每帧独立估计跨度'));self.meter_check.setChecked(True);line.addWidget(self.meter_check)
        self.keep_check.toggled.connect(self.navigation_options_changed);self.meter_check.toggled.connect(self.navigation_options_changed)
        line.addStretch();self.apply_button=_ui(QPushButton, _t('应用调色到全部帧'));self.apply_button.clicked.connect(self.apply_roll);line.addWidget(self.apply_button)
        self.frame_button=_ui(QPushButton, _t('片边辅助 / 切帧…'));_ui(self.frame_button.setToolTip, self.frame_action.toolTip())
        self.frame_button.clicked.connect(self.open_frames);line.addWidget(self.frame_button);layout.addLayout(line)
        workflow_button=_ui(QPushButton, _t('批量处理…'));workflow_button.clicked.connect(self.open_workflow);line.insertWidget(0,workflow_button)
        self.review_button=_ui(QPushButton, _t('复核当前照片'));_ui(self.review_button.setToolTip, _t('人工检查后标记已复核（Shift+R）；整条胶片请先查看全部帧'))
        self.review_button.clicked.connect(lambda:self.editing_shortcut('review',None));self.review_button.setEnabled(False);self.review_button.hide();line.insertWidget(1,self.review_button)
        dock.setWidget(box);self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea,dock);self.nav_dock=dock
        self.view_menu.addAction(dock.toggleViewAction())

    def restore_darkroom_layout(self):
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea,self.nav_dock);self.nav_dock.setFloating(False);self.nav_dock.show()
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea,self.controls_dock);self.controls_dock.setFloating(False);self.controls_dock.show()
        self.removeToolBar(self.alignment_toolbar);self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea,self.alignment_toolbar);self.alignment_toolbar.show();self.toolbar.show()
        self.resizeDocks([self.nav_dock],[220 if self.filmstrip_active else 104],Qt.Orientation.Vertical)
        self.resizeDocks([self.controls_dock],[360],Qt.Orientation.Horizontal)
        self.settings.setValue(self.settings_version,1)
    def set_switch_cache(self,mib):
        self.stop_prefetch();_ui(self.prefetch_attempted.clear)
        self.decoded_cache_mib=mib;self.settings.setValue('decodedCacheMiB',mib);model.DOCUMENTS.set_budget(mib*1024*1024)
        _ui(self.statusBar().showMessage, _t('原图切换缓存已关闭') if not mib else _tf('原图及分析缓存上限 {0} MiB；当前图像和处理临时内存另计', mib))
    def clear_switch_cache(self):
        if self.job is not None or self.batch_busy:_ui(self.statusBar().showMessage, _t('请等待当前处理结束后再清空缓存'));return
        self.stop_prefetch();_ui(self.prefetch_attempted.clear)
        model.clear_navigation_caches();_ui(self.statusBar().showMessage, _t('已清空切图缓存，当前照片和编辑保留'))
    def toggle_prefetch(self,checked):
        self.settings.setValue('adjacentPrefetch',checked);self.stop_prefetch();_ui(self.prefetch_attempted.clear)
        if checked:self.schedule_prefetch()
    def stop_prefetch(self):
        self.prefetch_timer.stop();self.prefetch_pending=False
        if self.prefetch_worker:self.prefetch_worker.stop()
    def schedule_prefetch(self):
        if not self.closing and self.loaded and self.photo_assets and self.prefetch_action.isChecked():self.prefetch_pending=True;self.prefetch_timer.start()
    def start_prefetch(self):
        if (self.closing or not self.loaded or self.job is not None or self.pending_command or self.batch_busy
            or self.queue_controller.running() or self.prefetch_worker or not self.prefetch_action.isChecked() or not model.DOCUMENTS.budget):return
        self.prefetch_pending=False
        current=self.current_photo_row();target=current+self.prefetch_direction
        if not 0<=target<len(self.photo_assets):target=current-self.prefetch_direction
        if current<0 or not 0<=target<len(self.photo_assets):return
        loaded=model.engine.STATE.get('native_loaded');identity=model.engine.STATE.get('native_source')
        if loaded is None or identity is None:return
        protected=model.DOCUMENTS.key(identity,loaded.color,loaded.working_space)
        asset=self.studio_store.get_asset(self.photo_assets[target]['id'])
        signature=(asset['id'],asset['updated'],protected,model.DOCUMENTS.budget)
        if signature in self.prefetch_attempted:return
        self.prefetch_attempted.add(signature)
        worker=PrefetchWorker(asset,model.DOCUMENTS,protected,loaded.linear.nbytes,self);worker.signature=signature;self.prefetch_worker=worker
        worker.finished.connect(self.prefetch_finished);worker.start(QThread.Priority.LowPriority)
    def prefetch_finished(self):
        worker=self.prefetch_worker;self.prefetch_worker=None;worker.deleteLater()
        if self.closing:QTimer.singleShot(0,self.close)
        elif worker.outcome=='cancelled':
            self.prefetch_attempted.discard(worker.signature)
            if self.prefetch_pending:self.schedule_prefetch()
    def refresh_photo_filter(self):
        self.stop_prefetch()
        rows=catalog(self.studio_store,[a['id'] for a in self.photo_all_assets]);self.photo_all_assets=rows
        items={};jobs={}
        if self.current_batch_id:
            data=self.batch_store.get(self.current_batch_id);items={i['asset']:i for i in data['items']}
            jobs=job_states(self.studio_store)
        self.photo_filters.groups(items.get(a['id'],{}).get('group',a['roll']) for a in rows)
        self.photo_assets=[];self.photos.blockSignals(True);_ui(self.photos.clear)
        for asset in rows:
            item=items.get(asset['id']);group=item['group'] if item else asset['roll']
            state=phase(item,self.batch_store.session,asset,jobs.get(item.get('job'))) if item else ('skip' if asset['flag']=='reject' else 'review' if asset['has_edits'] else 'prepare')
            if not self.photo_filters.accepts(asset,group,state):continue
            self.photo_assets.append(asset)
            cell=_ui(QListWidgetItem, tool_icon('library'),asset['name']);cell.setData(Qt.ItemDataRole.UserRole,asset['id'])
            _ui(cell.setToolTip, asset['path']+_tf('\n{0} 星 · ', asset["rating"])+{'pick':_t('精选'),'reject':_t('弃选'),'none':_t('未标记')}[asset['flag']]);_ui(self.photos.addItem, cell)
            self.paint_photo_icon(len(self.photo_assets)-1)
        self.photos.blockSignals(False);self.update_navigation();self.schedule_prefetch()
    def editing_shortcut(self,command,value):
        if command=='search':
            if not self.photo_all_assets:return False
            self.navigation_mode.setCurrentIndex(0);self.nav_dock.show();self.photo_filters.search.setFocus();return True
        if self.closing or self.batch_busy or self.job is not None or not self.loaded:return False
        if command=='navigate':self.navigate_relative(value);return True
        identifier=self.current_workflow_asset()
        if identifier is None:return False
        if command in ('rating','flag'):
            asset=self.studio_store.get_asset(identifier)
            self.studio_store.metadata(identifier,value if command=='rating' else asset['rating'],value if command=='flag' else asset['flag'],asset['roll'])
            self.refresh_photo_filter();_ui(self.statusBar().showMessage, _t('当前照片已设为 ')+(str(value)+_t(' 星') if command=='rating' else {'pick':_t('精选'),'reject':_t('弃选'),'none':_t('未标记')}[value]));return True
        if command=='review':
            self.save_session()
            data=self.batch_store.get(self.current_batch_id) if self.current_batch_id else None
            item=next((i for i in data['items'] if i['asset']==identifier),None) if data else None
            if not item or not item['snapshot'] or not item['valid'] or item['error'] or item.get('verified_session')!=self.batch_store.session:
                _ui(self.statusBar().showMessage, _t('当前照片尚未准备或校验，请先在批量工作流完成准备'));return True
            self.batch_store.review(self.current_batch_id,[identifier]);self.refresh_photo_filter();_ui(self.statusBar().showMessage, _t('当前照片已复核（整条胶片请先检查全部帧）'));return True
        return False
    def show_efficiency_help(self):
        _ui(QMessageBox.information, self,_t('批量操作与快捷键'),_t('底部导航和批量清单均支持搜索、分组、状态、星级及标记筛选。筛选不会更改原有导出勾选。\n\n← / →：上一张 / 下一张（帧模式下切帧）\n0–5：当前照片或清单选中项评级\nP / X / U：精选 / 弃选 / 清除标记\nShift+R：复核当前照片或清单选中项\nCtrl+F：搜索照片\n\n输入框、数值框和下拉框编辑时，切图和评级快捷键暂停。批次清单可点“仅勾选筛选结果”，再按勾选项导出。\n\n相邻原图在空闲时预加载；视图 → 切图缓存可关闭。正式打开仍校验原片并恢复该图独立编辑。'))
    def section(self,layout,text):
        label=_ui(QLabel, text);label.setProperty('role','section');layout.addWidget(label)
    def note(self,layout,text):
        label=_ui(QLabel, text);label.setWordWrap(True);label.setProperty('role','note');layout.addWidget(label);return label
    def button_row(self,layout,items):
        row=QHBoxLayout()
        for title,fn in items:
            button=_ui(QPushButton, title);button.clicked.connect(fn);row.addWidget(button)
        layout.addLayout(row)
    def parameter(self,layout,title,key,low,high,step,default=0):
        row=ParameterRow(title,key,low,high,step,default);layout.addWidget(row);self.rows[key]=row
        row.changed.connect(lambda v,k=key:self.edit_parameter(k,v));row.committed.connect(self.commit_history)
        return row
    def combo(self,layout,title,key,items):
        line=QHBoxLayout();label=_ui(QLabel, title);label.setMinimumWidth(74);line.addWidget(label)
        combo=QComboBox();combo.setObjectName(key);_ui(combo.setAccessibleName, title)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon);combo.setMinimumContentsLength(8)
        combo.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        for name,value in items:_ui(combo.addItem, name,value)
        line.addWidget(combo,1);layout.addLayout(line);self.combos[key]=combo
        combo.currentIndexChanged.connect(lambda _,k=key,c=combo:self.edit_parameter(k,c.currentData(),True))
        return combo
    def page(self,title):
        page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(12,5,12,12);layout.setSpacing(5)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(page);_ui(self.tabs.addTab, scroll,title)
        return layout
    def _build_controls(self):
        dock=_ui(QDockWidget, _t('调色工作台'),self);dock.setObjectName('adjustDock');dock.setMinimumWidth(355)
        panel=QWidget();layout=QVBoxLayout(panel);layout.setContentsMargins(5,5,5,5)
        self.histogram=Histogram();layout.addWidget(self.histogram)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        basic=self.page(_t('基础'));self.section(basic,_t('胶片与密度'))
        self.combo(basic,_t('模式'),'mode',[(_t('彩色负片'),'color_negative'),(_t('黑白负片'),'bw_negative'),(_t('正片'),'positive')])
        self.combo(basic,_t('胶片预设'),'film',[(_t('不使用预设'),'')]+[(v['name'],k) for k,v in FILMS.items()])
        for args in ((_t('反差'),'slope',.3,3,.01,1.6),(_t('密度跨度'),'span',.3,4,.01,1.75),
                     (_t('密度黑场'),'d_min',-.5,1.5,.01,0),(_t('串色校正'),'crosstalk',0,1,.01,0)):
            self.parameter(basic,*args)
        self.combo(basic,_t('自动测光'),'meter_mode',[(_t('排除片边'),'edges'),(_t('全幅（兼容）'),'legacy')])
        self.section(basic,_t('影调与颜色'))
        for title,key,low,high in ((_t('阴影'),'tone_shadows',-1,1),(_t('高光'),'tone_highlights',-1,1),
                (_t('白场调整'),'tone_whites',-1,1),(_t('黑场调整'),'tone_blacks',-1,1),(_t('高光柔化'),'tone_rolloff',0,1)):
            self.parameter(basic,title,key,low,high,.05,0)
        for args in ((_t('曝光 EV'),'exposure',-4,4,.05,0),(_t('色温'),'temp',-1,1,.01,0),
                     (_t('色调'),'tint',-1,1,.01,0),(_t('饱和度'),'saturation',0,2,.01,1)):
            self.parameter(basic,*args)
        self.button_row(basic,[(_t('灰世界'),lambda:self.automatic_wb('gray_world')),(_t('高光白点'),lambda:self.automatic_wb('white_patch')),(_t('重置 WB'),self.reset_wb)])
        self.wb_label=self.note(basic,_t('RGB 增益 1 / 1 / 1'))
        self.button_row(basic,[(_t('色卡标定…'),self.open_calibration),(_t('导入校正'),self.import_calibration),(_t('清除校正'),self.clear_calibration)])
        self.parameter(basic,_t('校正强度'),'calibration_strength',0,1,.05,1)
        self.calibration_label=self.note(basic,_t('未启用色卡校正'))
        self.flip_check=_ui(QCheckBox, _t('水平翻转'));self.flip_check.toggled.connect(lambda v:self.edit_parameter('flip',v,True));basic.addWidget(self.flip_check)
        self.repair_check=_ui(QCheckBox, _t('欠曝通道修复'));self.repair_check.toggled.connect(lambda v:self.edit_parameter('channel_repair',v,True));basic.addWidget(self.repair_check)
        self.health_label=self.note(basic,'')
        self.button_row(basic,[(_t('重估片基 / 跨度'),self.recalculate),(_t('重置当前帧'),self.reset_parameters)])
        self.base_label=self.note(basic,_t('片基 —'))
        self.button_row(basic,[(_t('裁切、校直与透视…'),self.open_geometry),(_t('重置几何'),self.reset_geometry)])
        self.geometry_label=self.note(basic,_t('完整画面'))
        basic.addStretch()
        curves=self.page(_t('曲线 / HSL'));self.section(curves,_t('单调影调曲线'))
        self.curve=CurveEditor();curves.addWidget(self.curve);self.curve.changed.connect(self.curve_changed);self.curve.committed.connect(self.commit_history)
        self.button_row(curves,[(_t('线性'),lambda:self.set_curve([[0,0],[1,1]])),(_t('柔和 S'),lambda:self.set_curve([[0,0],[.25,.2],[.75,.8],[1,1]])),(_t('强 S'),lambda:self.set_curve([[0,0],[.25,.12],[.75,.88],[1,1]]))])
        self.note(curves,_t('双击加点 / 删点，拖动调整；Delete 删除选中内点。三个 RGB 通道共用曲线。'))
        self.section(curves,_t('局部色调映射'))
        self.parameter(curves,_t('局部 HDR 强度'),'local_tone',0,1,.05,0)
        self.parameter(curves,_t('作用范围'),'local_radius',.005,.075,.005,.03)
        self.parameter(curves,_t('细节保护'),'local_detail',-1,1,.05,0)
        self.section(curves,_t('8 色 HSL'))
        color_row=QHBoxLayout();self.color_buttons=[]
        for i,name in enumerate(map(_t,('红','橙','黄','绿','青','蓝','紫','品'))):
            button=_ui(QPushButton, name);button.setCheckable(True);button.setFixedWidth(32)
            button.setStyleSheet('QPushButton{padding:6px 1px;}')
            button.clicked.connect(lambda _,c=i:self.select_hsl(c));self.color_buttons.append(button);color_row.addWidget(button)
        curves.addLayout(color_row)
        self.hsl_sat=ParameterRow(_t('饱和度'),'hsl_sat',-1,1,.01);curves.addWidget(self.hsl_sat)
        self.hsl_lum=ParameterRow(_t('明度'),'hsl_lum',-1,1,.01);curves.addWidget(self.hsl_lum)
        self.hsl_sat.changed.connect(lambda v:self.edit_hsl(0,v));self.hsl_lum.changed.connect(lambda v:self.edit_hsl(1,v))
        self.hsl_sat.committed.connect(self.commit_history);self.hsl_lum.committed.connect(self.commit_history)
        self.button_row(curves,[(_t('重置 HSL'),self.reset_hsl)]);curves.addStretch()
        restore=self.page(_t('修复'));self.button_row(restore,[(_t('局部仿制 / 曝光 / 渐变…'),self.open_local)]);self.note(restore,_t('局部笔画只随当前帧保存；应用到全部帧保留各帧原有局部编辑。'));self.section(restore,_t('孤立尘点修复'))
        self.parameter(restore,_t('去尘强度'),'dust_strength',0,1,.05)
        self.combo(restore,_t('尘点半径'),'dust_radius',[('1 px',1),('2 px',2),('3 px',3)])
        self.note(restore,_t('无红外通道的估计修复，可能影响真实小细节。预览缩图与全尺寸识别不同，请用原图 100% 或导出检查。'))
        self.section(restore,_t('输入照明校正'))
        self.parameter(restore,_t('暗角补偿'),'vignette',0,1,.05)
        self.parameter(restore,_t('平场强度'),'flat_strength',0,1,.05)
        self.button_row(restore,[(_t('导入均匀参考'),self.flat_dialog),(_t('片基照明估计'),self.strip_flat)])
        self.button_row(restore,[(_t('清除平场'),self.clear_flat),(_t('重估片基 / 跨度'),self.recalculate)])
        self.flat_label=self.note(restore,_t('未启用平场'))
        self.note(restore,_t('单张图的参考必须同尺寸、同方向、同一光路；整条片使用帧间隙估计横向照明。暗角补偿后重新取片基。'))
        restore.addStretch()
        output=self.page(_t('导出'));self.section(output,_t('16bit TIFF 与色彩管理'))
        self.combo(output,_t('TIFF 输出空间'),'output_space',[('sRGB','srgb'),('Adobe RGB (1998)','adobe_rgb'),('ProPhoto RGB','prophoto')])
        self.note(output,_t('输出空间用于保存 TIFF 和嵌入 ICC。RAW 解码与片基取样使用当前工作空间；更改请用“编辑 → 更改当前工作空间”。'))
        self.note(output,_t('导出嵌入匹配的 ICC。直方图与越界叠层基于所选 TIFF 空间；预览抽样不代表全尺寸统计。'))
        self.section(output,_t('输出位置'));self.output_edit=QLineEdit(str(self.settings.value('output',str(default_output(APP_DIR,self.profile)))));_ui(self.output_edit.setAccessibleName, _t('输出目录'));output.addWidget(self.output_edit)
        self.button_row(output,[(_t('选择文件夹'),self.output_folder),(_t('打开输出位置'),self.open_output)])
        self.button_row(output,[(_t('当前帧另存为…'),self.export_dialog),(_t('整卷导出…'),self.batch_dialog)])
        self.section(output,_t('命名与联系表'))
        self.naming_edit=QLineEdit(str(self.settings.value('naming_template','')));_ui(self.naming_edit.setPlaceholderText, _t('留空保持旧命名，或 {source}-f{frame:04d}'));output.addWidget(self.naming_edit)
        self.naming_edit.editingFinished.connect(lambda:self.settings.setValue('naming_template',self.naming_edit.text()))
        self.note(output,_t('支持 {source} / {frame:04d} / {roll} / {date}；自动追加 .tif。队列使用照片库胶卷标签，加入时固定命名。'))
        self.button_row(output,[(_t('导出整卷 PNG 联系表…'),self.contact_dialog)])
        self.section(output,_t('参数与会话'))
        self.button_row(output,[(_t('保存配方'),self.save_recipe),(_t('载入配方'),self.load_recipe)])
        self.button_row(output,[(_t('保存整卷工程'),self.save_project_dialog),(_t('打开整卷工程'),self.open_project_dialog)])
        self.note(output,_t('工程保留整卷模板、各帧独立参数与帧位置，不包含原图。自动会话也保存整卷编辑，可从“文件 → 恢复上次会话”打开。'))
        self.button_row(output,[(_t('打包工程与原片…'),self.pack_dialog),(_t('解包迁移工程…'),self.unpack_dialog)])
        self.last_path_label=self.note(output,_t('尚未导出'));output.addStretch()
        dock.setWidget(panel);self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea,dock);self.controls_dock=dock
        self.view_menu.addAction(dock.toggleViewAction())
    def _build_status(self):
        self.progress=QProgressBar();self.progress.setFixedWidth(160);self.progress.setVisible(False)
        self.cancel_button=_ui(QPushButton, _t('取消'));self.cancel_button.setVisible(False);self.cancel_button.clicked.connect(self.cancel_job)
        self.zoom_label=_ui(QLabel, _t('适应窗口'));self.history_label=_ui(QLabel, _t('0 步'))
        self.statusBar().addPermanentWidget(self.progress);self.statusBar().addPermanentWidget(self.cancel_button)
        self.statusBar().addPermanentWidget(self.history_label);self.statusBar().addPermanentWidget(self.zoom_label)
    def _enable_document(self,on):
        self.hdr_canvas_action.setEnabled(on)
        if not on:self.hdr_canvas.set_linear(None)
        self.hdr_export_action.setEnabled(on);self.hdr_preview_action.setEnabled(on)
        self.controls_dock.widget().setEnabled(on);self.nav_dock.widget().setEnabled(on or bool(self.photo_all_assets))
        self.export_button.setEnabled(on);self.sample_button.setEnabled(on)
        self.frame_button.setEnabled(on)
        for action in [self.export_action,self.roll_action,self.save_action,self.project_save_action,self.project_as_action,self.reset_action,self.local_action,self.geometry_action,self.straighten_action,self.perspective_action,self.frame_action,self.input_color_action,self.chart_action,self.chart_import_action,self.original_action,self.pixel_action,*self.sample_actions.values()]:action.setEnabled(on)
        self.layout_undo_action.setEnabled(on and self.layout_previous is not None)
        self.working_action.setEnabled(on)
        if on:self.update_navigation()
    def foreground(self,on):
        self.controls_dock.widget().setEnabled(self.loaded and not on);self.nav_dock.widget().setEnabled(self.loaded and not on)
        self.toolbar.setEnabled(not on);self.alignment_toolbar.setEnabled(not on);self.menuBar().setEnabled(not on);self.canvas.setEnabled(not on)
        if not on:self._enable_document(self.loaded)
    def start_task(self,fn,done,message,kind='command'):
        self.stop_prefetch()
        self.jobs=[j for j in self.jobs if j.isRunning()]
        job=Job(fn);self.jobs.append(job);self.job=job;self.callback=done;self.task_kind=kind
        job.result.connect(self._received);job.error.connect(self._failed);job.progress.connect(self._progress)
        job.finished.connect(self._thread_ended)
        if kind!='preview':self.foreground(True)
        self.progress.setRange(0,0);self.progress.setVisible(True);_ui(self.statusBar().showMessage, message)
        self.cancel_button.setEnabled(True);self.cancel_button.setVisible(kind=='batch');job.start()
    def command(self,fn,done,message,kind='command'):
        self.import_reveal.cancel()
        self.preview_timer.stop();self.history_timer.stop();self.commit_history();self.preview_pending=False
        if self.job is not None:
            if self.task_kind=='preview':
                self.revision+=1;self.job.cancel.set();self.preview_pending=True
            self.pending_command=(fn,done,message,kind);self.foreground(True);_ui(self.statusBar().showMessage, message+_t(' · 等待当前计算结束'))
        else:self.start_task(fn,done,message,kind)
    @Slot(object)
    def _received(self,result):
        callback=self.callback;kind=self.task_kind;self.job=None;self.callback=None
        self.progress.setVisible(False);self.cancel_button.setVisible(False)
        if kind!='preview':self.foreground(False)
        try:
            if callback:callback(result)
        except Exception:self.show_error(traceback.format_exc())
        self._next_task()
    @Slot(str)
    def _failed(self,error):
        kind=self.task_kind;self.job=None;self.callback=None;self.progress.setVisible(False);self.cancel_button.setVisible(False)
        if kind!='preview':self.foreground(False)
        if kind=='preview' and getattr(self,'preview_job_revision',self.revision)!=self.revision:
            self._next_task();return
        if kind=='preview' and self.full_resolution:
            self.full_resolution=False;self.reset_zoom=False;self.canvas.end_region();self.canvas.fit_image()
            self.revision+=1;self.preview_pending=not bool(self.pending_command)
            self.preview_fallback=error.strip().splitlines()[-1]+_t(' · 已退回适应窗口')
            io_path(self.profile/'last-error.log').write_text(error,encoding='utf-8')
            self._next_task();return
        if self.chart_dialog and self.chart_dialog.isVisible():
            self.chart_dialog.set_busy(False);_ui(self.chart_dialog.message.setText, error.strip().splitlines()[-1])
        if self.workflow_reloading:self.workflow_reloading=False;self.loaded=False
        self.show_error(error);self._next_task()
        if self.photo_assets:self.update_navigation()
    def _next_task(self):
        if self.closing:return
        if self.pending_command:
            args=self.pending_command;self.pending_command=None;self.start_task(*args)
        elif self.preview_pending:
            self.preview_pending=False;self.preview_timer.start()
    @Slot()
    def _thread_ended(self):
        if self.closing and not any(j.isRunning() for j in self.jobs):QTimer.singleShot(0,self.close)
    @Slot(int,int,str)
    def _progress(self,value,total,message):
        self.progress.setRange(0,total);self.progress.setValue(value);_ui(self.statusBar().showMessage, _tf('整卷导出 {0} / {1}', value, total))
    def show_error(self,error):
        io_path(self.profile/'last-error.log').write_text(error,encoding='utf-8')
        lines=[s for s in error.strip().splitlines() if s.strip()]
        message=lines[-1] if lines else error
        _ui(self.statusBar().showMessage, message)
        dialog=QMessageBox(QMessageBox.Icon.Warning,_t('处理未完成'),message,QMessageBox.StandardButton.Ok,self)
        dialog.setDetailedText(error);dialog.exec()
    def request_preview(self,*args,allow_reveal=False):
        if not allow_reveal:self.import_reveal.cancel()
        self.stop_prefetch()
        if not self.loaded or self.batch_busy:return
        self.revision+=1
        if self.job is not None and self.task_kind=='preview':self.job.cancel.set()
        if self.job is not None:self.preview_pending=True
        else:self.preview_timer.start()
    def render_preview(self):
        if not self.loaded or self.batch_busy:return
        if self.job is not None:self.preview_pending=True;return
        value=copy.deepcopy(self.p);revision=self.revision;full=self.full_resolution;original=self.original_action.isChecked();start=time.perf_counter()
        hdr=self.hdr_canvas.active
        reveal=bool(self.import_reveal.pending and not original)
        source_path=self.info.get('path');frame_index=(self.info.get('strip') or {}).get('index')
        self.preview_job_revision=revision
        region=None
        if full:
            shape=geometry.output_shape(model.engine.STATE['full'].shape,value)
            if not self.canvas.region_mode or self.canvas.region_shape!=shape:self.canvas.begin_region(shape)
            region=self.canvas.region_box()
        monitor=self.monitor_profile;proof=value['output_space'] if self.proof_action.isChecked() and not original and not hdr else None
        def done(result):
            if (hdr!=self.hdr_canvas.active or revision!=self.revision or self.pending_command or self.info.get('path')!=source_path
                    or (self.info.get('strip') or {}).get('index')!=frame_index):return
            if 'region' in result:
                self.canvas.set_region_pixels(result['pixels'],result['region'],result['image_shape'],display=result.get('display'),diagnostics=None if original else result.get('diagnostics'))
            else:self.canvas.set_pixels(result['pixels'],full=self.reset_zoom,display=result.get('display'),diagnostics=None if original else result.get('diagnostics'))
            self.hdr_canvas.set_linear(result.get('hdr_linear') if hdr else None)
            self.reset_zoom=False
            self.histogram.set_bins(result['hist'])
            if not original and not full:
                try:
                    self.save_session(refresh_library=False);self.studio_store.save_thumbnail(self.info['path'],result['pixels'])
                    if self.current_batch_id and not self.batch_busy:
                        asset_id=self.current_workflow_asset();asset=self.studio_store.get_asset(asset_id)
                        path=self.studio_store.thumbnails.path(self.studio_store.thumbnails.key(asset))
                        self.batch_store.preview(self.current_batch_id,asset_id,path)
                    if self.studio_dialog and self.studio_dialog.isVisible():self.studio_dialog.refresh_library()
                except (OSError,ValueError,sqlite3.Error):pass  # Disposable preview cache.
            info=self.info.get('strip')
            if not original:
                nav=result.get('navigation_positive',result['pixels'])
                key=self.frame_reveal_key();path=self.info.get('path');index=frame_index
                def complete():
                    self.developed_frames.add(key);self.settings.setValue('developedFramesV1',sorted(self.developed_frames))
                    if self.info.get('path')==path and self.frame_reveal_key(index)==key and info and self.frames.count()>index:
                        from native_widgets import monitor_image
                        from PySide6.QtCore import QBuffer,QIODevice
                        image=image_from_array(nav).scaled(140,95,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
                        buffer=QBuffer();buffer.open(QIODevice.OpenModeFlag.WriteOnly);image.save(buffer,'JPG')
                        self.frame_thumbs[index]=bytes(buffer.data())
                        pix=QPixmap.fromImage(monitor_image(image,self.monitor_profile))
                        self.frames.item(index).setIcon(photo_icon(pix))
                if reveal and self.import_reveal.pending and 'reveal_original' in result:
                    self.reveal_finish=complete
                    self.import_reveal.start(result['reveal_original'],result.get('display',result['pixels']),result.get('reveal_original_hdr'),result.get('hdr_linear') if hdr else None)
                    if not self.import_reveal.active:self.finish_frame_reveal()
                elif not full:complete()
            if self.photo_assets and not original and not full and 'region' not in result:
                row=self.current_photo_row()
                if row>=0:
                    identifier=self.photo_assets[row]['id'];self.photo_rendered.add(identifier)
                    self.photo_images[identifier]=image_from_array(result['pixels']).scaled(112,76,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
                    self.paint_photo_icon(row)
            elapsed=round((time.perf_counter()-start)*1000)
            h,w=result['pixels'].shape[:2];_ui(self.statusBar().showMessage, _tf('{0} · {1}×{2} · {3} ms · ', _t('原片') if original else _t('正片'), w, h, elapsed)+self.monitor_name+(_t(' · 输出色域预览') if proof else '')+((' · '+value['output_space']+_tf(' 越界 {0:.3f}% · 16bit端点 {1:.3f}%', result['diagnostics']['fraction']*100, result['diagnostics']['endpoints']*100)) if not original else ''))
            if hdr:
                status=self.statusBar().currentMessage()+_t(' · 主画布 HDR · 直方图为 SDR 输出统计')
                if self.hdr_canvas.info.get('peak_estimated'):status+=_t(' · 屏幕峰值报告异常，暂按 1000 nit 预览，请校准 Windows HDR')
                _ui(self.statusBar().showMessage,status)
            if full:_ui(self.statusBar().showMessage, self.statusBar().currentMessage()+_t(' · 100% 可见区域统计'))
            elif getattr(self,'preview_fallback',None):
                _ui(self.statusBar().showMessage, self.preview_fallback);self.preview_fallback=None
            self.schedule_prefetch()
        def work(c,p):
            result=model.hdr_edit_preview(value,full,original,region,c) if hdr else (model.preview(value,full,original,region=region,cancel=c) if region is not None else model.preview(value,full,original))
            if 'wide' in result:
                from input_color import display_working
                result['display']=display_working(result['wide'],'prophoto',monitor,proof)
            elif monitor or proof:
                from input_color import display_pixels
                result['display']=display_pixels(result['pixels'],monitor,proof)
            if reveal:
                if full:result['navigation_positive']=model.preview(value,False)['pixels']
                raw=model.hdr_edit_preview(value,full,True,region,c) if hdr else model.preview(value,full,True,region,c)
                if c.is_set():raise InterruptedError('Import reveal canceled')
                from input_color import display_pixels
                if 'wide' in raw:
                    from input_color import display_working
                    result['reveal_original']=display_working(raw['wide'],'prophoto',monitor,None)
                else:result['reveal_original']=display_pixels(raw['pixels'],monitor,None) if monitor else raw['pixels']
                result['reveal_original_hdr']=raw.get('hdr_linear')
            return result
        self.start_task(work,done,_t('正在生成原图预览…') if full else _t('正在更新预览…'),'preview')
    def edit_parameter(self,key,value,immediate=False):
        if self.syncing:return
        if key=='meter_mode' and self.loaded and value!=self.p.get(key,'legacy'):
            changed=copy.deepcopy(self.p);changed[key]=value
            self.command(lambda c,p:model.meter(changed),self.set_parameters,_t('正在重估片基与跨度…'));return
        self.p[key]=value;self.history_timer.start()
        self.update_calibration_label()
        self.request_preview()
        if immediate:self.commit_history()
    def sync_ui(self):
        self.syncing=True
        for key,row in self.rows.items():row.set_value(self.p.get(key,0))
        for key,combo in self.combos.items():
            index=combo.findData(self.p.get(key));combo.setCurrentIndex(max(0,index))
        self.flip_check.setChecked(bool(self.p.get('flip')));self.repair_check.setChecked(bool(self.p.get('channel_repair')))
        self.curve.set_points(self.p['curve']);self.select_hsl(self.hsl_index)
        _ui(self.wb_label.setText, _t('RGB 增益 ')+ ' / '.join(_tf('{0:.3f}', v) for v in self.p['wb_gain']))
        self.update_calibration_label()
        self.update_geometry_label()
        _ui(self.base_label.setText, _t('片基 ')+(' / '.join(_tf('{0:.4f}', v) for v in self.p['base']) if self.p.get('base') else '—'))
        clipped=core.base_clipped_channels(self.p.get('base')) if self.p.get('mode') in ('color_negative','bw_negative') else []
        if clipped:_ui(self.base_label.setText, _t('片基 ')+(' / '.join(_tf('{0:.4f}',v) for v in self.p['base']))+'\n⚠ '+_t('、').join(_t(('红','绿','蓝')[i]) for i in clipped)+_t('通道到解码端点，请检查片基并重新取样。'))
        self.base_label.setStyleSheet('color:#ffffff;font-weight:600;' if clipped else '')
        _ui(self.base_label.setToolTip, _t('端点提示不能区分传感器饱和与解码转换剪裁。选择未曝光且未剪裁的片基；RAW 可尝试 ProPhoto 后复核。') if clipped else '')
        mode=self.p.get('flat_mode','off');_ui(self.flat_label.setText, {'off':_t('未启用平场'),'strip':_t('片基横向照明校正 · 按条独立'),'reference':_t('均匀参考平场 · 网格随配方保存')}[mode])
        health=self.info.get('channel_health',{})
        _ui(self.health_label.setText, _t('检测到欠曝通道：')+_t('、').join(_t(('红','绿','蓝')[i]) for i in health.get('bad',[]))+_t('。修复颜色为推断值。') if health.get('suggest') else '')
        self.syncing=False
    def commit_history(self):
        self.history_timer.stop()
        if not self.loaded:return
        value=copy.deepcopy(self.p)
        if self.history_index>=0 and self.history[self.history_index]==value:return
        self.history=self.history[:self.history_index+1]+[value]
        if len(self.history)>51:self.history.pop(0)
        self.history_index=len(self.history)-1;self._history_state();self.save_session()
    def reset_history(self):
        self.history=[copy.deepcopy(self.p)];self.history_index=0;self._history_state()
    def _history_state(self):
        self.undo_action.setEnabled(self.history_index>0);self.redo_action.setEnabled(0<=self.history_index<len(self.history)-1)
        if hasattr(self,'history_label'):_ui(self.history_label.setText, _tf('{0} / {1} 步', max(0,self.history_index), max(0,len(self.history)-1)))
    def travel(self,direction):
        if not self.loaded:return
        self.commit_history();index=self.history_index+direction
        if 0<=index<len(self.history):
            self.history_index=index;self.p=copy.deepcopy(self.history[index]);self.sync_ui();self._history_state();self.save_session();self.request_preview()
    def set_parameters(self,value):
        value=copy.deepcopy(value)
        space=model.engine.STATE.get('native_input',{}).get('working_space','srgb')
        if value.get('working_space','srgb')!=space:raise ValueError(_t('配方的工作空间不同，请先切换当前工作空间或使用匹配配方'))
        if value.get('calibration') and value['calibration'].get('working_space','srgb')!=space:raise ValueError(_t('色卡配置与工作空间不一致，请重新标定'))
        self.p=copy.deepcopy(value);self.sync_ui();self.commit_history();self.request_preview()
    def curve_changed(self,value):self.p['curve']=value;self.request_preview()
    def set_curve(self,value):self.p['curve']=value;self.curve.set_points(value);self.commit_history();self.request_preview()
    def select_hsl(self,index):
        self.hsl_index=index
        for i,b in enumerate(self.color_buttons):b.setChecked(i==index)
        self.hsl_sat.set_value(self.p['hsl'][index][0]);self.hsl_lum.set_value(self.p['hsl'][index][1])
    def edit_hsl(self,channel,value):
        if self.syncing:return
        self.p['hsl'][self.hsl_index][channel]=value;self.history_timer.start();self.request_preview()
    def reset_hsl(self):self.p['hsl']=[[0,0] for _ in range(8)];self.select_hsl(self.hsl_index);self.commit_history();self.request_preview()
    def reset_wb(self):self.p.update(wb_gain=[1,1,1],temp=0,tint=0);self.sync_ui();self.commit_history();self.request_preview()
    def reset_parameters(self):
        if not self.loaded:return
        p=core.Params(base=self.info.get('base'),span=self.info.get('span',1.75),anchor=self.info.get('anchor')).__dict__
        p['working_space']=self.p.get('working_space','srgb')
        p['meter_mode']=self.p.get('meter_mode','legacy')
        p['scene_hdr']=self.p.get('scene_hdr',False)
        if self.info.get('hdr_kind')=='positive':p.update(mode='positive',black=0.,white=1.,tone_rolloff=.75)
        self.command(lambda c,progress:model.meter(p),self.set_parameters,_t('正在重估片基与跨度…'))
    def viewport_moved(self):
        if self.loaded and self.full_resolution:self.request_preview()
    def fit_view(self):
        import viewport_render
        viewport_render.clear_context()
        self.full_resolution=False;self.canvas.end_region();self.canvas.fit_image();self.request_preview()
    def pixel_view(self):
        if not self.loaded:return
        self.full_resolution=True;self.canvas.fit_mode=False;self.reset_zoom=True;self.request_preview()
    def choose_sample(self,kind,checked):
        self.sample_kind=kind if checked else None
        for key,action in self.sample_actions.items():action.setChecked(key==self.sample_kind)
        self.canvas.set_sampling(self.sample_kind is not None)
        if checked:_ui(self.statusBar().showMessage, _t('在画布上点击取样；片基应选未曝光区域'))
    def sample_at(self,x,y):
        if not self.loaded or not self.sample_kind or self.pending_command:return
        kind=self.sample_kind;self.choose_sample(kind,False)
        value=copy.deepcopy(self.p)
        full=self.full_resolution
        self.command(lambda c,p:model.sample(value,x,y,kind,full),self.set_parameters,_t('正在取样…'))
    def automatic_wb(self,method):
        value=copy.deepcopy(self.p);self.command(lambda c,p:model.white_balance(value,method),self.set_parameters,_t('正在估计白平衡…'))
    def update_geometry_label(self):
        h,w=self.info.get('height',0),self.info.get('width',0)
        if not h or not w:_ui(self.geometry_label.setText, _t('完整画面'));return
        oh,ow=geometry.output_shape((h,w),self.p)
        _ui(self.geometry_label.setText, _tf('输出 {0} × {1} px · 旋转 {2}° · 拉直 {3:+.2f}°', ow, oh, self.p.get("rotation",0), self.p.get("straighten",0)))
        if self.p.get('perspective') is not None:_ui(self.geometry_label.setText, i18n.source_text(self.geometry_label)+_t(' · 已启用透视'))
    def open_local(self):
        if not self.loaded:return
        value=copy.deepcopy(self.p)
        def done(preview):
            preview['monitor']=self.monitor_profile
            dialog=LocalDialog(preview,value.get('local_edits',[]),self);self.local_dialog=dialog
            dialog.applied.connect(self.apply_local)
            dialog.finished.connect(lambda _:self.release_local(dialog));dialog.show()
        self.command(lambda c,p:model.local_preview(value),done,_t('正在准备局部精修…'))
    def release_local(self,dialog):
        if self.local_dialog is dialog:self.local_dialog=None
        dialog.deleteLater()
    def apply_local(self,edits):
        value=copy.deepcopy(self.p);value['local_edits']=edits;self.set_parameters(value)
    def open_geometry(self,mode=None):
        if not self.loaded:return
        if mode not in ('crop','straighten','perspective'):mode=None
        value=copy.deepcopy(self.p)
        def done(preview):
            preview['monitor']=self.monitor_profile
            dialog=GeometryDialog(preview,value,self);self.geometry_dialog=dialog
            if mode=='crop':dialog.line_button.setChecked(False);dialog.perspective_button.setChecked(False)
            elif mode=='straighten':dialog.line_button.setChecked(True)
            elif mode=='perspective':dialog.perspective_button.setChecked(True)
            dialog.applied.connect(self.apply_geometry)
            dialog.finished.connect(lambda _:self.release_geometry(dialog));dialog.show()
        self.command(lambda c,p:model.geometry_preview(value),done,_t('正在准备裁切与校直…'))
    def release_geometry(self,dialog):
        if self.geometry_dialog is dialog:self.geometry_dialog=None
        dialog.deleteLater()
    def apply_geometry(self,value):
        p=copy.deepcopy(self.p)
        for key in ('rotation','straighten','crop'):p[key]=copy.deepcopy(value[key])
        p['perspective']=copy.deepcopy(value.get('perspective'))
        self.canvas.fit_mode=True
        self.set_parameters(model.params(p).__dict__)
    def reset_geometry(self):
        self.apply_geometry({'rotation':0,'straighten':0.,'crop':geometry.FULL.copy()})
    def open_frames(self):
        if not self.loaded:return
        def done(preview):
            preview['monitor']=self.monitor_profile
            dialog=FrameLayoutDialog(preview,self);self.frame_dialog=dialog
            dialog.applied.connect(self.apply_frames)
            dialog.finished.connect(lambda _:self.release_frames(dialog));dialog.show()
        self.command(lambda c,p:model.layout_editor(),done,_t('正在准备原始扫描与切帧选框…'))
    def release_frames(self,dialog):
        if self.frame_dialog is dialog:self.frame_dialog=None
        dialog.deleteLater()
    def apply_frames(self,spec):
        value=copy.deepcopy(self.p);options=self.project_options()
        def done(result):
            self.layout_previous=result['previous'];self.on_reframed(result)
            _ui(self.statusBar().showMessage, _tf('切帧已更新；{0} 帧边界 / 方向改变，可从编辑菜单恢复切帧前状态。', result["changed"]))
        self.command(lambda c,p:model.reframe(spec,value,options),done,_t('正在应用输入模式与切帧…'))
    def on_reframed(self,result):
        self.info=result['info'];self.p=result['params'];self.full_resolution=False;self.canvas.fit_mode=True
        if self.sample_kind:self.choose_sample(self.sample_kind,False)
        self.sync_ui();self.reset_history();self.build_frames(result['thumbs']);self._enable_document(True)
        self.save_session();self.request_preview()
    def restore_frames(self):
        if self.layout_previous is None:return
        snapshot=copy.deepcopy(self.layout_previous)
        def done(result):
            self.layout_previous=None
            self.keep_check.setChecked(snapshot['options']['keep_params']);self.meter_check.setChecked(snapshot['options']['auto_meter'])
            self.on_reframed(result)
        self.command(lambda c,p:model.restore_layout(snapshot),done,_t('正在恢复切帧前的整卷状态…'))
    def open_calibration(self):
        if not self.loaded:return
        value=copy.deepcopy(self.p)
        def done(preview):
            preview['monitor']=self.monitor_profile
            dialog=CalibrationDialog(preview,value.get('calibration'),ROOT/'samples',self);self.chart_dialog=dialog
            dialog.fitRequested.connect(lambda refs,points,name:self.run_calibration(value,refs,points,name))
            dialog.applied.connect(self.apply_calibration)
            dialog.finished.connect(lambda _:self.release_calibration(dialog));dialog.show()
        self.command(lambda c,p:model.calibration_preview(value),done,_t('正在准备色卡工作台…'))
    def release_calibration(self,dialog):
        if self.chart_dialog is dialog:self.chart_dialog=None
        dialog.deleteLater()
    def update_calibration_label(self):
        profile=self.p.get('calibration')
        text=(_t('黑白模式不应用彩色色卡校正') if self.p['mode']=='bw_negative' else
            _tf('色卡校正：{0} · 强度 {1:.0%}', profile.get('name',_t('自定义')), self.p['calibration_strength'])) if profile else _t('未启用色卡校正')
        if profile and self.p['mode']!='bw_negative':
            source=model.engine.STATE.get('native_source',{}).get('sha256')
            if profile.get('source_sha256') and source and profile['source_sha256']!=source:
                text+=_t('\n配置来自另一原片，请复验当前曝光与光源。')
            elif profile.get('parameter_signature') and profile['parameter_signature']!=model.calibration_signature(model.params(self.p)):
                text+=_t('\n参数与标定时不同，请重新验证。')
        _ui(self.calibration_label.setText, text)
    def run_calibration(self,value,reference,points,name):
        self.command(lambda c,p:model.fit_calibration(value,reference,points,name),self.chart_dialog.set_result,_t('正在拟合色卡并验证…'))
    def apply_calibration(self,profile):
        if profile and profile.get('working_space','srgb')!=self.p.get('working_space','srgb'):
            raise ValueError(_t('色卡配置与当前工作空间不一致，请重新标定'))
        value=copy.deepcopy(self.p);value.update(calibration=profile,calibration_strength=1.)
        self.set_parameters(value)
    def clear_calibration(self):
        if not self.loaded:return
        value=copy.deepcopy(self.p);value.update(calibration=None,calibration_strength=1.);self.set_parameters(value)
    def import_calibration(self):
        if not self.loaded:return
        path,_=_ui(QFileDialog.getOpenFileName, self,_t('导入线性 RGB 色卡校正'),self.output_edit.text(),_t('JSON 校正配置 (*.json)'))
        if path:
            try:
                if Path(path).stat().st_size>256*1024:raise ValueError(_t('校正配置超过 256 KB'))
                data=json.loads(Path(path).read_text('utf-8'));profile=calibration.normalize_profile(data)
                if profile is None or data.get('format')!='film-invert-calibration' or data.get('version') not in (1,2):
                    raise ValueError(_t('校正配置格式或矩阵无效'))
                if not data.get('report',{}).get('can_apply'):raise ValueError(_t('此校正配置尚未通过留一验证'))
                self.apply_calibration(profile)
            except Exception:self.show_error(traceback.format_exc())
    def recalculate(self):
        value=copy.deepcopy(self.p);self.command(lambda c,p:model.meter(value),self.set_parameters,_t('正在重估片基与跨度…'))
    def strip_flat(self):
        value=copy.deepcopy(self.p);value.update(flat_mode='strip',flat_strength=1)
        self.command(lambda c,p:model.meter(value),self.set_parameters,_t('正在估计各条照明…'))
    def clear_flat(self):
        value=copy.deepcopy(self.p);value.update(flat_mode='off',flat_strength=0,flat_field=None)
        self.command(lambda c,p:model.meter(value),self.set_parameters,_t('正在清除平场…'))
    def flat_dialog(self):
        if not self.loaded:return
        path,_=_ui(QFileDialog.getOpenFileName, self,_t('选择同尺寸均匀参考平场'),self.info['path'],IMAGE_FILTER)
        if path:
            value=copy.deepcopy(self.p);self.command(lambda c,p:model.flat_reference(value,path),self.set_parameters,_t('正在读取均匀参考平场…'))
    def open_dialog(self):
        path,_=_ui(QFileDialog.getOpenFileName, self,_t('打开胶片图像'),str(self.settings.value('lastOpen',ROOT/'samples')),IMAGE_FILTER)
        if path:self.open_path(path)
    def dragEnterEvent(self,event):
        if dropped_paths(event.mimeData()):event.acceptProposedAction()
        else:event.ignore()
    def dragMoveEvent(self,event):self.dragEnterEvent(event)
    def dropEvent(self,event):
        paths=dropped_paths(event.mimeData())
        if paths:self.open_dropped_paths(paths);event.acceptProposedAction()
        else:event.ignore()
    def open_dropped_paths(self,paths):
        if self.closing:return
        if len(paths)==1:self.open_path(paths[0])
        elif paths:
            result=self.import_library_paths(paths)
            if result and result['ids']:
                if self.studio_dialog:self.studio_dialog.hide()
                self.current_batch_id=self.batch_store.create(result['ids'],_t('拖入批次 ')+time.strftime('%m-%d %H:%M'))
                self.settings.setValue('lastBatch',self.current_batch_id)
                self.set_photo_batch(result['ids']);self.go_photo(0)
    def open_path(self,path):
        if not path:return
        if Path(path).suffix.lower()=='.filmproj':self.open_project_path(path);return
        path=str(Path(path).resolve());self.full_resolution=False;self.canvas.fit_mode=True
        mode=self.open_mode.currentData()
        color=self.settings.value('inputColor','legacy')
        space='prophoto' if self.wide_open_action.isChecked() else 'srgb'
        self.command(lambda c,p:model.open_image(path,input_mode=mode,input_color=color,working_space=space),self.on_opened,_t('正在读取并检测胶片…'))
    def show_input_color(self):
        if not self.loaded:return
        info=self.info.get('input_color') or {'encoding':self.info.get('note',_t('旧版读取规则'))}
        space=self.p.get('working_space','srgb')
        text=info.get('encoding','')+_t('\n内部处理：线性 ')+space+_t('\n选择：')+info.get('selection','legacy')
        text+=_t('\n输入转换超出当前工作空间范围的像素：')+_tf('{0:.3f}%', info.get("clipped_fraction",0)*100)
        if info.get('icc_sha256'):text+=_t('\n嵌入 ICC SHA256：')+info['icc_sha256']
        _ui(QMessageBox.information, self,_t('输入色彩信息'),text)
    def choose_monitor(self):
        from input_color import profile_space
        path,_=_ui(QFileDialog.getOpenFileName, self,_t('选择此显示器的 RGB ICC'),'',_t('ICC 配置 (*.icc *.icm)'))
        if not path:return
        try:
            if Path(path).stat().st_size>4*1024*1024:raise ValueError(_t('ICC 超过 4 MB'))
            data=Path(path).read_bytes();space=profile_space(data,True)
            self.monitor_profile=data;self.monitor_name=_t('显示器 ICC：')+(space.description() or Path(path).name);self.refresh_monitor()
        except Exception:self.show_error(traceback.format_exc())
    def refresh_monitor(self):
        if hasattr(self,'frame_thumbs'):self.build_frames(self.frame_thumbs)
        for row in range(len(self.photo_assets)):self.paint_photo_icon(row)
        if self.studio_dialog:self.studio_dialog.refresh_library()
        for name,method in (('local_dialog','refresh'),('geometry_dialog','refresh'),('chart_dialog','show_preview')):
            dialog=getattr(self,name,None)
            if dialog:
                dialog.preview['monitor']=self.monitor_profile;getattr(dialog,method)()
        dialog=getattr(self,'frame_dialog',None)
        if dialog:
            from input_color import display_working
            preview=dialog.preview
            shown=display_working(preview['working_pixels'],preview['working_space'],self.monitor_profile)
            dialog.canvas.set_pixels(preview['pixels'],display=shown)
        self.request_preview()

    def clear_monitor(self):
        self.monitor_profile=None;self.monitor_name=_t('sRGB 默认显示');self.refresh_monitor()
    def change_input_color(self):
        if not self.loaded:return
        from input_color import CHOICES
        titles=list(map(_t,CHOICES.values()));keys=list(CHOICES)
        current=model.engine.STATE['native_input'].get('color','legacy')
        title,ok=_ui(QInputDialog.getItem, self,_t('输入色彩'),_t('选择编码；将重新读取并重置整卷调色，当前工程会先自动备份。'),titles,keys.index(current),False)
        if not ok:return
        color=keys[titles.index(title)]
        if _ui(QMessageBox.question, self,_t('重新解释原片'),_t('输入色彩变化后需要重新调色。当前整卷编辑将备份到当前用户数据目录的 studio/snapshots。继续？'))!=QMessageBox.StandardButton.Yes:return
        self.commit_history();self.save_session()
        import uuid
        snapshot=model.project_snapshot(self.p,self.project_options())
        self.studio_store.materialize(snapshot,'before-input-color-'+uuid.uuid4().hex)
        spec=copy.deepcopy(model.engine.STATE['native_input']);path=self.info['path']
        self.full_resolution=False;self.canvas.fit_mode=True
        self.command(lambda c,p:model.open_image(path,input_mode=spec['mode'],manual=spec['manual'],input_color=color,working_space=spec.get('working_space','srgb')),self.on_opened,_t('正在按输入色彩重新解码…'))
    def change_working_space(self):
        if not self.loaded:return
        names=[_t('sRGB 兼容工作空间'),_t('ProPhoto 宽色域工作空间')]
        title,ok=_ui(QInputDialog.getItem, self,_t('当前工作空间'),_t('更改后重新解码并重置整卷编辑；先自动备份当前工程。'),names,1 if self.p.get('working_space')=='prophoto' else 0,False)
        if not ok:return
        space='prophoto' if title==names[1] else 'srgb'
        if space==self.p.get('working_space','srgb'):return
        if _ui(QMessageBox.question, self,_t('更改工作空间'),_t('将重置整卷调色，并备份当前工程。宽色域模式需要重新测光和色卡标定。继续？'))!=QMessageBox.StandardButton.Yes:return
        self.commit_history();self.save_session()
        import uuid
        self.studio_store.materialize(model.project_snapshot(self.p,self.project_options()),'before-workspace-'+uuid.uuid4().hex)
        spec=copy.deepcopy(model.engine.STATE['native_input']);path=self.info['path']
        self.full_resolution=False;self.canvas.fit_mode=True
        self.command(lambda c,p:model.open_image(path,input_mode=spec['mode'],manual=spec['manual'],input_color=spec.get('color','legacy'),working_space=space),self.on_opened,_t('正在转换工作空间…'))
    def on_opened(self,result):
        self.loaded=True;self.info=result['info'];self.p=result['params']
        if self.photo_all_assets and not any(os.path.normcase(a['path'])==os.path.normcase(self.info['path']) for a in self.photo_all_assets):self.set_photo_batch([])
        self.layout_previous=None
        self.project_path=result.get('project_path','')
        self.saved_project_signature=result.get('saved_signature');self.project_dirty=False
        options=result.get('options')
        if options:
            self.keep_check.blockSignals(True);self.meter_check.blockSignals(True)
            self.keep_check.setChecked(options['keep_params']);self.meter_check.setChecked(options['auto_meter'])
            self.keep_check.blockSignals(False);self.meter_check.blockSignals(False)
        if self.restore_params is not None:
            self.p=model.params(self.restore_params).__dict__;self.restore_params=None
        self.sync_ui();self.reset_history();self.build_frames(result['thumbs']);self._enable_document(True)
        self.update_document_title()
        self.settings.setValue('lastOpen',str(Path(self.info['path']).parent));self.remember(self.info['path']);self.save_session()
        if result.get('_navigation_reveal'):self.import_reveal.arm(self.p.get('mode'))
        else:self.arm_frame_reveal()
        self.request_preview(allow_reveal=True)
        if self.photo_filters.active():self.refresh_photo_filter()
    def frame_reveal_key(self,index=None):
        import hashlib
        source=model.engine.STATE.get('native_source') or {}
        identity=[os.path.normcase(source.get('path','')),source.get('sha256'),model.engine.STATE.get('native_input'),model.source_region(index)]
        return hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    def arm_frame_reveal(self):
        self.import_reveal.cancel()
        if self.frame_reveal_key() not in self.developed_frames:self.import_reveal.arm(self.p.get('mode'))
    def finish_frame_reveal(self):
        callback=self.reveal_finish;self.reveal_finish=None
        if callback:callback()
    def build_frames(self,thumbs):
        self.frame_thumbs=thumbs
        from native_widgets import monitor_image
        self.frames.blockSignals(True);_ui(self.frames.clear)
        originals=model.original_thumbnails() if self.p.get('mode') in ('color_negative','bw_negative') else []
        for i,jpg in enumerate(thumbs):
            if i<len(originals) and self.frame_reveal_key(i) not in self.developed_frames:jpg=originals[i]
            pix=QPixmap();pix.loadFromData(jpg)
            pix=QPixmap.fromImage(monitor_image(pix.toImage(),self.monitor_profile))
            item=_ui(QListWidgetItem, photo_icon(pix),_tf('第 {0} 帧', i+1));_ui(self.frames.addItem, item)
        index=self.info.get('strip',{}).get('index',0) if self.info.get('strip') else -1
        self.frames.setCurrentRow(index);self.frames.blockSignals(False);self.update_navigation()
    def update_navigation(self):
        text=_tf('{0} · {1} × {2} · ', self.info.get("name",_t('未打开')), self.info.get("width",0), self.info.get("height",0))+('ProPhoto' if self.p.get('working_space')=='prophoto' else 'sRGB')
        _ui(self.source_label.setText, text);_ui(self.source_label.setToolTip, text+'\n'+self.info.get('path','')+'\n'+self.info.get('note',''))
        info=self.info.get('strip');is_strip=bool(info);batch=bool(self.photo_all_assets)
        self.photo_filters.setVisible(batch and self.navigation_mode.currentIndex()==0)
        self.review_button.setVisible(batch);self.review_button.setEnabled(self.loaded and not self.batch_busy)
        self.navigation_mode.setVisible(batch)
        self.navigation_mode.model().item(1).setEnabled(is_strip)
        if not is_strip:self.navigation_mode.setCurrentIndex(0)
        photo_mode=batch and self.navigation_mode.currentIndex()==0
        active=is_strip or batch
        if active!=self.filmstrip_active:
            self.filmstrip_active=active;self.nav_dock.setMinimumHeight(180 if active else 94)
            self.nav_dock.setMaximumHeight(360 if active else 112)
            self.resizeDocks([self.nav_dock],[254 if batch else 220 if active else 104],Qt.Orientation.Vertical)
        _ui(self.nav_dock.setWindowTitle, _t('照片 / 帧导航') if batch else _t('整卷 / 帧导航'))
        self.photos.setVisible(photo_mode);self.frames.setVisible(is_strip and not photo_mode)
        for widget in (self.keep_check,self.meter_check,self.apply_button):widget.setVisible(not photo_mode);widget.setEnabled(is_strip)
        self.roll_action.setEnabled(self.loaded and is_strip)
        index=self.current_photo_row() if photo_mode else (info['index'] if is_strip else -1)
        count=len(self.photo_assets) if photo_mode else (info['frames'] if is_strip else 0)
        _ui(self.prev_button.setText, _t('‹ 上一张') if photo_mode else _t('‹ 上一帧'));_ui(self.next_button.setText, _t('下一张 ›') if photo_mode else _t('下一帧 ›'))
        self.prev_button.setEnabled(index>0);self.next_button.setEnabled(index<count-1 and (index>=0 or photo_mode))
        if photo_mode:
            self.photos.blockSignals(True);self.photos.setCurrentRow(index);self.photos.blockSignals(False)
            if index>=0:self.photos.scrollToItem(self.photos.item(index))
            _ui(self.frame_label.setText, _tf('{0} / {1} 张照片', index+1, count) if index>=0 else _tf('当前图不在筛选中 · {0} 张结果', count))
            if index>=0:
                asset=self.photo_assets[index]
                if asset['rating'] or asset['flag']!='none':_ui(self.frame_label.setText, i18n.source_text(self.frame_label)+_tf(' · {0}★ ', asset["rating"])+{'pick':_t('精选'),'reject':_t('弃选'),'none':''}[asset['flag']])
            _ui(self.frame_label.setToolTip, _tf('筛选显示 {0} / {1} 张；筛选不会切换或丢弃当前照片编辑', count, len(self.photo_all_assets)))
        else:_ui(self.frame_label.setText, (_tf('{0} / {1} 帧 · ', info["index"]+1, info["frames"])+(_t('手动布局') if info.get('manual') else _tf('{0} 条', info["strips"]))) if is_strip else _t('单张图像'))
    def current_photo_row(self):
        current=os.path.normcase(self.info.get('path',''))
        return next((i for i,row in enumerate(self.photo_assets) if os.path.normcase(row['path'])==current),-1)
    def navigate_relative(self,step):
        if self.photo_all_assets and self.navigation_mode.currentIndex()==0:self.go_photo(self.current_photo_row()+step)
        elif self.info.get('strip'):self.go_frame(self.info['strip']['index']+step)
    def set_photo_batch(self,identifiers):
        self.stop_prefetch();_ui(self.prefetch_attempted.clear)
        self.photo_generation+=1;_ui(self.photo_rendered.clear);_ui(self.photo_images.clear)
        if self.photo_worker:self.photo_worker.requestInterruption()
        self.photo_all_assets=catalog(self.studio_store,list(identifiers));self.photo_assets=list(self.photo_all_assets)
        self.photos.blockSignals(True);_ui(self.photos.clear)
        for row in self.photo_assets:
            item=_ui(QListWidgetItem, tool_icon('library'),row['name']);item.setData(Qt.ItemDataRole.UserRole,row['id'])
            _ui(item.setToolTip, row['path']+_t('\n正在读取缩略图…'));_ui(self.photos.addItem, item)
        self.photos.blockSignals(False);self.navigation_mode.setCurrentIndex(0)
        self.photo_filters.reset()
        if identifiers:self.nav_dock.show()
        self.start_photo_thumbnails()
    def start_photo_thumbnails(self):
        if self.photo_worker or not self.photo_all_assets or self.closing:return
        worker=ThumbnailWorker(self.studio_store.thumbnails,self.photo_all_assets,self.photo_generation,self,store=self.studio_store);self.photo_worker=worker
        worker.ready.connect(self.photo_thumbnail_ready);worker.finished.connect(self.photo_thumbnails_finished);worker.start()
    def photo_thumbnail_ready(self,generation,identifier,path,label):
        if generation!=self.photo_generation:return
        row=next((i for i,a in enumerate(self.photo_assets) if a['id']==identifier),-1)
        if identifier not in self.photo_rendered:
            if path:self.photo_images[identifier]=QPixmap(path).toImage().scaled(112,76,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
            if row>=0:
                self.paint_photo_icon(row);_ui(self.photos.item(row).setToolTip, self.photo_assets[row]['path']+'\n'+label+_t('；点击打开'))
    def paint_photo_icon(self,row):
        from native_widgets import monitor_image
        image=self.photo_images.get(self.photo_assets[row]['id'])
        if image is not None:self.photos.item(row).setIcon(photo_icon(QPixmap.fromImage(monitor_image(image,self.monitor_profile))))
    def photo_thumbnails_finished(self):
        worker=self.photo_worker;self.photo_worker=None;generation=worker.generation;worker.deleteLater()
        if self.closing:QTimer.singleShot(0,self.close)
        elif generation!=self.photo_generation:self.start_photo_thumbnails()
    def go_photo(self,index):
        if not 0<=index<len(self.photo_assets):return
        if index==self.current_photo_row():self.update_navigation();return
        _ui(self.prefetch_attempted.clear)
        self.prefetch_direction=1 if index>self.current_photo_row() else -1
        self.open_library_asset(self.photo_assets[index]['id'],reveal=True)
    def go_frame(self,index):
        info=self.info.get('strip')
        if not info or index<0 or index>=info['frames'] or index==info['index']:return
        value=copy.deepcopy(self.p);keep=self.keep_check.isChecked();meter=self.meter_check.isChecked()
        def done(result):
            self.info=result['info'];self.p=result['params'];self.sync_ui();self.reset_history();self.update_navigation()
            self.frames.blockSignals(True);self.frames.setCurrentRow(index);self.frames.blockSignals(False);self.save_session()
            self.import_reveal.arm(self.p.get('mode'));self.request_preview(allow_reveal=True)
        self.command(lambda c,p:model.change_frame(index,value,keep,meter),done,_t('正在切换帧…'))
    def apply_roll(self):
        value=copy.deepcopy(self.p);meter=self.meter_check.isChecked()
        def work(cancel,progress):model.apply_all(value,meter);return model.thumbnails()
        def done(thumbs):self.build_frames(thumbs);self.save_session();_ui(self.statusBar().showMessage, _t('当前调色已应用到全部帧'))
        self.command(work,done,_t('正在同步整卷参数…'))
    def current_metadata(self):
        if self.loaded:
            for row in self.studio_store.assets(include_snapshot=False):
                if Path(row['path'])==Path(self.info['path']):return {k:row[k] for k in ('rating','flag','roll')}
        return {'rating':0,'flag':'none','roll':''}
    def export_options(self):
        from export_policy import options
        return options(self.naming_edit.text().strip(),self.current_metadata()['roll'])
    def contact_dialog(self):
        if not self.loaded:return
        folder=_ui(QFileDialog.getExistingDirectory, self,_t('选择联系表输出位置'),self.output_edit.text())
        if folder:self.contact_to(folder)
    def contact_to(self,folder):
        from contact_sheet import export
        try:
            snapshot=model.project_snapshot(copy.deepcopy(self.p),self.project_options());snapshot['_export']=self.export_options()
        except ValueError as error:self.show_error(str(error));return
        def done(result):
            self.last_export=result['folder'];_ui(self.last_path_label.setText, _t('联系表：')+result['folder'])
            _ui(self.statusBar().showMessage, _tf('已保存 {0} 页 PNG 联系表 / {1} 帧：', result['pages'], result['frames'])+result['folder'])
        self.command(lambda c,p:export(folder,snapshot,c,p),done,_t('正在生成整卷联系表…'),'contact')
    def pack_dialog(self):
        if not self.loaded:return
        path,_=_ui(QFileDialog.getSaveFileName, self,_t('打包当前整卷工程与原片'),str(Path(self.output_edit.text())/(Path(self.info['path']).stem+'.filmtransfer')),_t('工程迁移包 (*.filmtransfer)'))
        if path:
            if not path.lower().endswith('.filmtransfer'):path+='.filmtransfer'
            self.pack_to(path)
    def pack_to(self,path):
        from project_transfer import pack
        snapshot=model.project_snapshot(copy.deepcopy(self.p),self.project_options());labels=self.current_metadata()
        def done(result):_ui(self.statusBar().showMessage, _t('工程、原片与标签迁移包已保存：')+result)
        self.command(lambda c,p:pack(snapshot,path,labels,c),done,_t('正在校验并打包原片与工程…'),'transfer')
    def unpack_dialog(self):
        path,_=_ui(QFileDialog.getOpenFileName, self,_t('选择工程迁移包'),'',_t('工程迁移包 (*.filmtransfer)'))
        if not path:return
        folder=_ui(QFileDialog.getExistingDirectory, self,_t('选择解包位置（自动新建独立文件夹）'),self.output_edit.text())
        if folder:self.unpack_from(path,folder)
    def unpack_from(self,path,folder):
        from project_transfer import unpack
        def work(cancel,progress):
            result=unpack(path,folder,cancel)
            result['document']=model.open_project(result['project']);return result
        def done(result):
            self.full_resolution=False;self.canvas.fit_mode=True;self.on_opened(result['document'])
            asset=self.studio_store.capture(model.project_snapshot(self.p,self.project_options()))
            self.studio_store.metadata(asset,**result['metadata'])
            _ui(self.statusBar().showMessage, _t('迁移工程已恢复：')+result['folder'])
        self.command(work,done,_t('正在校验、解包并恢复工程…'),'transfer')
    def acceptance_dialog(self):
        path,_=_ui(QFileDialog.getSaveFileName, self,_t('保存本机验收报告（不含原片内容或路径）'),'FilmInvert-acceptance.json','JSON (*.json)')
        if path:
            if not path.lower().endswith('.json'):path+='.json'
            from runtime_metrics import report
            try:model.write_json(path,report());_ui(self.statusBar().showMessage, _t('本机验收报告已保存：')+path)
            except Exception:self.show_error(traceback.format_exc())
    def output_folder(self):
        folder=_ui(QFileDialog.getExistingDirectory, self,_t('选择输出文件夹'),self.output_edit.text())
        if folder:_ui(self.output_edit.setText, folder);self.settings.setValue('output',folder)
    def open_output(self):
        path=Path(self.last_export).parent if self.last_export and Path(self.last_export).is_file() else Path(self.last_export or self.output_edit.text())
        path.mkdir(parents=True,exist_ok=True);QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
    def export_dialog(self,checked=False,*,hdr=None):
        if not self.loaded:return
        viewer=hdr is None and self.hdr_canvas.active
        hdr=self.hdr_canvas.active if hdr is None else hdr
        sdr_filter='SDR TIFF - 16bit (*.tif *.tiff)';hdr_filter='HDR TIFF - Float32 (*.tif *.tiff)'
        viewer_filter='Windows HDR - JPEG XR (*.jxr)'
        stem=Path(self.info['path']).stem;info=self.info.get('strip');suffix=_tf('-f{0:02d}', info["index"]+1) if info else ''
        try:
            from export_policy import filename
            name=filename(self.info['path'],info['index']+1 if info else 1,self.export_options(),_tf('{0}{1}-positive.tif', stem, suffix))
        except ValueError as error:self.show_error(str(error));return
        if hdr:name=Path(name).stem+('-hdr.jxr' if viewer else '-hdr.tif')
        default=Path(self.output_edit.text())/name
        path,selected=_ui(QFileDialog.getSaveFileName, self,_t('导出 TIFF / HDR'),str(default),';;'.join((sdr_filter,hdr_filter,viewer_filter)),viewer_filter if viewer else hdr_filter if hdr else sdr_filter)
        if not path:return
        if selected==viewer_filter:
            suffix=Path(path).suffix.lower()
            if suffix in ('.tif','.tiff'):path=str(Path(path).with_suffix('.jxr'))
            elif suffix!='.jxr':path+='.jxr'
            self.hdr_export_to(path,viewer=True);return
        if Path(path).suffix.lower()=='.jxr':path=str(Path(path).with_suffix('.tif'))
        elif Path(path).suffix.lower() not in ('.tif','.tiff'):path+='.tif'
        if selected==hdr_filter:self.hdr_export_to(path)
        else:self.export_to(path)
    def open_hdr_merge(self):
        from native_hdr import MergeDialog
        dialog=getattr(self,'hdr_merge_dialog',None)
        if dialog is not None and dialog.worker is not None:dialog.show();dialog.raise_();return
        self.hdr_merge_dialog=MergeDialog(self)
        self.hdr_merge_dialog.merged.connect(self.open_path);self.hdr_merge_dialog.show()
    def toggle_import_reveal(self,on):
        self.settings.setValue('importRevealEnabled',on)
        if not on:self.import_reveal.cancel()
    def hdr_export_dialog(self):
        self.export_dialog(hdr=True)
    def hdr_export_to(self,path,viewer=False):
        if not self.loaded:return
        value=copy.deepcopy(self.p)
        def done(result):
            self.last_export=result;_ui(self.last_path_label.setText,_t('已保存：\n')+result)
            _ui(self.output_edit.setText,str(Path(result).parent));self.settings.setValue('output',self.output_edit.text())
            _ui(self.statusBar().showMessage,_t('已保存 Windows HDR 图像：')+result if viewer else _t('已保存线性 HDR TIFF：')+result)
        export=model.export_hdr_view if viewer else model.export_hdr
        self.command(lambda c,p:export(path,value,c),done,_t('正在导出线性 HDR 数据…'),'export')
    def toggle_hdr_canvas(self,on):
        if on:
            try:self.hdr_canvas.start()
            except Exception as error:
                self.hdr_canvas_failed(str(error));return
        else:self.hdr_canvas.stop()
        for action in (self.proof_action,self.clipping_action):action.setEnabled(not on)
        self.canvas.clipping_item.setVisible(not on and self.clipping_action.isChecked())
        self.revision+=1;self.request_preview()
    def hdr_canvas_failed(self,error):
        self.hdr_canvas.stop();self.hdr_canvas_action.setChecked(False)
        for action in (self.proof_action,self.clipping_action):action.setEnabled(True)
        self.canvas.update_clipping();self.revision+=1
        _ui(self.statusBar().showMessage,_t('主画布 HDR 不可用，已切回 SDR：')+error)
        if self.loaded:self.request_preview()
    def open_hdr_preview(self):
        if not self.loaded:return
        value=copy.deepcopy(self.p)
        def done(result):
            from native_hdr import HDRPreview
            old=getattr(self,'hdr_preview_dialog',None)
            if old is not None:old.close()
            self.hdr_preview_dialog=HDRPreview(result,self);self.hdr_preview_dialog.show()
        self.command(lambda c,p:model.hdr_preview(value),done,_t('正在准备 HDR 屏幕预览…'))
    def export_to(self,path):
        value=copy.deepcopy(self.p)
        def done(result):
            self.last_export=result;_ui(self.last_path_label.setText, _t('已保存：\n')+result)
            _ui(self.output_edit.setText, str(Path(result).parent));self.settings.setValue('output',self.output_edit.text())
            _ui(self.statusBar().showMessage, _t('16bit TIFF 与完整参数配方已保存：')+result)
        self.command(lambda c,p:model.export_image(path,value),done,_t('正在导出全尺寸 TIFF…'),'export')
    def batch_dialog(self):
        if not self.info.get('strip'):return
        folder=_ui(QFileDialog.getExistingDirectory, self,_t('选择整卷输出位置'),self.output_edit.text())
        if folder:self.batch_to(folder)
    def batch_to(self,folder):
        value=copy.deepcopy(self.p);meter=self.meter_check.isChecked()
        try:config=self.export_options()
        except ValueError as error:self.show_error(str(error));return
        def done(result):
            self.last_export=result['folder'];_ui(self.last_path_label.setText, _t('整卷输出：\n')+result['folder'])
            _ui(self.statusBar().showMessage, _tf('{0}：{1}/{2} · ', _t('已取消，保留已完成帧') if result["cancelled"] else _t('整卷导出完成'), result["completed"], result["total"])+result['folder'])
        self.command(lambda c,p:model.export_roll(folder,value,meter,c,p,config),done,_t('正在导出整卷…'),'batch')
    def cancel_job(self):
        if self.job:
            self.job.cancel.set();self.cancel_button.setEnabled(False)
            _ui(self.statusBar().showMessage, _t('将在当前处理单元完成后停止；已发布的成品保留，未发布的暂存结果清理。'))
    def save_recipe(self):
        if not self.loaded:return
        path,_=_ui(QFileDialog.getSaveFileName, self,_t('保存完整参数配方'),str(Path(self.output_edit.text())/'film-invert-recipe.json'),_t('JSON 配方 (*.json)'))
        if path:
            if not path.lower().endswith('.json'):path+='.json'
            try:model.write_json(path,model.recipe(self.p));_ui(self.statusBar().showMessage, _t('配方已保存：')+path)
            except Exception:self.show_error(traceback.format_exc())
    def load_recipe(self):
        path,_=_ui(QFileDialog.getOpenFileName, self,_t('载入参数配方'),self.output_edit.text(),_t('JSON 配方 (*.json)'))
        if not path:return
        try:
            if Path(path).stat().st_size>32*1024*1024:raise ValueError(_t('配方超过 32 MB'))
            data=json.loads(Path(path).read_text('utf-8'))
            normalized=model.recipe_params(data)
            exclude=[] if self.full_recipe_action.isChecked() else ['base','anchor','span','black','white','flat_field','flat_strength','flat_mode','vignette','local_edits']
            self.set_parameters({key:normalized[key] if key not in exclude else value for key,value in self.p.items()})
        except Exception:self.show_error(traceback.format_exc())
    def save_session(self,refresh_library=True):
        if not self.loaded or self.batch_busy or self.workflow_reloading:return
        try:
            data=model.project_snapshot(self.p,self.project_options());data['project_file']=self.project_path
            data['saved_project_signature']=self.saved_project_signature
            self.project_dirty=bool(self.project_path and model.project_signature(data)!=self.saved_project_signature)
            self.update_document_title()
            model.write_json(self.profile/'session.json',data)
            asset_id=self.studio_store.capture(data)
            if self.current_batch_id:
                self.batch_store.adopt(self.current_batch_id,asset_id,data)
            if refresh_library and self.studio_dialog and self.studio_dialog.isVisible():self.studio_dialog.refresh_library()
        except (OSError,ValueError,sqlite3.Error) as error:
            _ui(self.statusBar().showMessage, _t('自动会话保存失败，请手动保存工程：')+str(error))
    def open_studio(self):
        self.commit_history();self.save_session()
        if self.studio_dialog is None:
            dialog=StudioDialog(self.studio_store,self.queue_controller,IMAGE_FILTER,self);self.studio_dialog=dialog
            dialog.openRequested.connect(self.open_library_asset);dialog.importRequested.connect(self.import_library_paths)
            dialog.captureRequested.connect(self.save_session)
            dialog.relinked.connect(self.library_relinked);dialog.restored.connect(self.library_restored)
        self.studio_dialog.refresh_library();self.studio_dialog.refresh_queue();self.studio_dialog.show();self.studio_dialog.raise_()
    def current_workflow_asset(self):
        if not self.loaded:return None
        with self.studio_store.connection() as db:
            row=db.execute('SELECT id FROM assets WHERE source_key=?',(os.path.normcase(str(Path(self.info['path']).resolve())),)).fetchone()
        return row['id'] if row else None
    def open_workflow(self):
        self.commit_history();self.save_session()
        if self.workflow_dialog is None:self.workflow_dialog=BatchDialog(self)
        identifier=self.current_batch_id or self.settings.value('lastBatch')
        if not identifier and self.photo_assets:identifier=self.batch_store.create([a['id'] for a in self.photo_assets])
        self.workflow_dialog.reload_batches(identifier);self.workflow_dialog.show();self.workflow_dialog.raise_()
    def reload_workflow_asset(self,batch_id=None):
        identifier=self.current_workflow_asset()
        batch_id=batch_id or self.current_batch_id
        if identifier is None or not batch_id or self.batch_busy:return
        data=self.batch_store.get(batch_id)
        if not any(i['asset']==identifier and i['snapshot'] for i in data['items']):return
        self.full_resolution=False;self.canvas.fit_mode=True
        self.workflow_reloading=True
        def done(result):self.workflow_reloading=False;self.on_opened(result)
        self.command(lambda c,p:self.studio_store.open_asset(identifier),done,_t('正在恢复批次处理后的当前照片…'))
    def library_relinked(self,paths):
        if not self.loaded or self.info.get('path') not in paths:return
        path=paths[self.info['path']]
        with self.studio_store.connection() as db:
            row=db.execute('SELECT snapshot FROM assets WHERE source_key=?',(os.path.normcase(str(Path(path).resolve())),)).fetchone()
        identity=json.loads(row['snapshot'])['source']
        with model.engine.LOCK:
            model.engine.STATE['path']=path;model.engine.STATE['native_source']=identity
            model.engine.STATE['native_loaded'].source=path
            model.engine.STATE['info']['path']=path;model.engine.STATE['info']['name']=Path(path).name
        self.info['path']=path;self.info['name']=Path(path).name;self.update_navigation();self.save_session()
    def library_restored(self):
        self.current_batch_id=None;self.batch_store=BatchStore(self.studio_store);self.batch_controller.store=self.batch_store
        self.batch_store.recover()
        if self.workflow_dialog:self.workflow_dialog.store=self.batch_store;self.workflow_dialog.reload_batches()
        self.set_photo_batch([])
        self.preview_timer.stop();self.history_timer.stop();self.loaded=False;self.info={}
        self.project_path='';self.saved_project_signature=None;self.project_dirty=False;self.reset_history()
        self._enable_document(False);_ui(self.frames.clear);_ui(self.source_label.setText, _t('照片库已恢复，请从库中打开照片'))
        _ui(self.canvas.clear);self.update_document_title()
        with model.engine.LOCK:
            for key in ('full','preview','strip_img','strip_layout','native_loaded'):model.engine.STATE[key]=None
            model.engine.STATE['frame_params']={};_ui(model.engine.THUMB_CACHE.clear);_ui(model.engine.FLAT_CACHE.clear)
        model.clear_navigation_caches()
        (self.profile/'session.json').unlink(missing_ok=True)
    def import_library_paths(self,paths,show=False):
        try:
            result=self.studio_store.import_paths(paths,self.open_mode.currentData())
            if show:self.open_studio()
            if self.studio_dialog:
                self.studio_dialog.show_imported(result)
            _ui(self.statusBar().showMessage, _tf('已接收 {0} 张照片：新增 {1} 张，已有 / 重复 {2} 个，忽略 {3} 个；在照片库双击打开修片', len(result["ids"]), result["added"], result["existing"], len(result["skipped"])))
            return result
        except Exception:self.show_error(traceback.format_exc())
    def open_library_asset(self,identifier,reveal=False):
        self.save_session();self.full_resolution=False;self.canvas.fit_mode=True
        def done(result):result['_navigation_reveal']=reveal;self.on_opened(result)
        self.command(lambda c,p:self.studio_store.open_asset(identifier),done,_t('正在恢复照片库中的编辑…'))
    def queue_stopped(self):
        if self.closing and not any(job.isRunning() for job in self.jobs):QTimer.singleShot(0,self.close)
    def restore_session(self):
        try:
            path=io_path(self.profile/'session.json');data=json.loads(path.read_text('utf-8'))
            if data.get('format')=='film-invert-project':
                self.open_project_path(path,session=True);return
            if not Path(data['source']).is_file():raise ValueError(_t('上次图像已移动，请重新打开'))
            # Restore frame and params after loading, using an explicit user action.
            source=data['source'];frame=int(data.get('frame',0));value=data['params']
            def work(c,p):
                result=model.open_image(source)
                if frame and result['info'].get('strip'):
                    changed=model.change_frame(frame,value,True,False);result.update(changed)
                result['params']=model.params(value).__dict__;return result
            self.command(work,self.on_opened,_t('正在恢复上次会话…'))
        except Exception:self.show_error(traceback.format_exc())
    def project_options(self):
        return {'keep_params':self.keep_check.isChecked(),'auto_meter':self.meter_check.isChecked()}
    def navigation_options_changed(self):
        if self.loaded:self.save_session()
    def update_document_title(self):
        if not self.loaded:_ui(self.setWindowTitle, APP_NAME+_t(' · Qt 原生桌面版'));return
        title=Path(self.info['path']).name
        if self.project_path:title=Path(self.project_path).name+' · '+title
        _ui(self.setWindowTitle, ('* ' if self.project_dirty else '')+title+' — '+APP_NAME)
    def save_project_dialog(self,save_as=False):
        if not self.loaded:return
        path=self.project_path
        if save_as or not path:
            default=path or str(Path(self.output_edit.text())/(Path(self.info['path']).stem+'.filmproj'))
            path,_=_ui(QFileDialog.getSaveFileName, self,_t('保存整卷工程'),default,_t('FilmInvert 工程 (*.filmproj)'))
        if path:
            if not path.lower().endswith('.filmproj'):path+='.filmproj'
            self.save_project_to(path)
    def save_project_to(self,path):
        value=copy.deepcopy(self.p);options=self.project_options()
        def done(saved):
            self.project_path=saved
            self.saved_project_signature=model.project_signature(model.project_snapshot(value,options))
            self.save_session()
            _ui(self.statusBar().showMessage, _t('整卷工程已保存（各帧参数、模板、帧位置）：')+saved)
        self.command(lambda c,p:model.save_project(path,value,options),done,_t('正在保存整卷工程…'))
    def open_project_dialog(self):
        path,_=_ui(QFileDialog.getOpenFileName, self,_t('打开整卷工程'),self.project_path or self.output_edit.text(),_t('FilmInvert 工程 (*.filmproj)'))
        if path:self.open_project_path(path)
    def open_project_path(self,path,source_override=None,session=False):
        try:
            data=model.read_project(path)
            try:source=model.project_source(data,path,source_override)
            except FileNotFoundError:
                selected,_=_ui(QFileDialog.getOpenFileName, self,_t('工程原图已移动，请选择原始文件'),str(Path(data['source']['path']).parent),IMAGE_FILTER)
                if not selected:return
                source=selected
            def work(c,p):
                result=model.open_project(path,source)
                if session:
                    result['project_path']=data.get('project_file','')
                    result['saved_signature']=data.get('saved_project_signature')
                return result
            self.full_resolution=False;self.canvas.fit_mode=True
            self.command(work,self.on_opened,_t('正在校验原图并恢复整卷工程…'))
        except Exception:self.show_error(traceback.format_exc())
    def remember(self,path):
        recent=self.settings.value('recent',[])
        if isinstance(recent,str):recent=[recent]
        recent=[path]+[p for p in recent if p!=path];self.settings.setValue('recent',recent[:10]);self.update_recent()
    def update_recent(self):
        _ui(self.recent_menu.clear);recent=self.settings.value('recent',[])
        if isinstance(recent,str):recent=[recent]
        for path in recent:self.recent_menu.addAction(self.action(Path(path).name+' — '+str(Path(path).parent),lambda _,p=path:self.open_path(p)))
        self.recent_menu.setEnabled(bool(recent))
    def show_help(self):
        path=APP_DIR/('README.en.html' if i18n.language()=='en' else '软件介绍.html')
        if not path.exists():path=ROOT/'DESKTOP-README.md'
        if path.exists():QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
    def switch_language(self,code):
        if code not in ('zh_CN','en'):return
        i18n.set_language(code)
        self.settings.setValue('language',code);self.settings.sync()
        if hasattr(self,'language_combo'):
            blocked=self.language_combo.blockSignals(True)
            self.language_combo.setCurrentIndex(self.language_combo.findData(code));self.language_combo.blockSignals(blocked)
        for key,action in self.language_actions.items():action.setChecked(key==code)
    def closeEvent(self,event):
        self.import_reveal.cancel()
        self.hdr_canvas.stop()
        merge=getattr(self,'hdr_merge_dialog',None)
        if merge is not None and merge.worker is not None:
            self.closing=True;merge.worker.cancel.set();merge.worker.finished.connect(lambda:QTimer.singleShot(0,self.close));event.ignore();return
        hdr=getattr(self,'hdr_preview_dialog',None)
        if hdr is not None:hdr.close()
        self.stop_prefetch()
        if self.prefetch_worker:
            self.closing=True;_ui(self.statusBar().showMessage, _t('正在结束相邻照片读取…'));event.ignore();return
        if self.batch_controller.running():
            self.closing=True;self.batch_controller.pause();_ui(self.statusBar().showMessage, _t('正在保存当前批次进度，完成当前照片后退出…'));event.ignore();return
        if self.photo_worker is not None:
            self.closing=True;self.photo_worker.requestInterruption();event.ignore();return
        if self.studio_dialog and self.studio_dialog.thumb_worker is not None:
            self.closing=True;self.studio_dialog.thumb_worker.requestInterruption();event.ignore();return
        if self.queue_controller.running():
            self.closing=True;self.queue_controller.pause();self.preview_timer.stop()
            _ui(self.statusBar().showMessage, _t('正在完成当前导出帧，随后暂停队列并退出…'));event.ignore();return
        if any(j.isRunning() for j in self.jobs):
            if not self.closing:
                answer=_ui(QMessageBox.question, self,_t('任务尚未结束'),_t('退出将在当前计算或当前帧完成后进行。是否退出？'))
                if answer!=QMessageBox.StandardButton.Yes:event.ignore();return
            self.closing=True;self.pending_command=None;self.preview_pending=False;self.preview_timer.stop()
            for job in self.jobs:job.cancel.set()
            _ui(self.statusBar().showMessage, _t('正在结束当前计算…'));event.ignore();return
        if self.studio_dialog:self.studio_dialog.close()
        if self.workflow_dialog:self.workflow_dialog.close()
        self.settings.setValue('geometry',self.saveGeometry());self.settings.setValue('dockState',self.saveState())
        self.settings.setValue('output',self.output_edit.text());self.settings.sync();self.save_session();event.accept()

def main():
    from native_startup import run
    return run(sys.argv[1:],APP_DIR,MainWindow)

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:
        error=traceback.format_exc()
        try:(APP_DIR/'startup-error.log').write_text(error,encoding='utf-8')
        except OSError:pass
        raise
