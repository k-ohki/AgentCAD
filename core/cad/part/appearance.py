"""外観（色）。一般的な 3D CAD の「外観」と同じく、面 > フィーチャー > パート の順に優先する（D69）。

PartDocument.appearance = {"part": "#rrggbb" | None, "features": {フィーチャー ID: 色}, "faces": {面の永続名: 色}}
面の永続名は "<フィーチャー ID>/..."（パターンの複製は "<ID>~<n>/..."）なので、名前の先頭でフィーチャーの色を引く。
パターン・ミラーの複製は、パターン自身に色が無ければ元（seeds）の色を引き継ぐ（一般的な 3D CAD と同じ）。
"""
from __future__ import annotations

import re

#: 色を付けていない部品の色（viewer.js の BASE と同じ）
DEFAULT_PART = "#b8c2d4"

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def empty() -> dict:
    return {"part": None, "features": {}, "faces": {}}


def normalize_color(c) -> str | None:
    """"#rrggbb" / "#rgb" / [r, g, b]（0〜1）/ None を "#rrggbb"（小文字）か None に。"""
    if c is None or c == "":
        return None
    if isinstance(c, (list, tuple)) and len(c) == 3:
        return "#" + "".join(f"{max(0, min(255, round(float(v) * 255))):02x}" for v in c)
    s = str(c).strip()
    if re.fullmatch(r"#[0-9a-fA-F]{3}", s):
        s = "#" + "".join(ch * 2 for ch in s[1:])
    if not _HEX.match(s):
        raise ValueError(f"色は #rrggbb で指定してください: {c}")
    return s.lower()


def rgb(c: str) -> tuple[float, float, float]:
    return tuple(int(c[i:i + 2], 16) / 255 for i in (1, 3, 5))


def feature_of_face(name: str) -> str:
    """面の永続名 → それを作ったフィーチャーの ID（パターンの複製 "LPattern1~2" は "LPattern1"）。"""
    return name.split("/", 1)[0].split("~", 1)[0]


def face_colors(app: dict, face_names: list[str], features: dict) -> tuple[str, list[str]]:
    """(パートの色, 面ごとの色)。features: ID → Feature（パターンの seeds を引くため）。"""
    part = app.get("part") or DEFAULT_PART
    fcol = app.get("features") or {}
    faces = app.get("faces") or {}

    def of_feature(fid: str, seen=()) -> str | None:
        if fid in fcol:
            return fcol[fid]
        f = features.get(fid)
        for s in (getattr(f, "seeds", None) or []) if fid not in seen else []:
            c = of_feature(s, seen + (fid,))
            if c:
                return c
        return None
    out = []
    for n in face_names:
        base = n.split("#", 1)[0]                       # 分かれた面（"…#2"）も同じ色
        out.append(faces.get(n) or faces.get(base) or of_feature(feature_of_face(n)) or part)
    return part, out
