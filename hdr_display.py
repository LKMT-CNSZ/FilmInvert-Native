"""Windows DXGI FP16/scRGB presentation, without changing OS display settings.

COM slots and layouts follow Microsoft's Windows SDK headers (d3d11.h,
dxgi1_2.h, dxgi1_4.h, dxgi1_6.h). RGB 1.0 in scRGB represents 80 cd/m².
No software-rendered SDR substitute is presented as an HDR display.
"""
import ctypes as C
import os,uuid,time
import numpy as np

U=C.c_uint32;I=C.c_int32;F=C.c_float;P=C.c_void_p

class GUID(C.Structure):_fields_=[('bytes',C.c_ubyte*16)]
def guid(value):return GUID((C.c_ubyte*16).from_buffer_copy(uuid.UUID(value).bytes_le))
class Sample(C.Structure):_fields_=[('Count',U),('Quality',U)]
class SwapDesc(C.Structure):
    _fields_=[('Width',U),('Height',U),('Format',U),('Stereo',I),('SampleDesc',Sample),
        ('BufferUsage',U),('BufferCount',U),('Scaling',U),('SwapEffect',U),('AlphaMode',U),('Flags',U)]
class TextureDesc(C.Structure):
    _fields_=[('Width',U),('Height',U),('MipLevels',U),('ArraySize',U),('Format',U),
        ('SampleDesc',Sample),('Usage',U),('BindFlags',U),('CPUAccessFlags',U),('MiscFlags',U)]
class Subresource(C.Structure):_fields_=[('pSysMem',P),('SysMemPitch',U),('SysMemSlicePitch',U)]
class Rect(C.Structure):_fields_=[('left',I),('top',I),('right',I),('bottom',I)]
class OutputDesc(C.Structure):
    _fields_=[('DeviceName',C.c_wchar*32),('DesktopCoordinates',Rect),('AttachedToDesktop',I),
        ('Rotation',U),('Monitor',P),('BitsPerColor',U),('ColorSpace',U),('RedPrimary',F*2),
        ('GreenPrimary',F*2),('BluePrimary',F*2),('WhitePoint',F*2),
        ('MinLuminance',F),('MaxLuminance',F),('MaxFullFrameLuminance',F)]

class OutputBasic(C.Structure):
    _fields_=[('DeviceName',C.c_wchar*32),('DesktopCoordinates',Rect),('AttachedToDesktop',I),('Rotation',U),('Monitor',P)]
class Luid(C.Structure):_fields_=[('low',U),('high',I)]
class ConfigHeader(C.Structure):_fields_=[('type',U),('size',U),('adapter',Luid),('id',U)]
class SourceName(C.Structure):_fields_=[('header',ConfigHeader),('name',C.c_wchar*32)]
class SDRWhite(C.Structure):_fields_=[('header',ConfigHeader),('level',U)]
class PathSource(C.Structure):_fields_=[('adapter',Luid),('id',U),('mode',U),('flags',U)]
class PathTarget(C.Structure):
    _fields_=[('adapter',Luid),('id',U),('mode',U),('technology',U),('rotation',U),('scaling',U),('rate_n',U),('rate_d',U),('scan',U),('available',I),('flags',U)]
class ConfigPath(C.Structure):_fields_=[('source',PathSource),('target',PathTarget),('flags',U)]


def window_monitor(hwnd):
    user=C.WinDLL('user32');user.MonitorFromWindow.argtypes=[P,U];user.MonitorFromWindow.restype=P
    monitor=user.MonitorFromWindow(P(hwnd),2)
    if not monitor:raise RuntimeError('Cannot determine the display containing the HDR window')
    return monitor


