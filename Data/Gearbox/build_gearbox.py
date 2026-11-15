"""遊星歯車減速機（モジュール 2、太陽 18 歯・遊星 15 歯 × 3・内歯車 48 歯、減速比 1 + 48/18 = 3.67）を API で組み立てて
Data/Gearbox に保存する。組立状態（Gearbox_asm）と、軸方向にばらした分解図（Gearbox_exploded）の 2 つのアセンブリを作る。

    cd core && python ../Data/Gearbox/build_gearbox.py

座標: 歯車の軸 = Z（入力軸が -Z 側、出力軸が +Z 側）。歯車の歯幅は z = 1..15、内歯車は z = 0..16。
歯形はインボリュート曲線を点列にして折れ線で描く。遊星 3 個とボルト 6 本は、1 個置いて円形パターン（部品）で増やす
（太陽・内歯車の歯数が 3 の倍数なので、120° 回しても噛み合いが保たれる）。
"""
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "core"))
from cad.api.rpc import Api  # noqa: E402
from cad.doc.workspace import Workspace  # noqa: E402

COLORS = {
    "housing": "#34465e", "ring": "#9aa3ad", "sun": "#d9a441", "planet": "#4f8fd0", "carrier": "#d8dde3",
    "steel": "#c5cad1", "seal": "#22252a", "bolt": "#1c1e22", "key": "#e0b040",
}

M = 2.0                                  # モジュール
Z_SUN, Z_PLANET, Z_RING = 18, 15, 48     # 歯数（Z_RING = Z_SUN + 2 * Z_PLANET）
A = M * (Z_SUN + Z_PLANET) / 2           # 太陽と遊星の軸間距離 = 33
PA = math.radians(20)                    # 圧力角
BACKLASH = 0.08                          # 歯厚を薄くする量（片側の歯車あたり）
R_OUT = 66.0                             # ハウジングの外半径
R_BOLT = 58.0                            # ボルトのピッチ円の半径
N_BOLT = 6
RATIO = 1 + Z_RING / Z_SUN


def gear_points(z, ra, rf, internal=False, n=5):
    """インボリュート歯形の点列（反時計回り。角度 0 に歯の中心）。ra = 歯先円、rf = 歯底円の半径。
    internal=True は内歯車の「穴」の形（穴の歯 = 内歯車の歯溝なので、歯厚をバックラッシ分だけ厚くする）。"""
    r = M * z / 2
    rb = r * math.cos(PA)
    inv = lambda R: (lambda p: math.tan(p) - p)(math.acos(min(1.0, rb / R)))
    half = math.pi / (2 * z) + (1 if internal else -1) * BACKLASH / (2 * r)
    theta = lambda R: half + inv(r) - inv(max(R, rb))             # 半径 R での歯厚の半角（基礎円より内側は放射状）
    r0 = max(rf, rb)
    radii = ([rf] if rf < rb - 1e-9 else []) + [r0 + (ra - r0) * i / (n - 1) for i in range(n)]
    pts = []
    for k in range(z):
        c = 2 * math.pi * k / z
        for R in radii:                                           # 右の歯面（歯底 → 歯先）
            pts.append((R * math.cos(c - theta(R)), R * math.sin(c - theta(R))))
        for R in reversed(radii):                                 # 左の歯面（歯先 → 歯底）
            pts.append((R * math.cos(c + theta(R)), R * math.sin(c + theta(R))))
        cm = c + math.pi / z                                      # 歯底の中央（歯底円の上）
        pts.append((rf * math.cos(cm), rf * math.sin(cm)))
    return pts


def ring_xy(r, n, phase=0.0):
    return [(r * math.cos(phase + 2 * math.pi * k / n), r * math.sin(phase + 2 * math.pi * k / n)) for k in range(n)]


