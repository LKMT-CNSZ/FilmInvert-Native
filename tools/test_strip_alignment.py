"""Physical border estimation, rotated input ROI, v6 restore and exact worker."""
from pathlib import Path
import os,sys,math,tempfile,copy,json
os.environ['QT_QPA_PLATFORM']='offscreen';ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np,strip,geometry,strip_alignment as alignment,desktop_model as model,studio_worker
import project_transfer,studio_archive
from studio_store import StudioStore
from tiffio import write_tiff_rgb16
from tiffread import read_tiff
from native_frames import FrameLayoutDialog
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt,QPointF,QEvent
from PySide6.QtGui import QMouseEvent
app=QApplication.instance() or QApplication([])
# Known physical parallel edges, no dependency on a scene horizon.
source=np.full((1200,1000,3),.008,np.float32)
for y in range(100,1100):
    left=round(250+math.tan(math.radians(1.3))*(y-600))
    source[y,left:left+420]=[.55,.35,.2]
rows=[{'id':'film-1','box':[250,100,670,1100]}]
candidate=alignment.suggest(source,rows)[0]
assert candidate['available'] and abs(candidate['angle']-1.3)<.08,candidate
assert not alignment.suggest(np.full_like(source,.2),rows)[0]['available']
assert not alignment.suggest(source,[{'id':'tiny','box':[250,100,252,102]}])[0]['available']
yy,xx=np.mgrid[:400,:500];ramp=np.stack([(xx+.5)/500,(yy+.5)/400,np.full_like(xx,.3)],axis=-1).astype(np.float32)
manual={'rotation':90,'frames':[{'id':'ramp','box':[50,40,420,350],'angle':2.}]}
layout=strip.manual_layout(ramp,manual);_,_,actual=strip.extract_one(ramp,layout,0)
oh,ow=actual.shape[:2]
for x,y in ((.2,.3),(.5,.6),(.8,.7)):
    ix,iy=int(x*ow),int(y*oh);u,v=(iy+.5)/oh,1-(ix+.5)/ow
    sx,sy=geometry.source_point(u,v,(310,370),{'straighten':2.})
    assert np.allclose(actual[iy,ix,:2],[(50+sx*370)/500,(40+sy*310)/400],atol=.004)