def sdr_white_level(device_name):
    """Read Windows' current per-target SDR white, never change OS settings."""
    try:
        user=C.WinDLL('user32');count=U();modes=U()
        user.GetDisplayConfigBufferSizes.argtypes=[U,C.POINTER(U),C.POINTER(U)]
        user.QueryDisplayConfig.argtypes=[U,C.POINTER(U),P,C.POINTER(U),P,P]
        user.DisplayConfigGetDeviceInfo.argtypes=[P]
        for _ in range(3):
            if user.GetDisplayConfigBufferSizes(2,C.byref(count),C.byref(modes)):return None
            if count.value>128 or modes.value>1024:return None
            paths=(ConfigPath*count.value)();mode_buffer=C.create_string_buffer(max(1,modes.value)*64)
            result=user.QueryDisplayConfig(2,C.byref(count),paths,C.byref(modes),mode_buffer,None)
            if result==122:continue
            if result:return None
            for path in paths[:count.value]:
                name=SourceName();name.header=ConfigHeader(1,C.sizeof(SourceName),path.source.adapter,path.source.id)
                if user.DisplayConfigGetDeviceInfo(C.byref(name)) or name.name!=device_name:continue
                white=SDRWhite();white.header=ConfigHeader(11,C.sizeof(SDRWhite),path.target.adapter,path.target.id)
                if not user.DisplayConfigGetDeviceInfo(C.byref(white)):
                    nits=white.level*80./1000
                    if 80<=nits<=1000:return nits
            return None
    except (OSError,AttributeError):return None


def create_factory():
    dxgi=C.WinDLL('dxgi.dll');fn=dxgi.CreateDXGIFactory1
    fn.argtypes=[C.POINTER(GUID),C.POINTER(P)];fn.restype=I
    factory=P();iid=guid('50c83a1c-e072-4c48-87b0-3630fa36a6d0');check(fn(C.byref(iid),C.byref(factory)),'CreateDXGIFactory1')
    return factory


def select_output(factory,hwnd):
    """Return the owned adapter/output for the window's physical monitor."""
    monitor=window_monitor(hwnd)
    for ai in range(64):
        adapter=P();hr=call(factory,12,I,[U,C.POINTER(P)],ai,C.byref(adapter))
        if hr==-2005270526:break # DXGI_ERROR_NOT_FOUND
        check(hr,'EnumAdapters1')
        try:
            for oi in range(64):
                output=P();hr=call(adapter,7,I,[U,C.POINTER(P)],oi,C.byref(output))
                if hr==-2005270526:break
                check(hr,'EnumOutputs')
                try:
                    desc=OutputBasic();check(call(output,7,I,[C.POINTER(OutputBasic)],C.byref(desc)),'Get output description')
                    if desc.Monitor==monitor:
                        selected=adapter;adapter=P();found=output;output=P();return selected,found,monitor
                finally:release(output)
        finally:release(adapter)
    raise RuntimeError('No DXGI adapter/output matches the HDR window display')


_WHITE_CACHE={}

def output_info(output,monitor):
    advanced=query(output,'068346e8-aaec-4b84-add7-137f513f77a1')
    try:
        desc=OutputDesc();check(call(advanced,27,I,[C.POINTER(OutputDesc)],C.byref(desc)),'IDXGIOutput6.GetDesc1')
        peak=float(desc.MaxLuminance);cached=_WHITE_CACHE.get(desc.DeviceName)
        if cached is None or time.monotonic()-cached[0]>1.:
            cached=(time.monotonic(),sdr_white_level(desc.DeviceName));_WHITE_CACHE[desc.DeviceName]=cached
        white=cached[1]
        return dict(name=desc.DeviceName,monitor=int(monitor),hdr_active=desc.ColorSpace==12,bits=int(desc.BitsPerColor),
            max_nits=peak,full_frame_nits=float(desc.MaxFullFrameLuminance),color_space=int(desc.ColorSpace),
            effective_peak_nits=display_peak(peak),peak_estimated=display_peak(peak)!=peak,
            sdr_white_nits=white or 203.,white_estimated=white is None)
    finally:release(advanced)


def inspect_display(hwnd,factory=None):
    own=factory is None;factory=create_factory() if own else factory
    adapter=P();output=P()
    try:
        adapter,output,monitor=select_output(factory,hwnd);return output_info(output,monitor)
    finally:
        release(output);release(adapter)
        if own:release(factory)


