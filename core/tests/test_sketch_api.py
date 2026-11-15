import pytest

from cad.api.rpc import Api, RpcError
from cad.doc.workspace import Workspace


@pytest.fixture
def api():
    return Api(Workspace("prism"))


def ents(st, t=None):
    return [e for e in st["sketch"]["entities"] if t is None or e["type"] == t]


def test_draw_dimension_extrude_edit_drag_undo(api):
    did = api.call("doc.new")["doc"]
    st = api.call("sketch.new", {"doc": did, "plane": {"plane": "Front"}})
    assert st["sketch"]["plane"]["normal"] == [0, 0, 1] and st["sketch"]["solve"]["dof"] == 0
    st = api.call("sketch.add", {"doc": did, "kind": "rect", "points": [[0, 0], [40, 20]]})
    lines = st["created"]
    assert len(ents(st, "line")) == 4 and st["sketch"]["solve"]["state"] == "under"
    p0 = next(e for e in ents(st, "line") if e["id"] == lines[0])["p1"]
    api.call("sketch.constrain", {"doc": did, "type": "fix", "refs": [p0]})
    st = api.call("sketch.dimension", {"doc": did, "refs": [lines[0]]})                 # 長さ 40
    kw = st["created"][0]
    st = api.call("sketch.dimension", {"doc": did, "refs": [lines[1]], "value": 25})
    s = st["sketch"]["solve"]
    assert (s["status"], s["dof"], s["state"]) == ("ok", 0, "full")
    assert all(v == "full" for v in s["entity_state"].values())
    assert st["sketch"]["profiles"] == 1
    st = api.call("sketch.end", {"doc": did})
    assert st["sketch"] is None
    sid = st["tree"][0]["id"]
    st = api.call("feature.add", {"doc": did, "type": "extrude", "sketch": sid, "distance": 10})
    assert st["volume"] == pytest.approx(40 * 25 * 10)
    # 寸法を変える（スケッチ編集に入らなくてもプロパティから）
    st = api.call("feature.edit", {"doc": did, "id": sid, "key": kw, "value": 50})
    assert st["volume"] == pytest.approx(50 * 25 * 10)
    # 編集に入ると押し出しは一時的にロールバック表示。終了で戻る
    st = api.call("sketch.begin", {"doc": did, "id": sid})
    assert [t["state"] for t in st["tree"]] == ["ok", "rolled_back"] and st["rollback"] is None
    # 寸法の付いていない点は無いので、ドラッグしても形は変わらない（完全定義）
    p2 = next(e for e in ents(st, "line") if e["id"] == lines[1])["p2"]
    mid = api.call("sketch.drag", {"doc": did, "point": p2, "x": 80, "y": 80})
    assert next(e for e in mid["entities"] if e["id"] == p2)["x"] == pytest.approx(50)
    api.call("sketch.drag", {"doc": did, "point": p2, "x": 80, "y": 80, "final": True})
    st = api.call("sketch.end", {"doc": did})
    assert st["volume"] == pytest.approx(50 * 25 * 10)


def test_drag_is_one_undo_step(api):
    did = api.call("doc.new")["doc"]
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Top"}})
    st = api.call("sketch.add", {"doc": did, "kind": "line", "points": [[0, 0], [10, 0]]})
    line = ents(st, "line")[0]
    assert any(c["type"] == "horizontal" for c in st["sketch"]["constraints"])        # 水平を自動で付ける
    for x in (12, 14, 16):
        api.call("sketch.drag", {"doc": did, "point": line["p2"], "x": x, "y": 3})
    st = api.call("sketch.drag", {"doc": did, "point": line["p2"], "x": 18, "y": 3, "final": True})
    pts = {e["id"]: e for e in ents(st, "point")}
    assert pts[line["p2"]]["y"] == pytest.approx(pts[line["p1"]]["y"])               # 水平を保ったまま
    assert st["undo"] == "点をドラッグ"
    st = api.call("edit.undo", {"doc": did})
    pts = {e["id"]: e for e in ents(st, "point")}
    assert pts[line["p2"]]["x"] == pytest.approx(10) and st["sketch"] is not None


def test_sketch_on_face_and_cut(api):
    did = api.call("doc.new")["doc"]
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Front"}})
    api.call("sketch.add", {"doc": did, "kind": "rect", "points": [[0, 0], [40, 20]]})
    st = api.call("sketch.end", {"doc": did})
    st = api.call("feature.add", {"doc": did, "type": "extrude", "sketch": st["tree"][0]["id"], "distance": 10})
    st = api.call("sketch.new", {"doc": did, "plane": {"face": "Extrude1/end"}})
    assert st["sketch"]["plane"]["origin"] == pytest.approx([0, 0, 10])
    st = api.call("sketch.add", {"doc": did, "kind": "circle", "points": [[20, 10], [25, 10]]})
    st = api.call("sketch.end", {"doc": did})
    st = api.call("feature.add", {"doc": did, "type": "extrude", "sketch": "Sketch2", "distance": 5})
    assert st["mesh"]["bbox"][1][2] == pytest.approx(15)
    with pytest.raises(RpcError):
        api.call("sketch.new", {"doc": did, "plane": {"face": "Extrude2/side/C3"}})   # 円筒面は不可（名前が無い場合も）


