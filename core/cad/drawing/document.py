"""図面の文書（D60）: シート（用紙・縮尺）、ビュー（モデルの投影図）、寸法。

- 座標は用紙の mm（原点 = 左下、y は上向き）。ビューの位置 (x, y) は「ワールドの原点の投影」を置く場所なので、
  投影図（親から派生したビュー）は親と x か y をそろえるだけで位置が合う
- ビューの向きは (右, 上) のベクトル。投影図は親の向きを 90° 回したもの（第三角法: 右に置けば右から見た図）
- 寸法の相手はモデルのエッジの永続名（"edge:名前@vertex|mid|center"、円は "edge:名前"）。モデルが変わると値も位置も追従する
- 形（投影）は覚えない。モデルの今の表示メッシュから毎回作る（向きごとにキャッシュ）
"""
from __future__ import annotations

import copy
from typing import Callable

import numpy as np

from ..doc.history import History
from ..geom import fit_circle, is_closed
from .hlr import ORIENT_LABELS, ORIENTS, ViewGeometry, edge_polylines, project_view

#: 用紙（横置きの幅, 高さ mm）
SHEETS = {"A4": (297.0, 210.0), "A3": (420.0, 297.0), "A2": (594.0, 420.0), "A1": (841.0, 594.0)}
#: 選べる縮尺（分子, 分母）。大きい順
SCALES = [(10, 1), (5, 1), (2, 1), (1, 1), (1, 2), (1, 5), (1, 10), (1, 20), (1, 50), (1, 100)]
MARGIN = 10.0               # 枠の余白
TITLE_W, TITLE_H = 170.0, 32.0


class DrawingError(Exception):
    pass


def _unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def project_frame(R, U, side: str):
    """親の向き (R, U) から、side（right / left / up / down）に置く投影図の向き（第三角法）。"""
    R, U = np.asarray(R, float), np.asarray(U, float)
    N = np.cross(R, U)
    if side == "right":
        return (-N).tolist(), U.tolist()
    if side == "left":
        return N.tolist(), U.tolist()
    if side == "up":
        return R.tolist(), (-N).tolist()
    if side == "down":
        return R.tolist(), N.tolist()
    raise DrawingError(f"未知の向き: {side}")


def frame_label(R, U) -> str:
    for k, (r, u) in ORIENTS.items():
        if np.allclose(R, r, atol=1e-6) and np.allclose(U, u, atol=1e-6):
            return ORIENT_LABELS[k]
    return "投影図"


