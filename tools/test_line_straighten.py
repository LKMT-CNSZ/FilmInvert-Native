from pathlib import Path
import os,sys,math,copy,tempfile,json
os.environ['QT_QPA_PLATFORM']='offscreen';sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import geometry,numpy as np
from native_geometry import GeometryDialog
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt,QPointF
from PySide6.QtGui import QMouseEvent
app=QApplication.instance() or QApplication([])
for shape in ((800,1600),(1600,800)):
    h,w=shape
    for angle in (-12,-3,0,4,14):
        dx=.5;dy=math.tan(math.radians(angle))*dx*w/h
        points=[[.2,.4],[.7,.4+dy]]
        assert abs(geometry.line_straighten(points,shape)+angle)<1e-10
        assert abs(geometry.line_straighten(points[::-1],shape)+angle)<1e-10
    assert abs(geometry.line_straighten([[.4,.2],[.4,.8]],shape,'vertical'))<1e-10
for points in ([[.5,.5],[.5,.5]],[[0,0],[1,1]]):
    try:geometry.line_straighten(points,(800,1600))
    except ValueError:pass
    else:raise AssertionError('invalid / out of range line accepted')

def reference(correction,length,axis,shape,start=(.2,.2)):
    theta=math.radians((90 if axis=='vertical' else 0)-correction)
    h,w=shape
    return {'points':[list(start),[start[0]+math.cos(theta)*length/w,start[1]+math.sin(theta)*length/h]],'axis':axis}

for shape in ((800,1600),(1600,800)):
    size=min(shape)
    refs=[reference(4,size*.5,'horizontal',shape),reference(-2,size*.3,'vertical',shape),reference(1,size*.4,'horizontal',shape)]
    independent=(4*.5-2*.3+1*.4)/1.2
    result=geometry.lines_straighten(refs,shape)
    assert abs(result['angle']-independent)<1e-10 and len(result['used'])==3
    reverse=copy.deepcopy(refs)
    for row in reverse:row['points'].reverse()
    assert abs(geometry.lines_straighten(reverse,shape)['angle']-independent)<1e-10
    bad=refs+[{'points':[[.5,.5],[.5,.5]],'axis':'horizontal'},reference(30,size*.5,'horizontal',shape)]
    result=geometry.lines_straighten(bad,shape)
    assert abs(result['angle']-independent)<1e-10 and [r['index'] for r in result['rejected']]==[3,4]
    assert abs(geometry.lines_straighten(refs[:1],shape)['angle']-4)<1e-10
