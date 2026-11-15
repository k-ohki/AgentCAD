"""スケッチの編集操作: フィレット、面取り、トリム、延長、ミラー、パターン、オフセット。

どちらも「定義（エンティティと拘束）」を書き換えるだけで、求解はしない。
呼び出し側が続けて solve() する。
"""
from __future__ import annotations

import math

from .sketch import Arc, Circle, Line, Point, Sketch

EPS = 1e-9
TAU = 2 * math.pi

# ---------------------------------------------------------------- 共通


def _uses(sk: Sketch, pid: str) -> bool:
    return any(pid in (getattr(e, a, None) for a in ("p1", "p2", "center", "start", "end"))
               for e in sk.entities.values() if not isinstance(e, Point))


def _referenced(sk: Sketch, pid: str) -> bool:
    return any(pid in c.refs for c in sk.constraints.values())


def _drop_if_orphan(sk: Sketch, pid: str):
    if pid in sk.entities and not _uses(sk, pid):
        sk.delete(pid)

# ---------------------------------------------------------------- フィレット


def fillet(sk: Sketch, l1: str, l2: str, radius: float) -> str:
    """角を共有する 2 本の線を半径 radius の円弧でつなぐ。円弧の ID を返す。
    - 線は接点まで縮め、正接 ×2 と半径寸法を付ける
    - 元の角の点は、寸法などから参照されていれば「仮想交点」として残す（両線の延長上に拘束）
    - 線の長さ寸法は「仮想交点から反対側の端点までの距離」に付け替える（一般的な 3D CAD と同じ見え方）"""
    L1, L2 = sk.entities[l1], sk.entities[l2]
    if not (isinstance(L1, Line) and isinstance(L2, Line)):
        raise ValueError("フィレットは線と線に対して行います")
    if radius <= 0:
        raise ValueError("半径は正の値にしてください")
    corner = None
    for a1 in ("p1", "p2"):
        for a2 in ("p1", "p2"):
            p, q = getattr(L1, a1), getattr(L2, a2)
            joined = p == q or any(c.type == "coincident" and set(c.refs) == {p, q}
                                   for c in sk.constraints.values())
            if joined and corner is None:
                corner = (a1, a2, p, q)
    if corner is None:
        raise ValueError("2 本の線が角を共有していません")
    a1, a2, p, q = corner
    far1 = L1.p2 if a1 == "p1" else L1.p1
    far2 = L2.p2 if a2 == "p1" else L2.p1
    P, A, B = sk.xy(p), sk.xy(far1), sk.xy(far2)
    la, lb = math.dist(P, A), math.dist(P, B)
    u = ((A[0] - P[0]) / la, (A[1] - P[1]) / la)
    v = ((B[0] - P[0]) / lb, (B[1] - P[1]) / lb)
    theta = math.acos(max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1])))
    if theta < 1e-6 or theta > math.pi - 1e-6:
        raise ValueError("線が平行なのでフィレットできません")
    d = radius / math.tan(theta / 2)
    if d >= la - EPS or d >= lb - EPS:
        raise ValueError("半径が大きすぎます")
    bis = (u[0] + v[0], u[1] + v[1]); bl = math.hypot(*bis)
    k = radius / math.sin(theta / 2) / bl
    T1 = (P[0] + u[0] * d, P[1] + u[1] * d)
    T2 = (P[0] + v[0] * d, P[1] + v[1] * d)
    C = (P[0] + bis[0] * k, P[1] + bis[1] * k)
    t1, t2, c = sk.point(*T1), sk.point(*T2), sk.point(*C)
    setattr(L1, a1, t1); setattr(L2, a2, t2)
    ccw = (T1[0] - C[0]) * (T2[1] - C[1]) - (T1[1] - C[1]) * (T2[0] - C[0]) > 0
    arc = sk.arc(c, t1, t2) if ccw else sk.arc(c, t2, t1)
    # 角の一致拘束（別々の点だった場合）は外す
    sk.constraints = {i: k_ for i, k_ in sk.constraints.items()
                      if not (k_.type == "coincident" and set(k_.refs) == {p, q} and p != q)}
    # 長さ寸法 → 仮想交点からの距離
    for k_ in sk.constraints.values():
        if k_.type == "length" and k_.refs[0] in (l1, l2):
            k_.type, k_.refs = "distance", [p, far1 if k_.refs[0] == l1 else far2]
    sk.constrain("tangent", l1, arc); sk.constrain("tangent", l2, arc)
    sk.constrain("radius", arc, value=radius)
    for pt in {p, q}:
        if _uses(sk, pt):
            continue
        if _referenced(sk, pt):                       # 仮想交点として残す
            sk.constrain("point_on_line", pt, l1); sk.constrain("point_on_line", pt, l2)
        else:
            sk.delete(pt)
    return arc

