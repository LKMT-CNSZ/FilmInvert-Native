"""Fetch official signed CPython and hash-verified wheels, without installing globally."""
from pathlib import Path
import argparse
import hashlib
import json
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
PYTHON_VERSION='3.13.17'
PYTHON_SHA256='9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38'
PACKAGES=[('numpy','2.3.5'),('Pillow','12.3.0'),('rawpy','0.27.1'),
          ('PySide6-Essentials','6.12.0'),('shiboken6','6.12.0')]

def fetch(proxy='http://127.0.0.1:7897'):
    handler=urllib.request.ProxyHandler({'http':proxy,'https':proxy}) if proxy else urllib.request.ProxyHandler()
    opener=urllib.request.build_opener(handler)
    cache=ROOT/'build'/'wheels';cache.mkdir(parents=True,exist_ok=True)
    records=[{'package':'CPython-embeddable','version':PYTHON_VERSION,
              'filename':f'python-{PYTHON_VERSION}-embed-amd64.zip',
              'url':f'https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip',
              'sha256':PYTHON_SHA256,
              'provenance':'https://www.python.org/downloads/release/python-31317/'}]
    for name,version in PACKAGES:
        data=json.loads(opener.open(f'https://pypi.org/pypi/{name}/{version}/json',timeout=45).read())
        candidates=[f for f in data['urls'] if f['filename'].endswith('win_amd64.whl') and
                    ('cp313-cp313-' in f['filename'] or 'cp310-abi3-' in f['filename'])]
        if len(candidates)!=1:raise RuntimeError(f'No unique wheel for {name}: {[f["filename"] for f in candidates]}')
        file=candidates[0]
        records.append({'package':name,'version':version,'filename':file['filename'],
                        'url':file['url'],'sha256':file['digests']['sha256'],
                        'provenance':f'https://pypi.org/pypi/{name}/{version}/json'})
    for record in records:
        dest=cache/record['filename']
        if not dest.exists() or hashlib.file_digest(dest.open('rb'),'sha256').hexdigest()!=record['sha256']:
            partial=dest.with_suffix('.download')
            with opener.open(record['url'],timeout=180) as response,partial.open('wb') as stream:
                while chunk:=response.read(1024*1024):stream.write(chunk)
            if hashlib.file_digest(partial.open('rb'),'sha256').hexdigest()!=record['sha256']:
                raise RuntimeError('Official download hash mismatch: '+dest.name)
            partial.replace(dest)
        print(record['package'],record['version'],dest.stat().st_size,'SHA256 verified',flush=True)
    (ROOT/'build'/'compat-dependencies.json').write_text(json.dumps(records,indent=2),encoding='utf-8')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--proxy',default='http://127.0.0.1:7897')
    fetch(parser.parse_args().proxy)
