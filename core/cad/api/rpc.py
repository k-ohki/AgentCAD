"""UI 向けの API（JSON-RPC のメソッド本体）。HTTP には依存しない（server.py が包む）。

方針
- 編集系のメソッドは 1 回 = 1 トランザクション（Undo 1 回分）で、結果として文書の状態を返す
- UI は状態を持たない。返ってきた状態をそのまま描く（docs/SPEC.md 9 章）
- メッシュは base64（リトルエンディアン）の float32 / uint32 配列で送る
"""
from __future__ import annotations

import os

import base64
import collections
import threading
import math
from typing import Any, Callable

import numpy as np

from ..doc.expr import ExprError, references
from ..naming import DanglingReference
from ..doc.workspace import Workspace, display_mesh
from ..kernel.base import STANDARD_PLANES
from ..part import ExtrudeFeature, FilletFeature, PartDocument, PlaneFeature, SketchFeature
from ..part import appearance
from ..part.features import InstanceFeature
from ..sketch import Sketch
from .agent_api import AgentApi
from .asm_api import AsmApi
from .drawing_api import DrawingApi
from .errors import RpcError
from .feature_api import FeatureApi
from .sketch_api import SketchApi

__all__ = ["Api", "RpcError"]


PLANE_LABELS = {"Front": "正面", "Top": "平面", "Right": "右側面"}


def _b64(a: np.ndarray, dtype) -> str:
    return base64.b64encode(np.ascontiguousarray(a, dtype=np.dtype(dtype).newbyteorder("<")).tobytes()).decode()


#: 文書を変えないメソッド（ほかのウィンドウに更新を知らせない）
#: HTTP サーバのロックの外で呼ぶメソッド（AI の経過を待つ間も、AI 自身が API を呼べるように。D67）
UNLOCKED = {"agent.poll", "agent.stop", "ui.progress"}
READ_ONLY = {"kernel.info", "doc.list", "doc.state", "ui.progress", "feature.params", "ws.rev", "ui.open_window", "ui.pick_path", "ui.settings",
             "agent.settings", "agent.chat", "agent.poll", "agent.stop", "agent.reset",
             "asm.mate_options", "asm.joints", "drw.export", "doc.export"}


