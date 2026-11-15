"""AI エージェントの道具（D67）。CAD の API（Api.call）をそのまま呼べる（全権限。利用者の指示）。

道具:
- cad_call: API のメソッドを 1 つ呼ぶ。doc を省くと「今の文書」
- cad_batch: 複数の呼び出しを順に（途中で失敗したらそこで止まる）
- api_help: メソッドの引数と説明
- render_view: 形を画像にして見る（render.py）
- ui_action: 画面だけの操作（断面表示・表示方向・表示スタイル・文書の切り替え・全体表示）。画面に頼む
- run_python: Python を実行する（api・call・np・math が使える。繰り返しの多い形を作るとき）
結果は要約した JSON（メッシュなどの大きな配列は除く）。
"""
from __future__ import annotations

import contextlib
import inspect
import io
import json
import math
import threading
import traceback
from typing import Any, Callable

import numpy as np

from ..api.errors import RpcError
from .providers import ToolOutput
from ..part import appearance
from .render import VIEWS, clip_triangles, render_png

#: 結果の JSON の長さの上限（文字）。超えた分は切る
MAX_RESULT = 24000
#: 要約で除くキー（画面用の大きなデータ）
DROP_KEYS = {"mesh", "parts", "positions", "indices", "face_of_triangle", "edges", "edge_of_segment", "segs", "snaps",
             "items", "edge_faces", "matrix", "data"}

#: 断面の平面 → 法線（viewer.js の断面表示と同じ。法線の向きの側を消して奥を残す）
SECTION_NORMALS = {"Front": (0.0, 0.0, 1.0), "Top": (0.0, 1.0, 0.0), "Right": (1.0, 0.0, 0.0)}
UI_ACTIONS = ("show_document", "set_view", "section", "section_off", "set_style", "fit", "normal_to")

TOOLS: list[dict] = [
    {"name": "cad_call",
     "description": "CAD の API のメソッドを 1 つ呼ぶ。文書・スケッチ・フィーチャー・アセンブリ・図面の作成と編集、状態の取得など、"
                    "モデルを作る・変える操作はすべてこれで行う。params に doc を書かなければ『今の文書』を使う。"
                    "引数が分からないときは先に api_help を呼ぶ。",
     "input_schema": {"type": "object", "properties": {
         "method": {"type": "string", "description": "メソッド名（例: sketch.add, feature.add, doc.state）"},
         "params": {"type": "object", "description": "引数（JSON）"}},
         "required": ["method"]}},
    {"name": "cad_batch",
     "description": "複数の API 呼び出しを順に実行する（スケッチの線を何本も描くときなど、往復を減らすため）。"
                    "途中で失敗したらそこで止まり、それまでの結果と失敗の理由を返す。",
     "input_schema": {"type": "object", "properties": {
         "calls": {"type": "array", "items": {"type": "object", "properties": {
             "method": {"type": "string"}, "params": {"type": "object"}}, "required": ["method"]}}},
         "required": ["calls"]}},
    {"name": "api_help",
     "description": "API のメソッドの引数と説明を返す。method を省くと一覧（名前と 1 行の説明）。名前の前方一致でも引ける（例: 'sketch.'）。",
     "input_schema": {"type": "object", "properties": {"method": {"type": "string"}}, "required": []}},
    {"name": "render_view",
     "description": "パートかアセンブリの形を画像にして返す（作った形が意図どおりかを目で確かめるために、形を作ったら呼ぶ）。"
                    f"view: {', '.join(VIEWS)}。section を付けると平面で切って奥の側だけを描く（空洞や内部の部品を見る。"
                    "画面の断面表示と同じ指定。切り口は塗らない）。",
     "input_schema": {"type": "object", "properties": {
         "doc": {"type": "string"}, "view": {"type": "string", "enum": list(VIEWS)},
         "width": {"type": "integer"}, "height": {"type": "integer"},
         "section": {"type": "object", "description": "{plane: Front|Top|Right, offset: 平面からの距離 mm, flip: 反対側を残す}",
                     "properties": {"plane": {"type": "string", "enum": ["Front", "Top", "Right"]},
                                    "offset": {"type": "number"}, "flip": {"type": "boolean"}}}}, "required": []}},
    {"name": "ui_action",
     "description": "画面だけの操作を画面に頼む（モデルは変わらない）。"
                    "show_document {doc}: その文書を画面に出す / set_view {view: front|back|left|right|top|bottom|iso} / "
                    "section {plane: Front|Top|Right, offset: mm, flip: bool}: 断面表示 / section_off / "
                    "set_style {style: shaded_edges|shaded|hlr|wire} / fit: 全体表示 / normal_to {face}: 面に正対。",
     "input_schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": list(UI_ACTIONS)}, "args": {"type": "object"}}, "required": ["action"]}},
    {"name": "run_python",
     "description": "Python のコードを実行する（全権限）。使える名前: call(method, **params)（API を呼ぶ。doc を省くと今の文書）、"
                    "api（Api の実体）、doc（今の文書の ID）、np、math。print した内容と、変数 result の値を返す。"
                    "柱や窓を何十個も並べるなど、繰り返しの多い形を作るときに使う。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
]


