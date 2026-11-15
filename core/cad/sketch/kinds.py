"""スケッチ拘束の残差定義（ソルバの Kind）。

p は局所変数のリスト、c は定数 (B, nc)。点は (x, y)、線は (ax, ay, bx, by)、
円・円弧は 中心点 (cx, cy) + 半径変数 r で表す。
"""
from __future__ import annotations

import numpy as np

from ..solver import Kind
from ..solver.ad import atan2, sqrt


def _hyp(dx, dy):
    return sqrt(dx * dx + dy * dy)


def _line_vec(p, o=0):
    return p[o + 2] - p[o], p[o + 3] - p[o + 1]


def _coincident(p, c):
    return [p[0] - p[2], p[1] - p[3]]


def _parallel(p, c):
    ux, uy = _line_vec(p); wx, wy = _line_vec(p, 4)
    return [(ux * wy - uy * wx) / (_hyp(ux, uy) * _hyp(wx, wy))]


def _perpendicular(p, c):
    ux, uy = _line_vec(p); wx, wy = _line_vec(p, 4)
    return [(ux * wx + uy * wy) / (_hyp(ux, uy) * _hyp(wx, wy))]


def _angle(p, c):
    """線1から線2への符号付き角度 = c0 [rad]。atan2 で (角度 − 目標) を直接出すので枝を間違えにくい。"""
    ux, uy = _line_vec(p); wx, wy = _line_vec(p, 4)
    cr, dt = ux * wy - uy * wx, ux * wx + uy * wy
    cs, sn = np.cos(c[:, 0]), np.sin(c[:, 0])
    return [atan2(cr * cs - dt * sn, dt * cs + cr * sn)]


def _equal_len(p, c):
    return [_hyp(*_line_vec(p)) - _hyp(*_line_vec(p, 4))]


def _signed_dist(px, py, p, o):
    """点 (px,py) の、線 p[o:o+4] からの符号付き距離（線の左側が正）。"""
    dx, dy = _line_vec(p, o)
    return (dx * (py - p[o + 1]) - dy * (px - p[o])) / _hyp(dx, dy)


def _point_on_line(p, c):
    return [_signed_dist(p[0], p[1], p, 2)]


def _point_on_circle(p, c):              # 点, 中心, r
    return [_hyp(p[0] - p[2], p[1] - p[3]) - p[4]]


def _midpoint(p, c):
    return [p[0] - 0.5 * (p[2] + p[4]), p[1] - 0.5 * (p[3] + p[5])]


def _tangent(p, c):                      # 線, 中心, r / c0 = 中心が線のどちら側か
    return [c[:, 0] * _signed_dist(p[4], p[5], p, 0) - p[6]]


def _tangent_at(p, c):                   # 線, 中心, 接点P: 半径方向 ⟂ 線
    dx, dy = _line_vec(p)
    rx, ry = p[6] - p[4], p[7] - p[5]
    return [(dx * rx + dy * ry) / (_hyp(dx, dy) * _hyp(rx, ry))]


def _tangent_cc(p, c):                   # 中心1, 中心2, r1, r2 / 中心間距離 = c0·r1 + c1·r2
    return [_hyp(p[2] - p[0], p[3] - p[1]) - (c[:, 0] * p[4] + c[:, 1] * p[5])]


def _tangent_cc_at(p, c):                # 中心1, 中心2, 接点P: 2つの中心と接点が一直線
    ax, ay, bx, by = p[4] - p[0], p[5] - p[1], p[4] - p[2], p[5] - p[3]
    return [(ax * by - ay * bx) / (_hyp(ax, ay) * _hyp(bx, by))]


def _symmetric(p, c):                    # 点P, 点Q, 線: 中点が線上 かつ PQ ⟂ 線
    dx, dy = _line_vec(p, 4)
    L = _hyp(dx, dy)
    mx, my = 0.5 * (p[0] + p[2]), 0.5 * (p[1] + p[3])
    return [(dx * (my - p[5]) - dy * (mx - p[4])) / L, ((p[2] - p[0]) * dx + (p[3] - p[1]) * dy) / L]


