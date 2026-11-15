"""フィーチャーの土台: Feature（定義）、BuildContext / FeatureResult、参照の解決、ボディの合成、置き直し。

Feature は「定義（シリアライズ可能なパラメータ）」だけを持ち、build(ctx) で結果を作る。
依存関係は deps() で宣言する。再生成エンジン（document.py）はこれを使って DAG を組む。

参照の書式:
  平面   {"plane": "Front"} | {"face": "<永続面名>"} | {"feature": "<参照平面フィーチャーの ID>"}
  向き   {"axis": "X"|"Y"|"Z"} | {"edge": "<直線エッジの永続名>"} | {"face": "<円筒面の永続名>"}（軸）
  面/エッジ  永続名の文字列（naming/toponame.py）
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar

import numpy as np

from ...doc.expr import ExprError, evaluate
from ...kernel.base import STANDARD_PLANES, Plane, Profile, Segment
from ...naming.toponame import name_result


class FeatureError(Exception):
    pass


@dataclass
class BuildContext:
    kernel: Any
    #: 直前のボディ（このフィーチャーより前で最後にボディを出したフィーチャーの結果）
    body: Any = None
    names: Any = None             # TopoNames（直前ボディの永続名）
    results: dict[str, Any] = field(default_factory=dict)   # feature id → FeatureResult
    features: dict[str, Any] = field(default_factory=dict)  # feature id → Feature（パターンが元のフィーチャーを引く）
    notes: list[str] = field(default_factory=list)          # build 中の警告（参照の修復など）

    def face(self, name: str):
        """面の永続名 → ハンドル。完全一致でなければ警告を残す。"""
        if self.names is None:
            raise FeatureError(f"面 {name} が見つかりません（ボディがありません）")
        r = self.names.find_face(name)
        if r.repaired:
            self.notes.append(f"参照 {name} を {r.name} に付け替えました")
        return r.handle

    def edge(self, name: str):
        if self.names is None:
            raise FeatureError(f"エッジ {name} が見つかりません（ボディがありません）")
        r = self.names.find_edge(name)
        if r.repaired:
            self.notes.append(f"参照 {name} を {r.name} に付け替えました")
        return r.handle


@dataclass
class FeatureResult:
    body: Any = None              # ボディを出すフィーチャーのみ
    names: Any = None
    data: Any = None              # スケッチ: Profile など
    error: str | None = None
    warning: str | None = None


#: 選択肢（値, 画面の名前）。押し出し・回転・スイープの「操作」と、押し出しの「押し出し状態」
OPS = (("add", "追加（和）"), ("cut", "カット（差）"), ("new", "新しいボディ"))
ENDS = (("blind", "ブラインド"), ("mid", "中間平面"), ("through", "全貫通"), ("through_both", "全貫通（両側）"))

REGISTRY: dict[str, type["Feature"]] = {}


def register(cls):
    REGISTRY[cls.type] = cls
    return cls


@dataclass
class Feature:
    type: ClassVar[str] = ""
    #: 画面に出す種類の名前と、ID・名前の頭（"Extrude" → Extrude1, Extrude2, …）
    label: ClassVar[str] = ""
    default_name: ClassVar[str] = ""
    #: 数値以外の設定（feature.set で変えられるもの）: 名前 → 種類。種類は 選択肢のタプル ((値, 名前), …) / bool /
    #: "plane"（平面の参照）/ "dir"（向き）/ "dir?"（省略可）/ "axis"（回転軸）/ "seeds"（元のフィーチャー）/
    #: "faces"（面の名前の一覧）/ "sketch"（ツリーで前にあるスケッチ）
    options: ClassVar[dict] = {}
    #: 設定の画面の名前を、この種類だけ変えるとき（既定は api/feature_api.py の OPTION_LABELS）
    option_labels: ClassVar[dict] = {}
    #: ボディを入力として使うか（True なら直前ボディに依存）
    uses_body: ClassVar[bool] = False
    #: ボディを出力するか
    makes_body: ClassVar[bool] = False

    id: str = ""
    name: str = ""
    suppressed: bool = False
    #: パラメータ名 → 式。再生成時に評価してパラメータを上書きする（cad.doc.expr）
    exprs: dict = field(default_factory=dict)

    def apply_exprs(self, lookup) -> None:
        for k, e in self.exprs.items():
            if k not in self.dims():
                raise ExprError(f"パラメータ {k} がありません")
            setattr(self, k, evaluate(e, lookup))

    def dims(self) -> dict[str, float]:
        """式から `<id>.<名前>` で参照できる数値パラメータ。"""
        return {k: v for k, v in self.__dict__.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)}

    def deps(self) -> set[str]:
        """明示的に参照する他フィーチャーのID。"""
        return set()

    def hidden_params(self) -> set[str]:
        """今の設定では使わない数値パラメータ（画面に出さない）。"""
        return set()

    def option_changed(self, key: str) -> None:
        """設定 key を変えた後に呼ばれる（連動して変えるものがあれば上書きする）。"""

    def param_edited(self, key: str) -> None:
        """数値パラメータ key を画面から直した後に呼ばれる。"""

    def build(self, ctx: BuildContext) -> FeatureResult:
        raise NotImplementedError

    def params(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("id", "name", "suppressed")}
        return d

    def to_dict(self) -> dict:
        return {"type": self.type, "id": self.id, "name": self.name,
                "suppressed": self.suppressed, "params": _ser(self.params())}

    @staticmethod
    def from_dict(d: dict) -> "Feature":
        cls = REGISTRY[d["type"]]
        return cls(id=d["id"], name=d["name"], suppressed=d["suppressed"], **cls.load_params(dict(d["params"])))

    @classmethod
    def load_params(cls, params: dict) -> dict:
        """保存された params を、コンストラクタに渡せる形に戻す（to_dict の逆）。"""
        return params


def _ser(p):
    return {k: (v.to_dict() if hasattr(v, "to_dict") else v) for k, v in p.items()}


def resolve_plane(ref: dict, ctx: BuildContext) -> Plane:
    if "plane" in ref:
        if ref["plane"] not in STANDARD_PLANES:
            raise FeatureError(f"平面 {ref['plane']} はありません")
        return STANDARD_PLANES[ref["plane"]]
    if "feature" in ref:
        r = ctx.results.get(ref["feature"])
        if r is None or r.error or not r.data or "plane" not in r.data:
            raise FeatureError(f"参照平面 {ref['feature']} が無効です")
        return r.data["plane"]
    face = ref["face"]
    pl = ctx.kernel.face_plane(ctx.body, ctx.face(face))
    if pl is None:
        raise FeatureError(f"面 {face} は平面ではありません")
    return pl


def sketch_profile(ctx, sketch_id) -> Profile:
    r = ctx.results.get(sketch_id)
    if r is None or r.error or r.data is None:
        raise FeatureError(f"スケッチ {sketch_id} が無効です")
    prof = r.data["profile"]
    if not prof.loops:
        raise FeatureError("閉じたプロファイルがありません")
    return prof


def combine(ctx, fid, op, res_tool):
    """新しいツールボディを直前ボディと合成し、永続名を付ける。"""
    k = ctx.kernel
    tool_names = name_result(fid, k, res_tool, [])
    if ctx.body is None or op == "new":
        if op == "cut":
            raise FeatureError("切り取る対象のボディがありません")
        return FeatureResult(body=res_tool.shape, names=tool_names)
    kop = {"add": "union", "cut": "cut"}[op]
    res = k.boolean(kop, ctx.body, res_tool.shape)
    names = name_result(fid, k, res, [ctx.names, tool_names])
    # 結果にこのフィーチャーの面が 1 つも無い = 形が変わっていない（ボディの中に埋まった追加・材料に届かないカット）。
    # カーネルはエラーにしないので、気づけるように警告にする（D68）
    warn = None
    # パターンの複製（ID に "~"）は、既存の形とちょうど重なることがよくあるので対象にしない
    if "~" not in fid and names.faces and not any(n.startswith(fid + "/") for n in names.faces):
        warn = ("形が変わりませんでした（追加した形がボディの中に埋まっている、またはカットが材料に届いていない）。"
                "位置と向きを確かめてください（カットはスケッチ平面の法線と逆向きに掘ります）")
    return FeatureResult(body=res.shape, names=names, warning=warn)


# ---------------------------------------------------------------- 置き直し（パターン・ミラー）


@dataclass(frozen=True)
class Placement:
    """ツール形状の置き直し: 世界座標の等長変換 p → R p + t。R の行列式が −1 なら鏡映。"""
    R: Any
    t: Any

    @property
    def mirror(self) -> bool:
        return float(np.linalg.det(self.R)) < 0

    def point(self, p):
        return tuple((self.R @ np.asarray(p, float) + self.t).tolist())

    def vec(self, v):
        return tuple((self.R @ np.asarray(v, float)).tolist())

    @staticmethod
    def translate(v) -> "Placement":
        return Placement(np.eye(3), np.asarray(v, float))

    @staticmethod
    def rotate(point, axis, angle_deg: float) -> "Placement":
        k = np.asarray(axis, float); k = k / np.linalg.norm(k)
        a = math.radians(angle_deg)
        K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
        R = np.eye(3) + math.sin(a) * K + (1 - math.cos(a)) * (K @ K)
        p = np.asarray(point, float)
        return Placement(R, p - R @ p)

    @staticmethod
    def reflect(point, normal) -> "Placement":
        n = np.asarray(normal, float); n = n / np.linalg.norm(n)
        R = np.eye(3) - 2 * np.outer(n, n)
        p = np.asarray(point, float)
        return Placement(R, p - R @ p)


def _flip_v(seg: Segment) -> Segment:
    pts = [(x, -y) for x, y in seg.points]
    if seg.kind == "arc":                         # 反時計回りを保つため始点と終点を入れ替える
        pts = [pts[0], pts[2], pts[1]]
    return replace(seg, points=pts)


def place_profile(prof: Profile, pl: Placement | None) -> Profile:
    """プロファイルを置き直す。鏡映のときは平面が左手系にならないよう、平面内の v を反転して法線を鏡映する
    （y' = n' × x' = −(鏡映した y) なので、v → −v でちょうど元の形の鏡映になる）。"""
    if pl is None:
        return prof
    plane = Plane(pl.point(prof.plane.origin), pl.vec(prof.plane.x_dir), pl.vec(prof.plane.normal))
    loops = [[_flip_v(s) for s in loop] for loop in prof.loops] if pl.mirror else prof.loops
    return Profile(plane, loops)


