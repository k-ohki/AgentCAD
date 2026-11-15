"""投影図の計算（D60）: パートの表示メッシュを平行投影し、見える線と隠れた線（隠線）に分ける。

入力は workspace.display_mesh() のメッシュ（三角形と、名前の付いたエッジの線分）。カーネルに投影の機能が要らない。
  1. 線の候補 = 名前の付いたエッジ（継ぎ目は除く）+ 輪郭線（なめらかな面の中で、手前向きと向こう向きの三角形の境目）
  2. 各線分を細かく区切り、区切りごとに「手前に三角形があるか」を調べる（三角形は 2D の格子に入れて近くだけ見る）。
     見える / 隠れるの変わり目は二分法で詰める
  3. 寸法のスナップ先（頂点・中点・円の中心）と、エッジごとの形（直線 / 正面を向いた円・円弧）を付ける
座標はビューの座標（右 R・上 U、単位はモデルの mm。原点 = ワールドの原点の投影）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..geom import chain, fit_circle, is_closed, is_straight

_S2, _S3, _S6 = math.sqrt(2), math.sqrt(3), math.sqrt(6)
#: 向きの名前 → (右, 上)。手前（見る人の側）は 右 × 上。第三角法の配置に合わせる（平面図は奥が上）
ORIENTS = {
    "front": ((1, 0, 0), (0, 1, 0)), "back": ((-1, 0, 0), (0, 1, 0)),
    "top": ((1, 0, 0), (0, 0, -1)), "bottom": ((1, 0, 0), (0, 0, 1)),
    "right": ((0, 0, -1), (0, 1, 0)), "left": ((0, 0, 1), (0, 1, 0)),
    "iso": ((1 / _S2, 0, -1 / _S2), (-1 / _S6, 2 / _S6, -1 / _S6)),
}
ORIENT_LABELS = {"front": "正面図", "back": "背面図", "top": "平面図", "bottom": "下面図", "right": "右側面図",
                 "left": "左側面図", "iso": "等角投影図"}


@dataclass
class ViewGeometry:
    segs: np.ndarray                    # (k, 4) x1, y1, x2, y2
    hidden: np.ndarray                  # (k,) 隠線か
    edge: np.ndarray                    # (k,) 元のエッジの番号（輪郭線は -1）
    bbox: tuple[float, float, float, float]          # 見える線の外接箱 x0, y0, x1, y1
    #: エッジごとの形: {index, name, kind: "line"|"circle"|"arc"|"curve", p1, p2 | c, r, a0, a1（度）, whole: 全部見えているか}
    edges: list[dict] = field(default_factory=list)
    #: スナップ先: {src（"edge:名前@vertex|mid|center"）, w（ワールド座標）, p（ビュー座標）, kind}
    snaps: list[dict] = field(default_factory=list)


class _Occluders:
    """三角形を 2D の格子に入れておき、点が手前の三角形に隠れるかを調べる。"""

    def __init__(self, tri2: np.ndarray, triz: np.ndarray, eps: float, cells: int = 48):
        a, b, c = tri2[:, 0], tri2[:, 1], tri2[:, 2]
        det = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])
        ok = np.abs(det) > 1e-12 * max(1.0, float(np.abs(tri2).max()) ** 2)       # 視線に平行な三角形は隠さない
        self.a, self.b, self.c, self.z, self.det = a[ok], b[ok], c[ok], triz[ok], det[ok]
        self.eps = eps
        lo = tri2.reshape(-1, 2).min(0) if len(tri2) else np.zeros(2)
        hi = tri2.reshape(-1, 2).max(0) if len(tri2) else np.ones(2)
        self.lo, self.n = lo, cells
        self.h = np.maximum((hi - lo) / cells, 1e-12)
        self.cell: dict[int, list[int]] = {}
        t2 = np.stack([self.a, self.b, self.c], 1)
        i0 = np.clip(((t2.min(1) - lo) / self.h).astype(int), 0, cells - 1)
        i1 = np.clip(((t2.max(1) - lo) / self.h).astype(int), 0, cells - 1)
        for t in range(len(t2)):
            for ix in range(i0[t, 0], i1[t, 0] + 1):
                for iy in range(i0[t, 1], i1[t, 1] + 1):
                    self.cell.setdefault(ix * cells + iy, []).append(t)
        self.cell = {k: np.asarray(v) for k, v in self.cell.items()}

    def hidden(self, p: np.ndarray, z: np.ndarray) -> np.ndarray:
        """点 p (m, 2)・奥行き z (m,)（大きいほど手前）が、手前の三角形に隠れるか。"""
        out = np.zeros(len(p), bool)
        if not len(p) or not len(self.a):
            return out
        ij = ((p - self.lo) / self.h).astype(int)
        inside = np.all((ij >= 0) & (ij < self.n), axis=1)
        key = ij[:, 0] * self.n + ij[:, 1]
        order = np.argsort(key, kind="stable")
        bounds = np.flatnonzero(np.diff(key[order])) + 1
        for grp in np.split(order, bounds):
            k = int(key[grp[0]])
            T = self.cell.get(k)
            grp = grp[inside[grp]]
            if T is None or not len(grp):
                continue
            a, b, c, det, tz = self.a[T], self.b[T], self.c[T], self.det[T], self.z[T]
            q = p[grp][:, None, :]
            w1 = ((q[..., 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (q[..., 1] - a[:, 1])) / det
            w2 = ((b[:, 0] - a[:, 0]) * (q[..., 1] - a[:, 1]) - (q[..., 0] - a[:, 0]) * (b[:, 1] - a[:, 1])) / det
            w0 = 1 - w1 - w2
            zt = w0 * tz[:, 0] + w1 * tz[:, 1] + w2 * tz[:, 2]
            hit = (w0 > 1e-9) & (w1 > 1e-9) & (w2 > 1e-9) & (zt > z[grp][:, None] + self.eps)
            out[grp] = hit.any(1)
        return out


def _silhouette(pos: np.ndarray, tri: np.ndarray, face: np.ndarray, n: np.ndarray, size: float) -> np.ndarray:
    """輪郭線: 同じ面の中で隣り合う三角形の、手前向きと向こう向きの境目の辺。(k, 2, 3)。"""
    T = pos[tri]
    nrm = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    front = nrm @ n > 1e-9 * np.maximum(np.linalg.norm(nrm, axis=1), 1e-300)
    q = np.round(pos / (1e-6 * size)).astype(np.int64)
    _, vid = np.unique(q, axis=0, return_inverse=True)
    vid = vid.reshape(-1)
    seen: dict[tuple[int, int, int], int] = {}
    out = []
    for t in range(len(tri)):
        for k in range(3):
            i, j = int(vid[tri[t, k]]), int(vid[tri[t, (k + 1) % 3]])
            key = (min(i, j), max(i, j), int(face[t]))
            o = seen.pop(key, None)
            if o is None:
                seen[key] = t
            elif front[o] != front[t]:
                out.append((pos[tri[t, k]], pos[tri[t, (k + 1) % 3]]))
    return np.asarray(out, float).reshape(-1, 2, 3)


def project_view(mesh: dict, R, U) -> ViewGeometry:
    """メッシュを（右 R・上 U）の向きで投影する。"""
    R, U = np.asarray(R, float), np.asarray(U, float)
    N = np.cross(R, U)
    pos = np.asarray(mesh["positions"], float).reshape(-1, 3)
    tri = np.asarray(mesh["indices"], int).reshape(-1, 3)
    size = max(float(np.ptp(pos, axis=0).max()) if len(pos) else 1.0, 1e-9)
    F = np.stack([R, U, N], 1)                                  # ワールド → ビュー（x, y, 奥行き）
    V = pos @ F
    occ = _Occluders(V[tri][:, :, :2], V[tri][:, :, 2], eps=1e-4 * size)

    E = np.asarray(mesh["edges"], float).reshape(-1, 2, 3)
    owner = np.asarray(mesh["edge_of_segment"], int).reshape(-1)
    faces = mesh.get("edge_faces") or []
    seam = np.array([len(f) == 2 and f[0] == f[1] and f[0] >= 0 for f in faces] + [False] * (int(owner.max(initial=-1)) + 1 - len(faces)))
    keep = ~seam[owner] if len(owner) else np.zeros(0, bool)    # 円筒などの継ぎ目は描かない
    sil = _silhouette(pos, tri, np.asarray(mesh["face_of_triangle"], int).reshape(-1), N, size)
    S3 = np.concatenate([E[keep], sil]) if len(sil) else E[keep]
    src = np.concatenate([owner[keep], np.full(len(sil), -1)]) if len(sil) else owner[keep]
    A, B = S3[:, 0] @ F, S3[:, 1] @ F
    L = np.linalg.norm(B[:, :2] - A[:, :2], axis=1)
    ok = L > 1e-7 * size                                        # 視線に平行な線は点になる
    A, B, L, src = A[ok], B[ok], L[ok], src[ok]

    # 区切りごとの見え方
    h = size / 200
    cnt = np.clip(np.ceil(L / h).astype(int), 1, 64)
    seg_of = np.repeat(np.arange(len(A)), cnt)
    first = np.concatenate([[0], np.cumsum(cnt)[:-1]])
    k = np.arange(len(seg_of)) - first[seg_of]
    t_mid = (k + 0.5) / cnt[seg_of]
    P = A[seg_of] + (B[seg_of] - A[seg_of]) * t_mid[:, None]
    hid = occ.hidden(P[:, :2], P[:, 2])
    # 変わり目（同じ線分の中で隣の区切りと見え方が違う所）を二分法で詰める
    change = np.flatnonzero((seg_of[1:] == seg_of[:-1]) & (hid[1:] != hid[:-1]))
    t_lo, t_hi = t_mid[change].copy(), t_mid[change + 1].copy()
    s = seg_of[change]
    for _ in range(7):
        tm = (t_lo + t_hi) / 2
        Pm = A[s] + (B[s] - A[s]) * tm[:, None]
        same = occ.hidden(Pm[:, :2], Pm[:, 2]) == hid[change]
        t_lo, t_hi = np.where(same, tm, t_lo), np.where(same, t_hi, tm)
    cut = dict(zip(change.tolist(), ((t_lo + t_hi) / 2).tolist()))
    segs, hidden, edge = [], [], []
    i = 0
    for si in range(len(A)):
        n = int(cnt[si])
        t0 = 0.0
        for j in range(i, i + n):
            last = j == i + n - 1
            if last or j in cut:
                t1 = 1.0 if last else cut[j]
                p, q = A[si] + (B[si] - A[si]) * t0, A[si] + (B[si] - A[si]) * t1
                segs.append((p[0], p[1], q[0], q[1])); hidden.append(bool(hid[j])); edge.append(int(src[si]))
                t0 = t1
        i += n
    segs = np.asarray(segs, float).reshape(-1, 4)
    hidden, edge = np.asarray(hidden, bool), np.asarray(edge, int)
    # 重なった線を除く（見える線どうしの重複、見える線と同じ位置の隠線）
    q = np.round(segs / (1e-6 * size)).astype(np.int64)
    key = [tuple(sorted(((a, b), (c, d)))) for a, b, c, d in q.tolist()]
    vis_keys, keep_i = set(), []
    for idx in np.argsort(hidden, kind="stable"):               # 見える線を先に
        kk = key[idx]
        if kk in vis_keys or (kk[0] == kk[1]):
            continue
        vis_keys.add(kk)
        keep_i.append(idx)
    keep_i = np.sort(np.asarray(keep_i, int))
    segs, hidden, edge = segs[keep_i], hidden[keep_i], edge[keep_i]
    vis = segs[~hidden] if (~hidden).any() else segs
    if len(vis):
        xs, ys = vis[:, [0, 2]], vis[:, [1, 3]]
        bbox = (float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))
    else:
        bbox = (0.0, 0.0, 0.0, 0.0)
    g = ViewGeometry(segs, hidden, edge, bbox)
    _describe_edges(g, mesh, E, owner, seam, F, N)
    return g


def edge_polylines(mesh: dict) -> dict[int, np.ndarray]:
    """エッジの番号 → つないだ折れ線（ワールド座標）。"""
    E = np.asarray(mesh["edges"], float).reshape(-1, 2, 3)
    owner = np.asarray(mesh["edge_of_segment"], int).reshape(-1)
    out = {}
    for i in np.unique(owner).tolist():
        try:
            out[i] = chain(E[owner == i])
        except ValueError:
            pass
    return out


def _describe_edges(g: ViewGeometry, mesh: dict, E, owner, seam, F, N) -> None:
    names = mesh["edge_names"]
    seen = set()
    all_hidden = {i: bool(g.hidden[g.edge == i].all()) for i in np.unique(g.edge).tolist() if i >= 0}
    whole = {i: not g.hidden[g.edge == i].any() for i in all_hidden}

    def snap(kind, name, w):
        p = np.asarray(w) @ F
        k = (kind, round(float(p[0]), 6), round(float(p[1]), 6))
        if k not in seen:
            seen.add(k)
            g.snaps.append({"src": f"edge:{name}@{kind}", "kind": kind, "w": [float(v) for v in w], "p": [float(p[0]), float(p[1])]})

    for i, P in edge_polylines(mesh).items():
        if i < len(seam) and seam[i] or names[i] == "?":
            continue
        if all_hidden.get(i, True) and i in all_hidden:          # 全部隠れているエッジには付けない
            continue
        Q = P @ F
        d = {"index": i, "name": names[i], "whole": whole.get(i, False), "kind": "curve"}
        if not is_closed(P):
            snap("vertex", names[i], P[0]); snap("vertex", names[i], P[-1])
        if is_straight(P):
            snap("mid", names[i], (P[0] + P[-1]) / 2)
            d.update(kind="line", p1=Q[0, :2].tolist(), p2=Q[-1, :2].tolist())
        else:
            c = fit_circle(P)
            if c is not None:
                C, r, nrm = c
                snap("center", names[i], C)
                if abs(float(nrm @ N)) > 1 - 1e-9:               # 正面を向いた円・円弧: そのままの形で写る
                    c2 = (C @ F)[:2]
                    if is_closed(P):
                        d.update(kind="circle", c=c2.tolist(), r=r)
                    else:
                        ang = np.degrees(np.arctan2(Q[:, 1] - c2[1], Q[:, 0] - c2[0]))
                        a0, a1, am = float(ang[0]), float(ang[-1]), float(ang[len(ang) // 2])
                        if not ((am - a0) % 360 < (a1 - a0) % 360):   # 中ほどの点が a0 → a1（反時計回り）に無ければ逆
                            a0, a1 = a1, a0
                        d.update(kind="arc", c=c2.tolist(), r=r, a0=a0, a1=a1)
        g.edges.append(d)
