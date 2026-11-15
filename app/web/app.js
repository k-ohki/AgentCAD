// 画面の組み立て（パート）。状態はすべて Python 側が持ち、ここは返ってきた状態を描くだけ（docs/SPEC.md 9 章）。
// 構成: core.js（土台）← windows.js / evaluate.js / sketch_ui.js / props.js ← asm_ui.js ← app.js（ここ）
import { ICONS, setTab, currentTab } from "./toolbar.js";
import { TOOLS } from "./sketch.js";
import { modelGeometry, edgeLoop, tangentChain, facesUnder, fmt } from "./select.js";
import { $, act, app, asm, decodeMesh, edgeAt, edgeLength, el, faceArea, featureOfFace,
  fmtLen, ICON, pickPath, rpc, rpcRaw, sameRef, showMenu, showPalette, withProgress, sketch, STATE_BADGE, toast, ui, viewer } from "./core.js";
import { drawMeasure, measureEnd, measurePick, renderMeasure, vertexAt } from "./evaluate.js";
import { renderSketchProps, renderSketchTools, sketchStatus } from "./sketch_ui.js";
import { clearProps, confirmPending, finishPick, loadProps } from "./props.js";
import { docTitle, presentDoc, refreshDocs } from "./windows.js";
import { drawing, drawingKey, renderDrawing } from "./drawing_ui.js";
import { initAgent, openAgent, toggleAgent } from "./agent_ui.js";
import { drawInterference, hideMatePopup, mateCancel, mateConfirm, mateEnter, mateExit, renderAsmProps, renderAssembly } from "./asm_ui.js";

Object.assign(viewer, {
  onHover: (i, pos) => {
    const F = (app.pickFor || sketch.active) ? "all" : app.filter;
    app.hoverVertex = pos && (F === "vertex" || (F === "all" && app.measure)) ? vertexAt(pos, i) : null;
    const e = pos && F !== "face" && F !== "vertex" && !app.hoverVertex ? edgeAt(pos, i) : null;
    app.hoverEdge = e;
    if (app.hoverVertex || (pos && ((F === "edge" && e === null) || F === "vertex"))) {   // フィルタで選べない面は光らせない
      viewer.setHover(-1); viewer.redraw();
      $("#status-pick").textContent = app.hoverVertex ? `頂点: (${app.hoverVertex.w.map((v) => fmt(v)).join(", ")})` : "";
      return;
    }
    app.hoverSketch = e === null && !app.pickFor ? sketchAt(pos) : null;
    if (app.hoverSketch) {
      viewer.setHover(-1); viewer.redraw();
      $("#status-pick").textContent = `スケッチ: ${app.state.sketches.find((s) => s.id === app.hoverSketch).name}（クリックで選択、右クリックで編集）`;
      return;
    }
    if (e !== null) viewer.setHover(-1);
    showPick(e !== null ? -1 : i, false);
    viewer.redraw();
  },
  onContext: (pos, ev) => viewContextMenu(pos, ev),
  onPick: (i, pos, ev) => {
    if (app.measure && !app.pickFor && !sketch.active) { measurePick(i, pos, ev); return; }
    const F = app.pickFor ? "all" : app.filter;
    const e = F === "face" || F === "vertex" ? null : edgeAt(pos, i);
    if (!app.pickFor && (F === "vertex" || (F === "edge" && e === null))) { viewer.setSelected(-1); app.selEdges.clear(); app.pickedName = null; updateButtons(); viewer.redraw(); return; }
    if (app.pickFor) { finishPick(e !== null ? { edge: app.mesh.edgeNames[e] } : i >= 0 && app.mesh ? { face: app.mesh.faceNames[i] } : null); return; }
    if (app.pending) { const id = app.pending; app.pending = null; if (app.selected === id) loadProps(id); }   // ほかの操作を始めたら確定
    const skHit = e === null ? sketchAt(pos) : null;
    if (skHit) {                                             // 3D に見えているスケッチをクリック → そのスケッチを選ぶ
      app.selEdges.clear(); viewer.setSelected(-1); app.pickedName = null;
      selectFeature(skHit, false); viewer.redraw(); return;
    }
    if (i >= 0 || e !== null) { app.selPlane = null; markPlaneRows(); }
    if (!ev?.ctrlKey) app.selEdges.clear();
    if (e !== null) {                                        // エッジを選ぶ（Ctrl で複数）
      const name = app.mesh.edgeNames[e];
      app.selEdges.has(name) ? app.selEdges.delete(name) : app.selEdges.add(name);
      viewer.setSelected(-1);
    } else {
      showPick(i, true);
      const name = i >= 0 && app.mesh ? app.mesh.faceNames[i] : null;
      if (name) selectFeature(featureOfFace(name), false);    // 面を選ぶと、それを作ったフィーチャーを選ぶ
    }
    showPick(-1, false);
    updateButtons();
    viewer.redraw();
  },
});

function drawEdgeHighlights() {
  const m = app.mesh;
  if (!m || (!app.selEdges.size && app.hoverEdge === null)) return;
  const ctx = $("#overlay").getContext("2d"), E = m.edges;
  for (let k = 0; k < m.edgeOfSegment.length; k++) {
    const ei = m.edgeOfSegment[k], sel = app.selEdges.has(m.edgeNames[ei]);
    if (!sel && ei !== app.hoverEdge) continue;
    const a = viewer.project([E[6 * k], E[6 * k + 1], E[6 * k + 2]]), b = viewer.project([E[6 * k + 3], E[6 * k + 4], E[6 * k + 5]]);
    if (!a || !b) continue;
    ctx.strokeStyle = sel ? "#ff8a00" : "#18a058"; ctx.lineWidth = 3; ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(...a); ctx.lineTo(...b); ctx.stroke();
  }
}
viewer.afterDraw = () => { sketch.draw(); drawSketches(); drawPlane(); drawEdgeHighlights(); drawMeasure(); drawInterference(); drawTriad(); };

/** 3D に描くスケッチ: 表示にしたもの（使われていないスケッチは既定で表示）と、ツリーで選んだもの（必ず表示・青）。
 *  スケッチ編集中は SketchEditor がほかのスケッチを描く（スナップ先）。 */
function visibleSketches() {
  const st = app.state;
  if (!st || st.kind !== "part" || st.sketch) return [];
  return (st.sketches || []).filter((s) => s.shown || s.id === app.selected);
}

function drawSketches() {
  const ctx = $("#overlay").getContext("2d");
  for (const s of visibleSketches()) {
    const sel = s.id === app.selected, hov = s.id === app.hoverSketch;
    ctx.strokeStyle = sel ? "#1e6fff" : hov ? "#18a058" : "#4a5b70";
    for (const c of s.curves) {
      const P = c.pts.map((p) => viewer.project(p));
      if (P.some((p) => !p)) continue;
      ctx.setLineDash(c.construction ? [7, 4] : []); ctx.lineWidth = sel || hov ? 2.2 : 1.3;
      ctx.beginPath(); P.forEach((p, i) => (i ? ctx.lineTo(...p) : ctx.moveTo(...p))); ctx.stroke();
    }
    ctx.setLineDash([]); ctx.fillStyle = ctx.strokeStyle;
    if (sel) for (const p of s.points) { const q = viewer.project(p.p); if (q) ctx.fillRect(q[0] - 2, q[1] - 2, 4, 4); }
  }
}

