"""USD（.usda テキスト）の読み書き。pxr（OpenUSD の Python）には依存しない（D32）。

パート（Part.usda）:
    /Part                 Xform（defaultPrim, kind=component）
    /Part/FeatureTree     agentcad:definition = PartDocument.to_dict() の JSON（これが正。開くときはここだけ読む）
    /Part/Display         Mesh（表示メッシュ。面の番号を primvars:agentcad:faceIndex、面の永続名を agentcad:faceNames）
    /Part/Edges           BasisCurves（エッジの線分。agentcad:edgeNames）
    /Part/Body            agentcad:body = BRep のファイル（カーネルが書けたときだけ。D33）
アセンブリ（Asm.usda）:
    /Assembly             agentcad:definition = Assembly.to_dict() の JSON。部品の source はパートファイルの相対パス
    /Assembly/<部品>      Xform。パートを reference し、姿勢を xformOp:transform に持つ

USD の prim（Display・部品の Xform）は他の USD ツールで見るための「写し」。開くときは JSON だけを読む。
単位は mm（metersPerUnit = 0.001）、上は +Y（一般的な 3D CAD と同じ）。
"""
from __future__ import annotations

import json
import os
import re

import numpy as np

FORMAT_KEY = "agentcad:definition"


# ---------------------------------------------------------------- 書く


def _q(s: str) -> str:
    """USD の文字列リテラル（ダブルクォート）。"""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r") + '"'


def _num(v: float) -> str:
    return repr(float(round(v, 9))).removesuffix(".0") if np.isfinite(v) else "0"


def _vecs(a) -> str:
    a = np.asarray(a, float).reshape(-1, 3)
    return "[" + ", ".join(f"({_num(x)}, {_num(y)}, {_num(z)})" for x, y, z in a) + "]"


def _ints(a) -> str:
    return "[" + ", ".join(str(int(v)) for v in np.asarray(a).ravel()) + "]"


def _strs(xs) -> str:
    return "[" + ", ".join(_q(str(x)) for x in xs) + "]"


def _matrix(m) -> str:
    """列ベクトル規約の 4×4（平行移動が 4 列目）→ USD の matrix4d（行ベクトル規約。転置して書く）。"""
    t = np.asarray(m, float).reshape(4, 4).T
    return "( " + ", ".join("(" + ", ".join(_num(v) for v in row) + ")" for row in t) + " )"


def prim_name(s: str, used: set[str]) -> str:
    """USD の prim 名に使える名前（英数字と _、先頭は数字以外）。重複したら _2, _3…"""
    base = re.sub(r"[^A-Za-z0-9_]", "_", s).strip("_") or "C"
    if base[0].isdigit():
        base = "_" + base
    name, n = base, 2
    while name in used:
        name, n = f"{base}_{n}", n + 1
    used.add(name)
    return name


def _header(fmt: str, default_prim: str) -> list[str]:
    return ["#usda 1.0", "(",
            "    customLayerData = {", f"        string \"agentcad:format\" = {_q(fmt)}", "    }",
            f"    defaultPrim = {_q(default_prim)}", "    metersPerUnit = 0.001", '    upAxis = "Y"', ")", ""]


def _signed_volume(pos: np.ndarray, tri: np.ndarray) -> float:
    if not len(tri):
        return 0.0
    a, b, c = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6)


