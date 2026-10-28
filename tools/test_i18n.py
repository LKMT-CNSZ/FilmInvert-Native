"""Real Qt language switching with edited photos, open dialogs and frozen jobs."""
from pathlib import Path
import ast,copy,hashlib,json,os,string,sys,tempfile,time
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import native_app as ui
import i18n
from i18n_en import EN
from PySide6.QtWidgets import QLabel,QAbstractButton,QComboBox,QTabWidget
from PySide6.QtCore import Qt,QSettings,QCoreApplication
from PySide6.QtGui import QFontDatabase
from unittest.mock import patch

# Every explicitly marked source message has an English entry with matching fields.
fields=lambda s:sorted((f,spec,conv) for _,f,spec,conv in string.Formatter().parse(s) if f is not None)
for key,value in EN.items():assert fields(key)==fields(value),(key,value)
for path in ROOT.glob('native_*.py'):
    for node in ast.walk(ast.parse(path.read_text('utf-8'))):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in ('_t','_tf') and node.args and isinstance(node.args[0],ast.Constant):
            key=node.args[0].value
            if any('\u4e00'<=c<='\u9fff' for c in key):assert key in EN,(path.name,node.lineno,key)
app=ui.QApplication.instance() or ui.QApplication([])
# The offscreen Windows plugin does not enumerate system fonts automatically.
for name in ('segoeui.ttf','msyh.ttc'):
    font=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/name
    if font.exists():QFontDatabase.addApplicationFont(str(font))
