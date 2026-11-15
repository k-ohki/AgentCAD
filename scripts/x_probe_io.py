"""ファイルの入出力（STEP・STL など）の調査。結果を x_probe_report.txt に書く。

    python scripts\\x_probe_io.py

usd-brep で外部の形式を読み書きできるか、自前で書き出すときに必要なデータが取れるかを確かめる:
  - ファイルの入出力の関数（io・usd モジュール）の一覧。STEP / IGES / STL などの関数があるか
  - 曲面・曲線の定義（NURBS の制御点・ノット）を取り出せるか（STEP を自前で書くのに要る）
  - tessellate の三角形メッシュ（STL・3MF・OBJ・glTF の書き出しに使う）
  - USD のメッシュの書き出し・読み込み（export_mesh / import_mesh）が往復するか
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(HERE, "x_probe_report.txt")


def main():
    import usd_brep as ub

    out = []
    p = out.append

    p("== 入出力の関数")
    for mod in ("io", "usd"):
        m = getattr(ub, mod)
        p(f"{mod}: {[n for n in dir(m) if not n.startswith('_')]}")
    keys = ("step", "stp", "iges", "igs", "stl", "obj", "3mf", "gltf", "export", "import", "read", "write")
    p("名前に入出力の語を含む関数: " + str([n for n in dir(ub) if any(k in n.lower() for k in keys)]))

    p("\n== 曲面・曲線から取り出せるもの（NURBS の制御点・ノットがあるか）")
    box = ub.create_box((0.0, 0.0, 0.0), 10.0, 20.0, 30.0)
    cyl = ub.create_cylinder((0.0, 0.0, 0.0), 5.0, 10.0)
    for name, b in (("box", box), ("cylinder", cyl)):
        nb = ub.turn_to_nurbs(b.copy())
        f = nb.faces()[0]
        s = f.surface()
        e = nb.edges()[0]
        c = e.curve()
        p(f"{name}: Face={_api(f)}")
        p(f"{name}: Surface={type(s).__name__} {_api(s)}")
        p(f"{name}: Curve={type(c).__name__} {_api(c)}")
        has = [n for n in _api(s) + _api(c) if any(k in n.lower() for k in ("control", "knot", "weight", "degree", "pole"))]
        p(f"{name}: 制御点・ノットを取り出す関数: {has or 'なし'}")

    p("\n== tessellate（三角形メッシュ）")
    m = ub.tessellate(cyl.copy())
    p(f"tessellate の戻り値: {type(m).__name__} {[(type(x).__name__, getattr(x, 'shape', None)) for x in (m if isinstance(m, (tuple, list)) else [m])]}")

    p("\n== USD のメッシュの往復")
    with tempfile.TemporaryDirectory() as d:
        try:
            pb = m if isinstance(m, ub.PolyBrep) else None
            p(f"tessellate が PolyBrep を返すか: {pb is not None}  PolyBrep={_api(ub.PolyBrep)}")
            if pb is not None:
                path = os.path.join(d, "m.usda")
                ub.usd.export_mesh(pb, path)
                back = ub.usd.import_mesh(path, "/World/Mesh0")
                p(f"往復: {type(back).__name__} ok")
        except Exception as ex:  # 調査なので何が起きたかを記録して続ける
            p(f"失敗: {ex!r}")

    text = "\n".join(out)
    with open(REPORT, "w", encoding="utf-8") as fp:
        fp.write(text + "\n")
    print(text)
    print(f"\n→ {REPORT}")


def _api(obj):
    return [n for n in dir(obj) if not n.startswith("_")]


if __name__ == "__main__":
    sys.exit(main())
