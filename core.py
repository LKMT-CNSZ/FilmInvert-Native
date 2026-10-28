"""胶片密度域反相核心。

设计前提
--------
翻拍时，相机传感器的线性输出值正比于底片的**透射率** T。
胶片记录的不是亮度，是光密度 D = -log10(T)。

所以正确的工作域是密度域，而不是 sRGB：

    T      = 线性 RGB（已归一化到 0..1）
    D      = -log10(T)                     片基处 D 最小，最深黑处 D 最大
    Drel   = D - D_base                    逐通道减去片基（= 去色罩）
    norm   = (Drel - d_min) / span         0 = 片基, 1 = 最深黑
    E      = 10 ** (-(1 - norm) * span * slope)   场景线性亮度，负片反相
    out    = srgb_encode(E)

`slope` = 1 / 胶片 gamma，是唯一真正决定"反差对不对"的参数。
负片 gamma 约 0.55~0.65 → slope 约 1.5~1.8。

所有函数都只吃 numpy，无第三方图像库依赖。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
import geometry

import numpy as np

from films import FILMS
import restoration
import calibration

EPS = 1e-6

MODES = ("color_negative", "bw_negative", "positive")

# --------------------------------------------------------------------------
# 染料串色校正
# --------------------------------------------------------------------------
# 胶片的青/品红/黄三层染料吸收谱并不正交，所以「去色罩后逐通道独立处理」
# 只是近似。严格做法是在密度域乘一个 3×3 矩阵：
#
#     D_ideal = C · D_measured
#
# 这里的预设是经验值（按典型 C-41 染料的吸收峰重叠估的），每行归一化到 1，
# 保证中性色不会被推色。真正准的系数必须用透射色卡标定，
# 见 tools/calibrate_matrix.py。
CROSSTALK_FULL = (
    (1.08, -0.08, 0.00),
    (-0.05, 1.11, -0.06),
    (0.00, -0.10, 1.10),
)


def crosstalk_matrix(strength: float) -> np.ndarray:
    """按强度在单位矩阵和预设之间插值。strength=0 就是不做校正。"""
    s = float(np.clip(strength, 0.0, 1.0))
    full = np.asarray(CROSSTALK_FULL, dtype=np.float32)
    return np.eye(3, dtype=np.float32) + s * (full - np.eye(3, dtype=np.float32))


# --------------------------------------------------------------------------
# 色彩空间小工具
# --------------------------------------------------------------------------
def srgb_encode(lin: np.ndarray) -> np.ndarray:
    lin = np.clip(lin, 0.0, 1.0)
    return np.where(
        lin <= 0.0031308,
        lin * 12.92,
        1.055 * np.power(lin, 1.0 / 2.4) - 0.055,
    )


def srgb_decode(s: np.ndarray) -> np.ndarray:
    s = np.clip(s, 0.0, 1.0)
    return np.where(
        s <= 0.04045,
        s / 12.92,
        np.power((s + 0.055) / 1.055, 2.4),
    )


def to_density(t: np.ndarray, eps: float = EPS) -> np.ndarray:
    """透射率 → 光密度。夹紧下限防止 log(0)。

    上限 3.5 是刻意留的：8bit 输入里某些通道会被量化到 0，
    真算出来密度能到 6 以上，这种假极值会直接把反相的整体归一度带偏。
    """
    t = np.clip(t, eps, 1.0)
    return np.clip(-np.log10(t), -1.0, 3.5)


def luma(rgb: np.ndarray,space='srgb') -> np.ndarray:
    """Rec.709 亮度。"""
    if space=='prophoto':
        import colorio
        weights=colorio.MATRICES['prophoto'][1].astype(np.float32)
        return (rgb*weights).sum(axis=-1)
    return (
        rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722
    )


def _num(v, default: float) -> float:
    """把任意输入掰成有限浮点，非法的回退到默认值。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if np.isfinite(f) else default


