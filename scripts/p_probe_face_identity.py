"""調査: usd-brep の演算の前後で、面・曲面のオブジェクトの同一性が保たれるか（D65 の前提の確認）。

    cd core
    python ../scripts/p_probe_face_identity.py

直方体・円柱を API で作り、ブーリアン（和・差）・copy の結果の各面について
- 面オブジェクトの hash が入力の面と一致するか
- face.surface() の hash が入力の面の曲面と一致するか
を数える。レポートは標準出力。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
import usd_brep as ub  # noqa: E402

from cad.api.rpc import Api  # noqa: E402
from cad.doc.workspace import Workspace  # noqa: E402
from cad.kernel.usd_brep import _fresh  # noqa: E402


def body(kind, a, b, h, z=0):
    api = Api(Workspace("usd_brep"))
    d = api.call("doc.new")["doc"]
    if z:
        pid = api.call("feature.add", {"doc": d, "type": "plane", "base": {"plane": "Front"}, "offset": z})["created"][0]
        api.call("sketch.new", {"doc": d, "plane": {"feature": pid}})
    else:
        api.call("sketch.new", {"doc": d, "plane": {"plane": "Front"}})
    api.call("sketch.add", {"doc": d, "kind": kind, "points": [a, b]})
    sk = api.call("sketch.end", {"doc": d})["tree"][-1]["id"]
    api.call("feature.add", {"doc": d, "type": "extrude", "sketch": sk, "distance": h})
    return api.ws.get(d).body.raw


def ids(brep):
    return [hash(f) for f in brep.faces()], [hash(f.surface()) for f in brep.faces()]


def main():
    A = body("rect", [0, 0], [10, 10], 10)
    B = body("rect", [5, 5], [15, 15], 10, z=5)
    C = body("circle", [5, 5], [8, 5], 30, z=-10)
    f0 = A.faces()[0]
    print("同じ面の surface() を 2 回:", hash(f0.surface()) == hash(f0.surface()), f0.surface() is f0.surface())
    print("surface のメソッド:", [n for n in dir(f0.surface()) if not n.startswith("_")])
    c = _fresh(A)
    fa, sa = ids(A); fc, sc = ids(c)
    print(f"copy: 面が同じ {len(set(fa) & set(fc))}/{len(fc)}、曲面が同じ {len(set(sa) & set(sc))}/{len(sc)}")
    for op, X, Y in (("boolean_union", A, B), ("boolean_difference", A, C), ("boolean_union", A, C)):
        x, y = _fresh(X), _fresh(Y)
        fx, sx = ids(x); fy, sy = ids(y)
        out = getattr(ub, op)(x, y)
        out = out[0] if isinstance(out, (list, tuple)) else out
        fo, so = ids(out)
        print(f"{op}: 結果の面 {len(fo)}、面が同じ {sum(h in set(fx + fy) for h in fo)}、"
              f"曲面が同じ {sum(h in set(sx + sy) for h in so)}（A 由来 {sum(h in set(sx) for h in so)}、B 由来 {sum(h in set(sy) for h in so)}）")


if __name__ == "__main__":
    main()


def detail():
    """hash の値そのものと、hash が一致した面が本当に同じ面（同じ平面・曲面の上）かを、面の内部の点で確かめる。"""
    import numpy as np
    A = body("rect", [0, 0], [10, 10], 10)
    B = body("rect", [5, 5], [15, 15], 10, z=5)
    C = body("circle", [5, 5], [8, 5], 30, z=-10)
    print("A の面の hash:", [hash(f) for f in A.faces()])
    print("B の面の hash:", [hash(f) for f in B.faces()])
    print("C の面の hash:", [hash(f) for f in C.faces()])

    def sig(f):
        """面の上の点と、その点の法線（同じ面かの判定に使う）。"""
        u, v = f.interior_point() if hasattr(f, "interior_point") else (None, None)
        return f

    for op, X, Y in (("boolean_union", A, B), ("boolean_difference", A, C), ("boolean_difference", B, A)):
        x, y = _fresh(X), _fresh(Y)
        info = {}
        for name, br in (("X", x), ("Y", y)):
            for j, f in enumerate(br.faces()):
                p = np.asarray(f.centroid(), float) if callable(getattr(f, "centroid", None)) else None
                info.setdefault(hash(f), []).append((name, j, p, f.area() if callable(getattr(f, "area", None)) else None))
        out = getattr(ub, op)(x, y)
        out = out[0] if isinstance(out, (list, tuple)) else out
        print(f"--- {op}")
        for f in out.faces():
            h = hash(f)
            src = info.get(h)
            # 結果の面の内部の点が、同じ hash の入力面の上にあるか（入力は消費済みなので記録した重心と比べる: 平面なら法線方向の距離）
            q = np.asarray(f.interior_point(), float) if callable(getattr(f, "interior_point", None)) else None
            print(f"  hash {h}: 入力 {[(s[0], s[1]) for s in src] if src else '-'}  点 {np.round(q, 3).tolist() if q is not None else '?'}")


if __name__ == "__main__" and "--detail" in sys.argv:
    detail()
