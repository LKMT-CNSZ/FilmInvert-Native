"""Explicit presentation-only text bindings for the native Qt interface.

Source strings stay Chinese; identifiers, paths and user text are never looked up.
Bindings retain formatting arguments so switching languages can update open windows
without rebuilding widgets, reloading a photograph or emitting edit signals.
"""
from PySide6.QtCore import QCoreApplication, QEvent, QTranslator
from PySide6.QtWidgets import QApplication, QComboBox, QFormLayout
from shiboken6 import isValid
from i18n_en import EN

_language = 'zh_CN'
_bindings = {}
_translator = None


class Text(str):
    def __new__(cls, source, values=(), parts=None):
        original = ''.join(str.__str__(p) if isinstance(p, Text) else str(p) for p in parts) if parts is not None else source.format(*[p.source_value() if isinstance(p, Text) else p for p in values]) if values else source
        obj = super().__new__(cls, original)
        obj.source, obj.values, obj.parts = source, values, parts
        return obj

    def source_value(self):
        return str.__str__(self)

    def render(self):
        if self.parts is not None:
            return ''.join(render(p) for p in self.parts)
        template = EN.get(self.source, self.source) if _language == 'en' else self.source
        return template.format(*[render(p) for p in self.values]) if self.values else template

    def __str__(self):
        return self.render()

    def __format__(self, spec):
        return format(self.render(), spec)

    def __add__(self, other):
        if not isinstance(other, str):return NotImplemented
        return Text('', parts=(self, other))

    def __radd__(self, other):
        if not isinstance(other, str):return NotImplemented
        return Text('', parts=(other, self))

    def join(self, iterable):
        parts = []
        for value in iterable:
            if parts:parts.append(self)
            parts.append(value)
        return Text('', parts=tuple(parts))


def tr(source):
    return source if isinstance(source, Text) else Text(source)


def trf(source, *values):
    return Text(source, values)


def render(value):
    if isinstance(value, Text):return value.render()
    if isinstance(value, list):return [render(v) for v in value]
    if isinstance(value, tuple):return tuple(render(v) for v in value)
    return value


def _marked(value):
    return isinstance(value, Text) or isinstance(value, (tuple, list)) and any(_marked(v) for v in value)


def _bind(obj, setter, args, index=None):
    if len(_bindings) % 128 == 0:
        for stale,(target,_,_) in list(_bindings.items()):
            if not isValid(target):_bindings.pop(stale,None)
    key = (id(obj), setter, index)
    if any(_marked(a) for a in args):_bindings[key] = (obj, setter, args)
    else:_bindings.pop(key, None)


_SETTERS = {'setText', 'setTitle', 'setWindowTitle', 'setToolTip', 'setStatusTip',
            'setAccessibleName', 'setAccessibleDescription', 'setPlaceholderText',
            'setPrefix', 'setSuffix', 'setHorizontalHeaderLabels', 'setHeaderLabels',
            'showMessage', 'setLabelText'}
_CONSTRUCTORS = {'QLabel': 'setText', 'QPushButton': 'setText', 'QCheckBox': 'setText',
                 'QToolButton': 'setText', 'QRadioButton': 'setText', 'QGroupBox': 'setTitle',
                 'QAction': 'setText', 'QMenu': 'setTitle', 'QDockWidget': 'setWindowTitle',
                 'QToolBar': 'setWindowTitle', 'QTableWidgetItem': 'setText',
                 'QListWidgetItem': 'setText', 'QGraphicsSimpleTextItem': 'setText'}


def ui(call, *args, **kwargs):
    """Call a Qt presentation boundary; only explicitly marked text is translated."""
    name = getattr(call, '__name__', '')
    obj = getattr(call, '__self__', None)
    if name == 'clear' and obj is not None:
        for key in list(_bindings):
            if key[0] == id(obj) and key[1] in ('setItemText','setTabText','setText'):_bindings.pop(key)
    if name == 'addItems' and isinstance(obj, QComboBox):
        for text in args[0]:ui(obj.addItem, text)
        return None
    result = call(*render(args), **{k:render(v) for k,v in kwargs.items()})
    if name in _CONSTRUCTORS:
        for value in args:
            if isinstance(value, str):
                _bind(result, _CONSTRUCTORS[name], (value,));break
    elif name in _SETTERS and obj is not None:
        _bind(obj, name, args)
    elif name in ('addItem', 'insertItem') and isinstance(obj, QComboBox):
        offset = 1 if name == 'insertItem' else 0
        index = args[0] if offset else obj.count()-1
        value = next((v for v in args[offset:] if isinstance(v,str)), None)
        if value is not None:_bind(obj, 'setItemText', (index,value), index)
    elif name == 'addTab':
        _bind(obj, 'setTabText', (result,args[-1]), result)
    elif name == 'addMenu' and args and isinstance(args[0], str):
        _bind(result, 'setTitle', (args[0],))
    elif name == 'addRow' and isinstance(obj, QFormLayout) and len(args)==2 and isinstance(args[0], str):
        label = obj.labelForField(args[1])
        if label is not None:_bind(label, 'setText', (args[0],))
    elif name == 'getItem' and len(args)>3 and result[1]:
        # Return the original choice to callers which map display choices to stable IDs.
        choices=args[3]
        for choice in choices:
            if render(choice)==result[0]:return choice,True
    return result


def language():
    return _language


def source_text(obj):
    """Preserve a binding when appending to a label's existing display message."""
    binding=_bindings.get((id(obj),'setText',None))
    return binding[2][0] if binding is not None else obj.text()


class StandardTranslator(QTranslator):
    """Qt standard buttons are translated separately from application messages."""
    BUTTONS={'OK':'确定','Cancel':'取消','&Cancel':'取消','Close':'关闭','&Close':'关闭',
             '&Yes':'是(&Y)','&No':'否(&N)','Yes':'是','No':'否','Save':'保存','&Save':'保存',
             'Open':'打开','&Open':'打开','Apply':'应用','&Apply':'应用','Reset':'重置',
             'Show Details...':'显示详情…','Hide Details...':'隐藏详情…',
             'Look in:':'查找位置：','File name:':'文件名：','Files of type:':'文件类型：',
             'All Files (*)':'所有文件 (*)','Back':'返回','Parent Directory':'上级目录',
             'Create New Folder':'新建文件夹','List View':'列表','Detail View':'详细信息',
             'Name':'名称','Size':'大小','Type':'类型','Date Modified':'修改时间'}
    def translate(self, context, sourceText, disambiguation=None, n=-1):
        return self.BUTTONS.get(sourceText, sourceText) if _language=='zh_CN' else sourceText


def set_language(value):
    global _language, _translator
    _language = value if value in ('zh_CN','en') else 'zh_CN'
    app = QApplication.instance()
    if app is not None:
        if _translator is None:_translator=StandardTranslator(app)
        else:app.removeTranslator(_translator)
        app.installTranslator(_translator)
    for key,(obj,setter,args) in list(_bindings.items()):
        if not isValid(obj):
            _bindings.pop(key,None);continue
        if setter=='showMessage' and not obj.currentMessage():continue
        previous = obj.blockSignals(True) if hasattr(obj,'blockSignals') else None
        try:getattr(obj,setter)(*render(args))
        finally:
            if previous is not None:obj.blockSignals(previous)
    if app is not None:
        for widget in app.allWidgets():
            QCoreApplication.sendEvent(widget,QEvent(QEvent.Type.LanguageChange))
            widget.update()
    return _language
