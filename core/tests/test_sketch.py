import math

import pytest

from cad.sketch import Sketch


def P(s, pid):
    p = s.entities[pid]; return p.x, p.y


def test_rect_dimensions_fully_defined():
    s = Sketch()
    ls = s.rect(0, 0, 9, 4.5)
    p0 = s.entities[ls[0]].p1
    # 頂点の一致（rect は点を共有しているので不要）、原点固定 + 幅/高さ
    s.constrain("fix", p0)
    s.constrain("length", ls[0], value=10)
    s.constrain("length", ls[1], value=5)
    r = s.solve()
    assert r.status == "ok"
    assert r.dof == 0 and r.state == "full"
    p2 = s.entities[ls[1]].p2
    assert P(s, p2) == pytest.approx((10, 5), abs=1e-7)
    assert all(v == "full" for v in r.entity_state.values())


def test_under_defined_reports_dof_and_free_entities():
    s = Sketch()
    ls = s.rect(0, 0, 10, 5)
    r = s.solve()
    assert r.status == "ok"
    assert r.dof == 4          # 8変数 - 4拘束（位置2 + 幅 + 高さが自由）
    assert r.state == "under"


def test_redundant_constraint_detected():
    s = Sketch()
    ls = s.rect(0, 0, 10, 5)
    k = s.constrain("parallel", ls[0], ls[2])   # 両方水平なので冗長
    r = s.solve()
    assert r.state == "over"
    assert k in r.redundant


def test_conflicting_constraint_detected():
    s = Sketch()
    l = s.line((0, 0), (10, 0))
    s.constrain("horizontal", l)
    s.constrain("length", l, value=10)
    k = s.constrain("vertical", l)
    r = s.solve()
    assert r.status == "inconsistent" and r.state == "over"
    assert r.conflicting == [k]
    # 解けないときは形状を変えない
    assert P(s, s.entities[l].p2) == pytest.approx((10, 0))


def test_min_movement_for_under_defined():
    s = Sketch()
    l = s.line((0, 0), (9, 1))
    s.constrain("horizontal", l)
    s.solve()
    a, b = s.entities[l].p1, s.entities[l].p2
    # 最小ノルム解: 両端が半分ずつ動いて y=0.5 で揃う
    assert P(s, a)[1] == pytest.approx(0.5, abs=1e-7)
    assert P(s, b)[1] == pytest.approx(0.5, abs=1e-7)
    assert P(s, a)[0] == pytest.approx(0, abs=1e-7)


def test_circle_tangent_and_radius():
    s = Sketch()
    l = s.line((0, 0), (10, 0))
    s.constrain("fix", s.entities[l].p1); s.constrain("fix", s.entities[l].p2)
    c = s.circle((5, 3), 2)
    s.constrain("tangent", l, c)
    s.constrain("radius", c, value=4)
    r = s.solve()
    assert r.status == "ok"
    cy = P(s, s.entities[c].center)[1]
    assert cy == pytest.approx(4, abs=1e-7)   # 線の上側に接したまま


def test_angle_and_dimension_edit():
    s = Sketch()
    l1 = s.line((0, 0), (10, 0)); l2 = s.line((0, 0), (5, 5))
    s.constrain("coincident", s.entities[l1].p1, s.entities[l2].p1)
    s.constrain("fix", s.entities[l1].p1); s.constrain("horizontal", l1)
    k = s.constrain("angle", l1, l2)
    assert s.constraints[k].value == pytest.approx(45)
    s.constraints[k].value = 30
    s.constrain("length", l2, value=10)
    assert s.solve().status == "ok"
    x, y = P(s, s.entities[l2].p2)
    assert math.degrees(math.atan2(y, x)) == pytest.approx(30, abs=1e-6)


def test_arc_implicit_radius_and_point_on_circle():
    s = Sketch()
    a = s.arc((0, 0), (5, 0), (0, 4))
    r = s.solve()
    e = s.entities[a]
    rs = math.dist(P(s, e.center), P(s, e.start)); re_ = math.dist(P(s, e.center), P(s, e.end))
    assert r.status == "ok" and rs == pytest.approx(re_, abs=1e-7)


def test_drag_moves_free_point_but_not_fixed():
    s = Sketch()
    l = s.line((0, 0), (10, 0))
    s.constrain("fix", s.entities[l].p1)
    s.constrain("length", l, value=10)
    p2 = s.entities[l].p2
    s.drag(p2, 0, 20)
    assert P(s, s.entities[l].p1) == pytest.approx((0, 0), abs=1e-7)
    x, y = P(s, p2)
    assert math.hypot(x, y) == pytest.approx(10, abs=1e-6)
    assert y > 9  # 目標方向へ回転している


