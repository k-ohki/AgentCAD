// 上のツールバー（一般的な 3D CAD の配置に合わせる。D53）。
//   1 段目 = 標準ツールバー（新規・開く・保存・元に戻す・文書・ウィンドウ）
//   2 段目 = CommandManager のタブ（フィーチャー / スケッチ / アセンブリ）
//   3 段目 = タブの中身。よく使うものは大きいボタン（アイコンの下に名前）、仲間は小さいボタンを縦に積む。
//            スケッチのタブは「スケッチ」「スマート寸法」を左端に大きく置く（一般的な 3D CAD と同じ）
// 表示のツール（全体表示・正対・表示方向・表示スタイル・透視）は 3D ビューの上の中央（ヘッドアップ ツールバー）。
// ここは DOM を組み立てるだけ。動作は app.js が ID で付ける（この模組は app.js より先に評価される）。

const S = (body) => `<svg viewBox="0 0 24 24" aria-hidden="true">${body}</svg>`;
// 色: 線 = currentColor、面 = 薄い青 / 黄（一般的な 3D CAD のアイコンに近い 2 色）
const F = 'fill="#cfe0f7" stroke="currentColor"', Y = 'fill="#ffe08a" stroke="currentColor"', N = 'fill="none" stroke="currentColor"';
const R = 'fill="#f6c6c0" stroke="currentColor"';

