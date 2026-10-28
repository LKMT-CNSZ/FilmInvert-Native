"""Create clean portable/source archives without local session or test data."""
from pathlib import Path
import hashlib
import json
import shutil
import zipfile

ROOT=Path(__file__).resolve().parents[1]
VERSION=(ROOT/'VERSION.txt').read_text().strip()
release=ROOT/'release';release.mkdir(exist_ok=True)
bundle=ROOT/'dist'/VERSION/'FilmInvertNative'
if not (bundle/'FilmInvertNative.exe').is_file():
    raise SystemExit('Build the native application first.')
documents=['DESKTOP-README.md','VERIFICATION.md']
for name in documents:
    shutil.copy2(ROOT/name,bundle/name)
shutil.copy2(ROOT/'DESKTOP-README.md',bundle/'_internal'/'DESKTOP-README.md')
for file in (ROOT/'samples').glob('*.filmproj'):
    shutil.copy2(file,bundle/'_internal'/'samples'/file.name)
shutil.copytree(ROOT/'THIRD_PARTY',bundle/'THIRD_PARTY',dirs_exist_ok=True)
shutil.copy2(ROOT/'build'/'qt-dependencies.json',bundle/'qt-dependencies.json')
for name in ('preview-native.jpg','preview-calibration.jpg'):
    if (ROOT/name).exists():shutil.copy2(ROOT/name,bundle/name)

def archive(path,items):
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for file,name in items:
            z.write(file,name)
    with zipfile.ZipFile(path) as z:
        bad=z.testzip()
        if bad:raise RuntimeError('ZIP CRC failed: '+bad)
    return {'filename':path.name,'bytes':path.stat().st_size,
            'sha256':hashlib.file_digest(path.open('rb'),'sha256').hexdigest()}

portable=[]
for file in sorted(bundle.rglob('*')):
    rel=file.relative_to(bundle)
    if not file.is_file() or rel.parts[0] in ('data','out') or '__pycache__' in rel.parts:
        continue
    if file.suffix=='.log':continue
    portable.append((file,Path('FilmInvertNative')/rel))
reports=[archive(release/f'FilmInvert-Native-{VERSION}-preview-win64-20261022.zip',portable)]
source=[]
for pattern in ('*.py','*.md','VERSION.txt','requirements-build.txt','tools/*.py','samples/*','THIRD_PARTY/**/*','build/qt-dependencies.json','preview-*.jpg'):
    for file in sorted(ROOT.glob(pattern)):
        if file.is_file():source.append((file,Path('FilmInvertNative-source')/file.relative_to(ROOT)))
reports.append(archive(release/f'FilmInvert-Native-{VERSION}-preview-source-20261022.zip',source))
(release/f'SHA256-{VERSION}.json').write_text(json.dumps(reports,indent=2),encoding='utf-8')
print(json.dumps(reports,indent=2))
