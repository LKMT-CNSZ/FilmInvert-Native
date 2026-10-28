"""Final offline audit: matching source, dependency bytes, manifests, privacy and hashes."""
from pathlib import Path
import hashlib,json,sys,zipfile
sys.path.insert(0,str(Path(__file__).resolve().parent))
from build_compat import ROOT,VERSION,RELEASE_DATE,qt_file
from package_stable import DOCUMENTS,source_files,digest
release=ROOT/'release'
records=json.loads((release/f'SHA256-{VERSION}.json').read_text('utf-8'))
for row in records:
    path=release/row['filename'];assert path.stat().st_size==row['bytes'] and digest(path)==row['sha256'],row['filename']
setup=next(row for row in records if row['filename'].endswith('.exe'))
installed=json.loads((release/f'{VERSION}-交付验证.json').read_text('utf-8'))
assert installed['status']=='pass' and installed['installer_sha256']==setup['sha256']
portable=release/f'FilmInvert-Native-{VERSION}-compat-win64-{RELEASE_DATE}.zip'
source=release/f'FilmInvert-Native-{VERSION}-source-{RELEASE_DATE}.zip'
assert installed['portable']['status']=='pass' and installed['portable']['archive_sha256']==digest(portable)
with zipfile.ZipFile(portable) as z,zipfile.ZipFile(source) as src:
    assert z.testzip() is None and src.testzip() is None
    prefix='FilmInvertNative/';sprefix='FilmInvertNative-source/'
    names=z.namelist();snames=src.namelist()
    for items in (names,snames):assert len(items)==len(set(items)) and all('..' not in Path(name).parts for name in items)
    manifest=json.loads(z.read(prefix+'file-manifest.json'))['files']
    assert set(names)=={prefix+name for name in manifest}|{prefix+'file-manifest.json'}
    for name,value in manifest.items():assert hashlib.sha256(z.read(prefix+name)).hexdigest()==value,name
    source_manifest=json.loads(src.read(sprefix+'source-manifest.json'));assert source_manifest['version']==VERSION
    expected={file.relative_to(ROOT).as_posix():file for file in source_files()}
    assert set(source_manifest['files'])==set(expected)
    assert set(snames)=={sprefix+name for name in expected}|{sprefix+'source-manifest.json'}
    for name,file in expected.items():
        payload=src.read(sprefix+name);assert payload==file.read_bytes(),name
        assert hashlib.sha256(payload).hexdigest()==source_manifest['files'][name]
    for file in ROOT.glob('*.py'):assert z.read(prefix+'app/'+file.name)==file.read_bytes()==src.read(sprefix+file.name)
    for name in DOCUMENTS:
        assert z.read(prefix+name)==z.read(prefix+'app/'+name)==src.read(sprefix+name)==(ROOT/name).read_bytes(),name
    assert z.read(prefix+'VERSION.txt').decode('utf-8').strip()==VERSION
    assert json.loads(z.read(prefix+'portable-layout.json'))['version']==VERSION
    assert json.loads(src.read(sprefix+'tools/installed-layout.json').decode('utf-8-sig'))['version']==VERSION
    assert ("VERSION = '"+VERSION+"'").encode() in z.read(prefix+'app/desktop_model.py')
    # Non-runtime images/projects are restricted to the known synthetic assets.
    sample_names={p.name for p in (ROOT/'samples').iterdir() if p.is_file()}
    source_file_names={p.relative_to(ROOT).as_posix() for p in source_files()}
    for name in names:
        rel=name[len(prefix):];parts=Path(rel).parts
        assert parts[0] not in ('validation','data','out','build') and '__pycache__' not in parts and not rel.endswith(('.log','.lnk','.sqlite3'))
        if parts[0]!='runtime' and rel.lower().endswith(('.nef','.fff','.cr2','.cr3','.arw','.dng','.jpg','.jpeg','.tif','.tiff','.filmproj')):
            assert parts[:2]==('app','samples') and len(parts)==3 and parts[-1] in sample_names,rel
    assert not any(name.startswith(('validation/','release/','dist/','vendor/','runtime/')) for name in source_file_names)
    # Compare every bundled dependency file to its pinned wheel/official ZIP.
    dependencies=json.loads(z.read(prefix+'dependencies.json'));expected_runtime={}
    for record in dependencies:
        archive=ROOT/'build/wheels'/record['filename'];assert digest(archive)==record['sha256']
        with zipfile.ZipFile(archive) as upstream:
            for info in upstream.infolist():
                if info.is_dir():continue
                rel=Path(info.filename)
                if '__pycache__' in rel.parts or rel.suffix in ('.log','.lnk'):continue
                if record['package']=='CPython-embeddable':dest='runtime/'+info.filename
                else:
                    if record['package']=='PySide6-Essentials' and not qt_file(rel):continue
                    dest='runtime/Lib/site-packages/'+info.filename
                expected_runtime[dest]=hashlib.sha256(upstream.read(info.filename)).hexdigest()
    overrides={'runtime/python313._pth','runtime/qt.conf'}
    for name,value in expected_runtime.items():
        if name not in overrides:assert hashlib.sha256(z.read(prefix+name)).hexdigest()==value,name
    assert {name for name in manifest if name.startswith('runtime/')}==set(expected_runtime)|overrides
    assert 'THIRD_PARTY/InnoSetup-LICENSE.txt' in manifest
    assert 'THIRD_PARTY/Python-LICENSE.txt' in manifest
# Retain and independently recheck the user's exact rc13 rollback baselines.
baselines={'FilmInvert-Native-3.0.0-rc13-compat-win64-20261027.zip':'220b3aafd38a57501a35b72837775fb0dd0e0ce79a3a98394805e353230ef70f',
           'FilmInvert-Native-3.0.0-rc13-source-20261027.zip':'1feb1fa383866c254e86c968b23f835fe868da3381523ad252becd1d24de905d'}
for name,value in baselines.items():
    path=release/name
    if path.is_file():assert digest(path)==value
# When available, verify processing remained at the accepted rc13 baseline.
old=release/'FilmInvert-Native-3.0.0-rc13-source-20261027.zip';changed=[]
if old.exists():
    with zipfile.ZipFile(old) as baseline:
        for file in ROOT.glob('*.py'):
            name='FilmInvertNative-source/'+file.name
            if name not in baseline.namelist() or baseline.read(name)!=file.read_bytes():changed.append(file.name)
    allowed={'app_paths.py','desktop_model.py','film_invert_launcher.py','native_app.py','native_studio.py'}
    if VERSION=='3.0.1':allowed.update({'i18n.py','i18n_en.py','native_batch.py','native_calibration.py','native_efficiency.py','native_frames.py','native_geometry.py','native_local.py','native_widgets.py'})
    assert set(changed)==allowed,changed
report={'version':VERSION,'status':'pass','portable_file_count':len(names),'source_file_count':len(snames),'official_dependency_files_checked':len(expected_runtime),
        'checks':['all_delivery_SHA256_and_sizes','ZIP_CRC_and_safe_paths','exact_portable_manifest','source_archive_matches_workspace','app_and_docs_match_source','version_consistency','synthetic_only_app_samples','no_local_state_validation_private_photos_or_outputs','all_runtime_bytes_match_pinned_official_archives_except_documented_path_config','required_licenses','exact_final_installer_test_hash','rc13_baselines_preserved'],
        'changed_application_files_vs_rc13':sorted(changed),'code_signing':'FilmInvert installer/application unsigned; upstream runtime signatures separate'}
(ROOT/'build/stable-release-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
