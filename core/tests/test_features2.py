"""F2: 押し出し状態（中間平面・全貫通）、参照平面、直線/円形パターン、ミラー、フィーチャーの移動。"""
import math

import numpy as np
import pytest

from cad.api.rpc import Api, RpcError
from cad.doc.workspace import Workspace
from cad.kernel.base import FRONT, Profile, Segment
from cad.part.features import Placement, place_profile


@pytest.fixture
def api():
    return Api(Workspace("prism"))


def block(api, w=100, h=60, t=20):
    """正面に w×h の板（+Z へ厚さ t）。"""
    did = api.call("doc.new")["doc"]
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Front"}})
    api.call("sketch.add", {"doc": did, "kind": "rect", "points": [[0, 0], [w, h]]})
    api.call("feature.add", {"doc": did, "type": "extrude", "sketch": "Sketch1", "distance": t})
    return did


def sketch_on(api, did, plane, kind="circle", pts=((30, 20), (35, 20))):
    st = api.call("sketch.new", {"doc": did, "plane": plane})
    api.call("sketch.add", {"doc": did, "kind": kind, "points": [list(p) for p in pts]})
    api.call("sketch.end", {"doc": did})
    return st["sketch"]["id"]


def bbox(st):
    return [[round(v, 4) for v in p] for p in st["mesh"]["bbox"]]


def node(st, fid):
    return next(t for t in st["tree"] if t["id"] == fid)


CYL = math.pi * 25          # 半径 5 の円の面積（簡易カーネルは 64 角形なので少し小さい）


# ---------------------------------------------------------------- 押し出し状態
def test_extrude_mid_plane(api):
    did = block(api)
    sk = sketch_on(api, did, {"plane": "Front"})
    st = api.call("feature.add", {"doc": did, "type": "extrude", "sketch": sk, "distance": 50, "end": "mid"})
    assert node(st, "Extrude2")["state"] == "ok"
    assert bbox(st) == [[0, 0, -25], [100, 60, 25]]
    p = api.call("feature.params", {"doc": did, "id": "Extrude2"})
    assert p["options"]["end"] == "mid" and [m["key"] for m in p["option_meta"]] == ["end", "op", "reverse"]
    assert p["option_meta"][0]["choices"][1] == ["mid", "中間平面"]
    with pytest.raises(RpcError, match="使えない値"):
        api.call("feature.set", {"doc": did, "id": "Extrude2", "key": "end", "value": "upto"})


def test_extrude_through_all(api):
    did = block(api)
    sk = sketch_on(api, did, {"plane": "Front"})
    st = api.call("feature.add", {"doc": did, "type": "extrude", "sketch": sk, "end": "through"})
    assert bbox(st)[1][2] == pytest.approx(20.2)                       # 板を抜けるまで（+ 余裕 1%）
    assert st["volume"] == pytest.approx(120000 + CYL * 20.2, rel=2e-3)
    p = api.call("feature.params", {"doc": did, "id": "Extrude2"})
    assert [q["key"] for q in p["params"]] == []                        # 全貫通では距離を出さない
    st = api.call("feature.set", {"doc": did, "id": "Extrude2", "key": "reverse", "value": True})
    assert node(st, "Extrude2")["state"] == "error" and "貫通するボディがありません" in node(st, "Extrude2")["message"]
    st = api.call("feature.set", {"doc": did, "id": "Extrude2", "key": "end", "value": "through_both"})
    assert node(st, "Extrude2")["state"] == "ok"
    assert (bbox(st)[0][2], bbox(st)[1][2]) == pytest.approx((-0.2, 20.2))
    # 板の厚さを変えると追従する
    st = api.call("feature.edit", {"doc": did, "id": "Extrude1", "key": "distance", "value": 40})
    assert (bbox(st)[0][2], bbox(st)[1][2]) == pytest.approx((-0.4, 40.4))