# ---------------------------------------------------------------- 交点計算


def _curve(sk: Sketch, eid: str):
    e = sk.entities[eid]
    if isinstance(e, Line):
        return ("line", sk.xy(e.p1), sk.xy(e.p2))
    c = sk.xy(e.center)
    if isinstance(e, Circle):
        return ("circle", c, e.radius)
    s, t = sk.xy(e.start), sk.xy(e.end)
    a0 = math.atan2(s[1] - c[1], s[0] - c[0])
    sweep = (math.atan2(t[1] - c[1], t[0] - c[0]) - a0) % TAU or TAU
    return ("arc", c, math.dist(c, s), a0, sweep)


def _param(cv, pt) -> float:
    """曲線上の位置。線・円弧は 0〜1（範囲外もありうる）、円は角度 0〜2π。"""
    if cv[0] == "line":
        (ax, ay), (bx, by) = cv[1], cv[2]
        dx, dy = bx - ax, by - ay
        return ((pt[0] - ax) * dx + (pt[1] - ay) * dy) / (dx * dx + dy * dy)
    ang = math.atan2(pt[1] - cv[1][1], pt[0] - cv[1][0])
    if cv[0] == "circle":
        return ang % TAU
    t = ((ang - cv[3]) % TAU) / cv[4]
    # 始点のわずか手前は 2π 回って 1 より大きく出るので、近い方に寄せる
    return t - TAU / cv[4] if t > 1 + (TAU / cv[4] - 1) / 2 else t


def _on(cv, pt, tol=1e-7) -> bool:
    return cv[0] == "circle" or -tol <= _param(cv, pt) <= 1 + tol


def _intersections(a, b) -> list[tuple[float, float]]:
    """2 曲線を無限直線 / 円とみなした交点。"""
    if a[0] == "line" and b[0] == "line":
        (x1, y1), (x2, y2), (x3, y3), (x4, y4) = a[1], a[2], b[1], b[2]
        den = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
        if abs(den) < 1e-12:
            return []
        t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / den
        return [(x1 + t * (x2 - x1), y1 + t * (y2 - y1))]
    if a[0] == "line" or b[0] == "line":
        ln, ci = (a, b) if a[0] == "line" else (b, a)
        (x1, y1), (x2, y2), (cx, cy), r = ln[1], ln[2], ci[1], ci[2]
        dx, dy = x2 - x1, y2 - y1
        fx, fy = x1 - cx, y1 - cy
        A, B, Cc = dx * dx + dy * dy, 2 * (fx * dx + fy * dy), fx * fx + fy * fy - r * r
        disc = B * B - 4 * A * Cc
        if disc < -1e-12 * A * r * r:
            return []
        sq = math.sqrt(max(disc, 0.0))
        ts = {(-B - sq) / (2 * A), (-B + sq) / (2 * A)} if sq > 1e-9 * A else {-B / (2 * A)}
        return [(x1 + t * dx, y1 + t * dy) for t in sorted(ts)]
    (ax, ay), ra, (bx, by), rb = a[1], a[2], b[1], b[2]
    d = math.hypot(bx - ax, by - ay)
    if d < 1e-12 or d > ra + rb + 1e-9 or d < abs(ra - rb) - 1e-9:
        return []
    x = (d * d + ra * ra - rb * rb) / (2 * d)
    h = math.sqrt(max(ra * ra - x * x, 0.0))
    ux, uy = (bx - ax) / d, (by - ay) / d
    mx, my = ax + x * ux, ay + x * uy
    if h < 1e-9:
        return [(mx, my)]
    return [(mx - h * uy, my + h * ux), (mx + h * uy, my - h * ux)]

