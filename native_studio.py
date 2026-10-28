"""Library and export queue UI; an isolated process owns each export job."""
from i18n import tr as _t, trf as _tf, ui as _ui
from pathlib import Path
import os
import sys
from native_widgets import photo_icon,dropped_paths
from PySide6.QtCore import QObject,Signal,QTimer,QProcess,QUrl,Qt,QProcessEnvironment,QThread,QSize
from PySide6.QtGui import QDesktopServices,QIcon,QPixmap
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QLineEdit,QComboBox,
    QCheckBox,QTableWidget,QTableWidgetItem,QAbstractItemView,QFileDialog,QTabWidget,QWidget,
    QListWidget,QListWidgetItem,QStackedWidget,QListView,QMessageBox)

STATUS={'queued':_t('待处理'),'running':_t('导出中'),'paused':_t('已暂停'),'failed':_t('失败'),'completed':_t('完成')}
FLAG={'none':_t('未标记'),'pick':_t('精选'),'reject':_t('弃选')}

class ThumbnailWorker(QThread):
    ready=Signal(int,str,str,str)
    def __init__(self,cache,rows,generation,parent,store=None):
        super().__init__(parent);self.cache=cache;self.rows=rows;self.generation=generation;self.store=store
    def run(self):
        for row in self.rows:
            if self.isInterruptionRequested():break
            try:path,label=self.cache.original(self.store.get_asset(row['id']) if self.store else row)
            except Exception as error:path='';label=_t('无法生成缩略图：')+str(error)
            self.ready.emit(self.generation,row['id'],path,label)


class QueueController(QObject):
    changed=Signal();message=Signal(object);stopped=Signal()
    def __init__(self,store,root,parent=None):
        super().__init__(parent);self.store=store;self.root=Path(root);self.process=None
        self.identifier=None;self.pid=None;self.automatic=False;self.errors=''
        self.store.recover()
    def running(self):return self.process is not None
    def start(self):
        if self.running():return
        if not self.store.recover():self.message.emit(_t('另一个窗口正在使用导出队列'));return
        self.automatic=True;self.next()
    def next(self):
        identifier=self.store.next_job() if self.automatic else None
        if identifier is None:self.stopped.emit();self.changed.emit();return
        self.identifier=identifier;self.errors='';process=QProcess(self);self.process=process
        executable=Path(sys.executable).with_name('python.exe') if os.name=='nt' else Path(sys.executable)
        process.setProgram(str(executable));process.setArguments(['-I','-X','utf8',str(self.root/'studio_worker.py'),'--profile',str(self.store.folder.parent),'--job',identifier])
        environment=QProcessEnvironment.systemEnvironment();environment.insert('PYTHONIOENCODING','utf-8');process.setProcessEnvironment(environment)
        process.setWorkingDirectory(str(self.root));process.started.connect(lambda:setattr(self,'pid',int(process.processId())))
        process.readyReadStandardError.connect(self.read_error)
        process.readyReadStandardOutput.connect(lambda:process.readAllStandardOutput())
        process.finished.connect(self.finished);process.errorOccurred.connect(self.process_error)
        process.start();self.changed.emit()
    def read_error(self):
        if self.process:self.errors=(self.errors+bytes(self.process.readAllStandardError()).decode('utf-8','replace'))[-8000:]
    def process_error(self,error):
        if error==QProcess.ProcessError.FailedToStart:
            self.store.status(self.identifier,'failed',_t('无法启动独立导出进程：')+self.process.errorString());self.finished(-1,None)
    def pause(self):
        self.automatic=False
        if self.identifier and self.running():self.store.request_pause(self.identifier);self.message.emit(_t('将在当前帧完成后暂停队列'))
        self.changed.emit()
    def finished(self,code,status):
        process=self.process
        if process is None:return
        self.read_error();job=self.store.get_job(self.identifier)
        if job['status']=='running' and job['worker_pid']==self.pid:
            self.store.status(self.identifier,'paused',_t('导出进程中断，可继续任务。')+self.errors[-1500:])
        elif code and job['status']=='queued' and code!=75:
            self.store.status(self.identifier,'failed',self.errors or _t('导出进程未完成初始化'))
        if code==75:self.automatic=False;self.message.emit(_t('另一个窗口正在导出；本窗口已停止调度'))
        self.process=None;self.identifier=None;self.pid=None;process.deleteLater();self.changed.emit()
        if self.automatic:QTimer.singleShot(0,self.next)
        else:self.stopped.emit()


