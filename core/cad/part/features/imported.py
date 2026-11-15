"""インポート（D71）: STL・3MF・OBJ から取り込んだ三角形メッシュのボディ。

メッシュは定義の中に持つ（zlib + base64。元のファイルが無くなっても開ける）。面は kernel/meshbody.py が三角形の向きから作る。
ボディは表示・計測・面上スケッチ・合致・図面に使えるが、形を変えるフィーチャー（押し出しの追加・カット・フィレットなど）の
相手にはできない（カーネルがエラーにする）。
"""
from __future__ import annotations

from dataclasses import dataclass

from ...kernel.base import OpResult
from ...naming.toponame import name_result
from .base import BuildContext, Feature, FeatureError, FeatureResult, register


@register
@dataclass
class ImportFeature(Feature):
    type = "import"
    label = "インポート"
    default_name = "Import"
    makes_body = True

    source: str = ""          # 取り込んだファイルの名前（表示用）
    mesh: str = ""            # meshbody.encode() の文字列

    def build(self, ctx: BuildContext) -> FeatureResult:
        from ...kernel.meshbody import decode, mesh_shape
        from ...kernel.meshtopo import MeshTopoKernel
        if not isinstance(ctx.kernel, MeshTopoKernel):
            raise FeatureError("このカーネルではメッシュを取り込めません")
        if ctx.body is not None:
            raise FeatureError("インポートはパートの最初のボディにしてください（ほかのボディとは組み合わせられません）")
        try:
            shape = mesh_shape(*decode(self.mesh))
        except ValueError as e:
            raise FeatureError(f"メッシュを読めません: {e}")
        res = OpResult(shape, {shape.fh(i): ("mesh", f"F{i + 1}") for i in range(len(shape.faces))})
        return FeatureResult(body=shape, names=name_result(self.id, ctx.kernel, res, []))
