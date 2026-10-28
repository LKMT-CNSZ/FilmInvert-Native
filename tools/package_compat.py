"""Package the unpacked official-runtime distribution and complete editable sources."""
from pathlib import Path
import hashlib
import json
import shutil
import zipfile
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_compat import ROOT,BUNDLE,VERSION,RELEASE_DATE,refresh_manifest

def included(path,root):
    rel=path.relative_to(root)
    return path.is_file() and rel.parts[0] not in ('data','out') and '__pycache__' not in rel.parts and rel.parts[:2]!=('app','build') and path.suffix not in ('.log','.lnk')

for name in ('DESKTOP-README.md','VERIFICATION.md','COMPAT-README.md','WORKSTATION-ROADMAP.md','DISTRIBUTION-VERIFICATION.md','ACCEPTANCE.md','CALIBRATION-VALIDATION.md','PREVIEW-AND-AUDIT.md','ADVANCED-ROI.md','NEF-FIXES.md','RC6-FIXES.md','RC7-FIXES.md','RC8-FIXES.md','RC9-FIXES.md','RC10-FIXES.md','RC10.1-FIXES.md','RC10.2-FIXES.md','RC11-FIXES.md','RC12-FIXES.md','RC13-FIXES.md','NEXT-ROADMAP.md','WORKFLOW-GUIDE.md','BATCH-WORKFLOW-PROPOSAL.md','PERFORMANCE.json','SWITCH-PERFORMANCE.json'):
    if (ROOT/name).exists():
        shutil.copy2(ROOT/name,BUNDLE/name);shutil.copy2(ROOT/name,BUNDLE/'app'/name)
for name in ('preview-native.jpg','preview-calibration.jpg','preview-batch.jpg'):
    if (ROOT/name).exists():shutil.copy2(ROOT/name,BUNDLE/name);shutil.copy2(ROOT/name,BUNDLE/'app'/name)
if (ROOT/'build'/'compat-signatures.json').exists():
    shutil.copy2(ROOT/'build'/'compat-signatures.json',BUNDLE/'distribution-signatures.json')
refresh_manifest()
release=ROOT/'release';release.mkdir(exist_ok=True)
def archive(path,items):
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for file,name in items:z.write(file,name)
    with zipfile.ZipFile(path) as z:
        if z.testzip():raise RuntimeError('ZIP verification failed')
    return {'filename':path.name,'bytes':path.stat().st_size,'sha256':hashlib.file_digest(path.open('rb'),'sha256').hexdigest()}
items=[(file,Path('FilmInvertNative')/file.relative_to(BUNDLE)) for file in sorted(BUNDLE.rglob('*')) if included(file,BUNDLE)]
records=[archive(release/f'FilmInvert-Native-{VERSION}-compat-win64-{RELEASE_DATE}.zip',items)]
files=[]
for pattern in ('*.py','*.md','VERSION.txt','requirements-build.txt','PERFORMANCE.json','SWITCH-PERFORMANCE.json','tools/*.py','assets/**/*','samples/*','THIRD_PARTY/**/*','build/*dependencies.json','preview-*.jpg'):
    files.extend((file,Path('FilmInvertNative-source')/file.relative_to(ROOT)) for file in sorted(ROOT.glob(pattern))
                 if file.is_file() and not file.name.startswith('RC6-HANDOFF-'))
records.append(archive(release/f'FilmInvert-Native-{VERSION}-source-{RELEASE_DATE}.zip',files))
(release/f'SHA256-{VERSION}.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
print(json.dumps(records,indent=2))
