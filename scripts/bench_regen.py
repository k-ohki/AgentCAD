"""再生成の速さの計測（D65）。実カーネル（usd-brep）が要る。

    cd core
    python ../scripts/bench_regen.py            # 合計と内訳
    python ../scripts/bench_regen.py --profile  # cProfile の上位も出す
    python ../scripts/bench_regen.py --names out.json   # 車・板の面とエッジの名前を書き出す（高速化の前後で比べる）

計るもの:
- car: 車のデモを API の呼び出しで作る（スケッチ・フィーチャーの追加ごとに再生成と状態の返却）
- car_regen: できた車の全フィーチャーを作り直す（変数 width の変更 = 全体の再生成）
- plate: 板のデモ
内訳は、カーネルの呼び出し（usd_brep の関数）と、面の対応づけ（meshtopo の関数）ごとの合計時間。
"""
from __future__ import annotations

import cProfile
import io
import os
import pstats
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
from cad.api.rpc import Api  # noqa: E402
from cad.doc.workspace import Workspace  # noqa: E402
from cad.kernel import available_kernels  # noqa: E402


def cases(kernel: str):
    def car():
        Api(Workspace(kernel)).call("doc.demo", {"kind": "car"})

    api = Api(Workspace(kernel))
    d = api.call("doc.demo", {"kind": "car"})["doc"]
    w = [80]

    def car_regen():
        w[0] = 170 - w[0]                                  # 80 ↔ 90（車幅が変わると全フィーチャーが作り直しになる）
        api.call("variable.set", {"doc": d, "name": "width", "expr": str(w[0])})

    def plate():
        Api(Workspace(kernel)).call("doc.demo", {"kind": "plate"})
    return {"car": car, "car_regen": car_regen, "plate": plate}


def breakdown(stats: pstats.Stats, n: int) -> list[tuple[str, float]]:
    """自分の時間（tottime）を、usd_brep の組み込み関数と meshtopo・naming の関数ごとに集める。"""
    out = {}
    for (fn, line, name), (cc, nc, tt, ct, callers) in stats.stats.items():
        if "usd_brep" in name or "_omni_solid" in fn or "method 'usd_brep" in name:
            key = f"kernel:{name}"
        elif fn.endswith(("meshtopo.py", "toponame.py")):
            key = f"{os.path.basename(fn)}:{name}"
        else:
            continue
        out[key] = out.get(key, 0.0) + tt
    return sorted(out.items(), key=lambda x: -x[1])[:n]


def names(kernel: str, path: str):
    """車（width 80 と 90）と板の、面の名前・エッジの名前・体積を JSON に書く。"""
    import json
    out = {}
    api = Api(Workspace(kernel))
    for key, params in (("plate", {"kind": "plate"}), ("car", {"kind": "car"})):
        st = api.call("doc.demo", params)
        doc = api.ws.get(st["doc"])
        out[key] = {"faces": sorted(st["mesh"]["face_names"]), "edges": sorted(doc.names.edges), "volume": round(st["volume"], 6)}
        if key == "car":
            st = api.call("variable.set", {"doc": st["doc"], "name": "width", "expr": "90"})
            out["car90"] = {"faces": sorted(st["mesh"]["face_names"]), "edges": sorted(doc.names.edges),
                            "volume": round(st["volume"], 6)}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("書き出し:", path, {k: (len(v["faces"]), len(v["edges"])) for k, v in out.items()})


def main():
    real = [k for k, c in available_kernels().items() if c.geometric and k != "prism"]
    if not real:
        print("実カーネル（usd-brep）がありません")
        return
    kernel = real[0]
    if "--names" in sys.argv:
        names(kernel, sys.argv[sys.argv.index("--names") + 1])
        return
    prof = "--profile" in sys.argv
    for name, fn in cases(kernel).items():
        fn()                                             # 一度目（import やキャッシュ）を除く
        times = []
        pr = cProfile.Profile()
        for _ in range(3):
            t = time.perf_counter()
            if prof:
                pr.enable()
            fn()
            if prof:
                pr.disable()
            times.append(time.perf_counter() - t)
        print(f"{name:10s} 最小 {min(times):6.3f} s  （3 回: {', '.join(f'{x:.3f}' for x in times)}）")
        if prof:
            st = pstats.Stats(pr)
            for k, v in breakdown(st, 12):
                print(f"    {v / 3:7.3f} s  {k}")
            s = io.StringIO()
            pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(15)
            print("\n".join(s.getvalue().splitlines()[-22:]))


if __name__ == "__main__":
    main()
