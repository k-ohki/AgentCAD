"""パターン・ミラー（D43）: 元のフィーチャーのツール形状を置き直して重ねる。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

import numpy as np

from .base import (BuildContext, Feature, FeatureError, FeatureResult, Placement, resolve_axis, combine, resolve_direction, register,
                   resolve_plane)


@dataclass
class InstanceFeature(Feature):
    """元のフィーチャー（押し出し・回転）のツール形状を置き直して、同じ操作（追加 / カット）で重ねる。
    カーネルに必要なのは押し出し・回転とブーリアンだけ（D43）。面の名前は `<このID>~<番号>/…`。"""
    uses_body: ClassVar[bool] = True
    makes_body: ClassVar[bool] = True
    seeds: list[str] = field(default_factory=list)

    def deps(self):
        return set(self.seeds)

    def placements(self, ctx) -> list[Placement]:
        raise NotImplementedError

    def build(self, ctx):
        if not self.seeds:
            raise FeatureError("元にするフィーチャーを選んでください")
        seeds = []
        for sid in self.seeds:
            f = ctx.features.get(sid)
            if f is None or not hasattr(f, "tool"):
                raise FeatureError(f"{sid} はパターンにできません（押し出し・カット・回転を選んでください）")
            seeds.append(f)
        places = self.placements(ctx)
        if len(places) * len(seeds) > 500:
            raise FeatureError("インスタンスが多すぎます（500 まで）")
        local = BuildContext(ctx.kernel, ctx.body, ctx.names, ctx.results, ctx.features, ctx.notes)
        n = 0
        res = FeatureResult(body=ctx.body, names=ctx.names)
        for pl in places:
            for f in seeds:
                for t in (f.tools(local, pl) if hasattr(f, "tools") else [f.tool(local, pl)]):   # 穴は位置ごとにツール
                    n += 1
                    res = combine(local, f"{self.id}~{n}", f.op, t)
                    local.body, local.names = res.body, res.names
        return res


@register
@dataclass
class LinearPatternFeature(InstanceFeature):
    """直線パターン。count は元を含む個数。direction2 を指定すると 2 方向（格子）。"""
    type: ClassVar[str] = "lpattern"
    label: ClassVar[str] = "直線パターン"
    default_name: ClassVar[str] = "LPattern"
    options: ClassVar[dict] = {"seeds": "seeds", "direction": "dir", "reverse": bool, "direction2": "dir?", "reverse2": bool}
    direction: dict = field(default_factory=lambda: {"axis": "X"})
    spacing: float = 20.0
    count: int = 2
    reverse: bool = False
    direction2: dict | None = None
    spacing2: float = 20.0
    count2: int = 1
    reverse2: bool = False

    def hidden_params(self):
        return {"spacing2", "count2"} if self.direction2 is None else set()

    def placements(self, ctx):
        n1, n2 = int(round(self.count)), int(round(self.count2))
        if n1 < 1 or n2 < 1:
            raise FeatureError("個数は 1 以上にしてください")
        d1 = resolve_direction(self.direction, ctx) * (-self.spacing if self.reverse else self.spacing)
        d2 = np.zeros(3)
        if self.direction2 is not None and n2 > 1:
            d2 = resolve_direction(self.direction2, ctx) * (-self.spacing2 if self.reverse2 else self.spacing2)
        else:
            n2 = 1
        out = [Placement.translate(i * d1 + j * d2) for j in range(n2) for i in range(n1) if i or j]
        if not out:
            raise FeatureError("個数を 2 以上にしてください")
        return out


@register
@dataclass
class CircularPatternFeature(InstanceFeature):
    """円形パターン。count は元を含む個数。angle が 360 なら等間隔に 1 周、それ以外は angle の範囲に等間隔。"""
    type: ClassVar[str] = "cpattern"
    label: ClassVar[str] = "円形パターン"
    default_name: ClassVar[str] = "CPattern"
    options: ClassVar[dict] = {"seeds": "seeds", "axis": "axis", "reverse": bool}
    axis: dict = field(default_factory=lambda: {"axis": "Y"})
    count: int = 4
    angle: float = 360.0
    reverse: bool = False

    def placements(self, ctx):
        n = int(round(self.count))
        if n < 2:
            raise FeatureError("個数を 2 以上にしてください")
        p, d = resolve_axis(self.axis, ctx)
        step = self.angle / n if abs(abs(self.angle) - 360) < 1e-9 else self.angle / (n - 1)
        if self.reverse:
            step = -step
        return [Placement.rotate(p, d, step * i) for i in range(1, n)]


@register
@dataclass
class MirrorFeature(InstanceFeature):
    """ミラー: 元のフィーチャーを平面（標準平面・平らな面・参照平面）について鏡映して重ねる。"""
    type: ClassVar[str] = "mirror"
    label: ClassVar[str] = "ミラー"
    default_name: ClassVar[str] = "Mirror"
    options: ClassVar[dict] = {"seeds": "seeds", "plane": "plane"}
    plane: dict = field(default_factory=lambda: {"plane": "Right"})

    def deps(self):
        return set(self.seeds) | ({self.plane["feature"]} if "feature" in self.plane else set())

    def placements(self, ctx):
        pl = resolve_plane(self.plane, ctx)
        return [Placement.reflect(pl.origin, pl.normal)]
