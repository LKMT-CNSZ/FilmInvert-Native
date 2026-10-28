"""Conservative physical frame-edge angle suggestions; no source mutation."""
import math
import numpy as np


def _fit_edge(image,box,vertical,side):
    x0,y0,x1,y1=box;h,w=image.shape[:2]
    span=(y1-y0) if vertical else (x1-x0)
    across=(x1-x0) if vertical else (y1-y0)
    if span<80 or across<32:return None
    position=(x0,x1)[side] if vertical else (y0,y1)[side]
    limit=w if vertical else h;radius=min(256,max(12,round(across*.06)))
    lo=max(0,position-radius);hi=min(limit,position+radius)
    if hi-lo<12:return None
    along=np.linspace((y0 if vertical else x0)+span*.12,(y1 if vertical else x1)-span*.12,64).astype(int)
    samples=image[along[:,None],np.arange(lo,hi)[None,:]] if vertical else image[np.arange(lo,hi)[:,None],along[None,:]].transpose(1,0,2)
    lum=samples.mean(axis=2)
    smooth=sum(lum[:,i:lum.shape[1]-4+i] for i in range(5))/5
    gradient=np.abs(smooth[:,4:]-smooth[:,:-4]);indices=np.argmax(gradient,axis=1)
    strengths=gradient[np.arange(len(along)),indices]
    points=lo+indices+4;keep=strengths>=max(.012,float(np.quantile(strengths,.25)))
    if keep.sum()<40:return None
    for _ in range(3):
        slope,intercept=np.polyfit(along[keep],points[keep],1)
        error=np.abs(points-(slope*along+intercept));keep=(error<max(3,across*.003))&(strengths>.012)
        if keep.sum()<40:return None
    residual=float(np.median(error[keep]));coverage=float(keep.mean())
    angle=math.degrees(math.atan(slope))*(1 if vertical else -1)
    endpoints=[[float(slope*v+intercept),float(v)] if vertical else [float(v),float(slope*v+intercept)] for v in (along[0],along[-1])]
    return {'angle':angle,'coverage':coverage,'residual_pixels':residual,'line':endpoints}


def suggest(image,rows):
    """Only agree on two long parallel physical borders; otherwise decline."""
    result=[]
    for row in rows:
        box=row['box'];vertical=(box[3]-box[1])>=(box[2]-box[0])
        edges=[_fit_edge(image,box,vertical,side) for side in (0,1)]
        reliable=all(edge is not None for edge in edges)
        if reliable:reliable=abs(edges[0]['angle']-edges[1]['angle'])<=.25 and max(abs(edge['angle']) for edge in edges)<=10
        if reliable:
            angle=float(np.mean([edge['angle'] for edge in edges]))
            result.append({'id':row['id'],'box':list(box),'available':True,'angle':angle,
                'confidence':'高' if min(e['coverage'] for e in edges)>.85 else '中','edges':edges,
                'reason':'两侧长片边倾角一致；请对照片框确认'})
        else:result.append({'id':row['id'],'box':list(box),'available':False,'reason':'片边证据不足或不一致，请手动画线'})
    return result


def same_strip(a,b):
    vertical=(a[3]-a[1])>=(a[2]-a[0])
    lo,hi=(0,2) if vertical else (1,3)
    overlap=max(0,min(a[hi],b[hi])-max(a[lo],b[lo]))
    return overlap>=.8*min(a[hi]-a[lo],b[hi]-b[lo])