def display_mapping(info,white=None,peak=None):
    peak=display_peak(info.get('max_nits',0)) if peak is None else float(peak)
    white=float(info.get('sdr_white_nits',203)) if white is None else float(white)
    if not np.isfinite(peak) or not 80<=peak<=10000 or not np.isfinite(white) or not 80<=white<=1000:raise ValueError('Invalid display luminance settings')
    white=min(white,peak)
    return white,peak,max(0.,float(np.log2(peak/white)))


def call(obj,index,result,args,*values):
    if not obj:raise RuntimeError('Direct3D object is closed')
    table=C.cast(obj,C.POINTER(C.POINTER(P))).contents
    return C.WINFUNCTYPE(result,P,*args)(table[index])(obj,*values)

def check(value,operation):
    if value<0:raise RuntimeError(f'{operation}: HRESULT 0x{value&0xffffffff:08X}')

def query(obj,iid):
    out=P();value=guid(iid)
    check(call(obj,0,I,[C.POINTER(GUID),C.POINTER(P)],C.byref(value),C.byref(out)),'QueryInterface')
    return out

def release(obj):
    if obj:call(obj,2,U,[])


def display_peak(peak_nits):
    """Reject missing/implausible HDR metadata; 1000 nits is an explicit estimate."""
    peak=float(peak_nits)
    return peak if np.isfinite(peak) and 80<=peak<=10000 else 1000.


def enhance_highlights(linear,stops=0.):
    """Display-only lift above linear 0.5; preserve shadows and RGB ratios."""
    value=float(stops)
    if not np.isfinite(value) or not 0<=value<=2:raise ValueError('HDR preview boost must be between 0 and 2 EV')
    source=np.asarray(linear,np.float32)
    if value==0:return source
    y=source[...,0]*.2126+source[...,1]*.7152+source[...,2]*.0722
    weight=np.clip((y-.5)/.5,0,1);weight=weight*weight*(3-2*weight)
    return source*np.exp2(weight*value)[...,None]


def scrgb_frame(linear,width,height,reference_white=203.,peak_nits=1000.):
    """Letterbox linear-sRGB pixels and map their shoulder to the actual display peak."""
    from PIL import Image
    if not 0<width<=8192 or not 0<height<=8192 or width*height>16_777_216:raise ValueError('HDR viewport exceeds the 16 MP surface budget')
    source=np.asarray(linear,np.float32)
    if source.ndim!=3 or source.shape[2]!=3 or not np.isfinite(source).all():raise ValueError('Invalid HDR preview pixels')
    h,w=source.shape[:2];scale=min(width/w,height/h);ow=max(1,round(w*scale));oh=max(1,round(h*scale))
    rgb=np.stack([np.asarray(Image.fromarray(source[...,c]).resize((ow,oh),Image.Resampling.BILINEAR)) for c in range(3)],axis=-1)
    rgb*=np.float32(reference_white/80.)
    y=rgb[...,0]*.2126+rgb[...,1]*.7152+rgb[...,2]*.0722
    peak=display_peak(peak_nits)/80.;knee=peak*.75;delta=np.maximum(y-knee,0)
    mapped=np.where(y>knee,knee+delta/(1+delta/(peak-knee)),y)
    rgb*=np.where(y>1e-8,mapped/np.maximum(y,1e-8),1)[...,None]
    out=np.zeros((height,width,4),np.float16);out[...,3]=1
    top=(height-oh)//2;left=(width-ow)//2;out[top:top+oh,left:left+ow,:3]=np.clip(rgb,-65504,65504).astype(np.float16)
    return out