# ---------------------------------------------------------------- トリム


def trim(sk: Sketch, eid: str, x: float, y: float) -> list[str]:
    """(x, y) 付近の区間を、他の要素との交点まで切り取る（一般的な 3D CAD のパワートリム相当）。
    残った要素の ID を返す。
    - 交点が無ければ要素ごと削除
    - 片側だけに交点があれば端点をそこへ移す
    - 両側にあれば 2 つに分ける（線: 同一直線上、円弧: 同心・等半径の拘束を付ける）
    - 円は交点が 2 つ以上あれば円弧になる（ID は同じ）
    - 新しい端点は、切った相手の上に拘束する
    - 切られた線の長さ寸法は意味が変わるので削除する"""
    e = sk.entities[eid]
    if isinstance(e, Point):
        raise ValueError("点はトリムできません")
    cv = _curve(sk, eid)
    closed = cv[0] == "circle"
    cuts = []
    for oid, o in sk.entities.items():
        if oid == eid or isinstance(o, Point):
            continue
        ocv = _curve(sk, oid)
        for pt in _intersections(cv, ocv):
            if not (_on(ocv, pt) and _on(cv, pt)):
                continue
            t = _param(cv, pt)
            if closed or 1e-7 < t < 1 - 1e-7:
                cuts.append((t, pt, oid))
    cuts.sort()
    tp = _param(cv, (x, y))

    def new_end(cut) -> str:
        _, pt, oid = cut
        p = sk.point(*pt)
        sk.constrain("point_on_line" if isinstance(sk.entities[oid], Line) else "point_on_circle", p, oid)
        return p

    def remove_whole():
        pts = [getattr(e, a) for a in ("p1", "p2", "center", "start", "end") if hasattr(e, a)]
        sk.delete(eid)
        for p in pts:
            _drop_if_orphan(sk, p)
        return []

    if closed:
        if len(cuts) < 2:
            return remove_whole()
        before = [c for c in cuts if c[0] < tp] or cuts          # 巡回
        after = [c for c in cuts if c[0] > tp] or cuts
        a, b = before[-1], after[0]
        sk.entities[eid] = Arc(e.center, new_end(b), new_end(a), e.radius, e.construction)
        return [eid]

    tp = min(max(tp, 0.0), 1.0)
    lower = [c for c in cuts if c[0] < tp]
    upper = [c for c in cuts if c[0] > tp]
    if not lower and not upper:
        return remove_whole()
    s_attr, e_attr = ("p1", "p2") if isinstance(e, Line) else ("start", "end")
    if isinstance(e, Line):
        sk.constraints = {i: k for i, k in sk.constraints.items() if not (k.type == "length" and k.refs == [eid])}
    out = [eid]
    if lower and upper:
        old_end = getattr(e, e_attr)
        setattr(e, e_attr, new_end(lower[-1]))
        pu = new_end(upper[0])
        if isinstance(e, Line):
            other = sk.line(pu, old_end, construction=e.construction)
            sk.constrain("point_on_line", pu, eid); sk.constrain("point_on_line", old_end, eid)
        else:
            other = sk.arc(e.center, pu, old_end, construction=e.construction)
            sk.constrain("equal", eid, other)
        out.append(other)
    elif upper:
        old = getattr(e, s_attr)
        setattr(e, s_attr, new_end(upper[0]))
        _drop_if_orphan(sk, old)
    else:
        old = getattr(e, e_attr)
        setattr(e, e_attr, new_end(lower[-1]))
        _drop_if_orphan(sk, old)
    return out


