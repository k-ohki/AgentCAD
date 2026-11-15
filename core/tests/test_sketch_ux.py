"""スケッチの使い勝手: 寸法の種類と配置、要素のドラッグ、エンティティ変換。"""
import math

import numpy as np
import pytest

from cad.api.rpc import Api, RpcError
from cad.doc.workspace import Workspace
from cad.kernel.base import FRONT, TOP
from cad.sketch import Sketch
from cad.sketch.fit import fit, project


@pytest.fixture
def api():
    return Api(Workspace("prism"))


def new_sketch(api, plane="Front"):
    did = api.call("doc.new")["doc"]
    api.call("sketch.new", {"doc": did, "plane": {"plane": plane}})
    return did


def con(st, cid):
    return next(c for c in st["sketch"]["constraints"] if c["id"] == cid)


def ent(st, eid):
    return next(e for e in st["sketch"]["entities"] if e["id"] == eid)


# ---------------------------------------------------------------- 寸法
def test_circle_gets_diameter_arc_gets_radius(api):
    did = new_sketch(api)
    c = api.call("sketch.add", {"doc": did, "kind": "circle", "points": [[0, 0], [5, 0]]})["created"][0]
    st = api.call("sketch.dimension", {"doc": did, "refs": [c]})
    k = con(st, st["created"][0])
    assert k["type"] == "diameter" and k["value"] == pytest.approx(10)
    st = api.call("feature.edit", {"doc": did, "id": "Sketch1", "key": k["id"], "value": 30})
    assert ent(st, c)["radius"] == pytest.approx(15)
    st = api.call("sketch.dimension", {"doc": did, "refs": [c], "type": "radius"})
    assert con(st, st["created"][0])["type"] == "radius"
    a = api.call("sketch.add", {"doc": did, "kind": "arc", "points": [[50, 0], [55, 0], [50, 5]]})["created"][0]
    st = api.call("sketch.dimension", {"doc": did, "refs": [a]})
    assert con(st, st["created"][0])["type"] == "radius"


def test_line_horizontal_vertical_aligned_and_label(api):
    did = new_sketch(api)
    ln = api.call("sketch.add", {"doc": did, "kind": "line", "points": [[0, 0], [30, 40]]})["created"][0]
    st = api.call("sketch.dimension", {"doc": did, "refs": [ln], "type": "hdistance", "label": [0, 12]})
    h = con(st, st["created"][0])
    assert h["type"] == "hdistance" and h["value"] == pytest.approx(30) and h["label"] == [0, 12]
    st = api.call("sketch.dimension", {"doc": did, "refs": [ln], "type": "vdistance"})
    assert con(st, st["created"][0])["value"] == pytest.approx(40)
    st = api.call("sketch.dimension", {"doc": did, "refs": [ln], "type": "hdistance"})   # 2 つ目は冗長
    assert st["created"][0] in st["sketch"]["solve"]["redundant"] + st["sketch"]["solve"]["conflicting"]
    api.call("edit.undo", {"doc": did})
    # 文字の位置を動かす → Undo で戻る
    st = api.call("sketch.dim_label", {"doc": did, "id": h["id"], "label": [5, -7]})
    assert con(st, h["id"])["label"] == [5, -7] and st["undo"] == "寸法を移動"
    st = api.call("edit.undo", {"doc": did})
    assert con(st, h["id"])["label"] == [0, 12]
    with pytest.raises(RpcError, match="水平・垂直の寸法は"):
        api.call("sketch.dimension", {"doc": did, "refs": [ln, ln], "type": "hdistance"})


def test_circle_to_line_distance_uses_center(api):
    did = new_sketch(api)
    ln = api.call("sketch.add", {"doc": did, "kind": "line", "points": [[0, 0], [50, 0]]})["created"][0]
    c = api.call("sketch.add", {"doc": did, "kind": "circle", "points": [[20, 15], [23, 15]]})["created"][0]
    st = api.call("sketch.dimension", {"doc": did, "refs": [c, ln]})
    k = con(st, st["created"][0])
    assert k["type"] == "distance" and k["value"] == pytest.approx(15) and k["refs"][0] == ent(st, c)["center"]


def test_parallel_lines_distance(api):
    did = new_sketch(api)
    a = api.call("sketch.add", {"doc": did, "kind": "line", "points": [[0, 0], [50, 0]]})["created"][0]
    b = api.call("sketch.add", {"doc": did, "kind": "line", "points": [[0, 12], [50, 12]]})["created"][0]
    st = api.call("sketch.dimension", {"doc": did, "refs": [a, b], "type": "distance"})
    k = con(st, st["created"][0])
    assert k["type"] == "distance" and k["value"] == pytest.approx(12) and k["refs"][1] == b


