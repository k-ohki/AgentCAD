import math

import numpy as np
import pytest

from cad.assembly import (Assembly, GAxis, GPlane, GPoint, PartSource, StaticSource,
                          quat_from_axis_angle, quat_rotate)
from cad.kernel.mock import MockKernel
from cad.part import ExtrudeFeature, PartDocument, SketchFeature
from cad.sketch import Sketch

# ブロック: 上面 z=4、穴の軸 (5,2.5) 方向 z / ピン: 底面 z=0（法線 -z）、軸 z
BLOCK = StaticSource({"top": GPlane((0, 0, 4), (0, 0, 1)), "side": GPlane((0, 0, 0), (1, 0, 0)),
                      "hole": GAxis((5, 2.5, 0), (0, 0, 1)), "corner": GPoint((0, 0, 4))})
PIN = StaticSource({"bottom": GPlane((0, 0, 0), (0, 0, -1)), "axis": GAxis((0, 0, 0), (0, 0, 1)),
                    "side": GPlane((1, 0, 0), (1, 0, 0)), "tip": GPoint((0, 0, 0))})


def asm():
    a = Assembly({"block": BLOCK, "pin": PIN})
    b = a.add_component("block")
    # わざと傾けた・ずれた位置に置く
    p = a.add_component("pin", position=(1, 7, 9), rotation=quat_from_axis_angle((1, 1, 0), 20))
    return a, b, p


def world(a, cid, local):
    c = a.components[cid]
    return np.asarray(c.position) + quat_rotate(c.rotation, local)


def test_first_component_fixed_and_free_dof():
    a, b, p = asm()
    r = a.solve()
    assert a.components[b].fixed and not a.components[p].fixed
    assert r.dof == 6 and r.component_dof == {b: 0, p: 6} and r.state == "under"


def test_pin_in_hole():
    a, b, p = asm()
    a.mate("concentric", (b, "hole"), (p, "axis"))
    r = a.solve()
    assert r.status == "ok" and r.component_dof[p] == 2        # 軸方向スライド + 回転
    a.mate("coincident", (b, "top"), (p, "bottom"))
    r = a.solve()
    assert r.status == "ok" and r.component_dof[p] == 1 and r.redundant == []
    tip = world(a, p, (0, 0, 0))
    assert tip == pytest.approx([5, 2.5, 4], abs=1e-6)
    # 平面一致の既定は逆向き: ピン底面の法線(-z) が上面法線(+z) と逆 → ピンは上向き
    assert quat_rotate(a.components[p].rotation, (0, 0, 1)) == pytest.approx([0, 0, 1], abs=1e-6)


def test_distance_and_angle():
    a, b, p = asm()
    a.mate("concentric", (b, "hole"), (p, "axis"))
    a.mate("distance", (b, "top"), (p, "bottom"), value=3)
    a.mate("angle", (b, "side"), (p, "side"), value=30)
    r = a.solve()
    assert r.status == "ok" and r.dof == 0 and r.state == "full"
    assert world(a, p, (0, 0, 0))[2] == pytest.approx(7, abs=1e-6)
    x = quat_rotate(a.components[p].rotation, (1, 0, 0))
    assert math.degrees(math.acos(x @ [1, 0, 0])) == pytest.approx(30, abs=1e-5)


def test_conflict_and_redundant():
    a, b, p = asm()
    a.mate("concentric", (b, "hole"), (p, "axis"))
    a.mate("distance", (b, "top"), (p, "bottom"), value=3)
    bad = a.mate("distance", (b, "top"), (p, "bottom"), value=5)
    r = a.solve()
    assert r.status == "inconsistent" and r.conflicting == [bad]
    a.mates[bad].value = 3
    r = a.solve()
    assert r.status == "ok" and r.redundant == [bad] and r.state == "over"


def test_drag_respects_mates():
    a, b, p = asm()
    a.mate("concentric", (b, "hole"), (p, "axis"))
    a.solve()
    a.drag(p, (20, 20, 15))            # 軸から外れた位置へ引っ張る
    tip = world(a, p, (0, 0, 0))
    assert tip[:2] == pytest.approx([5, 2.5], abs=1e-6)   # 軸上に留まる
    assert tip[2] == pytest.approx(15, abs=1e-6)          # 軸方向には付いてくる


def test_undo_and_roundtrip():
    a, b, p = asm()
    with a.transaction("mate"):
        a.mate("concentric", (b, "hole"), (p, "axis"))
        a.solve()
    assert a.undo() == "mate" and a.mates == {}
    a.redo()
    a2 = Assembly.from_dict(a.to_dict(), a.sources)
    assert a2.to_dict() == a.to_dict()
    assert a2.solve().status == "ok"


