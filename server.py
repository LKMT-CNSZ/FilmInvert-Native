"""交互式胶片反相工作台 —— 零依赖 HTTP 服务。

启动：python server.py [--port 8765] [图片路径]
然后浏览器打开 http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
import copy
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import core  # noqa: E402
import colorio
import restoration
import strip  # noqa: E402
from core import Params  # noqa: E402
from loader import HAVE_RAWPY, load  # noqa: E402
from tiffio import write_tiff_rgb16  # noqa: E402

STATIC = os.path.join(HERE, "static")
PREVIEW_MAX = 1400
EXPORT_DIR = os.path.join(HERE, "out")
UPLOAD_DIR = os.path.join(HERE, "uploads")
MAX_UPLOAD = 4 * 1024 * 1024 * 1024   # 4 GB，整条扫描也就 1 GB 上下

LOCK = threading.Lock()
REQ_LOG: list[tuple[float, str, str]] = []
THUMB_CACHE: dict[int, bytes] = {}
BATCH = {"running": False, "done": 0, "total": 0, "directory": "", "error": ""}
VERSION = "1.2-pro"
FLAT_CACHE = {}


def _log_req(method: str, path: str) -> None:
    with LOCK:
        REQ_LOG.append((time.time(), method, path))
        if len(REQ_LOG) > 300:
            del REQ_LOG[:150]


STATE: dict = {
    "path": None,
    "full": None,        # 当前帧（HxWx3 float32 线性）
    "preview": None,     # 降采样后的线性
    "params": Params(),
    "info": {},
    "strip_img": None,     # 整条原图（整条模式下才有）
    "strip_layout": None,
    "frame_index": 0,
    "frame_params": {},
    "roll_params": None,
    "auto_meter": True,
}


# --------------------------------------------------------------------------
# 图像工具
# --------------------------------------------------------------------------
def downscale(lin: np.ndarray, max_side: int) -> np.ndarray:
    h, w = lin.shape[:2]
    scale = max_side / max(h, w)
    if scale >= 1.0:
        return lin
    nh, nw = max(1, int(h * scale)), max(1, int(w * scale))
    out = np.empty((nh, nw, 3), dtype=np.float32)
    for c in range(3):
        # Preserve Pillow's existing uint16/Lanczos preview exactly, while
        # bounding float quantization scratch for complete large-strip views.
        ch = np.empty((h,w), dtype=np.uint16)
        step=max(1,8*1024*1024//max(1,w*4))
        for y in range(0,h,step):
            block=np.clip(lin[y:y+step,:,c],0.0,1.0)
            block*=65535.0
            ch[y:y+len(block)]=block.astype(np.uint16)
        im = Image.fromarray(ch).resize((nw, nh), Image.LANCZOS)
        out[..., c] = np.asarray(im, dtype=np.float32) / 65535.0
    return out


def encode_preview(srgb: np.ndarray) -> bytes:
    """预览用 JPEG：比 PNG 小一个数量级，交互才跟得上手。"""
    a = (np.clip(srgb, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    if a.ndim == 2:
        a = np.repeat(a[..., None], 3, axis=2)
    buf = io.BytesIO()
    Image.fromarray(a).save(buf, format="JPEG", quality=90, subsampling=0)
    return buf.getvalue()


def params_from(d: dict) -> Params:
    if not isinstance(d, dict):
        raise ValueError("参数必须是 JSON 对象")
    p = Params()
    for k, v in (d or {}).items():
        if k in Params.__dataclass_fields__:
            setattr(p, k, v)
    return p.normalized()


# --------------------------------------------------------------------------
# 动作
# --------------------------------------------------------------------------
def do_open(path: str, input_mode='auto', manual=None) -> dict:
    return install_loaded(path,load(path),input_mode,manual)


def install_loaded(path, ld, input_mode='auto', manual=None, legacy_detection=False):
    """Install a cached decoded original or a newly loaded file."""
    if input_mode not in ('auto','single','strip'):raise ValueError('输入模式无效')
    if manual is not None and input_mode!='strip':raise ValueError('手动选框需要整条输入模式')
    lin = ld.linear
    manual=strip.normalize_manual(manual,lin.shape) if manual is not None else None
    if manual is not None:
        layout=strip.manual_layout(lin,manual)
    elif input_mode=='single':
        layout=None
    else:
        layout=(strip.detect_layout_legacy if legacy_detection else strip.detect_layout)(lin)
        if input_mode=='strip' and layout.frame_count==0:
            h,w=lin.shape[:2]
            layout=strip.StripLayout([(0,w)],[[]],[core.auto_base(lin)],[[(0,h)]],0)
    is_strip = layout is not None and (input_mode=='strip' or layout.frame_count>1 or layout.kind=='window')

    with LOCK:
        FLAT_CACHE.clear()
        STATE["frame_params"] = {}
        STATE["roll_params"] = None
        STATE["auto_meter"] = True
        STATE['native_loaded']=ld
        STATE['native_input']={'mode':input_mode,'manual':manual}
        if ld.color!='legacy':STATE['native_input']['color']=ld.color
        if ld.working_space!='srgb':STATE['native_input']['working_space']=ld.working_space

    if is_strip:
        si, fi, arr = strip.extract_one(lin, layout, 0)
        base = layout.bases[si]
        _, span = core.auto_anchor(arr, base)
        p = Params(mode="color_negative", base=base, anchor=None, span=span, slope=1.60)
        p.working_space=ld.working_space
        with LOCK:
            THUMB_CACHE.clear()
            STATE["strip_img"] = lin
            STATE["strip_layout"] = layout
            STATE["frame_index"] = 0
            STATE["path"] = path
            STATE["full"] = arr
            STATE["preview"] = downscale(arr, PREVIEW_MAX)
            STATE["params"] = p
            STATE["info"] = {
                "path": path,
                "name": os.path.basename(path),
                "width": arr.shape[1],
                "height": arr.shape[0],
                "is_raw": ld.is_raw,
                "note": ld.note,
                'input_color':ld.color_info,
                "has_rawpy": HAVE_RAWPY,
                "input_mode": input_mode,
                "base": base,
                "anchor": None,
                "span": span,
                "channel_health": core.detect_channel_health(downscale(arr, 900)),
                "strip": {
                    "frames": layout.frame_count,
                    "index": 0,
                    "strips": len(layout.strips),
                    "rotation": layout.rotation,
                    "source_width": ld.width,
                    "source_height": ld.height,
                    "manual": manual is not None,
                },
            }
        return STATE["info"]

    base = core.auto_base(lin)
    anchor, span = core.auto_anchor(lin, base)
    p = Params(mode="color_negative", base=base, anchor=anchor, span=span, slope=1.60)
    p.working_space=ld.working_space

    with LOCK:
        STATE["strip_img"] = None
        STATE["strip_layout"] = None
        STATE["frame_index"] = 0
        STATE["path"] = path
        STATE["full"] = lin
        THUMB_CACHE.clear()
        preview = downscale(lin, PREVIEW_MAX)
        STATE["preview"] = preview
        STATE["params"] = p
        health = core.detect_channel_health(preview)
        STATE["info"] = {
            "path": path,
            "name": os.path.basename(path),
            "width": ld.width,
            "height": ld.height,
            "is_raw": ld.is_raw,
            "note": ld.note,
            'input_color':ld.color_info,
            "has_rawpy": HAVE_RAWPY,
            "input_mode": input_mode,
            "base": base,
            "anchor": anchor,
            "span": span,
            "channel_health": health,
            "strip": None,
        }
    return STATE["info"]


def strip_field(img, layout, index):
    si = strip.frame_boxes(layout)[index][0]
    key = (id(img), si, layout.rotation)
    with LOCK:
        cached = FLAT_CACHE.get(key)
    if cached is not None:
        return cached
    x0,x1 = layout.strips[si]
    patches = [img[max(0,g-3):min(img.shape[0],g+4),x0:x1] for g in layout.gaps[si]]
    if not patches:
        raise ValueError('此胶片条未检测到可靠帧间隙，不能自动估计平场')
    samples = np.concatenate(patches,axis=0)
    profile = np.median(samples,axis=0)
    variation = np.median(np.abs(samples-profile[None,...]),axis=(0,1))
    if np.any(variation/np.maximum(np.median(profile,axis=0),1e-6) > .15):
        raise ValueError('帧间隙亮度不一致，无法可靠估计照明场')
    grid = np.asarray(restoration.reference_field(np.repeat(profile[None,...],8,axis=0)),np.float32)
    if layout.rotation:
        grid = np.rot90(grid,k=-(layout.rotation//90))
    result = (grid.tolist(), np.median(profile,axis=0).astype(np.float32).tolist())
    with LOCK:
        FLAT_CACHE[key] = result
    return result


def prepare_params(p, img=None, layout=None, index=None):
    p.working_space=STATE.get('native_input',{}).get('working_space','srgb')
    if img is None and layout is None:
        with LOCK:
            img,layout,index = STATE['strip_img'],STATE['strip_layout'],STATE['frame_index']
    if p.flat_mode == 'strip':
        if img is None or layout is None:
            raise ValueError('片基照明校正仅用于整条胶片；单张图请导入参考平场')
        p.flat_field = strip_field(img,layout,index)[0]
    elif p.flat_mode == 'reference' and layout is not None:
        raise ValueError('参考平场仅用于单张图；整条扫描请使用片基照明校正')
    return p


def corrected_input(arr,p):
    return restoration.correct_light(arr,p.flat_field if p.flat_mode != 'off' else None,
                                     p.flat_strength,p.vignette)


LAST_EXPORT=None


def export_pixels(path,arr,p):
    from tiffio import write_tiff_strips,strip_rows
    from runtime_metrics import memory
    global LAST_EXPORT
    started=time.perf_counter();before=memory()
    LAST_EXPORT={'status':'running','input_shape':list(arr.shape),'output_space':p.output_space}
    try:
        wide=core.process(arr,p,target_space=p.working_space)
        rows=strip_rows(wide.shape[1]);report={}
        write_tiff_strips(path,wide.shape[:2],colorio.export_strips(wide,p.output_space,p.working_space,report,rows),icc_profile=colorio.icc_profile(p.output_space),rows=rows)
        LAST_EXPORT.update(status='completed',output_shape=list(wide.shape),strip_rows=rows)
        return {'space':p.output_space,'stage':'post_geometry_pre_target_clip','tolerance':1e-6,
                'out_of_gamut_fraction':report['fraction'],'endpoint_fraction':report['endpoints']}
    except Exception:
        LAST_EXPORT['status']='failed';raise
    finally:LAST_EXPORT.update(seconds=time.perf_counter()-started,memory_before=before,memory_after=memory())


def do_wb(data):
    with LOCK:
        lin = STATE['preview']
    if lin is None:
        raise ValueError('请先载入图片')
    p = prepare_params(params_from(data.get('params',{})))
    p.wb_gain = core.estimate_wb(lin,p,data.get('method','gray_world'))
    p.temp = p.tint = 0.
    with LOCK:
        STATE['params'] = p
    return {'params':p.__dict__}


def do_flat_meter(data):
    p = prepare_params(params_from(data.get('params',{})))
    with LOCK:
        lin,img,layout,index = STATE['full'],STATE['strip_img'],STATE['strip_layout'],STATE['frame_index']
    if lin is None:
        raise ValueError('请先载入图片')
    corrected = corrected_input(lin,p)
    if p.flat_mode == 'strip':
        p.base = strip_field(img,layout,index)[1]
    elif layout is not None:
        p.base = layout.bases[strip.frame_boxes(layout)[index][0]]
    else:
        p.base = core.auto_base(corrected)
    p.anchor,p.span = core.auto_anchor(corrected,p.base)
    with LOCK:
        STATE['params'] = p
    return {'params':p.__dict__}


def do_flat_upload(rfile,length,name):
    with LOCK:
        shape = STATE['full'].shape if STATE['full'] is not None else None
        layout = STATE['strip_layout']
    if shape is None:
        raise ValueError('请先载入要校正的图片')
    if layout is not None:
        raise ValueError('整条扫描请使用片基照明校正，参考平场仅支持单张图')
    path = save_upload(rfile,length,'flat-'+uuid.uuid4().hex[:8]+'-'+name)
    reference = load(path).linear
    if reference.shape != shape:
        raise ValueError('参考平场需与当前原图尺寸完全相同，并使用相同拍摄方向')
    return {'field':restoration.reference_field(reference)}


def do_thumb(index: int) -> bytes:
    """整条模式：某一帧的小缩略图（联系表用），带缓存。"""
    with LOCK:
        cached = THUMB_CACHE.get(index)
        layout = STATE["strip_layout"]
        img = STATE["strip_img"]
        saved = copy.deepcopy(STATE['frame_params'].get(index))
        roll = copy.deepcopy(STATE['roll_params'])
        auto_meter = STATE['auto_meter']
    if cached is not None:
        return cached
    if layout is None or img is None:
        raise RuntimeError("当前不是整条模式")

    si, _fi, arr = strip.extract_one(img, layout, index)
    base = layout.bases[si]
    small = downscale(arr, 320)
    _, span = core.auto_anchor(small, base)
    p = params_from(saved or roll) if saved is not None or roll is not None else Params()
    if saved is None:
        p.base, p.anchor = base, None
        if auto_meter or roll is None:
            p.span = span
    p = prepare_params(p,img,layout,index)
    out = core.process(small, p)
    jpg = encode_preview(out)
    with LOCK:
        THUMB_CACHE[index] = jpg
    return jpg


def _safe_name(name: str) -> str:
    """把上传的文件名洗成安全的文件名（防路径穿越、非法字符）。"""
    name = urllib.parse.unquote(name or "upload")
    name = os.path.basename(name.replace("\\", "/"))
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        name = "upload"
    stem, ext = os.path.splitext(name)
    return f"{stem[:80]}{ext[:12]}"


def do_upload(rfile, length: int, name: str) -> dict:
    """把上传的二进制流式落盘，然后按普通路径载入。

    不整体读进内存——整条扫描动辄 800 MB，一次性 read() 会直接把进程撑爆。
    """
    return do_open(save_upload(rfile,length,name))


def save_upload(rfile, length: int, name: str) -> str:
    if length <= 0:
        raise ValueError("空文件")
    if length > MAX_UPLOAD:
        raise ValueError(f"文件太大（{length / 2**30:.1f} GB），上限 4 GB")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    dst = os.path.join(UPLOAD_DIR, _safe_name(name))

    remaining = length
    with open(dst, "wb") as f:
        while remaining > 0:
            chunk = rfile.read(min(1 << 20, remaining))
            if not chunk:
                break
            f.write(chunk)
            remaining -= len(chunk)
    if remaining > 0:
        raise IOError(f"上传中断，还差 {remaining} 字节")

    return dst


def do_frame(index: int, current: dict | None = None, keep_params: bool = True,
             auto_meter: bool = True) -> dict:
    """整条模式：切到第 index 帧。"""
    with LOCK:
        img = STATE["strip_img"]
        layout = STATE["strip_layout"]
        previous = params_from(current) if current is not None else STATE["params"]
        source_index = STATE["frame_index"]
        saved = copy.deepcopy(STATE["frame_params"].get(index))
        roll = copy.deepcopy(STATE["roll_params"])
    if layout is None or img is None:
        raise ValueError("当前不是整条模式，没有可切换的帧")

    si, fi, arr = strip.extract_one(img, layout, index)
    base = layout.bases[si]
    _, span = core.auto_anchor(arr, base)
    if keep_params:
        p = params_from(previous.__dict__)
    elif saved is not None:
        p = params_from(saved)
    elif roll is not None:
        p = params_from(roll)
    else:
        p = Params(span=span)
    p.local_edits=copy.deepcopy((saved or {}).get('local_edits',[]))
    if not keep_params and saved is not None:
        base = p.base or base
    else:
        p.base, p.anchor = list(base), None
        if auto_meter:
            p.span = span
    p = prepare_params(p,img,layout,index)
    if not (not keep_params and saved is not None):
        if p.flat_mode == 'strip':
            p.base = strip_field(img,layout,index)[1]
        if auto_meter and (p.flat_mode != 'off' or p.vignette > 0):
            _,p.span = core.auto_anchor(corrected_input(arr,p),p.base)

    with LOCK:
        STATE["frame_index"] = index
        STATE["frame_params"][source_index] = copy.deepcopy(previous.__dict__)
        STATE["auto_meter"] = bool(auto_meter)
        STATE["full"] = arr
        STATE["preview"] = downscale(arr, PREVIEW_MAX)
        STATE["params"] = p
        STATE["info"].update({
            "width": arr.shape[1],
            "height": arr.shape[0],
            "base": base,
            "span": span,
            "channel_health": core.detect_channel_health(STATE["preview"]),
        })
        STATE["info"]["strip"]["index"] = index
    return {"index": index, "base": base, "span": p.span, "params": p.__dict__,
            "width": arr.shape[1], "height": arr.shape[0],
            "channel_health": STATE["info"]["channel_health"]}


def do_sample(x: float, y: float, kind: str, current: dict | None = None) -> dict:
    with LOCK:
        lin = STATE["full"]
        p = params_from(current) if current is not None else params_from(STATE['params'].__dict__)
    if lin is None:
        raise RuntimeError("还没有打开图片")
    p = prepare_params(p)
    corrected = corrected_input(lin,p)
    val = core.sample_patch(corrected, x, y, radius=10)
    if kind == "base":
        # Inspect the decoded input, before optional flat-field correction can
        # legitimately produce transmission values above one. Reject atomically.
        decoded=core.sample_patch(lin,x,y,radius=10)
        if core.base_clipped_channels(decoded):
            raise ValueError('片基取样已到解码端点（通道剪裁），未应用；请换一处未剪裁的未曝光片基。RAW 可尝试 ProPhoto 工作空间后重新检查。')
        p.base = val
        _, span = core.auto_anchor(corrected, val)
        p.span = span
    elif kind == "anchor":
        p.anchor = val
        db = core.to_density(np.asarray(p.base or [0.5, 0.5, 0.5], dtype=np.float32))
        d = core.to_density(np.asarray(val, dtype=np.float32))
        p.span = float(np.clip(np.median(d - db), 0.3, 4.0))
    elif kind == "gray":
        if p.mode == 'bw_negative':
            raise ValueError('黑白模式不需要白平衡')
        probe = params_from(p.__dict__); probe.flip = False
        # 使用同一反相管线的场景亮度，避免在负片透射率上猜色温方向。
        pixel = np.asarray(core.sample_patch(core.scene_linear(lin,probe),x,y,10),np.float32)
        if np.any(pixel <= 1e-6):
            raise ValueError('此取样点无有效信号，请换一个中性灰区域')
        gain = pixel.mean()/pixel
        gain /= core.luma(gain,p.working_space)
        if gain.min() < .125 or gain.max() > 8:
            raise ValueError('所需白平衡超出范围，请先检查片基')
        p.wb_gain = gain.tolist(); p.temp = p.tint = 0.
    else:
        raise ValueError('未知取样类型')
    with LOCK:
        STATE["params"] = p
    return {"sampled": [round(v, 5) for v in val], "params": p.__dict__,
            "base": p.base, "span": p.span}


def do_preview(params: dict) -> bytes:
    with LOCK:
        lin = STATE["preview"]
    if lin is None:
        raise RuntimeError("还没有打开图片")
    p = prepare_params(params_from(params))
    with LOCK:
        STATE["params"] = p
        STATE["frame_params"][STATE["frame_index"]] = copy.deepcopy(p.__dict__)
    out = core.process(lin, p)
    return encode_preview(out)


def do_export(params: dict) -> dict:
    with LOCK:
        lin = STATE["full"]
        src = STATE["path"]
        is_strip = STATE["strip_layout"] is not None
        frame_no = STATE["frame_index"] + 1
    if lin is None:
        raise RuntimeError("还没有打开图片")
    p = prepare_params(params_from(params))
    os.makedirs(EXPORT_DIR, exist_ok=True)
    stem = os.path.splitext(os.path.basename(src or "export"))[0]
    # 整条模式下必须带帧号，否则 12 帧会互相覆盖
    suffix = f"-f{frame_no:02d}" if is_strip else ""
    dst = os.path.join(EXPORT_DIR, f"{stem}{suffix}-positive-{uuid.uuid4().hex[:8]}.tif")
    export_pixels(dst,lin,p)
    write_recipe(dst + ".json", p, src, frame_no)
    with LOCK:
        STATE["params"] = p
    return {"path": dst, "recipe": dst + ".json"}


def write_recipe(path: str, p: Params, source: str, frame: int) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({"format": "film-invert-recipe", "version": 1,
                   "application": VERSION, "source": source, "frame": frame,
                   "params": p.__dict__}, f, ensure_ascii=False, indent=2, allow_nan=False)


def do_apply_all(data: dict) -> dict:
    p = params_from(data.get('params', {}))
    with LOCK:
        if STATE['strip_layout'] is None:
            raise ValueError('请先打开整条胶片')
        locals_by_frame={i:copy.deepcopy(v.get('local_edits',[])) for i,v in STATE['frame_params'].items()}
        locals_by_frame[STATE['frame_index']]=copy.deepcopy(p.local_edits)
        STATE['roll_params'] = copy.deepcopy(p.__dict__);STATE['roll_params']['local_edits']=[]
        STATE['frame_params'].clear()
        for i,edits in locals_by_frame.items():
            if not edits:continue
            si,_,arr=strip.extract_one(STATE['strip_img'],STATE['strip_layout'],i)
            fp=copy.deepcopy(p);fp.local_edits=edits;fp.base,fp.anchor=list(STATE['strip_layout'].bases[si]),None
            if data.get('auto_meter',True):_,fp.span=core.auto_anchor(arr,fp.base)
            fp=prepare_params(fp,STATE['strip_img'],STATE['strip_layout'],i)
            if fp.flat_mode=='strip':fp.base=strip_field(STATE['strip_img'],STATE['strip_layout'],i)[1]
            if data.get('auto_meter',True) and (fp.flat_mode!='off' or fp.vignette>0):_,fp.span=core.auto_anchor(corrected_input(arr,fp),fp.base)
            STATE['frame_params'][i]=copy.deepcopy(fp.__dict__)
        STATE['params'] = p
        STATE['auto_meter'] = bool(data.get('auto_meter', True))
        THUMB_CACHE.clear()
        count = STATE['strip_layout'].frame_count
    return {'frames': count}


def do_batch(data: dict) -> dict:
    """快照后逐帧导出，后台线程仅持有一帧成品，保持界面可用。"""
    p = params_from(data.get('params', {}))
    with LOCK:
        if BATCH['running']:
            raise ValueError('已有批量任务正在导出')
        layout, img, src = STATE['strip_layout'], STATE['strip_img'], STATE['path']
        if layout is None or img is None:
            raise ValueError('请先打开整条胶片')
        saved = copy.deepcopy(STATE['frame_params'])
        saved[STATE['frame_index']] = copy.deepcopy(p.__dict__)
        roll = copy.deepcopy(STATE['roll_params'])
        auto_meter = bool(data.get('auto_meter', True))
        export_space = p.output_space
        stem = os.path.splitext(os.path.basename(src))[0]
        directory = os.path.join(EXPORT_DIR, f'{stem}-roll-{uuid.uuid4().hex[:8]}')
        BATCH.update(running=True, done=0, total=layout.frame_count,
                     directory=directory, error='')

    def work():
        try:
            os.makedirs(directory, exist_ok=False)
            manifest = []
            for index in range(layout.frame_count):
                si, _, arr = strip.extract_one(img, layout, index)
                if index in saved:
                    fp = params_from(saved[index])
                else:
                    fp = params_from(roll) if roll is not None else Params()
                    fp.base, fp.anchor = layout.bases[si], None
                    if auto_meter or roll is None:
                        _, fp.span = core.auto_anchor(arr, fp.base)
                dst = os.path.join(directory, f'{stem}-f{index+1:02d}-positive.tif')
                fp = prepare_params(fp,img,layout,index)
                if index not in saved and fp.flat_mode == 'strip':
                    fp.base = strip_field(img,layout,index)[1]
                    if auto_meter:
                        _,fp.span = core.auto_anchor(corrected_input(arr,fp),fp.base)
                fp.output_space = export_space
                export_pixels(dst,arr,fp)
                write_recipe(dst + '.json', fp, src, index+1)
                manifest.append({'frame': index+1, 'file': os.path.basename(dst),
                                 'params': fp.__dict__})
                with LOCK:
                    BATCH['done'] = index+1
            with open(os.path.join(directory, 'manifest.json'), 'w', encoding='utf-8') as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2, allow_nan=False)
        except Exception as e:
            traceback.print_exc()
            with LOCK:
                BATCH['error'] = str(e)
        finally:
            with LOCK:
                BATCH['running'] = False

    threading.Thread(target=work, daemon=True).start()
    return dict(BATCH)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "FilmInvert/1.2"

    def log_message(self, fmt: str, *args) -> None:  # 关掉刷屏日志
        pass

    # --- helpers ---
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        if n > 1024*1024:
            raise ValueError('JSON 请求不能超过 1 MB')
        try:
            value = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeError) as e:
            raise ValueError('无效的 JSON 请求') from e
        if not isinstance(value, dict):
            raise ValueError('请求必须是 JSON 对象')
        return value

    # --- routes ---
    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        _log_req("GET", path)
        try:
            if path in ("/", "/index.html"):
                self._serve_static("index.html")
            elif path.startswith("/static/"):
                self._serve_static(path[len("/static/"):])
            elif path == "/api/state":
                with LOCK:
                    self._json({**STATE["info"], "params": STATE['params'].__dict__})
            elif path == '/api/batch':
                with LOCK:
                    self._json(dict(BATCH))
            elif path == "/api/thumb":
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                idx = int(qs.get("i", ["0"])[0])
                self._send(200, do_thumb(idx), "image/jpeg")
            elif path == "/api/films":
                from films import FILMS

                self._json([
                    {"key": k, "name": v["name"], "type": v["type"]}
                    for k, v in FILMS.items()
                ])
            elif path == "/api/debug":
                with LOCK:
                    log = [{"t": round(t, 3), "m": m, "p": p} for t, m, p in REQ_LOG[-40:]]
                self._json({"now": round(time.time(), 3), "log": log})
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")
        except (ValueError, TypeError, KeyError, IndexError) as e:
            self._json({'error': str(e)}, 400)
        except Exception:
            traceback.print_exc()
            self._json({"error": "内部错误"}, 500)

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        _log_req("POST", path)
        try:
            if path == "/api/upload":
                # 注意：这里不能走 _body()，那是整读进内存的，大文件会爆
                n = int(self.headers.get("Content-Length") or 0)
                name = self.headers.get("X-Filename", "upload")
                self._json(do_upload(self.rfile, n, name))
            elif path == '/api/flat-upload':
                n = int(self.headers.get('Content-Length') or 0)
                self._json(do_flat_upload(self.rfile,n,self.headers.get('X-Filename','flat.tif')))
            elif path == '/api/white-balance':
                self._json(do_wb(self._body()))
            elif path == '/api/flat-meter':
                self._json(do_flat_meter(self._body()))
            elif path == "/api/open":
                info = do_open(self._body().get("path", ""))
                self._json(info)
            elif path == "/api/sample":
                d = self._body()
                self._json(do_sample(float(d["x"]), float(d["y"]), d.get("kind", "base"), d.get('params')))
            elif path == '/api/normalize':
                self._json(params_from(self._body()).__dict__)
            elif path == "/api/preview":
                jpg = do_preview(self._body())
                self._send(200, jpg, "image/jpeg")
            elif path == "/api/frame":
                d = self._body()
                self._json(do_frame(int(d["index"]), d.get('params'),
                                    d.get('keep_params', True), d.get('auto_meter', True)))
            elif path == '/api/apply-all':
                self._json(do_apply_all(self._body()))
            elif path == '/api/batch':
                self._json(do_batch(self._body()))
            elif path == "/api/export":
                self._json(do_export(self._body()))
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")
        except FileNotFoundError as e:
            self._json({"error": f"找不到文件：{e}"}, 400)
        except (ValueError, TypeError, KeyError, IndexError) as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:
            traceback.print_exc()
            self._json({"error": str(e)}, 500)

    def _serve_static(self, name: str) -> None:
        safe = os.path.normpath(name).replace("\\", "/")
        if safe.startswith("..") or os.path.isabs(safe):
            self._send(403, b"forbidden", "text/plain")
            return
        fp = os.path.join(STATIC, safe)
        if not os.path.isfile(fp):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(os.path.splitext(fp)[1].lower(), "application/octet-stream")
        with open(fp, "rb") as fh:
            self._send(200, fh.read(), ctype)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    ap.add_argument("image", nargs="?")
    a = ap.parse_args()

    if a.image:
        try:
            info = do_open(os.path.abspath(a.image))
            print(f"已载入 {info['name']}  {info['width']}x{info['height']}")
        except Exception as e:
            print(f"载入失败: {e}")

    # Windows 上 allow_reuse_address 会让两个进程同时 bind 同一端口，
    # 结果后起的那个永远收不到请求。这里显式关掉。
    ThreadingHTTPServer.allow_reuse_address = False
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    url = f"http://{a.host}:{a.port}"
    print(f"film-invert 已启动: {url}")
    if a.open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    if not HAVE_RAWPY:
        print("提示：未检测到 rawpy，RAW 文件需要先导出 16bit TIFF（或 pip install rawpy）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
