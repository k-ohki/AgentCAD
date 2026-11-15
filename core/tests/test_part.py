import pytest

from cad.kernel.mock import MockKernel
from cad.naming import edge_name
from cad.part import ExtrudeFeature, FilletFeature, PartDocument, RevolveFeature, SketchFeature
from cad.sketch import Sketch


def rect_sketch(w=10, h=5):
    s = Sketch()
    ls = s.rect(0, 0, w, h)
    s.constrain("fix", s.entities[ls[0]].p1)
    s.constrain("length", ls[0], value=w)
    s.constrain("length", ls[1], value=h)
    return s, ls


def make_block():
    doc = PartDocument(MockKernel())
    sk, ls = rect_sketch()
    s1 = doc.add(SketchFeature(sketch=sk))
    e1 = doc.add(ExtrudeFeature(sketch=s1, distance=4))
    doc.regenerate()
    return doc, s1, e1, ls


def test_extrude_volume_and_names():
    doc, s1, e1, ls = make_block()
    assert doc.status[s1].state == "ok" and doc.status[e1].state == "ok"
    assert doc.kernel.volume(doc.body) == pytest.approx(200)
    assert f"{e1}/end" in doc.names.faces
    assert f"{e1}/side/{ls[0]}" in doc.names.faces


def test_incremental_regen_only_rebuilds_downstream():
    doc, s1, e1, ls = make_block()
    sk2 = Sketch(); sk2.circle((5, 2.5), 1)
    s2 = doc.add(SketchFeature(sketch=sk2))
    e2 = doc.add(ExtrudeFeature(sketch=s2, distance=4, op="cut"))
    doc.regenerate()
    doc.kernel.calls.clear()
    doc.regenerate()
    assert doc.kernel.calls == []                     # 変更なし → 再計算なし
    with doc.transaction("cut depth"):
        doc.get(e2).distance = 2
    assert doc.status[e1].rebuilt is False
    assert doc.status[e2].rebuilt is True
    assert doc.kernel.calls == ["extrude", "cut"]


def test_dimension_change_propagates():
    doc, s1, e1, ls = make_block()
    sk = doc.get(s1).sketch
    k = next(k for k, c in sk.constraints.items() if c.type == "length" and c.refs == [ls[0]])
    with doc.transaction("width"):
        sk.constraints[k].value = 20
    assert doc.kernel.volume(doc.body) == pytest.approx(400)


def test_fillet_reference_survives_upstream_change():
    doc, s1, e1, ls = make_block()
    edge = edge_name(f"{e1}/end", f"{e1}/side/{ls[0]}")
    f1 = doc.add(FilletFeature(edges=[edge], radius=1))
    doc.regenerate()
    assert doc.status[f1].state == "ok"
    with doc.transaction("height"):
        doc.get(e1).distance = 8
    assert doc.status[f1].state == "ok" and doc.status[f1].rebuilt
    assert any(n.startswith(f"{f1}/fillet/") for n in doc.names.faces)


def test_error_isolation_and_suppress():
    doc, s1, e1, ls = make_block()
    f1 = doc.add(FilletFeature(edges=["E[nope|nope]"], radius=1))
    sk2 = Sketch(); sk2.rect(20, 0, 25, 5)
    s2 = doc.add(SketchFeature(sketch=sk2))
    e2 = doc.add(ExtrudeFeature(sketch=s2, distance=1))
    doc.regenerate()
    assert doc.status[f1].state == "error"
    assert doc.status[e2].state in ("ok", "warning")    # 後続は続行
    assert doc.kernel.volume(doc.body) == pytest.approx(225)
    with doc.transaction("suppress"):
        doc.get(e1).suppressed = True
    assert doc.status[e1].state == "suppressed"
    assert doc.kernel.volume(doc.body) == pytest.approx(25)


def test_rollback_and_insert():
    doc, s1, e1, ls = make_block()
    doc.set_rollback(1)                     # Sketch1 の直後
    doc.regenerate()
    assert doc.status[e1].state == "rolled_back" and doc.body is None
    fid = doc.add(SketchFeature(sketch=Sketch()))
    assert [f.id for f in doc.features] == [s1, fid, e1]
    doc.set_rollback(None); doc.regenerate()
    assert doc.status[e1].state == "ok"


def test_undo_redo_and_serialization():
    doc, s1, e1, ls = make_block()
    with doc.transaction("depth"):
        doc.get(e1).distance = 10
    assert doc.kernel.volume(doc.body) == pytest.approx(500)
    assert doc.undo() == "depth"
    assert doc.kernel.volume(doc.body) == pytest.approx(200)
    doc.redo()
    assert doc.kernel.volume(doc.body) == pytest.approx(500)
    d2 = PartDocument.from_dict(MockKernel(), doc.to_dict())
    d2.regenerate()
    assert d2.kernel.volume(d2.body) == pytest.approx(500)


def test_sketch_on_face_and_revolve():
    doc, s1, e1, ls = make_block()
    sk = Sketch(); sk.circle((5, 2.5), 1)
    s2 = doc.add(SketchFeature(plane={"face": f"{e1}/end"}, sketch=sk))
    e2 = doc.add(ExtrudeFeature(sketch=s2, distance=3))
    doc.regenerate()
    assert doc.status[e2].state != "error"
    sk3 = Sketch(); sk3.rect(1, 0, 2, 3)
    ax = sk3.line((0, 0), (0, 5), construction=True)
    s3 = doc.add(SketchFeature(plane={"plane": "Right"}, sketch=sk3))
    r = doc.add(RevolveFeature(sketch=s3, axis_line=ax, angle=360))
    doc.regenerate()
    assert doc.status[r].state == "ok"


def test_reference_resolution_repairs_or_reports_candidates():
    from cad.naming import DanglingReference, TopoNames
    t = TopoNames(faces={"E1/end": 1, "E1/side/L1": 2, "E1/side/L1#2": 3, "E1/side/L2#2": 4})
    assert t.find_face("E1/end").handle == 1 and not t.find_face("E1/end").repaired
    r = t.find_face("E1/side/L2")                 # 分割が解消して #2 だけ残った → 1 候補なので救済
    assert r.handle == 4 and r.repaired and r.name == "E1/side/L2#2"
    with pytest.raises(DanglingReference) as e:
        t.find_face("E1/side/L1#3")               # 候補が複数 → 選べないので候補を提示
    assert e.value.candidates == ["E1/side/L1", "E1/side/L1#2"]
    with pytest.raises(DanglingReference):
        t.find_face("nope")


def test_dangling_edge_reports_error_with_message():
    doc, s1, e1, ls = make_block()
    f1 = doc.add(FilletFeature(edges=["E[nope|nope]"], radius=1))
    doc.regenerate()
    assert doc.status[f1].state == "error" and "見つかりません" in doc.status[f1].message


def test_kernel_failure_in_boolean_is_a_feature_error():
    """押し出し後の合成（ブーリアン）でカーネルが失敗しても、ツリー全体は止まらない。"""
    from cad.kernel.base import KernelError
    doc, s1, e1, ls = make_block()

    def boom(*a, **k):
        raise KernelError("合成に失敗")
    doc.kernel.boolean = boom
    sk = Sketch(); sk.circle((5, 2.5), 1)
    s2 = doc.add(SketchFeature(sketch=sk)); e2 = doc.add(ExtrudeFeature(sketch=s2, distance=1))
    doc.regenerate()
    assert doc.status[e2].state == "error" and "合成に失敗" in doc.status[e2].message
    assert doc.kernel.volume(doc.body) == pytest.approx(200)
