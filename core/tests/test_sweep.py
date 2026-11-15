"""スイープ（D58）。形（体積）は実カーネルのテストで見る。ここでは経路のつなぎ方・名前・選び方・エラー。"""
import math

import numpy as np
import pytest

from cad.api.rpc import Api, RpcError
from cad.doc.workspace import Workspace
from cad.kernel.base import Plane, Profile, Segment
from cad.kernel.meshtopo import path_polyline


@pytest.fixture
def doc():
    api = Api(Workspace("mock"))
    d = api.call("doc.new")["doc"]
    return api, d


def path_sketch(api, d, adds, plane="Top"):
    api.call("sketch.new", {"doc": d, "plane": {"plane": plane}})
    st = None
    for kind, pts in adds:
        st = api.call("sketch.add", {"doc": d, "kind": kind, "points": pts})
    api.call("sketch.end", {"doc": d})
    return st["sketch"]["id"]


def node(st, fid):
    return next(t for t in st["tree"] if t["id"] == fid)


def test_path_polyline_arc_directions():
    pl = Plane((0, 0, 0), (1, 0, 0), (0, 0, 1))
    ccw = Profile(pl, [[Segment("arc", "A", [(0, 0), (10, 0), (0, 10)])]])
    cw = Profile(pl, [[Segment("arc_cw", "A", [(0, 0), (0, 10), (10, 0)])]])
    P, T = path_polyline(ccw)
    assert P[0] == pytest.approx([10, 0, 0]) and P[-1] == pytest.approx([0, 10, 0], abs=1e-9) and T[0] == pytest.approx([0, 1, 0])
    P, T = path_polyline(cw)
    assert P[0] == pytest.approx([0, 10, 0], abs=1e-9) and P[-1] == pytest.approx([10, 0, 0]) and T[0] == pytest.approx([1, 0, 0], abs=1e-9)
    L = np.linalg.norm(np.diff(P, axis=0), axis=1).sum()
    assert L == pytest.approx(10 * math.pi / 2, rel=5e-3)


def test_sweep_feature_chain_and_names(doc):
    api, d = doc
    # 経路（Top = XZ 平面）: 原点から +X へ 20、円弧で曲がって、さらに直線。断面（Right = YZ 平面）: 原点の円
    pth = path_sketch(api, d, [("line", [[0, 0], [20, 0]]), ("arc", [[20, 10], [20, 0], [30, 10]]), ("line", [[30, 10], [30, 25]])])
    prf = path_sketch(api, d, [("circle", [[0, 0], [2, 0]])], plane="Right")
    st = api.call("feature.add", {"doc": d, "type": "sweep", "sketch": prf})     # 経路は自動で選ぶ
    sw = st["created"][0]
    assert node(st, sw)["state"] in ("ok", "warning"), node(st, sw)
    f = api.ws.get(d).get(sw)
    assert (f.profile, f.path) == (prf, pth)
    p = api.call("feature.params", {"doc": d, "id": sw})
    meta = {m["key"]: m for m in p["option_meta"]}
    assert meta["path"]["kind"] == "choice" and [pth, api.ws.get(d).get(pth).name] in meta["path"]["choices"]
    faces = set(api.ws.get(d).results[sw].names.faces)
    assert {f"{sw}/start", f"{sw}/end"} <= faces and any(n.startswith(f"{sw}/side/") for n in faces)
    # 断面と経路を入れ替えようとするとエラー
    with pytest.raises(RpcError):
        api.call("feature.set", {"doc": d, "id": sw, "key": "path", "value": sw})


def test_sweep_path_errors(doc):
    api, d = doc
    br = path_sketch(api, d, [("line", [[0, 0], [10, 0]]), ("line", [[10, 0], [20, 0]]), ("line", [[10, 0], [10, 10]])])
    prf = path_sketch(api, d, [("circle", [[0, 0], [1, 0]])], plane="Right")
    st = api.call("feature.add", {"doc": d, "type": "sweep", "sketch": prf, "path": br})
    assert node(st, st["created"][0])["state"] == "error" and "枝分かれ" in node(st, st["created"][0])["message"]
    gap = path_sketch(api, d, [("line", [[0, 0], [10, 0]]), ("line", [[12, 0], [20, 0]])])
    st = api.call("feature.add", {"doc": d, "type": "sweep", "sketch": prf, "path": gap})
    assert node(st, st["created"][0])["state"] == "error"
    with pytest.raises(RpcError):
        api.call("feature.add", {"doc": d, "type": "sweep", "sketch": prf, "path": prf})


def test_reverse_traversal_and_start_near_profile(doc):
    """経路を逆向きに描いても（終点側に断面がある）、断面に近い端から順に並べ、円弧は時計回りになる。"""
    from cad.part.features import sweep_path
    api, d = doc
    pth = path_sketch(api, d, [("line", [[0, 0], [20, 0]]), ("arc", [[20, 10], [20, 0], [30, 10]])])
    dd = api.ws.get(d)
    ctx = dd.context()
    plane = dd.results[pth].data["profile"].plane
    end = np.asarray(plane.to_world(30, 10))
    p = sweep_path(ctx, pth, None, end)
    kinds = [s.kind for s in p.loops[0]]
    assert kinds[0] in ("arc", "arc_cw") and kinds[1] == "line"
    P, _ = path_polyline(p)
    assert P[0] == pytest.approx(end, abs=1e-6) and P[-1] == pytest.approx(plane.to_world(0, 0), abs=1e-6)


def test_clockwise_arc_by_half_turn():
    """実カーネル用: 時計回りの円弧は、反時計回りの円弧を二等分線のまわりに 180° 回して作る（始点と終点が入れ替わる）。"""
    import math as m

    from cad.kernel.base import Segment
    from cad.kernel.base import arc_angles
    # S 字の 2 つ目: スケッチでは (30,20) → (20,10) の反時計回り。経路は (20,10) から (30,20) へ逆に（時計回りに）たどる
    c, b, a = np.array([30.0, 10.0]), np.array([20.0, 10.0]), np.array([30.0, 20.0])
    a0, sweep, flip = arc_angles(Segment("arc_cw", "A", [c, b, a]))
    r = 10.0

    def point(t):                                          # 作った円弧（回す前）: 中心から a0 + t
        th = m.radians(a0 + t)
        return c + r * np.array([m.cos(th), m.sin(th)])

    def turn(p):                                           # 中心を通る、向き flip の軸のまわりに 180°（平面の中では鏡映）
        u = np.array([m.cos(flip), m.sin(flip)])
        d = p - c
        return c + 2 * (d @ u) * u - d
    assert turn(point(0)) == pytest.approx(b) and turn(point(sweep)) == pytest.approx(a)      # 始点 b → 終点 a
    mid = turn(point(sweep / 2))
    assert np.linalg.norm(mid - c) == pytest.approx(r) and mid == pytest.approx(point(sweep / 2))
    # 時計回り（外積の向きが負）で 90°
    p1 = turn(point(sweep * 0.01))
    u, v = b - c, p1 - c
    assert u[0] * v[1] - u[1] * v[0] < 0 and sweep == pytest.approx(90)
    assert arc_angles(Segment("arc", "A", [c, a, b]))[2] is None
