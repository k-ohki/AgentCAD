"""USD 保存（S）: .usda の書き出し・読み込み、アセンブリの reference、旧形式からの移行。"""
import json
import os
import re

import numpy as np
import pytest

from cad.api.rpc import Api, RpcError
from cad.doc import usdio
from cad.doc.workspace import Workspace
from test_asm_api import comp, make_part


def balanced(text: str) -> bool:
    """文字列リテラルの外で括弧の対応が取れているか（USDA の構文の簡易確認）。"""
    depth, i, pairs = [], 0, {")": "(", "]": "[", "}": "{"}
    while i < len(text):
        c = text[i]
        if c in "\"'":
            _, i = usdio._parse_string(text, i)
            continue
        if c == "@":
            i = text.index("@", i + 1) + 1
            continue
        if c == "#" and (i == 0 or text[i - 1] == "\n") and not text.startswith("#usda", i):
            i = text.index("\n", i)
            continue
        if c in "([{":
            depth.append(c)
        elif c in ")]}":
            if not depth or depth.pop() != pairs[c]:
                return False
        i += 1
    return not depth


@pytest.mark.parametrize("s", ['a"b', "back\\slash", "改行\nあり", "'quote'", '{"k":"v\\"x"}', ""])
def test_string_literal_roundtrip(s):
    lit = usdio._q(s)
    assert usdio._parse_string(lit, 0) == (s, len(lit))


def test_other_quote_styles():
    assert usdio._parse_string("'a\"b'", 0)[0] == 'a"b'
    assert usdio._parse_string('"""x\n"y"\n"""', 0)[0] == 'x\n"y"\n'


def test_part_usda_content_and_roundtrip(tmp_path):
    api = Api(Workspace("prism"))
    did = api.call("doc.demo")["doc"]
    r = api.call("doc.save", {"doc": did, "path": str(tmp_path / "デモ")})
    assert r["path"].endswith("デモ.usda") and r["saved"] == [r["path"]] and r["warnings"] == []
    text = open(r["path"], encoding="utf-8").read()
    assert text.startswith("#usda 1.0") and balanced(text)
    for key in ('defaultPrim = "Part"', "metersPerUnit = 0.001", 'upAxis = "Y"', 'def Mesh "Display"',
                'def BasisCurves "Edges"', "primvars:agentcad:faceIndex", '"Extrude1/end"'):
        assert key in text, key
    assert "_body.usd" not in text                       # 簡易カーネルは BRep を書かない
    # 面番号の数 = 三角形の数、点の番号は範囲内
    counts = re.search(r"faceVertexCounts = \[([^\]]*)\]", text).group(1).count(",") + 1
    fidx = re.search(r"faceIndex = \[([^\]]*)\]", text).group(1).count(",") + 1
    assert counts == fidx
    d = usdio.read_definition(r["path"])
    assert d == api.ws.get(did).to_dict()
    ws2 = Api(Workspace("prism"))
    st = ws2.call("doc.open", {"path": r["path"]})
    assert st["name"] == "デモ" and st["modified"] is False
    assert st["mesh"]["bbox"][1] == pytest.approx([100, 60, 35], abs=1e-4)


def test_mesh_orientation_is_outward(tmp_path):
    """orientation（rightHanded / leftHanded）が三角形の向きと合っていること。"""
    api = Api(Workspace("prism"))
    did = api.call("doc.demo")["doc"]
    path = api.call("doc.save", {"doc": did, "path": str(tmp_path / "p")})["path"]
    text = open(path, encoding="utf-8").read()
    orient = re.search(r'orientation = "(\w+)"', text).group(1)
    pts = np.array(re.findall(r"\(([-\d.e]+), ([-\d.e]+), ([-\d.e]+)\)",
                              re.search(r"point3f\[\] points = \[([^\]]*)\]", text).group(1)), float)
    tri = np.array(re.search(r"faceVertexIndices = \[([^\]]*)\]", text).group(1).split(","), int).reshape(-1, 3)
    vol = usdio._signed_volume(pts, tri)
    assert (vol > 0) == (orient == "rightHanded") and abs(vol) > 1


def test_modified_flag_and_undo(tmp_path):
    api = Api(Workspace("prism"))
    did = api.call("doc.demo")["doc"]
    assert api.call("doc.state", {"doc": did})["modified"] is True          # 未保存
    api.call("doc.save", {"doc": did, "path": str(tmp_path / "p")})
    assert api.call("doc.state", {"doc": did})["modified"] is False
    st = api.call("variable.set", {"doc": did, "name": "width", "expr": "120"})
    assert st["modified"] is True and api.call("doc.list")[0]["modified"] is True
    assert api.call("edit.undo", {"doc": did})["modified"] is False         # 保存した状態に戻れば未変更


