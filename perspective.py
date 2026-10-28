"""Validated four-corner planar rectification in normalized image-edge coordinates."""
import math
import numpy as np

FULL=[[0.,0.],[1.,0.],[1.,1.],[0.,1.]]


def normalize(value):
    if value is None:return None
    if not isinstance(value,dict):raise ValueError('透视参数无效')
    try:points=np.asarray(value['points'],dtype=float)
    except (KeyError,TypeError,ValueError):raise ValueError('透视需要四个有效角点') from None
    if points.shape!=(4,2) or not np.isfinite(points).all() or np.any((points<0)|(points>1)):
        raise ValueError('透视角点必须位于图像内')
    edges=np.roll(points,-1,axis=0)-points
    cross=edges[:,0]*np.roll(edges,-1,axis=0)[:,1]-edges[:,1]*np.roll(edges,-1,axis=0)[:,0]
    area=.5*np.sum(points[:,0]*np.roll(points,-1,axis=0)[:,1]-points[:,1]*np.roll(points,-1,axis=0)[:,0])
    if np.any(cross<1e-5) or area<.001 or np.min(np.linalg.norm(edges,axis=1))<.005:
        raise ValueError('请按左上、右上、右下、左下排列，角点不可交叉或挤成直线')
    ratio=value.get('ratio')
    if ratio is not None:
        try:ratio=float(ratio)
        except (ValueError,TypeError):raise ValueError('透视输出比例无效') from None
        if not math.isfinite(ratio) or not .1<=ratio<=10:raise ValueError('透视输出比例需在 1:10 至 10:1 之间')
    if points.tolist()==FULL and ratio is None:return None
    matrix(points)
    return {'points':points.tolist(),'ratio':ratio}


def matrix(points):
    """Unit output square -> source quadrilateral, solved without a library warp."""
    equations=[];target=[]
    for (u,v),(x,y) in zip(FULL,points):
        equations.extend(([u,v,1,0,0,0,-u*x,-v*x],[0,0,0,u,v,1,-u*y,-v*y]))
        target.extend((x,y))
    try:result=np.append(np.linalg.solve(equations,target),1.).reshape(3,3)
    except np.linalg.LinAlgError:raise ValueError('透视四角无法求解，请重新选点') from None
    denominator=np.array([[*p,1] for p in FULL])@result[2]
    if not np.isfinite(result).all() or denominator.min()<1e-5 or np.linalg.cond(result)>1e7:
        raise ValueError('透视形变过大，请重新选点')
    return result


def layout(shape,value):
    """No enlargement: estimate opposing edge lengths, then fit chosen ratio."""
    h,w=shape[:2];points=np.asarray(value['points'])*[w,h]
    lengths=np.linalg.norm(np.roll(points,-1,axis=0)-points,axis=1)
    ow,oh=(lengths[0]+lengths[2])/2,(lengths[1]+lengths[3])/2
    ratio=value.get('ratio')
    if ratio:
        if ow/oh>ratio:ow=oh*ratio
        else:oh=ow/ratio
    scale=min(1.,w/ow,h/oh);ow,oh=max(1,int(ow*scale)),max(1,int(oh*scale))
    inverse=np.diag([w,h,1.])@matrix(value['points'])@np.diag([1./ow,1./oh,1.])
    return (oh,ow),inverse


def map_points(inverse,points):
    a=np.asarray([[x,y,1.] for x,y in points])@inverse.T
    return a[:,:2]/a[:,2,None]


def coefficients(inverse):
    return tuple((inverse/inverse[2,2]).ravel()[:8])
