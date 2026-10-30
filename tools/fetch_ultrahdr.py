"""Fetch the pinned official imagecodecs wheel; build_compat keeps only JPEG HDR.

Run with the build interpreter. No system Python installation is modified.
For offline builds, place the exact wheel in build/wheels instead.
"""
from pathlib import Path
import hashlib,json,urllib.request
ROOT=Path(__file__).resolve().parents[1]

def main():
    record=next(r for r in json.loads((ROOT/'build/compat-dependencies.json').read_text('utf-8')) if r['package']=='imagecodecs')
    path=ROOT/'build/wheels'/record['filename'];path.parent.mkdir(parents=True,exist_ok=True)
    if not path.exists():
        with urllib.request.urlopen(record['url'],timeout=60) as response:
            data=response.read()
        if hashlib.sha256(data).hexdigest()!=record['sha256']:raise ValueError('Codec download SHA256 mismatch')
        path.write_bytes(data)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256']:raise ValueError('Codec wheel SHA256 mismatch')
    print('Verified official codec:',record['filename'])
if __name__=='__main__':main()
