"""住戸の 2 次元図面（平面図 1:50・等角投影図、室名と畳数の注記、寸法）を作り、Data/Unit に保存して PDF に書き出す。

    cd core && python ../Data/Unit/build_drawing.py

モデルは住戸のアセンブリ（Unit_asm.usda: 躯体・床・住設・家具）。平面図は真上から見た図で、開口は天井まで抜いてあるので
ドア・窓の位置が見える。寸法の相手はビューのスナップ点（エッジの端点）から、モデルの座標で探す。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "core"))
sys.path.insert(0, HERE)
from cad.api.rpc import Api  # noqa: E402
from cad.doc.workspace import Workspace  # noqa: E402
import build_unit as U  # noqa: E402

S = 1 / 50                   # 平面図の縮尺


def build(api, asm):
    st = api.call("drw.new", {"model": asm, "size": "A2", "scale": "1:50", "standard": False})
    d = st["doc"]
    call = lambda m, **p: api.call(m, {"doc": d, **p})
    call("drw.sheet", title="住戸平面図 2LDK")
    # 平面図（真上から。図の上が北 = 玄関側）と、等角投影図
    st = call("drw.view_add", orient="top", x=135, y=210)          # x, y = 図の中心
    P = st["views"][-1]["id"]
    st = call("drw.view_add", orient="iso", x=430, y=255)
    I = st["views"][-1]["id"]
    call("drw.view_set", id=I, scale=[1, 60])

    view = lambda vid: next(v for v in call("doc.state")["views"] if v["id"] == vid)
    # 平面図のビュー座標（用紙の mm）とモデルの (x, p) の関係を、スナップ点から確かめる（p = -z）
    pv = view(P)
    s0 = next(s for s in pv["snaps"] if abs(s["w"][2]) > 1000)
    sign = 1 if abs(s0["p"][1] - (-s0["w"][2]) * S) < 1e-6 else -1
    to_view = lambda x, p: [x * S, sign * p * S]

    def snap(vid, x, p):
        """平面の座標 (x, p) にある端点（高さは問わない）。"""
        best, bd = None, 1e18
        for s in view(vid)["snaps"]:
            if s["kind"] != "vertex":
                continue
            dd = (s["w"][0] - x) ** 2 + (-s["w"][2] - p) ** 2
            if dd < bd:
                best, bd = s, dd
        if bd > 1.0:
            raise SystemExit(f"スナップ点が見つかりません: ({x}, {p})（最も近いもの {best and best['w']}）")
        return {"src": best["src"], "hint": best["w"]}

    def dim(a, b, at, dir):
        """寸法。at = 寸法線の位置（h なら p、v なら x。平面の座標）。"""
        mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
        pos = to_view(mid[0], at) if dir == "h" else to_view(at, mid[1])
        call("drw.dim_add", view=P, a=snap(P, *a), b=snap(P, *b), pos=pos, dir=dir)

    W, D, T, BAL = U.W, U.D, U.T_EXT, U.BAL
    # 外形: 間口 6000・奥行き 10000・バルコニー 1800
    dim((0, D), (W, D), D + 1400, "h")
    dim((W, 0), (W, D), W + 1400, "v")
    dim((W, -BAL), (W, 0), W + 1400, "v")
    # 北（玄関側）の内法の並び: 洋室 2 / 廊下 / トイレ / 収納
    for a, b in ((T, U.X_C0 - U.T_IN), (U.X_C0, U.X_C1), (U.X_C1 + U.T_IN, U.X_WC), (U.X_WC + U.T_IN, W - T)):
        dim((a, D - T), (b, D - T), D + 650, "h")
    # 西の内法の並び: LDK / 浴室 / 洋室 2
    for a, b in ((T, U.P_LDK), (U.P_LDK + U.T_IN, U.P_BATH), (U.P_BATH + U.T_IN, D - T)):
        dim((T, a), (T, b), -700, "v")
    # 東の内法の並び: LDK / 洋室 1 / 収納
    for a, b in ((T, U.P_LDK), (U.P_LDK + U.T_IN, U.P_R1), (U.P_R1 + U.T_IN, D - T)):
        dim((W - T, a), (W - T, b), W + 650, "v")
    # 南の掃き出し窓の幅
    dim((600, 0), (5400, 0), -BAL - 700, "h")

    # 室名と畳数（内法の面積 ÷ 1.62 m²）。位置は家具・建具に重ならないように図面用に決める（area_dp = 畳数の行のずれ）
    at = {"洋室 2": (1950, 9350, -420), "浴室": (950, 6900, -300), "洗面所": (2225, 6420, -190), "トイレ": (4480, 8770, 0),
          "洋室 1": (4800, 6200, -330), "LDK": (3000, 4150, -450)}
    for name, (x0, p0, x1, p1), (cx, cp) in U.ROOMS:
        cx, cp, dp = at.get(name, (cx, cp, -450))
        area = (x1 - x0) * (p1 - p0)
        h = 4.0 if name in ("LDK", "洋室 1", "洋室 2") else 2.5 if name == "トイレ" else 3.0
        call("drw.note_add", view=P, text=name, pos=to_view(cx, cp), h=h)
        if name not in ("廊下", "玄関", "収納", "バルコニー", "トイレ", "洗面所"):
            call("drw.note_add", view=P, text=f"約 {area / U.TATAMI:.1f} 畳", pos=to_view(cx, cp + dp), h=2.5)
    for text, x, p in (("キッチン", 2530, 4600), ("冷蔵庫", 4050, 4500), ("↑ 北", 3000, D + 2350)):
        call("drw.note_add", view=P, text=text, pos=to_view(x, p), h=2.5)
    iv = view(I)
    call("drw.note_add", view=I, text="3D 見取り図", pos=[(iv["bbox"][0] + iv["bbox"][2]) / 2, iv["bbox"][3] + 10], h=5.0)
    st = call("doc.state")
    print("寸法・注記:", len(st["dims"]), [x["text"] for x in st["dims"] if x["kind"] != "note"])
    return d


def main():
    api = Api(Workspace("usd_brep"))
    asm = api.call("doc.open", {"path": os.path.join(HERE, "Unit_asm.usda")})["doc"]
    d = build(api, asm)
    out = api.call("doc.save", {"doc": d, "path": os.path.join(HERE, "Unit_drawing.usda")})
    print("保存:", [os.path.basename(p) for p in out["saved"]])
    api.call("drw.export", {"doc": d, "format": "pdf", "path": os.path.join(HERE, "Unit_drawing.pdf")})
    return api, d


if __name__ == "__main__":
    main()
