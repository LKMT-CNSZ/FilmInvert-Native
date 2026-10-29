"""Exercise the final installer only in a new isolated Chinese/space directory."""
from pathlib import Path
import csv,hashlib,json,os,shutil,subprocess,sys,time,uuid,winreg
sys.path.insert(0,str(Path(__file__).resolve().parent))
from build_compat import ROOT,VERSION,RELEASE_DATE
assert VERSION in ('3.0.0','3.0.1','3.1.0-rc1','3.1.0-rc2','3.1.0-rc3','3.1.0-rc4','3.1.0-rc5','3.1.0-rc6','3.1.0-rc7','3.1.0-rc8','3.1.0-rc9','3.1.0-rc10','3.1.0-rc11','3.1.0-rc12','3.1.0-rc13','3.1.0-rc14','3.1.0')
setup=ROOT/'release'/f'FilmInvert-Native-{VERSION}-setup-win64-{RELEASE_DATE}.exe'
keyname=rf'Software\Microsoft\Windows\CurrentVersion\Uninstall\FilmInvertNative.Stable.{VERSION}_is1'
try:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,keyname):pass
except FileNotFoundError:pass
else:raise RuntimeError('Existing current-user 3.0.0 installation detected; refusing install test')
parent=ROOT/'build/stable-install-test'/('中文 空格-'+uuid.uuid4().hex[:8]);parent.mkdir(parents=True)
assert parent.resolve().is_relative_to((ROOT/'build/stable-install-test').resolve())
install=parent/'安装 程序';foreign=parent/'无关 工作目录';foreign.mkdir()
local=parent/'独立 用户目录';profile=local/'FilmInvertNative/data'
fixture=parent/'旧版 合成兼容';fixture.mkdir()
source=fixture/'合成 原片.tif';shutil.copy2(ROOT/'samples/pro-light-demo.tif',source)
source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
keep=parent/'已有 成品.tif';keep.write_bytes(b'existing-finished-output-do-not-touch')
keep_hash=hashlib.sha256(keep.read_bytes()).hexdigest()
old_bundle=ROOT/'dist/3.0.0-rc13-compat/FilmInvertNative'
if not old_bundle.exists():raise RuntimeError('rc13 shipped runtime needed for this release compatibility test; absent baseline is not a pass')
env=dict(os.environ);env.update(LOCALAPPDATA=str(local),PYTHONHOME=str(foreign/'bad'),PYTHONPATH=str(foreign/'bad'),QT_QPA_PLATFORM='offscreen',PATH=os.environ.get('WINDIR','C:/Windows')+'/System32;'+os.environ.get('WINDIR','C:/Windows'))
def run(args,label,timeout=180,success=True):
    result=subprocess.run(list(map(str,args)),cwd=foreign,env=env,capture_output=True,encoding='utf-8',errors='replace',timeout=timeout)
    (parent/(label+'.process.log')).write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    if success and result.returncode:raise AssertionError(label+': '+str(result.returncode)+'\n'+result.stdout+result.stderr)
    return result
