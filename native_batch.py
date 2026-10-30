"""Persistent batch workflow and an isolated, pausable processing controller."""
from i18n import tr as _t, trf as _tf, ui as _ui
from pathlib import Path
import json,os,sys
from PySide6.QtCore import QObject,Signal,QTimer,QProcess,Qt,QUrl,QSize
from PySide6.QtGui import QPixmap,QDesktopServices
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QGridLayout,QFormLayout,QLabel,QPushButton,
 QLineEdit,QComboBox,QCheckBox,QDoubleSpinBox,QTableWidget,QTableWidgetItem,QAbstractItemView,
 QFileDialog,QTabWidget,QWidget,QInputDialog,QMessageBox,QScrollArea)
from batch_store import BatchStore,FIELDS
from native_widgets import monitor_image,photo_icon
from native_efficiency import PhotoFilters,EditingKeys,catalog,phase,job_states

STAGE={'imported':_t('待检查'),'checked':_t('已检查'),'prepared':_t('已准备'),'styled':_t('待人工复核'),'reviewed':_t('已复核'),'queued':_t('已入队'),'failed':_t('失败')}
INPUT={'single':_t('单张'),'strip':_t('整条'),'auto':_t('自动')}
STATUS={'idle':_t('等待下一阶段'),'paused':_t('已暂停'),'running':_t('处理中'),'queued':_t('待导出'),'failed':_t('失败'),'completed':_t('已完成')}
FLAGS={'none':_t('未标记'),'pick':_t('精选'),'reject':_t('弃选')}
STEP={'check':_t('检查原片'),'prepare':_t('独立准备'),'style':_t('应用组风格'),'preview':_t('刷新校样'),'report':_t('生成汇总')}
FIELD_NAMES={'style':_t('胶片 / 反相风格'),'wb':_t('白平衡'),'exposure':_t('曝光'),'tone':_t('影调 / HSL / HDR'),'output':_t('输出空间'),'calibration':_t('色卡配置')}

class BatchController(QObject):
    changed=Signal();message=Signal(object);completed=Signal(str,str)
    def __init__(self,parent):
        super().__init__(parent);self.window=parent;self.store=parent.batch_store;self.process=None;self.waiting=False
        self.batch=None;self.operation=None;self.errors='';self.store.recover()
        parent.queue_controller.stopped.connect(self.scan_reports)
    def running(self):return self.process is not None or self.waiting
    def start(self,identifier,operation,ids=None,overwrite=False):
        if self.running():raise ValueError(_t('请先暂停或等待当前批次步骤'))
        self.window.stop_prefetch()
        if self.window.job is None or self.window.task_kind=='preview':self.window.commit_history();self.window.save_session()
        self.batch=identifier;self.operation=operation;self.waiting=True;self.window.batch_busy=True
        self.window.foreground(True);self.message.emit(_t('等待当前预览结束后开始…'));self.changed.emit()
        def launch():
            if not self.waiting:return
            w=self.window
            if w.job is not None or w.pending_command or any(j.isRunning() for j in w.jobs):QTimer.singleShot(100,launch);return
            w.batch_busy=False;w.commit_history();w.save_session();w.batch_busy=True;w.preview_timer.stop();w.foreground(True)
            try:token=self.store.start(identifier,operation,ids,overwrite)
            except Exception as error:self.waiting=False;self.errors=str(error);self.finish(-1,None);return
            self.waiting=False;process=QProcess(self);self.process=process;self.errors=''
            program=Path(sys.executable).with_name('python.exe') if os.name=='nt' else Path(sys.executable)
            process.setProgram(str(program));process.setArguments(['-I','-X','utf8',str(Path(__file__).resolve().parent/'batch_worker.py'),
                '--profile',str(w.profile),'--batch',identifier,'--run',token])
            process.readyReadStandardOutput.connect(self.read_output);process.readyReadStandardError.connect(self.read_error)
            process.finished.connect(self.finish)
            process.errorOccurred.connect(lambda e:self.finish(-1,None) if e==QProcess.ProcessError.FailedToStart else None)
            process.start();self.changed.emit()
        self.window.preview_timer.stop();QTimer.singleShot(0,launch)
    def read_error(self):
        if self.process:self.errors=(self.errors+bytes(self.process.readAllStandardError()).decode('utf-8','replace'))[-8000:]
    def read_output(self):
        if not self.process:return
        for line in bytes(self.process.readAllStandardOutput()).decode('utf-8','replace').splitlines():
            try:r=json.loads(line);self.message.emit(_tf('{0} · {1}/{2} · {3}', STEP.get(r["step"],r["step"]), r["done"], r["total"], r["name"]))
            except (ValueError,KeyError):pass
        self.changed.emit()
    def pause(self):
        if self.waiting:
            self.waiting=False;self.finish(0,None);return
        if self.process:self.store.run_update(self.batch,cancel=True);self.message.emit(_t('将在当前照片完成后暂停，已完成进度保留'))
    def finish(self,code,status):
        process=self.process;self.read_error();self.process=None;self.waiting=False
        if process:process.deleteLater()
        identifier=self.batch;operation=self.operation;self.batch=None
        if identifier:
            data=self.store.get(identifier)
            if data['status']=='running':self.store.finish(identifier,'paused')
            if code and operation=='report':
                from batch_engine import export_fingerprint
                self.store.edit(identifier,lambda d:d.update(report_error={'fingerprint':export_fingerprint(self.store.studio,d),'message':self.errors[-1800:]}))
        w=self.window;w.batch_busy=False;w.foreground(False)
        if code:self.message.emit(_t('步骤未完成，可继续；')+self.errors[-1800:])
        else:self.message.emit(_t('已暂停，进度保留；可继续步骤') if identifier and self.store.get(identifier)['status']=='paused' else _t('步骤完成；可查看逐图状态，继续下一阶段'))
        self.changed.emit()
        if identifier:self.completed.emit(identifier,operation or '')
        if w.closing:
            if process and operation!='report':w.loaded=False
            QTimer.singleShot(0,w.close)
        elif operation!='report' and identifier:w.reload_workflow_asset(identifier)
        if not w.closing:QTimer.singleShot(400,self.scan_reports)
    def scan_reports(self):
        if self.running() or self.window.closing or self.window.queue_controller.running():return
        from batch_engine import export_fingerprint
        for data in self.store.list():
            jobs=[self.store.studio.get_job(i['job']) for i in data['items'] if i.get('job')]
            if jobs and all(j['status'] in ('completed','failed') for j in jobs):
                fingerprint=export_fingerprint(self.store.studio,data)
                if (data.get('report') or {}).get('fingerprint')!=fingerprint and (data.get('report_error') or {}).get('fingerprint')!=fingerprint:
                    try:self.start(data['id'],'report')
                    except ValueError:pass
                    return

