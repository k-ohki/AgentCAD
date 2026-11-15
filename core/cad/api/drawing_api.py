"""図面の API（D60）。状態は doc.state（kind = "drawing"）で返す: シート・ビュー・寸法と、描く要素の一覧。"""
from __future__ import annotations

import os

from ..drawing import ORIENT_LABELS, SCALES, SHEETS, DrawingDocument, DrawingError
from ..drawing.dxf import ENCODING as DXF_ENCODING, dxf_text
from ..drawing.pdf import pdf_bytes
from ..drawing.render import flatten, fmt_value, render


class DrawingApi:
    """Api に混ぜる。self.ws / self.doc_state を使う。"""

    def _register_drawing(self):
        self.methods.update({
            "drw.new": self.drw_new, "drw.sheet": self.drw_sheet, "drw.view_add": self.drw_view_add,
            "drw.view_set": self.drw_view_set, "drw.dim_add": self.drw_dim_add, "drw.dim_set": self.drw_dim_set,
            "drw.delete": self.drw_delete, "drw.export": self.drw_export,
            "drw.note_add": self.drw_note_add, "drw.note_set": self.drw_note_set,
        })

    def _drw(self, doc: str) -> DrawingDocument:
        d = self.ws.get(doc)
        if not isinstance(d, DrawingDocument):
            raise ValueError("図面ではありません")
        return d

    def _drw_edit(self, doc: str, label: str, fn):
        d = self._drw(doc)
        try:
            with d.transaction(label):
                made = fn(d)
        except DrawingError as e:
            raise ValueError(str(e))
        st = self.doc_state(doc)
        if made:
            st["created"] = made if isinstance(made, list) else [made]
        return st

    def drw_new(self, model: str, size: str = "A3", landscape: bool = True, scale=None, standard: bool = True):
        """パートかアセンブリ model の図面を作る。scale を省くと、標準 3 面図が用紙に入るいちばん大きい縮尺。
        standard=True なら標準 3 面図（正面・平面・右側面。第三角法）を置く。"""
        did = self.ws.new_drawing(model)
        d = self._drw(did)
        try:
            d.set_sheet(size, landscape)
            d.set_sheet(scale=scale if scale is not None else d.auto_scale())
            if standard:
                d.standard_views()
        except (DrawingError, KeyError) as e:
            self.ws.close(did)
            raise ValueError(str(e.args[0]) if e.args else str(e))
        return self.doc_state(did)

    def drw_sheet(self, doc: str, size: str | None = None, landscape: bool | None = None, scale=None, title: str | None = None):
        """用紙・向き・縮尺（"1:2" か [1, 2]）・名称。"""
        def edit(d):
            d.set_sheet(size, landscape, scale)
            if title is not None:
                d.title = str(title).strip()
        return self._drw_edit(doc, "シートを変更", edit)

    def drw_view_add(self, doc: str, orient: str | None = None, x: float | None = None, y: float | None = None,
                     parent: str | None = None, side: str | None = None, standard: bool = False):
        """モデルビュー（orient: front / back / top / bottom / right / left / iso）か、parent の side
        （right / left / up / down）に置く投影図。standard=True なら標準 3 面図。"""
        if standard:
            return self._drw_edit(doc, "標準 3 面図", lambda d: d.standard_views())
        label = "投影図" if parent else ORIENT_LABELS.get(orient or "", "ビュー")
        return self._drw_edit(doc, f"{label}を追加", lambda d: d.add_view(orient, x, y, parent, side))

    def drw_view_set(self, doc: str, id: str, x: float | None = None, y: float | None = None, scale=..., hidden: bool | None = None):
        """ビューの位置（投影図は親とそろう方向へは動かない）・縮尺（None = シートの縮尺）・隠線の表示。"""
        def edit(d):
            if id not in d.views:
                raise DrawingError(f"ビュー {id} がありません")
            if x is not None and y is not None:
                d.move_view(id, x, y)
            d.set_view(id, scale, hidden)
        return self._drw_edit(doc, "ビューを移動" if x is not None else "ビューを変更", edit)

    def drw_dim_add(self, doc: str, view: str, a: dict, b: dict | None = None, pos: list | None = None, dir: str | None = None):
        """寸法。a・b = {"src": "edge:名前@vertex|mid|center", "hint": [x, y, z]}。b を省いて a.src = "edge:名前"（円・円弧）なら
        直径・半径。pos = 文字の位置（ビューの位置からの用紙の mm）。dir: h / v / aligned（省くと pos から決める）。"""
        return self._drw_edit(doc, "寸法を追加", lambda d: d.add_dim(view, a, b, pos or (0, 0), dir))

    def drw_dim_set(self, doc: str, id: str, pos: list):
        """寸法の文字の位置を動かす。"""
        def edit(d):
            if id not in d.dims:
                raise DrawingError(f"寸法 {id} がありません")
            d.dims[id]["pos"] = [float(pos[0]), float(pos[1])]
        return self._drw_edit(doc, "寸法を移動", edit)

    def drw_note_add(self, doc: str, view: str, text: str, pos: list | None = None, h: float = 3.5):
        """注記（自由な文字。室名など。D73）。pos = 文字の中心（ビューの位置からの用紙の mm）、h = 文字の高さ（mm）。
        動かすのは drw.dim_set、消すのは drw.delete（寸法と同じ）。"""
        return self._drw_edit(doc, "注記を追加", lambda d: d.add_note(view, text, pos or (0, 0), h))

    def drw_note_set(self, doc: str, id: str, text: str | None = None, h: float | None = None):
        """注記の文字・高さを変える。"""
        return self._drw_edit(doc, "注記を変更", lambda d: d.set_note(id, text, h))

    def drw_delete(self, doc: str, ids: list[str]):
        return self._drw_edit(doc, "削除", lambda d: d.delete(list(ids)))

    def drw_export(self, doc: str, format: str, path: str, scale: str = "sheet"):
        """PDF / DXF に書き出す。scale（DXF だけ）: "sheet" = 用紙どおり（mm）、"model" = 実寸（シートの縮尺で割る。図枠は出さない）。"""
        d = self._drw(doc)
        name = self.ws.names[doc]
        if format not in ("pdf", "dxf"):
            raise ValueError(f"未知の形式: {format}")
        path = os.path.abspath(path if path.lower().endswith("." + format) else f"{path}.{format}")
        r = render(d, name, curves=format == "dxf")
        if r["error"]:
            raise ValueError(r["error"])
        if format == "pdf":
            data = pdf_bytes(flatten(r), d.size, d.title or name)
        else:
            k = 1.0 / d.scale_of() if scale == "model" else 1.0
            data = dxf_text(flatten(r, k, sheet=scale != "model"), ltscale=k).encode(DXF_ENCODING)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        return {"path": path}

    def drw_state(self, doc: str) -> dict:
        d = self._drw(doc)
        name = self.ws.names[doc]
        r = render(d, name)
        model_open = d.model in self.ws.docs and self.ws.kind(d.model) in ("part", "assembly")
        fmt = lambda s: None if s is None else f"{fmt_value(s[0])}:{fmt_value(s[1])}"
        return {"doc": doc, "kind": "drawing", "name": name, "path": self.ws.paths[doc], "kernel": self.ws.kernel_name,
                "modified": self.ws.modified(doc),
                "model": {"doc": d.model, "name": self.ws.names.get(d.model) if model_open else None,
                          "kind": self.ws.kind(d.model) if model_open else None},
                "sheet": {**d.sheet, "scale_text": fmt(d.sheet["scale"]), "title": d.title, "width": r["size"][0], "height": r["size"][1],
                          "items": r["sheet"]},
                "sizes": list(SHEETS), "scales": [fmt(s) for s in SCALES],
                "orients": [[k, v] for k, v in ORIENT_LABELS.items()],
                "views": [{**v, "scale_text": fmt(v["scale"])} for v in r["views"]], "dims": r["dims"], "error": r["error"],
                "undo": d.undo_stack[-1][0] if d.undo_stack else None,
                "redo": d.redo_stack[-1][0] if d.redo_stack else None}
