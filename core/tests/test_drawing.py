"""2 次元図面（D60）: 投影（隠線）、ビューの配置、寸法、PDF / DXF、保存。"""
import math
import zlib

import numpy as np
import pytest

from cad.api.rpc import Api, RpcError
from cad.doc.workspace import Workspace, display_mesh
from cad.drawing.document import auto_direction, project_frame
from cad.drawing.hlr import ORIENTS, project_view
from cad.drawing.render import linear_dim


@pytest.fixture
def part():
    """板 60×40×10 の上面（z = 10）に、直径 10・高さ 15 の円柱。"""
    api = Api(Workspace("prism"))
    d = api.call("doc.new", {"name": "Plate"})["doc"]
    api.call("sketch.new", {"doc": d, "plane": {"plane": "Front"}})
    api.call("sketch.add", {"doc": d, "kind": "rect", "points": [[0, 0], [60, 40]]})
    api.call("feature.add", {"doc": d, "type": "extrude", "sketch": "Sketch1", "distance": 10})
    api.call("sketch.new", {"doc": d, "plane": {"face": "Extrude1/end"}})
    api.call("sketch.add", {"doc": d, "kind": "circle", "points": [[20, 20], [25, 20]]})
    api.call("feature.add", {"doc": d, "type": "extrude", "sketch": "Sketch2", "distance": 15})
    return api, d


def view(st, vid):
    return next(v for v in st["views"] if v["id"] == vid)


def length(segs):
    a = np.asarray(segs, float).reshape(-1, 4)
    return float(np.hypot(a[:, 2] - a[:, 0], a[:, 3] - a[:, 1]).sum())


def test_projection_visible_and_hidden(part):
    api, d = part
    m = display_mesh(api.ws.get(d))
    g = project_view(m, *ORIENTS["front"])
    assert g.bbox == pytest.approx((0, 0, 60, 40), abs=1e-6)
    vis = g.segs[~g.hidden]
    assert length(vis) == pytest.approx(2 * (60 + 40) + 2 * math.pi * 5, rel=5e-3)      # 外形 + 円（重なった線は 1 本）
    assert [e["kind"] for e in g.edges].count("circle") >= 1
    c = next(e for e in g.edges if e["kind"] == "circle")
    assert c["c"] == pytest.approx([20, 20]) and c["r"] == pytest.approx(5) and c["whole"]
    # 背面から見ると、円柱は板に隠れる（円が隠線になる）
    g = project_view(m, *ORIENTS["back"])
    assert length(g.segs[g.hidden]) == pytest.approx(2 * math.pi * 5, rel=2e-2)
    assert length(g.segs[~g.hidden]) == pytest.approx(200, rel=5e-3)
    # 平面図: 板（60×10）の手前に円柱（幅 10・高さ 15）。輪郭線（円柱の両端の母線）が出る。上は奥（-z）
    g = project_view(m, *ORIENTS["top"])
    assert g.bbox == pytest.approx((0, -25, 60, 0), abs=1e-6)
    assert length(g.segs[~g.hidden]) == pytest.approx(2 * (60 + 10) + 10 + 2 * 15, rel=1e-2)
    kinds = {s["kind"] for s in g.snaps}
    assert {"vertex", "mid", "center"} <= kinds


