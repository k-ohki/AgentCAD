"""形を PNG 画像にする（AI エージェントが作ったものを目で確かめるため。D67）。外部ライブラリなし（numpy と zlib）。

平行投影・面ごとの陰影・Z バッファ。面の境目（面番号が変わる所）と輪郭を濃い線で描くので、CAD の「エッジ付きシェーディング」に近い。
座標系は Y 上（画面と同じ）。表示方向は viewer.js の名前に合わせる。
"""
from __future__ import annotations

import math
import struct
import zlib

import numpy as np

#: 表示方向 → (yaw, pitch)（度）。viewer.js の setView と同じ
VIEWS = {"iso": (45.0, 35.264), "front": (0.0, 0.0), "back": (180.0, 0.0), "right": (90.0, 0.0), "left": (-90.0, 0.0),
         "top": (0.0, 89.999), "bottom": (0.0, -89.999), "iso_back": (225.0, 35.264), "iso_below": (45.0, -35.264)}


def _camera(view: str):
    yaw, pitch = VIEWS.get(view, VIEWS["iso"])
    y, p = math.radians(yaw), math.radians(pitch)
    d = np.array([math.cos(p) * math.sin(y), math.sin(p), math.cos(p) * math.cos(y)])     # 目の向き（モデル → 目）
    fwd = -d
    right = np.cross(fwd, [0.0, 1.0, 0.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, fwd)
    return fwd, right, up


def clip_triangles(tris: np.ndarray, face_ids: np.ndarray | None, normal, offset: float):
    """断面: 平面 n·p = offset より奥（n·p <= offset）だけを残す（画面の断面表示と同じ側）。切り口は塗らない。
    平面をまたぐ三角形は切って 1〜2 枚にする。"""
    tris = np.asarray(tris, float)
    n = np.asarray(normal, float)
    d = tris @ n - float(offset)                         # (T,3)
    keep = (d <= 0).all(1)
    part = ~keep & (d <= 0).any(1)
    out_t, out_f = [tris[keep]], [face_ids[keep]] if face_ids is not None else None
    extra_t, extra_f = [], []
    for i in np.nonzero(part)[0]:
        poly = []
        for j in range(3):
            a, b, da, db = tris[i, j], tris[i, (j + 1) % 3], d[i, j], d[i, (j + 1) % 3]
            if da <= 0:
                poly.append(a)
            if (da <= 0) != (db <= 0):
                poly.append(a + (b - a) * (da / (da - db)))
        for k in range(1, len(poly) - 1):
            extra_t.append([poly[0], poly[k], poly[k + 1]])
            if face_ids is not None:
                extra_f.append(face_ids[i])
    if extra_t:
        out_t.append(np.asarray(extra_t, float))
        if face_ids is not None:
            out_f.append(np.asarray(extra_f, face_ids.dtype))
    return np.concatenate(out_t), (np.concatenate(out_f) if face_ids is not None else None)


def render_png(tris: np.ndarray, face_ids: np.ndarray | None = None, view: str = "iso", width: int = 800,
               height: int = 600, face_colors=None) -> bytes:
    """三角形 (T,3,3) を描いた PNG（バイト列）。face_ids (T,) があれば面の境目に線を引く。
    face_colors: 面番号 → (r, g, b)（0〜1）の配列。あれば面ごとにその色で塗る（外観。D69）。"""
    W, H = int(width), int(height)
    img = np.full((H, W, 3), 244, np.uint8)
    img[:] = np.linspace(252, 222, H, dtype=np.uint8)[:, None, None]           # 背景のグラデーション
    if tris is None or len(tris) == 0:
        return _png(img)
    tris = np.asarray(tris, float)
    fwd, right, up = _camera(view)
    P = tris.reshape(-1, 3)
    sx, sy, sz = P @ right, P @ up, P @ fwd
    lo, hi = np.array([sx.min(), sy.min()]), np.array([sx.max(), sy.max()])
    span = np.maximum(hi - lo, 1e-9)
    k = 0.88 * min(W / span[0], H / span[1])
    cx, cy = (lo + hi) / 2
    X = ((sx - cx) * k + W / 2).reshape(-1, 3)
    Y = (H / 2 - (sy - cy) * k).reshape(-1, 3)
    Z = sz.reshape(-1, 3)
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    nl = np.linalg.norm(n, axis=1)
    ok = nl > 1e-15
    n[ok] /= nl[ok, None]
    n[(n @ fwd) > 0] *= -1                                                  # こちらを向いた側の法線（両面）
    L1, L2 = np.array([0.2, 0.4, 1.0]), np.array([-0.6, -0.3, 0.4])
    L1 = (L1[0] * right + L1[1] * up - L1[2] * fwd); L1 /= np.linalg.norm(L1)   # 光は目の座標で決める（viewer と同じ）
    L2 = (L2[0] * right + L2[1] * up - L2[2] * fwd); L2 /= np.linalg.norm(L2)
    shade = 0.42 + 0.5 * np.clip(n @ L1, 0, None) + 0.15 * np.clip(n @ L2, 0, None)
    base = np.array([0.72, 0.76, 0.83])
    fid0 = np.asarray(face_ids, np.int64) if face_ids is not None else None
    if face_colors is not None and fid0 is not None and len(face_colors):
        fc = np.asarray(face_colors, float)
        tc = fc[np.clip(fid0, 0, len(fc) - 1)]
    else:
        tc = np.broadcast_to(base, (len(tris), 3))
    col = np.clip(tc * shade[:, None] * 255, 0, 255).astype(np.uint8)
    zbuf = np.full((H, W), np.inf)
    fbuf = np.full((H, W), -1, np.int64)
    fid = np.asarray(face_ids, np.int64) if face_ids is not None else np.arange(len(tris))
    for t in np.where(ok)[0]:
        x0, y0, x1, y1, x2, y2 = X[t, 0], Y[t, 0], X[t, 1], Y[t, 1], X[t, 2], Y[t, 2]
        minx, maxx = max(int(math.floor(min(x0, x1, x2))), 0), min(int(math.ceil(max(x0, x1, x2))), W - 1)
        miny, maxy = max(int(math.floor(min(y0, y1, y2))), 0), min(int(math.ceil(max(y0, y1, y2))), H - 1)
        if minx > maxx or miny > maxy:
            continue
        den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(den) < 1e-12:
            continue
        gx, gy = np.meshgrid(np.arange(minx, maxx + 1) + 0.5, np.arange(miny, maxy + 1) + 0.5)
        a = ((y1 - y2) * (gx - x2) + (x2 - x1) * (gy - y2)) / den
        b = ((y2 - y0) * (gx - x2) + (x0 - x2) * (gy - y2)) / den
        c = 1 - a - b
        inside = (a >= -1e-6) & (b >= -1e-6) & (c >= -1e-6)
        if not inside.any():
            continue
        z = a * Z[t, 0] + b * Z[t, 1] + c * Z[t, 2]
        sub = zbuf[miny:maxy + 1, minx:maxx + 1]
        upd = inside & (z < sub)
        sub[upd] = z[upd]
        img[miny:maxy + 1, minx:maxx + 1][upd] = col[t]
        fbuf[miny:maxy + 1, minx:maxx + 1][upd] = fid[t]
    # 面の境目と輪郭（隣の画素と面番号が違う所）を濃く
    edge = np.zeros((H, W), bool)
    edge[:, 1:] |= fbuf[:, 1:] != fbuf[:, :-1]
    edge[1:, :] |= fbuf[1:, :] != fbuf[:-1, :]
    img[edge] = (35, 38, 46)
    return _png(img)


def _png(img: np.ndarray) -> bytes:
    H, W, _ = img.shape
    raw = b"".join(b"\x00" + img[y].tobytes() for y in range(H))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
