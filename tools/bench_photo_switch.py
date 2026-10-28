"""Real Qt source-switch timings against an explicitly supplied application."""
from pathlib import Path
import argparse,os,sys,time,json,copy,hashlib
parser=argparse.ArgumentParser();parser.add_argument('--app',required=True);parser.add_argument('--output',required=True)
parser.add_argument('--sources',nargs='+',required=True);args=parser.parse_args()
sys.path.insert(0,str(Path(args.app).resolve()));os.environ['QT_QPA_PLATFORM']='offscreen'
import native_app as ui,desktop_model as model
from runtime_metrics import memory
import numpy as np
app=ui.QApplication([]);out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
window=ui.MainWindow(out/'profile');window.show();errors=[];window.show_error=errors.append
stages=[];loads=[];real_load=model.load;real_install=model.engine.install_loaded
def load(*a,**k):
    t=time.perf_counter();value=real_load(*a,**k);loads.append({'source':str(a[0]),'seconds':time.perf_counter()-t});return value
def install(*a,**k):
    t=time.perf_counter();value=real_install(*a,**k);stages.append(time.perf_counter()-t);return value
model.load=load;model.engine.install_loaded=install
def idle():
    end=time.monotonic()+180
    while time.monotonic()<end:
        app.processEvents();time.sleep(.003)
        if window.job is None and not window.pending_command and not window.preview_timer.isActive() and not any(j.isRunning() for j in window.jobs) and window.photo_worker is None and not window.batch_controller.running():break
    else:raise AssertionError('switch timeout')
    assert not errors,errors
paths=[str(Path(p).resolve()) for p in args.sources]
originals={p:model.source_identity(p)['sha256'] for p in paths}
window.open_dropped_paths(paths);idle()
records=[];pixels={};grades={}
for index in range(len(paths)):
    window.go_photo(index);idle();p=copy.deepcopy(window.p);p.update(mode='bw_negative',exposure=index*.13)
    window.set_parameters(p);idle();grades[index]=model.params(window.p).__dict__
    pixels[index]=hashlib.sha256(window.canvas.pixels.tobytes()).hexdigest()
for number,index in enumerate(([0,1]*3) if len(paths)==2 else [0,1,2,3,2,3,2,3]):
    if index==window.current_photo_row():continue
    n=len(loads);t=time.perf_counter();window.photos.setCurrentRow(index);idle();elapsed=time.perf_counter()-t
    assert window.p==grades[index]
    assert hashlib.sha256(window.canvas.pixels.tobytes()).hexdigest()==pixels[index]
    records.append({'index':index,'seconds':elapsed,'new_decodes':len(loads)-n})
assert all(model.source_identity(p)['sha256']==digest for p,digest in originals.items())
result={'app':args.app,'sources':originals,'timings':records,'decode_calls':loads,'initialization_seconds':stages,
        'pixels':pixels,'memory':memory(),'scope':'Each measurement includes actual Qt switch/load/project restore/final fit preview. Timings are single-machine observations, not guarantees. Warm return visits measured after each source has an independent grade.'}
if hasattr(model,'DOCUMENTS'):result['cache']=model.DOCUMENTS.statistics()
(out/'SWITCH.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
window.loaded=False;window.close();app.processEvents();print('PASS actual Qt source switches, independent grades, same displayed pixels and original SHA256')
print(json.dumps({'switches':records,'memory':result['memory']},ensure_ascii=False),flush=True)