def shift_profile(prof: Profile, offset: float) -> Profile:
    if not offset:
        return prof
    o = np.asarray(prof.plane.origin, float) + offset * np.asarray(prof.plane.normal, float)
    return Profile(Plane(tuple(o.tolist()), prof.plane.x_dir, prof.plane.normal), prof.loops)


def body_extent(ctx: BuildContext, plane: Plane) -> tuple[float, float]:
    """直前のボディが、平面の法線方向に占める範囲（平面の原点からの距離の最小・最大）。"""
    if ctx.body is None:
        raise FeatureError("全貫通の相手になるボディがありません")
    pos = np.asarray(ctx.kernel.tessellate(ctx.body).positions, float).reshape(-1, 3)
    if not len(pos):
        raise FeatureError("全貫通の相手になるボディがありません")
    t = (pos - np.asarray(plane.origin, float)) @ np.asarray(plane.normal, float)
    return float(t.min()), float(t.max())


# ---------------------------------------------------------------- パターン・ミラー

WORLD_AXES = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}


def edge_line(ctx: BuildContext, name: str):
    """直線エッジ → (始点, 単位方向)。"""
    segs = np.asarray(ctx.kernel.edge_polyline(ctx.body, ctx.edge(name)), float).reshape(-1, 3)
    if len(segs) < 2:
        raise FeatureError(f"エッジ {name} の形が取れません")
    far = segs[np.argmax(np.linalg.norm(segs - segs[0], axis=1))]
    d = far - segs[0]
    L = float(np.linalg.norm(d))
    if L < 1e-9:
        raise FeatureError(f"エッジ {name} は直線ではありません")
    d = d / L
    off = (segs - segs[0]) - np.outer((segs - segs[0]) @ d, d)
    if float(np.linalg.norm(off, axis=1).max()) > 1e-4 * max(L, 1.0):
        raise FeatureError(f"エッジ {name} は直線ではありません")
    return segs[0], d


def resolve_direction(ref: dict, ctx: BuildContext):
    if "axis" in ref:
        if ref["axis"] not in WORLD_AXES:
            raise FeatureError(f"軸 {ref['axis']} はありません（X / Y / Z）")
        return np.array(WORLD_AXES[ref["axis"]])
    if "edge" in ref:
        return edge_line(ctx, ref["edge"])[1]
    raise FeatureError("向きには X / Y / Z か直線のエッジを指定してください")


def resolve_axis(ref: dict, ctx: BuildContext):
    """回転軸 → (軸上の点, 方向)。"""
    if "axis" in ref:
        return np.zeros(3), resolve_direction(ref, ctx)
    if "edge" in ref:
        return edge_line(ctx, ref["edge"])
    if "face" in ref:
        ax = ctx.kernel.face_axis(ctx.body, ctx.face(ref["face"]))
        if ax is None:
            raise FeatureError(f"面 {ref['face']} は円筒面ではありません")
        return np.asarray(ax[0], float), np.asarray(ax[1], float)
    raise FeatureError("軸には X / Y / Z、直線のエッジ、円筒面のどれかを指定してください")
