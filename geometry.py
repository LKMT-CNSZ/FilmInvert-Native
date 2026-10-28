"""Reversible output geometry. Rotations keep float precision; originals are untouched.

Order: existing input mirror, quarter turn clockwise, clockwise straightening with
an inscribed canvas, optional four-corner rectification, then normalized edge
crop. A and C share one inverse resampling when rectification is enabled.
"""
import math
import numpy as np
from PIL import Image
import perspective

FULL = [0., 0., 1., 1.]


def line_straighten(points,shape,axis='horizontal',limit=15.):
    """Clockwise correction from normalized endpoints in an unstraightened view."""
    if axis not in ('horizontal','vertical'):raise ValueError('请选择水平或竖直参考')
    a=np.asarray(points,dtype=float)
    if a.shape!=(2,2) or not np.isfinite(a).all() or np.any((a<0)|(a>1)):
        raise ValueError('参考线端点无效')
    h,w=shape[:2];dx,dy=(a[1]-a[0])*[w,h]
    if math.hypot(dx,dy)<max(2,min(w,h)*.01):raise ValueError('参考线太短，请沿较长的直线取点')
    angle=math.degrees(math.atan2(dy,dx))
    correction=((90 if axis=='vertical' else 0)-angle+90)%180-90
    if abs(correction)>limit:raise ValueError(f'所需校直 {correction:+.2f}° 超出 ±{limit:g}°，请先转 90°或重选参考线')
    return float(correction)


def lines_straighten(lines,shape,limit=15.):
    """Combine independent horizontal/vertical references by source-pixel length.

    Each reference measures the same rigid rotation. Invalid references remain
    visible in the editor but do not contribute; callers must show their errors.
    """
    valid=[];rejected=[]
    for index,line in enumerate(lines):
        try:
            angle=line_straighten(line['points'],shape,line['axis'],limit)
            delta=(np.asarray(line['points'][1])-line['points'][0])*[shape[1],shape[0]]
            length=float(np.linalg.norm(delta))
            valid.append({'index':index,'angle':angle,'length':length})
        except (ValueError,TypeError,KeyError,IndexError) as error:
            rejected.append({'index':index,'reason':str(error)})
    if not valid:
        raise ValueError('没有可用参考线；'+(rejected[0]['reason'] if rejected else '请在左侧拖动画线'))
    angle=sum(row['angle']*row['length'] for row in valid)/sum(row['length'] for row in valid)
    return {'angle':float(angle),'used':valid,'rejected':rejected,
            'max_deviation':max(abs(row['angle']-angle) for row in valid)}


def number(value, default=0.):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError, OverflowError):
        return default


