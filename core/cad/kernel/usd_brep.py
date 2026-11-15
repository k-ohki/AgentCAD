"""usd-brep アダプタ。

usd-brep は面の履歴を返さないので、MeshTopoKernel（面メッシュから履歴を逆算）を土台にする（D19）。
このモジュールが実装するのは「形を作る」「面ごとのメッシュを取る」「エッジと隣接面を取る」部分。

フェーズ0b（docs/PHASE0.md）で確認した usd-brep の性質:
- ブーリアンは入力を消費し、fillet_edges / rotate / translate は入力を書き換える。
  消費済みの形状を使うとプロセスごと落ちる → 演算の前に必ず copy() する（_fresh）
- linear_sweep は外形と穴の曲線をまとめて渡せば穴あきの立体を作る
- create_arc は XY 平面で反時計回り、角度は 360 を超えられない
- rotational_sweep は右ねじの向き
- find_face は faces() と同じオブジェクトを返す / find_edge は (エッジ番号, パラメータ)
- Edge.faces() で隣接面が取れる

0c で確認: メッシュは [3, i, j, k, …] 形式で、三角形 → 面 の対応は無い（重心で find_face して振り分ける）。
合格条件: `python -m pytest core/tests/test_kernel_conformance.py -v`
"""
from __future__ import annotations

import math
import os
from typing import Any

import numpy as np
import usd_brep as ub  # 無ければ ImportError → available_kernels() に載らない

from .base import KernelError, Plane, Segment, arc_angles
from .meshtopo import FaceMesh, MeshTopoKernel, TopoEdge, triangles_from_prefixed


def _fresh(brep):
    """消費・書き換えから元の形状を守るための複製。"""
    return brep.copy()


def _axis_angle(R: np.ndarray) -> tuple[np.ndarray, float]:
    """回転行列 → (軸, 角度[度])。"""
    c = (np.trace(R) - 1) / 2
    ang = math.acos(max(-1.0, min(1.0, c)))
    if ang < 1e-12:
        return np.array([0.0, 0, 1]), 0.0
    if abs(ang - math.pi) < 1e-6:
        M = (R + np.eye(3)) / 2
        axis = M[:, int(np.argmax(np.diag(M)))]
        return axis / np.linalg.norm(axis), 180.0
    axis = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]]) / (2 * math.sin(ang))
    return axis, math.degrees(ang)


