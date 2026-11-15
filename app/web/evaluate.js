// 選択フィルタ・頂点の選択・計測・質量特性（D55）。計算は select.js。
import { ICONS } from "./toolbar.js";
import { modelGeometry, measure, massProps, fmt } from "./select.js";
import { $, app, edgeAt, el, fill, toast, ui, viewer } from "./core.js";

// ---------------------------------------------------------------- 選択フィルタ・頂点・計測（D55）
/** 画面位置の近く（8px 以内）の、見えているモデルの頂点。{w, key}。 */
export function vertexAt(pos, face) {
  const G = modelGeometry(app.mesh); if (!G || !pos) return null;
  let best = null, bd = 8;
  for (const s of G.snaps) {
    if (s.kind !== "vertex") continue;
    const q = viewer.project(s.w); if (!q) continue;
    const d = Math.hypot(q[0] - pos[0], q[1] - pos[1]);
    if (d >= bd) continue;
    // 隠れていないか: その位置に見えている面が、この頂点につながるエッジの面なら見えている
    const k = s.w.map((v) => Math.round(v * 1e4)).join(",");
    const touching = G.edges.filter((g) => !g.closed && [g.a, g.b].some((p) => p.map((v) => Math.round(v * 1e4)).join(",") === k));
    if (face >= 0 && !touching.some((g) => (app.mesh.edgeFaces[g.index] || []).includes(face))) continue;
    bd = d; best = { w: s.w };
  }
  return best;
}

function setFilter(f) {
  app.filter = f;
  for (const b of document.querySelectorAll("#filter-menu [data-filter]")) b.classList.toggle("on", b.dataset.filter === f);
  $("#btn-filter .ic").innerHTML = ICONS[`f_${f}`];
  $("#btn-filter").classList.toggle("on", f !== "all");
}
document.querySelectorAll("#filter-menu [data-filter]").forEach((b) => (b.onclick = () => setFilter(b.dataset.filter)));
setFilter("all");

const itemName = (it) => it.kind === "face" ? `面 ${app.mesh.faceNames[it.i]}` : it.kind === "edge" ? `エッジ ${app.mesh.edgeNames[it.i]}`
  : `頂点 (${it.w.map((v) => fmt(v)).join(", ")})`;
const sameItem = (a, b) => a.kind === b.kind && (a.kind === "vertex" ? a.w.every((v, k) => Math.abs(v - b.w[k]) < 1e-9) : a.i === b.i);

function measureStart() {
  if (app.state?.kind !== "part" || !app.mesh) { toast("計測するモデルがありません", true); return; }
  if (app.state.sketch) { toast("スケッチを終えてから計測してください", true); return; }
  app.measure = { items: [] };
  app.selEdges.clear(); app.pickedName = null; viewer.setSelected(-1);
  $("#btn-measure").classList.add("on");
  renderMeasure();
}
export function measureEnd() {
  if (!app.measure) return;
  app.measure = null; app.selEdges.clear(); viewer.setSelected(-1);
  $("#btn-measure").classList.remove("on");
  $("#float-panel").hidden = true; viewer.redraw(); ui.updateButtons();
}

export function measurePick(i, pos, ev) {
  const F = app.filter;
  const v = F === "all" || F === "vertex" ? vertexAt(pos, i) : null;
  const e = !v && (F === "all" || F === "edge") ? edgeAt(pos, i) : null;
  const it = v ? { kind: "vertex", w: v.w } : e !== null ? { kind: "edge", i: e } : i >= 0 && (F === "all" || F === "face") ? { kind: "face", i } : null;
  const M = app.measure;
  if (!it) { if (!ev?.ctrlKey) M.items = []; }                       // 何もない所: 選び直し
  else {
    const k = M.items.findIndex((x) => sameItem(x, it));
    if (k >= 0) M.items.splice(k, 1); else M.items.push(it);
  }
  renderMeasure();
}

