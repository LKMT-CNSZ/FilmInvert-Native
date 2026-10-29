"""Startup lifetime, cold background import, UI-thread construction and handoff."""
from pathlib import Path
import os,sys,time,tempfile,argparse
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
if (ROOT/'vendor').exists():sys.path.insert(0,str(ROOT/'vendor'))
from unittest.mock import patch
from PySide6.QtWidgets import QApplication,QWidget
from PySide6.QtCore import QThread
from PySide6.QtGui import QFontDatabase
import native_startup as startup
app=QApplication([]);app.setQuitOnLastWindowClosed(False)
for file in ('C:/Windows/Fonts/segoeui.ttf','C:/Windows/Fonts/msyh.ttc'):
    if Path(file).is_file():QFontDatabase.addApplicationFont(file)
folder=ROOT/'build/startup-tests';folder.mkdir(parents=True,exist_ok=True)
def events_until(check):
    deadline=time.monotonic()+30
    while not check() and time.monotonic()<deadline:app.processEvents();time.sleep(.005)
    assert check(),'Startup timeout'
with tempfile.TemporaryDirectory(dir=folder) as tmp,patch.object(startup,'animations_enabled',return_value=True):
    args=argparse.Namespace(profile=str(Path(tmp)/'profile'),image=None)
    # Cold import happens in the worker, while the real window belongs to Qt's GUI thread.
    controller=startup.StartupController(app,ROOT,args);controller.start()
    assert controller.splash.isVisible() and controller.window is None
    events_until(lambda:controller.window is not None or controller.error is not None)
    assert controller.error is None,controller.error
    assert controller.window.thread()==app.thread() and controller.worker is not None
    events_until(lambda:controller.finished);assert controller.shown and controller.window.isVisible()
    controller.worker.wait();controller.window.loaded=False;controller.window.close()
    # Deterministic timing: reveal original -> positive -> editor within 1.2 seconds.
    clock=[0.];opened=[]
    class Editor(QWidget):
        def __init__(self,profile):
            super().__init__();assert QThread.currentThread()==app.thread()
        def open_path(self,path):opened.append(path)
    args.image='example-negative.tif'
    c=startup.StartupController(app,ROOT,args,Editor,lambda:clock[0]);c.start();events_until(lambda:c.window is not None)
    assert not c.window.isVisible() and startup.DURATION==1.2
    clock[0]=.96;c.tick();assert not c.window.isVisible()
    c.splash.repaint();c.splash.grab().save(str(ROOT/'build/startup-positive.png'))
    clock[0]=1.04;c.tick();app.processEvents();assert c.window.isVisible() and opened==[args.image]
    clock[0]=1.2;c.tick();assert c.finished and not c.splash.isVisible() and not c.timer.isActive();c.window.close()
    # Longer initialization does not add another 1.2-second wait.
    clock[0]=0.;c=startup.StartupController(app,ROOT,args,Editor,lambda:clock[0]);c.start();clock[0]=2.
    events_until(lambda:c.finished);assert c.shown;c.window.close()
    clock[0]=0.;c=startup.StartupController(app,ROOT,args,Editor,lambda:clock[0]);c.start();c.skip()
    events_until(lambda:c.finished);assert c.shown;c.window.close()
    with patch.object(startup,'animations_enabled',return_value=False):
        c=startup.StartupController(app,ROOT,args,Editor);c.start();events_until(lambda:c.finished)
        assert c.splash is None and c.shown;c.window.close()
    def broken(profile):raise RuntimeError('startup fixture failure')
    c=startup.StartupController(app,ROOT,args,broken);c.start();events_until(lambda:c.error is not None)
    assert 'startup fixture failure' in c.error and c.finished and not c.timer.isActive()
app.processEvents()
print('PASS 1.2s mascot startup, cold worker import, GUI-thread window creation, image argument once, handoff/skip, slow initialization, system preference and failure cleanup')
