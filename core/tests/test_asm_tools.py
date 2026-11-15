"""アセンブリの干渉チェック・複製・パターン（D56）。"""
import numpy as np
import pytest

from cad.assembly.interference import interference
from tests.test_asm_api import comp, setup  # noqa: F401  （fixture を使い回す）


def box(lo, hi):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    V = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    F = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                  [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    return V, F


def test_interference_boxes():
    A, B, C = box([0, 0, 0], [10, 10, 10]), box([8, 2, 2], [20, 8, 8]), box([10, 0, 0], [20, 10, 10])
    r = interference([("A", *A), ("B", *B), ("C", *C)])
    pairs = {(x["a"], x["b"]): x for x in r}
    assert set(pairs) == {("A", "B"), ("B", "C")}                     # A と C は面で接しているだけ
    assert pairs[("A", "B")]["volume"] == pytest.approx(2 * 6 * 6, rel=0.08)
    lo, hi = pairs[("A", "B")]["bbox"]
    assert lo[0] == pytest.approx(8, abs=0.3) and hi[0] == pytest.approx(10, abs=0.3)


def test_asm_interference_and_copy(setup):
    api, a, *_ = setup
    api.call("asm.drag", {"doc": a, "comp": "Pin<1>", "position": [100, 0, 0], "final": True})
    r = api.call("asm.interference", {"doc": a})
    assert r["checked"] == 2 and r["interferences"] == []
    # ピンをブロックに半分刺す（ピンの軸を z、ブロック上面 z=10 から 5 下げる）
    api.call("asm.drag", {"doc": a, "comp": "Pin<1>", "position": [20, 10, 5], "final": True})
    r = api.call("asm.interference", {"doc": a})["interferences"]
    assert len(r) == 1 and {r[0]["a"], r[0]["b"]} == {"Block<1>", "Pin<1>"}
    assert r[0]["volume"] == pytest.approx(np.pi * 25 * 5, rel=0.15)
    st = api.call("asm.copy", {"doc": a, "comp": "Pin<1>", "offset": [0, 0, 30]})
    c = comp(st, st["created"][0])
    assert c["id"] == "Pin<2>" and c["position"] == pytest.approx([20, 10, 35]) and not c["fixed"]


def test_linear_and_circular_pattern(setup):
    api, a, *_ = setup
    pin0 = comp(api.call("doc.state", {"doc": a}), "Pin<1>")["position"]
    st = api.call("asm.pattern", {"doc": a, "comp": "Pin<1>", "kind": "linear", "axis": "y", "count": 3, "spacing": 25})
    assert st["created"] == ["Pin<2>", "Pin<3>"]
    assert comp(st, "Pin<3>")["position"] == pytest.approx(np.add(pin0, [0, 50, 0]))
    assert sum(m["type"] == "lock" for m in st["mates"]) == 2 and st["solve"]["status"] == "ok"
    # 種を動かすとついてくる
    st = api.call("asm.drag", {"doc": a, "comp": "Pin<1>", "position": np.add(pin0, [5, 0, 0]).tolist(), "final": True})
    p1 = comp(st, "Pin<1>")["position"]
    assert p1 != pytest.approx(pin0)
    assert np.linalg.norm(np.subtract(comp(st, "Pin<3>")["position"], p1)) == pytest.approx(50, abs=1e-6)   # 形を保ってついてくる
    # 円形: z 軸まわりに 4 個（90° ずつ）。ブロックの円筒ではなく座標軸で
    st = api.call("asm.pattern", {"doc": a, "comp": "Pin<1>", "kind": "circular", "axis": "z", "count": 4})
    p1 = comp(st, "Pin<1>")["position"]
    q = comp(st, st["created"][0])["position"]
    assert q == pytest.approx([-p1[1], p1[0], p1[2]], abs=1e-6)
    # 円形パターンの軸に、ピンの円筒の面を使う
    st = api.call("asm.pattern", {"doc": a, "comp": "Block<1>", "kind": "circular", "ref": ["Pin<1>", "Extrude1/side/C2"],
                                  "count": 2})
    assert len(st["created"]) == 1
    with pytest.raises(Exception):
        api.call("asm.pattern", {"doc": a, "comp": "Pin<1>", "kind": "circular", "ref": ["Block<1>", "Extrude1/end"]})
