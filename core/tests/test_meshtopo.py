"""メッシュから位相・履歴を導く層（cad.kernel.meshtopo）のテスト。

実カーネルが無くても検証できるよう、合成メッシュと「台本どおりの結果を返す」偽カーネルを使う。
"""
import math

import numpy as np
import pytest

from cad.kernel import FRONT, Plane, Profile, Segment
from cad.kernel.meshtopo import (FaceMesh, MeshTopoKernel, adjacency, classify_extrude, classify_revolve,
                                 fit_plane, group_loops, match_sources, nearest_edge, polyline, revolve_geom)
from cad.naming import name_result


def quad(*p):
    return FaceMesh(np.array(p, float), [[0, 1, 2], [0, 2, 3]])


def box(x0, x1, y0, y1, z0, z1) -> dict[str, FaceMesh]:
    """外向きの 6 面。"""
    return {
        "-z": quad((x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)),
        "+z": quad((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)),
        "-y": quad((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)),
        "+y": quad((x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)),
        "-x": quad((x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)),
        "+x": quad((x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)),
    }


def merge(*meshes) -> FaceMesh:
    pos, tri, off = [], [], 0
    for m in meshes:
        pos.append(m.pos); tri.append(m.tri + off); off += len(m.pos)
    return FaceMesh(np.vstack(pos), np.vstack(tri))


def grid(f, nu, nv) -> FaceMesh:
    """パラメータ曲面 f(u, v)（u, v ∈ [0,1]）の格子メッシュ。"""
    P = np.array([f(i / nu, j / nv) for i in range(nu + 1) for j in range(nv + 1)], float)
    t = []
    for i in range(nu):
        for j in range(nv):
            a, b, c, d = i * (nv + 1) + j, (i + 1) * (nv + 1) + j, (i + 1) * (nv + 1) + j + 1, i * (nv + 1) + j + 1
            t += [[a, b, c], [a, c, d]]
    return FaceMesh(P, t)


def rect_segs(w=10.0, h=5.0):
    pts = [(0, 0), (w, 0), (w, h), (0, h)]
    return [Segment("line", f"L{i}", [pts[i], pts[(i + 1) % 4]]) for i in range(4)]


BOX = box(0, 10, 0, 5, 0, 4)
ORDER = ["-z", "+z", "-y", "+y", "-x", "+x"]


def test_classify_extrude_box():
    faces = [BOX[k] for k in ORDER]
    h = classify_extrude(FRONT, [rect_segs()], 4.0, faces, 1e-6)
    assert [h[i] for i in range(6)] == [("cap", "start"), ("cap", "end"), ("side", "L0"), ("side", "L2"),
                                        ("side", "L3"), ("side", "L1")]


def test_fit_plane_orientation_and_curved_face():
    p = fit_plane(BOX["+z"], 1e-9)
    assert p.normal == pytest.approx((0, 0, 1)) and p.origin[2] == pytest.approx(4)
    assert fit_plane(BOX["-y"], 1e-9).normal == pytest.approx((0, -1, 0))
    cyl = grid(lambda u, v: (math.cos(u * math.pi), math.sin(u * math.pi), 3 * v), 12, 2)
    assert fit_plane(cyl, 1e-3) is None


def test_adjacency_of_box():
    faces = [BOX[k] for k in ORDER]
    edges = adjacency(faces, 1e-6)
    pairs = sorted((ORDER[e.fa], ORDER[e.fb]) for e in edges)
    assert len(edges) == 12
    assert all({a[1], b[1]} != {a[1]} for a, b in pairs)           # 向かい合う面は隣接しない
    assert ("-z", "-y") in pairs and ("+z", "+x") in pairs
    e = next(e for e in edges if {ORDER[e.fa], ORDER[e.fb]} == {"+z", "-y"})
    assert e.mid[1] == pytest.approx(0) and e.mid[2] == pytest.approx(4)


