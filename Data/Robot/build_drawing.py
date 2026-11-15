"""人型ロボットの組立図（A3・1:4。正面図・右側面図・平面図・等角投影図、寸法、部品表）を作り、Data/Robot に保存して PDF に書き出す。

    cd core && python ../Data/Robot/build_drawing.py

寸法の相手はビューのスナップ点（端点・中点・円の中心）から、モデルの座標で探す。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "core"))
sys.path.insert(0, HERE)
from cad.api.rpc import Api  # noqa: E402
from cad.doc.workspace import Workspace  # noqa: E402
import build_robot as R  # noqa: E402

BOM = [  # 番号, 部品名, パート, 数量, 備考
    ("1", "胴", "Torso", 1, "骨盤・バッテリーを含む"),
    ("2", "頭", "Head", 1, ""),
    ("3", "サーボ", "Servo", 10, "両軸ホーン 24×40×20"),
    ("4", "足", "Foot", 2, "ゴムの足裏"),
    ("5", "すね", "Shin", 2, "t2 アルミ板"),
    ("6", "もも", "Thigh", 2, "t2 アルミ板"),
    ("7", "股の金具", "HipBracket", 2, "t2 アルミ板"),
    ("8", "上腕（左）", "UpperArm_L", 1, ""),
    ("9", "上腕（右）", "UpperArm_R", 1, ""),
    ("10", "前腕・手", "Forearm", 2, "グリッパー"),
]


def build(api, asm):
    st = api.call("drw.new", {"model": asm, "size": "A3", "scale": "1:4", "standard": False})
    d = st["doc"]
    def call(m, **p):
        t = time.time()
        r = api.call(m, {"doc": d, **p})
        if time.time() - t > 1:
            print(f"  {m}: {time.time() - t:.1f} 秒", flush=True)
        return r
    call("drw.sheet", title="小型人型ロボット 組立図")
    st = call("drw.view_add", orient="front", x=95, y=152)          # x, y = 図の中心
    F = st["views"][-1]["id"]
    st = call("drw.view_add", parent=F, side="right")
    S = st["views"][-1]["id"]
    st = call("drw.view_add", parent=F, side="up")
    T = st["views"][-1]["id"]
    st = call("drw.view_add", orient="iso", x=335, y=192)
    I = st["views"][-1]["id"]
    call("drw.view_set", id=I, scale=[1, 3])

    views = lambda: {v["id"]: v for v in call("doc.state")["views"]}
    vs = views()
    # 右側面図・平面図を正面図に寄せる（投影図はそろう方向にしか動かない）
    call("drw.view_set", id=S, x=vs[F]["x"] + 95, y=vs[S]["y"])
    call("drw.view_set", id=T, x=vs[T]["x"], y=vs[F]["y"] + 180)
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

    s = 1 / 4
    # 正面図: 全高・全幅・胴の幅・頭の幅・足の外側
    sole = snap(F, x=-68, y=0)
    dim(F, sole, snap(F, kinds=("center",), y=488, x=22, z=0), "v", -130 * s)
    dim(F, snap(F, x=-102, y=360), snap(F, x=102, y=360), "h", 122 + 16)
    dim(F, snap(F, x=-56, y=382), snap(F, x=56, y=382), "h", 122 + 9)
    dim(F, snap(F, kinds=("center",), x=-48, y=428), snap(F, kinds=("center",), x=48, y=428), "h", 122 + 23)
    dim(F, sole, snap(F, x=68, y=0), "h", -40 * s)
    # 右側面図: 関節の軸の高さ（足裏から）、足の長さ、奥行き
    base = snap(S, z=72, y=0)
    for i, y in enumerate((R.Y_ANKLE, R.Y_KNEE, R.Y_HIP, R.Y_SHOULDER)):
        c = snap(S, kinds=("center",), z=0, y=y)
        dim(S, base, c, "v", (110 + 45 * i) * s)
    dim(S, snap(S, z=-45, y=0), base, "h", -40 * s)
    dim(S, snap(S, z=-51, y=312), snap(S, z=72, y=0), "h", 520 * s)

    # 図の名前
    for vid, text in ((F, "正面図"), (S, "右側面図"), (T, "平面図"), (I, "等角投影図")):
        bb = views()[vid]["bbox"]
        call("drw.note_add", view=vid, text=text, pos=[(bb[0] + bb[2]) / 2, bb[1] - (7 if vid == T else 14)], h=4.0)
    # 部品表（用紙の座標で置く。注記は正面図に付ける）
    ox, oy = vs[F]["x"], vs[F]["y"]
    cols = [(18, "番号"), (40, "部品名"), (72, "パート"), (100, "数量"), (130, "備考")]
    x0, y0, dy = 232, 98, 5.0
    call("drw.note_add", view=F, text="部品表", pos=[x0 + 70 - ox, y0 + 7 - oy], h=4.0)
    rows = [tuple(c for _, c in cols)] + [(n, name, part, f"{q}", note or "—") for n, name, part, q, note in BOM]
    for r, row in enumerate(rows):
        for (cx, _), text in zip(cols, row):
            call("drw.note_add", view=F, text=text, pos=[x0 + cx - ox, y0 - r * dy - oy], h=3.0 if r else 3.2)
    st = call("doc.state")
    print("寸法:", [x["text"] for x in st["dims"] if x["kind"] != "note"])
    print("ビュー:", [(v["id"], v["x"], v["y"], [round(t) for t in v["bbox"]]) for v in st["views"]])
    return d


def main():
    api = Api(Workspace("usd_brep"))
    asm = api.call("doc.open", {"path": os.path.join(HERE, "Robot_asm.usda")})["doc"]
    d = build(api, asm)
    out = api.call("doc.save", {"doc": d, "path": os.path.join(HERE, "Robot_drawing.usda")})
    print("保存:", [os.path.basename(p) for p in out["saved"]])
    api.call("drw.export", {"doc": d, "format": "pdf", "path": os.path.join(HERE, "Robot_drawing.pdf")})
    return api, d


if __name__ == "__main__":
    main()