class DrawingDocument(History):
    def __init__(self, mesh_of: Callable[[str], tuple[object, dict | None]], model: str):
        """mesh_of(model) → (形が変わると変わる印, 表示メッシュ（ボディが無ければ None）)。"""
        super().__init__()
        self.mesh_of = mesh_of
        self.model = model
        self.sheet = {"size": "A3", "landscape": True, "scale": [1, 1]}
        self.title = ""                          # 空ならモデルの名前
        self.views: dict[str, dict] = {}
        self.dims: dict[str, dict] = {}
        self._seq = 0
        self._cache: dict[tuple, tuple[object, ViewGeometry]] = {}
        self._poly: tuple[object, dict[int, np.ndarray]] | None = None    # (形の印, エッジの番号 → 折れ線)

    # ------------------------------------------------------------ 保存・Undo
    def to_dict(self) -> dict:
        return {"format": "agentcad.drawing/1", "model": self.model, "sheet": copy.deepcopy(self.sheet), "title": self.title,
                "views": copy.deepcopy(self.views), "dims": copy.deepcopy(self.dims), "seq": self._seq}

    def _load(self, d: dict) -> None:
        self.model, self.sheet, self.title = d["model"], copy.deepcopy(d["sheet"]), d.get("title", "")
        self.views, self.dims, self._seq = copy.deepcopy(d["views"]), copy.deepcopy(d["dims"]), d.get("seq", 0)

    @classmethod
    def from_dict(cls, d: dict, mesh_of) -> "DrawingDocument":
        doc = cls(mesh_of, d["model"])
        doc._load(d)
        return doc

    # ------------------------------------------------------------ シート
    @property
    def size(self) -> tuple[float, float]:
        w, h = SHEETS[self.sheet["size"]]
        return (w, h) if self.sheet["landscape"] else (h, w)

    def scale_of(self, view: dict | None = None) -> float:
        n, d = (view or {}).get("scale") or self.sheet["scale"]
        return n / d

    def set_sheet(self, size=None, landscape=None, scale=None):
        if size is not None:
            if size not in SHEETS:
                raise DrawingError(f"未知の用紙: {size}")
            self.sheet["size"] = size
        if landscape is not None:
            self.sheet["landscape"] = bool(landscape)
        if scale is not None:
            self.sheet["scale"] = _scale(scale)

    # ------------------------------------------------------------ 形
    def mesh(self) -> dict:
        _, m = self.mesh_of(self.model)
        if m is None:
            raise DrawingError("モデルにボディがありません")
        return m

    def edge_polyline(self, name: str):
        """エッジ（永続名）の折れ線（ワールド座標）。無ければ None。全エッジの折れ線は形が変わるまで使い回す
        （寸法の位置は描くたびに解決するので、毎回つなぎ直すとアセンブリの図面で 1 回数秒かかっていた）。"""
        token, m = self.mesh_of(self.model)
        if m is None:
            raise DrawingError("モデルにボディがありません")
        if name not in m["edge_names"]:
            return None
        if self._poly is None or self._poly[0] is not token:
            self._poly = (token, edge_polylines(m))
        return self._poly[1].get(m["edge_names"].index(name))

    def geometry(self, view: dict) -> ViewGeometry:
        token, m = self.mesh_of(self.model)
        if m is None:
            raise DrawingError("モデルにボディがありません")
        key = tuple(np.round(np.concatenate([view["R"], view["U"]]), 9).tolist())
        hit = self._cache.get(key)
        if hit is None or hit[0] is not token:
            if any(t is not token for t, _ in self._cache.values()):
                self._cache.clear()              # モデルが変わった
            hit = self._cache[key] = (token, project_view(m, view["R"], view["U"]))
        return hit[1]

    # ------------------------------------------------------------ ビュー
    def _id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}{self._seq}"

    def add_view(self, orient: str | None = None, x: float | None = None, y: float | None = None,
                 parent: str | None = None, side: str | None = None) -> str:
        """モデルビュー（orient）か、親 parent の side に置く投影図。(x, y) = 図の中心を置く位置。
        省くと用紙の中央（投影図は親の隣）。"""
        if parent is not None:
            p = self.views[parent]
            R, U = project_frame(p["R"], p["U"], side or "right")
            v = {"R": R, "U": U, "parent": parent, "side": side or "right", "scale": p.get("scale"), "hidden": p.get("hidden", False)}
        else:
            if orient not in ORIENTS:
                raise DrawingError(f"未知の向き: {orient}")
            R, U = ORIENTS[orient]
            v = {"R": list(map(float, R)), "U": list(map(float, U)), "parent": None, "side": None, "scale": None, "hidden": False}
        w, h = self.size
        v["x"], v["y"] = (w / 2 if x is None else float(x)), (h / 2 if y is None else float(y))
        vid = self._id("View")
        self.views[vid] = v
        if x is not None and y is not None:      # 指定した位置に、図の中心が来るように
            x0, y0, x1, y1 = self.geometry(v).bbox
            s = self.scale_of(v)
            v["x"], v["y"] = v["x"] - (x0 + x1) / 2 * s, v["y"] - (y0 + y1) / 2 * s
        if parent is not None:
            if x is None or y is None:           # 親の隣に、外接箱が 15 mm 離れるように置く
                self._place_beside(vid)
            self._align(vid)
        return vid

    def _place_beside(self, vid: str, gap: float = 15.0) -> None:
        v, p = self.views[vid], self.views[self.views[vid]["parent"]]
        gv, gp, s, sp = self.geometry(v), self.geometry(p), self.scale_of(v), self.scale_of(p)
        (ax0, ay0, ax1, ay1), (bx0, by0, bx1, by1) = gp.bbox, gv.bbox
        v["x"], v["y"] = p["x"], p["y"]
        if v["side"] == "right":
            v["x"] = p["x"] + ax1 * sp + gap - bx0 * s
        elif v["side"] == "left":
            v["x"] = p["x"] + ax0 * sp - gap - bx1 * s
        elif v["side"] == "up":
            v["y"] = p["y"] + ay1 * sp + gap - by0 * s
        else:
            v["y"] = p["y"] + ay0 * sp - gap - by1 * s

    def _align(self, vid: str) -> None:
        """投影図は親と位置をそろえる（左右の図は y、上下の図は x）。"""
        v = self.views[vid]
        p = self.views.get(v.get("parent") or "")
        if p is None:
            return
        if v["side"] in ("right", "left"):
            v["y"] = p["y"]
        else:
            v["x"] = p["x"]

    def move_view(self, vid: str, x: float, y: float) -> None:
        v = self.views[vid]
        dx, dy = float(x) - v["x"], float(y) - v["y"]
        v["x"], v["y"] = float(x), float(y)
        self._align(vid)
        for cid, c in self.views.items():        # 子（投影図）はそろえる方向だけついてくる
            if c.get("parent") == vid:
                if c["side"] in ("right", "left"):
                    c["y"] += dy
                else:
                    c["x"] += dx
                self._align(cid)

    def set_view(self, vid: str, scale=..., hidden=None) -> None:
        v = self.views[vid]
        if scale is not ...:
            v["scale"] = None if scale is None else _scale(scale)
        if hidden is not None:
            v["hidden"] = bool(hidden)

    def delete(self, ids: list[str]) -> None:
        for i in ids:
            if i in self.views:
                del self.views[i]
                for c in self.views.values():    # 子は独立したビューになる
                    if c.get("parent") == i:
                        c["parent"], c["side"] = None, None
                self.dims = {k: d for k, d in self.dims.items() if d["view"] != i}
            elif i in self.dims:
                del self.dims[i]
            else:
                raise DrawingError(f"{i} がありません")

    def standard_views(self) -> list[str]:
        """標準 3 面図（第三角法）: 正面図、その上に平面図、右に右側面図。縮尺が合えば用紙の中ほどに並べる。"""
        f = self.add_view("front")
        t = self.add_view(parent=f, side="up")
        r = self.add_view(parent=f, side="right")
        self.layout([f, t, r])
        return [f, t, r]

    def layout(self, ids: list[str]) -> None:
        """ビューのまとまりを、枠と表題欄を避けた範囲の中央へ動かす。"""
        boxes = [self.view_box(i) for i in ids]
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        w, h = self.size
        cx, cy = w / 2, (h + MARGIN + TITLE_H) / 2          # 表題欄の上
        dx, dy = cx - (x0 + x1) / 2, cy - (y0 + y1) / 2
        for i in ids:
            self.views[i]["x"] += dx
            self.views[i]["y"] += dy

    def view_box(self, vid: str) -> tuple[float, float, float, float]:
        """ビューの外接箱（用紙の mm）。"""
        v = self.views[vid]
        s = self.scale_of(v)
        x0, y0, x1, y1 = self.geometry(v).bbox
        return v["x"] + x0 * s, v["y"] + y0 * s, v["x"] + x1 * s, v["y"] + y1 * s

    def auto_scale(self) -> list[int]:
        """標準 3 面図が用紙に入る、いちばん大きい標準の縮尺。"""
        m = self.mesh()
        P = np.asarray(m["positions"], float).reshape(-1, 3)
        dx, dy, dz = np.ptp(P, axis=0) if len(P) else (1, 1, 1)
        need_w, need_h = dx + dz, dy + dz                   # 正面 + 右側面 / 正面 + 平面
        w, h = self.size
        # 寸法を書く余白を残す: ビューの間隔と、使える範囲の 7 割まで
        free_w, free_h = (w - 2 * MARGIN - 30) * 0.7, (h - 2 * MARGIN - TITLE_H - 30) * 0.7
        for n, d in SCALES:
            if need_w * n / d <= free_w and need_h * n / d <= free_h:
                return [n, d]
        return list(SCALES[-1])

    # ------------------------------------------------------------ 寸法
    def resolve(self, view: dict, anchor: dict) -> tuple[np.ndarray, bool]:
        """寸法の相手（{"src", "hint"}）の今の位置（ビュー座標、モデルの mm）。見つからなければ前の位置と False。"""
        F = np.stack([view["R"], view["U"]], 1)
        hint = np.asarray(anchor.get("hint") or [0, 0, 0], float)
        base, _, at = anchor["src"].partition("@")
        P = self.edge_polyline(base[5:])
        if P is None:
            return hint @ F, False
        if at == "vertex":
            w = min((P[0], P[-1]), key=lambda p: float(np.linalg.norm(p - hint)))
        elif at == "mid":
            w = (P[0] + P[-1]) / 2
        elif at == "center":
            c = fit_circle(P)
            if c is None:
                return hint @ F, False
            w = c[0]
        else:
            return hint @ F, False
        anchor["hint"] = [float(x) for x in w]
        return w @ F, True

    def circle_of(self, view: dict, src: str):
        """円・円弧のエッジ（"edge:名前"）の (中心（ビュー座標）, 半径, 閉じているか)。正面を向いていなければ None。"""
        P = self.edge_polyline(src.partition("@")[0][5:])
        c = fit_circle(P) if P is not None else None
        if c is None:
            return None
        C, r, nrm = c
        N = np.cross(view["R"], view["U"])
        if abs(float(nrm @ N)) < 1 - 1e-9:
            return None
        return C @ np.stack([view["R"], view["U"]], 1), r, is_closed(P)

    def add_dim(self, view: str, a: dict, b: dict | None = None, pos=(0.0, 0.0), direction: str | None = None) -> str:
        """寸法を付ける。a・b = {"src", "hint"}。b が無く a が円のエッジなら直径（円弧は半径）。
        pos = 寸法の文字の位置（ビューの原点からの用紙の mm）。direction: h / v / aligned（省くと位置から決める）。"""
        if view not in self.views:
            raise DrawingError(f"ビュー {view} がありません")
        v = self.views[view]
        pos = [float(pos[0]), float(pos[1])]
        if b is None:
            c = self.circle_of(v, a["src"])
            if c is None:
                raise DrawingError("直径・半径の寸法は、正面を向いた円・円弧に付けてください")
            d = {"view": view, "kind": "diameter" if c[2] else "radius", "a": {"src": a["src"].partition("@")[0]}, "pos": pos}
        else:
            a, b = dict(a), dict(b)
            pa, oka = self.resolve(v, a)
            pb, okb = self.resolve(v, b)
            if not (oka and okb):
                raise DrawingError("寸法の相手が見つかりません")
            if float(np.linalg.norm(pa - pb)) < 1e-9:
                raise DrawingError("同じ位置の 2 点には寸法を付けられません")
            if direction not in ("h", "v", "aligned"):
                direction = auto_direction(pa * self.scale_of(v), pb * self.scale_of(v), pos)
            d = {"view": view, "kind": "linear", "a": a, "b": b, "dir": direction, "pos": pos}
        did = self._id("Dim")
        self.dims[did] = d
        return did


    def add_note(self, view: str, text: str, pos=(0.0, 0.0), h: float = 3.5) -> str:
        """注記（自由な文字。室名など）。寸法と同じ一覧（dims）に kind = "note" で持ち、ビューと一緒に動く（D73）。
        pos = 文字の中心の位置（ビューの位置からの用紙の mm）、h = 文字の高さ（用紙の mm）。"""
        if view not in self.views:
            raise DrawingError(f"ビュー {view} がありません")
        text = str(text).strip()
        if not text:
            raise DrawingError("注記の文字が空です")
        if not 0.5 <= float(h) <= 50:
            raise DrawingError("文字の高さは 0.5〜50 mm にしてください")
        did = self._id("Note")
        self.dims[did] = {"view": view, "kind": "note", "text": text, "pos": [float(pos[0]), float(pos[1])], "h": float(h)}
        return did

    def set_note(self, did: str, text: str | None = None, h: float | None = None) -> None:
        d = self.dims.get(did)
        if d is None or d.get("kind") != "note":
            raise DrawingError(f"注記 {did} がありません")
        if text is not None:
            if not str(text).strip():
                raise DrawingError("注記の文字が空です")
            d["text"] = str(text).strip()
        if h is not None:
            if not 0.5 <= float(h) <= 50:
                raise DrawingError("文字の高さは 0.5〜50 mm にしてください")
            d["h"] = float(h)

