"""面ごとのメッシュから、位相（エッジと隣接面）と履歴（各面の由来）を導く。カーネル非依存。

背景（docs/PHASE0.md）: usd-brep には「演算結果の面がどの入力から来たか」を返す API が無い。
そこで、カーネルには「面ごとの三角形メッシュ」だけを求め、残りは形状から逆算する。

- 押し出し・回転: 結果の面の点を、スケッチ平面の座標に戻して判定する
  （端面: 高さ 0 / 距離 / 側面: どのスケッチ要素の上にあるか）
- ブーリアン・フィレット: 結果の面の点が、入力のどの面の上にあるかで判定する。
  どの入力面にも乗らない面はフィレット面（最寄りの対象エッジに対応づける）
- エッジ: 面メッシュの境界線分のうち、別の面に接しているもの。同じ 2 面の間で連結な線分の集まりを 1 本とする
- 平面: 面メッシュの頂点に平面を当てはめる（誤差が許容値以内なら平面）

判定はすべて「点と面メッシュの距離 < 許容値」で行う。許容値はメッシュ化の弦誤差より大きく、
区別すべき形状の差より小さくする（MeshTopoKernel.tolerance）。
"""
from __future__ import annotations

import math
import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from .base import Kernel, KernelError, Mesh, OpResult, Plane, Profile, Segment

# ---------------------------------------------------------------- 面メッシュ


@dataclass
class FaceMesh:
    pos: np.ndarray            # (n,3)
    tri: np.ndarray            # (m,3) 外向きに反時計回り

    def __post_init__(self):
        self.pos = np.asarray(self.pos, float).reshape(-1, 3)
        self.tri = np.asarray(self.tri, np.int64).reshape(-1, 3)

    # T・areas・距離計算の下ごしらえは覚えておく（面メッシュは作ったあと変わらない。D65）。返した配列は書き換えないこと
    @property
    def T(self) -> np.ndarray:
        t = self.__dict__.get("_T")
        if t is None:
            t = self.__dict__["_T"] = self.pos[self.tri]
        return t

    def areas(self) -> np.ndarray:
        a = self.__dict__.get("_areas")
        if a is None:
            T = self.T
            a = self.__dict__["_areas"] = 0.5 * np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1)
        return a

    def centroid(self) -> np.ndarray:
        a = self.areas()
        c = self.T.mean(axis=1)
        return (c * a[:, None]).sum(0) / a.sum() if a.sum() > 0 else self.pos.mean(0)

    def bbox(self, pad: float = 0.0):
        return self.pos.min(0) - pad, self.pos.max(0) + pad


def weld(fm: FaceMesh, tol: float) -> FaceMesh:
    """近い頂点をまとめ、潰れた三角形を除く（三角形スープでも境界を求められるようにする）。"""
    if len(fm.pos) == 0:
        return fm
    tree = cKDTree(fm.pos)
    rep = np.arange(len(fm.pos))
    for i, j in sorted(tree.query_pairs(tol)):
        ri, rj = rep[i], rep[j]
        while rep[ri] != ri:
            ri = rep[ri]
        while rep[rj] != rj:
            rj = rep[rj]
        if ri != rj:
            rep[max(ri, rj)] = min(ri, rj)
    for i in range(len(rep)):
        r = rep[i]
        while rep[r] != r:
            r = rep[r]
        rep[i] = r
    uniq, inv = np.unique(rep, return_inverse=True)
    tri = inv[fm.tri]
    keep = (tri[:, 0] != tri[:, 1]) & (tri[:, 1] != tri[:, 2]) & (tri[:, 2] != tri[:, 0])
    tri = tri[keep]
    used, inv2 = np.unique(tri, return_inverse=True)          # 三角形に使われない頂点は捨てる（平面判定・範囲を正しく）
    return FaceMesh(fm.pos[uniq][used], inv2.reshape(-1, 3))


def weld_all(faces: list[FaceMesh], tol: float) -> list[FaceMesh]:
    """[weld(f, tol) for f in faces] と同じ結果を速く求める（D65）。全部の面の頂点で KD 木を 1 回だけ作り、同じ面の中に
    近い頂点の組がある面だけ weld する。組が無く、潰れた三角形も使われない頂点も無い面は weld しても配列が変わらないので
    そのまま（同じ値の FaceMesh）にする。"""
    if not faces:
        return []
    counts = [len(f.pos) for f in faces]
    if sum(counts):
        owner = np.repeat(np.arange(len(faces)), counts)
        pairs = cKDTree(np.concatenate([f.pos for f in faces if len(f.pos)])).query_pairs(tol, output_type="ndarray")
        need = set(owner[pairs[:, 0]][owner[pairs[:, 0]] == owner[pairs[:, 1]]].tolist()) if len(pairs) else set()
    else:
        need = set()
    out = []
    for i, f in enumerate(faces):
        t = f.tri
        clean = i not in need and len(f.pos) and not ((t[:, 0] == t[:, 1]) | (t[:, 1] == t[:, 2]) | (t[:, 2] == t[:, 0])).any() \
            and len(np.unique(t)) == len(f.pos)
        out.append(FaceMesh(f.pos, t) if clean else weld(f, tol))
    if os.environ.get("AGENTCAD_CHECK_MATCH"):                    # 検査: 1 面ずつ weld した結果と同じか
        for f, o in zip(faces, out):
            r = weld(f, tol)
            if not (np.array_equal(r.pos, o.pos) and np.array_equal(r.tri, o.tri)):
                raise AssertionError("weld_all が weld と食い違いました")
    return out


def triangles_from_prefixed(faces) -> tuple[np.ndarray, np.ndarray]:
    """[n, i1..in, n, …] 形式の多角形配列 → (三角形 (m,3), 元の多角形番号 (m,))。多角形は扇形に分割。"""
    f = np.asarray(faces, np.int64).ravel()
    tris, owner, o, p = [], [], 0, 0
    while o < len(f):
        n = int(f[o])
        v = f[o + 1:o + 1 + n]
        if n < 3 or len(v) < n:
            raise ValueError(f"多角形配列が壊れています（位置 {o}, 頂点数 {n}）")
        for j in range(1, n - 1):
            tris.append((v[0], v[j], v[j + 1]))
            owner.append(p)
        o += n + 1
        p += 1
    return np.asarray(tris, np.int64).reshape(-1, 3), np.asarray(owner, np.int64)


def samples(fm: FaceMesh, k: int = 7) -> np.ndarray:
    """面の内部の点（面積の大きい三角形の重心）。"""
    idx = np.argsort(-fm.areas(), kind="stable")[:k]
    return fm.T[idx].mean(axis=1)