def slot_case():
    """A = 10×5×4 の箱、B = 上面を横切る溝（x 2..4, z 3..6）。A − B の結果の面（10 面）。"""
    A, B = box(0, 10, 0, 5, 0, 4), box(2, 4, -1, 6, 3, 6)
    front = merge(quad((0, 0, 0), (10, 0, 0), (10, 0, 3), (0, 0, 3)), quad((0, 0, 3), (2, 0, 3), (2, 0, 4), (0, 0, 4)),
                  quad((4, 0, 3), (10, 0, 3), (10, 0, 4), (4, 0, 4)))
    back = merge(quad((0, 5, 0), (0, 5, 3), (10, 5, 3), (10, 5, 0)), quad((0, 5, 3), (0, 5, 4), (2, 5, 4), (2, 5, 3)),
                 quad((4, 5, 3), (4, 5, 4), (10, 5, 4), (10, 5, 3)))
    out = {
        "bottom": A["-z"], "left": A["-x"], "right": A["+x"], "front": front, "back": back,
        "top1": quad((0, 0, 4), (2, 0, 4), (2, 5, 4), (0, 5, 4)),
        "top2": quad((4, 0, 4), (10, 0, 4), (10, 5, 4), (4, 5, 4)),
        "wall_x2": quad((2, 0, 3), (2, 5, 3), (2, 5, 4), (2, 0, 4)),
        "wall_x4": quad((4, 0, 3), (4, 0, 4), (4, 5, 4), (4, 5, 3)),
        "slot_bottom": quad((2, 0, 3), (4, 0, 3), (4, 5, 3), (2, 5, 3)),
    }
    expect = {"bottom": (0, "-z"), "left": (0, "-x"), "right": (0, "+x"), "front": (0, "-y"), "back": (0, "+y"),
              "top1": (0, "+z"), "top2": (0, "+z"), "wall_x2": (1, "-x"), "wall_x4": (1, "+x"),
              "slot_bottom": (1, "-z")}
    return A, B, out, expect


def test_match_sources_after_cut():
    A, B, out, expect = slot_case()
    keys = list(out)
    m = match_sources([out[k] for k in keys], [[A[k] for k in ORDER], [B[k] for k in ORDER]], 1e-6)
    got = {keys[i]: (src[0], ORDER[src[1]]) for i, src in m.items()}
    assert got == expect


def test_nearest_edge_and_new_face_for_fillet():
    r, n = 1.0, 6
    # 上面(+z)と前面(-y)の間のエッジ (y=0, z=4) を半径 1 で丸めた面
    fil = grid(lambda u, v: (10 * v, r - r * math.cos(u * math.pi / 2), 4 - r + r * math.sin(u * math.pi / 2)), n, 2)
    m = match_sources([fil, quad((0, 1, 4), (10, 1, 4), (10, 5, 4), (0, 5, 4))], [[BOX[k] for k in ORDER]], 1e-3)
    assert m[0] is None and m[1] == (0, ORDER.index("+z"))
    faces = [BOX[k] for k in ORDER]
    edges = {("e", i): e.segs for i, e in enumerate(adjacency(faces, 1e-6))
             if {ORDER[e.fa], ORDER[e.fb]} in ({"+z", "-y"}, {"+z", "+y"})}
    hit = nearest_edge(fil, edges)
    e = adjacency(faces, 1e-6)[hit[1]]
    assert {ORDER[e.fa], ORDER[e.fb]} == {"+z", "-y"}


def test_classify_revolve_quarter_ring():
    # 前平面上の矩形 r∈[1,2], y∈[0,3] を y 軸まわりに 90° 回転（+X から -Z へ）
    pts = [(1, 0), (2, 0), (2, 3), (1, 3)]
    segs = [Segment("line", f"L{i}", [pts[i], pts[(i + 1) % 4]]) for i in range(4)]
    axis = ((0, 0), (0, 1))
    th = lambda u: u * math.pi / 2
    P = lambda r, y, u: (r * math.cos(th(u)), y, -r * math.sin(th(u)))
    faces = {
        "bottom": grid(lambda u, v: P(1 + v, 0, u), 16, 2), "top": grid(lambda u, v: P(1 + v, 3, u), 16, 2),
        "outer": grid(lambda u, v: P(2, 3 * v, u), 16, 2), "inner": grid(lambda u, v: P(1, 3 * v, u), 16, 2),
        "cap0": grid(lambda u, v: P(1 + u, 3 * v, 0), 2, 2), "cap90": grid(lambda u, v: P(1 + u, 3 * v, 1), 2, 2),
    }
    keys = list(faces)
    h = classify_revolve(FRONT, [segs], axis, 90, [faces[k] for k in keys], 0.01)
    got = {keys[i]: o for i, o in h.items()}
    assert got == {"bottom": ("side", "L0"), "outer": ("side", "L1"), "top": ("side", "L2"),
                   "inner": ("side", "L3"), "cap0": ("cap", "start"), "cap90": ("cap", "end")}
    g = revolve_geom(FRONT, h, [segs], axis)
    radii = sorted(v["radius"] for v in g.values())
    assert radii == pytest.approx([1, 2]) and all(v["axis"][1] == pytest.approx((0, 1, 0)) for v in g.values())