def test_open_twice_returns_same_doc_and_name_clash(tmp_path):
    api = Api(Workspace("prism"))
    a = api.call("doc.demo")["doc"]
    path = api.call("doc.save", {"doc": a, "path": str(tmp_path / "p")})["path"]
    assert api.call("doc.open", {"path": path})["doc"] == a
    b = api.call("doc.new")["doc"]
    with pytest.raises(RpcError, match="別の文書"):
        api.call("doc.save", {"doc": b, "path": path})
    with pytest.raises(RpcError, match="ファイルがありません"):
        api.call("doc.open", {"path": str(tmp_path / "none.usda")})
    (tmp_path / "x.usda").write_text('#usda 1.0\ndef Xform "A"\n{\n}\n', encoding="utf-8")
    with pytest.raises(RpcError, match="AgentCAD の定義がありません"):
        api.call("doc.open", {"path": str(tmp_path / "x.usda")})


@pytest.fixture
def asm_setup():
    api = Api(Workspace("prism"))
    blk = make_part(api, "rect", [[0, 0], [40, 20]], 10, "Block")
    pin = make_part(api, "circle", [[0, 0], [5, 0]], 15, "Pin")
    a = api.call("asm.new", {"name": "Asm"})["doc"]
    api.call("asm.add", {"doc": a, "part": blk})
    api.call("asm.add", {"doc": a, "part": pin})
    api.call("asm.add", {"doc": a, "part": pin})
    api.call("asm.mate", {"doc": a, "type": "coincident", "a": ["Block<1>", "Extrude1/end"], "b": ["Pin<1>", "Extrude1/start"]})
    return api, a, blk, pin


def test_assembly_references_parts(asm_setup, tmp_path):
    api, a, blk, pin = asm_setup
    sub = tmp_path / "parts"
    sub.mkdir()
    pin_path = api.call("doc.save", {"doc": pin, "path": str(sub / "pin")})["path"]
    mtime = os.path.getmtime(pin_path)
    r = api.call("doc.save", {"doc": a, "path": str(tmp_path / "asm")})
    # 未保存の Block はアセンブリの隣に保存され、保存済みで未変更の Pin は書き直さない
    assert r["saved"] == [str(tmp_path / "asm.usda"), str(tmp_path / "Block.usda")]
    assert os.path.getmtime(pin_path) == mtime
    text = open(r["path"], encoding="utf-8").read()
    assert balanced(text) and 'defaultPrim = "Assembly"' in text
    assert text.count("prepend references = @./parts/pin.usda@") == 2 and "@./Block.usda@" in text
    assert 'def Xform "Pin_1"' in text and 'def Xform "Pin_2"' in text
    # 姿勢は行ベクトル規約（平行移動が 4 行目）
    st = api.call("doc.state", {"doc": a, "mesh": False})
    p = comp(st, "Pin<1>")["position"]
    block = text[text.index('def Xform "Pin_1"'):]
    rows = re.search(r"xformOp:transform = \( (.*) \)", block).group(1)
    last = [float(v) for v in re.findall(r"\(([^()]*)\)", rows)[3].split(",")]
    assert last == pytest.approx([*p, 1], abs=1e-6)
    d = usdio.read_definition(r["path"])
    assert {c["source"] for c in d["components"].values()} == {"./parts/pin.usda", "./Block.usda"}

    # 新しいワークスペースで開くと部品のパートも開く
    api2 = Api(Workspace("prism"))
    st2 = api2.call("doc.open", {"path": r["path"]})
    assert st2["kind"] == "assembly" and st2["modified"] is False and st2["solve"]["status"] == "ok"
    assert [c["id"] for c in st2["components"]] == ["Block<1>", "Pin<1>", "Pin<2>"]
    assert comp(st2, "Pin<1>")["position"] == pytest.approx(p, abs=1e-6)
    assert sorted(x["name"] for x in api2.call("doc.list")) == ["Block", "asm", "pin"]
    # パートを編集して保存し直すと、アセンブリ側の部品が追従する
    blk2 = next(x["doc"] for x in api2.call("doc.list") if x["name"] == "Block")
    api2.call("feature.edit", {"doc": blk2, "id": "Extrude1", "key": "distance", "value": 25})
    r2 = api2.call("doc.save", {"doc": st2["doc"]})
    assert r2["saved"] == [r["path"], str(tmp_path / "Block.usda")]           # 変更したパートも保存
    api3 = Api(Workspace("prism"))
    st3 = api3.call("doc.open", {"path": r["path"]})
    assert comp(st3, "Pin<1>")["position"][2] == pytest.approx(25)


def test_assembly_missing_part(asm_setup, tmp_path):
    api, a, blk, pin = asm_setup
    r = api.call("doc.save", {"doc": a, "path": str(tmp_path / "asm")})
    os.remove(tmp_path / "Pin.usda")
    with pytest.raises(RpcError, match="部品のファイルが見つかりません: ./Pin.usda"):
        Api(Workspace("prism")).call("doc.open", {"path": r["path"]})


