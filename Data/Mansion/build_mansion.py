"""マンション（外階段つき）のモデルを API で組み立てて Data/Mansion に保存する。

    cd core && python ../Data/Mansion/build_mansion.py

座標: X = 間口（東西）、Y = 高さ、Z = 奥行き（+Z が南 = バルコニー側、-Z が北 = 外廊下・外階段側）。単位 mm。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "core"))
from cad.api.rpc import Api  # noqa: E402
from cad.doc.workspace import Workspace  # noqa: E402

FLOORS = 6          # 階数
H = 3000            # 階高
BAY = 6000          # 住戸の間口
UNITS = 4           # 1 フロアの住戸数
DEPTH = 10000       # 奥行き
W = BAY * UNITS     # 間口 24000
ROOF = FLOORS * H   # 屋上の床 18000
PARAPET = 1000      # パラペットの高さ
BAL = 1800          # バルコニーの出
COR = 1400          # 外廊下の幅

# 階段（1 層分）: 蹴上 9 段 × 2、踏面 250
RISERS = 9
R = H / 2 / RISERS
T = 250
RUN = T * (RISERS - 1)       # 2000
X_L0 = W                     # 階の踊り場（床）: x 24000..25200
X_F0 = W + 1200              # 階段の始まり
X_F1 = X_F0 + RUN            # 27200
X_L1 = X_F1 + 1200           # 中間踊り場の端 28400
Z_A = -DEPTH                 # 1 つ目の階段（上り）: z -11300..-10000
Z_MID = -DEPTH - 1300        # 2 つ目の階段: z -12600..-11300（境に手すり壁）
Z_END = Z_MID - 1300         # -12600
SLAB = 200
WALL = 150
RAIL = 1100

COLORS = {
    "concrete": "#ece8e1", "frame": "#f7f6f2", "dark": "#2e3a47", "glass": "#8fb4cf", "door": "#7a5638",
    "stair": "#c9c4bb", "rail": "#dcd7ce", "steel": "#55606b", "ground": "#6f8f55", "path": "#cbc2b2",
    "trunk": "#6b4a32", "leaf": "#4e7d3c", "unit": "#9aa3ab",
}


def part_builder(api, name):
    d = api.call("doc.new", {"name": name})["doc"]
    call = lambda m, **p: api.call(m, {"doc": d, **p})

    def sketch(plane, draw):
        call("sketch.new", plane=plane)
        draw(call)
        return call("sketch.end")["tree"][-1]["id"]

    def feat(type, **p):
        if os.environ.get("TRACE"):
            print("  +", name, type, flush=True)
        st = call("feature.add", type=type, **p)
        fid = st["created"][0]
        item = next(x for x in st["tree"] if x["id"] == fid)
        if item["state"] not in ("ok",):
            print(f"  ! {name}/{fid}: {item['state']} {item.get('message', '')}")
        return fid

    def rects(*rs):
        return lambda c: [c("sketch.add", kind="rect", points=[[a, b], [e, f]]) for a, b, e, f in rs]

    def poly(*ps):
        return lambda c: [c("sketch.add", kind="polyline", points=[list(p) for p in pts]) for pts in ps]

    def color(c, *fids):
        call("appearance.set", color=COLORS[c], features=list(fids))

    return d, call, sketch, feat, rects, poly, color


def stair_profile(x0, y0, dx):
    """階段の側面の輪郭（Front 平面系 = (x, y)）。x0, y0 から dx（+1 / -1）の向きに 9 段上がる。下は斜めの段裏（厚さ 200）。"""
    pts = [(x0, y0)]
    for i in range(1, RISERS):
        pts += [(x0 + dx * T * (i - 1), y0 + i * R), (x0 + dx * T * i, y0 + i * R)]
    xe = x0 + dx * RUN
    pts += [(xe, y0 + RISERS * R), (xe, y0 + RISERS * R - SLAB), (x0, y0 - SLAB)]
    return pts


# ---------------------------------------------------------------- 住棟
def build_building(api):
    d, call, sketch, feat, rects, poly, color = part_builder(api, "Building")
    call("variable.set", name="floor_h", expr=str(H))
    call("variable.set", name="floors", expr=str(FLOORS))
    call("variable.set", name="bay", expr=str(BAY))
    # 1. 本体（Top 平面 = (x, -z)）
    s = sketch({"plane": "Top"}, rects((0, 0, W, DEPTH)))
    body = feat("extrude", sketch=s, distance=ROOF + PARAPET)
    call("feature.edit", id=body, key="distance", expr=f"floor_h * floors + {PARAPET}")
    # 2. 屋上のくぼみ（パラペットを残す）
    top = feat("plane", base={"plane": "Top"}, offset=ROOF + PARAPET)
    s = sketch({"feature": top}, rects((250, 250, W - 250, DEPTH - 250)))
    roof = feat("extrude", sketch=s, distance=PARAPET, op="cut")
    # 3. 南面の掃き出し窓（Front 平面 z = 0 から奥へ 350）を 4 戸 × 6 階
    s = sketch({"plane": "Front"}, rects((900, 250, BAY - 900, 2500)))
    win = feat("extrude", sketch=s, distance=350, op="cut")
    pw = feat("lpattern", seeds=[win], direction={"axis": "X"}, spacing=BAY, count=UNITS)
    call("feature.set", id=pw, key="direction2", value={"axis": "Y"})
    call("feature.edit", id=pw, key="spacing2", value=H)
    call("feature.edit", id=pw, key="count2", value=FLOORS)
    call("feature.edit", id=pw, key="spacing", expr="bay")
    call("feature.edit", id=pw, key="spacing2", expr="floor_h")
    # 4. 北面（z = -10000）: 玄関ドアと廊下側の窓
    back = feat("plane", base={"plane": "Front"}, offset=DEPTH, reverse=True)
    s = sketch({"feature": back}, rects((BAY - 1500, 60, BAY - 550, 2200)))
    door = feat("extrude", sketch=s, distance=200, op="cut", reverse=True)
    s = sketch({"feature": back}, rects((800, 1000, 3200, 2300)))
    bwin = feat("extrude", sketch=s, distance=250, op="cut", reverse=True)
    # パターンの複製は最初の種の色を引き継ぐので、色の違うドアと窓は別のパターンにする
    pb = []
    for seed in (door, bwin):
        q = feat("lpattern", seeds=[seed], direction={"axis": "X"}, spacing=BAY, count=UNITS)
        call("feature.set", id=q, key="direction2", value={"axis": "Y"})
        call("feature.edit", id=q, key="spacing2", value=H)
        call("feature.edit", id=q, key="count2", value=FLOORS)
        pb.append(q)
    # 6. バルコニー: 床スラブ + 立ち上がり（L 字）とガラスの手すり。2〜6 階
    s = sketch({"plane": "Right"}, poly([(100, H - SLAB), (-BAL, H - SLAB), (-BAL, H + 450), (-BAL + WALL, H + 450),
                                         (-BAL + WALL, H), (100, H)]))
    bal = feat("extrude", sketch=s, distance=W)
    s = sketch({"plane": "Right"}, rects((-BAL + 60, H + 450, -BAL + 90, H + RAIL)))
    glass = feat("extrude", sketch=s, distance=W)
    pbal = feat("lpattern", seeds=[bal], direction={"axis": "Y"}, spacing=H, count=FLOORS - 1)
    pglass = feat("lpattern", seeds=[glass], direction={"axis": "Y"}, spacing=H, count=FLOORS - 1)
    # 7. 外廊下: 床スラブ + 腰壁（L 字）。2〜6 階
    s = sketch({"plane": "Right"}, poly([(DEPTH - 100, H - SLAB), (DEPTH + COR, H - SLAB), (DEPTH + COR, H + RAIL + 100),
                                         (DEPTH + COR - WALL, H + RAIL + 100), (DEPTH + COR - WALL, H), (DEPTH - 100, H)]))
    cor = feat("extrude", sketch=s, distance=W)
    pcor = feat("lpattern", seeds=[cor], direction={"axis": "Y"}, spacing=H, count=FLOORS - 1)
    # 8. 外階段の柱と屋根（階段ユニットは別の部品）
    cx, z1, z2 = X_L1 + WALL, -Z_A - WALL, -Z_END + WALL           # 柱は階段の腰壁の外
    s = sketch({"plane": "Top"}, rects((cx, z1 - 400, cx + 400, z1), (cx, z2, cx + 400, z2 + 400),
                                       (X_L0, z2, X_L0 + 400, z2 + 400)))
    col = feat("extrude", sketch=s, distance=ROOF + 3000)
    sroof = feat("plane", base={"plane": "Top"}, offset=ROOF + 2800)
    s = sketch({"feature": sroof}, rects((X_L0 - 300, z1 - 700, cx + 700, z2 + 700)))
    canopy = feat("extrude", sketch=s, distance=250)
    # 9. 屋上の室外機（3 × 4）と塔屋
    rf = feat("plane", base={"plane": "Top"}, offset=ROOF)
    s = sketch({"feature": rf}, rects((1500, 3000, 2700, 3700)))
    ac = feat("extrude", sketch=s, distance=900)
    pac = feat("lpattern", seeds=[ac], direction={"axis": "X"}, spacing=2000, count=4)
    call("feature.set", id=pac, key="direction2", value={"axis": "Z"})
    call("feature.edit", id=pac, key="spacing2", value=1500)
    call("feature.edit", id=pac, key="count2", value=3)
    s = sketch({"feature": rf}, rects((15000, 2500, 21000, 7500)))
    tower = feat("extrude", sketch=s, distance=3200)
    # 10. 東西の妻壁の縦長の窓（東 = Right 平面から x = 24000 へ、西 = x = 0。各階）
    east = feat("plane", base={"plane": "Right"}, offset=W)
    s = sketch({"feature": east}, rects((7600, 900, 8400, 2400), (4600, 900, 5400, 2400)))   # Right 平面 = (-z, y)
    ew = feat("extrude", sketch=s, distance=250, op="cut")
    s = sketch({"plane": "Right"}, rects((7600, 900, 8400, 2400), (4600, 900, 5400, 2400), (1600, 900, 2400, 2400)))
    ww = feat("extrude", sketch=s, distance=250, op="cut", reverse=True)
    pside = feat("lpattern", seeds=[ew, ww], direction={"axis": "Y"}, spacing=H, count=FLOORS)
    color("dark", ew, ww, pside)
    # 11. 1 階のエントランスの庇（北面の中央）
    epl = feat("plane", base={"plane": "Top"}, offset=2500)
    s = sketch({"feature": epl}, rects((BAY * 2 - 1800, DEPTH, BAY * 2 + 1800, DEPTH + 2400)))
    ent = feat("extrude", sketch=s, distance=200)

    for fid, nm in ((body, "躯体"), (top, "屋上面"), (roof, "パラペット"), (win, "掃き出し窓"), (pw, "掃き出し窓の配列"),
                    (back, "北面"), (door, "玄関ドア"), (bwin, "廊下側の窓"), (pb[0], "玄関ドアの配列"), (pb[1], "廊下側の窓の配列"),
                    (bal, "バルコニー"), (glass, "ガラス手すり"), (pbal, "バルコニーの配列"), (pglass, "ガラス手すりの配列"),
                    (cor, "外廊下"), (pcor, "外廊下の配列"), (col, "階段の柱"), (sroof, "階段の屋根の高さ"), (canopy, "階段の屋根"),
                    (rf, "屋上の床"), (ac, "室外機"), (pac, "室外機の配列"), (tower, "塔屋"), (east, "東の妻壁"),
                    (ew, "東の窓"), (ww, "西の窓"), (pside, "妻壁の窓の配列"), (epl, "庇の高さ"), (ent, "エントランスの庇")):
        call("feature.rename", id=fid, name=nm)
    color("concrete", body, roof, tower)
    color("dark", win, pw, bwin, pb[1])
    color("door", door, pb[0])
    color("frame", bal, pbal, cor, pcor)
    color("glass", glass, pglass)
    color("steel", col, canopy, ent)
    color("unit", ac, pac)
    return d


# ---------------------------------------------------------------- 南面の外枠（袖壁と最上部の梁。住棟とは別の部品）
def build_frame(api):
    d, call, sketch, feat, rects, poly, color = part_builder(api, "Frame")
    # 5. 南面の外枠: 戸境の壁（地面から屋上まで）を 5 枚と、最上部の梁
    s = sketch({"plane": "Top"}, rects((-150, -BAL - 100, 150, 100)))
    fin = feat("extrude", sketch=s, distance=ROOF + PARAPET + 200)
    pf = feat("lpattern", seeds=[fin], direction={"axis": "X"}, spacing=BAY, count=UNITS + 1)
    left = feat("plane", base={"plane": "Right"}, offset=150, reverse=True)
    # Right 平面 = (-z, y)
    s = sketch({"feature": left}, rects((100, ROOF, -BAL - 50, ROOF + PARAPET + 100)))
    beam = feat("extrude", sketch=s, distance=W + 300)
    for fid, nm in ((fin, "袖壁"), (pf, "袖壁の配列"), (left, "西端"), (beam, "最上部の梁")):
        call("feature.rename", id=fid, name=nm)
    color("frame", fin, pf, beam)
    return d


# ---------------------------------------------------------------- 階段ユニット（1 層分。床から上の階の踊り場まで）
def build_stair(api):
    d, call, sketch, feat, rects, poly, color = part_builder(api, "StairUnit")
    pa = feat("plane", base={"plane": "Front"}, offset=-Z_A, reverse=True)       # z = -10000
    pm = feat("plane", base={"plane": "Front"}, offset=-Z_MID, reverse=True)     # z = -11300（2 本の階段の境）
    # 1 つ目の階段（+X へ上る）と中間踊り場の手前半分、上の階の踊り場の手前半分。踊り場を同じ輪郭に入れるのは、
    # 段と踊り場を別の押し出しにして端面で接するとカーネルの和が後で失敗するため
    st = stair_profile(X_F0, 0, +1)[:-2]
    prof_a = st + [(X_L1, H / 2), (X_L1, H / 2 - SLAB), (X_F1, H / 2 - SLAB), (X_F0, -SLAB)]
    s = sketch({"feature": pa}, lambda c: (c("sketch.add", kind="polyline", points=[list(p) for p in prof_a]),
                                          c("sketch.add", kind="rect", points=[[X_L0, H - SLAB], [X_F0, H]])))
    fa = feat("extrude", sketch=s, distance=Z_A - Z_MID, reverse=True)
    # 2 つ目の階段（-X へ上る）と踊り場の奥半分
    sb = stair_profile(X_F1, H / 2, -1)[:-2]
    prof_b = [(X_L1, H / 2 - SLAB), (X_L1, H / 2)] + sb + [(X_L0, H), (X_L0, H - SLAB), (X_F0, H - SLAB), (X_F1, H / 2 - SLAB)]
    s = sketch({"feature": pm}, poly(prof_b))
    fb = feat("extrude", sketch=s, distance=Z_MID - Z_END, reverse=True)
    # 腰壁（厚さ 150）。階段・踊り場に 50 食い込ませ、下端は段裏より 50 下げる（段裏の斜面と同じ面で接すると
    # カーネルの和が失敗するため）
    o = 50
    e = 0           # 壁の端を段・踊り場の端面からずらす量
    lo = lambda y: y - SLAB - o

    def zplane(z):
        return feat("plane", base={"plane": "Front"}, offset=-z, reverse=True)
    # 外側（z = -12600 の外）: 上の階の踊り場から 2 つ目の階段・中間踊り場に沿って
    s = sketch({"feature": zplane(Z_END + o)}, poly([(X_L0 + e, lo(H)), (X_F0, lo(H)), (X_F1, lo(H / 2)), (X_L1 + WALL, lo(H / 2)),
                                                     (X_L1 + WALL, H / 2 + RAIL), (X_F1, H / 2 + RAIL), (X_F0, H + RAIL),
                                                     (X_L0 + e, H + RAIL)]))
    wout = feat("extrude", sketch=s, distance=WALL + o, reverse=True)
    # 中間踊り場の端（x = 28400 の外）
    s = sketch({"feature": pa}, rects((X_L1 - o, lo(H / 2), X_L1 + WALL, H / 2 + RAIL)))
    wend = feat("extrude", sketch=s, distance=Z_A - Z_END, reverse=True)
    # 手前（z = -10000 の外）: 1 つ目の階段に沿って、と上の階の踊り場
    s = sketch({"feature": zplane(Z_A - o)}, lambda c: (
        c("sketch.add", kind="polyline", points=[[X_F0 + e, lo(0) + e * 0.75], [X_F1, lo(H / 2)], [X_L1 + WALL, lo(H / 2)],
                                                 [X_L1 + WALL, H / 2 + RAIL], [X_F1, H / 2 + RAIL], [X_F0 + e, RAIL + e * 0.75]]),
        c("sketch.add", kind="rect", points=[[X_L0 + e, lo(H)], [X_F0 - e, H + RAIL]])))
    wfront = feat("extrude", sketch=s, distance=WALL + o)
    # 2 つの階段の間の手すり壁（中央。厚さ 200）
    s = sketch({"feature": zplane(Z_MID + 100)}, poly([(X_F0 + e, lo(H) - e * 0.75), (X_F1 - e, lo(H / 2) + e * 0.75), (X_F1 - e, H / 2 + RAIL + e * 0.75),
                                                         (X_F0 + e, H + RAIL - e * 0.75)]))
    wmid = feat("extrude", sketch=s, distance=200, reverse=True)
    for fid, nm in ((pa, "階段の手前の面"), (pm, "階段の中央の面"), (fa, "上り階段1と踊り場"), (fb, "上り階段2と踊り場"),
                    (wout, "腰壁（外側）"), (wend, "腰壁（踊り場の端）"), (wfront, "腰壁（手前）"), (wmid, "手すり壁（中央）")):
        call("feature.rename", id=fid, name=nm)
    color("stair", fa, fb)
    color("rail", wout, wend, wfront, wmid)
    return d


# ---------------------------------------------------------------- 敷地
def build_site(api):
    d, call, sketch, feat, rects, poly, color = part_builder(api, "Site")
    # 地面（Top 平面 = (x, -z)。z = -22000..14000、x = -8000..40000）
    s = sketch({"plane": "Top"}, rects((-8000, -14000, 40000, 22000)))
    g = feat("extrude", sketch=s, distance=400, reverse=True)
    # 通路（北側のアプローチと南の庭の園路）
    s = sketch({"plane": "Top"}, rects((X_L0 - 400, DEPTH + COR, X_L1 + 1200, 16000),
                                       (BAY * 2 - 1800, DEPTH, BAY * 2 + 1800, 16000),
                                       (-2000, -7000, W + 2000, -3500)))
    p = feat("extrude", sketch=s, distance=30)
    s = sketch({"plane": "Top"}, rects((-8000, 16000, 40000, 19000)))
    road = feat("extrude", sketch=s, distance=30)
    for fid, nm in ((g, "地面"), (p, "園路・アプローチ"), (road, "前面道路")):
        call("feature.rename", id=fid, name=nm)
    color("ground", g)
    color("path", p)
    call("appearance.set", color="#4a4d52", feature=road)
    return d


# ---------------------------------------------------------------- 木（回転）
def build_tree(api):
    d, call, sketch, feat, rects, poly, color = part_builder(api, "Tree")

    def trunk(c):
        c("sketch.add", kind="line", points=[[0, -100], [0, 3000]], construction=True)
        c("sketch.add", kind="polyline", points=[[0, 0], [180, 0], [120, 2400], [0, 2400]])
    s = sketch({"plane": "Front"}, trunk)
    t = feat("revolve", sketch=s)

    def crown(c):
        c("sketch.add", kind="line", points=[[0, 0], [0, 7000]], construction=True)
        st = c("sketch.add", kind="arc3", points=[[0, 1600], [0, 6200], [1900, 3700]])
        e = next(x for x in st["sketch"]["entities"] if x["id"] == st["created"][0])
        c("sketch.add", kind="line", points=[e["start"], e["end"]])
    s = sketch({"plane": "Front"}, crown)
    cr = feat("revolve", sketch=s)
    call("feature.rename", id=t, name="幹"); call("feature.rename", id=cr, name="樹冠")
    color("trunk", t)
    color("leaf", cr)
    return d


def main():
    out = HERE
    t0 = time.time()
    api = Api(Workspace("usd_brep"))
    docs = {}
    for name, fn in (("Building", build_building), ("Frame", build_frame), ("StairUnit", build_stair), ("Site", build_site), ("Tree", build_tree)):
        t = time.time()
        docs[name] = fn(api)
        print(f"{name}: {time.time() - t:.1f} 秒")
    a = api.call("asm.new", {"name": "Mansion"})["doc"]
    call = lambda m, **p: api.call(m, {"doc": a, **p})
    call("asm.add", part=docs["Building"], position=[0, 0, 0])
    call("asm.add", part=docs["Site"], position=[0, 0, 0])
    call("asm.add", part=docs["Frame"], position=[0, 0, 0])
    call("asm.add", part=docs["StairUnit"], position=[0, 0, 0])
    call("asm.add", part=docs["Tree"], position=[1500, 0, 10000])
    st = call("doc.state", mesh=False)
    comps = [c["id"] for c in st["components"]]
    b, site, frame, stair, tree = comps
    # 敷地・外枠・階段は住棟の標準平面にそろえる（一致の合致）
    for comp in (site, frame, stair):
        call("asm.mate", type="coincident", a=[b, "Top"], b=[comp, "Top"], aligned=True)
        call("asm.mate", type="coincident", a=[b, "Front"], b=[comp, "Front"], aligned=True)
        call("asm.mate", type="coincident", a=[b, "Right"], b=[comp, "Right"], aligned=True)
    # 階段を 6 層に（部品の直線パターン）、木を南の庭に 5 本・北の通路の脇に 3 本
    call("asm.pattern", comp=stair, kind="linear", axis="y", count=FLOORS, spacing=H)
    call("asm.mate", type="coincident", a=[site, "Top"], b=[tree, "Top"], aligned=True)
    call("asm.mate", type="distance", a=[b, "Front"], b=[tree, "Front"], value=10000)   # 南の庭（園路の外側）
    call("asm.mate", type="distance", a=[b, "Right"], b=[tree, "Right"], value=1500)
    call("asm.pattern", comp=tree, kind="linear", axis="x", count=5, spacing=BAY * 1.0 - 750)
    st = call("doc.state", mesh=False)
    print("合致:", st["solve"], [(m["id"], m["type"], m["state"]) for m in st["mates"]][:8])
    for name, did in docs.items():                       # パートは決まった名前で上書き保存
        api.call("doc.save", {"doc": did, "path": os.path.join(out, name + ".usda")})
    res = api.call("doc.save", {"doc": a, "path": os.path.join(out, "Mansion_asm.usda")})
    print("保存:", res["saved"], res.get("warnings"))
    print(f"合計 {time.time() - t0:.1f} 秒")


if __name__ == "__main__":
    main()
