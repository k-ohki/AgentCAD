"""三角形メッシュのファイル（STL・3MF・OBJ）の読み書き（D71）。外部ライブラリなし（numpy・標準ライブラリだけ）。

3D プリンタ（スライサ）・Blender とのやりとりに使う。単位は mm（STL・OBJ は単位を持たないので mm とみなす。
3MF は unit 属性を mm に直す）。色は 3MF（basematerials）と OBJ（MTL の Kd）で三角形ごとに持てる。
"""
from __future__ import annotations

import io
import os
import re
import struct
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import numpy as np

MESH_EXTS = (".stl", ".3mf", ".obj")


@dataclass
class MeshObject:
    """書き出す / 読み込んだ 1 つの物体。tri_color は三角形ごとの palette の番号（色なしは None）。"""
    name: str
    pos: np.ndarray                          # (n,3) mm
    tri: np.ndarray                          # (m,3) 外向きに反時計回り
    tri_color: np.ndarray | None = None      # (m,)
    palette: list[str] = field(default_factory=list)    # "#rrggbb"

    def __post_init__(self):
        self.pos = np.asarray(self.pos, float).reshape(-1, 3)
        self.tri = np.asarray(self.tri, np.int64).reshape(-1, 3)


# ---------------------------------------------------------------- 書き出し


def write(path: str, objects: list[MeshObject]) -> list[str]:
    """拡張子（.stl / .3mf / .obj）で形式を決めて書く。戻り値: 書いたファイル（OBJ は .mtl も）。"""
    ext = os.path.splitext(path)[1].lower()
    if ext not in MESH_EXTS:
        raise ValueError(f"書き出せない形式です: {ext or '拡張子なし'}（{' / '.join(MESH_EXTS)}）")
    if not objects or not any(len(o.tri) for o in objects):
        raise ValueError("書き出す形状がありません")
    return {".stl": write_stl, ".3mf": write_3mf, ".obj": write_obj}[ext](path, objects)


def write_stl(path: str, objects: list[MeshObject]) -> list[str]:
    """バイナリ STL。全部の物体を 1 つにまとめる（STL は物体を分けられない）。"""
    T = np.concatenate([o.pos[o.tri] for o in objects]).astype(np.float64)
    n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.divide(n, ln, out=np.zeros_like(n), where=ln > 0)
    rec = np.zeros(len(T), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
    rec["n"], rec["v"] = n, T
    with open(path, "wb") as f:
        f.write(b"AgentCAD binary STL (mm)".ljust(80, b" "))
        f.write(struct.pack("<I", len(T)))
        f.write(rec.tobytes())
    return [path]


_3MF_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"


def write_3mf(path: str, objects: list[MeshObject]) -> list[str]:
    """3MF（単位 mm）。物体ごとに object、色は basematerials（三角形ごとの pid / p1）。"""
    colors: list[str] = []
    cidx: dict[str, int] = {}
    for o in objects:
        for c in (c.lower() for c in o.palette):
            if c not in cidx:
                cidx[c] = len(colors)
                colors.append(c)
    out = io.StringIO()
    w = out.write
    w('<?xml version="1.0" encoding="UTF-8"?>\n')
    w(f'<model unit="millimeter" xml:lang="ja-JP" xmlns="{_3MF_NS}">\n')
    w(' <metadata name="Application">AgentCAD</metadata>\n <resources>\n')
    if colors:
        w('  <basematerials id="1">\n')
        for c in colors:
            w(f'   <base name="{c}" displaycolor="{c.upper()}FF"/>\n')
        w('  </basematerials>\n')
    ids = []
    for k, o in enumerate(objects):
        oid = k + 2
        ids.append(oid)
        default = ""
        if colors and o.tri_color is not None and len(o.tri):
            default = f' pid="1" pindex="{cidx[o.palette[int(o.tri_color[0])].lower()]}"'
        w(f'  <object id="{oid}" type="model" name="{_xml(o.name)}"{default}>\n   <mesh>\n    <vertices>\n')
        w("".join(f'     <vertex x="{x:.6g}" y="{y:.6g}" z="{z:.6g}"/>\n' for x, y, z in o.pos.tolist()))
        w('    </vertices>\n    <triangles>\n')
        if colors and o.tri_color is not None:
            remap = [cidx[c.lower()] for c in o.palette]
            w("".join(f'     <triangle v1="{a}" v2="{b}" v3="{c}" pid="1" p1="{remap[t]}"/>\n'
                      for (a, b, c), t in zip(o.tri.tolist(), o.tri_color.tolist())))
        else:
            w("".join(f'     <triangle v1="{a}" v2="{b}" v3="{c}"/>\n' for a, b, c in o.tri.tolist()))
        w('    </triangles>\n   </mesh>\n  </object>\n')
    w(' </resources>\n <build>\n')
    w("".join(f'  <item objectid="{i}"/>\n' for i in ids))
    w(' </build>\n</model>\n')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?>\n'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
                   '</Types>\n')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8"?>\n'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
                   'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>\n')
        z.writestr("3D/3dmodel.model", out.getvalue())
    return [path]


