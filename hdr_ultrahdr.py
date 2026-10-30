"""Ultra HDR JPEG: authored SDR base plus ISO/XMP gain map via libultrahdr.

The official imagecodecs 2026.10.10 Windows wheel embeds libultrahdr 2.0.2.
Linear HDR uses nominal 1.0 = 203 nits, independent of this monitor's mapping.
SDR base is sRGB, including the saved SDR rendition controls. JPEG is lossy.
"""
import os
import numpy as np
from file_io import io_path,temporary_path

def codec():
    try:
        from imagecodecs import _ultrahdr as api
        if api.ultrahdr_version()!='libultrahdr 2.0.2':
            raise RuntimeError('Expected libultrahdr 2.0.2')
        return api
    except ImportError as error:
        raise RuntimeError('Ultra HDR JPEG codec missing; use the complete FilmInvert distribution') from error

def canceled(cancel):
    if cancel is not None and cancel.is_set():raise InterruptedError('Ultra HDR JPEG export canceled')

def inputs(linear,p,sdr=None):
    import hdr_color,working_color,colorio
    a=np.asarray(linear,np.float32)
    if a.ndim!=3 or a.shape[2]!=3 or min(a.shape[:2])<2:raise ValueError('Ultra HDR JPEG requires an RGB image at least 2 x 2')
    if not np.isfinite(a).all():raise ValueError('Invalid HDR pixels')
    if max(a.shape[:2])>8192 or a.shape[0]*a.shape[1]*32>2**31:
        raise ValueError('Ultra HDR JPEG supports up to 8192 x 8192; use HDR TIFF for larger images')
    rgb=np.maximum(working_color.linear(a,p.working_space,'srgb'),0)
    if rgb.max()>10000/203:
        raise ValueError('Ultra HDR JPEG exceeds 10000 nits; reduce HDR range/exposure or export HDR TIFF')
    # Match the working-space SDR rendition used by ordinary preview/export,
    # followed by the same sRGB output conversion. No screen profile is baked in.
    if sdr is None:
        sdr=hdr_color.sdr_rendition(a,p)
        sdr=colorio.convert(hdr_color.encode(sdr,p.working_space),'srgb',p.working_space)
    else:
        sdr=np.asarray(sdr,np.float32)
        if sdr.shape!=a.shape or not np.isfinite(sdr).all():raise ValueError('Invalid SDR companion pixels')
    h,w=a.shape[:2];hdr=np.ones((h,w,4),np.float16);hdr[...,:3]=rgb
    base=np.full((h,w,4),255,np.uint8);base[...,:3]=np.rint(np.clip(sdr,0,1)*255).astype(np.uint8)
    return hdr,base

def write(path,linear,p,cancel=None,sdr=None):
    canceled(cancel);dest=io_path(path)
    if dest.exists():raise FileExistsError('Output already exists: '+str(path))
    hdr,base=inputs(linear,p,sdr);api=codec();canceled(cancel)
    # ISO 21496-1 multichannel gain map preserves colored highlight ratios.
    # Derive capacity from CONTENT, not the current display. libultrahdr's
    # linear default is 10000 nits, which unnecessarily attenuates typical edits.
    peak=min(10000.,max(203.203,float(hdr[...,:3].max())*203.))
    data=api.ultrahdr_encode(hdr,95,sdr=base,gamut=api.ULTRAHDR.CG.BT_709,
        transfer=api.ULTRAHDR.CT.LINEAR,codec=api.ULTRAHDR.CODEC.JPEG,
        nits=peak,scale=1,gainmap_level=95,multichannel=True)
    canceled(cancel)
    if not api.ultrahdr_check(data):raise RuntimeError('Encoder did not produce an Ultra HDR gain-map JPEG')
    dest.parent.mkdir(parents=True,exist_ok=True);temp=temporary_path(dest)
    try:
        temp.write_bytes(data);canceled(cancel)
        if os.name=='nt':os.rename(temp,dest)
        else:os.link(temp,dest);temp.unlink()
    finally:temp.unlink(missing_ok=True)
    return str(path)
