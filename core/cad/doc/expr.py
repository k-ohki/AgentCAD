"""式（グローバル変数と寸法の連動）。

    doc.variables.exprs["width"] = "100"
    doc.variables.exprs["height"] = "width / 2"
    extrude.exprs["distance"] = "height * 2"            # フィーチャーのパラメータ
    sketch.constraints["K5"].expr = "width - 2 * Extrude1.distance"   # 寸法。他の寸法も参照できる

使えるもの: 数値、+ - * / ** %、括弧、変数名、`フィーチャーID.パラメータ`（スケッチ寸法は
`Sketch1.K5`）、関数 sin cos tan asin acos atan atan2 sqrt abs min max floor ceil round、
定数 pi、rad()（度→ラジアン）、deg()。三角関数はラジアン。
式は ast で解釈し、上記以外の構文は受け付けない（eval は使わない）。
"""
from __future__ import annotations

import ast
import math
import operator
from typing import Callable

FUNCS = {"sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos,
         "atan": math.atan, "atan2": math.atan2, "sqrt": math.sqrt, "abs": abs, "min": min, "max": max,
         "floor": math.floor, "ceil": math.ceil, "round": round, "rad": math.radians, "deg": math.degrees}
CONSTS = {"pi": math.pi}
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod}
_UN = {ast.UAdd: operator.pos, ast.USub: operator.neg}


class ExprError(Exception):
    pass


def _parse(expr: str) -> ast.AST:
    try:
        return ast.parse(str(expr).strip(), mode="eval").body
    except SyntaxError:
        raise ExprError(f"式が読めません: {expr}")


def _ref(node) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return None


def references(expr: str) -> set[str]:
    """式が参照している名前（変数名、`Feature.param`）。"""
    out = set()

    def walk(n):
        if isinstance(n, ast.Call):
            for a in n.args:
                walk(a)
            return
        r = _ref(n)
        if r is not None:
            if r not in CONSTS:
                out.add(r)
            return
        for c in ast.iter_child_nodes(n):
            walk(c)
    walk(_parse(expr))
    return out


def evaluate(expr: str, lookup: Callable[[str], float] = None) -> float:
    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return float(n.value)
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            return _BIN[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _UN:
            return _UN[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call):
            if not (isinstance(n.func, ast.Name) and n.func.id in FUNCS) or n.keywords:
                raise ExprError(f"使えない関数です: {ast.unparse(n.func)}")
            return float(FUNCS[n.func.id](*[ev(a) for a in n.args]))
        r = _ref(n)
        if r is not None:
            if r in CONSTS:
                return CONSTS[r]
            if lookup is None:
                raise ExprError(f"{r} は未定義です")
            return float(lookup(r))
        raise ExprError(f"使えない書き方です: {ast.unparse(n)}")
    try:
        v = ev(_parse(expr))
    except ExprError:
        raise
    except (ZeroDivisionError, ValueError, OverflowError, TypeError) as e:
        raise ExprError(f"計算できません: {expr}（{e}）")
    if isinstance(v, complex) or not math.isfinite(v):
        raise ExprError(f"計算できません: {expr}")
    return v


class Variables:
    """グローバル変数（名前 → 式）。変数同士は参照でき、循環は検出してエラーにする。"""

    def __init__(self, exprs: dict[str, str] | None = None):
        self.exprs: dict[str, str] = dict(exprs or {})

    def evaluate(self) -> tuple[dict[str, float], dict[str, str]]:
        """(値, エラー) を返す。エラーのある変数は値に含まれない。"""
        values: dict[str, float] = {}
        errors: dict[str, str] = {}
        stack: list[str] = []

        def get(name: str) -> float:
            if name in values:
                return values[name]
            if name in errors:
                raise ExprError(f"{name} がエラーです（{errors[name]}）")
            if name not in self.exprs:
                raise ExprError(f"{name} は未定義です")
            if name in stack:
                raise ExprError("循環参照: " + " → ".join(stack[stack.index(name):] + [name]))
            if not name.isidentifier() or name in FUNCS or name in CONSTS:
                raise ExprError(f"変数名に使えません: {name}")
            stack.append(name)
            try:
                values[name] = evaluate(self.exprs[name], get)
            finally:
                stack.pop()
            return values[name]
        for name in self.exprs:
            try:
                get(name)
            except ExprError as e:
                errors[name] = str(e)
        return values, errors