def test_new_drawing_standard_views_and_alignment(part):
    api, d = part
    st = api.call("drw.new", {"model": d})
    drw = st["doc"]
    assert st["kind"] == "drawing" and st["sheet"]["size"] == "A3" and st["sheet"]["scale_text"] == "2:1"
    assert [v["label"] for v in st["views"]] == ["正面図", "平面図", "右側面図"]
    f, t, r = st["views"]
    assert t["x"] == pytest.approx(f["x"]) and t["y"] > f["y"] and r["y"] == pytest.approx(f["y"]) and r["x"] > f["x"]
    # 投影図は親とそろう方向にだけ動く。親を動かすとついてくる
    st = api.call("drw.view_set", {"doc": drw, "id": t["id"], "x": t["x"] + 30, "y": t["y"] + 7})
    assert view(st, t["id"])["x"] == pytest.approx(f["x"]) and view(st, t["id"])["y"] == pytest.approx(t["y"] + 7)
    st = api.call("drw.view_set", {"doc": drw, "id": f["id"], "x": f["x"] + 10, "y": f["y"] - 5})
    assert view(st, t["id"])["x"] == pytest.approx(f["x"] + 10) and view(st, t["id"])["y"] == pytest.approx(t["y"] + 7)
    assert view(st, r["id"])["y"] == pytest.approx(f["y"] - 5) and view(st, r["id"])["x"] == pytest.approx(r["x"])
    # 縮尺を変えると図の大きさが変わる。ビューごとの縮尺も
    st = api.call("drw.sheet", {"doc": drw, "scale": "1:2", "size": "A4"})
    b = view(st, f["id"])["bbox"]
    assert (b[2] - b[0], b[3] - b[1]) == pytest.approx((30, 20)) and st["sheet"]["width"] == 297
    st = api.call("drw.view_set", {"doc": drw, "id": f["id"], "scale": [1, 1]})
    b = view(st, f["id"])["bbox"]
    assert b[2] - b[0] == pytest.approx(60) and view(st, f["id"])["scale_text"] == "1:1"
    st = api.call("edit.undo", {"doc": drw})
    assert view(st, f["id"])["scale"] is None
    with pytest.raises(RpcError):
        api.call("drw.sheet", {"doc": drw, "scale": "0:1"})
    # 投影図の向き（第三角法）
    assert project_frame(*ORIENTS["front"], "right")[0] == pytest.approx(ORIENTS["right"][0])
    assert project_frame(*ORIENTS["front"], "up")[1] == pytest.approx(ORIENTS["top"][1])


def test_dimensions_follow_model(part):
    api, d = part
    st = api.call("drw.new", {"model": d, "scale": "1:1"})
    drw, f = st["doc"], st["views"][0]
    V = {tuple(round(c) for c in s["p"]): s for s in f["snaps"] if s["kind"] == "vertex"}
    anchor = lambda p: {"src": V[p]["src"], "hint": V[p]["w"]}
    st = api.call("drw.dim_add", {"doc": drw, "view": f["id"], "a": anchor((0, 0)), "b": anchor((60, 0)), "pos": [30, -10]})
    st = api.call("drw.dim_add", {"doc": drw, "view": f["id"], "a": anchor((60, 0)), "b": anchor((60, 40)), "pos": [72, 20]})
    st = api.call("drw.dim_add", {"doc": drw, "view": f["id"], "a": anchor((0, 0)), "b": anchor((60, 40)), "pos": [20, 25]})
    circ = next(e for e in f["edges"] if e["kind"] == "circle")
    st = api.call("drw.dim_add", {"doc": drw, "view": f["id"], "a": {"src": "edge:" + circ["name"]}, "pos": [35, 35]})
    assert [(x["kind"], x["text"]) for x in st["dims"]] == [("linear", "60"), ("linear", "40"), ("linear", "72.11"), ("diameter", "Ø10")]
    assert not any(x["dangling"] for x in st["dims"])
    # モデルの幅を 60 → 80 に変えると、寸法の値が追従する
    api.call("sketch.begin", {"doc": d, "id": "Sketch1"})
    k = api.call("sketch.dimension", {"doc": d, "refs": ["L5"]})["created"][0]
    api.call("feature.edit", {"doc": d, "id": "Sketch1", "key": k, "value": 80})
    api.call("sketch.end", {"doc": d})
    st = api.call("doc.state", {"doc": drw})
    assert st["dims"][0]["text"] == "80" and st["dims"][1]["text"] == "40" and not st["dims"][0]["dangling"]
    # 動かす・消す・元に戻す
    did = st["dims"][0]["id"]
    st = api.call("drw.dim_set", {"doc": drw, "id": did, "pos": [30, -20]})
    assert st["dims"][0]["pos"] == [30, -20]
    st = api.call("drw.delete", {"doc": drw, "ids": [did]})
    assert len(st["dims"]) == 3
    assert len(api.call("edit.undo", {"doc": drw})["dims"]) == 4
    # 正面を向いていない円には直径を付けられない。ビューを消すと寸法も消える
    top = st["views"][1]
    with pytest.raises(RpcError):
        api.call("drw.dim_add", {"doc": drw, "view": top["id"], "a": {"src": "edge:" + circ["name"]}, "pos": [0, 0]})
    st = api.call("drw.delete", {"doc": drw, "ids": [f["id"]]})
    assert not st["dims"] and [v["parent"] for v in st["views"]] == [None, None]