/** アイコン（24×24 の SVG）。 */
export const ICONS = {
  new: S(`<path ${F} d="M6 3h8l4 4v14H6z"/><path ${N} d="M14 3v4h4"/><path stroke="#1e8e3e" stroke-width="2" d="M12 11v6M9 14h6"/>`),
  part: S(`<path ${F} d="M4 8l8-4 8 4v8l-8 4-8-4z"/><path ${N} d="M4 8l8 4 8-4M12 12v8"/>`),
  assembly: S(`<path ${F} d="M3 10l5-2.5 5 2.5v5l-5 2.5-5-2.5z"/><path ${Y} d="M11 7l5-2.5 5 2.5v5l-5 2.5-5-2.5z"/>`),
  demo: S(`<path ${Y} d="M4 8l8-4 8 4v8l-8 4-8-4z"/><circle ${N} cx="12" cy="11" r="2.5"/>`),
  open: S(`<path ${Y} d="M3 6h6l2 2h10v11H3z"/><path ${N} d="M3 10h18"/>`),
  save: S(`<path ${F} d="M4 4h13l3 3v13H4z"/><path ${N} d="M7 4v5h9V4M7 20v-6h10v6"/>`),
  undo: S(`<path ${N} stroke-width="2" d="M9 7L4 12l5 5"/><path ${N} stroke-width="2" d="M4 12h10a6 6 0 0 1 0 12"/>`),
  redo: S(`<path ${N} stroke-width="2" d="M15 7l5 5-5 5"/><path ${N} stroke-width="2" d="M20 12H10a6 6 0 0 0 0 12"/>`),
  ai: S(`<path fill="#ffe08a" stroke="#b7791f" d="M10 3l1.6 4.4L16 9l-4.4 1.6L10 15l-1.6-4.4L4 9l4.4-1.6z"/><path fill="#cfe0f7" stroke="#1e6fff" d="M17.5 13l.9 2.6 2.6.9-2.6.9-.9 2.6-.9-2.6-2.6-.9 2.6-.9z"/>`),
  window: S(`<rect ${F} x="3" y="5" width="12" height="10" rx="1"/><rect ${F} x="9" y="9" width="12" height="10" rx="1"/><path ${N} d="M9 12h12"/>`),
  // スケッチ
  sketch: S(`<rect ${N} x="3" y="9" width="12" height="11"/><path ${Y} d="M14 14l7-7-2-2-7 7-1 3z"/>`),
  sketchExit: S(`<rect ${N} x="3" y="9" width="12" height="11"/><path stroke="#1e8e3e" stroke-width="2.5" fill="none" d="M11 8l3 3 7-7"/>`),
  note: S(`<path ${N} d="M4 4h16v12H10l-4 4v-4H4z"/><text x="8" y="13.5" font-size="8" fill="#1e6fff" stroke="none" font-family="sans-serif">A</text>`),
  dim: S(`<path ${N} d="M4 18L18 4"/><path stroke="#1e6fff" stroke-width="1.6" fill="none" d="M4 21h16M4 19v4M20 19v4M6 20l-2 1 2 1M18 20l2 1-2 1"/><text x="9" y="13" font-size="7" fill="#1e6fff" stroke="none" font-family="sans-serif">20</text>`),
  select: S(`<path fill="#fff" stroke="currentColor" d="M6 3l12 9-5 1 3 6-2 1-3-6-4 3z"/>`),
  line: S(`<path ${N} stroke-width="1.8" d="M4 19L20 5"/><circle fill="currentColor" cx="4" cy="19" r="1.6"/><circle fill="currentColor" cx="20" cy="5" r="1.6"/>`),
  cline: S(`<path ${N} stroke-width="1.5" stroke-dasharray="5 2 1 2" d="M3 20L21 4"/>`),
  rect: S(`<rect ${N} stroke-width="1.8" x="4" y="6" width="16" height="12"/><circle fill="currentColor" cx="4" cy="18" r="1.5"/><circle fill="currentColor" cx="20" cy="6" r="1.5"/>`),
  crect: S(`<rect ${N} stroke-width="1.8" x="4" y="6" width="16" height="12"/><path ${N} d="M10 12h4M12 10v4"/>`),
  circle: S(`<circle ${N} stroke-width="1.8" cx="12" cy="12" r="8"/><path ${N} d="M10.5 12h3M12 10.5v3"/>`),
  arc: S(`<path ${N} stroke-width="1.8" d="M4 16a8 8 0 0 1 16 0"/><path ${N} stroke-dasharray="2 2" d="M12 16L4 16M12 16l8 0"/><circle fill="currentColor" cx="12" cy="16" r="1.4"/>`),
  arc3: S(`<path ${N} stroke-width="1.8" d="M4 17a9 9 0 0 1 16 0"/><circle fill="currentColor" cx="4" cy="17" r="1.5"/><circle fill="currentColor" cx="20" cy="17" r="1.5"/><circle fill="currentColor" cx="12" cy="8" r="1.5"/>`),
  tarc: S(`<path ${N} stroke-width="1.8" d="M3 18h8a6 6 0 0 0 6-6V5"/><circle fill="currentColor" cx="11" cy="18" r="1.5"/>`),
  slot: S(`<path ${N} stroke-width="1.8" d="M8 7h8a5 5 0 0 1 0 10H8A5 5 0 0 1 8 7z"/><path ${N} stroke-dasharray="2 2" d="M8 12h8"/>`),
  polygon: S(`<path ${N} stroke-width="1.8" d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/>`),
  point: S(`<circle fill="currentColor" cx="12" cy="12" r="3"/><path ${N} d="M12 4v4M12 16v4M4 12h4M16 12h4"/>`),
  trim: S(`<path ${N} stroke-width="1.8" d="M3 12h7M17 12h4"/><path stroke="#d0342c" stroke-width="1.6" stroke-dasharray="2 2" d="M10 12h7"/><path ${N} d="M12 4v16"/><path stroke="#d0342c" stroke-width="2" d="M15 9l-4 6M11 9l4 6"/>`),
  extend: S(`<path ${N} stroke-width="1.8" d="M3 12h9"/><path stroke="#1e6fff" stroke-width="1.6" stroke-dasharray="2 2" d="M12 12h6"/><path ${N} stroke-width="1.8" d="M19 4v16"/><path stroke="#1e6fff" d="M15 10l3 2-3 2"/>`),
  sfillet: S(`<path ${N} stroke-width="1.8" d="M4 4v10a6 6 0 0 0 6 6h10"/>`),
  schamfer: S(`<path ${N} stroke-width="1.8" d="M4 4v11l5 5h11"/>`),
  offset: S(`<path ${N} stroke-width="1.8" d="M4 18c3-8 13-8 16 0"/><path stroke="#1e6fff" fill="none" stroke-dasharray="3 2" d="M4 12c3-7 13-7 16 0"/>`),
  convert: S(`<path ${F} d="M3 9l6-3 6 3v7l-6 3-6-3z"/><path ${N} d="M3 9l6 3 6-3M9 12v7"/><path stroke="#1e6fff" stroke-width="2" fill="none" d="M15 9l6-3"/><path stroke="#1e6fff" fill="none" d="M18 4l3 2-2 3"/>`),
  smirror: S(`<path ${N} stroke-dasharray="3 2" d="M12 3v18"/><path ${N} stroke-width="1.6" d="M9 6L4 18h5zM15 6l5 12h-5z"/>`),
  // フィーチャー
  extrude: S(`<path ${F} d="M4 15l8-4 8 4-8 4z"/><path ${F} d="M4 15V8l8-4 8 4v7l-8 4z"/><path ${N} d="M4 8l8 4 8-4M12 12v7"/><path stroke="#1e8e3e" stroke-width="2" d="M12 2v0"/>`),
  revolve: S(`<path ${F} d="M7 5h6v14H7z"/><path ${N} stroke-dasharray="3 2" d="M5 3v18"/><path stroke="#1e8e3e" stroke-width="1.8" fill="none" d="M17 7a7 7 0 0 1 0 10"/><path stroke="#1e8e3e" d="M15 17l2 1 1-2"/>`),
  sweep: S(`<path fill="none" stroke="#cfe0f7" stroke-width="6" stroke-linecap="round" d="M5 19V12a6 6 0 0 1 6-6h8"/><path fill="none" stroke="currentColor" d="M2.5 19V12a8.5 8.5 0 0 1 8.5-8.5h8M7.5 19V12a3.5 3.5 0 0 1 3.5-3.5h8"/><ellipse ${Y} cx="5" cy="19.5" rx="2.6" ry="1.4"/><path stroke="#1e6fff" stroke-dasharray="2 1.5" fill="none" d="M5 19V12a6 6 0 0 1 6-6h9"/>`),
  cut: S(`<path ${F} d="M3 9l9-4.5L21 9v7l-9 4.5L3 16z"/><path ${R} d="M9 10l3-1.5 3 1.5v4l-3 1.5-3-1.5z"/><path ${N} d="M3 9l9 4.5L21 9"/>`),
  rcut: S(`<path ${F} d="M7 5h6v14H7z"/><path ${R} d="M10 9h3v6h-3z"/><path ${N} stroke-dasharray="3 2" d="M5 3v18"/><path stroke="#d0342c" stroke-width="1.8" fill="none" d="M17 7a7 7 0 0 1 0 10"/>`),
  fillet: S(`<path ${F} d="M3 20V11a7 7 0 0 1 7-7h10v16z"/><path stroke="#1e6fff" stroke-width="2.2" fill="none" d="M3 11a7 7 0 0 1 7-7"/>`),
  chamfer: S(`<path ${F} d="M3 20V10l6-6h11v16z"/><path stroke="#1e6fff" stroke-width="2.2" d="M3 10l6-6"/>`),
  shell: S(`<path ${F} d="M3 8l9-4 9 4v9l-9 4-9-4z"/><path fill="#fff" stroke="currentColor" d="M7 8.5l5-2 5 2-5 2.2z"/><path ${N} d="M3 8l9 4 9-4M12 12v9"/>`),
  plane: S(`<path fill="#ffe7b3" stroke="#b7791f" stroke-width="1.6" d="M3 17l5-10h13l-5 10z"/>`),
  lpattern: S(`<rect ${F} x="3" y="13" width="5" height="5"/><rect ${F} x="10" y="13" width="5" height="5"/><rect ${F} x="17" y="13" width="5" height="5"/><rect ${F} x="3" y="6" width="5" height="5"/><path stroke="#1e6fff" fill="none" d="M10 8h11M19 6l2 2-2 2"/>`),
  cpattern: S(`<circle ${N} stroke-dasharray="2 2" cx="12" cy="12" r="7"/><circle ${F} cx="12" cy="5" r="2.4"/><circle ${F} cx="19" cy="12" r="2.4"/><circle ${F} cx="12" cy="19" r="2.4"/><circle ${F} cx="5" cy="12" r="2.4"/>`),
  mirror: S(`<path stroke="#b7791f" stroke-dasharray="3 2" d="M12 2v20"/><path ${F} d="M3 7h6v10H3z"/><path fill="#e8eef7" stroke="currentColor" stroke-dasharray="2 1.5" d="M15 7h6v10h-6z"/>`),
  // アセンブリ
  mate: S(`<path ${N} stroke-width="1.8" d="M9 15l6-6"/><path ${N} stroke-width="1.8" d="M8 11l-3 3a3.5 3.5 0 0 0 5 5l3-3M16 13l3-3a3.5 3.5 0 0 0-5-5l-3 3"/>`),
  insert: S(`<path ${F} d="M3 9l7-3.5L17 9v7l-7 3.5L3 16z"/><path stroke="#1e8e3e" stroke-width="2" d="M19 3v6M16 6h6"/>`),
  // 合致の種類（ポップアップ）
  m_coincident: S(`<path ${F} d="M3 10l9-4 9 4-9 4z"/><path fill="#ffe08a" stroke="currentColor" d="M3 14l9-4 9 4-9 4z" opacity=".85"/>`),
  m_parallel: S(`<path ${N} stroke-width="2" d="M5 18L15 4M10 20L20 6"/>`),
  m_perpendicular: S(`<path ${N} stroke-width="2" d="M4 20h16M12 20V4"/><path ${N} d="M12 16h4v4"/>`),
  m_tangent: S(`<circle ${F} cx="12" cy="10" r="6"/><path ${N} stroke-width="2" d="M3 16h18"/>`),
  m_concentric: S(`<circle ${N} stroke-width="1.8" cx="12" cy="12" r="8"/><circle ${F} cx="12" cy="12" r="4"/><circle fill="currentColor" cx="12" cy="12" r="1"/>`),
  m_lock: S(`<rect ${Y} x="5" y="11" width="14" height="10" rx="1.5"/><path ${N} stroke-width="1.8" d="M8 11V8a4 4 0 0 1 8 0v3"/>`),
  m_distance: S(`<path ${N} stroke-width="2" d="M4 4v16M20 4v16"/><path stroke="#1e6fff" stroke-width="1.6" fill="none" d="M5 12h14M8 9l-3 3 3 3M16 9l3 3-3 3"/>`),
  m_angle: S(`<path ${N} stroke-width="2" d="M4 20h16M4 20L16 6"/><path stroke="#1e6fff" stroke-width="1.6" fill="none" d="M12 20a8 8 0 0 0-2.6-5.9"/>`),
  hole: S(`<path ${F} d="M3 9l9-4.5L21 9v7l-9 4.5L3 16z"/><ellipse fill="#fff" stroke="currentColor" cx="12" cy="9" rx="4.2" ry="2.1"/><ellipse fill="#1d2533" cx="12" cy="9" rx="2.2" ry="1.1"/><path stroke="#1e6fff" stroke-width="1.6" fill="none" d="M18 2v5M16 5l2 2 2-2"/>`),
  // 図面
  drawing: S(`<path fill="#fff" stroke="currentColor" d="M3 4h18v16H3z"/><path ${N} d="M6 8h6v5H6zM14 8h4v5h-4zM6 15h6v2H6z"/><path stroke="#1e6fff" d="M14 16h5"/>`),
  d_std: S(`<path ${F} d="M3 12h9v8H3z"/><path ${F} d="M3 4h9v5H3z"/><path ${F} d="M15 12h6v8h-6z"/>`),
  d_view: S(`<path fill="#fff" stroke="currentColor" d="M3 4h18v16H3z"/><path ${F} d="M7 11l5-2.5 5 2.5v4l-5 2.5-5-2.5z"/><path ${N} d="M7 11l5 2.5 5-2.5M12 13.5v4"/>`),
  d_proj: S(`<path ${F} d="M3 8h8v9H3z"/><path fill="#e8eef7" stroke="currentColor" stroke-dasharray="2 1.5" d="M15 8h6v9h-6z"/><path stroke="#1e6fff" fill="none" d="M11 12.5h4M13 10.5l2 2-2 2"/>`),
  pdf: S(`<path fill="#fff" stroke="currentColor" d="M5 3h10l4 4v14H5z"/><path ${N} d="M15 3v4h4"/><text x="6.5" y="17" font-size="6.5" font-weight="700" fill="#d0342c" stroke="none" font-family="sans-serif">PDF</text>`),
  dxf: S(`<path fill="#fff" stroke="currentColor" d="M5 3h10l4 4v14H5z"/><path ${N} d="M15 3v4h4"/><text x="6.5" y="17" font-size="6.5" font-weight="700" fill="#1e6fff" stroke="none" font-family="sans-serif">DXF</text>`),
  // 評価・選択
  measure: S(`<path fill="#ffe08a" stroke="currentColor" d="M2 15l13-13 7 7-13 13z"/><path ${N} d="M6 11l2 2M9 8l3 3M12 5l2 2M15 2l3 3" transform="translate(0 0)"/>`),
  mass: S(`<path ${F} d="M4 8l8-4 8 4v8l-8 4-8-4z"/><circle fill="#d0342c" cx="12" cy="12" r="2.2"/><path stroke="#d0342c" d="M12 6v12M6 12h12" stroke-dasharray="1.5 1.5"/>`),
  filter: S(`<path ${Y} d="M3 4h18l-7 8v6l-4 2v-8z"/>`),
  f_all: S(`<path ${F} d="M4 8l8-4 8 4v8l-8 4-8-4z"/><path ${N} d="M4 8l8 4 8-4M12 12v8"/>`),
  f_face: S(`<path fill="#1e6fff" stroke="currentColor" opacity=".7" d="M4 8l8-4 8 4-8 4z"/><path ${N} d="M4 8v8l8 4 8-4V8M12 12v8"/>`),
  f_edge: S(`<path ${N} d="M4 8l8-4 8 4v8l-8 4-8-4z"/><path stroke="#1e6fff" stroke-width="3" d="M12 12v8"/>`),
  f_vertex: S(`<path ${N} d="M4 8l8-4 8 4v8l-8 4-8-4z"/><circle fill="#1e6fff" cx="12" cy="12" r="2.6"/>`),
  interference: S(`<path ${F} d="M3 9h11v11H3z"/><path ${Y} d="M10 4h11v11H10z" opacity=".9"/><path fill="#d0342c" stroke="#d0342c" d="M10 9h4v6h-4z"/>`),
  apattern: S(`<path ${F} d="M3 13l3-1.5 3 1.5v4l-3 1.5-3-1.5z"/><path ${F} d="M10 13l3-1.5 3 1.5v4l-3 1.5-3-1.5z"/><path ${F} d="M17 13l3-1.5 3 1.5v4l-3 1.5-3-1.5z" opacity=".6"/><path stroke="#1e6fff" fill="none" d="M4 7h16M18 5l2 2-2 2"/>`),
  motion: S(`<path ${F} d="M4 4h7v16H4z"/><path ${N} stroke-width="1.8" d="M14 6.5a7 7 0 0 1 0 11"/><path fill="currentColor" d="M11.5 15.5l3.5 3 .8-4.4z"/>`),
  acpattern: S(`<circle ${N} stroke-dasharray="2 2" cx="12" cy="12" r="7.5"/><path ${F} d="M10 2.5l2-1 2 1v3l-2 1-2-1z"/><path ${F} d="M17.5 10l2-1 2 1v3l-2 1-2-1z"/><path ${F} d="M10 17.5l2-1 2 1v3l-2 1-2-1z"/><path ${F} d="M2.5 10l2-1 2 1v3l-2 1-2-1z" opacity=".6"/>`),
  // 表示（ヘッドアップ）
  fit: S(`<path ${N} d="M3 8V3h5M16 3h5v5M21 16v5h-5M8 21H3v-5"/><circle ${N} cx="11" cy="11" r="4"/><path ${N} stroke-width="1.8" d="M14 14l4 4"/>`),
  section: S(`<path ${F} d="M4 9l8-4 8 4v8l-8 4-8-4z"/><path fill="#f3b27a" stroke="#c0571b" d="M12 5v16l8-4V9z"/><path stroke="#c0571b" stroke-dasharray="2 1.5" fill="none" d="M12 1v22"/>`),
  normal: S(`<path fill="#ffe7b3" stroke="#b7791f" d="M3 18l4-8h14l-4 8z"/><path stroke="#1e6fff" stroke-width="2" fill="none" d="M12 14V3M9 6l3-3 3 3"/>`),
  views: S(`<path ${F} d="M4 8l8-4 8 4v8l-8 4-8-4z"/><path fill="#1e6fff" stroke="currentColor" d="M4 8l8 4v8l-8-4z" opacity=".55"/><path ${N} d="M4 8l8 4 8-4"/>`),
  style: S(`<path fill="#8fb3e8" stroke="#1d2533" stroke-width="1.6" d="M4 8l8-4 8 4v8l-8 4-8-4z"/><path ${N} stroke-width="1.6" d="M4 8l8 4 8-4M12 12v8"/>`),
  shaded: S(`<path fill="#8fb3e8" stroke="none" d="M4 8l8-4 8 4v8l-8 4-8-4z"/><path fill="#5c86c7" d="M12 12l8-4v8l-8 4z"/>`),
  hlr: S(`<path fill="#fff" stroke="currentColor" stroke-width="1.6" d="M4 8l8-4 8 4v8l-8 4-8-4z"/><path ${N} stroke-width="1.6" d="M4 8l8 4 8-4M12 12v8"/>`),
  wire: S(`<path ${N} d="M4 8l8-4 8 4v8l-8 4-8-4z"/><path ${N} d="M4 8l8 4 8-4M12 12v8"/><path ${N} stroke-dasharray="2 2" d="M4 16l8-4 8 4M12 4v8"/>`),
  persp: S(`<path ${F} d="M6 5h12l3 15H3z"/><path ${N} d="M9 5l-2 15M15 5l2 15M4.5 12h15"/>`),
};

