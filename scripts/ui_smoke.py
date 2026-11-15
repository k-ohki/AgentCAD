"""UI の通し確認（開発用）: 簡易カーネルでサーバを立て、ヘッドレス Chrome で実際に操作し、結果を確かめて画面を撮る。

    python scripts/ui_smoke.py [出力フォルダ]

必要: playwright（pip install playwright）と Chrome。環境変数 CHROME で Chrome の場所を指定できる。
確認に失敗すると AssertionError で止まる。
"""
import os
import sys
import math
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
from cad.api.rpc import Api  # noqa: E402
from cad.api.server import make_handler  # noqa: E402
from cad.doc.workspace import Workspace  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
os.environ["AGENTCAD_NO_DIALOG"] = "1"
os.environ["AGENTCAD_AGENT_CONFIG"] = os.path.join(tempfile.mkdtemp(), "agent.json")   # 利用者の AI の設定に触れない      # 実機（tkinter がある）でも本物のダイアログを出さない（パスの入力欄で操作する）
os.makedirs(OUT, exist_ok=True)


def tree_states(pg):
    return pg.eval_on_selector_all("#tree li.item[data-id]", "els => els.map(e => e.dataset.id + ':' + e.className.split(' ')[1])")


def cmd(pg, sel, **kw):
    """CommandManager のボタンを押す（入っているタブが隠れていれば、先にそのタブを開く）。"""
    pg.evaluate("""s => { const b = document.querySelector(s), p = b && b.closest('.cm-panel');
        if (p && p.hidden) document.querySelector(`#cm-tabs [data-tab=${p.dataset.panel}]`).click(); }""", sel)
    pg.click(sel, **kw)


def view(pg, name):
    """表示方向メニューから選ぶ。"""
    pg.click("#btn-views"); pg.click(f"#views-menu [data-view={name}]")


def assert_menu_visible(pg, menu):
    """メニューの項目が、ほかの要素に隠れずに見えていること（ツールバーで切れる不具合の確認）。"""
    hidden = pg.evaluate(f"""() => [...document.querySelectorAll('{menu} button')].filter((b) => {{
        const r = b.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
        const hit = document.elementFromPoint(x, y);
        return !hit || !(hit === b || b.contains(hit));
    }}).map((b) => b.textContent)""")
    assert not hidden, f"{menu} の項目が隠れています: {hidden}"


def scenario_features(pg, shot):
    """E1: ロールバックバー、変数、式、エラー表示、視点。"""
    bar = pg.locator("#tree li.rollback").bounding_box()
    tgt = pg.locator("#tree li.item[data-id='Sketch2']").bounding_box()
    pg.mouse.move(bar["x"] + 50, bar["y"] + 3); pg.mouse.down()
    pg.mouse.move(tgt["x"] + 50, tgt["y"] + 4, steps=8); pg.mouse.up(); time.sleep(0.5)
    assert tree_states(pg)[2:] == [f"{n}:rolled_back" for n in ("Sketch2", "Extrude2", "Sketch3", "Extrude3", "Fillet1")]
    shot("ui_rollback")
    pg.keyboard.press("Control+z"); time.sleep(0.4)
    inp = pg.locator("#vars input").first
    inp.fill("150"); inp.press("Enter"); time.sleep(0.5)
    pg.fill("#var-add input[name=name]", "t"); pg.fill("#var-add input[name=expr]", "width / 5")
    pg.click("#var-add button"); time.sleep(0.4)
    pg.click("#tree li.item[data-id='Extrude1']"); pg.wait_for_selector("#prop .param input.expr")
    e = pg.locator("#prop .param input.expr").first; e.fill("t"); e.press("Enter"); time.sleep(0.6)
    assert pg.input_value("#prop .param input.value") == "30"
    e = pg.locator("#prop .param input.expr").first; e.fill("1 +"); e.press("Enter"); time.sleep(0.4)
    assert "式が読めません" in pg.inner_text("#toast")
    view(pg, "front"); time.sleep(0.3)
    shot("ui_front")


def scenario_sketch(pg, api, shot):
    """E2: 新規 → 正面にスケッチ → 原点から線を連続で引いて閉じる → 寸法（式も）→ 円弧・トリム → 拘束 → 押し出し → 面にスケッチ。"""
    pg.click("#btn-new-menu"); pg.click("#btn-newdoc"); time.sleep(0.3)
    did = pg.evaluate("() => window.__agentcad.app.doc")
    cmd(pg, "#btn-sketch"); pg.click("#sketch-menu button[data-plane=Front]"); time.sleep(0.4)
    vb = pg.locator("#view").bounding_box()

    def S(u, v):
        sx, sy = pg.evaluate(f"() => window.__agentcad.sketch.s({u}, {v})")
        return vb["x"] + sx, vb["y"] + sy

    def click(u, v):
        x, y = S(u, v); pg.mouse.move(x, y); time.sleep(0.05); pg.mouse.click(x, y); time.sleep(0.3)
    st = lambda: api.sketch_state(did)
    pg.keyboard.press("l")
    for uv in [(0, 0), (40, 0.4), (40.3, 25), (0, 25), (0, 0)]:
        click(*uv)
    s = st()
    assert sorted(c["type"] for c in s["constraints"]) == ["horizontal", "horizontal", "vertical", "vertical"]
    assert sum(e.get("fixed", False) for e in s["entities"] if e["type"] == "point") == 1 and s["profiles"] == 1
    pts = {e["id"]: e for e in s["entities"] if e["type"] == "point"}
    lines = [e for e in s["entities"] if e["type"] == "line"]
    mid = lambda l: ((pts[l["p1"]]["x"] + pts[l["p2"]]["x"]) / 2, (pts[l["p1"]]["y"] + pts[l["p2"]]["y"]) / 2)
    pg.keyboard.press("d")
    for i, val in ((0, "40"), (1, None)):
        click(*mid(lines[i]))
        pg.mouse.click(vb["x"] + 30, vb["y"] + vb["height"] - 60); time.sleep(0.4)
        if val is None:
            first = next(c["id"] for c in st()["constraints"] if c["type"] == "length")
            val = f"={s['name']}.{first} * 0.5"
        ed = pg.locator(".dim-editor"); ed.fill(val); ed.press("Enter"); time.sleep(0.4)
    s = st()
    assert (s["solve"]["state"], s["solve"]["dof"]) == ("full", 0)
    pg.keyboard.press("a")
    for uv in [(60, 10), (68, 10), (60, 18)]:
        click(*uv)
    pg.keyboard.press("l")
    for uv in [(55, 0), (75, 20)]:
        click(*uv)
    pg.keyboard.press("Escape"); pg.keyboard.press("t")
    click(58, 3)
    s = st()
    dl = [e for e in s["entities"] if e["type"] == "line"][-1]
    pp = {e["id"]: e for e in s["entities"] if e["type"] == "point"}
    assert abs(pp[dl["p1"]]["x"] - 67.57) < 0.05                     # 円弧との交点まで切れた
    shot("ui_sketch")
    pg.keyboard.press("s")
    click(*((pp[dl["p1"]]["x"] + pp[dl["p2"]]["x"]) / 2, (pp[dl["p1"]]["y"] + pp[dl["p2"]]["y"]) / 2))
    pg.click("#prop .btns button:has-text('水平')"); time.sleep(0.4)
    assert any(c["type"] == "horizontal" and dl["id"] in c["refs"] for c in st()["constraints"])
    pg.keyboard.press("Control+z"); time.sleep(0.4)
    assert not any(dl["id"] in c["refs"] and c["type"] == "horizontal" for c in st()["constraints"])
    cmd(pg, "#btn-extrude"); time.sleep(0.8)                         # 編集中のまま押し出せる
    assert "体積" not in pg.inner_text("#status-info")               # 体積は既定で非表示（D66）
    pg.check("#show-volume"); time.sleep(0.5)
    assert "体積 8000.00" in pg.inner_text("#status-info"), pg.inner_text("#status-info")
    assert pg.evaluate("() => JSON.parse(localStorage.getItem('agentcad.settings')).show_volume") is True
    assert pg.locator("#prop select[data-opt=op]").input_value() == "add"
    # エッジの選択（上面と前面の間）→ フィレットのボタンが押せるようになる
    view(pg, "iso"); time.sleep(0.4)
    sx, sy = pg.evaluate("() => window.__agentcad.viewer.project([20, 20, 10])")
    pg.mouse.move(vb["x"] + sx, vb["y"] + sy); time.sleep(0.3); pg.mouse.click(vb["x"] + sx, vb["y"] + sy); time.sleep(0.3)
    sel = pg.evaluate("() => [...window.__agentcad.app.selEdges]")
    assert len(sel) == 1 and "Extrude1/end" in sel[0], sel
    assert not pg.locator("#btn-fillet").is_disabled()
    shot("ui_edge")
    pg.keyboard.press("Escape")
    view(pg, "front"); time.sleep(0.3)
    sx, sy = pg.evaluate("() => window.__agentcad.viewer.project([20, 12, 10])")
    pg.mouse.move(vb["x"] + sx, vb["y"] + sy); time.sleep(0.2); pg.mouse.click(vb["x"] + sx, vb["y"] + sy); time.sleep(0.3)
    assert pg.evaluate("() => window.__agentcad.app.pickedName") == "Extrude1/end"
    cmd(pg, "#btn-sketch"); time.sleep(0.5)             # 面を選んであれば、その面にすぐ描ける
    assert api.sketch_state(did)["plane"]["origin"] == [0.0, 0.0, 10.0]
    pg.click("#btn-sketch-end"); time.sleep(0.3)
    view(pg, "iso"); time.sleep(0.3)
    shot("ui_extruded")