class Api(FeatureApi, SketchApi, AsmApi, DrawingApi, AgentApi):
    #: 表示の設定の既定値（D66）。一般的な 3D CAD が普段は出さないもの（体積など）は既定で計算しない。
    #: テストは体積で形を確かめるので、tests/conftest.py が show_volume を True にする
    DEFAULT_SETTINGS = {"show_volume": False}

    def __init__(self, ws: Workspace):
        self.last_dir: str | None = None     # 最後にファイルダイアログで選んだフォルダ
        self.settings = dict(self.DEFAULT_SETTINGS)
        #: API を呼ぶときのロック（HTTP サーバが自分のロックに差し替える。AI エージェントも同じロックで呼ぶ）
        self.lock = threading.RLock()
        self.ws = ws
        #: 変更の通し番号と、だれ（ウィンドウ）が変えたか。ほかのウィンドウはこれを見て表示を更新する
        self.rev = 0
        self.rev_log: collections.deque = collections.deque(maxlen=256)
        #: 文書を新しいウィンドウで開く関数（Tauri の殻から起動したとき server が入れる）。None ならブラウザが開く
        self.window_opener: Callable[[str, str], None] | None = None
        self.methods: dict[str, Callable[..., Any]] = {
            "kernel.info": self.kernel_info,
            "doc.list": self.doc_list, "doc.new": self.doc_new, "doc.demo": self.doc_demo,
            "doc.open": self.doc_open, "doc.save": self.doc_save, "doc.close": self.doc_close, "doc.export": self.doc_export,
            "doc.state": self.doc_state, "ui.pick_path": self.ui_pick_path, "ui.settings": self.ui_settings,
            "variable.set": self.variable_set,
            "edit.undo": self.undo, "edit.redo": self.redo,
            "ws.rev": self.ws_rev, "ui.open_window": self.ui_open_window, "ui.progress": self.ui_progress,
        }
        self._register_feature()
        self._register_drawing()
        self._register_sketch()
        self._register_asm()
        self._register_agent()

    def call(self, method: str, params: dict | None = None) -> Any:
        fn = self.methods.get(method)
        if fn is None:
            raise RpcError(f"未知のメソッド: {method}")
        params = dict(params or {})
        client = params.pop("_client", None)            # 呼んだウィンドウ（ほかのウィンドウの更新に使う）
        if method not in READ_ONLY:
            self.rev += 1
            self.rev_log.append((self.rev, client))
        try:
            return fn(**params)
        except TypeError as e:
            raise RpcError(f"{method} の引数が不正です: {e}")
        except (KeyError, ValueError, ExprError, DanglingReference) as e:
            raise RpcError(str(e.args[0]) if isinstance(e, KeyError) and e.args else str(e))

    # ------------------------------------------------------------ 文書
    def ui_settings(self, **values):
        """表示の設定を変えて、全部の設定を返す（引数なしなら読むだけ）。show_volume: 状態に体積を載せるか（D66）。"""
        for k, v in values.items():
            if k not in self.DEFAULT_SETTINGS:
                raise ValueError(f"未知の設定: {k}")
            self.settings[k] = type(self.DEFAULT_SETTINGS[k])(v)
        return dict(self.settings)

    def ws_rev(self, since: int = 0, me: str | None = None):
        """変更の通し番号。others = since より後に、自分（me）以外のウィンドウが何か変えたか。"""
        old = not self.rev_log or self.rev_log[0][0] > since + 1      # 記録より古い: 念のため更新させる
        others = old and since < self.rev or any(r > since and c != me for r, c in self.rev_log)
        return {"rev": self.rev, "others": bool(others)}

    def ui_open_window(self, doc: str):
        """文書を新しいウィンドウで開く（一般的な 3D CAD のように部品・アセンブリごとのウィンドウ）。
        Tauri では殻がウィンドウを作る。opened が False ならブラウザ側で window.open する。"""
        self.ws.get(doc)
        if self.window_opener is None:
            return {"opened": False}
        self.window_opener(doc, self.ws.names[doc])
        return {"opened": True}

    def kernel_info(self):
        from ..doc.workspace import kernel_choices
        return {"name": self.ws.kernel_name, "available": sorted(kernel_choices()),
                "shell_windows": self.window_opener is not None}

    def doc_list(self):
        return [{"doc": d, "name": self.ws.names[d], "path": self.ws.paths[d], "modified": self.ws.modified(d),
                 "kind": self.ws.kind(d)} for d in self.ws.docs]

    def doc_new(self, name: str = "Part"):
        return self.doc_state(self.ws.new_part(name))

    def doc_open(self, path: str):
        did = self.ws.open(path)
        # 形の表示メッシュ（面の分割）も時間がかかるので、進み具合を「表示の準備」にしておく
        self.ws.progress = {"title": f"{self.ws.names[did]} を開いています", "step": "表示の準備", "done": 1, "total": 1}
        try:
            return self.doc_state(did)
        finally:
            self.ws.progress = None

    def ui_progress(self):
        """時間のかかる処理（ファイルを開く）の進み具合 {title, step, done, total}。何もしていなければ null。
        HTTP のロックの外で呼ばれるので、開いている途中でも返る（D69）。"""
        p = self.ws.progress
        return dict(p) if p else None

    def ui_pick_path(self, mode: str, doc: str | None = None, ext: str = "usda"):
        """OS のファイルダイアログでパスを選ぶ。戻り値 {path（取り消しは null）, available}。
        ダイアログを出せない環境では available=false（UI はパスの入力欄に切り替える）。ext: usda / pdf / dxf / stl / 3mf / obj（書き出し先）。"""
        from .dialogs import DialogUnavailable, pick_path
        initial = None
        if doc is not None and doc in self.ws.docs:
            p = self.ws.paths.get(doc)
            initial = os.path.splitext(p)[0] + "." + ext if p else os.path.join(self.last_dir or "", self.ws.names[doc] + "." + ext)
        elif self.last_dir:
            initial = os.path.join(self.last_dir, "")
        try:
            path = pick_path(mode, initial, ext)
        except DialogUnavailable as e:
            return {"path": None, "available": False, "reason": str(e)}
        if path:
            self.last_dir = os.path.dirname(path)
        return {"path": path, "available": True}

    def doc_save(self, doc: str, path: str | None = None):
        """USD（.usda）で保存。戻り値 {path, saved（書いたファイル。アセンブリは部品も）, warnings}。"""
        return self.ws.save(doc, path)

    def doc_export(self, doc: str, path: str, quality: str = "standard"):
        """パート・アセンブリを STL / 3MF / OBJ に書き出す（拡張子で形式を決める。D71）。quality: coarse / standard / fine。
        戻り値 {path, files（OBJ は .mtl も）, objects, triangles}。"""
        return self.ws.export(doc, path, quality)

    def doc_close(self, doc: str):
        self.sessions.pop(doc, None)
        self.ws.close(doc)
        return {}

    def doc_demo(self, kind: str = "plate"):
        """動作確認用の部品。plate: 板 + ボス + 穴 + フィレット（幅は変数 width）。car: 車の形（demos.py、D63）。"""
        if kind == "car":
            from .demos import build_car
            did = build_car(self)
            self.ws.get(did).undo_stack.clear()
            return self.doc_state(did)
        if kind != "plate":
            raise ValueError(f"未知のデモ: {kind}")
        did = self.ws.new_part("Demo")
        d = self.ws.get(did)
        with d.transaction("デモ部品"):
            d.variables.exprs["width"] = "100"
            sk = Sketch(); ls = sk.rect(0, 0, 100, 60)
            sk.constrain("fix", sk.entities[ls[0]].p1)
            kw = sk.constrain("length", ls[0], value=100); sk.constrain("length", ls[1], value=60)
            sk.constraints[kw].expr = "width"
            s1 = d.add(SketchFeature(sketch=sk)); e1 = d.add(ExtrudeFeature(sketch=s1, distance=20))
            bs = Sketch(); bc = bs.circle((30, 30), 15)
            bs.constrain("fix", bs.entities[bc].center); bs.constrain("radius", bc, value=15)
            s2 = d.add(SketchFeature(plane={"face": f"{e1}/end"}, sketch=bs))
            e2 = d.add(ExtrudeFeature(sketch=s2, distance=15))
            hs = Sketch(); hc = hs.circle((30, 30), 6)
            hs.constrain("fix", hs.entities[hc].center); hs.constrain("radius", hc, value=6)
            s3 = d.add(SketchFeature(plane={"face": f"{e2}/end"}, sketch=hs))
            d.add(ExtrudeFeature(sketch=s3, distance=35, op="cut"))
            d.add(FilletFeature(edges=[f"E[{e1}/end|{e1}/side/{ls[1]}]"], radius=5))
        d.undo_stack.clear()
        return self.doc_state(did)

    # ------------------------------------------------------------ 状態
    def doc_state(self, doc: str, mesh: bool = True):
        d = self.ws.get(doc)
        if self.ws.is_assembly(doc):
            return self.asm_state(doc, mesh)
        if self.ws.kind(doc) == "drawing":
            return self.drw_state(doc)
        sketch = self.sketch_state(doc)
        vars_ = [{"name": n, "expr": e, "value": d.var_values.get(n), "error": d.var_errors.get(n)}
                 for n, e in d.variables.exprs.items()]
        tree = d.tree()
        for t in tree:
            f = d.get(t["id"])
            t["type_label"] = f.label
            if isinstance(f, ExtrudeFeature):
                t["op"] = f.op
            if isinstance(f, InstanceFeature):
                t["seeds"] = list(f.seeds)
        out = {"doc": doc, "kind": "part", "name": self.ws.names[doc], "path": self.ws.paths[doc], "kernel": self.ws.kernel_name,
               "modified": self.ws.modified(doc), "tree": tree, "rollback": d.rollback, "variables": vars_, "appearance": d.appearance,
               "planes": self._planes(d), "sketches": self._sketches(d),
               "undo": d.undo_stack[-1][0] if d.undo_stack else None,
               "redo": d.redo_stack[-1][0] if d.redo_stack else None,
               "volume": None, "has_body": d.body is not None, "mesh": None, "sketch": sketch}
        if d.body is not None:
            if self.settings["show_volume"]:          # 体積は表示するときだけ計算する（D66）
                try:
                    out["volume"] = float(d.kernel.volume(d.body))
                except NotImplementedError:
                    pass
            if mesh:
                out["mesh"] = self._mesh(d)
        if not mesh:
            del out["mesh"]                    # 送らない（UI は前のメッシュを使い続ける）
        return out

    @staticmethod
    def _planes(d: PartDocument) -> list[dict]:
        """標準平面と参照平面（UI がツリーに並べ、選んだものを 3D に描く）。"""
        def one(ref, name, pl):
            return {"ref": ref, "name": name, "origin": list(pl.origin), "x": list(pl.x_dir), "y": list(pl.y_dir),
                    "normal": list(pl.normal)}
        out = [one({"plane": k}, PLANE_LABELS[k], pl) for k, pl in STANDARD_PLANES.items()]
        for f in d.features:
            r = d.results.get(f.id)
            if isinstance(f, PlaneFeature) and r is not None and not r.error and r.data:
                out.append(one({"feature": f.id}, f.name, r.data["plane"]))
        return out

    @staticmethod
    def _sketches(d: PartDocument) -> list[dict]:
        """スケッチの 3D 表示用データ（ワールド座標）。点・線は、ほかのスケッチを描くときのスナップ先にもなる。
        shown = 表示するか（visible が None なら、どのフィーチャーにも使われていなければ表示: 一般的な 3D CAD と同じ）。"""
        users = {}
        for f in d.features:
            if isinstance(f, SketchFeature):        # ほかのスケッチからの参照（外部参照・平面）は「使われた」に入れない
                continue
            for dep in f.deps():
                users.setdefault(dep, []).append(f.id)
            sk = getattr(f, "sketch_id", None) or getattr(f, "sketch", None)
            if isinstance(sk, str):
                users.setdefault(sk, []).append(f.id)
        out = []
        for i, f in enumerate(d.features):
            r = d.results.get(f.id)
            if not isinstance(f, SketchFeature) or r is None or r.error or not r.data or "profile" not in r.data:
                continue
            pl, sk = r.data["profile"].plane, f.sketch
            W = lambda x, y: [float(v) for v in pl.to_world(x, y)]
            pts, curves = [], []
            for k, e in sk.entities.items():
                t = type(e).__name__
                if t == "Point":
                    pts.append({"id": k, "p": W(e.x, e.y)})
                elif t == "Line":
                    curves.append({"id": k, "type": "line", "construction": e.construction,
                                   "pts": [W(*sk.xy(e.p1)), W(*sk.xy(e.p2))]})
                else:
                    cx, cy = sk.xy(e.center)
                    if t == "Circle":
                        a0, a1 = 0.0, 2 * math.pi
                    else:
                        sx, sy = sk.xy(e.start); ex, ey = sk.xy(e.end)
                        a0, a1 = math.atan2(sy - cy, sx - cx), math.atan2(ey - cy, ex - cx)
                        a1 += 2 * math.pi if a1 <= a0 else 0.0
                    n = max(8, int(48 * (a1 - a0) / (2 * math.pi)))
                    curves.append({"id": k, "type": t.lower(), "construction": e.construction,
                                   "pts": [W(cx + e.radius * math.cos(a0 + (a1 - a0) * j / n),
                                             cy + e.radius * math.sin(a0 + (a1 - a0) * j / n)) for j in range(n + 1)]})
            used = bool(users.get(f.id))
            out.append({"id": f.id, "name": f.name, "index": i, "used": used, "visible": f.visible,
                        "shown": f.visible if f.visible is not None else not used,
                        "plane": {"origin": list(pl.origin), "x": list(pl.x_dir), "y": list(pl.y_dir), "normal": list(pl.normal)},
                        "points": pts, "curves": curves})
        return out

    def _mesh(self, d: PartDocument) -> dict:
        m = display_mesh(d)
        pos = m["positions"]
        lo, hi = (pos.min(0), pos.max(0)) if len(pos) else (np.zeros(3), np.zeros(3))
        return {
            "positions": _b64(pos.ravel(), np.float32),
            "indices": _b64(m["indices"].ravel(), np.uint32),
            "face_of_triangle": _b64(m["face_of_triangle"], np.uint32),
            "face_names": m["face_names"],
            "edges": _b64(m["edges"].ravel(), np.float32),
            "edge_of_segment": _b64(m["edge_of_segment"], np.uint32),
            "edge_names": m["edge_names"],
            "edge_faces": m["edge_faces"],
            "bbox": [lo.tolist(), hi.tolist()],
            **dict(zip(("part_color", "face_colors"), appearance.face_colors(
                d.appearance, m["face_names"], {f.id: f for f in d.features}))),    # 外観（D69）
        }


    def variable_set(self, doc: str, name: str, expr: str | None = None):
        d = self.ws.get(doc)
        if not name.isidentifier():
            raise RpcError(f"変数名に使えません: {name}")
        if expr:
            references(str(expr))
        with d.transaction(f"変数 {name}"):
            if expr is None or not str(expr).strip():
                d.variables.exprs.pop(name, None)
            else:
                d.variables.exprs[name] = str(expr).strip()
        return self.doc_state(doc)

    def undo(self, doc: str):
        self.ws.get(doc).undo()
        return self.doc_state(doc)

    def redo(self, doc: str):
        self.ws.get(doc).redo()
        return self.doc_state(doc)
