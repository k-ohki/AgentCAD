"""実カーネルでのフィーチャーツリー・命名・合致の通しテスト（実カーネルが無ければスキップ）。

    python -m pytest core/tests/test_part_real_kernel.py -v
"""
import math
import time

import pytest

from cad.assembly import Assembly, PartSource
from cad.kernel import available_kernels
from cad.part import ExtrudeFeature, FilletFeature, PartDocument, SketchFeature
from cad.sketch import Sketch

REAL = sorted(n for n, k in available_kernels().items() if k.geometric)
pytestmark = pytest.mark.skipif(not REAL, reason="実カーネルが無い")


def block_with_hole_and_fillet(kernel):
    doc = PartDocument(kernel)
    sk = Sketch(); ls = sk.rect(0, 0, 10, 5)
    sk.constrain("fix", sk.entities[ls[0]].p1)
    kw = sk.constrain("length", ls[0], value=10); sk.constrain("length", ls[1], value=5)
    s1 = doc.add(SketchFeature(sketch=sk)); e1 = doc.add(ExtrudeFeature(sketch=s1, distance=4))
    hs = Sketch(); c = hs.circle((5, 2.5), 1)
    hs.constrain("fix", hs.entities[c].center); hs.constrain("radius", c, value=1)
    s2 = doc.add(SketchFeature(plane={"face": f"{e1}/end"}, sketch=hs))       # 上面にスケッチ
    e2 = doc.add(ExtrudeFeature(sketch=s2, distance=4, op="cut"))             # 貫通穴
    f1 = doc.add(FilletFeature(edges=[f"E[{e1}/end|{e1}/side/{ls[0]}]"], radius=0.5))   # 上面と前面の間
    return doc, sk, kw, (s1, e1, s2, e2, f1), c


def fillet_loss(r, length):
    return (1 - math.pi / 4) * r * r * length


@pytest.fixture(params=REAL)
def kernel(request):
    return available_kernels()[request.param]()


def test_feature_tree_on_real_kernel(kernel):
    doc, sk, kw, fids, c = block_with_hole_and_fillet(kernel)
    t = time.perf_counter()
    st = doc.regenerate()
    print(f"\n再生成 {1000 * (time.perf_counter() - t):.0f} ms")
    assert {f: s.state for f, s in st.items()} == {f: "ok" for f in fids}
    s1, e1, s2, e2, f1 = fids
    assert kernel.volume(doc.body) == pytest.approx(200 - 4 * math.pi - fillet_loss(0.5, 10), rel=1e-3)
    assert any(n.startswith(f"{f1}/fillet/") for n in doc.names.faces)
    assert f"{e2}/side/{c}" in doc.names.faces                                    # 穴の壁

    # 幅を 10 → 20。穴は上面の同じ位置（部品原点基準）に残り、フィレットはエッジを追って伸びる
    t = time.perf_counter()
    with doc.transaction("幅"):
        sk.constraints[kw].value = 20
    print(f"寸法変更後の再生成 {1000 * (time.perf_counter() - t):.0f} ms")
    assert all(s.state == "ok" for s in doc.status.values()), doc.tree()
    assert kernel.volume(doc.body) == pytest.approx(400 - 4 * math.pi - fillet_loss(0.5, 20), rel=1e-3)
    axis = kernel.face_axis(doc.body, doc.names.find_face(f"{e2}/side/{c}").handle)
    assert axis[0][0] == pytest.approx(5) and axis[0][1] == pytest.approx(2.5)

    doc.undo()
    assert kernel.volume(doc.body) == pytest.approx(200 - 4 * math.pi - fillet_loss(0.5, 10), rel=1e-3)


