"""Build a windowed Windows directory bundle with PyInstaller."""
from pathlib import Path
import os
import sys
ROOT=Path(__file__).resolve().parents[1]
VERSION=(ROOT/'VERSION.txt').read_text().strip()
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'vendor'))
os.environ['PYTHONIOENCODING']='utf-8'
os.environ['PYINSTALLER_CONFIG_DIR']=str(ROOT/'build'/'pyinstaller-cache')
# The developer shell adds Poppler's ICU to PATH. Qt uses the Windows ICU API;
# collecting Poppler's same-named DLL breaks a frozen application at startup.
os.environ['PATH']=os.pathsep.join([str(ROOT/'vendor'/'PySide6'),str(ROOT/'runtime'),
    str(Path(os.environ.get('WINDIR','C:/Windows'))/'System32'),os.environ.get('WINDIR','C:/Windows')])
assets=ROOT/'assets'
assert (assets/'filminvert-native-856209a72631.ico').is_file(),'The reviewed application icon is required'

from PyInstaller.__main__ import run
run([str(ROOT/'native_app.py'),'--name','FilmInvertNative','--windowed','--onedir',
     '--noconfirm','--clean','--noupx','--icon',str(assets/'filminvert-native-856209a72631.ico'),
     '--distpath',str(ROOT/'dist'/VERSION),'--workpath',str(ROOT/'build'/'pyinstaller'),
     '--specpath',str(ROOT/'build'),'--paths',str(ROOT/'vendor'),
     '--add-data',str(ROOT/'samples')+';samples',
     '--add-data',str(ROOT/'DESKTOP-README.md')+';.',
     '--exclude-module','PySide6.QtWebEngineCore','--exclude-module','PySide6.QtQml',
     '--exclude-module','tkinter','--exclude-module','matplotlib','--exclude-module','pytest'])