artifacts=ROOT/'build/i18n-validation';artifacts.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='i18n-',dir=artifacts) as tmp:
    profile=Path(tmp)/'中文 profile'
    window=ui.MainWindow(profile);window.show();errors=[];window.show_error=errors.append
    def idle():
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            app.processEvents();time.sleep(.01)
            if window.job is None and not window.pending_command and not window.preview_timer.isActive() and not any(j.isRunning() for j in window.jobs) and window.photo_worker is None:break
        else:raise AssertionError('UI worker timeout')
        assert not errors,errors
    assert window.tabs.tabText(0)=='基础'
    assert QCoreApplication.translate('QDialogButtonBox','Cancel')=='取消'
    paths=[ROOT/'samples/pro-strip-demo.tif',ROOT/'samples/calibration-demo.png']
    # Find an actual bundled second sample without assuming a demo image extension.
    if not paths[1].is_file():paths[1]=next(p for p in (ROOT/'samples').glob('*') if p.suffix.lower() in ('.png','.tif') and p!=paths[0])
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    window.open_dropped_paths(list(map(str,paths)));idle()
    window.open_library_asset(window.photo_assets[0]['id']);idle()
    window.edit_parameter('exposure',.45,True);idle();window.commit_history();window.save_session()
    asset=window.current_workflow_asset()
    window.studio_store.enqueue([asset],Path(tmp)/'queued-output',False)
    ui.model.export_image(Path(tmp)/'before-language.tif',window.p)
    window.open_workflow();batch=window.workflow_dialog;app.processEvents()
    batch.item_group.setText('精选');batch.filters.search.setText('胶卷 A')
    window.open_studio();studio=window.studio_dialog;app.processEvents()
    studio.roll.setText('胶卷 A') if hasattr(studio,'roll') else None
    window.open_geometry();idle();geometry=window.geometry_dialog
    before=copy.deepcopy(window.p);history=copy.deepcopy(window.history)
    assets=copy.deepcopy(window.studio_store.assets()) if hasattr(window.studio_store,'assets') else None
    batch_before=copy.deepcopy(window.batch_store.get(window.current_batch_id))
    jobs=copy.deepcopy(window.studio_store.jobs())
    # A language change must not submit parameter edits, preview jobs or queue actions.
    with patch.object(window,'edit_parameter',side_effect=AssertionError('language changed editing state')):
        window.language_actions['en'].trigger();app.processEvents()
    assert i18n.language()=='en' and window.language_combo.currentData()=='en'
    assert QCoreApplication.translate('QDialogButtonBox','Cancel')=='Cancel'
    assert QCoreApplication.translate('QFileDialog','Unknown untranslated label')=='Unknown untranslated label'
    assert window.tabs.tabText(0)=='Basic'
    assert window.photo_filters.search.placeholderText()=='Search filename / group / path'
    assert window.combos['mode'].currentText()=='Color negative'
    assert window.combos['mode'].currentData()=='color_negative'
    assert batch.tabs.tabText(0)=='1 · List & preparation'
    assert studio.tabs.tabText(0)=='Photo library'
    assert geometry.windowTitle()=='Crop, straighten & perspective · Non-destructive edits'
    assert batch.item_group.text()=='精选' and batch.filters.search.text()=='胶卷 A'
    assert window.p==before and window.history==history and window.job is None
    assert window.batch_store.get(window.current_batch_id)==batch_before
    assert window.studio_store.jobs()==jobs
    assert jobs and jobs[0]['status']=='queued'
    ui.model.export_image(Path(tmp)/'after-language.tif',window.p)
    from tiffread import read_tiff
    import numpy as np
    assert np.array_equal(read_tiff(str(Path(tmp)/'before-language.tif')),read_tiff(str(Path(tmp)/'after-language.tif')))
    assert window.settings.value('language')=='en'
    # Localized getItem dialogs return the original choice to existing ID mappings.
    choices=[i18n.tr('未标记'),i18n.tr('精选'),i18n.tr('弃选')]
    def getItem(*args,**kwargs):return 'Pick',True
    with patch.object(ui.QInputDialog,'getItem',getItem):
        choice,ok=i18n.ui(ui.QInputDialog.getItem,None,'','',choices,0,False)
    assert ok and {'未标记':'none','精选':'pick','弃选':'reject'}[choice]=='pick'
    # Formatting preserves a user string even if it exactly matches a source key.
    label=i18n.ui(QLabel,i18n.trf('第 {0} 帧','精选'))
    assert label.text()=='Frame 精选'
    i18n.ui(label.setText,'精选')
    for code in ('zh_CN','en','zh_CN','en'):
        window.switch_language(code);app.processEvents()
        assert label.text()=='精选' and window.p==before
        assert window.combos['mode'].currentData()=='color_negative'
    # Newly opened dialogs and dynamic messages use the currently selected language.
    geometry.reject();window.open_frames();idle();frames=window.frame_dialog
    assert frames.windowTitle()=='Input & frames · Source scan overview'
    frames.reject();window.open_local();idle();local=window.local_dialog
    assert local.windowTitle()=='Local editing · Clone / exposure / gradient'
    local.reject();window.open_calibration();idle();chart=window.chart_dialog
    assert chart.windowTitle()=='Chart calibration · Linear RGB correction'
    chart.reject()
    # Check actual interface labels; user fields and the Chinese language name are allowed.
    remaining=[]
    for root in (window,batch,studio,geometry,frames,local,chart):
        for widget in root.findChildren(QLabel)+root.findChildren(QAbstractButton):
            text=widget.text()
            if any('\u4e00'<=c<='\u9fff' for c in text):remaining.append((type(widget).__name__,text))
    remaining=[r for r in remaining if r[1] not in ('简体中文','精选','胶卷 A') and not any(str(p.name) in r[1] for p in paths) and batch_before['name'] not in r[1]]
    assert not remaining,remaining
    for width,height in ((1040,700),(1440,940)):
        window.resize(width,height);app.processEvents()
        for index in range(window.tabs.count()):
            window.tabs.setCurrentIndex(index);app.processEvents()
            scroll=window.tabs.widget(index)
            assert scroll.widget().width()<=scroll.viewport().width(),(width,index,scroll.widget().width(),scroll.viewport().width())
        for action in (window.frame_action,window.geometry_action,window.straighten_action,window.perspective_action):
            widget=window.alignment_toolbar.widgetForAction(action)
            assert widget.isVisible() and window.alignment_toolbar.rect().contains(widget.geometry()),(width,action.text())
    window.tabs.setCurrentIndex(0)
    window.resize(1440,940);app.processEvents()
    assert window.grab().save(str(artifacts/'main-en.png'))
    window.switch_language('zh_CN');app.processEvents()
    assert window.tabs.tabText(0)=='基础' and batch.tabs.tabText(0)=='1 · 清单与准备'
    assert window.p==before and window.history==history
    window.language_combo.setCurrentIndex(window.language_combo.findData('en'));app.processEvents()
    window.loaded=False;window.close();app.processEvents()
    restored=ui.MainWindow(profile)
    assert restored.tabs.tabText(0)=='Basic' and restored.language_actions['en'].isChecked()
    restored.close();app.processEvents()
    settings=QSettings(str(profile/'settings.ini'),QSettings.Format.IniFormat);settings.setValue('language','invalid');settings.sync()
    fallback=ui.MainWindow(profile);assert fallback.tabs.tabText(0)=='基础';fallback.close();app.processEvents()
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==v for p,v in hashes.items())
print('PASS English catalog, live menu/toolbar/open-dialog switching, stable IDs, Chinese user text, edits/history/batches/jobs retained, saved language and safe fallback, source hashes unchanged')