def test_assembly_mates_on_real_kernel(kernel):
    blk, sk, kw, (s1, e1, s2, e2, f1), c = block_with_hole_and_fillet(kernel)
    blk.regenerate()
    pin = PartDocument(kernel)
    ps = Sketch(); pc = ps.circle((0, 0), 1)
    p1 = pin.add(SketchFeature(sketch=ps)); pe = pin.add(ExtrudeFeature(sketch=p1, distance=8))
    pin.regenerate()
    a = Assembly({"block": PartSource(blk), "pin": PartSource(pin)})
    B = a.add_component("block"); P = a.add_component("pin", position=(2, 9, 7))
    a.mate("concentric", (B, f"{e2}/side/{c}"), (P, f"{pe}/side/{pc}"))
    a.mate("coincident", (B, f"{e1}/end"), (P, f"{pe}/start"))
    r = a.solve()
    assert r.status == "ok" and r.component_dof[P] == 1 and r.flipped == []
    assert a.components[P].position == pytest.approx((5, 2.5, 4), abs=1e-6)
    with blk.transaction("厚み"):
        blk.get(e1).distance = 6
    assert a.solve().status == "ok"
    assert a.components[P].position == pytest.approx((5, 2.5, 6), abs=1e-6)


def test_usd_save_on_real_kernel(tmp_path):
    """S: 実カーネルで USD 保存 → 開き直し。BRep はパートから reference され、usd_brep で合成後のステージから読める。"""
    import usd_brep as ub
    from cad.api.rpc import Api
    from cad.doc.workspace import Workspace
    api = Api(Workspace(REAL[0]))
    did = api.call("doc.demo")["doc"]
    vol = api.call("doc.state", {"doc": did, "mesh": False})["volume"]
    r = api.call("doc.save", {"doc": did, "path": str(tmp_path / "demo")})
    assert r["warnings"] == []
    text = open(r["path"], encoding="utf-8").read()
    assert "prepend references = @./demo_body.usda@<" in text
    breps = ub.usd.import_breps(r["path"])                  # パート経由（reference を合成して読む）
    b = breps[0][0] if isinstance(breps[0], tuple) else breps[0]
    assert len(breps) == 1 and ub.compute_volume(b) == pytest.approx(vol, rel=1e-5)
    a = api.call("asm.new", {"name": "A"})["doc"]
    api.call("asm.add", {"doc": a, "part": did})
    api.call("asm.add", {"doc": a, "part": did})
    ra = api.call("doc.save", {"doc": a, "path": str(tmp_path / "asm")})
    assert len(ub.usd.import_breps(ra["path"])) == 2        # アセンブリ → パート → BRep の 2 段の reference
    st = Api(Workspace(REAL[0])).call("doc.open", {"path": r["path"]})
    assert st["volume"] == pytest.approx(vol, rel=1e-6)


def test_convert_entities_on_real_kernel():
    """スケッチの使い勝手: 実カーネルのエッジ（穴の円・板の外形）を正面に投影して、完全定義の輪郭になること。"""
    from cad.api.rpc import Api
    from cad.doc.workspace import Workspace
    api = Api(Workspace(REAL[0]))
    did = api.call("doc.demo")["doc"]
    m = api.call("doc.state", {"doc": did})["mesh"]
    fi = m["face_names"].index("Extrude1/start")
    edges = [n for n, f in zip(m["edge_names"], m["edge_faces"]) if fi in f]
    st = api.call("sketch.new", {"doc": did, "plane": {"plane": "Front"}})
    st = api.call("sketch.convert", {"doc": did, "edges": edges})
    sk = st["sketch"]
    assert st["warnings"] == [] and len(st["created"]) == len(edges), st["warnings"]
    assert sk["solve"]["state"] == "full" and sk["profiles"] >= 1, sk["solve"]
    api.call("sketch.end", {"doc": did})
    st = api.call("variable.set", {"doc": did, "name": "width", "expr": "120"})
    t = next(t for t in st["tree"] if t["id"] == sk["id"])
    assert t["state"] == "ok", t