# ---------------------------------------------------------------- ミラー


def mirror(sk: Sketch, ids: list[str], axis: str) -> list[str]:
    """要素を線 axis について鏡映コピーし、対称拘束でつなぐ（一般的な 3D CAD のエンティティのミラー）。
    - 点は 対称（元, 写し, 軸）。軸の上にある点は写しを作らず共有する（輪郭がつながる）
    - 円は中心の対称 + 半径が等しい。円弧は中心・両端の対称（向きを保つため始点と終点を入れ替える）
    - 鏡映しても自分自身に重なる要素（軸上の線、中心が軸上の円）は作らない
    作った要素（線・円・円弧・単独の点）の ID を返す。"""
    ax = sk.entities.get(axis)
    if not isinstance(ax, Line):
        raise ValueError("ミラーの軸には線（作図線）を選んでください")
    (ax1, ay1), (ax2, ay2) = sk.xy(ax.p1), sk.xy(ax.p2)
    dx, dy = ax2 - ax1, ay2 - ay1
    L2 = dx * dx + dy * dy
    if L2 < EPS:
        raise ValueError("軸の線の長さが 0 です")
    tol = 1e-7 * max(1.0, math.sqrt(L2))
    pmap: dict[str, str] = {}

    def pm(pid: str) -> str:
        if pid in pmap:
            return pmap[pid]
        x, y = sk.xy(pid)
        t = ((x - ax1) * dx + (y - ay1) * dy) / L2
        fx, fy = ax1 + t * dx, ay1 + t * dy
        if math.hypot(x - fx, y - fy) <= tol:
            pmap[pid] = pid                       # 軸上: 共有
        else:
            pmap[pid] = sk.point(2 * fx - x, 2 * fy - y)
            sk.constrain("symmetric", pid, pmap[pid], axis)
        return pmap[pid]

    out = []
    for eid in dict.fromkeys(ids):
        e = sk.entities.get(eid)
        if e is None or eid == axis:
            continue
        if isinstance(e, Point):
            if not _uses(sk, eid) and pm(eid) != eid:
                out.append(pmap[eid])
        elif isinstance(e, Line):
            a, b = pm(e.p1), pm(e.p2)
            if (a, b) != (e.p1, e.p2):
                out.append(sk.line(a, b, construction=e.construction))
        elif isinstance(e, Circle):
            c = pm(e.center)
            if c != e.center:
                out.append(sk.circle(c, e.radius, construction=e.construction))
                sk.constrain("equal", eid, out[-1])
        else:
            c, s_, e_ = pm(e.center), pm(e.end), pm(e.start)
            if (c, s_, e_) != (e.center, e.end, e.start):
                out.append(sk.arc(c, s_, e_, construction=e.construction))
    if not out:
        raise ValueError("ミラーする要素を選んでください（軸以外の線・円・円弧）")
    return out


# ---------------------------------------------------------------- パターン