class Builder:
    """パート 1 つを組み立てる道具。形はすべて座標軸に沿った押し出し。

    axis = 押し出す向き（"x" / "y" / "z"）、a0..a1 = その向きの範囲。断面は残りの 2 軸の座標で与える
    （y → (x, z)、z → (x, y)、x → (y, z)）。カットは a1 側の平面から a0 まで掘る。
    断面 = circles [(中心 p, q, 半径)]・polys [点列]（閉じた折れ線）・slots [(中心 1, 中心 2, 半径)]・rects [(p0, q0, p1, q1, 角の丸み)]。
    輪郭が入れ子なら内側は穴になる。"""
    BASE = {"x": "Right", "y": "Top", "z": "Front"}

    def __init__(self, api, name):
        self.api, self.name = api, name
        self.doc = api.call("doc.new", {"name": name})["doc"]
        self.planes = {}

    def call(self, m, **p):
        return self.api.call(m, {"doc": self.doc, **p})

    def plane(self, axis, off):
        base = self.BASE[axis]
        if abs(off) < 1e-9:
            return {"plane": base}
        key = (axis, round(off, 6))
        if key not in self.planes:
            st = self.call("feature.add", type="plane", base={"plane": base}, offset=abs(off), reverse=off < 0)
            self.planes[key] = st["created"][0]
            self.call("feature.rename", id=self.planes[key], name=f"{base} {off:+g}")
        return {"feature": self.planes[key]}

    @staticmethod
    def uv(axis, a, b):
        """断面の座標 (a, b) → スケッチの (u, v)。Top: (x, -z)、Front: (x, y)、Right: (-z, y)。"""
        return {"y": (a, -b), "z": (a, b), "x": (-b, a)}[axis]

    def solid(self, axis, a0, a1, name=None, color=None, op="add", circles=(), polys=(), slots=(), rects=()):
        self.call("sketch.new", plane=self.plane(axis, a1 if op == "cut" else a0))
        for p, q, r in circles:
            u, v = self.uv(axis, p, q)
            self.call("sketch.add", kind="circle", points=[[u, v], [u + r, v]])
        for pts in polys:
            self.call("sketch.add", kind="polyline", points=[list(self.uv(axis, p, q)) for p, q in pts], closed=True)
        for c1, c2, r in slots:
            (u1, v1), (u2, v2) = self.uv(axis, *c1), self.uv(axis, *c2)
            self.call("sketch.add", kind="slot", points=[[u1, v1], [u2, v2], [u1 + r, v1 + r]], radius=r)
        for p0, q0, p1, q1, r in rects:
            (u0, v0), (u1, v1) = self.uv(axis, p0, q0), self.uv(axis, p1, q1)
            L = self.call("sketch.add", kind="rect", points=[[min(u0, u1), min(v0, v1)], [max(u0, u1), max(v0, v1)]])["created"]
            for i in range(4 if r > 0 else 0):
                self.call("sketch.fillet", radius=r, l1=L[i], l2=L[(i + 1) % 4])
        s = self.call("sketch.end")["tree"][-1]["id"]
        st = self.call("feature.add", type="extrude", sketch=s, distance=a1 - a0, op=op)
        fid = st["created"][0]
        item = next(x for x in st["tree"] if x["id"] == fid)
        if item["state"] != "ok":
            print(f"  ! {self.name}/{name or fid}: {item['state']} {item.get('message', '')}", flush=True)
        if name:
            self.call("feature.rename", id=fid, name=name)
        if color:
            self.call("appearance.set", color=COLORS.get(color, color), feature=fid)
        return fid


def build_rear_housing(api):
    """入力側のハウジング: 取り付けフランジ（140 角）・胴・軸受 6001 のポケット・ボルトのねじ穴。"""
    b = Builder(api, "RearHousing")
    b.solid("z", -22, -14, "取り付けフランジ", "housing", rects=[(-70, -70, 70, 70, 14)])
    b.solid("z", -14, 0, "胴", "housing", circles=[(0, 0, R_OUT)])
    b.solid("z", -26, -22, "インロー", "housing", circles=[(0, 0, 30)])
    b.solid("z", -22, -14, "取り付け穴", op="cut", circles=[(sx * 58, sy * 58, 4.5) for sx in (1, -1) for sy in (1, -1)])
    b.solid("z", -26, 0, "軸の穴", op="cut", circles=[(0, 0, 8)])
    b.solid("z", -9, 0, "軸受のポケット（6001）", op="cut", circles=[(0, 0, 14)])
    b.solid("z", -14, 0, "ボルトのねじ穴", op="cut", circles=[(x, y, 3.1) for x, y in ring_xy(R_BOLT, N_BOLT)])
    return b.doc


def build_ring_gear(api):
    """内歯車（48 歯）。ハウジングの胴を兼ねる。"""
    b = Builder(api, "RingGear")
    r = M * Z_RING / 2
    hole = gear_points(Z_RING, r + 1.25 * M, r - M, internal=True)
    b.solid("z", 0, 16, "内歯車", "ring", circles=[(0, 0, R_OUT)], polys=[hole])
    b.solid("z", 0, 16, "ボルトの穴", op="cut", circles=[(x, y, 3.3) for x, y in ring_xy(R_BOLT, N_BOLT)])
    return b.doc


