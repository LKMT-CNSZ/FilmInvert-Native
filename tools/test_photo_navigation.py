"""Multi-file bottom navigation, independent grades and two-level film navigation."""
from pathlib import Path
import os,sys,tempfile,time,copy
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import native_app as ui,desktop_model as model
import numpy as np
from PIL import Image
from PySide6.QtCore import QMimeData,QUrl,QPoint,QPointF,Qt,QSize
from PySide6.QtGui import QDragEnterEvent,QDropEvent,QIcon
app=ui.QApplication.instance() or ui.QApplication([])
(ROOT/'build/tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build/tests',prefix='photo-nav-') as temporary:
    folder=Path(temporary);paths=[]
    for i in range(20):
        path=folder/(f'照片-{i+1:02d}.png');paths.append(path)
        y,x=np.mgrid[:180,:250];pixels=np.stack([(x+i*8)%255,y+40,(x//2+y//2+i*5)%255],axis=-1).astype(np.uint8)
        Image.fromarray(pixels).save(path)
    window=ui.MainWindow(folder/'profile');window.show();errors=[];window.show_error=errors.append
    window.open_mode.setCurrentIndex(window.open_mode.findData('single'))
    def idle():
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
            if window.job is None and not window.pending_command and not window.preview_timer.isActive() and not any(j.isRunning() for j in window.jobs) and window.photo_worker is None:break
        else:raise AssertionError('photo navigation timeout')
        assert not errors,errors
    mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    app.sendEvent(window.canvas.viewport(),QDragEnterEvent(QPoint(20,20),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier))
    drop=QDropEvent(QPointF(20,20),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    app.sendEvent(window.canvas.viewport(),drop);idle();assert drop.isAccepted()
    assert window.photos.count()==20 and window.photos.isVisible() and not window.frames.isVisible()
    assert window.photos.currentRow()==0 and window.current_photo_row()==0 and window.studio_dialog is None
    assert all(row['id'] in window.photo_images for row in window.photo_assets)
    assert '1 / 20' in window.frame_label.text() and not window.keep_check.isVisible()
    # Keep-parameters is deliberately ON; crossing source files must still restore
    # independent exposure, geometry and local edits instead of cloning a grade.
    p=copy.deepcopy(window.p);p.update(exposure=.34,straighten=.5,rotation=90,
        local_edits=[dict(kind='brush',points=[[.5,.5]],radius=.1,ev=.2)])
    window.set_parameters(p);idle();first=model.params(window.p).__dict__
    window.photos.setCurrentRow(1);idle();assert window.p['exposure']==0 and window.p['rotation']==0 and not window.p['local_edits']
    window.edit_parameter('exposure',-.27,True);idle();second=copy.deepcopy(window.p)
    window.prev_button.click();idle();assert window.p==first and window.photos.currentRow()==0
    window.next_button.click();idle();assert window.p==second
    window.photos.setCurrentRow(19);idle();assert window.photos.horizontalScrollBar().value()>0
    assert window.current_photo_row()==19 and window.photos.count()==20
    # ROI/original display must never replace a full-photo positive thumbnail.
    def icons():return [window.photos.item(i).icon().pixmap(QSize(112,76)).toImage() for i in range(20)]
    before=icons();window.pixel_view();idle();assert icons()==before
    window.canvas.horizontalScrollBar().setValue(60);idle();assert icons()==before
    window.fit_view();idle();window.original_action.setChecked(True);window.request_preview();idle();assert icons()==before
    window.original_action.setChecked(False);window.request_preview();idle()
    for i in range(20):
        icon=window.photos.item(i).icon()
        assert icon.pixmap(QSize(112,76),QIcon.Mode.Normal).toImage()==icon.pixmap(QSize(112,76),QIcon.Mode.Selected).toImage()
    # An old thumbnail result arriving after a new batch cannot touch new rows.
    old_generation=window.photo_generation;old_id=window.photo_assets[0]['id']
    window.open_mode.setCurrentIndex(window.open_mode.findData('auto'))
    window.open_dropped_paths([str(ROOT/'samples/pro-strip-demo.tif'),str(paths[1])]);idle()
    assert window.photos.count()==2 and window.frames.count()==6 and window.photos.isVisible()
    before_icon=window.photos.item(0).icon().cacheKey()
    window.photo_thumbnail_ready(old_generation,old_id,str(paths[0]),'late')
    assert window.photos.item(0).icon().cacheKey()==before_icon
    window.navigation_mode.setCurrentIndex(1);app.processEvents()
    assert window.frames.isVisible() and not window.photos.isVisible() and window.keep_check.isVisible()
    window.go_frame(2);idle();assert window.info['strip']['index']==2
    window.navigation_mode.setCurrentIndex(0);window.next_button.click();idle();assert window.p==second
    assert not window.navigation_mode.model().item(1).isEnabled()
    window.prev_button.click();idle();assert window.info['strip']['index']==2 and window.photos.count()==2
    # A failed source keeps its neighbours clickable, including with no document
    # loaded yet; a delayed original thumbnail cannot replace a positive image.
    row=window.current_photo_row();identifier=window.photo_assets[row]['id'];key=window.photos.item(row).icon().cacheKey()
    window.photo_thumbnail_ready(window.photo_generation,identifier,str(paths[0]),'late original')
    assert window.photos.item(row).icon().cacheKey()==key
    bad=folder/'damaged.png';bad.write_bytes(b'not an image')
    window.open_dropped_paths([str(bad),str(paths[1])])
    end=time.monotonic()+60
    while time.monotonic()<end:
        app.processEvents();time.sleep(.005)
        if window.job is None and not window.pending_command and window.photo_worker is None:break
    assert errors and window.photos.count()==2 and window.photos.isEnabled()
    errors.clear();window.photos.setCurrentRow(1);idle();assert window.p==second
    # Explicitly opening a separate source returns to the normal frame UI.
    window.open_path(str(ROOT/'samples/pro-light-demo.tif'));idle();assert not window.photo_assets and not window.photos.isVisible()
    window.loaded=False;window.close();app.processEvents()
print('PASS actual 20-photo drop/bottom icons/scroll, independent grade and geometry/local edits, prior/next, ROI/original icon isolation, stale thumbnail rejection, mixed-file six-frame navigation and saved frame restore')
