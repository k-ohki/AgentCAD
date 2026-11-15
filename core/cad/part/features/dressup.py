"""仕上げのフィーチャー: フィレット、面取り、シェル（D48）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from ...kernel.base import KernelError
from ...naming.toponame import name_result
from .base import Feature, FeatureError, FeatureResult, register


@register
@dataclass
class FilletFeature(Feature):
    type: ClassVar[str] = "fillet"
    label: ClassVar[str] = "フィレット"
    default_name: ClassVar[str] = "Fillet"
    uses_body: ClassVar[bool] = True
    makes_body: ClassVar[bool] = True
    edges: list[str] = field(default_factory=list)   # 永続エッジ名
    radius: float = 1.0

    def build(self, ctx):
        if ctx.body is None:
            raise FeatureError("ボディがありません")
        handles = [ctx.edge(e) for e in self.edges]
        try:
            res = ctx.kernel.fillet(ctx.body, handles, self.radius)
        except KernelError as e:
            raise FeatureError(str(e))
        return FeatureResult(body=res.shape, names=name_result(self.id, ctx.kernel, res, [ctx.names]))


@register
@dataclass
class ChamferFeature(Feature):
    """面取り（距離 - 距離、等距離）。distance は各面の後退量（90° のエッジのとき。一般的な 3D CAD の「距離」）。"""
    type: ClassVar[str] = "chamfer"
    label: ClassVar[str] = "面取り"
    default_name: ClassVar[str] = "Chamfer"
    uses_body: ClassVar[bool] = True
    makes_body: ClassVar[bool] = True
    edges: list[str] = field(default_factory=list)   # 永続エッジ名
    distance: float = 1.0

    def build(self, ctx):
        if ctx.body is None:
            raise FeatureError("ボディがありません")
        if not self.edges:
            raise FeatureError("面取りするエッジを選んでください")
        if self.distance <= 0:
            raise FeatureError("距離は正の値にしてください")
        handles = [ctx.edge(e) for e in self.edges]
        try:
            res = ctx.kernel.chamfer(ctx.body, handles, self.distance)
        except KernelError as e:
            raise FeatureError(str(e))
        return FeatureResult(body=res.shape, names=name_result(self.id, ctx.kernel, res, [ctx.names]))


@register
@dataclass
class ShellFeature(Feature):
    """シェル: faces（永続面名）を取り除き、残りを厚さ thickness の壁にする。faces が空なら中空の閉じた立体。
    既定は内側へ（一般的な 3D CAD と同じ）。outward で外側へ。"""
    type: ClassVar[str] = "shell"
    label: ClassVar[str] = "シェル"
    default_name: ClassVar[str] = "Shell"
    options: ClassVar[dict] = {"faces": "faces", "outward": bool}
    uses_body: ClassVar[bool] = True
    makes_body: ClassVar[bool] = True
    faces: list[str] = field(default_factory=list)
    thickness: float = 2.0
    outward: bool = False

    def build(self, ctx):
        if ctx.body is None:
            raise FeatureError("ボディがありません")
        if self.thickness <= 0:
            raise FeatureError("厚さは正の値にしてください")
        handles = [ctx.face(f) for f in self.faces]
        try:
            res = ctx.kernel.shell(ctx.body, handles, self.thickness if self.outward else -self.thickness)
        except KernelError as e:
            raise FeatureError(str(e))
        return FeatureResult(body=res.shape, names=name_result(self.id, ctx.kernel, res, [ctx.names]))