def test_constraint_mapping_and_errors(api):
    did = api.call("doc.new")["doc"]
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Front"}})
    st = api.call("sketch.add", {"doc": did, "kind": "line", "points": [[0, 0], [10, 3]]})
    line = st["created"][0]
    st = api.call("sketch.add", {"doc": did, "kind": "point", "points": [[5, 7]]})
    pt = st["created"][0]
    st = api.call("sketch.constrain", {"doc": did, "type": "coincident", "refs": [line, pt]})   # 線と点 → 点-線上
    assert st["sketch"]["constraints"][-1]["type"] == "point_on_line"
    st = api.call("sketch.add", {"doc": did, "kind": "circle", "points": [[30, 0], [33, 0]]})
    c = st["created"][0]
    st = api.call("sketch.constrain", {"doc": did, "type": "tangent", "refs": [c, line]})      # 順は自由
    assert st["sketch"]["constraints"][-1]["type"] == "tangent"
    with pytest.raises(RpcError):
        api.call("sketch.constrain", {"doc": did, "type": "parallel", "refs": [line, c]})
    st = api.call("sketch.construction", {"doc": did, "ids": [line]})
    assert next(e for e in ents(st) if e["id"] == line)["construction"] is True
    n = len(st["sketch"]["constraints"])
    st = api.call("sketch.delete", {"doc": did, "ids": [line]})
    assert all(e["id"] != line for e in ents(st)) and len(st["sketch"]["constraints"]) < n
    with pytest.raises(RpcError):
        api.call("sketch.add", {"doc": did, "kind": "line", "points": [[0, 0], [0, 0]]})
    api.call("sketch.end", {"doc": did})
    with pytest.raises(RpcError):
        api.call("sketch.add", {"doc": did, "kind": "point", "points": [[0, 0]]})       # 編集中でない


def test_over_defined_is_reported_not_rejected(api):
    did = api.call("doc.new")["doc"]
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Front"}})
    st = api.call("sketch.add", {"doc": did, "kind": "rect", "points": [[0, 0], [10, 5]]})
    l = st["created"]
    st = api.call("sketch.constrain", {"doc": did, "type": "parallel", "refs": [l[0], l[2]]})
    s = st["sketch"]["solve"]
    assert s["state"] == "over" and s["redundant"] == [st["created"][0]]
    assert st["undo"].startswith("拘束")
    st = api.call("edit.undo", {"doc": did})
    assert st["sketch"]["solve"]["state"] == "under"


def test_origin_snap_creates_one_fixed_point(api):
    did = api.call("doc.new")["doc"]
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Front"}})
    st = api.call("sketch.add", {"doc": did, "kind": "line", "points": ["ORIGIN", [10, 0]]})
    st = api.call("sketch.add", {"doc": did, "kind": "line", "points": ["ORIGIN", [0, 10]]})
    fixed = [e for e in ents(st, "point") if e["fixed"]]
    assert len(fixed) == 1 and (fixed[0]["x"], fixed[0]["y"]) == (0, 0)
    st = api.call("sketch.dimension", {"doc": did, "refs": [st["created"][0]]})
    lines = ents(st, "line")
    st = api.call("sketch.dimension", {"doc": did, "refs": [lines[0]["id"]]})
    assert st["sketch"]["solve"]["dof"] == 0


def test_closing_line_onto_existing_point_gets_vertical(api):
    """既存の点で閉じた線は、画面のスナップ誤差（1e-6 程度）があっても垂直・水平を自動で付ける。"""
    did = api.call("doc.new")["doc"]
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Front"}})
    st = api.call("sketch.add", {"doc": did, "kind": "line", "points": ["ORIGIN", [10, 0]]})
    st = api.call("sketch.add", {"doc": did, "kind": "line", "points": [[1e-6, 8], "ORIGIN"]})
    assert st["sketch"]["constraints"][-1]["type"] == "vertical"
    st = api.call("sketch.add", {"doc": did, "kind": "line", "points": [[0, 0.5], [10, 3]]})
    assert st["sketch"]["constraints"][-1]["type"] == "vertical"        # 斜めの線には付けない（直前のまま）
