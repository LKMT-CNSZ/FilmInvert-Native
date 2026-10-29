"""Scene-linear, luminance-preserving global tonal adjustments."""
import numpy as np

FIELDS=('tone_shadows','tone_highlights','tone_whites','tone_blacks','tone_rolloff')
LOCAL_FIELDS=('local_tone','local_detail')


def active(p):return any(getattr(p,key,0)!=0 for key in FIELDS+LOCAL_FIELDS)


def local_active(p):return p.local_tone!=0 or p.local_detail!=0


class LocalContext:
    """Small global, edge-aware log-luminance base with source-pixel coordinates."""
    def __init__(self,linear,p,shape,step):
        import core
        self.shape=shape;self.step=step
        log=np.log2(np.maximum(core.luma(np.maximum(linear,0),p.working_space),1e-5))
        self.reference=float(np.median(log))
        r=max(1,min(12,round(p.local_radius*max(log.shape))))
        padded=np.pad(log,r,mode='edge');total=np.zeros_like(log);weights=np.zeros_like(log)
        for dy in range(-r,r+1):
            for dx in range(-r,r+1):
                shifted=padded[r+dy:r+dy+log.shape[0],r+dx:r+dx+log.shape[1]]
                weight=np.exp(-.5*((shifted-log)/1.2)**2-(dx*dx+dy*dy)/max(1.,r*r*.5))
                total+=shifted*weight;weights+=weight
        self.base=total/np.maximum(weights,1e-8)

    def sample(self,box):
        x0,y0,x1,y1=box
        xx=np.minimum(np.arange(x0,x1,dtype=np.float32)/self.step,self.base.shape[1]-1)
        yy=np.minimum(np.arange(y0,y1,dtype=np.float32)/self.step,self.base.shape[0]-1)
        ix=xx.astype(np.int32);iy=yy.astype(np.int32);fx=xx-ix;fy=yy-iy
        jx=np.minimum(ix+1,self.base.shape[1]-1);jy=np.minimum(iy+1,self.base.shape[0]-1)
        top=self.base[iy[:,None],ix]*(1-fx)+self.base[iy[:,None],jx]*fx
        bottom=self.base[jy[:,None],ix]*(1-fx)+self.base[jy[:,None],jx]*fx
        return top*(1-fy[:,None])+bottom*fy[:,None]


def local_context(source,p,light=None,source_p=None,repair_context=None):
    """Build the same global base used by full and region processing, bounded RGB scratch."""
    import core
    h,w=source.shape[:2];step=max(1,int(np.ceil(max(h,w)/160)))
    if light is None:linear=source[::step,::step]
    else:
        sampled=np.concatenate([light(source,source_p or p,(0,y,w,y+1))[:,::step] for y in range(0,h,step)],axis=0)
        linear=core.scene_linear(sampled,p,repair_context)*core.wb_gain(p)
        if p.white-p.black>1e-5:linear=(linear-p.black)/(p.white-p.black)
        linear=linear*(2.**p.exposure)
    return LocalContext(linear,p,(h,w),step)


def apply(rgb,p,context=None,box=None):
    if not active(p):return rgb
    import core
    x=np.maximum(rgb,0)
    y=np.maximum(core.luma(x,p.working_space),0)
    z=y.copy();level=np.clip(y,0,1)
    if local_active(p):
        context=context or local_context(x,p)
        base=context.sample(box or (0,0,x.shape[1],x.shape[0]))
        detail=np.clip(np.log2(np.maximum(y,1e-5))-base,-2,2)
        adjustment=np.clip(p.local_tone*(context.reference-base)*.7+p.local_detail*detail*.25,-2,2)
        z*=np.exp2(adjustment)
    shadow=1/(1+(y/np.float32(.20))**2)
    highlight=(y/(y+np.float32(.35)))**2
    z*=np.exp2(np.float32(2*p.tone_shadows)*shadow+np.float32(2*p.tone_highlights)*highlight)
    z*=np.exp2(np.float32(p.tone_whites)*level**4)
    z=np.maximum(0,z+np.float32(.06*p.tone_blacks)*(1-level)**4)
    if p.tone_rolloff>0:
        threshold=np.float32(1-.45*p.tone_rolloff)
        d=np.maximum(z-threshold,0)
        z=np.where(z>threshold,threshold+d/(1+d/(1-threshold)),z)
    ratio=z/np.maximum(y,np.float32(1e-8))
    out=x*ratio[...,None]
    out=np.where((y<=1e-8)[...,None],z[...,None],out)
    return out.astype(np.float32)
