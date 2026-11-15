"""小型の人型ロボット（全高 約 490 mm、サーボ 10 個）を API で組み立てて Data/Robot に保存する。

    cd core && python ../Data/Robot/build_robot.py

座標: X = 左右（ロボットの左 +X）、Y = 高さ（足裏 = 0）、Z = 前後（顔の向き +Z）。
パート: Servo（両軸ホーンのサーボ。10 個とも同じ向き）、Foot、Shin、Thigh、HipBracket、Torso、Head、
UpperArm_L / UpperArm_R（外側の板だけなので左右で別）、Forearm（手先のグリッパーを含む）。
各パートは部品の基準（サーボならホーンの軸、脚ならその脚の中心線）を原点に作り、アセンブリで位置だけ与えて固定する。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "core"))
from cad.api.rpc import Api  # noqa: E402
from cad.doc.workspace import Workspace  # noqa: E402

COLORS = {
    "servo": "#2a2d33", "horn": "#c9ced6", "label": "#ff7a1a", "alu": "#b9c0c9", "white": "#eef0f2", "orange": "#ff7a1a",
    "dark": "#3b4048", "black": "#17191d", "rubber": "#26282c", "cyan": "#38d9ff",
}

# 寸法の要点（mm）
LEG_X = 36                        # 脚の中心（左右）
Y_ANKLE, Y_KNEE, Y_HIP = 45, 137, 237     # 足首・ひざ・股のサーボの軸の高さ
ARM_X = 80                        # 腕の中心（左右）
Y_SHOULDER, Y_ELBOW = 360, 280    # 肩・ひじのサーボの軸の高さ
SERVOS = [(s * LEG_X, y) for y in (Y_ANKLE, Y_KNEE, Y_HIP) for s in (1, -1)] + \
         [(s * ARM_X, y) for y in (Y_SHOULDER, Y_ELBOW) for s in (1, -1)]


class Builder:
    """パート 1 つを組み立てる道具。形はすべて座標軸に沿った押し出し。

    axis = 押し出す向き（"x" / "y" / "z"）、a0..a1 = その向きの範囲。断面は残りの 2 軸の座標で与える
    （y → (x, z)、z → (x, y)、x → (y, z)）。カットは a1 側の平面から a0 まで掘る。"""
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

    def _extrude(self, axis, a0, a1, draw, name, color, op):
        self.call("sketch.new", plane=self.plane(axis, a1 if op == "cut" else a0))
        draw()
        s = self.call("sketch.end")["tree"][-1]["id"]
        return self.feat("extrude", name=name, color=color, sketch=s, distance=a1 - a0, op=op)

    def box(self, axis, a0, a1, rects, r=0, name=None, color=None, op="add"):
        """rects = [(p0, q0, p1, q1), …]（断面の座標）。r > 0 なら角を丸める。"""
        if isinstance(rects[0], (int, float)):
            rects = [rects]

        def draw():
            for p0, q0, p1, q1 in rects:
                u0, v0 = self.uv(axis, p0, q0)
                u1, v1 = self.uv(axis, p1, q1)
                L = self.call("sketch.add", kind="rect", points=[[min(u0, u1), min(v0, v1)], [max(u0, u1), max(v0, v1)]])["created"]
                if r > 0:
                    for i in range(4):
                        self.call("sketch.fillet", radius=r, l1=L[i], l2=L[(i + 1) % 4])
        return self._extrude(axis, a0, a1, draw, name, color, op)

    def cyl(self, axis, a0, a1, centers, r, name=None, color=None, op="add"):
        def draw():
            for p, q in centers:
                u, v = self.uv(axis, p, q)
                self.call("sketch.add", kind="circle", points=[[u, v], [u + r, v]])
        return self._extrude(axis, a0, a1, draw, name, color, op)

    def slot(self, axis, a0, a1, c1, c2, r, name=None, color=None, op="add"):
        """長穴形（中心 c1–c2、半径 r）。"""
        def draw():
            u1, v1 = self.uv(axis, *c1)
            u2, v2 = self.uv(axis, *c2)
            self.call("sketch.add", kind="slot", points=[[u1, v1], [u2, v2], [u1 + r, v1 + r]], radius=r)
        return self._extrude(axis, a0, a1, draw, name, color, op)


def build_servo(api):
    """両軸ホーンのサーボ（ケース 24 × 40 × 20）。原点 = 出力軸、軸は X。"""
    b = Builder(api, "Servo")
    b.box("x", -12, 12, (-30, -10, 10, 10), r=2, name="ケース", color="servo")
    b.box("x", 1.5, 4, (-34, -10, 14, 10), r=2, name="取り付け耳", color="servo")
    b.cyl("x", 1.5, 4, [(-32, -5), (-32, 5), (12, -5), (12, 5)], 1.2, name="取り付け穴", op="cut")
    b.box("z", 9.4, 10, (-9, -26, 9, 4), name="ラベル", color="label", op="cut")
    b.cyl("x", 12, 14.5, [(0, 0)], 10, name="ホーン（+X）", color="horn")
    b.cyl("x", -14.5, -12, [(0, 0)], 10, name="ホーン（-X）", color="horn")
    b.cyl("x", 14.5, 16, [(0, 0)], 4, name="ホーンのボス（+X）", color="horn")
    b.cyl("x", -16, -14.5, [(0, 0)], 4, name="ホーンのボス（-X）", color="horn")
    b.cyl("x", 13.5, 14.5, [(6.5, 0), (-6.5, 0), (0, 6.5), (0, -6.5)], 1.1, name="ホーンのねじ穴", op="cut")
    b.box("y", -32, -30, (-5, -4, 5, 4), name="ケーブルの口", color="black")
    return b.doc


def build_foot(api):
    b = Builder(api, "Foot")
    b.box("y", 0, 3, (-32, -45, 32, 72), r=12, name="足裏（ゴム）", color="rubber")
    b.box("y", 3, 7, (-32, -45, 32, 72), r=12, name="足の板", color="orange")
    b.box("y", 7, 9, (-24, -40, 24, 64), r=8, name="足の補強", color="alu")
    return b.doc


def build_shin(api):
    """すね: 足首のホーンとひざのホーンを両側の板でつなぐ。"""
    b = Builder(api, "Shin")
    for s in (1, -1):
        x0, x1 = sorted((s * 16.5, s * 18.5))
        b.slot("x", x0, x1, (Y_ANKLE, 0), (Y_KNEE, 0), 13, name=f"すねの板（{'外' if s > 0 else '内'}）", color="alu")
    b.box("z", 11, 15, (-18.5, 70, 18.5, 105), name="すねの前の桁", color="alu")
    b.box("z", -15, -11, (-18.5, 75, 18.5, 100), name="すねの後ろの桁", color="alu")
    b.box("z", 15, 20, (-15, 62, 15, 112), r=6, name="すねのカバー", color="orange")
    return b.doc


def build_thigh(api):
    """もも: ひざのサーボのケースと股のサーボのケースを前後の板でつなぐ。"""
    b = Builder(api, "Thigh")
    b.box("z", 10.5, 12.5, (-12, 115, 12, 235), r=5, name="ももの板（前）", color="alu")
    b.box("z", -12.5, -10.5, (-12, 115, 12, 235), r=5, name="ももの板（後ろ）", color="alu")
    b.slot("z", -12.5, 12.5, (0, 152), (0, 198), 6, name="肉抜き", op="cut")
    b.box("z", 12.5, 17, (-14, 150, 14, 200), r=6, name="もものカバー", color="orange")
    return b.doc


def build_hip_bracket(api):
    """股: 股のサーボのホーンを骨盤へつなぐ U 字の金具。"""
    b = Builder(api, "HipBracket")
    for s in (1, -1):
        x0, x1 = sorted((s * 16.5, s * 18.5))
        b.slot("x", x0, x1, (Y_HIP, 0), (252, 0), 13, name=f"股の金具の板（{'外' if s > 0 else '内'}）", color="alu")
    b.box("y", 259, 265, (-18.5, -13, 18.5, 13), name="股の金具の天板", color="alu")
    return b.doc


def build_torso(api):
    b = Builder(api, "Torso")
    b.box("y", 265, 271, (-56, -30, 56, 30), r=8, name="骨盤の板", color="alu")
    b.box("y", 271, 296, (-40, -28, 40, 28), r=10, name="腰", color="dark")
    b.box("y", 296, 382, (-56, -36, 56, 36), r=14, name="胸", color="white")
    b.box("z", 35, 39, (-40, 312, 40, 368), r=8, name="胸の板", color="dark")
    b.cyl("z", 39, 41, [(0, 348)], 9, name="エンブレム", color="orange")
    b.cyl("z", 41, 42, [(0, 348)], 4, name="エンブレムの光", color="cyan")
    b.box("z", 39, 40, [(-30, 322, -20, 326), (20, 322, 30, 326)], name="表示灯", color="cyan")
    b.box("z", -48, -35, (-38, 304, 38, 372), r=8, name="バッテリー", color="dark")
    b.box("z", -51, -47, [(-30, y, 30, y + 4) for y in (312, 324, 336, 348, 360)], name="放熱のリブ", color="dark")
    b.cyl("x", 50, ARM_X - 16, [(Y_SHOULDER, 0)], 10, name="肩の軸受（左）", color="dark")
    b.cyl("x", -(ARM_X - 16), -50, [(Y_SHOULDER, 0)], 10, name="肩の軸受（右）", color="dark")
    b.cyl("y", 382, 394, [(0, 0)], 11, name="首", color="dark")
    return b.doc


def build_head(api):
    b = Builder(api, "Head")
    b.box("y", 394, 462, (-42, -34, 42, 38), r=18, name="頭", color="white")
    b.box("y", 462, 466, (-34, -26, 34, 30), r=14, name="頭頂", color="white")
    b.box("z", 37, 41, (-34, 410, 34, 446), r=12, name="バイザー", color="black")
    b.cyl("z", 41, 42.5, [(-14, 428), (14, 428)], 6, name="目", color="cyan")
    b.cyl("x", 42, 48, [(428, 2)], 12, name="耳（左）", color="orange")
    b.cyl("x", -48, -42, [(428, 2)], 12, name="耳（右）", color="orange")
    b.cyl("y", 466, 482, [(22, 0)], 2, name="アンテナ", color="alu")
    b.cyl("y", 482, 488, [(22, 0)], 4, name="アンテナの先", color="orange")
    return b.doc


def build_upper_arm(api, s):
    """上腕: 肩のホーン（外側）とひじのサーボのケースをつなぐ。s = +1（左）/ -1（右）。"""
    b = Builder(api, "UpperArm_" + ("L" if s > 0 else "R"))
    x0, x1 = sorted((s * 16.5, s * 18.5))
    b.slot("x", x0, x1, (Y_SHOULDER, 0), (308, 0), 12, name="上腕の板", color="alu")
    b.box("y", 295, 300, (min(-12 * s, 18.5 * s), -12, max(-12 * s, 18.5 * s), 12), name="ひじのサーボの台", color="alu")
    x0, x1 = sorted((s * 18.5, s * 22))
    b.slot("x", x0, x1, (Y_SHOULDER, 0), (332, 0), 13, name="肩のアーマー", color="orange")
    return b.doc


def build_forearm(api):
    """前腕: ひじのホーンを両側の板で受け、先にグリッパー。"""
    b = Builder(api, "Forearm")
    for s in (1, -1):
        x0, x1 = sorted((s * 19, s * 21))
        b.slot("x", x0, x1, (Y_ELBOW, 0), (215, 0), 12, name=f"前腕の板（{'外' if s > 0 else '内'}）", color="alu")
    b.box("z", 11, 15, (-21, 225, 21, 262), r=4, name="前腕のカバー", color="orange")
    b.box("y", 180, 212, (-19.5, -13, 19.5, 13), r=4, name="手首", color="dark")
    b.box("y", 150, 180, [(-13, -7, -5, 7), (5, -7, 13, 7)], name="指", color="dark")
    b.box("y", 145, 150, [(-13, -7, -5, 7), (5, -7, 13, 7)], name="指先", color="orange")
    return b.doc


def main():
    t0 = time.time()
    api = Api(Workspace("usd_brep"))
    docs = {}
    for name, fn in (("Torso", build_torso), ("Head", build_head), ("Servo", build_servo), ("Foot", build_foot),
                     ("Shin", build_shin), ("Thigh", build_thigh), ("HipBracket", build_hip_bracket),
                     ("UpperArm_L", lambda a: build_upper_arm(a, 1)), ("UpperArm_R", lambda a: build_upper_arm(a, -1)),
                     ("Forearm", build_forearm)):
        t = time.time()
        docs[name] = fn(api)
        print(f"{name}: {time.time() - t:.1f} 秒", flush=True)
    a = api.call("asm.new", {"name": "Robot_asm"})["doc"]
    place = [("Torso", (0, 0, 0)), ("Head", (0, 0, 0))]
    place += [("Servo", (x, y, 0)) for x, y in SERVOS]
    for part in ("Foot", "Shin", "Thigh", "HipBracket"):
        place += [(part, (s * LEG_X, 0, 0)) for s in (1, -1)]
    place += [("UpperArm_L", (ARM_X, 0, 0)), ("UpperArm_R", (-ARM_X, 0, 0))]
    place += [("Forearm", (s * ARM_X, 0, 0)) for s in (1, -1)]
    for part, pos in place:
        api.call("asm.add", {"doc": a, "part": docs[part], "position": list(pos)})
    st = api.call("doc.state", {"doc": a, "mesh": False})
    for c in st["components"][1:]:                      # 最初の部品（胴）は固定。ほかもこの姿勢で固定する
        api.call("asm.fix", {"doc": a, "comp": c["id"], "value": True})
    st = api.call("asm.interference", {"doc": a})
    print("干渉:", st["checked"], "部品中", [(i.get("a"), i.get("b")) if isinstance(i, dict) else i for i in st["interferences"]])
    for name, did in docs.items():
        api.call("doc.save", {"doc": did, "path": os.path.join(HERE, name + ".usda")})
    api.call("doc.save", {"doc": a, "path": os.path.join(HERE, "Robot_asm.usda")})
    print(f"合計 {time.time() - t0:.1f} 秒")
    return api, a, docs


if __name__ == "__main__":
    main()