class ProofDialog(QDialog):
    def __init__(self,items,parent,count):
        super().__init__(parent);self.items=items[:count];self.window=parent.parent();_ui(self.setWindowTitle, _t('批量校样 · ')+str(count)+_t(' 图'))
        self.resize(1080,780);outer=QVBoxLayout(self);self.before=_ui(QCheckBox, _t('查看同步前'));self.before.toggled.connect(self.refresh);outer.addWidget(self.before)
        self.image_limit=(280,145) if count==9 else (420,250)
        self.note=_ui(QLabel, _t('校样为 sRGB 小预览；评级和独立曝光可回清单修改。缺失预览请先刷新校样。'));self.note.setWordWrap(True);outer.addWidget(self.note)
        scroll=QScrollArea();scroll.setWidgetResizable(True);outer.addWidget(scroll,1);body=QWidget();self.grid=QGridLayout(body);scroll.setWidget(body)
        self.cells=[];columns=2 if count==4 else 3
        for n,item in enumerate(self.items):
            box=QWidget();layout=QVBoxLayout(box);image=_ui(QLabel);image.setMinimumSize(220,135) if count==9 else image.setMinimumSize(260,180);image.setAlignment(Qt.AlignmentFlag.AlignCenter);layout.addWidget(image)
            label=_ui(QLabel, item['name']+'\n'+item['group']);label.setWordWrap(True);layout.addWidget(label)
            self.grid.addWidget(box,n//columns,n%columns);self.cells.append(image)
        self.refresh()
    def refresh(self):
        for item,label in zip(self.items,self.cells):
            path=item['preview_before' if self.before.isChecked() else 'preview_after']
            pix=QPixmap(path)
            if pix.isNull():_ui(label.setText, _t('暂无预览'));continue
            image=monitor_image(pix.toImage(),self.window.monitor_profile)
            label.setPixmap(QPixmap.fromImage(image).scaled(*self.image_limit,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))

class BatchDialog(QDialog):
    def __init__(self,parent):
        super().__init__(parent);self.store=parent.batch_store;self.controller=parent.batch_controller;self.identifier=None;self.refreshing=False
        _ui(self.setWindowTitle, _t('批量工作流 · 准备 / 分组 / 校样 / 导出'));self.resize(1260,880);self.setMinimumSize(1020,720)
        outer=QVBoxLayout(self);top=QHBoxLayout();self.batches=QComboBox();self.batches.setMinimumWidth(250);top.addWidget(self.batches,1)
        self.add_button(top,_t('本次照片建批次'),self.create_current);self.add_button(top,_t('多选文件建批次…'),self.create_files)
        self.add_button(top,_t('重命名'),self.rename);self.add_button(top,_t('载入底部导航'),self.load_navigation);outer.addLayout(top)
        self.batches.currentIndexChanged.connect(self.choose_batch)
        line=QHBoxLayout();self.templates=QComboBox();line.addWidget(self.templates,1)
        self.add_button(line,_t('保存为模板…'),self.save_template);self.add_button(line,_t('应用模板设置'),self.apply_template)
        self.add_button(line,_t('撤销最近一次覆盖 / 同步'),self.undo);outer.addLayout(line)
        self.summary=_ui(QLabel);self.summary.setWordWrap(True);outer.addWidget(self.summary)
        self.filters=PhotoFilters(self);self.filters.changed.connect(self.refresh);outer.addWidget(self.filters)
        line=QHBoxLayout();self.filter_count=_ui(QLabel);line.addWidget(self.filter_count,1)
        self.add_button(line,_t('仅勾选筛选结果'),self.check_visible);self.add_button(line,_t('快捷键 / 操作帮助'),parent.show_efficiency_help);outer.addLayout(line)
        self.tabs=QTabWidget();outer.addWidget(self.tabs,1)
        page=QWidget();layout=QVBoxLayout(page);_ui(self.tabs.addTab, page,_t('1 · 清单与准备'))
        self.table=QTableWidget(0,10);_ui(self.table.setHorizontalHeaderLabels, [_t('导出'),_t('照片'),_t('分组'),_t('输入'),_t('帧数'),_t('状态'),_t('独立 EV'),_t('星级 / 标记'),_t('原图位置'),_t('错误')])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setIconSize(QSize(64,44));self.table.verticalHeader().setDefaultSectionSize(50)
        self.table.setColumnWidth(0,50);self.table.setColumnWidth(1,215);self.table.setColumnWidth(2,110);self.table.setColumnWidth(3,65);self.table.setColumnWidth(4,60);self.table.setColumnWidth(8,260);layout.addWidget(self.table,1)
        self.table.itemChanged.connect(self.item_changed);self.table.itemDoubleClicked.connect(lambda _:self.open_item())
        line=QHBoxLayout();self.item_group=QLineEdit();_ui(self.item_group.setPlaceholderText, _t('选中照片的胶卷 / 设备 / 光源分组'));line.addWidget(self.item_group,1)
        self.item_mode=QComboBox()
        for title,value in [(_t('继承批次输入'),None),(_t('每文件单张'),'single'),(_t('整条胶片'),'strip'),(_t('自动识别'),'auto')]:_ui(self.item_mode.addItem, title,value)
        line.addWidget(self.item_mode);self.ev=QDoubleSpinBox();self.ev.setRange(-4,4);self.ev.setSingleStep(.1);_ui(self.ev.setPrefix, _t('独立 EV '));line.addWidget(self.ev)
        self.add_button(line,_t('设置选中项'),self.set_items);layout.addLayout(line)
        line=QHBoxLayout()
        for text,fn in [(_t('打开选中微调'),self.open_item),(_t('跳过'),lambda:self.flag_items(True)),(_t('恢复参与'),lambda:self.flag_items(False)),(_t('移出批次'),self.remove_items),(_t('评级 / 精选…'),self.metadata)]:self.add_button(line,text,fn)
        self.add_button(line,_t('重新准备选中…'),self.reprepare);self.add_button(line,_t('仅重试失败项'),self.retry_failed);layout.addLayout(line)
        page=QWidget();layout=QVBoxLayout(page);_ui(self.tabs.addTab, page,_t('2 · 规则与分组同步'))
        form=QFormLayout();self.input_mode=QComboBox();_ui(self.input_mode.addItem, _t('每文件单张'),'single');_ui(self.input_mode.addItem, _t('整条胶片'),'strip');_ui(self.input_mode.addItem, _t('自动识别'),'auto')
        self.color=QComboBox();from input_color import CHOICES
        for key,title in CHOICES.items():_ui(self.color.addItem, _t(title),key)
        self.space=QComboBox();_ui(self.space.addItem, _t('线性 sRGB'),'srgb');_ui(self.space.addItem, _t('线性 ProPhoto'),'prophoto')
        self.method=QComboBox()
        for title,key in [(_t('各自自动反相；保留已有编辑'),'auto'),(_t('按组参考同步所选参数'),'sync'),(_t('只使用已有编辑'),'existing')]:_ui(self.method.addItem, title,key)
        self.stop=QComboBox()
        for title,key in [(_t('检查后暂停'),'check'),(_t('独立准备后暂停'),'prepare'),(_t('组风格后暂停，等待人工复核'),'style')]:_ui(self.stop.addItem, title,key)
        for title,widget in [(_t('新图默认输入'),self.input_mode),(_t('输入色彩'),self.color),(_t('新图工作空间'),self.space),(_t('处理方式'),self.method),(_t('流水线停靠点'),self.stop)]:_ui(form.addRow, title,widget)
        layout.addLayout(form);line=QHBoxLayout();self.field_checks={}
        for key,title in FIELD_NAMES.items():check=_ui(QCheckBox, title);self.field_checks[key]=check;line.addWidget(check)
        layout.addLayout(line);line=QHBoxLayout();self.sync_mode=QComboBox();_ui(self.sync_mode.addItem, _t('绝对参数替换'),'absolute');_ui(self.sync_mode.addItem, _t('相对曝光增减'),'relative');line.addWidget(self.sync_mode)
        self.delta=QDoubleSpinBox();self.delta.setRange(-4,4);self.delta.setSingleStep(.1);_ui(self.delta.setPrefix, _t('组曝光增减 EV '));line.addWidget(self.delta)
        self.add_button(line,_t('保存处理设置'),self.save_settings);layout.addLayout(line)
        line=QHBoxLayout();self.rule=QComboBox()
        for title,key in [(_t('手工分组'),'manual'),(_t('按所在文件夹'),'folder'),(_t('按照片库胶卷标签'),'tag'),(_t('按工作空间'),'space'),(_t('按文件名前缀（_ / -）'),'prefix')]:_ui(self.rule.addItem, title,key)
        line.addWidget(self.rule);self.add_button(line,_t('按规则重新分组'),self.rule_groups)
        warning=_ui(QLabel, _t('规则只组织文件，不自动判断胶片型号。不同工作空间请分组使用匹配参考图。'));warning.setWordWrap(True);line.addWidget(warning,1);layout.addLayout(line)
        self.group_table=QTableWidget(0,4);_ui(self.group_table.setHorizontalHeaderLabels, [_t('分组'),_t('参考照片 / 模板风格'),_t('共享实测片基'),_t('组内文件数')]);self.group_table.horizontalHeader().setStretchLastSection(True)
        self.group_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.group_table.setColumnWidth(0,180);self.group_table.setColumnWidth(1,390);self.group_table.setColumnWidth(2,170);layout.addWidget(self.group_table,1)
        line=QHBoxLayout();self.add_button(line,_t('保存组参考 / 片基设置…'),self.save_groups);self.add_button(line,_t('当前照片设为本组参考'),self.reference_current);layout.addLayout(line)
        page=QWidget();layout=QVBoxLayout(page);_ui(self.tabs.addTab, page,_t('3 · 校样与导出'))
        note=_ui(QLabel, _t('选中清单中的照片进行 4 / 9 图并排校样；可切换同步前后。独立 EV 不会应用到其他照片。复核通过后才能按默认规则加入队列。'));note.setWordWrap(True);layout.addWidget(note)
        line=QHBoxLayout()
        for text,fn in [(_t('4 图校样'),lambda:self.proof(4)),(_t('9 图校样'),lambda:self.proof(9)),(_t('刷新选中校样'),lambda:self.run('preview')),(_t('标记选中已复核'),self.review)]:self.add_button(line,text,fn)
        layout.addLayout(line);form=QFormLayout();self.output=QLineEdit();line=QHBoxLayout();line.addWidget(self.output,1);self.add_button(line,_t('选择…'),self.output_folder);_ui(form.addRow, _t('批次输出目录'),line)
        self.naming=QLineEdit();_ui(form.addRow, _t('命名模板'),self.naming);layout.addLayout(form)
        line=QHBoxLayout();self.whole=_ui(QCheckBox, _t('包含每个文件的全部帧'));self.review_required=_ui(QCheckBox, _t('加入队列前要求人工复核'));self.group_dirs=_ui(QCheckBox, _t('按组输出子目录'))
        for widget in (self.whole,self.review_required,self.group_dirs):line.addWidget(widget)
        layout.addLayout(line);line=QHBoxLayout()
        for text,fn in [(_t('保存导出设置'),self.save_settings),(_t('固定参数并加入导出队列'),self.enqueue),(_t('开始 / 继续本批次导出'),self.start_exports),(_t('暂停导出队列'),parent.queue_controller.pause)]:self.add_button(line,text,fn)
        layout.addLayout(line);line=QHBoxLayout()
        for text,fn in [(_t('新建下一轮导出…'),self.new_round),(_t('生成联系表 / 汇总'),lambda:self.run('report')),(_t('打开最新汇总'),self.open_report)]:self.add_button(line,text,fn)
        layout.addLayout(line);self.history_label=_ui(QLabel);self.history_label.setWordWrap(True);layout.addWidget(self.history_label);layout.addStretch()
        line=QHBoxLayout();self.run_buttons=[]
        for text,operation in [(_t('① 检查原片'),'check'),(_t('② 独立准备'),'prepare'),(_t('③ 应用组风格'),'style'),(_t('运行流水线到停靠点'),'pipeline')]:
            self.run_buttons.append(self.add_button(line,text,lambda _,op=operation:self.run(op)))
        self.pause_button=self.add_button(line,_t('暂停准备 / 同步'),self.controller.pause);outer.addLayout(line)
        self.message=_ui(QLabel, _t('默认各图独立估片基 / 测光；裁切、校直、透视和局部修复不会跨图复制。重开批次先检查原片身份。'));self.message.setWordWrap(True);outer.addWidget(self.message)
        self.controller.message.connect(lambda text:_ui(self.message.setText,text));self.controller.changed.connect(self.refresh)
        self.timer=QTimer(self);self.timer.setInterval(1500);self.timer.timeout.connect(self.refresh);self.timer.start()
        self.editing_keys=EditingKeys(self,self.editing_shortcut);self._table_signature=None
        self.reload_batches()
    @staticmethod
    def add_button(layout,text,fn):
        button=_ui(QPushButton, text);button.clicked.connect(fn);layout.addWidget(button);return button
    def attempt(self,fn):
        try:return fn()
        except Exception as error:_ui(self.message.setText, str(error));return None
    def reload_batches(self,identifier=None):
        identifier=identifier or self.identifier;self.batches.blockSignals(True);_ui(self.batches.clear)
        for data in self.store.list():_ui(self.batches.addItem, data['name'],data['id'])
        index=self.batches.findData(identifier);self.batches.setCurrentIndex(max(0,index));self.batches.blockSignals(False)
        _ui(self.templates.clear)
        for template in self.store.templates():_ui(self.templates.addItem, template['name'],template['id'])
        self.choose_batch()
    def choose_batch(self,*_):
        if self.controller.running():
            self.batches.blockSignals(True);self.batches.setCurrentIndex(self.batches.findData(self.identifier));self.batches.blockSignals(False)
            return
        previous=self.identifier;self.identifier=self.batches.currentData()
        if previous!=self.identifier:
            self.filters.blockSignals(True);self.filters.reset();self.filters.blockSignals(False);self._table_signature=None
        self.tabs.setEnabled(bool(self.identifier))
        for button in self.run_buttons:button.setEnabled(bool(self.identifier))
        if self.identifier:
            self.parent().current_batch_id=self.identifier;self.parent().settings.setValue('lastBatch',self.identifier)
            self.load_settings();self.refresh_groups();self.refresh()
    def selected(self):
        return [self.table.item(i.row(),1).data(Qt.ItemDataRole.UserRole) for i in self.table.selectionModel().selectedRows()]
    def active_items(self):
        data=self.store.get(self.identifier);ids=self.selected()
        scope=set(ids or self.visible_ids())
        return [i for i in data['items'] if not i['skip'] and i['asset'] in scope]
    def visible_ids(self):
        return [self.table.item(row,1).data(Qt.ItemDataRole.UserRole) for row in range(self.table.rowCount())]
    def check_visible(self):
        if not self.identifier or self.controller.running():return
        visible=set(self.visible_ids())
        def apply(data):
            for item in data['items']:item['selected']=item['asset'] in visible
        self.attempt(lambda:self.store.edit(self.identifier,apply));self.refresh()
        _ui(self.message.setText, _tf('已仅勾选 {0} 个筛选结果；按整批勾选项导出', len(visible)))
    def editing_shortcut(self,command,value):
        if command=='search':self.filters.search.setFocus();return True
        if not self.identifier or self.controller.running():return False
        if command=='navigate':
            if not self.table.rowCount():return True
            row=self.table.currentRow();self.table.selectRow(max(0,min(self.table.rowCount()-1,row+value)));return True
        ids=self.selected()
        if not ids:_ui(self.message.setText, _t('请先选中要评级、标记或复核的照片'));return True
        if command=='review':self.review();return True
        if command in ('rating','flag'):
            for identifier in ids:
                asset=self.store.studio.get_asset(identifier)
                self.store.studio.metadata(identifier,value if command=='rating' else asset['rating'],value if command=='flag' else asset['flag'],asset['roll'])
            self.refresh();return True
        return False
    def refresh(self):
        if not self.identifier:return
        data=self.store.get(self.identifier);selected=set(self.selected());self.refreshing=True
        assets={a['id']:a for a in catalog(self.store.studio,[i['asset'] for i in data['items']])};jobs=job_states(self.store.studio)
        self.filters.groups(i['group'] for i in data['items']);visible=[];frames=0;ready=0
        for item in data['items']:
            asset=assets[item['asset']];snapshot=item['snapshot'];count=snapshot['frame_count'] if snapshot else None
            if item['selected'] and not item['skip']:frames+=count or 0
            ready+=bool(snapshot and item['valid'] and not item['error'])
            if self.filters.accepts(asset,item['group'],phase(item,self.store.session,asset,jobs.get(item.get('job')))):visible.append(item)
        _ui(self.filter_count.setText, _tf('显示 {0} / {1} · 筛选不改变导出勾选；校样 / 复核仅作用于显示项或选中行', len(visible), len(data["items"])))
        fields=('asset','name','group','input_mode','stage','valid','verified_session','selected','skip','ev','error','preview_after','job')
        signature=json.dumps([data['config']['input_mode'],[[[item.get(k) for k in fields],(item['snapshot'] or {}).get('frame_count'),
            [assets[item['asset']][k] for k in ('rating','flag','path','updated')],jobs.get(item.get('job'))] for item in visible]],sort_keys=True)
        rebuild=signature!=self._table_signature
        scroll=self.table.verticalScrollBar().value()
        if rebuild:self.table.clearSelection();self.table.setRowCount(len(visible));self.table.setUpdatesEnabled(False)
        for row,item in enumerate(visible if rebuild else []):
            asset=assets[item['asset']];snapshot=item['snapshot'];count=snapshot['frame_count'] if snapshot else None
            stage=_t('跳过') if item['skip'] else STAGE.get(item['stage'],item['stage'])
            if snapshot and item.get('verified_session')!=self.store.session:stage+=_t(' · 需检查')
            if item.get('job'):
                job=jobs[item['job']];stage+=' / '+STATUS[job['status']]+' '+str(job['done_count'])+'/'+str(job['total_count'])
            values=['',item['name'],item['group'],INPUT[item['input_mode'] or data['config']['input_mode']],str(count or _t('待准备')),stage,str(item['ev']),_tf('{0} ★ / {1}', asset["rating"], FLAGS[asset["flag"]]),asset['path'],item['error']]
            for column,value in enumerate(values):self.table.setItem(row,column,_ui(QTableWidgetItem, value))
            check=self.table.item(row,0);check.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable);check.setCheckState(Qt.CheckState.Checked if item['selected'] else Qt.CheckState.Unchecked)
            self.table.item(row,1).setData(Qt.ItemDataRole.UserRole,item['asset'])
            if item['preview_after']:self.table.item(row,1).setIcon(photo_icon(QPixmap(item['preview_after'])))
            if item['asset'] in selected:self.table.selectRow(row) if len(selected)==1 else self._select_row(row)
        self.table.setUpdatesEnabled(True);self.table.verticalScrollBar().setValue(scroll);self._table_signature=signature
        self.refreshing=False;busy=self.controller.running();self.table.setEnabled(not busy);self.batches.setEnabled(not busy);self.templates.setEnabled(not busy);self.tabs.setEnabled(not busy)
        for button in self.run_buttons:button.setEnabled(not busy)
        self.pause_button.setEnabled(busy)
        _ui(self.summary.setText, _tf('{0} · {1} 个文件 · 已准备 {2} · 勾选项已知 {3} 帧 · {4}。未准备文件帧数待识别；入队时会显示准确数量。', data["name"], len(data["items"]), ready, frames, STATUS[data["status"]]))
        history=self.store.history(self.identifier);_ui(self.history_label.setText, _t('批次修改历史：\n')+'\n'.join(h['label'].split(' · ')[0] for h in history[:8]))
    def _select_row(self,row):
        from PySide6.QtCore import QItemSelectionModel
        self.table.selectionModel().select(self.table.model().index(row,1),QItemSelectionModel.SelectionFlag.Select|QItemSelectionModel.SelectionFlag.Rows)
    def load_settings(self):
        c=self.store.get(self.identifier)['config']
        for widget,key in ((self.input_mode,'input_mode'),(self.color,'input_color'),(self.space,'working_space'),(self.method,'method'),(self.stop,'stop_after'),(self.sync_mode,'sync_mode'),(self.rule,'group_rule')):widget.setCurrentIndex(max(0,widget.findData(c[key])))
        for key,check in self.field_checks.items():check.setChecked(key in c['fields'])
        self.delta.setValue(c['exposure_delta']);_ui(self.naming.setText, c['template']);_ui(self.output.setText, c['output'])
        self.whole.setChecked(c['whole_roll']);self.review_required.setChecked(c['require_review']);self.group_dirs.setChecked(c['group_directories'])
    def save_settings(self):
        if not self.identifier:return
        def apply():
            c=self.store.get(self.identifier)['config']
            for widget,key in ((self.input_mode,'input_mode'),(self.color,'input_color'),(self.space,'working_space'),(self.method,'method'),(self.stop,'stop_after'),(self.sync_mode,'sync_mode'),(self.rule,'group_rule')):c[key]=widget.currentData()
            c.update(fields=[k for k,v in self.field_checks.items() if v.isChecked()],exposure_delta=self.delta.value(),template=self.naming.text(),output=self.output.text(),
                     whole_roll=self.whole.isChecked(),require_review=self.review_required.isChecked(),group_directories=self.group_dirs.isChecked())
            self.store.configure(self.identifier,c);_ui(self.message.setText, _t('设置已保存；不会改变已加入队列的参数快照'))
            return True
        return self.attempt(apply)
    def create_current(self):
        if self.controller.running():_ui(self.message.setText, _t('请先暂停当前步骤再切换批次'));return
        ids=[x['id'] for x in self.parent().photo_assets]
        if not ids:_ui(self.message.setText, _t('请先多图拖入或多选文件'));return
        self.reload_batches(self.store.create(ids))
    def create_files(self):
        if self.controller.running():_ui(self.message.setText, _t('请先暂停当前步骤再建立批次'));return
        from native_app import IMAGE_FILTER
        paths,_=_ui(QFileDialog.getOpenFileNames, self,_t('多选文件建立批次'),'',IMAGE_FILTER)
        if paths:
            result=self.parent().import_library_paths(paths);self.reload_batches(self.store.create(result['ids']))
    def rename(self):
        if self.controller.running():return
        name,ok=_ui(QInputDialog.getText, self,_t('重命名批次'),_t('批次名称'))
        if ok and name:self.attempt(lambda:self.store.edit(self.identifier,lambda d:d.update(name=name[:120])));self.reload_batches()
    def load_navigation(self):
        if self.controller.running():return
        if self.identifier:
            visible=set(self.visible_ids());ids=[i['asset'] for i in self.store.get(self.identifier)['items'] if not i['skip'] and i['asset'] in visible]
            if not ids:_ui(self.message.setText, _t('当前筛选没有可载入的照片'));return
            self.parent().set_photo_batch(ids);self.hide()
    def item_changed(self,item):
        if not self.refreshing and item.column()==0:self.attempt(lambda:self.store.update_items(self.identifier,[self.table.item(item.row(),1).data(Qt.ItemDataRole.UserRole)],selected=item.checkState()==Qt.CheckState.Checked))
    def set_items(self):
        values={'input_mode':self.item_mode.currentData(),'ev':self.ev.value()}
        if self.item_group.text().strip():values['group']=self.item_group.text().strip()[:120]
        self.attempt(lambda:self.store.update_items(self.identifier,self.selected(),**values));self.refresh_groups();self.refresh()
    def flag_items(self,skip):self.attempt(lambda:self.store.update_items(self.identifier,self.selected(),skip=skip));self.refresh()
    def remove_items(self):self.attempt(lambda:self.store.remove(self.identifier,self.selected()));self.refresh_groups();self.refresh()
    def open_item(self):
        ids=self.selected()
        if len(ids)!=1:_ui(self.message.setText, _t('请选择一张照片微调'));return
        self.parent().current_batch_id=self.identifier;self.parent().open_library_asset(ids[0]);self.hide()
    def metadata(self):
        rating,ok=_ui(QInputDialog.getInt, self,_t('选中照片评级'),_t('星级 0–5'),0,0,5)
        if not ok:return
        flag,ok=_ui(QInputDialog.getItem, self,_t('标记'),_t('选中照片'),[_t('未标记'),_t('精选'),_t('弃选')],0,False)
        if not ok:return
        flag={'未标记':'none','精选':'pick','弃选':'reject'}[flag]
        for identifier in self.selected():
            row=self.store.studio.get_asset(identifier);self.store.studio.metadata(identifier,rating,flag,row['roll'])
        self.refresh()
    def refresh_groups(self):
        if not self.identifier:return
        for old_row in range(self.group_table.rowCount()):
            for column in (1,2):
                old=self.group_table.cellWidget(old_row,column)
                if old:old.hide();self.group_table.removeCellWidget(old_row,column)
        data=self.store.get(self.identifier);names=list(dict.fromkeys(i['group'] for i in data['items']));self.group_table.setRowCount(len(names))
        for row,name in enumerate(names):
            items=[i for i in data['items'] if i['group']==name];value=data['groups'].get(name,{})
            self.group_table.setItem(row,0,_ui(QTableWidgetItem, name));self.group_table.item(row,0).setFlags(Qt.ItemFlag.ItemIsEnabled)
            combo=QComboBox();_ui(combo.addItem, _t('使用模板风格') if value.get('values') else _t('请选择参考照片'),None)
            for item in items:_ui(combo.addItem, item['name'],item['asset'])
            combo.setCurrentIndex(max(0,combo.findData(value.get('reference'))));self.group_table.setCellWidget(row,1,combo)
            check=_ui(QCheckBox, _t('本组共享实测片基'));check.setChecked(value.get('share_base',False));self.group_table.setCellWidget(row,2,check)
            self.group_table.setItem(row,3,_ui(QTableWidgetItem, str(len(items))))
    def save_groups(self):
        def apply():
            for row in range(self.group_table.rowCount()):
                name=self.group_table.item(row,0).text();reference=self.group_table.cellWidget(row,1).currentData();share=self.group_table.cellWidget(row,2).isChecked()
                confirmed=False
                if share:
                    if not reference:raise ValueError(_t('共享实测片基必须指定本组实际参考照片'))
                    text=_tf('组“{0}”的 {1} 张将共享所选参考图的实测片基，并各自重新测光。请确认这些照片来自可共享片基的胶卷和光源，且参考片基取样可信。', name, self.group_table.item(row,3).text())
                    confirmed=_ui(QMessageBox.question, self,_t('确认本组共享片基'),text)==QMessageBox.StandardButton.Yes
                    if not confirmed:return
                self.store.group(self.identifier,name,reference,share,confirmed)
            _ui(self.message.setText, _t('分组参考已保存；应用组风格时生效'))
        self.attempt(apply)
    def reference_current(self):
        self.parent().save_session();asset=self.parent().current_workflow_asset()
        if not asset:return
        data=self.store.get(self.identifier);item=next((i for i in data['items'] if i['asset']==asset),None)
        if item:self.attempt(lambda:self.store.group(self.identifier,item['group'],asset));self.refresh_groups()
    def rule_groups(self):self.attempt(lambda:self.store.group_by_rule(self.identifier,self.rule.currentData()));self.refresh_groups();self.refresh()
    def run(self,operation,ids=None,overwrite=False):
        if not self.identifier:return
        if not self.save_settings():return
        scope=ids if ids is not None else (self.selected() or (self.visible_ids() if self.filters.active() else None));data=self.store.get(self.identifier)
        if operation!='report' and scope==[]:_ui(self.message.setText, _t('当前筛选没有可处理的照片'));return
        config=data['config'];includes_style=operation=='style' or (operation=='pipeline' and config['stop_after']=='style')
        if includes_style and config['method']=='sync':
            targets=[i for i in data['items'] if i['selected'] and not i['skip'] and (scope is None or i['asset'] in scope)]
            text=_tf('将同步 {0} 个文件的以下参数：', len(targets))+'、'.join(FIELD_NAMES[k] for k in config['fields'])+_t('。\n已有的所选参数将替换；几何和局部修复保留，可撤销本次同步。\n\n')+'\n'.join(i['name']+' · '+i['group'] for i in targets[:30])
            if _ui(QMessageBox.question, self,_t('确认批次同步范围'),text)!=QMessageBox.StandardButton.Yes:return
        self.attempt(lambda:self.controller.start(self.identifier,operation,scope,overwrite))
    def reprepare(self):
        ids=self.selected()
        if not ids:_ui(self.message.setText, _t('请选择需要重新准备的照片'));return
        items=[i['name'] for i in self.store.get(self.identifier)['items'] if i['asset'] in ids]
        text=_t('将重新估片基 / 测光并重置以下照片的已有调色、几何和局部修复；可用批次撤销恢复。\n\n')+'\n'.join(items[:30])
        if _ui(QMessageBox.question, self,_t('重新准备并替换这些编辑'),text)==QMessageBox.StandardButton.Yes:self.run('prepare',ids,True)
    def retry_failed(self):
        visible=set(self.visible_ids());ids=[i['asset'] for i in self.store.get(self.identifier)['items'] if i['error'] and i['asset'] in visible]
        self.run('pipeline',ids)
    def review(self):
        ids=[i['asset'] for i in self.active_items()]
        if not ids:_ui(self.message.setText, _t('当前筛选没有可复核的照片'));return
        self.attempt(lambda:self.store.review(self.identifier,ids));self.refresh()
    def proof(self,count):
        if not self.identifier:return
        items=self.active_items()
        if not items:_ui(self.message.setText, _t('当前筛选没有可校样的照片'));return
        self.proof_dialog=ProofDialog(items,self,count);self.proof_dialog.show()
    def output_folder(self):
        path=_ui(QFileDialog.getExistingDirectory, self,_t('选择批次输出根目录'),self.output.text())
        if path:_ui(self.output.setText, path)
    def enqueue(self):
        if not self.save_settings():return
        def apply():
            made=self.store.enqueue(self.identifier);jobs=[self.store.studio.get_job(j) for j in made]
            _ui(self.message.setText, _tf('已固定 {0} 个文件、{1} 帧；点击“开始 / 继续本批次导出”运行', len(made), sum(j["total_count"] for j in jobs)))
            self.refresh()
        self.attempt(apply)
    def start_exports(self):
        if not self.identifier:return
        ids=[i['job'] for i in self.store.get(self.identifier)['items'] if i.get('job')]
        self.store.studio.retry(ids);self.parent().queue_controller.start()
    def new_round(self):
        if not self.identifier:return
        text=_t('将为本批次开启新一轮导出，保留上一轮的任务、参数和成品。新一轮使用当前编辑与命名设置，复核后重新加入队列；不会覆盖上一轮文件。')
        if _ui(QMessageBox.question, self,_t('保留旧任务并新建下一轮'),text)==QMessageBox.StandardButton.Yes:
            self.attempt(lambda:self.store.new_round(self.identifier));self.refresh()
    def undo(self):
        self.parent().save_session()
        self.attempt(lambda:self.store.undo(self.identifier));self.parent().reload_workflow_asset();self.refresh()
    def save_template(self):
        if not self.identifier or not self.save_settings():return
        name,ok=_ui(QInputDialog.getText, self,_t('保存模板'),_t('模板名称（不保存原片路径）'))
        if ok and name:self.attempt(lambda:self.store.save_template(self.identifier,name));self.reload_batches()
    def apply_template(self):
        if self.controller.running():return
        if self.identifier and self.templates.currentData():
            self.attempt(lambda:self.store.apply_template(self.identifier,self.templates.currentData()));self.load_settings();self.refresh_groups()
            _ui(self.message.setText, _t('模板规则已载入；请检查参考图和输出位置，再显式运行。共享实测片基需重新确认。'))
    def open_report(self):
        data=self.store.get(self.identifier)
        if data.get('report'):QDesktopServices.openUrl(QUrl.fromLocalFile(data['report']['path']))
