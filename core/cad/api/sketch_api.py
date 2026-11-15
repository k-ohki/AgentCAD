"""スケッチ編集と、フィーチャー作成の API（rpc.Api に登録する）。

スケッチ編集セッション
- sketch.new / sketch.begin で開始。文書ごとに 1 つ。編集中は表示用の一時ロールバック（view_rollback）で
  スケッチの直後までに戻し、モデルは「スケッチを作ったときの形」で見せる（一般的な 3D CAD と同じ）
- 編集操作は 1 回 = 1 トランザクション（Undo 1 回分）。ただしドラッグは途中を記録せず、
  最後（final=True）に 1 操作として記録する
- sketch.end で一時ロールバックを外して全体を再生成する
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

from ..part import SketchFeature
from ..part.features import FeatureError, ext_kind, external_geometry, resolve_plane
from ..sketch import Arc, Circle, Line, Point, Sketch


@dataclass
class Session:
    fid: str
    drag_before: dict | None = None
    drag_origin: dict | None = None          # ドラッグ開始時の点の座標（線の平行移動に使う）


def _ent_dict(eid, e) -> dict:
    if isinstance(e, Point):
        return {"id": eid, "type": "point", "x": e.x, "y": e.y, "fixed": e.fixed, "construction": e.construction}
    if isinstance(e, Line):
        return {"id": eid, "type": "line", "p1": e.p1, "p2": e.p2, "construction": e.construction}
    if isinstance(e, Circle):
        return {"id": eid, "type": "circle", "center": e.center, "radius": e.radius, "construction": e.construction}
    return {"id": eid, "type": "arc", "center": e.center, "start": e.start, "end": e.end, "radius": e.radius,
            "construction": e.construction}


class SketchApi:
    """Api に混ぜて使う。self.ws と self.doc_state を前提にする。"""

    def _register_sketch(self):
        self.sessions: dict[str, Session] = {}
        self.methods.update({
            "sketch.new": self.sketch_new, "sketch.begin": self.sketch_begin, "sketch.end": self.sketch_end,
            "sketch.add": self.sketch_add, "sketch.constrain": self.sketch_constrain,
            "sketch.dimension": self.sketch_dimension, "sketch.drag": self.sketch_drag,
            "sketch.delete": self.sketch_delete, "sketch.construction": self.sketch_construction,
            "sketch.trim": self.sketch_trim, "sketch.fillet": self.sketch_fillet,
            "sketch.dim_label": self.sketch_dim_label, "sketch.convert": self.sketch_convert,
            "sketch.reference": self.sketch_reference,
            "sketch.mirror": self.sketch_mirror, "sketch.pattern": self.sketch_pattern, "sketch.chamfer": self.sketch_chamfer,
            "sketch.offset": self.sketch_offset, "sketch.extend": self.sketch_extend,
            "sketch.visible": self.sketch_visible,
        })

    # ------------------------------------------------------------ セッション
    def _session(self, doc: str) -> tuple:
        d = self.ws.get(doc)
        s = self.sessions.get(doc)
        if s is None:
            raise ValueError("スケッチを編集していません")
        if not any(f.id == s.fid for f in d.features):          # Undo でスケッチ自体が消えた
            self._close(doc)
            raise ValueError("編集中のスケッチがなくなりました")
        return d, s, d.get(s.fid)

    def _open(self, doc: str, fid: str):
        d = self.ws.get(doc)
        idx = d.index(fid)
        self.sessions[doc] = Session(fid)
        d.view_rollback = idx + 1
        d.regenerate()

    def _close(self, doc: str):
        d = self.ws.get(doc)
        self.sessions.pop(doc, None)
        d.view_rollback = None
        d.regenerate()

    def sketch_new(self, doc: str, plane: dict):
        """plane: {"plane": "Front"|"Top"|"Right"} / {"face": 面の永続名} / {"feature": 参照平面の ID}。"""
        d = self.ws.get(doc)
        if doc in self.sessions:
            raise ValueError("先に今のスケッチを終了してください")
        ref = self._ref(plane, ("plane", "face", "feature"))
        ctx = d.context()
        try:                                                # 使える平面かどうかを先に確かめる
            resolve_plane(ref, ctx)
        except FeatureError as e:
            raise ValueError(str(e))
        with d.transaction("スケッチを作成"):
            fid = d.add(SketchFeature(plane=ref, sketch=Sketch()))
        self._open(doc, fid)
        return self.doc_state(doc)

    def sketch_begin(self, doc: str, id: str):
        d = self.ws.get(doc)
        if not isinstance(d.get(id), SketchFeature):
            raise ValueError(f"{id} はスケッチではありません")
        if doc in self.sessions:
            self._close(doc)
        self._open(doc, id)
        return self.doc_state(doc)

    def sketch_end(self, doc: str):
        if doc in self.sessions:
            self._close(doc)
        return self.doc_state(doc)

    def sketch_visible(self, doc: str, feature: str, visible: bool | None):
        """スケッチの 3D 表示を切り替える（右クリック → 表示/非表示）。None で自動に戻す。Undo できる。"""
        d = self.ws.get(doc)
        f = d.get(feature)
        if not isinstance(f, SketchFeature):
            raise ValueError("スケッチではありません")
        with d.transaction("スケッチを表示" if visible else "スケッチを非表示"):
            f.visible = visible
        return self.doc_state(doc, mesh=False)

    def sketch_state(self, doc: str) -> dict | None:
        """doc.state に載せる、編集中のスケッチの状態。"""
        s = self.sessions.get(doc)
        if s is None:
            return None
        d = self.ws.get(doc)
        if not any(f.id == s.fid for f in d.features):
            self._close(doc)
            return None
        idx = d.index(s.fid)
        if d.view_rollback != idx + 1:                # Undo などで位置が変わった
            d.view_rollback = idx + 1
            d.regenerate()
        f = d.get(s.fid)
        sk = f.sketch
        r = sk.solve()
        ctx = d.context()
        try:
            pl = resolve_plane(f.plane, ctx)
            plane = {"origin": list(pl.origin), "x": list(pl.x_dir), "y": list(pl.y_dir), "normal": list(pl.normal)}
        except FeatureError as e:
            plane = None
            r_err = str(e)
        else:
            r_err = None
        return {
            "id": f.id, "name": f.name, "plane": plane, "plane_error": r_err,
            "entities": [_ent_dict(k, e) for k, e in sk.entities.items()],
            "constraints": [{"id": k, "type": c.type, "refs": c.refs, "value": c.value, "expr": c.expr,
                             "label": c.label, "source": c.source} for k, c in sk.constraints.items()],
            "solve": {"status": r.status, "dof": r.dof, "state": r.state, "redundant": r.redundant,
                      "conflicting": r.conflicting, "entity_state": r.entity_state},
            "profiles": len(sk.profiles()),
        }

    # ------------------------------------------------------------ 編集
    def _edit(self, doc: str, label: str, fn):
        d, s, f = self._session(doc)
        with d.transaction(label):
            out = fn(f.sketch)
        st = self.doc_state(doc)
        st["created"] = out
        return st

    def sketch_add(self, doc: str, kind: str, points: list, radius: float | None = None,
                   construction: bool = False, sides: int = 6, relations: list | None = None, closed: bool = True):
        """要素を追加する。points の各要素は [x, y] か既存の点 ID（その点を共有＝一致）。
        線は、ちょうど水平・垂直なら水平・垂直拘束を自動で付ける（UI がスナップした結果）。
        kind: point / line / polyline（点の列。closed=True で閉じる）/ rect / crect（中心, 角）/ circle / arc（中心, 始点, 終点）/ arc3（始点, 終点, 通る点）/
        tarc（既存の線・円弧の端点, 終点。正接円弧）/ slot（中心 1, 中心 2, 幅を決める点）/ polygon（中心, 頂点。sides 角形）。
        construction=True で作図線（中心線）として作る（line / circle / arc / arc3）。
        relations（line だけ）: 作図中の推測 [[種類, 相手], ...]。種類は perpendicular / parallel（相手は線）、tangent（相手は円弧・円）。
        作った線が実際にそうなっているとき（角度の差が小さいとき）だけ拘束を付ける。"""
        def add(sk: Sketch):
            P = [self._origin_point(sk) if p == "ORIGIN" else p for p in points]
            snapped = []
            for i, p in enumerate(P):                # スナップ: {"on": 線/円, x, y} / {"mid": 線} / {"ext": ほかのスケッチの要素}
                if not isinstance(p, dict):
                    continue
                if "ext" in p:                         # ほかのスケッチの点・線（外部参照）。点としてだけ使う
                    P[i] = self._ext_point(doc, sk, p["ext"], p.get("x"), p.get("y"))
                    snapped.append(P[i])
                    continue
                tid = p.get("mid") or p.get("on")
                t = sk.entities.get(tid)
                if "mid" in p:
                    if not isinstance(t, Line):
                        raise ValueError(f"線 {tid} がありません")
                    (ax, ay), (bx, by) = sk.xy(t.p1), sk.xy(t.p2)
                    xy0, ctype = ((ax + bx) / 2, (ay + by) / 2), "midpoint"
                else:
                    if t is None or isinstance(t, Point):
                        raise ValueError(f"線・円 {tid} がありません")
                    xy0, ctype = (float(p["x"]), float(p["y"])), ("point_on_line" if isinstance(t, Line) else "point_on_circle")
                # 点として使わない位置（円の半径、矩形の角、スロットの幅）は座標だけ使う
                as_point = kind in ("point", "line", "polyline", "arc", "arc3") or (kind in ("circle", "crect", "polygon", "slot") and i == 0) \
                    or (kind in ("slot", "polygon", "tarc") and i == 1)
                if as_point:
                    P[i] = sk.point(*xy0)
                    sk.constrain(ctype, P[i], tid)
                    snapped.append(P[i])
                else:
                    P[i] = xy0
            P = [p if isinstance(p, str) else (float(p[0]), float(p[1])) for p in P]
            for p in P:
                if isinstance(p, str) and not isinstance(sk.entities.get(p), Point):
                    raise ValueError(f"点 {p} がありません")
            xy = lambda p: sk.xy(p) if isinstance(p, str) else p
            if kind == "point":
                return [sk.point(*xy(P[0]))] if not isinstance(P[0], str) else snapped
            if kind == "crect":
                (cx, cy), (x1, y1) = xy(P[0]), xy(P[1])
                if abs(x1 - cx) < 1e-9 or abs(y1 - cy) < 1e-9:
                    raise ValueError("大きさ 0 の矩形は作れません")
                return sk.center_rect(cx, cy, x1, y1, center=P[0] if isinstance(P[0], str) else None)
            if kind == "line":
                (ax, ay), (bx, by) = xy(P[0]), xy(P[1])
                L = math.hypot(bx - ax, by - ay)
                if L < 1e-9:
                    raise ValueError("長さ 0 の線は作れません")
                lid = sk.line(P[0], P[1], construction=bool(construction))
                # UI が水平・垂直にスナップした線（既存の点で閉じた場合のわずかな誤差も含む）
                if abs(by - ay) <= 1e-5 * L:
                    sk.constrain("horizontal", lid)
                elif abs(bx - ax) <= 1e-5 * L:
                    sk.constrain("vertical", lid)
                else:
                    self._infer_relations(sk, lid, relations or [])
                return [lid]
            if kind == "polyline":                  # 折れ線（点を共有する線の列）。closed なら最後の点から最初の点へも引く（D67）
                if len(P) < 2:
                    raise ValueError("折れ線には点が 2 つ以上いります")
                ids = [p if isinstance(p, str) else sk.point(*p) for p in P]
                if closed and len(ids) >= 3:
                    ids.append(ids[0])
                out = []
                for a_, b_ in zip(ids[:-1], ids[1:]):
                    (ax, ay), (bx, by) = sk.xy(a_), sk.xy(b_)
                    L = math.hypot(bx - ax, by - ay)
                    if L < 1e-9:
                        continue
                    out.append(sk.line(a_, b_, construction=bool(construction)))
                    if abs(by - ay) <= 1e-5 * L:
                        sk.constrain("horizontal", out[-1])
                    elif abs(bx - ax) <= 1e-5 * L:
                        sk.constrain("vertical", out[-1])
                return out
            if kind == "rect":
                (x0, y0), (x1, y1) = xy(P[0]), xy(P[1])
                if abs(x1 - x0) < 1e-9 or abs(y1 - y0) < 1e-9:
                    raise ValueError("大きさ 0 の矩形は作れません")
                return sk.rect(x0, y0, x1, y1)
            if kind == "circle":
                r = radius if radius is not None else math.dist(xy(P[0]), xy(P[1]))
                if r <= 0:
                    raise ValueError("半径は正の値にしてください")
                return [sk.circle(P[0], r, construction=bool(construction))]
            if kind == "arc3":
                s, e, m = (xy(p) for p in P[:3])
                den = 2 * (s[0] * (e[1] - m[1]) + e[0] * (m[1] - s[1]) + m[0] * (s[1] - e[1]))
                if abs(den) < 1e-9 * max(1.0, math.dist(s, e) ** 2):
                    raise ValueError("3 点が一直線上にあるので円弧になりません")
                q = lambda p: p[0] * p[0] + p[1] * p[1]
                c = ((q(s) * (e[1] - m[1]) + q(e) * (m[1] - s[1]) + q(m) * (s[1] - e[1])) / den,
                     (q(s) * (m[0] - e[0]) + q(e) * (s[0] - m[0]) + q(m) * (e[0] - s[0])) / den)
                ccw = (e[0] - s[0]) * (m[1] - s[1]) - (e[1] - s[1]) * (m[0] - s[0]) < 0     # 通る点が弦の右 → 始点から反時計回り
                return [sk.arc(c, P[0], P[1], construction=bool(construction)) if ccw
                        else sk.arc(c, P[1], P[0], construction=bool(construction))]
            if kind == "tarc":
                return [self._tangent_arc(sk, P[0], P[1])]
            if kind == "slot":
                c1, c2, w = xy(P[0]), xy(P[1]), xy(P[2])
                L = math.dist(c1, c2)
                if L < 1e-9:
                    raise ValueError("長さ 0 のスロットは作れません")
                r = radius if radius is not None else \
                    abs((c2[0] - c1[0]) * (w[1] - c1[1]) - (c2[1] - c1[1]) * (w[0] - c1[0])) / L
                out = sk.slot(P[0], P[1], r)
                if abs(c2[1] - c1[1]) <= 1e-5 * L:
                    sk.constrain("horizontal", out[-1])
                elif abs(c2[0] - c1[0]) <= 1e-5 * L:
                    sk.constrain("vertical", out[-1])
                return out
            if kind == "polygon":
                return sk.polygon(P[0], P[1], int(sides))
            if kind == "arc":
                c, a, b = (xy(p) for p in P[:3])
                r = math.dist(c, a)
                if r <= 0:
                    raise ValueError("半径は正の値にしてください")
                ang = math.atan2(b[1] - c[1], b[0] - c[0])
                bproj = (c[0] + r * math.cos(ang), c[1] + r * math.sin(ang))
                cp = P[0]
                kw = {"construction": bool(construction)}
                if (a[0] - c[0]) * (bproj[1] - c[1]) - (a[1] - c[1]) * (bproj[0] - c[0]) >= 0:
                    return [sk.arc(cp, P[1], bproj if not isinstance(P[2], str) else P[2], **kw)]
                return [sk.arc(cp, bproj if not isinstance(P[2], str) else P[2], P[1], **kw)]   # 時計回りに描いた → 向きを入れ替え
            raise ValueError(f"未知の要素: {kind}")
        return self._edit(doc, {"point": "点", "line": "中心線" if construction else "線", "rect": "矩形", "crect": "中心矩形", "circle": "円",
                                     "arc": "円弧", "arc3": "3 点円弧", "tarc": "正接円弧", "slot": "スロット", "polygon": "多角形", "polyline": "折れ線"}.get(kind, kind) + "を追加", add)

    @staticmethod
    def _infer_relations(sk: Sketch, lid: str, relations: list):
        """作図中の推測で吸い付いた 直角・平行・正接 を、作った線 lid に付ける（形がそうなっていなければ付けない）。"""
        e = sk.entities[lid]
        (ax, ay), (bx, by) = sk.xy(e.p1), sk.xy(e.p2)
        L = math.hypot(bx - ax, by - ay)
        ux, uy = (bx - ax) / L, (by - ay) / L
        for rel in relations:
            if not isinstance(rel, (list, tuple)) or len(rel) != 2:
                raise ValueError(f"推測の指定が不正です: {rel}")
            kind, other = rel
            t = sk.entities.get(other)
            if kind in ("perpendicular", "parallel") and isinstance(t, Line):
                (cx, cy), (dx, dy) = sk.xy(t.p1), sk.xy(t.p2)
                M = math.hypot(dx - cx, dy - cy)
                if M < 1e-12:
                    continue
                cross, dot = (ux * (dy - cy) - uy * (dx - cx)) / M, (ux * (dx - cx) + uy * (dy - cy)) / M
                if abs(dot if kind == "perpendicular" else cross) < 1e-4:
                    sk.constrain(kind, lid, other)
            elif kind == "tangent" and isinstance(t, (Arc, Circle)):
                c = sk.xy(t.center)
                dist = abs((c[0] - ax) * uy - (c[1] - ay) * ux)          # 中心から線（の延長）までの距離 = 半径なら正接
                if abs(dist - t.radius) < 1e-4 * max(1.0, t.radius):
                    sk.constrain("tangent", lid, other)
            elif kind not in ("perpendicular", "parallel", "tangent"):
                raise ValueError(f"未知の推測: {kind}")

    @staticmethod
    def _tangent_arc(sk: Sketch, start, end) -> str:
        """既存の線・円弧の端点 start から、そこで正接して end に至る円弧（一般的な 3D CAD の正接円弧）。"""
        if not isinstance(start, str):
            raise ValueError("正接円弧は、線か円弧の端点から描き始めます")
        host = next((k for k, e in sk.entities.items()
                     if (isinstance(e, Line) and not e.construction and start in (e.p1, e.p2))
                     or (isinstance(e, Arc) and start in (e.start, e.end))), None)
        if host is None:
            raise ValueError("正接円弧は、線か円弧の端点から描き始めます")
        h = sk.entities[host]
        P = sk.xy(start)
        Q = sk.xy(end) if isinstance(end, str) else end
        if isinstance(h, Line):                         # 線を端点の先へ伸ばす向き
            o = sk.xy(h.p1 if start == h.p2 else h.p2)
            t = (P[0] - o[0], P[1] - o[1])
        else:                                           # 円弧: 終点なら反時計回りの接線、始点ならその逆
            c = sk.xy(h.center)
            t = (-(P[1] - c[1]), P[0] - c[0]) if start == h.end else (P[1] - c[1], -(P[0] - c[0]))
        tl = math.hypot(*t)
        n = (-t[1] / tl, t[0] / tl)                     # 進行方向の左
        v = (Q[0] - P[0], Q[1] - P[1])
        side = v[0] * n[0] + v[1] * n[1]
        d2 = v[0] * v[0] + v[1] * v[1]
        if d2 < 1e-18 or abs(side) < 1e-9 * math.sqrt(d2):
            raise ValueError("終点が接線の上にあるので円弧になりません")
        k = d2 / (2 * side)
        c = (P[0] + n[0] * k, P[1] + n[1] * k)
        arc = sk.arc(c, start, end) if side > 0 else sk.arc(c, end, start)      # 左へ曲がれば反時計回り
        sk.constrain("tangent", host, arc)
        return arc

    def _ext_context(self, doc: str, source: str):
        """外部参照を計算する文脈と、今のスケッチの平面。参照できるのはツリーで前にあるスケッチと、今のモデル。"""
        d, s, f = self._session(doc)
        if not source.startswith("edge:"):
            sid = source.partition(":")[0]
            order = [x.id for x in d.features]
            if sid not in order or order.index(sid) >= order.index(s.fid):
                raise ValueError("参照できるのは、ツリーで今のスケッチより前にあるスケッチだけです")
        ctx = d.context()
        try:
            return d, f, ctx, resolve_plane(f.plane, ctx)
        except FeatureError as e:
            raise ValueError(str(e))

    def _ext_point(self, doc: str, sk: Sketch, source: str, x=None, y=None) -> str:
        """外部参照の点を作る: ほかのスケッチ・モデルの点（端点・中点・中心）に一致する点 / 線の上の点（D52・D54）。
        座標は今の参照先から計算する。"""
        d, f, ctx, plane = self._ext_context(doc, source)
        base = source.partition("@")[0]
        ent = None if base.startswith("edge:") else d.get(base.partition(":")[0]).sketch.entities.get(base.partition(":")[2])
        kind = ext_kind(source, ent)
        hint = None if x is None or y is None else [x, y]
        try:
            g = external_geometry(ctx, source, plane, kind, prev=hint)
        except (FeatureError, KeyError) as e:
            raise ValueError(str(e))
        if kind == "ext_point":
            for c in sk.constraints.values():             # 同じ参照の点があれば使い回す
                if c.type == "ext_point" and c.source == source and c.data and math.hypot(c.data[0] - g[0], c.data[1] - g[1]) < 1e-9:
                    return c.refs[0]
            pid = sk.point(*g)
        else:                                          # 線の上: 渡された位置を線に下ろした点
            dx, dy = g[2] - g[0], g[3] - g[1]
            t = 0.5 if hint is None else ((x - g[0]) * dx + (y - g[1]) * dy) / (dx * dx + dy * dy)
            pid = sk.point(g[0] + t * dx, g[1] + t * dy)
        cid = sk.constrain(kind, pid)
        sk.constraints[cid].source, sk.constraints[cid].data = source, [float(v) for v in g]
        return pid

    def sketch_reference(self, doc: str, source: str, x: float | None = None, y: float | None = None):
        """スマート寸法の相手に、スケッチの外（ほかのスケッチ・モデル）を使う（D54）。参照を表す要素を作って created で返す
        （同じ参照がすでにあれば使い回す）。
        - 点（"S:P"・"S:L@mid"・"edge:…@vertex|mid|center"）→ その位置に固定された点
        - ほかのスケッチの線（"S:L"）→ 両端をその線の両端に固定した作図線。円・円弧（"S:C"）→ その中心の点
        - モデルのエッジ（"edge:名前"）→ エンティティ変換と同じく投影して固定した作図線・作図円"""
        d, s, f = self._session(doc)
        base, _, at = source.partition("@")
        if base.startswith("edge:") and not at:
            st = self.sketch_convert(doc, [base[5:]], construction=True)
            return st
        ent = None if base.startswith("edge:") else d.get(base.partition(":")[0]).sketch.entities.get(base.partition(":")[2])
        if ent is None and not base.startswith("edge:"):
            raise ValueError(f"{source} がありません")
        sid = base.partition(":")[0]

        def ref(sk: Sketch):
            if isinstance(ent, Line) and not at:
                a = self._ext_point(doc, sk, f"{sid}:{ent.p1}")
                b = self._ext_point(doc, sk, f"{sid}:{ent.p2}")
                for eid, e in sk.entities.items():          # 同じ作図線があれば使い回す
                    if isinstance(e, Line) and {e.p1, e.p2} == {a, b}:
                        return [eid]
                return [sk.line(a, b, construction=True)]
            if ent is not None and hasattr(ent, "center") and not at:
                return [self._ext_point(doc, sk, f"{sid}:{ent.center}")]
            return [self._ext_point(doc, sk, source, x, y)]
        return self._edit(doc, "参照（寸法の相手）", ref)

    @classmethod
    def _resolve_origin(cls, sk: Sketch, refs) -> list[str]:
        """参照の "ORIGIN"（UI で原点をクリックしたもの）を、原点の固定点に置き換える（無ければ作る）。"""
        return [cls._origin_point(sk) if r == "ORIGIN" else r for r in refs]

    @staticmethod
    def _origin_point(sk: Sketch) -> str:
        """スケッチの原点（部品原点の投影）にある固定点。無ければ作る。"""
        for k, e in sk.entities.items():
            if isinstance(e, Point) and e.fixed and abs(e.x) < 1e-12 and abs(e.y) < 1e-12:
                return k
        return sk.point(0.0, 0.0, fixed=True)

    def sketch_constrain(self, doc: str, type: str, refs: list[str], value: float | None = None):
        """拘束を追加する。参照の順は問わない（受け付ける順に並べ替える）。
        一致は相手に合わせて 点-線上 / 点-円上 に、固定は線なら両端の固定にする。"""
        def con(sk: Sketch):
            E = sk.entities
            refs_ = self._resolve_origin(sk, refs)
            for r in refs_:
                if r not in E:
                    raise ValueError(f"要素 {r} がありません")
            t = type
            if t == "coincident" and len(refs_) == 2:
                kinds = {type_name(E[r]) for r in refs_}
                if kinds == {"point", "line"}:
                    t = "point_on_line"
                elif kinds & {"circle", "arc"} and "point" in kinds:
                    t = "point_on_circle"
                elif kinds == {"circle"} or kinds == {"arc"} or kinds == {"circle", "arc"}:
                    t = "concentric"
            if t == "fix":
                out = []
                for r in refs_:
                    e = E[r]
                    pts = [r] if isinstance(e, Point) else [e.p1, e.p2] if isinstance(e, Line) else [e.center]
                    out += [sk.constrain("fix", p) for p in pts]
                return out
            if t in ("horizontal", "vertical") and len(refs_) > 1 and all(isinstance(E[r], Line) for r in refs_):
                return [sk.constrain(t, r) for r in refs_]
            last = None
            for perm in itertools.permutations(refs_):
                try:
                    return [sk.constrain(t, *perm, value=value)]
                except ValueError as e:
                    last = e
            raise last or ValueError("拘束を付けられません")
        return self._edit(doc, f"拘束（{type}）", con)

    def sketch_dimension(self, doc: str, refs: list[str], value: float | None = None, type: str | None = None,
                         label: list[float] | None = None):
        refs_in = list(refs)
        """スマート寸法: 選んだ要素から寸法の種類を決める。値を省くと今の値で作る。
        type: 線・2 点の向き（"hdistance" 水平 / "vdistance" 垂直 / 省略で平行）、円の "radius"（既定は円=直径・円弧=半径）。
        label: 寸法の文字の位置（既定の位置からのずれ、スケッチ座標）。UI は置いた場所から計算して渡す。"""
        def dim(sk: Sketch):
            E = sk.entities
            refs = self._resolve_origin(sk, refs_in)
            for r in refs:
                if r not in E:
                    raise ValueError(f"要素 {r} がありません")
            kinds = [type_name(E[r]) for r in refs]
            ctr = lambda r: E[r].center if not isinstance(E[r], Point) else r
            if type in ("hdistance", "vdistance"):
                if kinds == ["line"]:
                    pts = [E[refs[0]].p1, E[refs[0]].p2]
                elif len(refs) == 2 and "line" not in kinds:
                    pts = [ctr(r) for r in refs]
                else:
                    raise ValueError("水平・垂直の寸法は、線 1 本か点（円の中心）2 つに付けます")
                if pts[0] == pts[1]:
                    raise ValueError("同じ点どうしには寸法を付けられません")
                return [sk.constrain(type, *pts, value=value)]
            if type == "distance" and kinds == ["line", "line"]:          # 平行な 2 線の間隔
                return [sk.constrain("distance", E[refs[0]].p1, refs[1], value=value)]
            if kinds == ["line"]:
                return [sk.constrain("length", refs[0], value=value)]
            if len(refs) == 1 and kinds[0] in ("circle", "arc"):
                t = type or ("diameter" if kinds[0] == "circle" else "radius")
                if t not in ("radius", "diameter"):
                    raise ValueError(f"円・円弧に {t} は付けられません")
                return [sk.constrain(t, refs[0], value=value)]
            if sorted(kinds) == ["point", "point"]:
                return [sk.constrain("distance", *refs, value=value)]
            if sorted(kinds) == ["line", "point"]:
                p, l = (refs if kinds[0] == "point" else refs[::-1])
                return [sk.constrain("distance", p, l, value=value)]
            if sorted(kinds) in (["circle", "line"], ["arc", "line"]):
                c, l = (refs if kinds[1] == "line" else refs[::-1])
                return [sk.constrain("distance", E[c].center, l, value=value)]
            if kinds == ["line", "line"]:
                return [sk.constrain("angle", *refs, value=value)]
            if sorted(kinds) in (["circle", "point"], ["arc", "point"], ["circle", "circle"], ["arc", "circle"], ["arc", "arc"]):
                return [sk.constrain("distance", ctr(refs[0]), ctr(refs[1]), value=value)]
            raise ValueError("この組み合わせには寸法を付けられません（線 / 円 / 点と点 / 点と線 / 線と線）")

        def dim_with_label(sk: Sketch):
            out = dim(sk)
            if label is not None:
                sk.constraints[out[0]].label = [float(label[0]), float(label[1])]
            return out
        return self._edit(doc, "寸法を追加", dim_with_label)

    def sketch_dim_label(self, doc: str, id: str, label: list[float] | None):
        """寸法の文字の位置を変える（None で既定の位置）。"""
        def mv(sk: Sketch):
            c = sk.constraints.get(id)
            if c is None or c.value is None:
                raise ValueError(f"寸法 {id} がありません")
            c.label = None if label is None else [float(label[0]), float(label[1])]
            return []
        return self._edit(doc, "寸法を移動", mv)

    def sketch_drag(self, doc: str, x: float, y: float, point: str | None = None, entity: str | None = None,
                    grab: list[float] | None = None, final: bool = False):
        """ドラッグ。point: 点を (x, y) へ。entity: 線は掴んだ位置 grab からの移動量だけ平行移動、
        円・円弧は (x, y) を通る大きさに。途中（final=False）は形だけ返し、最後に 1 操作として記録する。"""
        d, s, f = self._session(doc)
        sk = f.sketch
        target = point or entity
        if target not in sk.entities or (point and not isinstance(sk.entities[point], Point)):
            raise ValueError(f"{'点' if point else '要素'} {target} がありません")
        if s.drag_before is None:
            s.drag_before = d.to_dict()
            s.drag_origin = {k: (e.x, e.y) for k, e in sk.entities.items() if isinstance(e, Point)}
        e = sk.entities[target]
        if isinstance(e, Point):
            sk.drag(target, float(x), float(y), diagnose=False)
        elif isinstance(e, Line):
            gx, gy = grab if grab is not None else (x, y)
            dx, dy = float(x) - gx, float(y) - gy
            o = s.drag_origin
            sk.drag_many({p: (o[p][0] + dx, o[p][1] + dy) for p in (e.p1, e.p2)})
        else:
            cx, cy = sk.xy(e.center)
            sk.drag_many({}, {target: max(math.hypot(float(x) - cx, float(y) - cy), 1e-6)})
        if not final:
            return {"entities": [_ent_dict(k, e) for k, e in sk.entities.items()]}
        before, s.drag_before, s.drag_origin = s.drag_before, None, None
        d.commit({Point: "点", Line: "線", Circle: "円", Arc: "円弧"}[type(e)] + "をドラッグ", before)
        return self.doc_state(doc)

    def sketch_convert(self, doc: str, edges: list[str], construction: bool = False):
        """エンティティ変換: モデルのエッジをスケッチ平面に投影して線・円・円弧を作り、エッジ上に固定する
        （on_edge。モデルが変わると再生成で追従する）。変換できないエッジは warnings に入れる。"""
        from ..sketch.fit import fit, project
        d, s, f = self._session(doc)
        if not edges:
            raise ValueError("モデルのエッジか面を選んでください")
        ctx = d.context()
        try:
            plane = resolve_plane(f.plane, ctx)
            if d.body is None:
                raise FeatureError("投影するモデルがありません")
        except FeatureError as e:
            raise ValueError(str(e))
        done = {c.source: c.refs[0] for c in f.sketch.constraints.values() if c.type == "on_edge"}
        if construction and len(edges) == 1 and edges[0] in done:   # 寸法の相手: 変換済みならそれを使う
            st = self.doc_state(doc, mesh=False)
            st["created"] = [done[edges[0]]]
            return st
        shapes, warnings = [], []
        for name in dict.fromkeys(edges):
            if name in done:
                continue
            try:
                shapes.append((name, *fit(project(d.kernel.edge_polyline(d.body, ctx.edge(name)), plane))))
            except Exception as e:                       # noqa: BLE001 変換できないエッジは飛ばして知らせる
                warnings.append(f"{name}: {e}")
        if not shapes:
            raise ValueError("変換できるエッジがありません" + (f"（{' / '.join(warnings)}）" if warnings else "（すべて変換済み）"))

        def conv(sk: Sketch):
            out = []
            for name, kind, g in shapes:
                if kind == "line":
                    eid = sk.line((g[0], g[1]), (g[2], g[3]), construction)
                elif kind == "circle":
                    eid = sk.circle((g[0], g[1]), g[2], construction)
                else:
                    eid = sk.arc((g[0], g[1]), (g[2], g[3]), (g[4], g[5]), construction)
                cid = sk.constrain("on_edge", eid)
                sk.constraints[cid].source = name
                out.append(eid)
            return out
        st = self._edit(doc, "参照（寸法の相手）" if construction else "エンティティ変換", conv)
        st["warnings"] = warnings
        return st

    def sketch_delete(self, doc: str, ids: list[str]):
        fid = self._session(doc)[1].fid

        def rm(sk: Sketch):
            import re
            for i in ids:
                if i in sk.entities or i in sk.constraints:
                    sk.delete(i)
            # 消えた寸法を式で参照していた寸法（オフセットの子など）は、今の値のまま式を外す
            for c in sk.constraints.values():
                if c.expr and any(n not in sk.constraints for n in re.findall(rf"\b{re.escape(fid)}\.(\w+)", c.expr)):
                    c.expr = None
            return []
        return self._edit(doc, "削除", rm)

    def sketch_construction(self, doc: str, ids: list[str], value: bool | None = None):
        def cons(sk: Sketch):
            es = [sk.entities[i] for i in ids if i in sk.entities and not isinstance(sk.entities[i], Point)]
            v = (not all(e.construction for e in es)) if value is None else bool(value)
            for e in es:
                e.construction = v
            return []
        return self._edit(doc, "作図線の切り替え", cons)

    def sketch_trim(self, doc: str, id: str, x: float, y: float):
        return self._edit(doc, "トリム", lambda sk: sk.trim(id, float(x), float(y)))

    def sketch_mirror(self, doc: str, ids: list[str], axis: str | None = None):
        """エンティティのミラー。axis を省くと、ids の中の作図線（1 本だけのとき）を軸にする。"""
        def mir(sk: Sketch):
            ax = axis
            if ax is None:
                cl = [i for i in ids if isinstance(sk.entities.get(i), Line) and sk.entities[i].construction]
                if len(cl) != 1:
                    raise ValueError("ミラーの軸にする作図線を 1 本含めて選んでください")
                ax = cl[0]
            return sk.mirror([i for i in ids if i != ax], ax)
        return self._edit(doc, "ミラー", mir)

    def sketch_pattern(self, doc: str, ids: list[str], kind: str, count: int, spacing: float = 10.0, angle: float = 0.0,
                       center: str | list | None = None, total: float = 360.0):
        """スケッチの直線パターン（kind="linear": count・spacing・angle）/ 円形パターン（kind="circular": count・total・center）。
        center は点の ID か [x, y]。省くと原点。"""
        def pat(sk: Sketch):
            c = (0.0, 0.0)
            if isinstance(center, str):
                if center != "ORIGIN" and not isinstance(sk.entities.get(center), Point):
                    raise ValueError(f"中心の点 {center} がありません")
                c = (0.0, 0.0) if center == "ORIGIN" else sk.xy(center)
            elif center is not None:
                c = (float(center[0]), float(center[1]))
            return sk.pattern([i for i in ids if i != center], kind, int(count), spacing=float(spacing), angle=float(angle),
                              center=c, total=float(total))
        return self._edit(doc, "直線パターン" if kind == "linear" else "円形パターン", pat)

    def sketch_fillet(self, doc: str, radius: float, l1: str | None = None, l2: str | None = None, point: str | None = None):
        """角を丸める。線 2 本（l1, l2）か、角の点（point）で指定する。"""
        def fil(sk: Sketch):
            from ..sketch.ops import corner_lines
            a, b = corner_lines(sk, point) if point else (l1, l2)
            return [sk.fillet(a, b, float(radius))]
        return self._edit(doc, "スケッチフィレット", fil)

    def sketch_chamfer(self, doc: str, distance: float, l1: str | None = None, l2: str | None = None,
                       point: str | None = None, distance2: float | None = None):
        """角を面取りする。線 2 本か角の点で指定。distance2 を省くと等距離。"""
        def cha(sk: Sketch):
            from ..sketch.ops import corner_lines
            a, b = corner_lines(sk, point) if point else (l1, l2)
            return [sk.chamfer(a, b, float(distance), None if distance2 is None else float(distance2))]
        return self._edit(doc, "スケッチ面取り", cha)

    def sketch_offset(self, doc: str, ids: list[str], distance: float, x: float | None = None, y: float | None = None,
                      reverse: bool = False):
        """エンティティ オフセット。(x, y) を渡すとその点の側へ。寸法は 1 つを親にして残りが式で従う。"""
        fid = self._session(doc)[1].fid
        side = None if x is None or y is None else (float(x), float(y))
        return self._edit(doc, "オフセット", lambda sk: sk.offset(ids, float(distance), side, bool(reverse), link=f"{fid}."))

    def sketch_extend(self, doc: str, id: str, x: float, y: float):
        return self._edit(doc, "延長", lambda sk: [sk.extend(id, float(x), float(y))])


def type_name(e) -> str:
    return {Point: "point", Line: "line", Circle: "circle", Arc: "arc"}[type(e)]
