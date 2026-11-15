"""Workspace: 開いている文書とカーネルの管理。UI（API）からはここを通して文書を扱う。

保存は USD（.usda、cad.doc.usdio）。旧形式（.ucpart / .ucasm の JSON）は開くことだけできる（D32）。
メッシュのファイル（STL・3MF・OBJ）は「開く」で新しいパートとして取り込み、export で書き出す（cad.doc.exchange。D71）。
"""
from __future__ import annotations

import json
import logging
import os
import uuid

import numpy as np

from ..part import PartDocument
from ..part import appearance
from . import exchange, meshio, usdio

log = logging.getLogger(__name__)

USD_EXT = ".usda"
LEGACY_EXTS = (".ucpart", ".ucasm")


class _Sources:
    """アセンブリの部品参照（Workspace の文書 ID → PartSource）。"""

    def __init__(self, ws):
        self.ws = ws

    def __getitem__(self, key):
        from ..assembly import PartSource
        return PartSource(self.ws.get(key))


def kernel_choices() -> dict:
    """使えるカーネル {名前: クラス}。auto は usd_brep → prism の順で最初に使えるもの。"""
    from ..kernel import available_kernels
    from ..kernel.prism import PrismKernel
    out = {k: v for k, v in available_kernels().items() if k != "mock"}
    out["prism"] = PrismKernel
    out["mock"] = available_kernels()["mock"]
    return out


def display_mesh(d: PartDocument) -> dict | None:
    """パートの表示メッシュ（numpy 配列）。API の送信と USD の Display の両方に使う。"""
    if d.body is None:
        return None
    k = d.kernel
    m = k.tessellate(d.body)
    names = [d.names.face_name(h) or "?" for h in m.face_handles] if d.names else []
    findex = {h: i for i, h in enumerate(m.face_handles)}
    segs, owner, enames, efaces = [], [], [], []
    for e, fa, fb in k.edges(d.body):
        pl = k.edge_polyline(d.body, e)
        if pl is None or not len(pl):
            continue
        segs.append(np.asarray(pl, float).reshape(-1, 2, 3))
        owner.append(np.full(len(segs[-1]), len(enames)))
        enames.append(d.names.edge_name_of(e) or "?")
        efaces.append([findex.get(fa, -1), findex.get(fb, -1)])
    pos = np.asarray(m.positions, float).reshape(-1, 3)
    return {"positions": pos, "indices": np.asarray(m.indices).reshape(-1, 3),
            "face_of_triangle": np.asarray(m.face_of_triangle), "face_names": names,
            "edges": np.concatenate(segs) if segs else np.zeros((0, 2, 3)),
            "edge_of_segment": np.concatenate(owner) if owner else np.zeros(0, int),
            "edge_names": enames, "edge_faces": efaces}


def _same(a: str | None, b: str | None) -> bool:
    return bool(a and b) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _usd_path(path: str) -> str:
    """保存先のパス: 拡張子が無ければ .usda を付け、旧形式の拡張子なら .usda に替える。"""
    root, ext = os.path.splitext(path)
    if ext.lower() in LEGACY_EXTS:
        return root + USD_EXT
    return path if ext.lower() == USD_EXT else path + USD_EXT


def _relpath(target: str, start_dir: str) -> str:
    """USD の asset パス（区切りは /）。別ドライブなら絶対パス。"""
    try:
        rel = os.path.relpath(target, start_dir)
        rel = rel if rel.startswith("..") else "./" + rel
    except ValueError:
        rel = os.path.abspath(target)
    return rel.replace("\\", "/")