def test_dimension_label_survives_save(api, tmp_path):
    did = new_sketch(api)
    ln = api.call("sketch.add", {"doc": did, "kind": "line", "points": [[0, 0], [30, 0]]})["created"][0]
    cid = api.call("sketch.dimension", {"doc": did, "refs": [ln], "label": [1, 2]})["created"][0]
    path = api.call("doc.save", {"doc": did, "path": str(tmp_path / "p")})["path"]
    api2 = Api(Workspace("prism"))
    d2 = api2.call("doc.open", {"path": path})["doc"]
    st = api2.call("sketch.begin", {"doc": d2, "id": "Sketch1"})
    assert con(st, cid)["label"] == [1, 2]


# ---------------------------------------------------------------- ドラッグ
def test_drag_line_translates(api):
    did = new_sketch(api)
    ln = api.call("sketch.add", {"doc": did, "kind": "line", "points": [[0, 0], [30, 0]]})["created"][0]
    e = ent(api.call("doc.state", {"doc": did, "mesh": False}), ln)
    for x, y in ((12, 3), (15, 5)):
        r = api.call("sketch.drag", {"doc": did, "entity": ln, "grab": [10, 0], "x": x, "y": y})
        assert "entities" in r and "tree" not in r
    st = api.call("sketch.drag", {"doc": did, "entity": ln, "grab": [10, 0], "x": 15, "y": 5, "final": True})
    p1, p2 = ent(st, e["p1"]), ent(st, e["p2"])
    assert (p1["x"], p1["y"], p2["x"], p2["y"]) == pytest.approx((5, 5, 35, 5), abs=1e-6)
    assert st["undo"] == "線をドラッグ"
    st = api.call("edit.undo", {"doc": did})
    assert ent(st, e["p1"])["x"] == pytest.approx(0)


def test_drag_constrained_line_slides(api):
    """水平・長さ寸法の付いた線は、縦にしか動かない…ではなく形を保ったまま近づく（最小二乗）。"""
    did = new_sketch(api)
    ln = api.call("sketch.add", {"doc": did, "kind": "line", "points": [[0, 0], [30, 0]]})["created"][0]
    api.call("sketch.dimension", {"doc": did, "refs": [ln]})
    st = api.call("sketch.drag", {"doc": did, "entity": ln, "grab": [15, 0], "x": 25, "y": 8, "final": True})
    e = ent(st, ln)
    p1, p2 = ent(st, e["p1"]), ent(st, e["p2"])
    assert math.dist((p1["x"], p1["y"]), (p2["x"], p2["y"])) == pytest.approx(30)
    assert p1["y"] == pytest.approx(8, abs=1e-6) and p2["y"] == pytest.approx(8, abs=1e-6)


def test_drag_circle_resizes_and_arc(api):
    did = new_sketch(api)
    c = api.call("sketch.add", {"doc": did, "kind": "circle", "points": [[0, 0], [5, 0]]})["created"][0]
    st = api.call("sketch.drag", {"doc": did, "entity": c, "x": 0, "y": 8, "final": True})
    assert ent(st, c)["radius"] == pytest.approx(8)
    a = api.call("sketch.add", {"doc": did, "kind": "arc", "points": [[50, 0], [55, 0], [50, 5]]})["created"][0]
    st = api.call("sketch.drag", {"doc": did, "entity": a, "x": 50, "y": 9, "final": True})
    e = ent(st, a)
    assert e["radius"] == pytest.approx(9, abs=0.01)                          # 端点も動くので目標にほぼ近づく
    s, c0 = ent(st, e["start"]), ent(st, e["center"])
    assert math.hypot(s["x"] - c0["x"], s["y"] - c0["y"]) == pytest.approx(e["radius"], abs=1e-6)   # 端点は円の上


