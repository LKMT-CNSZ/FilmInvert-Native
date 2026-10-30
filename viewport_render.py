"""Bounded, source-resolution viewport rendering. Export processing is unchanged."""
import copy
import math
import numpy as np
from PIL import Image
import core
import geometry
import perspective

MAX_PIXELS=10_000_000
_CONTEXT=None
_CONTEXT_KEY=None


class ViewportUnavailable(ValueError):pass


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():raise InterruptedError('区域预览已取消')


def box_for(shape,value,box):
    h,w=geometry.output_shape(shape,value)
    if len(box)!=4 or not np.isfinite(box).all():raise ValueError('预览区域无效')
    x0,y0,x1,y1=[int(v) for v in box]
    x0,y0=max(0,min(w-1,x0)),max(0,min(h-1,y0))
    x1,y1=max(x0+1,min(w,x1)),max(y0+1,min(h,y1))
    if (x1-x0)*(y1-y0)>MAX_PIXELS:raise ViewportUnavailable('可见区域超过预览预算，请缩小应用窗口')
    return (x0,y0,x1,y1),(h,w)


def _light(source,p,box,turns=0):
    """Correct an ROI using full-image light coordinates, before mirror/rotation."""
    h,w=source.shape[:2];x0,y0,x1,y1=box
    view=np.rot90(source[:,::-1] if p.flip else source,-turns)
    raw=view[y0:y1,x0:x1]
    field=p.flat_field if p.flat_mode!='off' else None
    if (field is None or p.flat_strength<=0) and p.vignette<=0:return raw
    out=np.nan_to_num(np.array(raw,np.float32,copy=True),nan=0.,posinf=1.,neginf=0.)
    xx=np.arange(x0,x1)[None,:];yy=np.arange(y0,y1)[:,None]
    if turns==0:sx,sy=xx,yy
    elif turns==1:sx,sy=yy,h-1-xx
    elif turns==2:sx,sy=w-1-xx,h-1-yy
    else:sx,sy=w-1-yy,xx
    if p.flip:sx=w-1-sx
    if field is not None and p.flat_strength>0:
        grid=np.asarray(field,np.float32)
        gx=np.linspace(0,grid.shape[1]-1,w,dtype=np.float32)[sx]
        gy=np.linspace(0,grid.shape[0]-1,h,dtype=np.float32)[sy]
        ix,iy=gx.astype(np.int32),gy.astype(np.int32);fx=(gx-ix.astype(np.float32))[...,None];fy=(gy-iy.astype(np.float32))[...,None]
        jx,jy=np.minimum(ix+1,grid.shape[1]-1),np.minimum(iy+1,grid.shape[0]-1)
        top=grid[iy,ix]*(1-fx)+grid[iy,jx]*fx
        bottom=grid[jy,ix]*(1-fx)+grid[jy,jx]*fx
        light=top*(1-fy)+bottom*fy
        out/=np.maximum(1+np.float32(p.flat_strength)*(light-1),np.float32(.1))
    if p.vignette>0:
        vx=np.linspace(-1,1,w,dtype=np.float32)[sx];vy=np.linspace(-1,1,h,dtype=np.float32)[sy]
        out/=(1-np.float32(.7*p.vignette)*(vx*vx+vy*vy)/2)[...,None]
    return (np.maximum(out,0) if p.scene_hdr else np.clip(out,0,1)).astype(np.float32)


def _plain(p):
    value=copy.deepcopy(p.__dict__)
    value.update(flat_mode='off',flat_field=None,vignette=0.,flip=False,rotation=0,straighten=0.,crop=geometry.FULL.copy(),perspective=None)
    return core.Params(**value)


def _levels(source,p,clean,repair_context=None,check=lambda:None):
    if p.black is not None and p.white is not None:return
    # Exact global quantiles require a scalar image, not full RGB working arrays.
    h,w=source.shape[:2]
    if h*w*4>256*1024*1024:raise ViewportUnavailable('自动黑白场统计超过预算，请先固定黑白场')
    luminance=np.empty((h,w),np.float32)
    for top in range(0,h,128):
        check()
        raw=_light(source,p,(0,top,w,min(h,top+128)))
        scene=core.scene_linear(raw,clean,repair_context)*core.wb_gain(clean)
        luminance[top:top+128]=core.luma(scene,p.working_space)
    import metering
    lo,hi=metering.levels(luminance,source[:,::-1] if p.flip else source,p.meter_mode)
    if p.black is None:clean.black=float(lo)
    if p.white is None:clean.white=float(hi)


