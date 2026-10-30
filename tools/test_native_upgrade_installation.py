"""Real old-to-new installation in an explicitly restricted workspace scope."""
from pathlib import Path
import json,os,subprocess,sys,winreg,time,uuid,hashlib
ROOT=Path(__file__).resolve().parents[1];BASE=ROOT/'build/native-upgrade-test'/uuid.uuid4().hex[:8];BASE.mkdir(parents=True)
new=BASE/'新版本';old=BASE/'旧版本';local=BASE/'用户数据'
env=dict(os.environ,LOCALAPPDATA=str(local),QT_QPA_PLATFORM='offscreen')
VERSION=(ROOT/'VERSION.txt').read_text('utf-8').strip()
key=r'Software\Microsoft\Windows\CurrentVersion\Uninstall\FilmInvertNative.Stable.'
available=None
for v in ('3.1.0','3.0.1'):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,key+v+'_is1'):continue
    except FileNotFoundError:available=v;break
if not available:raise RuntimeError('All test versions already installed; refusing to alter them')
try:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,key+VERSION+'_is1'):raise RuntimeError('rc3 already installed')
except FileNotFoundError:pass
def run(exe,*args):
    r=subprocess.run([str(exe),*map(str,args)],env=env,cwd=BASE,timeout=240,capture_output=True)
    if r.returncode:raise RuntimeError(str(exe)+' failed '+str(r.returncode))
try:
    run(next((ROOT/'release').glob(f'FilmInvert-Native-{available}-setup-win64-*.exe')),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-',f'/DIR={old}','/TASKS=',f'/LOG={BASE}/old.log')
    assert (old/'runtime/pythonw.exe').exists()
    # Unowned files inside the old install and separate profile must survive.
    extra=old/'用户额外文件.txt';extra.write_text('keep this project','utf-8')
    local.mkdir(exist_ok=True);settings=local/'existing-user-settings.json';settings.write_text('keep settings','utf-8')
    run(ROOT/f'release/FilmInvert-Native-{VERSION}-setup-win64-20261030.exe','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-',f'/DIR={new}','/TASKS=startmenu,cleanupold',f'/UPGRADESCOPE={BASE}',f'/LOG={BASE}/new.log')
    report=json.loads((new/'upgrade-result.json').read_text('utf-8'));assert report['verified'] and available in report['removed'],report
    assert not (old/'runtime/pythonw.exe').exists() and extra.read_text('utf-8')=='keep this project' and settings.read_text('utf-8')=='keep settings'
    setup=ROOT/f'release/FilmInvert-Native-{VERSION}-setup-win64-20261030.exe'
    result={'status':'pass','installer_sha256':hashlib.sha256(setup.read_bytes()).hexdigest(),'old_version':available,'new_version':VERSION,'report':report,'old_program_removed':True,'old_extra_user_file_preserved':True,'profile_preserved':True,'restricted_to_workspace':True}
    (ROOT/'build/native-upgrade-test/result.json').write_text(json.dumps(result,indent=2),'utf-8')
    print('PASS actual registered old installer -> verified new installer -> old uninstaller; extra user file and separate settings preserved')
finally:
    for folder in (new,old):
        un=folder/'uninstall/unins000.exe'
        if un.exists():run(un,'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART')