def pattern(sk: Sketch, ids: list[str], kind: str, count: int, spacing: float = 10.0, angle: float = 0.0,
            center: tuple[float, float] = (0.0, 0.0), total: float = 360.0) -> list[str]:
    """スケッチの直線パターン / 円形パターン（一般的な 3D CAD の「直線スケッチパターン」「円形スケッチパターン」）。
    kind="linear": 向き angle（度、u 軸から反時計回り）に spacing ずつずらして count 個（元を含む）。
    kind="circular": center の周りに count 個（元を含む）。total = 360 なら等間隔、それ以外は total を count - 1 等分。
    写しは元と同じ形（円・円弧・線は元と「等しい」、直線パターンの線は元と「平行」も）。写しの位置は拘束しない
    （寸法で決める）。選んだ要素どうしで共有している点は、写しでも共有する。作った要素の ID を返す。"""
    count = int(count)
    if count < 2:
        raise ValueError("個数は 2 以上にしてください")
    if count > 500:
        raise ValueError("個数が多すぎます（500 まで）")
    src = [i for i in dict.fromkeys(ids) if i in sk.entities]
    curves = [i for i in src if not isinstance(sk.entities[i], Point)]
    singles = [i for i in src if isinstance(sk.entities[i], Point) and not _uses(sk, i)]
    if not curves and not singles:
        raise ValueError("パターンにする要素（線・円・円弧・点）を選んでください")
    if kind == "linear":
        if abs(spacing) < EPS:
            raise ValueError("間隔を 0 以外にしてください")
        a = math.radians(angle)
        step = lambda k, x, y: (x + k * spacing * math.cos(a), y + k * spacing * math.sin(a))
    elif kind == "circular":
        if abs(total) < EPS:
            raise ValueError("角度を 0 以外にしてください")
        da = math.radians(total / count if abs(abs(total) - 360) < 1e-9 else total / (count - 1))
        cx, cy = center

        def step(k, x, y):
            c, s_ = math.cos(k * da), math.sin(k * da)
            return (cx + c * (x - cx) - s_ * (y - cy), cy + s_ * (x - cx) + c * (y - cy))
    else:
        raise ValueError(f"未知のパターン: {kind}")
    out: list[str] = []
    for k in range(1, count):
        pmap: dict[str, str] = {}

        def pm(pid: str) -> str:
            if pid not in pmap:
                pmap[pid] = sk.point(*step(k, *sk.xy(pid)), construction=sk.entities[pid].construction)
            return pmap[pid]

        for eid in curves:
            e = sk.entities[eid]
            if isinstance(e, Line):
                out.append(sk.line(pm(e.p1), pm(e.p2), construction=e.construction))
                sk.constrain("equal", eid, out[-1])
                if kind == "linear":
                    sk.constrain("parallel", eid, out[-1])
            elif isinstance(e, Circle):
                out.append(sk.circle(pm(e.center), e.radius, construction=e.construction))
                sk.constrain("equal", eid, out[-1])
            else:                                       # 平行移動・回転では向き（反時計回り）は変わらない
                out.append(sk.arc(pm(e.center), pm(e.start), pm(e.end), construction=e.construction))
                sk.constrain("equal", eid, out[-1])
        for pid in singles:
            out.append(pm(pid))
    return out


# ---------------------------------------------------------------- 面取り


def corner_lines(sk: Sketch, point: str) -> tuple[str, str]:
    """点を端点として共有する 2 本の線（角）。フィレット・面取りを「角の点」で指定するときに使う。"""
    ls = [k for k, e in sk.entities.items() if isinstance(e, Line) and point in (e.p1, e.p2)]
    if len(ls) != 2:
        raise ValueError("2 本の線が交わる角の点を選んでください")
    return ls[0], ls[1]


def chamfer(sk: Sketch, l1: str, l2: str, d1: float, d2: float | None = None) -> str:
    """角を共有する 2 本の線を、角から d1 / d2 の位置で切り落とす。面取りの線の ID を返す。
    元の角の点は「仮想交点」として残し（両線の延長上）、そこからの距離寸法 2 つで大きさを決める。"""
    L1, L2 = sk.entities.get(l1), sk.entities.get(l2)
    if not (isinstance(L1, Line) and isinstance(L2, Line)) or l1 == l2:
        raise ValueError("面取りは線と線に対して行います")
    d2 = d1 if d2 is None else d2
    if d1 <= 0 or d2 <= 0:
        raise ValueError("距離は正の値にしてください")
    corner = next(((a1, a2) for a1 in ("p1", "p2") for a2 in ("p1", "p2")
                   if getattr(L1, a1) == getattr(L2, a2)), None)
    if corner is None:
        raise ValueError("2 本の線が角を共有していません")
    a1, a2 = corner
    p = getattr(L1, a1)
    far1 = L1.p2 if a1 == "p1" else L1.p1
    far2 = L2.p2 if a2 == "p1" else L2.p1
    P, A, B = sk.xy(p), sk.xy(far1), sk.xy(far2)
    la, lb = math.dist(P, A), math.dist(P, B)
    if d1 >= la - EPS or d2 >= lb - EPS:
        raise ValueError("距離が大きすぎます")
    t1 = sk.point(P[0] + (A[0] - P[0]) / la * d1, P[1] + (A[1] - P[1]) / la * d1)
    t2 = sk.point(P[0] + (B[0] - P[0]) / lb * d2, P[1] + (B[1] - P[1]) / lb * d2)
    setattr(L1, a1, t1); setattr(L2, a2, t2)
    cut = sk.line(t1, t2)
    for k_ in sk.constraints.values():                 # 長さ寸法 → 仮想交点からの距離
        if k_.type == "length" and k_.refs[0] in (l1, l2):
            k_.type, k_.refs = "distance", [p, far1 if k_.refs[0] == l1 else far2]
    if _uses(sk, p):                                    # ほかの要素も使っている点は動かさず、新しい仮想交点を作る
        p = sk.point(*P)
    sk.constrain("point_on_line", p, l1); sk.constrain("point_on_line", p, l2)
    sk.constrain("distance", p, t1, value=d1); sk.constrain("distance", p, t2, value=d2)
    return cut