# --------------------------------------------------------------------------
# 参数
# --------------------------------------------------------------------------
@dataclass
class Params:
    mode: str = "color_negative"

    # 取样（线性 RGB，0..1）。None 表示尚未取样。
    base: list[float] | None = None          # 片基：负片最亮处
    anchor: list[float] | None = None        # 最深黑：负片最暗处（可选，用于自动定 span）

    # 密度域
    span: float = 1.75          # 片基到最深黑的相对密度跨度
    slope: float = 1.60         # 1 / 胶片 gamma
    d_min: float = 0.0          # 相对密度黑场偏移（正值会把片基压得更黑）

    # 线性域
    exposure: float = 0.0       # 以 stop 计
    black: float | None = None  # 黑场（E 域，None = 自动分位）
    white: float | None = None  # 白场
    temp: float = 0.0           # -1..1  偏暖(+R) / 偏冷(+B)
    tint: float = 0.0           # -1..1  偏品(+R+B) / 偏绿(+G)
    saturation: float = 1.0
    channel_repair: bool = False   # 通道欠曝时用其余通道推断
    crosstalk: float = 0.0         # 0..1 染料串色校正强度
    film: str = ""                 # 胶片型号 key（见 films.py），优先于 crosstalk
    curve: list = field(default_factory=lambda: [[0.0, 0.0], [1.0, 1.0]])
    hsl: list = field(default_factory=lambda: [[0.0, 0.0] for _ in range(8)])
    wb_gain: list = field(default_factory=lambda: [1.0, 1.0, 1.0])
    output_space: str = 'srgb'
    working_space: str = 'srgb'
    local_edits: list = field(default_factory=list)
    dust_strength: float = 0.0
    dust_radius: int = 1
    vignette: float = 0.0
    flat_mode: str = 'off'
    flat_strength: float = 0.0
    flat_field: list | None = None
    calibration: dict | None = None
    calibration_strength: float = 1.0

    # 其它
    flip: bool = False
    rotation: int = 0             # Clockwise degrees, multiples of 90.
    straighten: float = 0.0       # Clockwise degrees; safe inner canvas.
    crop: list = field(default_factory=lambda: [0., 0., 1., 1.])
    perspective: dict | None = None  # Four corners after A, before final crop.

    def normalized(self) -> "Params":
        p = Params(**asdict(self))
        p.mode = p.mode if p.mode in MODES else "color_negative"
        p.span = float(np.clip(_num(p.span, 1.75), 0.3, 4.0))
        p.slope = float(np.clip(_num(p.slope, 1.60), 0.3, 3.0))
        p.d_min = float(np.clip(_num(p.d_min, 0.0), -0.5, 1.5))
        p.exposure = float(np.clip(_num(p.exposure, 0.0), -4.0, 4.0))
        p.temp = float(np.clip(_num(p.temp, 0.0), -1.0, 1.0))
        p.tint = float(np.clip(_num(p.tint, 0.0), -1.0, 1.0))
        p.saturation = float(np.clip(_num(p.saturation, 1.0), 0.0, 2.0))
        p.crosstalk = float(np.clip(_num(p.crosstalk, 0.0), 0.0, 1.0))
        p.curve = normalize_curve(p.curve)
        rows = p.hsl if isinstance(p.hsl, (list, tuple)) else []
        p.hsl = [[float(np.clip(_num(row[c], 0), -1, 1))
                  if isinstance(row, (list, tuple)) and len(row) > c else 0.0
                  for c in range(2)] for row in (list(rows) + [[]] * 8)[:8]]
        for name in ('base', 'anchor'):
            value = getattr(p, name)
            if isinstance(value, (list, tuple)) and len(value) == 3:
                setattr(p, name, [float(np.clip(_num(v, 0.5), EPS, 1)) for v in value])
            else:
                setattr(p, name, None)
        for name in ('black', 'white'):
            value = getattr(p, name)
            setattr(p, name, None if value is None else float(np.clip(_num(value, 0), 0, 8)))
        p.film = p.film if isinstance(p.film, str) else ''
        p.flip = p.flip is True
        p.channel_repair = p.channel_repair is True
        if not isinstance(p.wb_gain, (list,tuple)) or len(p.wb_gain) != 3:
            p.wb_gain = [1.,1.,1.]
        p.wb_gain = [float(np.clip(_num(v,1),.125,8)) for v in p.wb_gain]
        p.output_space = p.output_space if p.output_space in ('srgb','adobe_rgb','prophoto') else 'srgb'
        p.working_space = p.working_space if p.working_space in ('srgb','prophoto') else 'srgb'
        import local_edits
        p.local_edits = local_edits.normalize(p.local_edits)
        p.dust_strength = float(np.clip(_num(p.dust_strength,0),0,1))
        p.dust_radius = int(np.clip(_num(p.dust_radius,1),1,3))
        p.vignette = float(np.clip(_num(p.vignette,0),0,1))
        p.flat_mode = p.flat_mode if p.flat_mode in ('off','reference','strip') else 'off'
        p.flat_strength = float(np.clip(_num(p.flat_strength,0),0,1))
        p.flat_field = restoration.normalize_field(p.flat_field)
        p.calibration = calibration.normalize_profile(p.calibration)
        p.calibration_strength = float(np.clip(_num(p.calibration_strength,1),0,1))
        turns,p.straighten,p.crop = geometry.settings(p.__dict__)
        p.rotation = turns * 90
        p.perspective = geometry.perspective.normalize(p.perspective)
        return p