def test_new_features_on_real_kernel():
    """F2: 全貫通カット、穴の直線パターン・円形パターン、ミラー、参照平面（実カーネルで体積を確かめる）。"""
    import math
    from cad.api.rpc import Api
    from cad.doc.workspace import Workspace
    api = Api(Workspace(REAL[0]))
    did = api.call("doc.new")["doc"]
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Front"}})
    api.call("sketch.add", {"doc": did, "kind": "rect", "points": [[-50, -30], [50, 30]]})
    st = api.call("feature.add", {"doc": did, "type": "extrude", "sketch": "Sketch1", "distance": 10})
    base = 100 * 60 * 10
    assert st["volume"] == pytest.approx(base, rel=1e-6)
    node = lambda st, fid: next(t for t in st["tree"] if t["id"] == fid)
    # 上面に穴（半径 3）を全貫通でカット
    api.call("sketch.new", {"doc": did, "plane": {"face": "Extrude1/end"}})
    api.call("sketch.add", {"doc": did, "kind": "circle", "points": [[-40, -20], [-37, -20]]})
    st = api.call("feature.add", {"doc": did, "type": "extrude", "sketch": "Sketch2", "op": "cut", "end": "through"})
    hole = math.pi * 9 * 10
    assert node(st, "Extrude2")["state"] == "ok", node(st, "Extrude2")
    assert st["volume"] == pytest.approx(base - hole, rel=1e-3)
    # 直線パターン: X へ 4 個 × Y へ 2 個
    st = api.call("feature.add", {"doc": did, "type": "lpattern", "seeds": ["Extrude2"], "spacing": 20, "count": 4})
    lp = st["created"][0]
    assert node(st, lp)["state"] == "ok", node(st, lp)
    assert st["volume"] == pytest.approx(base - 4 * hole, rel=1e-3)
    api.call("feature.set", {"doc": did, "id": lp, "key": "direction2", "value": {"axis": "Y"}})
    api.call("feature.edit", {"doc": did, "id": lp, "key": "spacing2", "value": 40})
    st = api.call("feature.edit", {"doc": did, "id": lp, "key": "count2", "value": 2})
    assert node(st, lp)["state"] == "ok", node(st, lp)
    assert st["volume"] == pytest.approx(base - 8 * hole, rel=1e-3)
    # 中間平面のボス + 円形パターン（穴の円筒面を軸に）+ ミラー
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Top"}})
    api.call("sketch.add", {"doc": did, "kind": "rect", "points": [[40, -2], [60, -8]]})
    st = api.call("feature.add", {"doc": did, "type": "extrude", "sketch": "Sketch3", "distance": 20, "end": "mid"})
    rib = st["created"][0]
    assert node(st, rib)["state"] == "ok", node(st, rib)
    v = st["volume"]
    st = api.call("feature.add", {"doc": did, "type": "mirror", "seeds": [rib], "plane": {"plane": "Right"}})
    assert node(st, st["created"][0])["state"] == "ok", node(st, st["created"][0])
    assert st["volume"] == pytest.approx(v + 10 * 6 * 20, rel=1e-3)
    st = api.call("feature.add", {"doc": did, "type": "cpattern", "seeds": [rib], "axis": {"axis": "Z"}, "count": 2})
    assert node(st, st["created"][0])["state"] == "ok", node(st, st["created"][0])   # 180° 回した位置はミラーと同じ → 体積は変わらない
    assert st["volume"] == pytest.approx(v + 10 * 6 * 20, rel=1e-3)
    # 参照平面の上のスケッチ
    st = api.call("feature.add", {"doc": did, "type": "plane", "base": {"face": "Extrude1/end"}, "offset": 5})
    api.call("sketch.new", {"doc": did, "plane": {"feature": st["created"][0]}})
    api.call("sketch.add", {"doc": did, "kind": "circle", "points": [[0, 0], [4, 0]]})
    st = api.call("feature.add", {"doc": did, "type": "extrude", "sketch": "Sketch4", "distance": 5, "reverse": True})
    assert node(st, st["created"][0])["state"] == "ok", node(st, st["created"][0])
    assert st["volume"] == pytest.approx(v + 10 * 6 * 20 + math.pi * 16 * 5, rel=1e-3)