const icon = (name) => `<span class="ic">${ICONS[name] || ""}</span>`;

// ---------------------------------------------------------------- 部品
/** 大きいボタン（アイコンの下に名前。2 行まで）。 */
const big = (id, ic, label, title, attrs = "") =>
  `<button class="big" id="${id}" ${attrs} title="${title}">${icon(ic)}<span class="lbl">${label}</span></button>`;
/** 小さいボタン（アイコンの右に名前）。 */
const small = (id, ic, label, title, attrs = "") =>
  `<button class="sm" ${id ? `id="${id}"` : ""} ${attrs} title="${title}">${icon(ic)}<span class="lbl">${label}</span></button>`;
const stack = (...items) => `<div class="stack">${items.join("")}</div>`;
const sep = `<div class="sep"></div>`;
const tool = (t, ic, label, key, hint) => small(null, ic, label, `${hint}${key ? ` (${key})` : ""}`, `data-tool="${t}"`);

const FEATURES = [
  big("btn-extrude", "extrude", "押し出し<br>ボス/ベース", "スケッチを押し出す（編集中のスケッチ、またはツリーで選んだスケッチ）"),
  big("btn-revolve", "revolve", "回転<br>ボス/ベース", "選んだスケッチを中心線（作図線）まわりに回転"),
  big("btn-sweep", "sweep", "スイープ", "スイープ: 断面のスケッチ（閉じた輪郭）を、経路のスケッチ（つながった線・円弧）に沿って掃引。断面のスケッチを選んで押す（経路は自動で選び、右のパネルで変えられる）"),
  big("btn-cut", "cut", "押し出し<br>カット", "スケッチでカット（編集中のスケッチ、またはツリーで選んだスケッチ）"),
  big("btn-hole", "hole", "穴<br>ウィザード", "穴ウィザード: 面を選んで押す → 穴の位置に点を置く → スケッチ終了 → 種類・サイズを選んで ✓"),
  sep,
  big("btn-fillet", "fillet", "フィレット", "選んだエッジにフィレット（3D でエッジをクリック、Ctrl で複数）"),
  stack(small("btn-chamfer", "chamfer", "面取り", "選んだエッジを面取り（3D でエッジをクリック、Ctrl で複数）"),
    small("btn-shell", "shell", "シェル", "シェル: 選んだ面を取り除いて中を空ける（面は右のパネルで足せます）")),
  sep,
  big("btn-lpattern", "lpattern", "直線<br>パターン", "直線パターン: ツリーで選んだ押し出し・カット・回転を並べる"),
  stack(small("btn-cpattern", "cpattern", "円形パターン", "円形パターン: ツリーで選んだ押し出し・カット・回転を軸まわりに並べる"),
    small("btn-mirror-f", "mirror", "ミラー", "ミラー: ツリーで選んだ押し出し・カット・回転を平面について鏡映する")),
  sep,
  big("btn-plane", "plane", "参照<br>平面", "参照平面: 選んだ面・平面からオフセットした平面を作る"),
].join("");

