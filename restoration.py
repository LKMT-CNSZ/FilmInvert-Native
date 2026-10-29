"""可选平场和孤立尘点修复，float32；关闭时不做数据往返。"""
import numpy as np

def normalize_field(value):
    try:
        a = np.asarray(value, np.float32)
    except (ValueError,TypeError):
        return None
    if a.ndim != 3 or a.shape[2] != 3 or not 1 <= a.shape[0] <= 32 or not 1 <= a.shape[1] <= 32:
        return None
    if not np.isfinite(a).all() or a.min() < .05 or a.max() > 20:
        return None
    return a.tolist()

def reference_field(reference):
    """将均匀参考缩成稳健照明网格，按通道中值归一化，保留原白平衡。"""
    x = np.asarray(reference,np.float32)
    if x.ndim != 3 or x.shape[2] != 3 or min(x.shape[:2]) < 8 or not np.isfinite(x).all():
        raise ValueError('平场参考应为有效的 RGB 图像，至少 8×8 像素')
    small = x[::max(1,x.shape[0]//512),::max(1,x.shape[1]//512)]
    if np.any(np.median(small,axis=(0,1)) < .005):
        raise ValueError('平场参考过暗；请使用同一光路拍摄、未过曝的均匀亮场')
    if np.mean(small >= .999) > .1:
        raise ValueError('平场参考有大量过曝像素，不能用于可靠校正')
    rows, cols = min(32,small.shape[0]//2), min(32,small.shape[1]//2)
    yedges = np.linspace(0,small.shape[0],rows+1,dtype=int)
    xedges = np.linspace(0,small.shape[1],cols+1,dtype=int)
    grid = np.empty((rows,cols,3),np.float32)
    for y in range(rows):
        for c in range(cols):
            grid[y,c] = np.median(small[yedges[y]:yedges[y+1],xedges[c]:xedges[c+1]],axis=(0,1))
    for _ in range(2):
        pad = np.pad(grid,((1,1),(1,1),(0,0)),mode='edge')
        grid = sum(pad[y:y+rows,c:c+cols] for y in range(3) for c in range(3))/np.float32(9)
    grid /= np.maximum(np.median(grid,axis=(0,1)),np.float32(1e-6))
    if grid.min() < .1 or grid.max() > 10:
        raise ValueError('平场亮度变化过大，请检查是否误用了普通照片作为参考')
    return grid.astype(np.float32).tolist()

def resize_field(field, height, width):
    grid = np.asarray(field,np.float32)
    y = np.linspace(0,grid.shape[0]-1,height,dtype=np.float32)
    x = np.linspace(0,grid.shape[1]-1,width,dtype=np.float32)
    iy, ix = y.astype(np.int32), x.astype(np.int32)
    fy, fx = (y-iy.astype(np.float32))[:,None,None], (x-ix.astype(np.float32))[None,:,None]
    iy2, ix2 = np.minimum(iy+1,grid.shape[0]-1), np.minimum(ix+1,grid.shape[1]-1)
    top = grid[iy[:,None],ix[None,:]]*(1-fx)+grid[iy[:,None],ix2[None,:]]*fx
    bottom = grid[iy2[:,None],ix[None,:]]*(1-fx)+grid[iy2[:,None],ix2[None,:]]*fx
    return (top*(1-fy)+bottom*fy).astype(np.float32)

def correct_light(rgb, field=None, strength=0., vignette=0.,hdr=False):
    if (field is None or strength <= 0) and vignette <= 0:
        return rgb
    h,w = rgb.shape[:2]
    out = np.array(rgb,np.float32,copy=True)
    if field is not None and strength > 0:
        # 分块展开场，避免 150MP 图额外持有完整 H×W×3 照明数组。
        grid = np.asarray(field,np.float32)
        horizontal = resize_field(grid,grid.shape[0],w)
        for y0 in range(0,h,128):
            yy = np.linspace(0,grid.shape[0]-1,h,dtype=np.float32)[y0:y0+128]
            iy = yy.astype(np.int32); mix = (yy-iy.astype(np.float32))[:,None,None]
            light = horizontal[iy]*(1-mix)+horizontal[np.minimum(iy+1,grid.shape[0]-1)]*mix
            out[y0:y0+128] /= np.maximum(1+np.float32(strength)*(light-1),np.float32(.1))
    if vignette > 0:
        xx = np.linspace(-1,1,w,dtype=np.float32)[None,:]
        for y0 in range(0,h,128):
            yy = np.linspace(-1,1,h,dtype=np.float32)[y0:y0+128,None]
            field = 1-np.float32(.7*vignette)*(xx*xx+yy*yy)/2
            out[y0:y0+128] /= field[...,None]
    return (np.maximum(out,0) if hdr else np.clip(out,0,1)).astype(np.float32)

def remove_dust(rgb, strength=0., radius=1):
    """中值残差 + 8 连通小斑点；拒绝长线及大块，保留未选中像素。"""
    if strength <= 0:
        return rgb
    radius = min(3,max(1,int(radius))); k = 2*radius+1
    x = np.asarray(rgb,np.float32); h,w = x.shape[:2]
    if min(h,w) < k:
        return rgb
    median = np.empty_like(x)
    for y in range(0,h,128):
        for c in range(0,w,128):
            y1,c1 = min(h,y+128),min(w,c+128)
            tile = x[max(0,y-radius):min(h,y1+radius),max(0,c-radius):min(w,c1+radius)]
            tile = np.pad(tile,((max(0,radius-y),max(0,y1+radius-h)),
                                (max(0,radius-c),max(0,c1+radius-w)),(0,0)),mode='reflect')
            windows = np.lib.stride_tricks.sliding_window_view(tile,(k,k),axis=(0,1))
            median[y:y+128,c:c+128] = np.median(windows,axis=(-2,-1))
    luma_weights = np.array([.2126,.7152,.0722],np.float32)
    residual = np.abs((x-median) @ luma_weights)
    mask = residual > np.float32(.18-.15*strength)
    mask[:radius] = False; mask[-radius:] = False
    mask[:,:radius] = False; mask[:,-radius:] = False
    # 候选过多表示这是纹理/强噪声画面；保护画面，停止自动替换。
    if np.mean(mask) > .08:
        return rgb
    out = x.copy(); seen = np.zeros((h,w),bool)
    for y,c in zip(*np.where(mask)):
        if seen[y,c]:
            continue
        component, todo = [], [(int(y),int(c))]
        seen[y,c] = True
        while todo:
            py,px = todo.pop(); component.append((py,px))
            for dy in (-1,0,1):
                for dx in (-1,0,1):
                    qy,qx = py+dy,px+dx
                    if 0 <= qy < h and 0 <= qx < w and mask[qy,qx] and not seen[qy,qx]:
                        seen[qy,qx] = True; todo.append((qy,qx))
        ys,xs = zip(*component)
        bh,bw = max(ys)-min(ys)+1,max(xs)-min(xs)+1
        if len(component) <= k*k and max(bh,bw) <= k and max(bh,bw) <= 3*min(bh,bw):
            out[ys,xs] = median[ys,xs]
    return out.astype(np.float32)