(ROOT/'build/tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build/tests',prefix='alignment-') as temporary:
    folder=Path(temporary);file=folder/'source.tif';write_tiff_rgb16(file,source)
    opened=model.open_image(file,input_mode='single');p=opened['params'];p['exposure']=.2
    spec={'mode':'strip','manual':{'rotation':90,'frames':[dict(rows[0],angle=candidate['angle'],base=[.55,.35,.2])]}}
    result=model.reframe(spec,p);p=result['params']
    assert model.source_region()['angle']==candidate['angle']
    project=folder/'aligned.filmproj';model.save_project(project,p);data=model.read_project(project)
    assert data['version']==8 and data['input']['manual']['frames'][0]['angle']==candidate['angle']
    before=model.preview(p)['pixels'];opened=model.open_project(project)
    assert np.array_equal(model.preview(opened['params'])['pixels'],before)
    # Explicit format gate and wrong-layout rollback.
    bad=copy.deepcopy(data);bad['version']=5;model.write_json(folder/'bad.filmproj',bad)
    try:model.open_project(folder/'bad.filmproj')
    except ValueError:pass
    else:raise AssertionError('input angle silently accepted in old project format')
    # Frozen queue includes the input angle and correct source region in v4 recipe.
    store=StudioStore(folder/'profile');asset=store.capture(model.project_snapshot(p));job=store.enqueue([asset],folder/'output')[0]
    assert studio_worker.execute(store,job)=='completed'
    output=Path(store.get_job(job)['destination'])/'frame-0001.tif'
    reference=model.core.process(model.engine.STATE['full'],model.params(p))
    assert np.array_equal(read_tiff(output),(np.clip(reference,0,1)*65535+.5).astype(np.uint16))
    recipe=json.loads(Path(str(output)+'.json').read_text('utf-8'))
    assert recipe['version']==6 and recipe['source_region']['angle']==candidate['angle']
    project_transfer.pack(model.project_snapshot(p),folder/'aligned.filmtransfer')
    moved=project_transfer.unpack(folder/'aligned.filmtransfer',folder/'moved')
    restored=model.open_project(moved['project'])
    assert model.source_region()['angle']==candidate['angle'] and restored['params']==p
    studio_archive.backup(store,folder/'aligned.filmbackup')
    assert Path(studio_archive.restore(store,folder/'aligned.filmbackup')).is_file()
    # UI preview then explicit adoption, undo/redo, cancel, stable angle metadata.
    preview=model.layout_editor();dialog=FrameLayoutDialog(preview)
    dialog.preview['alignment_candidates']=[dict(candidate,box=dialog.rows[0]['box'])]
    old=copy.deepcopy(dialog.rows);dialog.show_suggestion();assert dialog.rows==old
    dialog.adopt_suggestion(False);assert dialog.rows[0]['angle']==candidate['angle']
    dialog.set_input_angle(-.7);assert dialog.rows[0]['angle']==-.7
    dialog.travel(-1);assert dialog.rows[0]['angle']==candidate['angle']
    dialog.travel(1);assert dialog.rows[0]['angle']==-.7
    captured=[];dialog.applied.connect(captured.append);dialog.apply_result()
    assert captured[0]['manual']['frames'][0]['angle']==-.7
    # Multi-line input assistance: independent references per stable frame ID.
    three=[{'id':'left-1','box':[250,100,670,600]},
           {'id':'left-2','box':[250,650,670,1150]},
           {'id':'right-1','box':[710,100,990,600]}]
    opened=model.reframe({'mode':'strip','manual':{'rotation':0,'frames':three}},p);p=opened['params']
    initial=model.project_signature(model.project_snapshot(p))
    preview=model.layout_editor();dialog=FrameLayoutDialog(preview);dialog.show();app.processEvents()
    def interior_color():
        rect=dialog.canvas.item.boundingRect()
        point=dialog.canvas.mapFromScene(QPointF(.3*rect.width(),.2*rect.height()))
        return dialog.canvas.viewport().grab().toImage().pixelColor(point).getRgb()
    original_color=interior_color()
    dialog.line_button.setChecked(True);app.processEvents();canvas=dialog.canvas
    def event(kind,point,button,buttons):
        rect=canvas.item.boundingRect()
        local=QPointF(canvas.mapFromScene(QPointF(point[0]*rect.width(),point[1]*rect.height())))
        QApplication.sendEvent(canvas.viewport(),QMouseEvent(kind,local,local,button,buttons,Qt.KeyboardModifier.NoModifier));app.processEvents()
    def drag(a,b):
        event(QEvent.Type.MouseButtonPress,a,Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton)
        event(QEvent.Type.MouseMove,b,Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton)
        event(QEvent.Type.MouseButtonRelease,b,Qt.MouseButton.LeftButton,Qt.MouseButton.NoButton)
    drag((.25,.1),(.26,.48));drag((.65,.12),(.659,.48))
    assert len(canvas.reference_lines)==2
    dialog.add_line_button.click();dialog.line_axis.setCurrentIndex(1)
    drag((.3,.3),(.60,.294))
    assert [r['axis'] for r in canvas.reference_lines]==['vertical','vertical','horizontal']
    terms=[]
    for row in canvas.reference_lines:
        (x0,y0),(x1,y1)=row['points'];dx,dy=(x1-x0)*1000,(y1-y0)*1200
        correction=((90 if row['axis']=='vertical' else 0)-math.degrees(math.atan2(dy,dx))+90)%180-90
        terms.append((correction,math.hypot(dx,dy)))
    expected=sum(a*w for a,w in terms)/sum(w for a,w in terms)
    assert abs(dialog.rows[0]['angle']-expected)<1e-10
    assert interior_color()==original_color  # Reference/ROI overlays never fill over source pixels.
    assert dialog.candidate['source']=='manual' and dialog.strip_button.isEnabled()
    # Segment selection and endpoint editing must not create another reference.
    first=copy.deepcopy(canvas.reference_lines[0]['points']);mid=np.mean(first,axis=0);drag(mid,mid)
    assert canvas.selected_line==0
    drag(first[1],(.263,.48));assert len(canvas.reference_lines)==3
    dialog.line_axis.setCurrentIndex(1)
    assert canvas.invalid_lines=={0} and '未采用' in dialog.line_list.itemText(0)
    dialog.line_axis.setCurrentIndex(0);assert not canvas.invalid_lines
    refs0=copy.deepcopy(canvas.reference_lines);angle0=dialog.rows[0]['angle']
    dialog.frames.setCurrentRow(1);assert not canvas.reference_lines and 'angle' not in dialog.rows[1]
    drag((.25,.6),(.24,.94));assert len(canvas.reference_lines)==1
    refs1=copy.deepcopy(canvas.reference_lines);angle1=dialog.rows[1]['angle']
    dialog.frames.setCurrentRow(0);assert canvas.reference_lines==refs0
    assert dialog.rows[0]['angle']==angle0 and dialog.rows[1]['angle']==angle1
    dialog.strip_button.click()
    assert dialog.rows[0]['angle']==dialog.rows[1]['angle']==angle0 and 'angle' not in dialog.rows[2]
    assert dialog.reference_sets['left-2']['lines']==refs1
    dialog.travel(-1);assert dialog.rows[0]['angle']==angle0 and dialog.rows[1]['angle']==angle1
    dialog.travel(1);assert dialog.rows[1]['angle']==angle0
    # Reference edits and input angles travel together through dialog history.
    dialog.delete_line_button.click();assert len(canvas.reference_lines)==2
    dialog.travel(-1);assert canvas.reference_lines==refs0
    dialog.travel(1);assert len(canvas.reference_lines)==2
    retained=copy.deepcopy(canvas.reference_lines);angle=dialog.rows[0]['angle']
    dialog.clear_lines_button.click();assert not canvas.reference_lines and dialog.rows[0]['angle']==angle
    dialog.travel(-1);assert canvas.reference_lines==retained
    dialog.set_input_angle(-.3);dialog.frames.setCurrentRow(1);dialog.frames.setCurrentRow(0)
    assert dialog.rows[0]['angle']==-.3  # Selecting a frame never silently reapplies its references.
    # Auto suggestions and manual references remain distinct sources of adoption.
    dialog.preview['alignment_candidates']=[dict(candidate,box=dialog.rows[0]['box'],angle=.42)]
    dialog.show_suggestion();dialog.adopt_button.click();assert dialog.rows[0]['angle']==.42
    assert canvas.reference_lines==retained
    dialog.recalculate_input_lines();angle=dialog.rows[0]['angle'];dialog.commit()
    dialog.line_button.setChecked(False);dialog.line_button.setChecked(True)
    assert canvas.reference_lines==retained and dialog.rows[0]['angle']==angle
    dialog.rotation.setCurrentIndex(dialog.rotation.findData(90))
    assert canvas.reference_lines==retained and dialog.rows[0]['angle']==angle
    # An invalid zero-length reference is displayed/rejected, without changing angle.
    dialog.add_line_button.click();drag((.5,.5),(.5,.5))
    assert canvas.invalid_lines=={len(retained)} and dialog.rows[0]['angle']==angle
    dialog.delete_line_button.click();assert canvas.reference_lines==retained
    assert model.project_signature(model.project_snapshot(p))==initial
    captured=[];dialog.applied.connect(captured.append);dialog.apply_result()
    spec=captured[0]
    assert all('references' not in row and 'points' not in row for row in spec['manual']['frames'])
    opened=model.reframe(spec,p);p=opened['params'];p['mode']='positive'
    model.save_project(folder/'multi-input.filmproj',p);saved=model.read_project(folder/'multi-input.filmproj')
    assert saved['version']==8 and saved['input']['manual']['rotation']==spec['manual']['rotation']
    for actual,row in zip(saved['input']['manual']['frames'],spec['manual']['frames']):
        assert all(actual[key]==value for key,value in row.items())
    model.open_project(folder/'multi-input.filmproj')
    snapshot=model.project_snapshot(p);multi_store=StudioStore(folder/'multi-profile')
    asset=multi_store.capture(snapshot);job=multi_store.enqueue([asset],folder/'multi-output')[0]
    assert studio_worker.execute(multi_store,job)=='completed'
    destination=Path(multi_store.get_job(job)['destination'])
    for i in range(3):
        array,fp=studio_worker.frame_parameters(snapshot,i)
        expected=(np.clip(model.core.process(array,fp),0,1)*65535+.5).astype(np.uint16)
        assert np.array_equal(read_tiff(destination/f'frame-{i+1:04d}.tif'),expected)
    # Cancel after reference edits must not alter the actual loaded project.
    before=model.project_signature(model.project_snapshot(p))
    cancelled=FrameLayoutDialog(model.layout_editor());cancelled.line_button.setChecked(True)
    cancelled.input_line_changed([[.2,.1],[.21,.8]]);cancelled.reject()
    assert model.project_signature(model.project_snapshot(p))==before
for widget in app.topLevelWidgets():widget.close();widget.deleteLater()
app.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()
print('PASS physical borders/decline, source ramp/v6/v4/transfer/backup; multiline Qt draw/select/adjust/axis/delete/clear, frame isolation/history, current/same-strip adoption, auto/manual coexistence, project reopen and 3 exact queued TIFFs')