def samples_many(faces: list[FaceMesh], k: int = 7) -> list[np.ndarray]:
    """[samples(f, k) for f in faces] と同じ値を、全部の面をつないで一度に求める（D65）。
    面積・重心は三角形ごとの計算なので値は同じ。面ごとの「面積の大きい順（同じなら先の三角形）」は、
    （面, −面積, 三角形の番号）の順に並べて先頭 k 個を取るのと同じ。"""
    n = [len(f.tri) for f in faces]
    if not sum(n):
        return [np.zeros((0, 3)) for _ in faces]
    T = np.concatenate([f.T for f in faces if len(f.tri)])
    a = 0.5 * np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1)
    owner = np.repeat(np.arange(len(faces)), n)
    order = np.lexsort((np.arange(len(a)), -a, owner))
    starts = np.concatenate([[0], np.cumsum(n)[:-1]])
    rank = np.empty(len(a), dtype=np.int64)
    rank[order] = np.arange(len(a)) - starts[owner[order]]
    sel = order[rank[order] < k]                                # 面ごとに先頭 k 個（面の順、面積の大きい順）
    C = T[sel].mean(axis=1)
    out, s = [], 0
    for m in n:
        c = min(m, k)
        out.append(C[s:s + c]); s += c
    return out

# ---------------------------------------------------------------- 距離


def _segdist(p, a, b):
    """点 p と線分群 a-b の距離（a, b: (k,3)）。"""
    ab = b - a
    L = np.einsum("ij,ij->i", ab, ab)
    t = np.clip(np.einsum("ij,ij->i", p - a, ab) / np.where(L > 0, L, 1.0), 0.0, 1.0)
    return np.linalg.norm(a + t[:, None] * ab - p, axis=1)


def _cross(u, v):
    return np.stack([u[..., 1] * v[..., 2] - u[..., 2] * v[..., 1],
                     u[..., 2] * v[..., 0] - u[..., 0] * v[..., 2],
                     u[..., 0] * v[..., 1] - u[..., 1] * v[..., 0]], axis=-1)


def _tri_pre(fm: FaceMesh) -> dict:
    """距離計算の下ごしらえ（三角形ごとの頂点・辺・法線）。三角形ごとの計算なので、一部の三角形だけ取り出しても値は同じ。"""
    pre = fm.__dict__.get("_tri_pre")
    if pre is None:
        pre = fm.__dict__["_tri_pre"] = _tri_pre_T(fm.T)
    return pre


def _tri_pre_T(T: np.ndarray) -> dict:
    """_tri_pre の中身（三角形の頂点の配列 (m,3,3) から）。いくつもの面の三角形をつないで一度に計算してもよい。"""
    if True:
        a, b, c = T[:, 0], T[:, 1], T[:, 2]
        ab, bc, ca = b - a, c - b, a - c
        n = _cross(ab, -ca)
        nn = np.einsum("ij,ij->i", n, n)
        ok = nn > 1e-30
        pre = {
            "a": a, "b": b, "c": c, "ab": ab, "bc": bc, "ca": ca, "n": n, "ok": ok,
            "inv": 1.0 / np.sqrt(np.where(ok, nn, 1.0)),
            "e1": _cross(n, ab), "e2": _cross(n, bc), "e3": _cross(n, ca),       # 辺の内向き法線（面内）
            "L0": np.einsum("ij,ij->i", ab, ab), "L1": np.einsum("ij,ij->i", bc, bc), "L2": np.einsum("ij,ij->i", ca, ca)}
    return pre


def dist_points_mesh(P, fm: FaceMesh, chunk: int = 1 << 21, keep: np.ndarray | None = None) -> np.ndarray:
    """複数の点と面メッシュの距離（点ごとの最小値）。点 × 三角形をまとめて計算する。keep で三角形を絞れる（真偽の配列）。"""
    P = np.atleast_2d(np.asarray(P, float))
    if len(fm.tri) == 0:
        return np.full(len(P), math.inf)
    if keep is None:
        pre = _tri_pre(fm)                       # 全部の三角形: 下ごしらえを覚えておく
    else:                                        # 一部の三角形: その分だけ下ごしらえする（三角形ごとの計算なので値は同じ）
        if not keep.any():
            return np.full(len(P), math.inf)
        sub = FaceMesh.__new__(FaceMesh)
        sub.pos, sub.tri = fm.pos, fm.tri[keep]
        pre = _tri_pre(sub)
    a, b, c, ab, bc, ca, n, ok, inv, e1, e2, e3 = (pre[k] for k in ("a", "b", "c", "ab", "bc", "ca", "n", "ok", "inv",
                                                                  "e1", "e2", "e3"))
    L = [pre["L0"], pre["L1"], pre["L2"]]
    T = a
    out = np.empty(len(P))
    step = max(1, chunk // max(1, len(T)))
    for s0 in range(0, len(P), step):
        Q = P[s0:s0 + step, None, :]                                # (p,1,3)
        ap, bp, cp = Q - a, Q - b, Q - c                            # (p,m,3)
        inside = ok & (np.einsum("pmk,mk->pm", ap, e1) >= 0) & (np.einsum("pmk,mk->pm", bp, e2) >= 0) \
            & (np.einsum("pmk,mk->pm", cp, e3) >= 0)
        dplane = np.abs(np.einsum("pmk,mk->pm", ap, n)) * inv
        dedge = None
        for o, e, l in ((ap, ab, L[0]), (bp, bc, L[1]), (cp, ca, L[2])):
            t = np.clip(np.einsum("pmk,mk->pm", o, e) / np.where(l > 0, l, 1.0), 0.0, 1.0)
            d = np.linalg.norm(o - t[..., None] * e, axis=-1)
            dedge = d if dedge is None else np.minimum(dedge, d)
        out[s0:s0 + step] = np.where(inside, dplane, dedge).min(axis=1)
    return out


def dist_point_mesh(p, fm: FaceMesh) -> float:
    return float(dist_points_mesh(np.asarray(p, float)[None], fm)[0])


# ---------------------------------------------------------------- 平面


def fit_plane(fm: FaceMesh, tol: float) -> Plane | None:
    """頂点がすべて 1 平面上（誤差 tol 以内）なら Plane。法線は三角形の向き（外向き）に合わせる。"""
    if len(fm.tri) == 0:
        return None
    c = fm.centroid()
    _, _, vt = np.linalg.svd(fm.pos - c)
    n = vt[2]
    if np.abs((fm.pos - c) @ n).max() > tol:
        return None
    T = fm.T
    if np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]).sum(0) @ n < 0:
        n = -n
    return plane_frame(c, n)


def plane_frame(point, normal) -> Plane:
    """面上スケッチ用の座標系。原点 = 部品の原点を面へ投影した点、x = 世界の X を面へ投影した向き
    （X が法線に近ければ Y）。面の形（重心）に依存しないので、寸法を変えてもスケッチがずれない。"""
    n = np.asarray(normal, float); n = n / np.linalg.norm(n)
    o = (np.asarray(point, float) @ n) * n
    ref = np.array([1.0, 0, 0]) if abs(n[0]) < 0.9 else np.array([0, 1.0, 0])
    x = ref - (ref @ n) * n
    x /= np.linalg.norm(x)
    return Plane(tuple(o.tolist()), tuple(x.tolist()), tuple(n.tolist()))

# ---------------------------------------------------------------- エッジ（隣接）


