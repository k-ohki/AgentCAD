"""スイープの調査（利用者の PC で実行）。結果を w_probe_report.txt に書く。

    python scripts\\w_probe_sweep.py

usd-brep の sweep_along_planar_path（平面の経路に沿ったスイープ）が、一般的な 3D CAD のスイープ
（断面のスケッチ + 経路のスケッチ）に使えるかを確かめる:
  - 体積が理論値どおりか（円の断面を 直線 → 円弧 → 直線 の経路に沿って）
  - move_profile の意味（断面が経路の始点からずれているとき、断面は動くか）
  - 断面が経路に垂直でないとき・閉じた経路・穴のある断面
  - 面の数と、平らな面（端の蓋）がどこにあるか（面の名前付けに使う）
1 ケースずつ別のプロセスで動かすので、どれかで落ちても残りは続く。
"""
import json
import math
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(HERE, "w_probe_report.txt")


def run_case(name):
    import numpy as np
    import usd_brep as ub

    def line(a, b):
        return ub.create_line_segment(tuple(map(float, a)), tuple(map(float, b)))

    def arc_xy(c, r, a0, sweep):
        """XY 平面の円弧（中心 c, 半径 r, 開始角 a0 から反時計回りに sweep 度）。"""
        a = ub.create_arc((0.0, 0.0, 0.0), float(r), 0.0, float(sweep))
        if a0:
            a = ub.rotate(a, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), float(a0))
        return ub.translate(a, (float(c[0]), float(c[1]), float(c[2])))

    def circle_yz(c, r):
        """YZ 平面（法線 +X）の円。"""
        ci = ub.create_circle((0.0, 0.0, 0.0), float(r))
        ci = ub.rotate(ci, (0.0, 0.0, 0.0), (0.0, 1.0, 0.0), 90.0)
        return ub.translate(ci, tuple(map(float, c)))

    def rect_yz(c, w, h):
        """YZ 平面の長方形（中心 c、Y 方向 w、Z 方向 h）。"""
        x, y, z = c
        P = [(x, y - w / 2, z - h / 2), (x, y + w / 2, z - h / 2), (x, y + w / 2, z + h / 2), (x, y - w / 2, z + h / 2)]
        return [line(P[i], P[(i + 1) % 4]) for i in range(4)]

    def L_path():
        # 直線 (0,0,0)→(20,0,0)、円弧（中心 (20,10,0)、半径 10、-90°→0°）→ (30,10,0)、直線 → (30,25,0)
        return [line((0, 0, 0), (20, 0, 0)), arc_xy((20, 10, 0), 10, -90, 90), line((30, 10, 0), (30, 25, 0))]

    L_len = 20 + 15 + 10 * math.pi / 2
    out = {"case": name}

    def info(b):
        d = {"manifold": bool(ub.is_manifold_solid(b)), "volume": float(ub.compute_volume(b))}
        try:
            lo, hi = ub.brep_bounding_box(b)
            d["bbox"] = [list(map(float, lo)), list(map(float, hi))]
        except Exception as e:  # noqa: BLE001
            d["bbox_error"] = repr(e)
        try:
            fs = b.faces()
            d["faces"] = len(fs)
        except Exception as e:  # noqa: BLE001
            d["faces_error"] = repr(e)
        try:
            m = ub.tessellate(b)
            d["tessellate"] = type(m).__name__
        except Exception as e:  # noqa: BLE001
            d["tessellate_error"] = repr(e)
        return d

    if name == "circle_L":
        out["expect_volume"] = math.pi * 4 * L_len
        for mp in (True, False):
            b = ub.sweep_along_planar_path([circle_yz((0, 0, 0), 2)], L_path(), move_profile=mp)
            out[f"move_profile={mp}"] = info(b)
    elif name == "circle_offset":
        # 断面が経路の始点から (0, 3, 0) ずれている。move_profile で動くか
        for mp in (True, False):
            b = ub.sweep_along_planar_path([circle_yz((0, 3, 0), 2)], L_path(), move_profile=mp)
            out[f"move_profile={mp}"] = info(b)
    elif name == "rect_L":
        out["expect_volume"] = 4 * 2 * L_len
        b = ub.sweep_along_planar_path(rect_yz((0, 0, 0), 4, 2), L_path(), move_profile=False)
        out["result"] = info(b)
    elif name == "ring_hole":
        # 穴のある断面（外 r=3、内 r=2）→ 管
        out["expect_volume"] = math.pi * (9 - 4) * L_len
        b = ub.sweep_along_planar_path([circle_yz((0, 0, 0), 3), circle_yz((0, 0, 0), 2)], L_path(), move_profile=False)
        out["result"] = info(b)
    elif name == "tilted_profile":
        # 断面が経路に垂直でない（XZ 平面の円 = 経路の始点の向き +X を含む面）
        ci = ub.create_circle((0.0, 0.0, 0.0), 2.0)
        ci = ub.rotate(ci, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 90.0)
        b = ub.sweep_along_planar_path([ci], L_path(), move_profile=False)
        out["result"] = info(b)
    elif name == "closed_path":
        # 閉じた経路（40×30 の長方形）に円の断面 → 輪
        P = [(0, 0, 0), (40, 0, 0), (40, 30, 0), (0, 30, 0)]
        path = [line(P[i], P[(i + 1) % 4]) for i in range(4)]
        out["expect_volume_approx"] = math.pi * 4 * 140
        b = ub.sweep_along_planar_path([circle_yz((0, 0, 0), 2)], path, move_profile=False)
        out["result"] = info(b)
    elif name == "path_reversed_order":
        # 経路の曲線の順番が逆（終点側から並べた）
        b = ub.sweep_along_planar_path([circle_yz((0, 0, 0), 2)], list(reversed(L_path())), move_profile=False)
        out["result"] = info(b)
    elif name == "path_middle_start":
        # 断面が経路の端でなく途中（直線の中ほど (10,0,0)）にある
        b = ub.sweep_along_planar_path([circle_yz((10, 0, 0), 2)], L_path(), move_profile=False)
        out["result"] = info(b)
    elif name == "faces":
        # 面ごとの情報（平らか・中心）。名前付けの手がかり
        b = ub.sweep_along_planar_path(rect_yz((0, 0, 0), 4, 2), L_path(), move_profile=False)
        m = ub.tessellate(b)
        out["tessellate_type"] = type(m).__name__
        out["tessellate_attrs"] = [a for a in dir(m) if not a.startswith("_")][:40]
        faces = b.faces()
        out["n_faces"] = len(faces)
        out["face_methods"] = [a for a in dir(faces[0]) if not a.startswith("_")]
        fl = []
        for f in faces:
            d = {}
            try:
                lo, hi = ub.face_bounding_box(f)
                d["bbox"] = [list(map(float, lo)), list(map(float, hi))]
            except Exception as e:  # noqa: BLE001
                d["bbox_error"] = repr(e)
            try:
                d["area"] = float(ub.face_area(f))
            except Exception as e:  # noqa: BLE001
                d["area_error"] = repr(e)
            fl.append(d)
        out["faces"] = fl
    elif name == "pipe_bspline":
        # 参考: pipe_sweep / curve_sweep は B スプラインの経路が要る。線から作れるか
        try:
            pts = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (20.0, 5.0, 0.0), (30.0, 15.0, 0.0)]
            bs = ub.create_curve_interp_points(pts)
            out["interp_type"] = type(bs).__name__
            b = ub.pipe_sweep(bs, radius=1.0)
            out["pipe"] = info(b)
        except Exception as e:  # noqa: BLE001
            out["pipe_error"] = repr(e)
    print("@@" + json.dumps(out, ensure_ascii=False))


CASES = ["circle_L", "circle_offset", "rect_L", "ring_hole", "tilted_profile", "closed_path",
         "path_reversed_order", "path_middle_start", "faces", "pipe_bspline"]

if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_case(sys.argv[1])
        sys.exit(0)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("python " + sys.version.replace("\n", " ") + "\n")
        try:
            import importlib.metadata
            f.write("usd-brep " + importlib.metadata.version("usd-brep") + "\n")
        except Exception as e:  # noqa: BLE001
            f.write(f"usd-brep ? {e}\n")
        for c in CASES:
            r = subprocess.run([sys.executable, os.path.abspath(__file__), c], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=300)
            line = next((x for x in r.stdout.splitlines() if x.startswith("@@")), None)
            if line:
                f.write(json.dumps(json.loads(line[2:]), ensure_ascii=False, indent=1) + "\n")
            else:
                f.write(f"== {c}: 失敗（終了コード {r.returncode}）\n{r.stdout[-2000:]}\n{r.stderr[-3000:]}\n")
            f.flush()
            print(c, "OK" if line else f"失敗 {r.returncode}")
        f.write("完了\n")
    print("書き出しました:", REPORT)
