"""Data paths shared by installed, portable, and diagnostic entry points."""
from pathlib import Path
import os
import tempfile

def user_root():
    return Path(os.environ.get('LOCALAPPDATA') or (Path.home()/'AppData'/'Local'))/'FilmInvertNative'

def writable(folder):
    folder=Path(folder)
    folder.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryFile(dir=folder):pass
    return folder

def select_profile(bundle,explicit=None):
    if explicit is not None:return writable(Path(explicit))
    bundle=Path(bundle)
    if not (bundle/'installed-layout.json').is_file():
        try:return writable(bundle/'data')
        except OSError:pass
    return writable(user_root()/'data')

def default_output(bundle,profile):
    bundle=Path(bundle)
    if (bundle/'installed-layout.json').is_file() or Path(profile).resolve()==(user_root()/'data').resolve():
        return user_root()/'exports'
    return bundle/'out'