/** 画面位置の近く（6px 以内）に線が見えているスケッチの ID。 */
function sketchAt(pos) {
  if (!pos) return null;
  let best = null, bd = 6;
  for (const s of visibleSketches()) for (const c of s.curves) {
    const P = c.pts.map((p) => viewer.project(p));
    for (let i = 1; i < P.length; i++) {
      if (!P[i - 1] || !P[i]) continue;
      const a = P[i - 1], b = P[i], dx = b[0] - a[0], dy = b[1] - a[1], l = dx * dx + dy * dy;
      const t = l ? Math.max(0, Math.min(1, ((pos[0] - a[0]) * dx + (pos[1] - a[1]) * dy) / l)) : 0;
      const d = Math.hypot(a[0] + t * dx - pos[0], a[1] + t * dy - pos[1]);
      if (d < bd) { bd = d; best = s.id; }
    }
  }
  return best;
}

/** 選んだ平面（標準平面・参照平面）を半透明の四角で描く。 */
function drawPlane() {
  const st = app.state;
  if (!st || st.kind !== "part" || !app.selPlane) return;
  const p = (st.planes || []).find((x) => sameRef(x.ref, app.selPlane));
  if (!p) return;
  const R = Math.max(viewer.radius || 0, 25) * 1.15, c = app.mesh ? viewer.center : [0, 0, 0];
  // 四角の中心 = モデルの中心を平面に下ろした点
  const d = [0, 1, 2].reduce((a, i) => a + (c[i] - p.origin[i]) * p.normal[i], 0);
  const o = [0, 1, 2].map((i) => c[i] - d * p.normal[i]);
  const corner = (a, b) => viewer.project([0, 1, 2].map((i) => o[i] + a * R * p.x[i] + b * R * p.y[i]));
  const q = [corner(-1, -1), corner(1, -1), corner(1, 1), corner(-1, 1)];
  if (q.some((v) => !v)) return;
  const ctx = $("#overlay").getContext("2d");
  ctx.beginPath(); q.forEach((v, i) => (i ? ctx.lineTo(...v) : ctx.moveTo(...v))); ctx.closePath();
  ctx.fillStyle = "rgba(255,176,32,.13)"; ctx.fill();
  ctx.strokeStyle = "#d08a00"; ctx.lineWidth = 1.5; ctx.setLineDash([]); ctx.stroke();
  ctx.fillStyle = "#9a6500"; ctx.font = "12px Segoe UI, Yu Gothic UI, Meiryo, sans-serif"; ctx.textAlign = "left"; ctx.textBaseline = "bottom";
  ctx.fillText(p.name, q[3][0] + 4, q[3][1] - 3);
}

/** 画面左下の座標軸（一般的な 3D CAD のトライアド）。 */
function drawTriad() {
  const cam = viewer.cam; if (!cam) return;
  const cv = $("#overlay"), ctx = cv.getContext("2d"), L = 30, ox = 44, oy = cv.clientHeight - 62;
  const dot3 = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const axes = [["X", [1, 0, 0], "#d23c3c"], ["Y", [0, 1, 0], "#2f9a44"], ["Z", [0, 0, 1], "#2b62d9"]]
    .map(([n, a, col]) => ({ n, col, x: dot3(a, cam.right), y: -dot3(a, cam.up), z: -dot3(a, cam.fwd) }))
    .sort((a, b) => a.z - b.z);                              // 奥の軸から描く
  ctx.setLineDash([]); ctx.lineWidth = 2; ctx.font = "bold 11px Segoe UI, sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  for (const a of axes) {
    ctx.strokeStyle = ctx.fillStyle = a.col;
    ctx.beginPath(); ctx.moveTo(ox, oy); ctx.lineTo(ox + a.x * L, oy + a.y * L); ctx.stroke();
    ctx.fillText(a.n, ox + a.x * (L + 9), oy + a.y * (L + 9));
  }
}

/** ステータスバー: ホバー中・選択中の面やエッジの名前と大きさ（一般的な 3D CAD のステータスバーの測定値）。 */
function showPick(i, sticky) {
  const name = i >= 0 && app.mesh ? app.mesh.faceNames[i] : null;
  if (sticky) app.pickedName = name;
  if (sketch.active) return;
  const m = app.mesh, bar = $("#status-pick");
  if (app.pickFor) { bar.textContent = `${app.pickFor.want.includes("face") ? "面" : ""}${app.pickFor.want.length > 1 ? "か" : ""}${app.pickFor.want.includes("edge") ? "エッジ" : ""}をクリックしてください（Esc で取り消し）`; return; }
  if (!m) { bar.textContent = ""; return; }
  if (app.hoverEdge !== null) { bar.textContent = `エッジ: ${m.edgeNames[app.hoverEdge]}　長さ ${fmtLen(edgeLength(app.hoverEdge))} mm`; return; }
  if (name) { bar.textContent = `面: ${name}　面積 ${fmtLen(faceArea(i))} mm²`; return; }
  if (app.selEdges.size) {
    const total = [...app.selEdges].reduce((a, n) => a + edgeLength(m.edgeNames.indexOf(n)), 0);
    bar.textContent = `選択中のエッジ: ${app.selEdges.size} 本　長さの合計 ${fmtLen(total)} mm`;
    return;
  }
  const fi = app.pickedName ? m.faceNames.indexOf(app.pickedName) : -1;
  bar.textContent = fi >= 0 ? `選択中の面: ${app.pickedName}　面積 ${fmtLen(faceArea(fi))} mm²` : "";
}

