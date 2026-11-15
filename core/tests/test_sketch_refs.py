"""スケッチの外（モデルのエッジ・ほかのスケッチ）へのスナップと、スマート寸法の相手（D54）。"""
import pytest

from cad.api.rpc import Api, RpcError
from cad.doc.workspace import Workspace


@pytest.fixture
def part():
    api = Api(Workspace("prism"))
    d = api.call("doc.new")["doc"]
    api.call("sketch.new", {"doc": d, "plane": {"plane": "Front"}})
    api.call("sketch.add", {"doc": d, "kind": "rect", "points": [[0, 0], [40, 20]]})
    api.call("sketch.add", {"doc": d, "kind": "circle", "points": [[60, 10], [65, 10]]})
    api.call("sketch.end", {"doc": d})
    api.call("feature.add", {"doc": d, "type": "extrude", "sketch": "Sketch1", "distance": 10})
    names = api.call("doc.state", {"doc": d})["mesh"]["edge_names"]
    return api, d, names


def pts(st):
    return {e["id"]: (e["x"], e["y"]) for e in st["sketch"]["entities"] if e["type"] == "point"}


def edge(names, *parts):
    return next(n for n in names if all(p in n for p in parts))


def test_model_snap_points(part):
    api, d, names = part
    circ = edge(names, "C", "end#2")                    # 円柱の上（end 側）の円
    api.call("sketch.new", {"doc": d, "plane": {"plane": "Front"}})
    st = api.call("sketch.add", {"doc": d, "kind": "line", "points": [
        {"ext": f"edge:{circ}@center"}, {"ext": f"edge:{edge(names, 'end|', 'L5')}@mid"}]})
    P = pts(st)
    ln = next(e for e in st["sketch"]["entities"] if e["id"] == st["created"][0])
    assert P[ln["p1"]] == pytest.approx((60, 10))
    mid = P[ln["p2"]]
    assert mid in [pytest.approx(m) for m in [(20, 0), (40, 10), (20, 20), (0, 10)]]
    # 端点: クリック位置に近い方
    st = api.call("sketch.add", {"doc": d, "kind": "point", "points": [
        {"ext": f"edge:{edge(names, 'L5|', 'L6')}@vertex", "x": 0, "y": 0}]})
    p = pts(st)[st["created"][0]]
    assert p in [pytest.approx(m) for m in [(0, 0), (40, 0), (40, 20), (0, 20)]]
    # 円は中心だけ（端点・直線としては使えない）
    with pytest.raises(RpcError):
        api.call("sketch.add", {"doc": d, "kind": "point", "points": [{"ext": f"edge:{circ}@vertex"}]})
    with pytest.raises(RpcError):
        api.call("sketch.add", {"doc": d, "kind": "point", "points": [{"ext": f"edge:{circ}", "x": 1, "y": 1}]})


def test_dimension_to_model_vertex_follows_model(part):
    api, d, names = part
    # 右上の縦エッジ（Front 平面に垂直）の端点 = 角 (40, 20)
    corners = [n for n in names if n.count("side/L") == 2]
    api.call("sketch.new", {"doc": d, "plane": {"plane": "Front"}})
    st = api.call("sketch.add", {"doc": d, "kind": "point", "points": [[50, 30]]})
    p = st["created"][0]
    found = None
    for n in corners:
        st = api.call("sketch.reference", {"doc": d, "source": f"edge:{n}@vertex", "x": 40, "y": 20})
        r = st["created"][0]
        if pts(st)[r] == pytest.approx((40, 20)):
            found = r
            break
    assert found
    st = api.call("sketch.dimension", {"doc": d, "refs": [found, p], "type": "hdistance"})
    k = st["created"][0]
    st = api.call("feature.edit", {"doc": d, "id": "Sketch2", "key": k, "value": 15})
    assert pts(st)[p][0] == pytest.approx(55)
    api.call("sketch.end", {"doc": d})
    # モデル（Sketch1 の矩形の幅）を変えると、参照点が角に追従し、寸法 15 の点もついていく
    api.call("sketch.begin", {"doc": d, "id": "Sketch1"})
    k1 = api.call("sketch.dimension", {"doc": d, "refs": ["L5"]})["created"][0]
    api.call("feature.edit", {"doc": d, "id": "Sketch1", "key": k1, "value": 60})
    api.call("sketch.end", {"doc": d})
    st = api.call("sketch.begin", {"doc": d, "id": "Sketch2"})
    assert pts(st)[found][0] != pytest.approx(40)                 # 角が動いた
    assert pts(st)[p][0] - pts(st)[found][0] == pytest.approx(15)
    assert not [n for n in api.call("doc.state", {"doc": d})["tree"] if n.get("state") == "error"]


def test_reference_other_sketch_line_and_circle(part):
    api, d, names = part
    api.call("sketch.new", {"doc": d, "plane": {"plane": "Front"}})
    st = api.call("sketch.reference", {"doc": d, "source": "Sketch1:L5"})
    lid = st["created"][0]
    ln = next(e for e in st["sketch"]["entities"] if e["id"] == lid)
    assert ln["type"] == "line" and ln["construction"]
    assert api.call("sketch.reference", {"doc": d, "source": "Sketch1:L5"})["created"] == [lid]   # 使い回す
    cid = next(c["id"] for s in api.call("doc.state", {"doc": d})["sketches"] if s["id"] == "Sketch1"
               for c in s["curves"] if c["type"] == "circle")
    st = api.call("sketch.reference", {"doc": d, "source": f"Sketch1:{cid}"})
    assert pts(st)[st["created"][0]] == pytest.approx((60, 10))
    st = api.call("sketch.reference", {"doc": d, "source": "Sketch1:L5@mid"})
    assert pts(st)[st["created"][0]] in [pytest.approx(m) for m in [(20, 0), (40, 10), (20, 20), (0, 10)]]


def test_reference_model_edge_as_construction(part):
    api, d, names = part
    api.call("sketch.new", {"doc": d, "plane": {"plane": "Front"}})
    n = edge(names, "end|", "L5")
    st = api.call("sketch.reference", {"doc": d, "source": f"edge:{n}"})
    e = next(e for e in st["sketch"]["entities"] if e["id"] == st["created"][0])
    assert e["type"] == "line" and e["construction"]
    assert api.call("sketch.reference", {"doc": d, "source": f"edge:{n}"})["created"] == st["created"]
