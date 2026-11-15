"""トポロジカルネーミング（永続名）と参照の解決。

名前の付け方（カーネルの履歴から決める）
  押し出し/回転の側面   "<feature>/side/<スケッチ要素ID>"
  端面                  "<feature>/start" | "<feature>/end"
  変形して残った面      入力側の名前を引き継ぐ
  フィレット面          "<feature>/fillet/<エッジ名>"
  エッジ                "E[<面名a>|<面名b>]"（ソート済み）
同じ名前が複数できた場合（面の分割など）は "#2", "#3"… を付ける。順序はカーネルの
sort_key（重心など形状から決まる値）で決め、列挙順に依存しないようにする。

参照の解決（find_face / find_edge）
  1. 完全一致
  2. "#n" を無視して候補が 1 つだけならそれを採用（repaired=True。分割が解消/発生した場合の救済）
  3. それ以外は DanglingReference（候補一覧つき。UI で付け直せるようにする）
フィーチャー・合致からの参照は必ずここを通す。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..kernel.base import Kernel, OpResult

_SUFFIX = re.compile(r"#\d+")


def edge_name(a: str, b: str) -> str:
    a, b = sorted((a, b))
    return f"E[{a}|{b}]"


def base_name(name: str) -> str:
    return _SUFFIX.sub("", name)


class DanglingReference(Exception):
    def __init__(self, kind: str, name: str, candidates: list[str]):
        self.kind, self.name, self.candidates = kind, name, candidates
        hint = f"（候補: {', '.join(candidates)}）" if candidates else ""
        super().__init__(f"{kind} {name} が見つかりません{hint}")


@dataclass
class Resolved:
    handle: Any
    name: str                 # 実際に解決された名前
    repaired: bool = False    # 完全一致ではなく救済で解決した


@dataclass
class TopoNames:
    faces: dict[str, Any] = field(default_factory=dict)   # 名前 → ハンドル
    edges: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def _find(kind: str, table: dict, name: str) -> Resolved:
        if name in table:
            return Resolved(table[name], name)
        b = base_name(name)
        cands = [n for n in table if base_name(n) == b]
        if len(cands) == 1:
            return Resolved(table[cands[0]], cands[0], repaired=True)
        raise DanglingReference(kind, name, cands)

    def find_face(self, name: str) -> Resolved:
        return self._find("面", self.faces, name)

    def find_edge(self, name: str) -> Resolved:
        return self._find("エッジ", self.edges, name)

    def face_name(self, handle) -> str | None:
        return next((n for n, h in self.faces.items() if h == handle), None)

    def edge_name_of(self, handle) -> str | None:
        return next((n for n, h in self.edges.items() if h == handle), None)


def _assign(table: dict, named: list[tuple[str, Any]], key):
    """同名をまとめ、sort_key の順に #n を振る。"""
    groups: dict[str, list] = {}
    for name, h in named:
        groups.setdefault(name, []).append(h)
    for name, hs in groups.items():
        if len(hs) > 1:
            keys = [key(h) for h in hs]
            if all(k is not None for k in keys):
                hs = [h for _, h in sorted(zip(keys, hs), key=lambda t: t[0])]
        for i, h in enumerate(hs):
            table[name if i == 0 else f"{name}#{i + 1}"] = h


def name_result(fid: str, kernel: Kernel, res: OpResult, inputs: list[TopoNames]) -> TopoNames:
    prev_face = {h: n for t in inputs for n, h in t.faces.items()}
    prev_edge = {h: n for t in inputs for n, h in t.edges.items()}
    key = lambda h: kernel.sort_key(res.shape, h)
    out = TopoNames()
    named = []
    for h in kernel.faces(res.shape):
        kind, src = res.history.get(h, ("new", None))
        if kind == "side":
            name = f"{fid}/side/{src}"
        elif kind in ("cap", "mesh"):              # mesh: 取り込んだメッシュの面（"F1" など。D71）
            name = f"{fid}/{src}"
        elif kind == "modified":
            name = prev_face.get(src, f"{fid}/?")
        elif kind in ("fillet", "chamfer"):
            name = f"{fid}/{kind}/{prev_edge.get(src, '?')}"
        elif kind == "shell":                       # シェルの内側（外側）の壁: 元の面の名前で呼ぶ
            name = f"{fid}/wall/{prev_face.get(src, '?')}"
        else:
            name = f"{fid}/new"
        named.append((name, h))
    _assign(out.faces, named, key)
    rev = {h: n for n, h in out.faces.items()}
    _assign(out.edges, [(edge_name(rev[a], rev[b]), e) for e, a, b in kernel.edges(res.shape)], key)
    return out