def test_cut_direction_and_reverse_on_mock():
    """カットは平面の法線と逆（材料の中へ）、反転で法線の向き。距離 0 以下はエラー。"""
    from cad.kernel.mock import MockKernel
    from cad.part import ExtrudeFeature, PartDocument, SketchFeature
    from cad.sketch import Sketch
    k = MockKernel()
    seen = []
    orig = k.extrude
    k.extrude = lambda prof, dist: (seen.append((prof.plane.origin, dist)), orig(prof, dist))[1]
    d = PartDocument(k)
    sk = Sketch(); sk.rect(0, 0, 10, 10)
    s = d.add(SketchFeature(sketch=sk))
    d.add(ExtrudeFeature(sketch=s, distance=8))
    d.add(ExtrudeFeature(sketch=s, distance=6, op="cut"))
    d.add(ExtrudeFeature(sketch=s, distance=6, op="cut", reverse=True))
    d.add(ExtrudeFeature(sketch=s, distance=6, op="cut", end="mid"))
    bad = d.add(ExtrudeFeature(sketch=s, distance=0))
    st = d.regenerate()
    assert [x for x in seen] == [((0, 0, 0), 8), ((0, 0, 0), -6), ((0, 0, 0), 6), ((0.0, 0.0, 3.0), -6)]
    assert st[bad].state == "error" and "正の値" in st[bad].message


# ---------------------------------------------------------------- 参照平面
def test_reference_plane_and_sketch_on_it(api):
    did = block(api)
    st = api.call("feature.add", {"doc": did, "type": "plane", "base": {"plane": "Front"}, "offset": 30})
    pid = st["created"][0]
    assert pid == "Plane1" and node(st, pid)["type_label"] == "参照平面"
    pl = next(p for p in st["planes"] if p["ref"] == {"feature": pid})
    assert pl["origin"] == [0, 0, 30] and pl["normal"] == [0, 0, 1]
    assert [p["name"] for p in st["planes"]] == ["正面", "平面", "右側面", "Plane1"]
    sk = sketch_on(api, did, {"feature": pid})
    st = api.call("feature.add", {"doc": did, "type": "extrude", "sketch": sk, "distance": 5})
    assert bbox(st)[1][2] == 35
    st = api.call("feature.edit", {"doc": did, "id": pid, "key": "offset", "value": 50})      # 平面を動かすと追従
    assert bbox(st)[1][2] == 55
    st = api.call("feature.set", {"doc": did, "id": pid, "key": "base", "value": {"face": "Extrude1/end"}})
    assert bbox(st)[1][2] == 75                                                             # 板の上面（z=20）から 50
    st = api.call("feature.set", {"doc": did, "id": pid, "key": "reverse", "value": True})
    assert bbox(st)[0][2] == -30
    # 平面を消すとスケッチと押し出しがエラーになる（ツリーは壊れない）
    st = api.call("feature.delete", {"doc": did, "id": pid})
    assert node(st, sk)["state"] == "error" and "参照" in node(st, sk)["message"]
    with pytest.raises(RpcError, match="参照平面 Plane9 が無効"):
        api.call("sketch.new", {"doc": did, "plane": {"feature": "Plane9"}})
    with pytest.raises(RpcError, match="参照の形が不正"):
        api.call("sketch.new", {"doc": did, "plane": {"plane": "Front", "face": "x"}})


# ---------------------------------------------------------------- パターン・ミラー
def boss(api, did, **kw):
    sk = sketch_on(api, did, {"face": "Extrude1/end"}, **kw)
    return api.call("feature.add", {"doc": did, "type": "extrude", "sketch": sk, "distance": 10})["created"][0]


