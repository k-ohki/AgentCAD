"""取り込んだ三角形メッシュ（STL・3MF・OBJ）を、MeshTopoKernel の形状（TShape）にする（D71）。

メッシュには「面」の区切りが無いので、三角形の向きから面を作る:
1. 頂点を溶接し、辺を共有する三角形どうしをつなぐ（3 枚以上が共有する辺・開いた縁ではつながない）
2. 同一平面（法線の差 < PLANE_DEG）でつながった三角形を「平面の候補」にまとめる
3. 候補のうち、なめらかに（法線の差 < SMOOTH_DEG）つながる隣の候補が無いか、その隣より十分大きい（LARGER 倍以上）ものを
   「平面の面」とする。円筒・球のメッシュの帯や四角は、同じくらいの大きさの帯・四角になめらかにつながるので平面の面にしない。
   平面とフィレットのように接する場合は、平面のほうがフィレットの帯よりずっと大きいので平面の面になる
4. 平面の面でない三角形どうしは、法線の差が SMOOTH_DEG 未満ならつなぐ（曲面を 1 つの面にまとめる）
面ごとに、平面（MeshTopoKernel.face_plane）と円筒（軸・半径。同心・正接の合致に使う）を当てはめる。
エッジは異なる面の三角形が共有する辺の連なり。

形状はボディとして表示・計測・面上スケッチ・合致・図面に使えるが、形を変える演算（ブーリアン・フィレットなど）はできない。
"""
from __future__ import annotations

import base64
import struct
import zlib
from dataclasses import dataclass

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from .meshtopo import FaceMesh, TopoEdge, TShape, _chains

PLANE_DEG = 0.5
SMOOTH_DEG = 30.0
LARGER = 4.0              # なめらかにつながる隣の候補の面積の何倍以上なら平面の面にするか
TINY = 1e-4               # 全体の面積に対してこれ未満の候補は平面の面にしない（スキャンしたメッシュの細かい三角形）


@dataclass
class MeshRaw:
    """取り込んだメッシュ（TShape.raw）。溶接した頂点と三角形。"""
    pos: np.ndarray
    tri: np.ndarray

    def volume(self) -> float:
        T = self.pos[self.tri]
        return float(abs(np.einsum("ij,ij->i", T[:, 0], np.cross(T[:, 1], T[:, 2])).sum()) / 6.0)

    def size(self) -> float:
        return float(np.linalg.norm(self.pos.max(0) - self.pos.min(0))) if len(self.pos) else 0.0


def is_mesh(shape) -> bool:
    return isinstance(getattr(shape, "raw", None), MeshRaw)


# ---------------------------------------------------------------- 保存用の文字列


def encode(pos: np.ndarray, tri: np.ndarray) -> str:
    """頂点（float32）と三角形（uint32）を zlib + base64 の文字列にする（フィーチャーの定義に入れる）。"""
    pos = np.ascontiguousarray(pos, "<f4").reshape(-1, 3)
    tri = np.ascontiguousarray(tri, "<u4").reshape(-1, 3)
    raw = b"AGM1" + struct.pack("<II", len(pos), len(tri)) + pos.tobytes() + tri.tobytes()
    return base64.b64encode(zlib.compress(raw, 6)).decode()


def decode(s: str) -> tuple[np.ndarray, np.ndarray]:
    raw = zlib.decompress(base64.b64decode(s))
    if raw[:4] != b"AGM1":
        raise ValueError("メッシュのデータが壊れています")
    n, m = struct.unpack_from("<II", raw, 4)
    pos = np.frombuffer(raw, "<f4", 3 * n, 12).reshape(-1, 3).astype(float)
    tri = np.frombuffer(raw, "<u4", 3 * m, 12 + 12 * n).reshape(-1, 3).astype(np.int64)
    return pos, tri


# ---------------------------------------------------------------- 下ごしらえ


def clean(pos, tri) -> tuple[np.ndarray, np.ndarray]:
    """頂点を溶接（モデル寸法の 1e-7 で丸めて同じ点をまとめる）し、潰れた三角形を除く。"""
    pos = np.asarray(pos, float).reshape(-1, 3)
    tri = np.asarray(tri, np.int64).reshape(-1, 3)
    if not len(tri):
        raise ValueError("三角形がありません")
    size = float(np.linalg.norm(pos.max(0) - pos.min(0))) or 1.0
    key = np.round(pos / (size * 1e-7)).astype(np.int64)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    pos, tri = pos[first], inv.ravel()[tri]
    ok = (tri[:, 0] != tri[:, 1]) & (tri[:, 1] != tri[:, 2]) & (tri[:, 2] != tri[:, 0])
    T = pos[tri]
    area = 0.5 * np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1)
    tri = tri[ok & (area > (size * 1e-9) ** 2)]
    if not len(tri):
        raise ValueError("面積のある三角形がありません")
    used, inv = np.unique(tri, return_inverse=True)
    return pos[used], inv.reshape(-1, 3)


def _pairs(tri: np.ndarray):
    """辺を共有する三角形の組 (t1, t2, 頂点a, 頂点b)。ちょうど 2 枚が共有する辺だけ。"""
    e = tri[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)
    key = np.sort(e, axis=1)
    owner = np.repeat(np.arange(len(tri)), 3)
    order = np.lexsort((key[:, 1], key[:, 0]))
    k = key[order]
    same = np.all(k[1:] == k[:-1], axis=1)
    # ちょうど 2 枚: 前とは同じで、前の前・次とは違う
    prev = np.r_[False, same[:-1]]
    nxt = np.r_[same[1:], False]
    good = np.where(same & ~prev & ~nxt)[0]
    return owner[order[good]], owner[order[good + 1]], k[good, 0], k[good, 1]


