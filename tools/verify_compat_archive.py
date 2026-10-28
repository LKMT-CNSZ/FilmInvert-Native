"""Validate the final archive and smoke-test its extracted runtime."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import uuid
import zipfile
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from build_compat import RELEASE_DATE

ROOT = Path(__file__).resolve().parents[1]
version = (ROOT / 'VERSION.txt').read_text('utf-8').strip()
archive = ROOT / 'release' / f'FilmInvert-Native-{version}-compat-win64-{RELEASE_DATE}.zip'
destination = ROOT / 'build' / 'compat-final-zip-test' / ('中文 空格-' + uuid.uuid4().hex[:8])
with zipfile.ZipFile(archive) as package:
    names = package.namelist()
    prefix = 'FilmInvertNative/'
    assert len(names) == len(set(names))
    assert all(name.startswith(prefix) and '..' not in Path(name).parts for name in names)
    assert not any(name.endswith('.lnk') or '/__pycache__/' in name or name.startswith(prefix + 'data/') or name.startswith(prefix + 'out/') or name.startswith(prefix + 'app/build/') for name in names)
    assert prefix + 'FilmInvertNative.exe' not in names
    for name in ('runtime/pythonw.exe', 'runtime/python313.dll', 'app/native_app.py', '启动胶片工作室.cmd', '启动诊断.cmd'):
        assert prefix + name in names, name
    manifest = json.loads(package.read(prefix + 'file-manifest.json'))['files']
    assert set(names) == {prefix + name for name in manifest} | {prefix + 'file-manifest.json'}
    for name, expected in manifest.items():
        assert hashlib.sha256(package.read(prefix + name)).hexdigest() == expected, name
    assert hashlib.sha256(package.read(prefix + 'runtime/pythonw.exe')).hexdigest() == '9c1437a2e692e9ec9d8b9eb0fd8a6432f8376510ac81b174437ad6a216c2c84b'
    package.extractall(destination)
bundle = destination / 'FilmInvertNative'
cwd = destination / '无关工作目录'
cwd.mkdir()
env = dict(os.environ)
env.update(PATH='C:\\Windows\\System32;C:\\Windows', PYTHONHOME=str(cwd / 'invalid'), PYTHONPATH=str(cwd / 'invalid'))
result = subprocess.run([str(bundle / 'runtime' / 'python.exe'), '-I', '-m', 'film_invert_launcher', '--diagnose'],
                        cwd=cwd, env=env, capture_output=True, encoding='utf-8', errors='replace', timeout=90)
report = json.loads((bundle / 'data' / 'startup-diagnostics.json').read_text('utf-8'))
assert result.returncode == 0 and report['status'] == 'pass', result.stdout + result.stderr
print('PASS final ZIP manifest, runtime hash, excluded local state, fresh extraction, isolated startup and sample project')
print('Archive:', archive)
print('Extracted:', bundle)
