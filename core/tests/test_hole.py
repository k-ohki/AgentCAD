"""穴ウィザード（D57）。形（体積）は実カーネルのテスト（test_part_real_kernel.py）で見る。
ここでは記号カーネル（mock）で、位置・名前・規格の寸法・パターン・エラーを見る。"""
import pytest

from cad.api.rpc import Api, RpcError
from cad.doc.workspace import Workspace
from cad.part.features import HoleFeature
from cad.part.holes import hole_profile, standard_dims


@pytest.fixture
def plate():
    api = Api(Workspace("mock"))
    d = api.call("doc.new")["doc"]
    api.call("sketch.new", {"doc": d, "plane": {"plane": "Front"}})
    api.call("sketch.add", {"doc": d, "kind": "rect", "points": [[0, 0], [60, 40]]})
    api.call("feature.add", {"doc": d, "type": "extrude", "sketch": "Sketch1", "distance": 20})
    return api, d


def hole_sketch(api, d, pts):
    api.call("sketch.new", {"doc": d, "plane": {"face": "Extrude1/end"}})
    st = None
    for p in pts:
        st = api.call("sketch.add", {"doc": d, "kind": "point", "points": [p]})
    sid = (st or api.call("doc.state", {"doc": d}))["sketch"]["id"]
    api.call("sketch.end", {"doc": d})
    return sid


def node(st, fid):
    return next(t for t in st["tree"] if t["id"] == fid)


def test_standard_dims_and_profile():
    assert standard_dims("cbore", "M6") == {"diameter": 6.6, "cbore_diameter": 11.0, "cbore_depth": 6.5}
    assert standard_dims("tap", "M8") == {"diameter": 6.8}
    assert standard_dims("csink", "M4") == {"diameter": 4.5, "csink_diameter": 9.4}
    assert standard_dims("simple", "custom") == {}
    P = hole_profile("cbore", 3.3, 15, False, 118, 5.5, 6.5, 0, 90, 0.5)
    assert [t for t, _ in P] == ["top", "cbore_wall", "cbore_floor", "wall", "tip", "axis"]
    assert P[-1][1][0] == 0 and P[-1][1][1] > 15                   # 先端は奥へ尖る
    P = hole_profile("csink", 3.3, 15, True, 118, 0, 0, 6.3, 90, 0.5)
    assert [t for t, _ in P] == ["top", "csink", "wall", "floor", "axis"]
    assert P[2][1] == pytest.approx((3.3, 3.0))                      # 90° の皿: 深さ = 半径の差


def test_hole_positions_names_and_options(plate):
    api, d = plate
    sk = hole_sketch(api, d, [[15, 20], [45, 20]])
    st = api.call("feature.add", {"doc": d, "type": "hole", "sketch": sk, "kind": "cbore", "size": "M8", "depth": 12})
    h = st["created"][0]
    assert node(st, h)["state"] in ("ok", "warning"), node(st, h)
    doc = api.ws.get(d)
    assert len(doc.get(h).positions(_ctx(doc))[1]) == 2
    p = api.call("feature.params", {"doc": d, "id": h})
    vals = {x["key"]: x["value"] for x in p["params"]}
    assert (vals["diameter"], vals["cbore_diameter"], vals["cbore_depth"], vals["depth"]) == (9.0, 14.0, 8.6, 12.0)
    assert "csink_diameter" not in vals and {m["key"] for m in p["option_meta"]} == {"kind", "size", "end", "reverse"}
    api.call("feature.set", {"doc": d, "id": h, "key": "size", "value": "M5"})
    vals = {x["key"]: x["value"] for x in api.call("feature.params", {"doc": d, "id": h})["params"]}
    assert (vals["diameter"], vals["cbore_diameter"]) == (5.5, 9.5)
    api.call("feature.set", {"doc": d, "id": h, "key": "kind", "value": "csink"})
    vals = {x["key"]: x["value"] for x in api.call("feature.params", {"doc": d, "id": h})["params"]}
    assert vals["csink_diameter"] == 10.4 and "cbore_diameter" not in vals
    api.call("feature.edit", {"doc": d, "id": h, "key": "diameter", "value": 6})
    assert api.call("feature.params", {"doc": d, "id": h})["options"]["size"] == "custom"
    st = api.call("feature.set", {"doc": d, "id": h, "key": "end", "value": "through"})
    keys = {x["key"] for x in api.call("feature.params", {"doc": d, "id": h})["params"]}
    assert "depth" not in keys and "tip_angle" not in keys


def _ctx(doc):
    c = doc.context()
    return c


def test_hole_pattern_and_errors(plate):
    api, d = plate
    sk = hole_sketch(api, d, [[10, 10]])
    st = api.call("feature.add", {"doc": d, "type": "hole", "sketch": sk, "kind": "tap", "size": "M5", "depth": 10})
    h = st["created"][0]
    st = api.call("feature.add", {"doc": d, "type": "lpattern", "seeds": [h], "direction": {"axis": "X"}, "spacing": 20, "count": 3})
    assert node(st, st["created"][0])["state"] in ("ok", "warning"), node(st, st["created"][0])
    empty = hole_sketch(api, d, [])
    st = api.call("feature.add", {"doc": d, "type": "hole", "sketch": empty, "kind": "simple", "size": "M4"})
    assert node(st, st["created"][0])["state"] == "error"
    with pytest.raises(RpcError):
        api.call("feature.add", {"doc": d, "type": "hole", "sketch": sk, "kind": "nope"})
    with pytest.raises(RpcError):
        api.call("feature.add", {"doc": d, "type": "hole", "sketch": "Extrude1"})
    f = HoleFeature(sketch=sk, kind="cbore", cbore_diameter=3, diameter=5)
    assert f.type == "hole" and f.op == "cut"
