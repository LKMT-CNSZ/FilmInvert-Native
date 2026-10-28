"""Run one isolated, repeatable large-film operation against a supplied app.

Private inputs/results stay at the explicitly requested output location.
"""
from pathlib import Path
import argparse,hashlib,json,os,sys,time

parser=argparse.ArgumentParser()
parser.add_argument('--app',required=True);parser.add_argument('--project',required=True)
parser.add_argument('--output',required=True);parser.add_argument('--mode',choices=('decode','ui','queue'),required=True)
args=parser.parse_args();sys.path.insert(0,str(Path(args.app).resolve()))
os.environ['QT_QPA_PLATFORM']='offscreen'
out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
project=Path(args.project).resolve();data=json.loads(project.read_text('utf-8'))
from runtime_metrics import memory
import numpy as np
report={'mode':args.mode,'app':str(Path(args.app).resolve()),'stages':{}}
def timed(label,work):
    start=time.perf_counter();result=work()
    report['stages'][label]={'seconds':time.perf_counter()-start,'memory':memory()}
    print(label,json.dumps(report['stages'][label]),flush=True);return result
def digest(a):
    h=hashlib.sha256()
    for y in range(0,len(a),64):h.update(np.ascontiguousarray(a[y:y+64]).data)
    return h.hexdigest()
if args.mode=='decode':
    import loader
    loaded=timed('decode',lambda:loader.load(data['source']['path']))
    report.update(shape=list(loaded.linear.shape),dtype=str(loaded.linear.dtype),sha256=digest(loaded.linear))
elif args.mode=='ui':
    import native_app as ui,desktop_model as model
    from PySide6.QtGui import QFontDatabase,QIcon
    from PySide6.QtCore import QSize
    app=ui.QApplication([]);QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
    window=ui.MainWindow(out/'profile');errors=[];window.show_error=errors.append;window.show()
    def idle():
        deadline=time.monotonic()+240
        while time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
            if window.job is None and not window.pending_command and not window.preview_timer.isActive() and not any(j.isRunning() for j in window.jobs):break
        else:raise AssertionError('UI operation timeout')
        assert not errors,errors
    def step(work):work();idle()
    timed('open_project',lambda:step(lambda:window.open_project_path(str(project))))
    assert window.frames.count()==12
    for key,value in data['frames'].items():assert model.params(model.engine.STATE['frame_params'][int(key)]).__dict__==value
    report['thumb_sha256']=[hashlib.sha256(bytes(window.frames.item(i).icon().pixmap(QSize(112,76)).toImage().constBits())).hexdigest() for i in range(12)]
    window.keep_check.setChecked(False)
    for index in (11,5,0):
        timed('frame_'+str(index+1),lambda index=index:step(lambda:window.go_frame(index)))
        assert window.p==data['frames'][str(index)]
    timed('pixel_view',lambda:step(window.pixel_view))
    window.canvas.horizontalScrollBar().setValue(120);idle()
    timed('fit_view',lambda:step(window.fit_view))
    # The input-framing dialog builds an overview of the complete 150 MP source.
    overview=timed('source_overview',model.layout_editor)
    report['overview_sha256']=digest(overview['pixels'])
    report['source_sha256']=model.engine.STATE['native_source']['sha256']
    report['params']=window.p;report['frames']=window.frames.count()
    report['memory']=memory();window.loaded=False;window.close();app.processEvents()
else:
    import desktop_model as model,studio_worker
    from studio_store import StudioStore
    from tiffread import read_tiff
    from PIL import Image
    store=StudioStore(out/'profile');asset=store.capture(model.read_project(project))
    job=store.enqueue([asset],out/'exports',False)[0]
    assert timed('worker',lambda:studio_worker.execute(store,job))=='completed'
    path=Path(store.get_job(job)['destination'])/'frame-0001.tif'
    a=read_tiff(path);report.update(shape=list(a.shape),sha256=digest(a),output=str(path))
    with Image.open(path) as im:assert im.info.get('icc_profile');report['icc_sha256']=hashlib.sha256(im.info['icc_profile']).hexdigest()
    report['source_sha256']=model.engine.STATE['native_source']['sha256']
report.setdefault('memory',memory())
(out/(args.mode+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('PASS '+args.mode,flush=True)
