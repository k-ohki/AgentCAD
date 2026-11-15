"""穴ウィザード（D57）。規格の寸法は part/holes.py。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

import numpy as np

from ...kernel.base import KernelError, Plane, Profile, Segment
from .base import BuildContext, Feature, FeatureError, Placement, combine, body_extent, register
from ..holes import KINDS, SIZES, hole_profile, standard_dims
from .sketch import SketchFeature


@register
@dataclass
class HoleFeature(Feature):
    """穴ウィザード。位置のスケッチ（sketch）の点ごとに、規格の穴（ザグリ・皿・キリ・ねじ下穴・ドリル）を開ける。
    穴の形は軸まわりの断面を回転したもので、カーネルに要るのは回転とブーリアンだけ。
    向きはスケッチ平面の法線の逆（面の上のスケッチなら材料の中へ）。reverse で反転。
    寸法は種類・サイズから規格の値を入れ（holes.standard_dims）、後から直せる（直すとサイズは custom）。
    面の名前は 1 つ目の穴が `<ID>/side/<辺>`、2 つ目以降が `<ID>~<番号>/side/<辺>`（辺: wall・tip・floor・cbore_wall・cbore_floor・csink）。"""
    type: ClassVar[str] = "hole"
    label: ClassVar[str] = "穴ウィザード"
    default_name: ClassVar[str] = "Hole"
    options: ClassVar[dict] = {"kind": KINDS, "size": SIZES, "end": (("blind", "ブラインド"), ("through", "全貫通")), "reverse": bool}
    option_labels: ClassVar[dict] = {"end": "終了条件"}
    uses_body: ClassVar[bool] = True
    makes_body: ClassVar[bool] = True
    op: ClassVar[str] = "cut"
    sketch: str = ""
    kind: str = "cbore"
    size: str = "M6"
    end: str = "blind"            # blind / through
    depth: float = 15.0
    diameter: float = 6.6
    cbore_diameter: float = 11.0
    cbore_depth: float = 6.5
    csink_diameter: float = 12.6
    csink_angle: float = 90.0
    tip_angle: float = 118.0
    reverse: bool = False

    def deps(self):
        return {self.sketch}

    def apply_standard(self):
        """種類・サイズから規格の寸法を入れる。"""
        for k, v in standard_dims(self.kind, self.size).items():
            setattr(self, k, v)
            self.exprs.pop(k, None)

    def hidden_params(self):
        hide = {"depth", "tip_angle"} if self.end == "through" else set()
        if self.kind != "cbore":
            hide |= {"cbore_diameter", "cbore_depth"}
        if self.kind != "csink":
            hide |= {"csink_diameter", "csink_angle"}
        return hide

    def option_changed(self, key):
        if key in ("kind", "size"):                   # 種類・サイズを変えたら規格の寸法に
            self.apply_standard()

    def param_edited(self, key):
        if key in ("diameter", "cbore_diameter", "cbore_depth", "csink_diameter"):
            self.size = "custom"                      # 規格の寸法を直したらカスタム（一般的な 3D CAD の「カスタムサイズ」）

    def positions(self, ctx) -> tuple[Plane, list[tuple[float, float]]]:
        """位置のスケッチの平面と、穴の中心（スケッチ座標）。中心 = ほかの要素に使われていない点（固定点 = 原点の印は除く）。"""
        r = ctx.results.get(self.sketch)
        f = ctx.features.get(self.sketch)
        if not isinstance(f, SketchFeature) or r is None or r.error or not r.data or "profile" not in r.data:
            raise FeatureError("穴の位置のスケッチが無効です")
        sk = f.sketch
        used = set()
        for e in sk.entities.values():
            used.update(getattr(e, k) for k in ("p1", "p2", "center", "start", "end") if hasattr(e, k))
        pts = [sk.xy(k) for k, e in sk.entities.items()
               if type(e).__name__ == "Point" and k not in used and not getattr(e, "fixed", False)]
        if not pts:
            raise FeatureError("穴の位置がありません（位置のスケッチに点を置いてください）")
        return r.data["profile"].plane, pts

    def tools(self, ctx, placement: Placement | None = None) -> list:
        if self.end not in ("blind", "through"):
            raise FeatureError(f"未知の深さの条件: {self.end}")
        if self.diameter <= 0 or (self.end == "blind" and self.depth <= 0):
            raise FeatureError("直径・深さは正の値にしてください")
        plane, pts = self.positions(ctx)
        n = np.asarray(plane.normal, float)
        D = n if self.reverse else -n                                  # 穴の奥へ向かう向き
        X = np.asarray(plane.x_dir, float)
        r = self.diameter / 2
        outer = max(r, self.cbore_diameter / 2 if self.kind == "cbore" else 0, self.csink_diameter / 2 if self.kind == "csink" else 0)
        if self.kind == "cbore" and (self.cbore_diameter <= self.diameter or self.cbore_depth <= 0):
            raise FeatureError("ザグリの径は穴より大きく、深さは正にしてください")
        if self.kind == "csink" and (self.csink_diameter <= self.diameter or not 0 < self.csink_angle < 180):
            raise FeatureError("皿の径は穴より大きく、角度は 0〜180° にしてください")
        above = max(0.05, 0.1 * outer)
        out = []
        for (u, v) in pts:
            c = np.asarray(plane.to_world(u, v), float)
            if placement is not None:
                c, Dp, Xp = np.asarray(placement.point(c)), np.asarray(placement.vec(D)), np.asarray(placement.vec(X))
            else:
                Dp, Xp = D, X
            depth = self.depth
            if self.end == "through":
                lo, hi = body_extent(ctx, Plane(tuple(c.tolist()), tuple(Xp.tolist()), tuple(Dp.tolist())))
                if hi <= 1e-9:
                    raise FeatureError("その向きには貫通するボディがありません（向きを反転してください）")
                depth = hi + max(1e-3, 0.01 * (hi - lo))
            try:
                poly = hole_profile(self.kind, r, depth, self.end == "through", self.tip_angle,
                                    self.cbore_diameter / 2, self.cbore_depth, self.csink_diameter / 2, self.csink_angle, above)
            except ValueError as e:
                raise FeatureError(str(e))
            segs = [Segment("line", tag, [p, poly[(i + 1) % len(poly)][1]]) for i, (tag, p) in enumerate(poly)]
            # 断面の平面: u = 軸から離れる向き（Xp）、v = 奥へ（Dp）。法線 = Xp × Dp
            prof = Profile(Plane(tuple(c.tolist()), tuple(Xp.tolist()), tuple(np.cross(Xp, Dp).tolist())), [segs])
            try:
                out.append(ctx.kernel.revolve(prof, ((0.0, 0.0), (0.0, 1.0)), 360.0))
            except KernelError as e:
                raise FeatureError(str(e))
        return out

    def tool(self, ctx, placement: Placement | None = None):
        return self.tools(ctx, placement)[0]

    def build(self, ctx):
        if ctx.body is None:
            raise FeatureError("穴を開けるボディがありません")
        local = BuildContext(ctx.kernel, ctx.body, ctx.names, ctx.results, ctx.features, ctx.notes)
        res = None
        for i, t in enumerate(self.tools(ctx)):
            res = combine(local, self.id if i == 0 else f"{self.id}~{i + 1}", "cut", t)
            local.body, local.names = res.body, res.names
        return res