const SKETCH = [
  `<div class="menu-wrap">${big("btn-sketch", "sketch", "スケッチ", "スケッチを作成（面か平面を選んでいればそこに。選んでいなければ平面を選ぶ）。スケッチ中は終了")}
    <div id="sketch-menu" class="menu" hidden>
      <button data-plane="face" id="menu-face">選択した面</button>
      <button data-plane="Front">正面（XY）</button>
      <button data-plane="Top">平面（XZ）</button>
      <button data-plane="Right">右側面（YZ）</button>
    </div></div>`,
  big("btn-smartdim", "dim", "スマート<br>寸法", "スマート寸法 (D): 要素をクリック（2 つまで）→ 置く位置をクリック → 値", 'data-tool="dim"').replace('class="big"', 'class="big hero"'),
  sep,
  `<div id="sketch-tools" class="grid">${[
    tool("select", "select", "選択", "S", "選択: 要素を選んで拘束・寸法、ドラッグで移動"),
    tool("line", "line", "直線", "L", "直線"), tool("cline", "cline", "中心線", "", "中心線（作図線）"),
    tool("rect", "rect", "コーナー矩形", "R", "コーナー矩形: 対角の 2 点"),
    tool("crect", "crect", "中心矩形", "", "中心矩形: 中心 → 角"),
    tool("slot", "slot", "スロット", "", "ストレートスロット"),
    tool("circle", "circle", "円", "C", "円: 中心 → 円周"), tool("polygon", "polygon", "多角形", "", "多角形"),
    tool("point", "point", "点", "P", "点"),
    tool("arc", "arc", "中心点円弧", "A", "中心点円弧: 中心 → 始点 → 終点"),
    tool("arc3", "arc3", "3 点円弧", "", "3 点円弧: 始点 → 終点 → 通る点"),
    tool("tarc", "tarc", "正接円弧", "", "正接円弧: 端点 → 終点"),
  ].join("")}</div>`,
  sep,
  stack(tool("fillet", "sfillet", "スケッチ フィレット", "", "スケッチ フィレット: 角の点（または線 2 本）"),
    tool("chamfer", "schamfer", "スケッチ 面取り", "", "スケッチ 面取り: 角の点（または線 2 本）")),
  sep,
  big("btn-convert", "convert", "エンティティ<br>変換", "選んだモデルのエッジ・面をスケッチ平面に投影して要素を作ります（モデルが変わると追従）"),
  big("btn-offset-tool", "offset", "エンティティ<br>オフセット", "オフセット (O): 要素を選び、オフセットする側をクリック", 'data-tool="offset"'),
  big("btn-trim-tool", "trim", "エンティティ<br>トリム", "トリム (T): 消したい部分をクリック", 'data-tool="trim"'),
  stack(tool("extend", "extend", "エンティティ延長", "", "延長: 伸ばしたい線の端の近く"),
    small("btn-smirror", "smirror", "エンティティ ミラー", "選んだ要素を、選んだ中心線（作図線）について鏡映")),
  stack(small("btn-slpattern", "lpattern", "直線スケッチパターン", "選んだ要素を一方向に並べる（個数・間隔・角度）"),
    small("btn-scpattern", "cpattern", "円形スケッチパターン", "選んだ要素を中心（既定は原点）のまわりに並べる")),
].join("");