def test_linear_pattern(api):
    did = block(api)
    b = boss(api, did)                                      # (30, 20) に半径 5、高さ 10
    v0 = api.call("doc.state", {"doc": did, "mesh": False})["volume"]
    st = api.call("feature.add", {"doc": did, "type": "lpattern", "seeds": [b], "spacing": 20, "count": 3})
    lp = st["created"][0]
    one = v0 - 120000
    assert node(st, lp)["state"] == "ok" and st["volume"] == pytest.approx(120000 + 3 * one)
    names = st["mesh"]["face_names"]
    assert f"{lp}~1/end" in names and f"{lp}~2/end" in names and f"{lp}~3/end" not in names
    assert node(st, lp)["seeds"] == [b]
    assert api.call("feature.params", {"doc": did, "id": lp})["options"]["direction"] == {"axis": "X"}
    cp = api.call("feature.add", {"doc": did, "type": "cpattern", "seeds": [b]})["created"][0]     # 既定の軸 = スケッチ平面の法線
    assert api.call("feature.params", {"doc": did, "id": cp})["options"]["axis"] == {"axis": "Z"}
    api.call("feature.delete", {"doc": did, "id": cp})
    # 2 方向（格子）
    st = api.call("feature.set", {"doc": did, "id": lp, "key": "direction2", "value": {"axis": "Y"}})
    st = api.call("feature.edit", {"doc": did, "id": lp, "key": "count2", "value": 2})
    assert st["volume"] == pytest.approx(120000 + 6 * one)
    p = api.call("feature.params", {"doc": did, "id": lp})
    assert {q["key"] for q in p["params"]} == {"spacing", "count", "spacing2", "count2"}
    st = api.call("feature.set", {"doc": did, "id": lp, "key": "direction2", "value": None})
    assert st["volume"] == pytest.approx(120000 + 3 * one)
    assert {q["key"] for q in api.call("feature.params", {"doc": did, "id": lp})["params"]} == {"spacing", "count"}
    # 向き: 板の辺（直線エッジ）を使う。反転すると −X へ
    m = api.call("doc.state", {"doc": did})["mesh"]
    edge = next(n for n in m["edge_names"] if "Extrude1/start" in n and "Extrude1/side" in n)
    st = api.call("feature.set", {"doc": did, "id": lp, "key": "direction", "value": {"edge": edge}})
    assert node(st, lp)["state"] == "ok" and st["volume"] == pytest.approx(120000 + 3 * one)
    st = api.call("feature.set", {"doc": did, "id": lp, "key": "direction", "value": {"axis": "X"}})
    st = api.call("feature.set", {"doc": did, "id": lp, "key": "reverse", "value": True})
    assert bbox(st)[0][0] == pytest.approx(30 - 40 - 5, abs=0.02)
    # 元を変えると全部変わる / 元を消すとエラー
    st = api.call("feature.edit", {"doc": did, "id": b, "key": "distance", "value": 20})
    assert st["volume"] == pytest.approx(120000 + 6 * one)
    with pytest.raises(RpcError, match="元にできません"):
        api.call("feature.add", {"doc": did, "type": "lpattern", "seeds": ["Sketch1"]})
    st = api.call("feature.delete", {"doc": did, "id": b})
    assert node(st, lp)["state"] == "error"


def test_circular_pattern(api):
    did = block(api, 100, 100, 10)
    b = boss(api, did, pts=((80, 50), (85, 50)))
    one = api.call("doc.state", {"doc": did, "mesh": False})["volume"] - 100000
    # 板の中心 (50, 50) を通る Z 向きの軸が無いので、まず原点まわり Z 軸: (80,50) → 4 個
    st = api.call("feature.add", {"doc": did, "type": "cpattern", "seeds": [b], "axis": {"axis": "Z"}, "count": 4})
    cp = st["created"][0]
    assert node(st, cp)["state"] == "ok" and st["volume"] == pytest.approx(100000 + 4 * one)
    assert bbox(st)[0][0] == pytest.approx(-85, abs=0.05) and bbox(st)[0][1] == pytest.approx(-85, abs=0.05)
    # 180° の範囲に 3 個 → 0°, 90°, 180°
    st = api.call("feature.edit", {"doc": did, "id": cp, "key": "angle", "value": 180})
    st = api.call("feature.edit", {"doc": did, "id": cp, "key": "count", "value": 3})
    assert st["volume"] == pytest.approx(100000 + 3 * one)
    assert bbox(st)[0][0] == pytest.approx(-85, abs=0.05) and bbox(st)[0][1] == pytest.approx(-55, abs=0.05)
    st = api.call("feature.edit", {"doc": did, "id": cp, "key": "count", "value": 1})
    assert node(st, cp)["state"] == "error" and "2 以上" in node(st, cp)["message"]


