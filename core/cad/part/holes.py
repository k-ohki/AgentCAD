"""穴ウィザードの規格寸法（D57）。メートル並目ねじ（JIS / ISO）。

- キリ穴（ボルトを通す穴）: JIS B 1001 / ISO 273 の 2 級（中）
- ねじ下穴: 並目のタップ下穴（JIS B 1004 の目安）
- ザグリ: 六角穴付きボルト（JIS B 1176）用のザグリ径・深さの目安
- 皿穴: 皿小ねじ（90°）の皿の径の目安（ISO 15065）
値は一般的な設計の目安。使う規格・メーカーの値に合わせて、穴を作った後に寸法を直せる（直すとサイズは「カスタム」になる）。
"""
from __future__ import annotations

#: サイズ → (ねじ径, ピッチ, 下穴径, キリ穴径, ザグリ径, ザグリ深さ, 皿の径)
METRIC = {
    "M2":  (2.0, 0.4, 1.6, 2.4, 4.3, 2.3, 4.4),
    "M2.5": (2.5, 0.45, 2.05, 2.9, 5.5, 2.8, 5.5),
    "M3":  (3.0, 0.5, 2.5, 3.4, 6.5, 3.3, 6.3),
    "M4":  (4.0, 0.7, 3.3, 4.5, 8.0, 4.4, 9.4),
    "M5":  (5.0, 0.8, 4.2, 5.5, 9.5, 5.4, 10.4),
    "M6":  (6.0, 1.0, 5.0, 6.6, 11.0, 6.5, 12.6),
    "M8":  (8.0, 1.25, 6.8, 9.0, 14.0, 8.6, 17.3),
    "M10": (10.0, 1.5, 8.5, 11.0, 17.5, 10.8, 20.0),
    "M12": (12.0, 1.75, 10.2, 13.5, 20.0, 13.0, 24.0),
    "M16": (16.0, 2.0, 14.0, 17.5, 26.0, 17.5, 31.0),
    "M20": (20.0, 2.5, 17.5, 22.0, 32.0, 21.5, 40.0),
}

#: 穴の種類（一般的な 3D CAD の穴タイプ）
KINDS = (("cbore", "ザグリ穴"), ("csink", "皿穴"), ("clearance", "キリ穴"), ("tap", "ねじ穴（下穴）"), ("simple", "ドリル穴"))
SIZES = tuple((k, k) for k in METRIC) + (("custom", "カスタム"),)


def standard_dims(kind: str, size: str) -> dict[str, float]:
    """種類とサイズから、穴の寸法（直径・ザグリ・皿）。サイズがカスタムなら空。"""
    if size not in METRIC:
        return {}
    d, pitch, tap, clr, cb_d, cb_h, cs_d = METRIC[size]
    out = {"diameter": tap if kind == "tap" else d if kind == "simple" else clr}
    if kind == "cbore":
        out.update(cbore_diameter=cb_d, cbore_depth=cb_h)
    if kind == "csink":
        out.update(csink_diameter=cs_d)
    return out


def hole_profile(kind: str, r: float, depth: float, through: bool, tip_angle: float,
                 cbore_r: float, cbore_depth: float, csink_r: float, csink_angle: float,
                 above: float) -> list[tuple[str, tuple[float, float]]]:
    """回転させる断面: (r, s) の折れ線（s = 穴の奥へ向かう深さ、r = 軸からの距離）。
    各要素は (この点から次の点への辺の名前, 点)。最初の点は軸の上、最後の辺で軸に沿って閉じる。
    above = 面より手前に出す長さ（面と同じ所にカットの面ができないように）。"""
    pts: list[tuple[str, tuple[float, float]]] = [("top", (0.0, -above))]
    if kind == "cbore":
        pts += [("cbore_wall", (cbore_r, -above)), ("cbore_floor", (cbore_r, cbore_depth)), ("wall", (r, cbore_depth))]
    elif kind == "csink":
        h = (csink_r - r) / math_tan_half(csink_angle)          # 皿の円錐の深さ
        pts += [("csink", (csink_r + above * math_tan_half(csink_angle), -above)), ("wall", (r, h))]
        if h >= depth and not through:        # noqa: SIM102
            raise ValueError("皿が穴の深さより深くなります（深さを大きくしてください）")
    else:
        pts += [("wall", (r, -above))]
    if through or tip_angle >= 180 - 1e-9:
        pts += [("floor", (r, depth)), ("axis", (0.0, depth))]
    else:
        pts += [("tip", (r, depth)), ("axis", (0.0, depth + r / math_tan_half(tip_angle)))]
    return pts


def math_tan_half(angle_deg: float) -> float:
    import math
    return math.tan(math.radians(angle_deg) / 2)
