"""小さな幾何の道具（エッジの折れ線・円の当てはめ）。スケッチの外部参照（D54）と図面（D60）で使う。"""
from __future__ import annotations

import numpy as np


def chain(segments) -> np.ndarray:
    """エッジの線分（(k, 2, 3)）をつないだ折れ線の点列。順番どおりでなくても、つながる線分を探してつなぐ。"""
    S = np.asarray(segments, float).reshape(-1, 2, 3)
    if not len(S):
        raise ValueError("エッジの形を取れません")
    out = [S[0, 0], S[0, 1]]
    rest = list(S[1:])
    while rest:
        tail = out[-1]
        i = min(range(len(rest)), key=lambda j: min(np.linalg.norm(rest[j][0] - tail), np.linalg.norm(rest[j][1] - tail)))
        a, b = rest.pop(i)
        out.append(b if np.linalg.norm(a - tail) <= np.linalg.norm(b - tail) else a)
    return np.asarray(out)


def circumcenter(a, b, c):
    """3D の 3 点を通る円の中心（一直線なら None）。"""
    ab, ac = b - a, c - a
    n = np.cross(ab, ac)
    nn = float(n @ n)
    if nn < 1e-18:
        return None
    return a + (np.cross(n, ab) * float(ac @ ac) + np.cross(ac, n) * float(ab @ ab)) / (2 * nn)


def is_closed(P: np.ndarray) -> bool:
    return len(P) > 2 and float(np.linalg.norm(P[0] - P[-1])) < 1e-7 * max(1.0, float(np.abs(P).max()))


def is_straight(P: np.ndarray) -> bool:
    d = P[-1] - P[0]
    L = float(np.linalg.norm(d))
    if is_closed(P) or L < 1e-9:
        return False
    return max(float(np.linalg.norm(np.cross(p - P[0], d))) / L for p in P) <= 1e-6 * max(1.0, L)


def fit_circle(P: np.ndarray):
    """折れ線 P が円・円弧なら (中心, 半径, 法線)、そうでなければ None。"""
    closed = is_closed(P)
    n = len(P) - (1 if closed else 0)
    if n < 3:
        return None
    C = circumcenter(P[0], P[n // 3], P[(2 * n) // 3])
    if C is None:
        return None
    r = np.linalg.norm(P - C, axis=1)
    if float(r.max() - r.min()) > 1e-3 * max(float(r.mean()), 1e-9):
        return None
    nrm = np.cross(P[n // 3] - P[0], P[(2 * n) // 3] - P[0])
    return C, float(r.mean()), nrm / np.linalg.norm(nrm)
