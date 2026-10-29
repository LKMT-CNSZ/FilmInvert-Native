"""Compile a regular current-user installer from the reviewed bundle manifest."""
from pathlib import Path
import argparse,json,subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from build_compat import ROOT,BUNDLE,VERSION,RELEASE_DATE
parser=argparse.ArgumentParser();parser.add_argument('--iscc',type=Path,default=ROOT/'build/release-tools/InnoSetup/ISCC.exe');args=parser.parse_args()
assert VERSION in ('3.0.0','3.0.1','3.1.0-rc1','3.1.0-rc2','3.1.0-rc3','3.1.0-rc4','3.1.0-rc5','3.1.0-rc6','3.1.0-rc7','3.1.0-rc8','3.1.0-rc9','3.1.0-rc10','3.1.0-rc11','3.1.0-rc12','3.1.0-rc13','3.1.0-rc14','3.1.0')
manifest=json.loads((BUNDLE/'file-manifest.json').read_text('utf-8'))
names=sorted([*manifest['files'],'file-manifest.json'])
rows=[]
for name in names:
    relative=Path(name)
    assert not any(c in name for c in ('"',';','\n')) and '..' not in relative.parts
    destination='{app}'+('\\'+str(relative.parent) if relative.parent!=Path('.') else '')
    rows.append('Source: "{#BundleDir}\\'+str(relative)+'"; DestDir: "'+destination+'"; Flags: ignoreversion')
(ROOT/'tools/stable-files.iss').write_text('\n'.join(rows)+'\n',encoding='utf-8-sig')
subprocess.run([str(args.iscc),'/Qp','/DAppFileVersion='+VERSION.split('-')[0],'/DReleaseDate='+RELEASE_DATE,str(ROOT/'tools/stable-setup.iss')],cwd=ROOT,check=True)
