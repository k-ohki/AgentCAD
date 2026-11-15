"""マンションの住戸 1 戸（2LDK、間口 6m × 奥行 10m）の間取りを API で組み立てて Data/Unit に保存する。

    cd core && python ../Data/Unit/build_unit.py

座標: X = 間口（西 0 → 東 6000）、Y = 高さ（床 = 0）、Z = 奥行き。平面の座標 p = -Z（南のバルコニー側 0 → 北の玄関側 10000）。
Top 平面のスケッチは (u, v) = (x, p) になる。
部品: Shell（床スラブ・壁・バルコニー）、Floors（床の仕上げ）、Fixtures（キッチン・浴室・トイレ・建具など）、Furniture（家具）。
平面図で開口が見えるように、ドア・窓の開口は天井まで抜く（窓の下は腰壁）。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "core"))
from cad.api.rpc import Api  # noqa: E402
from cad.doc.workspace import Workspace  # noqa: E402

W, D = 6000, 10000          # 間口・奥行き（外壁の外面）
T_EXT, T_IN = 150, 100      # 外壁・間仕切りの厚さ
H = 2400                    # 壁の高さ（天井高）
BAL = 1800                  # バルコニーの出

COLORS = {
    "wall": "#f4f1ea", "slab": "#b9b3a7", "wood": "#c9a47c", "tile": "#d5dadd", "doma": "#8f8b86", "balcony": "#cfcac0",
    "counter": "#f1ece4", "cabinet": "#8c6f52", "dark": "#2f3133", "fridge": "#dde2e6", "white": "#fbfbfa",
    "door": "#a77d55", "glass": "#9cc3dd", "sofa": "#557a8a", "table": "#86583a", "chair": "#5f4330", "tvboard": "#3a3a3a",
    "bed": "#7a5a42", "mattress": "#efece4", "pillow": "#d9d2c3", "rug": "#e4d8c3", "plant": "#4f7d3c", "pot": "#b5714a",
}


class Builder:
    """パート 1 つを組み立てる道具。box = Top 平面に平行な面のスケッチ（矩形）を上へ押し出す。"""

    def __init__(self, api, name):
        self.api, self.name = api, name
        self.doc = api.call("doc.new", {"name": name})["doc"]
        self.planes = {}

    def call(self, m, **p):
        return self.api.call(m, {"doc": self.doc, **p})

    def plane(self, y):
        """高さ y の水平な参照平面（0 は Top 平面）。"""
        if y == 0:
            return {"plane": "Top"}
        if y not in self.planes:
            st = self.call("feature.add", type="plane", base={"plane": "Top"}, offset=abs(y), reverse=y < 0)
            self.planes[y] = st["created"][0]
            self.call("feature.rename", id=self.planes[y], name=f"高さ {y:g}")
        return {"feature": self.planes[y]}

    def feat(self, type, name=None, color=None, **p):
        st = self.call("feature.add", type=type, **p)
        fid = st["created"][0]
        item = next(x for x in st["tree"] if x["id"] == fid)
        if item["state"] != "ok":
            print(f"  ! {self.name}/{name or fid}: {item['state']} {item.get('message', '')}")
        if name:
            self.call("feature.rename", id=fid, name=name)
        if color:
            self.call("appearance.set", color=COLORS.get(color, color), feature=fid)
        return fid

    def sketch(self, y, draw):
        self.call("sketch.new", plane=self.plane(y))
        draw(lambda m, **p: self.call(m, **p))
        return self.call("sketch.end")["tree"][-1]["id"]

    def box(self, rects, y0, h, name=None, color=None, op="add"):
        """rects = [(x0, p0, x1, p1), …]（重ならないこと）を高さ y0 から h だけ押し出す（op = cut なら y0 から下へ h 掘る）。"""
        if isinstance(rects[0], (int, float)):
            rects = [rects]
        s = self.sketch(y0, lambda c: [c("sketch.add", kind="rect", points=[[a, b], [e, f]]) for a, b, e, f in rects])
        return self.feat("extrude", name=name, color=color, sketch=s, distance=h, op=op)

    def cyl(self, centers, r, y0, h, name=None, color=None, op="add"):
        s = self.sketch(y0, lambda c: [c("sketch.add", kind="circle", points=[[x, p], [x + r, p]]) for x, p in centers])
        return self.feat("extrude", name=name, color=color, sketch=s, distance=h, op=op)


def wall_x(p0, p1, x0, x1, gaps=(), extend=50):
    """X 方向の壁（p0..p1 の厚さ、x0..x1 の長さ）を開口 gaps = [(a, b)] で分けた矩形の一覧。両端は extend だけ相手の壁へ食い込ませる。"""
    xs = [x0 - extend] + [v for g in sorted(gaps) for v in g] + [x1 + extend]
    return [(xs[i], p0, xs[i + 1], p1) for i in range(0, len(xs), 2)]


def wall_p(x0, x1, p0, p1, gaps=(), extend=50):
    ps = [p0 - extend] + [v for g in sorted(gaps) for v in g] + [p1 + extend]
    return [(x0, ps[i], x1, ps[i + 1]) for i in range(0, len(ps), 2)]


# 間取り（平面の座標。壁の位置は面の値）
X_C0, X_C1 = 2700, 3700         # 廊下（壁の内法 1000）
P_LDK = 5500                    # LDK の北の間仕切り（5500..5600）
P_BATH = 7100                   # 浴室・洗面と洋室 2 の境（7100..7200）
X_BATH = 1750                   # 浴室と洗面の境（1750..1850）
P_R1 = 8550                     # 洋室 1 と トイレ・収納 の境（8550..8650）
X_WC = 4900                     # トイレと収納の境（4900..5000）
P_GENKAN = 9150                 # 玄関の土間（9150..9850）


def build_shell(api):
    b = Builder(api, "Shell")
    b.box((0, -BAL, W, D), -150, 150, "床スラブ", "slab")
    # 外壁（東西は戸境の壁。南は掃き出し窓、北は玄関ドアと洋室 2 の窓）
    b.box([(0, 0, T_EXT, D), (W - T_EXT, 0, W, D)], -50, H + 50, "戸境の壁", "wall")
    b.box(wall_x(0, T_EXT, T_EXT, W - T_EXT, [(600, 5400)]), -50, H + 50, "南の外壁", "wall")
    b.box((600, 0, 5400, T_EXT), -50, 450, "掃き出し窓の下枠", "wall")
    b.box(wall_x(D - T_EXT, D, T_EXT, W - T_EXT, [(700, 2100), (2800, 3600)]), -50, H + 50, "北の外壁", "wall")
    b.box((700, D - T_EXT, 2100, D), -50, 950, "腰壁（洋室 2 の窓）", "wall")
    # 間仕切り
    b.box(wall_x(P_LDK, P_LDK + T_IN, T_EXT, W - T_EXT, [(2800, 3600)]), -50, H + 50, "LDK の北の壁", "wall")
    b.box(wall_p(X_C0 - T_IN, X_C0, P_LDK + T_IN, D - T_EXT, [(6100, 6800), (8000, 8800)]), -50, H + 50, "廊下の西の壁", "wall")
    b.box(wall_p(X_C1, X_C1 + T_IN, P_LDK + T_IN, D - T_EXT, [(5700, 6500), (8850, 9600)]), -50, H + 50, "廊下の東の壁", "wall")
    b.box(wall_x(P_BATH, P_BATH + T_IN, T_EXT, X_C0 - T_IN), -50, H + 50, "洋室 2 の南の壁", "wall")
    b.box(wall_p(X_BATH, X_BATH + T_IN, P_LDK + T_IN, P_BATH, [(6500, 7000)]), -50, H + 50, "浴室の壁", "wall")
    b.box(wall_x(P_R1, P_R1 + T_IN, X_C1 + T_IN, W - T_EXT, [(5100, 5800)]), -50, H + 50, "洋室 1 の北の壁", "wall")
    b.box(wall_p(X_WC, X_WC + T_IN, P_R1 + T_IN, D - T_EXT), -50, H + 50, "トイレの壁", "wall")
    # バルコニー（手すり壁）
    b.box([(0, -BAL, W, -BAL + 150)], -50, 1150, "バルコニーの手すり壁", "balcony")
    return b.doc


def build_floors(api):
    b = Builder(api, "Floors")
    t = 12
    b.box([(T_EXT, T_EXT, W - T_EXT, P_LDK)], 0, t, "LDK の床（フローリング）", "wood")
    b.box([(T_EXT, P_BATH + T_IN, X_C0 - T_IN, D - T_EXT), (X_C1 + T_IN, P_LDK + T_IN, W - T_EXT, P_R1),
           (X_C0, P_LDK + T_IN, X_C1, P_GENKAN)], 0, t, "洋室・廊下の床", "wood")
    b.box([(T_EXT, P_LDK + T_IN, X_BATH, P_BATH), (X_BATH + T_IN, P_LDK + T_IN, X_C0 - T_IN, P_BATH),
           (X_C1 + T_IN, P_R1 + T_IN, X_WC, D - T_EXT)], 0, t, "浴室・洗面・トイレの床", "tile")
    b.box([(X_C0, P_GENKAN, X_C1, D - T_EXT)], -80, 60, "玄関の土間", "doma")
    b.box([(X_WC + T_IN, P_R1 + T_IN, W - T_EXT, D - T_EXT)], 0, t, "収納の床", "wood")
    b.box([(T_EXT, -BAL + 150, W - T_EXT, -20)], 0, 20, "バルコニーの床", "balcony")
    return b.doc


def build_fixtures(api):
    b = Builder(api, "Fixtures")
    # キッチン（I 型 2500、LDK の北の壁に沿って）: キャビネット・天板・シンク・コンロ・吊り戸棚・冷蔵庫
    b.box((200, 4850, 2700, 5480), 0, 800, "キッチンのキャビネット", "cabinet")
    b.box((180, 4820, 2720, 5490), 790, 60, "キッチンの天板", "counter")
    b.box((500, 5000, 1250, 5400), 850, 180, "シンク", op="cut")
    b.cyl([(1900, 5130), (2350, 5130)], 110, 850, 15, "コンロ", "dark")
    b.box((200, 5150, 2700, 5480), 1750, 600, "吊り戸棚", "counter")
    b.box((3700, 4850, 4400, 5480), 0, 1800, "冷蔵庫", "fridge")
    # 浴室（1600 × 1500）: 浴槽
    b.box((T_EXT + 40, P_LDK + T_IN + 40, X_BATH - 40, 6450), 0, 550, "浴槽", "white")
    b.box((T_EXT + 120, P_LDK + T_IN + 120, X_BATH - 120, 6370), 550, 450, "浴槽の内側", op="cut")
    # 洗面台・洗濯機
    b.box((X_BATH + T_IN + 30, 6600, X_C0 - T_IN - 30, P_BATH - 10), 0, 850, "洗面台", "white")
    b.cyl([(2225, 6850)], 170, 850, 150, "洗面ボウル", op="cut")
    b.box((X_BATH + T_IN + 30, 6600, X_C0 - T_IN - 30, P_BATH - 10), 1100, 700, "鏡の収納", "counter")
    b.box((X_BATH + T_IN + 30, P_LDK + T_IN + 30, X_C0 - T_IN - 30, 6150), 0, 850, "洗濯機", "fridge")
    # トイレ: 便器（タンク・便座）
    b.box((4130, 9600, 4570, 9840), 0, 850, "トイレのタンク", "white")
    b.box((4250, 9250, 4450, 9620), 0, 380, "便器の脚", "white")
    b.cyl([(4350, 9280)], 210, 0, 400, "便器", "white")
    b.cyl([(4350, 9280)], 215, 390, 40, "便座", "white")
    # 下駄箱（玄関）
    b.box((X_C1 - 340, P_GENKAN - 600, X_C1 - 10, D - T_EXT - 10), 0, 900, "下駄箱", "door")
    # 建具（開いた状態のドア・閉じた引き戸・玄関ドア）
    hd = 2000
    b.box([(2810, P_LDK - 760, 2850, P_LDK)], 0, hd, "LDK のドア", "door")
    b.box([(X_C0 - T_IN - 760, 8760, X_C0 - T_IN, 8800)], 0, hd, "洋室 2 のドア", "door")
    b.box([(X_C1 + T_IN, 5700, X_C1 + T_IN + 760, 5740)], 0, hd, "洋室 1 のドア", "door")
    b.box([(X_C1 + T_IN, 8850, X_C1 + T_IN + 720, 8890)], 0, hd, "トイレのドア", "door")
    b.box([(X_C0 - T_IN - 40, 6450, X_C0 - T_IN, 6800)], 0, hd, "洗面所の引き戸", "door")
    b.box([(X_BATH + 30, 6500, X_BATH + 70, 7000)], 0, hd, "浴室の折れ戸", "glass")
    b.box([(5100, P_R1 + 30, 5800, P_R1 + 70)], 0, 2200, "収納の扉", "door")
    b.box([(2800, D - T_EXT + 50, 3600, D - T_EXT + 100)], 0, 2100, "玄関ドア", "door")
    # 窓ガラス（掃き出し窓・洋室 2 の窓）
    b.box([(600, 60, 3000, 90)], 450, 1750, "掃き出し窓（西）", "glass")
    b.box([(3000, 30, 5400, 60)], 450, 1750, "掃き出し窓（東）", "glass")
    b.box([(700, D - 90, 2100, D - 60)], 950, 1150, "洋室 2 の窓", "glass")
    return b.doc


def build_furniture(api):
    b = Builder(api, "Furniture")
    # ダイニング（テーブル 1400 × 800、椅子 4 脚）
    b.box([(1000, 3500, 1060, 3560), (2340, 3500, 2400, 3560), (1000, 4240, 1060, 4300), (2340, 4240, 2400, 4300)],
          0, 700, "テーブルの脚", "table")
    b.box((980, 3480, 2420, 4320), 690, 35, "ダイニングテーブル", "table")
    seats = [(1150, 4380, 1570, 4800), (1830, 4380, 2250, 4800), (1150, 3000, 1570, 3420), (1830, 3000, 2250, 3420)]
    b.box(seats, 0, 430, "椅子の座面", "chair")
    b.box([(x0, 4740, x1, 4800) for x0, _, x1, _ in seats[:2]] + [(x0, 3000, x1, 3060) for x0, _, x1, _ in seats[2:]],
          420, 430, "椅子の背", "chair")
    # リビング: ソファ（東の壁沿い、西向き）、ローテーブル、ラグ、TV ボードと TV
    b.box((3300, 1200, 5600, 3300), 12, 8, "ラグ", "rug")
    b.box((4850, 1000, 5750, 3500), 5, 420, "ソファの座面", "sofa")
    b.box((5550, 1000, 5750, 3500), 420, 380, "ソファの背", "sofa")
    b.box([(4850, 1000, 5550, 1180), (4850, 3320, 5550, 3500)], 420, 180, "ソファの肘掛け", "sofa")
    b.box((3700, 1800, 4500, 2500), 5, 380, "ローテーブル", "table")
    b.box((200, 1200, 600, 3200), 0, 450, "TV ボード", "tvboard")
    b.box((360, 1500, 410, 2900), 440, 700, "TV", "dark")
    b.cyl([(5500, 600)], 180, 0, 350, "観葉植物の鉢", "pot")
    b.cyl([(5500, 600)], 240, 350, 250, "観葉植物（株元）", "plant")
    b.cyl([(5500, 600)], 380, 600, 700, "観葉植物", "plant")
    # 洋室 1: ダブルベッド（枕は北）
    b.box((4300, 6500, 5700, 8500), 0, 300, "ベッド（洋室 1）", "bed")
    b.box((4320, 6520, 5680, 8420), 300, 200, "マットレス（洋室 1）", "mattress")
    b.box((4300, 8430, 5700, 8500), 300, 600, "ヘッドボード（洋室 1）", "bed")
    b.box([(4400, 8000, 4950, 8380), (5050, 8000, 5600, 8380)], 500, 120, "枕（洋室 1）", "pillow")
    # 洋室 2: シングルベッドとデスク
    b.box((250, 7650, 1250, 9700), 0, 300, "ベッド（洋室 2）", "bed")
    b.box((270, 7670, 1230, 9620), 300, 200, "マットレス（洋室 2）", "mattress")
    b.box((250, 9630, 1250, 9700), 300, 600, "ヘッドボード（洋室 2）", "bed")
    b.box((350, 9200, 1150, 9580), 500, 120, "枕（洋室 2）", "pillow")
    b.box((1700, 7300, 2550, 7850), 0, 720, "デスク", "table")
    return b.doc


ROOMS = [   # 室名と、注記を置く位置（平面の座標）・内法の範囲（畳数の計算）
    ("LDK", (T_EXT, T_EXT, W - T_EXT, P_LDK), (3000, 4150)),
    ("洋室 1", (X_C1 + T_IN, P_LDK + T_IN, W - T_EXT, P_R1), (4250, 6100)),
    ("洋室 2", (T_EXT, P_BATH + T_IN, X_C0 - T_IN, D - T_EXT), (1650, 7400)),
    ("浴室", (T_EXT, P_LDK + T_IN, X_BATH, P_BATH), (950, 6750)),
    ("洗面所", (X_BATH + T_IN, P_LDK + T_IN, X_C0 - T_IN, P_BATH), (2225, 6370)),
    ("トイレ", (X_C1 + T_IN, P_R1 + T_IN, X_WC, D - T_EXT), (4350, 8900)),
    ("収納", (X_WC + T_IN, P_R1 + T_IN, W - T_EXT, D - T_EXT), (5425, 9250)),
    ("廊下", (X_C0, P_LDK + T_IN, X_C1, P_GENKAN), (3200, 7500)),
    ("玄関", (X_C0, P_GENKAN, X_C1, D - T_EXT), (3050, 9500)),
    ("バルコニー", (T_EXT, -BAL + 150, W - T_EXT, 0), (3000, -800)),
]
TATAMI = 1.62e6             # 1 畳（mm²）。不動産の表示の目安


def main():
    t0 = time.time()
    api = Api(Workspace("usd_brep"))
    docs = {}
    for name, fn in (("Shell", build_shell), ("Floors", build_floors), ("Fixtures", build_fixtures), ("Furniture", build_furniture)):
        t = time.time()
        docs[name] = fn(api)
        print(f"{name}: {time.time() - t:.1f} 秒")
    a = api.call("asm.new", {"name": "Unit_asm"})["doc"]
    for name in docs:
        api.call("asm.add", {"doc": a, "part": docs[name], "position": [0, 0, 0]})
    comps = [c["id"] for c in api.call("doc.state", {"doc": a, "mesh": False})["components"]]
    for c in comps[1:]:                                  # 床・住設・家具は躯体の標準平面にそろえる
        for pl in ("Top", "Front", "Right"):
            api.call("asm.mate", {"doc": a, "type": "coincident", "a": [comps[0], pl], "b": [c, pl], "aligned": True})
    st = api.call("doc.state", {"doc": a, "mesh": False})
    print("合致:", st["solve"])
    for name, did in docs.items():
        api.call("doc.save", {"doc": did, "path": os.path.join(HERE, name + ".usda")})
    api.call("doc.save", {"doc": a, "path": os.path.join(HERE, "Unit_asm.usda")})
    print(f"合計 {time.time() - t0:.1f} 秒")
    return api, a, docs


if __name__ == "__main__":
    main()