class UsdBrepKernel(MeshTopoKernel):
    # ------------------------------------------------------------ 形を作る
    @staticmethod
    def _curves(loops: list[list[Segment]]) -> list:
        """スケッチ座標（XY 平面, z=0）の曲線。"""
        out = []
        for loop in loops:
            for s in loop:
                if s.kind == "line":
                    (ax, ay), (bx, by) = s.points
                    out.append(ub.create_line_segment((float(ax), float(ay), 0.0), (float(bx), float(by), 0.0)))
                elif s.kind == "circle":
                    (cx, cy), = s.points
                    out.append(ub.create_circle((float(cx), float(cy), 0.0), float(s.radius)))
                else:
                    a0, sweep, flip = arc_angles(s)
                    c = np.asarray(s.points[0], float)
                    r = float(np.linalg.norm(np.asarray(s.points[1], float) - c))
                    # 角度は 360 を超えられないので、0° から作って回してから中心へ移す
                    arc = ub.create_arc((0.0, 0.0, 0.0), r, 0.0, sweep)
                    if a0:
                        arc = ub.rotate(arc, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), a0)
                    if flip is not None:            # 時計回り: 二等分線のまわりに 180° 回して向きを逆にする
                        arc = ub.rotate(arc, (0.0, 0.0, 0.0), (math.cos(flip), math.sin(flip), 0.0), 180.0)
                    out.append(ub.translate(arc, (float(c[0]), float(c[1]), 0.0)))
        return out

    @staticmethod
    def _place(body, plane: Plane):
        """スケッチ座標系（XY, +Z=法線）→ 平面の座標系へ（body を書き換える）。"""
        R = np.column_stack([plane.x_dir, plane.y_dir, plane.normal]).astype(float)
        axis, deg = _axis_angle(R)
        if deg:
            body = ub.rotate(body, (0.0, 0.0, 0.0), tuple(axis.tolist()), deg)
        if any(plane.origin):
            body = ub.translate(body, tuple(map(float, plane.origin)))
        return body

    def _raw_extrude(self, plane: Plane, groups, distance: float):
        sign = 1.0 if distance > 0 else -1.0
        body = None
        for outer, holes in groups:
            s = ub.linear_sweep(self._curves([outer, *holes]), (0.0, 0.0, sign), abs(float(distance)))
            if not ub.is_manifold_solid(s):
                raise KernelError("押し出しで閉じた立体になりませんでした")
            body = s if body is None else ub.boolean_union(body, s)
        return self._place(body, plane)

    def _raw_revolve(self, plane: Plane, groups, axis, angle_deg: float):
        (ax, ay), (bx, by) = axis
        base, d = (float(ax), float(ay), 0.0), (float(bx - ax), float(by - ay), 0.0)
        body = None
        for outer, holes in groups:
            s = ub.rotational_sweep(self._curves([outer]), base, d, float(angle_deg))
            for hole in holes:
                s = ub.boolean_difference(s, ub.rotational_sweep(self._curves([hole]), base, d, float(angle_deg)))
            body = s if body is None else ub.boolean_union(body, s)
        return self._place(body, plane)

    def _raw_sweep(self, profile, groups, path):
        """sweep_along_planar_path（w_probe で確認: 断面の外接箱の中心を経路の始点に合わせる、経路は順に並べること、
        閉じた経路・穴のある断面も可）。"""
        prof = [self._place(c, profile.plane) for outer, holes in groups for c in self._curves([outer, *holes])]
        pc = [self._place(c, path.plane) for c in self._curves([path.loops[0]])]
        body = ub.sweep_along_planar_path(prof, pc, move_profile=True)
        if not ub.is_manifold_solid(body):
            raise KernelError("スイープで閉じた立体になりませんでした（経路のつながり・断面の大きさを確かめてください）")
        return body

    def _raw_boolean(self, op: str, a, b):
        fn = {"union": ub.boolean_union, "cut": ub.boolean_difference, "intersect": ub.boolean_intersection}[op]
        return fn(_fresh(a), _fresh(b))

    def _raw_fillet(self, raw, edge_points, radius: float, xsect_type: int = 1):
        body = _fresh(raw)
        edges = body.edges()
        tol = 1e-4 * max(self._raw_size(body), 1.0)
        picked = []
        for p in edge_points:
            i, t = ub.find_edge(body, tuple(map(float, p)))
            e = edges[i]
            if np.linalg.norm(np.subtract(e.point_at(t), p)) > tol:
                raise KernelError(f"点 {tuple(p)} の上にエッジがありません")
            picked.append(e)
        return ub.fillet_edges(body, picked, radius=float(radius), xsect_type=int(xsect_type))

    def _raw_chamfer(self, raw, edge_points, distance: float):
        """面取り = fillet_edges の xsect_type=0（k_probe のレポート）。円のフィレットが面に接する線どうしを
        平らにつなぐので、90° のエッジでは distance が各面の後退量（一般的な 3D CAD の「距離」）になる。"""
        return self._raw_fillet(raw, edge_points, distance, xsect_type=0)

    def _raw_shell(self, raw, face_points, thickness: float):
        """シェル = shell_brep（k_probe のレポート）。distance は負で内側。faces_to_shell が取り除く面。
        面は find_face（点 → 面。_face_meshes と同じ使い方）でコピーの上から探す。"""
        body = _fresh(raw)
        tol = 1e-3 * max(self._raw_size(body), 1.0)
        faces = []
        for p in face_points:
            f = ub.find_face(body, tuple(map(float, p)))
            if f is None:
                raise KernelError(f"点 {tuple(p)} の上に面がありません")
            faces.append(f)
        out = ub.shell_brep(body, float(thickness), faces_to_shell=faces)
        if float(ub.compute_volume(out)) <= tol ** 3:
            raise KernelError("シェルの結果が空になりました（厚さが大きすぎないか確かめてください）")
        return out

    # ------------------------------------------------------------ 書き出し
    def export(self, shape, path: str, fmt: str) -> None:
        """BRep を USD に書く（D33）。usd_brep.usd.export_brep(brep, filename)。拡張子 .usda ならテキストで書く。
        export は Brep に UV トリムや書き出し先の属性を足すので、コピーを渡す（D21）。"""
        from .meshbody import is_mesh
        if fmt != "usd" or is_mesh(shape):      # 取り込んだメッシュ（D71）には BRep が無い
            raise NotImplementedError(fmt)
        ub.usd.export_brep(_fresh(shape.raw), path)

    # ------------------------------------------------------------ 形を読む
    def _raw_size(self, raw) -> float:
        lo, hi = (np.asarray(v, float) for v in ub.brep_bounding_box(raw))
        return float(np.linalg.norm(hi - lo))

    def _raw_volume(self, raw) -> float:
        return float(ub.compute_volume(raw))

    def _face_meshes(self, raw, chord: float, angle: float | None = None) -> list[FaceMesh]:
        faces = raw.faces()
        index = {hash(f): i for i, f in enumerate(faces)}
        kw = {} if angle is None else {"curve_angle_tolerance_deg": float(angle), "surface_angle_tolerance_deg": float(angle)}
        poly = ub.tessellate(raw, chord_height_tolerance=float(chord), **kw)
        pos, tri, fid = _extract(poly)
        if fid is None:                         # 三角形 → 面 の対応が無ければ重心で面を探す
            starts = _predict_runs(poly, pos, tri)
            fid = _face_runs(raw, index, pos[tri].mean(axis=1), len(faces), starts)
        out = []
        for i in range(len(faces)):
            t = tri[fid == i]
            used, inv = np.unique(t, return_inverse=True)
            out.append(FaceMesh(pos[used], inv.reshape(-1, 3)))
        return out

    def _raw_edges(self, raw) -> list[TopoEdge] | None:
        faces = raw.faces()
        findex = {hash(f): i for i, f in enumerate(faces)}
        pts, counts, handles = ub.tessellate_boundaries(raw)
        pts = np.asarray(pts, float).reshape(-1, 3)
        offs = np.concatenate([[0], np.cumsum(counts)]).astype(int)
        out = []
        for k, e in enumerate(handles):
            fs = [findex.get(hash(f), -1) for f in e.faces()]
            if len(fs) != 2 or fs[0] == fs[1] or -1 in fs:
                continue                        # 継ぎ目（同じ面の両側）や境界のエッジは選択対象にしない
            poly = pts[offs[k]:offs[k + 1]]
            if len(poly) < 2:
                continue
            t0, t1 = e.parameter_range()
            mid = np.asarray(e.point_at((t0 + t1) / 2), float)
            a, b = sorted(fs)
            out.append(TopoEdge(a, b, np.stack([poly[:-1], poly[1:]], axis=1), mid))
        return out


