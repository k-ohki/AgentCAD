"""フィーチャーの API: 作成（feature.add）、寸法・設定の編集、ツリーの操作（移動・削除・抑制・名前・ロールバック）。

種類ごとの違いはフィーチャーのクラスが持つ（label・options・hidden_params・option_changed・param_edited。part/features）。
ここにあるのは、作るときの既定値と検査（MAKERS）と、画面に出す名前の表だけ。
"""
from __future__ import annotations

import inspect
import math

from ..doc.expr import references
from ..part import appearance
from ..part import (ChamferFeature, CircularPatternFeature, ExtrudeFeature, FilletFeature, HoleFeature, LinearPatternFeature,
                    MirrorFeature, PartDocument, PlaneFeature, RevolveFeature, ShellFeature, SketchFeature, SweepFeature)
from ..part.features import END_CONDITIONS, OPS
from ..part.holes import KINDS as HOLE_KINDS, METRIC as HOLE_SIZES
from ..sketch import Arc, Circle, Line
from .errors import RpcError

DIM_LABELS = {"distance": "距離", "hdistance": "水平距離", "vdistance": "垂直距離", "length": "長さ",
              "radius": "半径", "angle": "角度"}
PARAM_LABELS = {"distance": "距離", "radius": "半径", "angle": "角度", "offset": "オフセット", "spacing": "間隔",
                "count": "個数", "spacing2": "間隔（方向 2）", "count2": "個数（方向 2）", "thickness": "厚さ",
                "depth": "深さ", "diameter": "穴の直径", "cbore_diameter": "ザグリの直径", "cbore_depth": "ザグリの深さ",
                "csink_diameter": "皿の直径", "csink_angle": "皿の角度", "tip_angle": "ドリルの先端角"}
PARAM_UNITS = {"angle": "°", "count": "個", "count2": "個", "csink_angle": "°", "tip_angle": "°"}
OPTION_LABELS = {"end": "押し出し状態", "op": "操作", "reverse": "向きを反転", "base": "基準の平面", "seeds": "元のフィーチャー",
                 "direction": "方向", "direction2": "方向 2", "reverse2": "方向 2 を反転", "axis": "回転軸", "plane": "ミラーの平面",
                 "faces": "取り除く面", "outward": "外側に厚みを付ける", "kind": "穴の種類", "size": "サイズ（メートル並目）",
                 "profile": "断面（スケッチ）", "path": "経路（スケッチ）"}
#: 参照の設定（options の種類）ごとの、使える参照の形
REF_KEYS = {"plane": ("plane", "face", "feature"), "dir": ("axis", "edge"), "dir?": ("axis", "edge"),
            "axis": ("axis", "edge", "face")}


def _world_axis(v) -> str:
    """ベクトルにいちばん近い世界の軸の名前。"""
    return "XYZ"[max(range(3), key=lambda i: abs(v[i]))]


def _sketch_of(d: PartDocument, sid, what: str = "スケッチ") -> SketchFeature:
    f = next((x for x in d.features if x.id == sid), None)
    if not isinstance(f, SketchFeature):
        raise ValueError(f"{what}を選んでください")
    return f


def _op(op: str) -> str:
    if op not in dict(OPS):
        raise ValueError(f"未知の操作: {op}")
    return op