def normalize_curve(points) -> list:
    """有限、严格递增 x、单调 y；最多 16 点，端点允许调整黑白输出。"""
    if not isinstance(points, (list, tuple)):
        return [[0.0, 0.0], [1.0, 1.0]]
    by_x = {}
    for row in points[:16]:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            continue
        x, y = (float(np.clip(_num(v, 0), 0, 1)) for v in row)
        by_x[round(x, 5)] = y
    by_x.setdefault(0.0, 0.0)
    by_x.setdefault(1.0, 1.0)
    out, prev = [], 0.0
    selected = [(0.0, by_x[0.0])] + [(x, y) for x, y in sorted(by_x.items()) if 0 < x < 1][:14] + [(1.0, by_x[1.0])]
    for x, y in selected:
        prev = max(prev, y)
        out.append([x, prev])
    return out


def curve_lut(points, size: int = 4096) -> np.ndarray:
    """Fritsch–Carlson 单调三次 Hermite，限制切线以避免过冲。"""
    pts = np.asarray(normalize_curve(points), dtype=np.float32)
    x, y = pts.T
    dx = np.diff(x)
    sec = np.diff(y) / dx
    tangent = np.empty_like(x)
    tangent[0], tangent[-1] = sec[0], sec[-1]
    tangent[1:-1] = (sec[:-1] + sec[1:]) * np.float32(0.5)
    for i, slope in enumerate(sec):
        if slope <= 0:
            tangent[i:i+2] = 0
        else:
            a, b = tangent[i:i+2] / slope
            length = a*a + b*b
            if length > 9:
                tangent[i:i+2] *= np.float32(3 / np.sqrt(length))
    grid = np.linspace(0, 1, size, dtype=np.float32)
    idx = np.clip(np.searchsorted(x, grid, side='right') - 1, 0, len(x)-2)
    t = (grid - x[idx]) / dx[idx]
    lut = ((2*t**3 - 3*t**2 + 1) * y[idx]
           + (t**3 - 2*t**2 + t) * dx[idx] * tangent[idx]
           + (-2*t**3 + 3*t**2) * y[idx+1]
           + (t**3 - t**2) * dx[idx] * tangent[idx+1])
    return np.clip(lut, 0, 1).astype(np.float32)