const EVALUATE = [
  big("btn-measure", "measure", "計測", "計測: 面・エッジ・頂点をクリック（2 つで距離・角度、1 つで長さ・面積・半径）"),
  big("btn-massprops", "mass", "質量<br>特性", "体積・表面積・重心・外形寸法"),
].join("");

const ASSEMBLY = [
  big("btn-insert", "insert", "部品の<br>挿入", "開いているパートを部品として置く（左の「部品を追加」）"),
  big("btn-mate", "mate", "合致", "合致: 面を 2 つ選んで合わせる（一般的な 3D CAD と同じ手順）"),
  sep,
  big("btn-apattern", "apattern", "直線パターン<br>（部品）", "部品を直線上に並べる（種の部品にロックで追従）"),
  big("btn-acpattern", "acpattern", "円形パターン<br>（部品）", "部品を軸のまわりに並べる（種の部品にロックで追従）"),
  sep,
  big("btn-interference", "interference", "干渉<br>チェック", "部品どうしの食い込みを調べる"),
  big("btn-asm-motion", "motion", "動かして<br>みる", "動かしてみる: 部品をドラッグで回転軸（同心円の合致）のまわりに回す。合致・当たりは無視し、やめると元に戻る"),
].join("");

const DRAWING = [
  big("btn-drw-std", "d_std", "標準<br>3 面図", "標準 3 面図: 正面図・平面図・右側面図を置く（第三角法）"),
  `<div class="menu-wrap">${big("btn-drw-view", "d_view", "モデル<br>ビュー", "モデルビュー: 向きを選び、置く位置をクリック")}
    <div id="drw-view-menu" class="menu" hidden>
      <button data-orient="front">正面図</button><button data-orient="top">平面図</button><button data-orient="right">右側面図</button>
      <button data-orient="left">左側面図</button><button data-orient="back">背面図</button><button data-orient="bottom">下面図</button>
      <button data-orient="iso">等角投影図</button>
    </div></div>`,
  big("btn-drw-proj", "d_proj", "投影図", "投影図: ビューを選んで押し、置く側（上下左右）をクリック。親のビューと位置がそろう"),
  sep,
  big("btn-drw-dim", "dim", "スマート<br>寸法", "スマート寸法 (D): 点を 2 つ、または直線・円をクリック → 置く位置をクリック").replace('class="big"', 'class="big hero"'),
  big("btn-drw-note", "note", "注記", "注記: 置く位置をクリック → 文字を入れる（室名など。近くのビューと一緒に動く）"),
  sep,
  big("btn-drw-pdf", "pdf", "PDF<br>出力", "PDF に書き出す（用紙の大きさ・縮尺どおり）"),
  stack(small("btn-drw-dxf", "dxf", "DXF（用紙どおり）", "DXF に書き出す: 用紙の mm（図枠・表題欄つき）"),
    small("btn-drw-dxf-real", "dxf", "DXF（実寸 1:1）", "DXF に書き出す: モデルの実寸（シートの縮尺で割る。図枠なし。加工用）"),
    small("btn-drw-fit", "fit", "全体表示", "シート全体を表示 (F)")),
].join("");