def test_unsaved_part_name_does_not_overwrite_other_file(asm_setup, tmp_path):
    api, a, blk, pin = asm_setup
    (tmp_path / "Block.usda").write_text("他人のファイル", encoding="utf-8")
    r = api.call("doc.save", {"doc": a, "path": str(tmp_path / "asm")})
    assert str(tmp_path / "Block_2.usda") in r["saved"]
    assert (tmp_path / "Block.usda").read_text(encoding="utf-8") == "他人のファイル"


def test_legacy_files_open_and_migrate(asm_setup, tmp_path):
    api, a, blk, pin = asm_setup
    ws = api.ws
    # 旧形式（E4 までの .ucasm: パートの定義を中に含む JSON）を作る
    d = ws.get(a).to_dict()
    d["parts"] = {s: {"name": ws.names[s], "data": ws.get(s).to_dict()} for s in (blk, pin)}
    legacy = tmp_path / "old.ucasm"
    legacy.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    api2 = Api(Workspace("prism"))
    st = api2.call("doc.open", {"path": str(legacy)})
    assert st["kind"] == "assembly" and len(st["components"]) == 3
    r = api2.call("doc.save", {"doc": st["doc"]})                         # 旧形式のパスで保存 → .usda に
    assert r["path"] == str(tmp_path / "old.usda")
    assert sorted(os.path.basename(p) for p in r["saved"]) == ["Block.usda", "Pin.usda", "old.usda"]
    st2 = Api(Workspace("prism")).call("doc.open", {"path": r["path"]})
    assert len(st2["components"]) == 3 and st2["solve"]["status"] == "ok"


def test_prim_names():
    used = set()
    assert [usdio.prim_name(s, used) for s in ("Pin<1>", "Pin<1>", "部品<1>", "1abc")] == ["Pin_1", "Pin_1_2", "_1", "_1abc"]


BODY_SAMPLE = '''#usda 1.0
(
    defaultPrim = "World"
    customLayerData = { string note = "{ not a prim }" }
)

def Xform "World" (
    customData = { int x = 1 }
)
{
    # def Mesh "Commented"
    dictionary d = { string k = "def Fake \\"x\\"" }
    def OmniSolidBrepArray "Brep0" (
        prepend apiSchemas = ["MaterialBindingAPI"]
    )
    {
        asset a = @./x{y}.usd@
        def GeomSubset "face_0"
        {
        }
    }
}
'''


def test_prim_specs_and_brep_prims(tmp_path):
    assert usdio.prim_specs(BODY_SAMPLE) == [("/World", "Xform"), ("/World/Brep0", "OmniSolidBrepArray"),
                                              ("/World/Brep0/face_0", "GeomSubset")]
    p = tmp_path / "b_body.usda"
    p.write_text(BODY_SAMPLE, encoding="utf-8")
    assert usdio.brep_prims(str(p)) == ["/World/Brep0"]
    p.write_bytes(b"PXR-USDC\0\0")
    assert usdio.brep_prims(str(p)) == []


def test_part_body_reference(tmp_path):
    text = usdio.part_usda({"format": "agentcad.part/1"}, None, ("./p_body.usda", "/World/Brep0"))
    assert balanced(text) and "prepend references = @./p_body.usda@</World/Brep0>" in text
    assert [p for p, _ in usdio.prim_specs(text)] == ["/Part", "/Part/FeatureTree", "/Part/Body"]


class FakeBodyKernel:
    """BRep の書き出しだけ偽物にしたカーネル（形状は prism）。"""

    def __init__(self, base, body_text):
        self.base, self.body_text = base, body_text

    def __getattr__(self, k):
        return getattr(self.base, k)

    def export(self, shape, path, fmt):
        open(path, "w", encoding="utf-8").write(self.body_text)


def test_save_with_body_reference(tmp_path):
    ws = Workspace("prism")
    ws.kernel = FakeBodyKernel(ws.kernel, BODY_SAMPLE)
    api = Api(ws)
    did = api.call("doc.demo")["doc"]
    (tmp_path / "p_body.usd").write_bytes(b"old")                      # 前の版の名前は消す
    r = api.call("doc.save", {"doc": did, "path": str(tmp_path / "p")})
    assert r["warnings"] == [] and not (tmp_path / "p_body.usd").exists()
    text = open(r["path"], encoding="utf-8").read()
    assert "prepend references = @./p_body.usda@</World/Brep0>" in text
    ws.kernel.body_text = BODY_SAMPLE.replace("OmniSolidBrepArray", "Xform")
    r = api.call("doc.save", {"doc": did})
    assert "参照にできませんでした" in r["warnings"][0]
    text = open(r["path"], encoding="utf-8").read()
    assert "references" not in text and "agentcad:body = @./p_body.usda@" in text