@dataclass
class TopoEdge:
    fa: int
    fb: int
    segs: np.ndarray           # (k,2,3)
    mid: np.ndarray            # エッジ上の代表点


def boundary_segments(fm: FaceMesh) -> np.ndarray:
    e = fm.tri[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)
    key = np.sort(e, axis=1)
    _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    b = e[cnt[inv.ravel()] == 1]
    return fm.pos[b]


def _chains(segs: np.ndarray, tol: float) -> list[np.ndarray]:
    """線分を端点のつながりで連結成分に分ける。"""
    k = len(segs)
    pts = segs.reshape(-1, 3)
    parent = list(range(k))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, j in cKDTree(pts).query_pairs(tol):
        a, b = find(i // 2), find(j // 2)
        if a != b:
            parent[a] = b
    groups: dict[int, list[int]] = {}
    for i in range(k):
        groups.setdefault(find(i), []).append(i)
    return [segs[g] for g in groups.values()]


def _subdivide(segs: np.ndarray, maxlen: float) -> np.ndarray:
    """長い線分を maxlen 以下に分割する。隣の面のメッシュと頂点がそろっていない（T 字の継ぎ目）
    場合でも、線分の中点で隣接面を判定できるようにするため。"""
    out = []
    for a, b in segs:
        n = int(min(1000, max(1, math.ceil(np.linalg.norm(b - a) / maxlen))))
        t = np.linspace(0, 1, n + 1)[:, None]
        p = a + t * (b - a)
        out.append(np.stack([p[:-1], p[1:]], axis=1))
    return np.concatenate(out) if out else segs


def adjacency(faces: list[FaceMesh], tol: float, seg_rel: float = 0.02) -> list[TopoEdge]:
    """面メッシュ群からエッジを求める。各エッジは番号の小さい面の境界から 1 回だけ作る。
    境界線分はモデル寸法 × seg_rel 以下に分割してから判定する。"""
    if not faces:
        return []
    allp = np.vstack([f.pos for f in faces])
    maxlen = max(seg_rel * float(np.linalg.norm(allp.max(0) - allp.min(0))), 4 * tol)
    boxes = [f.bbox(tol) for f in faces]
    out: list[TopoEdge] = []
    for a, fa in enumerate(faces):
        segs = boundary_segments(fa)
        if len(segs):
            segs = _subdivide(segs, maxlen)
        if not len(segs):
            continue
        mids = segs.mean(axis=1)
        best = np.full(len(mids), np.inf)
        owner = np.full(len(mids), -1)
        for b, fb in enumerate(faces):
            if b == a:
                continue
            lo, hi = boxes[b]
            idx = np.where(np.all((mids >= lo) & (mids <= hi), axis=1))[0]
            if not len(idx):
                continue
            d = dist_points_mesh(mids[idx], fb)
            better = d < np.minimum(best[idx], tol)
            best[idx[better]] = d[better]
            owner[idx[better]] = b
        for b in sorted(set(owner.tolist())):
            if b <= a:
                continue
            for ch in _chains(segs[owner == b], tol):
                m = ch.mean(axis=1)
                out.append(TopoEdge(a, b, ch, m[np.argmin(np.linalg.norm(m - m.mean(0), axis=1))]))
    return out

# ---------------------------------------------------------------- プロファイル（2D）


def _seg2d_dist(seg: Segment, u: float, v: float, swap_arc: bool = False) -> float:
    p = np.array([u, v])
    if seg.kind == "line":
        a, b = np.asarray(seg.points[0], float), np.asarray(seg.points[1], float)
        ab = b - a
        t = np.clip((p - a) @ ab / max(ab @ ab, 1e-300), 0, 1)
        return float(np.linalg.norm(a + t * ab - p))
    c = np.asarray(seg.points[0], float)
    if seg.kind == "circle":
        return abs(float(np.linalg.norm(p - c)) - seg.radius)
    s, e = np.asarray(seg.points[1], float), np.asarray(seg.points[2], float)
    if swap_arc:
        s, e = e, s
    r = float(np.linalg.norm(s - c))
    a0 = math.atan2(*(s - c)[::-1])
    sweep = (math.atan2(*(e - c)[::-1]) - a0) % (2 * math.pi) or 2 * math.pi
    ang = (math.atan2(*(p - c)[::-1]) - a0) % (2 * math.pi)
    if ang <= sweep:
        return abs(float(np.linalg.norm(p - c)) - r)
    return float(min(np.linalg.norm(p - s), np.linalg.norm(p - e)))


def polyline(seg: Segment, n: int = 32) -> np.ndarray:
    """要素を折れ線で近似（始点を含み終点を含まない）。"""
    if seg.kind == "line":
        return np.asarray(seg.points[:1], float)
    c = np.asarray(seg.points[0], float)
    if seg.kind == "circle":
        t = np.linspace(0, 2 * math.pi, n, endpoint=False)
        return c + seg.radius * np.c_[np.cos(t), np.sin(t)]
    s, e = np.asarray(seg.points[1], float), np.asarray(seg.points[2], float)
    r = np.linalg.norm(s - c)
    a0 = math.atan2(*(s - c)[::-1])
    sweep = (math.atan2(*(e - c)[::-1]) - a0) % (2 * math.pi) or 2 * math.pi
    t = a0 + sweep * np.arange(max(2, int(n * sweep / (2 * math.pi)))) / max(2, int(n * sweep / (2 * math.pi)))
    return c + r * np.c_[np.cos(t), np.sin(t)]


def _ends(seg: Segment):
    if seg.kind == "line":
        return np.asarray(seg.points[0], float), np.asarray(seg.points[1], float)
    return np.asarray(seg.points[1], float), np.asarray(seg.points[2], float)


def _chain_order(loop: list[Segment]) -> list[tuple[int, Segment]]:
    """ループの要素を、端点がつながる順に並べる（与えられた順がつながり順とは限らない）。"""
    if len(loop) <= 1 or loop[0].kind == "circle":
        return list(enumerate(loop))
    rest = list(range(1, len(loop)))
    order = [0]
    cur = _ends(loop[0])[1]
    while rest:
        j = min(rest, key=lambda i: min(np.linalg.norm(e - cur) for e in _ends(loop[i])))
        a, b = _ends(loop[j])
        cur = a if np.linalg.norm(b - cur) < np.linalg.norm(a - cur) else b
        order.append(j)
        rest.remove(j)
    return [(i, loop[i]) for i in order]


def _loop_poly(loop: list[Segment], with_owner: bool = False):
    """ループを順につないだ多角形。線分の向きがばらばらでもつながるように並べる。
    with_owner=True なら、各頂点から次の頂点への辺がどの要素（loop の番号）に属するかも返す。"""
    pts, owner = [], []
    prev_end = None
    for si, seg in _chain_order(loop):
        if seg.kind == "circle":
            pl = polyline(seg)
            return (pl, [0] * len(pl)) if with_owner else pl
        if seg.kind == "line":
            a, b = np.asarray(seg.points[0], float), np.asarray(seg.points[1], float)
            pl = a[None]
        else:
            a, b = np.asarray(seg.points[1], float), np.asarray(seg.points[2], float)
            pl = polyline(seg)                                  # 始点を含み終点を含まない
        if prev_end is not None and np.linalg.norm(b - prev_end) < np.linalg.norm(a - prev_end):
            pl = np.vstack([b[None], pl[:0:-1]]) if seg.kind == "arc" else b[None]
            a, b = b, a
        pts.append(pl)
        owner += [si] * len(pl)
        prev_end = b
    P = np.vstack(pts)
    return (P, owner) if with_owner else P


def _area(poly) -> float:
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _inside(pt, poly) -> bool:
    x, y = pt
    c = False
    for (x1, y1), (x2, y2) in zip(poly, np.roll(poly, -1, axis=0)):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            c = not c
    return c


def group_loops(loops: list[list[Segment]]) -> list[tuple[list[Segment], list[list[Segment]]]]:
    """ループを (外形, [穴…]) の組に分ける。入れ子の深さが偶数なら外形、奇数なら穴。"""
    polys = [_loop_poly(l) for l in loops]
    areas = [abs(_area(p)) for p in polys]
    depth = []
    parent = []
    for i, p in enumerate(polys):
        cont = [j for j, q in enumerate(polys) if j != i and areas[j] > areas[i] and _inside(p[0], q)]
        depth.append(len(cont))
        parent.append(min(cont, key=lambda j: areas[j]) if cont else None)
    groups = {i: (loops[i], []) for i in range(len(loops)) if depth[i] % 2 == 0}
    for i in range(len(loops)):
        if depth[i] % 2 == 1:
            groups[parent[i]][1].append(loops[i])
    return list(groups.values())

# ---------------------------------------------------------------- 履歴の判定


Origin = tuple[str, Any]


def _majority(votes: list) -> Any:
    """最多票。None（どこにも当てはまらない）も 1 票として数える。
    フィレット面の点のうち接線近くのものは隣の平面に近いので、None を無視すると平面と誤判定する。"""
    if not votes:
        return None
    return Counter(votes).most_common(1)[0][0]


def _local(plane: Plane, P: np.ndarray):
    o, x, y, n = (np.asarray(v, float) for v in (plane.origin, plane.x_dir, plane.y_dir, plane.normal))
    d = P - o
    return d @ x, d @ y, d @ n


def classify_extrude(plane: Plane, loops: list[list[Segment]], distance: float,
                     faces: list[FaceMesh], tol: float) -> dict[int, Origin]:
    segs = [s for l in loops for s in l]
    out = {}
    for h, fm in enumerate(faces):
        u, v, w = _local(plane, samples(fm))
        votes = []
        for i in range(len(u)):
            if abs(w[i]) < tol:
                votes.append(("cap", "start"))
            elif abs(w[i] - distance) < tol:
                votes.append(("cap", "end"))
            else:
                d = [_seg2d_dist(s, u[i], v[i]) for s in segs]
                j = int(np.argmin(d))
                votes.append(("side", segs[j].tag) if d[j] < tol else None)
        out[h] = _majority(votes) or ("new", None)
    return out


def classify_revolve(plane: Plane, loops: list[list[Segment]], axis, angle_deg: float,
                     faces: list[FaceMesh], tol: float) -> dict[int, Origin]:
    """回転体。軸はスケッチ座標の 2 点。プロファイルは軸の片側にある前提。
    端面（360° 未満のとき）は start / end。回転方向の取り決めはカーネルによるので、
    「始まりの面」以外の端面を end とする。"""
    a = np.asarray(axis[0], float)
    t = np.asarray(axis[1], float) - a
    t /= np.linalg.norm(t)
    pn = np.array([-t[1], t[0]])
    segs = [s for l in loops for s in l]
    side = 1.0 if np.mean([((np.asarray(p, float) - a) @ pn) for s in segs for p in s.points]) >= 0 else -1.0
    # (s, r) 座標へ写す: s = 軸方向、r = 軸からの距離。side<0 なら鏡映なので円弧の向きが逆になる
    M = np.vstack([t, side * pn])

    def to_sr(p):
        return tuple((M @ (np.asarray(p, float) - a)).tolist())
    segs_sr = [Segment(s.kind, s.tag, [to_sr(p) for p in s.points], s.radius) for s in segs]
    o = np.asarray(plane.origin, float)
    X, Y, N = (np.asarray(v, float) for v in (plane.x_dir, plane.y_dir, plane.normal))
    A3 = o + a[0] * X + a[1] * Y
    T3 = t[0] * X + t[1] * Y
    S3 = side * (pn[0] * X + pn[1] * Y)
    Q3 = np.cross(T3, S3)
    full = angle_deg >= 360 - 1e-9
    ang = math.radians(angle_deg)
    out = {}
    for h, fm in enumerate(faces):
        votes = []
        for p in samples(fm):
            q = p - A3
            s_, x1, x2 = q @ T3, q @ S3, q @ Q3
            r = math.hypot(x1, x2)
            phi = math.atan2(x2, x1) % (2 * math.pi)
            if not full and r * min(phi, 2 * math.pi - phi) < tol and x1 > 0:
                votes.append(("cap", "start"))
                continue
            if not full and min(r * abs(phi - ang), r * abs(phi - (2 * math.pi - ang))) < tol:
                votes.append(("cap", "end"))
                continue
            d = [_seg2d_dist(sg, s_, r, swap_arc=side < 0) for sg in segs_sr]
            j = int(np.argmin(d))
            votes.append(("side", segs[j].tag) if d[j] < tol else None)
        out[h] = _majority(votes) or ("new", None)
    return out


def path_polyline(path: Profile, n_arc: int = 24) -> tuple[np.ndarray, np.ndarray]:
    """経路（平面の線・円弧）をワールドの折れ線に。(点 (k,3), 各点の接線 (k,3))。"""
    pl = path.plane
    P, T = [], []
    for s in path.loops[0]:
        if s.kind == "line":
            a, b = (np.asarray(pl.to_world(*p), float) for p in s.points)
            d = (b - a) / (np.linalg.norm(b - a) or 1.0)
            pts, tg = [a, b], [d, d]
        else:
            c, p0, p1 = (np.asarray(p, float) for p in s.points[:3]) if s.kind != "circle" else (
                np.asarray(s.points[0], float), None, None)
            if s.kind == "circle":
                r, a0, sw = s.radius, 0.0, 2 * math.pi
            else:
                r = float(np.linalg.norm(p0 - c))
                a0 = math.atan2(p0[1] - c[1], p0[0] - c[0])
                a1 = math.atan2(p1[1] - c[1], p1[0] - c[0])
                sw = (a1 - a0) % (2 * math.pi) or 2 * math.pi
                if s.kind == "arc_cw":
                    sw = -((a0 - a1) % (2 * math.pi) or 2 * math.pi)
            k = max(2, int(n_arc * abs(sw) / (2 * math.pi)) + 2)
            ang = a0 + sw * np.linspace(0, 1, k)
            pts = [np.asarray(pl.to_world(c[0] + r * math.cos(t), c[1] + r * math.sin(t)), float) for t in ang]
            sgn = 1.0 if sw > 0 else -1.0
            tg = [sgn * np.asarray(pl.to_world(-math.sin(t), math.cos(t)), float) - np.asarray(pl.origin, float) for t in ang]
            tg = [t / np.linalg.norm(t) for t in tg]
        if P and np.linalg.norm(P[-1] - pts[0]) < 1e-9:
            pts, tg = pts[1:], tg[1:]
        P += pts
        T += tg
    return np.asarray(P), np.asarray(T)


def profile_center(profile: Profile) -> np.ndarray:
    """断面の曲線のワールドの外接箱の中心（カーネルが経路の始点に合わせる点）。"""
    pl = profile.plane
    pts = []
    for loop in profile.loops:
        for s in loop:
            if s.kind == "line":
                pts += [pl.to_world(*p) for p in s.points]
            else:
                c = np.asarray(s.points[0], float)
                r = s.radius if s.kind == "circle" else float(np.linalg.norm(np.asarray(s.points[1], float) - c))
                if s.kind == "circle":
                    ang = np.linspace(0, 2 * math.pi, 73)
                else:
                    a0 = math.atan2(s.points[1][1] - c[1], s.points[1][0] - c[0])
                    a1 = math.atan2(s.points[2][1] - c[1], s.points[2][0] - c[0])
                    ang = a0 + ((a1 - a0) % (2 * math.pi) or 2 * math.pi) * np.linspace(0, 1, 73)
                pts += [pl.to_world(c[0] + r * math.cos(t), c[1] + r * math.sin(t)) for t in ang]
    P = np.asarray(pts, float)
    return (P.min(0) + P.max(0)) / 2


def classify_sweep(profile: Profile, path: Profile, faces: list[FaceMesh], tol: float) -> dict[int, Origin]:
    """スイープの面の履歴。面の点を経路の最寄りの位置で始点の断面の座標へ戻し、どの断面の要素から生まれたかを見る。
    平面の経路では、断面の向きは「経路の接線・経路の平面の法線」の組ごと回る（ねじれない）。"""
    P, T = path_polyline(path)
    N = np.asarray(path.plane.normal, float)
    closed = np.linalg.norm(P[0] - P[-1]) < 1e-7
    shift = P[0] - profile_center(profile)                   # カーネルが断面を動かした量
    O = np.asarray(profile.plane.origin, float) + shift
    X, Y = np.asarray(profile.plane.x_dir, float), np.asarray(profile.plane.y_dir, float)
    T0, Te = T[0], T[-1]
    B0 = np.cross(T0, N)
    segs = [s for l in profile.loops for s in l]
    A, Bq = P[:-1], P[1:]
    R = max(float(np.linalg.norm(np.asarray(profile.plane.to_world(*q), float) + shift - P[0]))
            for sg in segs for q in (sg.points if sg.kind != "circle" else [sg.points[0]])) + max(
        [sg.radius or 0.0 for sg in segs if sg.kind == "circle"] + [0.0])        # 断面の大きさ（始点からの最大距離の目安）
    out = {}
    for h, fm in enumerate(faces):
        votes = []
        for p in samples(fm):
            if not closed and abs((p - P[0]) @ T0) < tol and np.linalg.norm(p - P[0]) < R + tol:
                votes.append(("cap", "start"))                   # 始点の断面の平面の上で、始点の近く
                continue
            if not closed and abs((p - P[-1]) @ Te) < tol and np.linalg.norm(p - P[-1]) < R + tol:
                votes.append(("cap", "end"))
                continue
            q = p - ((p - P[0]) @ N) * N                         # 経路の平面へ
            d = _segdist(q, A, Bq)
            i = int(np.argmin(d))
            ab = Bq[i] - A[i]
            t = float(np.clip((q - A[i]) @ ab / max(ab @ ab, 1e-300), 0, 1))
            c = A[i] + t * ab
            Tt = ab / (np.linalg.norm(ab) or 1.0)
            Bt = np.cross(Tt, N)
            w0 = P[0] + ((p - c) @ N) * N + ((p - c) @ Bt) * B0
            u, v = (w0 - O) @ X, (w0 - O) @ Y
            dd = [_seg2d_dist(sg, u, v) for sg in segs]
            j = int(np.argmin(dd))
            votes.append(("side", segs[j].tag) if dd[j] < max(tol, 0.02 * np.linalg.norm(ab)) else None)
        out[h] = _majority(votes) or ("new", None)
    return out


def match_sources(out_faces: list[FaceMesh], inputs: list[list[FaceMesh]], tol: float,
                  flat_tol: float | None = None) -> dict[int, tuple[int, int] | None]:
    """結果の各面 → (入力番号, 面番号)。どの入力面にも乗らなければ None。
    同じ距離なら先の入力（A）を優先する。
    flat_tol を与えると、平面の入力面にはその厳しい許容値を使う。平面のメッシュは誤差 0 なので、
    接線方向に平面へ近づく曲面（フィレット）の点を平面上と誤判定しない。
    速い実装（_match_sources_fast、D65）。環境変数 AGENTCAD_CHECK_MATCH=1 のときは元の実装（_match_sources_ref）とも
    計算して、食い違えば例外にする（高速化で名前が変わっていないことの検査）。"""
    out = _match_sources_fast(out_faces, inputs, tol, flat_tol)
    if os.environ.get("AGENTCAD_CHECK_MATCH"):
        ref = _match_sources_ref(out_faces, inputs, tol, flat_tol)
        if ref != out:
            bad = {h: (ref[h], out.get(h)) for h in ref if ref[h] != out.get(h)}
            raise AssertionError(f"match_sources が元の実装と食い違いました（面: (元, 新)）: {bad}")
    return out


def _tri_boxes(fm: FaceMesh):
    """三角形ごとの外接箱（覚えておく）。"""
    b = fm.__dict__.get("_tri_boxes")
    if b is None:
        T = fm.T
        b = fm.__dict__["_tri_boxes"] = (T.min(axis=1), T.max(axis=1))
    return b


_PRE_KEYS = ("a", "b", "c", "ab", "bc", "ca", "n", "ok", "inv", "e1", "e2", "e3", "L0", "L1", "L2")


def _pair_dist(Q: np.ndarray, pre: dict, ti: np.ndarray) -> np.ndarray:
    """点 Q[i] と三角形 ti[i] の距離（組ごと）。dist_points_mesh と同じ式・同じ順で計算するので、値はビット単位で同じ
    （点 × 三角形の表の各要素と、組の一覧の各要素が同じ値になる。D65 で車のデモの全部の面で確かめた）。"""
    a, b, c, ab, bc, ca, n, ok, inv, e1, e2, e3, L0, L1, L2 = (pre[k][ti] for k in _PRE_KEYS)
    ap, bp, cp = Q - a, Q - b, Q - c
    inside = ok & (np.einsum("ik,ik->i", ap, e1) >= 0) & (np.einsum("ik,ik->i", bp, e2) >= 0)         & (np.einsum("ik,ik->i", cp, e3) >= 0)
    dplane = np.abs(np.einsum("ik,ik->i", ap, n)) * inv
    dedge = None
    for o, e, l in ((ap, ab, L0), (bp, bc, L1), (cp, ca, L2)):
        t = np.clip(np.einsum("ik,ik->i", o, e) / np.where(l > 0, l, 1.0), 0.0, 1.0)
        d = np.linalg.norm(o - t[:, None] * e, axis=-1)
        dedge = d if dedge is None else np.minimum(dedge, d)
    return np.where(inside, dplane, dedge)


def _match_sources_fast(out_faces, inputs, tol, flat_tol):
    """_match_sources_ref と同じ結果を、まとめて計算して速く求める（D65）。
    元の実装は、結果の面ごと・点ごとに、入力の面を順に見て d < min(best, しきい値) なら更新する。この結果は
    「d < しきい値 の入力面のうち d が最小のもの（同じ d なら先の入力）」と同じなので、全部の点と入力面の距離の表を作ってから選ぶ:
    1. 外接箱（tol だけ広げる。元と同じ条件）に入る（点, 入力面）の組だけ調べる
    2. その中で、外接箱までの距離が tol を超える三角形は外す（最小になる三角形は d < tol なら必ず残るので、tol 未満の d は元と同じ）
    3. 残った（点, 三角形）の組の距離を一度に計算し（_pair_dist）、（点, 入力面）ごとの最小を取る
    4. しきい値: 平面の入力面は flat_tol、ほかは tol。どちらでも結果が同じ面（距離が flat_tol 以上 tol 未満の点が無い面）は
       平面の当てはめ（SVD）をしない
    多数決は元のまま。"""
    samp = samples_many(out_faces)
    counts = [len(x) for x in samp]
    faces = [g for fs in inputs for g in fs]
    keys = [(ii, j) for ii, fs in enumerate(inputs) for j in range(len(fs))]
    if not sum(counts) or not faces:
        return {h: _majority([None] * n) for h, n in enumerate(counts)}
    P = np.concatenate([x for x in samp if len(x)])
    K = len(faces)
    lim = max(tol, flat_tol or 0.0)
    boxes = np.array([np.concatenate(g.bbox(tol)) if len(g.tri) else [np.inf] * 3 + [-np.inf] * 3 for g in faces])
    inb = np.all((P[:, None] >= boxes[None, :, :3]) & (P[:, None] <= boxes[None, :, 3:]), axis=2)      # (点, 面)
    pis, tis, fjs, pres, off = [], [], [], [], 0
    for j in np.where(inb.any(axis=0))[0]:
        g = faces[j]
        idx = np.where(inb[:, j])[0]
        lo, hi = _tri_boxes(g)
        d = np.maximum(np.maximum(lo[None] - P[idx, None], P[idx, None] - hi[None]), 0.0)
        pi, ti = np.nonzero(np.sqrt((d * d).sum(axis=-1)) <= lim * (1 + 1e-9) + 1e-12)
        if len(pi):
            pis.append(idx[pi]); tis.append(ti + off); fjs.append(np.full(len(pi), j))
        pres.append(g.T); off += len(g.tri)
    D = np.full((len(P), K), np.inf)
    if pis:
        pre = _tri_pre_T(np.concatenate(pres))                     # 調べる面の三角形をつないで一度に下ごしらえ
        pi, ti, fj = np.concatenate(pis), np.concatenate(tis), np.concatenate(fjs)
        np.minimum.at(D, (pi, fj), _pair_dist(P[pi], pre, ti))
    thr = np.full(K, tol, float)
    if flat_tol is not None:
        need = ((D >= flat_tol) & (D < tol)).any(axis=0)            # 平面かどうかで結果が変わりうる面だけ当てはめる
        for j in np.where(need)[0]:
            if fit_plane(faces[j], flat_tol) is not None:
                thr[j] = flat_tol
    Dm = np.where(D < thr[None], D, np.inf)
    best = Dm.min(axis=1)
    first = np.argmax(Dm == best[:, None], axis=1)                  # 同じ距離なら先の入力
    arg = np.where(np.isfinite(best), first, -1)
    out, start = {}, 0
    for h, n in enumerate(counts):
        out[h] = _majority([keys[k] if k >= 0 else None for k in arg[start:start + n].tolist()])
        start += n
    return out


def _match_sources_ref(out_faces: list[FaceMesh], inputs: list[list[FaceMesh]], tol: float,
                       flat_tol: float | None = None) -> dict[int, tuple[int, int] | None]:
    """元の実装（検査用。D65）。"""
    boxes = [[f.bbox(tol) for f in faces] for faces in inputs]
    thr = [[(flat_tol if flat_tol is not None and fit_plane(f, flat_tol) is not None else tol) for f in faces]
           for faces in inputs]
    out = {}
    for h, fm in enumerate(out_faces):
        P = samples(fm)
        best = np.full(len(P), math.inf)
        arg: list = [None] * len(P)
        for ii, faces in enumerate(inputs):
            for j, g in enumerate(faces):
                lo, hi = boxes[ii][j]
                inb = np.all((P >= lo) & (P <= hi), axis=1)
                if not inb.any():
                    continue
                d = np.full(len(P), math.inf)
                d[inb] = dist_points_mesh(P[inb], g)
                better = d < np.minimum(best, thr[ii][j])          # 同じ距離なら先の入力が残る
                for k in np.where(better)[0]:
                    best[k], arg[k] = d[k], (ii, j)
        out[h] = _majority(arg)
    return out


def face_point(fm: FaceMesh) -> tuple:
    """面の上の点（いちばん大きい三角形の重心。境界から離れた点をカーネルの面探しに使う）。"""
    T = fm.pos[fm.tri]
    a = np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1)
    return tuple(T[int(np.argmax(a))].mean(axis=0).tolist())


