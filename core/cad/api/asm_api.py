"""アセンブリの API（rpc.Api に登録する）。

部品は同じ Workspace で開いているパート文書を参照する（source = 文書 ID）。
合致の参照は [部品 ID, 面の永続名]。状態を返すたびに解き直すので、パートを編集すると部品が追従する。
"""
from __future__ import annotations

import numpy as np

from ..assembly import MATE_TYPES
from ..assembly.assembly import GAxis, _dir, _org, quat_from_axis_angle, quat_mul, quat_rotate
from ..assembly.interference import interference
from ..naming import DanglingReference
from ..part import PartDocument

MATE_LABELS = {"coincident": "一致", "parallel": "平行", "perpendicular": "直角", "distance": "距離",
               "angle": "角度", "concentric": "同心", "tangent": "正接", "lock": "ロック"}


class AsmApi:
    def _register_asm(self):
        self._asm_drag: dict[str, dict] = {}
        self.methods.update({
            "asm.new": self.asm_new, "asm.add": self.asm_add, "asm.mate": self.asm_mate,
            "asm.edit": self.asm_edit, "asm.fix": self.asm_fix, "asm.delete": self.asm_delete,
            "asm.drag": self.asm_drag, "asm.joints": self.asm_joints, "asm.mate_options": self.asm_mate_options,
            "asm.mate_set": self.asm_mate_set, "asm.mate_cancel": self.asm_mate_cancel,
            "asm.interference": self.asm_interference, "asm.copy": self.asm_copy, "asm.pattern": self.asm_pattern,
        })
        #: 合致の PropertyManager で作ったばかりの（まだ ✓ を押していない）合致: 文書 → (合致ID, その時の Undo の数)
        self._mate_pending: dict[str, tuple[str, int]] = {}

    def _asm(self, doc: str):
        if not self.ws.is_assembly(doc):
            raise ValueError("アセンブリではありません")
        return self.ws.get(doc)

    def asm_new(self, name: str = "Assembly"):
        return self.doc_state(self.ws.new_assembly(name))

    def asm_add(self, doc: str, part: str, position: list | None = None):
        a = self._asm(doc)
        p = self.ws.get(part)
        if not isinstance(p, PartDocument):
            raise ValueError("部品にできるのはパートだけです")
        if p.body is None:
            raise ValueError("このパートにはまだ形状がありません")
        base = self.ws.names[part]
        n = 1
        while f"{base}<{n}>" in a.components:
            n += 1
        if position is None:                       # 既にある部品の右隣に置く
            position = [0.0, 0.0, 0.0]
            if a.components:
                m = p.kernel.tessellate(p.body)
                size = float(np.ptp(np.asarray(m.positions, float), axis=0).max()) if len(m.positions) else 10.0
                position[0] = max(c.position[0] for c in a.components.values()) + 1.5 * size
        with a.transaction("部品を追加"):
            a.add_component(part, position, id=f"{base}<{n}>")
        return self.doc_state(doc)

    def asm_mate(self, doc: str, type: str, a: list, b: list, value: float | None = None,
                 aligned: bool | None = None, pending: bool = False):
        """合致を付ける。pending=True は合致の PropertyManager から（一般的な 3D CAD と同じく、付けて部品を動かして見せ、
        ポップアップで種類・値・向きを変えて ✓ / ✗）。種類の変更は asm.mate_set、✗ は asm.mate_cancel。"""
        asm = self._asm(doc)
        if type not in MATE_TYPES:
            raise ValueError(f"未知の合致: {type}")
        if value is None and type == "distance":
            value = asm.measure(tuple(a), tuple(b))["distance"]
        with asm.transaction(f"合致（{MATE_LABELS[type]}）"):
            mid = asm.mate(type, tuple(a), tuple(b), value=value, aligned=aligned)
        if pending:
            self._mate_pending[doc] = (mid, len(asm.undo_stack))
        else:
            self._mate_pending.pop(doc, None)
        st = self.doc_state(doc)
        st["created"] = [mid]
        return st

    def asm_mate_options(self, doc: str, a: list, b: list):
        """選んだ 2 つに付けられる合致（ポップアップのボタン）と既定の種類、今の距離・角度。"""
        asm = self._asm(doc)
        types, default = asm.mate_options(tuple(a), tuple(b))
        return {"types": [{"type": t, "label": MATE_LABELS[t]} for t in types], "default": default,
                **asm.measure(tuple(a), tuple(b))}

    def asm_mate_set(self, doc: str, id: str, type: str, a: list, b: list, value: float | None = None,
                     aligned: bool | None = None, pending: bool = False):
        """合致の種類・値・向きを変える（ID はそのまま）。pending=True（作ったばかりで ✓ を押す前）なら、
        Undo は作成と合わせて 1 つ。付いている合致の編集（pending=False）は変更ごとに 1 つ。"""
        asm = self._asm(doc)
        if type not in MATE_TYPES:
            raise ValueError(f"未知の合致: {type}")
        if id not in asm.mates:
            raise ValueError(f"合致 {id} がありません")
        pend = self._mate_pending.get(doc)
        amend = pending and pend is not None and pend[0] == id and pend[1] == len(asm.undo_stack)
        with asm.transaction(f"合致（{MATE_LABELS[type]}）"):
            order = list(asm.mates)
            old = asm.mates.pop(id)
            if value is None and type == "distance":
                value = old.value if old.type == "distance" else asm.measure(tuple(a), tuple(b))["distance"]
            if value is None and type == "angle" and old.type == "angle":
                value = old.value
            nid = asm.mate(type, tuple(a), tuple(b), value=value, aligned=aligned)
            m = asm.mates.pop(nid)
            asm._seq -= 1
            m.suppressed = old.suppressed
            asm.mates[id] = m
            asm.mates = {k: asm.mates[k] for k in order}
        if amend:                                   # 作成の Undo にまとめる
            label, _ = asm.undo_stack.pop()
            _, before = asm.undo_stack.pop()
            asm.undo_stack.append((label, before))
        return self.doc_state(doc)

    def asm_mate_cancel(self, doc: str, id: str):
        """作ったばかりの合致を取り消す（✗）。部品も合致を付ける前の位置に戻る。"""
        asm = self._asm(doc)
        pend = self._mate_pending.pop(doc, None)
        if pend is not None and pend[0] == id and pend[1] == len(asm.undo_stack):
            asm.undo()
            asm.redo_stack.pop()
        elif id in asm.mates:
            with asm.transaction("合致を削除"):
                del asm.mates[id]
        return self.doc_state(doc)

    def asm_edit(self, doc: str, id: str, value: float | None = None, flip: bool = False,
                 suppressed: bool | None = None):
        asm = self._asm(doc)
        m = asm.mates[id]
        with asm.transaction("合致を変更"):
            if value is not None:
                m.value, m.expr = float(value), None
            if flip:
                m.aligned = not m.aligned
            if suppressed is not None:
                m.suppressed = bool(suppressed)
        return self.doc_state(doc)

    def asm_fix(self, doc: str, comp: str, value: bool = True):
        asm = self._asm(doc)
        with asm.transaction("固定の切り替え"):
            asm.components[comp].fixed = bool(value)
        return self.doc_state(doc)

    def asm_delete(self, doc: str, ids: list[str]):
        asm = self._asm(doc)
        with asm.transaction("削除"):
            for i in ids:
                if i in asm.mates:
                    del asm.mates[i]
                elif i in asm.components:
                    del asm.components[i]
                    asm.mates = {k: m for k, m in asm.mates.items() if i not in (m.refs[0][0], m.refs[1][0])}
        return self.doc_state(doc)

    def asm_drag(self, doc: str, comp: str, position: list, final: bool = False):
        """部品のドラッグ。途中は姿勢だけ返し、最後に 1 操作として記録する。"""
        asm = self._asm(doc)
        if comp not in asm.components:
            raise ValueError(f"部品 {comp} がありません")
        self._asm_drag.setdefault(doc, asm.to_dict())
        if not asm.components[comp].fixed:
            asm.drag(comp, tuple(float(v) for v in position), diagnose=False)
        if not final:
            return {"components": [{"id": k, "matrix": c.matrix().ravel().tolist()} for k, c in asm.components.items()]}
        asm.commit("部品をドラッグ", self._asm_drag.pop(doc))
        return self.doc_state(doc)

    def asm_joints(self, doc: str):
        """「動かしてみる」モード（D70）の回転軸: 固定されていない部品ごとに、同心円の合致の相手の軸（世界座標）。
        画面はこの軸のまわりに部品を回すだけで、合致は解かない（ほかの合致・当たりは無視。モードを抜けると元に戻す）。"""
        asm = self._asm(doc)
        out = []
        for cid, c in asm.components.items():
            if c.fixed:
                continue
            m = next((m for m in asm.mates.values() if m.type == "concentric" and not m.suppressed
                      and cid in (m.refs[0][0], m.refs[1][0])), None)
            if m is None:
                continue
            ref = m.refs[1] if m.refs[0][0] == cid else m.refs[0]       # 相手の部品の軸
            g, o = asm._local(ref), asm.components[ref[0]]
            out.append({"comp": cid,
                        "origin": (np.asarray(o.position) + quat_rotate(o.rotation, g.origin)).tolist(),
                        "dir": quat_rotate(o.rotation, g.direction).tolist()})
        return out

    # ------------------------------------------------------------ 干渉チェック・複製・パターン（D56）
    def asm_interference(self, doc: str):
        """部品どうしの干渉（食い込み）。表示用メッシュで調べる近似（cad/assembly/interference.py）。"""
        from ..doc.workspace import display_mesh
        asm = self._asm(doc)
        asm.solve()
        parts, cache = [], {}
        for cid, c in asm.components.items():
            p = self.ws.docs.get(c.source)
            if p is None or getattr(p, "body", None) is None:
                continue
            if c.source not in cache:
                m = display_mesh(p)
                cache[c.source] = (np.asarray(m["positions"], float).reshape(-1, 3), np.asarray(m["indices"], int).reshape(-1, 3))
            V, F = cache[c.source]
            M = c.matrix()
            parts.append((cid, V @ M[:3, :3].T + M[:3, 3], F))
        return {"interferences": interference(parts), "checked": len(parts)}

    def _new_comp_id(self, asm, source: str) -> str:
        base = self.ws.names[source]
        n = 1
        while f"{base}<{n}>" in asm.components:
            n += 1
        return f"{base}<{n}>"

    def asm_copy(self, doc: str, comp: str, offset: list | None = None):
        """部品を複製する（同じパートをもう 1 つ、少しずらして置く。合致は付けない）。"""
        asm = self._asm(doc)
        c = asm.components[comp]
        if offset is None:
            p = self.ws.get(c.source)
            pos = np.asarray(p.kernel.tessellate(p.body).positions, float) if p.body is not None else np.zeros((1, 3))
            offset = [float(np.ptp(pos, axis=0).max()) * 1.2 if len(pos) else 10.0, 0.0, 0.0]
        with asm.transaction("部品を複製"):
            nid = asm.add_component(c.source, np.add(c.position, offset), c.rotation, fixed=False,
                                    id=self._new_comp_id(asm, c.source))
        st = self.doc_state(doc)
        st["created"] = [nid]
        return st

    def asm_pattern(self, doc: str, comp: str, kind: str, ref: list | None = None, axis: str | None = None,
                    count: int = 3, spacing: float = 20.0, angle: float = 360.0, flip: bool = False):
        """部品のパターン（一般的な 3D CAD の直線 / 円形パターン（部品））。種の部品と同じパートを count - 1 個置き、
        種に「ロック」の合致でつなぐ（種を動かすとついてくる）。
        kind = "linear": 方向は ref（平面の面なら法線、円筒・直線なら軸）か axis（"x" / "y" / "z"）、間隔 spacing。
        kind = "circular": 軸は ref（円筒の面）か axis（原点を通る座標軸）、angle を count 等分（360 なら 1 周を等分）。"""
        asm = self._asm(doc)
        if comp not in asm.components:
            raise ValueError(f"部品 {comp} がありません")
        if count < 2:
            raise ValueError("個数は 2 以上にしてください")
        c = asm.components[comp]
        if ref:
            g = asm.world_geometry(tuple(ref))
            d = _dir(g)
            if d is None:
                raise ValueError("方向・軸にできない面です")
            if kind == "circular" and not isinstance(g, GAxis):
                raise ValueError("円形パターンの軸には円筒の面を選んでください")
            o = _org(g)
        elif axis in ("x", "y", "z"):
            d, o = np.eye(3)["xyz".index(axis)], np.zeros(3)
        else:
            raise ValueError("方向（面）か軸（x / y / z）を指定してください")
        d = np.asarray(d, float) / np.linalg.norm(d) * (-1 if flip else 1)
        made = []
        with asm.transaction("直線パターン（部品）" if kind == "linear" else "円形パターン（部品）"):
            for i in range(1, count):
                if kind == "linear":
                    pos, rot = np.add(c.position, d * spacing * i), c.rotation
                else:
                    step = angle / count if abs(angle - 360.0) < 1e-9 else angle / (count - 1)
                    q = quat_from_axis_angle(d, step * i)
                    pos = np.asarray(o) + quat_rotate(q, np.subtract(c.position, o))
                    rot = quat_mul(q, c.rotation)
                nid = asm.add_component(c.source, pos, rot, fixed=False, id=self._new_comp_id(asm, c.source))
                asm.mate("lock", (comp, ""), (nid, ""))
                made.append(nid)
        st = self.doc_state(doc)
        st["created"] = made
        return st

    def asm_state(self, doc: str, mesh: bool = True) -> dict:
        asm = self._asm(doc)
        error = None
        try:
            r = asm.solve()
        except (KeyError, ValueError, DanglingReference) as e:
            r, error = None, (str(e.args[0]) if isinstance(e, KeyError) and e.args else str(e))
        comps = []
        for k, c in asm.components.items():
            comps.append({"id": k, "source": c.source, "part_name": self.ws.names.get(c.source, "?"),
                          "fixed": c.fixed, "position": list(c.position), "matrix": c.matrix().ravel().tolist(),
                          "dof": r.component_dof.get(k) if r else None})
        mates = []
        for k, m in asm.mates.items():
            state = "ok"
            if r:
                state = ("conflict" if k in r.conflicting else "redundant" if k in r.redundant
                         else "flipped" if k in r.flipped else "ok")
            if m.suppressed:
                state = "suppressed"
            mates.append({"id": k, "type": m.type, "label": MATE_LABELS[m.type], "refs": m.refs, "value": m.value,
                          "aligned": m.aligned, "suppressed": m.suppressed, "state": state})
        out = {"doc": doc, "kind": "assembly", "name": self.ws.names[doc], "path": self.ws.paths[doc],
               "kernel": self.ws.kernel_name, "modified": self.ws.modified(doc), "components": comps, "mates": mates, "error": error,
               "solve": {"status": r.status, "dof": r.dof, "state": r.state} if r else None,
               "undo": asm.undo_stack[-1][0] if asm.undo_stack else None,
               "redo": asm.redo_stack[-1][0] if asm.redo_stack else None,
               "part_docs": [{"doc": d, "name": self.ws.names[d]} for d in self.ws.docs if self.ws.is_part(d)]}
        if mesh:
            parts = {}
            for src in {c.source for c in asm.components.values()}:
                p = self.ws.docs.get(src)
                parts[src] = self._mesh(p) if p is not None and p.body is not None else None
            out["parts"] = parts
        return out
