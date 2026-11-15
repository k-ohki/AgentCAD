"""図面を「描く要素の一覧」にする（D60）。画面（drawing.js）・PDF・DXF が同じ一覧を使うので、見た目がそろう。

要素（座標は mm）:
  {"k": "lines",  "layer", "segs": [[x1, y1, x2, y2], …]}
  {"k": "circle", "layer", "c": [x, y], "r"} / {"k": "arc", "layer", "c", "r", "a0", "a1"}（度、反時計回り）
  {"k": "poly",   "layer", "pts": [[x, y], …]}（塗りつぶし。矢印）
  {"k": "text",   "layer", "p": [x, y], "h": 文字の高さ, "s": 文字, "align": "l" | "c" | "r", "angle": 度}（p は下端の基準点）
レイヤー: frame（図枠）・title（表題欄）・visible（外形線）・hidden（隠線）・dim（寸法）
ビューと寸法の要素は、ビューの位置 (x, y) からの相対座標（画面でビューをドラッグしている間、サーバに聞かずに動かせる）。
"""
from __future__ import annotations

import datetime
import math

import numpy as np

from .document import MARGIN, TITLE_H, TITLE_W, DrawingDocument, DrawingError, frame_label

TEXT_H = 3.5            # 寸法の文字の高さ
ARROW_L, ARROW_W = 3.0, 1.0
EXT_GAP, EXT_OVER = 1.0, 2.0