const HUD = `
  <div class="menu-wrap">
    <button id="btn-filter" title="選択フィルタ（何を選べるか）">${icon("f_all")}<span class="caret">▾</span></button>
    <div id="filter-menu" class="menu" hidden>
      <button data-filter="all">${icon("f_all")}すべて（面・エッジ）</button>
      <button data-filter="face">${icon("f_face")}面だけ</button>
      <button data-filter="edge">${icon("f_edge")}エッジだけ</button>
      <button data-filter="vertex">${icon("f_vertex")}頂点だけ</button>
    </div>
  </div>
  <span class="hud-sep"></span>
  <button id="btn-fit" title="全体表示 (F)">${icon("fit")}</button>
  <button id="btn-normal" title="選択した面・平面（スケッチ中はスケッチ平面）に正対 (Ctrl+8)">${icon("normal")}</button>
  <button id="btn-section" title="断面表示: 平面でモデルを切って中を見る。ビューの矢印・輪をドラッグして平面を動かす（開いているときにもう一度押すとやめる）">${icon("section")}</button>
  <div class="menu-wrap">
    <button id="btn-views" title="表示方向（Ctrl+1〜8）">${icon("views")}<span class="caret">▾</span></button>
    <div id="views-menu" class="menu" hidden>
      <button data-view="front">正面<kbd>Ctrl+1</kbd></button>
      <button data-view="back">背面<kbd>Ctrl+2</kbd></button>
      <button data-view="left">左側面<kbd>Ctrl+3</kbd></button>
      <button data-view="right">右側面<kbd>Ctrl+4</kbd></button>
      <button data-view="top">平面<kbd>Ctrl+5</kbd></button>
      <button data-view="bottom">底面<kbd>Ctrl+6</kbd></button>
      <button data-view="iso">等角投影<kbd>Ctrl+7</kbd></button>
      <button id="menu-normal">選択した面・平面に正対<kbd>Ctrl+8</kbd></button>
    </div>
  </div>
  <div class="menu-wrap">
    <button id="btn-style" title="表示スタイル">${icon("style")}<span class="caret">▾</span></button>
    <div id="style-menu" class="menu" hidden>
      <button data-style="shaded_edges">${icon("style")}エッジ付きシェイディング</button>
      <button data-style="shaded">${icon("shaded")}シェイディング</button>
      <button data-style="hlr">${icon("hlr")}隠線なし</button>
      <button data-style="wire">${icon("wire")}ワイヤーフレーム</button>
    </div>
  </div>
  <button id="btn-persp" title="透視投影（押していないときは平行投影）">${icon("persp")}</button>`;

