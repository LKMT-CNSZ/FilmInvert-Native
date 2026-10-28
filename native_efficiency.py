"""Batch filters, editing-aware keys and read-only adjacent-source prefetch."""
from pathlib import Path
import struct
import threading
from PySide6.QtCore import Qt,QObject,QEvent,Signal,QThread
from PySide6.QtWidgets import (QWidget,QHBoxLayout,QLineEdit,QComboBox,QPushButton,
    QApplication,QAbstractSpinBox,QTextEdit,QPlainTextEdit)

def catalog(studio,identifiers):
    rows={}
    with studio.connection() as db:
        for start in range(0,len(identifiers),400):
            ids=identifiers[start:start+400]
            sql='SELECT id,source_key,path,name,rating,flag,roll,input_mode,updated,NULL AS snapshot,(snapshot IS NOT NULL) AS has_edits FROM assets WHERE id IN ('+','.join('?' for _ in ids)+')'
            rows.update((row['id'],studio.asset(row)) for row in db.execute(sql,ids))
    return [rows[i] for i in identifiers if i in rows]

def job_states(studio):
    with studio.connection() as db:return {r['id']:dict(r) for r in db.execute('SELECT id,status,done_count,total_count FROM jobs')}

def phase(item,session,asset,job=None):
    if item.get('skip') or asset['flag']=='reject':return 'skip'
    if item.get('error') or (job and job['status']=='failed'):return 'failed'
    if item.get('snapshot') and item.get('verified_session')!=session:return 'check'
    if not item.get('valid'):return 'check'
    if not item.get('snapshot'):return 'prepare'
    if job:return 'completed' if job['status']=='completed' else 'queued'
    if not item.get('reviewed'):return 'review'
    return 'ready' if asset['flag']!='reject' else 'skip'

class PhotoFilters(QWidget):
    changed=Signal()
    def __init__(self,parent=None):
        super().__init__(parent);line=QHBoxLayout(self);line.setContentsMargins(0,0,0,0)
        self.search=QLineEdit();self.search.setPlaceholderText('搜索文件名 / 分组 / 路径');self.search.setClearButtonEnabled(True);line.addWidget(self.search,2)
        self.group=QComboBox();self.group.addItem('全部分组',None);line.addWidget(self.group,1)
        self.state=QComboBox()
        for title,key in [('全部状态',None),('待检查','check'),('待准备','prepare'),('待复核','review'),('可导出','ready'),('已入队','queued'),('已完成','completed'),('失败','failed'),('跳过 / 弃选','skip')]:self.state.addItem(title,key)
        line.addWidget(self.state,1);self.rating=QComboBox()
        for n in range(6):self.rating.addItem('全部星级' if n==0 else f'{n} 星及以上',n)
        line.addWidget(self.rating);self.flag=QComboBox()
        for title,key in [('全部标记',None),('精选','pick'),('弃选','reject'),('未标记','none')]:self.flag.addItem(title,key)
        line.addWidget(self.flag);button=QPushButton('清除筛选');button.clicked.connect(self.reset);line.addWidget(button)
        self.search.textChanged.connect(self.changed)
        for widget in (self.group,self.state,self.rating,self.flag):widget.currentIndexChanged.connect(self.changed)
        for name,widget in [('搜索照片',self.search),('按分组筛选',self.group),('按处理状态筛选',self.state),('最低星级',self.rating),('按标记筛选',self.flag)]:widget.setAccessibleName(name)
    def reset(self):
        widgets=(self.search,self.group,self.state,self.rating,self.flag)
        for widget in widgets:widget.blockSignals(True)
        self.search.clear()
        for widget in widgets[1:]:widget.setCurrentIndex(0)
        for widget in widgets:widget.blockSignals(False)
        self.changed.emit()
    def groups(self,values):
        values=sorted(set(values))
        if [self.group.itemData(i) for i in range(1,self.group.count())]==values:return
        previous=self.group.currentData();self.group.blockSignals(True);self.group.clear();self.group.addItem('全部分组',None)
        for value in values:self.group.addItem(value or '未分组',value)
        self.group.setCurrentIndex(max(0,self.group.findData(previous)));self.group.blockSignals(False)
    def active(self):return bool(self.search.text().strip() or any(w.currentIndex() for w in (self.group,self.state,self.rating,self.flag)))
    def accepts(self,asset,group,state):
        text=self.search.text().strip().casefold()
        return ((not text or text in ' '.join((asset['name'],asset['path'],asset['roll'],group)).casefold())
            and (self.group.currentData() is None or self.group.currentData()==group)
            and (self.state.currentData() is None or self.state.currentData()==state)
            and asset['rating']>=self.rating.currentData()
            and (self.flag.currentData() is None or asset['flag']==self.flag.currentData()))

