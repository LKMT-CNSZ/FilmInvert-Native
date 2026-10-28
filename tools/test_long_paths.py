"""Real deep-directory IO and isolated durable queue regression (no private data)."""
from pathlib import Path
import json,os,sys,tempfile,subprocess,copy,shutil
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ['QT_QPA_PLATFORM']='offscreen'
import desktop_model as model,studio_worker as worker,project_transfer,studio_archive
from file_io import io_path,windows_path
from studio_store import StudioStore,sha256
from tiffread import read_tiff
import numpy as np

assert windows_path(r'C:\one\..\two')==r'\\?\C:\two'
assert windows_path(r'\\server\share\one\..\two')==r'\\?\UNC\server\share\two'
assert windows_path(r'\\?\C:\two')==r'\\?\C:\two'
assert windows_path('relative/../photo')==windows_path(Path('photo').absolute())
(ROOT/'build/tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build/tests',prefix='paths-') as temporary:
    base=Path(temporary);deep=base
    while len(str(deep))<330:deep=deep/('中文 空格-'+('x'*44))
    io_path(deep).mkdir(parents=True)
    targets=[base/('r'*(length-len(str(base))-6)+'.json') for length in (230,254)]
    for path in (*targets,deep/'recipe.json',deep/('f'*245+'.json')):
        model.write_json(path,{'old':True});before=io_path(path).read_bytes()
        try:model.write_json(path,{'invalid':float('nan')})
        except ValueError:pass
        else:raise AssertionError('invalid JSON accepted')
        assert io_path(path).read_bytes()==before
        assert not list(io_path(path.parent).glob('*.part'))
        model.write_json(path,{'new':'中文'});assert json.loads(io_path(path).read_text('utf-8'))=={'new':'中文'}
    relative=Path(os.path.relpath(deep/'relative.json',Path.cwd()))
    model.write_json(relative,{'relative':True});assert io_path(deep/'relative.json').is_file()
    source=deep/'source.tif';io_path(source).write_bytes((ROOT/'samples/pro-light-demo.tif').read_bytes())
    opened=model.open_image(source);p=opened['params'];snapshot=model.project_snapshot(p)
    project=deep/'document.filmproj';model.save_project(project,p)
    assert model.open_project(project)['params']==p
    output=deep/'direct.tif';model.export_image(output,p)
    assert read_tiff(output).dtype==np.uint16 and io_path(str(output)+'.json').is_file()
    # Real subprocess, >330-character profile and >400-character destination.
    profile=deep/'profile';store=StudioStore(profile);asset=store.capture(snapshot)
    job=store.enqueue([asset],deep/'队列 成品',False)[0]
    result=subprocess.run([sys.executable,'-I','-X','utf8',str(ROOT/'studio_worker.py'),
        '--profile',str(profile),'--job',job],capture_output=True,encoding='utf-8',errors='replace',timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
    saved=store.get_job(job);assert saved['status']=='completed',saved
    directory=io_path(saved['destination']);record=saved['outputs']['0']
    assert read_tiff(directory/record['tiff']['name']).dtype==np.uint16
    assert json.loads((directory/record['recipe']['name']).read_text('utf-8'))['params']==p
    assert (directory/'manifest.json').is_file()
    assert not list(directory.glob('.pending-*')) and not list(directory.glob('*.part'))
    # Failure before intent leaves no orphan; existing output and snapshot survive.
    failed=store.enqueue([asset],deep/'失败 恢复',False)[0];write=model.write_json
    def fail_recipe(path,data):
        if str(path).endswith('.tif.json'):raise OSError('injected recipe IO failure')
        return write(path,data)
    model.write_json=fail_recipe
    try:
        try:worker.execute(store,failed)
        except OSError:pass
        else:raise AssertionError('IO failure not exercised')
    finally:model.write_json=write
    failed_dir=io_path(store.get_job(failed)['destination'])
    assert store.get_job(failed)['status']=='failed'
    assert not list(failed_dir.iterdir())
    store.retry([failed]);assert worker.execute(store,failed)=='completed'
    # Interrupted publication retains durable stages; retry never rewrites TIFF.
    partial=store.enqueue([asset],deep/'中断 恢复',False)[0];publish=worker.publish;calls=[]
    def interrupt(stage,dest,digest):
        calls.append(1)
        if len(calls)==2:raise OSError('interruption between TIFF and recipe')
        publish(stage,dest,digest)
    worker.publish=interrupt
    try:
        try:worker.execute(store,partial)
        except OSError:pass
        else:raise AssertionError('publication interruption not exercised')
    finally:worker.publish=publish
    saved=store.get_job(partial);directory=io_path(saved['destination']);record=saved['outputs']['0']
    tiff=directory/record['tiff']['name'];stamp=tiff.stat().st_mtime_ns;digest=sha256(tiff)
    assert (directory/record['recipe']['stage']).is_file()
    store.retry([partial]);assert worker.execute(store,partial)=='completed'
    assert tiff.stat().st_mtime_ns==stamp and sha256(tiff)==digest
    assert not list(directory.glob('.pending-*'))
    # Transfer and SQLite backup use the same filesystem boundary.
    packed=deep/('transfer-'+'a'*170+'.filmtransfer')
    project_transfer.pack(snapshot,packed);unpacked=project_transfer.unpack(packed,deep/'unpack')
    assert model.open_project(unpacked['project'])['params']==p
    backup=deep/('backup-'+'b'*170+'.filmbackup');studio_archive.backup(store,backup)
    recovery=studio_archive.restore(store,backup);assert io_path(recovery).is_file()
    print('PASS deep paths: JSON atomic replace/NaN cleanup, 250-character component, relative IO, project/TIFF/recipe, real isolated worker, pre-intent cleanup/retry, durable publication recovery, transfer and SQLite backup/restore')
    print('PATH-LENGTHS',json.dumps({'profile':len(str(profile)),'queue_tiff':len(str(tiff)),'transfer':len(str(packed))}))
    # tempfile's ordinary root cannot recursively delete deep descendants on this
    # interpreter. Remove only this test's verified private root using extended IO.
    assert base.resolve().parent==(ROOT/'build/tests').resolve() and base.name.startswith('paths-')
    shutil.rmtree(io_path(base))
