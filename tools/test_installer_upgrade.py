"""Post-install safety boundaries without touching installed applications."""
from pathlib import Path
import sys,tempfile,json,hashlib
from unittest.mock import patch,Mock
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import installer_upgrade as u

def fixture(root,v):
    root.mkdir();(root/'VERSION.txt').write_text(v,'utf-8');(root/'runtime').mkdir();(root/'runtime/pythonw.exe').write_bytes(b'fixture')
    (root/'installed-layout.json').write_text(json.dumps({'format':'film-invert-installed-runtime','version':v}),'utf-8')
    files={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    (root/'file-manifest.json').write_text(json.dumps({'files':files}),'utf-8');(root/'uninstall').mkdir();(root/'uninstall/unins000.exe').write_bytes(b'fixture')
    return dict(InstallLocation=str(root),DisplayVersion=v,DisplayName='FilmInvert Native '+v,UninstallString='"'+str(root/'uninstall/unins000.exe')+'"',key='FilmInvertNative.Stable.'+v+'_is1')

folder=ROOT/'build/installer-unit';folder.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=folder) as t:
    root=Path(t);new=root/'new';fixture(new,'3.2.0-rc3');old=root/'old';item=fixture(old,'3.2.0-rc2')
    other=root/'newer';future=fixture(other,'3.3.0')
    assert u.version('3.2.0-rc10')>u.version('3.2.0-rc3') and u.version('3.2.0')>u.version('3.2.0-rc999')
    with patch.object(u,'registered',return_value=[item,future]),patch.object(u,'running',return_value=True),patch.object(u.subprocess,'run') as run:
        report=u.execute(new,True);assert report['verified'] and report['retained'][0]['reason']=='running' and not run.called
    with patch.object(u,'registered',return_value=[item]),patch.object(u.subprocess,'run') as run:
        assert not u.candidates(new,'3.2.0-rc3',root/'unrelated')
        report=u.execute(new,False);assert report['verified'] and not run.called
    with patch.object(u,'registered',return_value=[dict(item,UninstallString='cmd /c anything')]):assert not u.candidates(new,'3.2.0-rc3')
    with patch.object(u,'registered',return_value=[item]),patch.object(u,'running',return_value=False),patch.object(u.subprocess,'run',return_value=Mock(returncode=1)):
        report=u.execute(new,True);assert report['verified'] and report['retained'] and not report['removed']
    (new/'runtime/pythonw.exe').write_bytes(b'modified')
    with patch.object(u,'registered') as registry,patch.object(u.subprocess,'run') as run:
        report=u.execute(new,True);assert not report['verified'] and not registry.called and not run.called
print('PASS full-file verification before cleanup; numeric version ordering, new/same/future/unmanaged exclusion, scope restriction, shell rejection, running/failed retention, disabled cleanup')
