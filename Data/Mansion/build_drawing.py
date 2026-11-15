"""住棟の 2 次元図面（A2・1:150）を作り、寸法を入れて Data/Mansion に保存・PDF 書き出しする。
図面のモデルは、住棟・外枠・階段ユニット（6 層）だけのアセンブリ（Mansion_building.usda。敷地と木は入れない）。

    cd core && python ../Data/Mansion/build_drawing.py

寸法の相手はビューのスナップ点（エッジの端点）から、モデルの座標で探す。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "core"))
from cad.api.rpc import Api  # noqa: E402
from cad.doc.workspace import Workspace  # noqa: E402


def build(api, part_doc):
    st = api.call("drw.new", {"model": part_doc, "size": "A2", "scale": "1:150"})
    d = st["doc"]
    call = lambda m, **p: api.call(m, {"doc": d, **p})
    call("drw.sheet", title="マンション 住棟 一般図")
    views = {v["label"]: v for v in st["views"]}
    F, T, R = views["正面図"]["id"], views["平面図"]["id"], views["右側面図"]["id"]
    # 配置（用紙の mm。y は上向き）: 正面図（南立面）を左下、平面図をその上、右側面図（東立面）を右、等角投影図を右上
    call("drw.view_set", id=F, x=45, y=50)
    call("drw.view_set", id=R, x=300, y=50)
    call("drw.view_set", id=T, x=45, y=258)
    st = call("drw.view_add", orient="iso", x=455, y=255)
    I = st["views"][-1]["id"]
    call("drw.view_set", id=I, scale=[1, 250])

    def snap(view, x=None, y=None, z=None):
        """モデル座標（None は問わない）にいちばん近いスナップ点（端点）。"""
        v = next(v for v in call("doc.state")["views"] if v["id"] == view)
        want = (x, y, z)
        best, bd = None, 1e18
        for s in v["snaps"]:
            if s["kind"] != "vertex":
                continue
            dd = sum((s["w"][i] - t) ** 2 for i, t in enumerate(want) if t is not None)
            if dd < bd:
                best, bd = s, dd
        if bd > 1.0:
            raise SystemExit(f"スナップ点が見つかりません: {view} {want}（最も近いもの {best and best['w']}）")
        return best

    def dim(view, a, b, off, dir):
        """寸法。文字の位置 = 2 点の中点 + off（用紙の mm）。"""
        sa, sb = snap(view, **a), snap(view, **b)
        pos = [(sa["p"][0] + sb["p"][0]) / 2 + off[0], (sa["p"][1] + sb["p"][1]) / 2 + off[1]]
        call("drw.dim_add", view=view, a={"src": sa["src"], "hint": sa["w"]}, b={"src": sb["src"], "hint": sb["w"]},
             pos=pos, dir=dir)

    # ---- 正面図（南立面）
    dim(F, dict(x=0, y=0), dict(x=24000, y=0), (0, -22), "h")                                 # 間口 24000
    dim(F, dict(x=900, y=250), dict(x=6900, y=250), (0, -12), "h")                           # 住戸の間口 6000
    dim(F, dict(x=0, y=0), dict(x=0, y=19000), (-16, 0), "v")                                 # 高さ 19000
    levels = [0, 3000, 6000, 9000, 12000, 15000, 19000]                                      # 階高 3000（最上階はパラペットまで）
    for y0, y1 in zip(levels[:-1], levels[1:]):
        dim(F, dict(x=24000, y=y0), dict(x=24000, y=y1), (44, 0), "v")
    dim(F, dict(x=900, y=15250), dict(x=5100, y=15250), (0, 34), "h")                        # 掃き出し窓 4200
    dim(F, dict(x=5100, y=15250), dict(x=6900, y=15250), (0, 34), "h")                       # 窓の間の壁 1800
    dim(F, dict(x=900, y=15250), dict(x=900, y=17500), (-9, 0), "v")                         # 窓の高さ 2250
    # ---- 右側面図（東立面）
    dim(R, dict(y=0, z=0), dict(y=0, z=-10000), (0, -12), "h")                                # 奥行き 10000
    dim(R, dict(y=2800, z=0), dict(y=2800, z=1800), (0, -25), "h")                             # バルコニーの出 1800
    dim(R, dict(y=2800, z=-10000), dict(y=2800, z=-11400), (0, -25), "h")                      # 外廊下の幅 1400
    dim(R, dict(x=28950, y=0, z=-9850), dict(x=28950, y=0, z=-13150), (0, -12), "h")          # 外階段の柱の外々 3300
    dim(R, dict(x=28950, y=0, z=-13150), dict(x=23700, y=21050, z=-13450), (12, 0), "v")      # 外階段の屋根まで 21050
    # ---- 平面図
    dim(T, dict(x=23700, y=20800, z=-13450), dict(x=29250, y=20800, z=-13450), (0, 9), "h")   # 階段の屋根 5550
    dim(T, dict(x=29250, y=20800, z=-9150), dict(x=29250, y=20800, z=-13450), (10, 0), "v")   # 4300
    dim(T, dict(x=15000, y=18000, z=-7500), dict(x=21000, y=18000, z=-7500), (0, 7), "h")     # 塔屋 6000
    st = call("doc.state")
    print("寸法:", [x["text"] for x in st["dims"]])
    return d


def building_assembly(api):
    """住棟・外枠・階段（部品の直線パターンで 6 層）のアセンブリ。部品はどれも原点に置く（モデルが同じ座標で作ってある）。"""
    parts = {n: api.call("doc.open", {"path": os.path.join(HERE, n + ".usda")})["doc"] for n in ("Building", "Frame", "StairUnit")}
    a = api.call("asm.new", {"name": "Mansion_building"})["doc"]
    for n in ("Building", "Frame", "StairUnit"):
        api.call("asm.add", {"doc": a, "part": parts[n], "position": [0, 0, 0]})
    comps = [c["id"] for c in api.call("doc.state", {"doc": a, "mesh": False})["components"]]
    for c in comps[1:]:
        api.call("asm.mate", {"doc": a, "type": "lock", "a": [comps[0], ""], "b": [c, ""]})
    api.call("asm.pattern", {"doc": a, "comp": comps[2], "kind": "linear", "axis": "y", "count": 6, "spacing": 3000})
    return a


def main():
    api = Api(Workspace("usd_brep"))
    a = building_assembly(api)
    api.call("doc.save", {"doc": a, "path": os.path.join(HERE, "Mansion_building.usda")})   # 決まった名前で上書き
    d = build(api, a)
    out = api.call("doc.save", {"doc": d, "path": os.path.join(HERE, "Building_drawing.usda")})
    print("保存:", [os.path.basename(p) for p in out["saved"]])
    api.call("drw.export", {"doc": d, "format": "pdf", "path": os.path.join(HERE, "Building_drawing.pdf")})
    return api, d


if __name__ == "__main__":
    main()