run([old_bundle/'runtime/python.exe','-I','-X','utf8',ROOT/'tools/stable_compat_probe.py','baseline',fixture],'baseline')
shutil.copytree(fixture/'rc13-data',profile)
old_profile_hash=hashlib.sha256((fixture/'rc13-data/studio/library.sqlite3').read_bytes()).hexdigest()
group='FilmInvert Native '+VERSION
existing_group=Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs'/group
if existing_group.exists():raise RuntimeError('Existing stable start menu group detected; refusing to alter it')
installed=False;denied=False
try:
    blocked=parent/'已有目录';blocked.mkdir();sentinel=blocked/'用户文件.txt';sentinel.write_text('must survive',encoding='utf-8')
    result=run([setup,'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-',f'/DIR={blocked}',f'/LOG={parent / "blocked-install.log"}'],'blocked',success=False)
    assert result.returncode!=0 and sentinel.read_text('utf-8')=='must survive' and list(blocked.iterdir())==[sentinel]
    run([setup,'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-',f'/DIR={install}',f'/GROUP={group}','/TASKS=',f'/LOG={parent / "install.log"}'],'install')
    installed=True
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,keyname) as key:
        assert Path(winreg.QueryValueEx(key,'InstallLocation')[0]).resolve()==install.resolve()
    assert json.loads((install/'installed-layout.json').read_text('utf-8-sig'))['version']==VERSION
    # Read the actual installed shortcut; no private shortcuts are changed.
    ps=parent/'check-shortcut.ps1'
    ps.write_text('''$ErrorActionPreference='Stop'
$shell=New-Object -ComObject WScript.Shell
$folder=Join-Path ($shell.SpecialFolders.Item('Programs')) $env:FILMINVERT_TEST_GROUP
$path=Join-Path $folder ($env:FILMINVERT_TEST_GROUP + '.lnk')
$link=$shell.CreateShortcut($path)
@{exists=(Test-Path -LiteralPath $path);target=$link.TargetPath;arguments=$link.Arguments;working=$link.WorkingDirectory;folder=$folder;icon=$link.IconLocation}|ConvertTo-Json -Compress
''',encoding='utf-8-sig')
    env['FILMINVERT_TEST_GROUP']=group
    shortcut=json.loads(run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',ps],'shortcut').stdout)
    assert shortcut['exists'] and Path(shortcut['target']).resolve()==(install/'runtime/pythonw.exe').resolve()
    assert shortcut['arguments']=='-I -m film_invert_launcher' and Path(shortcut['working']).resolve()==install.resolve()
    if VERSION in ('3.1.0-rc14','3.1.0'):
        icon_file=install/'app/assets/film-invert.ico'
        icon_name='filminvert-native-'+hashlib.sha256(icon_file.read_bytes()).hexdigest()[:12]+'.ico'
        assert Path(shortcut['icon'].rsplit(',',1)[0]).resolve()==(icon_file.parent/icon_name).resolve()
    manifest=json.loads((install/'file-manifest.json').read_text('utf-8'))['files']
    for name,digest in manifest.items():assert hashlib.sha256((install/name).read_bytes()).hexdigest()==digest,name
    # Real NTFS deny-write ACL: data must stay outside the installation.
    who=subprocess.run(['whoami.exe','/user','/fo','csv','/nh'],capture_output=True,encoding='utf-8',check=True)
    sid=list(csv.reader(who.stdout.strip().splitlines()))[0][-1]
    run(['icacls.exe',install,'/deny',f'*{sid}:(OI)(CI)(WD,AD,WEA,WA)'],'deny-write');denied=True
    refusal=run([install/'runtime/python.exe','-I','-c',"from pathlib import Path;Path(__import__('sys').argv[1]).write_text('probe')",install/'must-not-write.txt'],'write-probe',success=False)
    assert refusal.returncode!=0 and not (install/'must-not-write.txt').exists()
    run([install/'runtime/python.exe','-I','-X','utf8','-m','film_invert_launcher','--diagnose'],'diagnostic')
    diagnostic=json.loads((profile/'startup-diagnostics.json').read_text('utf-8'))
    assert diagnostic['status']=='pass' and diagnostic['application']==VERSION
    run([install/'runtime/pythonw.exe','-I','-X','utf8',ROOT/'tools/stable_compat_probe.py','stable',fixture],'native-launch')
    report=json.loads((fixture/'native-launch.json').read_text('utf-8'));assert report['status']=='pass' and report['profile_outside_install']
    assert not (install/'data').exists() and not (install/'out').exists()
    run(['icacls.exe',install,'/remove:d',f'*{sid}'],'restore-acl');denied=False
    user_extra=install/'用户 保留文件.txt';user_extra.write_text('user extra file',encoding='utf-8')
    preserved=(profile/'session.json').read_bytes();library=(profile/'studio/library.sqlite3').read_bytes()
    uninstaller=install/'uninstall/unins000.exe';assert uninstaller.is_file()
    run([uninstaller,'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',f'/LOG={parent / "uninstall.log"}'],'uninstall')
    deadline=time.monotonic()+40
    while (install/'runtime/pythonw.exe').exists() and time.monotonic()<deadline:time.sleep(.25)
    assert not (install/'runtime/pythonw.exe').exists()
    assert (profile/'session.json').read_bytes()==preserved and (profile/'studio/library.sqlite3').read_bytes()==library
    assert user_extra.read_text('utf-8')=='user extra file'
    assert source_hash==hashlib.sha256(source.read_bytes()).hexdigest() and keep_hash==hashlib.sha256(keep.read_bytes()).hexdigest()
    assert old_profile_hash==hashlib.sha256((fixture/'rc13-data/studio/library.sqlite3').read_bytes()).hexdigest()
    try:winreg.OpenKey(winreg.HKEY_CURRENT_USER,keyname)
    except FileNotFoundError:pass
    else:raise AssertionError('uninstall registry entry remains')
    assert not (Path(shortcut['folder'])/('FilmInvert Native '+VERSION+'.lnk')).exists()
    installed=False
    result={'version':VERSION,'date':'2026-10-30' if VERSION=='3.1.0' else '2026-10-29','status':'pass','installer_sha256':hashlib.sha256(setup.read_bytes()).hexdigest(),
            'checks':['nonempty_target_refused','Chinese_space_install','start_menu_shortcut_target','uninstall_registry','installed_manifest_matches','actual_NTFS_write_denied','isolated_diagnostic','launcher_Qt_event_loop_offscreen','rc13_project_recipe_session_library_restored','rc13_vs_stable_full_uint16_TIFF_equal','old_frozen_queue_engine_refused','uninstall_removes_program_and_entry','uninstall_preserves_profile_library_original_finished_output_and_extra_file','old_rc13_fixture_untouched'],
            'compatibility':report,'foreign_cwd':True,'invalid_system_python':True,'test_location':'build/stable-install-test (isolated)','no_D_drive_install_changed':True}
    (ROOT/'release'/f'{VERSION}-交付验证.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS final installer install/launch/compatibility/uninstall; user data, original, old data and outputs preserved')
    print('Evidence folder:',parent)
finally:
    if denied:run(['icacls.exe',install,'/remove:d',f'*{sid}'],'cleanup-acl',success=False)
    if installed and (install/'uninstall/unins000.exe').exists():run([install/'uninstall/unins000.exe','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'],'cleanup-uninstall',success=False)