def test_chamfer_and_shell_on_real_kernel():
    """面取り（fillet_edges xsect_type=0）とシェル（shell_brep）。k_probe のレポートから書いたので、実カーネルで体積を確かめる。"""
    import numpy as np
    from cad.api.rpc import Api
    from cad.doc.workspace import Workspace
    api = Api(Workspace(REAL[0]))
    did = api.call("doc.new")["doc"]
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Front"}})
    api.call("sketch.add", {"doc": did, "kind": "rect", "points": [[0, 0], [100, 60]]})
    st = api.call("feature.add", {"doc": did, "type": "extrude", "sketch": "Sketch1", "distance": 20})
    base = 100 * 60 * 20
    assert st["volume"] == pytest.approx(base, rel=1e-6)
    node = lambda st, fid: next(t for t in st["tree"] if t["id"] == fid)
    doc = api.ws.get(did)
    # 上面の長い辺（長さ 100）を 2 mm 面取り → 0.5 × 2 × 2 × 100 = 200 減る
    def length(name):
        segs = np.asarray(doc.kernel.edge_polyline(doc.body, doc.names.edges[name]), float).reshape(-1, 2, 3)
        return float(np.linalg.norm(segs[:, 1] - segs[:, 0], axis=1).sum())
    edge = next(n for n in doc.names.edges if "Extrude1/end" in n and abs(length(n) - 100) < 1e-3)
    st = api.call("feature.add", {"doc": did, "type": "chamfer", "edges": [edge], "distance": 2})
    c = st["created"][0]
    assert node(st, c)["state"] == "ok", node(st, c)
    assert st["volume"] == pytest.approx(base - 200, rel=1e-4)
    assert any(n.startswith(f"{c}/chamfer/") for n in st["mesh"]["face_names"]), st["mesh"]["face_names"]
    api.call("feature.suppress", {"doc": did, "id": c, "value": True})
    # 上面を取り除いて内側へ厚さ 2 のシェル → 100×60×20 − 96×56×18
    st = api.call("feature.add", {"doc": did, "type": "shell", "faces": ["Extrude1/end"], "thickness": 2})
    sh = st["created"][0]
    assert node(st, sh)["state"] == "ok", node(st, sh)
    assert st["volume"] == pytest.approx(base - 96 * 56 * 18, rel=1e-3)
    walls = [n for n in st["mesh"]["face_names"] if n.startswith(f"{sh}/wall/")]
    assert len(walls) >= 5, st["mesh"]["face_names"]
    inward = st["volume"]
    st = api.call("feature.set", {"doc": did, "id": sh, "key": "outward", "value": True})
    assert node(st, sh)["state"] == "ok", node(st, sh)
    assert st["volume"] > inward
    # 面取りを戻しても、シェルが続けて作れる（面取りの面も壁になる）
    api.call("feature.set", {"doc": did, "id": sh, "key": "outward", "value": False})
    st = api.call("feature.suppress", {"doc": did, "id": c, "value": False})
    assert node(st, sh)["state"] == "ok", node(st, sh)


def test_model_references_on_real_kernel():
    """D54: モデルの円エッジの中心・直線エッジの中点 / 端点への外部参照が実カーネルの形状で正しく、モデルに追従する。"""
    from cad.api.rpc import Api
    from cad.doc.workspace import Workspace
    api = Api(Workspace(REAL[0]))
    d = api.call("doc.new")["doc"]
    api.call("sketch.new", {"doc": d, "plane": {"plane": "Front"}})
    api.call("sketch.add", {"doc": d, "kind": "rect", "points": [[0, 0], [40, 20]]})
    api.call("sketch.add", {"doc": d, "kind": "circle", "points": [[60, 10], [65, 10]]})
    api.call("sketch.end", {"doc": d})
    api.call("feature.add", {"doc": d, "type": "extrude", "sketch": "Sketch1", "distance": 10})
    names = api.call("doc.state", {"doc": d})["mesh"]["edge_names"]
    api.call("sketch.new", {"doc": d, "plane": {"plane": "Front"}})
    got = {}
    for n in names:
        for at in ("center", "mid", "vertex"):
            try:
                st = api.call("sketch.reference", {"doc": d, "source": f"edge:{n}@{at}", "x": 100, "y": 100})
            except Exception:                     # noqa: BLE001 合わない組み合わせ（直線の中心など）は飛ばす
                continue
            P = {e["id"]: (round(e["x"], 6), round(e["y"], 6)) for e in st["sketch"]["entities"] if e["type"] == "point"}
            got.setdefault(at, set()).add(P[st["created"][0]])
    assert (60, 10) in got["center"]
    assert {(20, 0), (40, 10), (20, 20), (0, 10)} <= got["mid"]
    assert (40, 20) in got["vertex"]


