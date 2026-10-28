"""Download official PyPI wheels into workspace only; no global install."""
from pathlib import Path
import hashlib
import json
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({'http':'http://127.0.0.1:7897','https':'http://127.0.0.1:7897'}))
cache = ROOT/'build'/'wheels'; cache.mkdir(parents=True,exist_ok=True)
manifest = []
version = None
for name in ('PySide6-Essentials','shiboken6'):
    api = f'https://pypi.org/pypi/{name}/'+(f'{version}/json' if version else 'json')
    data = json.loads(opener.open(api,timeout=40).read())
    version = data['info']['version']
    candidates = [f for f in data['urls'] if f['filename'].endswith('win_amd64.whl') and
                  any(tag in f['filename'] for tag in ('cp39-abi3','cp310-abi3','cp311-abi3','cp312'))]
    if len(candidates) != 1:
        raise RuntimeError(f'No unique compatible wheel: {name}: {candidates}')
    wheel = candidates[0]; path = cache/wheel['filename']
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != wheel['digests']['sha256']:
        partial = path.with_suffix('.download')
        with opener.open(wheel['url'],timeout=180) as source, partial.open('wb') as dest:
            while chunk := source.read(1024*1024): dest.write(chunk)
        assert hashlib.sha256(partial.read_bytes()).hexdigest() == wheel['digests']['sha256']
        partial.replace(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == wheel['digests']['sha256'], 'Wheel checksum mismatch'
    with zipfile.ZipFile(path) as z:
        assert all('..' not in Path(n).parts for n in z.namelist())
        z.extractall(ROOT/'vendor')
    manifest.append({'package':name,'version':version,'filename':wheel['filename'],'sha256':digest,'url':wheel['url']})
    print(name,version,f'{path.stat().st_size/1048576:.1f} MiB',flush=True)
(ROOT/'build'/'qt-dependencies.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
