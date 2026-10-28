"""整条胶片的布局检测与切帧。

扫描仪一次扫一整条 135 胶片，画面里通常并排 1~2 条，每条 6 帧左右。
这个模块负责：找出胶片条 → 找出帧间隙 → 切成单帧 → 给出旋转方向。

判据
----
* **列方向**：胶片条有橙色片基，比条间黑隔亮得多 → 用列均值找黑隔
* **行方向**：帧间隙是未曝光的片基，是整条里最亮的地方 → 用行均值的峰值
* **片基**：直接取帧间隙的颜色，这是物理上真正的未曝光区，比自动估计准
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re

import numpy as np


def normalize_manual(value, shape=None):
    """Validate source-pixel edge boxes; never silently clamp a damaged project."""
    if not isinstance(value,dict) or value.get('rotation') not in (0,90,180,270):
        raise ValueError('手动切帧方向无效')
    rows=value.get('frames')
    if not isinstance(rows,list) or not 1<=len(rows)<=500:
        raise ValueError('手动切帧需要 1–500 个选框')
    result=[];seen=set()
    for row in rows:
        if not isinstance(row,dict):raise ValueError('手动帧记录无效')
        identifier=row.get('id');box=row.get('box')
        if not isinstance(identifier,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',identifier) or identifier in seen:
            raise ValueError('手动帧身份无效或重复')
        if not isinstance(box,(list,tuple)) or len(box)!=4 or any(type(v)!=int for v in box):
            raise ValueError('帧边界必须是整数像素')
        x0,y0,x1,y1=box
        if min(x0,y0)<0 or x1-x0<2 or y1-y0<2 or max(x1,y1)>1000000:
            raise ValueError('帧边界无效，每边至少需要 2 像素')
        if shape is not None and (x1>shape[1] or y1>shape[0]):
            raise ValueError('帧边界超出原图范围')
        normalized={'id':identifier,'box':list(box)}
        if 'angle' in row:
            if isinstance(row['angle'],bool) or not isinstance(row['angle'],(int,float)) or not math.isfinite(row['angle']) or abs(row['angle'])>15:
                raise ValueError('输入片框校直角度必须在 ±15° 内')
            if row['angle']:normalized['angle']=float(row['angle'])
        if 'base' in row:
            base=row['base']
            if not isinstance(base,list) or len(base)!=3 or any(not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1 for v in base):
                raise ValueError('输入片框片基无效')
            normalized['base']=[float(v) for v in base]
        seen.add(identifier);result.append(normalized)
    return {'rotation':int(value['rotation']),'frames':result}


def manual_layout(img, value):
    """Each manual ROI is an independent band; no inferred gap flat-field."""
    import core
    value=normalize_manual(value,img.shape)
    strips=[];bounds=[];bases=[]
    for row in value['frames']:
        x0,y0,x1,y1=row['box']
        strips.append((x0,x1));bounds.append([(y0,y1)])
        bases.append(row.get('base') or core.auto_base(img[y0:y1,x0:x1]))
    layout=StripLayout(strips,[[] for _ in strips],bases,bounds,value['rotation'])
    layout.angles=[row.get('angle',0.) for row in value['frames']]
    return layout


@dataclass
class StripLayout:
    strips: list[tuple[int, int]]      # 每条胶片的 (x0, x1)
    gaps: list[list[int]]              # 每条胶片各自的帧间隙中心 y
    bases: list[list[float]]           # 每条胶片的片基颜色（线性 0..1）
    bounds: list[list[tuple[int, int]]]  # 每条胶片内各帧的 y 范围
    rotation: int = 0                  # 0 / 90 / 180 / 270
    kind: str = 'strip'
    angles: list | None = None

    @property
    def frame_count(self) -> int:
        return sum(len(b) for b in self.bounds)


def _runs(mask: np.ndarray, min_len: int = 1) -> list[tuple[int, int]]:
    """连续的 True 段。"""
    idx = np.where(mask)[0]
    if len(idx) == 0:
        return []
    splits = np.split(idx, np.where(np.diff(idx) > 1)[0] + 1)
    return [(int(s[0]), int(s[-1])) for s in splits if len(s) >= min_len]


def detect_layout_legacy(
    img: np.ndarray,
    min_strip_width: int = 200,
    min_gap: int = 20,
    pad: int = 45,
    min_frame_height: int = 200,
) -> StripLayout:
    """img: HxWx3 线性 float。返回布局信息。"""
    h, w = img.shape[:2]

    # ---- 1. 列方向找胶片条（条间是黑隔）----
    step = max(1, w // 1200)
    col = img[::step].mean(axis=(0, 2))
    lo, hi = float(col.min()), float(col.max())
    thr = lo + (hi - lo) * 0.25
    dark = col < thr
    # 暗段（条间黑隔）可能只有几像素宽，不能拿 min_strip_width 去过滤
    dark_runs = _runs(dark, min_len=3)

    # 暗段之间就是胶片条
    edges = [(-1, -1)] + dark_runs + [(w, w)]
    strips = []
    for i in range(len(edges) - 1):
        x0 = edges[i][1] + 1
        x1 = edges[i + 1][0] - 1
        if x1 - x0 >= min_strip_width:
            strips.append((int(x0), int(x1)))
    if not strips:  # 没找到黑隔 → 整张就是一条
        strips = [(0, w)]

    # ---- 2. 每条找帧间隙（片基最亮）----
    gaps_list, bases, bounds = [], [], []
    for x0, x1 in strips:
        band = img[:, x0:x1]
        row = band.mean(axis=(1, 2))
        peak = float(np.quantile(row, 0.995))
        bright = row > peak * 0.8
        gaps = [int((a + b) / 2) for a, b in _runs(bright, min_len=min_gap)]

        # 片基：取每个间隙中心附近的像素中值
        samples = []
        for g in gaps:
            y0, y1 = max(0, g - 25), min(h, g + 25)
            samples.append(band[y0:y1].reshape(-1, 3))
        if samples:
            base = np.median(np.concatenate(samples, axis=0), axis=0)
        else:  # 没有间隙可参考，退回最亮分位
            flat = band.reshape(-1, 3)
            lum = flat @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
            base = np.median(flat[lum >= np.quantile(lum, 0.995)], axis=0)
        bases.append([float(v) for v in base])

        # ---- 3. 帧的 y 范围 ----
        content = _runs(row > row.mean() * 0.4, min_len=min_gap)
        top = content[0][0] if content else 0
        bottom = content[-1][1] if content else h
        marks = [top] + [g for g in gaps if top < g < bottom] + [bottom]
        fb = []
        for i in range(len(marks) - 1):
            y0, y1 = marks[i] + pad, marks[i + 1] - pad
            if y1 - y0 > min_frame_height:
                fb.append((int(y0), int(y1)))
        bounds.append(fb)
        gaps_list.append(gaps)

    # ---- 4. 旋转方向：135 帧是 3:2，若切出来是竖的就转 ----
    rot = 0
    if bounds and bounds[0]:
        x0, x1 = strips[0]
        y0, y1 = bounds[0][0]
        aspect = (y1 - y0) / max(1, (x1 - x0))
        if aspect > 1.0:
            rot = 90

    return StripLayout(strips, gaps_list, bases, bounds, rot)


def _window_box(small):
    """A large picture between two continuous, clear-film side rails.

    Work in transmission, normalizing each channel for the orange mask. Rails
    must span most of the source height; an isolated bright subject is not a
    rail. A narrow partial neighbour cannot become another complete frame.
    """
    h,w=small.shape[:2]
    base=np.maximum(np.quantile(small.reshape(-1,3),.98,axis=0),1e-6)
    transmission=np.min(small/base,axis=2)
    middle=transmission[round(h*.05):max(round(h*.95),round(h*.05)+1)]
    rails=_runs(np.mean(middle>.65,axis=0)>.80,max(2,round(w*.01)))
    candidates=[]
    for left,right in zip(rails,rails[1:]):
        x0,x1=left[1]+1,right[0]
        width=x1-x0
        if width<.45*w or not .50<h/max(width,1)<1.85:continue
        # A border alone is also common in flat-field/color fixtures and complete
        # scans. Trim automatically only when an exterior holder is present.
        dark_columns=np.mean(transmission<.14,axis=0)>.75
        exterior=int(dark_columns[:left[0]].sum()+dark_columns[right[1]+1:].sum())
        if exterior<max(2,round(w*.04)):continue
        inside=transmission[:,x0:x1]
        if np.mean(inside[round(h*.1):round(h*.9)]>.65)>.55:continue
        # At least one transverse border supports the parallel-rail hypothesis.
        profile=np.mean(inside>.50,axis=1)
        edge=max(2,round(h*.07))
        if max(float(profile[:edge].max()),float(profile[-edge:].max()))<.35:continue
        y0,y1=round(h*.0075),round(h*.9925)
        borders=_runs(profile>.60,1)
        top=[r for r in borders if r[0]<edge and r[1]<edge]
        bottom=[r for r in borders if r[0]>h-edge and r[1]-r[0]<edge]
        if top:y0=max(y0,top[-1][1]+1)
        if bottom:y1=min(y1,bottom[0][0])
        inset=max(1,round(w*.0015))
        box=(x0+inset,y0,x1-inset,y1)
        if box[2]>box[0] and box[3]>box[1]:
            rail_pixels=np.concatenate([small[:,left[0]:left[1]+1].reshape(-1,3),
                                        small[:,right[0]:right[1]+1].reshape(-1,3)])
            candidates.append((width*(y1-y0),box,np.median(rail_pixels,axis=0).tolist()))
    if not candidates:return None
    candidates.sort(key=lambda v:v[0],reverse=True)
    # Two comparable windows are ambiguous here; leave them to strip detection.
    if len(candidates)>1 and candidates[1][0]>.65*candidates[0][0]:return None
    return candidates[0][1:]


def _credible_strip(img, layout):
    """Require actual clear, transverse gaps rather than peaks in mean luma."""
    if layout.frame_count<2:return False
    gap_evidence=0
    h,w=img.shape[:2]
    lum=img.mean(axis=2);ceiling=float(np.quantile(lum,.99))
    if ceiling<=1e-6:return False
    # A dark subject must not divide a film strip into unrelated columns.
    for (_,end),(start,_) in zip(layout.strips,layout.strips[1:]):
        if start>end and np.mean(np.quantile(lum[:,end:start],.90,axis=0)<ceiling*.14)<.70:
            return False
    for (x0,x1),gaps,base,frames in zip(layout.strips,layout.gaps,layout.bases,layout.bounds):
        if not frames:return False
        internal=[g for g in gaps if frames[0][0]<g<frames[-1][1]]
        gap_evidence+=len(internal)
        if len(frames)>1 and len(internal)<len(frames)-1:return False
        reference=np.maximum(np.quantile(img[:,x0:x1].reshape(-1,3),.995,axis=0),1e-6)
        row_clear=np.mean(np.min(img[:,x0:x1]/reference,axis=2)>.80,axis=1)>.85
        for g in internal:
            runs=[(a,b) for a,b in _runs(row_clear) if a<=g<=b]
            pitch=float(np.median([b-a for a,b in frames]))
            if not runs or runs[0][1]-runs[0][0]+1>max(3,pitch*.15):return False
            band=img[max(0,g-1):min(h,g+2),x0:x1]
            clear=np.min(band/reference,axis=2)>.80
            if np.mean(clear)<.85:return False
    return gap_evidence>0


def detect_layout(img, min_strip_width=200, min_gap=20, pad=45):
    """Conservative detection with bounded analysis memory and source-pixel boxes.

    Single camera reproductions can contain clear film and partial neighbours;
    these are not automatically a multi-frame strip. With insufficient physical
    border/gap evidence, retain the complete source for manual framing.
    """
    h,w=img.shape[:2]
    step=max(1,int(np.ceil(max(h,w)/1200)))
    small=img[::step,::step]
    if min(small.shape[:2])>=24:
        for transpose in (False,True):
            view=small.transpose(1,0,2) if transpose else small
            window=_window_box(view)
            if window:
                (a,b,c,d),base=window
                x0,y0,x1,y1=(b,a,d,c) if transpose else (a,b,c,d)
                x0,y0,x1,y1=x0*step,y0*step,min(w,x1*step),min(h,y1*step)
                return StripLayout([(x0,x1)],[[]],[base],[[(y0,y1)]],0,'window')
    # Keep established strip coordinates where their clear gaps are supported.
    candidate=detect_layout_legacy(small,max(2,round(min_strip_width/step)),
                                    max(1,round(min_gap/step)),max(0,round(pad/step)),max(2,round(200/step)))
    if _credible_strip(small,candidate):
        result=StripLayout([(a*step,min(w,b*step)) for a,b in candidate.strips],
            [[v*step for v in row] for row in candidate.gaps],candidate.bases,
            [[(a*step,min(h,b*step)) for a,b in row] for row in candidate.bounds],candidate.rotation)
        result.bases=clear_gap_bases(img,result)
        return result
    import core
    return StripLayout([(0,w)],[[]],[core.auto_base(small)],[[(0,h)]],0,'single')


def clear_gap_bases(img,layout):
    """Bounded full-resolution samples contained in verified clear gaps.

    Detection may run at 1/20 scale. A fixed analysis-pixel radius would mix
    neighbouring image content into the base. Find the actual clear run near
    each proposed center, then sample only its interior in source pixels.
    Legacy detection stays unchanged for restoration of recorded old layouts.
    """
    result=[];h=img.shape[0]
    for si,((x0,x1),gaps) in enumerate(zip(layout.strips,layout.gaps)):
        width=x1-x0;left=x0+round(width*.15);right=x1-round(width*.15)
        xs=np.linspace(left,max(left,right-1),min(96,max(1,right-left))).astype(int)
        probe=img[::max(1,h//1200),xs]
        reference=np.maximum(np.quantile(probe.reshape(-1,3),.995,axis=0),1e-6)
        patches=[]
        for center in gaps:
            # Search a local source interval; bounded even for very tall scans.
            radius=min(512,max(24,h//100));top=max(0,center-radius);bottom=min(h,center+radius+1)
            band=img[top:bottom,xs]
            clear=np.mean(np.min(band/reference,axis=2)>.80,axis=1)>.85
            runs=[(a,b) for a,b in _runs(clear,2) if a<=center-top<=b]
            if not runs:continue
            a,b=runs[0];cy=top+(a+b)//2
            half=min(12,max(0,(b-a)//4))
            sample=img[cy-half:cy+half+1,xs].reshape(-1,3)
            patches.append(sample)
        result.append(np.median(np.concatenate(patches),axis=0).astype(float).tolist() if patches else list(layout.bases[si]))
    return result


def split_frames(img: np.ndarray, layout: StripLayout, rotate: int | None = None):
    """按布局切帧，返回 [(strip_idx, frame_idx, 数组)]，已按 rotate 旋转。"""
    rot = layout.rotation if rotate is None else rotate
    out = [];index=0
    for si, ((x0, x1), frames) in enumerate(zip(layout.strips, layout.bounds)):
        for fi, (y0, y1) in enumerate(frames):
            arr = np.ascontiguousarray(img[y0:y1, x0:x1])
            if frame_angle(layout,index):
                import geometry
                arr=geometry.apply(arr,{'straighten':frame_angle(layout,index)})
            index+=1
            if rot:
                arr = np.rot90(arr, k=-(rot // 90))  # 正数 = 顺时针
            out.append((si, fi, arr))
    return out


def frame_boxes(layout: StripLayout) -> list[tuple[int, int, int, int, int, int]]:
    """展平成 [(strip_idx, frame_idx, x0, x1, y0, y1)]。"""
    boxes = []
    for si, ((x0, x1), frames) in enumerate(zip(layout.strips, layout.bounds)):
        for fi, (y0, y1) in enumerate(frames):
            boxes.append((si, fi, x0, x1, y0, y1))
    return boxes


def extract_one(img: np.ndarray, layout: StripLayout, index: int):
    """只切第 index 帧（不复制整个列表），返回 (strip_idx, frame_idx, 已旋转数组)。"""
    boxes = frame_boxes(layout)
    if not 0 <= index < len(boxes):
        raise IndexError(f"帧号越界：{index} / {len(boxes)}")
    si, fi, x0, x1, y0, y1 = boxes[index]
    arr = np.ascontiguousarray(img[y0:y1, x0:x1])
    angle=frame_angle(layout,index)
    if angle:
        import geometry
        arr=geometry.apply(arr,{'straighten':angle})
    if layout.rotation:
        arr = np.rot90(arr, k=-(layout.rotation // 90))
    return si, fi, arr


def frame_angle(layout,index):
    return float(layout.angles[index]) if layout.angles else 0.