def test_hole_wizard_on_real_kernel():
    """D57: 穴ウィザード（全貫通のキリ穴 2 個、先端付きのザグリ穴、皿穴、穴の直線パターン）の体積と面の名前。"""
    from cad.api.rpc import Api
    from cad.doc.workspace import Workspace
    api = Api(Workspace(REAL[0]))
    d = api.call("doc.new")["doc"]
    api.call("sketch.new", {"doc": d, "plane": {"plane": "Front"}})
    api.call("sketch.add", {"doc": d, "kind": "rect", "points": [[0, 0], [100, 60]]})
    st = api.call("feature.add", {"doc": d, "type": "extrude", "sketch": "Sketch1", "distance": 20})
    v = st["volume"]
    node = lambda st, fid: next(t for t in st["tree"] if t["id"] == fid)
    cone = lambda r1, r2, h: math.pi * h * (r1 * r1 + r1 * r2 + r2 * r2) / 3

    def holes(pts, **kw):
        api.call("sketch.new", {"doc": d, "plane": {"face": "Extrude1/end"}})
        for p in pts:
            sk = api.call("sketch.add", {"doc": d, "kind": "point", "points": [p]})["sketch"]["id"]
        api.call("sketch.end", {"doc": d})
        st = api.call("feature.add", {"doc": d, "type": "hole", "sketch": sk, **kw})
        h = st["created"][0]
        assert node(st, h)["state"] == "ok", node(st, h)
        return st, h

    st, h = holes([[15, 15], [15, 45]], kind="clearance", size="M6", end="through")
    cut = 2 * math.pi * 3.3 ** 2 * 20
    assert v - st["volume"] == pytest.approx(cut, rel=2e-3)
    assert f"{h}/side/wall" in st["mesh"]["face_names"] and f"{h}~2/side/wall" in st["mesh"]["face_names"]
    st, h = holes([[50, 30]], kind="cbore", size="M8", depth=15)
    r, R, hc = 4.5, 7.0, 8.6
    cut += math.pi * R * R * hc + math.pi * r * r * (15 - hc) + cone(r, 0, r / math.tan(math.radians(59)))
    assert v - st["volume"] == pytest.approx(cut, rel=3e-3)
    st, h = holes([[80, 15]], kind="csink", size="M6", end="through")
    r, Rs = 3.3, 6.3
    one = cone(Rs, r, Rs - r) + math.pi * r * r * (20 - (Rs - r))
    cut += one
    assert v - st["volume"] == pytest.approx(cut, rel=3e-3)
    st = api.call("feature.add", {"doc": d, "type": "lpattern", "seeds": [h], "direction": {"axis": "Y"}, "spacing": 30, "count": 2})
    assert node(st, st["created"][0])["state"] == "ok", node(st, st["created"][0])
    assert v - st["volume"] == pytest.approx(cut + one, rel=3e-3)


