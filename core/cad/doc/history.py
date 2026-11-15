"""Undo/Redo（スナップショット方式）。パートとアセンブリで共用する。

利用側は to_dict() / _load(dict) / _changed() を実装する。
    with doc.transaction("押し出し距離変更"):
        ...編集...
ブロックを抜けると 1 操作として記録し、_changed()（再生成・再求解）を呼ぶ。
例外が出た場合は編集前の状態に戻す。
"""
from __future__ import annotations

from contextlib import contextmanager

MAX_UNDO = 200


class History:
    def __init__(self):
        self.undo_stack: list[tuple[str, dict]] = []
        self.redo_stack: list[tuple[str, dict]] = []

    def to_dict(self) -> dict:
        raise NotImplementedError

    def _load(self, d: dict) -> None:
        raise NotImplementedError

    def _changed(self) -> None:
        """定義が変わった後の再計算。"""

    @contextmanager
    def transaction(self, label: str):
        before = self.to_dict()
        try:
            yield
        except Exception:
            self._load(before)
            self._changed()
            raise
        self.undo_stack.append((label, before))
        del self.undo_stack[:-MAX_UNDO]
        self.redo_stack.clear()
        self._changed()

    def commit(self, label: str, before: dict) -> None:
        """transaction を使わずに変更した分を 1 操作として記録する（スケッチのドラッグなど、
        途中の状態を何度も描きたい操作用）。before は変更前の to_dict()。"""
        self.undo_stack.append((label, before))
        del self.undo_stack[:-MAX_UNDO]
        self.redo_stack.clear()
        self._changed()

    def _swap(self, src: list, dst: list) -> str | None:
        if not src:
            return None
        label, snap = src.pop()
        dst.append((label, self.to_dict()))
        self._load(snap)
        self._changed()
        return label

    def undo(self) -> str | None:
        return self._swap(self.undo_stack, self.redo_stack)

    def redo(self) -> str | None:
        return self._swap(self.redo_stack, self.undo_stack)