def scenario_sketch_ux(pg, api, shot):
    """スケッチの使い勝手: 置く位置で水平/垂直が変わる寸法、円は直径、線のドラッグ、寸法の移動・ダブルクリック編集、エンティティ変換。"""
    pg.click("#btn-new-menu"); pg.click("#btn-newdoc"); time.sleep(0.3)
    did = pg.evaluate("() => window.__agentcad.app.doc")
    cmd(pg, "#btn-sketch"); pg.click("#sketch-menu button[data-plane=Front]"); time.sleep(0.4)
    ln = api.call("sketch.add", {"doc": did, "kind": "line", "points": [[0, 0], [30, 20]]})["created"][0]
    c = api.call("sketch.add", {"doc": did, "kind": "circle", "points": [[60, 10], [66, 10]]})["created"][0]
    pg.evaluate("async () => { await window.__agentcad.refresh(); window.__agentcad.sketch._fitSketch(); }"); time.sleep(0.4)
    vb = pg.locator("#view").bounding_box()

    def S(u, v):
        sx, sy = pg.evaluate(f"() => window.__agentcad.sketch.s({u}, {v})")
        return vb["x"] + sx, vb["y"] + sy

    def click(u, v):
        x, y = S(u, v); pg.mouse.move(x, y); time.sleep(0.08); pg.mouse.click(x, y); time.sleep(0.35)
    st = lambda: api.sketch_state(did)
    cons = lambda t: [k for k in st()["constraints"] if k["type"] == t]
    pg.keyboard.press("d")
    click(15, 10); x, y = S(15, 40); pg.mouse.move(x, y); time.sleep(0.2)
    shot("ui_dim_preview")
    pg.mouse.click(x, y); time.sleep(0.4)                              # 線の上に置く → 水平距離
    ed = pg.locator(".dim-editor"); ed.fill("35"); ed.press("Enter"); time.sleep(0.4)
    assert [k["value"] for k in cons("hdistance")] == [35], st()["constraints"]
    s = st(); P = {e["id"]: e for e in s["entities"]}; L = P[ln]
    mid = ((P[L["p1"]]["x"] + P[L["p2"]]["x"]) / 2, (P[L["p1"]]["y"] + P[L["p2"]]["y"]) / 2)
    click(*mid); click(-6, mid[1])                            # 線の横に置く → 垂直距離
    pg.locator(".dim-editor").press("Escape"); time.sleep(0.2)
    assert [round(k["value"], 6) for k in cons("vdistance")] == [20], st()["constraints"]
    click(66, 10); click(80, 28)                                        # 円 → 直径
    pg.locator(".dim-editor").press("Escape"); time.sleep(0.2)
    dia = cons("diameter")
    assert [k["value"] for k in dia] == [12] and dia[0]["label"] == pytest_approx([80 - 60, 28 - 10]), dia
    # 選択ツール: 線をドラッグ（平行移動）
    pg.keyboard.press("s")
    s = st(); P = {e["id"]: e for e in s["entities"]}; L = P[ln]
    p1 = (P[L["p1"]]["x"], P[L["p1"]]["y"])
    a = ((P[L["p1"]]["x"] * 3 + P[L["p2"]]["x"]) / 4, (P[L["p1"]]["y"] * 3 + P[L["p2"]]["y"]) / 4)
    x, y = S(*a); pg.mouse.move(x, y); pg.mouse.down()
    x2, y2 = S(a[0] + 10, a[1] - 6); pg.mouse.move(x2, y2, steps=8); pg.mouse.up(); time.sleep(0.6)
    s = st(); P = {e["id"]: e for e in s["entities"]}
    assert abs(P[L["p1"]]["x"] - (p1[0] + 10)) < 0.3 and abs(P[L["p1"]]["y"] - (p1[1] - 6)) < 0.3, (p1, P[L["p1"]])
    assert api.call("doc.state", {"doc": did, "mesh": False})["undo"] == "線をドラッグ"
    # 寸法の文字をドラッグして移動 → ダブルクリックで値の編集
    x, y = S(80, 28); pg.mouse.move(x, y); pg.mouse.down(); pg.mouse.move(x + 40, y + 10, steps=5); pg.mouse.up(); time.sleep(0.5)
    lab = cons("diameter")[0]["label"]
    assert lab[0] > 21 and api.call("doc.state", {"doc": did, "mesh": False})["undo"] == "寸法を移動", lab
    x, y = S(60 + lab[0], 10 + lab[1]); pg.mouse.dblclick(x, y); time.sleep(0.3)
    ed = pg.locator(".dim-editor"); assert ed.input_value() == "12"
    ed.fill("16"); ed.press("Enter"); time.sleep(0.4)
    r = next(e for e in st()["entities"] if e["id"] == c)["radius"]; assert abs(r - 8) < 1e-6, (r, cons("diameter"))
    shot("ui_sketch_dims")
    # エンティティ変換: 円を押し出し → 上面にスケッチ → 面をクリック → 変換
    cmd(pg, "#btn-extrude"); time.sleep(0.8)
    st0 = api.call("sketch.new", {"doc": did, "plane": {"face": "Extrude1/end"}})
    pg.evaluate("() => window.__agentcad.refresh()"); time.sleep(0.5)
    pg.keyboard.press("s")
    click(60, 10)                                                       # 面の中（スケッチ要素は無い）
    assert pg.evaluate("() => [...window.__agentcad.app.selEdges]") and not pg.locator("#btn-convert").is_disabled()
    cmd(pg, "#btn-convert"); time.sleep(0.6)
    s = st()
    assert [e["type"] for e in s["entities"] if e["type"] != "point"] == ["circle"] and s["solve"]["state"] == "full", s
    assert any(k["type"] == "on_edge" for k in s["constraints"])
    shot("ui_convert")
    pg.click("#btn-sketch-end"); time.sleep(0.3)


def scenario_sketch_u2(pg, api, shot):
    """U2: 中心矩形、線の中点・線上へのスナップ（拘束が付く）、範囲選択（左→右 / 右→左）、ミラー。"""
    pg.click("#btn-new-menu"); pg.click("#btn-newdoc"); time.sleep(0.3)
    did = pg.evaluate("() => window.__agentcad.app.doc")
    cmd(pg, "#btn-sketch"); pg.click("#sketch-menu button[data-plane=Front]"); time.sleep(0.4)
    ax = api.call("sketch.add", {"doc": did, "kind": "line", "points": ["ORIGIN", [0, 60]]})["created"][0]
    api.call("sketch.construction", {"doc": did, "ids": [ax]})
    base = api.call("sketch.add", {"doc": did, "kind": "line", "points": [[10, 0], [50, 0]]})["created"][0]
    pg.evaluate("async () => { await window.__agentcad.refresh(); window.__agentcad.sketch._fitSketch(); }"); time.sleep(0.4)
    vb = pg.locator("#view").bounding_box()

    def S(u, v):
        sx, sy = pg.evaluate(f"() => window.__agentcad.sketch.s({u}, {v})")
        return vb["x"] + sx, vb["y"] + sy

    def click(u, v):
        x, y = S(u, v); pg.mouse.move(x, y); time.sleep(0.08); pg.mouse.click(x, y); time.sleep(0.35)
    st = lambda: api.sketch_state(did)
    types = lambda: sorted(k["type"] for k in st()["constraints"])
    # 線: 中点から始めて、もう一度線の上で終える
    pg.keyboard.press("l")
    click(30.3, 0.2); click(30, 25); click(42, 0.3); pg.keyboard.press("Escape")
    assert types().count("midpoint") == 1 and types().count("point_on_line") == 1, types()
    assert types().count("vertical") == 2                                # 軸と、中点から真上に引いた線
    # 中心矩形
    cmd(pg, "#cmd button[data-tool=crect]")
    click(30, 45); click(40, 52)
    s = st()
    assert sum(e["type"] == "line" and e["construction"] for e in s["entities"]) == 3 and s["profiles"] >= 1, s["profiles"]
    pg.keyboard.press("s")
    shot("ui_snap_crect")
    # 範囲選択: 左→右は完全に入ったものだけ（矩形の 4 辺 + 対角 2 本 + 点）
    def drag(a, b):
        x, y = S(*a); pg.mouse.move(x, y); pg.mouse.down()
        x2, y2 = S(*b); pg.mouse.move((x + x2) / 2, (y + y2) / 2, steps=3); pg.mouse.move(x2, y2, steps=3)
    drag((15, 58), (45, 34)); shot("ui_box_select"); pg.mouse.up(); time.sleep(0.3)
    sel = pg.evaluate("() => [...window.__agentcad.sketch.sel]")
    E = {e["id"]: e for e in st()["entities"]}
    assert sum(E[i]["type"] == "line" for i in sel if i in E) == 6 and base not in sel, sel
    drag((45, 10), (38, -5)); pg.mouse.up(); time.sleep(0.3)                    # 右→左: かかった線も
    sel = pg.evaluate("() => [...window.__agentcad.sketch.sel]")
    assert base in sel and len([i for i in sel if E.get(i, {}).get("type") == "line"]) == 2, sel
    # ミラー: 全部を囲んで（軸の作図線も入る）→ ミラー
    api.call("sketch.delete", {"doc": did, "ids": [e["id"] for e in st()["entities"] if e["type"] == "line" and e["construction"] and e["id"] != ax]})
    pg.evaluate("() => window.__agentcad.refresh()"); time.sleep(0.4)
    n = sum(e["type"] == "line" for e in st()["entities"])
    drag((-3, 65), (56, -5)); pg.mouse.up(); time.sleep(0.3)
    pg.click("#btn-mirror"); time.sleep(0.6)
    s = st()
    assert sum(e["type"] == "line" for e in s["entities"]) == 2 * n - 1, (n, pg.inner_text("#toast"), pg.evaluate("() => [...window.__agentcad.sketch.sel]"))
    assert types().count("symmetric") >= 6 and s["solve"]["status"] == "ok" and not s["solve"]["redundant"], s["solve"]
    pg.evaluate("() => window.__agentcad.sketch._fitSketch()"); time.sleep(0.3)
    shot("ui_mirror")
    pg.click("#btn-sketch-end"); time.sleep(0.3)


def scenario_sketch_infer_pattern(pg, api, shot):
    """D61: 作図中の推測（直角・正接・平行が付く）と、直線・円形スケッチパターン（プレビュー → ✓）。"""
    pg.click("#btn-new-menu"); pg.click("#btn-newdoc"); time.sleep(0.3)
    did = pg.evaluate("() => window.__agentcad.app.doc")
    cmd(pg, "#btn-sketch"); pg.click("#sketch-menu button[data-plane=Front]"); time.sleep(0.4)
    api.call("sketch.add", {"doc": did, "kind": "line", "points": [[0, 0], [60, 60]]})        # 視野を決めるための線（あとで消す）
    api.call("sketch.add", {"doc": did, "kind": "line", "points": [[-20, 0], [-19, 0]]})
    pg.evaluate("async () => { await window.__agentcad.refresh(); window.__agentcad.sketch._fitSketch(); }"); time.sleep(0.4)
    api.call("sketch.delete", {"doc": did, "ids": [e["id"] for e in api.sketch_state(did)["entities"] if e["type"] == "line"]})
    pg.evaluate("() => window.__agentcad.refresh()"); time.sleep(0.3)
    vb = pg.locator("#view").bounding_box()

    def S(u, v):
        sx, sy = pg.evaluate(f"() => window.__agentcad.sketch.s({u}, {v})")
        return vb["x"] + sx, vb["y"] + sy

    def click(u, v):
        x, y = S(u, v); pg.mouse.move(x, y); time.sleep(0.08); pg.mouse.click(x, y); time.sleep(0.35)
    st = lambda: api.sketch_state(did)
    types = lambda: [k["type"] for k in st()["constraints"]]
    # 斜めの線 → 続けて、ほぼ直角に（2° ずれた所をクリック）→ 直角が付く
    pg.keyboard.press("l")
    click(0, 0); click(30, 15)
    d = (-15, 30); a = math.radians(2); q = (30 + d[0] * math.cos(a) - d[1] * math.sin(a), 15 + d[0] * math.sin(a) + d[1] * math.cos(a))
    x, y = S(*q); pg.mouse.move(x, y); time.sleep(0.2)
    assert pg.evaluate("() => window.__agentcad.sketch.hint") == "⊥", pg.evaluate("() => window.__agentcad.sketch.hint")
    shot("ui_infer_perp")
    pg.mouse.click(x, y); time.sleep(0.4); pg.keyboard.press("Escape")
    assert types().count("perpendicular") == 1, types()
    # 離れた所から、最初の線にほぼ平行に → 平行
    click(40, -10)
    x, y = S(40 + 30, -10 + 15 + 1); pg.mouse.move(x, y); time.sleep(0.2)
    assert pg.evaluate("() => window.__agentcad.sketch.hint") == "∥"
    pg.mouse.click(x, y); time.sleep(0.4); pg.keyboard.press("Escape")
    assert types().count("parallel") == 1, types()
    assert st()["solve"]["status"] == "ok" and not st()["solve"]["conflicting"]
    pg.keyboard.press("Escape")
    # 円を描いて、直線パターン（3 個、間隔 20、向き 90°）
    api.call("sketch.add", {"doc": did, "kind": "circle", "points": [[60, 0], [68, 0]]})
    pg.evaluate("() => window.__agentcad.refresh()"); time.sleep(0.3)
    cid = next(e["id"] for e in st()["entities"] if e["type"] == "circle")
    click(68, 0)
    assert pg.evaluate("() => [...window.__agentcad.sketch.sel]") == [cid], (pg.evaluate("() => [...window.__agentcad.sketch.sel]"), cid, pg.evaluate("() => window.__agentcad.sketch.tool"))
    cmd(pg, "#btn-slpattern"); time.sleep(0.3)
    assert pg.inner_text("#prop-title") == "直線スケッチパターン"
    pg.fill("#pat-angle", "90"); pg.dispatch_event("#pat-angle", "input"); time.sleep(0.2)
    shot("ui_spattern_preview")
    pg.click("#btn-pat-ok"); time.sleep(0.5)
    cs = [e for e in st()["entities"] if e["type"] == "circle"]
    assert len(cs) == 3, len(cs)
    # 円形パターン: 中心を最初の線の始点に（点を選ぶ → クリック）、6 個
    click(68, 40)
    c3 = pg.evaluate("() => [...window.__agentcad.sketch.sel]")
    assert len(c3) == 1
    cmd(pg, "#btn-scpattern"); time.sleep(0.3)
    pg.fill("#pat-count", "6"); pg.dispatch_event("#pat-count", "input")
    pg.click("#btn-pat-center"); click(30, 15)
    assert pg.evaluate("() => window.__agentcad.sketch.patternMode.center") != "ORIGIN"
    shot("ui_scpattern_preview")
    pg.click("#btn-pat-ok"); time.sleep(0.5)
    assert len([e for e in st()["entities"] if e["type"] == "circle"]) == 8
    # Esc で取り消せる
    click(68, 0); cmd(pg, "#btn-slpattern"); time.sleep(0.2); pg.keyboard.press("Escape"); time.sleep(0.2)
    assert pg.evaluate("() => window.__agentcad.sketch.patternMode") is None
    assert len([e for e in st()["entities"] if e["type"] == "circle"]) == 8
    pg.evaluate("() => window.__agentcad.sketch._fitSketch()"); time.sleep(0.3)
    shot("ui_spattern")
    pg.click("#btn-sketch-end"); time.sleep(0.3)


