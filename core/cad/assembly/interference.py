"""干渉チェック（D56）: 部品どうしが食い込んでいる所と、その体積（近似）。

カーネルには形状の移動（配置）が無いので、表示用の三角形メッシュで調べる:
  1. 部品の外接箱が重なる組だけを見る
  2. 重なる箱の中に格子点を取り、両方の部品の内側にある点を数える（内外は視線の交差回数の偶奇）
  3. 体積 ≈ 点の数 × 格子 1 つの体積。重なる所の外接箱も返す（画面で示すため）
面が接しているだけ（一致の合致など）は、格子点が境界の上に乗らないように半格子ずらしているので干渉にならない。
薄い食い込み（格子より薄い）は見落とすことがある。
"""
from __future__ import annotations

import numpy as np

#: 内外判定の視線の向き（座標軸に平行な面・辺にちょうど当たらない向き）
_DIR = np.array([0.8728, 0.3911, 0.2918])
_DIR = _DIR / np.linalg.norm(_DIR)


def _inside(points: np.ndarray, tris: np.ndarray) -> np.ndarray:
    """points (k,3) が閉じたメッシュ tris (t,3,3) の内側か。"""
    if not len(points) or not len(tris):
        return np.zeros(len(points), bool)
    A, B, C = tris[:, 0], tris[:, 1], tris[:, 2]
    e1, e2 = B - A, C - A
    p = np.cross(_DIR, e2)
    det = np.einsum("ij,ij->i", e1, p)
    ok = np.abs(det) > 1e-14
    A, e1, e2, p, det = A[ok], e1[ok], e2[ok], p[ok], det[ok]
    inv = 1.0 / det
    count = np.zeros(len(points), int)
    step = max(1, 2_000_000 // max(1, len(A)))
    for s in range(0, len(points), step):
        P = points[s:s + step]
        T = P[:, None, :] - A[None]                                  # (k,t,3)
        u = np.einsum("ktj,tj->kt", T, p) * inv
        q = np.cross(T, e1[None])
        v = (q @ _DIR) * inv
        d = np.einsum("ktj,tj->kt", q, e2) * inv
        hit = (u >= 0) & (u <= 1) & (v >= 0) & (u + v <= 1) & (d > 0)
        count[s:s + step] = hit.sum(1)
    return count % 2 == 1


def interference(parts: list[tuple[str, np.ndarray, np.ndarray]], cells: int = 28,
                 max_points: int = 60_000) -> list[dict]:
    """parts = [(部品ID, 頂点 (n,3) ワールド座標, 三角形 (m,3))]。干渉する組の一覧
    [{a, b, volume, bbox: [lo, hi]}]（体積の大きい順）。"""
    T = [(cid, V[F]) for cid, V, F in parts if len(F)]
    box = {cid: (t.reshape(-1, 3).min(0), t.reshape(-1, 3).max(0)) for cid, t in T}
    out = []
    for i in range(len(T)):
        for j in range(i + 1, len(T)):
            (ca, ta), (cb, tb) = T[i], T[j]
            lo = np.maximum(box[ca][0], box[cb][0])
            hi = np.minimum(box[ca][1], box[cb][1])
            size = hi - lo
            if np.any(size <= 1e-9):
                continue
            h = max(float(size.max()) / cells, float((size.prod() / max_points) ** (1 / 3)))
            n = np.maximum(1, np.ceil(size / h).astype(int))
            h3 = size / n
            axes = [lo[k] + (np.arange(n[k]) + 0.5) * h3[k] for k in range(3)]
            G = np.stack(np.meshgrid(*axes, indexing="ij"), -1).reshape(-1, 3)
            inA = _inside(G, ta)
            if not inA.any():
                continue
            G = G[inA]
            inB = _inside(G, tb)
            if not inB.any():
                continue
            Q = G[inB]
            out.append({"a": ca, "b": cb, "volume": float(inB.sum() * h3.prod()),
                        "bbox": [(Q.min(0) - h3 / 2).tolist(), (Q.max(0) + h3 / 2).tolist()]})
    return sorted(out, key=lambda r: -r["volume"])
