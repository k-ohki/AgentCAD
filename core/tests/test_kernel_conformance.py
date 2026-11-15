"""カーネル適合テスト: どのカーネル実装も満たすべき条件（docs/SPEC.md 6章 R1〜R7）。

アダプタ（cad/kernel/usd_brep.py など）を書いたら、そのマシンで
    python -m pytest tests/test_kernel_conformance.py -v
を実行する。全部通れば上位層（フィーチャーツリー・命名・合致）はそのまま動く。
"""
import numpy as np
import pytest

from cad.kernel import FRONT, Profile, Segment, available_kernels

KERNELS = available_kernels()


@pytest.fixture(params=sorted(KERNELS))
def k(request):
    return KERNELS[request.param]()


def rect(w=10.0, h=5.0, x=0.0, y=0.0):
    pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    return [Segment("line", f"L{i}", [pts[i], pts[(i + 1) % 4]]) for i in range(4)]


def box(k, d=4.0):
    return k.extrude(Profile(FRONT, [rect()]), d)


def test_r1_extrude_topology_and_history(k):
    res = box(k)
    faces = k.faces(res.shape)
    assert len(faces) == 6
    origins = sorted(res.history[f] for f in faces)          # R5: すべての面に由来がある
    assert origins == sorted([("cap", "start"), ("cap", "end")] + [("side", f"L{i}") for i in range(4)])
    edges = k.edges(res.shape)
    assert len(edges) == 12 and all(a in faces and b in faces and a != b for _, a, b in edges)


def test_r1_circle_gives_one_side_face_with_axis(k):
    res = k.extrude(Profile(FRONT, [[Segment("circle", "C1", [(2.0, 3.0)], 1.5)]]), 5.0)
    side = [f for f in k.faces(res.shape) if res.history[f] == ("side", "C1")]
    assert len(side) == 1
    origin, direction = k.face_axis(res.shape, side[0])
    assert np.abs(np.cross(direction, (0, 0, 1))).max() < 1e-9          # 軸は押し出し方向
    assert np.cross(np.subtract(origin, (2, 3, 0)), direction) == pytest.approx([0, 0, 0], abs=1e-9)


def test_r2_boolean_history_maps_every_face_to_an_input(k):
    a = box(k)
    b = k.extrude(Profile(FRONT, [[Segment("circle", "C1", [(5.0, 2.5)], 1.0)]]), 4.0)
    res = k.boolean("cut", a.shape, b.shape)
    inputs = set(k.faces(a.shape)) | set(k.faces(b.shape))
    for f in k.faces(res.shape):
        kind, src = res.history[f]
        assert kind == "modified" and src in inputs
    survived = {src for _, src in res.history.values()}
    assert set(k.faces(a.shape)) <= survived                    # 箱の 6 面はすべて残る


def test_r3_fillet_reports_new_face_per_edge(k):
    a = box(k)
    e = k.edges(a.shape)[0][0]
    res = k.fillet(a.shape, [e], 0.5)
    kinds = [res.history[f] for f in k.faces(res.shape)]
    assert kinds.count(("fillet", e)) == 1
    assert sum(1 for kind, _ in kinds if kind == "modified") == 6


def test_r7_cap_planes(k):
    res = box(k, 4.0)
    end = next(f for f in k.faces(res.shape) if res.history[f] == ("cap", "end"))
    pl = k.face_plane(res.shape, end)
    assert pl.normal == pytest.approx((0, 0, 1)) and pl.origin[2] == pytest.approx(4.0)


def test_r6_tessellation_maps_triangles_to_faces(k):
    res = box(k)
    m = k.tessellate(res.shape)
    assert set(m.face_handles) == set(k.faces(res.shape))
    assert m.face_of_triangle.shape == (len(m.indices),)
    if not k.geometric:
        pytest.skip("形状を計算しないカーネル")
    assert len(m.indices) >= 12 and m.indices.max() < len(m.positions)
    assert set(np.unique(m.face_of_triangle)) == set(range(6))          # どの面にも三角形がある
    assert m.positions.min(axis=0) == pytest.approx((0, 0, 0)) and m.positions.max(axis=0) == pytest.approx((10, 5, 4))


def test_geometry_volume(k):
    if not k.geometric:
        pytest.skip("形状を計算しないカーネル")
    a = box(k)
    assert k.volume(a.shape) == pytest.approx(200.0, rel=1e-6)
    b = k.extrude(Profile(FRONT, [[Segment("circle", "C1", [(5.0, 2.5)], 1.0)]]), 4.0)
    assert k.volume(k.boolean("cut", a.shape, b.shape).shape) == pytest.approx(200 - 4 * np.pi, rel=1e-4)


def test_r1_revolve_history_volume_and_axis(k):
    if not k.geometric:
        pytest.skip("形状を計算しないカーネル")
    pts = [(1.0, 0.0), (2.0, 0.0), (2.0, 3.0), (1.0, 3.0)]
    segs = [Segment("line", f"L{i}", [pts[i], pts[(i + 1) % 4]]) for i in range(4)]
    res = k.revolve(Profile(FRONT, [segs]), ((0.0, 0.0), (0.0, 1.0)), 90.0)
    faces = k.faces(res.shape)
    assert sorted(res.history[f] for f in faces) == sorted(
        [("cap", "start"), ("cap", "end")] + [("side", f"L{i}") for i in range(4)])
    assert k.volume(res.shape) == pytest.approx(np.pi / 4 * 3 * 3, rel=1e-3)
    outer = next(f for f in faces if res.history[f] == ("side", "L1"))
    origin, direction = k.face_axis(res.shape, outer)
    assert abs(np.dot(direction, (0, 1, 0))) == pytest.approx(1) and k.face_radius(res.shape, outer) == pytest.approx(2)