def scenario_section(pg, api, shot):
    """D62: 断面表示。右側面で切る → 切り口が塗られる（オレンジ）、距離・反対側、ピックは消した側を通り抜ける、やめる。"""
    pg.click("#btn-new-menu"); pg.click("#btn-new"); time.sleep(0.8)
    pg.keyboard.press("Control+7"); time.sleep(0.4)
    pg.click("#btn-section"); time.sleep(0.3)
    assert pg.is_visible("#section-panel") and pg.evaluate("() => !!window.__agentcad.viewer.section")
    pg.click("#section-panel [data-sec=Right]"); time.sleep(0.2)
    lo, hi = float(pg.get_attribute("#sec-range", "min")), float(pg.get_attribute("#sec-range", "max"))
    mid = (lo + hi) / 2
    pg.fill("#sec-offset", str(mid)); pg.dispatch_event("#sec-offset", "input"); time.sleep(0.3)
    sec = pg.evaluate("() => window.__agentcad.viewer.section")
    assert sec["normal"] == [1, 0, 0] and abs(sec["offset"] - mid) < 1e-6, sec

    def cap_pixels():
        return pg.evaluate("""() => { const v = window.__agentcad.viewer, gl = v.gl, w = gl.drawingBufferWidth, h = gl.drawingBufferHeight;
          v.capColor = [1, 0, 1]; v._draw(); v.capColor = null; const px = new Uint8Array(w * h * 4); gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);
          let n = 0; for (let i = 0; i < px.length; i += 4) if (px[i] > 230 && px[i + 1] < 30 && px[i + 2] > 230) n++;   // 試しに塗った色（マゼンタ）
          v.redraw(); return n; }""")
    n1 = cap_pixels()
    assert n1 > 500, n1                                  # 切り口が塗られている
    shot("ui_section")
    pg.check("#sec-flip"); time.sleep(0.3)
    sec2 = pg.evaluate("() => window.__agentcad.viewer.section")
    assert sec2["normal"] == [-1, 0, 0] and abs(sec2["offset"] + mid) < 1e-6
    # 等角の視点（+x 側から見る）では、反対側を表示すると切り口は奥を向いて見えない
    assert cap_pixels() < n1 / 4
    shot("ui_section_flip")
    pg.uncheck("#sec-flip"); time.sleep(0.2)
    # ハンドル: 矢印を法線の向きへドラッグ → 距離が増える
    vb = pg.locator("#view").bounding_box()
    G = lambda: pg.evaluate("""() => { const v = window.__agentcad.viewer, g = v._gizmoFrame();
      const P = (w) => v.project(w), at = (a, k) => g.c.map((x, i) => x + a[i] * k);
      return { s0: P(g.c), arrow: P(at(g.n, g.L * 0.6)), tip: P(at(g.n, g.L)), ring: P(at(g.v, g.r)), ring2: P(at(g.n, g.r)) }; }""")
    g = G()
    x, y = vb["x"] + g["arrow"][0], vb["y"] + g["arrow"][1]
    d = (g["tip"][0] - g["s0"][0], g["tip"][1] - g["s0"][1]); dl = math.hypot(*d)
    pg.mouse.move(x, y); time.sleep(0.15)
    assert pg.evaluate("() => window.__agentcad.viewer._gHover") == "move"
    pg.mouse.down(); pg.mouse.move(x + 40 * d[0] / dl, y + 40 * d[1] / dl, steps=5); pg.mouse.up(); time.sleep(0.3)
    sec3 = pg.evaluate("() => window.__agentcad.viewer.section")
    assert sec3["normal"] == [1, 0, 0] and sec3["offset"] > mid + 1, (sec3, mid)
    assert abs(float(pg.input_value("#sec-offset")) - sec3["offset"]) < 1e-3
    # 輪（u のまわり）を回す → 平面が傾く（正面・平面・右側面のどれでもなくなる）
    x, y = vb["x"] + g["ring"][0], vb["y"] + g["ring"][1]
    pg.mouse.move(x, y); time.sleep(0.15)
    hv = pg.evaluate("() => window.__agentcad.viewer._gHover")
    assert hv in ("rotU", "rotV"), hv
    s0 = (vb["x"] + g["s0"][0], vb["y"] + g["s0"][1])
    pg.mouse.down()
    for k in range(1, 6):                               # 中心のまわりに少しずつ回す
        t = math.atan2(y - s0[1], x - s0[0]) + 0.08 * k; r = math.hypot(x - s0[0], y - s0[1])
        pg.mouse.move(s0[0] + r * math.cos(t), s0[1] + r * math.sin(t))
    pg.mouse.up(); time.sleep(0.3)
    n = pg.evaluate("() => window.__agentcad.viewer.section.normal")
    assert abs(n[0]) < 0.999 and abs(math.hypot(*n) - 1) < 1e-9, n
    assert not pg.locator("#section-panel [data-sec].on").count()
    assert cap_pixels() > 300
    shot("ui_section_gizmo")
    pg.click("#sec-ok"); time.sleep(0.2)
    assert not pg.is_visible("#section-panel") and pg.evaluate("() => !!window.__agentcad.viewer.section")
    assert not pg.evaluate("() => window.__agentcad.viewer.sectionGizmo")
    pg.click("#btn-section"); time.sleep(0.2)                # 閉じていれば開き直す
    assert pg.is_visible("#section-panel") and pg.evaluate("() => window.__agentcad.viewer.sectionGizmo")
    pg.click("#btn-section"); time.sleep(0.2)                # 開いているときに押すとやめる
    assert pg.evaluate("() => window.__agentcad.viewer.section") is None and cap_pixels() == 0


def scenario_agent(pg, api, shot):
    """D67: AI パネル。台本どおりに答える偽のモデルで、依頼 → 新しいパート → 画像で確認 → 断面の依頼 → 返事 を画面で確かめる。"""
    from cad.agent.providers import ToolCall, Turn

    class Scripted:
        def __init__(self, turns):
            self.turns = list(turns)

        def add_user(self, text, images=()):
            self.last_user = (text, images)

        def add_tool_results(self, outs):
            pass

        def step(self, system, tools, on_text=lambda s: None, on_thinking=lambda s: None):
            t = self.turns.pop(0)
            if t.text:
                on_text(t.text)
            return t
    turns = [
        Turn(text="L 字の台を作ります。", calls=[ToolCall("1", "cad_call", {"method": "doc.new", "params": {"name": "AIPart"}})], stop="tool_use"),
        Turn(calls=[ToolCall("2", "cad_batch", {"calls": [
            {"method": "sketch.new", "params": {"plane": {"plane": "Front"}}},
            {"method": "sketch.add", "params": {"kind": "polyline", "points": [[0, 0], [60, 0], [60, 10], [10, 10], [10, 40], [0, 40]]}},
            {"method": "sketch.end"},
            {"method": "feature.add", "params": {"type": "extrude", "sketch": "Sketch1", "distance": 30}}]})], stop="tool_use"),
        Turn(calls=[ToolCall("3", "render_view", {"view": "iso", "width": 360, "height": 260}),
                    ToolCall("4", "ui_action", {"action": "section", "args": {"plane": "Front", "offset": 15}})], stop="tool_use"),
        Turn(text="**60×40×30** の L 字の台を作り、中央で断面を表示しました。", stop="end_turn"),
    ]
    api.agent.provider_factory = lambda cfg: Scripted(turns)
    cmd(pg, "#btn-ai"); time.sleep(0.3)
    assert pg.is_visible("#ai") and pg.evaluate("() => document.body.classList.contains('ai-open')")
    if pg.is_visible("#ai-settings"):                         # キー未設定なら設定が開く: キーを入れて保存
        pg.fill("#ai-settings [name=api_key]", "sk-test-0000"); pg.click("#ai-settings button.primary"); time.sleep(0.4)
    assert not pg.is_visible("#ai-settings")
    before = pg.evaluate("() => window.__agentcad.app.doc")
    pg.fill("#ai-input", "L 字の台を作って、断面を見せて"); pg.press("#ai-input", "Enter")
    for _ in range(60):
        time.sleep(0.25)
        if not pg.evaluate("() => document.querySelector('#ai').classList.contains('running')") and pg.locator("#ai .ai-msg.assistant").count() >= 2:
            break
    doc = pg.evaluate("() => window.__agentcad.app.doc")
    assert doc != before and api.ws.names[doc] == "AIPart", (doc, before)
    assert pg.locator("#ai .ai-render").count() == 1 and pg.locator("#ai .ai-tool").count() == 4
    sec = pg.evaluate("() => window.__agentcad.viewer.section")
    assert sec and sec["normal"] == [0, 0, 1] and abs(sec["offset"] - 15) < 1e-9, sec
    assert "断面を表示しました" in pg.inner_text("#ai-log") and pg.locator("#ai .ai-msg.assistant b").count() == 1
    time.sleep(1.2)                                          # 1 秒ごとの更新で形が出る
    shot("ui_agent")
    pg.click("#btn-section"); time.sleep(0.1); pg.click("#btn-section"); time.sleep(0.1)   # 断面を片付ける（開く → やめる）
    pg.click("#ai .ai-head button[title=閉じる]"); time.sleep(0.3)
    assert not pg.is_visible("#ai")


