"""形状カーネルの抽象インターフェース。

上位層（フィーチャー・ネーミング）はこのIFだけを使う。実装: mock(テスト用) / usd_brep / occt。
カーネルに要求する最重要機能は「履歴」: 演算結果の各面が入力の何から生まれたか。
これがトポロジカルネーミングの土台になる。
"""
from __future__ import annotations

import math

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

Vec3 = tuple[float, float, float]


@dataclass(frozen=True)
class Plane:
    origin: Vec3
    x_dir: Vec3
    normal: Vec3

    @property
    def y_dir(self) -> Vec3:
        (a, b, c), (d, e, f) = self.normal, self.x_dir           # 外積（numpy を使わない: 1 点ずつ呼ばれるので速さが効く。D64）
        return (float(b * f - c * e), float(c * d - a * f), float(a * e - b * d))

    def to_world(self, u: float, v: float) -> Vec3:
        o, x, y = self.origin, self.x_dir, self.y_dir
        return tuple(float(o[i] + u * x[i] + v * y[i]) for i in range(3))


# 標準の 3 平面（正面・平面・右側面）
FRONT = Plane((0, 0, 0), (1, 0, 0), (0, 0, 1))
TOP = Plane((0, 0, 0), (1, 0, 0), (0, 1, 0))
RIGHT = Plane((0, 0, 0), (0, 0, -1), (1, 0, 0))
STANDARD_PLANES = {"Front": FRONT, "Top": TOP, "Right": RIGHT}


@dataclass
class Segment:
    """プロファイルの1要素（平面座標）。tag はスケッチのエンティティID（履歴/命名に使う）。"""
    kind: Literal["line", "arc", "circle"]
    tag: str
    points: list[tuple[float, float]]      # line: [a,b] / arc: [center,start,end] / circle: [center]
    radius: float | None = None


@dataclass
class Profile:
    plane: Plane
    loops: list[list[Segment]]             # 先頭が外形、以降は穴（実装側で内外判定してもよい）


@dataclass
class Mesh:
    positions: np.ndarray                  # (N,3) float32
    indices: np.ndarray                    # (M,3) uint32
    face_of_triangle: np.ndarray           # (M,)  面ハンドルの番号
    face_handles: list[Any]                # 番号 → 面ハンドル


# 履歴: 出力面ハンドル → 由来
#   ("side", segment_tag)      押し出し/回転の側面
#   ("cap", "start"|"end")    押し出しの端面
#   ("modified", in_handle)    入力面が変形して残ったもの
#   ("fillet", edge_handle)    フィレット面（入力エッジ由来）
Origin = tuple[str, Any]


@dataclass
class OpResult:
    shape: Any
    history: dict[Any, Origin] = field(default_factory=dict)


class KernelError(Exception):
    pass


class Kernel(ABC):
    #: 実形状を計算するか（False のカーネルは形状に関する適合テストを省く）
    geometric: bool = True

    @abstractmethod
    def extrude(self, profile: Profile, distance: float) -> OpResult: ...

    @abstractmethod
    def revolve(self, profile: Profile, axis: tuple[tuple[float, float], tuple[float, float]],
                angle_deg: float) -> OpResult: ...

    def sweep(self, profile: Profile, path: Profile) -> OpResult:
        """スイープ（D58）: 断面 profile を、平面の経路 path に沿って掃引する。path.loops[0] は始点から順に並べ、向きをそろえた
        線・円弧（kind "arc" は反時計回り、"arc_cw" は時計回りにたどる。points は [中心, 始点, 終点]）。
        断面は経路の始点に置く（カーネルは断面の外接箱の中心を経路の始点に合わせる）。
        面の履歴: 端の蓋 ("cap", "start"|"end")、側面 ("side", 断面の要素のタグ)。"""
        raise KernelError("このカーネルはスイープに対応していません")

    @abstractmethod
    def boolean(self, op: Literal["union", "cut", "intersect"], a: Any, b: Any) -> OpResult:
        """a,b 両方の面について ("modified", handle) を返すこと。"""

    @abstractmethod
    def fillet(self, shape: Any, edges: list[Any], radius: float) -> OpResult: ...

    def chamfer(self, shape: Any, edges: list[Any], distance: float) -> OpResult:
        """エッジの面取り（等距離）。新しい面の履歴は ("chamfer", 元のエッジ)。"""
        raise KernelError("このカーネルは面取りに対応していません")

    def shell(self, shape: Any, faces: list[Any], thickness: float) -> OpResult:
        """シェル: faces を取り除き、残りの面を厚さ |thickness| の壁にする（負 = 内側へ、正 = 外側へ）。
        新しい壁の面の履歴は ("shell", 元の面)。"""
        raise KernelError("このカーネルはシェルに対応していません")

    @abstractmethod
    def faces(self, shape: Any) -> list[Any]: ...

    @abstractmethod
    def edges(self, shape: Any) -> list[tuple[Any, Any, Any]]:
        """(edge_handle, face_a, face_b) の一覧。エッジ命名は隣接2面の名前で行う。"""

    @abstractmethod
    def face_plane(self, shape: Any, face: Any) -> Plane | None:
        """平面なら Plane を返す（面上スケッチ用）。"""

    def face_axis(self, shape: Any, face: Any) -> tuple[Vec3, Vec3] | None:
        """円筒/円錐/回転面なら (軸上の点, 軸方向) を返す（同心合致用）。"""
        return None

    def face_radius(self, shape: Any, face: Any) -> float | None:
        """円筒面なら半径（正接合致用）。"""
        return None

    def edge_polyline(self, shape: Any, edge: Any):
        """表示用: エッジの折れ線（線分の配列 (k,2,3)）。形状を計算しないカーネルは None。"""
        return None

    def sort_key(self, shape: Any, handle: Any) -> tuple | None:
        """面/エッジの形状から決まる順序キー（重心など）。同名の面に #n を振る順序に使う。"""
        return None

    @abstractmethod
    def tessellate(self, shape: Any, tol: float = 0.1) -> Mesh: ...

    def volume(self, shape: Any) -> float:
        raise NotImplementedError

    def export(self, shape: Any, path: str, fmt: str) -> None:
        raise NotImplementedError


def arc_angles(s: Segment) -> tuple[float, float, float | None]:
    """円弧（points = [中心, 始点, 終点]）を作る角度: (開始角 度, 反時計回りの角度 度, 向きを逆にする軸の角度 rad | None)。
    "arc" は始点 → 終点を反時計回り。"arc_cw" は始点 → 終点を時計回り（スイープの経路を逆向きにたどるとき。D58）。
    usd-brep には曲線を逆向きにする関数が無いので、"arc_cw" は 終点 → 始点 の反時計回りの円弧を作り、中心を通る弦の垂直二等分線の
    まわりに 180° 回す（点の集まりは同じで、始点と終点が入れ替わる。平面の上に残る）。"""
    c, p, q = (np.asarray(v, float) for v in s.points[:3])
    if s.kind == "arc_cw":
        p, q = q, p
    a0 = math.degrees(math.atan2(p[1] - c[1], p[0] - c[0]))
    sweep = (math.degrees(math.atan2(q[1] - c[1], q[0] - c[0])) - a0) % 360 or 360
    return a0, sweep, (math.radians(a0 + sweep / 2) if s.kind == "arc_cw" else None)
