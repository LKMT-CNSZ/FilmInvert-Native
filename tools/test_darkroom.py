"""Darkroom layout migration, discoverable modes, scrolling and view persistence."""
from pathlib import Path
import os,sys,time,tempfile
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import native_app as ui
from PySide6.QtCore import Qt,QPointF,QPoint,QEvent
from PySide6.QtGui import QWheelEvent,QIcon,QPixmap
app=ui.QApplication.instance() or ui.QApplication([])
(ROOT/'build/tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build/tests',prefix='darkroom-') as temporary:
    profile=Path(temporary)/'profile';window=ui.MainWindow(profile);window.show();app.processEvents()
    window.nav_dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
    window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea,window.nav_dock)
    window.nav_dock.hide();window.settings.setValue('sentinel','preserve')
    window.close();app.processEvents();window.settings.remove(window.settings_version);window.settings.sync()
    window=ui.MainWindow(profile);window.show();app.processEvents()
    assert window.settings.value('sentinel')=='preserve'
    assert window.dockWidgetArea(window.nav_dock)==Qt.DockWidgetArea.BottomDockWidgetArea and window.nav_dock.isVisible()
    assert window.dockWidgetArea(window.controls_dock)==Qt.DockWidgetArea.RightDockWidgetArea
    errors=[];window.show_error=errors.append
    def idle():
        deadline=time.monotonic()+50
        while time.monotonic()<deadline:
            app.processEvents();time.sleep(.01)
            if window.job is None and not window.pending_command and not window.preview_timer.isActive() and not any(j.isRunning() for j in window.jobs):break
        else:raise AssertionError('darkroom UI timeout')
        assert not errors,errors
    window.open_path(str(ROOT/'samples/pro-strip-demo.tif'));idle()
    assert window.frames.count()==6 and window.frames.flow()==ui.QListView.Flow.LeftToRight and not window.frames.isWrapping()
    for width,height in ((1040,700),(1440,940)):
        window.resize(width,height);app.processEvents();idle()
        for action in (window.frame_action,window.geometry_action,window.straighten_action,window.perspective_action):
            widget=window.alignment_toolbar.widgetForAction(action)
            assert widget.isVisible() and widget.isEnabled() and window.alignment_toolbar.rect().contains(widget.geometry()),action.text()
        assert window.canvas.width()>400 and window.canvas.height()>200
        for index in range(window.tabs.count()):
            window.tabs.setCurrentIndex(index);app.processEvents()
            scroll=window.tabs.widget(index)
            assert scroll.widget().width()<=scroll.viewport().width(),(index,scroll.widget().width(),scroll.viewport().width())
        window.tabs.setCurrentIndex(0);app.processEvents()
    saved=window.p.copy()
    window.straighten_action.trigger();idle();assert window.geometry_dialog.canvas.line_mode
    window.geometry_dialog.reject();app.processEvents()
    window.perspective_action.trigger();idle();assert window.geometry_dialog.canvas.perspective_mode
    window.geometry_dialog.reject();app.processEvents()
    window.geometry_action.trigger();idle();assert not window.geometry_dialog.canvas.line_mode and not window.geometry_dialog.canvas.perspective_mode
    window.geometry_dialog.reject();app.processEvents();assert window.p==saved
    # Horizontal browsing is independent of processing and preserves photo RGB.
    strip=ui.FilmstripList();strip.setViewMode(ui.QListView.ViewMode.IconMode);strip.setFlow(ui.QListView.Flow.LeftToRight)
    strip.setWrapping(False);strip.setGridSize(ui.QSize(128,106));strip.resize(420,145)
    icon=window.frames.item(0).icon()
    for i in range(24):strip.addItem(ui.QListWidgetItem(icon,str(i+1)))
    strip.show();app.processEvents();assert strip.horizontalScrollBar().maximum()>0
    wheel=QWheelEvent(QPointF(200,60),QPointF(200,60),QPoint(0,0),QPoint(0,-120),Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
    app.sendEvent(strip.viewport(),wheel);app.processEvents();assert strip.horizontalScrollBar().value()>0
    assert icon.pixmap(ui.QSize(112,76),QIcon.Mode.Normal).toImage()==icon.pixmap(ui.QSize(112,76),QIcon.Mode.Selected).toImage()
    strip.close();strip.deleteLater()
    # Once migrated, deliberately hidden panels stay hidden across restarts.
    window.nav_dock.hide();window.loaded=False;window.close();app.processEvents()
    restored=ui.MainWindow(profile);restored.show();app.processEvents();assert not restored.nav_dock.isVisible()
    restored.restore_darkroom_layout();app.processEvents();assert restored.nav_dock.isVisible()
    restored.close();app.processEvents()
for widget in app.topLevelWidgets():widget.close();widget.deleteLater()
app.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()
print('PASS darkroom legacy dock migration, visible labeled mode entries at two sizes, cancel preserves grades, horizontal wheel/photo RGB, hidden state persistence and layout reset')