def test_profiles_and_serialization_roundtrip():
    s = Sketch()
    s.rect(0, 0, 10, 5)
    s.circle((20, 0), 2)
    s.line((30, 0), (40, 0))  # 開いた線はプロファイルにならない
    loops = s.profiles()
    assert sorted(len(l) for l in loops) == [1, 4]
    s2 = Sketch.from_dict(s.to_dict())
    assert s2.to_dict() == s.to_dict()
    assert s2.solve().status == "ok"


# ---------------------------------------------------------------- 2026-11 レビューで追加

def slot():
    """長穴（2直線 + 2円弧、端点で正接）。一般的な 3D CAD の典型的なスケッチ。"""
    s = Sketch()
    c1 = s.point(0, 0); c2 = s.point(20, 0)
    a1 = s.arc(c1, (0, 5), (0, -5)); a2 = s.arc(c2, (20, -5), (20, 5))
    A1, A2 = s.entities[a1], s.entities[a2]
    l1 = s.line(A1.end, A2.start); l2 = s.line(A2.end, A1.start)
    s.constrain("fix", c1); s.constrain("horizontal", c1, c2)
    for l in (l1, l2):
        s.constrain("tangent", l, a1); s.constrain("tangent", l, a2)
    s.constrain("equal", a1, a2)
    kd = s.constrain("hdistance", c1, c2, value=20); kr = s.constrain("radius", a1, value=5)
    return s, kd, kr, (a1, a2, l1, l2)


def test_slot_with_endpoint_tangency_is_fully_defined():
    s, kd, kr, (a1, a2, l1, l2) = slot()
    r = s.solve()
    assert (r.status, r.dof, r.state) == ("ok", 0, "full")       # 旧実装は「自由度4・過剰定義」と誤判定
    s.constraints[kd].value = 40; s.constraints[kr].value = 8
    assert s.solve().status == "ok"
    assert P(s, s.entities[a2].center) == pytest.approx((40, 0), abs=1e-7)
    assert s.entities[a2].radius == pytest.approx(8)
    assert P(s, s.entities[l1].p2) == pytest.approx((40, -8), abs=1e-6)
    assert s.profiles() == [[a1, l1, a2, l2]] or len(s.profiles()[0]) == 4


def chain(n):
    s = Sketch(); prev = None
    for i in range(n):
        ls = s.rect(i * 12, 0, i * 12 + 10.3, 5.2)
        s.constrain("length", ls[0], value=10); s.constrain("length", ls[1], value=5)
        p0 = s.entities[ls[0]].p1
        if prev is None:
            s.constrain("fix", p0)
        else:
            s.constrain("hdistance", prev, p0, value=12); s.constrain("vdistance", prev, p0, value=0)
        prev = p0
    return s


def test_zero_distance_dimension_does_not_break_dof():
    r = chain(20).solve()                    # 旧実装: abs() の微分が 0 になり「自由度3・過剰定義」
    assert (r.status, r.dof, r.state) == ("ok", 0, "full")


def test_performance_budget():
    """対話操作の目安: 400変数のスケッチでドラッグ1回 100ms 以内（実測 ~6ms。旧実装は ~6700ms）。"""
    import time
    s = chain(50); s.solve()
    pid = list(s.entities)[2]
    t = time.perf_counter()
    for k in range(5):
        s.drag(pid, 11 + k, 6, diagnose=False)
    assert (time.perf_counter() - t) / 5 < 0.1
    t = time.perf_counter(); r = s.solve()
    assert time.perf_counter() - t < 0.5 and r.status == "ok"


def test_random_perturbation_recovers():
    import random
    from cad.sketch import Point
    rnd = random.Random(3)
    for _ in range(40):
        s, *_ = slot(); s.solve()
        for e in s.entities.values():
            if isinstance(e, Point) and not e.fixed:
                e.x += rnd.uniform(-3, 3); e.y += rnd.uniform(-3, 3)
        r = s.solve()
        assert r.status == "ok" and r.dof == 0


def test_symmetric_and_point_horizontal():
    s = Sketch()
    ax = s.line((0, -5), (0, 5), construction=True)
    s.constrain("fix", s.entities[ax].p1); s.constrain("fix", s.entities[ax].p2)
    a = s.point(-3, 1); b = s.point(4, 2)
    s.constrain("symmetric", a, b, ax)
    r = s.solve()
    (xa, ya), (xb, yb) = P(s, a), P(s, b)
    assert r.status == "ok" and xa == pytest.approx(-xb) and ya == pytest.approx(yb) and r.dof == 2


def test_reference_types_are_validated():
    s = Sketch()
    l = s.line((0, 0), (1, 0)); c = s.circle((0, 0), 1)
    with pytest.raises(ValueError):
        s.constrain("parallel", l, c)
    with pytest.raises(ValueError):
        s.constrain("radius", l)
    assert s.constraints == {}


def test_delete_removes_dependents():
    s = Sketch()
    l = s.line((0, 0), (1, 0)); k = s.constrain("horizontal", l)
    s.delete(s.entities[l].p1)
    assert l not in s.entities and k not in s.constraints


