"""Lightweight startup surface; import processing modules off the Qt event thread."""
from pathlib import Path
import argparse,sys,time,traceback,ctypes,os
from PySide6.QtCore import Qt,QObject,QThread,Signal,QTimer,QRectF
from PySide6.QtGui import QPainter,QImage,QPixmap,QColor,QFont,QLinearGradient,QIcon
from PySide6.QtWidgets import QApplication,QWidget
from app_paths import select_profile

DURATION=1.2

def animations_enabled():
    if QApplication.platformName()!='windows':return False
    value=ctypes.c_int(1)
    if os.name=='nt':
        ok=ctypes.windll.user32.SystemParametersInfoW(0x1042,0,ctypes.byref(value),0)
        return bool(value.value) if ok else True
    return True

class ImportWindow(QThread):
    ready=Signal(object);failed=Signal(str)
    def run(self):
        try:
            from native_app import MainWindow
            self.ready.emit(MainWindow)
        except Exception:self.failed.emit(traceback.format_exc())

class StartupSplash(QWidget):
    skipped=Signal()
    def __init__(self,logo,english=False):
        super().__init__(None,Qt.WindowType.SplashScreen|Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle('FilmInvert Native');self.setWindowIcon(QIcon(str(logo)))
        self.logo=QPixmap(str(logo));self.progress=0.;self.english=english
        screen=QApplication.primaryScreen();area=screen.availableGeometry() if screen else None
        self.resize(min(620,area.width()-32) if area else 620,min(370,area.height()-32) if area else 370)
        if area:self.move(area.center()-self.rect().center())
        ratio=self.devicePixelRatioF();self.positive=QImage(round(self.width()*ratio),round(self.height()*ratio),QImage.Format.Format_RGB32)
        self.positive.setDevicePixelRatio(ratio);self.positive.fill(QColor('#000000'))
        p=QPainter(self.positive);p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w=self.width();h=self.height();side=min(190,h*.52);top=(h-270)/2
        p.drawPixmap(QRectF((w-side)/2,top,side,side),self.logo,QRectF(self.logo.rect()))
        font=QFont('Segoe UI');font.setPixelSize(30);font.setWeight(QFont.Weight.Medium);p.setFont(font)
        brand='FilmInvert';bw=p.fontMetrics().horizontalAdvance(brand)
        small=QFont('Segoe UI');small.setPixelSize(14);p.setFont(small);nw=p.fontMetrics().horizontalAdvance('Native')
        left=(w-bw-nw-12)/2;baseline=top+side+30
        p.setFont(font);p.setPen(QColor('#f5f5f5'));p.drawText(round(left),round(baseline),brand)
        p.setFont(small);p.setPen(QColor('#b9b9b9'));p.drawText(round(left+bw+12),round(baseline),'Native')
        small.setFamily('Microsoft YaHei');small.setPixelSize(12);p.setFont(small);p.setPen(QColor('#aaaaaa'))
        p.drawText(QRectF(0,baseline+10,w,24),Qt.AlignmentFlag.AlignHCenter|Qt.AlignmentFlag.AlignTop,'Bring negatives to life' if english else '让负片重现色彩');p.end()
        self.negative=self.positive.copy();self.negative.invertPixels()
        p=QPainter(self.negative);p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply);p.fillRect(self.rect(),QColor('#8d4b23'));p.end()
    def paintEvent(self,event):
        p=QPainter(self);p.drawImage(self.rect(),self.negative)
        t=max(0.,min(1.,(self.progress-.08)/.67));t=t*t*(3-2*t)
        edge=(-.07+1.14*t)*self.width();feather=self.width()*.1
        start=max(0,int(edge-feather/2));end=min(self.width(),int(edge+feather/2)+1)
        if start>0:
            p.save();p.setClipRect(0,0,min(start,self.width()),self.height());p.drawImage(self.rect(),self.positive);p.restore()
        if end>start:
            ratio=self.devicePixelRatioF();tile=QImage(round((end-start)*ratio),round(self.height()*ratio),QImage.Format.Format_ARGB32_Premultiplied);tile.setDevicePixelRatio(ratio);tile.fill(0)
            q=QPainter(tile);q.translate(-start,0);q.drawImage(self.rect(),self.positive)
            mask=QLinearGradient(edge-feather/2,0,edge+feather/2,0);mask.setColorAt(0,QColor(0,0,0,255));mask.setColorAt(1,QColor(0,0,0,0))
            q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn);q.fillRect(QRectF(start,0,end-start,self.height()),mask);q.end();p.drawImage(start,0,tile)
    def mousePressEvent(self,event):self.skipped.emit();event.accept()
    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape:self.skipped.emit();event.accept()
        else:super().keyPressEvent(event)

