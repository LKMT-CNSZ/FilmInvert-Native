"""Batch A/B/C integration: resume, selective sync, history, templates, frozen exports."""
from pathlib import Path
import os,sys,tempfile,copy,json,hashlib,sqlite3,subprocess
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from PIL import Image
import desktop_model as model,core,studio_worker,batch_engine
from batch_store import BatchStore,snapshot_key
from studio_store import StudioStore,sha256
from tiffread import read_tiff
from studio_archive import backup,restore

def rejected(fn):
    try:fn()
    except (ValueError,KeyError):return
    raise AssertionError('invalid action accepted')
(ROOT/'build/tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build/tests',prefix='batch-') as temporary:
    folder=Path(temporary);studio=StudioStore(folder/'profile');store=BatchStore(studio)
    paths=[]
    y,x=np.mgrid[:80,:110]
    for index in range(4):
        path=folder/(f'胶卷{index//2}-照片{index}.png');paths.append(path)
        image=np.stack([60+x*.6+index*7,45+y*.8+index*3,30+x*.3+y*.3+index*9],axis=-1).astype(np.uint8)
        Image.fromarray(image).save(path)
    bad=folder/'损坏.png';bad.write_bytes(b'not an image');paths.append(bad)
    ids=studio.import_paths(paths)['ids'];batch=store.create(ids,'两卷批次')
    config=store.get(batch)['config'];config['output']=str(folder/'output');store.configure(batch,config)
    events=[]
    def run(operation,ids=None,overwrite=False,callback=None):
        token=store.start(batch,operation,ids,overwrite)
        batch_engine.execute(studio,batch,token,callback or (lambda *a:events.append(a)))
        return store.get(batch)
    def pause_after_first(step,n,total,name):
        if n==1:store.run_update(batch,cancel=True)
    data=run('check',callback=pause_after_first)
    assert data['status']=='paused' and data['items'][0]['valid'] and not data['items'][1]['valid']
    data=run('pipeline');items=data['items']
    assert all(i['snapshot'] and not i['error'] and i['snapshot']['frame_count']==1 for i in items[:4])
    assert items[4]['error'] and items[4]['failed_step']=='prepare'
    assert model.engine.STATE.get('native_loaded') is None
    assert len({tuple(i['snapshot']['frames']['0']['base']) for i in items[:4]})>1
    previews=[i['preview_before'] for i in items[:4]];timestamps=[Path(p).stat().st_mtime_ns for p in previews]
    # Completed preparation is not decoded again when resuming/retrying.
    original=batch_engine.fresh_snapshot;batch_engine.fresh_snapshot=lambda *a:(_ for _ in ()).throw(AssertionError('already-prepared decoded again'))
    try:run('prepare',ids[:4])
    finally:batch_engine.fresh_snapshot=original
    assert timestamps==[Path(p).stat().st_mtime_ns for p in previews]
    assert run('pipeline',[ids[4]])['items'][4]['error']
    store.update_items(batch,[ids[4]],skip=True)
    print('PASS independent bases, default single, serial release, bad-file continuation, durable per-file pause/resume and skip completed preparation')
    # Different crops/local operations/base/span must survive a curve-only sync.
    first=store.get(batch)['items'][0];second=store.get(batch)['items'][1]
    edited=copy.deepcopy(second['snapshot']);p=edited['frames']['0'];p.update(exposure=.28,rotation=90,straighten=.4,crop=[.05,.05,.95,.95],
       local_edits=[dict(kind='brush',points=[[.5,.5]],radius=.1,ev=.2)])
    edited['frames']['0']=model.params(p).__dict__;edited['version']=8;studio.capture(edited);store.adopt(batch,ids[1],edited)
    ref=copy.deepcopy(first['snapshot']);ref['frames']['0']['curve']=[[0,0],[.5,.62],[1,1]];ref['frames']['0']['exposure']=.5
    studio.capture(ref);store.adopt(batch,ids[0],ref)
    store.update_items(batch,ids[:2],group='卷 A');store.update_items(batch,ids[2:4],group='卷 B')
    store.group(batch,'卷 A',ids[0]);store.group(batch,'卷 B',ids[2])
    config=store.get(batch)['config'];config.update(method='sync',fields=['tone']);store.configure(batch,config)
    before=[copy.deepcopy(i['snapshot']) for i in store.get(batch)['items']]
    data=run('style',ids[:2]);after=data['items'][1]['snapshot']['frames']['0']
    assert not data['items'][1]['error'],data['items'][1]['error']
    for key in ('base','anchor','span','rotation','straighten','crop','perspective','local_edits','exposure','wb_gain'):
        assert after[key]==edited['frames']['0'][key],key
    assert after['curve']==ref['frames']['0']['curve']
    assert data['items'][2]['snapshot']==before[2]
    store.undo(batch);assert all(snapshot_key(i['snapshot'])==snapshot_key(s) for i,s in zip(store.get(batch)['items'],before))
    # Relative EV is idempotent and preserves per-file origins / offsets.
    config.update(fields=['exposure'],sync_mode='relative',exposure_delta=.3);store.configure(batch,config)
    store.update_items(batch,[ids[1]],ev=-.1)
    data=run('style',ids[:2]);a=data['items'][1]['snapshot']['frames']['0']['exposure']
    assert abs(a-(edited['frames']['0']['exposure']+.3-.1))<1e-6,(a,edited['frames']['0']['exposure'])
    data=run('style',ids[:2]);assert data['items'][1]['snapshot']['frames']['0']['exposure']==a
    # Sharing is explicit and restricted to the same group's actual reference.
    rejected(lambda:store.group(batch,'卷 A',ids[2],True,True))
    store.group(batch,'卷 A',ids[0],True,False);data=run('style',[ids[1]])
    assert '确认' in data['items'][1]['error']
    store.group(batch,'卷 A',ids[0],True,True);run('check',[ids[1]]);data=run('style',[ids[1]])
    assert not data['items'][1]['error']
    assert data['items'][1]['snapshot']['frames']['0']['base']==data['items'][0]['snapshot']['frames']['0']['base']
    assert data['items'][2]['snapshot']['frames']['0']['base']==before[2]['frames']['0']['base']
    # A different working space fails instead of silently copying incompatible color.
    item=copy.deepcopy(data['items'][3]);item['snapshot']['input']['working_space']='prophoto';item['snapshot']['version']=8
    for p in item['snapshot']['frames'].values():p['working_space']='prophoto'
    studio.capture(item['snapshot']);store.adopt(batch,ids[3],item['snapshot']);data=run('style',[ids[3]])
    assert '工作空间' in data['items'][3]['error']
    print('PASS selected-field isolation, per-group references, undo, idempotent relative/individual EV, explicit shared base and cross-space rejection')
    template=store.save_template(batch,'两卷模板');other=store.create(ids[:2],'新批次');store.apply_template(other,template)
    loaded=store.get(other)
    assert loaded['config']['fields']==config['fields'] and all(not g['share_base'] and not g['reference'] for g in loaded['groups'].values())
    assert loaded['groups']['卷 A'].get('values')
    store.group_by_rule(other,'folder');assert len({i['group'] for i in store.get(other)['items']})==1
    content=paths[0].read_bytes();paths[0].write_bytes(content+b'changed')
    data=run('check',[ids[0]]);assert not data['items'][0]['valid'] and data['items'][0]['error']
    paths[0].write_bytes(content);data=run('check',[ids[0]])
    assert data['items'][0]['valid'] and not data['items'][0]['error']
    assert data['items'][0]['snapshot']['source']['mtime_ns']==paths[0].stat().st_mtime_ns
    # Reopening a process requires renewed source checks before queueing.
    store.update_items(batch,ids[2:],skip=True);store.review(batch,ids[:2]);reopened=BatchStore(studio)
    rejected(lambda:reopened.enqueue(batch))
    run('check',ids[:2]);store.review(batch,ids[:2]);jobs=store.enqueue(batch)
    assert len(jobs)==2 and len(store.enqueue(batch))==0
    frozen=[copy.deepcopy(studio.get_job(j)['snapshot']) for j in jobs]
    config['template']='changed-{frame:03d}';store.configure(batch,config);store.apply_template(other,template)
    assert frozen==[studio.get_job(j)['snapshot'] for j in jobs]
    # Independent old worker exports every selected frame with byte-exact pixels.
    for job in jobs:
        child=subprocess.run([sys.executable,'-I','-X','utf8',str(ROOT/'studio_worker.py'),'--profile',str(studio.folder.parent),'--job',job],capture_output=True,encoding='utf-8',errors='replace',timeout=60)
        assert child.returncode==0,child.stdout+child.stderr
        record=studio.get_job(job);snapshot=record['snapshot'];model.open_project(studio.materialize(snapshot,'verify-batch-output'));array,p=studio_worker.frame_parameters(snapshot,0)
        expected=(np.clip(core.process(array,p,p.output_space),0,1)*65535+.5).astype(np.uint16)
        output=next(iter(record['outputs'].values()))['tiff']['name'];assert np.array_equal(read_tiff(Path(record['destination'])/output),expected)
        before_time=(Path(record['destination'])/output).stat().st_mtime_ns
        studio.status(job,'paused');studio.retry([job]);studio_worker.execute(studio,job)
        assert (Path(record['destination'])/output).stat().st_mtime_ns==before_time
        batch_engine.release_image()
    report_dir=batch_engine.report(studio,store,batch);summary=json.loads((Path(report_dir)/'batch-summary.json').read_text('utf-8'))
    assert summary['completed_frames']==2 and len(summary['contact_pages'])==1 and summary['failures']
    with Image.open(summary['contact_pages'][0]) as im:assert im.info.get('icc_profile') and im.size==(1500,1200)
    old_jobs=[studio.get_job(j) for j in jobs];store.new_round(batch);store.review(batch,ids[:2]);next_jobs=store.enqueue(batch)
    assert len(next_jobs)==2 and set(next_jobs).isdisjoint(jobs)
    assert old_jobs==[studio.get_job(j) for j in jobs]
    assert all(studio.get_job(j)['snapshot']['_export']['template']=='changed-{frame:03d}' for j in next_jobs)
    preserved=copy.deepcopy(store.get(batch)['items'][1]['snapshot'])
    run('prepare',[ids[1]],True);assert store.get(batch)['items'][1]['snapshot']['frames']['0']['rotation']==0
    store.undo(batch);assert snapshot_key(store.get(batch)['items'][1]['snapshot'])==snapshot_key(preserved)
    print('PASS reusable versioned template, group rules, reopen identity gate, atomic no-duplicate queue, frozen params/names, exact separate-worker TIFF and resume, contact/parameter/failure report')
    # Existing library backup includes batches/templates/history, old structure remains supported.
    archive=folder/'batch.filmbackup';backup(studio,archive);restore(studio,archive)
    store=BatchStore(studio);restored=store.get(batch)
    assert restored['status']=='paused' and not any(i['valid'] for i in restored['items']) and store.templates()
    assert not any(not Path(p).exists() for p in paths)
    legacy=StudioStore(folder/'old-profile');legacy_archive=folder/'old-two-table.filmbackup';backup(legacy,legacy_archive)
    restore(legacy,legacy_archive)
    assert BatchStore(legacy).list()==[]
    print('PASS workflow tables included in checksummed library backup/restoration, restored source recheck and original preservation')
print('PASS batch A/B/C workflow regressions')
