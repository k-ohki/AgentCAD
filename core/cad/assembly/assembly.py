"""アセンブリと合致。

- 部品の姿勢 = 位置 t(3) + 四元数 q(4)。|q|=1 を拘束に入れるので自由度は 6
- 合致はスケッチと同じソルバで解く（診断・ドラッグも共通）。残差定義は kinds.py
- 合致の参照は (部品ID, 幾何名)。幾何名はパートの永続名なので、パート再生成後も維持される
- 向き（法線・軸が同じ向きか逆向きか）は 2 つの解の枝になる。求解前に部品を正しい側へ
  回しておき（事前位置合わせ）、求解後に確認して、違っていれば flipped として報告する
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from ..doc.expr import ExprError, Variables, evaluate
from ..doc.history import History
from ..solver import Kind, SolveStatus, System
from . import kinds as M

# ---------------------------------------------------------------- 幾何


@dataclass(frozen=True)
class GPlane:
    origin: tuple
    normal: tuple


@dataclass(frozen=True)
class GAxis:
    origin: tuple
    direction: tuple
    radius: float | None = None       # 円筒面なら半径（正接合致に使う）


@dataclass(frozen=True)
class GPoint:
    p: tuple


Geom = GPlane | GAxis | GPoint


class GeometrySource(Protocol):
    """部品のローカル幾何を名前で返す。"""

    def geometry(self, name: str) -> Geom: ...


class StaticSource:
    """名前 → 幾何 の辞書（テスト・簡易部品用）。"""

    def __init__(self, geoms: dict):
        self.geoms = geoms

    def geometry(self, name):
        return self.geoms[name]


class PartSource:
    """PartDocument から幾何を取る。
    面の永続名 → 平面なら GPlane / 円筒なら GAxis。"Origin" → 原点。"Front"/"Top"/"Right" → 基準平面。"""

    def __init__(self, part):
        self.part = part

    def geometry(self, name):
        from ..kernel.base import STANDARD_PLANES
        if name == "Origin":
            return GPoint((0.0, 0.0, 0.0))
        if name in STANDARD_PLANES:
            pl = STANDARD_PLANES[name]
            return GPlane(pl.origin, pl.normal)
        p = self.part
        if p.names is None:
            raise KeyError(f"面 {name} が見つかりません（ボディがありません）")
        h = p.names.find_face(name).handle
        ax = p.kernel.face_axis(p.body, h)
        if ax is not None:
            return GAxis(*ax, p.kernel.face_radius(p.body, h))
        pl = p.kernel.face_plane(p.body, h)
        if pl is not None:
            return GPlane(pl.origin, pl.normal)
        raise KeyError(f"面 {name} は合致に使えない形状です")


def _unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def _perp_basis(n):
    n = _unit(n)
    a = np.array([1.0, 0, 0]) if abs(n[0]) < 0.9 else np.array([0, 1.0, 0])
    u = _unit(np.cross(n, a))
    return u, np.cross(n, u)


def quat_rotate(q, v):
    q = np.asarray(q, float); q = q / np.linalg.norm(q)
    w, qv = q[0], q[1:]
    t = np.cross(qv, np.asarray(v, float))
    return np.asarray(v, float) + 2 * (w * t + np.cross(qv, t))


def quat_mul(a, b):
    aw, av, bw, bv = a[0], np.asarray(a[1:]), b[0], np.asarray(b[1:])
    return (aw * bw - av @ bv, *(aw * bv + bw * av + np.cross(av, bv)))


def quat_from_axis_angle(axis, deg):
    a = _unit(axis) * math.sin(math.radians(deg) / 2)
    return (math.cos(math.radians(deg) / 2), *a.tolist())


def _dir(g):
    return None if isinstance(g, GPoint) else np.asarray(g.normal if isinstance(g, GPlane) else g.direction, float)


def _org(g):
    return np.asarray(g.p if isinstance(g, GPoint) else g.origin, float)


def _to_world(pos, rot, g: Geom) -> Geom:
    o = np.asarray(pos) + quat_rotate(rot, _org(g))
    if isinstance(g, GPoint):
        return GPoint(tuple(o))
    d = quat_rotate(rot, _unit(_dir(g)))
    return GPlane(tuple(o), tuple(d)) if isinstance(g, GPlane) else GAxis(tuple(o), tuple(d), g.radius)

# ---------------------------------------------------------------- データ


@dataclass
class Component:
    id: str
    source: str                       # 参照元（Assembly.sources のキー。通常はパートファイルのパス）
    position: tuple = (0.0, 0.0, 0.0)
    rotation: tuple = (1.0, 0.0, 0.0, 0.0)   # w,x,y,z
    fixed: bool = False

    def matrix(self) -> np.ndarray:
        m = np.eye(4)
        for i, e in enumerate(np.eye(3)):
            m[:3, i] = quat_rotate(self.rotation, e)
        m[:3, 3] = self.position
        return m


MATE_TYPES = ("coincident", "parallel", "perpendicular", "distance", "angle", "concentric", "tangent", "lock")

_P, _A, _T = GPlane, GAxis, GPoint
#: (合致, 幾何A, 幾何B) → Kind。A/B は必要なら入れ替えて (平面, x) / (軸, 点) の順に正規化する
_KINDS: dict[tuple, Kind] = {
    ("coincident", _P, _P): M.PLANE_PLANE, ("distance", _P, _P): M.PLANE_PLANE,
    ("coincident", _P, _T): M.PLANE_POINT, ("distance", _P, _T): M.PLANE_POINT,
    ("coincident", _P, _A): M.PLANE_AXIS, ("distance", _P, _A): M.PLANE_AXIS,
    ("coincident", _T, _T): M.POINT_POINT, ("distance", _T, _T): M.DIST_PP,
    ("coincident", _A, _T): M.AXIS_POINT,
    ("coincident", _A, _A): M.CONCENTRIC, ("concentric", _A, _A): M.CONCENTRIC,
    ("distance", _A, _T): M.AXIS_POINT_DIST, ("distance", _A, _A): M.AXIS_AXIS_DIST,
    ("tangent", _P, _A): M.PLANE_AXIS,          # 平面と円筒: 軸が平面に平行で、距離 = 半径
}
#: 平面のどちら側かを sign で持つ Kind
_SIGNED = (M.PLANE_PLANE, M.PLANE_POINT, M.PLANE_AXIS)
for _t, _k in (("parallel", M.PARALLEL), ("perpendicular", M.PERPENDICULAR), ("angle", M.ANGLE)):
    for _a in (_P, _A):
        for _b in (_P, _A):
            _KINDS[(_t, _a, _b)] = _k
#: 向き（aligned）を守らせる Kind。面同士（突き合わせ / 同じ向き）だけが物理的に意味を持つ。
#: 軸には向きがないので、同心・平行では向きを問わない
_ORIENTED = (M.PLANE_PLANE,)


def _plan(type_: str, ga: Geom, gb: Geom) -> tuple[Kind, bool]:
    """(Kind, A/B を入れ替えるか) を返す。使えない組み合わせは ValueError。"""
    for swap, (a, b) in ((False, (ga, gb)), (True, (gb, ga))):
        k = _KINDS.get((type_, type(a), type(b)))
        if k is not None:
            return k, swap
    names = {GPlane: "平面", GAxis: "軸", GPoint: "点"}
    raise ValueError(f"合致 {type_} は {names[type(ga)]}-{names[type(gb)]} に使えません")


@dataclass
class Mate:
    type: str
    refs: list[list[str]]             # [[部品, 幾何名], [部品, 幾何名]]
    value: float | None = None        # 距離 / 角度（度）
    aligned: bool = True              # 法線・軸の向き: True=同じ向き, False=逆向き
    sign: float = 1.0                 # 距離をどちら側に取るか（作成時の配置から決定）
    suppressed: bool = False
    expr: str | None = None           # 式（あれば求解前に value を上書き）
    data: list[float] | None = None   # ロック合致の相対姿勢など


@dataclass
class AssemblySolveResult:
    status: str                       # ok / inconsistent
    dof: int
    state: str                        # under / full / over
    redundant: list[str]
    conflicting: list[str]
    flipped: list[str]                # 向きが指定と逆に収束した合致
    component_dof: dict[str, int]     # 部品ごとの残り自由度（0〜6）
    expr_errors: dict[str, str] = field(default_factory=dict)   # 合致ID / 変数名 → 式エラー


@dataclass
class _Build:
    sys: System
    idx: dict[str, int]
    n_internal: int
    cmap: list[str]


class Assembly(History):
    def __init__(self, sources: dict[str, GeometrySource] | None = None):
        super().__init__()
        self.sources: dict[str, GeometrySource] = sources or {}
        self.components: dict[str, Component] = {}
        self.mates: dict[str, Mate] = {}
        self._seq = 0
        self.last: AssemblySolveResult | None = None
        self.variables = Variables()
        self._expr_errors: dict[str, str] = {}

    # ------------------------------------------------------------ 編集
    def add_component(self, source: str, position=(0, 0, 0), rotation=(1, 0, 0, 0),
                      fixed: bool | None = None, id: str | None = None) -> str:
        if id is None:
            base = source.rsplit("/", 1)[-1].split(".")[0]
            n = 1
            while f"{base}<{n}>" in self.components:
                n += 1
            id = f"{base}<{n}>"
        if fixed is None:
            fixed = not self.components       # 一般的な 3D CAD と同じく最初の部品は固定
        self.components[id] = Component(id, source, tuple(map(float, position)),
                                        tuple(map(float, rotation)), fixed)
        return id

    def mate(self, type: str, a: tuple[str, str], b: tuple[str, str], value: float | None = None,
             aligned: bool | None = None) -> str:
        if type not in MATE_TYPES:
            raise ValueError(f"unknown mate {type}")
        if a[0] == b[0]:
            raise ValueError("同じ部品同士は合致できません")
        m = Mate(type, [list(a), list(b)], value)
        if type == "lock":
            ca, cb = self.components[a[0]], self.components[b[0]]
            inv = (ca.rotation[0], -ca.rotation[1], -ca.rotation[2], -ca.rotation[3])
            rel = quat_mul(inv, cb.rotation)
            m.refs = [[a[0], ""], [b[0], ""]]
            m.data = [float(v) for v in (*quat_rotate(inv, np.subtract(cb.position, ca.position)),
                                         *quat_rotate(rel, (1, 0, 0)), *quat_rotate(rel, (0, 1, 0)),
                                         *quat_rotate(rel, (0, 0, 1)))]
            return self._add_mate(m)
        kind, ga, gb = self._canonical(m)
        da, db = _dir(ga), _dir(gb)
        if aligned is None:
            if kind is M.PLANE_PLANE and type == "coincident":
                aligned = False               # 面同士を突き合わせる
            else:
                aligned = bool(da @ db >= 0) if kind in _ORIENTED else True
        m.aligned = aligned
        if type == "distance":
            if value is None:
                raise ValueError("distance には値が必要です")
        if type == "tangent" and gb.radius is None:
            raise ValueError("正接には円筒面が必要です")
        if type in ("distance", "tangent") and kind in _SIGNED:
            m.sign = 1.0 if (_org(gb) - _org(ga)) @ da >= 0 else -1.0
        if type == "angle" and value is None:
            m.value = math.degrees(math.acos(float(np.clip(da @ db, -1, 1))))
        return self._add_mate(m)

    def mate_options(self, a: tuple[str, str], b: tuple[str, str]) -> tuple[list[str], str]:
        """2 つの幾何に付けられる合致の種類と、既定の種類（一般的な 3D CAD の合致ポップアップ）。
        既定: 円筒どうし → 同心、平面と円筒 → 正接、ほか → 一致（付けられなければ最初のもの）。"""
        if a[0] == b[0]:
            raise ValueError("同じ部品同士は合致できません")
        ga, gb = self._local(a), self._local(b)
        types = []
        for t in MATE_TYPES:
            if t != "lock":
                try:
                    _plan(t, ga, gb)
                except ValueError:
                    continue
                if t == "tangent" and not any(isinstance(g, GAxis) and g.radius for g in (ga, gb)):
                    continue
            types.append(t)
        kinds = {type(ga), type(gb)}
        if kinds == {GAxis}:
            default = "concentric"
        elif kinds == {GPlane, GAxis} and "tangent" in types:
            default = "tangent"
        else:
            default = "coincident" if "coincident" in types else types[0]
        return types, default

    def measure(self, a: tuple[str, str], b: tuple[str, str]) -> dict:
        """今の配置での 2 つの幾何の距離（mm）と角度（度。向きのある幾何どうしだけ）。距離・角度合致の初期値。"""
        ga, gb = self.world_geometry(a), self.world_geometry(b)
        unit = lambda v: v / (np.linalg.norm(v) or 1.0)
        if isinstance(ga, GPlane) or isinstance(gb, GPlane):
            p, o = (ga, gb) if isinstance(ga, GPlane) else (gb, ga)
            dist = abs(float((_org(o) - _org(p)) @ unit(_dir(p))))
        elif isinstance(ga, GPoint) and isinstance(gb, GPoint):
            dist = float(np.linalg.norm(_org(gb) - _org(ga)))
        else:
            ax, o = (ga, gb) if isinstance(ga, GAxis) else (gb, ga)
            d, v = unit(_dir(ax)), _org(o) - _org(ax)
            dist = float(np.linalg.norm(v - (v @ d) * d))
        da, db = _dir(ga), _dir(gb)
        ang = None if da is None or db is None else math.degrees(math.acos(float(np.clip(unit(da) @ unit(db), -1, 1))))
        return {"distance": dist, "angle": ang}

    def _add_mate(self, m: Mate) -> str:
        self._seq += 1
        mid = f"Mate{self._seq}"
        self.mates[mid] = m
        return mid

    # ------------------------------------------------------------ 幾何の取得
    def _local(self, ref) -> Geom:
        comp, name = ref
        return self.sources[self.components[comp].source].geometry(name)

    def world_geometry(self, ref) -> Geom:
        c = self.components[ref[0]]
        return _to_world(c.position, c.rotation, self._local(ref))

    def _canonical(self, m: Mate):
        """(Kind, ワールド幾何A, B)。A/B は Kind の定義順に並べ替え済み。"""
        ra, rb = self._ordered(m)
        kind = _plan(m.type, self._local(ra), self._local(rb))[0]
        return kind, self.world_geometry(ra), self.world_geometry(rb)

    def _ordered(self, m: Mate):
        swap = _plan(m.type, self._local(m.refs[0]), self._local(m.refs[1]))[1]
        return (m.refs[1], m.refs[0]) if swap else (m.refs[0], m.refs[1])

    # ------------------------------------------------------------ System 構築
    def _build(self) -> _Build:
        sys = System()
        idx: dict[str, int] = {}
        for cid, c in self.components.items():
            idx[cid] = sys.add_param(c.position[0], c.fixed)
            for val in (*c.position[1:], *c.rotation):
                sys.add_param(val, c.fixed)
        for cid, c in self.components.items():
            if not c.fixed:
                sys.add(M.QUAT_NORM, range(idx[cid] + 3, idx[cid] + 7), [0.0] * M.N_CONSTS)
        n_internal = sys.n_constraints
        cmap = []
        for mid, m in self.mates.items():
            if m.suppressed:
                continue
            if m.type == "lock":
                ia, ib = idx[m.refs[0][0]], idx[m.refs[1][0]]
                sys.add(M.LOCK, [*range(ia, ia + 7), *range(ib, ib + 7)], [*m.data, *[0.0] * (M.N_CONSTS - 12)])
                cmap.append(mid)
                continue
            ra, rb = self._ordered(m)
            la, lb = self._local(ra), self._local(rb)
            kind = _plan(m.type, la, lb)[0]
            da = _dir(la)
            u, w = _perp_basis(da) if da is not None else (np.zeros(3), np.zeros(3))
            off = 0.0
            if m.type == "distance":
                off = m.sign * m.value if kind in _SIGNED else m.value
            elif m.type == "tangent":
                off = m.sign * lb.radius
            elif m.type == "angle":
                off = math.cos(math.radians(m.value))
            zero = np.zeros(3)
            consts = [*_org(la), *(_unit(da) if da is not None else zero), *u, *w,
                      *_org(lb), *(_unit(_dir(lb)) if _dir(lb) is not None else zero),
                      1.0 if m.aligned else -1.0, off]
            ia, ib = idx[ra[0]], idx[rb[0]]
            sys.add(kind, [*range(ia, ia + 7), *range(ib, ib + 7)], consts)
            # 部品が原点付近でも形が大きいと、変数だけで決めた許容誤差は厳しすぎて解けない扱いになる（D70）
            sys.length_scale = max(sys.length_scale, float(np.abs([*_org(la), *_org(lb)]).max()))
            cmap.append(mid)
        L = sys.length_scale
        sys.param_scale = np.array([L if (i % 7) < 3 else 1.0 for i in range(len(sys.x))])   # 部品ごとに [位置 3, 四元数 4]
        return _Build(sys, idx, n_internal, cmap)

    def _writeback(self, b: _Build):
        for cid, c in self.components.items():
            i = b.idx[cid]
            q = b.sys.x[i + 3:i + 7]
            c.position = tuple(float(v) for v in b.sys.x[i:i + 3])
            c.rotation = tuple(float(v) for v in q / np.linalg.norm(q))

    # ------------------------------------------------------------ 向き
    def _misaligned(self) -> list[str]:
        out = []
        for mid, m in self.mates.items():
            if m.suppressed or m.type == "lock":
                continue
            kind, ga, gb = self._canonical(m)
            if kind in _ORIENTED and (_dir(ga) @ _dir(gb)) * (1 if m.aligned else -1) < 0:
                out.append(mid)
        return out

    def _prealign(self):
        """向きが逆の合致について、動かせる側の部品を最小回転で正しい側へ回す（初期値づくり）。"""
        for mid in self._misaligned():
            m = self.mates[mid]
            ra, rb = self._ordered(m)
            _, ga, gb = self._canonical(m)
            s = 1.0 if m.aligned else -1.0
            # B を動かすなら B の方向を s·(A の方向) へ、A を動かすなら逆
            for ref, g, want in ((rb, gb, s * _dir(ga)), (ra, ga, s * _dir(gb))):
                c = self.components[ref[0]]
                if c.fixed:
                    continue
                cur = _dir(g)
                axis = np.cross(cur, want)
                n = np.linalg.norm(axis)
                if n < 1e-9:                                # 真逆: 垂直な軸まわりに 180°
                    axis, ang = _perp_basis(cur)[0], 180.0
                else:
                    ang = math.degrees(math.atan2(n, cur @ want))
                dq = quat_from_axis_angle(axis, ang)
                pivot = _org(g)
                c.position = tuple(float(v) for v in pivot + quat_rotate(dq, np.asarray(c.position) - pivot))
                c.rotation = tuple(float(v) for v in quat_mul(dq, c.rotation))
                break

    # ------------------------------------------------------------ 求解
    def solve(self) -> AssemblySolveResult:
        """合致を解いて部品の姿勢を更新する。解けない場合は姿勢を変えない。"""
        before = {cid: (c.position, c.rotation) for cid, c in self.components.items()}
        self._apply_exprs()
        self._prealign()
        b = self._build()
        res = b.sys.solve(max_iter=500)        # 大きく回す（扉を一度に 90° 開く）と 300 回ほどかかる（D70）
        if res.status == SolveStatus.OK:
            self._writeback(b)
        else:
            for cid, (p, q) in before.items():
                self.components[cid].position, self.components[cid].rotation = p, q
            b = self._build()
        return self._result(b, res.status)

    def drag(self, comp: str, position: tuple, diagnose: bool = True) -> AssemblySolveResult | None:
        """部品を指定位置へ引っ張る（合致で許される方向にだけ動く）。"""
        b = self._build()
        i = b.idx[comp]
        res = b.sys.drag({i + k: float(position[k]) for k in range(3)}, max_iter=100)
        self._writeback(b)
        return self._result(b, res.status) if diagnose else None

    def _apply_exprs(self):
        values, errors = self.variables.evaluate()
        self._expr_errors = dict(errors)

        def lookup(name):
            if name in values:
                return values[name]
            raise ExprError(f"{name} は未定義です" if name not in errors else f"変数 {name} がエラーです")
        for mid, m in self.mates.items():
            if m.expr is not None:
                try:
                    m.value = evaluate(m.expr, lookup)
                except ExprError as e:
                    self._expr_errors[mid] = str(e)        # 値は前回のまま

    def _result(self, b: _Build, status) -> AssemblySolveResult:
        d = b.sys.diagnose()
        names = lambda lst: [b.cmap[i - b.n_internal] for i in lst if i >= b.n_internal]
        cdof = {cid: (0 if c.fixed else d.dof_of(range(b.idx[cid], b.idx[cid] + 7)))
                for cid, c in self.components.items()}
        flipped = self._misaligned() if status == SolveStatus.OK else []
        self.last = AssemblySolveResult(status.value, d.dof, d.state, names(d.redundant),
                                        names(d.conflicting), flipped, cdof, dict(self._expr_errors))
        return self.last

    # ------------------------------------------------------------ 保存 / Undo
    def _changed(self):
        self.solve()

    def to_dict(self) -> dict:
        return {"format": "agentcad.assembly/1", "seq": self._seq, "variables": dict(self.variables.exprs),
                "components": {k: dict(c.__dict__) for k, c in self.components.items()},
                "mates": {k: dict(m.__dict__) for k, m in self.mates.items()}}

    def _load(self, d):
        d = json.loads(json.dumps(d))
        self._seq = d["seq"]
        self.variables = Variables(d.get("variables"))
        self.components = {k: Component(**{**c, "position": tuple(c["position"]),
                                           "rotation": tuple(c["rotation"])})
                           for k, c in d["components"].items()}
        self.mates = {k: Mate(**m) for k, m in d["mates"].items()}

    @classmethod
    def from_dict(cls, d, sources):
        a = cls(sources)
        a._load(d)
        return a