const STANDARD = `
  <div class="brand">AgentCAD</div>
  <div class="group">
    <div class="menu-wrap">
      <button id="btn-new-menu" class="tb" title="新規">${icon("new")}<span class="caret">▾</span></button>
      <div id="new-menu" class="menu" hidden>
        <button id="btn-newdoc" title="新しいパート">${icon("part")}パート</button>
        <button id="btn-newasm" title="新しいアセンブリ">${icon("assembly")}アセンブリ</button>
        <button id="btn-newdrw" title="今のパート・アセンブリの図面を作る（標準 3 面図つき）">${icon("drawing")}図面</button>
        <button id="btn-new" title="新しいデモ部品（板・ボス・穴・フィレット）">${icon("demo")}デモ部品</button>
        <button id="btn-new-ai" title="作りたいものを AI に伝えて、新しいパートを作ってもらう">${icon("ai")}AI で作成…</button>
        <button id="btn-new-car" title="車のデモ部品（参照平面・ミラー・スケッチのパターンなど。カットは usd-brep が要る）">${icon("demo")}デモ（車）</button>
      </div>
    </div>
    <button id="btn-open" class="tb" title="開く (Ctrl+O)">${icon("open")}</button>
    <button id="btn-save" class="tb" title="USD（.usda）で保存 (Ctrl+S)。名前を付けて保存は Ctrl+Shift+S（または Shift+クリック）">${icon("save")}</button>
    <div class="menu-wrap">
      <button id="btn-save-menu" class="tb" title="名前を付けて保存・書き出し（STL / 3MF / OBJ）"><span class="caret">▾</span></button>
      <div id="save-menu" class="menu" hidden>
        <button id="menu-saveas">名前を付けて保存…<kbd>Ctrl+Shift+S</kbd></button>
        <hr>
        <button data-export="stl" title="3D プリンタ（スライサ）・Blender 向け。色と部品の区別は持たない">STL で書き出し…<kbd>3D プリンタ</kbd></button>
        <button data-export="3mf" title="3D プリンタ（PrusaSlicer・Bambu Studio・Cura など）向け。部品ごと・色つき">3MF で書き出し…<kbd>3D プリンタ・色</kbd></button>
        <button data-export="obj" title="Blender ほか向け。部品ごと・色つき（.mtl も書く）">OBJ で書き出し…<kbd>Blender</kbd></button>
        <hr>
        <button data-quality="coarse" title="三角形を少なく（ファイルが小さい）">細かさ: 粗い</button>
        <button data-quality="standard" title="画面の表示と同じ">細かさ: 標準</button>
        <button data-quality="fine" title="曲面を細かく（3D プリントの仕上がり向け）">細かさ: 細かい</button>
      </div>
    </div>
  </div>
  <div class="group">
    <button id="btn-undo" class="tb" title="元に戻す (Ctrl+Z)">${icon("undo")}</button>
    <button id="btn-redo" class="tb" title="やり直し (Ctrl+Y)">${icon("redo")}</button>
  </div>
  <div class="group">
    <select id="doc-select" title="開いている文書（このウィンドウで切り替え）"></select>
    <button id="btn-window" class="tb" title="文書を別のウィンドウで開く">${icon("window")}</button>
  </div>
  <div class="group">
    <button id="btn-ai" class="tb ai-btn" title="AI アシスタント（自然な言葉で作る・操作する）">${icon("ai")}<span>AI</span></button>
  </div>
  <div class="spacer"></div>
  <div id="doc-title"></div>
  <div id="kernel" title="形状カーネル"></div>`;