try:geometry.lines_straighten([],shape)
except ValueError:pass
else:raise AssertionError('empty multi-reference fit accepted')
preview={'pixels':np.full((300,600,3),128,np.uint8),'source_shape':(800,1600)}
dialog=GeometryDialog(preview,{'rotation':0,'straighten':0,'crop':geometry.FULL});dialog.show();app.processEvents()
dialog.line_button.setChecked(True);app.processEvents();canvas=dialog.canvas
def event(kind,p,button,buttons):
    bounds=canvas.item.boundingRect()
    point=canvas.mapFromScene(QPointF(p[0]*bounds.width(),p[1]*bounds.height()));local=QPointF(point)
    mouse=QMouseEvent(kind,local,local,button,buttons,Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(canvas.viewport(),mouse);app.processEvents()
from PySide6.QtCore import QEvent
event(QEvent.Type.MouseButtonPress,(.2,.4),Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton)
event(QEvent.Type.MouseMove,(.7,.5),Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton)
event(QEvent.Type.MouseButtonRelease,(.7,.5),Qt.MouseButton.LeftButton,Qt.MouseButton.NoButton)
assert abs(dialog.value['straighten']+math.degrees(math.atan2(.1*800,.5*1600)))<.1
before=dialog.value['straighten'];dialog.line_changed([[0,0],[1,1]])
assert dialog.value['straighten']==before and '超出' in dialog.line_message.text()
dialog.line_button.setChecked(False);assert not canvas.line_mode
dialog.turn(90);dialog.line_axis.setCurrentIndex(1);dialog.line_changed([[.3,.2],[.4,.7]])
assert abs(dialog.value['straighten']-(90-math.degrees(math.atan2(.5*1600,.1*800))))<1e-8
dialog.reject()
original={'rotation':0,'straighten':.3,'crop':geometry.FULL.copy()}
dialog=GeometryDialog(preview,original);dialog.show();app.processEvents()
dialog.line_button.setChecked(True);app.processEvents();canvas=dialog.canvas
def drag(a,b):
    event(QEvent.Type.MouseButtonPress,a,Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton)
    event(QEvent.Type.MouseMove,b,Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton)
    event(QEvent.Type.MouseButtonRelease,b,Qt.MouseButton.LeftButton,Qt.MouseButton.NoButton)

drag((.15,.25),(.8,.30));drag((.15,.65),(.75,.69))
assert len(canvas.reference_lines)==2
dialog.add_line_button.click();dialog.line_axis.setCurrentIndex(1)
drag((.9,.2),(.91,.8))
assert [r['axis'] for r in canvas.reference_lines]==['horizontal','horizontal','vertical']
# Independent source-pixel line-angle and length calculation from mouse endpoints.
terms=[]
for row in canvas.reference_lines:
    (x0,y0),(x1,y1)=row['points'];dx,dy=(x1-x0)*1600,(y1-y0)*800
    correction=((90 if row['axis']=='vertical' else 0)-math.degrees(math.atan2(dy,dx))+90)%180-90
    terms.append((correction,math.hypot(dx,dy)))
assert abs(dialog.value['straighten']-sum(a*w for a,w in terms)/sum(w for a,w in terms))<1e-10
assert '3 条有效' in dialog.line_message.text()
# Clicking a segment selects it, then dragging an endpoint adjusts without adding.
points=copy.deepcopy(canvas.reference_lines[0]['points'])
mid=np.mean(points,axis=0);drag(mid,mid)
assert canvas.selected_line==0 and len(canvas.reference_lines)==3
drag(points[1],(.8,.32));assert len(canvas.reference_lines)==3
assert canvas.reference_lines[0]['points']!=points
# Axis is per line; changing it rejects only that reference, with visible status.
dialog.line_axis.setCurrentIndex(1)
assert canvas.invalid_lines=={0} and '未采用' in dialog.line_list.itemText(0)
dialog.line_axis.setCurrentIndex(0);assert not canvas.invalid_lines
# Remove the selected reference and recompute with the remaining two.
dialog.delete_line_button.click();assert len(canvas.reference_lines)==2 and dialog.line_list.count()==2
assert abs(dialog.value['straighten']-geometry.lines_straighten(canvas.reference_lines,(800,1600))['angle'])<1e-10
before=dialog.value['straighten'];retained=copy.deepcopy(canvas.reference_lines)
dialog.line_button.setChecked(False);dialog.line_button.setChecked(True)
assert canvas.reference_lines==retained and dialog.value['straighten']==before
dialog.clear_lines_button.click()
assert not canvas.reference_lines and dialog.value['straighten']==before
assert not dialog.delete_line_button.isEnabled()
# Invalid references preserve the last usable angle and can be removed.
dialog.line_axis.setCurrentIndex(0);drag((.5,.5),(.5,.5))
assert canvas.invalid_lines=={0} and dialog.value['straighten']==before
dialog.delete_line_button.click();assert not canvas.reference_lines
drag((.1,.3),(.8,.35));dialog.refresh()
captured=[];dialog.applied.connect(captured.append);dialog.apply_result()
assert set(captured[0])=={'rotation','straighten','crop'} and original['straighten']==.3
final=captured[0]
dialog=GeometryDialog(preview,final);dialog.line_button.setChecked(True)
dialog.line_changed([[.2,.2],[.8,.3]]);dialog.turn(90)
assert not dialog.canvas.reference_lines
dialog.reset_geometry();assert dialog.value['straighten']==0 and not dialog.canvas.reference_lines
dialog.reject()
# The combined angle uses the existing project/queue path, verified as actual TIFF.
import desktop_model as model,studio_worker
from studio_store import StudioStore
from tiffio import write_tiff_rgb16
from tiffread import read_tiff
root=Path(__file__).resolve().parents[1];(root/'build/tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=root/'build/tests',prefix='multiline-') as folder:
    folder=Path(folder);yy,xx=np.mgrid[:180,:360]
    source=np.stack([xx/360,yy/180,(xx+yy)/540],axis=-1).astype(np.float32)
    path=folder/'source.tif';write_tiff_rgb16(path,source)
    opened=model.open_image(path,input_mode='single');p=opened['params'];p.update(final,mode='positive')
    model.save_project(folder/'multi.filmproj',p);reopened=model.open_project(folder/'multi.filmproj')
    assert reopened['params']['straighten']==final['straighten']
    store=StudioStore(folder/'profile');asset=store.capture(model.project_snapshot(p))
    job=store.enqueue([asset],folder/'output')[0];assert studio_worker.execute(store,job)=='completed'
    actual=read_tiff(Path(store.get_job(job)['destination'])/'frame-0001.tif')
    expected=(np.clip(model.core.process(model.engine.STATE['full'],model.params(p)),0,1)*65535+.5).astype(np.uint16)
    assert np.array_equal(actual,expected)
# Dispose standalone test windows/timers while QApplication still exists. Relying
# on interpreter shutdown can destroy Qt wrappers in an indeterminate order.
for widget in app.topLevelWidgets():widget.close();widget.deleteLater()
app.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()
print('PASS multi-reference source-length weighting, mixed axes/reversed lines, real Qt add/select/endpoint/delete/clear, invalid rejection, rotation/reset/cancel, project reopen and exact queued TIFF')