def scenario_part_ux(pg, api, shot):
    """F2 の画面: ツリーの平面 → スケッチ、✓ / ✗、押し出し状態、右クリックメニュー、名前の変更、参照平面、
    パターン（向きを 3D のエッジで指定）、表示スタイル、表示方向のキー、正対。"""
    pg.click("#btn-new-menu"); pg.click("#btn-newdoc"); time.sleep(0.4)
    did = pg.evaluate("() => window.__agentcad.app.doc")
    tree = lambda: {t["id"]: t for t in api.call("doc.state", {"doc": did, "mesh": False})["tree"]}
    # ツリーで「平面」を選んで スケッチ → その平面に描ける
    pg.click("#tree li.item.plane:has-text('平面')"); time.sleep(0.2)
    assert pg.evaluate("() => window.__agentcad.app.selPlane") == {"plane": "Top"}
    shot("ui_plane_selected")
    cmd(pg, "#btn-sketch"); time.sleep(0.5)
    assert api.sketch_state(did)["plane"]["normal"] == [0, 1, 0]
    api.call("sketch.add", {"doc": did, "kind": "rect", "points": [[0, 0], [80, -50]]})
    pg.evaluate("() => window.__agentcad.refresh()"); time.sleep(0.3)
    # 押し出し → ✓ / ✗ が出る。✗（Esc）で取り消すとフィーチャーが消える
    cmd(pg, "#btn-extrude"); pg.wait_for_selector("#btn-confirm")
    assert "Extrude1" in tree()
    pg.keyboard.press("Escape"); time.sleep(0.5)
    assert "Extrude1" not in tree() and pg.locator("#btn-confirm").count() == 0
    pg.click("#tree li.item[data-id='Sketch1']"); cmd(pg, "#btn-extrude"); pg.wait_for_selector("#btn-confirm")
    pg.select_option("#prop select[data-opt=end]", "mid"); time.sleep(0.5)
    st = api.call("doc.state", {"doc": did})
    assert [round(v, 3) for v in (st["mesh"]["bbox"][0][1], st["mesh"]["bbox"][1][1])] == [-5, 5], st["mesh"]["bbox"]
    v = pg.locator("#prop .param input.value").first; v.fill("20"); v.press("Enter"); time.sleep(0.5)
    assert pg.locator("#btn-confirm").count() == 1                     # 値を変えてもまだ確定前
    pg.locator("#view").focus() if False else pg.evaluate("() => document.activeElement.blur()")
    pg.keyboard.press("Enter"); time.sleep(0.3)
    assert pg.locator("#btn-confirm").count() == 0 and tree()["Extrude1"]["state"] == "ok"
    shot("ui_extrude_mid")
    # 右クリックメニュー → 名前を変更（F2 と同じ）
    pg.click("#tree li.item[data-id='Extrude1']", button="right"); pg.wait_for_selector("#ctx-menu:not([hidden])")
    shot("ui_tree_menu")
    pg.click("#ctx-menu button:has-text('名前を変更')")
    box = pg.locator("#tree input.rename"); box.fill("ベース"); box.press("Enter"); time.sleep(0.4)
    assert tree()["Extrude1"]["name"] == "ベース"
    # 上面にボス → 直線パターン（✓）→ 向きを 3D のエッジで指定
    api.call("sketch.new", {"doc": did, "plane": {"face": "Extrude1/end"}})
    api.call("sketch.add", {"doc": did, "kind": "circle", "points": [[10, -10], [14, -10]]})
    api.call("sketch.end", {"doc": did})
    pg.evaluate("() => window.__agentcad.refresh()"); time.sleep(0.3)
    pg.click("#tree li.item[data-id='Sketch2']"); cmd(pg, "#btn-extrude"); pg.wait_for_selector("#btn-confirm")
    pg.click("#btn-confirm"); time.sleep(0.3)
    assert not pg.locator("#btn-lpattern").is_disabled()               # 押し出しを選んでいるのでパターンにできる
    cmd(pg, "#btn-lpattern"); pg.wait_for_selector("#btn-confirm"); time.sleep(0.4)
    assert tree()["LPattern1"]["state"] == "ok"
    c = pg.locator("#prop .param:has(label:has-text('個数')) input.value").first; c.fill("3"); c.press("Enter"); time.sleep(0.5)
    vol3 = api.call("doc.state", {"doc": did, "mesh": False})["volume"]
    pg.click("#prop button[data-pick=direction]"); time.sleep(0.3)
    assert "エッジをクリック" in pg.inner_text("#status-pick")
    vb = pg.locator("#view").bounding_box()
    sx, sy = pg.evaluate("() => window.__agentcad.viewer.project([80, 10, 25])")       # 板の右上の辺（Z 方向）
    pg.mouse.move(vb["x"] + sx, vb["y"] + sy); time.sleep(0.2); pg.mouse.click(vb["x"] + sx, vb["y"] + sy); time.sleep(0.6)
    p = api.call("feature.params", {"doc": did, "id": "LPattern1"})
    assert "edge" in p["options"]["direction"], p["options"]
    assert tree()["LPattern1"]["state"] == "ok" and abs(api.call("doc.state", {"doc": did, "mesh": False})["volume"] - vol3) < 1e-6
    pg.click("#btn-confirm"); time.sleep(0.2)
    shot("ui_pattern")
    # 参照平面: 面を選んで → 参照平面。ツリーに出て、選ぶと 3D に表示される
    sx, sy = pg.evaluate("() => window.__agentcad.viewer.project([60, 10, 40])")
    pg.mouse.click(vb["x"] + sx, vb["y"] + sy); time.sleep(0.3)
    assert pg.evaluate("() => window.__agentcad.app.pickedName").startswith("Extrude1/end")   # 簡易カーネルは重なった面に #n を付ける
    assert "面積 4000" in pg.inner_text("#status-pick"), pg.inner_text("#status-pick")
    cmd(pg, "#btn-plane"); pg.wait_for_selector("#btn-confirm"); pg.click("#btn-confirm"); time.sleep(0.3)
    assert tree()["Plane1"]["state"] == "ok" and pg.evaluate("() => window.__agentcad.app.selPlane") == {"feature": "Plane1"}
    shot("ui_ref_plane")
    # 表示スタイルと表示方向（Ctrl+1 = 正面、正対 = 選んだ平面）
    pg.click("#btn-views"); time.sleep(0.2); assert_menu_visible(pg, "#views-menu"); shot("ui_views_menu")
    pg.keyboard.press("Escape"); pg.mouse.click(5, 300); time.sleep(0.1)
    pg.click("#tree li.item.plane:has-text('正面')"); cmd(pg, "#btn-sketch", modifiers=["Alt"]); time.sleep(0.2)
    assert_menu_visible(pg, "#sketch-menu"); pg.mouse.click(5, 300); time.sleep(0.1)
    pg.click("#tree li.item[data-id='Plane1']"); time.sleep(0.3)        # 正対の対象を参照平面に戻す
    pg.click("#btn-style"); pg.click("#style-menu [data-style=hlr]"); time.sleep(0.3); shot("ui_style_hlr")
    pg.click("#btn-style"); pg.click("#style-menu [data-style=wire]"); time.sleep(0.2); shot("ui_style_wire")
    pg.click("#btn-style"); pg.click("#style-menu [data-style=shaded_edges]")
    pg.evaluate("() => document.activeElement.blur()")
    pg.keyboard.press("Control+1"); time.sleep(0.5)
    assert pg.evaluate("() => [Math.round(window.__agentcad.viewer.yaw) % 360, Math.round(window.__agentcad.viewer.pitch)]") == [0, 0]
    pg.click("#btn-normal"); time.sleep(0.5)                           # Plane1（上向き）に正対
    assert pg.evaluate("() => Math.round(window.__agentcad.viewer.pitch)") == 90
    view(pg, "iso"); time.sleep(0.4)
    # 3D の右クリックメニュー（面の上）
    sx, sy = pg.evaluate("() => window.__agentcad.viewer.project([60, 10, 40])")
    pg.mouse.click(vb["x"] + sx, vb["y"] + sy, button="right"); pg.wait_for_selector("#ctx-menu:not([hidden])")
    assert "この面にスケッチ" in pg.inner_text("#ctx-menu")
    shot("ui_view_menu")
    pg.keyboard.press("Escape"); time.sleep(0.2)
    assert pg.locator("#ctx-menu").is_hidden()


def scenario_origin_dims(pg, api, shot):
    """原点から矩形を寸法で位置決めする: 原点と辺を選ぶ → 付けられる拘束だけが出る → D → 置く → 値。
    角の点と原点は、置く位置で水平距離・垂直距離になる。"""
    pg.click("#btn-new-menu"); pg.click("#btn-newdoc"); time.sleep(0.4)
    did = pg.evaluate("() => window.__agentcad.app.doc")
    pg.click("#tree li.item.plane:has-text('正面')"); cmd(pg, "#btn-sketch"); time.sleep(0.5)
    assert "拘束・寸法の付け方" in pg.inner_text("#prop")
    rect = api.call("sketch.add", {"doc": did, "kind": "rect", "points": [[10, 5], [50, 25]]})["created"]
    pg.evaluate("async () => { await window.__agentcad.refresh(); window.__agentcad.sketch._fitSketch(); window.__agentcad.viewer.dist *= 1.6; window.__agentcad.viewer.redraw(); }")
    time.sleep(0.4)
    vb = pg.locator("#view").bounding_box()

    def S(u, v):
        sx, sy = pg.evaluate(f"() => window.__agentcad.sketch.s({u}, {v})")
        return vb["x"] + sx, vb["y"] + sy

    def click(u, v, ctrl=False):
        x, y = S(u, v); pg.mouse.move(x, y); time.sleep(0.08)
        if ctrl: pg.keyboard.down("Control")
        pg.mouse.click(x, y)
        if ctrl: pg.keyboard.up("Control")
        time.sleep(0.3)
    st = lambda: api.sketch_state(did)
    pts = lambda: {e["id"]: e for e in st()["entities"]}
    # 原点 + 左の辺（x = 10）を選ぶ
    click(0, 0)
    click(10, 15, ctrl=True)
    assert pg.evaluate("() => [...window.__agentcad.sketch.sel]") == ["ORIGIN", rect[3]]
    cons = pg.eval_on_selector_all("#prop button[data-con]", "bs => bs.map((b) => b.dataset.con)")
    assert cons == ["coincident", "midpoint"], cons                    # 点と線に付けられるものだけ
    shot("ui_origin_selected")
    pg.click("#btn-dim-sel"); time.sleep(0.2)
    x, y = S(5, 30); pg.mouse.move(x, y); time.sleep(0.2); shot("ui_origin_dim_preview")
    pg.mouse.click(x, y); time.sleep(0.4)
    ed = pg.locator(".dim-editor"); ed.fill("20"); ed.press("Enter"); time.sleep(0.4)
    P = pts(); L = P[rect[3]]
    assert abs(P[L["p1"]]["x"] - 20) < 1e-6 and abs(P[L["p2"]]["x"] - 20) < 1e-6, (P[L["p1"]], P[L["p2"]])
    # 角の点（下の辺の左端）と原点 → D を押してから選ぶ → 横に置くと垂直距離
    pg.keyboard.press("Escape"); pg.keyboard.press("d")
    corner = P[rect[0]]["p1"]
    click(P[corner]["x"], P[corner]["y"]); click(0, 0)
    click(-12, 2)
    ed = pg.locator(".dim-editor"); ed.fill("10"); ed.press("Enter"); time.sleep(0.4)
    P = pts()
    assert abs(P[corner]["y"] - 10) < 1e-6, P[corner]
    vd = [k for k in st()["constraints"] if k["type"] == "vdistance"]
    assert len(vd) == 1 and vd[0]["value"] == 10
    pg.keyboard.press("s")
    shot("ui_origin_dims")
    pg.click("#btn-sketch-end"); time.sleep(0.3)


def scenario_chamfer_shell(pg, api, shot):
    """面取り・シェルのボタンと、シェルの「取り除く面」を 3D で足す・外す流れ（簡易カーネルでは形状はエラーになる）。"""
    pg.click("#btn-new-menu"); pg.click("#btn-newdoc"); time.sleep(0.4)
    did = pg.evaluate("() => window.__agentcad.app.doc")
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Front"}})
    api.call("sketch.add", {"doc": did, "kind": "rect", "points": [[0, 0], [80, 50]]})
    api.call("feature.add", {"doc": did, "type": "extrude", "sketch": "Sketch1", "distance": 30})
    pg.evaluate("() => window.__agentcad.refresh()"); time.sleep(0.3)
    view(pg, "iso"); time.sleep(0.4)
    vb = pg.locator("#view").bounding_box()

    def at(p, button="left"):
        sx, sy = pg.evaluate(f"() => window.__agentcad.viewer.project({list(p)})")
        pg.mouse.move(vb["x"] + sx, vb["y"] + sy); time.sleep(0.15); pg.mouse.click(vb["x"] + sx, vb["y"] + sy, button=button); time.sleep(0.4)
    assert pg.locator("#btn-chamfer").is_disabled()
    at([40, 50, 30])                                                   # 上の面と奥の面の間のエッジ
    assert not pg.locator("#btn-chamfer").is_disabled()
    cmd(pg, "#btn-chamfer"); pg.wait_for_selector("#btn-confirm"); time.sleep(0.3)
    p = api.call("feature.params", {"doc": did, "id": "Chamfer1"})
    assert len(p["options"]["edges"]) == 1 and "対応していません" in p["message"]
    pg.click("#btn-cancel"); time.sleep(0.4)
    assert "Chamfer1" not in [t["id"] for t in api.call("doc.state", {"doc": did, "mesh": False})["tree"]]
    # シェル: 前の面を選んで → シェル → 右のパネルで上の面を足す → もう一度クリックで外す
    at([40, 25, 30])
    front = pg.evaluate("() => window.__agentcad.app.pickedName")
    cmd(pg, "#btn-shell"); pg.wait_for_selector("#btn-confirm"); time.sleep(0.3)
    faces = lambda: api.call("feature.params", {"doc": did, "id": "Shell1"})["options"]["faces"]
    assert faces() == [front]
    pg.click("#prop button[data-pick=faces]"); time.sleep(0.4)
    at([40, 50, 15])                                                   # 上の面（y = 50）
    assert len(faces()) == 2, faces()
    assert "選んでいます" in pg.inner_text("#prop")
    shot("ui_shell_faces")
    at([40, 50, 15])                                                   # もう一度 → 外す
    assert faces() == [front], faces()
    pg.keyboard.press("Escape"); time.sleep(0.3)                        # 選ぶのを終える（フィーチャーは残る）
    assert pg.evaluate("() => window.__agentcad.app.pickFor") is None
    pg.click("#btn-confirm"); time.sleep(0.3)
    assert [t["id"] for t in api.call("doc.state", {"doc": did, "mesh": False})["tree"]][-1] == "Shell1"