def test_mates_follow_part_regeneration():
    """パートの寸法を変えても、永続名で参照した合致が維持される。"""
    k = MockKernel()
    blk = PartDocument(k)
    s = Sketch(); s.rect(0, 0, 10, 5)
    s1 = blk.add(SketchFeature(sketch=s)); e1 = blk.add(ExtrudeFeature(sketch=s1, distance=4))
    hs = Sketch(); c = hs.circle((5, 2.5), 1)
    s2 = blk.add(SketchFeature(sketch=hs)); e2 = blk.add(ExtrudeFeature(sketch=s2, distance=4, op="cut"))
    blk.regenerate()
    pin = PartDocument(k)
    ps = Sketch(); pc = ps.circle((0, 0), 1)
    p1 = pin.add(SketchFeature(sketch=ps)); pe = pin.add(ExtrudeFeature(sketch=p1, distance=8))
    pin.regenerate()

    a = Assembly({"block.part": PartSource(blk), "pin.part": PartSource(pin)})
    B = a.add_component("block.part"); P = a.add_component("pin.part", position=(3, 3, 3))
    a.mate("concentric", (B, f"{e2}/side/{c}"), (P, f"{pe}/side/{pc}"))
    a.mate("coincident", (B, f"{e1}/end"), (P, f"{pe}/start"))
    assert a.solve().status == "ok"
    assert a.components[P].position == pytest.approx((5, 2.5, 4), abs=1e-6)
    # 穴の位置と板厚を変更 → 再生成 → 合致を解き直すとピンが追従
    hs.entities[hs.entities[c].center].x = 7
    with blk.transaction("move hole"):
        blk.get(e1).distance = 6
    assert a.solve().status == "ok"
    assert a.components[P].position == pytest.approx((7, 2.5, 6), abs=1e-6)


# ---------------------------------------------------------------- 2026-11 レビューで追加

def test_orientation_is_enforced_from_any_start():
    """向き（aligned）は初期姿勢に関係なく守られる。旧実装は近い側へ収束するだけだった。"""
    for start in ((1, 0, 0, 0), quat_from_axis_angle((1, 0, 0), 180), quat_from_axis_angle((1, 2, 0), 150)):
        a = Assembly({"block": BLOCK, "pin": PIN})
        b = a.add_component("block"); p = a.add_component("pin", position=(3, 1, 9), rotation=start)
        a.mate("concentric", (b, "hole"), (p, "axis"))
        a.mate("coincident", (b, "top"), (p, "bottom"))          # 既定: 面を突き合わせる
        r = a.solve()
        assert r.status == "ok" and r.flipped == []
        assert quat_rotate(a.components[p].rotation, (0, 0, 1)) == pytest.approx([0, 0, 1], abs=1e-6)
    # aligned=True を明示すると裏返しで付く
    a = Assembly({"block": BLOCK, "pin": PIN})
    b = a.add_component("block"); p = a.add_component("pin", position=(3, 1, 9))
    a.mate("coincident", (b, "top"), (p, "bottom"), aligned=True)
    assert a.solve().status == "ok"
    assert quat_rotate(a.components[p].rotation, (0, 0, 1)) == pytest.approx([0, 0, -1], abs=1e-6)


def test_plane_axis_mate_keeps_axis_in_plane():
    a, b, p = asm()
    a.mate("coincident", (b, "top"), (p, "axis"))                # 軸が平面内に寝る（引数の順は自由）
    r = a.solve()
    assert r.status == "ok" and r.component_dof[p] == 4
    d = quat_rotate(a.components[p].rotation, (0, 0, 1))
    assert d[2] == pytest.approx(0, abs=1e-7) and world(a, p, (0, 0, 0))[2] == pytest.approx(4, abs=1e-7)


def test_invalid_mates_are_rejected_at_creation():
    a, b, p = asm()
    with pytest.raises(ValueError):
        a.mate("concentric", (b, "top"), (p, "axis"))            # 平面と軸は同心にできない
    with pytest.raises(ValueError):
        a.mate("parallel", (b, "corner"), (p, "axis"))           # 点には方向がない
    with pytest.raises(ValueError):
        a.mate("coincident", (b, "top"), (b, "side"))            # 同じ部品
    assert a.mates == {}


def test_failed_solve_keeps_pose():
    a, b, p = asm()
    a.mate("concentric", (b, "hole"), (p, "axis")); a.solve()
    pose = (a.components[p].position, a.components[p].rotation)
    a.mate("distance", (b, "corner"), (p, "tip"), value=6); a.mate("distance", (b, "corner"), (p, "tip"), value=7)
    r = a.solve()
    assert r.status == "inconsistent" and r.conflicting == ["Mate3"]
    assert (a.components[p].position, a.components[p].rotation) == pose


# ---------------------------------------------------------------- 追加の合致（正接・ロック・軸の距離）

ROLLER = StaticSource({"surface": GAxis((0, 0, 0), (0, 1, 0), 2.0), "end": GPlane((0, 0, 0), (0, -1, 0)),
                       "center": GPoint((0, 0, 0))})