def _components(n: int, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    g = coo_matrix((np.ones(len(a)), (a, b)), shape=(n, n))
    return connected_components(g, directed=False)[1]


def segment(pos: np.ndarray, tri: np.ndarray) -> np.ndarray:
    """三角形ごとの面の番号（0 から。番号は三角形の並び順で最初に現れた順）。"""
    T = pos[tri]
    nrm = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    area = 0.5 * np.linalg.norm(nrm, axis=1)
    nrm = nrm / np.maximum(2 * area, 1e-300)[:, None]
    t1, t2, _, _ = _pairs(tri)
    cos = np.einsum("ij,ij->i", nrm[t1], nrm[t2])
    flat_pair = cos > np.cos(np.radians(PLANE_DEG))
    smooth_pair = cos > np.cos(np.radians(SMOOTH_DEG))
    n = len(tri)
    region = _components(n, t1[flat_pair], t2[flat_pair])
    R = region.max() + 1
    # 平面の候補ごとの面積と、なめらかにつながる隣の候補の最大の面積
    rarea = np.bincount(region, area, R)
    nb = np.zeros(R)
    sm = smooth_pair & (region[t1] != region[t2])
    np.maximum.at(nb, region[t1[sm]], rarea[region[t2[sm]]])
    np.maximum.at(nb, region[t2[sm]], rarea[region[t1[sm]]])
    is_face = (rarea >= TINY * rarea.sum()) & (rarea >= LARGER * nb)
    # 平面の面でない三角形どうしを、なめらかにつながっていればまとめる
    curved = ~is_face[region]
    join = flat_pair | (smooth_pair & curved[t1] & curved[t2])
    comp = _components(n, t1[join], t2[join])
    _, first = np.unique(comp, return_index=True)
    order = np.argsort(first)                  # 最初に現れた順に番号を振り直す（読み込むたびに同じ番号）
    rank = np.empty_like(order)
    rank[order] = np.arange(len(order))
    return rank[comp]


def _cylinder(fm: FaceMesh, size: float) -> dict | None:
    """曲面が円筒なら {"axis": (軸上の点, 向き), "radius"}。法線がすべて 1 つの向きに垂直で、断面が円なら円筒。"""
    T = fm.T
    n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    w = np.linalg.norm(n, axis=1)
    if len(T) < 4 or w.sum() <= 0:
        return None
    u = n / np.maximum(w, 1e-300)[:, None]
    M = (u * w[:, None]).T @ u
    ev, vec = np.linalg.eigh(M / w.sum())
    if ev[0] > 1e-3 or ev[1] < 1e-2:          # 法線が軸に垂直でない / 平面に近い
        return None
    axis = vec[:, 0]
    x = np.cross(axis, [1.0, 0, 0] if abs(axis[0]) < 0.9 else [0, 1.0, 0])
    x /= np.linalg.norm(x)
    y = np.cross(axis, x)
    p = np.c_[fm.pos @ x, fm.pos @ y]
    A = np.c_[2 * p, np.ones(len(p))]
    sol, *_ = np.linalg.lstsq(A, (p ** 2).sum(1), rcond=None)
    c = sol[:2]
    r = float(np.sqrt(max(sol[2] + c @ c, 0.0)))
    if r <= 0 or np.abs(np.linalg.norm(p - c, axis=1) - r).max() > 1e-3 * r + 1e-6 * size:
        return None
    if np.ptp(p[:, 0]) < 1e-6 * size and np.ptp(p[:, 1]) < 1e-6 * size:
        return None
    point = c[0] * x + c[1] * y + (fm.pos @ axis).mean() * axis
    return {"axis": (tuple(point.tolist()), tuple(axis.tolist())), "radius": r}


def mesh_shape(pos, tri) -> TShape:
    """取り込んだメッシュの形状。面は segment()、エッジは面の境の辺の連なり。"""
    pos, tri = clean(pos, tri)
    raw = MeshRaw(pos, tri)
    size = max(raw.size(), 1e-9)
    fid = segment(pos, tri)
    order = np.argsort(fid, kind="stable")
    bounds = np.searchsorted(fid[order], np.arange(fid.max() + 2))
    faces, geom = [], {}
    for i in range(fid.max() + 1):
        t = tri[order[bounds[i]:bounds[i + 1]]]
        used, inv = np.unique(t, return_inverse=True)
        fm = FaceMesh(pos[used], inv.reshape(-1, 3))
        faces.append(fm)
        g = _cylinder(fm, size)
        if g is not None:
            geom[i] = g
    tol = 1e-4 * size

    def edges() -> list[TopoEdge]:
        t1, t2, a, b = _pairs(tri)
        fa, fb = fid[t1], fid[t2]
        cut = fa != fb
        lo, hi = np.minimum(fa, fb)[cut], np.maximum(fa, fb)[cut]
        segs = np.stack([pos[a[cut]], pos[b[cut]]], 1)
        out = []
        key = lo * (fid.max() + 1) + hi
        for k in np.unique(key):
            sel = key == k
            for ch in _chains(segs[sel], tol):
                m = ch.mean(axis=1)
                out.append(TopoEdge(int(lo[sel][0]), int(hi[sel][0]), ch, m[np.argmin(np.linalg.norm(m - m.mean(0), axis=1))]))
        return out
    return TShape(raw, faces, geom, tol=tol, flat_tol=1e-5 * size, edge_source=edges)
