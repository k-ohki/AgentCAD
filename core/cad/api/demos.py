"""動作確認用のデモ部品（D63）。API の呼び出しを並べて、利用者が画面で作るのと同じ手順で組み立てる。

car: 車の形。側面の輪郭（線と 3 点円弧）を中間平面で押し出し、左側面の参照平面にホイールアーチのカット・タイヤ・
ホイールの穴（スケッチの円形パターン）・窓のくぼみを作って、正面についてミラー。前面にライトとグリルのくぼみ。
車幅は変数 width、ホイールの半径は変数 wheel_r（式で寸法をつなぐ）。
"""
from __future__ import annotations


def build_car(api) -> str:
    """車のデモ部品を作って文書 ID を返す。カット（差のブーリアン）は実カーネル（usd-brep）が要る。"""
    d = api.call("doc.new", {"name": "Car"})["doc"]
    call = lambda m, **p: api.call(m, {"doc": d, **p})
    call("variable.set", name="width", expr="80")
    call("variable.set", name="wheel_r", expr="16")

    def sketch(plane: dict, draw) -> str:
        call("sketch.new", plane=plane)
        draw()
        sid = call("sketch.end")["tree"][-1]["id"]
        return sid

    def poly(points, arcs=None):
        """閉じた輪郭。arcs = {i: 通る点} なら i 番目の辺（points[i] → points[i+1]）を 3 点円弧にする。"""
        arcs = arcs or {}
        first = prev = None
        n = len(points)
        for i in range(n):
            a = prev if prev is not None else points[i]
            b = first if i == n - 1 else points[i + 1]
            if i in arcs:
                st = call("sketch.add", kind="arc3", points=[a, b, arcs[i]])
                e = next(x for x in st["sketch"]["entities"] if x["id"] == st["created"][0])
                # 3 点円弧は反時計回りに直されるので、始点・終点のどちらが b かを座標で見分ける
                ends = [e["start"], e["end"]]
                P = {x["id"]: (x["x"], x["y"]) for x in st["sketch"]["entities"] if x["type"] == "point"}
                bx = P[first] if isinstance(b, str) else b
                prev_id = min(ends, key=lambda p: (P[p][0] - bx[0]) ** 2 + (P[p][1] - bx[1]) ** 2)
                if first is None:
                    first = next(p for p in ends if p != prev_id)
                prev = prev_id
                continue
            st = call("sketch.add", kind="line", points=[a, b])
            e = next(x for x in st["sketch"]["entities"] if x["id"] == st["created"][0])
            if first is None:
                first = e["p1"]
            prev = e["p2"]

    def circles(centers, r):
        for cx, cy in centers:
            call("sketch.add", kind="circle", points=[[cx, cy], [cx + r, cy]])

    def feat(type, **p) -> str:
        st = call("feature.add", type=type, **p)
        return st["created"][0]

    # 1. 車体: 側面の輪郭（正面 = XY。x が前後、y が高さ）を車幅で中間平面に押し出す
    body_pts = [(5, 12), (192, 12), (205, 22), (205, 38), (190, 47), (135, 52), (110, 77), (62, 79), (32, 55), (2, 50), (0, 22)]
    s_body = sketch({"plane": "Front"}, lambda: poly(body_pts, arcs={6: (86, 83), 4: (163, 51)}))
    body = feat("extrude", sketch=s_body, distance=80, end="mid")
    call("feature.edit", id=body, key="distance", expr="width")
    # 2. 左側面より少し外の参照平面（z = width / 2 + 1）
    side = feat("plane", base={"plane": "Front"}, offset=41)
    call("feature.edit", id=side, key="offset", expr="width / 2 + 1")
    wheels = [(45, 18), (165, 18)]
    # 3. ホイールアーチ（材料の中へ 15）
    s_arch = sketch({"feature": side}, lambda: circles(wheels, 20))
    arch = feat("extrude", sketch=s_arch, distance=15, op="cut")
    # 4. タイヤ（参照平面から車体の内側へ 17。アーチの奥の壁に 2 食い込んでつながる）
    s_tire = sketch({"feature": side}, lambda: circles(wheels, 16))
    tire = feat("extrude", sketch=s_tire, distance=17, reverse=True)
    # 5. ホイール: くぼみ（半径 10、深さ 3）と、5 つ穴（スケッチの円形パターン）
    s_rim = sketch({"feature": side}, lambda: circles(wheels, 10))
    rim = feat("extrude", sketch=s_rim, distance=3, op="cut")

    def lug_holes():
        for cx, cy in wheels:
            st = call("sketch.add", kind="circle", points=[[cx + 5.5, cy], [cx + 7, cy]])
            c = st["created"][0]
            pt = call("sketch.add", kind="point", points=[[cx, cy]])["created"][0]
            call("sketch.pattern", ids=[c], kind="circular", count=5, center=pt)
    s_lug = sketch({"feature": side}, lug_holes)
    lug = feat("extrude", sketch=s_lug, distance=8, op="cut")
    # 6. 窓のくぼみ（前と後ろ）
    s_win = sketch({"feature": side}, lambda: (poly([(88, 55), (124, 55), (107, 73), (88, 75)]),
                                              poly([(40, 55), (84, 55), (84, 75), (64, 75)])))
    win = feat("extrude", sketch=s_win, distance=2.5, op="cut")
    # 7. 正面（車幅の中央）についてミラー
    feat("mirror", seeds=[arch, tire, rim, lug, win], plane={"plane": "Front"})
    # 8. 前面のライトとグリル（右側面 = YZ に平行な参照平面 x = 206 から 2 のくぼみ）
    front = feat("plane", base={"plane": "Right"}, offset=206)

    def lights():
        for u0, u1 in ((18, 32), (-32, -18)):
            call("sketch.add", kind="rect", points=[[u0, 29], [u1, 35]])
        call("sketch.add", kind="rect", points=[[-12, 24], [12, 31]])
    s_light = sketch({"feature": front}, lights)
    feat("extrude", sketch=s_light, distance=2, op="cut")
    return d