def build_sun(api):
    """太陽歯車（18 歯）と入力軸（φ12、キー溝 4 × 2.5）。"""
    b = Builder(api, "SunGear")
    r = M * Z_SUN / 2
    b.solid("z", 1, 15, "太陽歯車", "sun", polys=[gear_points(Z_SUN, r + M, r - 1.25 * M)])
    b.solid("z", -1, 1, "ボス", "sun", circles=[(0, 0, 8)])
    b.solid("z", -50, -1, "入力軸", "steel", circles=[(0, 0, 6)])
    b.solid("y", 3.5, 6, "キー溝", op="cut", slots=[((0, -46), (0, -34), 2)])
    return b.doc


def build_planet(api):
    """遊星歯車（15 歯、穴 φ10）。原点 = 歯車の中心。"""
    b = Builder(api, "PlanetGear")
    r = M * Z_PLANET / 2
    b.solid("z", 1, 15, "遊星歯車", "planet", polys=[gear_points(Z_PLANET, r + M, r - 1.25 * M)], circles=[(0, 0, 5)])
    return b.doc


def build_carrier(api):
    """キャリア: 円板・遊星のピン 3 本・出力軸（φ20、キー溝 6 × 3.5）。"""
    b = Builder(api, "Carrier")
    b.solid("z", 17, 23, "円板", "carrier", circles=[(0, 0, 48)])
    b.solid("z", 1, 17, "遊星のピン", "steel", circles=[(x, y, 5) for x, y in ring_xy(A, 3)])
    b.solid("z", 17, 23, "肉抜き", op="cut", circles=[(x, y, 9) for x, y in ring_xy(30, 3, math.pi / 3)])
    b.solid("z", 23, 30, "軸の段", "carrier", circles=[(0, 0, 12)])
    b.solid("z", 30, 70, "出力軸", "steel", circles=[(0, 0, 10)])
    b.solid("y", 6.5, 10, "キー溝", op="cut", slots=[((0, 49), (0, 63), 3)])
    return b.doc


def build_cover(api):
    """出力側のカバー: 縁・板・軸受 6004 のボス・リブ 6 本・ボルトの座ぐり。"""
    b = Builder(api, "Cover")
    b.solid("z", 16, 24, "縁", "housing", circles=[(0, 0, R_OUT), (0, 0, 50)])
    b.solid("z", 24, 28, "板", "housing", circles=[(0, 0, R_OUT)])
    b.solid("z", 28, 42, "軸受のボス", "housing", circles=[(0, 0, 26)])
    ribs = []
    for k in range(N_BOLT):                                       # ボルトの間（30° + 60° k）に放射状のリブ
        a = math.pi / 6 + 2 * math.pi * k / N_BOLT
        c, s = math.cos(a), math.sin(a)
        ribs.append([(r * c - t * s, r * s + t * c) for r, t in ((24, -2.5), (62, -2.5), (62, 2.5), (24, 2.5))])
    b.solid("z", 28, 34, "リブ", "housing", polys=ribs)
    b.solid("z", 24, 42, "軸の穴", op="cut", circles=[(0, 0, 13)])
    b.solid("z", 30, 42, "軸受のポケット（6004）", op="cut", circles=[(0, 0, 21)])
    b.solid("z", 16, 28, "ボルトの穴", op="cut", circles=[(x, y, 3.3) for x, y in ring_xy(R_BOLT, N_BOLT)])
    b.solid("z", 21.5, 28, "座ぐり", op="cut", circles=[(x, y, 5.5) for x, y in ring_xy(R_BOLT, N_BOLT)])
    return b.doc


def build_bearing(api, name, d, D, z0, z1):
    """シール付きの玉軸受（内径 d・外径 D）。"""
    b = Builder(api, name)
    t = (D - d) / 2
    b.solid("z", z0, z1, "外輪", "steel", circles=[(0, 0, D / 2), (0, 0, D / 2 - 0.28 * t)])
    b.solid("z", z0, z1, "内輪", "steel", circles=[(0, 0, d / 2 + 0.28 * t), (0, 0, d / 2)])
    b.solid("z", z0 + 0.6, z1 - 0.6, "シール", "seal", circles=[(0, 0, D / 2 - 0.2 * t), (0, 0, d / 2 + 0.2 * t)])
    return b.doc