# ---------------------------------------------------------------- エンティティ変換
def test_fit_shapes():
    def poly(pts):
        pts = np.asarray(pts, float)
        return np.stack([pts[:-1], pts[1:]], 1)
    k, g = fit(poly([[0, 0], [5, 0], [10, 0]])[::-1])
    assert k == "line" and sorted([g[0], g[2]]) == [0, 10]
    ang = np.linspace(0, np.pi / 2, 9)
    cw = np.c_[np.cos(ang), np.sin(ang)][::-1] * 4 + [1, 2]          # 時計回りに並んだ円弧
    k, g = fit(poly(cw))
    assert k == "arc" and g == pytest.approx([1, 2, 5, 2, 1, 6])        # 反時計回り（始点 0°, 終点 90°）
    full = np.linspace(0, 2 * np.pi, 33)
    k, g = fit(poly(np.c_[np.cos(full), np.sin(full)] * 3))
    assert k == "circle" and g == pytest.approx([0, 0, 3], abs=1e-9)
    with pytest.raises(ValueError, match="直線・円・円弧ではない"):
        fit(poly(np.c_[2 * np.cos(full), np.sin(full)]))                  # 楕円
    # 弦の中点を含む折れ線（カーネルの表示用）でも、半径は外側の点で正しく取る
    pts = np.c_[np.cos(full), np.sin(full)] * 8
    mids = (pts[:-1] + pts[1:]) / 2
    k, g = fit(poly(np.stack([pts[:-1], mids], 1).reshape(-1, 2).tolist() + [pts[-1].tolist()]))
    assert k == "circle" and g == pytest.approx([0, 0, 8], abs=1e-9)
    seg3 = np.array([[[0, 0, 0], [0, 0, 5]]])                              # 正面に垂直な線
    with pytest.raises(ValueError, match="垂直なエッジ"):
        fit(project(seg3, FRONT))
    assert project(np.array([[[1, 7, 2], [3, 7, 4]]]), TOP)[0].tolist() == [[1, -2], [3, -4]]   # 平面の y = -Z


def face_edges(st, face):
    m = st["mesh"]
    fi = m["face_names"].index(face)
    return [n for n, f in zip(m["edge_names"], m["edge_faces"]) if fi in f]


def test_convert_entities_follows_model(api):
    did = api.call("doc.demo")["doc"]
    st = api.call("doc.state", {"doc": did})
    edges = face_edges(st, "Extrude1/start") + face_edges(st, "Extrude2/end")[:1]
    st = api.call("sketch.new", {"doc": did, "plane": {"plane": "Front"}})
    sid = st["sketch"]["id"]
    with pytest.raises(RpcError, match="エッジか面を選んで"):
        api.call("sketch.convert", {"doc": did, "edges": []})
    st = api.call("sketch.convert", {"doc": did, "edges": edges})
    sk = st["sketch"]
    kinds = sorted(ent(st, e)["type"] for e in st["created"])
    assert kinds == ["circle", "line", "line", "line", "line"] and st["warnings"] == []
    assert sk["solve"]["state"] == "full" and sk["profiles"] == 2
    assert {c["source"] for c in sk["constraints"] if c["type"] == "on_edge"} == set(edges)
    xs = sorted({round(ent(st, p)["x"], 6) for e in st["created"] if ent(st, e)["type"] == "line"
                 for p in (ent(st, e)["p1"], ent(st, e)["p2"])})
    assert xs == [0, 100]
    with pytest.raises(RpcError, match="すべて変換済み"):
        api.call("sketch.convert", {"doc": did, "edges": edges[:1]})
    # モデルを変える（幅 100 → 120）と、投影した線が追従する
    api.call("sketch.end", {"doc": did})
    st = api.call("variable.set", {"doc": did, "name": "width", "expr": "120"})
    assert next(t for t in st["tree"] if t["id"] == sid)["state"] == "ok"
    st = api.call("sketch.begin", {"doc": did, "id": sid})
    xs = sorted({round(e["x"], 6) for e in st["sketch"]["entities"] if e["type"] == "point"})
    assert xs[0] == 0 and xs[-1] == 120 and st["sketch"]["solve"]["state"] == "full"
    # 投影元が消えたら警告（前の位置のまま）
    api.call("sketch.end", {"doc": did})
    st = api.call("feature.suppress", {"doc": did, "id": "Extrude2", "value": True})
    t = next(t for t in st["tree"] if t["id"] == sid)
    assert t["state"] == "warning" and "投影元のエッジ" in t["message"]


def test_convert_rejects_inclined_circle(api):
    did = api.call("doc.demo")["doc"]
    st = api.call("doc.state", {"doc": did})
    circle = face_edges(st, "Extrude2/end")[:1]
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Right"}})       # 円を横から見ると線分（向きが合わない）
    with pytest.raises(RpcError, match="変換できるエッジがありません"):
        api.call("sketch.convert", {"doc": did, "edges": circle})


def test_on_edge_kind_in_sketch():
    sk = Sketch()
    a = sk.arc((0, 0), (5, 0), (0, 5))
    cid = sk.constrain("on_edge", a)
    assert sk.constraints[cid].data == pytest.approx([0, 0, 5, 0, 0, 5])
    r = sk.solve()
    assert r.state == "full" and r.dof == 0 and not r.redundant
    l = sk.line((0, 0), (3, 4)); sk.constrain("on_edge", l)
    c = sk.circle((1, 1), 2); sk.constrain("on_edge", c)
    r = sk.solve()
    assert r.state == "full" and not r.redundant


