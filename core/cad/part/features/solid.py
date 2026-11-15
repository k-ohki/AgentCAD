"""形を作るフィーチャー: 押し出し / カット、回転、スイープ（D58）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

import numpy as np

from ...kernel.base import KernelError, Profile, Segment
from ...sketch import Arc, Circle, Line
from ...kernel.meshtopo import path_polyline, profile_center
from .base import (ENDS, OPS, Feature, FeatureError, Placement, combine, body_extent, place_profile, register, shift_profile,
                   sketch_profile)
from .sketch import SketchFeature


END_CONDITIONS = ("blind", "mid", "through", "through_both")


@register
@dataclass
class ExtrudeFeature(Feature):
    """押し出し / カット。end（一般的な 3D CAD の「押し出し状態」）:
    blind = ブラインド（距離）、mid = 中間平面（スケッチ平面の両側に距離の半分ずつ）、
    through = 全貫通（向きの側のボディを抜けるまで）、through_both = 全貫通（両側）。
    向き: 追加は平面の法線の向き、カットはその逆（材料の中へ）。reverse で反転。"""
    type: ClassVar[str] = "extrude"
    label: ClassVar[str] = "押し出し"
    default_name: ClassVar[str] = "Extrude"
    options: ClassVar[dict] = {"end": ENDS, "op": OPS, "reverse": bool}
    uses_body: ClassVar[bool] = True
    makes_body: ClassVar[bool] = True
    sketch: str = ""
    distance: float = 10.0
    op: str = "add"               # new / add / cut
    reverse: bool = False
    end: str = "blind"

    def deps(self):
        return {self.sketch}

    def hidden_params(self):
        return {"distance"} if self.end in ("through", "through_both") else set()   # 全貫通では距離を使わない

    def tool(self, ctx, placement: Placement | None = None):
        prof = place_profile(sketch_profile(ctx, self.sketch), placement)
        sign = -1.0 if (self.reverse != (self.op == "cut")) else 1.0
        if self.end not in END_CONDITIONS:
            raise FeatureError(f"未知の押し出し状態: {self.end}")
        if self.end in ("blind", "mid") and self.distance <= 0:
            raise FeatureError("距離は正の値にしてください")
        if self.end == "blind":
            start, length = 0.0, sign * self.distance
        elif self.end == "mid":
            start, length = -sign * self.distance / 2, sign * self.distance
        else:
            lo, hi = body_extent(ctx, prof.plane)
            margin = max(1e-3, 0.01 * (hi - lo))          # 面と面が重ならないよう少し突き抜ける
            eps = 1e-6 * max(hi - lo, 1.0)
            if self.end == "through_both":
                start, length = lo - margin, (hi - lo) + 2 * margin
            elif sign > 0:
                start, length = 0.0, hi + margin
            else:
                start, length = 0.0, lo - margin
            if self.end == "through" and (hi <= eps if sign > 0 else lo >= -eps):
                raise FeatureError("その向きには貫通するボディがありません（向きを反転してください）")
        try:
            return ctx.kernel.extrude(shift_profile(prof, start), length)
        except KernelError as e:
            raise FeatureError(str(e))

    def build(self, ctx):
        return combine(ctx, self.id, self.op, self.tool(ctx))


@register
@dataclass
class RevolveFeature(Feature):
    type: ClassVar[str] = "revolve"
    label: ClassVar[str] = "回転"
    default_name: ClassVar[str] = "Revolve"
    options: ClassVar[dict] = {"op": OPS}
    uses_body: ClassVar[bool] = True
    makes_body: ClassVar[bool] = True
    sketch: str = ""
    axis_line: str = ""           # スケッチ内の作図線ID
    angle: float = 360.0
    op: str = "add"

    def deps(self):
        return {self.sketch}

    def tool(self, ctx, placement: Placement | None = None):
        prof = place_profile(sketch_profile(ctx, self.sketch), placement)
        axis = ctx.results[self.sketch].data["axes"].get(self.axis_line)
        if axis is None:
            raise FeatureError(f"回転軸 {self.axis_line} が見つかりません（作図線を指定）")
        if placement is not None and placement.mirror:   # v を反転し、回る向きも逆にする（軸の向きを入れ替える）
            (ax, ay), (bx, by) = axis
            axis = ((bx, -by), (ax, -ay))
        try:
            return ctx.kernel.revolve(prof, axis, self.angle)
        except KernelError as e:
            raise FeatureError(str(e))

    def build(self, ctx):
        return combine(ctx, self.id, self.op, self.tool(ctx))


def sweep_path(ctx, sketch_id: str, placement: "Placement | None", hint) -> Profile:
    """経路のスケッチ → 始点から順に並べ、向きをそろえた経路（Kernel.sweep の path）。
    経路 = 作図線でない線・円弧・円が 1 本につながったもの（開いていても閉じていてもよい）。
    始点は、開いた経路なら断面（hint、ワールド座標）に近い端、閉じた経路なら断面に近い頂点。"""
    r, f = ctx.results.get(sketch_id), ctx.features.get(sketch_id)
    if not isinstance(f, SketchFeature) or r is None or r.error or not r.data or "profile" not in r.data:
        raise FeatureError(f"経路のスケッチ {sketch_id} が無効です")
    segs = [f._segment(k) for k, e in f.sketch.entities.items()
            if isinstance(e, (Line, Arc, Circle)) and not e.construction]
    if not segs:
        raise FeatureError("経路のスケッチに線・円弧がありません")
    prof = place_profile(Profile(r.data["profile"].plane, [segs]), placement)
    plane, segs = prof.plane, prof.loops[0]
    hint2 = np.asarray([(np.asarray(hint) - np.asarray(plane.origin)) @ np.asarray(v, float) for v in (plane.x_dir, plane.y_dir)])
    if len(segs) == 1 and segs[0].kind == "circle":
        return Profile(plane, [segs])
    if any(s.kind == "circle" for s in segs):
        raise FeatureError("円は単独でだけ経路にできます（ほかの線とつなぐなら円弧にしてください）")
    ends = lambda s: (s.points[0], s.points[1]) if s.kind == "line" else (s.points[1], s.points[2])
    size = max(1.0, max(abs(c) for s in segs for p in s.points for c in p))
    key = lambda p: (round(p[0] / (1e-6 * size)), round(p[1] / (1e-6 * size)))
    deg: dict = {}
    for s in segs:
        for p in ends(s):
            deg[key(p)] = deg.get(key(p), 0) + 1
    if any(v > 2 for v in deg.values()):
        raise FeatureError("経路が枝分かれしています（1 本につながった線にしてください）")
    loose = [p for s in segs for p in ends(s) if deg[key(p)] == 1]
    cand = loose if loose else [p for s in segs for p in ends(s)]
    if loose and len(loose) != 2:
        raise FeatureError("経路が途切れています（1 本につながった線にしてください）")
    cur = min(cand, key=lambda p: float(np.hypot(p[0] - hint2[0], p[1] - hint2[1])))
    left, out = list(segs), []
    while left:
        nxt = next((s for s in left if key(cur) in (key(ends(s)[0]), key(ends(s)[1]))), None)
        if nxt is None:
            break
        left.remove(nxt)
        a, b = ends(nxt)
        fwd = key(a) == key(cur)
        if nxt.kind == "line":
            out.append(nxt if fwd else Segment("line", nxt.tag, [b, a]))
        else:
            out.append(nxt if fwd else Segment("arc_cw", nxt.tag, [nxt.points[0], b, a]))
        cur = b if fwd else a
    if left:
        raise FeatureError("経路が離れた線を含んでいます（1 本につながった線にしてください）")
    return Profile(plane, [out])


@register
@dataclass
class SweepFeature(Feature):
    """スイープ（一般的な 3D CAD のスイープ ボス / スイープ カット）。断面のスケッチ（閉じた輪郭）を、経路のスケッチ（平面の上で
    1 本につながった線・円弧）に沿って掃引する。断面は経路の始点に置いて描く（中心が始点からずれていると、
    カーネルが中心を始点へ動かして掃引し、警告を出す）。面の名前: `<ID>/start`・`<ID>/end`（端の蓋）、`<ID>/side/<断面の要素>`。"""
    type: ClassVar[str] = "sweep"
    label: ClassVar[str] = "スイープ"
    default_name: ClassVar[str] = "Sweep"
    options: ClassVar[dict] = {"profile": "sketch", "path": "sketch", "op": OPS}
    uses_body: ClassVar[bool] = True
    makes_body: ClassVar[bool] = True
    profile: str = ""
    path: str = ""
    op: str = "add"

    def deps(self):
        return {self.profile, self.path}

    def tool(self, ctx, placement: Placement | None = None):
        if self.profile == self.path:
            raise FeatureError("断面と経路は別のスケッチにしてください")
        prof = place_profile(sketch_profile(ctx, self.profile), placement)
        c = profile_center(prof)
        path = sweep_path(ctx, self.path, placement, c)
        P, T = path_polyline(path)
        size = max(1.0, float(np.ptp(P, axis=0).max()))
        if np.linalg.norm(c - P[0]) > 1e-4 * size:
            ctx.notes.append("断面の中心が経路の始点からずれています（中心を始点に移して掃引しました）")
        if abs(float(np.asarray(prof.plane.normal, float) @ T[0])) < 0.999:
            ctx.notes.append("断面が経路の始点で経路に垂直ではありません")
        try:
            return ctx.kernel.sweep(prof, path)
        except KernelError as e:
            raise FeatureError(str(e))

    def build(self, ctx):
        if self.op not in ("add", "cut", "new"):
            raise FeatureError(f"未知の操作: {self.op}")
        return combine(ctx, self.id, self.op, self.tool(ctx))