def test_group_loops_holes_and_islands():
    outer = rect_segs(10, 10)
    hole = [Segment("circle", "C1", [(5, 5)], 2)]
    island = [Segment("circle", "C2", [(5, 5)], 1)]
    other = [Segment("line", f"M{i}", [p, q]) for i, (p, q) in
             enumerate(zip([(20, 0), (25, 0), (25, 5), (20, 5)], [(25, 0), (25, 5), (20, 5), (20, 0)]))]
    g = group_loops([hole, other, island, outer])
    tags = sorted((o[0].tag, sorted(h[0].tag for h in hs)) for o, hs in g)
    assert tags == [("C2", []), ("L0", ["C1"]), ("M0", [])]


# ---------------------------------------------------------------- 偽カーネルで MeshTopoKernel の配線を確認


class FakeKernel(MeshTopoKernel):
    """押し出し（凸多角形・円、穴なし）だけを実際に作り、ブーリアン/フィレットは台本の結果を返す。"""

    def __init__(self):
        self.script: list[list[FaceMesh]] = []

    def _raw_extrude(self, plane, groups, distance):
        faces = []
        for outer, holes in groups:
            if holes:
                raise ValueError("FakeKernel は穴に対応しません")
            n = np.asarray(plane.normal, float) * distance
            circle = len(outer) == 1 and outer[0].kind == "circle"
            poly = polyline(outer[0], 64) if circle else np.array([s.points[0] for s in outer], float)
            a = 0.5 * np.sum(poly[:, 0] * np.roll(poly[:, 1], -1) - poly[:, 1] * np.roll(poly[:, 0], -1))
            if (a < 0) != (distance < 0):
                poly = poly[::-1]
            P0 = np.array([plane.to_world(*p) for p in poly]); P1 = P0 + n
            k = len(poly)
            fan = [[0, i, i + 1] for i in range(1, k - 1)]
            faces.append(FaceMesh(P0, [t[::-1] for t in fan]))
            faces.append(FaceMesh(P1, fan))
            if circle:
                faces.append(FaceMesh(np.vstack([P0, P1]), [t for i in range(k) for t in
                                                             ([i, (i + 1) % k, k + (i + 1) % k], [i, k + (i + 1) % k, k + i])]))
            else:
                for i in range(k):
                    j = (i + 1) % k
                    faces.append(quad(P0[i], P0[j], P1[j], P1[i]))
        return faces

    def _raw_boolean(self, op, a, b):
        return self.script.pop(0)

    def _raw_fillet(self, raw, edge_points, radius):
        self.last_fillet_points = edge_points
        return self.script.pop(0)

    def _face_meshes(self, raw, chord):
        return raw

    def _raw_size(self, raw):
        P = np.vstack([f.pos for f in raw])
        return float(np.linalg.norm(P.max(0) - P.min(0)))

    def _raw_volume(self, raw):
        return sum(float(np.einsum("ij,ij->i", T[:, 0], np.cross(T[:, 1], T[:, 2])).sum()) for T in (f.T for f in raw)) / 6


def test_fake_kernel_passes_extrude_conformance():
    import test_kernel_conformance as C
    k = FakeKernel()
    for t in (C.test_r1_extrude_topology_and_history, C.test_r1_circle_gives_one_side_face_with_axis,
              C.test_r7_cap_planes, C.test_r6_tessellation_maps_triangles_to_faces):
        t(k)
    assert k.volume(C.box(k).shape) == pytest.approx(200)


