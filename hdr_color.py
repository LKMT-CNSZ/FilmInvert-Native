"""Extended-range RGB transfer functions; 1.0 is the reference white, not a ceiling."""
import numpy as np

def encode(value,space='srgb'):
    x=np.maximum(np.asarray(value,np.float32),0)
    if space=='srgb':return np.where(x<=.0031308,x*12.92,1.055*x**np.float32(1/2.4)-.055).astype(np.float32)
    if space=='prophoto':return np.where(x<1/512,x*16,x**np.float32(1/1.8)).astype(np.float32)
    raise ValueError('Unsupported HDR working space')

def decode(value,space='srgb'):
    x=np.maximum(np.asarray(value,np.float32),0)
    if space=='srgb':return np.where(x<=.04045,x/12.92,((x+.055)/1.055)**np.float32(2.4)).astype(np.float32)
    if space=='prophoto':return np.where(x<.03125,x/16,x**np.float32(1.8)).astype(np.float32)
    raise ValueError('Unsupported HDR working space')

def render(source,p):
    """Full-resolution HDR master including grade, repairs and linear geometry.

    SDR highlight roll-off is bypassed explicitly. The user's other edits are kept;
    reference white stays at 1.0 and above-white samples remain floating point.
    """
    import core,calibration,restoration,local_edits,geometry
    p=p.normalized();p.tone_rolloff=0;p.scene_hdr=True
    linear=core.working_linear(source,p,unbounded=True)
    if p.calibration and p.calibration_strength>0 and p.mode!='bw_negative':
        if p.calibration.get('working_space','srgb')!=p.working_space:raise ValueError('HDR calibration working space mismatch')
        matrix=np.eye(3,dtype=np.float32)+np.float32(p.calibration_strength)*(np.asarray(p.calibration['matrix'],np.float32)-np.eye(3,dtype=np.float32))
        linear=linear@matrix.T
    out=encode(linear,p.working_space)
    if p.mode!='positive' and abs(p.saturation-1)>1e-4:
        y=core.luma(out,p.working_space)[...,None];out=np.maximum(0,y+(out-y)*p.saturation)
    curve=core.normalize_curve(p.curve)
    if not all(x==y for x,y in curve):
        mapped=core.apply_curve(out,curve)
        slope=(curve[-1][1]-curve[-2][1])/max(curve[-1][0]-curve[-2][0],1e-6)
        out=np.where(out>1,curve[-1][1]+(out-1)*slope,mapped)
    if p.mode!='bw_negative' and np.any(p.hsl):
        headroom=np.maximum(1,out.max(axis=2,keepdims=True))
        out=core.apply_hsl(out/headroom,p.hsl)*headroom
    out=restoration.remove_dust(out,p.dust_strength,p.dust_radius)
    out=local_edits.apply(out,p.local_edits,p.working_space,p.flip,hdr=True)
    linear=decode(out,p.working_space)
    peak=max(1.,float(np.max(linear)))
    if p.rotation or p.straighten or p.perspective is not None or p.crop!=geometry.FULL:
        linear=geometry.apply(linear/peak,p.__dict__)*np.float32(peak)
    return np.maximum(linear,0).astype(np.float32)