def write_obj(path: str, objects: list[MeshObject]) -> list[str]:
    """OBJ（+ 同じ名前の .mtl）。物体ごとに o、色ごとに usemtl。"""
    mtl = os.path.splitext(path)[0] + ".mtl"
    colors = sorted({c.lower() for o in objects for c in o.palette})
    lines = ["# AgentCAD (mm)"]
    if colors:
        lines.append(f"mtllib {os.path.basename(mtl)}")
    off = 1
    for o in objects:
        lines.append(f"o {_objname(o.name)}")
        lines += [f"v {x:.6g} {y:.6g} {z:.6g}" for x, y, z in o.pos.tolist()]
        if o.tri_color is not None and colors:
            order = np.argsort(o.tri_color, kind="stable")
            last = None
            for t in order.tolist():
                c = o.palette[int(o.tri_color[t])].lower()
                if c != last:
                    lines.append(f"usemtl {_mtlname(c)}")
                    last = c
                a, b, cc = (o.tri[t] + off).tolist()
                lines.append(f"f {a} {b} {cc}")
        else:
            lines += [f"f {a} {b} {c}" for a, b, c in (o.tri + off).tolist()]
        off += len(o.pos)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    if not colors:
        return [path]
    with open(mtl, "w", encoding="utf-8", newline="\n") as f:
        for c in colors:
            r, g, b = (int(c[i:i + 2], 16) / 255 for i in (1, 3, 5))
            f.write(f"newmtl {_mtlname(c)}\nKd {r:.4f} {g:.4f} {b:.4f}\n\n")
    return [path, mtl]