def test_dim_direction_and_geometry():
    assert auto_direction((0, 0), (10, 5), (5, -8)) == "h"
    assert auto_direction((0, 0), (10, 5), (15, 2)) == "v"
    assert auto_direction((0, 0), (10, 5), (5, 2)) == "aligned"
    assert auto_direction((0, 0), (10, 0), (20, 30)) == "h"
    items, box = linear_dim((0, 0), (40, 0), "h", (20, -10), "40")
    segs = items[0]["segs"]
    assert [0, -1, 0, -12] in [[round(v, 6) for v in s] for s in segs]            # 寸法補助線（すき間 1、はみ出し 2）
    assert [0, -10, 40, -10] in [[round(v, 6) for v in s] for s in segs]          # 寸法線
    assert items[-1]["s"] == "40" and items[-1]["p"] == pytest.approx([20, -9]) and box[0] < 20 < box[2]


def test_export_pdf_dxf_and_save(part, tmp_path):
    api, d = part
    st = api.call("drw.new", {"model": d, "scale": "1:2"})
    drw, f = st["doc"], st["views"][0]
    circ = next(e for e in f["edges"] if e["kind"] == "circle")
    api.call("drw.dim_add", {"doc": drw, "view": f["id"], "a": {"src": "edge:" + circ["name"]}, "pos": [20, 18]})
    api.call("drw.view_set", {"doc": drw, "id": f["id"], "hidden": True})
    p = api.call("drw.export", {"doc": drw, "format": "pdf", "path": str(tmp_path / "out")})["path"]
    data = open(p, "rb").read()
    assert p.endswith(".pdf") and data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    assert b"/MediaBox [0 0 1190.551 841.89]" in data                                # A3 横（pt）
    body = zlib.decompress(data.split(b"stream\n", 1)[1].split(b"\nendstream", 1)[0]).decode("latin-1")
    assert "(\xd810) Tj" in body and "/F2" in body                                    # Ø10 と、全角の文字（表題欄）
    xref = int(data.rsplit(b"startxref\n", 1)[1].split()[0])
    assert data[xref:xref + 4] == b"xref"
    # DXF（用紙どおり）: 円は CIRCLE で、半径は 5 × 1/2
    p = api.call("drw.export", {"doc": drw, "format": "dxf", "path": str(tmp_path / "out.dxf")})["path"]
    L = open(p, encoding="cp932").read().split("\n")
    i = L.index("CIRCLE")
    assert L[i + 1:i + 3] == ["8", "VISIBLE"] and float(L[L.index("40", i) + 1]) == pytest.approx(2.5)
    assert "FRAME" in L and "%%c10" in L and L[-2] == "EOF"
    assert "ANSI_932" in L and "縮尺" in L and not any("\\U+" in x for x in L)      # 全角は Shift-JIS のまま
    # DXF（実寸）: 半径 5、図枠なし
    p = api.call("drw.export", {"doc": drw, "format": "dxf", "path": str(tmp_path / "real.dxf"), "scale": "model"})["path"]
    L = open(p, encoding="cp932").read().split("\n")
    i = L.index("CIRCLE")
    assert float(L[L.index("40", i) + 1]) == pytest.approx(5) and L.count("FRAME") == 1      # レイヤー表の 1 回だけ
    # 保存 → 閉じる → 開く（モデルのパートも一緒に保存される）
    out = api.call("doc.save", {"doc": drw, "path": str(tmp_path / "plate_drw")})
    assert len(out["saved"]) == 2 and out["saved"][0].endswith("plate_drw.usda")
    api.call("doc.close", {"doc": drw}); api.call("doc.close", {"doc": d})
    st = api.call("doc.open", {"path": out["saved"][0]})
    assert st["kind"] == "drawing" and len(st["views"]) == 3 and st["dims"][0]["text"] == "Ø10" and st["views"][0]["hidden"]
    assert {x["kind"] for x in api.call("doc.list")} == {"part", "drawing"}