def scenario_sketch_u3(pg, api, shot):
    """U3 の作図ツール: 中心線、スロット、多角形（辺の数を聞く）、3 点円弧、正接円弧、フィレット、面取り、オフセット、延長。"""
    pg.click("#btn-new-menu"); pg.click("#btn-newdoc"); time.sleep(0.4)
    did = pg.evaluate("() => window.__agentcad.app.doc")
    pg.click("#tree li.item.plane:has-text('正面')"); cmd(pg, "#btn-sketch"); time.sleep(0.5)
    api.call("sketch.add", {"doc": did, "kind": "rect", "points": [[0, 0], [60, 40]]})
    pg.evaluate("async () => { await window.__agentcad.refresh(); window.__agentcad.sketch._fitSketch(); window.__agentcad.viewer.dist *= 2.2; window.__agentcad.viewer.redraw(); }")
    time.sleep(0.4)
    vb = pg.locator("#view").bounding_box()

    def S(u, v):
        sx, sy = pg.evaluate(f"() => window.__agentcad.sketch.s({u}, {v})")
        return vb["x"] + sx, vb["y"] + sy

    def click(u, v):
        x, y = S(u, v); pg.mouse.move(x, y); time.sleep(0.08); pg.mouse.click(x, y); time.sleep(0.35)

    def tool(name, value=None):
        cmd(pg, f"#cmd button[data-tool={name}]")
        if value is not None:
            box = pg.locator(".modal input"); box.wait_for(timeout=5000); box.fill(str(value)); box.press("Enter")
        time.sleep(0.2)
    st = lambda: api.sketch_state(did)
    kinds = lambda: sorted(e["type"] for e in st()["entities"] if e["type"] != "point")
    types = lambda: [k["type"] for k in st()["constraints"]]
    tool("fillet", 6); click(60, 40)                                   # 角の点をクリック
    assert kinds().count("arc") == 1 and "tangent" in types()
    tool("chamfer", 5); click(0, 40)
    assert kinds().count("line") == 5 and st()["profiles"] == 1
    tool("cline"); click(-20, -10); click(-20, 50); pg.keyboard.press("Escape")
    assert sum(e["type"] == "line" and e["construction"] for e in st()["entities"]) == 1
    tool("slot"); click(80, 10); click(110, 10); click(95, 16)
    assert kinds().count("arc") == 3 and st()["profiles"] == 2
    tool("polygon", 5); click(95, 45); click(105, 45)
    assert kinds().count("line") == 5 + 1 + 3 + 5 and st()["profiles"] == 3
    tool("arc3"); click(-60, 0); click(-40, 0); click(-50, 8)
    assert kinds().count("arc") == 4
    tool("line"); click(-60, 30); click(-45, 30); pg.keyboard.press("Escape")
    tool("tarc"); click(-45, 30); click(-35, 40); pg.keyboard.press("Escape")
    assert kinds().count("arc") == 5 and types().count("tangent") >= 6
    shot("ui_sketch_tools")
    # オフセット: スロットの 4 要素をクリックして、外側をクリック
    tool("offset", 3)
    for uv in ((95, 4), (95, 16), (116, 10), (74, 10)):
        click(*uv)
    picked = pg.evaluate("() => [...window.__agentcad.sketch.sel]")
    assert len(picked) == 4, (picked, pg.inner_text("#toast"))
    click(95, 25)
    assert kinds().count("arc") == 7 and st()["profiles"] == 4 and types().count("offset") == 2, (kinds().count("arc"), st()["profiles"], types().count("offset"))
    assert st()["solve"]["status"] == "ok" and not st()["solve"]["redundant"]
    # 延長: 線を引いて、矩形の辺まで伸ばす
    tool("line"); click(20, 20); click(40, 20); pg.keyboard.press("Escape")
    tool("extend"); click(38, 20)
    s = st(); P = {e["id"]: e for e in s["entities"]}
    last = [e for e in s["entities"] if e["type"] == "line"][-1]
    assert abs(P[last["p2"]]["x"] - 60) < 1e-6 and "延長" in api.call("doc.state", {"doc": did, "mesh": False})["undo"], \
        (P[last["p1"]], P[last["p2"]], pg.inner_text("#toast"), api.call("doc.state", {"doc": did, "mesh": False})["undo"])
    assert "延長" in pg.inner_text("#status-pick")                    # ツールの説明がステータスバーに出る
    # 右クリックメニュー
    pg.keyboard.press("Escape")
    x, y = S(30, -20); pg.mouse.click(x, y, button="right"); pg.wait_for_selector("#ctx-menu:not([hidden])")
    assert "スケッチ終了" in pg.inner_text("#ctx-menu")
    shot("ui_sketch_menu")
    pg.click("#ctx-menu button:has-text('スケッチ終了')"); time.sleep(0.4)
    assert api.sketch_state(did) is None


def pytest_approx(v, tol=0.5):
    class A:
        def __eq__(self, o):
            return all(abs(a - b) <= tol for a, b in zip(v, o))

        def __repr__(self):
            return f"≈{v}"
    return A()


def scenario_assembly(pg, api, shot):
    """E4: パート 2 つ → 新規アセンブリ → 部品を追加 → 面を 2 つ選んで一致 → ドラッグ（合致を保つ）→ Undo。"""
    def part(kind, pts, dist):
        pg.click("#btn-new-menu"); pg.click("#btn-newdoc"); time.sleep(0.3)
        cmd(pg, "#btn-sketch"); pg.click("#sketch-menu button[data-plane=Front]"); time.sleep(0.3)
        did = pg.evaluate("() => window.__agentcad.app.doc")
        api.call("sketch.add", {"doc": did, "kind": kind, "points": pts})       # 描画は E2 で確認済みなので API で
        cmd(pg, "#btn-extrude"); time.sleep(0.5)
        pg.locator("#prop .param input.value").first.fill(str(dist)); pg.keyboard.press("Enter"); time.sleep(0.4)
        return did
    part("rect", [[0, 0], [40, 20]], 10)
    part("circle", [[0, 0], [5, 0]], 15)
    pg.click("#btn-new-menu"); pg.click("#btn-newasm"); time.sleep(0.4)
    a = pg.evaluate("() => window.__agentcad.app.doc")
    opts = pg.eval_on_selector_all("#asm-add select option", "os => os.map(o => o.value)")
    for v in opts[-2:]:
        pg.select_option("#asm-add select", v); pg.click("#asm-add button"); time.sleep(0.4)
    st = api.call("doc.state", {"doc": a, "mesh": False})
    names = [c["id"] for c in st["components"]]
    assert len(names) == 2 and st["components"][0]["fixed"], st["components"]
    vb = pg.locator("#view").bounding_box()

    def click_world(p, ctrl=False):
        sx, sy = pg.evaluate(f"() => window.__agentcad.viewer.project({list(p)})")
        pg.mouse.move(vb["x"] + sx, vb["y"] + sy); time.sleep(0.15)
        if ctrl:
            pg.keyboard.down("Control")
        pg.mouse.click(vb["x"] + sx, vb["y"] + sy); time.sleep(0.25)
        if ctrl:
            pg.keyboard.up("Control")
    pin = next(c for c in st["components"] if c["id"] == names[1])
    px = pin["position"]
    # 合致（一般的な 3D CAD の手順）: 合致 → 面を 2 つ → 既定の種類で付いて部品が動く → ポップアップで距離に変える → ✗ で元に戻る
    cmd(pg, "#btn-mate"); time.sleep(0.3)
    assert pg.evaluate("() => window.__agentcad.asm.mateMode") and "合致設定のエンティティ" in pg.inner_text("#prop")
    click_world([20, 10, 10])                                        # ブロックの上面
    click_world([px[0], px[1], px[2] + 15]); time.sleep(0.5)         # ピンの上面 → 一致が付く（プレビュー）
    assert pg.locator("#mate-popup").is_visible()
    st = api.call("doc.state", {"doc": a, "mesh": False})
    assert [m["type"] for m in st["mates"]] == ["coincident"], st["mates"]
    assert pg.locator("#mate-popup button[data-mate=coincident]").get_attribute("class") == "on"
    pg.click("#mate-popup button[data-mate=distance]"); time.sleep(0.5)
    pg.fill("#pop-value", "5"); pg.press("#pop-value", "Enter"); time.sleep(0.5)
    st = api.call("doc.state", {"doc": a, "mesh": False})
    assert st["mates"][0]["type"] == "distance" and abs(st["mates"][0]["value"] - 5) < 1e-9, st["mates"]
    shot("ui_asm_mate_popup")
    pg.click("#mate-pop-cancel"); time.sleep(0.5)                    # ✗: 合致は消え、ピンは元の位置
    st = api.call("doc.state", {"doc": a, "mesh": False})
    assert not st["mates"] and next(c for c in st["components"] if c["id"] == names[1])["position"] == px
    assert not pg.locator("#mate-popup").is_visible() and pg.evaluate("() => window.__agentcad.asm.mateMode")
    # もう一度: 2 面 → 一致のまま ✓ → PropertyManager の ✓ で終える
    click_world([20, 10, 10])
    click_world([px[0], px[1], px[2] + 15]); time.sleep(0.5)
    sel = pg.evaluate("() => window.__agentcad.asm.sel.map(s => s.comp + '|' + s.face)")
    assert sel == [f"{names[0]}|Extrude1/end", f"{names[1]}|Extrude1/end"], sel
    shot("ui_asm_select")
    pg.click("#mate-pop-ok"); time.sleep(0.3)
    pg.click("#btn-mate-ok"); time.sleep(0.3)
    assert not pg.evaluate("() => window.__agentcad.asm.mateMode")
    st = api.call("doc.state", {"doc": a, "mesh": False})
    pin = next(c for c in st["components"] if c["id"] == names[1])
    assert st["mates"][0]["state"] == "ok" and pin["dof"] == 3, (st["mates"], pin)
    z0 = pin["position"][2]
    assert abs(z0 - 25) < 1e-6, z0                                   # 上面どうしを突き合わせ（ピンは裏返り、原点は z=25）
    # ピンの側面をドラッグ（面は z=10〜-5 の円筒。中ほどを掴む）
    view(pg, "iso"); time.sleep(0.3)
    sx, sy = pg.evaluate(f"() => window.__agentcad.viewer.project([{pin['position'][0]}, {pin['position'][1]}, 25])")
    pg.mouse.move(vb["x"] + sx, vb["y"] + sy); pg.mouse.down()
    pg.mouse.move(vb["x"] + sx - 120, vb["y"] + sy - 80, steps=10); pg.mouse.up(); time.sleep(0.8)
    st = api.call("doc.state", {"doc": a, "mesh": False})
    pin2 = next(c for c in st["components"] if c["id"] == names[1])
    assert abs(pin2["position"][2] - 25) < 1e-6 and abs(pin2["position"][0] - pin["position"][0]) > 1, pin2
    assert st["undo"] == "部品をドラッグ"
    shot("ui_asm")
    pg.keyboard.press("Control+z"); time.sleep(0.5)
    st = api.call("doc.state", {"doc": a, "mesh": False})
    assert next(c for c in st["components"] if c["id"] == names[1])["position"] == pin["position"]
    # 合致の編集（一覧で名前をクリック）→ 距離に変える → ✗ で元の一致に戻る
    pg.click("#asm-mates li .name.link >> nth=0"); time.sleep(0.5)
    assert pg.locator("#mate-popup").is_visible() and "合致を編集" in pg.inner_text("#prop-title")
    pg.click("#mate-popup button[data-mate=distance]"); time.sleep(0.5)
    assert api.call("doc.state", {"doc": a, "mesh": False})["mates"][0]["type"] == "distance"
    pg.click("#mate-pop-cancel"); time.sleep(0.6)
    st = api.call("doc.state", {"doc": a, "mesh": False})
    assert [m["type"] for m in st["mates"]] == ["coincident"] and not pg.evaluate("() => window.__agentcad.asm.mateMode")
    # 複製・直線パターン（部品）・干渉チェック
    n0 = len(st["components"])
    pg.click(f"#asm-comps li:has-text('{names[1]}') button[data-act=copy]"); time.sleep(0.5)
    assert len(api.call("doc.state", {"doc": a, "mesh": False})["components"]) == n0 + 1
    cmd(pg, "#btn-apattern"); time.sleep(0.3)
    pg.select_option("#pat-comp", names[1])
    pg.click("#prop button[data-axis=y]"); pg.fill("#pat-count", "3"); pg.press("#pat-count", "Tab")
    pg.fill("#pat-spacing", "30"); pg.press("#pat-spacing", "Tab")
    pg.click("#btn-pattern-ok"); time.sleep(0.6)
    st = api.call("doc.state", {"doc": a, "mesh": False})
    assert len(st["components"]) == n0 + 3 and sum(m["type"] == "lock" for m in st["mates"]) == 2, st["mates"]
    cmd(pg, "#btn-interference"); time.sleep(0.8)
    assert pg.locator("#interf-table, #interf-none").count() == 1
    shot("ui_asm_tools")


