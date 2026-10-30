"""Post-install verification and conservative current-user registered upgrades.

Never deletes directories. Only a validated prior Inno uninstaller removes its
own files. Unmanaged, modified, running and newer installations are retained.
"""
from pathlib import Path
import argparse,ctypes,hashlib,json,os,re,subprocess,sys,winreg
KEY=r'Software\Microsoft\Windows\CurrentVersion\Uninstall'

def version(value):
    m=re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?',value)
    if not m:raise ValueError('Unknown version')
    return (*map(int,m.group(1,2,3)),1 if m[4] is None else 0,int(m[4] or 0))

def verify(root):
    root=Path(root).resolve();v=(root/'VERSION.txt').read_text('utf-8').strip();version(v)
    layout=json.loads((root/'installed-layout.json').read_text('utf-8-sig'))
    if layout.get('format')!='film-invert-installed-runtime' or layout.get('version')!=v:raise ValueError('Installed layout mismatch')
    manifest=json.loads((root/'file-manifest.json').read_text('utf-8'))['files']
    for name,digest in manifest.items():
        file=(root/name).resolve()
        if not file.is_relative_to(root) or not file.is_file():raise ValueError('Missing or invalid installed file: '+name)
        if hashlib.file_digest(file.open('rb'),'sha256').hexdigest()!=digest:raise ValueError('Installed file hash mismatch: '+name)
    return v

def registered():
    found={}
    for flag in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
        try:parent=winreg.OpenKey(winreg.HKEY_CURRENT_USER,KEY,0,winreg.KEY_READ|flag)
        except OSError:continue
        with parent:
            for i in range(winreg.QueryInfoKey(parent)[0]):
                name=winreg.EnumKey(parent,i)
                if not re.fullmatch(r'FilmInvertNative\.Stable\.[0-9.a-z-]+_is1',name):continue
                try:
                    with winreg.OpenKey(parent,name) as k:
                        values={n:winreg.QueryValueEx(k,n)[0] for n in ('InstallLocation','DisplayVersion','DisplayName','UninstallString')}
                    values['key']=name;found[os.path.normcase(values['InstallLocation'])]=values
                except OSError:continue
    return list(found.values())

def running(root):
    from ctypes import wintypes as W
    api=ctypes.WinDLL('kernel32',use_last_error=True);ps=ctypes.WinDLL('psapi')
    api.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];api.OpenProcess.restype=W.HANDLE
    api.CloseHandle.argtypes=[W.HANDLE];api.QueryFullProcessImageNameW.argtypes=[W.HANDLE,W.DWORD,W.LPWSTR,ctypes.POINTER(W.DWORD)]
    ids=(W.DWORD*65536)();size=W.DWORD()
    if not ps.EnumProcesses(ids,ctypes.sizeof(ids),ctypes.byref(size)):raise OSError('Process enumeration failed')
    for pid in ids[:size.value//ctypes.sizeof(W.DWORD)]:
        handle=api.OpenProcess(0x1000,False,pid)
        if not handle:continue
        try:
            buf=ctypes.create_unicode_buffer(32768);count=W.DWORD(len(buf))
            if api.QueryFullProcessImageNameW(handle,0,buf,ctypes.byref(count)) and Path(buf.value).resolve().is_relative_to(root):return True
        finally:api.CloseHandle(handle)
    return False

def candidates(root,current,scope=None):
    rows=[]
    for item in registered():
        try:
            old=Path(item['InstallLocation']).resolve();v=item['DisplayVersion']
            if old==root or old.is_relative_to(root) or root.is_relative_to(old) or version(v)>=version(current):continue
            if scope and not old.is_relative_to(Path(scope).resolve()):continue
            if item['key']!='FilmInvertNative.Stable.'+v+'_is1' or not item['DisplayName'].startswith('FilmInvert Native '):continue
            uninstaller=old/'uninstall/unins000.exe'
            if item['UninstallString'].strip().strip('"').casefold()!=str(uninstaller).casefold():continue
            if uninstaller.is_symlink() or not uninstaller.is_file():continue
            # Validate product-owned metadata before allowing its uninstall entry.
            if verify(old)!=v:continue
            rows.append((v,old,uninstaller))
        except (OSError,ValueError,KeyError):continue
    return rows

def execute(root,remove=False,scope=None):
    root=Path(root).resolve();report={'verified':False,'removed':[],'retained':[]}
    try:
        current=verify(root);report['version']=current;report['verified']=True
        if remove:
            for v,old,uninstaller in candidates(root,current,scope):
                try:
                    if running(old):report['retained'].append({'version':v,'reason':'running'});continue
                    # Explicit executable + argument list, no shell and no recursive delete.
                    result=subprocess.run([str(uninstaller),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'],cwd=str(old),timeout=180)
                    if result.returncode==0 and not (old/'runtime/pythonw.exe').exists():report['removed'].append(v)
                    else:report['retained'].append({'version':v,'reason':'uninstall failed','code':result.returncode})
                except (OSError,subprocess.TimeoutExpired) as e:report['retained'].append({'version':v,'reason':str(e)})
    except Exception as e:report['error']=str(e)
    return report

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--remove',action='store_true');parser.add_argument('--scope-root');parser.add_argument('--result',required=True);args=parser.parse_args()
    report=execute(Path(__file__).resolve().parent.parent,args.remove,args.scope_root)
    Path(args.result).write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(report,ensure_ascii=False));return 0 if report['verified'] else 1
if __name__=='__main__':raise SystemExit(main())