# ---------------------------------------------------------------- 延長


def extend(sk: Sketch, eid: str, x: float, y: float) -> str:
    """線の (x, y) に近い側の端を、その先で最初に当たる要素まで伸ばす。端点は相手の上に拘束する。"""
    e = sk.entities.get(eid)
    if not isinstance(e, Line):
        raise ValueError("延長できるのは線です")
    cv = _curve(sk, eid)
    end = "p2" if _param(cv, (x, y)) >= 0.5 else "p1"
    pid = getattr(e, end)
    if sum(pid in (getattr(o, a, None) for a in ("p1", "p2", "center", "start", "end"))
           for o in sk.entities.values() if not isinstance(o, Point)) > 1:
        raise ValueError("この端点はほかの要素につながっているので延長できません")
    best = None
    for oid, o in sk.entities.items():
        if oid == eid or isinstance(o, Point):
            continue
        ocv = _curve(sk, oid)
        for pt in _intersections(cv, ocv):
            if not _on(ocv, pt):
                continue
            t = _param(cv, pt)
            ahead = t - 1 if end == "p2" else -t
            if ahead > 1e-7 and (best is None or ahead < best[0]):
                best = (ahead, pt, oid)
    if best is None:
        raise ValueError("延長した先に当たる要素がありません")
    _, pt, oid = best
    P = sk.entities[pid]
    P.x, P.y = float(pt[0]), float(pt[1])
    sk.constraints = {i: k for i, k in sk.constraints.items() if not (k.type == "length" and k.refs == [eid])}
    sk.constrain("point_on_line" if isinstance(sk.entities[oid], Line) else "point_on_circle", pid, oid)
    return eid

# ---------------------------------------------------------------- オフセット


