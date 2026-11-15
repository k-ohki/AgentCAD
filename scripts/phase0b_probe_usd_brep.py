"""フェーズ0b: usd-brep アダプタ（core/cad/kernel/usd_brep.py）の「要確認」箇所を確かめる。

実行（リポジトリ直下で。Windows PowerShell の例）:
    .venv\\Scripts\\activate
    python scripts\\phase0b_probe_usd_brep.py

結果は phase0b_report.txt に UTF-8 で書く（例外のトレースバックも含む）。画面にも同じ内容を出す。
各実験は独立しているので、途中で失敗しても最後まで走る。
"""
from __future__ import annotations

import importlib.metadata
import inspect
import math
import os
import platform
import sys
import time
import traceback
from contextlib import contextmanager

# 画面出力: 日本語を表示できないコンソールでも止まらないようにする
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass


def _open_report():
    """レポートを開く。`> phase0b_report.txt` のように画面出力を同じファイルへ向けて実行された場合、
    Windows ではそのファイルを開けないので、別名に書く。"""
    for name in ("phase0b_report.txt", "phase0b_report_full.txt", f"phase0b_report_{os.getpid()}.txt"):
        try:
            return name, open(name, "w", encoding="utf-8")
        except OSError:
            continue
    raise SystemExit("レポートファイルを開けません。書き込みできるフォルダで実行してください")


REPORT, _out = _open_report()


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    _out.write(s + "\n")
    _out.flush()


