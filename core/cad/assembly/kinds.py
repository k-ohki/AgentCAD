"""合致の残差定義（ソルバの Kind）。

局所変数 p (14): 部品A [tx,ty,tz,qw,qx,qy,qz] + 部品B 同じ。
定数 c (N_CONSTS=20): A のローカル幾何 [原点3, 方向3, 方向に垂直な u 3, w 3]、
                      B のローカル幾何 [原点3, 方向3]、s（向き。現在は残差に使わず、事前位置合わせで扱う）、off（距離 or cos角）。
残差の数は「外す自由度の数」と一致させる（ランク解析で冗長と誤判定しないため）。
"""
from __future__ import annotations

from ..solver import Kind
from ..solver.ad import dot, qrot, sqrt, vadd, vsub

N_CONSTS = 20


def _frames(p, c):
    v = lambda i: (c[:, i], c[:, i + 1], c[:, i + 2])
    ta, qa, tb, qb = p[0:3], p[3:7], p[7:10], p[10:14]
    Oa = vadd(ta, qrot(qa, v(0))); Da = qrot(qa, v(3)); Ua = qrot(qa, v(6)); Wa = qrot(qa, v(9))
    Ob = vadd(tb, qrot(qb, v(12))); Db = qrot(qb, v(15))
    return Oa, Da, Ua, Wa, Ob, Db, c[:, 18], c[:, 19]


def _parallel(p, c):
    Oa, Da, Ua, Wa, Ob, Db, s, off = _frames(p, c)
    return [dot(Db, Ua), dot(Db, Wa)]


def _perpendicular(p, c):
    Oa, Da, Ua, Wa, Ob, Db, s, off = _frames(p, c)
    return [dot(Da, Db)]


def _angle(p, c):                       # off = cos(角度)
    Oa, Da, Ua, Wa, Ob, Db, s, off = _frames(p, c)
    return [dot(Da, Db) - off]


def _concentric(p, c):
    Oa, Da, Ua, Wa, Ob, Db, s, off = _frames(p, c)
    d = vsub(Ob, Oa)
    return [dot(Db, Ua), dot(Db, Wa), dot(d, Ua), dot(d, Wa)]


def _plane_plane(p, c):
    Oa, Da, Ua, Wa, Ob, Db, s, off = _frames(p, c)
    return [dot(Db, Ua), dot(Db, Wa), dot(vsub(Ob, Oa), Da) - off]


def _plane_point(p, c):
    Oa, Da, Ua, Wa, Ob, Db, s, off = _frames(p, c)
    return [dot(vsub(Ob, Oa), Da) - off]


def _plane_axis(p, c):                  # 軸が平面内（または平面から off の距離で平行）
    Oa, Da, Ua, Wa, Ob, Db, s, off = _frames(p, c)
    return [dot(Db, Da), dot(vsub(Ob, Oa), Da) - off]


def _axis_point(p, c):
    Oa, Da, Ua, Wa, Ob, Db, s, off = _frames(p, c)
    d = vsub(Ob, Oa)
    return [dot(d, Ua), dot(d, Wa)]


def _point_point(p, c):
    Oa, Da, Ua, Wa, Ob, Db, s, off = _frames(p, c)
    return list(vsub(Ob, Oa))


def _dist_pp(p, c):
    Oa, Da, Ua, Wa, Ob, Db, s, off = _frames(p, c)
    d = vsub(Ob, Oa)
    return [sqrt(dot(d, d)) - off]


def _axis_point_dist(p, c):             # 点と軸の距離 = off
    Oa, Da, Ua, Wa, Ob, Db, s, off = _frames(p, c)
    d = vsub(Ob, Oa)
    a, b = dot(d, Ua), dot(d, Wa)
    return [sqrt(a * a + b * b) - off]


def _axis_axis_dist(p, c):              # 平行な 2 軸の距離 = off
    Oa, Da, Ua, Wa, Ob, Db, s, off = _frames(p, c)
    return [dot(Db, Ua), dot(Db, Wa)] + _axis_point_dist(p, c)


def _lock(p, c):
    """相対姿勢を固定する。定数: A から見た B の位置 (3)、A から見た B の x,y,z 軸 (9)。"""
    v = lambda i: (c[:, i], c[:, i + 1], c[:, i + 2])
    ta, qa, tb, qb = p[0:3], p[3:7], p[7:10], p[10:14]
    pos = vsub(vadd(ta, qrot(qa, v(0))), tb)
    Xa, Ya, Za = qrot(qa, v(3)), qrot(qa, v(6)), qrot(qa, v(9))
    Xb, Yb, Zb = qrot(qb, (1.0, 0.0, 0.0)), qrot(qb, (0.0, 1.0, 0.0)), qrot(qb, (0.0, 0.0, 1.0))
    return [*pos, dot(Xa, Yb), dot(Ya, Zb), dot(Za, Xb)]


QUAT_NORM = Kind("quat_norm", lambda p, c: [p[0] * p[0] + p[1] * p[1] + p[2] * p[2] + p[3] * p[3] - 1.0], 1)
PARALLEL = Kind("parallel", _parallel, 2)
PERPENDICULAR = Kind("perpendicular", _perpendicular, 1)
ANGLE = Kind("angle", _angle, 1)
CONCENTRIC = Kind("concentric", _concentric, 4)
PLANE_PLANE = Kind("plane_plane", _plane_plane, 3)
PLANE_POINT = Kind("plane_point", _plane_point, 1)
PLANE_AXIS = Kind("plane_axis", _plane_axis, 2)
AXIS_POINT = Kind("axis_point", _axis_point, 2)
POINT_POINT = Kind("point_point", _point_point, 3)
DIST_PP = Kind("dist_pp", _dist_pp, 1)
AXIS_POINT_DIST = Kind("axis_point_dist", _axis_point_dist, 1)
AXIS_AXIS_DIST = Kind("axis_axis_dist", _axis_axis_dist, 3)
LOCK = Kind("lock", _lock, 6)
