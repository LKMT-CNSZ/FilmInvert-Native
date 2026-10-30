"""Extended-range RGB transfer functions; 1.0 is the reference white, not a ceiling."""
import numpy as np

# Apply only HDR/SDR rendition controls; exposure, film profile, WB, curves and
# local edits remain the user's. These ordinary Params fields persist in v9/v7.
PRESETS={
    'natural':dict(hdr_enabled=True,hdr_strength=.8,hdr_start=.55,hdr_softness=.7,hdr_limit=2.,sdr_hdr_rolloff=1.,sdr_hdr_exposure=0.),
    'clear':dict(hdr_enabled=True,hdr_strength=1.3,hdr_start=.45,hdr_softness=.75,hdr_limit=3.,sdr_hdr_rolloff=1.,sdr_hdr_exposure=-.1),
    'soft':dict(hdr_enabled=True,hdr_strength=.5,hdr_start=.65,hdr_softness=1.,hdr_limit=1.5,sdr_hdr_rolloff=1.,sdr_hdr_exposure=.1),
}

def preset(value,name):
    import copy
    if name not in PRESETS:raise ValueError('Unknown HDR preset')
    out=copy.deepcopy(value);out.update(PRESETS[name]);return out

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

def grade(linear,p):
    if not p.hdr_enabled:return linear
    import core
    y=np.maximum(core.luma(linear,p.working_space),0)
    weight=np.clip((y-p.hdr_start)/p.hdr_softness,0,1);weight=weight*weight*(3-2*weight)
    out=linear*np.exp2(weight*p.hdr_strength)[...,None]
    maximum=np.maximum(out.max(axis=-1),0);limit=2.**p.hdr_limit;knee=limit*.75
    delta=np.maximum(maximum-knee,0);mapped=knee+delta/(1+delta/(limit-knee))
    return (out*np.where(maximum>knee,mapped/np.maximum(maximum,1e-8),1)[...,None]).astype(np.float32)


def sdr_rendition(linear,p):
    import core
    out=np.maximum(linear,0)*np.float32(2.**p.sdr_hdr_exposure)
    y=core.luma(out,p.working_space);delta=np.maximum(y-.5,0)
    mapped=np.where(y>.5,.5+delta/(1+delta/.5),y)
    target=y+(mapped-y)*p.sdr_hdr_rolloff
    return np.clip(out*(target/np.maximum(y,1e-8))[...,None],0,1).astype(np.float32)


def render(source,p,repair_context=None,tone_context=None,tone_box=None):
    """Full-resolution HDR master including grade, repairs and linear geometry.

    SDR highlight roll-off is bypassed explicitly. The user's other edits are kept;
    reference white stays at 1.0 and above-white samples remain floating point.
    """
    import core,calibration,restoration,local_edits,geometry
    p=p.normalized();p.tone_rolloff=0;p.scene_hdr=True
    linear=core.working_linear(source,p,repair_context,tone_context,tone_box,unbounded=True)
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
    linear=grade(linear,p)
    peak=max(1.,float(np.max(linear)))
    if p.rotation or p.straighten or p.perspective is not None or p.crop!=geometry.FULL:
        linear=geometry.apply(linear/peak,p.__dict__)*np.float32(peak)
    return np.maximum(linear,0).astype(np.float32)