def test_sweep_on_real_kernel():
    """D58: スイープ（円の断面を 直線 → 円弧 → 直線 の経路に沿って）の体積・面の名前、逆向きに描いた経路、スイープカット、
    S 字の経路（時計回りにたどる円弧を含む）、閉じた経路。"""
    from cad.api.rpc import Api
    from cad.doc.workspace import Workspace
    api = Api(Workspace(REAL[0]))
    node = lambda st, fid: next(t for t in st["tree"] if t["id"] == fid)

    def sk(d, plane, adds):
        api.call("sketch.new", {"doc": d, "plane": {"plane": plane}})
        for kind, pts in adds:
            st = api.call("sketch.add", {"doc": d, "kind": kind, "points": pts})
        api.call("sketch.end", {"doc": d})
        return st["sketch"]["id"]

    L = 20 + 15 + 10 * math.pi / 2
    for order in ("forward", "backward"):
        d = api.call("doc.new")["doc"]
        segs = [("line", [[0, 0], [20, 0]]), ("arc", [[20, 10], [20, 0], [30, 10]]), ("line", [[30, 10], [30, 25]])]
        if order == "backward":                     # 終点側から描いた（線の向きも逆）
            segs = [("line", [[30, 25], [30, 10]]), ("arc", [[20, 10], [20, 0], [30, 10]]), ("line", [[20, 0], [0, 0]])]
        path = sk(d, "Top", segs)
        prof = sk(d, "Right", [("circle", [[0, 0], [2, 0]])])
        st = api.call("feature.add", {"doc": d, "type": "sweep", "sketch": prof, "path": path})
        sw = st["created"][0]
        assert node(st, sw)["state"] == "ok", (order, node(st, sw).get("message"))
        assert st["volume"] == pytest.approx(math.pi * 4 * L, rel=2e-3), order
        names = st["mesh"]["face_names"]
        assert {f"{sw}/start", f"{sw}/end"} <= set(names), names
        assert any(n.startswith(f"{sw}/side/") for n in names), names
    # スイープカット: 板（厚さ 10、中間平面）の中を通る半径 2 の溝。経路は板の外から入って外へ抜ける
    d = api.call("doc.new")["doc"]
    sk(d, "Top", [("rect", [[-10, -40], [50, 30]])])
    st = api.call("feature.add", {"doc": d, "type": "extrude", "sketch": "Sketch1", "distance": 10, "end": "mid"})
    v0 = st["volume"]
    path = sk(d, "Top", [("line", [[-20, 0], [20, 0]]), ("arc", [[20, 10], [20, 0], [30, 10]]), ("line", [[30, 10], [30, 40]])])
    prof = sk(d, "Right", [("circle", [[0, 0], [2, 0]])])
    # 断面（原点）は経路の始点 (-20, 0) からずれている → カーネルが始点へ動かす（警告）
    st = api.call("feature.add", {"doc": d, "type": "sweep", "sketch": prof, "path": path, "op": "cut"})
    assert node(st, st["created"][0])["state"] in ("ok", "warning"), node(st, st["created"][0]).get("message")
    assert v0 - st["volume"] == pytest.approx(math.pi * 4 * (30 + 5 * math.pi + 20), rel=3e-3)
    # S 字（2 つ目の円弧は時計回りにたどる）
    d = api.call("doc.new")["doc"]
    path = sk(d, "Top", [("line", [[0, 0], [10, 0]]), ("arc", [[10, 10], [10, 0], [20, 10]]),
                         ("arc", [[30, 10], [30, 20], [20, 10]])])
    prof = sk(d, "Right", [("circle", [[0, 0], [1.5, 0]])])
    st = api.call("feature.add", {"doc": d, "type": "sweep", "sketch": prof, "path": path})
    assert node(st, st["created"][0])["state"] == "ok", node(st, st["created"][0]).get("message")
    assert st["volume"] == pytest.approx(math.pi * 2.25 * (10 + 2 * 10 * math.pi / 2), rel=3e-3)
    # 閉じた経路（長方形の輪）
    d = api.call("doc.new")["doc"]
    path = sk(d, "Top", [("rect", [[0, 0], [40, 30]])])
    prof = sk(d, "Right", [("circle", [[0, 0], [2, 0]])])
    st = api.call("feature.add", {"doc": d, "type": "sweep", "sketch": prof, "path": path})
    assert node(st, st["created"][0])["state"] == "ok", node(st, st["created"][0]).get("message")
    assert st["volume"] == pytest.approx(math.pi * 4 * 140, rel=3e-3)