def apply_curve(rgb: np.ndarray, points) -> np.ndarray:
    pts = normalize_curve(points)
    # identity 必须逐像素保持旧输出，不经过 LUT 的舍入。
    if all(x == y for x, y in pts):
        return rgb
    lut = curve_lut(pts)
    pos = np.clip(rgb, 0, 1) * np.float32(len(lut)-1)
    index = np.minimum(pos.astype(np.int32), len(lut)-2)
    mix = pos - index.astype(np.float32)
    return (lut[index] + (lut[index+1] - lut[index]) * mix).astype(np.float32)


def apply_hsl(rgb: np.ndarray, adjustments) -> np.ndarray:
    """八个色相的软选择；输入输出均为显示域 float32。

    灰色不参与调整。高斯尾部在 3 sigma 截断，防止远端颜色被牵动。
    """
    adj = np.asarray(adjustments, dtype=np.float32)
    if not np.any(adj):
        return rgb
    x = np.clip(rgb, 0, 1)
    v, low = x.max(axis=-1), x.min(axis=-1)
    delta = v-low
    safe = np.maximum(delta, EPS)
    sat = delta / np.maximum(v, EPS)
    r, g, b = np.moveaxis(x, -1, 0)
    hue = np.where(v == r, (g-b)/safe % 6,
                   np.where(v == g, (b-r)/safe + 2, (r-g)/safe + 4)) * 60
    changes = np.zeros(x.shape[:2] + (2,), dtype=np.float32)
    for i, center in enumerate((0, 30, 60, 120, 180, 240, 280, 320)):
        if not np.any(adj[i]):
            continue
        distance = np.abs((hue-center+180) % 360-180)
        weight = np.exp(-0.5*(distance/20)**2).astype(np.float32)
        weight[distance >= 60] = 0
        changes += weight[..., None] * adj[i]
    s2 = np.clip(sat * (1+changes[..., 0]), 0, 1)
    v2 = np.clip(v * (1+changes[..., 1] * sat), 0, 1)
    sector = hue / 60
    chroma = v2*s2
    mid = chroma * (1-np.abs(sector % 2-1))
    zero = np.zeros_like(v)
    choices = ((chroma, mid, zero), (mid, chroma, zero), (zero, chroma, mid),
               (zero, mid, chroma), (mid, zero, chroma), (chroma, zero, mid))
    result = np.empty_like(x)
    for i, channels in enumerate(choices):
        mask = np.floor(sector).astype(np.int32) % 6 == i
        for c in range(3):
            result[..., c][mask] = channels[c][mask]
    result += (v2-chroma)[..., None]
    # 未调整区域直接复用输入，避免 RGB/HSV 往返误差。
    return np.where(np.any(changes != 0, axis=-1)[..., None], result, rgb).astype(np.float32)


# --------------------------------------------------------------------------
# 取样辅助
# --------------------------------------------------------------------------
def base_clipped_channels(base):
    """Channels whose measured median reaches a normalized decode endpoint.

    This is an endpoint warning, not evidence of RAW sensor saturation. Values
    above one can result from illumination correction and are not decode clips.
    """
    if base is None:return []
    return [i for i,v in enumerate(base) if v<=1e-5 or abs(v-1.)<=1e-5]


def sample_patch(img: np.ndarray, x: float, y: float, radius: int = 8) -> list[float]:
    """在归一化坐标 (x, y) 处取一个块的稳健中值。img 为 HxWx3 线性。"""
    h, w = img.shape[:2]
    px = int(np.clip(x, 0.0, 0.9999) * w)
    py = int(np.clip(y, 0.0, 0.9999) * h)
    x0, x1 = max(0, px - radius), min(w, px + radius + 1)
    y0, y1 = max(0, py - radius), min(h, py + radius + 1)
    patch = img[y0:y1, x0:x1].reshape(-1, img.shape[2])
    return [float(np.median(patch[:, c])) for c in range(img.shape[2])]


