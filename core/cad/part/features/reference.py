"""参照ジオメトリ: 参照平面。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

import numpy as np

from ...kernel.base import Plane
from .base import Feature, FeatureResult, register, resolve_plane


@register
@dataclass
class PlaneFeature(Feature):
    """参照平面: 基準の平面（標準平面・平らな面・ほかの参照平面）から法線方向に offset だけ離れた平面。"""
    type: ClassVar[str] = "plane"
    label: ClassVar[str] = "参照平面"
    default_name: ClassVar[str] = "Plane"
    options: ClassVar[dict] = {"base": "plane", "reverse": bool}
    base: dict = field(default_factory=lambda: {"plane": "Front"})
    offset: float = 10.0
    reverse: bool = False

    @property
    def uses_body(self):
        return "face" in self.base

    def deps(self):
        return {self.base["feature"]} if "feature" in self.base else set()

    def build(self, ctx):
        base = resolve_plane(self.base, ctx)
        d = -self.offset if self.reverse else self.offset
        o = np.asarray(base.origin, float) + d * np.asarray(base.normal, float)
        return FeatureResult(data={"plane": Plane(tuple(o.tolist()), base.x_dir, base.normal)})