class Workspace:
    def __init__(self, kernel: str = "auto"):
        choices = kernel_choices()
        if kernel == "auto":
            kernel = "usd_brep" if "usd_brep" in choices else "prism"
        if kernel not in choices:
            raise ValueError(f"カーネル {kernel} は使えません（使えるもの: {', '.join(choices)}）")
        self.kernel_name = kernel
        self.kernel = choices[kernel]()
        self.docs: dict = {}                    # 文書 ID → PartDocument | Assembly
        self.names: dict[str, str] = {}
        self.paths: dict[str, str | None] = {}
        self._saved: dict[str, str] = {}        # 文書 ID → 保存（または読み込み）したときの定義（変更の判定用）
        self._meshes: dict[str, tuple] = {}     # パートの文書 ID → (ボディ, 表示メッシュ)（図面用のキャッシュ）
        #: 時間のかかる処理（ファイルを開く）の進み具合。{title, step, done, total} か None。
        #: 画面は ui.progress（HTTP のロックの外）で取りに来る（D69）
        self.progress: dict | None = None

    def _add(self, doc, name: str, path: str | None = None) -> str:
        did = uuid.uuid4().hex[:8]
        self.docs[did], self.names[did], self.paths[did] = doc, name, path
        if isinstance(doc, PartDocument):
            if self.progress is not None:          # 開いている途中: フィーチャーごとに進み具合を知らせる
                doc.progress = lambda i, n, step: self.progress.update(title=f"{name} を開いています", step=step, done=i, total=n)
            try:
                doc.regenerate()
            finally:
                doc.progress = None
        if path:
            self._saved[did] = self._snapshot(did)
        return did

    def _snapshot(self, did: str) -> str:
        return json.dumps(self.get(did).to_dict(), sort_keys=True, default=str)

    def modified(self, did: str) -> bool:
        """最後に保存（読み込み）してから定義が変わったか。未保存の文書は常に True。"""
        return self._saved.get(did) != self._snapshot(did)

    def kind(self, did: str) -> str:
        """文書の種類: part / assembly / drawing。"""
        from ..drawing import DrawingDocument
        d = self.get(did)
        return "part" if isinstance(d, PartDocument) else "drawing" if isinstance(d, DrawingDocument) else "assembly"

    def is_assembly(self, did: str) -> bool:
        return self.kind(did) == "assembly"

    def is_part(self, did: str) -> bool:
        return did in self.docs and isinstance(self.docs[did], PartDocument)

    def new_drawing(self, model: str, name: str | None = None) -> str:
        """パート model の図面を作る（ビューはまだ無い）。"""
        from ..drawing import DrawingDocument
        if not self.is_part(model):
            raise ValueError("図面にするパートを選んでください")
        return self._add(DrawingDocument(self.part_mesh, model), name or self.names[model])

    def part_mesh(self, did: str) -> tuple[object, dict | None]:
        """パートの表示メッシュと、形が変わると変わる印（ボディそのもの）。同じボディなら作り直さない。図面が使う。"""
        if not self.is_part(did):
            raise KeyError("図面のモデル（パート）が開かれていません")
        d = self.docs[did]
        hit = self._meshes.get(did)
        if hit is None or hit[0] is not d.body:
            hit = self._meshes[did] = (d.body, display_mesh(d))
        return hit

    def new_assembly(self, name: str = "Assembly") -> str:
        from ..assembly import Assembly
        return self._add(Assembly(_Sources(self)), name)

    def new_part(self, name: str = "Part") -> str:
        return self._add(PartDocument(self.kernel), name)

    def get(self, did: str):
        if did not in self.docs:
            raise KeyError(f"文書 {did} は開かれていません")
        return self.docs[did]

    def find_open(self, path: str) -> str | None:
        return next((d for d, p in self.paths.items() if _same(p, path)), None)

    # ------------------------------------------------------------ 開く
    def open(self, path: str) -> str:
        """.usda（AgentCAD で保存したもの）か旧形式の JSON を開く。既に開いていればその文書を返す。
        メッシュのファイル（.stl / .3mf / .obj）は、取り込んだ新しいパート（未保存）にする（D71）。"""
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            raise ValueError(f"ファイルがありません: {path}")
        if (did := self.find_open(path)) is not None:
            return did
        outer = self.progress is None             # 図面・アセンブリから部品を開くときは入れ子になる
        if outer:
            self.progress = {"title": f"{os.path.basename(path)} を読んでいます", "step": "", "done": 0, "total": 0}
        try:
            return self._open(path)
        finally:
            if outer:
                self.progress = None

    def _open(self, path: str) -> str:
        if path.lower().endswith(meshio.MESH_EXTS):
            doc, name = exchange.import_part(self.kernel, path)
            return self._add(doc, name)
        if path.lower().endswith(USD_EXT):
            d = usdio.read_definition(path)
        else:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        name = os.path.splitext(os.path.basename(path))[0]
        fmt = str(d.get("format", ""))
        if fmt.startswith("agentcad.assembly"):
            return self._open_assembly(d, name, path)
        if fmt.startswith("agentcad.drawing"):
            from ..drawing import DrawingDocument
            full = os.path.normpath(os.path.join(os.path.dirname(path), d["model"]))
            if not os.path.isfile(full):
                raise ValueError(f"図面のモデルのファイルが見つかりません: {d['model']}")
            d["model"] = self.open(full)
            return self._add(DrawingDocument.from_dict(d, self.part_mesh), name, path)
        if not fmt.startswith("agentcad.part"):
            raise ValueError(f"{os.path.basename(path)} は AgentCAD の文書ではありません（format: {fmt or 'なし'}）")
        return self._add(PartDocument.from_dict(self.kernel, d), name, path)

    def _open_assembly(self, d: dict, name: str, path: str) -> str:
        from ..assembly import Assembly
        legacy = d.pop("parts", None)
        if legacy is not None:                  # 旧形式（.ucasm）: パートの定義が中に入っている（D30）
            remap = {old: self._add(PartDocument.from_dict(self.kernel, p["data"]), p["name"])
                     for old, p in legacy.items()}
        else:                                   # USD: source はパートファイルの相対パス
            base = os.path.dirname(path)
            remap, missing = {}, []
            for src in sorted({c["source"] for c in d["components"].values()}):
                full = os.path.normpath(os.path.join(base, src))
                if not os.path.isfile(full):
                    missing.append(src)
                    continue
                remap[src] = self.open(full)
            if missing:
                raise ValueError("部品のファイルが見つかりません: " + ", ".join(missing))
        for c in d["components"].values():
            c["source"] = remap[c["source"]]
        return self._add(Assembly.from_dict(d, _Sources(self)), name, path)

    # ------------------------------------------------------------ 保存
    def save(self, did: str, path: str | None = None) -> dict:
        """USD（.usda）で保存する。戻り値: {path, saved（書いたファイル）, warnings}。
        アセンブリは、使っているパートのうち未保存・変更あり・旧形式のものも一緒に保存する（D34）。"""
        path = path or self.paths.get(did)
        if not path:
            raise ValueError("保存先のパスを指定してください")
        path = os.path.abspath(_usd_path(path))
        other = self.find_open(path)
        if other is not None and other != did:
            raise ValueError(f"{os.path.basename(path)} は別の文書（{self.names[other]}）として開いています")
        out = {"path": path, "saved": [], "warnings": []}
        if self.is_assembly(did):
            self._save_assembly(did, path, out)
        elif self.kind(did) == "drawing":
            self._save_drawing(did, path, out)
        else:
            self._save_part(did, path, out)
        return out

    def _commit_path(self, did: str, path: str, out: dict):
        self.paths[did] = path
        self.names[did] = os.path.splitext(os.path.basename(path))[0]
        self._saved[did] = self._snapshot(did)
        out["saved"].append(path)

    def _save_part(self, did: str, path: str, out: dict):
        doc = self.get(did)
        body_ref = self._export_body(doc, path, out["warnings"])
        mesh = display_mesh(doc)
        if mesh is not None:                      # 外観（D69）: 表示メッシュに面ごとの色（primvars:displayColor）を書く
            _, cols = appearance.face_colors(doc.appearance, mesh["face_names"], {f.id: f for f in doc.features})
            mesh = {**mesh, "face_colors": cols}
        usdio.write_text(path, usdio.part_usda(doc.to_dict(), mesh, body_ref))
        self._commit_path(did, path, out)

    def _export_body(self, doc: PartDocument, path: str, warnings: list) -> tuple[str, str | None] | None:
        """カーネルが BRep を書ければ <名前>_body.usda に書き、(asset パス, BrepArray の prim パス) を返す。
        パートの /Part/Body がその prim を reference する。書けなければ警告だけ（D33）。"""
        root = os.path.splitext(path)[0]
        body_path = root + "_body.usda"
        if os.path.exists(root + "_body.usd"):   # S の最初の版は .usd（バイナリ）に書いていた
            os.remove(root + "_body.usd")
        if doc.body is None:
            return None
        try:
            self.kernel.export(doc.body, body_path, "usd")
        except NotImplementedError:
            return None                          # 簡易カーネル: BRep が無いので書かない
        except Exception as e:                   # noqa: BLE001 書けなくても保存は続ける
            log.exception("BRep の書き出しに失敗")
            warnings.append(f"BRep（omniSolid）を書き出せませんでした: {e}")
            if os.path.exists(body_path):
                os.remove(body_path)             # 古い BRep を残すと形状と食い違う
            return None
        prims = usdio.brep_prims(body_path)
        if len(prims) != 1:
            warnings.append(f"BRep のファイルに BrepArray が {len(prims)} 個あり、参照にできませんでした（ファイルは書きました）")
        return "./" + os.path.basename(body_path), (prims[0] if len(prims) == 1 else None)

    def _save_assembly(self, did: str, path: str, out: dict):
        asm = self.get(did)
        base = os.path.dirname(path)
        refs = {}
        for src in sorted({c.source for c in asm.components.values()}):
            p = self.paths.get(src)
            if p is None:                        # 未保存のパートはアセンブリの隣に置く
                p = self._free_path(os.path.join(base, self.names[src] + USD_EXT))
            if not p.lower().endswith(USD_EXT) or self.modified(src):    # 未保存は常に modified
                self._save_part(src, os.path.abspath(_usd_path(p)), out)
            refs[src] = _relpath(self.paths[src], base)
        d = asm.to_dict()
        for c in d["components"].values():
            c["source"] = refs[c["source"]]
        comps = [(cid, refs[c.source], c.matrix()) for cid, c in asm.components.items()]
        usdio.write_text(path, usdio.assembly_usda(d, comps))
        self._commit_path(did, path, out)
        out["saved"].insert(0, out["saved"].pop())   # アセンブリ自身を先頭に

    def _save_drawing(self, did: str, path: str, out: dict):
        """図面を保存する。モデルのパートが未保存・変更ありなら一緒に保存する（アセンブリと同じ）。"""
        drw = self.get(did)
        base = os.path.dirname(path)
        src = drw.model
        if not self.is_part(src):
            raise ValueError("図面のモデル（パート）が開かれていません")
        p = self.paths.get(src) or self._free_path(os.path.join(base, self.names[src] + USD_EXT))
        if _same(p, path):
            raise ValueError("図面はモデルのパートと別の名前で保存してください")
        if not p.lower().endswith(USD_EXT) or self.modified(src):
            self._save_part(src, os.path.abspath(_usd_path(p)), out)
        ref = _relpath(self.paths[src], base)
        d = drw.to_dict()
        d["model"] = ref
        usdio.write_text(path, usdio.drawing_usda(d, ref))
        self._commit_path(did, path, out)
        out["saved"].insert(0, out["saved"].pop())

    def export(self, did: str, path: str, quality: str = "standard") -> dict:
        """パート・アセンブリを STL / 3MF / OBJ に書き出す（拡張子で形式を決める。D71）。"""
        return exchange.export(self, did, path, quality)

    def _free_path(self, path: str) -> str:
        """既存のファイル・開いている文書と重ならないパス（名前_2.usda…）。"""
        root, ext = os.path.splitext(path)
        cand, n = path, 2
        while os.path.exists(cand) or self.find_open(cand) is not None:
            cand, n = f"{root}_{n}{ext}", n + 1
        return cand

    def close(self, did: str):
        for t in (self.docs, self.names, self.paths, self._saved, self._meshes):
            t.pop(did, None)
