"""Build a transparent portable app using unmodified official signed CPython."""
from pathlib import Path,PurePosixPath
import hashlib
import json
import shutil
import zipfile

ROOT=Path(__file__).resolve().parents[1]
VERSION=(ROOT/'VERSION.txt').read_text('utf-8').strip()
RELEASE_DATE='20261030' if VERSION=='3.1.0' else '20261029' if VERSION in ('3.1.0-rc3','3.1.0-rc4','3.1.0-rc5','3.1.0-rc6','3.1.0-rc7','3.1.0-rc8','3.1.0-rc9','3.1.0-rc10','3.1.0-rc11','3.1.0-rc12','3.1.0-rc13','3.1.0-rc14','3.1.0') else '20261028' if VERSION in ('3.0.0','3.0.1','3.1.0-rc1','3.1.0-rc2','3.1.0-rc3','3.1.0-rc4','3.1.0-rc5','3.1.0-rc6','3.1.0-rc7','3.1.0-rc8','3.1.0-rc9','3.1.0-rc10','3.1.0-rc11','3.1.0-rc12','3.1.0-rc13','3.1.0-rc14','3.1.0') else '20261027'
DOCUMENTS=('README.md','README.en.md','README.en.html','LOCALIZATION.md','DESKTOP-README.md',
           'COMPAT-README.md','WORKFLOW-GUIDE.md','软件介绍.md','软件介绍.html',
           f'RELEASE-NOTES-{VERSION}.md',f'RELEASE-VALIDATION-{VERSION}.md','RELEASE-VALIDATION-3.0.0.md','BUILD.md','INSTALL-NOTES.txt','NEXT-ROADMAP.md','HDR-GUIDE.md')
BUNDLE=ROOT/'dist'/f'{VERSION}-compat'/'FilmInvertNative'
QT_DLLS={'Qt6Core.dll','Qt6Gui.dll','Qt6Widgets.dll','Qt6Network.dll','Qt6Svg.dll','Qt6OpenGL.dll','pyside6.abi3.dll','opengl32sw.dll'}
QT_MODULES={'QtCore.pyd','QtGui.pyd','QtWidgets.pyd','QtNetwork.pyd'}
PLUGINS={'platforms','imageformats','iconengines','styles','networkinformation','tls'}

def extract(archive,dest,select=lambda path:True):
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            path=PurePosixPath(info.filename)
            if path.is_absolute() or '..' in path.parts:raise ValueError('Unsafe archive member')
            if not info.is_dir() and select(path):
                target=dest/Path(*path.parts);target.parent.mkdir(parents=True,exist_ok=True)
                with z.open(info) as source,target.open('wb') as output:shutil.copyfileobj(source,output)

def qt_file(path):
    if path.parts[0]!='PySide6':return path.parts[0].endswith('.dist-info')
    if len(path.parts)==2:
        return path.suffix=='.py' or path.name in QT_DLLS or path.name in QT_MODULES or (
            path.suffix.lower()=='.dll' and path.name.lower().startswith(('msvcp','vcruntime','concrt','vcomp','vccor','vcamp')))
    return path.parts[1]=='support' or (path.parts[1]=='plugins' and path.parts[2] in PLUGINS and
             (path.parts[2]!='platforms' or path.name in ('qwindows.dll','qminimal.dll','qoffscreen.dll')))