def build_bolt(api):
    """六角穴付きボルト M6（首下 33.5）。原点 = 軸の上。"""
    b = Builder(api, "Bolt_M6")
    b.solid("z", -12, 21.5, "ねじ部", "bolt", circles=[(0, 0, 3)])
    b.solid("z", 21.5, 27.5, "頭", "bolt", circles=[(0, 0, 5)])
    hexagon = [(2.887 * math.cos(math.pi / 3 * k), 2.887 * math.sin(math.pi / 3 * k)) for k in range(6)]   # 二面幅 5
    b.solid("z", 24.5, 27.5, "六角穴", op="cut", polys=[hexagon])
    return b.doc


def build_key(api, name, w, h, y0, z0, z1):
    """平行キー（幅 w・高さ h、両端が丸い）。"""
    b = Builder(api, name)
    b.solid("y", y0, y0 + h, "キー", "key", slots=[((0, z0 + w / 2), (0, z1 - w / 2), w / 2)])
    return b.doc


PARTS = [   # 名前, 作る関数, 置く位置 (x, y), 円形パターンの個数, 分解図でのずらし (dy, dz)
    ("RearHousing", build_rear_housing, (0, 0), 1, (0, -110)),
    ("Bearing_6001", lambda a: build_bearing(a, "Bearing_6001", 12, 28, -9, -1), (0, 0), 1, (0, -72)),
    ("SunGear", build_sun, (0, 0), 1, (0, -12)),
    ("Key_4x4", lambda a: build_key(a, "Key_4x4", 4, 4, 3.5, -48, -32), (0, 0), 1, (14, -12)),
    ("RingGear", build_ring_gear, (0, 0), 1, (0, 22)),
    ("PlanetGear", build_planet, (A, 0), 3, (0, 78)),
    ("Carrier", build_carrier, (0, 0), 1, (0, 128)),
    ("Key_6x6", lambda a: build_key(a, "Key_6x6", 6, 6, 6.5, 46, 66), (0, 0), 1, (16, 128)),
    ("Cover", build_cover, (0, 0), 1, (0, 212)),
    ("Bearing_6004", lambda a: build_bearing(a, "Bearing_6004", 20, 42, 30, 42), (0, 0), 1, (0, 250)),
    ("Bolt_M6", build_bolt, (R_BOLT, 0), N_BOLT, (0, 302)),
]


def assemble(api, docs, name, exploded):
    a = api.call("asm.new", {"name": name})["doc"]
    for part, _, (x, y), count, (dy, dz) in PARTS:
        pos = [x, y + (dy if exploded else 0), dz if exploded else 0]
        st = api.call("asm.add", {"doc": a, "part": docs[part], "position": pos})
        cid = st["components"][-1]["id"]
        api.call("asm.fix", {"doc": a, "comp": cid, "value": True})
        if count > 1:
            api.call("asm.pattern", {"doc": a, "comp": cid, "kind": "circular", "axis": "z", "count": count})
    st = api.call("doc.state", {"doc": a, "mesh": False})
    print(f"{name}: 部品 {len(st['components'])} 個、{st['solve']}")
    return a


def main():
    t0 = time.time()
    api = Api(Workspace("usd_brep"))
    docs = {}
    for name, fn, *_ in PARTS:
        t = time.time()
        docs[name] = fn(api)
        print(f"{name}: {time.time() - t:.1f} 秒", flush=True)
    asm = assemble(api, docs, "Gearbox_asm", False)
    st = api.call("asm.interference", {"doc": asm})
    print("干渉:", st["checked"], "部品中", [(i["a"], i["b"], round(i["volume"], 1)) for i in st["interferences"]])
    exp = assemble(api, docs, "Gearbox_exploded", True)
    for name, did in docs.items():
        api.call("doc.save", {"doc": did, "path": os.path.join(HERE, name + ".usda")})
    api.call("doc.save", {"doc": asm, "path": os.path.join(HERE, "Gearbox_asm.usda")})
    api.call("doc.save", {"doc": exp, "path": os.path.join(HERE, "Gearbox_exploded.usda")})
    print(f"減速比 {RATIO:.3f}、合計 {time.time() - t0:.1f} 秒")
    return api, asm, exp, docs


if __name__ == "__main__":
    main()
