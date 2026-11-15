"""カーネルの追加調査（利用者の PC で実行）。結果を k_probe_report.txt に書く。

    python scripts\\k_probe_more_ops.py

面取り・シェル・抜き勾配・スイープ・ロフト・断面・質量特性など、まだ使っていない usd-brep の関数の
名前と説明（引数）を書き出すだけ。形状は作らないので、プロセスが落ちる心配はない。
このレポートがあれば、面取り・シェルなどのフィーチャーを足せる。
"""
import importlib.metadata
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(HERE, "k_probe_report.txt")
KEYS = ("chamfer", "shell", "offset", "draft", "taper", "sweep", "skin", "loft", "mirror", "scale", "transform",
        "plane", "section", "mass", "area", "centroid", "center", "inertia", "distance", "relationship", "hole",
        "thicken", "blend", "fillet", "step", "iges", "import", "export", "heal", "split", "imprint")

import usd_brep as ub  # noqa: E402

with open(REPORT, "w", encoding="utf-8") as f:
    def out(*a):
        f.write(" ".join(str(x) for x in a) + "\n"); f.flush()
    out("python", sys.version.replace("\n", " "))
    try:
        out("usd-brep", importlib.metadata.version("usd-brep"))
    except Exception as e:                      # noqa: BLE001
        out("usd-brep ?", e)
    names = sorted(n for n in dir(ub) if not n.startswith("_"))
    out("\n== usd_brep の公開名（全部）\n" + ", ".join(names))
    out("\n== 関係しそうな関数の説明")
    for n in names:
        if not any(k in n.lower() for k in KEYS):
            continue
        obj = getattr(ub, n)
        if isinstance(obj, type(sys)):          # サブモジュール
            out(f"\n-- module {n}: " + ", ".join(sorted(x for x in dir(obj) if not x.startswith('_'))))
            continue
        doc = (getattr(obj, "__doc__", "") or "").strip().splitlines()
        out(f"\n- {n}")
        for line in doc[:40]:
            out("    " + line)
    for cls in ("Brep", "Face", "Edge", "Surface", "Curve"):
        c = getattr(ub, cls, None)
        if c is not None:
            out(f"\n== {cls} のメソッド\n" + ", ".join(sorted(x for x in dir(c) if not x.startswith("_"))))
    out("\n完了")
print("書き出しました:", REPORT)
