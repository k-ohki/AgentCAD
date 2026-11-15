"""2D スケッチ: エンティティ + 幾何拘束 + 寸法拘束。

Sketch はシリアライズ可能な「定義」だけを持ち、solve() のたびに solver.System を組み立てる
（System を永続状態にしないので、Undo・保存・差分再生成が単純になる）。
点の座標は「定義」であると同時にソルバの初期値でもある。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..solver import SolveStatus, System
from . import kinds as K

# ---------------------------------------------------------------- エンティティ


@dataclass
class Point:
    x: float
    y: float
    fixed: bool = False
    construction: bool = False


@dataclass
class Line:
    p1: str
    p2: str
    construction: bool = False


@dataclass
class Circle:
    center: str
    radius: float
    construction: bool = False


@dataclass
class Arc:
    """中心・始点・終点（反時計回り）＋半径変数。始点・終点が円上にあることは暗黙拘束。"""
    center: str
    start: str
    end: str
    radius: float = 0.0
    construction: bool = False


ENTITY_TYPES = {"point": Point, "line": Line, "circle": Circle, "arc": Arc}
ROUND = (Circle, Arc)

#: 拘束種別 → (参照の型の組み合わせ, 寸法値を持つか)
CONSTRAINT_TYPES: dict[str, tuple[list[tuple], bool]] = {
    "coincident": ([(Point, Point)], False),
    "horizontal": ([(Line,), (Point, Point)], False),
    "vertical": ([(Line,), (Point, Point)], False),
    "parallel": ([(Line, Line)], False),
    "perpendicular": ([(Line, Line)], False),
    "equal": ([(Line, Line), (ROUND, ROUND)], False),
    "point_on_line": ([(Point, Line)], False),
    "point_on_circle": ([(Point, ROUND)], False),
    "midpoint": ([(Point, Line)], False),
    "tangent": ([(Line, ROUND), (ROUND, ROUND)], False),
    "concentric": ([(ROUND, ROUND)], False),
    "symmetric": ([(Point, Point, Line)], False),
    #: 3 点が一直線（オフセットした円弧の端点を、元の端点と中心を結ぶ線の上に保つ）
    "collinear": ([(Point, Point, Point)], False),
    "fix": ([(Point,)], False),
    #: 外部参照（ほかのスケッチ）: 点を、ほかのスケッチの点（source = "スケッチID:点ID"）の投影に一致させる /
    #: ほかのスケッチの線（source = "スケッチID:線ID"）の投影の上に置く。data は投影した座標（再生成で更新）
    "ext_point": ([(Point,)], False),
    "ext_line": ([(Point,)], False),
    "distance": ([(Point, Point), (Point, Line)], True),
    "hdistance": ([(Point, Point)], True),
    "vdistance": ([(Point, Point)], True),
    "length": ([(Line,)], True),
    "radius": ([(ROUND,)], True),
    "diameter": ([(ROUND,)], True),
    #: オフセット（円・円弧）: 半径の差 = 値。refs = [新しい方, 元]（線のオフセットは 平行 + 距離 で表す）
    "offset": ([(ROUND, ROUND)], True),
    #: 投影（エンティティ変換）: 要素をモデルのエッジ（source）の投影に固定する。data は投影した形
    "on_edge": ([(Line,), (Circle,), (Arc,)], False),
    "angle": ([(Line, Line)], True),          # 線1→線2 の符号付き角度（度）
}


@dataclass
class SketchConstraint:
    type: str
    refs: list[str]
    value: float | None = None
    #: 解の枝（正接の側、距離の向きなど）。作成時の形状から決めて保持する
    sign: float = 1.0
    #: fix の固定座標 / 円同士の正接の係数（外接 [1,1]、内接 [1,-1] か [-1,1]）
    data: list[float] | None = None
    #: 式（例 "width/2"）。あれば再生成時に value を上書きする
    expr: str | None = None
    #: 寸法の文字の位置（既定の位置からのずれ、スケッチ座標）。None なら既定の位置
    label: list[float] | None = None
    #: 投影元（on_edge）: モデルのエッジの永続名
    source: str | None = None


@dataclass
class SketchSolveResult:
    status: str                       # ok / inconsistent
    dof: int
    state: str                        # under / full / over（スケッチ全体）
    redundant: list[str]
    conflicting: list[str]
    entity_state: dict[str, str]      # エンティティ毎の定義状態（UI の色分け用）


@dataclass
class _Build:
    sys: System
    idx: dict[str, int]               # 点 → x の位置（y は +1）、円/円弧 → 半径変数の位置
    n_implicit: int
    cmap: list[str]                   # ソルバ拘束番号 − n_implicit → スケッチ拘束ID


@dataclass
class Sketch:
    entities: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, SketchConstraint] = field(default_factory=dict)
    _seq: int = 0

    # ------------------------------------------------------------ 作成
    def _id(self, prefix):
        self._seq += 1
        return f"{prefix}{self._seq}"

    def point(self, x, y, fixed=False, construction=False) -> str:
        i = self._id("P"); self.entities[i] = Point(float(x), float(y), fixed, construction); return i

    def _p(self, p) -> str:
        return p if isinstance(p, str) else self.point(*p)

    def line(self, p1: str | tuple, p2: str | tuple, construction=False) -> str:
        p1, p2 = self._p(p1), self._p(p2)
        i = self._id("L"); self.entities[i] = Line(p1, p2, construction); return i

    def circle(self, center: str | tuple, radius: float, construction=False) -> str:
        center = self._p(center)
        i = self._id("C"); self.entities[i] = Circle(center, float(radius), construction); return i

    def arc(self, center, start, end, construction=False) -> str:
        center, start, end = self._p(center), self._p(start), self._p(end)
        r = math.dist(self.xy(center), self.xy(start))
        i = self._id("A"); self.entities[i] = Arc(center, start, end, r, construction); return i

    def rect(self, x0, y0, x1, y1) -> list[str]:
        """矩形（4線 + 水平/垂直）。線IDを返す。"""
        ps = [self.point(x0, y0), self.point(x1, y0), self.point(x1, y1), self.point(x0, y1)]
        ls = [self.line(ps[i], ps[(i + 1) % 4]) for i in range(4)]
        self.constrain("horizontal", ls[0]); self.constrain("horizontal", ls[2])
        self.constrain("vertical", ls[1]); self.constrain("vertical", ls[3])
        return ls

    def center_rect(self, cx, cy, x1, y1, center: str | None = None) -> list[str]:
        """中心矩形（一般的な 3D CAD と同じ作り）: 矩形 + 対角の作図線 2 本 + 中心点が対角線の中点。線 ID（4 辺, 対角 2 本）を返す。"""
        ls = self.rect(2 * cx - x1, 2 * cy - y1, x1, y1)
        ps = [self.entities[l].p1 for l in ls]
        c = center or self.point(cx, cy)
        d1 = self.line(ps[0], ps[2], construction=True)
        d2 = self.line(ps[1], ps[3], construction=True)
        self.constrain("midpoint", c, d1)
        return ls + [d1, d2]

    def pattern(self, ids: list[str], kind: str, count: int, **kw) -> list[str]:
        """直線・円形パターン（ops.pattern）。"""
        from .ops import pattern
        return pattern(self, ids, kind, count, **kw)

    def mirror(self, ids: list[str], axis: str) -> list[str]:
        from .ops import mirror
        return mirror(self, ids, axis)

    def slot(self, c1, c2, radius: float) -> list[str]:
        """直線スロット（長穴）: 中心線（作図線）+ 両端の半円 + 2 本の線。正接 ×4 と「半径が等しい」で形を保つ。
        [線, 円弧, 線, 円弧, 中心線] を返す。"""
        c1, c2 = self._p(c1), self._p(c2)
        (x1, y1), (x2, y2) = self.xy(c1), self.xy(c2)
        L = math.hypot(x2 - x1, y2 - y1)
        if L < 1e-9 or radius <= 0:
            raise ValueError("スロットの長さと幅は正の値にしてください")
        nx, ny = -(y2 - y1) / L * radius, (x2 - x1) / L * radius
        a1, a2 = self.point(x1 + nx, y1 + ny), self.point(x2 + nx, y2 + ny)
        b1, b2 = self.point(x1 - nx, y1 - ny), self.point(x2 - nx, y2 - ny)
        lb, arc2 = self.line(b1, b2), self.arc(c2, b2, a2)
        la, arc1 = self.line(a2, a1), self.arc(c1, a1, b1)
        axis = self.line(c1, c2, construction=True)
        for ln, ar in ((lb, arc2), (la, arc2), (la, arc1), (lb, arc1)):
            self.constrain("tangent", ln, ar)
        self.constrain("equal", arc1, arc2)
        return [lb, arc2, la, arc1, axis]

    def polygon(self, center, vertex, sides: int = 6) -> list[str]:
        """正多角形: 頂点が作図線の円（外接円）に乗り、辺の長さが等しい。[辺…, 外接円] を返す。"""
        if not 3 <= sides <= 40:
            raise ValueError("辺の数は 3〜40 にしてください")
        center = self._p(center)
        cx, cy = self.xy(center)
        vx, vy = self.xy(vertex) if isinstance(vertex, str) else vertex
        R, a0 = math.hypot(vx - cx, vy - cy), math.atan2(vy - cy, vx - cx)
        if R < 1e-9:
            raise ValueError("大きさ 0 の多角形は作れません")
        circ = self.circle(center, R, construction=True)
        ps = [self._p(vertex)] + [self.point(cx + R * math.cos(a0 + 2 * math.pi * i / sides),
                                             cy + R * math.sin(a0 + 2 * math.pi * i / sides)) for i in range(1, sides)]
        ls = [self.line(ps[i], ps[(i + 1) % sides]) for i in range(sides)]
        for p in ps:
            self.constrain("point_on_circle", p, circ)
        for l in ls[1:]:
            self.constrain("equal", ls[0], l)
        return ls + [circ]

    def chamfer(self, l1: str, l2: str, d1: float, d2: float | None = None) -> str:
        from .ops import chamfer
        return chamfer(self, l1, l2, d1, d2)

    def extend(self, eid: str, x: float, y: float) -> str:
        from .ops import extend
        return extend(self, eid, x, y)

    def offset(self, ids: list[str], distance: float, side: tuple | None = None, reverse: bool = False,
               link: str | None = None) -> list[str]:
        from .ops import offset
        return offset(self, ids, distance, side, reverse, link)

    def constrain(self, type: str, *refs: str, value: float | None = None) -> str:
        if type not in CONSTRAINT_TYPES:
            raise ValueError(f"unknown constraint {type}")
        c = SketchConstraint(type, list(refs), value)
        self._check_refs(c)
        self._init_branch(c)
        if CONSTRAINT_TYPES[type][1] and c.value is None:
            c.value = self._measure(c)          # 寸法挿入時は現在の形状の値を使う
        i = self._id("K"); self.constraints[i] = c
        return i

    def delete(self, eid: str):
        """エンティティまたは拘束を削除する。依存するエンティティ・拘束も削除する。"""
        if eid in self.constraints:
            del self.constraints[eid]; return
        doomed = {eid}
        if isinstance(self.entities[eid], Point):
            doomed |= {k for k, e in self.entities.items() if not isinstance(e, Point)
                       and eid in (getattr(e, "p1", None), getattr(e, "p2", None), getattr(e, "center", None),
                                   getattr(e, "start", None), getattr(e, "end", None))}
        for k in doomed:
            del self.entities[k]
        self.constraints = {k: c for k, c in self.constraints.items() if not doomed & set(c.refs)}

    def fillet(self, l1: str, l2: str, radius: float) -> str:
        """角をフィレットする（ops.fillet）。"""
        from .ops import fillet
        return fillet(self, l1, l2, radius)

    def trim(self, eid: str, x: float, y: float) -> list[str]:
        """(x, y) 付近の区間を交点まで切り取る（ops.trim）。"""
        from .ops import trim
        return trim(self, eid, x, y)

    # ------------------------------------------------------------ 参照
    def xy(self, pid: str) -> tuple[float, float]:
        p = self.entities[pid]
        return p.x, p.y

    def _pt(self, pid) -> Point:
        return self.entities[pid]

    def _line_xy(self, lid):
        L = self.entities[lid]
        return (*self.xy(L.p1), *self.xy(L.p2))

    def _sig(self, c: SketchConstraint) -> tuple:
        """参照の型の組み合わせのうち、実際に一致したものを返す。"""
        try:
            ents = [self.entities[r] for r in c.refs]
        except KeyError as e:
            raise ValueError(f"エンティティ {e.args[0]} がありません")
        for sig in CONSTRAINT_TYPES[c.type][0]:
            if len(sig) == len(ents) and all(isinstance(e, s) for e, s in zip(ents, sig)):
                return sig
        names = " / ".join(",".join(getattr(s, "__name__", "Circle|Arc") for s in sig)
                           for sig in CONSTRAINT_TYPES[c.type][0])
        raise ValueError(f"{c.type} の参照が不正です（必要: {names}）")

    _check_refs = _sig

    def _signed_dist(self, pid, lid) -> float:
        px, py = self.xy(pid)
        ax, ay, bx, by = self._line_xy(lid)
        return ((bx - ax) * (py - ay) - (by - ay) * (px - ax)) / (math.hypot(bx - ax, by - ay) or 1.0)

    def _init_branch(self, c: SketchConstraint):
        """解の枝を現在の形状から決める。"""
        t, sig, r = c.type, self._sig(c), c.refs
        sgn = lambda v: 1.0 if v >= 0 else -1.0
        if t == "tangent" and sig[0] is Line:
            c.sign = sgn(self._signed_dist(self.entities[r[1]].center, r[0]))
        elif t == "tangent":
            e1, e2 = self.entities[r[0]], self.entities[r[1]]
            d = math.dist(self.xy(e1.center), self.xy(e2.center))
            if abs(d - (e1.radius + e2.radius)) <= abs(d - abs(e1.radius - e2.radius)):
                c.data = [1.0, 1.0]                                  # 外接
            else:
                c.data = [1.0, -1.0] if e1.radius >= e2.radius else [-1.0, 1.0]   # 内接
        elif t == "distance" and sig[1] is Line:
            c.sign = sgn(self._signed_dist(r[0], r[1]))
        elif t in ("hdistance", "vdistance"):
            k = 0 if t == "hdistance" else 1
            c.sign = sgn(self.xy(r[1])[k] - self.xy(r[0])[k])
        elif t == "fix":
            c.data = list(self.xy(r[0]))
        elif t == "offset":
            c.sign = sgn(self.entities[r[0]].radius - self.entities[r[1]].radius)
        elif t == "on_edge" and c.data is None:
            c.data = self.entity_geom(r[0])

    def _measure(self, c: SketchConstraint) -> float:
        t, sig, r = c.type, self._sig(c), c.refs
        if t == "distance":
            return abs(self._signed_dist(r[0], r[1])) if sig[1] is Line else math.dist(self.xy(r[0]), self.xy(r[1]))
        if t in ("hdistance", "vdistance"):
            k = 0 if t == "hdistance" else 1
            return abs(self.xy(r[1])[k] - self.xy(r[0])[k])
        if t == "length":
            ax, ay, bx, by = self._line_xy(r[0]); return math.hypot(bx - ax, by - ay)
        if t == "radius":
            return self.entities[r[0]].radius
        if t == "diameter":
            return 2 * self.entities[r[0]].radius
        if t == "offset":
            return abs(self.entities[r[0]].radius - self.entities[r[1]].radius)
        if t == "angle":
            ax, ay, bx, by = self._line_xy(r[0]); cx, cy, dx, dy = self._line_xy(r[1])
            ux, uy, wx, wy = bx - ax, by - ay, dx - cx, dy - cy
            return math.degrees(math.atan2(ux * wy - uy * wx, ux * wx + uy * wy))
        raise ValueError(t)

    # ------------------------------------------------------------ System 構築
    def _build(self) -> _Build:
        sys = System()
        idx: dict[str, int] = {}
        for eid, e in self.entities.items():
            if isinstance(e, Point):
                idx[eid] = sys.add_param(e.x, e.fixed)
                sys.add_param(e.y, e.fixed)
        for eid, e in self.entities.items():
            if isinstance(e, ROUND):
                idx[eid] = sys.add_param(e.radius)
        pt = lambda r: [idx[r], idx[r] + 1]
        for eid, e in self.entities.items():
            if isinstance(e, Arc):
                for end in (e.start, e.end):
                    sys.add(K.POINT_ON_CIRCLE, pt(end) + pt(e.center) + [idx[eid]])
        n_implicit = sys.n_constraints
        cmap = []
        for cid, c in self.constraints.items():
            sys.add(*self._spec(c, idx))
            cmap.append(cid)
        return _Build(sys, idx, n_implicit, cmap)

    def _spec(self, c: SketchConstraint, idx):
        """スケッチ拘束 → (Kind, 変数, 定数)。"""
        E, t, r, sig = self.entities, c.type, c.refs, self._sig(c)
        pt = lambda i: [idx[r[i]], idx[r[i]] + 1]
        ln = lambda i: [idx[E[r[i]].p1], idx[E[r[i]].p1] + 1, idx[E[r[i]].p2], idx[E[r[i]].p2] + 1]
        ctr = lambda i: [idx[E[r[i]].center], idx[E[r[i]].center] + 1]
        rad = lambda i: [idx[r[i]]]
        val = c.value if c.value is not None else 0.0
        if t == "coincident":
            return K.COINCIDENT, pt(0) + pt(1), ()
        if t in ("horizontal", "vertical"):
            kind = K.HORIZONTAL if t == "horizontal" else K.VERTICAL
            return kind, (ln(0) if sig[0] is Line else pt(0) + pt(1)), ()
        if t == "parallel":
            return K.PARALLEL, ln(0) + ln(1), ()
        if t == "perpendicular":
            return K.PERPENDICULAR, ln(0) + ln(1), ()
        if t == "angle":
            return K.ANGLE, ln(0) + ln(1), (math.radians(val),)
        if t == "equal":
            return (K.EQUAL_LEN, ln(0) + ln(1), ()) if sig[0] is Line else (K.EQUAL_PARAM, rad(0) + rad(1), ())
        if t == "point_on_line":
            return K.POINT_ON_LINE, pt(0) + ln(1), ()
        if t == "point_on_circle":
            return K.POINT_ON_CIRCLE, pt(0) + ctr(1) + rad(1), ()
        if t == "midpoint":
            return K.MIDPOINT, pt(0) + ln(1), ()
        if t == "tangent":
            tp = self._tangent_point(c)
            tpp = [idx[tp], idx[tp] + 1] if tp is not None else None
            if sig[0] is Line:
                if tp is not None:
                    return K.TANGENT_AT, ln(0) + ctr(1) + tpp, ()
                return K.TANGENT, ln(0) + ctr(1) + rad(1), (c.sign,)
            if tp is not None:
                return K.TANGENT_CC_AT, ctr(0) + ctr(1) + tpp, ()
            return K.TANGENT_CC, ctr(0) + ctr(1) + rad(0) + rad(1), tuple(c.data)
        if t == "concentric":
            return K.COINCIDENT, ctr(0) + ctr(1), ()
        if t == "symmetric":
            return K.SYMMETRIC, pt(0) + pt(1) + ln(2), ()
        if t == "collinear":
            return K.COLLINEAR3, pt(0) + pt(1) + pt(2), ()
        if t in ("fix", "ext_point"):
            return K.FIX, pt(0), tuple(c.data)
        if t == "ext_line":
            return K.ON_FIXED_LINE, pt(0), tuple(c.data)
        if t == "distance":
            if sig[1] is Line:
                return K.DIST_PL, pt(0) + ln(1), (val, c.sign)
            return K.DIST_PP, pt(0) + pt(1), (val,)
        if t in ("hdistance", "vdistance"):
            return (K.HDIST if t == "hdistance" else K.VDIST), pt(0) + pt(1), (val, c.sign)
        if t == "length":
            return K.LENGTH, ln(0), (val,)
        if t == "radius":
            return K.PARAM_VALUE, rad(0), (val,)
        if t == "diameter":
            return K.PARAM_VALUE, rad(0), (val / 2,)
        if t == "offset":
            return K.PARAM_DIFF, rad(0) + rad(1), (c.sign * val,)
        if t == "on_edge":
            if sig[0] is Line:
                return K.FIX_LINE, ln(0), tuple(c.data)
            e = E[r[0]]
            if sig[0] is Circle:
                return K.FIX_CIRCLE, ctr(0) + rad(0), tuple(c.data)
            return K.FIX_ARC, ctr(0) + [idx[e.start], idx[e.start] + 1, idx[e.end], idx[e.end] + 1], tuple(c.data)
        raise ValueError(t)

    def entity_geom(self, eid: str) -> list[float]:
        """要素の形（線: 両端 / 円: 中心, r / 円弧: 中心, 始点, 終点）。on_edge の data と同じ並び。"""
        e = self.entities[eid]
        if isinstance(e, Line):
            return [*self.xy(e.p1), *self.xy(e.p2)]
        if isinstance(e, Circle):
            return [*self.xy(e.center), e.radius]
        return [*self.xy(e.center), *self.xy(e.start), *self.xy(e.end)]

    def set_geom(self, eid: str, g: list[float]):
        """要素の座標を g（entity_geom の並び）にする（投影し直したときの初期値）。"""
        e = self.entities[eid]
        put = lambda pid, x, y: setattr(self.entities[pid], "x", float(x)) or setattr(self.entities[pid], "y", float(y))
        if isinstance(e, Line):
            put(e.p1, g[0], g[1]); put(e.p2, g[2], g[3])
        elif isinstance(e, Circle):
            put(e.center, g[0], g[1]); e.radius = float(g[2])
        else:
            put(e.center, g[0], g[1]); put(e.start, g[2], g[3]); put(e.end, g[4], g[5])
            e.radius = math.hypot(g[2] - g[0], g[3] - g[1])

    def _points_on(self, rid: str) -> set[str]:
        """円/円弧 rid の上にあると分かっている点（円弧の端点、点-円上拘束、それらと一致する点）。"""
        e = self.entities[rid]
        on = {e.start, e.end} if isinstance(e, Arc) else set()
        for k in self.constraints.values():
            if k.type == "point_on_circle" and k.refs[1] == rid:
                on.add(k.refs[0])
        for _ in range(2):
            for k in self.constraints.values():
                if k.type == "coincident" and on.intersection(k.refs):
                    on.update(k.refs)
        return on

    def _tangent_point(self, c: SketchConstraint) -> str | None:
        """正接する 2 要素が共有する点（接点）が分かっていれば返す。
        その場合「距離 = 半径」型の式は重根になりヤコビアンが 0 になる（自由度を誤判定する）ので、
        接点を使った定式化（半径方向 ⟂ 線 / 中心と接点が一直線）に切り替える。"""
        a, b = self.entities[c.refs[0]], self.entities[c.refs[1]]
        on_a = {a.p1, a.p2} if isinstance(a, Line) else self._points_on(c.refs[0])
        shared = sorted(on_a & self._points_on(c.refs[1]))
        return shared[0] if shared else None

    # ------------------------------------------------------------ 求解
    def solve(self) -> SketchSolveResult:
        """拘束を解いて形状を更新する。解けない場合は形状を変えない。"""
        b = self._build()
        start = b.sys.x.copy()
        res = b.sys.solve()
        if res.status == SolveStatus.OK:
            self._writeback(b)
        else:
            b.sys.x = start
        return self._result(b, res.status)

    def drag(self, pid: str, x: float, y: float, diagnose: bool = True) -> SketchSolveResult | None:
        """点を (x, y) へ引っ張る。マウス移動ごとに呼ぶときは diagnose=False で速くできる。"""
        b = self._build()
        i = b.idx[pid]
        res = b.sys.drag({i: x, i + 1: y})
        self._writeback(b)
        return self._result(b, res.status) if diagnose else None

    def drag_many(self, points: dict[str, tuple[float, float]], radii: dict[str, float] | None = None,
                  diagnose: bool = False) -> SketchSolveResult | None:
        """複数の点・半径を同時に引っ張る（線の平行移動、円の大きさの変更）。"""
        b = self._build()
        targets = {}
        for pid, (x, y) in points.items():
            targets[b.idx[pid]] = float(x); targets[b.idx[pid] + 1] = float(y)
        for rid, r in (radii or {}).items():
            targets[b.idx[rid]] = float(r)
        res = b.sys.drag(targets)
        self._writeback(b)
        return self._result(b, res.status) if diagnose else None

    def _writeback(self, b: _Build):
        x = b.sys.x
        for eid, e in self.entities.items():
            i = b.idx[eid] if eid in b.idx else None
            if isinstance(e, Point):
                e.x, e.y = float(x[i]), float(x[i + 1])
            elif isinstance(e, ROUND):
                e.radius = float(x[i])

    def _entity_params(self, e, eid, idx):
        pt = lambda p: [idx[p], idx[p] + 1]
        if isinstance(e, Point):
            return pt(eid)
        if isinstance(e, Line):
            return pt(e.p1) + pt(e.p2)
        if isinstance(e, Circle):
            return pt(e.center) + [idx[eid]]
        return pt(e.center) + pt(e.start) + pt(e.end) + [idx[eid]]

    def _result(self, b: _Build, status) -> SketchSolveResult:
        d = b.sys.diagnose()
        cids = lambda lst: [b.cmap[i - b.n_implicit] for i in lst if i >= b.n_implicit]
        free = set(d.free_params)
        state = {eid: ("under" if free.intersection(self._entity_params(e, eid, b.idx)) else "full")
                 for eid, e in self.entities.items()}
        red, con = cids(d.redundant), cids(d.conflicting)
        for cid in red + con:
            for r in self.constraints[cid].refs:
                state[r] = "over"
        return SketchSolveResult(status.value, d.dof, d.state, red, con, state)

    # ------------------------------------------------------------ 閉ループ抽出（押し出し用）
    def profiles(self, tol=1e-6) -> list[list[str]]:
        """非作図要素の閉ループを返す。円は単独で1ループ。
        線/円弧は端点座標で接続し、すべての端点の次数が2の連結成分をループとみなす。"""
        loops = []
        segs = {}
        for eid, e in self.entities.items():
            if getattr(e, "construction", False):
                continue
            if isinstance(e, Circle):
                loops.append([eid])
            elif isinstance(e, Line):
                segs[eid] = (e.p1, e.p2)
            elif isinstance(e, Arc):
                segs[eid] = (e.start, e.end)
        keys: dict[str, tuple] = {}

        def key(pid):
            x, y = self.xy(pid)
            for k, (kx, ky) in keys.items():
                if abs(kx - x) < tol and abs(ky - y) < tol:
                    return k
            keys[pid] = (x, y)
            return pid
        adj: dict[str, list[str]] = {}
        ends = {}
        for sid, (a, b_) in segs.items():
            ka, kb = key(a), key(b_)
            ends[sid] = (ka, kb)
            adj.setdefault(ka, []).append(sid)
            adj.setdefault(kb, []).append(sid)
        used = set()
        for sid in segs:
            if sid in used:
                continue
            loop, cur, start = [sid], ends[sid][1], ends[sid][0]
            used.add(sid)
            ok = True
            while cur != start:
                nxt = [s for s in adj[cur] if s not in used]
                if len(adj[cur]) != 2 or not nxt:
                    ok = False; break
                s = nxt[0]; used.add(s); loop.append(s)
                a, b_ = ends[s]
                cur = b_ if a == cur else a
            if ok and all(len(adj[k]) == 2 for s in loop for k in ends[s]):
                loops.append(loop)
        return loops

    # ------------------------------------------------------------ シリアライズ
    def to_dict(self) -> dict:
        ents = {}
        for k, e in self.entities.items():
            t = next(n for n, cls in ENTITY_TYPES.items() if isinstance(e, cls))
            ents[k] = {"type": t, **e.__dict__}
        return {"entities": ents,
                "constraints": {k: dict(c.__dict__) for k, c in self.constraints.items()},
                "seq": self._seq}

    @classmethod
    def from_dict(cls, d: dict) -> "Sketch":
        s = cls()
        for k, e in d["entities"].items():
            e = dict(e); t = e.pop("type")
            s.entities[k] = ENTITY_TYPES[t](**e)
        for k, c in d["constraints"].items():
            s.constraints[k] = SketchConstraint(**c)
        s._seq = d["seq"]
        return s