@contextmanager
def step(title):
    log("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70)
    t = time.perf_counter()
    try:
        yield
    except Exception:
        log("!! 例外:\n" + traceback.format_exc())
    log(f"（{1000 * (time.perf_counter() - t):.0f} ms）")


def summ(x, depth=2, width=6) -> str:
    """オブジェクトの中身を短く表す。メソッドは呼ばない（プロパティ値だけ読む）。"""
    try:
        import numpy as np
    except ImportError:
        np = None
    t = type(x).__name__
    if x is None or isinstance(x, (bool, int, float, str)):
        return repr(x)[:300]
    if np is not None and isinstance(x, np.ndarray):
        return f"ndarray{x.shape} {x.dtype} 先頭={x.ravel()[:9].tolist()}"
    if isinstance(x, dict):
        items = list(x.items())
        body = ", ".join(f"{k!r}: {summ(v, depth - 1)}" for k, v in items[:width]) if depth > 0 else "…"
        return f"dict[{len(items)}]{{{body}}}"
    if isinstance(x, (list, tuple)):
        body = ", ".join(summ(v, depth - 1) for v in x[:width]) if depth > 0 else "…"
        return f"{t}[{len(x)}][{body}{', …' if len(x) > width else ''}]"
    attrs = []
    if depth > 0:
        for n in dir(x):
            if n.startswith("_"):
                continue
            try:
                v = getattr(x, n)
            except Exception as e:
                attrs.append(f"{n}=<{type(e).__name__}>")
                continue
            if callable(v):
                continue
            attrs.append(f"{n}={summ(v, depth - 1)}")
    return f"<{t}{' ' + ', '.join(attrs) if attrs else ''}>"


def members(obj, label):
    log(f"--- {label}: {type(obj).__name__}")
    for n in dir(obj):
        if n.startswith("_"):
            continue
        try:
            v = getattr(obj, n)
        except Exception as e:
            log(f"  {n}: <{type(e).__name__}: {e}>")
            continue
        if callable(v):
            doc = (inspect.getdoc(v) or "").strip().splitlines()
            log(f"  {n}()  # {' / '.join(doc[:2])[:300]}")
        else:
            log(f"  {n} = {summ(v, 1)}")


def call(label, fn, *a, **kw):
    t = time.perf_counter()
    r = fn(*a, **kw)
    log(f"{label} → {summ(r)}  （{1000 * (time.perf_counter() - t):.1f} ms）")
    return r


def val(x):
    return x() if callable(x) else x


def main():
    with step("環境"):
        log("python:", sys.version.replace("\n", " "))
        log("platform:", platform.platform())
        for pkg in ("usd-brep", "numpy", "scipy"):
            try:
                log(f"{pkg}:", importlib.metadata.version(pkg))
            except Exception as e:
                log(f"{pkg}: ? ({e})")
    try:
        import usd_brep as ub
    except Exception:
        log("usd_brep を import できません:\n" + traceback.format_exc())
        _out.close()
        return

    with step("関数の説明（全文）"):
        names = ["linear_sweep", "non_manifold_linear_sweep", "rotational_sweep", "create_planar_faces",
                 "create_line_segment", "create_arc", "create_circle", "evaluate_curve", "boolean_union",
                 "boolean_difference", "boolean_intersection", "boolean_with_options", "fillet_edges",
                 "fillet_edges_per_edge", "find_face", "find_edge", "get_closest_point", "brep_closest_point",
                 "tessellate", "tessellate_boundaries", "tessellation_defaults", "compute_volume",
                 "brep_bounding_box", "rotate", "translate", "transform", "face_bounding_box", "face_area",
                 "evaluate_surface_normal", "get_faces_via_api", "get_edges_via_api", "material_census",
                 "is_manifold_solid", "vertex_point", "edge_length", "create_box", "create_cylinder"]
        for n in names:
            f = getattr(ub, n, None)
            log(f"\n## {n}\n{inspect.getdoc(f) if f else '（無い）'}")

    with step("クラスのメンバー"):
        for n in ("Brep", "Face", "Edge", "Vertex", "Surface", "Curve", "Circle", "PolyBrep", "PolyFace",
                  "Axis2Placement", "Vec3"):
            c = getattr(ub, n, None)
            if c is None:
                log(f"## {n}: 無い"); continue
            log(f"\n## {n}\n{(inspect.getdoc(c) or '').strip()[:800]}")
            members(c, n)

    box = faces = edges = None
    with step("E1 箱: create_box((0,0,0), 10, 5, 4)"):
        box = call("create_box", ub.create_box, (0.0, 0.0, 0.0), 10.0, 5.0, 4.0)
        log("face/edge/vertex count:", val(box.face_count), val(box.edge_count), val(box.vertex_count))
        log("volume:", val(box.volume), " 期待 200")
        call("brep_bounding_box（x,y,z の対応を確認。期待 0..10, 0..5, 0..4）", ub.brep_bounding_box, box)
        faces = list(val(box.faces)); edges = list(val(box.edges))
        log("faces:", summ(faces, 1)); log("edges:", summ(edges, 1))

    with step("E2 Face / Edge / Vertex の中身"):
        members(faces[0], "Face[0]")
        members(edges[0], "Edge[0]")
        vs = list(val(box.vertices))
        members(vs[0], "Vertex[0]")
        for i, f in enumerate(faces):
            log(f"face {i}: area={ub.face_area(f):.4f} bbox={summ(ub.face_bounding_box(f))}")

    with step("E3 メッシュ化の戻り値（三角形 → 面 の対応があるか）"):
        call("tessellation_defaults()", ub.tessellation_defaults)
        r = call("ub.tessellate(box)", ub.tessellate, box)
        log("詳細:", summ(r, 3, 10))
        if hasattr(r, "__dict__") or not isinstance(r, (list, tuple, dict)):
            members(r, "tessellate の戻り値")
        try:
            r2 = call("box.tessellate()", box.tessellate)
            log("詳細:", summ(r2, 3, 10))
        except Exception:
            log("box.tessellate() 失敗:\n" + traceback.format_exc())
        r3 = call("tessellate_boundaries(box)", ub.tessellate_boundaries, box)
        log("詳細:", summ(r3, 3, 10))

    with step("E4 点から面・エッジを探す / 同一性の判定方法"):
        f = call("find_face(box, (5, 2.5, 4))  # 上面の中央", ub.find_face, box, (5.0, 2.5, 4.0))
        log("faces[i] is f:", [x is f for x in faces])
        try:
            log("faces[i] == f:", [x == f for x in faces])
        except Exception as e:
            log("== 不可:", e)
        log("hash:", [hash(x) for x in faces][:6], hash(f))
        e = call("find_edge(box, (5, 0, 4))  # 上面と前面の間", ub.find_edge, box, (5.0, 0.0, 4.0))
        call("get_closest_point(box, (5, 2.5, 10))", ub.get_closest_point, box, (5.0, 2.5, 10.0))
        call("brep_closest_point(box, (5, 2.5, 10))", ub.brep_closest_point, box, (5.0, 2.5, 10.0))

    with step("E5 曲線と押し出し"):
        P = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 5.0, 0.0), (0.0, 5.0, 0.0)]
        rect = [ub.create_line_segment(P[i], P[(i + 1) % 4]) for i in range(4)]
        log("line:", summ(rect[0], 2))
        arc = call("create_arc((0,0,0), 1, 0, 90)", ub.create_arc, (0.0, 0.0, 0.0), 1.0, 0.0, 90.0)
        for t in (0.0, 0.5, 1.0):
            try:
                call(f"evaluate_curve(arc, {t})  # 反時計回りなら t=0 で (1,0), t=1 で (0,1)", ub.evaluate_curve, arc, t)
            except Exception as ex:
                log(f"evaluate_curve(arc,{t}) 失敗: {ex}")
        try:
            call("create_arc((0,0,0), 1, 300, 420)  # 360 を超える角度", ub.create_arc, (0.0, 0.0, 0.0), 1.0, 300.0, 420.0)
        except Exception as ex:
            log("360 超は不可:", ex)
        circ = call("create_circle((5,2.5,0), 1)", ub.create_circle, (5.0, 2.5, 0.0), 1.0)
        try:
            s = call("linear_sweep(rect, (0,0,1), 4)", ub.linear_sweep, rect, (0.0, 0.0, 1.0), 4.0)
            log("solid?", ub.is_manifold_solid(s), "faces", val(s.face_count), "volume", ub.compute_volume(s), "期待 200")
        except Exception:
            log("linear_sweep 失敗:\n" + traceback.format_exc())
        try:
            s = call("linear_sweep(rect + [circle], (0,0,1), 4)  # 穴を自動で作るか", ub.linear_sweep,
                     rect + [circ], (0.0, 0.0, 1.0), 4.0)
            log("solid?", ub.is_manifold_solid(s), "faces", val(s.face_count), "volume", ub.compute_volume(s),
                f"穴なら {200 - 4 * math.pi:.4f}")
        except Exception:
            log("穴付き linear_sweep 失敗:\n" + traceback.format_exc())
        try:
            pf = call("create_planar_faces(rect)", ub.create_planar_faces, rect)
            s = call("non_manifold_linear_sweep(pf, (0,0,1), 4)", ub.non_manifold_linear_sweep, pf, (0.0, 0.0, 1.0), 4.0)
            log("solid?", ub.is_manifold_solid(s), "volume", ub.compute_volume(s))
        except Exception:
            log("planar_faces 経由 失敗:\n" + traceback.format_exc())

    with step("E6 回転: XY 平面の矩形 x 1..2, y 0..3 を Y 軸まわりに 90°"):
        Q = [(1.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 3.0, 0.0), (1.0, 3.0, 0.0)]
        prof = [ub.create_line_segment(Q[i], Q[(i + 1) % 4]) for i in range(4)]
        s = call("rotational_sweep(prof, (0,0,0), (0,1,0), 90)", ub.rotational_sweep, prof, (0.0, 0.0, 0.0),
                 (0.0, 1.0, 0.0), 90.0)
        log("solid?", ub.is_manifold_solid(s), "faces", val(s.face_count), "volume", ub.compute_volume(s),
            f"期待 {math.pi / 4 * 3 * 3:.4f}")
        call("bbox（右ねじなら z は -2..0）", ub.brep_bounding_box, s)

    with step("E7 ブーリアン"):
        cyl = call("create_cylinder((5,2.5,0), 1, 4)", ub.create_cylinder, (5.0, 2.5, 0.0), 1.0, 4.0)
        call("cylinder bbox（z 方向・底面中心が原点か）", ub.brep_bounding_box, cyl)
        d = call("boolean_difference(box, cyl)", ub.boolean_difference, box, cyl)
        log("volume", ub.compute_volume(d), f"期待 {200 - 4 * math.pi:.4f}", "faces", val(d.face_count))
        u = call("boolean_union(box, cyl)", ub.boolean_union, box, ub.translate(cyl, (0.0, 0.0, 2.0)))
        log("volume", ub.compute_volume(u), f"期待 {200 + 2 * math.pi:.4f}")
        t = time.perf_counter(); b = box
        for i in range(20):
            b = ub.boolean_difference(b, ub.create_sphere((0.5 + i * 0.45, 2.5, 4.0), 0.3))
        log(f"球で 20 回連続 difference: {1000 * (time.perf_counter() - t):.0f} ms, faces {val(b.face_count)}")
        log("material_census(box):", summ(ub.material_census(box), 3))
        log("material_census(diff):", summ(ub.material_census(d), 3))

    with step("E8 フィレット"):
        r = ub.find_edge(box, (5.0, 0.0, 4.0))
        e = next((x for x in (r if isinstance(r, tuple) else (r,)) if isinstance(x, ub.Edge)), None)
        log("find_edge の Edge:", summ(e))
        for kw in ({"radius": 0.5}, {}):
            try:
                args = (box, [e]) if kw else (box, [e], 0.5)
                f = call(f"fillet_edges(box, [edge], {kw or '0.5'})", ub.fillet_edges, *args, **kw)
                log("volume", ub.compute_volume(f), f"期待 {200 - (1 - math.pi / 4) * 0.25 * 10:.4f}",
                    "faces", val(f.face_count))
                break
            except Exception:
                log("失敗:\n" + traceback.format_exc())

    with step("E9 変換"):
        r = call("rotate(box, (0,0,0), (0,0,1), 90)", ub.rotate, box, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 90.0)
        call("bbox（期待 x -5..0, y 0..10）", ub.brep_bounding_box, r)
        log("元の box は変わらないか:", summ(ub.brep_bounding_box(box)))
        t = call("translate(box, (1,2,3))", ub.translate, box, (1.0, 2.0, 3.0))
        call("bbox", ub.brep_bounding_box, t)

    with step("E10 アダプタの試運転（core/cad/kernel/usd_brep.py）"):
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, os.path.join(here, "..", "core"))
        from cad.kernel import FRONT, Profile, Segment
        from cad.kernel.usd_brep import UsdBrepKernel
        k = UsdBrepKernel()
        pts = [(0, 0), (10, 0), (10, 5), (0, 5)]
        segs = [Segment("line", f"L{i}", [pts[i], pts[(i + 1) % 4]]) for i in range(4)]
        a = k.extrude(Profile(FRONT, [segs]), 4.0)
        log("extrude 履歴:", sorted(map(str, a.history.values())))
        log("edges:", len(k.edges(a.shape)), "volume:", k.volume(a.shape))
        c = k.extrude(Profile(FRONT, [[Segment("circle", "C1", [(5.0, 2.5)], 1.0)]]), 4.0)
        cut = k.boolean("cut", a.shape, c.shape)
        log("cut 履歴:", summ(sorted(map(str, cut.history.values())), 1, 20), "volume:", k.volume(cut.shape))
        log("→ 続けて: cd core && python -m pytest tests/test_kernel_conformance.py -v")

    _out.close()
    print(f"\n{REPORT} に書きました")


if __name__ == "__main__":
    main()