class StudioDialog(QDialog):
    openRequested=Signal(str);importRequested=Signal(object);captureRequested=Signal()
    relinked=Signal(object);restored=Signal()
    def __init__(self,store,controller,image_filter,parent=None):
        super().__init__(parent);self.store=store;self.controller=controller;self.image_filter=image_filter
        _ui(self.setWindowTitle, _t('照片库与导出队列'));self.resize(1150,780);self.setMinimumSize(880,620)
        self.setModal(False);self.refreshing=False;self.page=0;self.generation=0;self.thumb_worker=None
        self.setAcceptDrops(True);self.imported_ids=set()
        outer=QVBoxLayout(self);intro=_ui(QLabel, _t('照片库只登记原图位置与编辑，不复制照片。切换前自动保存整卷编辑；导出队列固定加入时的参数，独立运行。'))
        intro.setWordWrap(True);outer.addWidget(intro);self.tabs=tabs=QTabWidget();outer.addWidget(tabs,1)
        library=QWidget();layout=QVBoxLayout(library);_ui(tabs.addTab, library,_t('照片库'))
        row=QHBoxLayout();self.search=QLineEdit();_ui(self.search.setPlaceholderText, _t('搜索文件名、路径或胶卷标签'));_ui(self.search.setAccessibleName, _t('照片库搜索'));row.addWidget(self.search,1)
        self.minimum=QComboBox();_ui(self.minimum.setAccessibleName, _t('最低星级'))
        for i in range(6):_ui(self.minimum.addItem, _t('全部星级') if i==0 else _tf('{0} 星及以上', i),i)
        row.addWidget(self.minimum);self.hide_rejected=_ui(QCheckBox, _t('隐藏弃选'));row.addWidget(self.hide_rejected)
        add=_ui(QPushButton, _t('添加多张照片…'));add.clicked.connect(self.import_files);row.addWidget(add);layout.addLayout(row)
        row=QHBoxLayout();self.view=QComboBox();_ui(self.view.addItems, [_t('列表'),_t('缩略图墙')]);row.addWidget(self.view)
        previous=_ui(QPushButton, _t('上一页'));previous.clicked.connect(lambda:self.change_page(-1));row.addWidget(previous)
        following=_ui(QPushButton, _t('下一页'));following.clicked.connect(lambda:self.change_page(1));row.addWidget(following)
        self.page_label=_ui(QLabel);row.addWidget(self.page_label);row.addStretch();layout.addLayout(row)
        self.only_imported=_ui(QCheckBox, _t('仅本次导入'));self.only_imported.setEnabled(False);row.addWidget(self.only_imported)
        self.only_imported.toggled.connect(self.refresh_library)
        self.library=QTableWidget(0,6);_ui(self.library.setHorizontalHeaderLabels, [_t('文件'),_t('胶卷标签'),_t('星级'),_t('标记'),_t('编辑'),_t('原图位置')])
        self.configure_table(self.library);self.library.setColumnWidth(0,190);self.library.setColumnWidth(1,120);self.library.setColumnWidth(5,340)
        self.library.itemDoubleClicked.connect(lambda _:self.open_selected());self.library.itemSelectionChanged.connect(self.select_asset)
        self.wall=QListWidget();self.wall.setViewMode(QListView.ViewMode.IconMode);self.wall.setResizeMode(QListView.ResizeMode.Adjust)
        self.wall.setIconSize(QSize(220,150));self.wall.setGridSize(QSize(245,205));self.wall.setWordWrap(True)
        self.wall.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.wall.itemDoubleClicked.connect(lambda _:self.open_selected());self.wall.itemSelectionChanged.connect(self.select_asset)
        self.views=QStackedWidget();self.views.addWidget(self.library);self.views.addWidget(self.wall);layout.addWidget(self.views,1)
        self.view.currentIndexChanged.connect(self.change_view)
        row=QHBoxLayout();self.roll=QLineEdit();_ui(self.roll.setPlaceholderText, _t('胶卷 / 项目标签'));_ui(self.roll.setAccessibleName, _t('胶卷标签'));row.addWidget(self.roll,1)
        self.rating=QComboBox();_ui(self.rating.setAccessibleName, _t('照片星级'))
        for i in range(6):_ui(self.rating.addItem, _tf('{0} 星', i),i)
        row.addWidget(self.rating);self.flag=QComboBox();_ui(self.flag.setAccessibleName, _t('照片标记'))
        for key,text in FLAG.items():_ui(self.flag.addItem, text,key)
        row.addWidget(self.flag);save=_ui(QPushButton, _t('保存标签 / 评级'));save.clicked.connect(self.save_metadata);row.addWidget(save);layout.addLayout(row)
        row=QHBoxLayout();open_button=_ui(QPushButton, _t('打开选中照片'));open_button.clicked.connect(self.open_selected);row.addWidget(open_button)
        capture=_ui(QPushButton, _t('保存当前编辑到库'));capture.clicked.connect(lambda _:self.captureRequested.emit());row.addWidget(capture)
        self.whole_roll=_ui(QCheckBox, _t('包含所选照片的整卷帧'));self.whole_roll.setChecked(True);row.addWidget(self.whole_roll)
        enqueue=_ui(QPushButton, _t('选中照片加入导出队列…'));enqueue.clicked.connect(self.enqueue);row.addWidget(enqueue);layout.addLayout(row)
        row=QHBoxLayout()
        for title,fn in [(_t('重新定位缺失原片…'),self.relink_files),(_t('备份照片库…'),self.backup_library),(_t('恢复照片库…'),self.restore_library)]:
            button=_ui(QPushButton, title);button.clicked.connect(fn);row.addWidget(button)
        row.addStretch();layout.addLayout(row)
        note=_ui(QLabel, _t('每页最多 200 张。缩略图为最近修片预览；未生成时显示标注的原片。备份包含编辑、标签和队列，不包含原片及成品。'))
        note.setWordWrap(True);layout.addWidget(note)
        queue=QWidget();layout=QVBoxLayout(queue);_ui(tabs.addTab, queue,_t('导出队列'))
        self.queue=QTableWidget(0,6);_ui(self.queue.setHorizontalHeaderLabels, [_t('照片 / 任务'),_t('状态'),_t('完成帧'),_t('加入时间'),_t('输出目录'),_t('错误信息')])
        self.configure_table(self.queue);self.queue.setColumnWidth(0,190);self.queue.setColumnWidth(4,300);self.queue.setColumnWidth(5,310);layout.addWidget(self.queue,1)
        row=QHBoxLayout()
        for title,fn in [(_t('开始待处理任务'),controller.start),(_t('暂停队列'),controller.pause),(_t('继续 / 重试选中'),self.retry),(_t('打开输出目录'),self.open_output)]:
            button=_ui(QPushButton, title);button.clicked.connect(fn);row.addWidget(button)
        row.addStretch();layout.addLayout(row)
        note=_ui(QLabel, _t('暂停和退出会等待当前帧完成。重启后手动继续；只跳过哈希核对成功的成品。若原图、成品或暂存文件被改动，请检查错误信息并新建任务。'))
        note.setWordWrap(True);layout.addWidget(note)
        self.message=_ui(QLabel, _t('先打开照片完成修片，再加入队列。照片库和队列保存在当前用户数据目录的 studio 中（帮助 → 打开用户数据目录）。'));self.message.setWordWrap(True);outer.addWidget(self.message)
        self.search.textChanged.connect(self.refresh_library);self.minimum.currentIndexChanged.connect(self.refresh_library);self.hide_rejected.toggled.connect(self.refresh_library)
        controller.changed.connect(self.refresh_queue);controller.message.connect(lambda text:_ui(self.message.setText,text))
        self.timer=QTimer(self);self.timer.setInterval(1000);self.timer.timeout.connect(self.refresh_queue);self.timer.start()
        self.refresh_library();self.refresh_queue()
    @staticmethod
    def configure_table(table):
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);table.horizontalHeader().setStretchLastSection(True)
    @staticmethod
    def selected(table):
        if isinstance(table,QListWidget):return [item.data(Qt.ItemDataRole.UserRole) for item in table.selectedItems()]
        return [table.item(row.row(),0).data(Qt.ItemDataRole.UserRole) for row in table.selectionModel().selectedRows()]
    def selected_assets(self):return self.selected(self.wall if self.view.currentIndex() else self.library)
    def change_view(self,index):
        ids=set(self.selected(self.wall if self.views.currentIndex() else self.library))
        self.refreshing=True
        self.views.setCurrentIndex(index)
        for i in range(self.wall.count()):self.wall.item(i).setSelected(self.wall.item(i).data(Qt.ItemDataRole.UserRole) in ids)
        self.library.clearSelection()
        for i in range(self.library.rowCount()):
            if self.library.item(i,0).data(Qt.ItemDataRole.UserRole) in ids:
                for c in range(self.library.columnCount()):self.library.item(i,c).setSelected(True)
        self.refreshing=False;self.select_asset()
        if index:self.load_thumbnails()
    def change_page(self,step):
        self.page=max(0,self.page+step);self.refresh_library()
    def refresh_library(self,*_):
        selected=set(self.selected_assets());self.refreshing=True
        assets=self.store.assets(self.search.text(),self.minimum.currentData(),self.hide_rejected.isChecked(),include_snapshot=False)
        if self.only_imported.isChecked():assets=[row for row in assets if row['id'] in self.imported_ids]
        pages=max(1,(len(assets)+199)//200);self.page=min(self.page,pages-1)
        _ui(self.page_label.setText, _tf('{0} / {1} 页 · 共 {2} 张', self.page+1, pages, len(assets)))
        assets=assets[self.page*200:(self.page+1)*200];self.page_assets=assets
        self.generation+=1;_ui(self.wall.clear)
        self.library.setRowCount(len(assets))
        for i,row in enumerate(assets):
            values=[row['name'],row['roll'],str(row['rating']),FLAG[row['flag']],_t('已保存') if row['has_edits'] else _t('未修片'),row['path']]
            for c,value in enumerate(values):self.library.setItem(i,c,_ui(QTableWidgetItem, value))
            self.library.item(i,0).setData(Qt.ItemDataRole.UserRole,row['id'])
            if row['id'] in selected:
                for c in range(self.library.columnCount()):self.library.item(i,c).setSelected(True)
            item=_ui(QListWidgetItem, row['name']+'\n'+'★'*row['rating']+' '+FLAG[row['flag']]);item.setData(Qt.ItemDataRole.UserRole,row['id'])
            _ui(item.setToolTip, row['path']);_ui(self.wall.addItem, item);item.setSelected(row['id'] in selected)
        self.refreshing=False
        if self.view.currentIndex():self.load_thumbnails()
    def load_thumbnails(self):
        if self.thumb_worker is not None:
            self.thumb_worker.requestInterruption();return
        rows=[self.store.get_asset(row['id']) for row in self.page_assets]
        worker=ThumbnailWorker(self.store.thumbnails,rows,self.generation,self);self.thumb_worker=worker
        worker.ready.connect(self.thumbnail_ready);worker.finished.connect(self.thumbnails_finished);worker.start()
    def thumbnail_ready(self,generation,identifier,path,label):
        if generation!=self.generation:return
        for i in range(self.wall.count()):
            item=self.wall.item(i)
            if item.data(Qt.ItemDataRole.UserRole)==identifier:
                if path:
                    from native_widgets import monitor_image
                    item.setIcon(photo_icon(QPixmap.fromImage(monitor_image(QPixmap(path).toImage(),self.parent().monitor_profile))))
                _ui(item.setToolTip, item.toolTip()+'\n'+label)
    def thumbnails_finished(self):
        worker=self.thumb_worker;self.thumb_worker=None;generation=worker.generation;worker.deleteLater()
        if self.parent().closing:QTimer.singleShot(0,self.parent().close)
        elif generation!=self.generation and self.view.currentIndex() and self.isVisible():self.load_thumbnails()
    def select_asset(self):
        if self.refreshing:return
        ids=self.selected_assets()
        if len(ids)!=1:return
        row=self.store.get_asset(ids[0]);_ui(self.roll.setText, row['roll']);self.rating.setCurrentIndex(row['rating']);self.flag.setCurrentIndex(self.flag.findData(row['flag']))
    def save_metadata(self):
        ids=self.selected_assets()
        for identifier in ids:self.store.metadata(identifier,self.rating.currentData(),self.flag.currentData(),self.roll.text())
        self.refresh_library();_ui(self.message.setText, _tf('已更新 {0} 张照片的标签和评级', len(ids)))
    def import_files(self):
        paths,_=_ui(QFileDialog.getOpenFileNames, self,_t('添加照片到库'),'',self.image_filter)
        if paths:self.importRequested.emit(paths)
    def dragEnterEvent(self,event):
        if dropped_paths(event.mimeData()):event.acceptProposedAction()
        else:event.ignore()
    def dragMoveEvent(self,event):self.dragEnterEvent(event)
    def dropEvent(self,event):
        paths=dropped_paths(event.mimeData())
        if paths:self.importRequested.emit(paths);event.acceptProposedAction()
        else:event.ignore()
    def show_imported(self,result):
        self.imported_ids=set(result['ids']);self.page=0;self.tabs.setCurrentIndex(0)
        # Old search/rating/page filters must not hide the files just dropped.
        widgets=(self.search,self.minimum,self.hide_rejected,self.only_imported)
        for widget in widgets:widget.blockSignals(True)
        _ui(self.search.clear);self.minimum.setCurrentIndex(0);self.hide_rejected.setChecked(False)
        self.only_imported.setEnabled(True);self.only_imported.setChecked(True)
        for widget in widgets:widget.blockSignals(False)
        self.refresh_library();self.refreshing=True
        self.library.selectAll()
        for i in range(self.wall.count()):self.wall.item(i).setSelected(True)
        self.refreshing=False;self.select_asset()
        _ui(self.message.setText, _tf('本次接收 {0} 张照片：新增 {1} 张，已有 / 重复 {2} 个，忽略 {3} 个。双击照片开始修片；取消“仅本次导入”可查看整个照片库。', len(result["ids"]), result["added"], result["existing"], len(result["skipped"])))
        _ui(self.message.setToolTip, _t('忽略的无效或不支持文件：\n')+'\n'.join(result['skipped']) if result['skipped'] else '')
    def open_selected(self):
        ids=self.selected_assets()
        if len(ids)==1:self.openRequested.emit(ids[0]);self.hide()
        else:_ui(self.message.setText, _t('请选择一张照片打开修片'))
    def enqueue(self):
        ids=self.selected_assets()
        if not ids:_ui(self.message.setText, _t('请先选择照片'));return
        folder=_ui(QFileDialog.getExistingDirectory, self,_t('选择队列输出目录'))
        if not folder:return
        try:
            jobs=self.store.enqueue(ids,folder,self.whole_roll.isChecked(),self.parent().export_options());self.refresh_queue();_ui(self.message.setText, _tf('已固定 {0} 个任务的参数快照；在导出队列页开始处理', len(jobs)))
        except Exception as error:_ui(self.message.setText, str(error))
    def maintenance(self,work,done,label):
        parent=self.parent()
        if parent.job is not None or self.controller.running() or parent.batch_controller.running():_ui(self.message.setText, _t('请等待当前处理或暂停导出 / 批次步骤后再操作'));return
        self.captureRequested.emit();self.setEnabled(False)
        def success(result):self.setEnabled(True);done(result)
        def finished():self.setEnabled(True)
        parent.command(lambda c,p:work(),success,label)
        # command reports failures in the main window; re-enable the dialog afterwards.
        if parent.job:parent.job.finished.connect(finished)
    def relink_files(self):
        ids=self.selected_assets() or [row['id'] for row in self.store.assets(include_snapshot=False)]
        folder=_ui(QFileDialog.getExistingDirectory, self,_t('选择原片的新根目录（包含子目录）'))
        if not folder:return
        def done(result):
            self.relinked.emit(result['changed']);self.refresh_library()
            _ui(self.message.setText, _tf('已关联 {0} 张；队列保留原快照，请重新加入任务。\n', len(result["changed"]))+'\n'.join(result['errors'][:10]))
        self.maintenance(lambda:self.store.relink(ids,folder),done,_t('正在核对原片内容并重新关联…'))
    def backup_library(self):
        from studio_archive import backup
        path,_=_ui(QFileDialog.getSaveFileName, self,_t('备份照片库'),'照片库.filmbackup',_t('照片库备份 (*.filmbackup)'))
        if not path:return
        if not path.lower().endswith('.filmbackup'):path+='.filmbackup'
        self.maintenance(lambda:backup(self.store,path),lambda result:_ui(self.message.setText, _t('备份已保存：')+result),_t('正在备份照片库…'))
    def restore_library(self):
        from studio_archive import restore
        path,_=_ui(QFileDialog.getOpenFileName, self,_t('恢复照片库'),'',_t('照片库备份 (*.filmbackup)'))
        if not path:return
        if _ui(QMessageBox.question, self,_t('恢复照片库'),_t('将恢复备份中的照片库并关闭当前编辑。当前照片库会先自动备份，原片和成品不会改动。继续？'))!=QMessageBox.StandardButton.Yes:return
        def done(recovery):
            _ui(self.imported_ids.clear);self.only_imported.setChecked(False);self.only_imported.setEnabled(False)
            self.restored.emit();self.refresh_library();self.refresh_queue()
            _ui(self.message.setText, _t('照片库已恢复；未完成任务已暂停。恢复前的自动备份：')+recovery)
        self.maintenance(lambda:restore(self.store,path),done,_t('正在校验并恢复照片库…'))
    def refresh_queue(self):
        import time
        selected=set(self.selected(self.queue));self.queue.setRowCount(0)
        jobs=self.store.job_summaries();self.queue.setRowCount(len(jobs))
        for i,row in enumerate(jobs):
            values=[row['name']+' · '+row['id'][:8],STATUS[row['status']],_tf('{0} / {1}', row["done_count"], row["total_count"]),time.strftime('%m-%d %H:%M',time.localtime(row['created'])),row['destination'],row['error']]
            for c,value in enumerate(values):self.queue.setItem(i,c,_ui(QTableWidgetItem, value))
            self.queue.item(i,0).setData(Qt.ItemDataRole.UserRole,row['id'])
            if row['id'] in selected:
                for c in range(self.queue.columnCount()):self.queue.item(i,c).setSelected(True)
    def retry(self):
        self.store.retry(self.selected(self.queue));self.refresh_queue();self.controller.start()
    def open_output(self):
        ids=self.selected(self.queue)
        if len(ids)!=1:return
        path=Path(self.store.get_job(ids[0])['destination'])
        if path.is_dir():QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:_ui(self.message.setText, _t('任务尚未创建输出目录'))
