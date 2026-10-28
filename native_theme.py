"""Darkroom presentation only: neutral surfaces, controls and line icons."""
from pathlib import Path
from PySide6.QtCore import Qt,QPointF,QRectF
from PySide6.QtGui import QColor,QPalette,QPainter,QPen,QPixmap,QIcon,QFont

CANVAS='#111111'
PANEL='#1e1e1e'
TEXT='#ededed'
MUTED='#ababab'
LINE='#383838'

STYLE='''
QWidget { color:#ededed; font-family:"Microsoft YaHei UI","Segoe UI"; font-size:12px; }
QMainWindow,QDialog { background:#171717; }
QMainWindow::separator { background:#303030; width:1px; height:1px; }
QMainWindow::separator:hover { background:#777777; }
QDockWidget { border:0; }
QDockWidget::title { background:#1e1e1e; color:#ababab; padding:7px 12px; border-bottom:1px solid #303030; }
QScrollArea { background:#1e1e1e; border:0; }
QScrollArea>QWidget>QWidget { background:#1e1e1e; }
QToolBar { background:#1b1b1b; border:0; border-bottom:1px solid #303030; spacing:5px; padding:7px 10px; }
QToolBar#alignmentToolbar { background:#202020; }
QToolBar::separator { background:#383838; width:1px; margin:4px 6px; }
QToolButton,QPushButton { background:#262626; border:1px solid #3d3d3d; border-radius:3px; padding:6px 10px; }
QToolBar QToolButton { background:transparent; border-color:transparent; padding:6px 9px; }
QToolButton:hover,QPushButton:hover { background:#333333; border-color:#707070; }
QToolButton:pressed,QPushButton:pressed { background:#444444; }
QToolButton:checked,QPushButton:checked { color:#111111; background:#ededed; border-color:#ededed; }
QPushButton[role="primary"],QPushButton:default { color:#111111; background:#ededed; border-color:#ededed; }
QPushButton[role="primary"]:hover,QPushButton:default:hover { background:#ffffff; border-color:#ffffff; }
QToolButton:disabled,QPushButton:disabled { color:#757575; background:#202020; border-color:#303030; }
QPushButton:focus,QToolButton:focus { border-color:#ededed; }
QComboBox,QAbstractSpinBox,QLineEdit,QTextEdit,QPlainTextEdit { background:#151515; color:#ededed; border:1px solid #414141; border-radius:3px; padding:5px 7px; selection-background-color:#ededed; selection-color:#111111; }
QComboBox:hover,QAbstractSpinBox:hover,QLineEdit:hover { border-color:#777777; }
QComboBox:focus,QAbstractSpinBox:focus,QLineEdit:focus { border-color:#ededed; }
QComboBox:disabled,QAbstractSpinBox:disabled,QLineEdit:disabled { color:#757575; border-color:#303030; }
QComboBox::drop-down { border:0; width:20px; }
QComboBox::down-arrow { image:url("__DOWN_ICON__"); width:12px; height:12px; }
QAbstractSpinBox::up-button,QAbstractSpinBox::down-button { border:0; background:#242424; width:15px; }
QAbstractSpinBox::up-arrow { image:url("__UP_ICON__"); width:10px; height:10px; }
QAbstractSpinBox::down-arrow { image:url("__DOWN_ICON__"); width:10px; height:10px; }
QAbstractSpinBox::up-button:hover,QAbstractSpinBox::down-button:hover { background:#414141; }
QComboBox QAbstractItemView { background:#262626; color:#ededed; selection-background-color:#ededed; selection-color:#111111; border:1px solid #414141; }
QCheckBox,QRadioButton { spacing:7px; padding:4px 0; }
QCheckBox::indicator { width:13px; height:13px; background:#151515; border:1px solid #6a6a6a; border-radius:2px; }
QCheckBox::indicator:checked { background:#ededed; border:1px solid #ededed; image:url("__CHECK_ICON__"); }
QCheckBox::indicator:disabled { background:#242424; border-color:#444444; }
QTabWidget::pane { border:0; background:#1e1e1e; }
QTabBar::tab { color:#ababab; background:#1e1e1e; padding:9px 13px; border-bottom:2px solid #1e1e1e; }
QTabBar::tab:selected { color:#ffffff; border-bottom-color:#ededed; }
QTabBar::tab:hover { color:#ededed; background:#282828; }
QSlider::groove:horizontal { background:#4a4a4a; height:2px; border:0; }
QSlider::sub-page:horizontal { background:#aaaaaa; height:2px; }
QSlider::handle:horizontal { background:#ededed; width:10px; height:10px; margin:-4px 0; border-radius:5px; }
QSlider::handle:horizontal:disabled { background:#777777; }
QListView,QTreeView,QTableView { background:#191919; alternate-background-color:#202020; border:1px solid #333333; outline:0; gridline-color:#333333; selection-background-color:#353535; selection-color:#ffffff; }
QListWidget::item { padding:6px; border:1px solid transparent; }
QListWidget::item:selected { background:#303030; color:#ffffff; border:1px solid #ededed; border-radius:2px; }
QListWidget::item:hover { background:#292929; }
QListWidget#frameList { background:#191919; border:0; }
QListWidget#frameList::item { padding:5px; }
QHeaderView::section { background:#252525; color:#bdbdbd; padding:7px; border:0; border-bottom:1px solid #414141; }
QTableCornerButton::section { background:#252525; border:0; }
QMenuBar,QMenu { background:#1b1b1b; color:#ededed; }
QMenuBar { border-bottom:1px solid #303030; }
QMenuBar::item { padding:5px 9px; }
QMenuBar::item:selected,QMenu::item:selected { background:#383838; }
QMenu { border:1px solid #414141; padding:5px; }
QMenu::item { padding:7px 25px 7px 12px; }
QMenu::separator { height:1px; background:#414141; margin:5px 8px; }
QStatusBar { background:#191919; color:#ababab; border-top:1px solid #303030; }
QStatusBar::item { border:0; }
QStatusBar QLabel { color:#ababab; padding:3px 5px; }
QProgressBar { color:#ededed; border:0; background:#303030; min-height:8px; max-height:15px; text-align:center; }
QProgressBar::chunk { background:#bdbdbd; }
QLabel[role="brand"] { color:#ffffff; font-family:"Segoe UI","Microsoft YaHei UI"; font-size:18px; font-weight:600; padding:0 12px 0 2px; }
QLabel[role="section"] { color:#ededed; font-size:12px; font-weight:600; padding-top:14px; padding-bottom:6px; }
QLabel[role="note"] { color:#ababab; font-size:11px; padding:4px 0; }
QLabel[role="subtle"] { color:#ababab; }
QWidget#filmstripBody { background:#1b1b1b; }
QScrollBar:vertical { background:#1b1b1b; width:9px; margin:0; }
QScrollBar:horizontal { background:#1b1b1b; height:9px; margin:0; }
QScrollBar::handle { background:#555555; border-radius:3px; }
QScrollBar::handle:vertical { min-height:24px; }
QScrollBar::handle:horizontal { min-width:24px; }
QScrollBar::handle:hover { background:#888888; }
QScrollBar::add-line,QScrollBar::sub-line { width:0; height:0; }
QScrollBar::add-page,QScrollBar::sub-page { background:transparent; }
QToolTip { color:#ededed; background:#2c2c2c; border:1px solid #555555; padding:6px; }
QSplitter::handle { background:#303030; }
'''
STYLE=STYLE.replace('__CHECK_ICON__',(Path(__file__).resolve().parent/'assets/interface/check.svg').as_posix())
STYLE=STYLE.replace('__DOWN_ICON__',(Path(__file__).resolve().parent/'assets/interface/down.svg').as_posix())
STYLE=STYLE.replace('__UP_ICON__',(Path(__file__).resolve().parent/'assets/interface/up.svg').as_posix())


