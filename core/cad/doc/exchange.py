"""ほかのソフトとのやりとり（D71）: 三角形メッシュのファイル（STL・3MF・OBJ）の読み込みと書き出し。

読み込み: ファイルの全部の物体を 1 つのメッシュにまとめ、インポートのフィーチャー 1 つだけのパートにする。
書き出し: パートはボディ、アセンブリは部品ごとに姿勢を反映したボディ。3MF・OBJ は物体（部品）ごとに分け、面の色（外観）も書く。
"""
from __future__ import annotations

import os

import numpy as np

from ..part import ImportFeature, PartDocument
from ..part import appearance
from . import meshio

#: 書き出しの細かさ: 名前 → (弦誤差 / モデル寸法, 曲面の角度の許容値 度)。standard は画面の表示と同じメッシュ
QUALITY = {"coarse": (4e-3, 30.0), "standard": None, "fine": (2e-4, 8.0)}


def import_part(kernel, path: str) -> tuple[PartDocument, str]:
    """メッシュのファイルを読み、インポートのフィーチャーだけのパートを作る（再生成はまだ）。戻り値 (パート, 名前)。"""
    from ..kernel.meshbody import encode
    objs = meshio.read(path)
    pos, tri, off = [], [], 0
    for o in objs:
        pos.append(o.pos); tri.append(o.tri + off); off += len(o.pos)
    doc = PartDocument(kernel)
    name = os.path.splitext(os.path.basename(path))[0]
    doc.add(ImportFeature(source=os.path.basename(path), mesh=encode(np.concatenate(pos), np.concatenate(tri))))
    return doc, name


def part_object(doc: PartDocument, name: str, quality: str = "standard", matrix=None) -> meshio.MeshObject:
    """パートのボディを書き出す物体にする（面の色つき）。matrix: 4x4 の姿勢（アセンブリの部品）。"""
    if quality not in QUALITY:
        raise ValueError(f"未知の細かさ: {quality}（{' / '.join(QUALITY)}）")
    if doc.body is None:
        raise ValueError(f"{name} には形状がありません")
    k = doc.kernel
    q = QUALITY[quality]
    m = k.export_mesh(doc.body, *q) if (q and hasattr(k, "export_mesh")) else k.tessellate(doc.body)
    pos = np.asarray(m.positions, float).reshape(-1, 3)
    tri = np.asarray(m.indices, np.int64).reshape(-1, 3)
    if not len(tri):
        raise ValueError(f"{name} には形状がありません（このカーネルは形を計算しません）")
    if matrix is not None:
        M = np.asarray(matrix, float)
        pos = pos @ M[:3, :3].T + M[:3, 3]
    names = [doc.names.face_name(h) or "?" for h in m.face_handles] if doc.names else ["?"] * len(m.face_handles)
    _, cols = appearance.face_colors(doc.appearance, names, {f.id: f for f in doc.features})
    palette = sorted(set(cols))
    pidx = {c: i for i, c in enumerate(palette)}
    face_col = np.array([pidx[c] for c in cols], np.int64)
    return meshio.MeshObject(name, pos, tri, face_col[np.asarray(m.face_of_triangle, np.int64)], palette)


def export(ws, did: str, path: str, quality: str = "standard") -> dict:
    """文書を書き出す。拡張子で形式を決める。戻り値 {path, files, objects, triangles}。"""
    kind = ws.kind(did)
    if kind == "part":
        objs = [part_object(ws.get(did), ws.names[did], quality)]
    elif kind == "assembly":
        asm = ws.get(did)
        objs = [part_object(ws.get(c.source), cid, quality, c.matrix()) for cid, c in asm.components.items()]
        if not objs:
            raise ValueError("アセンブリに部品がありません")
    else:
        raise ValueError("図面は PDF・DXF で書き出してください")
    path = os.path.abspath(path)
    files = meshio.write(path, objs)
    return {"path": path, "files": files, "objects": len(objs), "triangles": int(sum(len(o.tri) for o in objs))}