function render(st) {
  const switching = app.state?.doc !== st.doc;
  if (switching) { app.selPlane = null; app.pending = null; app.pickFor = null; }
  app.state = st;
  refreshDocs();
  document.body.classList.toggle("asm-mode", st.kind === "assembly");
  document.body.classList.toggle("drw-mode", st.kind === "drawing");
  $("#asm-left").hidden = st.kind !== "assembly";
  $("#drw-left").hidden = $("#sheet").hidden = st.kind !== "drawing";
  if (st.kind !== "drawing") drawing.set(null);
  if (st.kind === "drawing") {
    sketch.set(null); asm.set(null); asm.mateMode = false; asm.pendingMate = null; hideMatePopup();
    return renderDrawing(st, switching);
  }
  if (st.kind === "assembly") return renderAssembly(st, switching);
  asm.set(null); asm.mateMode = false; asm.pendingMate = null; hideMatePopup();
  if (app.measure && (switching || st.sketch)) measureEnd();
  docTitle(st);
  const k = $("#kernel");
  k.textContent = `カーネル: ${st.kernel}`;
  k.classList.toggle("preview", st.kernel !== "usd_brep");
  k.title = st.kernel === "usd_brep" ? "形状カーネル" : "簡易カーネル（押し出しと和のみ）。usd-brep を入れると全機能が動きます";
  $("#btn-undo").disabled = !st.undo; $("#btn-undo").title = st.undo ? `元に戻す: ${st.undo} (Ctrl+Z)` : "元に戻す";
  $("#btn-redo").disabled = !st.redo; $("#btn-redo").title = st.redo ? `やり直し: ${st.redo} (Ctrl+Y)` : "やり直し";
  if (app.selPlane && !(st.planes || []).some((p) => sameRef(p.ref, app.selPlane))) app.selPlane = null;
  if (app.pending && !st.tree.some((t) => t.id === app.pending)) app.pending = null;
  if (app.pickFor && !st.tree.some((t) => t.id === app.pickFor.id)) app.pickFor = null;
  renderTree(st);
  renderVars(st);
  if ("mesh" in st) {                  // mesh が無い応答（名前の変更など）では前の表示を保つ
    app.mesh = decodeMesh(st.mesh);
    if (app.measure) { app.measure.items = []; renderMeasure(); }      // 形が変わったら選び直し
    app.hoverEdge = null;
    for (const n of [...app.selEdges]) if (!app.mesh || !app.mesh.edgeNames.includes(n)) app.selEdges.delete(n);
    if (app.pickedName && !(app.mesh && app.mesh.faceNames.includes(app.pickedName))) app.pickedName = null;
    viewer.setMesh(app.mesh);
    viewer.setSelected(-1);
    if ((app.firstMesh || switching) && app.mesh) { viewer.setView("iso"); app.firstMesh = false; }
  }
  const wasSketch = sketch.active;
  if (wasSketch && !st.sketch && app.holeFlow && !switching) {   // 穴ウィザード: 位置のスケッチを終えたら穴を作る
    const sid = app.holeFlow; app.holeFlow = null;
    if (st.tree.some((t) => t.id === sid)) setTimeout(() => addFeature("hole", { sketch: sid, kind: "cbore", size: "M6", depth: 15 }), 0);
  }
  if (switching) { app.holeFlow = null; $("#float-panel").hidden = true; }
  if (wasSketch !== !!st.sketch) { app.selEdges.clear(); app.hoverEdge = null; }
  if (st.sketch && app.selPlane) { app.selPlane = null; markPlaneRows(); }
  sketch.ext = st.sketch ? (st.sketches || []).filter((s) => s.id !== st.sketch.id && s.shown) : [];
  sketch.modelGeoFn = () => (app.state?.sketch ? modelGeometry(app.mesh) : null);
  sketch.set(st.sketch);
  $("#confirm-corner").hidden = !st.sketch;
  document.body.classList.toggle("sketching", !!st.sketch);
  if (wasSketch !== !!st.sketch || switching || !currentTab() || currentTab() === "assembly") setTab(st.sketch ? "sketch" : "features");
  const sb = $("#btn-sketch");                          // スケッチ中は「スケッチ終了」（一般的な 3D CAD と同じボタン）
  sb.querySelector(".ic").innerHTML = ICONS[st.sketch ? "sketchExit" : "sketch"];
  sb.querySelector(".lbl").textContent = st.sketch ? "スケッチ終了" : "スケッチ";
  if (st.sketch) {
    app.selected = st.sketch.id;
    renderSketchProps();
    sketchStatus();
  } else {
    if (wasSketch) { $("#status-pick").textContent = ""; }
    if (app.selected && st.tree.some((t) => t.id === app.selected)) loadProps(app.selected);
    else if (app.selected) { app.selected = null; clearProps(); }
  }
  $("#status-info").textContent = !st.has_body ? "ボディなし" : st.volume != null ? `体積 ${st.volume.toFixed(2)} mm³` : "";
  renderSketchTools();
  updateButtons();
}

/** 押し出し・カット・回転の対象にするスケッチ: 編集中 → ツリーで選択中 → まだ使われていない最後のスケッチ。 */
function targetSketch() {
  const st = app.state;
  if (st.sketch) return st.sketch.id;
  const sel = st.tree.find((t) => t.id === app.selected);
  if (sel && sel.type === "sketch") return sel.id;
  const last = st.tree[st.tree.length - 1];
  return last && last.type === "sketch" ? last.id : null;
}

function updateButtons() {
  const st = app.state; if (!st || st.kind !== "part") return;
  const has = !!targetSketch();
  for (const id of ["#btn-extrude", "#btn-cut", "#btn-revolve"]) $(id).disabled = !has;
  $("#btn-sweep").disabled = !has || st.tree.filter((t) => t.type === "sketch").length < 2;
  $("#btn-fillet").disabled = $("#btn-chamfer").disabled = !!st.sketch || !app.selEdges.size;
  $("#btn-shell").disabled = !!st.sketch || !app.mesh;
  $("#menu-face").disabled = !app.pickedName;
  $("#menu-face").textContent = app.pickedName ? `選択した面（${app.pickedName}）` : "選択した面（3D で面をクリック）";
  const seed = seedFeature();
  for (const id of ["#btn-lpattern", "#btn-cpattern", "#btn-mirror-f"]) $(id).disabled = !!st.sketch || !seed;
  $("#btn-plane").disabled = !!st.sketch;
  $("#btn-hole").disabled = !!st.sketch || !app.mesh;
}

/** パターン・ミラーの元にするフィーチャー: ツリーで選んでいる押し出し・カット・回転。 */
function seedFeature() {
  const t = app.state?.tree?.find((x) => x.id === app.selected);
  return t && ["extrude", "revolve"].includes(t.type) && t.state !== "rolled_back" ? t.id : null;
}

/** 今選んでいる平面の参照: ツリーで選んだ平面 → 3D で選んだ面。 */
function selectedPlaneRef() {
  return app.selPlane || (app.pickedName ? { face: app.pickedName } : null);
}

// ---------------------------------------------------------------- フィーチャーツリー
function renderTree(st) {
  const ol = $("#tree");
  ol.replaceChildren();
  ol.append(el("li", { class: "item origin" }, el("span", { class: "ico" }, "⊕"), el("span", { class: "name" }, "原点")));
  for (const p of (st.planes || []).filter((x) => x.ref.plane)) {
    ol.append(el("li", {
      class: `item plane${sameRef(app.selPlane, p.ref) ? " selected" : ""}`, "data-plane": JSON.stringify(p.ref),
      title: "基準面。選んで「スケッチ」でこの平面に描きます（ダブルクリックでも）。右クリックでメニュー",
      onclick: () => selectPlane(p.ref),
      ondblclick: () => { if (!sketch.active) act("sketch.new", { plane: p.ref }); },
      oncontextmenu: (e) => { e.preventDefault(); selectPlane(p.ref); planeMenu(e, p.ref); },
    }, el("span", { class: "ico" }, "▱"), el("span", { class: "name" }, p.name)));
  }
  const rb = st.rollback ?? st.tree.length;
  st.tree.forEach((t, i) => {
    if (i === rb) ol.append(rollbackBar());
    // 未定義・過剰定義のスケッチは一般的な 3D CAD と同じく名前の前に (-) / (+)。⚠ は本当の警告だけに使う（埋もれないように）
    const under = t.type === "sketch" && t.state === "warning" && t.message === "スケッチが未定義です";
    const over = t.type === "sketch" && t.state === "warning" && t.message === "過剰定義";
    const state = under ? "ok" : t.state;
    const badge = under ? "" : STATE_BADGE[t.state] ?? "";
    const prefix = under ? "(-) " : over ? "(+) " : "";
    const editing = st.sketch && st.sketch.id === t.id;
    const icon = t.type === "extrude" && t.op === "cut" ? "▱" : (ICON[t.type] || "•");
    ol.append(el("li", {
      class: `item ${state}${app.selected === t.id ? " selected" : ""}`, "data-id": t.id, "data-index": i,
      title: `${t.type_label}${t.message ? "\n" + t.message : ""}\n${t.type === "sketch" ? "ダブルクリックで編集。" : ""}右クリックでメニュー、F2 で名前の変更`,
      onclick: () => selectFeature(t.id, true),
      ondblclick: () => { if (t.type === "sketch") act("sketch.begin", { id: t.id }); },
      oncontextmenu: (e) => { e.preventDefault(); featureMenu(e, t); },
    }, el("span", { class: "ico" }, icon), el("span", { class: "name" }, prefix + t.name + (editing ? "（編集中）" : "")),
    el("span", { class: "badge" }, badge)));
    if (t.state === "error" && t.message) ol.append(el("li", { class: "msg" }, t.message));
  });
  if (rb >= st.tree.length) ol.append(rollbackBar());
}

function markPlaneRows() {
  document.querySelectorAll("#tree li.item.plane").forEach((li) =>
    li.classList.toggle("selected", sameRef(app.selPlane, JSON.parse(li.dataset.plane))));
  viewer.redraw();
}

