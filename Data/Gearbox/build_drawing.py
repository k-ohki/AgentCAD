"""遊星歯車減速機の組立図（A2・1:1。正面図・右側面図（隠線つき）・等角投影図、寸法、歯車の諸元、部品表）を作り、
Data/Gearbox に保存して PDF に書き出す。

    cd core && python ../Data/Gearbox/build_drawing.py

寸法の相手はビューのスナップ点（端点・円の中心）から、モデルの座標で探す。直径はビューの円のエッジ（中心と半径）から探す。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "core"))
sys.path.insert(0, HERE)
from cad.api.rpc import Api  # noqa: E402
from cad.doc.workspace import Workspace  # noqa: E402
import build_gearbox as G  # noqa: E402

BOM = [  # 番号, 部品名, パート, 数量, 備考
    ("1", "入力側ハウジング", "RearHousing", 1, "フランジ 140 角"),
    ("2", "内歯車", "RingGear", 1, "m2 z48"),
    ("3", "太陽歯車・入力軸", "SunGear", 1, "m2 z18 / 軸 φ12"),
    ("4", "遊星歯車", "PlanetGear", 3, "m2 z15"),
    ("5", "キャリア・出力軸", "Carrier", 1, "軸 φ20"),
    ("6", "出力側カバー", "Cover", 1, "リブ 6 本"),
    ("7", "玉軸受", "Bearing_6001", 1, "12 × 28 × 8"),
    ("8", "玉軸受", "Bearing_6004", 1, "20 × 42 × 12"),
    ("9", "六角穴付きボルト", "Bolt_M6", 6, "M6 / PCD 116"),
    ("10", "平行キー", "Key_4x4", 1, "4 × 4 × 16"),
    ("11", "平行キー", "Key_6x6", 1, "6 × 6 × 20"),
]
GEARS = [  # 歯車の諸元
    ("", "太陽", "遊星", "内歯車"),
    ("モジュール", "2", "2", "2"),
    ("歯数", f"{G.Z_SUN}", f"{G.Z_PLANET}", f"{G.Z_RING}"),
    ("圧力角", "20°", "20°", "20°"),
    ("基準円直径", f"{G.M * G.Z_SUN:g}", f"{G.M * G.Z_PLANET:g}", f"{G.M * G.Z_RING:g}"),
    ("歯幅", "14", "14", "16"),
]


def build(api, asm):
    st = api.call("drw.new", {"model": asm, "size": "A2", "scale": "1:1", "standard": False})
    d = st["doc"]
    call = lambda m, **p: api.call(m, {"doc": d, **p})
    call("drw.sheet", title="遊星歯車減速機 組立図")
    st = call("drw.view_add", orient="front", x=115, y=280)          # x, y = 図の中心
    F = st["views"][-1]["id"]
    st = call("drw.view_add", parent=F, side="right")
    S = st["views"][-1]["id"]
    st = call("drw.view_add", orient="iso", x=480, y=285)
    I = st["views"][-1]["id"]
    call("drw.view_set", id=S, hidden=True)                           # 側面図は隠線で中の歯車・軸受を見せる
    views = lambda: {v["id"]: v for v in call("doc.state")["views"]}
    vs = views()
    call("drw.view_set", id=S, x=vs[F]["x"] + 185, y=vs[S]["y"])
    vs = views()

    def snap(vid, kinds=("vertex", "center", "mid"), **want):
        """モデルの座標の条件（x=…, y=…, z=…）に最も近いスナップ点。端点を優先する。"""
        ax = {"x": 0, "y": 1, "z": 2}
        best, bd = None, 1e18
        for s in vs[vid]["snaps"]:
            if s["kind"] not in kinds:
                continue
            dd = sum((s["w"][ax[k]] - v) ** 2 for k, v in want.items()) + (0 if s["kind"] == "vertex" else 1e-3)
            if dd < bd:
                best, bd = s, dd
        if bd > 0.25:
            raise SystemExit(f"スナップ点が見つかりません: {vid} {want}（最も近いもの {best and best['w']}）")
        return best

    def dim(vid, a, b, dir, off):
        """off = 寸法線の位置（h なら y、v なら x。ビューの原点からの用紙の mm）。"""
        pa, pb = a["p"], b["p"]
        pos = [(pa[0] + pb[0]) / 2, off] if dir == "h" else [off, (pa[1] + pb[1]) / 2]
        call("drw.dim_add", view=vid, a={"src": a["src"], "hint": a["w"]}, b={"src": b["src"], "hint": b["w"]}, pos=pos, dir=dir)

    def dia(vid, c, r, pos, part=None):
        """中心 c・半径 r の円（ビュー座標）の直径寸法。"""
        for e in vs[vid]["edges"]:
            if e["kind"] == "circle" and abs(e["r"] - r) < 1e-3 and abs(e["c"][0] - c[0]) + abs(e["c"][1] - c[1]) < 1e-3 \
                    and (part is None or e["name"].startswith(part)):
                call("drw.dim_add", view=vid, a={"src": "edge:" + e["name"]}, pos=pos)
                return
        raise SystemExit(f"円が見つかりません: {vid} 中心 {c} 半径 {r}")

    # 正面図（出力軸側から）: フランジ 140 角、取り付け穴のピッチ 116、外径 φ132、出力軸 φ20、取り付け穴 φ9
    dim(F, snap(F, x=-70, y=-56), snap(F, x=70, y=-56), "h", -92)
    c = lambda x, y: snap(F, kinds=("center",), x=x, y=y, z=-22)
    dim(F, c(-58, -58), c(58, -58), "h", -82)
    dim(F, c(58, -58), c(58, 58), "v", 84)
    dia(F, (0, 0), G.R_OUT, [-62, 78], part="Cover")
    dia(F, (0, 0), 10, [34, 22], part="Carrier")
    dia(F, (-58, 58), 4.5, [-88, 70])
    # 右側面図: 全長と、軸方向の区切り（入力軸の端 -50、フランジ -22 / -14、カバーの面 28、ボスの端 42、出力軸の端 70）
    ax = lambda z: snap(S, kinds=("center",), x=0, y=0, z=z)
    fl = lambda z: snap(S, kinds=("vertex",), y=-70, z=z)
    dim(S, ax(-50), ax(70), "h", 92)
    chain = [ax(-50), fl(-22), fl(-14), ax(28), ax(42), ax(70)]
    for a, b in zip(chain[:-1], chain[1:]):
        dim(S, a, b, "h", -82)
    dim(S, snap(S, kinds=("vertex",), y=-70, z=-22), snap(S, kinds=("vertex",), y=70, z=-22), "v", min(a["p"][0] for a in chain) - 14)

    note = lambda vid, text, pos, h=3.5: call("drw.note_add", view=vid, text=text, pos=pos, h=h)
    sp = {z: ax(z)["p"][0] for z in (-50, 70)}
    note(S, "入力軸 φ12（キー 4 × 4）", [(sp[-50] * 3 + sp[70]) / 4 + (0 if sp[-50] < sp[70] else 4), 80], 3.0)
    note(S, "出力軸 φ20（キー 6 × 6）", [(sp[70] * 3 + sp[-50]) / 4 + (0 if sp[-50] < sp[70] else 4), 80], 3.0)
    note(F, "6 × M6 六角穴付きボルト（PCD 116）", [25, 80], 3.0)
    for vid, text in ((F, "正面図（出力側）"), (S, "右側面図（隠線）"), (I, "等角投影図")):
        bb = views()[vid]["bbox"]
        note(vid, text, [(bb[0] + bb[2]) / 2, bb[1] - (40 if vid != I else 14)], 4.5)

    # 歯車の諸元と部品表（用紙の座標で置く。注記は正面図に付ける）
    ox, oy = vs[F]["x"], vs[F]["y"]
    at = lambda x, y: [x - ox, y - oy]
    x0, y0, dy = 30, 128, 6.5
    note(F, "歯車の諸元", at(x0 + 62, y0 + 9), 4.5)
    for r, row in enumerate(GEARS):
        for cx, text in zip((18, 58, 84, 110), row):
            if text:
                note(F, text, at(x0 + cx, y0 - r * dy), 3.5)
    note(F, f"減速比 1 + {G.Z_RING}/{G.Z_SUN} = {G.RATIO:.2f}（内歯車を固定、キャリアから出力）", at(x0 + 62, y0 - len(GEARS) * dy - 2), 3.5)
    note(F, f"軸間距離 {G.A:g}、遊星 3 個を 120° おきに配置", at(x0 + 62, y0 - (len(GEARS) + 1) * dy - 2), 3.5)
    x0, y0, dy = 190, 136, 6.5
    cols = [(10, "番号"), (44, "部品名"), (92, "パート"), (124, "数量"), (160, "備考")]
    note(F, "部品表", at(x0 + 85, y0 + 9), 4.5)
    rows = [tuple(t for _, t in cols)] + [(n, name, part, f"{q}", memo) for n, name, part, q, memo in BOM]
    for r, row in enumerate(rows):
        for (cx, _), text in zip(cols, row):
            note(F, text, at(x0 + cx, y0 - r * dy), 3.5)
    st = call("doc.state")
    print("寸法:", [x["text"] for x in st["dims"] if x["kind"] != "note"])
    print("ビュー:", [(v["id"], round(v["x"]), round(v["y"]), [round(t) for t in v["bbox"]]) for v in st["views"]])
    return d


def main():
    api = Api(Workspace("usd_brep"))
    asm = api.call("doc.open", {"path": os.path.join(HERE, "Gearbox_asm.usda")})["doc"]
    d = build(api, asm)
    out = api.call("doc.save", {"doc": d, "path": os.path.join(HERE, "Gearbox_drawing.usda")})
    print("保存:", [os.path.basename(p) for p in out["saved"]])
    api.call("drw.export", {"doc": d, "format": "pdf", "path": os.path.join(HERE, "Gearbox_drawing.pdf")})
    return api, d


if __name__ == "__main__":
    main()
