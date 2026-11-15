"""フィーチャー定義（種類ごとのモジュール）。

  base       Feature・BuildContext・参照の解決・ボディの合成・置き直し
  sketch     スケッチと外部参照
  solid      押し出し / カット・回転・スイープ
  hole       穴ウィザード
  dressup    フィレット・面取り・シェル
  reference  参照平面
  pattern    直線 / 円形パターン・ミラー
  imported   インポート（取り込んだメッシュ。D71）

フィーチャーを足すとき: Feature を継承して @register し、label・default_name・options（数値以外の設定）を書く。
作るときの既定値と検査は api/feature_api.py の MAKERS に足す。UI のボタンは app/web/toolbar.js。
"""
from .base import (ENDS, OPS, REGISTRY, BuildContext, Feature, FeatureError, FeatureResult, Placement, body_extent, combine,
                   place_profile, register, resolve_axis, resolve_direction, resolve_plane, shift_profile, sketch_profile)
from .dressup import ChamferFeature, FilletFeature, ShellFeature
from .hole import HoleFeature
from .imported import ImportFeature
from .pattern import CircularPatternFeature, InstanceFeature, LinearPatternFeature, MirrorFeature
from .reference import PlaneFeature
from .sketch import SketchFeature, ext_kind, external_geometry
from .solid import END_CONDITIONS, ExtrudeFeature, RevolveFeature, SweepFeature, sweep_path

__all__ = [
    "ENDS", "OPS", "REGISTRY", "BuildContext", "Feature", "FeatureError", "FeatureResult", "Placement", "body_extent",
    "combine", "place_profile", "register", "resolve_axis", "resolve_direction", "resolve_plane", "shift_profile",
    "sketch_profile", "ChamferFeature", "FilletFeature", "ShellFeature", "HoleFeature", "ImportFeature", "CircularPatternFeature",
    "InstanceFeature", "LinearPatternFeature", "MirrorFeature", "PlaneFeature", "SketchFeature", "ext_kind",
    "external_geometry", "END_CONDITIONS", "ExtrudeFeature", "RevolveFeature", "SweepFeature", "sweep_path",
]
