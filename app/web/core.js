// 画面の土台: DOM の小道具、通信（RPC）、共有の状態 app、3D ビューとスケッチ / アセンブリの編集器、メニューと入力ダイアログ。
// ほかのモジュールはここから import する。上の層（app.js など）の関数は ui（あとから登録される表）経由で呼ぶ。
import { ICONS } from "./toolbar.js";                       // 先にツールバーを組み立てる（下で DOM を引く）
import { Viewer } from "./viewer.js";
import { SketchEditor } from "./sketch.js";
import { AsmEditor } from "./asm.js";

export const $ = (s) => document.querySelector(s);
export const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) e.append(k);
  return e;
};

/** アイコン（toolbar.js の ICONS）の要素。 */
export const iconEl = (name) => { const s = el("span", { class: "ic" }); s.innerHTML = ICONS[name] || ""; return s; };

/** 子を入れ替える（null・false は飛ばす。replaceChildren は null を "null" という文字にしてしまう）。 */
export const fill = (node, ...kids) => node.replaceChildren(...kids.flat().filter((k) => k !== null && k !== undefined && k !== false));

// ---------------------------------------------------------------- 通信
let rpcSeq = 0;
/** このウィンドウの ID。サーバは変更したウィンドウを覚え、ほかのウィンドウは ws.rev で気づいて表示を更新する。 */
export const CLIENT_ID = Math.random().toString(36).slice(2);
export async function rpcRaw(method, params = {}) {
  const res = await fetch("/rpc", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: ++rpcSeq, method, params: { ...params, _client: CLIENT_ID } }) });
  const j = await res.json();
  if (j.error) throw new Error(j.error.message);
  return j.result;
}
export const rpc = (method, params = {}) => rpcRaw(method, { doc: app.doc, ...params });

export function toast(msg, error = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = "show" + (error ? " error" : "");
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.className = ""), error ? 5000 : 2000);
}

export async function act(method, params) {
  try {
    const st = await rpc(method, params);
    if (st && st.kind) ui.render(st);
    return st;
  } catch (e) { toast(e.message, true); return null; }
}

// ---------------------------------------------------------------- メッシュの復元
function b64(s, Type) {
  const bin = atob(s), u8 = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  return new Type(u8.buffer);
}
export function decodeMesh(m) {
  if (!m) return null;
  return { positions: b64(m.positions, Float32Array), indices: b64(m.indices, Uint32Array),
    faceOfTriangle: b64(m.face_of_triangle, Uint32Array), edges: b64(m.edges, Float32Array),
    faceNames: m.face_names, bbox: m.bbox, edgeOfSegment: b64(m.edge_of_segment, Uint32Array),
    edgeNames: m.edge_names, edgeFaces: m.edge_faces, faceColors: m.face_colors, partColor: m.part_color };
}

// ---------------------------------------------------------------- 状態
export const app = { doc: null, state: null, selected: null, mesh: null, firstMesh: true, pickedName: null,
  selEdges: new Set(), hoverEdge: null, hoverSketch: null,
  filter: "all", hoverVertex: null,
  measure: null,            // 計測中: { items: [{kind, i | w}] }（D55）
  selPlane: null,      // ツリーで選んだ平面の参照（{plane: "Front"} / {feature: id}）
  pending: null,       // 作ったばかりで、まだ ✓ / ✗ を押していないフィーチャーの ID
  pickFor: null,       // 3D で面・エッジを選んでフィーチャーの設定に入れる途中（{id, key, want: ["face", "edge"]}）
  section: null };     // 断面表示（D62）: { plane, origin, normal, offset, flip }
export const ICON = { sketch: "✎", extrude: "▰", revolve: "⟳", fillet: "◜", plane: "▱", lpattern: "⋯", cpattern: "◌", mirror: "⇋",
  chamfer: "◢", shell: "▢", hole: "◎", sweep: "∿", import: "△" };
export const sameRef = (a, b) => !!a && !!b && JSON.stringify(a) === JSON.stringify(b);
/** 面の名前 → それを作ったフィーチャーの ID（パターンの面は「ID~番号/…」）。 */
export const featureOfFace = (name) => name.split("/")[0].split("~")[0];
export const STATE_BADGE = { error: "✖", warning: "⚠", suppressed: "抑制", rolled_back: "" };
export const SOLVE_LABEL = { full: "完全定義", under: "未定義", over: "過剰定義" };