def test_mirror_feature(api):
    did = block(api)
    # 円弧を含む非対称の形（D 字）: 鏡映で円弧の向きが正しく反転すること
    st = api.call("sketch.new", {"doc": did, "plane": {"face": "Extrude1/end"}})
    sk = st["sketch"]["id"]
    a = api.call("sketch.add", {"doc": did, "kind": "arc", "points": [[20, 20], [20, 10], [20, 30]]})["created"][0]
    e = next(x for x in api.call("doc.state", {"doc": did, "mesh": False})["sketch"]["entities"] if x["id"] == a)
    api.call("sketch.add", {"doc": did, "kind": "line", "points": [e["end"], e["start"]]})
    st = api.call("feature.add", {"doc": did, "type": "extrude", "sketch": sk, "distance": 10})
    b = st["created"][0]
    assert node(st, b)["state"] == "ok"
    one = st["volume"] - 120000
    assert one == pytest.approx(math.pi * 100 / 2 * 10, rel=1e-2) and bbox(st)[1][0] == 100
    st = api.call("feature.add", {"doc": did, "type": "mirror", "seeds": [b], "plane": {"plane": "Right"}})
    mi = st["created"][0]
    assert node(st, mi)["state"] == "ok" and st["volume"] == pytest.approx(120000 + 2 * one)
    assert bbox(st)[0][0] == pytest.approx(-30, abs=0.01)             # x=20 から +10 張り出す半円 → −30 まで
    # ミラーの平面を参照平面（x = 50）に変える → 写しは板の中（x = 70〜80）に収まる
    api.call("feature.add", {"doc": did, "type": "plane", "base": {"plane": "Right"}, "offset": 50})
    api.call("feature.move", {"doc": did, "id": "Plane1", "index": 0})
    st = api.call("feature.set", {"doc": did, "id": mi, "key": "plane", "value": {"feature": "Plane1"}})
    assert node(st, mi)["state"] == "ok" and bbox(st)[0][0] == 0 and st["volume"] == pytest.approx(120000 + 2 * one)


def test_place_profile_mirror_keeps_shape():
    prof = Profile(FRONT, [[Segment("arc", "A1", [(0, 0), (5, 0), (0, 5)]), Segment("line", "L1", [(0, 5), (0, 0)]),
                            Segment("line", "L2", [(0, 0), (5, 0)])]])
    m = place_profile(prof, Placement.reflect((0, 0, 0), (1, 0, 0)))
    arc = m.loops[0][0]
    to3 = lambda p: np.array(m.plane.to_world(*p))
    c, s, e = (to3(p) for p in arc.points)
    assert c.tolist() == [0, 0, 0] and sorted([s.tolist(), e.tolist()]) == [[-5, 0, 0], [0, 5, 0]]
    # 新しい平面は右手系のまま、円弧は反時計回りのまま（始点 → 終点が法線まわりに正の向き）
    (cu, cv), (su, sv), (eu, ev) = arc.points
    assert (su - cu) * (ev - cv) - (sv - cv) * (eu - cu) > 0
    assert np.allclose(np.cross(m.plane.normal, m.plane.x_dir), m.plane.y_dir)
    r = Placement.rotate((1, 0, 0), (0, 0, 1), 90)
    assert np.allclose(r.point((2, 0, 0)), (1, 1, 0)) and not r.mirror


def test_feature_move(api):
    did = block(api)
    b = boss(api, did)
    st = api.call("feature.move", {"doc": did, "id": b, "index": 0})
    assert [t["id"] for t in st["tree"]][0] == b and node(st, b)["state"] == "error"     # スケッチより上では作れない
    assert st["undo"] == f"{b} を移動"
    st = api.call("edit.undo", {"doc": did})
    assert node(st, b)["state"] == "ok"
    with pytest.raises(RpcError, match="範囲外"):
        api.call("feature.move", {"doc": did, "id": b, "index": 9})


def test_new_features_roundtrip(api, tmp_path):
    did = block(api)
    b = boss(api, did)
    api.call("feature.add", {"doc": did, "type": "plane", "base": {"face": "Extrude1/end"}, "offset": 5})
    api.call("feature.add", {"doc": did, "type": "lpattern", "seeds": [b], "count": 2})
    api.call("feature.add", {"doc": did, "type": "cpattern", "seeds": [b], "axis": {"axis": "Z"}})
    st = api.call("feature.add", {"doc": did, "type": "mirror", "seeds": [b]})
    path = api.call("doc.save", {"doc": did, "path": str(tmp_path / "p")})["path"]
    st2 = Api(Workspace("prism")).call("doc.open", {"path": path})
    assert [(t["id"], t["state"]) for t in st2["tree"]] == [(t["id"], t["state"]) for t in st["tree"]]
    assert st2["volume"] == pytest.approx(st["volume"])


