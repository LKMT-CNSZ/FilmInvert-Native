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
from PIL import Image
from PySide6.QtWidgets import QApplication
from native_app import app_icon

app=QApplication([])
assets=ROOT/'assets';assets.mkdir(exist_ok=True)
app_icon().pixmap(128,128).save(str(assets/'film-invert.png'))
with Image.open(assets/'film-invert.png') as image:
    image.save(assets/'film-invert.ico',sizes=[(16,16),(32,32),(48,48),(64,64),(128,128)])

from PyInstaller.__main__ import run
run([str(ROOT/'native_app.py'),'--name','FilmInvertNative','--windowed','--onedir',
     '--noconfirm','--clean','--noupx','--icon',str(assets/'film-invert.ico'),
     '--distpath',str(ROOT/'dist'/VERSION),'--workpath',str(ROOT/'build'/'pyinstaller'),
     '--specpath',str(ROOT/'build'),'--paths',str(ROOT/'vendor'),
     '--add-data',str(ROOT/'samples')+';samples',
     '--add-data',str(ROOT/'DESKTOP-README.md')+';.',
     '--exclude-module','PySide6.QtWebEngineCore','--exclude-module','PySide6.QtQml',
     '--exclude-module','tkinter','--exclude-module','matplotlib','--exclude-module','pytest'])