def part_usda(definition: dict, mesh: dict | None, body_ref: tuple[str, str | None] | None = None) -> str:
    """body_ref: (BRep ファイルの asset パス, その中の BrepArray の prim パス)。prim パスが分かれば reference する。
    mesh: {positions (n,3), indices (m,3), face_of_triangle (m,), face_names, edges (k,2,3),
    edge_of_segment (k,), edge_names}。None なら表示メッシュを書かない（形状がまだ無い）。"""
    L = _header(definition.get("format", "agentcad.part/1"), "Part")
    L += ['def Xform "Part" (', '    kind = "component"', ")", "{",
          '    def Scope "FeatureTree"', "    {",
          f"        custom string {FORMAT_KEY} = {_q(json.dumps(definition, ensure_ascii=False, separators=(',', ':')))}",
          "    }"]
    if mesh is not None and len(mesh["indices"]):
        pos = np.asarray(mesh["positions"], float).reshape(-1, 3)
        tri = np.asarray(mesh["indices"], np.int64).reshape(-1, 3)
        lo, hi = pos.min(0), pos.max(0)
        # 三角形の向き: 外向きが反時計回りなら rightHanded（体積の符号で判定。カーネルごとに違ってもよい）
        orient = "rightHanded" if _signed_volume(pos, tri) >= 0 else "leftHanded"
        L += ["", '    def Mesh "Display"', "    {",
              f"        float3[] extent = {_vecs([lo, hi])}",
              f"        int[] faceVertexCounts = {_ints(np.full(len(tri), 3))}",
              f"        int[] faceVertexIndices = {_ints(tri)}",
              f"        uniform token orientation = {_q(orient)}",
              f"        point3f[] points = {_vecs(pos)}",
              f"        int[] primvars:agentcad:faceIndex = {_ints(mesh['face_of_triangle'])} (",
              '            interpolation = "uniform"', "        )",
              '        uniform token subdivisionScheme = "none"',
              f"        custom string[] agentcad:faceNames = {_strs(mesh['face_names'])}"]
        if mesh.get("face_colors"):             # 外観（D69）: 三角形ごとの色（ほかの USD ツールでも色が付く）
            fc = np.array([[int(c[i:i + 2], 16) / 255 for i in (1, 3, 5)] for c in mesh["face_colors"]])
            L += [f"        color3f[] primvars:displayColor = {_vecs(fc[np.asarray(mesh['face_of_triangle'], np.int64)])} (",
                  '            interpolation = "uniform"', "        )"]
        L += ["    }"]
        seg = np.asarray(mesh.get("edges", np.zeros((0, 2, 3))), float).reshape(-1, 2, 3)
        if len(seg):
            L += ["", '    def BasisCurves "Edges"', "    {",
                  f"        int[] curveVertexCounts = {_ints(np.full(len(seg), 2))}",
                  f"        point3f[] points = {_vecs(seg.reshape(-1, 3))}",
                  f"        int[] primvars:agentcad:edgeIndex = {_ints(mesh['edge_of_segment'])} (",
                  '            interpolation = "uniform"', "        )",
                  '        uniform token type = "linear"',
                  f"        custom string[] agentcad:edgeNames = {_strs(mesh['edge_names'])}",
                  "    }"]
    if body_ref:
        asset, prim = body_ref
        if prim:                                 # 型は参照先（BrepArray）から来るので書かない
            L += ["", '    def "Body" (', f"        prepend references = @{asset}@<{prim}>", "    )", "    {",
                  f"        custom asset agentcad:body = @{asset}@", "    }"]
        else:
            L += ["", '    def Scope "Body"', "    {", f"        custom asset agentcad:body = @{asset}@", "    }"]
    L += ["}", ""]
    return "\n".join(L)


def assembly_usda(definition: dict, components: list[tuple[str, str, object]]) -> str:
    """components: [(部品 ID, パートファイルの相対パス, 4×4 行列（列ベクトル規約）)]。
    definition の components[*].source はパートファイルの相対パスにしておくこと。"""
    L = _header(definition.get("format", "agentcad.assembly/1"), "Assembly")
    L += ['def Xform "Assembly" (', '    kind = "assembly"', ")", "{",
          f"    custom string {FORMAT_KEY} = {_q(json.dumps(definition, ensure_ascii=False, separators=(',', ':')))}"]
    used: set[str] = set()
    for cid, ref, m in components:
        L += ["", f'    def Xform "{prim_name(cid, used)}" (', f"        prepend references = @{ref}@", "    )", "    {",
              f"        custom string agentcad:component = {_q(cid)}",
              f"        matrix4d xformOp:transform = {_matrix(m)}",
              '        uniform token[] xformOpOrder = ["xformOp:transform"]', "    }"]
    L += ["}", ""]
    return "\n".join(L)