def auto_base(img: np.ndarray) -> list[float]:
    """粗估片基：取最亮 0.5% 像素的中值。

    翻拍时片基通常是画面里最亮的连续区域（未曝光处透光最多）。
    画面里没有片基时会失准，所以 UI 里永远允许手动点选覆盖。
    """
    small = img[::4, ::4]
    lum = luma(small) if small.shape[2] >= 3 else small[..., 0]
    thr = np.quantile(lum, 0.995)
    mask = lum >= thr
    vals = small[mask].reshape(-1, small.shape[2])
    if len(vals) == 0:
        vals = small.reshape(-1, small.shape[2])
    return [float(np.median(vals[:, c])) for c in range(small.shape[2])]


def detect_channel_health(img: np.ndarray, threshold: float = 0.5) -> dict:
    """检测哪个通道在量化里死掉了。

    判据不是密度跨度（实测没用：一张坏图里坏通道和好通道的跨度可以几乎相同），
    而是**有多少像素的线性值落在量化死区**（<= 0.005，约 sRGB 20/255）。
    8bit 的暗部就这么点级数，一旦某通道 95% 的像素都挤在底部，
    这个通道的密度信息就已经没了，反相时它只会输出一片纯色。

    返回 {"near_zero": [r,g,b], "bad": [...], "suggest": bool}
    """
    x = np.asarray(img, dtype=np.float32)
    if x.ndim == 2 or x.shape[2] < 3:
        return {"near_zero": [0.0, 0.0, 0.0], "bad": [], "suggest": False}

    small = x[::2, ::2]
    near = [float((small[..., c] <= 0.005).mean()) for c in range(3)]

    bad = []
    for c in range(3):
        others = [near[i] for i in range(3) if i != c]
        ref = max(max(others), 1e-6)
        if near[c] > threshold and near[c] > 3.0 * ref:
            bad.append(c)

    return {
        "near_zero": [round(v, 4) for v in near],
        "bad": bad,
        "suggest": bool(bad) and len(bad) < 3,
    }


def auto_anchor(img: np.ndarray, base: list[float]) -> tuple[list[float], float]:
    """估最深黑：取最暗分位的中值，返回 (取样值, 相对密度跨度)。"""
    small = img[::4, ::4]
    if small.shape[2] >= 3:
        d = to_density(small) - to_density(np.asarray(base, dtype=np.float32))
        rel = d.mean(axis=2)
    else:
        rel = to_density(small[..., 0]) - float(to_density(np.asarray(base[:1])))
    thr = np.quantile(rel, 0.999)
    mask = rel >= thr
    vals = small[mask].reshape(-1, small.shape[2])
    if len(vals) == 0:
        return list(base), 1.75
    anchor = [float(np.median(vals[:, c])) for c in range(small.shape[2])]
    # 用接近极值的分位估跨度：负片的黑点往往只是画面极小一块，
    # 取中值会严重低估，导致反相后暗部被压成一片白；
    # 但分位太靠尾巴又会被暗部噪声拉高，所以取 99.9%
    span = float(np.quantile(rel, 0.999))
    span = float(np.clip(span, 0.6, 3.5))
    return anchor, span