# ---------------------------------------------------------------- U2: スナップの拘束、中心矩形、ミラー
def test_snap_points_add_relations(api):
    did = new_sketch(api)
    base = api.call("sketch.add", {"doc": did, "kind": "line", "points": [[0, 0], [40, 0]]})["created"][0]
    c = api.call("sketch.add", {"doc": did, "kind": "circle", "points": [[60, 0], [65, 0]]})["created"][0]
    st = api.call("sketch.add", {"doc": did, "kind": "line", "points": [{"mid": base}, {"on": c, "x": 60, "y": 5}]})
    ln = ent(st, st["created"][0])
    types = {(k["type"], tuple(k["refs"])) for k in st["sketch"]["constraints"]}
    assert ("midpoint", (ln["p1"], base)) in types and ("point_on_circle", (ln["p2"], c)) in types
    assert (ent(st, ln["p1"])["x"], ent(st, ln["p1"])["y"]) == pytest.approx((20, 0))
    st = api.call("sketch.add", {"doc": did, "kind": "point", "points": [{"on": base, "x": 10, "y": 0}]})
    assert len(st["created"]) == 1 and st["undo"] == "点を追加"
    n = len(st["sketch"]["constraints"])
    st = api.call("sketch.add", {"doc": did, "kind": "circle", "points": [[0, 30], {"on": base, "x": 0, "y": 0}]})
    assert len(st["sketch"]["constraints"]) == n and ent(st, st["created"][0])["radius"] == pytest.approx(30)
    with pytest.raises(RpcError, match="がありません"):
        api.call("sketch.add", {"doc": did, "kind": "point", "points": [{"mid": c}]})


def test_center_rectangle(api):
    did = new_sketch(api)
    st = api.call("sketch.add", {"doc": did, "kind": "crect", "points": ["ORIGIN", [20, 10]]})
    sk = st["sketch"]
    assert len(st["created"]) == 6 and sk["profiles"] == 1 and sk["solve"]["dof"] == 2 and not sk["solve"]["redundant"]
    side = st["created"][0]
    st = api.call("sketch.dimension", {"doc": did, "refs": [side], "value": 60})
    xs = sorted(round(e["x"], 6) for e in st["sketch"]["entities"] if e["type"] == "point")
    assert xs[0] == -30 and xs[-1] == 30                                   # 中心（原点）を保って広がる


def test_mirror(api):
    did = new_sketch(api)
    ax = api.call("sketch.add", {"doc": did, "kind": "line", "points": ["ORIGIN", [0, 50]]})["created"][0]
    api.call("sketch.construction", {"doc": did, "ids": [ax]})
    st0 = api.call("doc.state", {"doc": did, "mesh": False})
    top = ent(st0, ax)["p2"]
    l1 = api.call("sketch.add", {"doc": did, "kind": "line", "points": ["ORIGIN", [30, 0]]})["created"][0]
    st = api.call("doc.state", {"doc": did, "mesh": False})
    l2 = api.call("sketch.add", {"doc": did, "kind": "line", "points": [ent(st, l1)["p2"], top]})["created"][0]
    c = api.call("sketch.add", {"doc": did, "kind": "circle", "points": [[10, 10], [13, 10]]})["created"][0]
    a = api.call("sketch.add", {"doc": did, "kind": "arc", "points": [[20, 30], [25, 30], [20, 35]]})["created"][0]
    before = api.call("doc.state", {"doc": did, "mesh": False})["sketch"]["solve"]["dof"]
    st = api.call("sketch.mirror", {"doc": did, "ids": [l1, l2, c, a, ax]})        # 軸は選択の中の作図線
    sk = st["sketch"]
    kinds = [ent(st, e)["type"] for e in st["created"]]
    assert kinds == ["line", "line", "circle", "arc"] and st["undo"] == "ミラー"
    assert sk["solve"]["dof"] == before and not sk["solve"]["redundant"] and sk["solve"]["status"] == "ok"
    m1 = ent(st, st["created"][0])
    assert m1["p1"] == ent(st, l1)["p1"]                                       # 軸上の点は共有
    assert (ent(st, m1["p2"])["x"], ent(st, m1["p2"])["y"]) == pytest.approx((-30, 0))
    assert sk["profiles"] == 3                                                  # 三角形（左右で 1 つ）+ 円 2 つ
    ma = ent(st, st["created"][3])
    assert (ent(st, ma["start"])["x"], ent(st, ma["start"])["y"]) == pytest.approx((-20, 35))
    # 元を動かすと写しが追従する
    st = api.call("sketch.dimension", {"doc": did, "refs": [c], "value": 10})
    assert [e["radius"] for e in st["sketch"]["entities"] if e["type"] == "circle"] == pytest.approx([5, 5])
    with pytest.raises(RpcError, match="作図線を 1 本"):
        api.call("sketch.mirror", {"doc": did, "ids": [l1]})


