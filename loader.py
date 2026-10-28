"""输入适配层。

支持的输入
----------
* 相机 RAW（CR2/CR3/NEF/ARW/DNG/RAF/ORF/RW2/PEF/SRW…）— 需要 rawpy
* 16bit TIFF                          — 视为线性
* 8bit PNG / JPEG / TIFF              — 视为 sRGB 编码，自动解码

没有 rawpy 时不会崩，给出明确的处理建议（导出 16bit TIFF）。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

# 项目自带的 rawpy（装在 vendor/，不污染 DSH 运行时）
_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

import numpy as np
from PIL import Image

from tiffio import write_tiff_rgb16
from tiffread import read_tiff_linear

try:  # rawpy 可选依赖
    import rawpy  # type: ignore

    HAVE_RAWPY = True
except Exception:  # pragma: no cover
    rawpy = None  # type: ignore
    HAVE_RAWPY = False

RAW_EXTS = {
    ".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2",
    ".dng", ".raf", ".orf", ".rw2", ".pef", ".srw", ".raw", ".rwl",
    ".3fr", ".fff", ".erf", ".mef", ".mos", ".iiq", ".k25", ".kdc", ".dcr",
}

IMAGE_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp", ".webp"}

SUPPORTED_EXTS = RAW_EXTS | IMAGE_EXTS


@dataclass
class Loaded:
    linear: np.ndarray        # HxWx3 float32, 0..1, 线性
    source: str               # 原始路径
    width: int
    height: int
    is_raw: bool
    note: str = ""
    color: str = 'legacy'
    color_info: dict | None = None
    working_space: str = 'srgb'


def _decode_srgb8(arr: np.ndarray) -> np.ndarray:
    """8bit sRGB 编码值 → 线性。"""
    s = arr.astype(np.float32) / 255.0
    return np.where(
        s <= 0.04045,
        s / 12.92,
        np.power((s + 0.055) / 1.055, 2.4),
    ).astype(np.float32)


def _decode_linear16(arr: np.ndarray) -> np.ndarray:
    scale = 65535.0 if arr.dtype == np.uint16 else 1.0
    result=arr.astype(np.float32,copy=True)
    result/=scale
    np.clip(result,0,1,out=result)
    return result


def _to_3ch(arr: np.ndarray) -> np.ndarray:
    if arr.ndim == 2:
        return np.repeat(arr[..., None], 3, axis=2)
    if arr.shape[2] == 1:
        return np.repeat(arr, 3, axis=2)
    if arr.shape[2] == 4:  # RGBA / CMYK 兜底
        return arr[..., :3]
    return arr[..., :3]


def _load_raw(path: str,working_space='srgb') -> np.ndarray:
    if not HAVE_RAWPY:
        raise RuntimeError(
            "这个文件是相机 RAW，但当前环境没有 rawpy。\n"
            "两个办法：\n"
            "  1) 运行  python tools/install_rawpy.py  把 rawpy 装进 vendor/；\n"
            "  2) 用 Lightroom / 相机厂商软件导出 16bit TIFF，直接喂给本工具（推荐，更可控）。"
        )
    try:
        from file_io import io_path
        with rawpy.imread(str(io_path(path))) as raw:
            # 白平衡策略：优先用 libraw 给的 daylight 固定值。
            # 整卷必须用同一组增益，否则片基颜色一帧一变，去色罩就没法做了。
            # 绝不能用 [1,1,1,1]——未白平衡的原始通道比例会让红通道接近 0，
            # 量化精度直接丢掉一大半。
            wb = list(raw.daylight_whitebalance or [])
            if len(wb) >= 3 and min(wb[:3]) > 0:
                wb_kwargs = dict(use_camera_wb=False, user_wb=wb)
            else:
                wb_kwargs = dict(use_camera_wb=True)

            rgb = raw.postprocess(
                gamma=(1, 1),              # 不施加 gamma，保留线性
                no_auto_bright=True,       # 关键：别自动提亮，会毁掉密度关系
                output_bps=16,
                output_color=rawpy.ColorSpace.ProPhoto if working_space=='prophoto' else rawpy.ColorSpace.sRGB,
                demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
                **wb_kwargs,
            )
    except Exception as exc:
        # libraw 的异常 message 是 bytes，直接抛出去前端会看到 b'...'
        msg = exc.args[0] if exc.args else str(exc)
        if isinstance(msg, bytes):
            msg = msg.decode("utf-8", "ignore")
        raise RuntimeError(f"RAW 解码失败（{type(exc).__name__}）：{msg}") from None
    return _decode_linear16(rgb)


def load(path: str, input_color='legacy',working_space='srgb') -> Loaded:
    if working_space not in ('srgb','prophoto'):raise ValueError('未知工作空间')
    if input_color!='legacy' or working_space!='srgb':
        from input_color import managed_load
        return managed_load(path,input_color,working_space)
    ext = os.path.splitext(path)[1].lower()
    from file_io import io_path
    if not io_path(path).exists():
        raise FileNotFoundError(path)

    if ext in RAW_EXTS:
        try:
            lin = _load_raw(path)
        except RuntimeError:
            # Hasselblad 的 .fff / .3fr 很多其实是标准 TIFF 容器，
            # libraw 不认但可以直接按 TIFF 读。别因为 rawpy 摇头就放弃。
            if ext in (".fff", ".3fr", ".tif", ".tiff"):
                lin = read_tiff_linear(path)
            else:
                raise
        h, w = lin.shape[:2]
        return Loaded(lin, path, w, h, True, "RAW/扫描 TIFF 线性解码")

    if ext not in IMAGE_EXTS:
        raise ValueError(f"不支持的文件类型：{ext}")

    # TIFF 单独处理：16bit 必须走自研读取器，Pillow 会降成 8bit
    if ext in (".tif", ".tiff"):
        bits = 16
        try:
            with Image.open(io_path(path)) as probe:
                bps = probe.tag_v2.get(258, (8,))
                bits = int(bps[0]) if isinstance(bps, tuple) else int(bps)
        except Exception:
            pass
        if bits >= 16:
            lin = read_tiff_linear(path)
            note = "TIFF 16bit，按线性处理"
        else:
            with Image.open(io_path(path)) as im:
                arr = np.array(im.convert("RGB"))
            lin = _decode_srgb8(arr)
            note = "TIFF 8bit，按 sRGB 解码"
        lin = _to_3ch(lin).astype(np.float32,copy=False)
        h, w = lin.shape[:2]
        return Loaded(lin, path, w, h, False, note)

    with Image.open(io_path(path)) as im:
        im.load()
        mode = im.mode
        arr = np.array(im)

    note = ""
    if arr.ndim == 3:
        arr = _to_3ch(arr)

    if mode in ("I;16", "I;16B", "I;16L", "I", "F"):
        lin = _decode_linear16(np.asarray(arr, dtype=np.float32))
        note = "16bit TIFF，按线性处理"
    elif arr.dtype == np.uint16:
        lin = _decode_linear16(arr)
        note = "16bit TIFF，按线性处理"
    else:
        lin = _decode_srgb8(arr)
        note = "8bit 图像，按 sRGB 解码"

    lin = _to_3ch(lin).astype(np.float32,copy=False)
    h, w = lin.shape[:2]
    return Loaded(lin, path, w, h, False, note)


def save_tiff(path: str, srgb: np.ndarray) -> None:
    """把 sRGB 编码的 float (0..1) 存成 16bit TIFF。"""
    write_tiff_rgb16(path, srgb)


def save_png_rgb(path: str, srgb: np.ndarray) -> None:
    a = (np.clip(srgb, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    Image.fromarray(a).save(path)
