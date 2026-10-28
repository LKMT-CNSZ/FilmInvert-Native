"""Preserve distribution notices and fetch standard Qt open-source license texts."""
from pathlib import Path
import json
import shutil
import urllib.request
ROOT=Path(__file__).resolve().parents[1]
dest=ROOT/'THIRD_PARTY';dest.mkdir(exist_ok=True)
roots=[ROOT/'vendor',ROOT/'runtime'/'Lib'/'site-packages']
for root in roots:
    for directory in root.glob('*.dist-info'):
        if not any(directory.name.lower().startswith(n) for n in ('pyside6','shiboken6','numpy','pillow','rawpy')):
            continue
        target=dest/directory.name;target.mkdir(exist_ok=True)
        for file in directory.rglob('*'):
            if file.is_file() and ('license' in file.as_posix().lower() or file.name=='METADATA'):
                out=target/file.relative_to(directory);out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,out)
shutil.copy2(ROOT/'runtime'/'LICENSE.txt',dest/'Python-LICENSE.txt')
shutil.copy2(ROOT/'vendor'/'pyinstaller-6.22.3.dist-info'/'licenses'/'COPYING.txt',dest/'PyInstaller-COPYING.txt')
opener=urllib.request.build_opener(urllib.request.ProxyHandler({'http':'http://127.0.0.1:7897','https':'http://127.0.0.1:7897'}))
version=json.loads((ROOT/'build'/'qt-dependencies.json').read_text())[0]['version']
for name in ('LGPL-3.0-only.txt','GPL-2.0-only.txt','GPL-3.0-only.txt'):
    target=dest/name
    if not target.exists():
        url=f'https://raw.githubusercontent.com/qt/qtbase/v{version}/LICENSES/{name}'
        data=opener.open(url,timeout=40).read();assert len(data)>1000;target.write_bytes(data)
notice=f'''Third-party components

Qt / PySide6 / Shiboken {version}: unmodified dynamically linked libraries,
distributed under their open-source license alternatives (see metadata and
LGPL/GPL texts). Commercial-license text is retained from the original wheel;
this application does not claim or require a commercial Qt license.

Corresponding upstream sources and build information:
https://code.qt.io/cgit/qt/qtbase.git/tag/?h=v{version}
https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v{version}
https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-{version}-src/

Application source and reproducible build scripts accompany this preview.
Qt DLLs remain external in _internal and can be replaced with compatible builds.
Other components: Python, NumPy/OpenBLAS, Pillow/LittleCMS, rawpy/LibRaw and
PyInstaller runtime. Their original notices are retained in this directory.
'''
(dest/'NOTICE.txt').write_text(notice,encoding='utf-8')
print('Third-party notices prepared:',dest)
