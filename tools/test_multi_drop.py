"""Real Qt multi-URL drops: all photos retained, discoverable, edits preserved."""
from pathlib import Path
import os,sys,tempfile,time,copy,hashlib
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import native_app as ui
from native_widgets import dropped_paths
from PySide6.QtCore import QMimeData,QUrl,QPoint,QPointF,Qt
from PySide6.QtGui import QDragEnterEvent,QDropEvent
from PIL import Image
app=ui.QApplication.instance() or ui.QApplication([])
(ROOT/'build/tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build/tests',prefix='multi-drop-') as temporary:
    folder=Path(temporary)/'中文 空格';folder.mkdir();(folder/'另一个目录').mkdir()
    paths=[folder/'同名.png',folder/'另一个目录/同名.png',folder/'第三张.tif',folder/'第四张.png']
    for i,path in enumerate(paths):Image.new('RGB',(112+i*4,84),(80+i*20,70,60)).save(path)
    bad=folder/'非照片.txt';bad.write_text('not a photo',encoding='utf-8')
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    window=ui.MainWindow(folder/'profile');window.show();errors=[];window.show_error=errors.append
    window.open_mode.setCurrentIndex(window.open_mode.findData('single'))
    def idle():
        end=time.monotonic()+60
        while time.monotonic()<end:
            app.processEvents();time.sleep(.005)
            if window.job is None and not window.pending_command and not window.preview_timer.isActive() and not any(j.isRunning() for j in window.jobs) and window.photo_worker is None:break
        else:raise AssertionError('Qt timeout')
        assert not errors,errors
    def drop(widget,values):
        mime=QMimeData();mime.setUrls([value if isinstance(value,QUrl) else QUrl.fromLocalFile(str(value)) for value in values])
        enter=QDragEnterEvent(QPoint(25,25),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        app.sendEvent(widget,enter)
        event=QDropEvent(QPointF(25,25),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        app.sendEvent(widget,event);app.processEvents();return enter.isAccepted(),event.isAccepted()
    # Single-file canvas drop keeps the familiar direct editor behavior.
    assert drop(window.canvas.viewport(),[ROOT/'samples/pro-light-demo.tif'])==(True,True)
    idle();assert window.loaded
    window.edit_parameter('exposure',.47,True);idle()
    original=window.info['path'];before=copy.deepcopy(window.p)
    window.open_studio();dialog=window.studio_dialog
    dialog.search.setText('no such filename');dialog.minimum.setCurrentIndex(5);dialog.hide_rejected.setChecked(True)
    dialog.page=20;dialog.tabs.setCurrentIndex(1);dialog.hide()
    values=[bad,paths[0],paths[1],paths[2],paths[0],folder/'missing.png']
    dialog.show();assert drop(dialog,values)==(True,True);idle()
    assert dialog.isVisible() and dialog.tabs.currentIndex()==0
    assert dialog.only_imported.isChecked() and dialog.page==0 and dialog.library.rowCount()==3
    assert dialog.search.text()=='' and dialog.minimum.currentIndex()==0 and not dialog.hide_rejected.isChecked()
    assert len(dialog.selected_assets())==3 and '新增 3' in dialog.message.text() and '忽略 2' in dialog.message.text()
    assert window.info['path']==original and window.p==before
    assets=window.studio_store.assets();by_path={a['path']:a for a in assets}
    assert all(str(p.resolve()) in by_path for p in paths[:3])
    assert len(assets)==4
    assert by_path[original]['snapshot']['frames']['0']==before
    # Open the second dropped photo, proving later files are independently usable.
    target=by_path[str(paths[1].resolve())]['id'];dialog.library.clearSelection()
    for i in range(dialog.library.rowCount()):
        if dialog.library.item(i,0).data(Qt.ItemDataRole.UserRole)==target:dialog.library.selectRow(i)
    dialog.open_selected();idle();assert window.info['path']==str(paths[1].resolve())
    window.edit_parameter('exposure',.82,True);idle();saved=copy.deepcopy(window.studio_store.get_asset(target)['snapshot'])
    window.studio_store.metadata(target,4,'pick','keep')
    # Dropping into the library deduplicates without replacing edits or metadata.
    window.open_studio();assert drop(dialog,[paths[1],paths[3]])==(True,True);idle()
    assert dialog.library.rowCount()==2 and '新增 1' in dialog.message.text() and '已有 / 重复 1' in dialog.message.text()
    asset=window.studio_store.get_asset(target)
    assert asset['snapshot']==saved and (asset['rating'],asset['flag'],asset['roll'])==(4,'pick','keep')
    assert window.p['exposure']==.82
    dialog.only_imported.setChecked(False);assert dialog.library.rowCount()==5
    # Whole-window drop now presents the files in the bottom photo navigator.
    assert drop(window,[paths[0],paths[2]])==(True,True);idle();assert window.photos.count()==2
    assert window.info['path']==str(paths[0].resolve()) and len(window.studio_store.assets())==5
    assert window.studio_store.get_asset(target)['snapshot']['frames']['0']['exposure']==.82
    # Remote URLs never enter the importer, including when they precede a file.
    mime=QMimeData();mime.setUrls([QUrl('https://example.invalid/photo.png'),QUrl.fromLocalFile(str(paths[2]))])
    assert [Path(p).resolve() for p in dropped_paths(mime)]==[paths[2].resolve()]
    assert drop(window.canvas.viewport(),[QUrl('https://example.invalid/photo.png')])==(False,False)
    assert drop(dialog,[bad,folder/'missing.png'])==(True,True);idle()
    assert dialog.library.rowCount()==0 and '忽略 2' in dialog.message.text()
    assert len(window.studio_store.assets())==5
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==digest for p,digest in hashes.items())
    window.loaded=False;window.close();app.processEvents()
print('PASS real canvas/window/library multi-drop, single direct open, same-name files, filter reset, duplicate/invalid accounting, later photo opens, edits/metadata/source unchanged, remote URL rejection')