def test_drawing_on_real_kernel(tmp_path):
    """D60: 実カーネルのパート（板 60×40×10 + 円柱 Ø10×15 + 貫通穴 Ø8）の投影・隠線、寸法の追従、PDF / DXF。"""
    import numpy as np
    from cad.api.rpc import Api
    from cad.doc.workspace import Workspace, display_mesh
    from cad.drawing.hlr import ORIENTS, project_view
    api = Api(Workspace(REAL[0]))
    d = api.call("doc.new", {"name": "Plate"})["doc"]
    api.call("sketch.new", {"doc": d, "plane": {"plane": "Front"}})
    api.call("sketch.add", {"doc": d, "kind": "rect", "points": [[0, 0], [60, 40]]})
    api.call("feature.add", {"doc": d, "type": "extrude", "sketch": "Sketch1", "distance": 10})
    api.call("sketch.new", {"doc": d, "plane": {"face": "Extrude1/end"}})
    api.call("sketch.add", {"doc": d, "kind": "circle", "points": [[20, 20], [25, 20]]})
    api.call("feature.add", {"doc": d, "type": "extrude", "sketch": "Sketch2", "distance": 15})
    api.call("sketch.new", {"doc": d, "plane": {"face": "Extrude1/end"}})
    api.call("sketch.add", {"doc": d, "kind": "circle", "points": [[45, 20], [49, 20]]})
    st = api.call("feature.add", {"doc": d, "type": "extrude", "sketch": "Sketch3", "distance": 10, "op": "cut"})
    assert all(t["state"] == "ok" for t in st["tree"] if t["type"] == "extrude"), st["tree"]

    def length(segs):
        a = np.asarray(segs, float).reshape(-1, 4)
        return float(np.hypot(a[:, 2] - a[:, 0], a[:, 3] - a[:, 1]).sum())

    m = display_mesh(api.ws.get(d))
    g = project_view(m, *ORIENTS["front"])
    assert g.bbox == pytest.approx((0, 0, 60, 40), abs=1e-3)
    # 正面: 外形 + 円柱の円 + 穴の円（見える）。穴の壁の奥の線は重なるので隠線はほぼ無い
    assert length(g.segs[~g.hidden]) == pytest.approx(200 + 2 * math.pi * 5 + 2 * math.pi * 4, rel=1e-2)
    circles = sorted(round(e["r"], 3) for e in g.edges if e["kind"] == "circle")
    assert 4 in circles and 5 in circles, g.edges
    # 平面図: 穴は隠線（板の厚さ 10 の縦線 2 本）
    g = project_view(m, *ORIENTS["top"])
    assert length(g.segs[g.hidden]) == pytest.approx(2 * 10, rel=2e-2)
    # 図面: 標準 3 面図、円柱の直径寸法、モデルを変えると追従
    st = api.call("drw.new", {"model": d, "scale": "1:1"})
    drw, f = st["doc"], st["views"][0]
    assert len(st["views"]) == 3
    circ = next(e for e in f["edges"] if e["kind"] == "circle" and abs(e["r"] - 5) < 1e-3)
    st = api.call("drw.dim_add", {"doc": drw, "view": f["id"], "a": {"src": "edge:" + circ["name"]}, "pos": [20, 30]})
    assert st["dims"][0]["text"] == "Ø10"
    # PDF / DXF（日本語の表題欄を含む）
    p = api.call("drw.export", {"doc": drw, "format": "pdf", "path": str(tmp_path / "real")})["path"]
    data = open(p, "rb").read()
    assert data.startswith(b"%PDF-1.4") and b"HeiseiKakuGo-W5" in data
    p = api.call("drw.export", {"doc": drw, "format": "dxf", "path": str(tmp_path / "real.dxf")})["path"]
    L = open(p, encoding="cp932").read().split("\n")
    assert L.count("CIRCLE") >= 2 and "HIDDEN" in L and L[-2] == "EOF"


def test_car_demo_on_real_kernel():
    """D63: 車のデモ部品が全フィーチャー ok で作れる。変数 width を変えても通る（式でつないだ寸法）。"""
    from cad.api.rpc import Api
    from cad.doc.workspace import Workspace
    api = Api(Workspace(REAL[0]))
    st = api.call("doc.demo", {"kind": "car"})
    d = st["doc"]
    bad = [(t["id"], t.get("message")) for t in st["tree"] if t["type"] != "sketch" and t["state"] != "ok"]
    assert not bad, bad
    assert st["undo"] is None
    v = st["volume"]
    st = api.call("variable.set", {"doc": d, "name": "width", "expr": "90"})
    assert all(t["state"] == "ok" for t in st["tree"] if t["type"] != "sketch")
    assert st["volume"] > v


