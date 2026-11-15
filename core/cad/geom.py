"""小さな幾何の道具（エッジの折れ線・円の当てはめ）。スケッチの外部参照（D54）と図面（D60）で使う。"""
from __future__ import annotations

import numpy as np


def chain(segments) -> np.ndarray:
    """エッジの線分（(k, 2, 3)）をつないだ折れ線の点列。順番どおりでなくても、つながる線分を探してつなぐ。

    最初の線分から始め、末尾にいちばん近い線分（同じ距離なら番号の小さいもの）を順に足す。端点がちょうど一致する線分は
    表で引き（テッセレーションの線分は端点を共有する）、無いときだけ全部から探す。"""
    S = np.asarray(segments, float).reshape(-1, 2, 3)
    if not len(S):
        raise ValueError("エッジの形を取れません")
    n = len(S)
    T = S.tolist()
    ends: dict[tuple, list[int]] = {}                 # 端点 → その端点を持つ線分（番号の小さい順）
    for j in range(1, n):
        ends.setdefault(tuple(T[j][0]), []).append(j)
        if T[j][1] != T[j][0]:
            ends.setdefault(tuple(T[j][1]), []).append(j)
    alive = np.ones(n, bool)
    alive[0] = False
    out = [S[0, 0], S[0, 1]]
    for _ in range(n - 1):
        tail = out[-1]
        j = None
        lst = ends.get(tuple(tail.tolist()))
        if lst:
            while lst and not alive[lst[0]]:
                lst.pop(0)
            if lst:
                j = lst[0]
        if j is None:
            idx = np.flatnonzero(alive)
            d = np.minimum(np.linalg.norm(S[idx, 0] - tail, axis=1), np.linalg.norm(S[idx, 1] - tail, axis=1))
            j = int(idx[int(np.argmin(d))])
        alive[j] = False
        a, b = S[j]
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