def _round(x: Any) -> Any:
    if isinstance(x, float):
        return round(x, 4)
    if isinstance(x, dict):
        return {k: _round(v) for k, v in x.items() if k not in DROP_KEYS}
    if isinstance(x, (list, tuple)):
        if len(x) > 80:
            return [_round(v) for v in x[:80]] + [f"…（ほか {len(x) - 80} 件）"]
        return [_round(v) for v in x]
    return x


def _dump(x: Any) -> str:
    s = json.dumps(_round(x), ensure_ascii=False, default=str)
    return s if len(s) <= MAX_RESULT else s[:MAX_RESULT] + "…（長いので省略）"


class CadTools:
    """道具の実行。lock は HTTP サーバと同じロック（画面からの操作と交互に API を呼ぶ）。"""

    def __init__(self, api, lock: threading.RLock | None = None, emit: Callable[[dict], None] = lambda e: None):
        self.api = api
        self.lock = lock or getattr(api, "lock", None) or threading.RLock()
        self.emit = emit
        self.doc: str | None = None              # 今の文書

    # ------------------------------------------------------------ API
    def call(self, method: str, params: dict | None = None) -> Any:
        params = dict(params or {})
        fn = self.api.methods.get(method)
        if fn is None:
            raise RpcError(f"未知のメソッド: {method}（api_help で一覧を見られます）")
        if "doc" not in params and self.doc is not None and "doc" in inspect.signature(fn).parameters \
                and method not in ("doc.new", "doc.demo", "doc.open", "doc.list"):
            params["doc"] = self.doc
        with self.lock:
            res = self.api.call(method, params)
        self._track(method, params, res)
        return res

    def _track(self, method: str, params: dict, res: Any):
        """文書が変わる呼び出しなら「今の文書」を覚え、画面に知らせる。"""
        new = None
        if isinstance(res, dict) and isinstance(res.get("doc"), str) and method in (
                "doc.new", "doc.demo", "doc.open", "asm.new", "drw.new"):
            new = res["doc"]
        elif method in ("doc.close",) and params.get("doc") == self.doc:
            self.doc = None
        if new:
            self.doc = new
            self.emit({"type": "doc", "doc": new})
        elif method not in ("doc.state", "doc.list", "kernel.info", "feature.params", "asm.mate_options", "ui.settings"):
            self.emit({"type": "changed", "doc": params.get("doc", self.doc)})

    def summary(self, res: Any) -> Any:
        """状態の要約。パートには体積と外形（外接箱）を足す（画面の既定では体積を計算しないので。D66）。"""
        if isinstance(res, dict) and res.get("kind") == "part" and res.get("doc") in self.api.ws.docs:
            d = self.api.ws.get(res["doc"])
            out = dict(res)
            if d.body is not None:
                with self.lock:
                    try:
                        out["volume"] = float(d.kernel.volume(d.body))
                    except NotImplementedError:
                        pass
                    mesh = res.get("mesh")
                if mesh and mesh.get("bbox"):
                    out["bbox"] = mesh["bbox"]
                if mesh and mesh.get("face_names"):
                    out["face_names"] = mesh["face_names"]          # 面の名前（スケッチの面・フィレット・合致の指定に使う）
            sk = out.get("sketches")
            if isinstance(sk, list):
                out["sketches"] = [{"id": s.get("id"), "name": s.get("name"), "shown": s.get("shown")} for s in sk]
            if isinstance(out.get("planes"), list):
                out["planes"] = [{"ref": p.get("ref"), "name": p.get("name")} for p in out["planes"]]
            return out
        return res

    # ------------------------------------------------------------ 道具
    def run(self, name: str, args: dict, call_id: str) -> ToolOutput:
        try:
            if "__invalid_json__" in args:
                raise ValueError(f"引数が JSON として読めません: {str(args['__invalid_json__'])[:200]}")
            fn = getattr(self, "t_" + name, None)
            if fn is None:
                raise ValueError(f"未知の道具: {name}")
            text, images = fn(**args)
            return ToolOutput(call_id, text, images)
        except TypeError as e:
            return ToolOutput(call_id, f"引数が違います: {e}", is_error=True)
        except (RpcError, ValueError, KeyError) as e:
            return ToolOutput(call_id, f"エラー: {e.args[0] if isinstance(e, KeyError) and e.args else e}", is_error=True)
        except Exception as e:  # noqa: BLE001  道具の失敗はモデルに返して立て直させる
            return ToolOutput(call_id, f"内部エラー: {type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}", is_error=True)

    def t_cad_call(self, method: str, params: dict | None = None):
        if not isinstance(method, str):
            raise ValueError("method は文字列で指定してください")
        return _dump(self.summary(self.call(method, params if isinstance(params, dict) else {}))), []

    def t_cad_batch(self, calls: list):
        if not isinstance(calls, list):
            raise ValueError("calls は配列で指定してください")
        out = []
        for i, c in enumerate(calls):
            try:
                res = self.call(c.get("method", ""), c.get("params") or {})
            except (RpcError, ValueError, KeyError, TypeError) as e:
                msg = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
                out.append({"index": i, "method": c.get("method"), "error": msg})
                return _dump({"stopped_at": i, "results": out}), []
            short = {k: res[k] for k in ("doc", "created") if isinstance(res, dict) and k in res}
            out.append({"index": i, "method": c.get("method"), **short})
        last = self.summary(res) if calls else None                   # 最後の状態だけ詳しく
        return _dump({"ok": len(calls), "results": out, "last_state": last}), []

    def t_api_help(self, method: str = ""):
        rows = []
        for name, fn in sorted(self.api.methods.items()):
            if method and not name.startswith(method):
                continue
            doc = inspect.getdoc(fn) or ""
            if method and (name == method or len([n for n in self.api.methods if n.startswith(method)]) <= 8):
                rows.append(f"{name}{inspect.signature(fn)}\n{doc}")
            else:
                rows.append(f"{name}: {doc.splitlines()[0] if doc else ''}")
        return ("\n\n" if method else "\n").join(rows) or f"見つかりません: {method}", []

    def t_render_view(self, doc: str | None = None, view: str = "iso", width: int = 800, height: int = 600,
                      section: dict | None = None):
        did = doc or self.doc
        if did is None:
            raise ValueError("文書がありません（先に doc.new などで作ってください）")
        width, height = max(200, min(int(width), 1600)), max(150, min(int(height), 1200))
        with self.lock:
            tris, fids, colors = self._triangles(did)
        if tris is None or not len(tris):
            return "形がありません（ボディが無い）", []
        cut = ""
        if section:
            n = SECTION_NORMALS.get(section.get("plane", "Front"))
            if n is None:
                raise ValueError(f"section.plane は Front / Top / Right: {section.get('plane')}")
            off = float(section.get("offset") or 0)
            if section.get("flip"):
                n, off = tuple(-x for x in n), -off
            tris, fids = clip_triangles(tris, fids, n, off)
            cut = f"、{section.get('plane', 'Front')} から {float(section.get('offset') or 0):g} mm で切断"
            if not len(tris):
                return f"切った奥の側に形がありません{cut}", []
        png = render_png(tris, fids, view, width, height, face_colors=colors)
        lo, hi = tris.reshape(-1, 3).min(0), tris.reshape(-1, 3).max(0)
        return f"view={view}{cut}、外形 {np.round(lo, 2).tolist()} 〜 {np.round(hi, 2).tolist()}（mm、Y が上）", [png]

    def _triangles(self, did: str):
        """(三角形, 面番号, 面番号 → 色 (r, g, b)) 。色は外観（D69）。"""
        ws = self.api.ws

        def colors_of(part_did, m):
            d = ws.get(part_did)
            _, cols = appearance.face_colors(d.appearance, m["face_names"], {f.id: f for f in d.features})
            return [appearance.rgb(c) for c in cols]
        if ws.is_part(did):
            _, m = ws.part_mesh(did)
            if not m:
                return None, None, None
            return m["positions"][m["indices"].reshape(-1, 3)], m["face_of_triangle"], colors_of(did, m)
        if ws.is_assembly(did):
            asm = ws.get(did)
            asm.solve()
            tris, fids, cols, off = [], [], [], 0
            for c in asm.components.values():
                if not ws.is_part(c.source):
                    continue
                _, m = ws.part_mesh(c.source)
                if not m:
                    continue
                M = np.asarray(c.matrix(), float).reshape(4, 4)
                P = m["positions"] @ M[:3, :3].T + M[:3, 3]
                tris.append(P[m["indices"].reshape(-1, 3)]); fids.append(m["face_of_triangle"] + off)
                cc = colors_of(c.source, m)
                cols += cc
                off += len(cc)
            return (np.concatenate(tris), np.concatenate(fids), cols) if tris else (None, None, None)
        raise ValueError("画像にできるのはパートかアセンブリです（図面は drw.export で PDF にできます）")

    def t_ui_action(self, action: str, args: dict | None = None):
        if action not in UI_ACTIONS:
            raise ValueError(f"未知の画面操作: {action}")
        args = dict(args or {})
        if action == "show_document":
            args.setdefault("doc", self.doc)
            if args["doc"] not in self.api.ws.docs:
                raise ValueError(f"文書 {args['doc']} は開かれていません")
            self.doc = args["doc"]
        self.emit({"type": "ui", "action": action, "args": args})
        return f"画面に頼みました: {action} {json.dumps(args, ensure_ascii=False)}", []

    def t_run_python(self, code: str):
        buf = io.StringIO()

        def call(method: str, **params):
            return self.call(method, params)
        env = {"api": self.api, "call": call, "doc": self.doc, "np": np, "math": math, "result": None}
        with contextlib.redirect_stdout(buf):
            exec(compile(code, "<agent>", "exec"), env)       # 全権限（利用者の指示: 個人用なので制限しない）
        out = buf.getvalue()
        if env.get("result") is not None:
            out += ("\n" if out else "") + "result = " + _dump(self.summary(env["result"]))
        return (out[:MAX_RESULT] or "(出力なし)"), []
