"""Naming, contact pagination, self-contained transfer and native workflow regressions."""
from pathlib import Path
import os,sys,tempfile,copy,json,threading,time,zipfile,hashlib
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import native_app as ui
import numpy as np
from PIL import Image
import export_policy as policy,project_transfer as transfer,contact_sheet,desktop_model as model,studio_worker
from studio_store import StudioStore,sha256
from tiffio import write_tiff_rgb16
from tiffread import read_tiff
app=ui.QApplication.instance() or ui.QApplication([])
def rejected(fn):
 try:fn()
 except (ValueError,KeyError,zipfile.BadZipFile):return
 raise AssertionError('invalid input accepted')
for template in ('../{source}','{source.__class__}','{frame!r}','{frame:99999d}','{date:%Y}','{missing}','bad:', 'x\n', '{frame'):
 rejected(lambda:policy.validate(template))
rejected(lambda:policy.names('a.tif',[0,1],policy.options('{source}')))
rejected(lambda:policy.filename('a.tif',1,policy.options('CON')))
config=policy.options('{roll}-{source}-{date}-{frame:04d}','卷 A','20261023')
assert policy.names('原片.tif',[0,1],config)==['卷 A-原片-20261023-0001.tif','卷 A-原片-20261023-0002.tif']
assert policy.filename('a.tif',1,None)=='frame-0001.tif'
assert policy.filename('a.tif',1,None,'a-positive.tif')=='a-positive.tif'
print('PASS naming whitelist, padding, Unicode, frozen date, duplicate/device/path/format rejection and legacy defaults')
(ROOT/'build/tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build/tests',prefix='delivery-') as tmp:
 folder=Path(tmp);source=folder/'中文 原片.tif'
 y,x=np.mgrid[:40,:84];image=np.stack([.15+.6*x/84,.2+.4*y/40,.25+.4*x/84],axis=-1).astype(np.float32)
 write_tiff_rgb16(source,image)
 frames=[{'id':str(i),'box':[i*4,0,(i+1)*4,40]} for i in range(21)]
 manual={'rotation':0,'frames':frames}
 opened=model.open_image(source,input_mode='strip',manual=manual)
 p=opened['params'];p.update(mode='positive',black=0,white=1,local_edits=[{'kind':'brush','points':[[.5,.5]],'radius':.1,'ev':-.5}],rotation=90)
 model.preview(p);snapshot=model.project_snapshot(p);snapshot['_export']=config
 identity=model.source_identity(source);original=source.read_bytes()
 contacts=contact_sheet.export(folder,snapshot)
 assert contacts['pages']==2 and contacts['frames']==21
 index=json.loads((Path(contacts['folder'])/'contact-index.json').read_text('utf-8'))
 assert index['frames'][20]['page']==2 and index['frames'][0]['filename'].endswith('0001.tif')
 with Image.open(Path(contacts['folder'])/'contact-001.png') as im:
  assert im.size==(1600,1900) and im.info.get('icc_profile')
  # Cell centers correspond to the actual sRGB render, not raw input or monitor output.
  arr,fp=studio_worker.frame_parameters(snapshot,0)
  rendered=model.core.process(model.engine.downscale(arr,600),fp,'srgb')
  thumb=Image.fromarray((np.clip(rendered,0,1)*255+.5).astype(np.uint8));thumb.thumbnail((370,300),Image.Resampling.LANCZOS)
  px,py=20+(370-thumb.width)//2,70+(300-thumb.height)//2
  assert np.array_equal(np.asarray(im)[py:py+thumb.height,px:px+thumb.width],np.asarray(thumb))
 cancel=threading.Event();cancel.set();before=set(folder.iterdir())
 rejected(lambda:contact_sheet.export(folder,snapshot,cancel))
 assert set(folder.iterdir())==before
 # Legacy/default and custom roll export names, duplicate template preflight has no output directory.
 rejected(lambda:model.export_roll(folder,p,False,cancel,lambda *a:None,policy.options('{source}')))
 assert set(folder.iterdir())==before
 cancel.clear();roll=model.export_roll(folder,p,False,cancel,lambda *a:None,config)
 assert len(list(Path(roll['folder']).glob('*.tif')))==21
 assert (Path(roll['folder'])/index['frames'][0]['filename']).is_file()
 print('PASS edited PNG contact pixels/ICC, 21-frame two-page order, contact cancellation cleanup, custom roll names and collision preflight')
 store=StudioStore(folder/'profile');asset=store.capture(snapshot);store.metadata(asset,4,'pick','队列卷')
 cfg=policy.options('{roll}-{source}-{frame:03d}',date='20261023')
 job=store.enqueue([asset],folder/'queue',False,cfg)[0]
 captured=store.get_job(job)['snapshot'];assert captured['_export']['roll']=='队列卷'
 cfg['template']='changed';store.metadata(asset,0,'none','changed')
 assert studio_worker.execute(store,job)=='completed'
 final=Path(store.get_job(job)['destination'])/policy.filename(source,1,captured['_export'])
 assert final.is_file();digest=sha256(final);mtime=final.stat().st_mtime_ns
 store.status(job,'paused');store.retry([job]);assert studio_worker.execute(store,job)=='completed'
 assert sha256(final)==digest and final.stat().st_mtime_ns==mtime
 print('PASS queue names and roll labels frozen at enqueue, verified resume does not rewrite completed TIFF')
 # Pack original and project; move away original before reopening from Chinese/space location.
 model.open_image(source,input_mode='strip',manual=manual);model.preview(p);snapshot=model.project_snapshot(p)
 archive=folder/'完整工程.filmtransfer';labels={'rating':5,'flag':'pick','roll':'胶卷 A'}
 transfer.pack(snapshot,archive,labels)
 rejected(lambda:transfer.pack(snapshot,archive,labels))
 target=folder/'迁移 中文 空格';target.mkdir()
 relocated=transfer.unpack(archive,target)
 source.rename(source.with_suffix('.held'))
 restored=model.open_project(relocated['project'])
 assert restored['params']['local_edits']==model.params(p).local_edits and relocated['metadata']==labels
 migrated=Path(model.engine.STATE['path']);assert migrated.read_bytes()==original
 assert migrated.resolve().is_relative_to(Path(relocated['folder']).resolve())
 assert model.source_identity(migrated)['sha256']==identity['sha256']
 # Reject tampering, duplicate members, traversal and wrong version, leaving existing files untouched.
 with zipfile.ZipFile(archive) as z:parts={n:z.read(n) for n in z.namelist()}
 for case in ('tamper','path','version','duplicate'):
  bad=folder/(case+'.filmtransfer');data=copy.deepcopy(parts)
  if case=='tamper':data['project.filmproj']+=b' '
  if case in ('path','version'):
   manifest=json.loads(data['manifest.json'])
   if case=='path':manifest['original']='../escaped.tif'
   else:manifest['version']=999
   data['manifest.json']=json.dumps(manifest).encode()
  with zipfile.ZipFile(bad,'w') as z:
   for name,payload in data.items():z.writestr(name,payload)
   if case=='duplicate':z.writestr('project.filmproj',data['project.filmproj'])
  existing=set(target.iterdir());rejected(lambda:transfer.unpack(bad,target));assert set(target.iterdir())==existing
 cancel.set();existing=set(target.iterdir());rejected(lambda:transfer.unpack(archive,target,cancel));assert set(target.iterdir())==existing
 assert not (folder/'escaped.tif').exists()
 print('PASS self-contained source/project/local edit relocation, source hash and labels, existing archive protection, tamper/traversal/version/duplicate rejection and cancel cleanup')
 # Native commands restore metadata and document; contact export does not change current frame/params.
 window=ui.MainWindow(folder/'native');errors=[];window.show_error=errors.append
 def idle():
  end=time.monotonic()+45
  while time.monotonic()<end:
   app.processEvents();time.sleep(.005)
   if window.job is None and not window.pending_command and not window.preview_timer.isActive() and not window.preview_pending:break
  else:raise AssertionError('native timeout')
  assert not errors,errors
 window.unpack_from(str(archive),str(target));idle()
 assert window.current_metadata()==labels
 window.naming_edit.setText('{source}-{frame:04d}');before=copy.deepcopy(window.p);frame=model.engine.STATE['frame_index']
 window.contact_to(str(folder/'native-contact'));idle()
 assert window.p==before and model.engine.STATE['frame_index']==frame
 window.pack_to(str(folder/'native.filmtransfer'));idle();assert (folder/'native.filmtransfer').is_file()
 window.loaded=False;window.preview_timer.stop();window.history_timer.stop();window.close();app.processEvents()
 print('PASS native import restores document/labels, contact preserves edits/frame, native transfer export')
print('PASS delivery regressions')
