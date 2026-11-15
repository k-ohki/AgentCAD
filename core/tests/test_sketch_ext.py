"""ほかのスケッチの表示と、その点・線へのスナップ（外部参照）。"""
import pytest

from cad.api.rpc import Api, RpcError
from cad.doc.workspace import Workspace


@pytest.fixture
def api():
    return Api(Workspace("prism"))


def sk_of(st, fid):
    return next(s for s in st["sketches"] if s["id"] == fid)


def make_rect_sketch(api, did, w=40, h=20):
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Front"}})
    st = api.call("sketch.add", {"doc": did, "kind": "rect", "points": [[0, 0], [w, h]]})
    sid = st["sketch"]["id"]
    api.call("sketch.end", {"doc": did})
    return sid


def test_sketches_in_state_and_visibility(api):
    did = api.call("doc.new")["doc"]
    s1 = make_rect_sketch(api, did)
    st = api.call("doc.state", {"doc": did})
    s = sk_of(st, s1)
    assert s["shown"] and not s["used"] and len(s["curves"]) == 4 and len(s["points"]) >= 4
    assert all(len(p["p"]) == 3 for p in s["points"])
    st = api.call("feature.add", {"doc": did, "type": "extrude", "sketch": s1, "distance": 10})
    s = sk_of(api.call("doc.state", {"doc": did}), s1)
    assert s["used"] and not s["shown"]                 # 使われたスケッチは自動で隠れる（一般的な 3D CAD と同じ）
    st = api.call("sketch.visible", {"doc": did, "feature": s1, "visible": True})
    assert sk_of(st, s1)["shown"]
    api.call("edit.undo", {"doc": did})
    assert not sk_of(api.call("doc.state", {"doc": did}), s1)["shown"]


def test_snap_to_other_sketch_point_and_line_follows(api):
    did = api.call("doc.new")["doc"]
    s1 = make_rect_sketch(api, did)
    src = next(s for s in api.call("doc.state", {"doc": did})["sketches"] if s["id"] == s1)
    corner = next(p for p in src["points"] if abs(p["p"][0] - 40) < 1e-6 and abs(p["p"][1] - 20) < 1e-6)
    edge = next(c for c in src["curves"] if abs(c["pts"][0][1]) < 1e-6 and abs(c["pts"][1][1]) < 1e-6)  # 下の辺 y=0
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Front"}})
    st = api.call("sketch.add", {"doc": did, "kind": "line",
                                 "points": [{"ext": f"{s1}:{corner['id']}"}, {"ext": f"{s1}:{edge['id']}", "x": 10, "y": 3}]})
    s2 = st["sketch"]["id"]
    types = sorted(c["type"] for c in st["sketch"]["constraints"])
    assert "ext_point" in types and "ext_line" in types
    ln = next(e for e in st["sketch"]["entities"] if e["id"] == st["created"][0])
    P = {e["id"]: e for e in st["sketch"]["entities"] if e["type"] == "point"}
    assert (P[ln["p1"]]["x"], P[ln["p1"]]["y"]) == pytest.approx((40, 20))
    assert P[ln["p2"]]["y"] == pytest.approx(0, abs=1e-6) and P[ln["p2"]]["x"] == pytest.approx(10)
    api.call("sketch.end", {"doc": did})
    # 元のスケッチを変える → 参照している点がついてくる
    api.call("sketch.begin", {"doc": did, "id": s1})
    st = api.call("sketch.dimension", {"doc": did, "refs": [edge["id"]]})
    k = st["created"][0]
    api.call("feature.edit", {"doc": did, "id": s1, "key": k, "value": 60})
    api.call("sketch.end", {"doc": did})
    src = next(s for s in api.call("doc.state", {"doc": did})["sketches"] if s["id"] == s1)
    cx, cy = next(p["p"] for p in src["points"] if p["id"] == corner["id"])[:2]
    assert cx != pytest.approx(40)
    st = api.call("sketch.begin", {"doc": did, "id": s2})
    P = {e["id"]: e for e in st["sketch"]["entities"] if e["type"] == "point"}
    assert (P[ln["p1"]]["x"], P[ln["p1"]]["y"]) == pytest.approx((cx, cy))   # 角についてくる
    assert P[ln["p2"]]["y"] == pytest.approx(0, abs=1e-6)
    assert st["sketch"]["solve"]["status"] != "conflicting"


def test_cannot_reference_later_sketch(api):
    did = api.call("doc.new")["doc"]
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Front"}})
    api.call("sketch.add", {"doc": did, "kind": "line", "points": [[0, 0], [10, 0]]})
    api.call("sketch.end", {"doc": did})
    s2 = make_rect_sketch(api, did)
    api.call("sketch.begin", {"doc": did, "id": "Sketch1"})
    with pytest.raises(RpcError):
        api.call("sketch.add", {"doc": did, "kind": "point", "points": [{"ext": f"{s2}:P1"}]})


def test_sketch_referenced_by_sketch_stays_shown(api):
    did = api.call("doc.new")["doc"]
    s1 = make_rect_sketch(api, did)
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Front"}})
    st = api.call("sketch.add", {"doc": did, "kind": "point", "points": [{"ext": f"{s1}:P1"}]})
    assert sk_of(st, s1)["shown"]                          # スケッチから参照されても隠れない


def test_ext_reference_survives_save_open(api, tmp_path):
    did = api.call("doc.new")["doc"]
    s1 = make_rect_sketch(api, did)
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Front"}})
    api.call("sketch.add", {"doc": did, "kind": "point", "points": [{"ext": f"{s1}:P3"}]})
    api.call("sketch.end", {"doc": did})
    api.call("sketch.visible", {"doc": did, "feature": s1, "visible": False})
    path = str(tmp_path / "p.usda")
    api.call("doc.save", {"doc": did, "path": path})
    api.call("doc.close", {"doc": did})
    st = api.call("doc.open", {"path": path})
    assert not sk_of(st, s1)["shown"] and sk_of(st, s1)["visible"] is False
    st = api.call("sketch.begin", {"doc": st["doc"], "id": "Sketch2"})
    c = next(c for c in st["sketch"]["constraints"] if c["type"] == "ext_point")
    assert c["source"] == f"{s1}:P3"