/** 画面位置の近く（6px 以内）にあるエッジの番号。face はその位置に見えている面（隠れたエッジを除くのに使う）。 */
export function edgeAt(pos, face) {
  const m = app.mesh;
  if (!m || !m.edgeNames.length) return null;
  let best = null, bd = 6;
  const E = m.edges;
  for (let k = 0; k < m.edgeOfSegment.length; k++) {
    const ei = m.edgeOfSegment[k];
    if (face >= 0 && !m.edgeFaces[ei].includes(face)) continue;
    const a = viewer.project([E[6 * k], E[6 * k + 1], E[6 * k + 2]]), b = viewer.project([E[6 * k + 3], E[6 * k + 4], E[6 * k + 5]]);
    if (!a || !b) continue;
    const dx = b[0] - a[0], dy = b[1] - a[1], l = dx * dx + dy * dy;
    const t = l ? Math.max(0, Math.min(1, ((pos[0] - a[0]) * dx + (pos[1] - a[1]) * dy) / l)) : 0;
    const d = Math.hypot(a[0] + t * dx - pos[0], a[1] + t * dy - pos[1]);
    if (d < bd) { bd = d; best = ei; }
  }
  return best;
}

export const fmtLen = (v) => (Math.round(v * 1000) / 1000).toString();

/** エッジの長さ（mm）。 */
export function edgeLength(ei) {
  const m = app.mesh, E = m.edges; let L = 0;
  for (let k = 0; k < m.edgeOfSegment.length; k++) if (m.edgeOfSegment[k] === ei)
    L += Math.hypot(E[6 * k + 3] - E[6 * k], E[6 * k + 4] - E[6 * k + 1], E[6 * k + 5] - E[6 * k + 2]);
  return L;
}

/** 面の面積（mm²）。 */
export function faceArea(fi) {
  const m = app.mesh, P = m.positions, I = m.indices; let A = 0;
  for (let t = 0; t < m.faceOfTriangle.length; t++) {
    if (m.faceOfTriangle[t] !== fi) continue;
    const a = 3 * I[3 * t], b = 3 * I[3 * t + 1], c = 3 * I[3 * t + 2];
    const u = [P[b] - P[a], P[b + 1] - P[a + 1], P[b + 2] - P[a + 2]], v = [P[c] - P[a], P[c + 1] - P[a + 1], P[c + 2] - P[a + 2]];
    A += Math.hypot(u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]) / 2;
  }
  return A;
}

// ---------------------------------------------------------------- メニュー（右クリック）
/** 画面の (x, y) にメニューを出す。items: { label, key, onclick, disabled } か "-"（区切り）。 */
export function showMenu(x, y, items) {
  const m = $("#ctx-menu");
  m.replaceChildren(...items.filter(Boolean).map((it) => it === "-" ? el("hr") : el("button", {
    disabled: !!it.disabled, onclick: () => { m.hidden = true; it.onclick(); },
    onmouseenter: it.onhover ? () => it.onhover(true) : null, onmouseleave: it.onhover ? () => it.onhover(false) : null,
  }, el("span", {}, it.label), it.key ? el("kbd", {}, it.key) : null)));
  m.hidden = false;
  const r = m.getBoundingClientRect();
  m.style.left = `${Math.max(4, Math.min(x, window.innerWidth - r.width - 4))}px`;
  m.style.top = `${Math.max(4, Math.min(y, window.innerHeight - r.height - 4))}px`;
}
document.addEventListener("pointerdown", (e) => { if (!e.target.closest("#ctx-menu")) $("#ctx-menu").hidden = true; }, true);

/** 外観の色見本（D69）。金属・樹脂・塗装でよく使う色 */
export const SWATCHES = ["#c9ced6", "#9aa3ad", "#5b6470", "#2b2f36", "#f2f2f0", "#d9b26f", "#c87533", "#b33a3a",
  "#e0663a", "#e8c547", "#5d9b4f", "#3f7fbf", "#2f4f8f", "#7a55a6", "#3aa6a0", "#8a6a4f"];