def build():
    records=json.loads((ROOT/'build'/'compat-dependencies.json').read_text('utf-8'))
    BUNDLE.mkdir(parents=True,exist_ok=True);runtime=BUNDLE/'runtime';app=BUNDLE/'app'
    runtime.mkdir(exist_ok=True);app.mkdir(exist_ok=True)
    site=runtime/'Lib'/'site-packages';site.mkdir(parents=True,exist_ok=True)
    for record in records:
        archive=ROOT/'build'/'wheels'/record['filename']
        if hashlib.file_digest(archive.open('rb'),'sha256').hexdigest()!=record['sha256']:raise ValueError('Dependency hash mismatch')
        if record['package']=='CPython-embeddable':extract(archive,runtime)
        else:extract(archive,site,qt_file if record['package']=='PySide6-Essentials' else lambda p:True)
    (runtime/'python313._pth').write_text('python313.zip\n.\n../app\nLib/site-packages\nimport site\n',encoding='ascii')
    (runtime/'qt.conf').write_text('[Paths]\nPrefix=Lib/site-packages/PySide6\nPlugins=plugins\n',encoding='ascii')
    shutil.copy2(ROOT/'VERSION.txt',BUNDLE/'VERSION.txt')
    for path in ROOT.glob('*.py'):shutil.copy2(path,app/path.name)
    documents=DOCUMENTS if VERSION in ('3.0.1','3.1.0-rc1','3.1.0-rc2','3.1.0-rc3','3.1.0-rc4','3.1.0-rc5','3.1.0-rc6','3.1.0-rc7','3.1.0-rc8','3.1.0-rc9','3.1.0-rc10','3.1.0-rc11','3.1.0-rc12','3.1.0-rc13','3.1.0-rc14','3.1.0') else ('DESKTOP-README.md','COMPAT-README.md','WORKFLOW-GUIDE.md','软件介绍.md','软件介绍.html',
               'RELEASE-NOTES-3.0.0.md','RELEASE-VALIDATION-3.0.0.md','BUILD.md','INSTALL-NOTES.txt','NEXT-ROADMAP.md') if VERSION=='3.0.0' else (
               'DESKTOP-README.md','VERIFICATION.md','COMPAT-README.md','WORKFLOW-GUIDE.md','RC13-FIXES.md','NEXT-ROADMAP.md')
    for name in documents:
        if (ROOT/name).exists():shutil.copy2(ROOT/name,BUNDLE/name);shutil.copy2(ROOT/name,app/name)
    shutil.copytree(ROOT/'samples',app/'samples',dirs_exist_ok=True)
    shutil.copytree(ROOT/'assets',app/'assets',dirs_exist_ok=True)
    tests=app/'tools';tests.mkdir(exist_ok=True)
    for name in ('test_native.py','test_project.py','test_chart.py','test_chart_validation.py','test_viewport.py','test_region_context.py','test_geometry.py','test_frames.py','test_frame_detection.py','test_studio.py','test_workflow.py','test_input_color.py','test_wide_color.py','test_output_color.py','test_local.py','test_delivery.py','test_streaming.py','test_long_paths.py','test_navigation.py','test_strip_base.py','test_line_straighten.py','test_strip_alignment.py','test_perspective.py','test_darkroom.py','test_large_film.py','test_multi_drop.py','test_photo_navigation.py','test_batch_workflow.py','test_batch_ui.py','test_switch_cache.py','test_efficiency.py','test_release_paths.py','test_i18n.py','test_metering.py','test_tone.py','test_hdr_merge.py','test_hdr_display.py','test_hdr_ui.py','test_hdr_canvas.py','test_hdr_jxr.py','test_import_reveal.py','test_startup.py','bench_large_film.py','bench_photo_switch.py','bench_memory.py','make_calibration_demo.py','selfcheck.py','test_core_compatibility.py'):
        shutil.copy2(ROOT/'tools'/name,tests/name)
    shutil.copytree(ROOT/'THIRD_PARTY',BUNDLE/'THIRD_PARTY',dirs_exist_ok=True)
    (BUNDLE/'THIRD_PARTY'/'PyInstaller-COPYING.txt').unlink(missing_ok=True)
    (BUNDLE/'THIRD_PARTY'/'NOTICE.txt').write_text('''FilmInvert Native compatible runtime distribution

This distribution uses unmodified official CPython 3.13.17 interpreter binaries,
NumPy 2.3.5, Pillow 12.3.0, rawpy 0.27.1, PySide6 Essentials / Shiboken6 6.12.0.
Official download URLs and SHA256 values are in ../dependencies.json.
No PyInstaller bootloader or frozen Python executable is included in this edition.
Application Python sources remain in ../app/; Qt DLLs are dynamically linked and
remain replaceable with compatible builds. Original upstream licenses and notices
are retained in this directory.

Qt and PySide corresponding sources:
https://code.qt.io/cgit/qt/qtbase.git/tag/?h=v6.12.0
https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.12.0
''',encoding='utf-8')
    shutil.copy2(runtime/'LICENSE.txt',BUNDLE/'THIRD_PARTY'/'Python-LICENSE.txt')
    for directory in site.glob('*.dist-info'):
        for file in directory.rglob('*'):
            if file.is_file() and ('license' in file.as_posix().lower() or file.name=='METADATA'):
                target=BUNDLE/'THIRD_PARTY'/directory.name/file.relative_to(directory)
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,target)
    (BUNDLE/'dependencies.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
    (BUNDLE/'portable-layout.json').write_text(json.dumps({'format':'film-invert-portable-runtime','version':VERSION,
        'python':'3.13.17','architecture':'Windows x64','entry':'runtime/pythonw.exe -I -m film_invert_launcher'},indent=2),encoding='utf-8')
    (BUNDLE/'启动胶片工作室.lnk').unlink(missing_ok=True)  # Retired relative-only test shortcut.
    (BUNDLE/'启动胶片工作室.cmd').write_bytes(('@echo off\r\nstart "" "%~dp0runtime\\pythonw.exe" -I -m film_invert_launcher %*\r\n').encode('ascii'))
    if VERSION in ('3.0.1','3.1.0-rc1','3.1.0-rc2','3.1.0-rc3','3.1.0-rc4','3.1.0-rc5','3.1.0-rc6','3.1.0-rc7','3.1.0-rc8','3.1.0-rc9','3.1.0-rc10','3.1.0-rc11','3.1.0-rc12','3.1.0-rc13','3.1.0-rc14','3.1.0'):(BUNDLE/'Launch FilmInvert.cmd').write_bytes(('@echo off\r\nstart "" "%~dp0runtime\\pythonw.exe" -I -m film_invert_launcher %*\r\n').encode('ascii'))
    (BUNDLE/'启动诊断.cmd').write_bytes(('@echo off\r\n"%~dp0runtime\\python.exe" -I -m film_invert_launcher --diagnose\r\necho.\r\necho See the diagnostic report path printed above.\r\npause\r\n').encode('ascii'))
    refresh_manifest()
    print('Built compatibility distribution:',BUNDLE)

def refresh_manifest():
    files={}
    for path in sorted(BUNDLE.rglob('*')):
        relative=path.relative_to(BUNDLE)
        if not path.is_file() or relative.parts[0] in ('data','out') or '__pycache__' in relative.parts or relative.parts[:2]==('app','build'):
            continue
        if path.name=='file-manifest.json' or path.suffix in ('.log','.lnk'):continue
        files[relative.as_posix()]=hashlib.file_digest(path.open('rb'),'sha256').hexdigest()
    application=[name for name in files if name.startswith('app/') and name.endswith('.py')]
    (BUNDLE/'file-manifest.json').write_text(json.dumps({'format':'film-invert-file-manifest','version':1,
        'files':files,'application_files':application},indent=2),encoding='utf-8')

if __name__=='__main__':build()
