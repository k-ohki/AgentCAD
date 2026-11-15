import pytest

from cad.api.rpc import Api, RpcError
from cad.doc.workspace import Workspace


def make_part(api, kind, pts, dist, name):
    did = api.call("doc.new", {"name": name})["doc"]
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Front"}})
    api.call("sketch.add", {"doc": did, "kind": kind, "points": pts})
    api.call("feature.add", {"doc": did, "type": "extrude", "sketch": "Sketch1", "distance": dist})
    return did


@pytest.fixture
def setup():
    api = Api(Workspace("prism"))
    blk = make_part(api, "rect", [[0, 0], [40, 20]], 10, "Block")
    pin = make_part(api, "circle", [[0, 0], [5, 0]], 15, "Pin")
    a = api.call("asm.new")["doc"]
    api.call("asm.add", {"doc": a, "part": blk})
    st = api.call("asm.add", {"doc": a, "part": pin})
    return api, a, blk, pin, st


def comp(st, name):
    return next(c for c in st["components"] if c["id"] == name)


def test_add_mate_solve_and_follow_part_edit(setup):
    api, a, blk, pin, st = setup
    assert [c["id"] for c in st["components"]] == ["Block<1>", "Pin<1>"]
    assert comp(st, "Block<1>")["fixed"] and comp(st, "Pin<1>")["dof"] == 6 and comp(st, "Pin<1>")["position"][0] > 0
    assert set(st["parts"]) == {blk, pin} and st["parts"][pin]["face_names"]
    st = api.call("asm.mate", {"doc": a, "type": "coincident", "a": ["Block<1>", "Extrude1/end"], "b": ["Pin<1>", "Extrude1/start"]})
    assert st["solve"]["status"] == "ok" and comp(st, "Pin<1>")["dof"] == 3
    assert comp(st, "Pin<1>")["position"][2] == pytest.approx(10)
    st = api.call("asm.mate", {"doc": a, "type": "distance", "a": ["Block<1>", "Extrude1/side/L8"], "b": ["Pin<1>", "Extrude1/side/C2"], "value": 12})
    assert comp(st, "Pin<1>")["position"][0] == pytest.approx(12) and st["mates"][1]["state"] == "ok"
    st = api.call("asm.edit", {"doc": a, "id": st["mates"][1]["id"], "value": 20})
    assert comp(st, "Pin<1>")["position"][0] == pytest.approx(20)
    api.call("feature.edit", {"doc": blk, "id": "Extrude1", "key": "distance", "value": 25})     # 板を厚くする
    st = api.call("doc.state", {"doc": a})
    assert comp(st, "Pin<1>")["position"][2] == pytest.approx(25)
    with pytest.raises(RpcError):
        api.call("asm.mate", {"doc": a, "type": "concentric", "a": ["Block<1>", "Extrude1/end"], "b": ["Pin<1>", "Extrude1/side/C2"]})
    with pytest.raises(RpcError):
        api.call("asm.add", {"doc": a, "part": a})


def test_drag_undo_delete_save_open(setup, tmp_path):
    api, a, blk, pin, st = setup
    api.call("asm.mate", {"doc": a, "type": "coincident", "a": ["Block<1>", "Extrude1/end"], "b": ["Pin<1>", "Extrude1/start"]})
    r = api.call("asm.drag", {"doc": a, "comp": "Pin<1>", "position": [30, 5, 99]})
    assert set(r) == {"components"}
    st = api.call("asm.drag", {"doc": a, "comp": "Pin<1>", "position": [33, 6, 99], "final": True})
    assert comp(st, "Pin<1>")["position"] == pytest.approx([33, 6, 10], abs=1e-6) and st["undo"] == "部品をドラッグ"
    st = api.call("edit.undo", {"doc": a})
    assert comp(st, "Pin<1>")["position"][0] != pytest.approx(33)
    path = api.call("doc.save", {"doc": a, "path": str(tmp_path / "asm")})["path"]
    assert path.endswith(".usda")
    st2 = api.call("doc.open", {"path": path})
    assert st2["kind"] == "assembly" and len(st2["components"]) == 2 and st2["solve"]["status"] == "ok"
    assert comp(st2, "Pin<1>")["position"][2] == pytest.approx(10)
    st = api.call("asm.fix", {"doc": a, "comp": "Pin<1>", "value": True})
    assert comp(st, "Pin<1>")["dof"] == 0
    st = api.call("asm.delete", {"doc": a, "ids": ["Pin<1>"]})
    assert len(st["components"]) == 1 and st["mates"] == []
    assert {d["kind"] for d in api.call("doc.list")} == {"part", "assembly"}


