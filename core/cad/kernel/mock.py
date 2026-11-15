"""テスト用の記号的カーネル。実形状は計算せず、面・エッジの位相と履歴だけを模擬する。
フィーチャーツリー・再生成・ネーミングのテストをカーネル無しで回すために使う。"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

import numpy as np

from .base import Kernel, KernelError, Mesh, OpResult, Plane, Profile
from .meshtopo import plane_frame

_ids = itertools.count(1)


def _h(prefix):
    return f"{prefix}{next(_ids)}"


@dataclass
class MockShape:
    faces: dict[str, dict] = field(default_factory=dict)      # handle → {"plane": Plane|None}
    edges: dict[str, tuple[str, str]] = field(default_factory=dict)
    volume: float = 0.0


def _area(loop):
    if len(loop) == 1 and loop[0].kind == "circle":
        return math.pi * loop[0].radius ** 2
    pts = [s.points[0] if s.kind == "line" else s.points[1] for s in loop]
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))) / 2


class MockKernel(Kernel):
    geometric = False

    def __init__(self):
        self.calls: list[str] = []      # テストで再計算回数を確認する

    def extrude(self, profile: Profile, distance: float) -> OpResult:
        self.calls.append("extrude")
        if distance == 0:
            raise KernelError("距離が0です")
        s = MockShape()
        hist = {}
        n = np.asarray(profile.plane.normal)
        start, end = _h("F"), _h("F")
        o = np.asarray(profile.plane.origin)
        s.faces[start] = {"plane": plane_frame(o, -n)}
        s.faces[end] = {"plane": plane_frame(o + distance * n, n)}
        hist[start] = ("cap", "start"); hist[end] = ("cap", "end")
        area = 0.0
        for li, loop in enumerate(profile.loops):
            a = _area(loop)
            area += a if li == 0 else -a
            sides = []
            for seg in loop:
                f = _h("F"); s.faces[f] = self._side_geom(profile.plane, seg)
                hist[f] = ("side", seg.tag); sides.append(f)
                s.edges[_h("E")] = (f, start); s.edges[_h("E")] = (f, end)
            for a_, b_ in zip(sides, sides[1:] + sides[:1]):
                if a_ != b_:
                    s.edges[_h("E")] = (a_, b_)
        s.volume = area * abs(distance)
        return OpResult(s, hist)

    @staticmethod
    def _side_geom(plane: Plane, seg) -> dict:
        """側面の幾何: 線分 → 平面、円/円弧 → 円筒軸。"""
        n = np.asarray(plane.normal, float)
        if seg.kind == "line":
            (ax, ay), (bx, by) = seg.points
            a = np.asarray(plane.to_world(ax, ay)); b = np.asarray(plane.to_world(bx, by))
            d = (b - a) / np.linalg.norm(b - a)
            return {"plane": plane_frame(a, np.cross(d, n))}
        c = plane.to_world(*seg.points[0])
        r = seg.radius if seg.kind == "circle" else float(np.hypot(*np.subtract(seg.points[1], seg.points[0])))
        return {"plane": None, "axis": (tuple(c), tuple(n)), "radius": r}

    def face_radius(self, shape, face):
        return shape.faces[face].get("radius")

    def face_axis(self, shape, face):
        return shape.faces[face].get("axis")

    def revolve(self, profile, axis, angle_deg):
        self.calls.append("revolve")
        s = MockShape(); hist = {}
        for loop in profile.loops:
            for seg in loop:
                f = _h("F"); s.faces[f] = {"plane": None}; hist[f] = ("side", seg.tag)
        s.volume = _area(profile.loops[0]) * angle_deg / 360.0
        return OpResult(s, hist)

    def sweep(self, profile, path):
        """記号的なスイープ: 端の蓋 2 つと、断面の要素ごとの側面。体積 = 断面の面積 × 経路の長さ。"""
        from .meshtopo import path_polyline
        self.calls.append("sweep")
        s = MockShape(); hist = {}
        P, _ = path_polyline(path)
        closed = float(np.linalg.norm(P[0] - P[-1])) < 1e-7
        if not closed:
            for cap in ("start", "end"):
                f = _h("F"); s.faces[f] = {"plane": None}; hist[f] = ("cap", cap)
        area = 0.0
        for li, loop in enumerate(profile.loops):
            a = _area(loop)
            area += a if li == 0 else -a
            for seg in loop:
                f = _h("F"); s.faces[f] = {"plane": None}; hist[f] = ("side", seg.tag)
        s.volume = area * float(np.linalg.norm(np.diff(P, axis=0), axis=1).sum())
        return OpResult(s, hist)

    def boolean(self, op, a: MockShape, b: MockShape) -> OpResult:
        self.calls.append(op)
        s = MockShape(); hist = {}
        for src in (a, b):
            for f, info in src.faces.items():
                nf = _h("F"); s.faces[nf] = dict(info); hist[nf] = ("modified", f)
        rev = {old: new for new, (_, old) in hist.items()}
        for src in (a, b):
            for e, (fa, fb) in src.edges.items():
                s.edges[_h("E")] = (rev[fa], rev[fb])
        s.volume = {"union": a.volume + b.volume, "cut": a.volume - b.volume,
                    "intersect": min(a.volume, b.volume)}[op]
        return OpResult(s, hist)

    def fillet(self, shape: MockShape, edges, radius) -> OpResult:
        self.calls.append("fillet")
        for e in edges:
            if e not in shape.edges:
                raise KernelError(f"エッジ {e} が存在しません")
        s = MockShape(); hist = {}
        rev = {}
        for f, info in shape.faces.items():
            nf = _h("F"); s.faces[nf] = dict(info); hist[nf] = ("modified", f); rev[f] = nf
        for e, (fa, fb) in shape.edges.items():
            if e in edges:
                ff = _h("F"); s.faces[ff] = {"plane": None}; hist[ff] = ("fillet", e)
                s.edges[_h("E")] = (rev[fa], ff); s.edges[_h("E")] = (ff, rev[fb])
            else:
                s.edges[_h("E")] = (rev[fa], rev[fb])
        s.volume = shape.volume - 0.2146 * radius ** 2 * len(edges)
        return OpResult(s, hist)

    def chamfer(self, shape: MockShape, edges, distance) -> OpResult:
        res = self.fillet(shape, edges, distance)
        self.calls[-1] = "chamfer"
        res.history.update({h: ("chamfer", src) for h, (k, src) in res.history.items() if k == "fillet"})
        res.shape.volume = shape.volume - 0.5 * distance ** 2 * len(edges)
        return res

    def shell(self, shape: MockShape, faces, thickness) -> OpResult:
        """取り除く面以外を残し、残った面ごとに壁の面を 1 つ足す（体積は面の数から決めた仮の値）。"""
        self.calls.append("shell")
        for f in faces:
            if f not in shape.faces:
                raise KernelError(f"面 {f} が存在しません")
        if thickness == 0:
            raise KernelError("厚さは 0 より大きくしてください")
        s = MockShape(); hist = {}; rev = {}
        for f, info in shape.faces.items():
            if f in faces:
                continue
            nf = _h("F"); s.faces[nf] = dict(info); hist[nf] = ("modified", f); rev[f] = nf
            w = _h("F"); s.faces[w] = {"plane": None}; hist[w] = ("shell", f)
            s.edges[_h("E")] = (nf, w)
        for e, (fa, fb) in shape.edges.items():
            if fa in rev and fb in rev:
                s.edges[_h("E")] = (rev[fa], rev[fb])
        s.volume = shape.volume * 0.2
        return OpResult(s, hist)

    def faces(self, shape):
        return list(shape.faces)

    def edges(self, shape):
        return [(e, a, b) for e, (a, b) in shape.edges.items()]

    def face_plane(self, shape, face):
        return shape.faces[face]["plane"]

    def tessellate(self, shape, tol=0.1) -> Mesh:
        handles = list(shape.faces)
        return Mesh(np.zeros((0, 3), np.float32), np.zeros((0, 3), np.uint32), np.zeros(0, np.uint32), handles)

    def volume(self, shape):
        return shape.volume