def auto_direction(pa, pb, pos) -> str:
    """文字の位置から寸法の向きを決める（一般的な 3D CAD のスマート寸法と同じ考え方）:
    2 点の x の範囲の上下に置けば水平、y の範囲の左右に置けば垂直、それ以外（斜めの外）は平行。"""
    x0, x1 = sorted((pa[0], pb[0]))
    y0, y1 = sorted((pa[1], pb[1]))
    in_x, in_y = x0 <= pos[0] <= x1, y0 <= pos[1] <= y1
    if abs(pa[1] - pb[1]) < 1e-9 or (in_x and not in_y):
        return "h"
    if abs(pa[0] - pb[0]) < 1e-9 or (in_y and not in_x):
        return "v"
    if in_x and in_y:
        return "aligned"
    # 角の外: 近い方の辺で決める
    dx = min(abs(pos[0] - x0), abs(pos[0] - x1))
    dy = min(abs(pos[1] - y0), abs(pos[1] - y1))
    return "h" if dy > dx else "v"


def _scale(s) -> list[int]:
    """縮尺: [分子, 分母] か "1:2" の文字。"""
    if isinstance(s, str):
        parts = s.replace("：", ":").split(":")
        if len(parts) != 2:
            raise DrawingError(f"縮尺は 1:2 のように書いてください: {s}")
        s = parts
    try:
        n, d = float(s[0]), float(s[1])
    except (TypeError, ValueError, IndexError):
        raise DrawingError(f"縮尺が不正です: {s}")
    if not (n > 0 and d > 0):
        raise DrawingError("縮尺は正の数にしてください")
    return [int(n) if n == int(n) else n, int(d) if d == int(d) else d]