# ---------------------------------------------------------------- U3: 作図ツールの追加
def solve_of(st):
    return st["sketch"]["solve"]


def test_centerline_and_three_point_arc(api):
    did = new_sketch(api)
    st = api.call("sketch.add", {"doc": did, "kind": "line", "points": [[0, 0], [0, 30]], "construction": True})
    assert ent(st, st["created"][0])["construction"] is True and st["undo"] == "中心線を追加"
    st = api.call("sketch.add", {"doc": did, "kind": "arc3", "points": [[10, 0], [-10, 0], [0, 10]]})
    a = ent(st, st["created"][0])
    assert a["radius"] == pytest.approx(10) and (ent(st, a["center"])["x"], ent(st, a["center"])["y"]) == pytest.approx((0, 0))
    assert (ent(st, a["start"])["x"], ent(st, a["end"])["x"]) == pytest.approx((10, -10))      # 上を通る → 反時計回り
    st = api.call("sketch.add", {"doc": did, "kind": "arc3", "points": [[10, 0], [-10, 0], [0, -10]]})
    a = ent(st, st["created"][0])
    assert (ent(st, a["start"])["x"], ent(st, a["end"])["x"]) == pytest.approx((-10, 10))      # 下を通る
    with pytest.raises(RpcError, match="一直線"):
        api.call("sketch.add", {"doc": did, "kind": "arc3", "points": [[0, 0], [10, 0], [5, 0]]})


def test_tangent_arc(api):
    did = new_sketch(api)
    st = api.call("sketch.add", {"doc": did, "kind": "line", "points": [[0, 0], [20, 0]]})
    ln = ent(st, st["created"][0])
    st = api.call("sketch.add", {"doc": did, "kind": "tarc", "points": [ln["p2"], [30, 10]]})
    a = ent(st, st["created"][0])
    assert a["radius"] == pytest.approx(10) and a["start"] == ln["p2"]
    assert (ent(st, a["center"])["x"], ent(st, a["center"])["y"]) == pytest.approx((20, 10))
    assert any(k["type"] == "tangent" for k in st["sketch"]["constraints"])
    assert solve_of(st)["status"] == "ok" and not solve_of(st)["redundant"]
    # 円弧の終点から続けて正接円弧（S 字）。右へ曲がるので時計回り → 始点と終点が入れ替わる
    st = api.call("sketch.add", {"doc": did, "kind": "tarc", "points": [a["end"], [40, 20]]})
    b = ent(st, st["created"][0])
    assert b["end"] == a["end"] and b["radius"] == pytest.approx(10)
    # 線を動かしても正接が保たれる
    st = api.call("sketch.drag", {"doc": did, "point": ln["p1"], "x": 0, "y": -8, "final": True})
    P = lambda i: (ent(st, i)["x"], ent(st, i)["y"])
    c, s, o = P(ent(st, a["id"])["center"]), P(ln["p2"]), P(ln["p1"])
    assert abs((s[0] - o[0]) * (c[0] - s[0]) + (s[1] - o[1]) * (c[1] - s[1])) < 1e-6
    with pytest.raises(RpcError, match="端点から"):
        api.call("sketch.add", {"doc": did, "kind": "tarc", "points": [[5, 5], [9, 9]]})
    st = api.call("sketch.add", {"doc": did, "kind": "line", "points": [[0, 50], [20, 50]]})
    with pytest.raises(RpcError, match="接線の上"):
        api.call("sketch.add", {"doc": did, "kind": "tarc", "points": [ent(st, st["created"][0])["p2"], [35, 50]]})


def test_slot_and_polygon(api):
    did = new_sketch(api)
    st = api.call("sketch.add", {"doc": did, "kind": "slot", "points": [[0, 0], [30, 0], [10, 5]]})
    sk = st["sketch"]
    assert [ent(st, e)["type"] for e in st["created"]] == ["line", "arc", "line", "arc", "line"]
    assert sk["profiles"] == 1 and sk["solve"]["dof"] == 4 and not sk["solve"]["redundant"]   # 位置 2 + 長さ 1 + 幅 1（水平は自動）
    arc = st["created"][1]
    st = api.call("sketch.dimension", {"doc": did, "refs": [arc], "value": 8})                 # 半径を変えると両端が変わる
    assert [e["radius"] for e in st["sketch"]["entities"] if e["type"] == "arc"] == pytest.approx([8, 8])
    ys = sorted({round(e["y"], 6) for e in st["sketch"]["entities"] if e["type"] == "point"})
    assert ys == [-8, 0, 8] and st["sketch"]["profiles"] == 1
    did = new_sketch(api)
    st = api.call("sketch.add", {"doc": did, "kind": "polygon", "points": ["ORIGIN", [10, 0]], "sides": 6})
    sk = st["sketch"]
    assert len(st["created"]) == 7 and sk["profiles"] == 1 and not sk["solve"]["redundant"]
    assert sk["solve"]["dof"] == 2                                                             # 大きさと回転（中心は原点）
    circ = st["created"][-1]
    st = api.call("sketch.dimension", {"doc": did, "refs": [circ], "value": 40})               # 外接円の直径
    L = ent(st, st["created"][0] if False else sk["entities"][0]["id"])
    pts = [(e["x"], e["y"]) for e in st["sketch"]["entities"] if e["type"] == "point" and not e["fixed"]]
    assert all(math.hypot(*p) == pytest.approx(20, abs=1e-6) for p in pts) and len(pts) == 6
    with pytest.raises(RpcError, match="3〜40"):
        api.call("sketch.add", {"doc": did, "kind": "polygon", "points": [[0, 0], [5, 0]], "sides": 2})


