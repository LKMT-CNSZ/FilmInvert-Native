"""Relocate the whole bundle; verify isolated startup and damaged-file diagnostics."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import uuid

ROOT=Path(__file__).resolve().parents[1]
version=(ROOT/'VERSION.txt').read_text('utf-8').strip()
source=ROOT/'dist'/f'{version}-compat'/'FilmInvertNative'
parent=ROOT/'build'/'compat-portability'/('中文路径 带空格-'+uuid.uuid4().hex[:8])
target=parent/'FilmInvertNative'
assert parent.resolve().is_relative_to((ROOT/'build').resolve())
def ignore(folder,names):
    folder=Path(folder);relative=folder.relative_to(source)
    blocked={'__pycache__'}
    if not relative.parts:blocked.update(('data','out'))
    if relative.parts==('app',):blocked.add('build')
    return [name for name in names if name in blocked]
shutil.copytree(source,target,ignore=ignore)
cwd=parent/'无关工作目录';cwd.mkdir()
windows=Path(os.environ.get('WINDIR','C:/Windows'));env=dict(os.environ)
env.update(PYTHONHOME=str(cwd/'bad-home'),PYTHONPATH=str(cwd/'bad-modules'),
           PATH=str(windows/'System32')+os.pathsep+str(windows),PYTHONIOENCODING='utf-8')
def diagnose(expected):
    result=subprocess.run([str(target/'runtime'/'python.exe'),'-I','-m','film_invert_launcher','--diagnose'],
                          cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,encoding='utf-8',errors='replace',timeout=90)
    if result.returncode!=expected:raise AssertionError(result.stdout)
    return json.loads((target/'data'/'startup-diagnostics.json').read_text('utf-8'))
report=diagnose(0);assert report['native_ui']['app_dir_matches_bundle'] and report['sample']['calibration_restored']
path=target/'app'/'core.py';original=path.read_bytes()
try:
    path.write_bytes(original+b'\n# integrity diagnostic fixture\n')
    failure=diagnose(1)
    assert any(row['file']=='app/core.py' and row['problem']=='hash_mismatch' for row in failure['integrity']['errors'])
finally:path.write_bytes(original)
assert diagnose(0)['status']=='pass'
(ROOT/'build'/'compat-portability'/'last-target.txt').write_text(str(target),encoding='utf-8')
print('PASS relocated Chinese/space path, foreign working directory, official runtime isolation, native UI, restored project, tampered-file diagnostics and recovery')
print('Relocated acceptance folder:',target)