def normalize_crop(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return FULL.copy()
    if any(not math.isfinite(number(v, float('nan'))) for v in value):
        return FULL.copy()
    x0, y0, x1, y1 = [float(np.clip(float(v), 0, 1)) for v in value]
    if x1 - x0 < .001 or y1 - y0 < .001:
        return FULL.copy()
    return [x0, y0, x1, y1]


def settings(value):
    return (int(round(number(value.get('rotation', 0)) / 90)) % 4,
            float(np.clip(number(value.get('straighten', 0)), -15, 15)),
            normalize_crop(value.get('crop')))


def bounds(shape, value):
    """Pixel-edge boxes used by both resampling and inverse coordinate mapping."""
    turns, angle, crop = settings(value)
    height, width = shape[:2]
    if turns % 2:
        width, height = height, width
    if abs(angle) < 1e-8:
        safe = (0, 0, width, height)
    else:
        cosine, sine = abs(math.cos(math.radians(angle))), abs(math.sin(math.radians(angle)))
        # Two source pixels of guard keep cubic filtering away from the fill edge.
        scale = min(max(1, width - 4) / (cosine * width + sine * height),
                    max(1, height - 4) / (sine * width + cosine * height), 1.)
        inner_w, inner_h = max(1, int(width * scale)), max(1, int(height * scale))
        left, top = (width - inner_w) // 2, (height - inner_h) // 2
        safe = (left, top, left + inner_w, top + inner_h)
    sw, sh = safe[2] - safe[0], safe[3] - safe[1]
    left, top = min(sw - 1, int(round(crop[0] * sw))), min(sh - 1, int(round(crop[1] * sh)))
    right, bottom = max(left + 1, int(round(crop[2] * sw))), max(top + 1, int(round(crop[3] * sh)))
    return (height, width), safe, (left, top, min(sw, right), min(sh, bottom))


def output_shape(shape, value):
    if value.get('perspective') is not None:return projective_layout(shape,value)[0]
    _, _, (left, top, right, bottom) = bounds(shape, value)
    return bottom - top, right - left


def apply(array, value):
    turns, angle, crop = settings(value)
    if value.get('perspective') is not None:
        shape,inverse=projective_layout(array.shape,value)
        source=np.rot90(array,-turns) if turns else array
        out=np.empty((*shape,source.shape[2]),np.float32)
        for channel in range(source.shape[2]):
            image=Image.fromarray(np.ascontiguousarray(source[...,channel],dtype=np.float32))
            out[...,channel]=np.asarray(image.transform((shape[1],shape[0]),Image.Transform.PERSPECTIVE,
                perspective.coefficients(inverse),resample=Image.Resampling.BICUBIC))
        return np.clip(out,0,1)
    if not turns and abs(angle) < 1e-8 and crop == FULL:
        return array
    out = np.rot90(array, -turns) if turns else array
    (height, width), safe, box = bounds(array.shape, value)
    if abs(angle) >= 1e-8:
        # Pillow mode F preserves float32 values; no 8-bit intermediate or JPEG.
        cosine, sine = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        cx, cy = width / 2, height / 2
        inverse = (cosine, sine, cx - cosine * cx - sine * cy,
                   -sine, cosine, cy + sine * cx - cosine * cy)
        transformed = np.empty_like(out, dtype=np.float32)
        for channel in range(out.shape[2]):
            image = Image.fromarray(np.ascontiguousarray(out[..., channel], dtype=np.float32))
            transformed[..., channel] = np.asarray(image.transform((width, height), Image.Transform.AFFINE,
                inverse, resample=Image.Resampling.BICUBIC), dtype=np.float32)
        out = transformed
    out = out[safe[1]:safe[3], safe[0]:safe[2]]
    return np.clip(out[box[1]:box[3], box[0]:box[2]], 0, 1).astype(np.float32, copy=False)


def projective_layout(shape,value):
    """One inverse map composes A rotation, C rectification, and final crop."""
    spec=perspective.normalize(value.get('perspective'))
    turns,angle,crop=settings(value)
    (height,width),safe,_=bounds(shape,dict(value,crop=FULL))
    sh,sw=safe[3]-safe[1],safe[2]-safe[0]
    if spec is None:
        oh,ow=sh,sw;project=np.eye(3)
    else:(oh,ow),project=perspective.layout((sh,sw),spec)
    left,top=min(ow-1,int(round(crop[0]*ow))),min(oh-1,int(round(crop[1]*oh)))
    right,bottom=max(left+1,min(ow,int(round(crop[2]*ow)))),max(top+1,min(oh,int(round(crop[3]*oh))))
    co,si=math.cos(math.radians(angle)),math.sin(math.radians(angle))
    rotate=np.array([[co,si,width/2-co*width/2-si*height/2],[-si,co,height/2+si*width/2-co*height/2],[0,0,1.]])
    offset=np.array([[1,0,safe[0]],[0,1,safe[1]],[0,0,1.]])
    crop_offset=np.array([[1,0,left],[0,1,top],[0,0,1.]])
    return (bottom-top,right-left),rotate@offset@project@crop_offset


def source_point(x, y, shape, value):
    """Display normalized edge coordinate -> unmirrored original coordinate."""
    turns, angle, _ = settings(value)
    if value.get('perspective') is not None:
        (oh,ow),inverse=projective_layout(shape,value)
        px,py=perspective.map_points(inverse,[(float(np.clip(x,0,1))*ow,float(np.clip(y,0,1))*oh)])[0]
        height,width=shape[:2]
        if turns%2:height,width=width,height
        x,y=px/width,py/height
        if turns==1:x,y=y,1-x
        elif turns==2:x,y=1-x,1-y
        elif turns==3:x,y=1-y,x
        if value.get('flip'):x=1-x
        return float(np.clip(x,0,1)),float(np.clip(y,0,1))
    (height, width), safe, box = bounds(shape, value)
    x = safe[0] + box[0] + float(np.clip(x, 0, 1)) * (box[2] - box[0])
    y = safe[1] + box[1] + float(np.clip(y, 0, 1)) * (box[3] - box[1])
    cosine, sine = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    x, y = (cosine * (x - width / 2) + sine * (y - height / 2) + width / 2,
            -sine * (x - width / 2) + cosine * (y - height / 2) + height / 2)
    x, y = x / width, y / height
    if turns == 1:
        x, y = y, 1 - x
    elif turns == 2:
        x, y = 1 - x, 1 - y
    elif turns == 3:
        x, y = 1 - y, x
    if value.get('flip'):
        x = 1 - x
    return float(np.clip(x, 0, 1)), float(np.clip(y, 0, 1))