def offset(sk: Sketch, ids: list[str], distance: float, side: tuple | None = None, reverse: bool = False,
           link: str | None = None) -> list[str]:
    """要素を distance だけ離して写す（一般的な 3D CAD のエンティティ オフセット）。新しい要素の ID を返す。
    - つながった線・円弧は鎖として同じ側へオフセットし、端点は新しい交点で共有する
    - 線は「平行 + 元の線からの距離」、円・円弧は中心を共有して「半径の差」（offset 拘束）
    - 正接でつながる所（線と円弧、円弧と円弧）は、交点が重根になって解が不安定になるので別の形で決める:
      新しい端点を「元の端点と円弧の中心を結ぶ線の上」に置き（collinear）、その端点を通る線からは距離を
      （両端とも正接なら平行も）外し、後から処理する円弧からは半径の差を外す（D45）
    - 最初の寸法が親。link（"<フィーチャーID>." の形）を渡すと、残りは親を式で参照する（1 つ変えれば全部変わる）
    - side: オフセットする側の点 (x, y)。省くと進行方向の左（reverse で右）
    つながっていない端は線・円弧に沿って動ける（未定義のまま）。"""
    if distance <= 0:
        raise ValueError("オフセットの距離は正の値にしてください")
    ents = [i for i in dict.fromkeys(ids) if i in sk.entities and not isinstance(sk.entities[i], Point)]
    if not ents:
        raise ValueError("オフセットする線・円・円弧を選んでください")
    E = sk.entities
    ends = lambda i: (E[i].p1, E[i].p2) if isinstance(E[i], Line) else \
        (E[i].start, E[i].end) if isinstance(E[i], Arc) else ()

    # ---- 鎖ごとに進行方向をそろえる（flag = +1: 定義どおり / −1: 逆向き）
    flag: dict[str, int] = {}
    chains: list[list[str]] = []
    for seed in ents:
        if seed in flag:
            continue
        flag[seed] = 1
        chain, todo = [seed], [seed]
        while todo:
            a = todo.pop()
            for b in ents:
                if b in flag:
                    continue
                shared = set(ends(a)) & set(ends(b))
                if not shared:
                    continue
                p = next(iter(shared))
                a_head = (ends(a)[1] if flag[a] > 0 else ends(a)[0]) == p      # a の進行方向の先が p か
                # a の先が p なら b は p から始まる向き、a の元が p なら b は p で終わる向き
                flag[b] = 1 if (ends(b)[0] == p) == a_head else -1
                chain.append(b); todo.append(b)
        chains.append(chain)

    def left_of(i: str, pt) -> bool:
        """pt が要素 i の（定義どおりの進行方向の）左側か。円・円弧は内側が左。"""
        e = E[i]
        if isinstance(e, Line):
            (ax, ay), (bx, by) = sk.xy(e.p1), sk.xy(e.p2)
            return (bx - ax) * (pt[1] - ay) - (by - ay) * (pt[0] - ax) > 0
        return math.dist(pt, sk.xy(e.center)) < e.radius

    def dist_to(i: str, pt) -> float:
        e = E[i]
        if isinstance(e, Line):
            (ax, ay), (bx, by) = sk.xy(e.p1), sk.xy(e.p2)
            dx, dy = bx - ax, by - ay
            t = max(0.0, min(1.0, ((pt[0] - ax) * dx + (pt[1] - ay) * dy) / (dx * dx + dy * dy or 1.0)))
            return math.hypot(ax + t * dx - pt[0], ay + t * dy - pt[1])
        return abs(math.dist(pt, sk.xy(e.center)) - e.radius)

    # ---- 要素ごとの符号付きオフセット（定義どおりの進行方向の左が正）
    s: dict[str, float] = {}
    for chain in chains:
        if side is not None:
            near = min(chain, key=lambda i: dist_to(i, side))
            sgn = (1 if left_of(near, side) else -1) * flag[near]      # 鎖の進行方向に対する側
        else:
            sgn = -1 if reverse else 1
        for i in chain:
            s[i] = sgn * flag[i] * distance

    def curve(i: str):
        """オフセット後の曲線（無限直線 / 円）。"""
        e = E[i]
        if isinstance(e, Line):
            (ax, ay), (bx, by) = sk.xy(e.p1), sk.xy(e.p2)
            L = math.hypot(bx - ax, by - ay)
            if L < EPS:
                raise ValueError("長さ 0 の線はオフセットできません")
            nx, ny = -(by - ay) / L * s[i], (bx - ax) / L * s[i]
            return ("line", (ax + nx, ay + ny), (bx + nx, by + ny))
        r = e.radius - s[i]
        if r <= EPS:
            raise ValueError("オフセットが大きすぎます（円・円弧の半径が 0 以下になります）")
        return ("circle", sk.xy(e.center), r)

    cvs = {i: curve(i) for i in ents}

    def own_end(i: str, pid: str):
        """要素 i だけをオフセットしたときの、端点 pid の行き先。"""
        cv, (x, y) = cvs[i], sk.xy(pid)
        if cv[0] == "line":
            e = E[i]
            return cv[1] if pid == e.p1 else cv[2]
        (cx, cy), r = cv[1], cv[2]
        d = math.hypot(x - cx, y - cy) or 1.0
        return (cx + (x - cx) / d * r, cy + (y - cy) / d * r)

    def tangent_dir(i: str, pid: str):
        e = E[i]
        if isinstance(e, Line):
            (ax, ay), (bx, by) = sk.xy(e.p1), sk.xy(e.p2)
            L = math.hypot(bx - ax, by - ay)
            return ((bx - ax) / L, (by - ay) / L)
        (cx, cy), (x, y) = sk.xy(e.center), sk.xy(pid)
        d = math.hypot(x - cx, y - cy) or 1.0
        return (-(y - cy) / d, (x - cx) / d)

    # ---- 新しい端点（共有されていた端点は、2 つのオフセット曲線の交点で共有する）
    newp: dict[tuple[str, str], str] = {}
    smooth: dict[str, tuple[str, str]] = {}          # 正接でつながる端点 → (要素, 要素)。少なくとも片方は円弧
    for i in ents:
        for pid in ends(i):
            if (i, pid) in newp:
                continue
            mates = [j for j in ents if j != i and pid in ends(j)]
            if len(mates) == 1:
                j = mates[0]
                ti, tj = tangent_dir(i, pid), tangent_dir(j, pid)
                arcs = [k for k in (i, j) if isinstance(E[k], Arc)]
                if arcs and abs(ti[0] * tj[1] - ti[1] * tj[0]) < 1e-7:
                    smooth[pid] = (i, j)
                    newp[(i, pid)] = newp[(j, pid)] = sk.point(*own_end(arcs[0], pid))
                    continue
                hits = _intersections(cvs[i], cvs[j])
                if hits:
                    o = sk.xy(pid)
                    newp[(i, pid)] = newp[(j, pid)] = sk.point(*min(hits, key=lambda h: math.dist(h, o)))
                    continue
                a, b = own_end(i, pid), own_end(j, pid)
                if math.dist(a, b) < 1e-9 * max(1.0, distance):            # 一直線につながる 2 本
                    newp[(i, pid)] = newp[(j, pid)] = sk.point(*a)
                    continue
            newp[(i, pid)] = sk.point(*own_end(i, pid))

    # ---- 要素と拘束
    out, master = [], None

    def dim(cid: str):
        nonlocal master
        if master is None:
            master = cid
        elif link:
            sk.constraints[cid].expr = f"{link}{master}"

    done_arcs: set[str] = set()
    for i in ents:
        e = E[i]
        if isinstance(e, Line):
            n = sk.line(newp[(i, e.p1)], newp[(i, e.p2)], construction=e.construction)
            n_smooth = sum(p in smooth for p in (e.p1, e.p2))
            if n_smooth < 2:
                sk.constrain("parallel", n, i)
            if n_smooth == 0:
                # 距離を測る点は、共有していない端（あれば）にする
                dim(sk.constrain("distance", sk.entities[n].p1, i, value=distance))
        elif isinstance(e, Circle):
            n = sk.circle(e.center, cvs[i][2], construction=e.construction)
            dim(sk.constrain("offset", n, i, value=distance))
        else:
            n = sk.arc(e.center, newp[(i, e.start)], newp[(i, e.end)], construction=e.construction)
            sk.entities[n].radius = cvs[i][2]
            # 先に処理した円弧と正接でつながっていれば、半径はその端点から決まる
            if not any(p in smooth and any(k in done_arcs for k in smooth[p] if k != i) for p in (e.start, e.end)):
                dim(sk.constrain("offset", n, i, value=distance))
            for p in (e.start, e.end):
                if p in smooth and not any(k in done_arcs for k in smooth[p] if k != i):
                    sk.constrain("collinear", e.center, p, newp[(i, p)])
            done_arcs.add(i)
        out.append(n)
    return out