export function renderMeasure() {
  const M = app.measure; if (!M) return;
  const faces = M.items.filter((x) => x.kind === "face").map((x) => x.i);
  viewer.setSelected(faces[0] ?? -1, faces[1] ?? -1);
  app.selEdges = new Set(M.items.filter((x) => x.kind === "edge").map((x) => app.mesh.edgeNames[x.i]));
  M.result = measure(app.mesh, M.items);
  const p = $("#float-panel");
  fill(p, 
    el("h4", {}, "計測", el("button", { id: "btn-measure-close", title: "計測を終える (Esc)", onclick: measureEnd }, "×")),
    M.items.length ? el("ul", {}, M.items.map((x) => el("li", {}, itemName(x)))) : null,
    M.result.rows.length ? el("table", { id: "measure-table" }, M.result.rows.map(([k, v]) => el("tr", {}, el("td", {}, k), el("td", {}, v)))) : null,
    el("div", { class: "hint" }, M.items.length ? "続けてクリックで追加、同じものをクリックで外す、何もない所で選び直し。"
      : "面・エッジ・頂点をクリック（頂点は角に近づけると選べます。選択フィルタで絞れます）"),
  );
  p.hidden = false;
  viewer.redraw();
}

/** 計測の寸法線と、選んだ頂点・カーソルの近くの頂点の印。 */
export function drawMeasure() {
  const ctx = $("#overlay").getContext("2d");
  const mark = (w, color) => { const q = viewer.project(w); if (!q) return; ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.strokeRect(q[0] - 5, q[1] - 5, 10, 10); };
  if (app.hoverVertex) mark(app.hoverVertex.w, "#18a058");
  const M = app.measure; if (!M) return;
  for (const it of M.items) if (it.kind === "vertex") mark(it.w, "#ff8a00");
  for (const L of M.result?.lines || []) {
    const a = viewer.project(L.a), b = viewer.project(L.b); if (!a || !b) continue;
    ctx.strokeStyle = "#d0342c"; ctx.lineWidth = 1.5; ctx.setLineDash([6, 3]);
    ctx.beginPath(); ctx.moveTo(...a); ctx.lineTo(...b); ctx.stroke(); ctx.setLineDash([]);
    const m = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    ctx.font = "bold 12px sans-serif"; ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
    const w = ctx.measureText(L.label).width + 8;
    ctx.fillStyle = "#fff"; ctx.fillRect(m[0] - w / 2, m[1] - 9, w, 18);
    ctx.strokeStyle = "#d0342c"; ctx.lineWidth = 1; ctx.strokeRect(m[0] - w / 2, m[1] - 9, w, 18);
    ctx.fillStyle = "#d0342c"; ctx.fillText(L.label, m[0] - w / 2 + 4, m[1] + 4);
  }
}

function showMassProps() {
  if (app.state?.kind !== "part" || !app.mesh) { toast("ボディがありません", true); return; }
  measureEnd();
  const r = massProps(app.mesh), xyz = (p) => `(${p.map((v) => fmt(v)).join(", ")})`;
  const p = $("#float-panel");
  fill(p, 
    el("h4", {}, "質量特性", el("button", { title: "閉じる", onclick: () => { p.hidden = true; } }, "×")),
    el("table", { id: "mass-table" },
      [["体積", `${fmt(r.volume)} mm³`], ["表面積", `${fmt(r.area)} mm²`], ["重心", r.centroid ? xyz(r.centroid) : "—"],
        ["外形 X × Y × Z", `${fmt(r.size[0])} × ${fmt(r.size[1])} × ${fmt(r.size[2])} mm`]]
        .map(([k, v]) => el("tr", {}, el("td", {}, k), el("td", {}, v)))),
    el("div", { class: "hint" }, "表示用メッシュから計算しています（曲面はわずかに小さめに出ます）。密度は設定していないので質量は出しません。"));
  p.hidden = false;
}
$("#btn-measure").onclick = () => (app.measure ? measureEnd() : measureStart());
$("#btn-massprops").onclick = () => showMassProps();