def drawing_usda(definition: dict, model_ref: str) -> str:
    """図面（D60）。定義（シート・ビュー・寸法）と、モデルのパートファイルへの参照だけを持つ（形は開くたびに作る）。
    definition の model はパートファイルの相対パスにしておくこと。"""
    L = _header(definition.get("format", "agentcad.drawing/1"), "Drawing")
    L += ['def Scope "Drawing"', "{",
          f"    custom string {FORMAT_KEY} = {_q(json.dumps(definition, ensure_ascii=False, separators=(',', ':')))}",
          f"    custom asset agentcad:model = @{model_ref}@", "}", ""]
    return "\n".join(L)


def write_text(path: str, text: str) -> None:
    """一時ファイルに書いてから置き換える（書きかけのファイルを残さない）。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 読む


def _parse_string(s: str, i: int) -> tuple[str, int]:
    """s[i] から始まる USD の文字列リテラル（' " ''' \"\"\"）を読み、(値, 次の位置) を返す。"""
    q = s[i]
    if q not in "\"'":
        raise ValueError("文字列がありません")
    triple = s.startswith(q * 3, i)
    i += 3 if triple else 1
    out = []
    esc = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'", "0": "\0"}
    while i < len(s):
        c = s[i]
        if c == "\\":
            n = s[i + 1]
            if n == "x":
                out.append(chr(int(s[i + 2:i + 4], 16))); i += 4; continue
            out.append(esc.get(n, n)); i += 2; continue
        if triple and s.startswith(q * 3, i):
            return "".join(out), i + 3
        if not triple and c == q:
            return "".join(out), i + 1
        out.append(c); i += 1
    raise ValueError("文字列が閉じていません")


def prim_specs(text: str) -> list[tuple[str, str]]:
    """.usda の prim 定義 [(パス, 型名)]（def / over / class）。variantSet の中は数えない（AgentCAD では使わない）。"""
    out: list[tuple[str, str]] = []
    stack: list[str | None] = []                 # { ごとに prim 名（prim でない { は None）
    pending: tuple[str, str] | None = None
    paren = i = 0
    ident = re.compile(r"[A-Za-z_][A-Za-z0-9_:.]*")
    while i < len(text):
        c = text[i]
        if c in "\"'":
            value, i = _parse_string(text, i)
            if pending is not None and pending[1] is None and paren == 0:
                pending = (pending[0], value)    # (型, 名前)
            continue
        if c == "@":
            i = text.index("@@@", i + 3) + 3 if text.startswith("@@@", i) else text.index("@", i + 1) + 1
            continue
        if c == "#":
            j = text.find("\n", i)
            i = len(text) if j < 0 else j
            continue
        if c == "(":
            paren += 1
        elif c == ")":
            paren -= 1
        elif c == "{" and paren == 0:
            if pending is not None and pending[1] is not None:
                names = [n for n in stack if n is not None]
                path = "/" + "/".join([*names, pending[1]])
                out.append((path, pending[0]))
                stack.append(pending[1])
            else:
                stack.append(None)
            pending = None
        elif c == "}" and paren == 0:
            stack.pop()
        elif paren == 0 and (m := ident.match(text, i)) and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] in "_:.")):
            word = m.group()
            if word in ("def", "over", "class"):
                rest = text[m.end():].lstrip()
                t = ident.match(rest)
                pending = (t.group() if t else "", None)
            i = m.end()
            continue
        i += 1
    return out


def brep_prims(path: str) -> list[str]:
    """BRep のファイル（usd-brep が書いた .usda）の BrepArray の prim パス。テキストでなければ []。"""
    with open(path, "rb") as f:
        if not f.read(5) == b"#usda":
            return []
    with open(path, encoding="utf-8") as f:
        return [p for p, t in prim_specs(f.read()) if "Brep" in t]


def read_definition(path: str) -> dict:
    """AgentCAD の .usda から定義（to_dict() の JSON）を取り出す。"""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("#usda"):
        raise ValueError(f"{os.path.basename(path)} はテキスト形式の USD（.usda）ではありません")
    m = re.search(r"string\s+" + re.escape(FORMAT_KEY) + r"\s*=\s*", text)
    if not m:
        raise ValueError(f"{os.path.basename(path)} に AgentCAD の定義がありません（AgentCAD で保存した USD ではありません）")
    value, _ = _parse_string(text, m.end())
    return json.loads(value)
