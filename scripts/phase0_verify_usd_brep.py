"""フェーズ0: usd-brep が本プロジェクトのカーネル要件を満たすか検証する。

実行（Python 3.12 の venv で）:
    pip install usd-brep
    python scripts/phase0_verify_usd_brep.py > phase0_report.txt

usd-brep の詳細 API は公開情報が少ないため、このスクリプトは
(1) 各サブモジュールの公開関数を一覧化し、(2) 要件ごとに候補 API を探して試す。
出力レポートを読めば、kernel/usd_brep.py アダプタを書けるかどうか判断できる。

要件（docs/SPEC.md「カーネル要件」）
  R1 平面プロファイル(線/円弧/円)からの押し出し・回転
  R2 ブーリアン（和・差・積）
  R3 エッジ指定フィレット
  R4 面・エッジの列挙と、エッジ→隣接2面の対応
  R5 履歴: 演算結果の面が入力のどの要素から生成/変形されたか   ← 最重要
  R6 メッシュ化で「三角形 → 面」の対応が取れる
  R7 面が平面かどうか・その平面の取得
  R8 速度（ブーリアン/フィレットを繰り返したときの時間）
"""
from __future__ import annotations

import importlib
import inspect
import time
import traceback

KEYWORDS = {
    "R1": ["extrude", "prism", "revolve", "sweep", "profile", "wire", "polygon"],
    "R2": ["union", "subtract", "intersect", "fuse", "cut", "common"],
    "R3": ["fillet", "chamfer"],
    "R4": ["face", "edge", "adjacent", "topology", "shell", "loop"],
    "R5": ["history", "generated", "modified", "deleted", "naming", "origin", "track", "provenance"],
    "R6": ["tessellat", "mesh", "triangul"],
    "R7": ["plane", "normal", "surface_type", "planar"],
}


def section(t):
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


def main():
    try:
        ub = importlib.import_module("usd_brep")
    except Exception:
        print("usd_brep を import できません"); traceback.print_exc(); return
    section("バージョン / サブモジュール")
    print("version:", getattr(ub, "__version__", "?"))
    subs = {}
    for name in dir(ub):
        obj = getattr(ub, name)
        if inspect.ismodule(obj):
            subs[name] = obj
    for name, mod in sorted(subs.items()):
        pub = [n for n in dir(mod) if not n.startswith("_")]
        print(f"\n[{name}] {len(pub)} items")
        for n in pub:
            o = getattr(mod, n)
            try:
                sig = str(inspect.signature(o)) if callable(o) else ""
            except (TypeError, ValueError):
                sig = "(...)"
            doc = (inspect.getdoc(o) or "").split("\n")[0][:90]
            print(f"  {n}{sig}  # {doc}")

    section("要件ごとの候補 API")
    for req, kws in KEYWORDS.items():
        hits = []
        for mname, mod in subs.items():
            for n in dir(mod):
                if any(k in n.lower() for k in kws):
                    hits.append(f"{mname}.{n}")
        print(f"{req}: {', '.join(hits) if hits else '（候補なし）'}")

    section("基本操作の試行")
    try:
        box = ub.primitives.create_box((0, 0, 0), 10, 5, 4)
        cyl = None
        for cand in ("create_cylinder", "cylinder"):
            if hasattr(ub.primitives, cand):
                try:
                    cyl = getattr(ub.primitives, cand)((5, 2.5, 0), 1.0, 4.0)
                except TypeError as e:
                    print(f"{cand} 呼び出し失敗: {e}")
        print("box type:", type(box).__name__, [n for n in dir(box) if not n.startswith("_")][:60])
        t = time.perf_counter()
        tool = cyl or ub.primitives.create_sphere((5, 2.5, 2), 1.5)
        cut = ub.booleans.subtract(box, tool)
        print(f"subtract: {1000 * (time.perf_counter() - t):.1f} ms")
        print("cut type:", type(cut).__name__)
        for attr in ("faces", "edges", "num_faces", "face_count", "history", "topology"):
            if hasattr(cut, attr):
                v = getattr(cut, attr)
                v = v() if callable(v) else v
                print(f"cut.{attr}:", type(v).__name__, (len(v) if hasattr(v, "__len__") else v))
        # R8: 繰り返し
        t = time.perf_counter()
        b = box
        for i in range(20):
            s = ub.primitives.create_sphere((i * 0.5, 2.5, 4), 0.4)
            b = ub.booleans.subtract(b, s)
        print(f"20回連続 subtract: {1000 * (time.perf_counter() - t):.1f} ms")
        ub.usd.export_brep(b, "phase0_out.usda")
        print("export_brep → phase0_out.usda OK")
        section("書き出した USD の先頭（スキーマ確認用）")
        with open("phase0_out.usda", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= 120:
                    break
                print(line.rstrip()[:200])
    except Exception:
        traceback.print_exc()

    section("判定のヒント")
    print("R5(履歴) と R6(三角形→面) の候補が無い場合はカーネルを OCCT にする（docs/DECISIONS.md D3）")


if __name__ == "__main__":
    main()