def nearest_offset_face(fm: FaceMesh, faces: list[FaceMesh], dist: float) -> int | None:
    """fm（シェルの壁の面）が「dist だけ離れて」重なる元の面の番号。どの面とも合わなければ None。"""
    P = samples(fm)
    best, arg = math.inf, None
    for j, g in enumerate(faces):
        err = float(np.abs(dist_points_mesh(P, g) - dist).max())
        if err < best:
            best, arg = err, j
    return arg if best <= 0.05 * dist + 1e-9 else None


def nearest_edge(fm: FaceMesh, edges: dict[Any, np.ndarray]) -> Any:
    """フィレット面に最も近いエッジ（edges: ハンドル → 線分 (k,2,3)）。"""
    P = samples(fm)
    best, arg = math.inf, None
    for h, segs in edges.items():
        d = max(_segdist(p, segs[:, 0], segs[:, 1]).min() for p in P)
        if d < best:
            best, arg = d, h
    return arg

# ---------------------------------------------------------------- 面の幾何（円筒軸など）


def extrude_geom(plane: Plane, history: dict[int, Origin], loops) -> dict[int, dict]:
    """押し出しの側面のうち、円・円弧から生まれた面の円筒軸と半径。"""
    by_tag = {s.tag: s for l in loops for s in l}
    out = {}
    for h, (kind, src) in history.items():
        s = by_tag.get(src) if kind == "side" else None
        if s is not None and s.kind in ("circle", "arc"):
            c = s.points[0]
            r = s.radius if s.kind == "circle" else float(np.linalg.norm(np.subtract(s.points[1], c)))
            out[h] = {"axis": (plane.to_world(*c), tuple(plane.normal)), "radius": r}
    return out