/** ツリーで平面を選ぶ（3D に表示し、「スケッチ」「参照平面」「正対」の対象にする）。 */
function selectPlane(ref) {
  if (sketch.active) return;
  if (app.pickFor?.want.includes("plane")) { finishPick(ref); return; }
  app.selPlane = ref; app.selected = null; app.pickedName = null; app.selEdges.clear();
  document.querySelectorAll("#tree li.item[data-id]").forEach((li) => li.classList.remove("selected"));
  viewer.setSelected(-1);
  markPlaneRows(); clearProps(); updateButtons();
  $("#status-pick").textContent = `平面: ${(app.state.planes.find((p) => sameRef(p.ref, ref)) || {}).name || ""}`;
}

function planeMenu(e, ref) {
  showMenu(e.clientX, e.clientY, [
    { label: "この平面にスケッチ", onclick: () => act("sketch.new", { plane: ref }) },
    { label: "正対", key: "Ctrl+8", onclick: () => normalTo() },
    { label: "参照平面を作成（オフセット）", onclick: () => addFeature("plane", { base: ref, offset: 10 }) },
  ]);
}

function featureMenu(e, t) {
  if (sketch.active && app.state.sketch.id !== t.id) return;
  const idx = app.state.tree.findIndex((x) => x.id === t.id), n = app.state.tree.length;
  const go = async (m, p) => { await act(m, p); };
  selectFeature(t.id, true);
  showMenu(e.clientX, e.clientY, [
    t.type === "sketch" ? { label: "スケッチを編集", onclick: () => act("sketch.begin", { id: t.id }) } : null,
    t.type === "sketch" ? sketchShowItem(t.id) : null,
    t.type === "plane" ? { label: "この平面にスケッチ", onclick: () => act("sketch.new", { plane: { feature: t.id } }) } : null,
    { label: "フィーチャーを編集（プロパティ）", onclick: () => selectFeature(t.id, true) },
    { label: "名前を変更", key: "F2", onclick: () => renameFeature(t.id) },
    !["sketch", "plane"].includes(t.type) ? appearanceItem(e.clientX, e.clientY, "feature", t.id, `${t.name} の色`) : null,
    "-",
    { label: t.state === "suppressed" ? "抑制を解除" : "抑制", onclick: () => go("feature.suppress", { id: t.id, value: t.state !== "suppressed" }) },
    { label: "ここまでロールバック", onclick: () => go("rollback.set", { index: idx + 1 }) },
    { label: "ロールバックを解除", disabled: app.state.rollback == null, onclick: () => go("rollback.set", { index: null }) },
    "-",
    { label: "上へ移動", disabled: idx === 0, onclick: () => go("feature.move", { id: t.id, index: idx - 1 }) },
    { label: "下へ移動", disabled: idx >= n - 1, onclick: () => go("feature.move", { id: t.id, index: idx + 1 }) },
    "-",
    { label: "削除", key: "Del", onclick: () => deleteFeature(t.id) },
  ]);
}

/** 外観（色）のメニュー項目（D69）。kind: part / feature / face。押すと色見本のパレットを出す。 */
function appearanceItem(x, y, kind, target, title) {
  const ap = app.state?.appearance || {};
  const cur = kind === "part" ? ap.part : kind === "feature" ? ap.features?.[target] : ap.faces?.[target];
  const params = kind === "part" ? {} : kind === "feature" ? { feature: target } : { face: target };
  return { label: `外観: ${title}…`, onclick: () => showPalette(x, y, title, cur || null, (color) => act("appearance.set", { ...params, color })) };
}

/** スケッチの「表示 / 非表示」（一般的な 3D CAD の目のアイコン）。 */
function sketchShowItem(id) {
  const s = (app.state.sketches || []).find((x) => x.id === id);
  if (!s) return null;
  return { label: s.shown ? "スケッチを非表示" : "スケッチを表示", onclick: () => act("sketch.visible", { feature: id, visible: !s.shown }) };
}

/** 3D ビューの右クリック。 */
function viewContextMenu(pos, ev) {
  const st = app.state; if (!st) return;
  const x = ev.clientX, y = ev.clientY;
  if (st.kind !== "part") { showMenu(x, y, viewItems()); return; }
  if (sketch.active) {
    const n = sketch.sel.size;
    showMenu(x, y, [
      { label: "選択", key: "Esc", onclick: () => sketch.setTool("select") },
      { label: "スマート寸法", key: "D", onclick: () => sketch.setTool("dim") },
      { label: "線", key: "L", onclick: () => sketch.setTool("line") },
      "-",
      { label: "作図線の切り替え", disabled: !n, onclick: () => sketch.toggleConstruction() },
      { label: "削除", key: "Del", disabled: !n, onclick: () => sketch.deleteSelected() },
      "-",
      { label: "スケッチに正対", key: "Ctrl+8", onclick: () => normalTo() },
      { label: "スケッチ終了", key: "Ctrl+Enter", onclick: () => act("sketch.end") },
    ]);
    return;
  }
  const f = viewer.pick(...pos), e = edgeAt(pos, f);
  const face = f >= 0 && app.mesh ? app.mesh.faceNames[f] : null;
  if (e !== null && !app.selEdges.has(app.mesh.edgeNames[e])) { app.selEdges.clear(); app.selEdges.add(app.mesh.edgeNames[e]); }
  if (face && e === null) { app.pickedName = face; app.selPlane = null; viewer.setSelected(f); markPlaneRows(); }
  updateButtons(); showPick(-1, false);
  const fid = face ? featureOfFace(face) : null, ft = fid && st.tree.find((t) => t.id === fid);
  const skAt = e === null ? sketchAt(pos) : null;
  if (skAt) { selectFeature(skAt, false); showMenu(x, y, [
    { label: "スケッチを編集", onclick: () => act("sketch.begin", { id: skAt }) }, sketchShowItem(skAt), "-", ...viewItems()]); return; }
  const selectEdges = (list) => { app.selEdges = new Set(list.map((k) => app.mesh.edgeNames[k])); updateButtons(); showPick(-1, false); viewer.redraw(); };
  const others = f >= 0 || e !== null ? facesUnder(app.mesh, viewer.ray(...pos)) : [];
  showMenu(x, y, [
    e !== null ? { label: "ループを選択", onclick: () => selectEdges(edgeLoop(app.mesh, e, f)) } : null,
    e !== null ? { label: "正接の連続を選択", onclick: () => selectEdges(tangentChain(app.mesh, e)) } : null,
    others.length > 1 ? { label: `別の選択…（奥の面 ${others.length - 1} 枚）`, onclick: () => selectOther(x, y, others) } : null,
    e !== null || others.length > 1 ? "-" : null,
    face && e === null ? { label: "この面にスケッチ", onclick: () => act("sketch.new", { plane: { face } }) } : null,
    face && e === null ? { label: "この面に正対", key: "Ctrl+8", onclick: () => normalTo() } : null,
    face && e === null ? { label: "参照平面を作成（この面からオフセット）", onclick: () => addFeature("plane", { base: { face }, offset: 10 }) } : null,
    ft && e === null ? { label: `フィーチャーを編集: ${ft.name}`, onclick: () => selectFeature(ft.id, false) } : null,
    app.selEdges.size ? { label: `フィレット（エッジ ${app.selEdges.size} 本）`, onclick: () => $("#btn-fillet").click() } : null,
    (face || app.selEdges.size) ? "-" : null,
    face && e === null ? appearanceItem(x, y, "face", face, "この面の色") : null,
    ft && e === null && !["sketch", "plane"].includes(ft.type) ? appearanceItem(x, y, "feature", ft.id, `${ft.name} の色`) : null,
    st.has_body ? appearanceItem(x, y, "part", null, "パートの色") : null,
    st.has_body ? "-" : null,
    ...viewItems(),
  ]);
}