# --------------------------------------------------------------------------
# 主处理函数
# --------------------------------------------------------------------------
def scene_linear(rgb: np.ndarray, p: Params, repair_context=None) -> np.ndarray:
    """反相后、白平衡和黑白场之前的场景线性值，供 WB 估计复用。"""
    p = p.normalized()
    x = np.asarray(rgb, dtype=np.float32)
    # 扫描软件/上游偶尔会吐 NaN 或 Inf，一处脏数据能污染整张
    if not np.isfinite(x).all():
        x = np.nan_to_num(x, nan=0.0, posinf=1.0, neginf=0.0)

    if x.ndim == 2:
        x = np.repeat(x[..., None], 3, axis=2)
    x = restoration.correct_light(x, p.flat_field if p.flat_mode != 'off' else None,
                                  p.flat_strength, p.vignette)
    if p.flip:
        x = x[:, ::-1]

    # 片基：未取样时用中性假设，失败会明显偏色，UI 里会提示
    base = np.asarray(p.base if p.base is not None else [0.5, 0.5, 0.5], dtype=np.float32)
    base = np.clip(base, EPS, 1.0)

    span = max(float(p.span), 0.3)
    dmin = float(p.d_min)

    if p.mode == "positive":
        # 正片 / 反转片：透射率本身就是场景亮度，不需要反相，
        # 只做黑场白场拉伸（翻拍时 no_auto_bright 会让最大值远低于 1）
        e = np.clip(x, 0.0, 1.0)
    elif p.mode == "bw_negative":
        # 黑白：先合成单通道再走同一套密度逻辑
        g = luma(x,p.working_space) if x.shape[2] >= 3 else x[..., 0]
        g = np.clip(g, EPS, 1.0)
        d = -np.log10(g)
        db = float(to_density(np.array([np.mean(base)], dtype=np.float32))[0])
        rel = (d - db) - dmin
        norm = rel / span
        norm = np.clip(norm, 0.0, 1.0)
        e = np.power(10.0, -(1.0 - norm) * span * p.slope)
        e = e[..., None].repeat(3, axis=2)
    else:
        # 彩色负片：逐通道密度反相
        d = to_density(x)
        db = to_density(base)
        rel = (d - db) - dmin           # 逐通道去色罩

        # 通道修复：某个通道已经死在量化死区里时，用其余通道推断它的密度。
        # 换来的颜色是猜的，但结构和反差能救回来——总比一片纯蓝强。
        if p.channel_repair:
            health = repair_context['health'] if repair_context is not None else detect_channel_health(x)
            if health["suggest"]:
                good = [c for c in range(3) if c not in health["bad"]]
                mix = rel[..., good].mean(axis=2)
                for c in health["bad"]:
                    rel[..., c] = mix
                # 跨度要按修复后的数据重估，否则会被坏通道的虚高密度带偏
                span = repair_context['span'] if repair_context is not None else float(np.clip(np.quantile(rel.mean(axis=2), 0.999), 0.6, 3.5))

        # 染料串色校正：密度域左乘 3×3 矩阵。
        # 优先用胶片型号对应的系数（films.py，从厂商光谱数据表推导），
        # 没有型号时才退回强度滑块的经验预设。
        m = None
        if p.film and p.film in FILMS:
            raw = np.asarray(FILMS[p.film]["matrix"], dtype=np.float32)
            m = raw / raw.sum(axis=1, keepdims=True)
        elif p.crosstalk > 1e-4:
            m = crosstalk_matrix(p.crosstalk)
        if m is not None:
            rel = rel @ m.T

        norm = rel / span
        norm = np.clip(norm, 0.0, 1.0)
        e = np.power(10.0, -(1.0 - norm) * span * p.slope)

    return np.clip(e, 0.0, 8.0)