def scenario_sketch_ext(pg, api, shot):
    """ほかのスケッチの表示（使っていないスケッチ・選んだスケッチ）、3D でのクリック選択、点・線へのスナップ。"""
    pg.click("#btn-new-menu"); pg.click("#btn-newdoc"); time.sleep(0.4)
    did = pg.evaluate("() => window.__agentcad.app.doc")
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Front"}})
    api.call("sketch.add", {"doc": did, "kind": "rect", "points": [[0, 0], [40, 20]]})
    api.call("sketch.end", {"doc": did})
    pg.evaluate("() => window.__agentcad.refresh()"); time.sleep(0.3)
    view(pg, "front"); time.sleep(0.4)
    pg.evaluate("() => window.__agentcad.viewer.fit()"); time.sleep(0.3)
    vb = pg.locator("#view").bounding_box()

    def scr(p):
        sx, sy = pg.evaluate(f"() => window.__agentcad.viewer.project({list(p)})")
        return vb["x"] + sx, vb["y"] + sy
    assert pg.evaluate("() => window.__agentcad.app.state.sketches.map(s => s.shown)") == [True]
    x, y = scr([20, 20, 0]); pg.mouse.move(x, y); time.sleep(0.2); pg.mouse.click(x, y); time.sleep(0.4)   # 上の辺をクリック
    assert pg.evaluate("() => window.__agentcad.app.selected") == "Sketch1"
    shot("ui_sketch_shown")
    # 新しいスケッチで、Sketch1 の角（点）から下の辺（線上）へ線を引く
    cmd(pg, "#btn-sketch"); pg.click("#sketch-menu button[data-plane=Front]"); time.sleep(0.5)
    pg.keyboard.press("l"); time.sleep(0.2)
    for p in ([40, 20, 0], [10, 0, 0]):
        x, y = scr(p); pg.mouse.move(x, y); time.sleep(0.2); pg.mouse.click(x, y); time.sleep(0.5)
    pg.keyboard.press("Escape"); time.sleep(0.2)
    types = pg.evaluate("() => window.__agentcad.app.state.sketch.constraints.map(c => c.type)")
    assert "ext_point" in types and "ext_line" in types, types
    assert pg.evaluate("() => window.__agentcad.sketch.extHits.length") > 0      # 参照した後もほかのスケッチは見えている
    shot("ui_sketch_ext_snap")
    pg.keyboard.press("Control+Enter"); time.sleep(0.4)
    # 押し出すと Sketch1 は使われて隠れる。ツリーで選ぶと表示される
    api.call("feature.add", {"doc": did, "type": "extrude", "sketch": "Sketch1", "distance": 5})
    pg.evaluate("() => window.__agentcad.refresh()"); time.sleep(0.3)
    sk = lambda: pg.evaluate("() => window.__agentcad.app.state.sketches.find(s => s.id === 'Sketch1')")
    assert not sk()["shown"]
    pg.click("#tree li.item[data-id=Sketch1]"); time.sleep(0.3)
    assert pg.evaluate("() => window.__agentcad.visibleSketches().map(s => s.id)").count("Sketch1") == 1
    pg.click("#tree li.item[data-id=Sketch1]", button="right"); time.sleep(0.2)
    pg.click("#ctx-menu button:has-text('スケッチを表示')"); time.sleep(0.4)
    assert sk()["shown"]


def scenario_model_refs(pg, api, shot):
    """モデルの円の中心・エッジの中点へのスナップ、推測線、モデルの角を相手にしたスマート寸法（D54）。"""
    pg.click("#btn-new-menu"); pg.click("#btn-newdoc"); time.sleep(0.4)
    did = pg.evaluate("() => window.__agentcad.app.doc")
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Front"}})
    api.call("sketch.add", {"doc": did, "kind": "rect", "points": [[0, 0], [40, 20]]})
    api.call("sketch.add", {"doc": did, "kind": "circle", "points": [[60, 10], [65, 10]]})
    api.call("sketch.end", {"doc": did})
    api.call("feature.add", {"doc": did, "type": "extrude", "sketch": "Sketch1", "distance": 10})
    pg.evaluate("() => window.__agentcad.refresh()"); time.sleep(0.3)
    cmd(pg, "#btn-sketch"); pg.click("#sketch-menu button[data-plane=Front]"); time.sleep(0.6)
    vb = pg.locator("#view").bounding_box()

    def scr(p):
        sx, sy = pg.evaluate(f"() => window.__agentcad.viewer.project({list(p)})")
        return vb["x"] + sx, vb["y"] + sy

    def click(p, wait=0.5):
        x, y = scr(p); pg.mouse.move(x - 3, y - 3); pg.mouse.move(x, y); time.sleep(0.2); pg.mouse.click(x, y); time.sleep(wait)
    pg.keyboard.press("l"); time.sleep(0.2)
    x, y = scr([60, 10, 10]); pg.mouse.move(x, y); time.sleep(0.3)
    assert "中心" in (pg.evaluate("() => window.__agentcad.sketch.hint") or "")
    click([60, 10, 10])                                         # 円柱の中心から
    click([20, 0, 10])                                          # 下のエッジの中点へ
    pg.keyboard.press("Escape"); time.sleep(0.2)
    st = pg.evaluate("() => window.__agentcad.app.state.sketch")
    srcs = sorted(c["source"].split("@")[-1] for c in st["constraints"] if c["type"] == "ext_point")
    assert srcs == ["center", "mid"], srcs
    shot("ui_model_snaps")
    # 推測線: 正対していれば、既存の点と縦に揃う
    pg.keyboard.press("p"); time.sleep(0.2)
    x0, y0 = scr([40, 20, 10]); x1, _ = scr([40, 40, 10]); _, y1 = scr([40, 40, 10])
    pg.mouse.move(x0 + 3, y1); time.sleep(0.3)
    assert pg.evaluate("() => window.__agentcad.sketch.infer.length") >= 1
    pg.mouse.click(x0 + 3, y1); time.sleep(0.5)
    pts = [e for e in pg.evaluate("() => window.__agentcad.app.state.sketch.entities") if e["type"] == "point"]
    assert any(abs(p["x"] - 40) < 1e-6 and abs(p["y"] - 40) < 1e-3 for p in pts), pts
    pg.keyboard.press("Escape")
    # スマート寸法: モデルの角（端点）と、置いた点 → 値 15
    cmd(pg, "#cmd button[data-tool=dim]"); time.sleep(0.2)
    pid = next(p["id"] for p in pts if abs(p["y"] - 40) < 1e-3)
    click([40, 20, 10])                                         # モデルの角 → 参照の点ができる
    st = pg.evaluate("() => window.__agentcad.app.state.sketch")
    ref = [c for c in st["constraints"] if c["type"] == "ext_point" and c["source"].endswith("@vertex")]
    assert ref, st["constraints"]
    click([40, 40, 10])                                         # 置いた点
    x, y = scr([55, 30, 10]); pg.mouse.move(x, y); time.sleep(0.2); pg.mouse.click(x, y); time.sleep(0.6)
    pg.keyboard.type("15"); pg.keyboard.press("Enter"); time.sleep(0.6)
    st = pg.evaluate("() => window.__agentcad.app.state.sketch")
    dims = [c for c in st["constraints"] if c.get("value") is not None and ref[0]["refs"][0] in c["refs"] and pid in c["refs"]]
    assert dims and abs(dims[0]["value"] - 15) < 1e-9, st["constraints"]
    shot("ui_dim_to_model")
    pg.keyboard.press("Control+Enter"); time.sleep(0.4)


def scenario_select_measure(pg, api, shot):
    """選択フィルタ・ループ選択・別の選択・計測・質量特性（D55）。"""
    pg.click("#btn-new-menu"); pg.click("#btn-newdoc"); time.sleep(0.4)
    did = pg.evaluate("() => window.__agentcad.app.doc")
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Front"}})
    api.call("sketch.add", {"doc": did, "kind": "rect", "points": [[0, 0], [40, 20]]})
    api.call("sketch.end", {"doc": did})
    api.call("feature.add", {"doc": did, "type": "extrude", "sketch": "Sketch1", "distance": 10})
    pg.evaluate("() => window.__agentcad.refresh()"); time.sleep(0.3)
    view(pg, "iso"); time.sleep(0.4); pg.evaluate("() => window.__agentcad.viewer.fit()"); time.sleep(0.4)
    vb = pg.locator("#view").bounding_box()

    def scr(p):
        sx, sy = pg.evaluate(f"() => window.__agentcad.viewer.project({list(p)})")
        return vb["x"] + sx, vb["y"] + sy

    def click(p, **kw):
        x, y = scr(p); pg.mouse.move(x - 2, y - 2); pg.mouse.move(x, y); time.sleep(0.15); pg.mouse.click(x, y, **kw); time.sleep(0.35)
    # ループ選択: 前面（z=10）の上のエッジ → 右クリック → ループを選択 → 4 本
    x, y = scr([20, 20, 10]); pg.mouse.move(x, y + 1); time.sleep(0.2)
    pg.mouse.click(x, y + 1, button="right"); time.sleep(0.3)
    pg.click("#ctx-menu button:has-text('ループを選択')"); time.sleep(0.3)
    n = pg.evaluate("() => window.__agentcad.app.selEdges.size")
    assert n == 4, n
    # 別の選択: 前面の中央 → 奥の面（背面）を選ぶ
    x, y = scr([20, 10, 10]); pg.mouse.move(x, y); time.sleep(0.2)
    pg.mouse.click(x, y, button="right"); time.sleep(0.3)
    pg.click("#ctx-menu button:has-text('別の選択')"); time.sleep(0.3)
    items = pg.locator("#ctx-menu button").all_inner_texts()
    assert len(items) >= 2 and "end" in items[0] and "start" in items[1], items
    pg.click("#ctx-menu button >> nth=1"); time.sleep(0.3)
    assert pg.evaluate("() => window.__agentcad.app.pickedName").endswith("start")
    # 選択フィルタ: 面だけ → エッジの近くをクリックしても面
    pg.click("#btn-filter"); pg.click("#filter-menu [data-filter=face]"); time.sleep(0.2)
    click([20, 20, 10])
    assert pg.evaluate("() => window.__agentcad.app.selEdges.size") == 0
    pg.click("#btn-filter"); pg.click("#filter-menu [data-filter=all]"); time.sleep(0.2)
    # 計測: 頂点 2 つ → 距離。平行なエッジ 2 本 → 距離
    cmd(pg, "#btn-measure"); time.sleep(0.2)
    click([0, 20, 10]); click([40, 0, 10])
    rows = pg.locator("#measure-table tr").all_inner_texts()
    assert any("44.721" in r for r in rows), rows
    shot("ui_measure")
    pg.mouse.click(vb["x"] + 15, vb["y"] + vb["height"] - 15); time.sleep(0.3)          # 何もない所 → 選び直し
    click([20, 20, 10]); click([20, 0, 10])
    rows = pg.locator("#measure-table tr").all_inner_texts()
    assert any("平行な線の距離" in r and "20" in r for r in rows), rows
    pg.keyboard.press("Escape"); time.sleep(0.2)
    assert pg.locator("#float-panel").is_hidden()
    cmd(pg, "#btn-massprops"); time.sleep(0.3)
    rows = pg.locator("#mass-table tr").all_inner_texts()
    assert any("8000" in r for r in rows) and any("(20, 10, 5)" in r for r in rows), rows
    shot("ui_massprops")