class FeatureApi:
    """Api に混ぜる。self.ws / self.sessions / self._close / self.doc_state を使う。"""

    def _register_feature(self):
        self.methods.update({
            "feature.add": self.feature_add, "feature.params": self.feature_params, "feature.edit": self.feature_edit,
            "feature.set": self.feature_set, "feature.suppress": self.feature_suppress,
            "feature.rename": self.feature_rename, "feature.delete": self.feature_delete,
            "feature.move": self.feature_move, "rollback.set": self.rollback_set, "view.rollback": self.view_rollback,
            "appearance.set": self.appearance_set,
        })

    # ------------------------------------------------------------ 作成
    def feature_add(self, doc: str, type: str, **params):
        """フィーチャーを作る。type ごとの引数は _make_<type>（MAKERS）。関係ない引数は無視する
        （UI は「スケッチから作る」ボタンで同じ引数を送る）が、どの種類にも無い引数はエラー。"""
        d = self.ws.get(doc)
        make = MAKERS.get(type)
        if make is None:
            raise ValueError(f"未知のフィーチャー: {type}")
        unknown = set(params) - KNOWN_PARAMS
        if unknown:
            raise TypeError(f"unexpected keyword argument {sorted(unknown)[0]!r}")
        if doc in self.sessions:
            self._close(doc)
        accepted = inspect.signature(make).parameters
        feat = make(self, d, **{k: v for k, v in params.items() if k in accepted})
        label = "カット" if getattr(feat, "op", None) == "cut" and isinstance(feat, ExtrudeFeature) else \
            "穴" if isinstance(feat, HoleFeature) else feat.label
        with d.transaction(f"{label}を作成"):
            fid = d.add(feat)
        st = self.doc_state(doc)
        st["created"] = [fid]
        return st

    def _make_extrude(self, d, sketch=None, distance: float = 10.0, op: str = "add", reverse: bool = False, end: str = "blind"):
        if end not in END_CONDITIONS:
            raise ValueError(f"未知の押し出し状態: {end}")
        return ExtrudeFeature(sketch=_sketch_of(d, sketch).id, distance=float(distance), op=_op(op), reverse=bool(reverse), end=end)

    def _make_revolve(self, d, sketch=None, angle: float = 360.0, axis_line: str | None = None, op: str = "add"):
        sk = _sketch_of(d, sketch).sketch
        _op(op)
        if axis_line is None:
            axes = [k for k, e in sk.entities.items() if isinstance(e, Line) and e.construction]
            if len(axes) != 1:
                raise ValueError("回転軸にする作図線（中心線）を 1 本だけ用意してください")
            axis_line = axes[0]
        return RevolveFeature(sketch=sketch, axis_line=axis_line, angle=float(angle), op=op)

    def _make_sweep(self, d, sketch=None, path: str | None = None, op: str = "add"):
        sks = [f for f in d.features if isinstance(f, SketchFeature)]
        _sketch_of(d, sketch, "断面のスケッチ")
        curves = lambda f, all_=False: [e for e in f.sketch.entities.values()
                                        if isinstance(e, (Line, Arc, Circle)) and (all_ or not e.construction)]
        if path is None:                       # 経路: 断面でない、いちばん新しい「閉じていない線」のスケッチ
            def is_path(f):
                r = d.results.get(f.id)
                return curves(f) and not (r and r.data and r.data.get("profile") and r.data["profile"].loops)
            cands = [f for f in sks if f.id != sketch and is_path(f)] or [f for f in sks if f.id != sketch and curves(f, True)]
            if not cands:
                raise ValueError("経路のスケッチがありません（断面とは別のスケッチに、経路の線・円弧を描いてください）")
            path = cands[-1].id
        if path == sketch:
            raise ValueError("経路のスケッチを選んでください")
        _sketch_of(d, path, "経路のスケッチ")
        return SweepFeature(profile=sketch, path=path, op=op)

    def _make_hole(self, d, sketch=None, kind: str = "cbore", size: str = "M6", depth: float = 15.0, end: str = "blind",
                   reverse: bool = False):
        _sketch_of(d, sketch, "穴の位置のスケッチ")
        if kind not in dict(HOLE_KINDS) or (size not in HOLE_SIZES and size != "custom"):
            raise ValueError(f"未知の穴: {kind} {size}")
        feat = HoleFeature(sketch=sketch, kind=kind, size=size, depth=float(depth),
                           end=end if end in ("blind", "through") else "blind", reverse=bool(reverse))
        feat.apply_standard()
        return feat

    def _make_fillet(self, d, edges=None, radius: float = 1.0):
        if not edges:
            raise ValueError("フィレットするエッジを選んでください")
        return FilletFeature(edges=list(edges), radius=float(radius))

    def _make_chamfer(self, d, edges=None, distance: float = 10.0):
        if not edges:
            raise ValueError("面取りするエッジを選んでください")
        return ChamferFeature(edges=list(edges), distance=float(distance))

    def _make_shell(self, d, faces=None, thickness: float = 2.0):
        if d.body is None:
            raise ValueError("シェルにするボディがありません")
        return ShellFeature(faces=list(faces or []), thickness=float(thickness))

    def _make_plane(self, d, base=None, offset: float = 10.0, reverse: bool = False):
        return PlaneFeature(base=self._ref(base or {"plane": "Front"}, REF_KEYS["plane"]), offset=float(offset), reverse=bool(reverse))

    def _make_lpattern(self, d, seeds=None, direction=None, spacing: float = 20.0, count=None, reverse: bool = False):
        return LinearPatternFeature(
            seeds=self._seeds(d, seeds), spacing=float(spacing), count=int(count or 2), reverse=bool(reverse),
            direction=self._ref(direction or self._seed_axis(d, seeds, "x_dir", "X"), REF_KEYS["dir"]))

    def _make_cpattern(self, d, seeds=None, axis=None, angle: float = 360.0, count=None, reverse: bool = False):
        return CircularPatternFeature(
            seeds=self._seeds(d, seeds), count=int(count or 4), angle=float(angle), reverse=bool(reverse),
            axis=self._ref(axis or self._seed_axis(d, seeds, "normal", "Y"), REF_KEYS["axis"]))

    def _make_mirror(self, d, seeds=None, plane=None):
        return MirrorFeature(seeds=self._seeds(d, seeds), plane=self._ref(plane or {"plane": "Right"}, REF_KEYS["plane"]))

    @staticmethod
    def _seed_axis(d, seeds, attr: str, fallback: str) -> dict:
        """パターンの向き・軸の既定: 元のフィーチャーのスケッチ平面の x 方向 / 法線にいちばん近い世界の軸。
        （正面のスケッチから作った穴なら、直線パターンは X、円形パターンは Z まわりになる）"""
        try:
            pl = d.results[d.get(seeds[0]).sketch].data["profile"].plane
            return {"axis": _world_axis(getattr(pl, attr))}
        except Exception:                                # noqa: BLE001 分からなければ決まった値
            return {"axis": fallback}

    # ------------------------------------------------------------ 寸法・設定
    def feature_params(self, doc: str, id: str):
        d = self.ws.get(doc)
        f = d.get(id)
        out = []
        if isinstance(f, SketchFeature):
            for cid, c in f.sketch.constraints.items():
                if c.value is None:
                    continue
                unit = "°" if c.type == "angle" else "mm"
                out.append({"key": cid, "label": f"{DIM_LABELS.get(c.type, c.type)} ({cid})", "value": c.value,
                            "expr": c.expr, "unit": unit})
        else:
            hide = f.hidden_params()
            for k, v in f.dims().items():
                if k in hide:
                    continue
                out.append({"key": k, "label": PARAM_LABELS.get(k, k), "value": v, "expr": f.exprs.get(k),
                            "unit": PARAM_UNITS.get(k, "mm")})
        st = d.status.get(id)
        spec = f.options
        options = {k: getattr(f, k) for k in spec}
        meta = []
        for k, kind in spec.items():
            m = {"key": k, "label": f.option_labels.get(k) or OPTION_LABELS.get(k, k), "value": getattr(f, k)}
            if isinstance(kind, tuple):
                m.update(kind="choice", choices=[list(c) for c in kind])
            elif kind == "sketch":                    # ツリーでこのフィーチャーより前のスケッチから選ぶ
                before = d.features[:d.index(id)]
                m.update(kind="choice", choices=[[x.id, x.name] for x in before if isinstance(x, SketchFeature)])
            elif kind is bool:
                m["kind"] = "bool"
            else:
                m["kind"] = kind
            meta.append(m)
        if hasattr(f, "edges"):                       # フィレット・面取りの対象（表示用）
            options["edges"] = list(f.edges)
        return {"id": id, "name": f.name, "type": f.type, "type_label": f.label,
                "suppressed": f.suppressed, "params": out, "options": options, "option_meta": meta,
                "state": st.state if st else None, "message": st.message if st else None}

    def feature_edit(self, doc: str, id: str, key: str, value: float | None = None, expr: str | None = None):
        """パラメータを数値か式で変更する。expr="" は式の解除。"""
        d = self.ws.get(doc)
        f = d.get(id)
        if value is None and expr is None:
            raise RpcError("value か expr を指定してください")
        if value is not None and not math.isfinite(float(value)):
            raise RpcError("数値が不正です")
        if expr:
            references(expr)                  # 構文だけ先に確かめる（未定義の名前は再生成時にエラー表示）
        with d.transaction(f"{f.name} の {key} を変更"):
            if isinstance(f, SketchFeature):
                if key not in f.sketch.constraints or f.sketch.constraints[key].value is None:
                    raise KeyError(f"寸法 {key} がありません")
                c = f.sketch.constraints[key]
                if expr is not None:
                    c.expr = expr.strip() or None
                if value is not None:
                    c.value, c.expr = float(value), None
            else:
                if key not in f.dims():
                    raise KeyError(f"パラメータ {key} がありません")
                if expr is not None:
                    if expr.strip():
                        f.exprs[key] = expr.strip()
                    else:
                        f.exprs.pop(key, None)
                if value is not None:
                    f.exprs.pop(key, None)
                    setattr(f, key, float(value))
                    f.param_edited(key)
        return self.doc_state(doc)

    def feature_set(self, doc: str, id: str, key: str, value):
        """数値以外の設定を変える: 押し出しの操作 op・押し出し状態 end・反転、パターンの向き・軸・元のフィーチャー、
        参照平面の基準など（OPTIONS）。参照は {"plane"|"face"|"feature"|"axis"|"edge": 名前} の形。"""
        d = self.ws.get(doc)
        f = d.get(id)
        allowed = f.options
        if key not in allowed:
            raise RpcError(f"{f.name} に設定 {key} はありません")
        kind = allowed[key]
        if kind is bool:
            value = bool(value)
        elif isinstance(kind, tuple):
            if value not in [c[0] for c in kind]:
                raise RpcError(f"{key} に使えない値です: {value}")
        elif kind == "seeds":
            value = self._seeds(d, value, exclude=id)
        elif kind == "sketch":
            before = d.features[:d.index(f.id)]
            if not any(isinstance(x, SketchFeature) and x.id == value for x in before):
                raise RpcError("ツリーでこのフィーチャーより前のスケッチを選んでください")
        elif kind == "faces":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise RpcError("面の名前の一覧を渡してください")
            value = list(dict.fromkeys(value))
        elif value is None and kind.endswith("?"):
            pass
        else:
            value = self._ref(value, REF_KEYS[kind])
        if doc not in self.sessions:
            d.view_rollback = None                   # 参照を選ぶための一時的な巻き戻し（view.rollback）を外す
        with d.transaction(f"{f.name} の設定を変更"):
            setattr(f, key, value)
            f.option_changed(key)
        return self.doc_state(doc)

    def view_rollback(self, doc: str, id: str | None = None):
        """表示だけを「フィーチャー id の直前」まで巻き戻す（id を省くと元に戻す）。保存・Undo の対象外。
        既にあるフィーチャーの参照（面・エッジ）を選び直すとき、そのフィーチャーから見える形で選べるようにする
        （後のフィーチャーが面を分けると名前が変わるため。一般的な 3D CAD のフィーチャー編集と同じ見せ方）。"""
        d = self.ws.get(doc)
        if doc in self.sessions:
            raise RpcError("スケッチの編集中は使えません")
        d.view_rollback = None if id is None else d.index(id)
        d.regenerate()
        return self.doc_state(doc)

    @staticmethod
    def _ref(value, keys) -> dict:
        if not isinstance(value, dict) or len(value) != 1 or next(iter(value)) not in keys \
                or not isinstance(next(iter(value.values())), str):
            raise RpcError(f"参照の形が不正です（{' / '.join(keys)} のどれか 1 つ）: {value}")
        return dict(value)

    @staticmethod
    def _seeds(d: PartDocument, ids, exclude: str | None = None) -> list[str]:
        if not isinstance(ids, list) or not ids:
            raise RpcError("元にするフィーチャー（押し出し・カット・回転）を選んでください")
        for i in ids:
            f = next((x for x in d.features if x.id == i), None)
            if f is None or not hasattr(f, "tool") or i == exclude:
                raise RpcError(f"{i} はパターン・ミラーの元にできません（押し出し・カット・回転を選んでください）")
        return list(dict.fromkeys(ids))

    # ------------------------------------------------------------ ツリーの操作
    def feature_move(self, doc: str, id: str, index: int):
        """ツリーの中でフィーチャーを移す（index は移動後の位置）。依存が逆転したものはエラー表示になる。"""
        d = self.ws.get(doc)
        f = d.get(id)
        if doc in self.sessions:
            self._close(doc)
        with d.transaction(f"{f.name} を移動"):
            d.move(id, int(index))
        return self.doc_state(doc)

    def feature_delete(self, doc: str, id: str):
        d = self.ws.get(doc)
        f = d.get(id)
        s = self.sessions.get(doc)
        if s and s.fid == id:
            self._close(doc)
        with d.transaction(f"{f.name} を削除"):
            d.delete(id)
        return self.doc_state(doc)

    def feature_suppress(self, doc: str, id: str, value: bool = True):
        d = self.ws.get(doc)
        f = d.get(id)
        with d.transaction(f"{f.name} を{'抑制' if value else '抑制解除'}"):
            f.suppressed = bool(value)
        return self.doc_state(doc)

    def feature_rename(self, doc: str, id: str, name: str):
        d = self.ws.get(doc)
        f = d.get(id)
        if not name.strip():
            raise RpcError("名前が空です")
        with d.transaction(f"{f.name} の名前を変更"):
            f.name = name.strip()
        return self.doc_state(doc, mesh=False)

    def appearance_set(self, doc: str, color=None, feature: str | None = None, face: str | None = None,
                       faces: list | None = None, features: list | None = None):
        """外観（色。D69）。color: "#rrggbb"（null で外す）。対象: faces / face（面の永続名）> features / feature（ID）> 省略でパート全体。
        優先は 面 > フィーチャー > パート。パターン・ミラーの複製は、自身に色が無ければ元の色を引き継ぐ。"""
        d = self.ws.get(doc)
        c = appearance.normalize_color(color)
        face_list = list(faces or []) + ([face] if face else [])
        feat_list = list(features or []) + ([feature] if feature else [])
        for fid in feat_list:
            d.get(fid)                                   # 無ければ KeyError
        app = d.appearance
        what = "面" if face_list else "フィーチャー" if feat_list else "パート"
        with d.transaction(f"{what}の外観を{'変更' if c else '解除'}"):
            if face_list:
                tbl = app.setdefault("faces", {})
                for n in face_list:
                    tbl.pop(n, None) if c is None else tbl.__setitem__(n, c)
            elif feat_list:
                tbl = app.setdefault("features", {})
                for fid in feat_list:
                    tbl.pop(fid, None) if c is None else tbl.__setitem__(fid, c)
            else:
                app["part"] = c
        return self.doc_state(doc)

    def rollback_set(self, doc: str, index: int | None = None):
        d = self.ws.get(doc)
        if index is not None and not 0 <= index <= len(d.features):
            raise RpcError("ロールバック位置が範囲外です")
        if index == len(d.features):
            index = None
        with d.transaction("ロールバック"):
            d.set_rollback(index)
        return self.doc_state(doc)


#: 種類 → 作る関数。引数の名前がそのまま feature.add の引数になる
MAKERS = {name[len("_make_"):]: fn for name, fn in vars(FeatureApi).items() if name.startswith("_make_")}
KNOWN_PARAMS = {p for fn in MAKERS.values() for p in list(inspect.signature(fn).parameters)[2:]}