# ---------------------------------------------------------------- メッシュ結果の解釈


def _extract(poly: Any):
    """PolyBrep → (pos (n,3), tri (m,3), 面番号 None)。
    0c で確認: to_numpy_arrays() のキーは points / normals / faces / face_normals。
    faces は [3, i, j, k, 3, …] の形。三角形 → Brep の面 の対応は無い（呼び出し側で find_face する）。
    三角形の向きは外向き。"""
    d = poly.to_numpy_arrays()
    if "points" not in d or "faces" not in d:
        raise KernelError(f"to_numpy_arrays のキーを解釈できません: {sorted(d)}")
    pos = np.asarray(d["points"], float).reshape(-1, 3)
    tri, _ = triangles_from_prefixed(d["faces"])
    return pos, tri, None


def _predict_runs(poly, pos: np.ndarray, tri: np.ndarray) -> list[int] | None:
    """面が変わりそうな三角形の番号（区間の始まり）の予測（D65）。カーネルに聞かずに、三角形分割の性質から決める:
    - tessellate は面ごとに三角形を続けて出し、角ごとに「元の面の法線」を返せる（face_varying_normals）
    - 同じ面の三角形どうしは、共有する頂点で法線が一致する（違う面なら、なめらかにつながっていない限り一致しない）
    - 平らな面の三角形は、法線も平面の位置も同じ
    今の区間の (頂点, 法線) のどれかを共有するか、同じ平面の上にあれば同じ区間、そうでなければ新しい区間とする。
    予測は外れてよい（_face_runs が両端を find_face で確かめ、外れた区間だけ二分探索する）。使えない形なら None。"""
    try:
        d = poly.to_numpy_arrays(face_varying_normals=True)
    except Exception:  # noqa: BLE001
        return None
    N = np.asarray(d.get("normals", ()), float)
    if N.size != 9 * len(tri) or not len(tri):            # 三角形でない多角形がある: 角の並びが合わない
        return None
    CN = N.reshape(-1, 3, 3)
    size = float(np.linalg.norm(pos.max(0) - pos.min(0))) or 1.0
    flat = np.abs(CN - CN[:, :1]).max(axis=(1, 2)) == 0.0                 # 3 つの角の法線が完全に同じ = 平らな面の三角形
    off = np.round(np.einsum("ij,ij->i", CN[:, 0], pos[tri[:, 0]]) / (1e-7 * size)).astype(np.int64)
    # 角の (頂点, 法線) と平面 (法線, 位置) を整数の番号にしてから、区間を順にたどる
    rows = np.concatenate([tri.reshape(-1, 1).astype(float), CN.reshape(-1, 3)], axis=1)
    corner = np.unique(rows, axis=0, return_inverse=True)[1].reshape(-1, 3).tolist()
    prow = np.concatenate([CN[:, 0], off[:, None].astype(float)], axis=1)
    pid = np.where(flat, np.unique(prow, axis=0, return_inverse=True)[1].ravel(), -1).tolist()
    starts, cur, plane = [0], set(corner[0]), pid[0]
    for t in range(1, len(tri)):
        c0, c1, c2 = corner[t]
        pl = pid[t]
        if c0 not in cur and c1 not in cur and c2 not in cur and (pl < 0 or pl != plane):
            starts.append(t)
            cur, plane = set(), -1
        cur.update((c0, c1, c2))
        if plane < 0:
            plane = pl
    return starts