/** 別の選択: カーソルの下にある面を手前から並べ、選ぶと（奥の面でも）その面を選ぶ。項目に乗ると光る。 */
function selectOther(x, y, list) {
  showMenu(x, y, list.map(({ face }, k) => ({
    label: `${k + 1}. ${app.mesh.faceNames[face]}`,
    onhover: (on) => { viewer.setHover(on ? face : -1); },
    onclick: () => {
      viewer.setHover(-1);
      if (app.measure) { app.measure.items.push({ kind: "face", i: face }); renderMeasure(); return; }
      app.selEdges.clear(); app.selPlane = null; markPlaneRows(); viewer.setSelected(face); showPick(face, true);
      selectFeature(featureOfFace(app.mesh.faceNames[face]), false); updateButtons(); viewer.redraw();
    },
  })));
}

function viewItems() {
  return [
    { label: "全体表示", key: "F", onclick: () => viewer.fit() },
    { label: "等角投影", key: "Ctrl+7", onclick: () => viewer.setView("iso") },
    { label: "正面", key: "Ctrl+1", onclick: () => viewer.setView("front") },
    { label: "平面", key: "Ctrl+5", onclick: () => viewer.setView("top") },
    { label: "右側面", key: "Ctrl+4", onclick: () => viewer.setView("right") },
  ];
}

/** 正対: スケッチ中はスケッチ平面、そうでなければ選んだ平面か面（平らな面）に正面から向く。 */
function normalTo() {
  const st = app.state; if (!st) return;
  if (st.sketch?.plane) { viewer.lookNormal(st.sketch.plane.origin, st.sketch.plane.normal); return; }
  const g = selectedPlaneGeom();
  if (!g) { toast(g === null ? "正対する面か平面を選んでください" : "この面には正対できません", true); return; }
  viewer.lookNormal(g.origin, g.normal);
}

/** 選んだ平面（ツリー）か面（ビュー）の { origin, normal }。何も選んでいなければ null、向きが決まらなければ false。
 *  面の法線 = 三角形の法線の面積重み平均（曲面なら平均の向き）、origin = 面の点の 1 つ。 */
function selectedPlaneGeom() {
  const st = app.state;
  const pl = app.selPlane && (st?.planes || []).find((p) => sameRef(p.ref, app.selPlane));
  if (pl) return { origin: pl.origin, normal: pl.normal };
  const m = app.mesh, fi = m && app.pickedName ? m.faceNames.indexOf(app.pickedName) : -1;
  if (fi < 0) return null;
  const P = m.positions, I = m.indices, n = [0, 0, 0];
  let o = null;
  for (let t = 0; t < m.faceOfTriangle.length; t++) {
    if (m.faceOfTriangle[t] !== fi) continue;
    const a = 3 * I[3 * t], b = 3 * I[3 * t + 1], c = 3 * I[3 * t + 2];
    o = o || [P[a], P[a + 1], P[a + 2]];
    const u = [P[b] - P[a], P[b + 1] - P[a + 1], P[b + 2] - P[a + 2]], v = [P[c] - P[a], P[c + 1] - P[a + 1], P[c + 2] - P[a + 2]];
    n[0] += u[1] * v[2] - u[2] * v[1]; n[1] += u[2] * v[0] - u[0] * v[2]; n[2] += u[0] * v[1] - u[1] * v[0];
  }
  if (Math.hypot(...n) < 1e-12) return false;
  return { origin: o, normal: n };
}

// ---------------------------------------------------------------- 断面表示（D62。表示だけ。モデルは変えない）
// 一般的な 3D CAD と同じ: 平面（正面・平面・右側面か、選んだ平面・面）と距離で切り、法線の側を消す。「反対側を表示」で逆。
// ✓ で断面のまま閉じる、ボタンをもう一度押すか ✗ でやめる
const SEC_PLANES = { Front: [0, 0, 1], Top: [0, 1, 0], Right: [1, 0, 0] };
function sectionApply() {
  const s = app.section;
  if (!s) { viewer.setSection(null); return; }
  const k = s.flip ? -1 : 1, base = s.normal[0] * s.origin[0] + s.normal[1] * s.origin[1] + s.normal[2] * s.origin[2];
  viewer.setSection({ normal: s.normal.map((v) => k * v), offset: k * (base + s.offset) });
  $("#btn-section").classList.add("on");
}
function sectionUi() {
  const s = app.section; if (!s) return;
  for (const b of document.querySelectorAll("#section-panel [data-sec]")) b.classList.toggle("on", b.dataset.sec === s.plane);
  const base = s.normal[0] * s.origin[0] + s.normal[1] * s.origin[1] + s.normal[2] * s.origin[2];
  const [lo, hi] = viewer.sectionRange(s.normal), r = $("#sec-range");
  r.min = String(lo - base); r.max = String(hi - base); r.step = String(Math.max((hi - lo) / 400, 1e-6));
  r.value = String(s.offset); $("#sec-offset").value = String(+s.offset.toFixed(4)); $("#sec-flip").checked = s.flip;
}
function sectionPlane(plane) {
  let g = SEC_PLANES[plane] ? { origin: [0, 0, 0], normal: SEC_PLANES[plane] } : selectedPlaneGeom();
  if (!g) { toast(g === null ? "切る平面か平らな面を選んでから押してください" : "この面では切れません", true); return false; }
  const l = Math.hypot(...g.normal);
  app.section = { plane, origin: g.origin, normal: g.normal.map((v) => v / l), offset: 0, flip: app.section?.flip || false };
  sectionApply(); sectionUi(); return true;
}
function sectionOff() {
  app.section = null; viewer.setSection(null); viewer.sectionGizmo = false;
  $("#section-panel").hidden = true; $("#btn-section").classList.remove("on");
}
function sectionPanel(open) { $("#section-panel").hidden = !open; viewer.sectionGizmo = open; viewer.redraw(); }
/** 断面のハンドルのドラッグ（viewer が量を知らせる）: 矢印 = 距離、輪 = 平面を回す（回すと「自由な平面」になる）。 */
viewer.onSectionGizmo = (ev) => {
  const s = app.section; if (!s || ev.end) return;
  if (ev.move != null) s.offset += (s.flip ? -1 : 1) * ev.move;
  if (ev.rotate) {
    const k = s.flip ? -1 : 1, a = ev.rotate, t = ev.angle, n = s.normal.map((v) => k * v);   // 見えている法線を回す
    const c = Math.cos(t), sn = Math.sin(t), ad = a[0] * n[0] + a[1] * n[1] + a[2] * n[2];
    const ax = [a[1] * n[2] - a[2] * n[1], a[2] * n[0] - a[0] * n[2], a[0] * n[1] - a[1] * n[0]];
    const r = [0, 1, 2].map((i) => n[i] * c + ax[i] * sn + a[i] * ad * (1 - c));               // ロドリゲスの回転
    const l = Math.hypot(...r);
    Object.assign(s, { plane: "free", normal: r.map((v) => k * v / l), origin: ev.about, offset: 0 });
  }
  sectionApply(); sectionUi();
};
$("#btn-section").onclick = (e) => {
  e.stopPropagation();
  if (app.section) { if ($("#section-panel").hidden) sectionPanel(true); else sectionOff(); return; }   // 閉じていれば開き直す
  // 平面・面を選んであればそれで、なければ正面で切る（一般的な 3D CAD の既定）
  if (!(selectedPlaneGeom() && sectionPlane("sel"))) sectionPlane("Front");
  sectionPanel(true);
};
document.querySelectorAll("#section-panel [data-sec]").forEach((b) => (b.onclick = () => sectionPlane(b.dataset.sec)));
$("#sec-range").oninput = (e) => { app.section.offset = Number(e.target.value); $("#sec-offset").value = String(+app.section.offset.toFixed(4)); sectionApply(); };
$("#sec-offset").oninput = (e) => { const v = Number(e.target.value); if (Number.isFinite(v)) { app.section.offset = v; $("#sec-range").value = String(v); sectionApply(); } };
$("#sec-offset").onkeydown = (e) => { e.stopPropagation(); if (e.key === "Enter") sectionPanel(false); };
$("#sec-flip").onchange = (e) => { app.section.flip = e.target.checked; sectionApply(); };
$("#sec-ok").onclick = () => sectionPanel(false);
$("#sec-off").onclick = () => sectionOff();