class Surface:
    def __init__(self,hwnd,width,height):
        if os.name!='nt':raise RuntimeError('Native HDR preview requires Windows')
        self.hwnd=hwnd;self.adapter=P();self.output=P()
        self.device=P();self.context=P();self.factory=P();self.swap=P();self.texture=P();self.size=(width,height);self.texture_size=None
        try:
            self.factory=create_factory();self.adapter,self.output,self.monitor=select_output(self.factory,hwnd)
            d3d=C.WinDLL('d3d11.dll')
            create=d3d.D3D11CreateDevice
            create.argtypes=[P,U,P,U,P,U,U,C.POINTER(P),C.POINTER(U),C.POINTER(P)];create.restype=I
            feature=U()
            check(create(self.adapter,0,None,0x20,None,0,7,C.byref(self.device),C.byref(feature),C.byref(self.context)),'D3D11CreateDevice')
            desc=SwapDesc(width,height,10,0,Sample(1,0),0x20,2,0,4,3,0)
            swap=P()
            check(call(self.factory,15,I,[P,P,C.POINTER(SwapDesc),P,P,C.POINTER(P)],self.device,P(hwnd),C.byref(desc),None,None,C.byref(swap)),'CreateSwapChainForHwnd')
            try:self.swap=query(swap,'94d99bdb-f1f8-4ab0-b236-7da0170edab1')
            finally:release(swap)
            call(self.factory,8,I,[P,U],P(hwnd),2)
            support=U();check(call(self.swap,37,I,[U,C.POINTER(U)],1,C.byref(support)),'CheckColorSpaceSupport')
            if not support.value&1:raise RuntimeError('FP16 scRGB presentation is unavailable on this device')
            check(call(self.swap,38,I,[U],1),'SetColorSpace1(scRGB)')
            self.info=self.capabilities()
        except Exception:self.close();raise

    def capabilities(self):
        if window_monitor(self.hwnd)!=self.monitor:return inspect_display(self.hwnd,self.factory)
        return output_info(self.output,self.monitor)

    def present(self,linear,width,height,reference_white=203.,peak_override=None):
        info=self.capabilities()
        if not info['hdr_active']:raise RuntimeError('This display does not have Windows HDR enabled. HDR preview has stopped; SDR editing and float TIFF export remain available.')
        pixels=scrgb_frame(linear,width,height,reference_white,peak_override if peak_override is not None else info['effective_peak_nits'])
        return self._present_pixels(pixels,width,height,info)

    def present_prepared(self,pixels,width,height):
        """Present owned, already mapped FP16 animation pixels without remapping."""
        if pixels.dtype!=np.float16 or pixels.shape!=(height,width,4) or not pixels.flags.c_contiguous:raise ValueError('Invalid prepared HDR surface')
        if not 0<width<=8192 or not 0<height<=8192 or width*height>16_777_216:raise ValueError('HDR viewport exceeds surface budget')
        info=self.capabilities()
        if not info['hdr_active']:raise RuntimeError('Windows HDR is no longer enabled on this display')
        return self._present_pixels(pixels,width,height,info)

    def _present_pixels(self,pixels,width,height,info):
        if (width,height)!=self.size:
            check(call(self.swap,13,I,[U,U,U,U,U],2,width,height,10,0),'ResizeBuffers');self.size=(width,height)
        if self.texture_size!=(width,height):
            release(self.texture);self.texture=P();self.texture_size=None
            desc=TextureDesc(width,height,1,1,10,Sample(1,0),0,0,0,0)
            data=Subresource(P(pixels.ctypes.data),width*8,width*height*8)
            check(call(self.device,5,I,[C.POINTER(TextureDesc),C.POINTER(Subresource),C.POINTER(P)],C.byref(desc),C.byref(data),C.byref(self.texture)),'CreateTexture2D')
            self.texture_size=(width,height)
        else:
            call(self.context,48,None,[P,U,P,P,U,U],self.texture,0,None,P(pixels.ctypes.data),width*8,0)
        back=P()
        try:
            iid=guid('6f15aaf2-d208-4e89-9ab4-489535d34f9c')
            check(call(self.swap,9,I,[U,C.POINTER(GUID),C.POINTER(P)],0,C.byref(iid),C.byref(back)),'GetBuffer')
            call(self.context,47,None,[P,P],back,self.texture)
            check(call(self.swap,8,I,[U,U],1,0),'Present')
        finally:release(back)
        self.info=info
        return info

    def close(self):
        if getattr(self,'context',None):
            call(self.context,110,None,[]);call(self.context,111,None,[])
        for name in ('texture','swap','context','device','output','adapter','factory'):
            obj=getattr(self,name,None)
            release(obj);setattr(self,name,P())
