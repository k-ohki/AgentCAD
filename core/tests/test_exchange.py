"""メッシュのファイル（STL・3MF・OBJ）の読み込み・書き出し（D71）。"""
import math
import zipfile

import numpy as np
import pytest

from cad.api.rpc import Api, RpcError
from cad.doc import meshio
from cad.doc.workspace import Workspace, kernel_choices
from cad.kernel.meshbody import decode, encode, mesh_shape
from test_asm_api import comp, make_part


def box(lx=40.0, ly=20.0, lz=10.0):
    """直方体（外向き）。面ごとに別の頂点を持つ三角形スープにして、溶接を確かめる。"""
    P = np.array([[x, y, z] for x in (0, lx) for y in (0, ly) for z in (0, lz)], float)
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    tri = [t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))]
    T = P[np.array(tri)]
    return T.reshape(-1, 3), np.arange(len(T) * 3).reshape(-1, 3)


def cylinder(r=5.0, h=12.0, n=48):
    ang = np.linspace(0, 2 * math.pi, n, endpoint=False)
    ring = np.c_[r * np.cos(ang), r * np.sin(ang)]
    P = np.vstack([np.c_[ring, np.zeros(n)], np.c_[ring, np.full(n, h)], [[0, 0, 0], [0, 0, h]]])
    tri = []
    for i in range(n):
        j = (i + 1) % n
        tri += [(i, j, n + j), (i, n + j, n + i), (2 * n, j, i), (2 * n + 1, n + i, n + j)]
    return P, np.array(tri)


def write_mesh(path, P, T, **kw):
    meshio.write(str(path), [meshio.MeshObject("m", P, T, **kw)])
    return str(path)


# ---------------------------------------------------------------- ファイル形式


@pytest.mark.parametrize("ext", ["stl", "3mf", "obj"])
def test_write_read_roundtrip(tmp_path, ext):
    P, T = box()
    files = meshio.write(str(tmp_path / f"b.{ext}"), [meshio.MeshObject("b", P, T, np.zeros(len(T), int), ["#ff0000"])])
    assert len(files) == (2 if ext == "obj" else 1)                # OBJ は色の .mtl も
    objs = meshio.read(files[0])
    assert sum(len(o.tri) for o in objs) == 12
    Q = np.vstack([o.pos for o in objs])
    assert Q.min(0) == pytest.approx([0, 0, 0]) and Q.max(0) == pytest.approx([40, 20, 10])
    if ext == "3mf":
        assert objs[0].palette == ["#ff0000"]


def test_ascii_stl_and_3mf_units_and_transform(tmp_path):
    p = tmp_path / "a.stl"
    p.write_text("solid x\n facet normal 0 0 1\n  outer loop\n   vertex 0 0 0\n   vertex 1 0 0\n   vertex 0 1 0\n"
                 "  endloop\n endfacet\nendsolid x\n")
    assert len(meshio.read(str(p))[0].tri) == 1
    model = ('<?xml version="1.0"?><model unit="centimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             '<resources><object id="1" type="model"><mesh><vertices><vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/>'
             '<vertex x="0" y="1" z="0"/></vertices><triangles><triangle v1="0" v2="1" v3="2"/></triangles></mesh></object>'
             '</resources><build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 5 0 0"/></build></model>')
    with zipfile.ZipFile(tmp_path / "a.3mf", "w") as z:
        z.writestr("3D/3dmodel.model", model)
    o = meshio.read(str(tmp_path / "a.3mf"))[0]
    assert o.pos.max(0) == pytest.approx([60, 10, 0])               # (5 + 1) cm → 60 mm


def test_obj_polygons_and_negative_indices(tmp_path):
    p = tmp_path / "q.obj"
    p.write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\no quad\nf -4/1 -3/2 -2/3 -1/4\n")
    o = meshio.read(str(p))[0]
    assert o.name == "quad" and len(o.tri) == 2


def test_bad_files(tmp_path):
    (tmp_path / "x.stl").write_bytes(b"not an stl")
    with pytest.raises(ValueError):
        meshio.read(str(tmp_path / "x.stl"))
    with pytest.raises(ValueError, match="形式"):
        meshio.write(str(tmp_path / "x.ply"), [meshio.MeshObject("m", *box())])


# ---------------------------------------------------------------- 取り込んだメッシュの面


def test_encode_decode():
    P, T = box()
    Q, U = decode(encode(P, T))
    assert np.allclose(P, Q) and (T == U).all()


def test_box_mesh_faces_are_planes():
    from cad.kernel.prism import PrismKernel
    k = PrismKernel()
    s = mesh_shape(*box())
    assert len(s.faces) == 6 and len(s.edge_list) == 12
    assert all(k.face_plane(s, h) is not None for h in k.faces(s))
    assert k.volume(s) == pytest.approx(40 * 20 * 10)


def test_cylinder_mesh_has_axis():
    from cad.kernel.prism import PrismKernel
    k = PrismKernel()
    s = mesh_shape(*cylinder())
    assert len(s.faces) == 3                                         # 底・上・側面（48 枚の帯は 1 つの面）
    side = [h for h in k.faces(s) if k.face_plane(s, h) is None]
    assert len(side) == 1
    (pt, d), r = k.face_axis(s, side[0]), k.face_radius(s, side[0])
    assert r == pytest.approx(5, rel=1e-3) and abs(d[2]) == pytest.approx(1) and pt[:2] == pytest.approx([0, 0], abs=1e-6)