def revolve_geom(plane: Plane, history: dict[int, Origin], loops, axis) -> dict[int, dict]:
    """回転の側面のうち、軸に平行な線分から生まれた面（円筒）の軸と半径。"""
    by_tag = {s.tag: s for l in loops for s in l}
    a = np.asarray(axis[0], float)
    t = np.asarray(axis[1], float) - a
    t /= np.linalg.norm(t)
    o3 = plane.to_world(*a)
    d3 = tuple((t[0] * np.asarray(plane.x_dir) + t[1] * np.asarray(plane.y_dir)).tolist())
    out = {}
    for h, (kind, src) in history.items():
        s = by_tag.get(src) if kind == "side" else None
        if s is None or s.kind != "line":
            continue
        p, q = np.asarray(s.points[0], float), np.asarray(s.points[1], float)
        d = q - p
        if abs(d[0] * t[1] - d[1] * t[0]) < 1e-9 * max(1.0, np.linalg.norm(d)):
            r = abs((p - a) @ np.array([-t[1], t[0]]))
            if r > 1e-12:
                out[h] = {"axis": (o3, d3), "radius": float(r)}
    return out

# ---------------------------------------------------------------- カーネル基底

_uid = iter(range(1, 1 << 62))


@dataclass
class TShape:
    """MeshTopoKernel の形状: カーネル固有の形状 + 面メッシュ + 面の幾何 + エッジ（遅延計算）。
    面ハンドル ("f", uid, 番号)、エッジハンドル ("e", uid, 番号)。uid は形状ごとに一意なので、
    ブーリアンの 2 入力の面ハンドルが衝突しない（命名は入力の名前表をハンドルで引くため）。"""
    raw: Any
    faces: list[FaceMesh]
    geom: dict[int, dict] = field(default_factory=dict)      # 面番号 → {"axis", "radius"}
    tol: float = 1e-6
    flat_tol: float = 1e-9                    # 平面判定の許容値（平面メッシュは誤差 0 なので厳しくてよい）
    uid: int = field(default_factory=lambda: next(_uid))
    #: カーネルが隣接を正確に返せるならその関数（無ければメッシュから求める）
    edge_source: Any = None
    _edges: list[TopoEdge] | None = None
    _volume: float | None = None              # 体積のキャッシュ（形状は作ったあと変わらない。D64）

    @property
    def edge_list(self) -> list[TopoEdge]:
        if self._edges is None:
            got = self.edge_source() if self.edge_source else None
            self._edges = got if got is not None else adjacency(self.faces, self.tol)
        return self._edges

    def fh(self, i: int) -> tuple:
        return ("f", self.uid, i)

    def eh(self, i: int) -> tuple:
        return ("e", self.uid, i)