def test_view_rollback_for_picking_references(api):
    """既にあるフィーチャーの参照を選び直すときは、そのフィーチャーの直前の形を見せる（Undo・保存には残らない）。"""
    did = block(api)
    b = boss(api, did)
    lp = api.call("feature.add", {"doc": did, "type": "lpattern", "seeds": [b], "count": 3})["created"][0]
    full = api.call("doc.state", {"doc": did})
    st = api.call("view.rollback", {"doc": did, "id": lp})
    assert node(st, lp)["state"] == "rolled_back" and st["volume"] < full["volume"] and st["rollback"] is None
    assert st["undo"] == full["undo"] and st["modified"] == full["modified"]
    edge = next(n for n in st["mesh"]["edge_names"] if "Extrude1/end" in n and "Extrude1/side" in n)
    st = api.call("feature.set", {"doc": did, "id": lp, "key": "direction", "value": {"edge": edge}})
    assert node(st, lp)["state"] == "ok" and st["volume"] == pytest.approx(full["volume"])      # 巻き戻しは自動で外れる
    api.call("view.rollback", {"doc": did, "id": lp})
    assert api.call("view.rollback", {"doc": did})["volume"] == pytest.approx(full["volume"])
    api.call("sketch.begin", {"doc": did, "id": "Sketch1"})
    with pytest.raises(RpcError, match="編集中"):
        api.call("view.rollback", {"doc": did, "id": lp})


# ---------------------------------------------------------------- 面取り・シェル
def mock_block():
    from cad.kernel.mock import MockKernel
    from cad.part import ExtrudeFeature, PartDocument, SketchFeature
    from cad.sketch import Sketch
    d = PartDocument(MockKernel())
    sk = Sketch(); sk.rect(0, 0, 100, 60)
    s = d.add(SketchFeature(sketch=sk))
    d.add(ExtrudeFeature(sketch=s, distance=20))
    d.regenerate()
    return d


def test_chamfer_and_shell_features_on_mock():
    from cad.part import ChamferFeature, ShellFeature
    d = mock_block()
    edge = next(n for n in d.names.edges if "Extrude1/end" in n)
    c = d.add(ChamferFeature(edges=[edge], distance=2))
    sh = d.add(ShellFeature(faces=["Extrude1/end"], thickness=3))
    st = d.regenerate()
    assert st[c].state == "ok" and st[sh].state == "ok", (st[c], st[sh])
    names = list(d.names.faces)
    assert f"{c}/chamfer/{edge}" in names                       # 面取りの面は元のエッジの名前で呼ぶ
    assert "Extrude1/end" not in names                          # 取り除いた面
    assert f"{sh}/wall/Extrude1/start" in names                  # 壁の面は元の面の名前で呼ぶ
    d.get(sh).faces = ["Extrude1/nothing"]
    st = d.regenerate()
    assert st[sh].state == "error"
    d.get(sh).faces = []; d.get(sh).thickness = 0
    assert d.regenerate()[sh].state == "error"


def test_chamfer_shell_api_and_prism_message(api):
    did = block(api)
    m = api.call("doc.state", {"doc": did})["mesh"]
    edge = next(n for n in m["edge_names"] if "Extrude1/end" in n)
    st = api.call("feature.add", {"doc": did, "type": "chamfer", "edges": [edge], "distance": 2})
    c = st["created"][0]
    assert c == "Chamfer1" and node(st, c)["state"] == "error" and "面取りに対応していません" in node(st, c)["message"]
    p = api.call("feature.params", {"doc": did, "id": c})
    assert p["type_label"] == "面取り" and [q["key"] for q in p["params"]] == ["distance"] and p["options"]["edges"] == [edge]
    st = api.call("feature.add", {"doc": did, "type": "shell", "faces": ["Extrude1/end"]})
    sh = st["created"][0]
    assert sh == "Shell1" and "シェルに対応していません" in node(st, sh)["message"]
    p = api.call("feature.params", {"doc": did, "id": sh})
    assert [q["key"] for q in p["params"]] == ["thickness"] and p["params"][0]["label"] == "厚さ"
    assert [(m_["key"], m_["kind"]) for m_ in p["option_meta"]] == [("faces", "faces"), ("outward", "bool")]
    st = api.call("feature.set", {"doc": did, "id": sh, "key": "faces", "value": ["Extrude1/end", "Extrude1/start", "Extrude1/end"]})
    assert api.call("feature.params", {"doc": did, "id": sh})["options"]["faces"] == ["Extrude1/end", "Extrude1/start"]
    with pytest.raises(RpcError, match="面の名前"):
        api.call("feature.set", {"doc": did, "id": sh, "key": "faces", "value": "Extrude1/end"})
    with pytest.raises(RpcError, match="エッジを選んで"):
        api.call("feature.add", {"doc": did, "type": "chamfer", "edges": []})