def _xml(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def _objname(s: str) -> str:
    return re.sub(r"\s+", "_", s) or "object"


def _mtlname(c: str) -> str:
    return "color_" + c.lstrip("#")


# ---------------------------------------------------------------- 読み込み


def read(path: str) -> list[MeshObject]:
    """STL（バイナリ / テキスト）・3MF・OBJ を読む。単位は mm に直す。"""
    ext = os.path.splitext(path)[1].lower()
    if ext not in MESH_EXTS:
        raise ValueError(f"読み込めない形式です: {ext or '拡張子なし'}（{' / '.join(MESH_EXTS)}）")
    try:
        objs = {".stl": read_stl, ".3mf": read_3mf, ".obj": read_obj}[ext](path)
    except (ValueError, OSError):
        raise
    except Exception as e:                       # 壊れたファイル: 形式ごとの例外をまとめて ValueError にする
        raise ValueError(f"{os.path.basename(path)} を読めません: {type(e).__name__}: {e}") from e
    objs = [o for o in objs if len(o.tri)]
    if not objs:
        raise ValueError(f"{os.path.basename(path)} に三角形がありません")
    return objs


def read_stl(path: str) -> list[MeshObject]:
    with open(path, "rb") as f:
        data = f.read()
    name = os.path.splitext(os.path.basename(path))[0]
    if len(data) >= 84:
        n = struct.unpack_from("<I", data, 80)[0]
        if len(data) == 84 + 50 * n:              # バイナリ（テキストの STL が偶然この長さになることはまず無い）
            rec = np.frombuffer(data, dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")], count=n, offset=84)
            T = rec["v"].astype(float)
            return [MeshObject(name, T.reshape(-1, 3), np.arange(3 * n).reshape(-1, 3))]
    text = data.decode("latin-1")
    if not text.lstrip().lower().startswith("solid"):
        raise ValueError(f"{os.path.basename(path)} は STL ではありません")
    v = re.findall(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", text)
    if len(v) % 3:
        raise ValueError(f"{os.path.basename(path)} の三角形の頂点の数が合いません")
    P = np.array(v, float)
    return [MeshObject(name, P, np.arange(len(P)).reshape(-1, 3))]


_UNITS = {"micron": 1e-3, "millimeter": 1.0, "centimeter": 10.0, "inch": 25.4, "foot": 304.8, "meter": 1000.0}


def _matrix(s: str | None) -> np.ndarray:
    """3MF の transform（12 個。行ベクトル規約 p' = [p 1] M）→ 4x4（列ベクトル規約）。"""
    M = np.eye(4)
    if s:
        v = np.array(s.split(), float).reshape(4, 3)
        M[:3, :3], M[:3, 3] = v[:3].T, v[3]
    return M


def read_3mf(path: str) -> list[MeshObject]:
    with zipfile.ZipFile(path) as z:
        model = next((n for n in z.namelist() if n.lower().endswith(".model") and n.lower().startswith("3d/")), None)
        if model is None:
            raise ValueError(f"{os.path.basename(path)} に 3D モデル（3D/*.model）がありません")
        root = ET.fromstring(z.read(model))
    ns = root.tag[1:root.tag.index("}")] if root.tag.startswith("{") else ""
    q = (lambda t: f"{{{ns}}}{t}") if ns else (lambda t: t)
    scale = _UNITS.get(root.get("unit", "millimeter"), 1.0)
    res = root.find(q("resources"))
    colors: dict[str, list[str]] = {}
    for bm in res.iter(q("basematerials")):
        colors[bm.get("id")] = [(b.get("displaycolor") or "#808080")[:7].lower() for b in bm.iter(q("base"))]
    objects = {o.get("id"): o for o in res.iter(q("object"))}

    def emit(oid: str, M: np.ndarray, name: str, out: list):
        o = objects[oid]
        mesh = o.find(q("mesh"))
        if mesh is not None:
            P = np.array([[float(v.get(a)) for a in "xyz"] for v in mesh.find(q("vertices")).iter(q("vertex"))]).reshape(-1, 3)
            tris = list(mesh.find(q("triangles")).iter(q("triangle")))
            T = np.array([[int(t.get(a)) for a in ("v1", "v2", "v3")] for t in tris], np.int64).reshape(-1, 3)
            P = (np.c_[P, np.ones(len(P))] @ M.T)[:, :3] * scale
            if np.linalg.det(M[:3, :3]) < 0:     # 鏡映: 三角形の向きを保つ
                T = T[:, [0, 2, 1]]
            pal, tc = [], None
            dpid, didx = o.get("pid"), o.get("pindex", "0")
            if colors and tris:
                keys = []
                for t in tris:
                    pid, pi = t.get("pid", dpid), t.get("p1", didx)
                    keys.append(colors[pid][int(pi)] if pid in colors and pi is not None and int(pi) < len(colors[pid]) else None)
                if any(keys):
                    pal = sorted({k for k in keys if k})
                    tc = np.array([pal.index(k) if k else 0 for k in keys])
            out.append(MeshObject(name or o.get("name") or f"object{oid}", P, T, tc, pal))
        comps = o.find(q("components"))
        if comps is not None:
            for c in comps.iter(q("component")):
                emit(c.get("objectid"), M @ _matrix(c.get("transform")), o.get("name") or name, out)

    out: list[MeshObject] = []
    for item in root.find(q("build")).iter(q("item")):
        emit(item.get("objectid"), _matrix(item.get("transform")), "", out)
    return out


def read_obj(path: str) -> list[MeshObject]:
    """OBJ: v と f（多角形は扇形に三角形へ分ける。負の番号も可）。o / g ごとに物体を分ける。色は読まない。"""
    name0 = os.path.splitext(os.path.basename(path))[0]
    P: list[list[float]] = []
    groups: list[tuple[str, list]] = [(name0, [])]
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.split()
            if not s:
                continue
            if s[0] == "v":
                P.append([float(x) for x in s[1:4]])
            elif s[0] in ("o", "g") and len(s) > 1:
                if groups[-1][1]:
                    groups.append((" ".join(s[1:]), []))
                else:
                    groups[-1] = (" ".join(s[1:]), [])
            elif s[0] == "f":
                idx = []
                for tok in s[1:]:
                    i = int(tok.split("/")[0])
                    idx.append(i - 1 if i > 0 else len(P) + i)
                groups[-1][1].extend([idx[0], idx[k], idx[k + 1]] for k in range(1, len(idx) - 1))
    Pa = np.array(P, float).reshape(-1, 3)
    out = []
    for name, tris in groups:
        if not tris:
            continue
        T = np.array(tris, np.int64)
        used, inv = np.unique(T, return_inverse=True)
        out.append(MeshObject(name, Pa[used], inv.reshape(-1, 3)))
    return out