def test_tangent_plane_cylinder():
    a = Assembly({"block": BLOCK, "roller": ROLLER})
    b = a.add_component("block"); r = a.add_component("roller", position=(3, 1, 9),
                                                      rotation=quat_from_axis_angle((1, 0, 0), 25))
    a.mate("tangent", (r, "surface"), (b, "top"))               # 引数の順は自由
    res = a.solve()
    assert res.status == "ok" and res.component_dof[r] == 4
    assert a.components[r].position[2] == pytest.approx(6)       # 上面 z=4 + 半径 2（置いた側に付く）
    assert quat_rotate(a.components[r].rotation, (0, 1, 0))[2] == pytest.approx(0, abs=1e-7)
    a.drag(r, (8, 3, 20))                                        # 転がしても面から離れない
    assert a.components[r].position[2] == pytest.approx(6, abs=1e-6)
    with pytest.raises(ValueError):
        a.mate("tangent", (b, "top"), (r, "end"))                # 平面同士は正接にできない


def test_lock_keeps_relative_pose():
    a, b, p = asm()
    a.components[b].fixed = False
    base = a.add_component("block", id="base", fixed=True)
    lock = a.mate("lock", (b, ""), (p, ""))
    r = a.solve()
    assert r.status == "ok" and r.dof == 6                       # 2 部品が一体で動く
    rel0 = world(a, p, (0, 0, 0)) - world(a, b, (0, 0, 0))
    d0 = np.linalg.norm(rel0)
    a.mate("coincident", (base, "top"), (b, "side")); a.mate("angle", (base, "side"), (b, "top"), value=40)
    assert a.solve().status == "ok"
    rel = world(a, p, (0, 0, 0)) - world(a, b, (0, 0, 0))
    assert np.linalg.norm(rel) == pytest.approx(d0, abs=1e-7)
    # 相対姿勢: block 座標系で見た pin の位置と軸が変わらない
    inv = lambda q: (q[0], -q[1], -q[2], -q[3])
    qb = a.components[b].rotation
    assert quat_rotate(inv(qb), rel) == pytest.approx([1, 7, 9], abs=1e-6)
    z_in_b = quat_rotate(inv(qb), quat_rotate(a.components[p].rotation, (0, 0, 1)))
    assert z_in_b == pytest.approx(quat_rotate(quat_from_axis_angle((1, 1, 0), 20), (0, 0, 1)), abs=1e-6)
    a2 = Assembly.from_dict(a.to_dict(), a.sources)
    assert a2.mates[lock].data == a.mates[lock].data and a2.solve().status == "ok"


def test_axis_distances():
    a, b, p = asm()
    a.mate("distance", (b, "hole"), (p, "axis"), value=3)        # 平行な 2 軸を 3 離す
    r = a.solve()
    assert r.status == "ok" and r.component_dof[p] == 3
    tip = world(a, p, (0, 0, 0))
    assert np.hypot(tip[0] - 5, tip[1] - 2.5) == pytest.approx(3)
    assert abs(quat_rotate(a.components[p].rotation, (0, 0, 1))[2]) == pytest.approx(1)
    a2, b2, p2 = asm()
    a2.mate("distance", (p2, "tip"), (b2, "hole"), value=2)      # 点と軸の距離
    r = a2.solve()
    tip = world(a2, p2, (0, 0, 0))
    assert r.status == "ok" and r.component_dof[p2] == 5 and np.hypot(tip[0] - 5, tip[1] - 2.5) == pytest.approx(2)


def test_door_hinge_far_from_origin_opens_90():
    """D70: 部品の原点から遠い軸（m 単位の装置の扉のヒンジ）まわりに、角度合致で一度に 90° 開ける。
    以前は許容誤差が変数の大きさだけで決まり、回転と移動の変数の大きさも食い違って「矛盾」になっていた。"""
    frame = StaticSource({"pin": GAxis((-470, 1000, 480), (0, 1, 0)), "top": GPlane((0, 1150, 0), (0, 1, 0)),
                          "slope": GPlane((0, 0, 0), (math.sqrt(.5), 0, math.sqrt(.5)))})
    door = StaticSource({"pin": GAxis((-470, 1150, 480), (0, 1, 0)), "bottom": GPlane((0, 1150, 0), (0, -1, 0)),
                         "front": GPlane((0, 0, 465), (0, 0, 1))})
    a = Assembly({"frame": frame, "door": door})
    f, d = a.add_component("frame"), a.add_component("door")
    a.mate("concentric", (f, "pin"), (d, "pin"))
    a.mate("coincident", (f, "top"), (d, "bottom"))
    ang = a.mate("angle", (f, "slope"), (d, "front"), value=45)            # 閉じた状態
    assert a.solve().status == "ok"
    a.mates[ang].value = 135                                                # 90° 開く
    r = a.solve()
    assert r.status == "ok", r
    assert world(a, d, (440, 1400, 430)) == pytest.approx([-420, 1400, 1390], abs=1e-3)   # 自由端は手前へ
    assert world(a, d, (-470, 1400, 480)) == pytest.approx([-470, 1400, 480], abs=1e-6)   # ヒンジの軸は動かない
