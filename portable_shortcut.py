"""Create a normal Windows shortcut for this machine after a portable first launch."""
from pathlib import Path
import ctypes
from ctypes import wintypes
import os
import uuid

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
        check(invoke(shell,17,[wintypes.LPCWSTR,ctypes.c_int],str(bundle/'app'/'assets'/'film-invert.ico'),0))
        check(invoke(shell,0,[ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)],ctypes.byref(persist_iid),ctypes.byref(persist)))
        target=bundle/'胶片工作室.lnk'
        check(invoke(persist,6,[wintypes.LPCWSTR,wintypes.BOOL],str(target),True))
        return target
    finally:
        if persist.value:invoke(persist,2,[])
        if shell.value:invoke(shell,2,[])
        if initialized>=0:ole.CoUninitialize()