def render(source,p,box,original=False,cancel=None):
    p=p.normalized();box,shape=box_for(source.shape,p.__dict__,box)
    if p.hdr_enabled and not original:
        if source.shape[0]*source.shape[1]*128>768*1024*1024:raise ViewportUnavailable('HDR SDR proof exceeds the temporary budget; use fit view')
        check_cancel(cancel);wide=core.process(source,p,p.working_space);check_cancel(cancel)
        x0,y0,x1,y1=box
        return wide[y0:y1,x0:x1],box,shape,'HDR SDR proof'
    global _CONTEXT,_CONTEXT_KEY
    clean=_plain(p);context=None
    advanced=not original and (p.local_tone!=0 or p.local_detail!=0 or p.dust_strength>0 or p.local_edits or (p.channel_repair and p.mode=='color_negative') or (box[2]-box[0])*(box[3]-box[1])>1_000_000)
    if advanced:
        import region_context
        key=(id(source),repr(p.__dict__))
        if _CONTEXT_KEY!=key or _CONTEXT is None or _CONTEXT.source is not source:
            _CONTEXT=None;_CONTEXT_KEY=None
            context=region_context.Context(source,p,clean,_light,_levels,cancel)
            context.check();_CONTEXT=context;_CONTEXT_KEY=key
        else:context=_CONTEXT;context.cancel=cancel
        context.calls=0;context.work=0
    elif not original:_levels(source,p,clean,check=lambda:check_cancel(cancel))
    turns,angle,_=geometry.settings(p.__dict__)
    (rh,rw),safe,crop=geometry.bounds(source.shape,p.__dict__)
    left,top=box[0]+safe[0]+crop[0],box[1]+safe[1]+crop[1]
    width,height=box[2]-box[0],box[3]-box[1]
    co,si=math.cos(math.radians(angle)),math.sin(math.radians(angle))
    tx=rw/2-co*rw/2-si*rh/2;ty=rh/2+si*rw/2-co*rh/2
    corners=[(co*x+si*y+tx,-si*x+co*y+ty) for x in (left,left+width) for y in (top,top+height)]
    project=None
    if p.perspective is not None:
        _,project=geometry.projective_layout(source.shape,p.__dict__)
        project=project@np.array([[1,0,box[0]],[0,1,box[1]],[0,0,1.]])
        corners=perspective.map_points(project,[(0,0),(width,0),(width,height),(0,height)])
        source_box=(max(0,math.floor(min(v[0] for v in corners))-3),max(0,math.floor(min(v[1] for v in corners))-3),
                    min(rw,math.ceil(max(v[0] for v in corners))+3),min(rh,math.ceil(max(v[1] for v in corners))+3))
    elif abs(angle)<1e-8:source_box=(left,top,left+width,top+height)
    else:
        source_box=(max(0,math.floor(min(v[0] for v in corners))-3),max(0,math.floor(min(v[1] for v in corners))-3),
                    min(rw,math.ceil(max(v[0] for v in corners))+3),min(rh,math.ceil(max(v[1] for v in corners))+3))
    if (source_box[2]-source_box[0])*(source_box[3]-source_box[1])>MAX_PIXELS*2:raise ViewportUnavailable('旋转预览区域超过预算')
    raw=np.rot90(source[:,::-1] if p.flip else source,-turns)[source_box[1]:source_box[3],source_box[0]:source_box[2]] if original else (None if context is not None else _light(source,p,source_box,turns))
    if original:
        import colorio
        wide=core.srgb_encode(raw).astype(np.float32) if p.working_space=='srgb' else colorio.encode(raw,p.working_space)
    elif context is not None:
        x0,y0,x1,y1=source_box;h,w=source.shape[:2]
        if turns==0:canonical=source_box
        elif turns==1:canonical=(y0,h-x1,y1,h-x0)
        elif turns==2:canonical=(w-x1,h-y1,w-x0,h-y0)
        else:canonical=(w-y1,x0,w-y0,x1)
        wide=np.rot90(context.get(canonical),-turns)
    else:wide=core.process(raw,clean,p.working_space)
    if project is not None or abs(angle)>=1e-8:
        inverse=(co,si,co*left+si*top+tx-source_box[0],-si,co,-si*left+co*top+ty-source_box[1])
        method=Image.Transform.AFFINE
        if project is not None:
            project=np.array([[1,0,-source_box[0]],[0,1,-source_box[1]],[0,0,1.]])@project
            inverse=perspective.coefficients(project);method=Image.Transform.PERSPECTIVE
        out=np.empty((height,width,3),np.float32)
        for channel in range(3):
            image=Image.fromarray(np.ascontiguousarray(wide[...,channel]))
            out[...,channel]=np.asarray(image.transform((width,height),method,inverse,resample=Image.Resampling.BICUBIC))
        wide=np.clip(out,0,1)
    return wide,box,shape,'可见区域'


def clear_context():
    global _CONTEXT,_CONTEXT_KEY
    _CONTEXT=None;_CONTEXT_KEY=None