class StartupController(QObject):
    def __init__(self,app,bundle,args,window_factory=None,clock=time.perf_counter):
        super().__init__(app);self.app=app;self.bundle=Path(bundle);self.args=args;self.factory=window_factory;self.clock=clock
        self.window=None;self.worker=None;self.splash=None;self.error=None;self.finished=False;self.skipped=False;self.shown=False
        self.profile=select_profile(self.bundle,args.profile)
        self.timer=QTimer(self);self.timer.setTimerType(Qt.TimerType.PreciseTimer);self.timer.setInterval(16);self.timer.timeout.connect(self.tick)
    def start(self):
        self.started=self.clock();logo=Path(__file__).resolve().parent/'assets/startup-mascot.png'
        if animations_enabled() and logo.is_file():
            from PySide6.QtCore import QSettings
            english=QSettings(str(self.profile/'settings.ini'),QSettings.Format.IniFormat).value('language','zh_CN')=='en'
            self.splash=StartupSplash(logo,english);self.splash.skipped.connect(self.skip);self.splash.show();self.splash.repaint();self.timer.start()
        else:self.skipped=True
        if self.factory:QTimer.singleShot(0,lambda:self.construct(self.factory))
        else:
            self.worker=ImportWindow(self);self.worker.ready.connect(self.construct);self.worker.failed.connect(self.fail);self.worker.start()
    def construct(self,factory):
        try:self.window=factory(self.profile);self.tick()
        except Exception:self.fail(traceback.format_exc())
    def show_editor(self):
        if self.shown or self.window is None:return
        self.shown=True;self.window.show()
        if self.splash:self.splash.raise_()
        if self.args.image:QTimer.singleShot(0,lambda:self.window.open_path(self.args.image))
    def tick(self):
        if self.finished:return
        p=max(0.,min(1.,(self.clock()-self.started)/DURATION))
        if self.splash:
            self.splash.progress=p;self.splash.update()
        if self.window is None:return
        if p>=.86 or self.skipped:self.show_editor()
        if self.splash and not self.skipped:
            t=max(0.,min(1.,(p-.86)/.14));self.splash.setWindowOpacity(1-t*t*(3-2*t))
        if p>=1 or self.skipped:
            self.show_editor();self.timer.stop();self.finished=True
            if self.splash:self.splash.close()
    def skip(self):self.skipped=True;self.tick()
    def fail(self,error):
        self.error=error;self.timer.stop();self.finished=True
        if self.splash:self.splash.close()
        self.app.exit(1)

def run(argv,bundle,window_factory=None):
    parser=argparse.ArgumentParser();parser.add_argument('image',nargs='?');parser.add_argument('--profile');args=parser.parse_args(argv)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs,True)
    app=QApplication(sys.argv[:1]);app.setApplicationName('FilmInvert Native');app.setOrganizationName('FilmInvert')
    controller=StartupController(app,bundle,args,window_factory);app.startup_controller=controller;controller.start()
    result=app.exec()
    if controller.worker:controller.worker.wait()
    if controller.error:raise RuntimeError(controller.error)
    return result
