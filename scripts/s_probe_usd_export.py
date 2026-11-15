"""S（USD 保存）の確認用（利用者の PC で実行）。結果を s_probe_report.txt に書く。
2 回目（2026-11-11）: BRep を .usda で書き、パートから reference する形の確認。

    python scripts\\s_probe_usd_export.py

調べること
1. usd_brep.usd の関数と説明（export_brep の引数がこの開発環境では分からないため）
2. デモ部品を usd-brep で USD 保存し、BRep（omniSolid）が書けたか・警告
3. pxr（OpenUSD）が使えるなら、保存した .usda を本物の USD として開けるか（構文・reference・姿勢）
4. 書けた BRep を usd_brep で読み戻して体積が合うか
レポートは 1 行ずつ書き足すので、途中でプロセスが落ちてもそこまでは残る。
"""
import importlib.metadata
import inspect
import os
import platform
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "core"))
REPORT = os.path.join(HERE, "s_probe_report.txt")
_f = open(REPORT, "w", encoding="utf-8")


def out(*a):
    s = " ".join(str(x) for x in a)
    _f.write(s + "\n"); _f.flush()
    try:
        print(s)
    except UnicodeEncodeError:
        pass


def step(title):
    def deco(fn):
        out(f"\n== {title}")
        try:
            return fn()
        except Exception:                       # noqa: BLE001
            out("例外:\n" + traceback.format_exc())
    return deco


out("python", sys.version.replace("\n", " "), platform.platform())
for pkg in ("usd-brep", "usd_brep", "usd-core", "numpy"):
    try:
        out(pkg, importlib.metadata.version(pkg))
    except Exception:                           # noqa: BLE001
        out(pkg, "-")

import usd_brep as ub  # noqa: E402


@step("1. usd_brep.usd の関数")
def _():
    mod = getattr(ub, "usd", None)
    out("usd:", mod)
    for name in sorted(dir(mod)):
        if name.startswith("_"):
            continue
        obj = getattr(mod, name)
        doc = (getattr(obj, "__doc__", "") or "").strip().splitlines()
        out(f"- {name}")
        for line in doc[:25]:
            out("    " + line)
    try:
        out("signature:", inspect.signature(mod.export_brep))
    except Exception as e:                      # noqa: BLE001
        out("signature 取得不可:", e)


try:
    import pxr  # noqa: F401
    from pxr import Usd, UsdGeom
    HAVE_PXR = True
except Exception as e:                          # noqa: BLE001
    HAVE_PXR = False
    out("\npxr: 使えません", e)

TMP = tempfile.mkdtemp(prefix="agentcad_s_")
out("\n作業フォルダ:", TMP)
RES = {}


@step("2. デモ部品を usd-brep で保存")
def _():
    from cad.api.rpc import Api
    from cad.doc.workspace import Workspace
    api = Api(Workspace("usd_brep"))
    did = api.call("doc.demo")["doc"]
    st = api.call("doc.state", {"doc": did, "mesh": False})
    out("体積:", st["volume"], " ツリー:", [(t["name"], t["state"]) for t in st["tree"]])
    r = api.call("doc.save", {"doc": did, "path": os.path.join(TMP, "demo")})
    out("保存:", r)
    for name in sorted(os.listdir(TMP)):
        p = os.path.join(TMP, name)
        out(f"  {name}: {os.path.getsize(p)} bytes")
    body = os.path.join(TMP, "demo_body.usda")
    if os.path.exists(body):
        from cad.doc import usdio
        head = open(body, "rb").read(64)
        out("BRep ファイルの先頭:", head[:16])
        if head.startswith(b"#usda"):
            text = open(body, encoding="utf-8", errors="replace").read()
            out("prim:", usdio.prim_specs(text)[:20])
            out("--- 先頭 40 行 ---")
            for line in text.splitlines()[:40]:
                out("  " + line[:200])
    out("--- demo.usda の Body ---")
    t = open(r["path"], encoding="utf-8").read()
    out(t[t.find('def "Body"') if 'def "Body"' in t else t.find('"Body"'):][:300])
    a = api.call("asm.new", {"name": "A"})["doc"]
    api.call("asm.add", {"doc": a, "part": did})
    api.call("asm.add", {"doc": a, "part": did})
    r2 = api.call("doc.save", {"doc": a, "path": os.path.join(TMP, "asm")})
    out("アセンブリ保存:", r2)
    st2 = Api(Workspace("usd_brep")).call("doc.open", {"path": r2["path"]})
    out("開き直し:", st2["kind"], [c["id"] for c in st2["components"]], st2["solve"])
    RES.update(part=r["path"], asm=r2["path"], body=body, volume=st["volume"])


@step("3. pxr で開く")
def _():
    if not HAVE_PXR:
        out("pxr が無いので省略")
        return
    for key in ("part", "asm"):
        if key not in RES:
            continue
        stage = Usd.Stage.Open(RES[key])
        out(f"[{key}] defaultPrim:", stage.GetDefaultPrim().GetPath(), " upAxis:", UsdGeom.GetStageUpAxis(stage),
            " metersPerUnit:", UsdGeom.GetStageMetersPerUnit(stage))
        for prim in list(stage.Traverse())[:40]:
            extra = ""
            if prim.IsA(UsdGeom.Mesh):
                m = UsdGeom.Mesh(prim)
                extra = f" points={len(m.GetPointsAttr().Get() or [])} faces={len(m.GetFaceVertexCountsAttr().Get() or [])}"
            if prim.IsA(UsdGeom.Xformable):
                t = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default()).ExtractTranslation()
                extra += f" world_t={tuple(round(v, 4) for v in t)}"
            out(f"  {prim.GetPath()} [{prim.GetTypeName()}]{extra}")
    if os.path.exists(RES.get("body", "")):
        stage = Usd.Stage.Open(RES["body"])
        out("[body] defaultPrim:", stage.GetDefaultPrim().GetPath() if stage.GetDefaultPrim() else None)
        for prim in list(stage.Traverse())[:40]:
            out(f"  {prim.GetPath()} [{prim.GetTypeName()}] attrs={[a.GetName() for a in prim.GetAttributes()][:12]}")


@step("4. BRep を読み戻す")
def _():
    for key, expect in (("body", 1), ("part", 1), ("asm", 2)):     # part / asm は reference を合成して読む
        if not os.path.exists(RES.get(key, "")):
            out(f"[{key}] ファイルが無いので省略")
            continue
        try:
            breps = ub.usd.import_breps(RES[key])
        except Exception as e:                  # noqa: BLE001
            out(f"[{key}] 読めない: {e}")
            continue
        vols = [round(ub.compute_volume(b[0] if isinstance(b, tuple) else b), 3) for b in breps]
        out(f"[{key}] 読めた数: {len(breps)}（期待 {expect}） 体積: {vols} 期待: {RES['volume']:.3f}")


out("\n完了")
_f.close()