/** ツリーの上で名前を書き換える（F2）。 */
function renameFeature(id) {
  const li = document.querySelector(`#tree li.item[data-id="${id}"]`), t = app.state.tree.find((x) => x.id === id);
  if (!li || !t) return;
  const span = li.querySelector(".name");
  const inp = el("input", { class: "rename", type: "text", value: t.name, spellcheck: "false" });
  span.replaceWith(inp); inp.focus(); inp.select();
  let done = false;
  const finish = async (commit) => {
    if (done) return; done = true;
    const v = inp.value.trim();
    if (commit && v && v !== t.name) await act("feature.rename", { id, name: v });
    else renderTree(app.state);
  };
  inp.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); });
  inp.addEventListener("blur", () => finish(true));
  inp.addEventListener("click", (e) => e.stopPropagation());
  inp.addEventListener("dblclick", (e) => e.stopPropagation());
}

async function deleteFeature(id) {
  const t = app.state.tree.find((x) => x.id === id); if (!t) return;
  if (app.pending === id) app.pending = null;
  if (await act("feature.delete", { id })) toast(`${t.name} を削除しました（Ctrl+Z で戻せます）`);
}

function rollbackBar() {
  const bar = el("li", { class: "rollback", title: "ロールバックバー（ドラッグで移動）" });
  bar.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    bar.classList.add("dragging");
    let target = null;
    const move = (ev) => {
      const rows = [...document.querySelectorAll("#tree li.item[data-index]")];
      target = rows.length;
      for (const r of rows) {
        const b = r.getBoundingClientRect();
        if (ev.clientY < b.top + b.height / 2) { target = +r.dataset.index; break; }
      }
      const ref = rows.find((r) => +r.dataset.index === target);
      if (ref) ref.before(bar); else $("#tree").append(bar);
    };
    const up = async () => {
      document.removeEventListener("pointermove", move); document.removeEventListener("pointerup", up);
      bar.classList.remove("dragging");
      if (target !== null) await act("rollback.set", { index: target });
    };
    document.addEventListener("pointermove", move); document.addEventListener("pointerup", up);
  });
  return bar;
}

async function selectFeature(id, fromTree) {
  const t = app.state.tree.find((x) => x.id === id);
  if (!t || sketch.active) return;
  if (app.pending && app.pending !== id) app.pending = null;       // ほかを選んだら確定したことにする
  app.selected = id;
  app.selPlane = t.type === "plane" && t.state !== "rolled_back" ? { feature: id } : (fromTree ? null : app.selPlane);
  markPlaneRows();
  document.querySelectorAll("#tree li.item").forEach((li) => li.classList.toggle("selected", li.dataset.id === id));
  if (fromTree) viewer.setSelected(-1);
  updateButtons();
  await loadProps(id);
}

// ---------------------------------------------------------------- 変数
function renderVars(st) {
  const tb = $("#vars");
  tb.replaceChildren();
  for (const v of st.variables) {
    const inp = el("input", { value: v.expr, spellcheck: "false" });
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") act("variable.set", { name: v.name, expr: inp.value }); });
    tb.append(el("tr", {}, el("td", { class: "name" }, v.name), el("td", {}, inp),
      el("td", { class: v.error ? "err" : "val", title: v.error || "" }, v.error ? "エラー" : (+v.value.toFixed(6)).toString())));
  }
}

$("#var-add").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  if (await act("variable.set", { name: f.name.value.trim(), expr: f.expr.value })) f.reset();
});