# ---------------------------------------------------------------- API


@pytest.fixture
def api():
    return Api(Workspace("prism"))


def test_open_mesh_as_part_sketch_and_save(api, tmp_path):
    P, T = box()
    st = api.call("doc.open", {"path": write_mesh(tmp_path / "blk.stl", P, T)})
    d = st["doc"]
    assert st["name"] == "blk" and st["path"] is None and st["modified"]
    assert [(t["id"], t["type"], t["state"]) for t in st["tree"]] == [("Import1", "import", "ok")]
    assert st["volume"] == pytest.approx(8000) and len(st["mesh"]["face_names"]) == 6
    top = next(n for n in st["mesh"]["face_names"] if n.startswith("Import1/F"))
    # 面にスケッチは描けるが、形を変える演算はエラー（ツリーは壊れない）
    api.call("sketch.new", {"doc": d, "plane": {"face": top}})
    api.call("sketch.add", {"doc": d, "kind": "rect", "points": [[1, 1], [3, 3]]})
    st = api.call("feature.add", {"doc": d, "type": "extrude", "sketch": "Sketch1", "distance": 5})
    assert st["tree"][-1]["state"] == "error" and "メッシュ" in st["tree"][-1]["message"]
    r = api.call("doc.save", {"doc": d, "path": str(tmp_path / "blk.usda")})
    st2 = Api(Workspace("prism")).call("doc.open", {"path": r["path"]})
    assert st2["tree"][0]["state"] == "ok" and st2["volume"] == pytest.approx(8000)


def test_imported_part_in_assembly_with_mate_and_export(api, tmp_path):
    P, T = cylinder()
    pin = api.call("doc.open", {"path": write_mesh(tmp_path / "pin.stl", P, T)})
    blk = make_part(api, "rect", [[0, 0], [40, 20]], 10, "Block")
    a = api.call("asm.new")["doc"]
    api.call("asm.add", {"doc": a, "part": blk})
    st = api.call("asm.add", {"doc": a, "part": pin["doc"]})
    assert st["parts"][pin["doc"]]["face_names"] == ["Import1/F1", "Import1/F2", "Import1/F3"]
    bottom = "Import1/F2"                       # cylinder() の三角形の順: 側面・底・上
    st = api.call("asm.mate", {"doc": a, "type": "coincident", "a": ["Block<1>", "Extrude1/end"], "b": ["pin<1>", bottom]})
    assert st["solve"]["status"] == "ok"
    r = api.call("doc.export", {"doc": a, "path": str(tmp_path / "asm.3mf")})
    assert r["objects"] == 2
    objs = meshio.read(r["path"])
    assert [o.name for o in objs] == ["Block<1>", "pin<1>"]
    z = np.vstack([o.pos for o in objs])[:, 2]
    assert z.max() == pytest.approx(10 + 12, abs=1e-4) or z.min() == pytest.approx(10 - 12, abs=1e-4)


@pytest.mark.parametrize("ext", ["stl", "3mf", "obj"])
def test_export_part_with_colors(api, tmp_path, ext):
    d = make_part(api, "rect", [[0, 0], [40, 20]], 10, "Block")
    api.call("appearance.set", {"doc": d, "face": "Extrude1/end", "color": "#00ff00"})
    r = api.call("doc.export", {"doc": d, "path": str(tmp_path / f"b.{ext}")})
    assert r["triangles"] >= 12
    o = meshio.read(r["path"])[0]
    assert o.pos.max(0) == pytest.approx([40, 20, 10])
    if ext == "3mf":
        assert "#00ff00" in o.palette and len(o.palette) == 2
    if ext == "obj":
        assert "Kd 0.0000 1.0000 0.0000" in (tmp_path / "b.mtl").read_text()


def test_export_errors(api, tmp_path):
    d = api.call("doc.new")["doc"]
    with pytest.raises(RpcError, match="形状"):
        api.call("doc.export", {"doc": d, "path": str(tmp_path / "e.stl")})
    d = make_part(api, "rect", [[0, 0], [4, 2]], 1, "B")
    with pytest.raises(RpcError, match="形式"):
        api.call("doc.export", {"doc": d, "path": str(tmp_path / "e.step")})
    with pytest.raises(RpcError, match="細かさ"):
        api.call("doc.export", {"doc": d, "path": str(tmp_path / "e.stl"), "quality": "ultra"})


@pytest.mark.skipif("usd_brep" not in kernel_choices(), reason="usd-brep が要る")
def test_export_quality_real_kernel(tmp_path):
    api = Api(Workspace("usd_brep"))
    d = make_part(api, "circle", [[0, 0], [10, 0]], 5, "Disk")
    n = {q: api.call("doc.export", {"doc": d, "path": str(tmp_path / f"{q}.stl"), "quality": q})["triangles"]
         for q in ("coarse", "standard", "fine")}
    assert n["coarse"] <= n["standard"] < n["fine"]
    st = api.call("doc.open", {"path": str(tmp_path / "fine.stl")})
    assert st["volume"] == pytest.approx(math.pi * 100 * 5, rel=2e-3)
    assert len(st["mesh"]["face_names"]) == 3