COINCIDENT = Kind("coincident", _coincident, 2)
HORIZONTAL = Kind("horizontal", lambda p, c: [p[1] - p[3]], 1)
VERTICAL = Kind("vertical", lambda p, c: [p[0] - p[2]], 1)
PARALLEL = Kind("parallel", _parallel, 1)
PERPENDICULAR = Kind("perpendicular", _perpendicular, 1)
ANGLE = Kind("angle", _angle, 1)
EQUAL_LEN = Kind("equal_len", _equal_len, 1)
EQUAL_PARAM = Kind("equal_param", lambda p, c: [p[0] - p[1]], 1)
POINT_ON_LINE = Kind("point_on_line", _point_on_line, 1)
POINT_ON_CIRCLE = Kind("point_on_circle", _point_on_circle, 1)
MIDPOINT = Kind("midpoint", _midpoint, 2)
TANGENT = Kind("tangent", _tangent, 1)
TANGENT_AT = Kind("tangent_at", _tangent_at, 1)
TANGENT_CC = Kind("tangent_cc", _tangent_cc, 1)
TANGENT_CC_AT = Kind("tangent_cc_at", _tangent_cc_at, 1)
SYMMETRIC = Kind("symmetric", _symmetric, 2)
FIX = Kind("fix", lambda p, c: [p[0] - c[:, 0], p[1] - c[:, 1]], 2)


def _on_fixed_line(p, c):
    """点が、定数で与えた直線（c = x1, y1, x2, y2）の上にある。"""
    dx, dy = c[:, 2] - c[:, 0], c[:, 3] - c[:, 1]
    return [((p[0] - c[:, 0]) * dy - (p[1] - c[:, 1]) * dx) / np.hypot(dx, dy)]


ON_FIXED_LINE = Kind("on_fixed_line", _on_fixed_line, 1)
DIST_PP = Kind("dist_pp", lambda p, c: [_hyp(p[2] - p[0], p[3] - p[1]) - c[:, 0]], 1)
DIST_PL = Kind("dist_pl", lambda p, c: [c[:, 1] * _signed_dist(p[0], p[1], p, 2) - c[:, 0]], 1)
HDIST = Kind("hdist", lambda p, c: [c[:, 1] * (p[2] - p[0]) - c[:, 0]], 1)
VDIST = Kind("vdist", lambda p, c: [c[:, 1] * (p[3] - p[1]) - c[:, 0]], 1)
LENGTH = Kind("length", lambda p, c: [_hyp(*_line_vec(p)) - c[:, 0]], 1)
PARAM_VALUE = Kind("param_value", lambda p, c: [p[0] - c[:, 0]], 1)


def _collinear3(p, c):
    """3 点 a, b, c が一直線（a から見た b と c の向きが同じか逆）。"""
    ux, uy, wx, wy = p[2] - p[0], p[3] - p[1], p[4] - p[0], p[5] - p[1]
    return [(ux * wy - uy * wx) / (_hyp(ux, uy) * _hyp(wx, wy))]


COLLINEAR3 = Kind("collinear3", _collinear3, 1)
PARAM_DIFF = Kind("param_diff", lambda p, c: [p[0] - p[1] - c[:, 0]], 1)    # 半径の差（円・円弧のオフセット）


# 投影（エンティティ変換）: 要素を投影元のエッジの形に固定する（c = 投影した形）
FIX_LINE = Kind("fix_line", lambda p, c: [p[i] - c[:, i] for i in range(4)], 4)          # 線の両端
FIX_CIRCLE = Kind("fix_circle", lambda p, c: [p[i] - c[:, i] for i in range(3)], 3)      # 中心, r


def _fix_arc(p, c):
    """中心・始点を固定し、終点は中心から目標の方向にあること（半径は始点が円上にある暗黙拘束で決まる）。"""
    ex, ey, tx, ty = p[4] - p[0], p[5] - p[1], c[:, 4] - c[:, 0], c[:, 5] - c[:, 1]
    return [p[0] - c[:, 0], p[1] - c[:, 1], p[2] - c[:, 2], p[3] - c[:, 3],
            (ex * ty - ey * tx) / (_hyp(ex, ey) * np.hypot(tx, ty))]


FIX_ARC = Kind("fix_arc", _fix_arc, 5)                   # 中心, 始点, 終点（の方向）
