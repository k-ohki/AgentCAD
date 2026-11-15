"""PDF の書き出し（D60）。外部ライブラリなし（PDF 1.4 を直接書く）。1 ページ = 用紙、座標は mm（実寸で印刷できる）。

文字: 半角は Helvetica（標準フォント）、全角は埋め込まない日本語フォント（HeiseiKakuGo-W5 / UniJIS-UTF16-H。
表示する側が持っている日本語フォントで代わりに描かれる）。
"""
from __future__ import annotations

import math
import zlib

MM = 72.0 / 25.4
#: レイヤー → (線の太さ mm, 破線)
STYLE = {"frame": (0.5, None), "title": (0.18, None), "visible": (0.35, None), "hidden": (0.18, (2.0, 1.0)), "dim": (0.18, None)}
CAP = 0.718                      # Helvetica の大文字の高さ / フォントサイズ
_W = {" ": 278, ".": 278, ",": 278, ":": 278, "-": 333, "(": 333, ")": 333, "/": 278, "Ø": 778, "°": 400, "_": 556}


def _width(c: str) -> float:
    """Helvetica の文字の幅（em の 1/1000）。"""
    if c in _W:
        return _W[c]
    if c.isdigit():
        return 556
    if c.isupper():
        return 690
    return 520


def _runs(s: str) -> list[tuple[bool, str]]:
    """文字列を (全角か, 文字列) の並びに分ける。"""
    out: list[tuple[bool, str]] = []
    for c in s:
        wide = ord(c) > 0xFF
        if out and out[-1][0] == wide:
            out[-1] = (wide, out[-1][1] + c)
        else:
            out.append((wide, c))
    return out


def _n(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".") or "0"


def _text(it: dict) -> str:
    size = it["h"] / CAP
    runs = _runs(it["s"])
    total = sum(sum(1000 if wide else _width(c) for c in t) for wide, t in runs) * size / 1000
    x0 = {"l": 0.0, "c": -total / 2, "r": -total}[it.get("align", "l")]
    a = math.radians(it.get("angle", 0.0))
    out = [f"q {_n(math.cos(a))} {_n(math.sin(a))} {_n(-math.sin(a))} {_n(math.cos(a))} {_n(it['p'][0])} {_n(it['p'][1])} cm BT"]
    x = x0
    for wide, t in runs:
        if wide:
            hexs = t.encode("utf-16-be").hex().upper()
            out.append(f"/F2 {_n(size)} Tf 1 0 0 1 {_n(x)} 0 Tm <{hexs}> Tj")
            x += len(t) * size
        else:
            esc = t.encode("cp1252", "replace").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
            out.append(f"/F1 {_n(size)} Tf 1 0 0 1 {_n(x)} 0 Tm ({esc.decode('latin-1')}) Tj")
            x += sum(_width(c) for c in t) * size / 1000
    out.append("ET Q")
    return "\n".join(out)


def _content(items: list[dict]) -> str:
    L = [f"{_n(MM)} 0 0 {_n(MM)} 0 0 cm", "1 J 1 j 0 G 0 g"]
    layer = None
    for it in items:
        if it["layer"] != layer:
            layer = it["layer"]
            w, dash = STYLE.get(layer, (0.18, None))
            L.append(f"{_n(w)} w [{' '.join(_n(d) for d in dash) if dash else ''}] 0 d")
        k = it["k"]
        if k == "lines":
            out, prev = [], None                     # 前の線分の終点から続くなら 1 本の線にする（破線が途切れない）
            for a, b, c, d in it["segs"]:
                if prev is None or abs(a - prev[0]) > 1e-6 or abs(b - prev[1]) > 1e-6:
                    out.append(f"{_n(a)} {_n(b)} m")
                out.append(f"{_n(c)} {_n(d)} l")
                prev = (c, d)
            if out:
                L.append(" ".join(out) + " S")
        elif k in ("circle", "arc"):
            a0, a1 = (0.0, 360.0) if k == "circle" else (it["a0"], it["a0"] + ((it["a1"] - it["a0"]) % 360 or 360))
            n = max(8, int(abs(a1 - a0) / 5))
            pts = [(it["c"][0] + it["r"] * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
                    it["c"][1] + it["r"] * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]
            L.append(" ".join(f"{_n(x)} {_n(y)} {'m' if i == 0 else 'l'}" for i, (x, y) in enumerate(pts)) + " S")
        elif k == "poly":
            L.append(" ".join(f"{_n(x)} {_n(y)} {'m' if i == 0 else 'l'}" for i, (x, y) in enumerate(it["pts"])) + " h f")
        elif k == "text":
            L.append(_text(it))
    return "\n".join(L)


def pdf_bytes(items: list[dict], size: tuple[float, float], title: str = "") -> bytes:
    """要素の一覧（用紙の mm）→ PDF。size = 用紙の (幅, 高さ) mm。"""
    stream = zlib.compress(_content(items).encode("latin-1"))
    info_title = "FEFF" + title.encode("utf-16-be").hex().upper()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_n(size[0] * MM)} {_n(size[1] * MM)}] /Contents 4 0 R "
         "/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>").encode(),
        b"<< /Length " + str(len(stream)).encode() + b" /Filter /FlateDecode >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type0 /BaseFont /HeiseiKakuGo-W5 /Encoding /UniJIS-UTF16-H /DescendantFonts [7 0 R] >>",
        (b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /HeiseiKakuGo-W5 /CIDSystemInfo << /Registry (Adobe) "
         b"/Ordering (Japan1) /Supplement 2 >> /FontDescriptor 8 0 R /DW 1000 >>"),
        (b"<< /Type /FontDescriptor /FontName /HeiseiKakuGo-W5 /Flags 4 /FontBBox [0 -137 1000 863] /ItalicAngle 0 "
         b"/Ascent 752 /Descent -221 /CapHeight 737 /StemV 114 >>"),
        f"<< /Title <{info_title}> /Creator (AgentCAD) >>".encode(),
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info {len(objs)} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