// ---------------------------------------------------------------- ツールバー・キー
$("#btn-mate").onclick = () => (asm.mateMode ? mateExit(true) : mateEnter());
$("#btn-undo").onclick = () => act("edit.undo");
$("#btn-redo").onclick = () => act("edit.redo");
$("#btn-fit").onclick = () => viewer.fit();
document.querySelectorAll("#cm-tabs [data-tab]").forEach((b) => (b.onclick = () => setTab(b.dataset.tab)));
$("#btn-insert").onclick = () => { const s = $("#asm-add select"); s.focus(); s.classList.add("flash"); setTimeout(() => s.classList.remove("flash"), 900); };
/** ツールバーのドロップダウン（新規・スケッチの平面・表示方向・表示スタイル）。開くのは 1 つだけ。 */
const MENUS = ["#new-menu", "#save-menu", "#sketch-menu", "#views-menu", "#style-menu", "#filter-menu"];
function toggleMenu(id, e) {
  e.stopPropagation();
  const open = $(id).hidden;
  for (const m of MENUS) $(m).hidden = true;
  $(id).hidden = !open;
}
$("#btn-new-menu").onclick = (e) => toggleMenu("#new-menu", e);
$("#btn-style").onclick = (e) => toggleMenu("#style-menu", e);
$("#btn-filter").onclick = (e) => toggleMenu("#filter-menu", e);
document.querySelectorAll("#style-menu [data-style]").forEach((b) => (b.onclick = () => {
  viewer.setStyle(b.dataset.style);
  for (const x of document.querySelectorAll("#style-menu [data-style]")) x.classList.toggle("on", x === b);
  $("#btn-style .ic").innerHTML = b.querySelector(".ic").innerHTML;
}));
$("#style-menu [data-style=shaded_edges]").classList.add("on");
$("#btn-persp").onclick = () => { const p = !$("#btn-persp").classList.contains("on"); $("#btn-persp").classList.toggle("on", p); viewer.setOrtho(!p); };
document.querySelectorAll("[data-view]").forEach((b) => (b.onclick = () => viewer.setView(b.dataset.view)));
$("#btn-save").onclick = (e) => save(e.shiftKey);
$("#btn-save-menu").onclick = (e) => toggleMenu("#save-menu", e);
$("#menu-saveas").onclick = () => save(true);
document.querySelectorAll("#save-menu [data-export]").forEach((b) => (b.onclick = () => exportMesh(b.dataset.export)));
/** 書き出しの細かさ（D71）。前に選んだものを覚えておく */
let exportQuality = "standard";
try { exportQuality = localStorage.getItem("agentcad.exportQuality") || "standard"; } catch { /* 使えなくてもよい */ }
function setQuality(q) {
  exportQuality = q;
  for (const x of document.querySelectorAll("#save-menu [data-quality]")) x.classList.toggle("on", x.dataset.quality === q);
  try { localStorage.setItem("agentcad.exportQuality", q); } catch { /* 使えなくてもよい */ }
}
document.querySelectorAll("#save-menu [data-quality]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); setQuality(b.dataset.quality); }));
setQuality(exportQuality);
$("#btn-new").onclick = async () => presentDoc(await rpcRaw("doc.demo"));
$("#btn-ai").onclick = () => toggleAgent();
$("#btn-new-ai").onclick = () => openAgent(true);
$("#btn-new-car").onclick = async () => { await presentDoc(await rpcRaw("doc.demo", { kind: "car" })); viewer.setView("iso"); };
$("#btn-newdoc").onclick = async () => { await presentDoc(await rpcRaw("doc.new")); if (app.state?.kind === "part" && !app.mesh) viewer.setView("iso"); };
// スケッチ: 平面か面を選んであればそこに描く（一般的な 3D CAD と同じ）。選んでいなければ平面を選ぶメニュー
$("#btn-sketch").onclick = (e) => {
  e.stopPropagation();
  if (app.state?.sketch) { act("sketch.end"); return; }          // スケッチ中は「スケッチ終了」
  const ref = selectedPlaneRef();
  if (ref && !e.altKey) { act("sketch.new", { plane: ref }); return; }
  updateButtons(); toggleMenu("#sketch-menu", e);
};
$("#btn-views").onclick = (e) => toggleMenu("#views-menu", e);
document.addEventListener("click", () => { for (const m of MENUS) $(m).hidden = true; });
$("#menu-normal").onclick = () => normalTo();
$("#btn-normal").onclick = () => normalTo();
document.querySelectorAll("#sketch-menu button").forEach((b) => (b.onclick = () => {
  $("#sketch-menu").hidden = true;
  const plane = b.dataset.plane === "face" ? { face: app.pickedName } : { plane: b.dataset.plane };
  act("sketch.new", { plane });
}));

async function addFeature(type, params) {
  const st = await act("feature.add", { type, ...params });
  const fid = st?.created?.[0];
  if (!fid) return;
  app.selEdges.clear(); app.pickedName = null; viewer.setSelected(-1);
  app.pending = fid;                                   // 右のパネルに ✓ / ✗ を出す
  await selectFeature(fid, true);
  const t = st.tree.find((x) => x.id === fid);
  if (t.state === "error") toast(`${t.name}: ${t.message}`, true);
  else toast("右のパネルで寸法や設定を変えて、✓ で確定（Enter）/ ✗ で取り消し（Esc）");
  if (app.mesh) viewer.fit();
}
function addFromSketch(type, op) {
  const sk = targetSketch();
  if (!sk) { toast("スケッチを描くか、ツリーでスケッチを選んでください", true); return; }
  addFeature(type, { sketch: sk, op, distance: 10 });
}
$("#btn-extrude").onclick = () => addFromSketch("extrude", "add");
$("#btn-cut").onclick = () => addFromSketch("extrude", "cut");
/** スイープ（D58）: 選んだ（か編集中の）スケッチを断面に、経路はサーバが選ぶ（右のパネルで変えられる）。 */
$("#btn-sweep").onclick = () => {
  const sk = targetSketch();
  if (!sk) { toast("断面のスケッチを描くか、ツリーで選んでください", true); return; }
  addFeature("sweep", { sketch: sk, op: "add" });
};
/** 穴ウィザード: 選んだ面（か平面）に位置のスケッチを作り、点のツールにする。スケッチを終えると穴を作る（D57）。 */
$("#btn-hole").onclick = async () => {
  const ref = selectedPlaneRef();
  if (!ref) { toast("穴を開ける面（または平面）を 3D かツリーで選んでから押してください", true); return; }
  const st = await act("sketch.new", { plane: ref });
  if (!st?.sketch) return;
  app.holeFlow = st.sketch.id;
  sketch.setTool("point");
  toast("穴の位置に点を置いてください（中心・中点にスナップできます。寸法も付けられます）→ スケッチ終了で穴の設定へ");
};
$("#btn-revolve").onclick = () => addFromSketch("revolve", "add");
$("#btn-fillet").onclick = () => addFeature("fillet", { edges: [...app.selEdges], radius: 2 });
$("#btn-chamfer").onclick = () => addFeature("chamfer", { edges: [...app.selEdges], distance: 1 });
$("#btn-shell").onclick = () => addFeature("shell", { faces: app.pickedName ? [app.pickedName] : [], thickness: 2 });
$("#btn-plane").onclick = () => addFeature("plane", { base: selectedPlaneRef() || { plane: "Front" }, offset: 10 });
/** パターン・ミラー: 元 = ツリーで選んだフィーチャー。エッジを選んであれば向き・軸に使う。 */
function addPattern(type) {
  const seed = seedFeature();
  if (!seed) { toast("元にする押し出し・カット・回転をツリーで選んでください", true); return; }
  const edge = app.selEdges.size === 1 ? { edge: [...app.selEdges][0] } : null;
  const extra = type === "lpattern" ? (edge ? { direction: edge } : {})
    : type === "cpattern" ? (edge ? { axis: edge } : {})
      : (app.selPlane ? { plane: app.selPlane } : {});
  addFeature(type, { seeds: [seed], ...extra });
}
$("#btn-lpattern").onclick = () => addPattern("lpattern");
$("#btn-cpattern").onclick = () => addPattern("cpattern");
$("#btn-mirror-f").onclick = () => addPattern("mirror");

async function save(saveAs = false) {
  let path = app.state.path;
  if (saveAs || !path || !path.toLowerCase().endsWith(".usda")) {
    const hint = path ? path.replace(/\.(ucpart|ucasm)$/i, ".usda") : app.state.name;
    path = await pickPath("save", hint, "保存先のパス（USD 形式。拡張子 .usda は自動で付きます）"
      + (app.state.kind === "assembly" ? "\n未保存・変更ありの部品のパートも一緒に保存します" : "")
      + (app.state.kind === "drawing" ? "\n未保存・変更ありのモデル（パート）も一緒に保存します" : ""));
    if (!path) return;
  }
  try {
    const r = await rpc("doc.save", { path });
    const more = r.saved.length > 1 ? `（部品 ${r.saved.length - 1} 件も保存）` : "";
    if (r.warnings.length) toast(`保存しました: ${r.path}${more}\n${r.warnings.join("\n")}`, true);
    else toast(`保存しました: ${r.path}${more}`);
    render(await rpc("doc.state", { mesh: false }));
  } catch (e) { toast(e.message, true); }
}

/** パート・アセンブリを STL / 3MF / OBJ に書き出す（D71）。 */
async function exportMesh(format) {
  const st = app.state;
  if (!st || !["part", "assembly"].includes(st.kind)) { toast("書き出せるのはパートとアセンブリです（図面は図面のタブの PDF・DXF）", true); return; }
  const hint = (st.path ? st.path.replace(/\.(usda|ucpart|ucasm)$/i, "") : st.name) + "." + format;
  const path = await pickPath("save", hint, `書き出し先のパス（${format.toUpperCase()}。単位は mm）`, format);
  if (!path) return;
  const full = /\.[^\\/.]+$/.test(path) ? path : `${path}.${format}`;
  try {
    const r = await withProgress("書き出しています…", rpcRaw("doc.export", { doc: app.doc, path: full, quality: exportQuality }));
    toast(`書き出しました: ${r.files.join(", ")}（三角形 ${r.triangles.toLocaleString()}${r.objects > 1 ? `・部品 ${r.objects}` : ""}）`);
  } catch (e) { toast(e.message, true); }
}

async function openDoc() {
  const path = await pickPath("open", "", "開くファイルのパス（.usda。メッシュの .stl / .3mf / .obj は取り込んだパートになります。旧形式の .ucpart / .ucasm も開けます）");
  if (!path) return;
  try {
    await presentDoc(await withProgress("開いています…", rpcRaw("doc.open", { path })));
  } catch (e) { toast(e.message, true); }
}
$("#btn-open").onclick = openDoc;

const VIEW_KEYS = { 1: "front", 2: "back", 3: "left", 4: "right", 5: "top", 6: "bottom", 7: "iso" };

document.addEventListener("keydown", (e) => {
  const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
  const mod = e.ctrlKey || e.metaKey, key = e.key.toLowerCase();
  if (document.querySelector(".modal-back")) return;                 // 入力のダイアログが出ている間は何もしない
  if (e.key === "Escape" && !$("#ctx-menu").hidden) { $("#ctx-menu").hidden = true; viewer.setHover(-1); return; }
  if (e.key === "Escape" && app.measure && !typing) { measureEnd(); return; }
  if (mod && key === "s") { e.preventDefault(); save(e.shiftKey); return; }
  if (mod && key === "o") { e.preventDefault(); openDoc(); return; }
  if (mod && VIEW_KEYS[e.key]) { e.preventDefault(); viewer.setView(VIEW_KEYS[e.key]); return; }     // Ctrl+1〜7: 標準の表示方向
  if (mod && e.key === "8") { e.preventDefault(); normalTo(); return; }
  if (typing) return;
  if (mod && key === "z" && !e.shiftKey) { e.preventDefault(); if (app.state?.undo) act("edit.undo"); return; }
  if (mod && (key === "y" || (key === "z" && e.shiftKey))) { e.preventDefault(); if (app.state?.redo) act("edit.redo"); return; }
  if (mod && key === "b") { e.preventDefault(); act("doc.state"); return; }                           // 再構築（表示の更新）
  if (app.state?.kind === "drawing") { if (drawingKey(e)) e.preventDefault(); return; }
  if (asm.active && asm.mateMode && !typing) {
    if (e.key === "Escape") { if (asm.pendingMate) mateCancel(); else if (asm.sel.length) { asm.clearSelection(); renderAsmProps(); } else mateExit(false); return; }
    if (e.key === "Enter") { if (asm.pendingMate) mateConfirm(); else mateExit(true); return; }
  }
  if (asm.active && asm.motion && e.key === "Escape") { $("#btn-asm-motion").click(); return; }
  if (asm.active && asm.patternMode && e.key === "Escape") { asm.patternMode = null; asm.clearSelection(); renderAsmProps(); return; }
  if (asm.active && e.key === "Escape") { asm.clearSelection(); return; }
  if (sketch.active) {
    if (mod && key === "enter") { act("sketch.end"); return; }
    if (e.key === "Escape") { sketch.cancel(); return; }
    if (e.key === "Delete" || e.key === "Backspace") { sketch.deleteSelected(); return; }
    const t = TOOLS.find(([, , k]) => k && k.toLowerCase() === key);
    if (t && !mod) { sketch.setTool(t[0]); return; }
  } else if (app.state?.kind === "part") {
    if (e.key === "Escape") {
      if (app.pickFor) { app.pickFor = null; act("view.rollback", {}).then(() => showPick(-1, false)); return; }
      if (app.pending) { confirmPending(false); return; }
      app.selEdges.clear(); app.selPlane = null; app.pickedName = null; viewer.setSelected(-1); markPlaneRows(); updateButtons(); showPick(-1, false);
      return;
    }
    if (e.key === "Enter" && app.pending) { confirmPending(true); return; }
    if (e.key === "F2" && app.selected) { e.preventDefault(); renameFeature(app.selected); return; }
    if (e.key === "Delete" && app.selected) { deleteFeature(app.selected); return; }
  }
  // 矢印キー: 15° ずつ回転（Shift で 90°）。Z / Shift+Z: 縮小 / 拡大。F: 全体表示
  const step = e.shiftKey ? 90 : 15;
  if (e.key === "ArrowLeft") { viewer.animateTo({ yaw: viewer.yaw - step }, 80); return; }
  if (e.key === "ArrowRight") { viewer.animateTo({ yaw: viewer.yaw + step }, 80); return; }
  if (e.key === "ArrowUp") { viewer.animateTo({ pitch: Math.min(89.999, viewer.pitch + step) }, 80); return; }
  if (e.key === "ArrowDown") { viewer.animateTo({ pitch: Math.max(-89.999, viewer.pitch - step) }, 80); return; }
  if (key === "z" && !mod) { viewer.animateTo({ dist: viewer.dist * (e.shiftKey ? 0.8 : 1.25) }, 80); return; }
  if (key === "f" && !mod) viewer.fit();
});

// ---------------------------------------------------------------- 下の層（core.js・props.js など）から呼ばれる関数
Object.assign(ui, { render, updateButtons, deleteFeature, showPick, sketchShowItem, renderSketchProps, renderAsmProps });

// ---------------------------------------------------------------- 表示の設定（D66）
// 一般的な 3D CAD が普段は出さないもの（体積）は既定で非表示（サーバも計算しない）。切り替えはブラウザに覚えておく
const SETTINGS_KEY = "agentcad.settings";
function loadSettings() { try { return JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}"); } catch { return {}; } }
function saveSettings(s) { try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(s)); } catch { /* 覚えられなくても動く */ } }
$("#show-volume").onchange = async (e) => {
  const s = { ...loadSettings(), show_volume: e.target.checked };
  saveSettings(s);
  await rpcRaw("ui.settings", { show_volume: s.show_volume });
  if (app.state?.kind === "part") render(await rpc("doc.state", { mesh: false }));
};

