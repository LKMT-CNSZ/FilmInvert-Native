"""Transparent portable entry point run by the unmodified PSF-signed interpreter."""
from pathlib import Path
import ctypes
import hashlib
import importlib
import json
import os
import platform
import sys
import tempfile
import traceback

APP=Path(__file__).resolve().parent
BUNDLE=APP.parent
DLL_HANDLES=[]

def configure():
    runtime=BUNDLE/'runtime';site=runtime/'Lib'/'site-packages';windows=Path(os.environ.get('WINDIR','C:/Windows'))
    directories=[runtime,site/'PySide6',site/'shiboken6',site/'rawpy',site/'numpy.libs']
    if os.name=='nt':
        for path in directories:
            if path.is_dir():DLL_HANDLES.append(os.add_dll_directory(str(path)))
        os.environ['PATH']=os.pathsep.join(map(str,[*directories,windows/'System32',windows]))
        try:ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('FilmInvert.Native.Compat')
        except OSError:pass
    os.environ['QT_PLUGIN_PATH']=str(site/'PySide6'/'plugins')
    os.environ['QT_QPA_PLATFORM_PLUGIN_PATH']=str(site/'PySide6'/'plugins'/'platforms')
    sys.dont_write_bytecode=True

def report_path():
    from app_paths import select_profile
    return select_profile(BUNDLE)/'startup-diagnostics.json'


def verify_bundle():
    manifest=json.loads((BUNDLE/'file-manifest.json').read_text('utf-8'))
    errors=[]
    for name,expected in manifest['files'].items():
        path=BUNDLE/name
        if not path.is_file():errors.append({'file':name,'problem':'missing'})
        elif hashlib.file_digest(path.open('rb'),'sha256').hexdigest()!=expected:
            errors.append({'file':name,'problem':'hash_mismatch'})
    return {'checked':len(manifest['files']),'errors':errors}

def diagnose():
    target=report_path()
    report={'application':json.loads((BUNDLE/'portable-layout.json').read_text('utf-8'))['version'],'python':sys.version,'os':platform.platform(),
            'data_directory':str(target.parent),'machine':platform.machine(),'isolated':bool(sys.flags.isolated),
            'runtime':str(Path(sys.executable).relative_to(BUNDLE)),'imports':{}}
    try:
        report['integrity']=verify_bundle()
        if report['integrity']['errors']:raise RuntimeError('Missing or modified distribution files')
        for name in ('numpy','PIL','rawpy','PySide6','shiboken6'):
            module=importlib.import_module(name);report['imports'][name]=getattr(module,'__version__','available')
        from hdr_ultrahdr import codec
        report['imports']['libultrahdr']=codec().ultrahdr_version()
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PySide6.QtWidgets import QApplication
        app=QApplication.instance() or QApplication([])
        import native_app
        with tempfile.TemporaryDirectory(dir=target.parent,prefix='filminvert-diagnostic-') as profile:
            window=native_app.MainWindow(profile)
            report['native_ui']={'tabs':window.tabs.count(),'app_dir_matches_bundle':native_app.APP_DIR==BUNDLE}
            window.close();app.processEvents()
        import desktop_model as model
        source=APP/'samples'/'calibration-demo.filmproj'
        result=model.open_project(source)
        image=model.preview(result['params'])['pixels']
        report['sample']={'width':image.shape[1],'height':image.shape[0],
                          'calibration_restored':result['params']['calibration'] is not None}
        report['status']='pass'
    except Exception:
        report['status']='failed';report['error']=traceback.format_exc()
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if sys.stdout:print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['status']=='pass' else 1

def main():
    configure()
    if '--diagnose' in sys.argv:return diagnose()
    try:
        # Source-file integrity helps distinguish incomplete extraction or changed
        # application files; it is not a malware verdict or a publisher signature.
        manifest=json.loads((BUNDLE/'file-manifest.json').read_text('utf-8'))
        for name in manifest['application_files']:
            path=BUNDLE/name
            if not path.is_file() or hashlib.file_digest(path.open('rb'),'sha256').hexdigest()!=manifest['files'][name]:
                raise RuntimeError('Application file missing or changed: '+name)
        try:
            if (BUNDLE/'installed-layout.json').exists():raise OSError('Installed shortcuts are managed by Setup')
            from portable_shortcut import create
            create(BUNDLE)
        except OSError:
            pass  # Read-only folders still launch; the portable CMD remains usable.
        from native_startup import run
        return run(sys.argv[1:],BUNDLE)
    except Exception:
        error=traceback.format_exc();target=report_path()
        target.write_text(json.dumps({'status':'startup_failed','python':sys.version,'error':error},ensure_ascii=False,indent=2),encoding='utf-8')
        if os.name=='nt':
            ctypes.windll.user32.MessageBoxW(None,'程序未能启动。\n\n'+error.strip().splitlines()[-1]+'\n\n诊断文件：'+str(target),
                                            'FilmInvert Native · 启动诊断',0x10)
        elif sys.stderr:sys.stderr.write(error)
        return 1

if __name__=='__main__':raise SystemExit(main())