def test_assembly_drawing(part, tmp_path):
    """D72: アセンブリの図面。部品を姿勢どおりに置いて投影し、エッジの名前は "<部品>/…"。部品を動かすと図面も追従し、
    保存するとアセンブリ（とパート）も一緒に保存され、開き直せる。"""
    api, d = part
    a = api.call("asm.new", {"name": "Pair"})["doc"]
    api.call("asm.add", {"doc": a, "part": d, "position": [0, 0, 0]})
    api.call("asm.add", {"doc": a, "part": d, "position": [100, 0, 0]})
    st = api.call("drw.new", {"model": a, "scale": "1:2"})
    drw, f = st["doc"], st["views"][0]
    assert f["label"] == "正面図" and f["bbox"][2] - f["bbox"][0] == pytest.approx(160 / 2)     # 0..160 を 1:2
    assert st["model"] == {"doc": a, "name": "Pair", "kind": "assembly"}
    names = {s["src"].partition("@")[0] for s in f["snaps"]}
    assert any(n.startswith("edge:Plate<2>/") for n in names) and any(n.startswith("edge:Plate<1>/") for n in names)
    # 2 つ目の部品の右下の端点と、1 つ目の左下の端点の距離 = 160
    def corner(x):
        s = min((s for s in f["snaps"] if s["kind"] == "vertex"), key=lambda s: abs(s["w"][0] - x) + abs(s["w"][1]))
        return {"src": s["src"], "hint": s["w"]}
    st = api.call("drw.dim_add", {"doc": drw, "view": f["id"], "a": corner(0), "b": corner(160), "pos": [40, -10]})
    assert st["dims"][0]["text"] == "160"
    # 部品を動かすと寸法が追従する
    comp = next(c for c in api.call("doc.state", {"doc": a})["components"] if c["id"] == "Plate<2>")
    api.call("asm.drag", {"doc": a, "comp": comp["id"], "position": [120, 0, 0], "final": True})
    assert api.call("doc.state", {"doc": drw})["dims"][0]["text"] == "180"
    # 保存 → 閉じる → 開く
    out = api.call("doc.save", {"doc": drw, "path": str(tmp_path / "pair_drw")})
    assert out["saved"][0].endswith("pair_drw.usda") and any(p.endswith("Pair.usda") for p in out["saved"])
    for x in (drw, a, d):
        api.call("doc.close", {"doc": x})
    st = api.call("doc.open", {"path": out["saved"][0]})
    assert st["kind"] == "drawing" and st["dims"][0]["text"] == "180"
    assert {x["kind"] for x in api.call("doc.list")} == {"part", "assembly", "drawing"}


def test_notes(part, tmp_path):
    """D73: 注記（自由な文字）。ビューに付いて一緒に動き、文字・高さを変えられ、PDF・DXF にも出て、保存して開き直せる。"""
    api, d = part
    st = api.call("drw.new", {"model": d, "scale": "1:2"})
    drw, f = st["doc"], st["views"][0]
    st = api.call("drw.note_add", {"doc": drw, "view": f["id"], "text": "リビング", "pos": [10, 5]})
    nid = st["created"][0]
    n = next(x for x in st["dims"] if x["id"] == nid)
    assert n["kind"] == "note" and n["text"] == "リビング" and n["h"] == 3.5 and n["box"][0] < 10 < n["box"][2]
    assert n["items"][0]["k"] == "text" and n["items"][0]["align"] == "c"
    st = api.call("drw.note_set", {"doc": drw, "id": nid, "text": "LDK", "h": 5})
    n = next(x for x in st["dims"] if x["id"] == nid)
    assert n["text"] == "LDK" and n["h"] == 5
    # ビューを動かすと一緒に動く（位置はビューからの相対）
    st = api.call("drw.view_set", {"doc": drw, "id": f["id"], "x": f["x"] + 20, "y": f["y"]})
    n = next(x for x in st["dims"] if x["id"] == nid)
    assert n["x"] == pytest.approx(f["x"] + 20) and n["pos"] == [10, 5]
    with pytest.raises(RpcError):
        api.call("drw.note_add", {"doc": drw, "view": f["id"], "text": "  "})
    with pytest.raises(RpcError):
        api.call("drw.note_set", {"doc": drw, "id": nid, "h": 0})
    p = api.call("drw.export", {"doc": drw, "format": "dxf", "path": str(tmp_path / "n.dxf")})["path"]
    assert "LDK" in open(p, encoding="cp932").read()
    out = api.call("doc.save", {"doc": drw, "path": str(tmp_path / "n_drw")})
    api.call("doc.close", {"doc": drw}); api.call("doc.close", {"doc": d})
    st = api.call("doc.open", {"path": out["saved"][0]})
    assert [x["text"] for x in st["dims"] if x["kind"] == "note"] == ["LDK"]
    st = api.call("drw.delete", {"doc": st["doc"], "ids": [nid]})
    assert not st["dims"]
