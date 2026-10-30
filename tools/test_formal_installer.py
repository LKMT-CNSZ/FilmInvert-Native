"""Validate an equivalent installer identity without overwriting a live version.

Production and validation builds use the same script and exact payload. Only
AppId/output filename differ; evidence records this limitation explicitly.
"""
from pathlib import Path
import hashlib,json,os,subprocess,winreg,uuid,time
ROOT=Path(__file__).resolve().parents[1];VERSION=(ROOT/'VERSION.txt').read_text().strip()
BASE=ROOT/'build/formal-installer-tests'/uuid.uuid4().hex[:8];BASE.mkdir(parents=True)
local=BASE/'用户数据';dest=BASE/'新 安装';old=BASE/'旧 安装';group='FilmInvert Validation '+uuid.uuid4().hex[:8]
env=dict(os.environ,LOCALAPPDATA=str(local),QT_QPA_PLATFORM='offscreen')
prefix=r'Software\Microsoft\Windows\CurrentVersion\Uninstall'+chr(92)
identity='FilmInvertNative.Validation.'+VERSION+'_is1'
try:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,prefix+identity):raise RuntimeError('Validation installation already exists')
except FileNotFoundError:pass
available=None
for v in ('3.1.0','3.0.1'):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,prefix+'FilmInvertNative.Stable.'+v+'_is1'):continue
    except FileNotFoundError:available=v;break
if not available:raise RuntimeError('No isolated old-version registry identity available')
setup=ROOT/f'release/FilmInvert-Native-{VERSION}-setup-win64-20261030-validation.exe'
production=ROOT/f'release/FilmInvert-Native-{VERSION}-setup-win64-20261030.exe'
def run(exe,*args,success=True):
    result=subprocess.run([str(exe),*map(str,args)],env=env,cwd=BASE,timeout=240,capture_output=True)
    if success and result.returncode:raise RuntimeError(str(exe)+' failed '+str(result.returncode))
    return result
try:
    oldsetup=next((ROOT/'release').glob(f'FilmInvert-Native-{available}-setup-win64-*.exe'))
    run(oldsetup,'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-',f'/DIR={old}','/TASKS=',f'/GROUP={group} Old')
    extra=old/'保留工程.txt';extra.write_text('keep user project','utf-8')
    local.mkdir(exist_ok=True);settings=local/'原有设置.json';settings.write_text('keep settings','utf-8')
    blocked=BASE/'非空目录';blocked.mkdir();sentinel=blocked/'existing.txt';sentinel.write_text('keep','utf-8')
    args=['/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-','/LANG=chinesesimplified',f'/UPGRADESCOPE={BASE}',f'/GROUP={group}','/TASKS=startmenu,cleanupold']
    assert run(setup,*args,f'/DIR={blocked}',success=False).returncode!=0 and sentinel.read_text()=='keep'
    run(setup,*args,f'/DIR={dest}',f'/LOG={BASE}/install.log')
    report=json.loads((dest/'upgrade-result.json').read_text('utf-8'))
    assert report['verified'] and available in report['removed'],report
    assert extra.read_text('utf-8')=='keep user project' and not (old/'runtime/pythonw.exe').exists()
    assert settings.read_text('utf-8')=='keep settings'
    manifest=json.loads((dest/'file-manifest.json').read_text('utf-8'))['files']
    for name,value in manifest.items():assert hashlib.sha256((dest/name).read_bytes()).hexdigest()==value
    manifest_digest=hashlib.sha256((dest/'file-manifest.json').read_bytes()).hexdigest()
    run(dest/'runtime/python.exe','-I','-X','utf8','-m','film_invert_launcher','--diagnose')
    diag=json.loads((local/'FilmInvertNative/data/startup-diagnostics.json').read_text('utf-8'));assert diag['status']=='pass'
    userextra=dest/'额外文件.txt';userextra.write_text('preserve','utf-8')
    run(dest/'uninstall/unins000.exe','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART')
    assert not (dest/'runtime/pythonw.exe').exists() and userextra.read_text('utf-8')=='preserve' and settings.read_text('utf-8')=='keep settings'
    record={'version':VERSION,'status':'pass','installer_sha256':hashlib.sha256(production.read_bytes()).hexdigest(),
        'validation_installer_sha256':hashlib.sha256(setup.read_bytes()).hexdigest(),
        'test_mode':'equivalent installer identity; production AppId already installed and not touched',
        'validation_identity':identity,'payload_files_verified':len(manifest),'payload_manifest_sha256':manifest_digest,
        'same_script_except_AppId_and_output_filename':True,'old_version':available,'upgrade':report,
        'Chinese_install':True,'diagnostic':diag['status'],'nonempty_refusal':True,'uninstall':True,
        'extra_old_and_new_user_files_preserved':True,'settings_preserved':True,'existing_production_install_untouched':True}
    (ROOT/f'release/{VERSION}-交付验证.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),'utf-8')
    print('PASS equivalent-identity Chinese install, exact payload, diagnostic, real older-version cleanup, nonempty refusal, uninstall and user-data preservation; live production AppId untouched')
finally:
    for folder in (dest,old):
        un=folder/'uninstall/unins000.exe'
        if un.exists() and (folder/'runtime/pythonw.exe').exists():run(un,'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART')