# ---------------------------------------------------------------- 一般的な 3D CAD と同じ合致の手順
def test_mate_options_defaults(setup):
    api, a, *_ = setup
    o = api.call("asm.mate_options", {"doc": a, "a": ["Block<1>", "Extrude1/end"], "b": ["Pin<1>", "Extrude1/start"]})
    types = [t["type"] for t in o["types"]]
    assert o["default"] == "coincident" and {"coincident", "parallel", "distance", "angle", "lock"} <= set(types)
    assert "concentric" not in types and o["distance"] >= 0 and o["angle"] is not None
    o = api.call("asm.mate_options", {"doc": a, "a": ["Block<1>", "Extrude1/end"], "b": ["Pin<1>", "Extrude1/side/C2"]})
    assert o["default"] == "tangent"
    with pytest.raises(RpcError):
        api.call("asm.mate_options", {"doc": a, "a": ["Pin<1>", "Extrude1/end"], "b": ["Pin<1>", "Extrude1/start"]})


def test_pending_mate_change_type_and_cancel(setup):
    api, a, *_ = setup
    A, B = ["Block<1>", "Extrude1/end"], ["Pin<1>", "Extrude1/start"]
    pos0 = comp(api.call("doc.state", {"doc": a}), "Pin<1>")["position"]
    n0 = len(api.ws.get(a).undo_stack)
    st = api.call("asm.mate", {"doc": a, "type": "coincident", "a": A, "b": B, "pending": True})
    mid = st["created"][0]
    st = api.call("asm.mate_set", {"doc": a, "id": mid, "type": "distance", "a": A, "b": B, "value": 7, "pending": True})
    m = next(x for x in st["mates"] if x["id"] == mid)
    assert m["type"] == "distance" and m["value"] == pytest.approx(7) and len(st["mates"]) == 1
    st = api.call("asm.mate_set", {"doc": a, "id": mid, "type": "distance", "a": A, "b": B, "value": 7,
                                   "aligned": not m["aligned"], "pending": True})
    assert next(x for x in st["mates"] if x["id"] == mid)["aligned"] != m["aligned"]
    assert len(api.ws.get(a).undo_stack) == n0 + 1            # 作成と変更で Undo は 1 つ
    st = api.call("asm.mate_cancel", {"doc": a, "id": mid})
    assert not st["mates"] and comp(st, "Pin<1>")["position"] == pytest.approx(pos0)
    assert len(api.ws.get(a).undo_stack) == n0 and not st["redo"]


def test_pending_mate_ok_keeps_one_undo(setup):
    api, a, *_ = setup
    A, B = ["Block<1>", "Extrude1/end"], ["Pin<1>", "Extrude1/start"]
    st = api.call("asm.mate", {"doc": a, "type": "coincident", "a": A, "b": B, "pending": True})
    mid = st["created"][0]
    st = api.call("asm.mate_set", {"doc": a, "id": mid, "type": "parallel", "a": A, "b": B, "pending": True})
    assert st["undo"] == "合致（平行）"
    st = api.call("edit.undo", {"doc": a})
    assert not st["mates"]



def test_joints_for_motion_mode(setup):
    """D70: 「動かしてみる」の回転軸。同心円の合致の相手（固定のブロック）の穴の軸を世界座標で返す。文書は変えない。"""
    api, a, blk, pin, st = setup
    assert api.call("asm.joints", {"doc": a}) == []                              # 合致が無ければ回せる部品は無い
    api.call("sketch.new", {"doc": blk, "plane": {"face": "Extrude1/end"}})
    api.call("sketch.add", {"doc": blk, "kind": "circle", "points": [[10, 10], [13, 10]]})
    api.call("feature.add", {"doc": blk, "type": "extrude", "sketch": "Sketch2", "distance": 4})   # 上に円柱（軸 = 穴の代わり）
    api.call("asm.mate", {"doc": a, "type": "concentric", "a": ["Block<1>", "Extrude2/side/C2"], "b": ["Pin<1>", "Extrude1/side/C2"]})
    undo = api.call("doc.state", {"doc": a})["undo"]
    j = api.call("asm.joints", {"doc": a})
    assert [x["comp"] for x in j] == ["Pin<1>"]
    assert j[0]["origin"][:2] == pytest.approx([10, 10]) and [abs(v) for v in j[0]["dir"]] == pytest.approx([0, 0, 1])
    assert api.call("doc.state", {"doc": a})["undo"] == undo
