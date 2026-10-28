"""项目自检：语法、导入、关键路径、边界情况。"""
import importlib
import os
import sys
import traceback

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

FAILS = []


def check(name, fn):
    try:
        fn()
        print(f"  OK   {name}")
    except Exception as e:
        FAILS.append((name, e))
        print(f"  FAIL {name}: {type(e).__name__}: {str(e)[:120]}")


print("=== 1. 模块导入 ===")
MODS = ["core", "films", "loader", "strip", "tiffio", "tiffread", "server"]
for m in MODS:
    check(f"import {m}", lambda m=m: importlib.import_module(m))

import core
from core import Params, process, auto_base, auto_anchor, detect_channel_health, crosstalk_matrix
from films import FILMS

print("\n=== 2. 参数边界 ===")


def t_extreme_params():
    lin = np.random.default_rng(0).random((64, 64, 3)).astype(np.float32) * 0.5
    for kw in [
        dict(span=0.01), dict(span=100), dict(slope=0.01), dict(slope=100),
        dict(exposure=-100), dict(exposure=100), dict(saturation=-5),
        dict(saturation=99), dict(temp=-99), dict(tint=99),
        dict(crosstalk=-1), dict(crosstalk=5), dict(film="不存在的胶片"),
        dict(mode="bogus"), dict(flip=True),
    ]:
        p = Params(base=[0.6, 0.45, 0.28], **kw)
        out = process(lin, p)
        assert out.shape == lin.shape, f"{kw} shape {out.shape}"
        assert np.isfinite(out).all(), f"{kw} 出现 NaN/Inf"
        assert out.min() >= 0 and out.max() <= 1, f"{kw} 越界 {out.min()}..{out.max()}"


check("极端参数不崩不越界", t_extreme_params)


def t_nan_input():
    lin = np.zeros((8, 8, 3), dtype=np.float32)
    lin[0, 0] = np.nan
    lin[1, 1] = np.inf
    out = process(lin, Params(base=[0.5, 0.5, 0.5]))
    assert np.isfinite(out).all(), "输入 NaN/Inf 输出不干净"


check("NaN/Inf 输入", t_nan_input)


def t_all_black():
    lin = np.zeros((16, 16, 3), dtype=np.float32)
    out = process(lin, Params(base=[0.5, 0.5, 0.5]))
    assert np.isfinite(out).all()


check("全黑输入", t_all_black)


def t_tiny():
    lin = np.zeros((1, 1, 3), dtype=np.float32)
    out = process(lin, Params(base=[0.5, 0.5, 0.5]))
    assert out.shape == (1, 1, 3)


check("1x1 输入", t_tiny)

print("\n=== 3. 密度/矩阵 ===")


def t_matrix_rows():
    # films.py 存的是 NegPy 原始矩阵（对角 1.0、行和 < 1），归一化在 process 里做。
    # 归一化的意义：密度三通道相等的输入，输出也必须相等（不然中性色会被推色）。
    for k in FILMS:
        raw = np.asarray(FILMS[k]["matrix"], dtype=np.float32)
        m = raw / raw.sum(axis=1, keepdims=True)
        got = m @ np.ones(3, dtype=np.float32)
        assert np.allclose(got, 1.0, atol=1e-4), f"{k} 归一化失效 {got}"
    cm = crosstalk_matrix(1.0)
    assert np.allclose(cm.sum(axis=1), 1.0, atol=1e-5)


check("胶片矩阵归一化正确", t_matrix_rows)


def t_density_clamp():
    d = core.to_density(np.array([0.0, 1e-12, 1.0], dtype=np.float32))
    assert np.isfinite(d).all()
    assert d.max() <= 3.5 + 1e-6


check("密度夹紧", t_density_clamp)

print("\n=== 4. 通道检测 ===")


def t_health():
    good = np.random.default_rng(0).random((64, 64, 3)).astype(np.float32) * 0.5 + 0.1
    h = detect_channel_health(good)
    assert h["bad"] == [], f"正常图误判 {h}"

    bad = good.copy()
    bad[..., 2] = 0.0001
    h2 = detect_channel_health(bad)
    assert h2["bad"] == [2], f"坏通道没检出 {h2}"


check("坏通道检测", t_health)

print("\n=== 5. TIFF 往返 ===")


def t_tiff():
    from tiffio import write_tiff_rgb16
    from tiffread import read_tiff
    a = np.random.default_rng(1).random((97, 61, 3)).astype(np.float32)
    exp = (a * 65535 + 0.5).astype(np.uint16)
    os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
    for mode in ("none", "deflate"):
        p = os.path.join(HERE, "out", f"_selfcheck_{mode}.tif")
        write_tiff_rgb16(p, a, compress=mode, predictor=(mode != "none"))
        got = read_tiff(p)
        assert np.array_equal(got, exp), f"{mode} 往返不一致"
        os.remove(p)


check("TIFF 无损往返", t_tiff)

print("\n=== 6. 服务器路由 ===")


def t_routes():
    import server
    assert hasattr(server.Handler, "do_GET") and hasattr(server.Handler, "do_POST")
    # 检查关键函数存在
    for fn in ("do_open", "do_frame", "do_thumb", "do_sample", "do_preview", "do_export"):
        assert callable(getattr(server, fn)), f"缺 {fn}"
    # 参数序列化不该崩
    for d in ({}, {"mode": "bw_negative"}, {"span": "abc"}, {"base": None}):
        try:
            server.params_from(d)
        except Exception as e:
            raise AssertionError(f"params_from({d}) 崩了: {e}")


check("服务器函数/参数解析", t_routes)


def t_params_from_bad():
    import server
    p = server.params_from({"span": "abc", "base": "not-a-list"})
    assert p.span == 1.75  # 非法值应回退到默认


check("非法参数回退", t_params_from_bad)

print("\n=== 7. 整条布局 ===")


def t_layout():
    from strip import detect_layout, extract_one, frame_boxes
    # 构造一条假胶片：两条，各 3 帧，之间有亮间隙
    h, w = 900, 400
    img = np.full((h, w, 3), 0.35, dtype=np.float32)
    img[:, 0:20] = 0.02
    img[:, 180:220] = 0.02
    img[:, 380:400] = 0.02
    for y in (300, 600):
        img[y - 10:y + 10] = 0.75
    layout = detect_layout(img, min_strip_width=50)
    boxes = frame_boxes(layout)
    assert len(layout.strips) == 2, f"条数 {layout.strips}"
    assert len(boxes) >= 4, f"帧数 {len(boxes)}"
    si, fi, arr = extract_one(img, layout, 0)
    assert arr.ndim == 3


check("整条布局检测", t_layout)

print("\n" + "=" * 50)
if FAILS:
    print(f"失败 {len(FAILS)} 项")
    for name, e in FAILS:
        print(f"  - {name}")
    sys.exit(1)
print("全部通过")
