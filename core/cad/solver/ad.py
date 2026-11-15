"""バッチ化した前進型自動微分（dual number）。

同じ種類の拘束 B 個をまとめて評価する。D は
  v: 値 (B,)      g: 局所変数に対する勾配 (B, P)
を持ち、四則演算と sqrt / atan2 を定義する。残差関数を1回書けば
正確なヤコビアンが得られ、評価コストは拘束数にほぼ依存しない（numpy 側でループ）。
"""
from __future__ import annotations

import numpy as np


def _col(o):
    """D でない相手（スカラー or (B,) 配列）を勾配に掛けられる形にする。"""
    return o[:, None] if isinstance(o, np.ndarray) and o.ndim else o


class D:
    __slots__ = ("v", "g")
    __array_ufunc__ = None          # ndarray * D で D.__rmul__ が呼ばれるようにする

    def __init__(self, v, g):
        self.v, self.g = v, g

    def __add__(self, o):
        return D(self.v + o.v, self.g + o.g) if isinstance(o, D) else D(self.v + o, self.g)
    __radd__ = __add__

    def __sub__(self, o):
        return D(self.v - o.v, self.g - o.g) if isinstance(o, D) else D(self.v - o, self.g)

    def __rsub__(self, o):
        return D(o - self.v, -self.g)

    def __neg__(self):
        return D(-self.v, -self.g)

    def __mul__(self, o):
        if isinstance(o, D):
            return D(self.v * o.v, self.g * o.v[:, None] + o.g * self.v[:, None])
        return D(self.v * o, self.g * _col(o))
    __rmul__ = __mul__

    def __truediv__(self, o):
        if isinstance(o, D):
            inv = 1.0 / o.v
            q = self.v * inv
            return D(q, (self.g - o.g * q[:, None]) * inv[:, None])
        return D(self.v / o, self.g / _col(o))

    def __rtruediv__(self, o):
        q = o / self.v
        return D(q, -self.g * (q / self.v)[:, None])


def sqrt(a: D) -> D:
    r = np.sqrt(np.maximum(a.v, 1e-300))
    return D(r, a.g * (0.5 / r)[:, None])


def atan2(y: D, x: D) -> D:
    d = np.maximum(x.v * x.v + y.v * y.v, 1e-300)
    return D(np.arctan2(y.v, x.v), (y.g * x.v[:, None] - x.g * y.v[:, None]) / d[:, None])


# ---- 3D ベクトル（D または (B,) 配列の3要素タプル） ----

def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def vadd(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vsub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def qrot(q, v):
    """四元数 q=(w,x,y,z) で v を回転。q は正規化されていなくてよい（内部で |q|² で割る）。"""
    w, qv = q[0], (q[1], q[2], q[3])
    n = w * w + dot(qv, qv)
    t = cross(qv, v)
    u = cross(qv, t)
    k = 2.0 / n
    return (v[0] + k * (w * t[0] + u[0]), v[1] + k * (w * t[1] + u[1]), v[2] + k * (w * t[2] + u[2]))
