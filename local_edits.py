"""Non-destructive local edits in unflipped, pre-geometry normalized coordinates."""
import copy
import numpy as np
MAX_EDITS=256
MAX_POINTS=1024

def normalize(edits):
    if edits is None:return []
    if not isinstance(edits,list) or len(edits)>MAX_EDITS:raise ValueError('局部编辑必须是列表，最多 256 笔')
    result=[]
    for edit in edits:
        if not isinstance(edit,dict) or edit.get('kind') not in ('clone','brush','gradient'):raise ValueError('局部编辑类型无效')
        kind=edit['kind'];points=edit.get('points')
        if not isinstance(points,list) or not 1<=len(points)<=MAX_POINTS:raise ValueError('笔画取样点数量无效')
        a=np.asarray(points,dtype=np.float64)
        if a.shape!=(len(points),2) or not np.isfinite(a).all() or np.any((a<0)|(a>1)):raise ValueError('局部编辑坐标应为 0–1 有限数值')
        def number(key,default,low,high):
            try:v=float(edit.get(key,default))
            except (TypeError,ValueError):raise ValueError('局部编辑参数无效：'+key)
            if not np.isfinite(v) or not low<=v<=high:raise ValueError('局部编辑参数越界：'+key)
            return v
        item={'kind':kind,'points':a.tolist(),'opacity':number('opacity',1,0,1)}
        if kind=='gradient':
            if len(points)!=2 or np.linalg.norm(a[1]-a[0])<1e-5:raise ValueError('渐变需要两个不同端点')
        else:item.update(radius=number('radius',.025,.001,.25),feather=number('feather',.5,0,1))
        if kind=='clone':
            offset=np.asarray(edit.get('offset'),dtype=np.float64)
            if offset.shape!=(2,) or not np.isfinite(offset).all() or np.max(np.abs(offset))>1:raise ValueError('仿制来源偏移无效')
            item['offset']=offset.tolist()
        else:item['ev']=number('ev',0,-4,4)
        result.append(item)
    return result

def apply(rgb,edits,space='srgb',flip=False,origin=(0,0),shape=None,source_reader=None,hdr=False):
    if not edits:return rgb
    import colorio
    if hdr:import hdr_color as colorio
    h,w=shape or rgb.shape[:2];out=np.array(rgb,np.float32,copy=True)
    rx,ry=origin;rh,rw=rgb.shape[:2]
    for op in normalize(edits):
        if op['opacity']==0 or (op['kind']!='clone' and op['ev']==0):continue
        pts=np.array(op['points'],np.float64)
        if flip:pts[:,0]=1-pts[:,0]
        pts*=np.array([max(1,w-1),max(1,h-1)],np.float32)
        radius=op.get('radius',0)*max(1,min(h-1,w-1))
        if op['kind']=='gradient':x0,y0,x1,y1=0,0,w,h
        else:
            x0,y0=np.maximum(0,np.floor(pts.min(axis=0)-radius).astype(int))
            x1,y1=np.minimum([w,h],np.ceil(pts.max(axis=0)+radius+1).astype(int))
        x0,y0=max(x0,rx),max(y0,ry);x1,y1=min(x1,rx+rw),min(y1,ry+rh)
        if x1<=x0 or y1<=y0:continue
        # Each clone reads the immutable result before this stroke, never its own writes.
        source=None
        if op['kind']=='clone':
            dx,dy=op['offset'];dx=-dx if flip else dx
            ox0=max(0,min(w-1,int(np.floor(x0+dx*max(1,w-1)))-2))
            oy0=max(0,min(h-1,int(np.floor(y0+dy*max(1,h-1)))-2))
            ox1=max(ox0+1,min(w,int(np.ceil(x1+dx*max(1,w-1)))+2))
            oy1=max(oy0+1,min(h,int(np.ceil(y1+dy*max(1,h-1)))+2))
            source=source_reader((ox0,oy0,ox1,oy1)) if source_reader else out[oy0-ry:oy1-ry,ox0-rx:ox1-rx].copy()
        for top in range(int(y0),int(y1),128):
            bottom=min(top+128,int(y1));xx=np.arange(x0,x1,dtype=np.float32)[None,:];yy=np.arange(top,bottom,dtype=np.float32)[:,None]
            if op['kind']=='gradient':
                delta=pts[1]-pts[0];weight=np.clip(((xx-pts[0,0])*delta[0]+(yy-pts[0,1])*delta[1])/max(1e-12,float(np.dot(delta,delta))),0,1)
            else:
                distance=np.full((bottom-top,int(x1-x0)),np.inf,np.float32)
                for a,b in zip(pts[:-1] if len(pts)>1 else pts,pts[1:] if len(pts)>1 else pts):
                    delta=b-a;den=float(np.dot(delta,delta));t=np.clip(((xx-a[0])*delta[0]+(yy-a[1])*delta[1])/max(1e-12,den),0,1)
                    distance=np.minimum(distance,np.sqrt((xx-a[0]-t*delta[0])**2+(yy-a[1]-t*delta[1])**2))
                weight=(distance < radius-1e-7).astype(np.float32) if op['feather']==0 else np.clip((radius-distance)/max(radius*op['feather'],1e-6),0,1)
            weight=weight*np.float32(op['opacity']);tile=out[top-ry:bottom-ry,int(x0)-rx:int(x1)-rx]
            if source is not None:
                dx,dy=op['offset'];dx=-dx if flip else dx
                sx=xx+dx*max(1,w-1);sy=yy+dy*max(1,h-1)
                valid=(sx>=0)&(sx<=w-1)&(sy>=0)&(sy<=h-1);weight=weight*valid
                sx=np.clip(sx,0,w-1);sy=np.clip(sy,0,h-1);ix=sx.astype(int);iy=sy.astype(int);fx=(sx-ix)[...,None];fy=(sy-iy)[...,None]
                jx=np.clip(np.minimum(ix+1,w-1)-ox0,0,source.shape[1]-1);jy=np.clip(np.minimum(iy+1,h-1)-oy0,0,source.shape[0]-1)
                ix=np.clip(ix-ox0,0,source.shape[1]-1);iy=np.clip(iy-oy0,0,source.shape[0]-1)
                sampled=(source[iy,ix]*(1-fx)+source[iy,jx]*fx)*(1-fy)+(source[jy,ix]*(1-fx)+source[jy,jx]*fx)*fy
                adjusted=tile+(sampled-tile)*weight[...,None]
            else:
                adjusted=colorio.encode(colorio.decode(tile,space)*np.exp2(weight[...,None]*np.float32(op['ev'])),space)
            active=weight>0;tile[active]=adjusted[active]
    return out