def apply(app):
    if app.property('filmDarkroomTheme') is True:return
    app.setStyle('Fusion')
    palette=QPalette()
    values={QPalette.ColorRole.Window:'#171717',QPalette.ColorRole.WindowText:TEXT,
        QPalette.ColorRole.Base:'#151515',QPalette.ColorRole.AlternateBase:'#202020',
        QPalette.ColorRole.Text:TEXT,QPalette.ColorRole.Button:'#262626',QPalette.ColorRole.ButtonText:TEXT,
        QPalette.ColorRole.Highlight:'#454545',QPalette.ColorRole.HighlightedText:'#ffffff',
        QPalette.ColorRole.ToolTipBase:'#2c2c2c',QPalette.ColorRole.ToolTipText:TEXT,
        QPalette.ColorRole.Link:'#dddddd',QPalette.ColorRole.Light:'#777777',QPalette.ColorRole.Mid:'#414141',QPalette.ColorRole.Dark:'#111111'}
    for role,value in values.items():palette.setColor(role,QColor(value))
    for role in (QPalette.ColorRole.Text,QPalette.ColorRole.WindowText,QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled,role,QColor('#757575'))
    app.setPalette(palette);app.setStyleSheet(STYLE);app.setProperty('filmDarkroomTheme',True)