/** ツールバーとヘッドアップ ツールバーを組み立てる。 */
function build() {
  document.querySelector("#toolbar").innerHTML = `
    <div class="bar std">${STANDARD}</div>
    <nav id="cm-tabs">
      <button data-tab="features" class="part-only">フィーチャー</button>
      <button data-tab="sketch" class="part-only">スケッチ</button>
      <button data-tab="evaluate" class="part-only">評価</button>
      <button data-tab="assembly" class="asm-only">アセンブリ</button>
      <button data-tab="drawing" class="drw-only">図面</button>
    </nav>
    <div id="cmd">
      <div class="cm-panel" data-panel="features">${FEATURES}</div>
      <div class="cm-panel" data-panel="sketch" hidden>${SKETCH}</div>
      <div class="cm-panel" data-panel="evaluate" hidden>${EVALUATE}</div>
      <div class="cm-panel" data-panel="assembly" hidden>${ASSEMBLY}</div>
      <div class="cm-panel" data-panel="drawing" hidden>${DRAWING}</div>
    </div>`;
  const hud = document.createElement("div");
  hud.id = "hud";
  hud.innerHTML = HUD;
  document.querySelector("#viewport").append(hud);
  // 断面表示の PropertyManager（ビューの右上に浮かべる。D62）
  const sec = document.createElement("div");
  sec.id = "section-panel"; sec.hidden = true;
  sec.innerHTML = `
    <div class="sec-title">${icon("section")}断面表示</div>
    <div class="sec-planes">
      <button data-sec="Front">正面</button><button data-sec="Top">平面</button><button data-sec="Right">右側面</button>
      <button data-sec="sel" title="ツリーやビューで選んだ平面・平らな面で切る">選択した面</button>
    </div>
    <label class="sec-row">距離 <input type="range" id="sec-range" step="any"><input type="number" id="sec-offset" step="any"> mm</label>
    <label class="sec-row"><input type="checkbox" id="sec-flip"> 反対側を表示</label>
    <div class="btns"><button id="sec-ok" class="primary" title="断面のまま閉じる">✓ OK</button><button id="sec-off" title="断面表示をやめる">✗ やめる</button></div>`;
  document.querySelector("#viewport").append(sec);
  const fp = document.createElement("div");           // 計測・質量特性の結果（3D ビューの左上）
  fp.id = "float-panel"; fp.hidden = true;
  document.querySelector("#viewport").append(fp);
}
build();

/** CommandManager のタブを切り替える。 */
export function setTab(name) {
  for (const b of document.querySelectorAll("#cm-tabs [data-tab]")) b.classList.toggle("on", b.dataset.tab === name);
  for (const p of document.querySelectorAll("#cmd .cm-panel")) p.hidden = p.dataset.panel !== name;
}
export const currentTab = () => document.querySelector("#cm-tabs .on")?.dataset.tab;
