"""モデルのエッジ（折れ線）をスケッチ平面に投影し、線・円・円弧に当てはめる（エンティティ変換）。"""
from __future__ import annotations

import numpy as np


def project(segs, plane) -> np.ndarray:
    """線分の配列 (k, 2, 3)（世界座標）→ スケッチ平面の (k, 2, 2)。"""
    s = np.asarray(segs, float).reshape(-1, 2, 3) - np.asarray(plane.origin, float)
    return np.stack([s @ np.asarray(plane.x_dir, float), s @ np.asarray(plane.y_dir, float)], -1)


def chain(segs2d: np.ndarray, tol: float) -> tuple[np.ndarray, bool]:
    """線分をつないだ点列と、閉じているか。線分の向き・順番はばらばらでもよい。"""
    pts: list[np.ndarray] = []

    def node(p):
        for i, q in enumerate(pts):
            if np.hypot(*(p - q)) <= tol:
                return i
        pts.append(p)
        return len(pts) - 1
    adj: dict[int, list[int]] = {}
    for a, b in segs2d:
        i, j = node(a), node(b)
        if i == j:
            continue
        adj.setdefault(i, []).append(j); adj.setdefault(j, []).append(i)
    if not adj:
        return np.zeros((0, 2)), False
    ends = [i for i, n in adj.items() if len(n) == 1]
    if any(len(n) > 2 for n in adj.values()) or len(ends) not in (0, 2):
        raise ValueError("エッジが 1 本につながっていません")
    start = ends[0] if ends else next(iter(adj))
    order, prev, cur = [start], None, start
    while True:
        nxt = [n for n in adj[cur] if n != prev]
        if not nxt or nxt[0] == start:
            break
        prev, cur = cur, nxt[0]
        order.append(cur)
    if len(order) != len(adj):
        raise ValueError("エッジが 1 本につながっていません")
    return np.array([pts[i] for i in order]), not ends


def fit(segs2d: np.ndarray) -> tuple[str, list[float]]:
    """投影した折れ線 → ("line", [x1,y1,x2,y2]) / ("circle", [cx,cy,r]) / ("arc", [cx,cy,sx,sy,ex,ey])（反時計回り）。
    直線・円弧に乗らなければ ValueError（自由曲線や、傾いた面の円 → 楕円）。"""
    flat = segs2d.reshape(-1, 2)
    size = float(np.ptp(flat, 0).max()) if len(flat) else 0.0
    if size < 1e-9:
        raise ValueError("スケッチ平面に垂直なエッジです（投影すると点になります）")
    tol = 1e-4 * max(size, 1.0)
    P, closed = chain(segs2d, tol)
    if not closed:
        a, b = P[0], P[-1]
        d = b - a
        L = float(np.hypot(*d))
        if L > tol:
            dev = np.abs((P[:, 0] - a[0]) * d[1] - (P[:, 1] - a[1]) * d[0]) / L
            if dev.max() <= tol:
                return "line", [*a, *b]
    if len(P) >= 3:
        A = np.c_[P, np.ones(len(P))]
        D, E, F = np.linalg.lstsq(A, -(P ** 2).sum(1), rcond=None)[0]
        c = np.array([-D / 2, -E / 2])
        dist = np.hypot(*(P - c).T)
        # 表示用の折れ線は弦の中点も含む（円の内側に弦の高さ分ずれる）ので、半径は外側の点で取り、
        # ずれは弦の高さ（32 分割以上なら 0.5% 未満）まで許す
        r = float(dist.max())
        if r > tol and r - dist.min() <= max(tol, 5e-3 * r):
            if closed:
                return "circle", [*c, r]
            v = P - c
            turn = float(np.sum(v[:-1, 0] * v[1:, 1] - v[:-1, 1] * v[1:, 0]))
            s, e = (P[0], P[-1]) if turn > 0 else (P[-1], P[0])
            return "arc", [*c, *s, *e]
    raise ValueError("直線・円・円弧ではないエッジは変換できません（傾いた面の円は楕円になります）")
