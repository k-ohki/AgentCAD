"""DXF の書き出し（D60）。R12 の DXF（どの CAD でも読める形）。外部ライブラリなし。

レイヤー: FRAME・TITLE・VISIBLE・HIDDEN（線種 HIDDEN）・DIM。寸法は線・矢印（SOLID）・文字に分けて出す。
全角の文字は Shift-JIS のまま書き（$DWGCODEPAGE = ANSI_932、ファイルは ENCODING = cp932。日本語の CAD がそのまま読める。
\\U+XXXX は R12 を読むソフトの多くが解さず文字化けした）、Shift-JIS に無い文字だけ \\U+XXXX。Ø は %%c。
文字のスタイルは MS ゴシック（全角が出るように）。
"""
from __future__ import annotations

#: レイヤー → (DXF のレイヤー名, 色番号, 線種)
LAYERS = {"frame": ("FRAME", 7, "CONTINUOUS"), "title": ("TITLE", 7, "CONTINUOUS"), "visible": ("VISIBLE", 7, "CONTINUOUS"),
          "hidden": ("HIDDEN", 2, "HIDDEN"), "dim": ("DIM", 3, "CONTINUOUS")}


def _n(v: float) -> str:
    return f"{v:.6f}".rstrip("0").rstrip(".") or "0"


ENCODING = "cp932"


def _sjis(c: str) -> bool:
    try:
        c.encode(ENCODING)
        return True
    except UnicodeEncodeError:
        return False


def _str(s: str) -> str:
    return "".join("%%c" if c == "Ø" else c if ord(c) < 128 or _sjis(c) else f"\\U+{ord(c):04X}" for c in s)


def _chains(segs) -> list[list[tuple[float, float]]]:
    """前の線分の終点から始まる線分をつないだ点列。"""
    out: list[list[tuple[float, float]]] = []
    for a, b, c, d in segs:
        if out and abs(out[-1][-1][0] - a) < 1e-6 and abs(out[-1][-1][1] - b) < 1e-6:
            out[-1].append((c, d))
        else:
            out.append([(a, b), (c, d)])
    return out


def dxf_text(items: list[dict], ltscale: float = 1.0) -> str:
    """要素の一覧 → DXF の文字列。ltscale = 線種（破線）の倍率（実寸出力では 1 / 縮尺）。"""
    L: list[str] = []
    add = lambda *pairs: L.extend(str(p) for p in pairs)
    add(0, "SECTION", 2, "HEADER", 9, "$ACADVER", 1, "AC1009", 9, "$DWGCODEPAGE", 3, "ANSI_932", 9, "$LTSCALE", 40, _n(ltscale), 0, "ENDSEC")
    add(0, "SECTION", 2, "TABLES")
    add(0, "TABLE", 2, "LTYPE", 70, 2)
    add(0, "LTYPE", 2, "CONTINUOUS", 70, 0, 3, "Solid line", 72, 65, 73, 0, 40, "0.0")
    add(0, "LTYPE", 2, "HIDDEN", 70, 0, 3, "Hidden __ __ __", 72, 65, 73, 2, 40, "3.0", 49, "2.0", 49, "-1.0")
    add(0, "ENDTAB")
    add(0, "TABLE", 2, "LAYER", 70, len(LAYERS))
    for name, color, lt in LAYERS.values():
        add(0, "LAYER", 2, name, 70, 0, 62, color, 6, lt)
    add(0, "ENDTAB")
    add(0, "TABLE", 2, "STYLE", 70, 1)
    add(0, "STYLE", 2, "STANDARD", 70, 0, 40, "0.0", 41, "1.0", 50, "0.0", 71, 0, 42, "2.5", 3, "msgothic.ttc", 4, "")
    add(0, "ENDTAB", 0, "ENDSEC")
    add(0, "SECTION", 2, "ENTITIES")
    for it in items:
        layer = LAYERS.get(it["layer"], LAYERS["visible"])[0]
        k = it["k"]
        if k == "lines" and it["layer"] == "hidden":     # 隠線: つながった線分は POLYLINE に（破線が途切れない）
            for chain in _chains(it["segs"]):
                if len(chain) == 2:
                    (a, b), (c, d) = chain
                    add(0, "LINE", 8, layer, 10, _n(a), 20, _n(b), 30, "0", 11, _n(c), 21, _n(d), 31, "0")
                    continue
                add(0, "POLYLINE", 8, layer, 66, 1, 70, 0)
                for x, y in chain:
                    add(0, "VERTEX", 8, layer, 10, _n(x), 20, _n(y), 30, "0")
                add(0, "SEQEND", 8, layer)
        elif k == "lines":
            for a, b, c, d in it["segs"]:
                add(0, "LINE", 8, layer, 10, _n(a), 20, _n(b), 30, "0", 11, _n(c), 21, _n(d), 31, "0")
        elif k == "circle":
            add(0, "CIRCLE", 8, layer, 10, _n(it["c"][0]), 20, _n(it["c"][1]), 30, "0", 40, _n(it["r"]))
        elif k == "arc":
            add(0, "ARC", 8, layer, 10, _n(it["c"][0]), 20, _n(it["c"][1]), 30, "0", 40, _n(it["r"]),
                50, _n(it["a0"] % 360), 51, _n(it["a1"] % 360))
        elif k == "poly":
            p = list(it["pts"]) + [it["pts"][-1]] * (4 - len(it["pts"]))
            add(0, "SOLID", 8, layer)
            for i, (x, y) in enumerate(p[:4]):
                add(10 + i, _n(x), 20 + i, _n(y), 30 + i, "0")
        elif k == "text":
            h = {"l": 0, "c": 1, "r": 2}[it.get("align", "l")]
            add(0, "TEXT", 8, layer, 10, _n(it["p"][0]), 20, _n(it["p"][1]), 30, "0", 40, _n(it["h"]), 1, _str(it["s"]),
                50, _n(it.get("angle", 0.0)))
            if h:
                add(72, h, 11, _n(it["p"][0]), 21, _n(it["p"][1]), 31, "0")
    add(0, "ENDSEC", 0, "EOF")
    return "\n".join(L) + "\n"
