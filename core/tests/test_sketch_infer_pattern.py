"""作図中の推測（直角・平行・正接の自動拘束）と、スケッチの直線・円形パターン（D61）。"""
import math

import pytest

from cad.api.rpc import Api, RpcError
from cad.doc.workspace import Workspace


@pytest.fixture
def api():
    a = Api(Workspace("prism"))
    d = a.call("doc.new")["doc"]
    a.call("sketch.new", {"doc": d, "plane": {"plane": "Front"}})
    return a, d


def cons(st, t):
    return [c for c in st["sketch"]["constraints"] if c["type"] == t]


def ent(st, i):
    return next(e for e in st["sketch"]["entities"] if e["id"] == i)


def xy(st, pid):
    e = ent(st, pid)
    return e["x"], e["y"]


def test_line_relations_perpendicular_parallel_tangent(api):
    a, d = api
    st = a.call("sketch.add", {"doc": d, "kind": "line", "points": [[0, 0], [30, 10]]})
    l1 = st["created"][0]; p2 = ent(st, l1)["p2"]
    # 終点から直角に（UI が向きを合わせた点）→ 直角の拘束
    st = a.call("sketch.add", {"doc": d, "kind": "line", "points": [p2, [30 - 5, 10 + 15]], "relations": [["perpendicular", l1]]})
    l2 = st["created"][0]
    assert [c["refs"] for c in cons(st, "perpendicular")] == [[l2, l1]]
    # 離れた所に平行な線
    st = a.call("sketch.add", {"doc": d, "kind": "line", "points": [[0, 20], [15, 25]], "relations": [["parallel", l1]]})
    assert [c["refs"] for c in cons(st, "parallel")] == [[st["created"][0], l1]]
    # 形が合っていなければ付けない（UI の吸い付きと食い違ったとき）
    st = a.call("sketch.add", {"doc": d, "kind": "line", "points": [[0, 40], [10, 47]], "relations": [["parallel", l1]]})
    assert len(cons(st, "parallel")) == 1
    # 円弧の端から接線の向きに → 正接
    r = 10 / math.sqrt(2)
    st = a.call("sketch.add", {"doc": d, "kind": "arc", "points": [[60, 0], [70, 0], [60 + r, r]]})      # 0° → 45°
    arc = st["created"][0]; end = ent(st, arc)["end"]
    st = a.call("sketch.add", {"doc": d, "kind": "line", "points": [end, [60 + r - 10, r + 10]], "relations": [["tangent", arc]]})
    assert [c["refs"] for c in cons(st, "tangent")] == [[st["created"][0], arc]]
    assert st["sketch"]["solve"]["status"] == "ok" and not st["sketch"]["solve"]["conflicting"]
    with pytest.raises(RpcError):
        a.call("sketch.add", {"doc": d, "kind": "line", "points": [[0, 0], [1, 2]], "relations": [["bogus", l1]]})


def test_linear_pattern(api):
    a, d = api
    st = a.call("sketch.add", {"doc": d, "kind": "circle", "points": [[10, 10], [13, 10]]})
    c = st["created"][0]
    st = a.call("sketch.add", {"doc": d, "kind": "line", "points": [[0, 0], [5, 2]]})
    ln = st["created"][0]
    st = a.call("sketch.pattern", {"doc": d, "ids": [c, ln], "kind": "linear", "count": 3, "spacing": 20, "angle": 90})
    new = st["created"]
    assert len(new) == 4                                       # (円 + 線) × 2
    circles = [e for e in st["sketch"]["entities"] if e["type"] == "circle"]
    assert sorted(round(xy(st, e["center"])[1], 6) for e in circles) == [10, 30, 50]
    assert all(e["radius"] == pytest.approx(3) for e in circles)
    assert len(cons(st, "equal")) == 4 and len(cons(st, "parallel")) == 2
    # 元の円の半径を変えると写しも変わる（等しい）
    st = a.call("sketch.dimension", {"doc": d, "refs": [c], "value": 8, "type": "diameter"})
    assert all(e["radius"] == pytest.approx(4) for e in st["sketch"]["entities"] if e["type"] == "circle")


def test_circular_pattern_shares_points_and_profiles(api):
    a, d = api
    a.call("sketch.add", {"doc": d, "kind": "circle", "points": [[0, 0], [30, 0]]})        # 外形
    st = a.call("sketch.add", {"doc": d, "kind": "rect", "points": [[20, -2], [26, 2]]})   # 矩形（4 線、点を共有）
    rect = st["created"]
    n_pts = len([e for e in st["sketch"]["entities"] if e["type"] == "point"])
    st = a.call("sketch.pattern", {"doc": d, "ids": rect, "kind": "circular", "count": 4})
    assert len(st["created"]) == 12
    assert len([e for e in st["sketch"]["entities"] if e["type"] == "point"]) == n_pts + 12     # 写し 1 つにつき 4 点
    assert st["sketch"]["profiles"] >= 4
    # 90° 回した写しの角 (20, -2) → (2, 20)
    pts = [xy(st, e["id"]) for e in st["sketch"]["entities"] if e["type"] == "point"]
    assert any(math.dist(p, (2, 20)) < 1e-6 for p in pts)
    # 中心を点で指定、角度 90° を 3 個（0, 45, 90）
    st = a.call("sketch.add", {"doc": d, "kind": "point", "points": [[100, 0]]})
    cp = st["created"][0]
    st = a.call("sketch.add", {"doc": d, "kind": "circle", "points": [[110, 0], [111, 0]]})
    st = a.call("sketch.pattern", {"doc": d, "ids": [st["created"][0], cp], "kind": "circular", "count": 3, "total": 90,
                                    "center": cp})
    cs = [xy(st, ent(st, i)["center"]) for i in st["created"]]
    assert cs[0] == pytest.approx((100 + 10 / math.sqrt(2), 10 / math.sqrt(2))) and cs[1] == pytest.approx((100, 10))
    # Undo 1 回でパターンごと消える
    n = len(st["sketch"]["entities"])
    st = a.call("edit.undo", {"doc": d})
    assert len(st["sketch"]["entities"]) < n


def test_pattern_errors(api):
    a, d = api
    st = a.call("sketch.add", {"doc": d, "kind": "circle", "points": [[0, 0], [1, 0]]})
    with pytest.raises(RpcError):
        a.call("sketch.pattern", {"doc": d, "ids": st["created"], "kind": "linear", "count": 1, "spacing": 5})
    with pytest.raises(RpcError):
        a.call("sketch.pattern", {"doc": d, "ids": [], "kind": "linear", "count": 3, "spacing": 5})
    with pytest.raises(RpcError):
        a.call("sketch.pattern", {"doc": d, "ids": st["created"], "kind": "spiral", "count": 3})


def test_polyline_closed_profile(api):
    """D67: 折れ線（点を共有する線の列）。閉じると輪郭になる。水平・垂直の辺には拘束が付く。"""
    a, d = api
    st = a.call("sketch.add", {"doc": d, "kind": "polyline", "points": [[0, 0], [40, 0], [40, 10], [20, 25], [0, 10]]})
    assert len(st["created"]) == 5 and st["sketch"]["profiles"] == 1
    assert len(cons(st, "horizontal")) == 1 and len(cons(st, "vertical")) == 2
    ends = [(ent(st, i)["p1"], ent(st, i)["p2"]) for i in st["created"]]
    assert all(ends[k][1] == ends[(k + 1) % 5][0] for k in range(5))          # 点を共有
    st = a.call("sketch.add", {"doc": d, "kind": "polyline", "points": [[50, 0], [60, 0], [60, 5]], "closed": False})
    assert len(st["created"]) == 2
