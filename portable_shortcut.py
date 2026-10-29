"""Create a normal Windows shortcut for this machine after a portable first launch."""
from pathlib import Path
import ctypes
from ctypes import wintypes
import os
import uuid
import hashlib

def shortcut_icon(bundle):
    # A new path invalidates Explorer's cached icon when artwork changes.
    assets=Path(bundle)/'app'/'assets'
    source=assets/'film-invert.ico'
    digest=hashlib.sha256(source.read_bytes()).hexdigest()[:12]
    versioned=assets/f'filminvert-native-{digest}.ico'
    if not versioned.is_file():
        raise FileNotFoundError(versioned)
    return versioned

def create(bundle):
    if os.name!='nt':raise OSError('Windows shortcuts are only available on Windows')
    bundle=Path(bundle).resolve();exe=bundle/'runtime'/'pythonw.exe'
    if not exe.is_file():raise FileNotFoundError(exe)
    ole=ctypes.OleDLL('ole32');GUID=ctypes.c_ubyte*16
    guid=lambda value:GUID.from_buffer_copy(uuid.UUID(value).bytes_le)
    clsid=guid('00021401-0000-0000-c000-000000000046')
    shell_iid=guid('000214f9-0000-0000-c000-000000000046')
    persist_iid=guid('0000010b-0000-0000-c000-000000000046')
    ole.CoInitializeEx.argtypes=[ctypes.c_void_p,wintypes.DWORD];ole.CoInitializeEx.restype=ctypes.c_long
    initialized=ole.CoInitializeEx(None,2)
    if initialized<0 and initialized!=-2147417850:raise OSError(f'COM initialization failed: {initialized}')
    shell=ctypes.c_void_p();persist=ctypes.c_void_p()
    def check(result):
        if result<0:raise OSError(f'Windows shortcut operation failed: 0x{result&0xffffffff:08x}')
    def invoke(pointer,index,types,*args):
        vtable=ctypes.cast(pointer,ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.c_long,ctypes.c_void_p,*types)(vtable[index])(pointer,*args)
    try:
        ole.CoCreateInstance.argtypes=[ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)]
        ole.CoCreateInstance.restype=ctypes.c_long
        check(ole.CoCreateInstance(ctypes.byref(clsid),None,1,ctypes.byref(shell_iid),ctypes.byref(shell)))
        check(invoke(shell,20,[wintypes.LPCWSTR],str(exe)))
        check(invoke(shell,11,[wintypes.LPCWSTR],'-I -m film_invert_launcher'))
        check(invoke(shell,9,[wintypes.LPCWSTR],str(bundle)))
        check(invoke(shell,7,[wintypes.LPCWSTR],'FilmInvert Native - local portable runtime'))
        check(invoke(shell,17,[wintypes.LPCWSTR,ctypes.c_int],str(shortcut_icon(bundle)),0))
        check(invoke(shell,0,[ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)],ctypes.byref(persist_iid),ctypes.byref(persist)))
        target=bundle/'胶片工作室.lnk'
        check(invoke(persist,6,[wintypes.LPCWSTR,wintypes.BOOL],str(target),True))
        # Notify the actual changed item instead of flushing every application's cache.
        notify=ctypes.windll.shell32.SHChangeNotify
        notify.argtypes=[wintypes.LONG,wintypes.UINT,wintypes.LPCWSTR,ctypes.c_void_p]
        notify.restype=None
        notify(0x2000,0x1005,str(target),None)
        return target
    finally:
        if persist.value:invoke(persist,2,[])
        if shell.value:invoke(shell,2,[])
        if initialized>=0:ole.CoUninitialize()