// ---------------------------------------------------------------- AI アシスタント（D67）
// AI が作った文書はこのウィンドウに出し、画面だけの操作（断面・視点・表示スタイル）は断面のパネルなどと同じ関数で行う
async function agentShowDoc(doc) {
  if (!doc || doc === app.doc) return;
  try { await presentDoc(await rpcRaw("doc.state", { doc }), true); refreshDocs(); } catch (e) { toast(e.message, true); }
}
async function agentUi(action, a) {
  if (action === "show_document") return agentShowDoc(a.doc);
  if (action === "set_view") return viewer.setView(a.view || "iso");
  if (action === "fit") return viewer.fit();
  if (action === "set_style") { document.querySelector(`#style-menu [data-style=${a.style}]`)?.click(); return; }
  if (action === "section_off") return sectionOff();
  if (action === "normal_to") { app.pickedName = a.face || null; app.selPlane = null; return normalTo(); }
  if (action === "section") {
    let ok;
    if (a.face) { app.pickedName = a.face; app.selPlane = null; ok = sectionPlane("sel"); }
    else ok = sectionPlane(["Front", "Top", "Right"].includes(a.plane) ? a.plane : "Front");
    if (!ok) return;
    app.section.offset = Number(a.offset) || 0; app.section.flip = !!a.flip;
    sectionApply(); sectionUi();
  }
}
initAgent({ showDoc: agentShowDoc, ui: agentUi });

// ---------------------------------------------------------------- 起動
(async () => {
  try {
    app.shellWindows = !!(await rpcRaw("kernel.info")).shell_windows;
    const saved = loadSettings();
    $("#show-volume").checked = !!saved.show_volume;
    await rpcRaw("ui.settings", { show_volume: !!saved.show_volume });
    const docs = await rpcRaw("doc.list");
    const want = new URLSearchParams(location.search).get("doc");      // ?doc=…: その文書のウィンドウ
    const first = docs.find((d) => d.doc === want) || docs[0];
    const st = first ? await rpcRaw("doc.state", { doc: first.doc }) : await rpcRaw("doc.demo");
    app.doc = st.doc;
    render(st);
    document.body.dataset.ready = "1";
  } catch (e) {
    toast(`起動に失敗しました: ${e.message}`, true);
    document.body.dataset.ready = "error";
  }
})();

// 開発・自動確認用（scripts/ui_smoke.py が使う）
window.__agentcad = { viewer, sketch, asm, drawing, app, refresh: async () => render(await rpc("doc.state")), normalTo, visibleSketches };