def _idx(shape: TShape, handle) -> int:
    if handle[1] != shape.uid:
        raise KernelError(f"別の形状のハンドルです: {handle}")
    return handle[2]


class MeshTopoKernel(Kernel):
    """履歴を返さないカーネル用の基底。サブクラスは _raw_* と _face_meshes と _raw_size / _raw_volume を実装する。"""

    #: メッシュ化の弦誤差（モデル寸法に対する比）
    chord_rel: float = 1e-3
    #: 平面判定の許容値（モデル寸法に対する比）。メッシュの座標が float32 でも十分大きく、曲面の弦誤差より十分小さい値
    flat_rel: float = 1e-6

    # ---- サブクラスが実装する ----
    def _raw_extrude(self, plane: Plane, groups, distance: float) -> Any:
        """groups = group_loops() の結果。plane 上で法線方向へ distance（負なら逆向き）押し出した立体。"""
        raise NotImplementedError

    def _raw_revolve(self, plane: Plane, groups, axis, angle_deg: float) -> Any:
        raise NotImplementedError

    def _raw_boolean(self, op: str, a: Any, b: Any) -> Any:
        raise NotImplementedError

    def _raw_fillet(self, raw: Any, edge_points: list[tuple], radius: float) -> Any:
        """edge_points: 対象エッジ上の点（点からのエッジの特定はカーネル側で行う）。"""
        raise NotImplementedError

    def _raw_chamfer(self, raw: Any, edge_points: list[tuple], distance: float) -> Any:
        raise KernelError("このカーネルは面取りに対応していません")

    def _raw_shell(self, raw: Any, face_points: list[tuple], thickness: float) -> Any:
        """face_points: 取り除く面の上の点。thickness: 負 = 内側へ、正 = 外側へ。"""
        raise KernelError("このカーネルはシェルに対応していません")

    def _face_meshes(self, raw: Any, chord: float, angle: float | None = None) -> list[FaceMesh]:
        """angle: 曲面の角度の許容値（度。None はカーネルの既定）。書き出しの細かさに使う（D71）。"""
        raise NotImplementedError

    def _raw_size(self, raw: Any) -> float:
        """モデルの大きさ（バウンディングボックスの対角）。"""
        raise NotImplementedError

    def _raw_volume(self, raw: Any) -> float:
        raise NotImplementedError

    def _raw_edges(self, raw: Any) -> list[TopoEdge] | None:
        """任意: カーネルがエッジと隣接 2 面を返せるなら TopoEdge の一覧（面番号は _face_meshes の順）。
        None ならメッシュから求める。"""
        return None

    # ---- 共通実装 ----
    def _wrap(self, raw) -> TShape:
        size = max(self._raw_size(raw), 1e-9)
        chord = self.chord_rel * size
        tol = max(4 * chord, 1e-7 * size)
        faces = weld_all(self._face_meshes(raw, chord), 1e-9 * size)
        return TShape(raw, faces, tol=tol, flat_tol=self.flat_rel * size, edge_source=lambda: self._raw_edges(raw))

    def _guard(self, fn, *args):
        try:
            return fn(*args)
        except KernelError:
            raise
        except Exception as e:  # カーネルの例外はすべて KernelError にする
            raise KernelError(f"{type(e).__name__}: {e}") from e

    def extrude(self, profile: Profile, distance: float) -> OpResult:
        if distance == 0:
            raise KernelError("距離が0です")
        groups = group_loops(profile.loops)
        s = self._wrap(self._guard(self._raw_extrude, profile.plane, groups, distance))
        hist = classify_extrude(profile.plane, profile.loops, distance, s.faces, s.tol)
        s.geom = extrude_geom(profile.plane, hist, profile.loops)
        return OpResult(s, {s.fh(i): o for i, o in hist.items()})

    def revolve(self, profile: Profile, axis, angle_deg: float) -> OpResult:
        groups = group_loops(profile.loops)
        s = self._wrap(self._guard(self._raw_revolve, profile.plane, groups, axis, angle_deg))
        hist = classify_revolve(profile.plane, profile.loops, axis, angle_deg, s.faces, s.tol)
        s.geom = revolve_geom(profile.plane, hist, profile.loops, axis)
        return OpResult(s, {s.fh(i): o for i, o in hist.items()})

    def sweep(self, profile: Profile, path: Profile) -> OpResult:
        groups = group_loops(profile.loops)
        s = self._wrap(self._guard(self._raw_sweep, profile, groups, path))
        hist = classify_sweep(profile, path, s.faces, s.tol)
        return OpResult(s, {s.fh(i): o for i, o in hist.items()})

    def _raw_sweep(self, profile: Profile, groups, path: Profile) -> Any:
        raise KernelError("このカーネルはスイープに対応していません")

    @staticmethod
    def _editable(*shapes):
        """取り込んだメッシュ（D71）は形を変える演算に使えない。"""
        from .meshbody import is_mesh
        if any(is_mesh(x) for x in shapes):
            raise KernelError("取り込んだメッシュのボディは形を変えられません（表示・計測・スケッチの参照・合致に使えます）")

    def boolean(self, op, a: TShape, b: TShape) -> OpResult:
        self._editable(a, b)
        s = self._wrap(self._guard(self._raw_boolean, op, a.raw, b.raw))
        src = match_sources(s.faces, [a.faces, b.faces], max(s.tol, a.tol, b.tol), max(s.flat_tol, a.flat_tol, b.flat_tol))
        hist = {}
        for i, m in src.items():
            if m is None:
                hist[s.fh(i)] = ("new", None)
                continue
            inp = (a, b)[m[0]]
            hist[s.fh(i)] = ("modified", inp.fh(m[1]))
            if m[1] in inp.geom:
                s.geom[i] = inp.geom[m[1]]
        return OpResult(s, hist)

    def fillet(self, shape: TShape, edges, radius) -> OpResult:
        return self._edge_op(shape, edges, radius, self._raw_fillet, "fillet")

    def chamfer(self, shape: TShape, edges, distance) -> OpResult:
        return self._edge_op(shape, edges, distance, self._raw_chamfer, "chamfer")

    def _edge_op(self, shape: TShape, edges, value, raw_fn, kind: str) -> OpResult:
        """エッジを丸める・面取りする操作の共通部分: 残った面は元の面に、新しい面はいちばん近い元のエッジに対応させる。"""
        self._editable(shape)
        es = [shape.edge_list[_idx(shape, e)] for e in edges]
        s = self._wrap(self._guard(raw_fn, shape.raw, [tuple(e.mid.tolist()) for e in es], value))
        src = match_sources(s.faces, [shape.faces], max(s.tol, shape.tol), max(s.flat_tol, shape.flat_tol))
        cand = {e: shape.edge_list[_idx(shape, e)].segs for e in edges}
        hist = {}
        for i, m in src.items():
            if m is not None:
                hist[s.fh(i)] = ("modified", shape.fh(m[1]))
                if m[1] in shape.geom:
                    s.geom[i] = shape.geom[m[1]]
            else:
                hist[s.fh(i)] = (kind, nearest_edge(s.faces[i], cand))
        return OpResult(s, hist)

    def shell(self, shape: TShape, faces, thickness) -> OpResult:
        """シェル。残った外側の面は元の面に、新しい壁の面は「厚さだけ離れた元の面」に対応させる。"""
        self._editable(shape)
        if abs(thickness) <= 0:
            raise KernelError("厚さは 0 より大きくしてください")
        pts = [face_point(shape.faces[_idx(shape, f)]) for f in faces]
        s = self._wrap(self._guard(self._raw_shell, shape.raw, pts, float(thickness)))
        src = match_sources(s.faces, [shape.faces], max(s.tol, shape.tol), max(s.flat_tol, shape.flat_tol))
        hist = {}
        for i, m in src.items():
            if m is not None:
                hist[s.fh(i)] = ("modified", shape.fh(m[1]))
                if m[1] in shape.geom:
                    s.geom[i] = shape.geom[m[1]]
            else:
                j = nearest_offset_face(s.faces[i], shape.faces, abs(thickness))
                hist[s.fh(i)] = ("shell", shape.fh(j) if j is not None else None)
        return OpResult(s, hist)

    def faces(self, shape: TShape):
        return [shape.fh(i) for i in range(len(shape.faces))]

    def edges(self, shape: TShape):
        return [(shape.eh(i), shape.fh(e.fa), shape.fh(e.fb)) for i, e in enumerate(shape.edge_list)]

    def edge_polyline(self, shape: TShape, edge):
        return shape.edge_list[_idx(shape, edge)].segs

    def face_plane(self, shape: TShape, face):
        return fit_plane(shape.faces[_idx(shape, face)], shape.flat_tol)

    def face_axis(self, shape: TShape, face):
        g = shape.geom.get(_idx(shape, face))
        return g["axis"] if g else None

    def face_radius(self, shape: TShape, face):
        g = shape.geom.get(_idx(shape, face))
        return g["radius"] if g else None

    def sort_key(self, shape: TShape, handle):
        i = _idx(shape, handle)
        p = shape.edge_list[i].mid if handle[0] == "e" else shape.faces[i].centroid()
        return tuple(np.round(p / shape.tol).astype(int).tolist())

    def tessellate(self, shape: TShape, tol: float = 0.1) -> Mesh:
        pos, idx, fot, off = [], [], [], 0
        for i, f in enumerate(shape.faces):
            pos.append(f.pos); idx.append(f.tri + off); fot.append(np.full(len(f.tri), i))
            off += len(f.pos)

        def cat(xs, empty, dt):
            return np.concatenate(xs).astype(dt) if xs else np.zeros(empty, dt)
        return Mesh(cat(pos, (0, 3), np.float32), cat(idx, (0, 3), np.uint32), cat(fot, (0,), np.uint32),
                    self.faces(shape))

    def export_mesh(self, shape: TShape, chord_rel: float | None = None, angle: float | None = None) -> Mesh:
        """書き出し用のメッシュ（D71）。chord_rel（モデル寸法に対する弦誤差）を指定すれば細かさを変えて作り直す。
        面の番号・ハンドルは表示用（tessellate）と同じ。取り込んだメッシュはそのまま。"""
        from .meshbody import is_mesh
        if chord_rel is None or is_mesh(shape):
            return self.tessellate(shape)
        size = max(self._raw_size(shape.raw), 1e-9)
        faces = self._guard(self._face_meshes, shape.raw, chord_rel * size, angle)
        if len(faces) != len(shape.faces):        # 念のため: 面の数が合わなければ表示用を使う
            return self.tessellate(shape)
        return self.tessellate(TShape(shape.raw, weld_all(faces, 1e-9 * size), uid=shape.uid))

    def volume(self, shape: TShape) -> float:
        if shape._volume is None:
            from .meshbody import MeshRaw
            shape._volume = shape.raw.volume() if isinstance(shape.raw, MeshRaw) else float(self._raw_volume(shape.raw))
        return shape._volume
