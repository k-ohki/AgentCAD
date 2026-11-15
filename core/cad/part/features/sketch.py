"""スケッチのフィーチャーと、スケッチの外（ほかのスケッチ・モデル）への参照（D52・D54）。"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import ClassVar

import numpy as np

from ...doc.expr import ExprError, evaluate
from ...geom import chain, circumcenter
from ...kernel.base import Plane, Profile, Segment
from ...sketch import Arc, Circle, Line, Point, Sketch
from ...sketch.fit import fit, project
from .base import BuildContext, Feature, FeatureError, FeatureResult, register, resolve_plane


def ext_kind(source: str, entity=None) -> str:
    """外部参照の source から拘束の種類: 点に一致（ext_point）か、線の上（ext_line）か。"""
    base, _, at = source.partition("@")
    if at:
        return "ext_point"
    if base.startswith("edge:"):
        return "ext_line"
    return "ext_line" if isinstance(entity, Line) else "ext_point"


def external_geometry(ctx: BuildContext, source: str, plane: Plane, kind: str, prev=None) -> list[float]:
    """外部参照を plane に投影した座標。ext_point → [u, v]、ext_line → [u1, v1, u2, v2]（線の両端）。
    source（D52・D54）:
      "スケッチID:点ID" / "スケッチID:線ID"（線の上）/ "スケッチID:線ID@mid"（線の中点）
      "edge:エッジ名"（モデルの直線エッジの上）/ "edge:エッジ名@vertex|mid|center"（端点 / 中点 / 円・円弧の中心）
    @vertex は両端のうち prev（前の位置・クリック位置 [u, v]）に近い方。"""
    O, X, Yd = (np.asarray(v, float) for v in (plane.origin, plane.x_dir, plane.y_dir))

    def uv(w):
        w = np.asarray(w, float) - O
        return [float(w @ X), float(w @ Yd)]

    base, _, at = source.partition("@")
    if base.startswith("edge:"):
        name = base[5:]
        if ctx.body is None:
            raise FeatureError("参照するモデルがありません")
        try:
            P = chain(ctx.kernel.edge_polyline(ctx.body, ctx.edge(name)))
        except ValueError as e:
            raise FeatureError(str(e))
        closed = len(P) > 2 and np.linalg.norm(P[0] - P[-1]) < 1e-7
        if kind == "ext_line" or at == "":
            d = P[-1] - P[0]
            L = float(np.linalg.norm(d))
            if closed or L < 1e-9 or max(float(np.linalg.norm(np.cross(p - P[0], d))) / L for p in P) > 1e-6 * max(1.0, L):
                raise FeatureError(f"エッジ {name} は直線ではありません")
            g = uv(P[0]) + uv(P[-1])
            if math.hypot(g[2] - g[0], g[3] - g[1]) < 1e-9:
                raise FeatureError(f"エッジ {name} はこの平面に垂直です（投影すると点になります）")
            return g
        if at == "vertex":
            if closed:
                raise FeatureError(f"エッジ {name} は閉じていて端点がありません")
            ends = [uv(P[0]), uv(P[-1])]
            if prev is None:
                return ends[0]
            return min(ends, key=lambda e: math.hypot(e[0] - prev[0], e[1] - prev[1]))
        if at == "mid":
            seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
            acc, half = np.concatenate([[0.0], np.cumsum(seg)]), float(seg.sum()) / 2
            i = int(np.searchsorted(acc, half)) - 1
            i = min(max(i, 0), len(seg) - 1)
            t = (half - acc[i]) / (seg[i] or 1.0)
            return uv(P[i] + t * (P[i + 1] - P[i]))
        if at == "center":
            n = len(P) - (1 if closed else 0)
            C = circumcenter(P[0], P[n // 3], P[(2 * n) // 3]) if n >= 3 else None
            if C is None:
                raise FeatureError(f"エッジ {name} は円・円弧ではありません")
            r = np.linalg.norm(P - C, axis=1)
            if float(r.max() - r.min()) > 1e-3 * max(float(r.mean()), 1e-9):
                raise FeatureError(f"エッジ {name} は円・円弧ではありません")
            return uv(C)
        raise FeatureError(f"{source} がわかりません")
    sid, _, eid = base.partition(":")
    f, r = ctx.features.get(sid), ctx.results.get(sid)
    if not isinstance(f, SketchFeature) or r is None or r.error or not r.data or "profile" not in r.data:
        raise FeatureError(f"スケッチ {sid} が無効です（参照するスケッチはツリーで前に置いてください）")
    e = f.sketch.entities.get(eid)
    src_plane = r.data["profile"].plane
    proj = lambda pid: uv(src_plane.to_world(*f.sketch.xy(pid)))
    if kind == "ext_point" and isinstance(e, Point) and not at:
        return proj(eid)
    if kind == "ext_point" and isinstance(e, Line) and at == "mid":
        a, b = proj(e.p1), proj(e.p2)
        return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
    if kind == "ext_line" and isinstance(e, Line) and not at:
        g = proj(e.p1) + proj(e.p2)
        if math.hypot(g[2] - g[0], g[3] - g[1]) < 1e-9:
            raise FeatureError(f"線 {source} はこの平面に垂直です（投影すると点になります）")
        return g
    raise FeatureError(f"{source} がありません")


@register
@dataclass
class SketchFeature(Feature):
    type: ClassVar[str] = "sketch"
    label: ClassVar[str] = "スケッチ"
    default_name: ClassVar[str] = "Sketch"
    plane: dict = field(default_factory=lambda: {"plane": "Front"})
    sketch: Sketch = field(default_factory=Sketch)
    #: 3D 表示（一般的な 3D CAD の「スケッチを表示/非表示」）。None = 自動（どのフィーチャーにも使われていなければ表示）
    visible: bool | None = None

    @classmethod
    def load_params(cls, params):
        params["sketch"] = Sketch.from_dict(params["sketch"])
        return params

    @property
    def uses_body(self):  # 面上スケッチか、モデルのエッジを投影しているときだけボディに依存
        return "face" in self.plane or any(
            c.type == "on_edge" or (c.type in ("ext_point", "ext_line") and (c.source or "").startswith("edge:"))
            for c in self.sketch.constraints.values())

    def deps(self):
        out = {self.plane["feature"]} if "feature" in self.plane else set()
        return out | {c.source.split(":")[0] for c in self.sketch.constraints.values()
                      if c.type in ("ext_point", "ext_line") and c.source and not c.source.startswith("edge:")}

    def dims(self):
        return {cid: c.value for cid, c in self.sketch.constraints.items() if c.value is not None}

    def apply_exprs(self, lookup):
        cons = self.sketch.constraints
        own = {cid: c.value for cid, c in cons.items() if c.expr is None and c.value is not None}
        pre = f"{self.id}."

        def lk(name):
            if name.startswith(pre):
                if name[len(pre):] in own:
                    return own[name[len(pre):]]
                raise ExprError(f"{name} は未定義か、まだ決まっていません（循環・後方参照）")
            return lookup(name)
        for cid, c in cons.items():
            if c.expr is not None:
                c.value = own[cid] = evaluate(c.expr, lk)

    def build(self, ctx):
        plane = resolve_plane(self.plane, ctx)
        self.update_projections(ctx, plane)
        r = self.sketch.solve()
        if r.status != "ok":
            raise FeatureError(f"スケッチが解けません（矛盾: {', '.join(r.conflicting)}）")
        loops = []
        for loop in self.sketch.profiles():
            loops.append([self._segment(eid) for eid in loop])
        xy = lambda pid: (self.sketch.entities[pid].x, self.sketch.entities[pid].y)
        warn = None if r.state == "full" else ("スケッチが未定義です" if r.state == "under" else "過剰定義")
        axes = {eid: (xy(e.p1), xy(e.p2)) for eid, e in self.sketch.entities.items()
                if isinstance(e, Line) and e.construction}
        return FeatureResult(data={"profile": Profile(plane, loops), "axes": axes, "solve": r}, warning=warn)

    def update_projections(self, ctx, plane):
        """投影した要素（on_edge）を、今のモデルのエッジに合わせ直す（一般的な 3D CAD の「エッジ上」）。
        ほかのスケッチへの参照（ext_point / ext_line）も、参照先の今の位置に合わせ直す。
        エッジ・参照先が見つからない・形が変わったときは前の位置のまま警告する。"""
        for c in self.sketch.constraints.values():
            if c.type in ("ext_point", "ext_line") and c.source:
                try:
                    c.data = external_geometry(ctx, c.source, plane, c.type, prev=(c.data or None))
                except FeatureError as e:
                    ctx.notes.append(f"参照先 {c.source} を使えません（前の位置のまま）: {e}")
        for c in self.sketch.constraints.values():
            if c.type != "on_edge" or not c.source:
                continue
            eid = c.refs[0]
            try:
                h = ctx.edge(c.source)
                kind, g = fit(project(ctx.kernel.edge_polyline(ctx.body, h), plane))
            except Exception as e:                      # noqa: BLE001 参照切れ・形の変化はどれも警告にする
                ctx.notes.append(f"投影元のエッジ {c.source} を使えません（前の位置のまま）: {e}")
                continue
            want = type(self.sketch.entities[eid]).__name__.lower()
            if kind != want:
                ctx.notes.append(f"投影元のエッジ {c.source} の形が変わりました（{want} → {kind}）")
                continue
            if kind == "line":                           # 端点の向きは今の要素に合わせる
                old = self.sketch.entity_geom(eid)
                if np.hypot(g[0] - old[0], g[1] - old[1]) > np.hypot(g[2] - old[0], g[3] - old[1]):
                    g = [g[2], g[3], g[0], g[1]]
            c.data = [float(v) for v in g]
            self.sketch.set_geom(eid, c.data)

    def _segment(self, eid):
        s = self.sketch; e = s.entities[eid]
        xy = lambda pid: (s.entities[pid].x, s.entities[pid].y)
        if isinstance(e, Line):
            return Segment("line", eid, [xy(e.p1), xy(e.p2)])
        if isinstance(e, Arc):
            return Segment("arc", eid, [xy(e.center), xy(e.start), xy(e.end)])
        if isinstance(e, Circle):
            return Segment("circle", eid, [xy(e.center)], e.radius)
        raise FeatureError(eid)