def rect_ids(api, did, x0=0, y0=0, x1=40, y1=20):
    st = api.call("sketch.add", {"doc": did, "kind": "rect", "points": [[x0, y0], [x1, y1]]})
    return st, st["created"]


def test_sketch_chamfer_and_corner_fillet(api):
    did = new_sketch(api)
    st, ls = rect_ids(api, did)
    api.call("sketch.dimension", {"doc": did, "refs": [ls[0]]})                    # 幅 40 の寸法
    corner = ent(st, ls[0])["p2"]                                                   # (40, 0)
    st = api.call("sketch.chamfer", {"doc": did, "point": corner, "distance": 5})
    cut = ent(st, st["created"][0])
    ends = sorted([(ent(st, cut["p1"])["x"], ent(st, cut["p1"])["y"]), (ent(st, cut["p2"])["x"], ent(st, cut["p2"])["y"])])
    assert ends == [pytest.approx((35, 0)), pytest.approx((40, 5))]
    sk = st["sketch"]
    assert sk["profiles"] == 1 and sk["solve"]["status"] == "ok" and not sk["solve"]["redundant"]
    dims = [k for k in sk["constraints"] if k["type"] == "distance"]
    assert sorted(k["value"] for k in dims) == [5, 5, 40]                           # 幅の寸法は仮想交点からの距離に
    # 面取りの距離を変える
    d = next(k for k in dims if k["value"] == 5)
    st = api.call("feature.edit", {"doc": did, "id": "Sketch1", "key": d["id"], "value": 8})
    assert st["sketch"]["solve"]["status"] == "ok"
    # 別の角は点を指定してフィレット
    corner2 = ent(st, ls[2])["p2"]                                                  # (0, 20)
    st = api.call("sketch.fillet", {"doc": did, "point": corner2, "radius": 4})
    assert ent(st, st["created"][0])["type"] == "arc" and st["sketch"]["profiles"] == 1
    with pytest.raises(RpcError, match="角の点"):
        api.call("sketch.chamfer", {"doc": did, "point": ent(st, st["created"][0])["center"], "distance": 2})
    with pytest.raises(RpcError, match="大きすぎ"):
        api.call("sketch.chamfer", {"doc": did, "l1": ls[1], "l2": ls[2], "distance": 100})


def test_extend(api):
    did = new_sketch(api)
    wall = api.call("sketch.add", {"doc": did, "kind": "line", "points": [[30, -10], [30, 10]]})["created"][0]
    c = api.call("sketch.add", {"doc": did, "kind": "circle", "points": [[-20, 0], [-15, 0]]})["created"][0]
    ln = api.call("sketch.add", {"doc": did, "kind": "line", "points": [[0, 0], [10, 0]]})["created"][0]
    api.call("sketch.dimension", {"doc": did, "refs": [ln]})
    st = api.call("sketch.extend", {"doc": did, "id": ln, "x": 9, "y": 0})
    e = ent(st, ln)
    assert ent(st, e["p2"])["x"] == pytest.approx(30)
    types = [k["type"] for k in st["sketch"]["constraints"]]
    assert "point_on_line" in types and "length" not in types and st["undo"] == "延長"
    st = api.call("sketch.extend", {"doc": did, "id": ln, "x": 1, "y": 0})        # 反対側は円（近い方の交点）まで
    assert ent(st, e["p1"])["x"] == pytest.approx(-15)
    assert "point_on_circle" in [k["type"] for k in st["sketch"]["constraints"]]
    with pytest.raises(RpcError, match="当たる要素がありません"):
        api.call("sketch.extend", {"doc": did, "id": wall, "x": 30, "y": 9})
    with pytest.raises(RpcError, match="線です"):
        api.call("sketch.extend", {"doc": did, "id": c, "x": 0, "y": 0})


