import math

import pytest

from cad.doc import ExprError, Variables, evaluate, references
from cad.kernel.mock import MockKernel
from cad.part import ExtrudeFeature, PartDocument, SketchFeature
from cad.sketch import Sketch


def test_evaluate_and_safety():
    assert evaluate("2 * (3 + 4) ** 2 / 7 - 1") == pytest.approx(13)
    assert evaluate("sin(rad(30)) + max(1, 2) + pi") == pytest.approx(0.5 + 2 + math.pi)
    assert evaluate("w / 2 + Sketch1.K5", {"w": 10, "Sketch1.K5": 3}.__getitem__) == 8
    assert references("w / 2 + Sketch1.K5 + sin(a) + pi") == {"w", "Sketch1.K5", "a"}
    for bad in ("__import__('os')", "w.__class__", "[1][0]", "1 if 1 else 2", "open('x')", "1 / 0", "2 +", "x"):
        with pytest.raises(ExprError):
            evaluate(bad, None if bad == "x" else (lambda n: (_ for _ in ()).throw(ExprError(n))))


def test_variables_order_and_cycle():
    v = Variables({"h": "w / 2", "w": "100", "a": "b + 1", "b": "a + 1", "c": "nope * 2"})
    values, errors = v.evaluate()
    assert values == {"w": 100, "h": 50}
    assert "循環" in errors["a"] and "循環" in errors["b"] and "未定義" in errors["c"]


def block():
    doc = PartDocument(MockKernel())
    s = Sketch(); ls = s.rect(0, 0, 10, 5)
    s.constrain("fix", s.entities[ls[0]].p1)
    kw = s.constrain("length", ls[0], value=10); kh = s.constrain("length", ls[1], value=5)
    s1 = doc.add(SketchFeature(sketch=s)); e1 = doc.add(ExtrudeFeature(sketch=s1, distance=4))
    doc.regenerate()
    return doc, s, s1, e1, kw, kh


def test_dimensions_follow_variables_and_other_dimensions():
    doc, s, s1, e1, kw, kh = block()
    with doc.transaction("式"):
        doc.variables.exprs["width"] = "20"
        sk = doc.get(s1).sketch
        sk.constraints[kw].expr = "width"
        sk.constraints[kh].expr = f"{s1}.{kw} / 4"          # 同じスケッチの寸法を参照
        doc.get(e1).exprs["distance"] = f"{s1}.{kh} * 2"    # 上のフィーチャーの寸法を参照
    assert doc.status[e1].state == "ok"
    assert doc.kernel.volume(doc.body) == pytest.approx(20 * 5 * 10)
    doc.kernel.calls.clear()
    with doc.transaction("幅"):
        doc.variables.exprs["width"] = "40"                 # 変数 1 つで全体が追従
    assert doc.kernel.volume(doc.body) == pytest.approx(40 * 10 * 20)
    assert doc.kernel.calls == ["extrude"]
    doc.undo()
    assert doc.kernel.volume(doc.body) == pytest.approx(20 * 5 * 10)
    d2 = PartDocument.from_dict(MockKernel(), doc.to_dict()); d2.regenerate()
    assert d2.kernel.volume(d2.body) == pytest.approx(1000) and d2.var_values == {"width": 20}


def test_expression_errors_are_isolated_to_the_feature():
    doc, s, s1, e1, kw, kh = block()
    with doc.transaction("bad"):
        doc.get(e1).exprs["distance"] = "undefined_var * 2"
    assert doc.status[e1].state == "error" and "式エラー" in doc.status[e1].message
    assert doc.status[s1].state == "ok"
    with doc.transaction("forward ref"):                    # 下のフィーチャーの寸法は参照できない
        doc.get(e1).exprs.clear()
        doc.get(s1).sketch.constraints[kw].expr = f"{e1}.distance"
    assert doc.status[s1].state == "error" and doc.status[e1].state == "error"
    with doc.transaction("fix"):
        doc.get(s1).sketch.constraints[kw].expr = None
    assert doc.status[e1].state == "ok"


def test_mate_value_from_expression():
    from cad.assembly import Assembly, GPlane, StaticSource
    src = StaticSource({"top": GPlane((0, 0, 0), (0, 0, 1)), "bottom": GPlane((0, 0, 0), (0, 0, -1))})
    a = Assembly({"p": src})
    A = a.add_component("p"); B = a.add_component("p", position=(0, 0, 5))
    m = a.mate("distance", (A, "top"), (B, "bottom"), value=5)
    a.variables.exprs["gap"] = "3"
    a.mates[m].expr = "gap * 2"
    r = a.solve()
    assert r.status == "ok" and a.components[B].position[2] == pytest.approx(6) and r.expr_errors == {}
    a.mates[m].expr = "nope"
    r = a.solve()
    assert m in r.expr_errors and a.components[B].position[2] == pytest.approx(6)   # 値は前回のまま
