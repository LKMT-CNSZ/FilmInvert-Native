"""Real Qt workflow controls, real child preparation/export and automatic report."""
from pathlib import Path
import os,sys,tempfile,time,copy,json,hashlib
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import native_app as ui
import numpy as np
from PIL import Image
from PySide6.QtCore import QItemSelectionModel
app=ui.QApplication.instance() or ui.QApplication([])
(ROOT/'build/tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build/tests',prefix='batch-ui-') as temporary:
    folder=Path(temporary);paths=[]
    for i in range(9):
        path=folder/(f'照片{i}.png');paths.append(path);y,x=np.mgrid[:90,:120]
        Image.fromarray(np.stack([x+40+i*2,y+50,(x+y)//2+40],axis=-1).astype(np.uint8)).save(path)
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    window=ui.MainWindow(folder/'profile');window.show();errors=[];window.show_error=errors.append
    def idle(exports=False):
        deadline=time.monotonic()+120
        stable=0
        while time.monotonic()<deadline:
            app.processEvents();time.sleep(.01)
            busy=window.job is not None or window.pending_command or window.preview_timer.isActive() or any(j.isRunning() for j in window.jobs) or window.photo_worker is not None or window.batch_controller.running()
            if exports:busy=busy or window.queue_controller.running() or any(j['status']=='queued' for j in window.studio_store.jobs())
            if not busy:stable+=1
            else:stable=0
            if stable>65:break # includes delayed automatic report scheduling
        else:raise AssertionError('workflow timeout')
        assert not errors,errors
    window.open_dropped_paths([str(p) for p in paths]);idle();identifier=window.current_batch_id
    window.open_workflow();dialog=window.workflow_dialog;assert dialog.identifier==identifier and dialog.tabs.count()==3
    assert dialog.table.rowCount()==9 and window.photos.count()==9
    dialog.stop.setCurrentIndex(dialog.stop.findData('prepare'));dialog.output.setText(str(folder/'output'));assert dialog.save_settings()
    dialog.run('pipeline');assert not dialog.batches.isEnabled() and not dialog.tabs.isEnabled()
    dialog.create_current();assert window.current_batch_id==identifier
    idle();data=window.batch_store.get(identifier)
    assert all(i['snapshot'] and i['valid'] and not i['error'] for i in data['items']),[i['error'] for i in data['items']]
    assert all(i['snapshot']['frame_count']==1 for i in data['items'])
    # Batch controls assign two groups, preserving every item selection across refreshes.
    def select(rows):
        dialog.table.clearSelection()
        for row in rows:dialog.table.selectionModel().select(dialog.table.model().index(row,1),QItemSelectionModel.SelectionFlag.Select|QItemSelectionModel.SelectionFlag.Rows)
    select(range(4));dialog.item_group.setText('卷 A');dialog.set_items()
    select(range(4,9));dialog.item_group.setText('卷 B');dialog.set_items();dialog.refresh_groups()
    for row in range(dialog.group_table.rowCount()):dialog.group_table.cellWidget(row,1).setCurrentIndex(1)
    dialog.save_groups();dialog.table.clearSelection()
    first=copy.deepcopy(window.p);first['curve']=[[0,0],[.5,.64],[1,1]];window.set_parameters(first);idle()
    dialog.method.setCurrentIndex(dialog.method.findData('sync'))
    for key,check in dialog.field_checks.items():check.setChecked(key=='tone')
    assert dialog.save_settings()
    question=ui.QMessageBox.question;ui.QMessageBox.question=lambda *a:ui.QMessageBox.StandardButton.Yes
    try:dialog.run('style')
    finally:ui.QMessageBox.question=question
    idle();data=window.batch_store.get(identifier)
    assert all(not i['error'] for i in data['items']),[i['error'] for i in data['items']]
    assert window.p['curve']==first['curve'],'old UI overwrote the new batch result during reload'
    assert all(i['snapshot']['frames']['0']['curve']==first['curve'] for i in data['items'][:4])
    assert all(i['snapshot']['frames']['0']['curve']==[[0.,0.],[1.,1.]] for i in data['items'][4:])
    dialog.proof(4);app.processEvents();assert len(dialog.proof_dialog.cells)==4
    dialog.proof_dialog.before.setChecked(True);app.processEvents();assert all(not c.pixmap().isNull() for c in dialog.proof_dialog.cells)
    dialog.proof_dialog.close();dialog.proof(9);app.processEvents();assert len(dialog.proof_dialog.cells)==9;dialog.proof_dialog.close()
    dialog.review();dialog.enqueue();data=window.batch_store.get(identifier)
    assert len([i for i in data['items'] if i.get('job')])==9
    frozen=[window.studio_store.get_job(i['job'])['snapshot'] for i in data['items']]
    dialog.naming.setText('future-{frame:03d}');assert dialog.save_settings()
    assert frozen==[window.studio_store.get_job(i['job'])['snapshot'] for i in data['items']]
    dialog.start_exports();idle(True);data=window.batch_store.get(identifier)
    assert all(window.studio_store.get_job(i['job'])['status']=='completed' for i in data['items'])
    assert data.get('report'),data.get('report_error')
    summary=json.loads((Path(data['report']['path'])/'batch-summary.json').read_text('utf-8'))
    assert summary['completed_frames']==9 and len(summary['contact_pages'])==1 and not summary['failures']
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==value for p,value in hashes.items())
    window.loaded=False;window.close();app.processEvents()
    restored=ui.MainWindow(folder/'profile');restored.show();restored.open_workflow();app.processEvents()
    assert restored.workflow_dialog.identifier==identifier and restored.workflow_dialog.table.rowCount()==9
    assert any('需检查' in restored.workflow_dialog.table.item(i,5).text() for i in range(9))
    restored.loaded=False;restored.close();app.processEvents()
print('PASS Qt batch create/settings/groups/sync/current reload, real isolated serial preparation and nine export processes, 4/9 proof before/after, frozen queue, automatic sRGB contact/report, reopened identity checks, source unchanged')