def fmt_value(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def text_width(s: str, h: float) -> float:
    """文字の幅の見積もり（半角 0.6 em、全角 1 em）。"""
    return sum(h * (1.0 if ord(c) > 0x2E7F else 0.6) for c in s)


def _arrow(tip, d):
    tip, d = np.asarray(tip, float), np.asarray(d, float)
    n = np.array([-d[1], d[0]])
    b = tip - d * ARROW_L
    return {"k": "poly", "layer": "dim", "pts": [tip.tolist(), (b + n * ARROW_W / 2).tolist(), (b - n * ARROW_W / 2).tolist()]}


def _line(a, b):
    return [float(a[0]), float(a[1]), float(b[0]), float(b[1])]


def linear_dim(p1, p2, direction: str, pos, text: str) -> tuple[list[dict], list[float]]:
    """長さ寸法の要素と、文字の箱 [x0, y0, x1, y1]。p1・p2・pos は同じ座標系（用紙の mm）。"""
    p1, p2, pos = (np.asarray(v, float) for v in (p1, p2, pos))
    if direction == "h":
        u = np.array([1.0, 0.0])
    elif direction == "v":
        u = np.array([0.0, 1.0])
    else:
        u = (p2 - p1) / np.linalg.norm(p2 - p1)
    if (p2 - p1) @ u < 0:
        p1, p2 = p2, p1
    n = np.array([-u[1], u[0]])
    e1, e2 = p1 + n * ((pos - p1) @ n), p2 + n * ((pos - p2) @ n)
    L = float((e2 - e1) @ u)
    segs = []
    for p, e in ((p1, e1), (p2, e2)):                    # 寸法補助線
        d = float((e - p) @ n)
        if abs(d) > EXT_GAP:
            s = math.copysign(1.0, d)
            segs.append(_line(p + n * s * EXT_GAP, e + n * s * EXT_OVER))
    w = text_width(text, TEXT_H)
    t = float((pos - e1) @ u)
    outside = L < 2 * ARROW_L + 2                        # 狭い: 矢印を外から向ける
    a, b = e1, e2
    if outside:
        a, b = e1 - u * (ARROW_L + 2), e2 + u * (ARROW_L + 2)
    if t < 0:
        a = e1 + u * (t - w / 2)
    elif t > L:
        b = e1 + u * (t + w / 2)
    segs.append(_line(a, b))
    items = [{"k": "lines", "layer": "dim", "segs": segs},
             _arrow(e1, u if outside else -u), _arrow(e2, -u if outside else u)]
    ang = math.degrees(math.atan2(u[1], u[0]))
    if ang > 90 or ang <= -90:                           # 文字は読める向きに
        ang += 180 if ang <= -90 else -180
    up = np.array([-math.sin(math.radians(ang)), math.cos(math.radians(ang))])
    c = e1 + u * t + up * 1.0
    items.append({"k": "text", "layer": "dim", "p": c.tolist(), "h": TEXT_H, "s": text, "align": "c", "angle": ang})
    m = c + up * TEXT_H / 2
    r = max(w, TEXT_H) / 2 + 1
    return items, [float(m[0] - r), float(m[1] - r), float(m[0] + r), float(m[1] + r)]


def radial_dim(c, r: float, pos, text: str, diameter: bool) -> tuple[list[dict], list[float]]:
    """直径・半径寸法。c = 中心、r = 用紙での半径、pos = 文字の位置。"""
    c, pos = np.asarray(c, float), np.asarray(pos, float)
    d = float(np.linalg.norm(pos - c))
    u = (pos - c) / d if d > 1e-9 else np.array([1.0, 0.0])
    A, B = c - u * r, c + u * r
    end = c + u * d if d > r else B
    segs = [_line(A if diameter else c, end)]
    items = [_arrow(B, u)] + ([_arrow(A, -u)] if diameter else [])
    w = text_width(text, TEXT_H)
    sx = 1.0 if u[0] >= 0 else -1.0
    if d > r:                                            # 外: 水平の棚を付けて、その上に文字
        segs.append(_line(end, end + np.array([sx * (w + 2), 0.0])))
        tc = end + np.array([sx * (w / 2 + 1), 1.0])
    else:
        tc = pos + np.array([0.0, 1.0])
    items.insert(0, {"k": "lines", "layer": "dim", "segs": segs})
    items.append({"k": "text", "layer": "dim", "p": tc.tolist(), "h": TEXT_H, "s": text, "align": "c", "angle": 0.0})
    rr = max(w, TEXT_H) / 2 + 1
    return items, [float(tc[0] - rr), float(tc[1] + TEXT_H / 2 - rr), float(tc[0] + rr), float(tc[1] + TEXT_H / 2 + rr)]


def sheet_items(doc: DrawingDocument, name: str) -> list[dict]:
    """図枠と表題欄。"""
    w, h = doc.size
    x0, y0, x1, y1 = MARGIN, MARGIN, w - MARGIN, h - MARGIN
    frame = [[x0, y0, x1, y0], [x1, y0, x1, y1], [x1, y1, x0, y1], [x0, y1, x0, y0]]
    tx, ty = x1 - TITLE_W, y0
    col = x1 - 62.0
    row = TITLE_H / 4
    lines = [[tx, ty + TITLE_H, x1, ty + TITLE_H], [tx, ty, tx, ty + TITLE_H], [col, ty, col, ty + TITLE_H]]
    lines += [[col, ty + row * i, x1, ty + row * i] for i in (1, 2, 3)]
    n, d = doc.sheet["scale"]
    cells = [("縮尺", f"{fmt_value(n)}:{fmt_value(d)}"), ("用紙", doc.sheet["size"]), ("投影法", "第三角法"),
             ("日付", datetime.date.today().isoformat())]
    items = [{"k": "lines", "layer": "frame", "segs": frame}, {"k": "lines", "layer": "title", "segs": lines},
             {"k": "text", "layer": "title", "p": [tx + 3, ty + TITLE_H - 6], "h": 2.5, "s": "名称", "align": "l", "angle": 0.0},
             {"k": "text", "layer": "title", "p": [tx + 6, ty + 9], "h": 6.0, "s": doc.title or name, "align": "l", "angle": 0.0}]
    for i, (k, v) in enumerate(cells):
        y = ty + TITLE_H - row * (i + 1) + 2.4
        items.append({"k": "text", "layer": "title", "p": [col + 2, y], "h": 2.5, "s": k, "align": "l", "angle": 0.0})
        items.append({"k": "text", "layer": "title", "p": [col + 18, y], "h": 3.5, "s": v, "align": "l", "angle": 0.0})
    return items


def view_items(doc: DrawingDocument, v: dict, curves: bool = False) -> dict:
    """ビュー 1 つ分（相対座標）: 要素、外接箱、スナップ先、エッジの形。"""
    g = doc.geometry(v)
    s = doc.scale_of(v)
    skip = np.zeros(len(g.segs), bool)
    items = []
    if curves:                                           # DXF: 全部見えている円・円弧は、線分でなく円・円弧で出す
        for e in g.edges:
            if e["whole"] and e["kind"] in ("circle", "arc"):
                skip |= g.edge == e["index"]
                it = {"k": e["kind"], "layer": "visible", "c": [e["c"][0] * s, e["c"][1] * s], "r": e["r"] * s}
                if e["kind"] == "arc":
                    it.update(a0=e["a0"], a1=e["a1"])
                items.append(it)
    P = g.segs * s
    if v.get("hidden"):
        items.insert(0, {"k": "lines", "layer": "hidden", "segs": P[g.hidden & ~skip].round(5).tolist()})
    items.insert(0 if not v.get("hidden") else 1, {"k": "lines", "layer": "visible", "segs": P[~g.hidden & ~skip].round(5).tolist()})
    x0, y0, x1, y1 = (c * s for c in g.bbox)
    if v.get("scale") and abs(s - doc.scale_of()) > 1e-12:   # シートと違う縮尺は、ビューの下に書く
        n, d = v["scale"]
        items.append({"k": "text", "layer": "dim", "p": [(x0 + x1) / 2, y0 - 8], "h": TEXT_H,
                      "s": f"縮尺 {fmt_value(n)}:{fmt_value(d)}", "align": "c", "angle": 0.0})
    edges = []
    for e in g.edges:
        d = {"name": e["name"], "kind": e["kind"]}
        if e["kind"] == "line":
            d.update(p1=[e["p1"][0] * s, e["p1"][1] * s], p2=[e["p2"][0] * s, e["p2"][1] * s])
        elif e["kind"] in ("circle", "arc"):
            d.update(c=[e["c"][0] * s, e["c"][1] * s], r=e["r"] * s)
        else:
            continue
        edges.append(d)
    snaps = [{"src": p["src"], "kind": p["kind"], "w": p["w"], "p": [p["p"][0] * s, p["p"][1] * s]} for p in g.snaps]
    return {"items": items, "bbox": [x0, y0, x1, y1], "snaps": snaps, "edges": edges}


def dim_items(doc: DrawingDocument, d: dict) -> dict:
    """寸法 1 つ分（ビューの位置からの相対座標）。"""
    v = doc.views[d["view"]]
    s = doc.scale_of(v)
    if d["kind"] == "note":                              # 注記（D73）: 文字の中心が pos
        h, (x, y) = d["h"], d["pos"]
        w = text_width(d["text"], h)
        item = {"k": "text", "layer": "dim", "p": [x, y - h / 2], "h": h, "s": d["text"], "align": "c", "angle": 0.0}
        return {"items": [item], "box": [x - w / 2 - 1, y - h / 2 - 1, x + w / 2 + 1, y + h / 2 + 1], "value": None,
                "text": d["text"], "dangling": False}
    if d["kind"] == "linear":
        pa, oka = doc.resolve(v, d["a"])
        pb, okb = doc.resolve(v, d["b"])
        u = {"h": np.array([1.0, 0.0]), "v": np.array([0.0, 1.0])}.get(d["dir"])
        value = float(np.linalg.norm(pb - pa)) if u is None else abs(float((pb - pa) @ u))
        text = fmt_value(value)
        items, box = linear_dim(pa * s, pb * s, d["dir"], d["pos"], text)
        return {"items": items, "box": box, "value": value, "text": text, "dangling": not (oka and okb)}
    c = doc.circle_of(v, d["a"]["src"])
    if c is None:
        return {"items": [], "box": [d["pos"][0] - 3, d["pos"][1] - 3, d["pos"][0] + 3, d["pos"][1] + 3], "value": None,
                "text": "?", "dangling": True}
    center, r, _ = c
    dia = d["kind"] == "diameter"
    value = 2 * r if dia else r
    text = ("Ø" if dia else "R") + fmt_value(value)
    items, box = radial_dim(center * s, r * s, d["pos"], text, dia)
    return {"items": items, "box": box, "value": value, "text": text, "dangling": False}


def render(doc: DrawingDocument, name: str, curves: bool = False) -> dict:
    """図面全体。views・dims の要素は相対座標（flatten で用紙の座標にする）。"""
    out = {"size": list(doc.size), "sheet": sheet_items(doc, name), "views": [], "dims": [], "error": None}
    try:
        doc.mesh()
    except (DrawingError, KeyError) as e:
        out["error"] = str(e.args[0]) if e.args else str(e)
        return out
    for vid, v in doc.views.items():
        r = view_items(doc, v, curves)
        out["views"].append({"id": vid, "label": frame_label(v["R"], v["U"]), "x": v["x"], "y": v["y"], "scale": v.get("scale"),
                             "hidden": bool(v.get("hidden")), "parent": v.get("parent"), "side": v.get("side"), **r})
    for did, d in doc.dims.items():
        v = doc.views[d["view"]]
        out["dims"].append({"id": did, "view": d["view"], "kind": d["kind"], "x": v["x"], "y": v["y"], "pos": d["pos"], "h": d.get("h"),
                            **dim_items(doc, d)})
    return out


def _shift(it: dict, dx: float, dy: float, k: float) -> dict:
    """要素を (dx, dy) ずらして k 倍する。"""
    it = dict(it)
    if it["k"] == "lines":
        a = np.asarray(it["segs"], float).reshape(-1, 4)
        it["segs"] = ((a + [dx, dy, dx, dy]) * k).tolist()
    elif it["k"] in ("circle", "arc"):
        it["c"], it["r"] = [(it["c"][0] + dx) * k, (it["c"][1] + dy) * k], it["r"] * k
    elif it["k"] == "poly":
        it["pts"] = [[(x + dx) * k, (y + dy) * k] for x, y in it["pts"]]
    elif it["k"] == "text":
        it["p"], it["h"] = [(it["p"][0] + dx) * k, (it["p"][1] + dy) * k], it["h"] * k
    return it


def flatten(r: dict, k: float = 1.0, sheet: bool = True) -> list[dict]:
    """render の結果を、用紙の座標の要素の一覧にする。k = 全体の倍率（DXF の実寸出力で 1 / 縮尺）。"""
    out = [_shift(it, 0, 0, k) for it in r["sheet"]] if sheet else []
    for v in r["views"]:
        out += [_shift(it, v["x"], v["y"], k) for it in v["items"]]
    for d in r["dims"]:
        out += [_shift(it, d["x"], d["y"], k) for it in d["items"]]
    return out