# ---------------------------------------------------------------- 円同士の正接・フィレット・トリム

def test_circle_circle_tangent_external_and_internal():
    s = Sketch()
    c1 = s.circle((0, 0), 5); c2 = s.circle((9, 0), 3); c3 = s.circle((1.5, 0), 3)
    for c in (c1, c2, c3):
        s.constrain("horizontal", s.entities[c1].center, s.entities[c].center) if c != c1 else s.constrain("fix", s.entities[c].center)
    s.constrain("radius", c1, value=5); s.constrain("radius", c2, value=3); s.constrain("radius", c3, value=3)
    s.constrain("tangent", c1, c2)        # 外接（近い方を選ぶ）
    s.constrain("tangent", c1, c3)        # 内接
    r = s.solve()
    assert (r.status, r.dof) == ("ok", 0)
    assert P(s, s.entities[c2].center) == pytest.approx((8, 0), abs=1e-7)
    assert P(s, s.entities[c3].center) == pytest.approx((2, 0), abs=1e-7)


def test_tangent_arcs_sharing_endpoint_do_not_lose_rank():
    s = Sketch()
    a1 = s.arc((0, 0), (5, 0), (-5, 0))
    a2 = s.arc((8, 0.5), s.entities[a1].start, (11, 0))      # 始点を共有
    s.constrain("fix", s.entities[a1].center); s.constrain("fix", s.entities[a1].start)
    k = s.constrain("tangent", a1, a2)
    r = s.solve()
    assert r.status == "ok" and k not in r.redundant
    assert P(s, s.entities[a2].center)[1] == pytest.approx(0, abs=1e-7)    # 中心と接点が一直線


def test_fillet_keeps_dimensions_via_virtual_sharp():
    s = Sketch()
    ls = s.rect(0, 0, 10, 5)
    s.constrain("fix", s.entities[ls[0]].p1)
    kw = s.constrain("length", ls[0], value=10); s.constrain("length", ls[1], value=5)
    assert s.solve().dof == 0
    arc = s.fillet(ls[0], ls[1], 2)                      # 右下の角
    r = s.solve()
    assert (r.status, r.dof, r.state) == ("ok", 0, "full")
    A = s.entities[arc]
    assert A.radius == pytest.approx(2) and P(s, A.center) == pytest.approx((8, 2), abs=1e-7)
    assert P(s, s.entities[ls[0]].p2) == pytest.approx((8, 0), abs=1e-7)
    assert len(s.profiles()) == 1 and len(s.profiles()[0]) == 5
    # 幅の寸法は仮想交点までの距離として生きている
    s.constraints[kw].value = 20
    assert s.solve().status == "ok"
    assert P(s, s.entities[arc].center) == pytest.approx((18, 2), abs=1e-7)
    with pytest.raises(ValueError):
        s.fillet(ls[2], ls[3], 50)                       # 半径が大きすぎる
    with pytest.raises(ValueError):
        s.fillet(ls[0], ls[2], 1)                        # 角を共有していない


def test_trim_line_between_end_and_both_sides():
    s = Sketch()
    h = s.line((0, 0), (10, 0)); v1 = s.line((3, -2), (3, 2)); v2 = s.line((7, -2), (7, 2))
    assert s.trim(h, 1, 0) == [h]                        # 左端〜x=3 を切る
    assert P(s, s.entities[h].p1) == pytest.approx((3, 0))
    s.trim(v1, 3, 1)                                     # 交点より上を切る
    assert P(s, s.entities[v1].p2) == pytest.approx((3, 0))
    s2 = Sketch()
    h = s2.line((0, 0), (10, 0)); s2.line((3, -2), (3, 2)); s2.line((7, -2), (7, 2))
    a, b = s2.trim(h, 5, 0)                              # 中央を切る → 2 本に分かれる
    assert P(s2, s2.entities[a].p2) == pytest.approx((3, 0)) and P(s2, s2.entities[b].p1) == pytest.approx((7, 0))
    assert s2.solve().status == "ok"
    assert s2.trim(b, 9, 0) == [] and b not in s2.entities    # 残りに交点が無ければ丸ごと消える


def test_trim_circle_becomes_arc_and_isolated_is_deleted():
    from cad.sketch import Arc
    s = Sketch()
    c = s.circle((0, 0), 5); s.line((-10, 0), (10, 0)); lone = s.line((20, 20), (30, 20))
    assert s.trim(c, 0, 5) == [c]                        # 上半分を切る
    A = s.entities[c]
    assert isinstance(A, Arc) and P(s, A.start) == pytest.approx((-5, 0)) and P(s, A.end) == pytest.approx((5, 0))
    r = s.solve()
    assert r.status == "ok"
    n = len(s.entities)
    assert s.trim(lone, 25, 20) == [] and lone not in s.entities and len(s.entities) == n - 3
