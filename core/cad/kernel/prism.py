"""押し出しだけの簡易カーネル（開発・UI 確認用）。

実カーネル（usd-brep）が無い環境でも、画面に形を出して UI を作れるようにする。
- 押し出し: 凸でも凹でもよい多角形（円・円弧は折れ線で近似）。穴は未対応
- 和: 面を並べるだけ（重なりは処理しない）
- 差・積・回転・フィレット・面取り・シェル: 未対応（KernelError → そのフィーチャーがエラー表示になる）
"""
from __future__ import annotations

import numpy as np

from .base import KernelError, Plane
from .meshtopo import FaceMesh, MeshTopoKernel, _loop_poly, polyline


def _ear_clip(poly: np.ndarray) -> list[tuple[int, int, int]]:
    """反時計回りの単純多角形を三角形に分ける（耳刈り）。"""
    idx = list(range(len(poly)))
    out = []

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = poly[i0], poly[i1], poly[i2]
            if cross(a, b, c) <= 1e-15:
                continue
            if any(cross(a, b, poly[j]) >= 0 and cross(b, c, poly[j]) >= 0 and cross(c, a, poly[j]) >= 0
                   for j in idx if j not in (i0, i1, i2)):
                continue
            out.append((i0, i1, i2))
            idx.pop(k)
            break
        else:
            break
    if len(idx) == 3:
        out.append(tuple(idx))
    return out


def _loop_points(loop):
    """ループ → (多角形の頂点, 各辺の要素番号)。円も円弧も折れ線で近似する。"""
    if len(loop) == 1 and loop[0].kind == "circle":
        p = polyline(loop[0], 64)
        return p, [0] * len(p)
    return _loop_poly(loop, with_owner=True)


class PrismKernel(MeshTopoKernel):
    def _raw_extrude(self, plane: Plane, groups, distance: float):
        faces = []
        n = np.asarray(plane.normal, float) * distance
        for outer, holes in groups:
            if holes:
                raise KernelError("簡易カーネルは穴のある押し出しに対応していません")
            poly, owner = _loop_points(outer)
            owner = np.asarray(owner)
            area = 0.5 * np.sum(poly[:, 0] * np.roll(poly[:, 1], -1) - poly[:, 1] * np.roll(poly[:, 0], -1))
            if area < 0:                       # 反時計回りにそろえる（辺 i は頂点 i→i+1。逆順では辺の持ち主も 1 つずれる）
                poly = poly[::-1]
                owner = np.roll(owner[::-1], -1)
            tris = _ear_clip(poly)
            P0 = np.array([plane.to_world(*p) for p in poly])
            P1 = P0 + n
            k = len(poly)
            flip = distance < 0
            cap0 = [t[::-1] for t in tris] if not flip else tris
            cap1 = tris if not flip else [t[::-1] for t in tris]
            faces.append(FaceMesh(P0, cap0))
            faces.append(FaceMesh(P1, cap1))
            sides = []
            for i in range(k):
                j = (i + 1) % k
                sides.append([i, j, k + j] if not flip else [i, k + j, j])
                sides.append([i, k + j, k + i] if not flip else [i, k + i, k + j])
            # スケッチ要素ごとに 1 面（円弧を近似した複数の辺も 1 面にまとめる）
            for o in sorted(set(owner.tolist())):
                tri = [t for i in np.where(owner == o)[0] for t in sides[2 * i:2 * i + 2]]
                faces.append(FaceMesh(np.vstack([P0, P1]), tri))
        return faces

    def _raw_boolean(self, op, a, b):
        if op != "union":
            raise KernelError("簡易カーネルは和以外のブーリアンに対応していません（usd-brep を使ってください）")
        return list(a) + list(b)

    def _raw_revolve(self, plane, groups, axis, angle_deg):
        raise KernelError("簡易カーネルは回転に対応していません")

    def _raw_fillet(self, raw, edge_points, radius):
        raise KernelError("簡易カーネルはフィレットに対応していません")

    def _raw_chamfer(self, raw, edge_points, distance):
        raise KernelError("簡易カーネルは面取りに対応していません")

    def _raw_shell(self, raw, face_points, thickness):
        raise KernelError("簡易カーネルはシェルに対応していません")

    def _face_meshes(self, raw, chord, angle=None):
        return raw

    def _raw_size(self, raw):
        P = np.vstack([f.pos for f in raw])
        return float(np.linalg.norm(P.max(0) - P.min(0)))

    def _raw_volume(self, raw):
        return sum(float(np.einsum("ij,ij->i", T[:, 0], np.cross(T[:, 1], T[:, 2])).sum())
                   for T in (f.T for f in raw)) / 6
