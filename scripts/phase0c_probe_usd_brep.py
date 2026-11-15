"""フェーズ0c: usd-brep アダプタの残りの「要確認」を確かめ、適合テストまで実行する。

実行（リポジトリ直下で。画面出力をファイルに向ける必要はない）:
    python scripts\\phase0c_probe_usd_brep.py

結果は phase0c_report.txt（UTF-8）。適合テストの出力も末尾に入るので、このファイル 1 つを送ればよい。
注意: usd-brep のブーリアンは入力を消費する。ここでは必ず copy() してから渡す（0b はこれで落ちた）。
"""
from __future__ import annotations

import math
import os
import subprocess
import sys
import time
import traceback
from contextlib import contextmanager

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _open_report():
    for name in ("phase0c_report.txt", f"phase0c_report_{os.getpid()}.txt"):
        try:
            return name, open(name, "w", encoding="utf-8")
        except OSError:
            continue
    raise SystemExit("レポートファイルを開けません")


REPORT, _out = _open_report()


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    _out.write(s + "\n")
    _out.flush()          # 落ちてもそこまでは残る


@contextmanager
def step(title):
    log("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70)
    t = time.perf_counter()
    try:
        yield
    except Exception:
        log("!! 例外:\n" + traceback.format_exc())
    log(f"（{1000 * (time.perf_counter() - t):.0f} ms）")


def arr(name, v):
    try:
        import numpy as np
        a = np.asarray(v)
        log(f"  {name}: {type(v).__name__} shape={a.shape} dtype={a.dtype} 先頭={a.ravel()[:12].tolist()}")
    except Exception as e:
        log(f"  {name}: {type(v).__name__} ({e}) {str(v)[:200]}")


def main():
    import numpy as np
    import usd_brep as ub

    def box():
        return ub.create_box((0.0, 0.0, 0.0), 10.0, 5.0, 4.0)

    with step("C1 メッシュの配列（三角形 → 面 の対応を探す）"):
        b = box()
        poly = ub.tessellate(b)
        for meth in ("to_numpy_arrays", "to_mesh_arrays"):
            d = getattr(poly, meth)()
            log(f"{meth}() keys: {sorted(d)}")
            for k, v in d.items():
                arr(k, v)
        pf = poly.get_faces()
        log("get_faces():", len(pf), "個 / Brep の面:", b.face_count())
        log("PolyFace[0] の属性:", [n for n in dir(pf[0]) if not n.startswith("_")])
        log("PolyFace[0].get_normal():", pf[0].get_normal())
        log("get_points():", len(poly.get_points()), "先頭", poly.get_points()[:3])
        pe = poly.get_edges()
        log("get_edges():", len(pe), "PolyEdge の属性:", [n for n in dir(pe[0]) if not n.startswith("_")] if pe else "-")
        pv = poly.get_vertices()
        log("get_vertices():", len(pv), "PolyVertex の属性:", [n for n in dir(pv[0]) if not n.startswith("_")] if pv else "-")
        d = poly.to_numpy_arrays(face_varying_normals=True)
        log("to_numpy_arrays(face_varying_normals=True) keys:", sorted(d))
        cyl = ub.create_cylinder((0.0, 0.0, 0.0), 1.0, 2.0)
        d = ub.tessellate(cyl, chord_height_tolerance=0.01).to_numpy_arrays()
        log("円柱 (chord 0.01):", {k: np.asarray(v).shape for k, v in d.items()})

    with step("C2 円弧: 負の角度 / 曲線の回転"):
        for a0, a1 in ((-60.0, 60.0), (0.0, 360.0)):
            try:
                c = ub.create_arc((0.0, 0.0, 0.0), 1.0, a0, a1)
                log(f"create_arc({a0}, {a1}): 始点 {c.start_point()} 終点 {c.end_point()}")
            except Exception as e:
                log(f"create_arc({a0}, {a1}) 不可: {e}")
        c = ub.create_arc((0.0, 0.0, 0.0), 1.0, 0.0, 120.0)
        c = ub.rotate(c, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 300.0)
        c = ub.translate(c, (5.0, 0.0, 0.0))
        log("0→120 を 300° 回して (5,0) へ: 始点", c.start_point(), "終点", c.end_point(), "期待 (5.5,-0.866) → (5.5,0.866)")

    with step("C3 ブーリアン（copy してから）"):
        b = box()
        cyl = ub.create_cylinder((5.0, 2.5, 0.0), 1.0, 4.0)
        d = ub.boolean_difference(b.copy(), cyl.copy())
        log("difference:", ub.compute_volume(d), f"期待 {200 - 4 * math.pi:.4f}", "faces", d.face_count())
        log("元の box は無事か:", ub.compute_volume(b), "faces", b.face_count())
        u = ub.boolean_union(b.copy(), ub.translate(cyl.copy(), (0.0, 0.0, 2.0)))
        log("union:", ub.compute_volume(u), f"期待 {200 + 2 * math.pi:.4f}", "faces", u.face_count())
        i = ub.boolean_intersection(b.copy(), ub.translate(cyl.copy(), (0.0, 0.0, 2.0)))
        log("intersection:", ub.compute_volume(i), f"期待 {2 * math.pi:.4f}")
        t = time.perf_counter(); x = b.copy()
        for k in range(20):
            x = ub.boolean_difference(x, ub.create_sphere((0.5 + k * 0.45, 2.5, 4.0), 0.3))
        log(f"球で 20 回連続 difference: {1000 * (time.perf_counter() - t):.0f} ms, faces {x.face_count()}")

    with step("C4 フィレット（copy してから）"):
        b = box().copy()
        idx, t = ub.find_edge(b, (5.0, 0.0, 4.0))
        e = b.edges()[idx]
        log("find_edge →", idx, t, "point_at:", e.point_at(t), "faces:", len(e.faces()))
        f = ub.fillet_edges(b, [e], radius=0.5)
        log("volume", ub.compute_volume(f), f"期待 {200 - (1 - math.pi / 4) * 0.25 * 10:.4f}", "faces", f.face_count())

    with step("C5 エッジと隣接面 / 境界の折れ線"):
        b = box()
        faces = b.faces()
        idx = {hash(f): i for i, f in enumerate(faces)}
        for e in b.edges()[:4]:
            log("edge faces →", [idx.get(hash(f)) for f in e.faces()], "is:", [any(f is g for g in faces) for f in e.faces()])
        pts, counts, hs = ub.tessellate_boundaries(b)
        log("tessellate_boundaries: 点", len(pts), "counts", list(counts), "edges() と同じ順か:",
            [a is c for a, c in zip(hs, b.edges())])
        cyl = ub.create_cylinder((0.0, 0.0, 0.0), 1.0, 2.0)
        pts, counts, hs = ub.tessellate_boundaries(cyl)
        log("円柱: edges", len(hs), "counts", list(counts), "各エッジの隣接面数", [len(e.faces()) for e in hs])

    with step("C6 面の種類"):
        cyl = ub.create_cylinder((0.0, 0.0, 0.0), 1.0, 2.0)
        for f in cyl.faces():
            log("面: planar", f.surface().is_planar(1e-9), "interior", f.interior_point(), "area", f.area())

    with step("C7 アダプタの試運転"):
        sys.path.insert(0, os.path.join(ROOT, "core"))
        from cad.kernel import FRONT, Profile, Segment
        from cad.kernel.usd_brep import UsdBrepKernel
        from cad.kernel.usd_brep import _extract
        k = UsdBrepKernel()
        pos, tri, fid = _extract(ub.tessellate(box()))
        log("_extract(box):", pos.shape, tri.shape, "面番号あり" if fid is not None else "面番号なし（find_face で補う）")
        pts = [(0, 0), (10, 0), (10, 5), (0, 5)]
        segs = [Segment("line", f"L{i}", [pts[i], pts[(i + 1) % 4]]) for i in range(4)]
        t = time.perf_counter()
        a = k.extrude(Profile(FRONT, [segs]), 4.0)
        log(f"extrude {1000 * (time.perf_counter() - t):.0f} ms 履歴:", sorted(map(str, a.history.values())))
        log("edges:", len(k.edges(a.shape)), "volume:", k.volume(a.shape))
        c = k.extrude(Profile(FRONT, [[Segment("circle", "C1", [(5.0, 2.5)], 1.0)]]), 4.0)
        t = time.perf_counter()
        cut = k.boolean("cut", a.shape, c.shape)
        log(f"cut {1000 * (time.perf_counter() - t):.0f} ms volume:", k.volume(cut.shape),
            "履歴:", sorted(map(str, cut.history.values())))
        log("元の形状は無事か:", k.volume(a.shape), k.volume(c.shape))
        e = next(h for h, fa, fb in k.edges(cut.shape))
        t = time.perf_counter()
        fil = k.fillet(cut.shape, [e], 0.5)
        log(f"fillet {1000 * (time.perf_counter() - t):.0f} ms volume:", k.volume(fil.shape),
            "fillet 面:", sum(1 for v in fil.history.values() if v[0] == "fillet"))

    with step("C8 適合テスト"):
        r = subprocess.run([sys.executable, "-m", "pytest", os.path.join(ROOT, "core", "tests", "test_kernel_conformance.py"),
                            "-v", "-p", "no:cacheprovider"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=ROOT,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        log(r.stdout[-20000:])
        if r.stderr.strip():
            log("stderr:\n" + r.stderr[-5000:])
        log("終了コード:", r.returncode, "（負の値や 3221225477 ならプロセスが落ちた）")

    _out.close()
    print(f"\n{REPORT} に書きました")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log("!! 例外:\n" + traceback.format_exc())