def scenario_hole(pg, api, shot):
    """穴ウィザード（D57）: 面を選ぶ → 穴ウィザード → 点を 2 つ → スケッチ終了 → 穴の設定（種類・サイズ）→ ✓。
    簡易カーネル（prism）は回転が無いので形はエラーになる。流れと設定だけを見る（形は実カーネルのテスト）。"""
    pg.click("#btn-new-menu"); pg.click("#btn-newdoc"); time.sleep(0.4)
    did = pg.evaluate("() => window.__agentcad.app.doc")
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Front"}})
    api.call("sketch.add", {"doc": did, "kind": "rect", "points": [[0, 0], [60, 40]]})
    api.call("sketch.end", {"doc": did})
    api.call("feature.add", {"doc": did, "type": "extrude", "sketch": "Sketch1", "distance": 10})
    pg.evaluate("() => window.__agentcad.refresh()"); time.sleep(0.3)
    view(pg, "front"); time.sleep(0.4); pg.evaluate("() => window.__agentcad.viewer.fit()"); time.sleep(0.3)
    vb = pg.locator("#view").bounding_box()

    def click(p):
        sx, sy = pg.evaluate(f"() => window.__agentcad.viewer.project({list(p)})")
        pg.mouse.move(vb["x"] + sx - 2, vb["y"] + sy - 2); pg.mouse.move(vb["x"] + sx, vb["y"] + sy); time.sleep(0.15)
        pg.mouse.click(vb["x"] + sx, vb["y"] + sy); time.sleep(0.4)
    cmd(pg, "#btn-hole"); time.sleep(0.3)
    assert not pg.evaluate("() => window.__agentcad.app.state.sketch")          # 面を選んでいないので始まらない
    click([30, 20, 10])                                                      # 前面を選ぶ
    assert pg.evaluate("() => window.__agentcad.app.pickedName") == "Extrude1/end"
    cmd(pg, "#btn-hole"); time.sleep(0.5)
    assert pg.evaluate("() => window.__agentcad.sketch.tool") == "point"
    click([15, 20, 10]); click([45, 20, 10])
    pg.click("#btn-sketch-end"); time.sleep(1.0)
    st = pg.evaluate("() => window.__agentcad.app.state")
    hole = next(t for t in st["tree"] if t["type"] == "hole")
    assert pg.evaluate("() => window.__agentcad.app.pending") == hole["id"]
    assert pg.locator("#prop select[data-opt=kind]").count() == 1
    pg.select_option("#prop select[data-opt=size]", "M10"); time.sleep(0.5)
    p = api.call("feature.params", {"doc": did, "id": hole["id"]})
    assert {x["key"]: x["value"] for x in p["params"]}["cbore_diameter"] == 17.5
    shot("ui_hole")
    pg.keyboard.press("Enter"); time.sleep(0.3)


def scenario_sweep(pg, api, shot):
    """スイープ（D58）: 断面のスケッチを選んで「スイープ」→ 経路は自動で選ばれ、右のパネルで断面・経路・操作を選べる。
    簡易カーネルはスイープできないので形はエラー（形は実カーネルのテスト）。"""
    pg.click("#btn-new-menu"); pg.click("#btn-newdoc"); time.sleep(0.4)
    did = pg.evaluate("() => window.__agentcad.app.doc")
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Top"}})
    api.call("sketch.add", {"doc": did, "kind": "line", "points": [[0, 0], [20, 0]]})
    api.call("sketch.end", {"doc": did})
    api.call("sketch.new", {"doc": did, "plane": {"plane": "Right"}})
    api.call("sketch.add", {"doc": did, "kind": "circle", "points": [[0, 0], [2, 0]]})
    api.call("sketch.end", {"doc": did})
    pg.evaluate("() => window.__agentcad.refresh()"); time.sleep(0.3)
    pg.click("#tree li.item[data-id=Sketch2]"); time.sleep(0.3)
    cmd(pg, "#btn-sweep"); time.sleep(0.8)
    sw = pg.evaluate("() => window.__agentcad.app.pending")
    assert sw and sw.startswith("Sweep"), sw
    assert pg.locator("#prop select[data-opt=path]").input_value() == "Sketch1"
    assert pg.locator("#prop select[data-opt=profile]").input_value() == "Sketch2"
    assert pg.locator("#prop select[data-opt=op] option").count() == 3
    shot("ui_sweep")
    pg.keyboard.press("Enter"); time.sleep(0.3)


def scenario_drawing(pg, api, shot):
    """図面（D60）: 新規 → 図面（標準 3 面図）、縮尺、ビューの移動、等角投影図・投影図の追加、スマート寸法、隠線、PDF / DXF。"""
    import os
    import tempfile
    pg.click("#btn-new-menu"); pg.click("#btn-new"); time.sleep(0.6)                       # デモ部品
    part = pg.evaluate("() => window.__agentcad.app.doc")
    pg.click("#btn-new-menu"); pg.click("#btn-newdrw"); time.sleep(1.0)
    S = lambda: pg.evaluate("() => window.__agentcad.app.state")
    st = S()
    drw = st["doc"]
    assert st["kind"] == "drawing" and [v["label"] for v in st["views"]] == ["正面図", "平面図", "右側面図"]
    assert pg.locator("#sheet").is_visible() and pg.locator("#cm-tabs [data-tab=drawing]").is_visible()
    assert not pg.locator("#hud").is_visible() and "シート" in pg.inner_text("#drw-tree")
    vb = pg.locator("#sheet").bounding_box()

    def scr(x, y):
        sx, sy = pg.evaluate(f"() => window.__agentcad.drawing.S({x}, {y})")
        return vb["x"] + sx, vb["y"] + sy

    def click(x, y, wait=0.5):
        sx, sy = scr(x, y); pg.mouse.move(sx - 2, sy - 2); pg.mouse.move(sx, sy); time.sleep(0.1); pg.mouse.click(sx, sy); time.sleep(wait)
    # 縮尺を 1:1 → 1:2（右のパネル）
    assert st["sheet"]["scale_text"] == "1:1"
    pg.select_option("#drw-scale", "1:2"); time.sleep(0.6)
    st = S(); f = st["views"][0]
    assert st["sheet"]["scale_text"] == "1:2" and abs((f["bbox"][2] - f["bbox"][0]) - 50) < 1e-6
    # 正面図をドラッグ → 平面図・右側面図がそろったままついてくる
    cx, cy = f["x"] + 25, f["y"] + 15
    a, b = scr(cx, cy), scr(cx - 30, cy - 20)
    pg.mouse.move(*a); pg.mouse.down(); pg.mouse.move((a[0] + b[0]) / 2, (a[1] + b[1]) / 2); pg.mouse.move(*b); pg.mouse.up(); time.sleep(0.6)
    st = S(); f2, t2, r2 = st["views"]
    assert abs(f2["x"] - (f["x"] - 30)) < 0.5 and abs(t2["x"] - f2["x"]) < 1e-6 and abs(r2["y"] - f2["y"]) < 1e-6
    assert pg.evaluate("() => window.__agentcad.drawing.sel") == f2["id"] and "ビュー" in pg.inner_text("#prop-title")
    # 等角投影図を置く
    cmd(pg, "#btn-drw-view"); pg.click("#drw-view-menu [data-orient=iso]"); time.sleep(0.2)
    click(330, 210, 0.8)
    st = S()
    assert [v["label"] for v in st["views"]][-1] == "等角投影図"
    # 投影図: 右側面図を選んで、その右に（= 背面図）
    click(r2["x"] + (r2["bbox"][0] + r2["bbox"][2]) / 2, r2["y"] + 15)
    cmd(pg, "#btn-drw-proj"); time.sleep(0.2)
    click(r2["x"] + r2["bbox"][2] + 45, r2["y"] + 10, 0.8)
    st = S(); back = st["views"][-1]
    assert back["label"] == "背面図" and back["parent"] == r2["id"] and abs(back["y"] - r2["y"]) < 1e-6
    # スマート寸法: 正面図の下の辺（直線）→ 下に置く = 100。円 → 直径 30
    f = st["views"][0]
    cmd(pg, "#btn-drw-dim"); time.sleep(0.2)
    click(f["x"] + 15, f["y"]); click(f["x"] + 25, f["y"] - 12, 0.8)
    circ = next(e for e in f["edges"] if e["kind"] == "circle")
    click(f["x"] + circ["c"][0] + circ["r"], f["y"] + circ["c"][1]); click(f["x"] + circ["c"][0] + 22, f["y"] + circ["c"][1] + 16, 0.8)
    # 2 点（端点どうし）: 左下と左上 → 左に置く = 60
    click(f["x"], f["y"]); click(f["x"], f["y"] + 30); click(f["x"] - 12, f["y"] + 15, 0.8)
    st = S()
    assert [d["text"] for d in st["dims"]] == ["100", "Ø30", "60"], [d["text"] for d in st["dims"]]
    pg.keyboard.press("Escape"); time.sleep(0.2)
    assert pg.evaluate("() => window.__agentcad.drawing.tool") == "select"
    # 寸法を選んで Delete、Ctrl+Z で戻す
    d0 = st["dims"][2]
    click(d0["x"] + (d0["box"][0] + d0["box"][2]) / 2, d0["y"] + (d0["box"][1] + d0["box"][3]) / 2)
    assert pg.evaluate("() => window.__agentcad.drawing.sel") == d0["id"]
    pg.keyboard.press("Delete"); time.sleep(0.5)
    assert len(S()["dims"]) == 2
    pg.keyboard.press("Control+z"); time.sleep(0.5)
    assert len(S()["dims"]) == 3
    # 隠線（背面図）と、モデルの変更への追従（幅 100 → 120）
    pg.click(f"#drw-tree li[data-id={back['id']}]"); time.sleep(0.2)
    pg.check("#drw-view-hidden"); time.sleep(0.6)
    assert S()["views"][-1]["hidden"]
    api.call("variable.set", {"doc": part, "name": "width", "expr": "120"})
    pg.evaluate("() => window.__agentcad.refresh()"); time.sleep(0.6)
    assert S()["dims"][0]["text"] == "120"
    shot("ui_drawing")
    # 書き出し（ファイルダイアログが無い環境: パスの入力欄）
    out = tempfile.mkdtemp()
    for btn, name, head in (("#btn-drw-pdf", "d.pdf", b"%PDF"), ("#btn-drw-dxf", "d.dxf", b"0\nSECTION"), ("#btn-drw-dxf-real", "r.dxf", b"0\nSECTION")):
        cmd(pg, btn); time.sleep(0.4)
        pg.fill(".modal input", os.path.join(out, name)); pg.keyboard.press("Enter"); time.sleep(0.8)
        assert open(os.path.join(out, name), "rb").read().startswith(head), name
    # パートへ戻ると 3D の画面に戻る
    pg.select_option("#doc-select", part); time.sleep(0.6)
    assert not pg.locator("#sheet").is_visible() and pg.locator("#hud").is_visible()
    pg.select_option("#doc-select", drw); time.sleep(0.6)
    assert pg.locator("#sheet").is_visible() and len(S()["views"]) == 5
    pg.select_option("#doc-select", part); time.sleep(0.5)          # 次の確認はパートから始める


