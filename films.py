"""胶片染料串色矩阵库。

数据来源：https://github.com/marcinz606/NegPy  (GPL-3.0)
         crosstalk/*.toml，社区整理的 specsheet-based 推导值。

这些系数是从厂商公开的染料光谱密度数据表推导出来的：取青/品红/黄三层染料
在 650/550/450 nm 的密度，减去各自的 D-min 后解线性混合。
注意是「近似值」——datasheet 上的曲线是平均值，不是你这卷胶片的实测值，
也没有考虑光源光谱和传感器滤镜。真要精确，还是得拍一张透射色卡自己标定。

格式：每行的三个数对应「输出的 R/G/B ← 输入的 R/G/B」，使用前按行归一化。
"""

FILMS = {
    "kodak_aerocolor_iv_2460": {
        "name": 'Kodak Aerocolor IV 2460',
        "type": "specsheet-based",
        "matrix": [
            [1.0000, -0.0963, -0.0233],
            [-0.0580, 1.0000, -0.0683],
            [-0.0205, -0.1579, 1.0000],
        ],
    },
    "kodak_ektacolor_pro_160": {
        "name": 'Kodak Ektacolor Pro 160 / Portra 160',
        "type": "specsheet-based",
        "matrix": [
            [1.0000, -0.0739, -0.0004],
            [-0.0754, 1.0000, -0.0733],
            [-0.1535, 0.0199, 1.0000],
        ],
    },
    "kodak_ektacolor_pro_400": {
        "name": 'Kodak Ektacolor Pro 400 / Portra 400',
        "type": "specsheet-based",
        "matrix": [
            [1.0000, -0.0738, -0.0004],
            [-0.0894, 1.0000, -0.0709],
            [-0.1174, 0.0165, 1.0000],
        ],
    },
    "kodak_ektacolor_pro_800": {
        "name": 'Kodak Ektacolor Pro 800 / Portra 800',
        "type": "specsheet-based",
        "matrix": [
            [1.0000, -0.0734, -0.0005],
            [-0.0922, 1.0000, -0.0699],
            [-0.1111, 0.0134, 1.0000],
        ],
    },
    "kodak_ektar_100": {
        "name": 'Kodak Ektar 100',
        "type": "specsheet-based",
        "matrix": [
            [1.0000, -0.0739, -0.0004],
            [-0.0972, 1.0000, -0.0692],
            [-0.1037, 0.0157, 1.0000],
        ],
    },
    "kodak_gold_200": {
        "name": 'Kodak Gold 200',
        "type": "specsheet-based",
        "matrix": [
            [1.0000, -0.0733, -0.0006],
            [-0.0961, 1.0000, -0.0679],
            [-0.1054, 0.0126, 1.0000],
        ],
    },
    "kodak_pro_image_100": {
        "name": 'Kodak Pro Image 100',
        "type": "specsheet-based",
        "matrix": [
            [1.0000, -0.0681, -0.0159],
            [-0.0488, 1.0000, -0.0590],
            [-0.0140, -0.1190, 1.0000],
        ],
    },
    "kodak_ultramax_400": {
        "name": 'Kodak Ultra Max 400',
        "type": "specsheet-based",
        "matrix": [
            [1.0000, -0.0733, -0.0005],
            [-0.0932, 1.0000, -0.0694],
            [-0.1096, 0.0128, 1.0000],
        ],
    },
    "kodak_verita_200d": {
        "name": 'Kodak VERITA 200D 5206/7206',
        "type": "specsheet-based",
        "matrix": [
            [1.0000, -0.0738, -0.0007],
            [-0.1551, 1.0000, -0.0622],
            [-0.0482, 0.0007, 1.0000],
        ],
    },
    "kodak_vision3_200t": {
        "name": 'Kodak VISION3 200T 5213/7213',
        "type": "specsheet-based",
        "matrix": [
            [1.0000, -0.0549, -0.0002],
            [-0.1518, 1.0000, -0.0317],
            [-0.0575, -0.0259, 1.0000],
        ],
    },
    "kodak_vision3_250d": {
        "name": 'Kodak VISION3 250D 5207/7207',
        "type": "specsheet-based",
        "matrix": [
            [1.0000, -0.0906, 0.0091],
            [-0.1274, 1.0000, -0.0565],
            [-0.0661, 0.0177, 1.0000],
        ],
    },
    "kodak_vision3_500t": {
        "name": 'Kodak VISION3 500T 5219/7219',
        "type": "specsheet-based",
        "matrix": [
            [1.0000, -0.0734, 0.0087],
            [-0.1386, 1.0000, -0.1051],
            [-0.0337, 0.0367, 1.0000],
        ],
    },
    "kodak_vision3_50d": {
        "name": 'Kodak VISION3 50D 5203/7203',
        "type": "specsheet-based",
        "matrix": [
            [1.0000, -0.0753, -0.0214],
            [-0.1431, 1.0000, -0.0455],
            [-0.0828, 0.0052, 1.0000],
        ],
    },
    "fujifilm_100": {
        "name": 'Fujicolor 100',
        "type": "specsheet-based",
        "matrix": [
            [1.0000, -0.0425, -0.0177],
            [-0.0486, 1.0000, -0.0540],
            [-0.0180, -0.1392, 1.0000],
        ],
    },
    "fujifilm_200": {
        "name": 'Fujicolor 200',
        "type": "specsheet-based",
        "matrix": [
            [1.0000, -0.0462, -0.0272],
            [-0.0482, 1.0000, -0.0586],
            [-0.0231, -0.1388, 1.0000],
        ],
    },
    "fujifilm_400": {
        "name": 'Fujicolor 400',
        "type": "specsheet-based",
        "matrix": [
            [1.0000, -0.0459, -0.0272],
            [-0.0482, 1.0000, -0.0586],
            [-0.0226, -0.1489, 1.0000],
        ],
    },
    "fujifilm_c200": {
        "name": 'Fujicolor C200',
        "type": "specsheet-based",
        "matrix": [
            [1.0000, -0.0459, -0.0272],
            [-0.0482, 1.0000, -0.0586],
            [-0.0226, -0.1489, 1.0000],
        ],
    },
    "fujifilm_natura_1600": {
        "name": 'Fujicolor Natura 1600',
        "type": "specsheet-based",
        "matrix": [
            [1.0000, -0.0564, -0.0261],
            [-0.0580, 1.0000, -0.0683],
            [-0.0217, -0.1387, 1.0000],
        ],
    },
    "fujifilm_pro_400h": {
        "name": 'Fujicolor Pro 400H',
        "type": "specsheet-based",
        "matrix": [
            [1.0000, -0.0278, -0.0186],
            [-0.0590, 1.0000, -0.0488],
            [-0.0128, -0.1196, 1.0000],
        ],
    },
    "fujifilm_superia_premium_400": {
        "name": 'Fujicolor Superia Premium 400',
        "type": "specsheet-based",
        "matrix": [
            [1.0000, -0.0474, -0.0172],
            [-0.0485, 1.0000, -0.0590],
            [-0.0175, -0.1491, 1.0000],
        ],
    },
    "fujifilm_superia_xtra_400": {
        "name": 'Fujicolor Superia X-TRA 400',
        "type": "specsheet-based",
        "matrix": [
            [1.0000, -0.0459, -0.0272],
            [-0.0482, 1.0000, -0.0586],
            [-0.0226, -0.1489, 1.0000],
        ],
    },
    "harman_phoenix_ii": {
        "name": 'Harman Phoenix II',
        "type": "specsheet-based",
        "matrix": [
            [1.0000, -0.0579, -0.0171],
            [-0.0390, 1.0000, -0.0492],
            [-0.0152, -0.1191, 1.0000],
        ],
    },
}