def test_offset_rectangle_inside_and_outside(api):
    did = new_sketch(api)
    st, ls = rect_ids(api, did)
    st = api.call("sketch.offset", {"doc": did, "ids": ls, "distance": 3, "x": 20, "y": 10})      # 内側
    sk = st["sketch"]
    new = [ent(st, i) for i in st["created"]]
    xs = sorted({round(ent(st, p)["x"], 6) for e in new for p in (e["p1"], e["p2"])})
    ys = sorted({round(ent(st, p)["y"], 6) for e in new for p in (e["p1"], e["p2"])})
    assert xs == [3, 37] and ys == [3, 17]
    assert len({p for e in new for p in (e["p1"], e["p2"])}) == 4                   # 角は共有（閉じた輪郭）
    assert sk["profiles"] == 2 and sk["solve"]["status"] == "ok" and not sk["solve"]["conflicting"]
    dims = [k for k in sk["constraints"] if k["type"] == "distance"]
    assert len(dims) == 4 and [k["expr"] for k in dims] == [None] + [f"Sketch1.{dims[0]['id']}"] * 3
    # 親の寸法 1 つを変えると全体が変わる
    st = api.call("feature.edit", {"doc": did, "id": "Sketch1", "key": dims[0]["id"], "value": 5})
    xs = sorted({round(ent(st, p)["x"], 6) for e in new for p in (e["p1"], e["p2"])})
    ox = sorted({round(ent(st, p)["x"], 6) for i in ls for p in (ent(st, i)["p1"], ent(st, i)["p2"])})
    assert [xs[0] - ox[0], ox[1] - xs[1]] == pytest.approx([5, 5])
    assert all(k["value"] == 5 for k in st["sketch"]["constraints"] if k["type"] == "distance")
    # 親を消すと、子は今の値のまま独立する
    st = api.call("sketch.delete", {"doc": did, "ids": [dims[0]["id"]]})
    rest = [k for k in st["sketch"]["constraints"] if k["type"] == "distance"]
    assert len(rest) == 3 and all(k["expr"] is None and k["value"] == 5 for k in rest)
    assert next(t for t in st["tree"] if t["id"] == "Sketch1")["state"] != "error"
    # 外側
    st = api.call("sketch.offset", {"doc": did, "ids": ls, "distance": 2, "x": -10, "y": 10})
    new = [ent(st, i) for i in st["created"]]
    xs = sorted({round(ent(st, p)["x"], 6) for e in new for p in (e["p1"], e["p2"])})
    ox = sorted({round(ent(st, p)["x"], 6) for i in ls for p in (ent(st, i)["p1"], ent(st, i)["p2"])})
    assert [ox[0] - xs[0], xs[1] - ox[1]] == pytest.approx([2, 2])


def test_offset_circle_arc_chain(api):
    did = new_sketch(api)
    c = api.call("sketch.add", {"doc": did, "kind": "circle", "points": [[0, 0], [10, 0]]})["created"][0]
    st = api.call("sketch.offset", {"doc": did, "ids": [c], "distance": 3, "x": 20, "y": 0})       # 外側
    n = ent(st, st["created"][0])
    assert n["radius"] == pytest.approx(13) and n["center"] == ent(st, c)["center"]
    k = next(k for k in st["sketch"]["constraints"] if k["type"] == "offset")
    assert k["value"] == 3 and k["refs"] == [n["id"], c]
    st = api.call("sketch.dimension", {"doc": did, "refs": [c], "value": 30})                      # 元を大きくすると追従
    assert ent(st, n["id"])["radius"] == pytest.approx(18)
    st = api.call("sketch.offset", {"doc": did, "ids": [c], "distance": 4, "x": 1, "y": 0})        # 内側
    assert ent(st, st["created"][0])["radius"] == pytest.approx(11)
    with pytest.raises(RpcError, match="大きすぎ"):
        api.call("sketch.offset", {"doc": did, "ids": [c], "distance": 20, "x": 1, "y": 0})
    # 線 + 正接円弧 の鎖: 外側へ 2
    did = new_sketch(api)
    st = api.call("sketch.add", {"doc": did, "kind": "line", "points": [[0, 0], [20, 0]]})
    ln = ent(st, st["created"][0])
    a = api.call("sketch.add", {"doc": did, "kind": "tarc", "points": [ln["p2"], [30, 10]]})["created"][0]
    st = api.call("sketch.offset", {"doc": did, "ids": [ln["id"], a], "distance": 2, "x": 10, "y": -5})
    nl, na = (ent(st, i) for i in st["created"])
    assert na["radius"] == pytest.approx(12) and ent(st, nl["p1"])["y"] == pytest.approx(-2)
    assert nl["p2"] == na["start"] and (ent(st, nl["p2"])["x"], ent(st, nl["p2"])["y"]) == pytest.approx((20, -2))
    assert st["sketch"]["solve"]["status"] == "ok" and not st["sketch"]["solve"]["redundant"]
    types = [k["type"] for k in st["sketch"]["constraints"]]
    assert types.count("collinear") == 1 and types.count("offset") == 1 and types.count("distance") == 0
    # 元の線を動かすと、オフセットした線・円弧が正接のまま追従する
    k = next(k for k in st["sketch"]["constraints"] if k["type"] == "offset")
    st = api.call("feature.edit", {"doc": did, "id": "Sketch1", "key": k["id"], "value": 4})
    nl, na = (ent(st, i["id"]) for i in (nl, na))
    P = lambda i: (ent(st, i)["x"], ent(st, i)["y"])
    c, s_, o = P(na["center"]), P(nl["p2"]), P(nl["p1"])
    assert abs((s_[0] - o[0]) * (c[0] - s_[0]) + (s_[1] - o[1]) * (c[1] - s_[1])) < 1e-6      # 半径 ⟂ 線
    assert na["radius"] - ent(st, a)["radius"] == pytest.approx(4)
    with pytest.raises(RpcError, match="選んでください"):
        api.call("sketch.offset", {"doc": did, "ids": [ln["p1"]], "distance": 2})


