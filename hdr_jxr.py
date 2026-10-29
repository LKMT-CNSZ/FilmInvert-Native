"""Windows JPEG XR HDR: linear scRGB, 1.0 = 80 nits, native WIC codec.

Interfaces/identifiers follow Microsoft's wincodec.h and oaidl.h.
No display-specific shoulder is baked into the exported image.
"""
import ctypes as C
import os
import numpy as np
from hdr_display import GUID,guid,call,check,release,P,U,I
from file_io import io_path,temporary_path

RGB_FLOAT='6fddc324-4e03-4bfe-b185-3d77768dc91b'

class Property(C.Structure):
    _fields_=[('type',U),('vt',C.c_ushort),('cf',C.c_ushort),('hint',U),('name',C.c_wchar_p),('clsid',GUID)]
class Value(C.Union):
    _fields_=[('boolean',C.c_short),('number',C.c_float),('storage',C.c_uint64*2)]
class Variant(C.Structure):
    _fields_=[('vt',C.c_ushort),('reserved',C.c_ushort*3),('value',Value)]

class WIC:
    def __init__(self):
        if os.name!='nt':raise RuntimeError('JPEG XR HDR export requires Windows')
        self.objects=[];self.factory=P();self.initialized=False
        self.ole=C.WinDLL('ole32')
        self.ole.CoInitializeEx.argtypes=[P,U];self.ole.CoInitializeEx.restype=I
        self.ole.CoCreateInstance.argtypes=[C.POINTER(GUID),P,U,C.POINTER(GUID),C.POINTER(P)];self.ole.CoCreateInstance.restype=I
        self.ole.CoUninitialize.restype=None
        hr=self.ole.CoInitializeEx(None,0)
        if hr in (0,1):self.initialized=True
        elif hr!=-2147417850:check(hr,'CoInitializeEx') # Already initialized as STA is also valid.
        try:
            clsid=guid('cacaf262-9370-4615-a13b-9f5539da4c0a');iid=guid('ec5ec8a9-c395-4314-9c77-54d7a935ff70')
            check(self.ole.CoCreateInstance(C.byref(clsid),None,1,C.byref(iid),C.byref(self.factory)),'Create WIC factory')
            self.objects.append(self.factory)
        except Exception:self.close();raise
    def pointer(self):
        p=P();self.objects.append(p);return p
    def close(self):
        for p in reversed(self.objects):release(p)
        self.objects=[]
        if self.initialized:self.ole.CoUninitialize();self.initialized=False
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

def canceled(cancel):
    if cancel is not None and cancel.is_set():raise InterruptedError('HDR export canceled')

def write(path,linear,space='srgb',cancel=None,reference_white=203.):
    """Encode nominal-white RGB as absolute scRGB without clipping values above 1."""
    from working_color import linear as convert
    a=np.asarray(linear)
    if a.ndim!=3 or a.shape[2]!=3 or min(a.shape[:2])<1:raise ValueError('Expected an RGB image')
    if not np.isfinite(a).all() or not np.isfinite(reference_white) or not 0<reference_white<=10000:raise ValueError('Invalid HDR pixels or reference white')
    if space not in ('srgb','prophoto'):raise ValueError('Unsupported HDR working space')
    h,w=a.shape[:2]
    if w*h*16>=2**32:raise ValueError('JPEG XR exceeds the 4 GB pixel budget')
    canceled(cancel);dest=io_path(path)
    if dest.exists():raise FileExistsError('Output already exists: '+str(path))
    dest.parent.mkdir(parents=True,exist_ok=True);temp=temporary_path(dest)
    try:
        with WIC() as api:
            stream=api.pointer();encoder=api.pointer();frame=api.pointer();bag=api.pointer()
            check(call(api.factory,14,I,[C.POINTER(P)],C.byref(stream)),'Create WIC stream')
            check(call(stream,15,I,[C.c_wchar_p,U],str(temp),0x40000000),'Open output')
            container=guid('57a37caa-367a-4540-916b-f183c5093a4b')
            check(call(api.factory,8,I,[C.POINTER(GUID),P,C.POINTER(P)],C.byref(container),None,C.byref(encoder)),'Create JPEG XR encoder')
            check(call(encoder,3,I,[P,U],stream,2),'Initialize JPEG XR')
            check(call(encoder,10,I,[C.POINTER(P),C.POINTER(P)],C.byref(frame),C.byref(bag)),'Create JPEG XR frame')
            prop=Property();prop.name='Lossless';prop.vt=11
            val=Variant();val.vt=11;val.value.boolean=-1
            check(call(bag,4,I,[U,C.POINTER(Property),C.POINTER(Variant)],1,C.byref(prop),C.byref(val)),'Set lossless mode')
            check(call(frame,3,I,[P],bag),'Initialize frame')
            check(call(frame,4,I,[U,U],w,h),'Set image dimensions')
            fmt=guid(RGB_FLOAT)
            check(call(frame,6,I,[C.POINTER(GUID)],C.byref(fmt)),'Set float scRGB format')
            if bytes(fmt)!=bytes(guid(RGB_FLOAT)):raise RuntimeError('JPEG XR encoder rejected floating-point HDR')
            for y in range(0,h,128):
                canceled(cancel)
                rgb=convert(a[y:y+128],space,'srgb')*np.float32(reference_white/80.)
                if not np.isfinite(rgb).all():raise ValueError('HDR conversion overflow')
                block=np.zeros((*rgb.shape[:2],4),np.float32);block[...,:3]=rgb
                check(call(frame,10,I,[U,U,U,P],len(block),w*16,block.nbytes,P(block.ctypes.data)),'Write HDR pixels')
            check(call(frame,12,I,[]),'Commit HDR frame');check(call(encoder,11,I,[]),'Commit JPEG XR')
        canceled(cancel);os.rename(temp,dest) # Windows atomically refuses an existing target.
    finally:temp.unlink(missing_ok=True)
    return str(path)

def read_scrgb(path):
    """Decode native pixels for format/unit validation; no ICC or tone transform."""
    with WIC() as api:
        decoder=api.pointer();frame=api.pointer()
        check(call(api.factory,3,I,[C.c_wchar_p,P,U,U,C.POINTER(P)],str(io_path(path)),None,0x80000000,0,C.byref(decoder)),'Decode JPEG XR')
        check(call(decoder,13,I,[U,C.POINTER(P)],0,C.byref(frame)),'Decode first frame')
        w=U();h=U();fmt=GUID()
        check(call(frame,3,I,[C.POINTER(U),C.POINTER(U)],C.byref(w),C.byref(h)),'Get dimensions')
        check(call(frame,4,I,[C.POINTER(GUID)],C.byref(fmt)),'Get decoded pixel format')
        if bytes(fmt)!=bytes(guid(RGB_FLOAT)):raise ValueError('Expected native 128bpp RGB float JPEG XR')
        if not w.value or not h.value or w.value*h.value*16>1024**3:raise ValueError('Decoded HDR image exceeds 1 GB validation budget')
        pixels=np.empty((h.value,w.value,4),np.float32)
        check(call(frame,7,I,[P,U,U,P],None,w.value*16,pixels.nbytes,P(pixels.ctypes.data)),'Read float pixels')
        return pixels[...,:3].copy()