def scenario_exchange(pg, api, shot):
    """書き出し（保存 ▾ → STL / 3MF / OBJ、細かさ）と、メッシュのファイルを開く（取り込んだパート）。D71。"""
    import zipfile
    pg.click("#btn-new-menu"); pg.click("#btn-new"); time.sleep(0.8)          # デモ部品
    pg.click("#btn-save-menu"); time.sleep(0.2)
    assert_menu_visible(pg, "#save-menu")
    shot("ui_export_menu")
    pg.click("#save-menu [data-quality=fine]"); time.sleep(0.1)               # 細かさを選んでもメニューは閉じない
    assert pg.locator("#save-menu [data-quality=fine]").get_attribute("class") == "on"
    out = tempfile.mkdtemp()
    for fmt in ("3mf", "stl", "obj"):
        if pg.locator("#save-menu").is_hidden():
            pg.click("#btn-save-menu"); time.sleep(0.2)
        pg.click(f"#save-menu [data-export='{fmt}']"); time.sleep(0.3)
        pg.fill(".modal input", os.path.join(out, "demo")); pg.keyboard.press("Enter"); time.sleep(1.0)
        assert os.path.getsize(os.path.join(out, "demo." + fmt)) > 0, fmt
    assert zipfile.ZipFile(os.path.join(out, "demo.3mf")).namelist()
    assert os.path.exists(os.path.join(out, "demo.mtl"))
    # 開く → STL: インポートのフィーチャーだけのパートになる
    pg.click("#btn-open"); time.sleep(0.3)
    pg.fill(".modal input", os.path.join(out, "demo.stl")); pg.keyboard.press("Enter"); time.sleep(1.5)
    st = pg.evaluate("() => window.__agentcad.app.state")
    assert st["name"] == "demo" and [t["type"] for t in st["tree"]] == ["import"] and st["tree"][0]["state"] == "ok", st["tree"]
    assert len(st["mesh"]["face_names"]) >= 9
    view(pg, "iso")
    shot("ui_import_stl")


def scenario_windows(pg, api, shot, url):
    """文書ごとのウィンドウ: ?doc= で開く。ほかのウィンドウでの変更が反映される。"""
    did = pg.evaluate("() => window.__agentcad.app.doc")
    other = api.call("doc.new", {"name": "Other"})["doc"]
    pg2 = pg.context.new_page()
    pg2.goto(f"{url}?doc={other}"); pg2.wait_for_selector("body[data-ready='1']", timeout=15000)
    assert pg2.evaluate("() => window.__agentcad.app.doc") == other and pg2.title().startswith("Other")
    n = len(api.call("doc.state", {"doc": did, "mesh": False})["tree"])
    pg2.evaluate(f"() => fetch('/rpc', {{method: 'POST', body: JSON.stringify({{id: 1, method: 'sketch.new', params: {{doc: '{did}', plane: {{plane: 'Top'}}, _client: 'w2'}}}})}})")
    time.sleep(1.6)                                         # 1 秒ごとに確かめて取り直す
    assert len(pg.evaluate("() => window.__agentcad.app.state.tree")) == n + 1
    api.call("sketch.end", {"doc": did})
    with pg.expect_popup() as pop:                          # ブラウザでは「⧉」→ 文書 → 新しいウィンドウ
        pg.click("#btn-window"); pg.click(f"#ctx-menu button:has-text('Other')")
    w = pop.value; w.wait_for_selector("body[data-ready='1']", timeout=15000)
    assert w.evaluate("() => window.__agentcad.app.doc") == other
    w.close(); pg2.close()


def scenario_save_open(pg, api, shot):
    """S: アセンブリを USD で保存（未保存のパートも一緒に）→ 変更マーク → 開き直す。"""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="agentcad_ui_")
    os.environ["AGENTCAD_NO_DIALOG"] = "1"            # OS のダイアログは出さず、画面内の入力欄を使う

    def answer(text):
        box = pg.locator(".modal input"); box.wait_for(timeout=5000); box.fill(text); box.press("Enter")
    a = pg.evaluate("() => window.__agentcad.app.doc")
    assert "未保存" in pg.inner_text("#doc-title")
    pg.click("#btn-save"); shot_modal = pg.locator(".modal"); shot_modal.wait_for(timeout=5000); shot("ui_save_dialog")
    pg.keyboard.press("Escape"); time.sleep(0.2)   # 取り消し → 保存されない
    assert not os.listdir(tmp) and pg.locator(".modal").count() == 0
    pg.click("#btn-save"); answer(os.path.join(tmp, "asm")); time.sleep(0.6)
    assert "保存しました" in pg.inner_text("#toast") and "部品 2 件も保存" in pg.inner_text("#toast"), pg.inner_text("#toast")
    files = sorted(os.listdir(tmp))
    assert files == ["Part.usda", "Part_2.usda", "asm.usda"], files
    assert pg.inner_text("#doc-title") == "asm"
    shot("ui_saved")
    api.call("asm.fix", {"doc": a, "comp": api.call("doc.state", {"doc": a, "mesh": False})["components"][1]["id"]})
    pg.click("#btn-save"); time.sleep(0.5)          # パスが決まっているので聞かない。変更の無いパートは書かない
    assert pg.locator(".modal").count() == 0 and "部品" not in pg.inner_text("#toast"), pg.inner_text("#toast")
    # 別の文書から .usda を開く（開いているものは同じ文書に切り替わる）
    pg.click("#btn-new-menu"); pg.click("#btn-newdoc"); time.sleep(0.3)
    pg.keyboard.press("Control+o"); answer(os.path.join(tmp, "asm.usda")); time.sleep(0.6)
    assert pg.evaluate("() => window.__agentcad.app.doc") == a and pg.inner_text("#doc-title") == "asm"
    shot("ui_opened")


def scenario_appearance_open(pg, api, shot):
    """D69: ファイルを開く間の進み具合と、外観（右クリック → 色見本 → 面の色が変わる）。"""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="agentcad_ui_")
    did = api.call("doc.new", {"name": "Colored"})["doc"]
    call = lambda m, **p: api.call(m, {"doc": did, **p})
    call("sketch.new", plane={"plane": "Front"}); call("sketch.add", kind="rect", points=[[0, 0], [60, 30]]); call("sketch.end")
    call("feature.add", type="extrude", sketch="Sketch1", distance=20)
    call("sketch.new", plane={"plane": "Front"}); call("sketch.add", kind="rect", points=[[10, 30], [30, 50]]); call("sketch.end")
    call("feature.add", type="extrude", sketch="Sketch2", distance=10)
    path = os.path.join(tmp, "colored.usda")
    call("doc.save", path=path); api.call("doc.close", {"doc": did})
    # 開くのを遅くして、進み具合のダイアログを見る（簡易カーネルでは一瞬で開くので）
    orig = api.methods["doc.open"]

    def slow_open(path):
        api.ws.progress = {"title": "colored を開いています", "step": "Extrude1", "done": 0, "total": 2}
        time.sleep(1.2)
        api.ws.progress = None
        return orig(path)
    api.methods["doc.open"] = slow_open

    def answer(text):
        box = pg.locator(".modal input"); box.wait_for(timeout=5000); box.fill(text); box.press("Enter")
    try:
        pg.keyboard.press("Control+o"); answer(path)
        pg.locator(".prog").wait_for(timeout=3000); time.sleep(0.3)
        assert "（1 / 2）" in pg.inner_text(".prog") and "colored を開いています" in pg.inner_text(".prog"), pg.inner_text(".prog")
        shot("ui_progress")
        pg.locator(".prog").wait_for(state="detached", timeout=5000)
    finally:
        api.methods["doc.open"] = orig
    time.sleep(0.4)
    assert pg.inner_text("#doc-title").startswith("colored")
    # フィーチャーの右クリック → 外観 → 赤 → その面が赤に
    pg.click("#tree li.item[data-id=Extrude2]", button="right"); time.sleep(0.1)
    pg.click("#ctx-menu button:has-text('外観')"); time.sleep(0.1)
    assert pg.locator("#ctx-menu .pal-sw").count() == 16
    pg.click("#ctx-menu .pal-sw[title='#b33a3a']"); time.sleep(0.5)
    m = pg.evaluate("() => { const m = window.__agentcad.app.mesh; return { n: m.faceNames, c: m.faceColors }; }")
    col = dict(zip(m["n"], m["c"]))
    assert all(c == "#b33a3a" for n, c in col.items() if n.startswith("Extrude2/")) and col["Extrude1/start"] != "#b33a3a", col
    # パート全体の色（3D ビューの何も無い所で右クリック）
    vb = pg.locator("#view").bounding_box()
    pg.mouse.click(vb["x"] + 30, vb["y"] + vb["height"] - 30, button="right"); time.sleep(0.1)
    pg.click("#ctx-menu button:has-text('パートの色')"); time.sleep(0.1)
    pg.click("#ctx-menu .pal-sw[title='#3f7fbf']"); time.sleep(0.5)
    st = api.call("doc.state", {"doc": pg.evaluate("() => window.__agentcad.app.doc"), "mesh": False})
    assert st["appearance"]["part"] == "#3f7fbf" and st["appearance"]["features"] == {"Extrude2": "#b33a3a"}, st["appearance"]
    shot("ui_appearance")


def main():
    api = Api(Workspace("prism"))
    H = make_handler(api, threading.Lock())
    H.log_message = lambda *a: None
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    errors = []
    try:
        with sync_playwright() as p:
            # Chrome の場所: 環境変数 CHROME > /usr/bin/google-chrome（クラウド環境）> playwright 同梱（Windows の実機）
            chrome = os.environ.get("CHROME") or ("/usr/bin/google-chrome" if os.path.exists("/usr/bin/google-chrome") else None)
            b = p.chromium.launch(executable_path=chrome,
                                  args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
            pg = b.new_context(viewport={"width": 1400, "height": 820}).new_page()
            pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            pg.on("pageerror", lambda e: errors.append(str(e)))
            shot = lambda name: pg.screenshot(path=os.path.join(OUT, name + ".png"))
            pg.goto(url); pg.wait_for_selector("body[data-ready='1']", timeout=15000)
            scenario_features(pg, shot)
            scenario_sketch(pg, api, shot)
            scenario_sketch_ux(pg, api, shot)
            scenario_sketch_u2(pg, api, shot)
            scenario_part_ux(pg, api, shot)
            scenario_sketch_u3(pg, api, shot)
            scenario_sketch_infer_pattern(pg, api, shot)
            scenario_section(pg, api, shot)
            scenario_agent(pg, api, shot)
            scenario_origin_dims(pg, api, shot)
            scenario_chamfer_shell(pg, api, shot)
            scenario_sketch_ext(pg, api, shot)
            scenario_model_refs(pg, api, shot)
            scenario_select_measure(pg, api, shot)
            scenario_hole(pg, api, shot)
            scenario_sweep(pg, api, shot)
            scenario_drawing(pg, api, shot)
            scenario_exchange(pg, api, shot)
            scenario_windows(pg, api, shot, url)
            scenario_assembly(pg, api, shot)
            scenario_save_open(pg, api, shot)
            scenario_appearance_open(pg, api, shot)
            b.close()
    finally:
        httpd.shutdown()
    assert not errors, errors
    print("UI 確認 OK:", OUT)


if __name__ == "__main__":
    main()