def estimate_wb(rgb: np.ndarray, p: Params, method='gray_world') -> list:
    if p.mode == 'bw_negative':
        raise ValueError('黑白负片不需要自动白平衡')
    if method not in ('gray_world','white_patch'):
        raise ValueError('未知白平衡策略')
    e = scene_linear(rgb,p)
    small = e[::max(1,e.shape[0]//512),::max(1,e.shape[1]//512)]
    lum = luma(small,p.working_space)
    mask = (lum > EPS) & (small.max(axis=2) < .999)
    if method == 'white_patch':
        brightness = lum[mask]
        if not brightness.size:
            raise ValueError('没有可用的未剪裁高光，请改用灰世界或点中性灰')
        mask &= lum >= np.quantile(brightness,.995)
        mask &= (small.max(axis=2)-small.min(axis=2))/np.maximum(small.max(axis=2),EPS) < .45
        if not np.any(mask):
            raise ValueError('未找到高光中性色，请改用灰世界或点中性灰')
        sample = np.median(small[mask],axis=0)
    else:
        if not np.any(mask):
            raise ValueError('没有可用的未剪裁像素，无法估计白平衡')
        lo,hi = np.quantile(lum[mask],[.02,.98])
        sample = small[mask & (lum >= lo) & (lum <= hi)].mean(axis=0)
    if np.any(sample <= EPS):
        raise ValueError('某通道无有效信号，无法可靠估计白平衡')
    gain = sample.mean()/sample
    gain /= luma(gain,p.working_space)
    if gain.min() < .125 or gain.max() > 8:
        raise ValueError('所需校正超过安全范围，请检查片基或通道欠曝')
    return gain.astype(np.float32).tolist()


def wb_gain(p):
    """Shared gain vector; viewport global metering must use identical arithmetic."""
    gain = np.ones(3, dtype=np.float32)
    gain[0] *= 1.0 + 0.25 * p.temp
    gain[2] *= 1.0 - 0.25 * p.temp
    gain[1] *= 1.0 + 0.15 * p.tint
    gain[0] *= 1.0 - 0.10 * p.tint
    gain[2] *= 1.0 - 0.10 * p.tint
    if p.mode != 'bw_negative':
        gain *= np.asarray(p.wb_gain,np.float32)
    return gain


def working_linear(rgb: np.ndarray, p: Params, repair_context=None) -> np.ndarray:
    """After WB/levels/exposure, before chart correction, encoding and styling."""
    p = p.normalized()
    e = scene_linear(rgb,p,repair_context)
    e = e * wb_gain(p)

    # 黑白场：物理模型给的是场景亮度，片基处只是"接近 0"而不是 0，
    # 不拉伸的话暗部永远浮在 45/255 左右，整张发灰。
    lo = float(p.black) if p.black is not None else None
    hi = float(p.white) if p.white is not None else None
    if lo is None or hi is None:
        lum = luma(e,p.working_space) if e.shape[2] >= 3 else e[..., 0]
        q_lo = float(np.quantile(lum, 0.002))
        q_hi = float(np.quantile(lum, 0.998))
        if lo is None:
            lo = q_lo
        if hi is None:
            hi = q_hi
    if hi - lo > 1e-5:
        e = (e - lo) / (hi - lo)

    e = e * (2.0 ** p.exposure)
    e = np.clip(e, 0.0, 1.0)
    return e


def process(rgb: np.ndarray, p: Params, target_space='srgb', repair_context=None) -> np.ndarray:
    """线性 RGB → sRGB 显示域；输出色彩空间由导出器另行转换。"""
    p = p.normalized()
    e = working_linear(rgb,p,repair_context)
    if p.mode != 'bw_negative':
        e = calibration.apply(e,p.calibration,p.calibration_strength,p.working_space)

    # 饱和度（在 sRGB 编码域做更符合直觉）
    if p.working_space=='prophoto':
        import colorio
        out=colorio.encode(e,'prophoto')
    else:out = srgb_encode(e)
    if p.mode != "positive" and abs(p.saturation - 1.0) > 1e-4:
        l = luma(out,p.working_space)[..., None]
        out = l + (out - l) * p.saturation

    out = np.clip(out, 0.0, 1.0).astype(np.float32)
    out = apply_curve(out, p.curve)
    if p.mode != 'bw_negative':
        out = apply_hsl(out, p.hsl)
    out = np.clip(out, 0.0, 1.0).astype(np.float32)
    out=restoration.remove_dust(out,p.dust_strength,p.dust_radius)
    if p.local_edits:
        import local_edits
        out=local_edits.apply(out,p.local_edits,p.working_space,p.flip)
    out=geometry.apply(out,p.__dict__)
    if p.working_space!='srgb' or target_space!='srgb':
        import colorio
        out=colorio.convert(out,target_space,p.working_space)
    return out