def test_fake_kernel_boolean_and_fillet_naming():
    k = FakeKernel()
    a = k.extrude(Profile(FRONT, [rect_segs()]), 4.0)
    names_a = name_result("E1", k, a, [])
    assert set(names_a.faces) == {"E1/start", "E1/end", "E1/side/L0", "E1/side/L1", "E1/side/L2", "E1/side/L3"}
    # 溝のツール: x 2..4, z 3..6 を y 方向に押し出した箱（TOP 平面相当の向きで作る）
    plane = Plane((0, -1, 0), (1, 0, 0), (0, 1, 0))     # y_dir = n × x = (0,0,-1)
    tool_segs = [Segment("line", f"T{i}", [p, q]) for i, (p, q) in
                 enumerate(zip([(2, -3), (4, -3), (4, -6), (2, -6)], [(4, -3), (4, -6), (2, -6), (2, -3)]))]
    b = k.extrude(Profile(plane, [tool_segs]), 7.0)
    names_b = name_result("E2", k, b, [])
    _, _, out, expect = slot_case()
    k.script.append(list(out.values()))
    res = k.boolean("cut", a.shape, b.shape)
    names = name_result("E2", k, res, [names_a, names_b])
    by_face = {names.face_name(k.faces(res.shape)[i]): key for i, key in enumerate(out)}
    assert by_face["E1/end"] in ("top1", "top2") and by_face["E1/end#2"] in ("top1", "top2")
    assert by_face["E1/side/L0"] == "front" and by_face["E1/start"] == "bottom"
    assert {by_face[n] for n in names.faces if n.startswith("E2/")} == {"wall_x2", "wall_x4", "slot_bottom"}
    assert by_face["E2/side/T0"] == "slot_bottom"                 # 溝の底 = ツール断面の線 T0 の側面
    # #n の順序は形状（重心）で決まる: x の小さい top1 が先
    assert by_face["E1/end"] == "top1"
    # エッジ名から実エッジへ、フィレットへ
    e = names.find_edge("E[E1/end#2|E1/side/L0]").handle            # 溝の右側の上面と前面の間
    fil = grid(lambda u, v: (4 + 6 * v, 1 - math.cos(u * math.pi / 2), 3 + math.sin(u * math.pi / 2)), 6, 2)
    shrunk = [f for key, f in out.items() if key not in ("top2", "front")]
    k.script.append(shrunk + [quad((4, 1, 4), (10, 1, 4), (10, 5, 4), (4, 5, 4)), out["front"], fil])
    rf = k.fillet(res.shape, [e], 1.0)
    x, y, z = k.last_fillet_points[0]                               # カーネルにはエッジ上の点を渡す
    assert 4 < x < 10 and y == pytest.approx(0) and z == pytest.approx(4)
    nf = name_result("F1", k, rf, [names])
    assert "F1/fillet/E[E1/end#2|E1/side/L0]" in nf.faces
    assert {"E1/end", "E1/end#2", "E2/side/T0", "E2/side/T1", "E2/side/T3"} <= set(nf.faces)   # 名前は引き継がれる


def test_prefixed_polygon_array_from_usd_brep():
    from cad.kernel.meshtopo import triangles_from_prefixed
    tri, owner = triangles_from_prefixed([3, 0, 2, 3, 3, 2, 0, 1, 4, 0, 1, 2, 3])   # 0c レポートの先頭 + 四角形
    assert tri.tolist() == [[0, 2, 3], [2, 0, 1], [0, 1, 2], [0, 2, 3]] and owner.tolist() == [0, 1, 2, 2]
    with pytest.raises(ValueError):
        triangles_from_prefixed([3, 0, 1])


def test_fillet_face_is_not_taken_for_its_tangent_planes():
    """メッシュの弦誤差程度の許容値でも、接線近くのフィレットの点を平面上と判定しない。"""
    r = 0.5
    fil = grid(lambda u, v: (10 * v, r - r * math.cos(u * math.pi / 2), 4 - r + r * math.sin(u * math.pi / 2)), 4, 2)
    faces = [BOX[k] for k in ORDER]
    loose = match_sources([fil], [faces], 0.12)                   # 平面にも緩い許容値を使うと平面と誤判定
    strict = match_sources([fil], [faces], 0.12, flat_tol=1e-6)   # 平面は厳しく → どの面にも乗らない
    assert loose[0] is not None and strict[0] is None


def test_loops_listed_out_of_chain_order():
    """要素の並びがつながり順でなくても、正しい多角形・外形/穴の判定になる。"""
    from cad.kernel.meshtopo import _loop_poly, _area
    slot = [Segment("arc", "A1", [(0, 0), (0, 5), (0, -5)]), Segment("line", "L1", [(0, 5), (20, 5)]),
            Segment("arc", "A2", [(20, 0), (20, -5), (20, 5)]), Segment("line", "L2", [(20, -5), (0, -5)])]
    assert abs(_area(_loop_poly(slot))) == pytest.approx(200 + 25 * math.pi, rel=1e-2)
    hole = [Segment("circle", "C1", [(10, 0)], 2)]
    g = group_loops([hole, slot])
    assert len(g) == 1 and g[0][0] is slot and g[0][1] == [hole]


def test_weld_drops_unused_vertices_so_side_faces_are_planar():
    from cad.kernel.meshtopo import weld
    pos = np.array([(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1), (5, 5, 5)], float)   # 最後の点は使われない
    fm = weld(FaceMesh(pos, [[0, 1, 2], [0, 2, 3]]), 1e-9)
    assert len(fm.pos) == 4 and fit_plane(fm, 1e-9) is not None