def test_feature_that_changes_nothing_warns(kernel):
    """D68: ボディの中に埋まった追加・材料に届かないカットは、カーネルはエラーにしないが警告にする。"""
    doc = PartDocument(kernel)

    def box(x0, y0, x1, y1, **kw):
        sk = Sketch(); sk.rect(x0, y0, x1, y1)
        return doc.add(ExtrudeFeature(sketch=doc.add(SketchFeature(sketch=sk)), **kw))
    base = box(0, 0, 10, 10, distance=10)
    inside = box(2, 2, 8, 8, distance=5)                      # 中に埋まる
    miss = box(2, 2, 8, 8, distance=5, op="cut", reverse=True)   # 平面から +Z へ掘る: 材料（0〜10）の中なので削れる
    away = box(20, 20, 25, 25, distance=5, op="cut")          # 材料の無い所
    st = doc.regenerate()
    assert st[base].state == "ok" and st[miss].state == "ok", doc.tree()
    for f in (inside, away):
        assert st[f].state == "warning" and "形が変わりませんでした" in st[f].message, (f, st[f])


def test_appearance_colors_and_save(kernel, tmp_path):
    """D69: 外観。面 > フィーチャー > パート、パターンは元の色を引き継ぐ。保存して開いても残り、Undo できる。"""
    from cad.api.rpc import Api
    from cad.doc.workspace import Workspace
    api = Api(Workspace(next(n for n, k in available_kernels().items() if isinstance(kernel, k))))
    did = api.call("doc.new", {"name": "C"})["doc"]
    call = lambda m, **p: api.call(m, {"doc": did, **p})
    call("sketch.new", plane={"plane": "Front"}); call("sketch.add", kind="rect", points=[[0, 0], [40, 20]]); call("sketch.end")
    call("feature.add", type="extrude", sketch="Sketch1", distance=10)
    call("sketch.new", plane={"face": "Extrude1/end"}); call("sketch.add", kind="circle", points=[[5, 5], [8, 5]]); call("sketch.end")
    call("feature.add", type="extrude", sketch="Sketch2", distance=5)
    call("feature.add", type="lpattern", seeds=["Extrude2"], direction={"axis": "X"}, spacing=10, count=3)
    call("appearance.set", color="#336699")
    call("appearance.set", feature="Extrude2", color="#CC0000")
    st = call("appearance.set", face="Extrude1/start", color="#00aa00")
    m = st["mesh"]
    col = dict(zip(m["face_names"], m["face_colors"]))
    assert m["part_color"] == "#336699" and col["Extrude1/start"] == "#00aa00"
    assert col["Extrude1/side/" + next(n.split("/")[-1] for n in col if n.startswith("Extrude1/side/"))] == "#336699"
    assert all(c == "#cc0000" for n, c in col.items() if n.startswith(("Extrude2/", "LPattern1~")))
    assert any(n.startswith("LPattern1~") for n in col)
    path = str(tmp_path / "c.usda")
    call("doc.save", path=path)
    assert "primvars:displayColor" in open(path, encoding="utf-8").read()
    st2 = api.call("doc.open", {"path": path})
    assert st2["doc"] == did                                   # 開いている文書はそのまま
    call("edit.undo")
    assert call("doc.state")["appearance"]["faces"] == {}
    api.call("doc.close", {"doc": did})
    st3 = api.call("doc.open", {"path": path})
    assert st3["appearance"]["faces"] == {"Extrude1/start": "#00aa00"} and st3["appearance"]["part"] == "#336699"
    assert api.call("ui.progress") is None                     # 開き終わったら進み具合は消える


def test_open_reports_progress(kernel, tmp_path):
    """D69: 開いている途中、フィーチャーごとに ws.progress が進む。"""
    from cad.api.rpc import Api
    from cad.doc.workspace import Workspace
    ws = Workspace(next(n for n, k in available_kernels().items() if isinstance(kernel, k))); api = Api(ws)
    did = api.call("doc.demo", {"kind": "plate"})["doc"]
    path = str(tmp_path / "p.usda"); api.call("doc.save", {"doc": did, "path": path}); api.call("doc.close", {"doc": did})
    seen = []
    orig = ws._add

    def spy(doc, name, path=None):
        if hasattr(doc, "regenerate"):
            r0 = doc.regenerate
            doc.regenerate = lambda: (seen.append(dict(ws.progress or {})), r0())[1]
        return orig(doc, name, path)
    ws._add = spy
    api.call("doc.open", {"path": path})
    assert seen and seen[0]["title"].startswith("p.usda")