def test_offset_slot_is_well_defined(api):
    """正接だけでつながる閉じた輪郭（スロット）のオフセット: 冗長・矛盾なし、自由度は元と同じ。"""
    did = new_sketch(api)
    st = api.call("sketch.add", {"doc": did, "kind": "slot", "points": [[0, 0], [30, 0], [10, 5]]})
    dof = st["sketch"]["solve"]["dof"]
    st = api.call("sketch.offset", {"doc": did, "ids": st["created"][:4], "distance": 2, "x": 15, "y": 20})
    sv = st["sketch"]["solve"]
    assert sv["status"] == "ok" and not sv["redundant"] and not sv["conflicting"] and sv["dof"] == dof
    assert st["sketch"]["profiles"] == 2
    arcs = [e["radius"] for e in st["sketch"]["entities"] if e["type"] == "arc"]
    assert sorted(arcs) == pytest.approx([5, 5, 7, 7])
    offs = [k for k in st["sketch"]["constraints"] if k["type"] == "offset"]
    st = api.call("feature.edit", {"doc": did, "id": "Sketch1", "key": offs[0]["id"], "value": 3})
    assert sorted(e["radius"] - 0 for e in st["sketch"]["entities"] if e["type"] == "arc")[2:] == pytest.approx(
        [sorted(e["radius"] for e in st["sketch"]["entities"] if e["type"] == "arc")[0] + 3] * 2)
    assert st["sketch"]["solve"]["status"] == "ok" and st["sketch"]["profiles"] == 2


# ---------------------------------------------------------------- 原点を寸法・拘束の相手にする
def test_dimension_and_constrain_from_origin(api):
    """矩形を原点から寸法で位置決めする（3D CAD で一番よくやる操作）。原点の固定点が無くても "ORIGIN" で指定できる。"""
    did = new_sketch(api)
    st, ls = rect_ids(api, did, 10, 5, 50, 25)
    assert not any(e["type"] == "point" and e["fixed"] for e in st["sketch"]["entities"])
    left = ls[3]                                                            # x = 10 の縦の辺
    st = api.call("sketch.dimension", {"doc": did, "refs": ["ORIGIN", left], "value": 15})
    k = con(st, st["created"][0])
    o = next(e for e in st["sketch"]["entities"] if e["type"] == "point" and e["fixed"])
    assert k["type"] == "distance" and k["refs"] == [o["id"], left] and (o["x"], o["y"]) == (0, 0)
    xs = sorted({round(e["x"], 6) for e in st["sketch"]["entities"] if e["type"] == "point" and not e["fixed"]})
    assert xs == [15, 50]                                                   # 幅の寸法が無いので左の辺だけ動く
    corner = ent(st, ls[0])["p1"]                                          # (15, 5) の角
    st = api.call("sketch.dimension", {"doc": did, "refs": ["ORIGIN", corner], "type": "vdistance", "value": 8})
    assert con(st, st["created"][0])["refs"][0] == o["id"]                 # 2 回目は同じ原点の点を使う
    assert ent(st, corner)["y"] == pytest.approx(8)
    st = api.call("sketch.constrain", {"doc": did, "type": "horizontal", "refs": ["ORIGIN", ent(st, ls[0])["p2"]]})
    # 矛盾（y=8 と y=0）は診断に出る
    assert st["created"][0] in st["sketch"]["solve"]["conflicting"] + st["sketch"]["solve"]["redundant"]