class EditingKeys(QObject):
    """A key event filter leaves text, spin-box and combo-box editing untouched."""
    def __init__(self,host,callback):
        super().__init__(host);self.host=host;self.callback=callback;QApplication.instance().installEventFilter(self)
    def eventFilter(self,obj,event):
        if event.type()!=QEvent.Type.KeyPress or QApplication.activeWindow()!=self.host:return False
        focus=QApplication.focusWidget();editing=False
        while focus and focus!=self.host:
            if isinstance(focus,(QLineEdit,QAbstractSpinBox,QComboBox,QTextEdit,QPlainTextEdit)):editing=True;break
            focus=focus.parentWidget()
        mods=event.modifiers();key=event.key()
        if mods==Qt.KeyboardModifier.ControlModifier and key==Qt.Key.Key_F:
            return bool(self.callback('search',None))
        if editing:return False
        command=None;value=None
        if mods==Qt.KeyboardModifier.NoModifier:
            if key in (Qt.Key.Key_Left,Qt.Key.Key_Right):command='navigate';value=-1 if key==Qt.Key.Key_Left else 1
            elif Qt.Key.Key_0<=key<=Qt.Key.Key_5:command='rating';value=key-Qt.Key.Key_0
            elif key in (Qt.Key.Key_P,Qt.Key.Key_X,Qt.Key.Key_U):command='flag';value={Qt.Key.Key_P:'pick',Qt.Key.Key_X:'reject',Qt.Key.Key_U:'none'}[key]
        elif mods==Qt.KeyboardModifier.ShiftModifier and key==Qt.Key.Key_R:command='review'
        if command:
            if event.isAutoRepeat():return True
            return bool(self.callback(command,value))
        return False

def estimate_decoded_bytes(path):
    """Read bounded TIFF/RAW IFD dimensions, never decompress a sensor to guess."""
    from file_io import io_path
    from loader import RAW_EXTS
    path=Path(path)
    with io_path(path).open('rb') as stream:
        header=stream.read(8)
        if len(header)==8 and header[:2] in (b'II',b'MM'):
            order='<' if header[:2]==b'II' else '>'
            if struct.unpack(order+'H',header[2:4])[0]!=42:return None
            offsets=[struct.unpack(order+'I',header[4:])[0]];seen=set();largest=0
            while offsets and len(seen)<64:
                offset=offsets.pop()
                if not offset or offset in seen:continue
                seen.add(offset);stream.seek(offset);count=stream.read(2)
                if len(count)!=2:return None
                n=struct.unpack(order+'H',count)[0]
                if n>1024:return None
                data=stream.read(n*12+4)
                if len(data)!=n*12+4:return None
                dims={}
                for index in range(n):
                    tag,kind,length,value=struct.unpack(order+'HHI4s',data[index*12:index*12+12])
                    if tag not in (256,257,330) or kind not in (3,4) or not 1<=length<=64:continue
                    size=2 if kind==3 else 4
                    if size*length>4:
                        position=stream.tell();stream.seek(struct.unpack(order+'I',value)[0]);value=stream.read(size*length);stream.seek(position)
                    if len(value)<size*length:return None
                    values=struct.unpack(order+('H' if kind==3 else 'I')*length,value[:size*length])
                    if tag==330:offsets.extend(values)
                    else:dims[tag]=values[0]
                largest=max(largest,dims.get(256,0)*dims.get(257,0)*12)
                offsets.append(struct.unpack(order+'I',data[-4:])[0])
            return largest or None
    if path.suffix.lower() in RAW_EXTS:return None
    from PIL import Image
    with Image.open(io_path(path)) as image:return image.width*image.height*12

class PrefetchWorker(QThread):
    """Only publishes a decoded immutable original. Never installs engine.STATE."""
    def __init__(self,asset,cache,protected,protected_bytes,parent=None):
        super().__init__(parent);self.asset=asset;self.cache=cache;self.protected=protected;self.protected_bytes=protected_bytes
        self.cancel=threading.Event();self.outcome='cancelled'
    def stop(self):self.cancel.set()
    def run(self):
        try:
            from loader import load
            from desktop_model import source_identity
            from file_io import io_path
            spec=(self.asset.get('snapshot') or {}).get('input',{})
            color=spec.get('color','legacy');space=spec.get('working_space','srgb')
            estimate=estimate_decoded_bytes(self.asset['path'])
            if self.cancel.is_set():return
            if not estimate or not self.cache.prefetch_fits(estimate,self.protected_bytes):self.outcome='budget';return
            identity=source_identity(self.asset['path']);expected=(self.asset.get('snapshot') or {}).get('source')
            if expected and any(identity[k]!=expected[k] for k in ('size','sha256')):self.outcome='changed';return
            key=self.cache.key(identity,color,space)
            if self.cancel.is_set():return
            if self.cache.contains(key):self.outcome='cached';return
            loaded=load(identity['path'],color,space)
            if self.cancel.is_set():return
            stat=io_path(identity['path']).stat()
            if (stat.st_size,stat.st_mtime_ns)!=(identity['size'],identity['mtime_ns']):self.outcome='changed';return
            if self.cache.put_prefetched(key,loaded,self.protected,self.protected_bytes,self.cancel):self.outcome='loaded'
            else:self.outcome='budget' if not self.cancel.is_set() else 'cancelled'
        except Exception:self.outcome='unavailable' # Foreground open owns any actionable error.
