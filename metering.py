"""Conservative edge-band exclusion for automatic metering, never output cropping."""
import numpy as np


def bounds(image):
    """Return a bounded-analysis source-pixel rectangle and evidence.

    Only coherent, nearly uniform extreme bands connected to an image edge
    qualify. A small interior inset is never used as an automatic fallback.
    The user can always select legacy metering for unusual photographed borders.
    """
    h,w=image.shape[:2]
    if min(h,w)<48:return (0,0,w,h),[]
    sy=max(1,int(np.ceil(h/900)));sx=max(1,int(np.ceil(w/900)))
    a=np.asarray(image[::sy,::sx],np.float32)
    if a.ndim==3:a=a.mean(axis=2)
    a=np.nan_to_num(a,nan=0.,posinf=1.,neginf=0.)
    low,high=np.quantile(a,[.01,.99]);span=float(high-low)
    if span<1e-5:return (0,0,w,h),[]
    rect=[0,0,a.shape[1],a.shape[0]];evidence=[]
    for _ in range(3):
        changed=False
        for side in range(4):
            x0,y0,x1,y1=rect;view=a[y0:y1,x0:x1]
            if side==1:view=view[::-1]
            elif side==2:view=view.T
            elif side==3:view=view.T[::-1]
            n,m=view.shape
            limit=min(int(n*.08),max(2,int((h if side<2 else w)*.08/(sy if side<2 else sx))))
            if limit<2 or m<24:continue
            middle=view[:,max(0,m//20):m-max(1,m//20)]
            profile=np.median(middle,axis=1)
            candidates=[]
            for k in range(1,limit+1):
                band=middle[:k];outside=float(np.median(band))
                if not (outside<low+.06*span or outside>high-.06*span):continue
                if np.quantile(band,.90)-np.quantile(band,.10)>.12*span:continue
                inside=np.median(middle[k:min(n,k+3)],axis=0)
                jump=np.abs(inside-np.median(band,axis=0))
                if abs(float(np.median(inside))-outside)>.15*span and np.mean(jump>.10*span)>.72:
                    candidates.append(k)
            if not candidates:continue
            k=max(candidates)
            # One analysis cell of guard avoids interpolation mixing at the seam.
            amount=k+1
            if side==0:rect[1]+=amount
            elif side==1:rect[3]-=amount
            elif side==2:rect[0]+=amount
            else:rect[2]-=amount
            evidence.append({'side':('top','bottom','left','right')[side],'analysis_pixels':amount})
            changed=True
        if not changed:break
    x0,y0,x1,y1=rect
    if (x1-x0)*(y1-y0)<a.size*.60:return (0,0,w,h),[]
    return (x0*sx,y0*sy,min(w,x1*sx),min(h,y1*sy)),evidence


def region(image,mode='legacy'):
    if mode!='edges':return image
    (x0,y0,x1,y1),_=bounds(image)
    return image[y0:y1,x0:x1]


def levels(luminance,source,mode='legacy'):
    if mode!='edges':return float(np.quantile(luminance,.002)),float(np.quantile(luminance,.998))
    values=luminance
    if mode=='edges':
        (x0,y0,x1,y1),_=bounds(source)
        values=luminance[y0:y1,x0:x1]
    return np.quantile(values,[.002,.998])