def tool_icon(kind):
    """Small consistent line icons; every action keeps its visible text label."""
    icon=QIcon()
    for mode,state,color in ((QIcon.Mode.Normal,QIcon.State.Off,TEXT),(QIcon.Mode.Normal,QIcon.State.On,'#111111'),
                             (QIcon.Mode.Disabled,QIcon.State.Off,'#757575')):
        pix=QPixmap(24,24);pix.fill(Qt.GlobalColor.transparent);p=QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing);pen=QPen(QColor(color),1.5);pen.setCapStyle(Qt.PenCapStyle.RoundCap);pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen);p.setBrush(Qt.BrushStyle.NoBrush)
        def line(x,y,a,b):p.drawLine(QPointF(x,y),QPointF(a,b))
        if kind=='open':
            p.drawRoundedRect(QRectF(3,7,18,13),1,1);line(3,7,3,4);line(3,4,10,4);line(10,4,13,7)
        elif kind=='library':
            for x in (3,13):
                for y in (3,13):p.drawRect(QRectF(x,y,7,7))
        elif kind=='export':
            line(12,3,12,15);line(8,11,12,15);line(16,11,12,15);line(4,15,4,21);line(4,21,20,21);line(20,21,20,15)
        elif kind=='crop':
            line(7,2,7,17);line(7,17,22,17);line(2,7,17,7);line(17,7,17,22)
        elif kind=='straighten':
            line(3,18,21,18);line(4,10,20,6);p.drawEllipse(QPointF(4,10),2,2);p.drawEllipse(QPointF(20,6),2,2)
        elif kind=='perspective':
            points=[(7,4),(18,6),(21,20),(3,20)]
            for a,b in zip(points,points[1:]+points[:1]):line(*a,*b)
        elif kind=='frames':
            p.drawRect(QRectF(3,3,18,18));line(3,9,21,9);line(3,15,21,15);line(7,3,7,21)
        elif kind=='fit':
            for x,y,sx,sy in ((3,3,1,1),(21,3,-1,1),(3,21,1,-1),(21,21,-1,-1)):
                line(x,y,x+5*sx,y);line(x,y,x,y+5*sy)
        elif kind=='pixel':
            p.setFont(QFont('Segoe UI',9));p.drawText(QRectF(1,1,22,22),Qt.AlignmentFlag.AlignCenter,'1:1')
        elif kind=='sample':
            line(5,19,18,6);line(13,4,20,11);line(3,21,7,20)
        p.end();icon.addPixmap(pix,mode,state)
    return icon