def _face_runs(raw, index: dict, centroids: np.ndarray, n_faces: int, starts: list[int] | None = None) -> np.ndarray:
    """三角形ごとの面番号。tessellate は面ごとに三角形を続けて出す（面の順は faces() と違う）ので、区間の境目だけを find_face で
    決める（三角形 1 枚ごとに聞くと遅い。D64）。
    starts（_predict_runs の予測、D65）があれば、予測した区間ごとに両端を聞き、同じ面なら区間全体をその面とする（面ごとに連続して
    並ぶので、両端が同じ面なら間も同じ面）。違えば、その区間の中を倍々 + 二分探索で分ける。
    最後に、区間の数と面の数が合わない・同じ面が離れた区間に出る（並び方の前提が崩れた）ときは、1 枚ずつ聞く方法に戻す。
    環境変数 AGENTCAD_CHECK_MATCH=1 のときは 1 枚ずつ聞いた結果とも比べる。"""
    m = len(centroids)
    cache: dict[int, int] = {}

    def F(i: int) -> int:
        if i not in cache:
            cache[i] = index.get(hash(ub.find_face(raw, tuple(map(float, centroids[i])))), -1)
        return cache[i]

    def split(s: int, e: int) -> list[tuple[int, int, int]]:
        """[s, e] を、面が変わる所で区間 (始め, 終わり, 面) に分ける（倍々に伸ばしてから二分探索）。"""
        out = []
        while s <= e:
            f = F(s)
            lo, step = s, 1
            while lo + step <= e and F(lo + step) == f:
                lo += step
                step *= 2
            hi = min(lo + step, e + 1)
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if F(mid) == f:
                    lo = mid
                else:
                    hi = mid
            out.append((s, lo, f))
            s = lo + 1
        return out

    runs: list[tuple[int, int, int]] = []
    if starts:
        bounds = list(starts) + [m]
        for a, b in zip(bounds[:-1], bounds[1:]):
            e = b - 1
            if F(a) == F(e):
                runs.append((a, e, F(a)))
            else:
                runs.extend(split(a, e))
    else:
        runs = split(0, m - 1)
    merged: list[list[int]] = []
    for a, e, f in runs:                                    # 予測で分かれすぎた区間（同じ面が続く）をつなぐ
        if merged and merged[-1][2] == f and merged[-1][1] == a - 1:
            merged[-1][1] = e
        else:
            merged.append([a, e, f])
    faces_seen = [f for _, _, f in merged]
    if len(set(faces_seen)) == len(faces_seen) == n_faces and -1 not in faces_seen:
        fid = np.empty(m, dtype=int)
        for a, e, f in merged:
            fid[a:e + 1] = f
    else:
        fid = np.array([F(i) for i in range(m)])
    if os.environ.get("AGENTCAD_CHECK_MATCH"):
        ref = np.array([index.get(hash(ub.find_face(raw, tuple(map(float, c)))), -1) for c in centroids])
        if not np.array_equal(ref, fid):
            raise AssertionError(f"三角形 → 面 が 1 枚ずつ聞いた結果と食い違いました（{int((ref != fid).sum())} 枚）")
    return fid
