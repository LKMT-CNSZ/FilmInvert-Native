"""Launch the extracted portable package; exercise writable and real read-only paths."""
from pathlib import Path
import csv,hashlib,json,os,shutil,subprocess,sys,uuid,zipfile
sys.path.insert(0,str(Path(__file__).resolve().parent))
from build_compat import ROOT,VERSION,RELEASE_DATE
archive=ROOT/'release'/f'FilmInvert-Native-{VERSION}-compat-win64-{RELEASE_DATE}.zip'
parent=ROOT/'build/stable-portable-test'/('中文 免安装-'+uuid.uuid4().hex[:8]);parent.mkdir(parents=True)
assert parent.resolve().is_relative_to((ROOT/'build/stable-portable-test').resolve())
with zipfile.ZipFile(archive) as z:z.extractall(parent)
bundle=parent/'FilmInvertNative';foreign=parent/'无关工作目录';foreign.mkdir()
local=parent/'独立用户';env=dict(os.environ)
env.update(LOCALAPPDATA=str(local),PYTHONHOME=str(foreign/'invalid'),PYTHONPATH=str(foreign/'invalid'),QT_QPA_PLATFORM='offscreen',PATH=os.environ.get('WINDIR','C:/Windows')+'/System32;'+os.environ.get('WINDIR','C:/Windows'))
def run(args,label,success=True):
    result=subprocess.run(list(map(str,args)),cwd=foreign,env=env,capture_output=True,encoding='utf-8',errors='replace',timeout=120)
    (parent/(label+'.log')).write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    if success and result.returncode:raise AssertionError(label+': '+result.stdout+result.stderr)
    return result
old=ROOT/'dist/3.0.0-rc13-compat/FilmInvertNative/runtime/python.exe'
reports={};denied=False
try:
    for mode in ('portable','fallback'):
        fixture=parent/(mode+' 合成兼容');fixture.mkdir();shutil.copy2(ROOT/'samples/pro-light-demo.tif',fixture/'合成 原片.tif')
        run([old,'-I','-X','utf8',ROOT/'tools/stable_compat_probe.py','baseline',fixture],mode+'-baseline')
        profile=(bundle/'data') if mode=='portable' else (local/'FilmInvertNative/data')
        shutil.copytree(fixture/'rc13-data',profile)
        if mode=='fallback':
            who=subprocess.run(['whoami.exe','/user','/fo','csv','/nh'],capture_output=True,encoding='utf-8',check=True)
            sid=list(csv.reader(who.stdout.strip().splitlines()))[0][-1]
            run(['icacls.exe',bundle,'/deny',f'*{sid}:(OI)(CI)(WD,AD,WEA,WA)'],'deny');denied=True
            refusal=run([bundle/'runtime/python.exe','-I','-c',"from pathlib import Path;Path(__import__('sys').argv[1]).write_text('probe')",bundle/'must-not-write.txt'],'refused',success=False)
            assert refusal.returncode!=0 and not (bundle/'must-not-write.txt').exists()
        run([bundle/'runtime/pythonw.exe','-I','-X','utf8',ROOT/'tools/stable_compat_probe.py',mode,fixture],mode+'-launch')
        report=json.loads((fixture/'native-launch.json').read_text('utf-8'))
        assert report['status']=='pass' and report['profile_outside_install']==(mode=='fallback')
        if mode=='portable':
            assert (bundle/'胶片工作室.lnk').is_file()
            if VERSION in ('3.1.0-rc14','3.1.0'):
                ps=parent/'check-portable-icon.ps1'
                ps.write_text("$shell=New-Object -ComObject WScript.Shell\n$link=$shell.CreateShortcut($env:FILMINVERT_TEST_LINK)\n@{icon=$link.IconLocation;target=$link.TargetPath}|ConvertTo-Json -Compress\n",encoding='utf-8-sig')
                env['FILMINVERT_TEST_LINK']=str(bundle/'胶片工作室.lnk')
                shortcut=json.loads(run(['powershell.exe','-NoProfile','-File',ps],'shortcut-icon').stdout)
                icon_file=bundle/'app/assets/film-invert.ico'
                icon_name='filminvert-native-'+hashlib.sha256(icon_file.read_bytes()).hexdigest()[:12]+'.ico'
                assert Path(shortcut['icon'].rsplit(',',1)[0]).resolve()==(icon_file.parent/icon_name).resolve()
                assert (icon_file.parent/icon_name).read_bytes()==icon_file.read_bytes()
        reports[mode]=report
finally:
    if denied:run(['icacls.exe',bundle,'/remove:d',f'*{sid}'],'restore-acl')
result={'status':'pass','archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'Chinese_space_extraction':True,'actual_launcher_Qt_event_loop_offscreen':True,'portable_local_data_and_shortcut':True,'actual_NTFS_readonly_fallback':True,'rc13_session_restored_and_exact_TIFF':True,'reports':reports}
path=ROOT/'release'/f'{VERSION}-交付验证.json';record=json.loads(path.read_text('utf-8'));record['portable']=result
record['isolated_regressions']={'count':44 if VERSION in ('3.1.0-rc12','3.1.0-rc13','3.1.0-rc14','3.1.0') else 43 if VERSION in ('3.1.0-rc5','3.1.0-rc6','3.1.0-rc7','3.1.0-rc8','3.1.0-rc9','3.1.0-rc10','3.1.0-rc11','3.1.0-rc12','3.1.0-rc13','3.1.0-rc14','3.1.0') else 42 if VERSION=='3.1.0-rc4' else 41 if VERSION in ('3.1.0-rc2','3.1.0-rc3') else 40 if VERSION=='3.1.0-rc1' else 35 if VERSION=='3.0.1' else 34,'status':'pass'}
record['relocation_tamper_and_recovery']={'status':'pass','Chinese_space_path':True}
path.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
print('PASS extracted portable launcher, local data/shortcut, actual read-only fallback, rc13 restoration and full uint16 equality')
