"""Strict, deterministic ZIP packaging for the 3.0.0 release and matching source."""
from pathlib import Path
import argparse,hashlib,json,shutil,zipfile
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from build_compat import ROOT,BUNDLE,VERSION,RELEASE_DATE,refresh_manifest,DOCUMENTS
def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def stage():
    assert VERSION in ('3.0.0','3.0.1')
    for folder in (BUNDLE,BUNDLE/'app'):
        for path in folder.glob('*.md'):
            if path.name not in DOCUMENTS:path.unlink()
        for name in DOCUMENTS:
            if not (ROOT/name).is_file():raise FileNotFoundError(ROOT/name)
            shutil.copy2(ROOT/name,folder/name)
    shutil.copy2(ROOT/'VERSION.txt',BUNDLE/'VERSION.txt')
    shutil.copy2(ROOT/'THIRD_PARTY/NOTICE.txt',BUNDLE/'THIRD_PARTY/NOTICE.txt')
    shutil.copy2(ROOT/'THIRD_PARTY/InnoSetup-LICENSE.txt',BUNDLE/'THIRD_PARTY/InnoSetup-LICENSE.txt')
    refresh_manifest()
    manifest=json.loads((BUNDLE/'file-manifest.json').read_text('utf-8'))['files']
    # A reviewed allowlist: source, chosen docs, selected tests, synthetic samples/assets,
    # official runtime and license files. State is never included by a recursive ZIP glob.
    approved={'VERSION.txt','portable-layout.json','dependencies.json','启动胶片工作室.cmd','启动诊断.cmd','Launch FilmInvert.cmd',*DOCUMENTS}
    for name in manifest:
        parts=Path(name).parts
        allowed=(name in approved or parts[0] in ('runtime','THIRD_PARTY') or
                 (parts[0]=='app' and (len(parts)==2 and (name.endswith('.py') or parts[1] in DOCUMENTS) or len(parts)>2 and parts[1] in ('tools','assets','samples'))))
        if not allowed:raise ValueError('Unexpected bundle file: '+name)
        if parts[0] in ('data','out','validation') or '__pycache__' in parts or parts[:2]==('app','build'):raise ValueError(name)
    return manifest

def archive(path,items):
    stamp=(2026,10,28,0,0,0)
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
        for file,name in sorted(items,key=lambda row:row[1]):
            info=zipfile.ZipInfo(name,stamp);info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
            z.writestr(info,file.read_bytes(),compresslevel=6)
    with zipfile.ZipFile(path) as z:
        if z.testzip():raise ValueError('ZIP CRC failed')
    return {'filename':path.name,'bytes':path.stat().st_size,'sha256':digest(path)}

def source_files():
    files=set()
    for pattern in ('*.py','tools/*.py','tools/*.ps1','assets/**/*','samples/*','THIRD_PARTY/**/*'):
        files.update(p for p in ROOT.glob(pattern) if p.is_file() and '__pycache__' not in p.parts and p.name!='PyInstaller-COPYING.txt')
    for name in (*DOCUMENTS,'VERSION.txt','requirements-build.txt','tools/stable-setup.iss','tools/ChineseSimplified.isl','tools/installed-layout.json','build/compat-dependencies.json','build/installer-dependencies.json'):
        files.add(ROOT/name)
    return sorted(files)

def package():
    manifest=stage();release=ROOT/'release';release.mkdir(exist_ok=True)
    items=[(BUNDLE/name,'FilmInvertNative/'+name) for name in [*manifest,'file-manifest.json']]
    records=[archive(release/f'FilmInvert-Native-{VERSION}-compat-win64-{RELEASE_DATE}.zip',items)]
    files=source_files()
    source_manifest={file.relative_to(ROOT).as_posix():digest(file) for file in files}
    source_record=ROOT/'build/stable-source-manifest.json'
    source_record.write_text(json.dumps({'version':VERSION,'files':source_manifest},ensure_ascii=False,indent=2),encoding='utf-8')
    records.append(archive(release/f'FilmInvert-Native-{VERSION}-source-{RELEASE_DATE}.zip',[(file,'FilmInvertNative-source/'+file.relative_to(ROOT).as_posix()) for file in files]+[(source_record,'FilmInvertNative-source/source-manifest.json')]))
    setup=release/f'FilmInvert-Native-{VERSION}-setup-win64-{RELEASE_DATE}.exe'
    if not setup.is_file():raise FileNotFoundError(setup)
    records.insert(0,{'filename':setup.name,'bytes':setup.stat().st_size,'sha256':digest(setup)})
    docs=release if VERSION=='3.0.0' else release/VERSION
    docs.mkdir(exist_ok=True)
    for name in DOCUMENTS:
        dest=docs/name;shutil.copy2(ROOT/name,dest);records.append({'filename':dest.relative_to(release).as_posix(),'bytes':dest.stat().st_size,'sha256':digest(dest)})
    # External test results can carry final EXE hashes without making the EXE self-referential.
    for name in (f'{VERSION}-交付验证.json',):
        path=release/name
        if path.is_file():records.append({'filename':name,'bytes':path.stat().st_size,'sha256':digest(path)})
    (release/f'SHA256-{VERSION}.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    (release/f'SHA256-{VERSION}.txt').write_text(''.join(r['sha256']+'  '+r['filename']+'\n' for r in records),encoding='utf-8')
    print(json.dumps(records,ensure_ascii=False,indent=2))
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--stage-only',action='store_true');args=parser.parse_args()
    if args.stage_only:print('Staged',len(stage()),'reviewed files')
    else:package()