/** 色を選ぶ小さなパレット（右クリックメニューの場所に出す）。onPick(色 "#rrggbb" | null = 解除)。 */
export function showPalette(x, y, title, current, onPick) {
  const m = $("#ctx-menu");
  const done = (c) => { m.hidden = true; onPick(c); };
  const other = el("input", { type: "color", value: current || "#b8c2d4", class: "pal-native",
    onchange: (e) => done(e.target.value) });
  m.replaceChildren(el("div", { class: "pal-title" }, title),
    el("div", { class: "pal-grid" }, ...SWATCHES.map((c) => el("button", {
      class: `pal-sw${c === current ? " on" : ""}`, title: c, style: `background:${c}`, onclick: () => done(c) }))),
    el("label", { class: "pal-more" }, "その他の色…", other),
    el("hr"),
    el("button", { disabled: !current, onclick: () => done(null) }, el("span", {}, "色を解除")));
  m.hidden = false;
  const r = m.getBoundingClientRect();
  m.style.left = `${Math.max(4, Math.min(x, window.innerWidth - r.width - 4))}px`;
  m.style.top = `${Math.max(4, Math.min(y, window.innerHeight - r.height - 4))}px`;
}

/** 文字の入力欄（画面内のダイアログ）。取り消しは null。ブラウザの prompt() は Tauri の窓で使えないことがあるので使わない。 */
export function askText(message, value = "") {
  return new Promise((resolve) => {
    const input = el("input", { type: "text", value: String(value ?? ""), spellcheck: "false" });
    const done = (v) => { back.remove(); resolve(v); };
    const back = el("div", { class: "modal-back", onpointerdown: (e) => { if (e.target === back) done(null); } },
      el("form", { class: "modal", onsubmit: (e) => { e.preventDefault(); done(input.value); } },
        el("div", { class: "modal-msg" }, message), input,
        el("div", { class: "btns" },
          el("button", { type: "button", onclick: () => done(null) }, "キャンセル"),
          el("button", { type: "submit", class: "primary" }, "OK"))));
    input.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Escape") done(null); });
    document.body.append(back);
    input.focus(); input.select();
  });
}

/** 時間のかかる処理（ファイルを開く）の間、進み具合を出す（D69）。0.3 秒で終われば何も出さない。
 *  サーバの ui.progress（HTTP のロックの外）を 0.2 秒ごとに見て、何番目のフィーチャーを作っているかを棒で出す。 */
export async function withProgress(title, promise) {
  let box = null, stop = false;
  const bar = el("div", { class: "prog-bar" }, el("div", { class: "prog-fill" }));
  const head = el("div", { class: "prog-title" }, title), step = el("div", { class: "prog-step" }, "");
  const show = () => { box = el("div", { class: "modal-back prog-back" }, el("div", { class: "modal prog" }, head, bar, step)); document.body.append(box); };
  const poll = async () => {
    while (!stop) {
      await new Promise((r) => setTimeout(r, 200));
      if (stop) break;
      if (!box) show();
      let p = null;
      try { p = await rpcRaw("ui.progress"); } catch { /* 古いサーバ */ }
      if (!p || stop) continue;
      head.textContent = p.title || title;
      const frac = p.total ? Math.min(1, (p.done + 1) / p.total) : 0;
      bar.firstChild.style.width = `${Math.round(100 * frac)}%`;
      bar.classList.toggle("busy", !p.total);
      step.textContent = p.total > 1 ? `${p.step}（${p.done + 1} / ${p.total}）` : (p.step || "");
    }
  };
  const t = setTimeout(() => poll(), 100);
  try { return await promise; } finally { stop = true; clearTimeout(t); box?.remove(); }
}

/** 開く・保存のパスを選ぶ: OS のファイルダイアログ。出せない環境ではパスの入力欄。取り消しは null。 */
export async function pickPath(mode, hint, message, ext = "usda") {
  let r;
  try { r = await rpcRaw("ui.pick_path", { mode, ext, ...(mode === "save" ? { doc: app.doc } : {}) }); }
  catch { r = { available: false }; }
  if (r.available) return r.path;
  const v = await askText(message, hint);
  return v ? v.trim().replace(/^"|"$/g, "") : null;
}


/** 上の層（app.js など）の関数を、下の層から呼ぶための表。app.js が起動時に登録する。 */
export const ui = {};

// ---------------------------------------------------------------- 3D ビューと編集器（入力の配線は app.js / sketch_ui.js / asm_ui.js）
export const viewer = new Viewer($("#view"));
export const sketch = new SketchEditor(viewer, $("#overlay"), {
  rpc, toast, onState: (st) => ui.render(st), onSelection: () => ui.renderSketchProps(), ask: (m, v) => askText(m, v),
});
export const asm = new AsmEditor(viewer, { rpc, toast, decode: decodeMesh, onState: (st) => ui.render(st),
  onSelection: () => ui.renderAsmProps() });
