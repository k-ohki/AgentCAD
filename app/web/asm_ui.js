// アセンブリの画面: 部品と合致の一覧、合致の手順（D51）、部品のパターン・干渉チェック（D56）。
import { setTab } from "./toolbar.js";
import { fmt } from "./select.js";
import { MATES, STATE_LABEL } from "./asm.js";
import { $, act, app, askText, asm, el, fill, iconEl, rpc, sketch, toast, viewer } from "./core.js";
import { measureEnd } from "./evaluate.js";
import { docTitle, openDocWindow } from "./windows.js";

asm.onHover = (ref) => { $("#status-pick").textContent = ref ? `${ref[0]} / ${ref[1]}` : ""; };

// ---------------------------------------------------------------- アセンブリ
export function renderAssembly(st, switching) {
  measureEnd(); if (!app.interf) $("#float-panel").hidden = true;
  sketch.set(null);
  $("#confirm-corner").hidden = true;
  setTab("assembly");
  app.mesh = null;
  docTitle(st);
  $("#btn-undo").disabled = !st.undo; $("#btn-undo").title = st.undo ? `元に戻す: ${st.undo} (Ctrl+Z)` : "元に戻す";
  $("#btn-redo").disabled = !st.redo; $("#btn-redo").title = st.redo ? `やり直し: ${st.redo} (Ctrl+Y)` : "やり直し";
  $("#kernel").textContent = `カーネル: ${st.kernel}`;
  if (switching) { asm.mateMode = false; asm.pendingMate = null; asm.editing = null; asm.patternMode = null; app.interf = null; }
  if (asm.pendingMate && !st.mates.some((m) => m.id === asm.pendingMate)) asm.pendingMate = null;   // Undo などで消えた
  asm.set(st, { first: switching });
  $("#btn-asm-motion").classList.remove("on");                     // 新しい状態で「動かしてみる」は終わる
  if (asm.pendingMate) showMatePopup(); else hideMatePopup();
  const comps = $("#asm-comps");
  comps.replaceChildren(...st.components.map((c) => el("li", {},
    el("span", { class: "ico" }, c.fixed ? "⚓" : "▣"),
    el("span", { class: "name", title: `元のパート: ${c.part_name}` }, c.id),
    el("span", { class: "tag", title: "残りの自由度" }, c.fixed ? "固定" : `自由度 ${c.dof ?? "?"}`),
    el("span", { class: "acts" },
      el("button", { class: "ib", "data-act": "open", title: "開く: パートを別のウィンドウで開いて編集（一般的な 3D CAD の「部品を開く」）", onclick: () => openDocWindow(c.source) }, "↗"),
      el("button", { class: "ib", "data-act": "copy", title: "複製: 同じパートをもう 1 つ置く", onclick: () => act("asm.copy", { comp: c.id }) }, "⧉"),
      el("button", { class: "ib", "data-act": "fix", title: c.fixed ? "固定を外す" : "固定する", onclick: () => act("asm.fix", { comp: c.id, value: !c.fixed }) }, c.fixed ? "🔓" : "📌"),
      el("button", { class: "ib", "data-act": "delete", title: "削除（関係する合致も消えます）", onclick: () => act("asm.delete", { ids: [c.id] }) }, "×")))));
  if (!st.components.length) comps.append(el("li", { class: "tag" }, "下の「部品を追加」でパートを置きます"));
  const mates = $("#asm-mates");
  mates.replaceChildren(...st.mates.map((m) => el("li", { class: m.state, title: `${m.refs[0].join(" / ")}\n${m.refs[1].join(" / ")}` },
    el("span", { class: "name link", title: m.type === "lock" ? "" : "クリックで合致を編集（種類・値・整列）", onclick: () => mateEditStart(m) },
      `${m.label}${m.value != null ? ` ${+m.value.toFixed(4)}` : ""}（${m.refs[0][0]} – ${m.refs[1][0]}）`),
    el("span", { class: "tag" }, STATE_LABEL[m.state]),
    ["distance", "angle"].includes(m.type) ? el("button", { title: "値を変更", onclick: () => {
      askText(`${m.label}の値`, m.value).then((v) => {
        if (v !== null && v.trim() !== "" && Number.isFinite(Number(v))) act("asm.edit", { id: m.id, value: Number(v) });
      });
    } }, "値") : null,
    m.type === "coincident" || m.type === "distance" ? el("button", { title: "向きを反転", onclick: () => act("asm.edit", { id: m.id, flip: true }) }, "反転") : null,
    el("button", { title: m.suppressed ? "抑制を解除" : "抑制", onclick: () => act("asm.edit", { id: m.id, suppressed: !m.suppressed }) }, m.suppressed ? "有効" : "抑制"),
    el("button", { title: "削除", onclick: () => act("asm.delete", { ids: [m.id] }) }, "×"))));
  if (!st.mates.length) mates.append(el("li", { class: "tag" }, "面を 2 つ選んで、右のボタンで合致を付けます"));
  const sel = $("#asm-add select[name=part]");
  sel.replaceChildren(...st.part_docs.map((p) => el("option", { value: p.doc }, p.name)));
  const s = st.solve;
  $("#status-info").textContent = st.error ? `エラー: ${st.error}` : s ? `${{ full: "完全定義", under: "未定義", over: "過剰定義" }[s.state]}（自由度 ${s.dof}）${s.status !== "ok" ? "：解けません" : ""}` : "";
  renderAsmProps();
}

// ---------------------------------------------------------------- 合致（一般的な 3D CAD と同じ手順。D51）
// 合致 → 面を 2 つクリック → 既定の種類で合致が付いて部品が動く（プレビュー）→ ポップアップで種類・値・整列を変える
// → ✓（続けて次の合致）/ ✗（取り消して元の位置へ）。PropertyManager の ✓ で合致を終える。
const mateById = (id) => app.state?.mates?.find((m) => m.id === id);
const mateRefs = () => asm.sel.map((s) => [s.comp, s.face]);

export function mateEnter() {
  if (app.state?.kind !== "assembly") return;
  asm.mateMode = true;
  if (asm.sel.length === 2 && asm.sel[0].comp !== asm.sel[1].comp) mateStart();    // 先に選んだ 2 面で始める
  else { asm.sel = []; asm._highlight(); }
  renderAsmProps();
  $("#status-pick").textContent = "合致: 合わせる面を 2 つクリックしてください（別々の部品）";
}

export async function mateExit(ok) {
  if (asm.pendingMate) await (ok ? mateConfirm() : mateCancel());
  asm.mateMode = false; asm.sel = []; asm._highlight(); hideMatePopup(); renderAsmProps();
}

async function matePick(ref, pos) {
  if (asm.pendingMate) await mateConfirm();               // 次の面を選んだら、今の合致は確定
  const i = asm.sel.findIndex((s) => s.comp === ref.comp && s.face === ref.face);
  if (i >= 0) asm.sel.splice(i, 1);
  else {
    asm.sel = asm.sel.filter((s) => s.comp !== ref.comp);     // 同じ部品の面は置き換える
    asm.sel.push(ref);
    if (asm.sel.length > 2) asm.sel.shift();
  }
  app.matePos = pos;
  asm._highlight();
  if (asm.sel.length === 2) await mateStart();
  else renderAsmProps();
}

async function mateStart() {
  const [A, B] = mateRefs();
  try { app.mateOpts = await rpc("asm.mate_options", { a: A, b: B }); }
  catch (e) { toast(e.message, true); asm.sel.pop(); asm._highlight(); renderAsmProps(); return; }
  const st = await act("asm.mate", { type: app.mateOpts.default, a: A, b: B, pending: true });
  if (st?.created?.[0]) asm.pendingMate = st.created[0];
  renderAsmProps(); showMatePopup();
}

/** 作ったばかりの合致を変える（種類 / 値 / 整列）。 */
async function mateSet({ type, value, aligned }) {
  const m = mateById(asm.pendingMate); if (!m) return;
  const t = type ?? m.type, same = t === m.type;
  const v = value ?? (same ? m.value : t === "distance" ? app.mateOpts?.distance : t === "angle" ? app.mateOpts?.angle : null);
  const [A, B] = mateRefs();
  const st = await act("asm.mate_set", { id: m.id, type: t, a: A, b: B, value: v ?? undefined, aligned: aligned ?? (same ? m.aligned : undefined), pending: !asm.editing });
  if (st && asm.editing) asm.editing.n++;
  renderAsmProps(); showMatePopup();
}

export async function mateConfirm() {
  if (asm.editing) { asm.editing = null; asm.pendingMate = null; asm.mateMode = false; asm.sel = []; asm._highlight(); hideMatePopup(); renderAsmProps(); return; }
  const m = mateById(asm.pendingMate);
  asm.pendingMate = null; asm.sel = []; asm._highlight(); hideMatePopup(); renderAsmProps();
  if (m) toast(`${m.label}の合致を付けました（続けて面を選べます。終わるときは ✓）`);
}

export async function mateCancel() {
  if (asm.editing) {                                   // 編集の取り消し: 変えた分だけ元に戻す
    const n = asm.editing.n; asm.editing = null; asm.pendingMate = null; asm.mateMode = false; asm.sel = []; hideMatePopup();
    for (let k = 0; k < n; k++) await act("edit.undo");
    asm._highlight(); renderAsmProps(); return;
  }
  const id = asm.pendingMate;
  asm.pendingMate = null; asm.sel = []; hideMatePopup();
  if (id) await act("asm.mate_cancel", { id });
  asm._highlight(); renderAsmProps();
}

/** 付いている合致を編集する（合致の一覧で名前をクリック）。合致の PropertyManager を、その合致の面を選んだ状態で開く。 */
async function mateEditStart(m) {
  if (m.type === "lock") { toast("ロックの合致は編集できません（削除して付け直してください）", true); return; }
  if (asm.pendingMate) await mateConfirm();
  const [A, B] = m.refs;
  try { app.mateOpts = await rpc("asm.mate_options", { a: A, b: B }); } catch (e) { toast(e.message, true); return; }
  asm.mateMode = true; asm.editing = { id: m.id, n: 0 }; asm.pendingMate = m.id;
  asm.sel = [{ comp: A[0], face: A[1] }, { comp: B[0], face: B[1] }]; asm._highlight();
  app.matePos = null; renderAsmProps(); showMatePopup();
}

/** 種類・値・整列のボタン（ポップアップと PropertyManager で共用）。 */
function mateControls(prefix) {
  const m = mateById(asm.pendingMate), avail = new Set((app.mateOpts?.types || []).map((t) => t.type));
  const out = [el("div", { class: "mate-types" }, MATES.map(([t, label]) => el("button", {
    "data-mate": t, class: m?.type === t ? "on" : "", disabled: !m || !avail.has(t), title: label,
    onclick: () => mateSet({ type: t }) }, iconEl(`m_${t}`), label)))];
  if (m && ["distance", "angle"].includes(m.type)) {
    const inp = el("input", { type: "text", value: m.value != null ? +m.value.toFixed(4) : "", size: 7, id: `${prefix}-value`,
      title: m.type === "distance" ? "距離（mm）。Enter で反映" : "角度（°）。Enter で反映" });
    inp.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      e.stopPropagation();
      if (Number.isFinite(Number(inp.value)) && inp.value.trim() !== "") mateSet({ value: Number(inp.value) });
    });
    out.push(el("div", { class: "row" }, el("label", {}, m.type === "distance" ? "距離 (mm)" : "角度 (°)"), inp));
  }
  if (m && m.type !== "lock") out.push(el("div", { class: "row" }, el("label", {}, "整列"),
    el("button", { class: m.aligned ? "on" : "", title: "同方向（法線・軸の向きをそろえる）", onclick: () => mateSet({ aligned: true }) }, "同方向"),
    el("button", { class: m.aligned ? "" : "on", title: "反対方向（面を突き合わせる）", onclick: () => mateSet({ aligned: false }) }, "反対方向")));
  return out;
}

function showMatePopup() {
  const pop = $("#mate-popup");
  if (!asm.pendingMate || !mateById(asm.pendingMate)) { hideMatePopup(); return; }
  fill(pop, ...mateControls("pop"), el("div", { class: "row end" },
    el("button", { class: "ok", id: "mate-pop-ok", title: "この合致を確定 (Enter)", onclick: () => mateConfirm() }, "✓"),
    el("button", { class: "cancel", id: "mate-pop-cancel", title: "この合致を取り消す (Esc)", onclick: () => mateCancel() }, "✗")));
  pop.hidden = false;
  const vp = $("#viewport").getBoundingClientRect(), p = app.matePos || [vp.width / 2, vp.height / 2];
  pop.style.left = `${Math.max(4, Math.min(p[0] + 18, vp.width - pop.offsetWidth - 4))}px`;
  pop.style.top = `${Math.max(4, Math.min(p[1] + 18, vp.height - pop.offsetHeight - 4))}px`;
}
export function hideMatePopup() { const p = $("#mate-popup"); if (p) p.hidden = true; }

export function renderAsmProps() {
  const st = app.state; if (!st || st.kind !== "assembly") return;
  $("#btn-mate").classList.toggle("on", asm.mateMode);
  const box = $("#prop");
  if (asm.patternMode) { renderPatternPM(); return; }
  if (asm.mateMode) {                                       // 合致の PropertyManager
    $("#prop-title").textContent = asm.editing ? `合致を編集: ${asm.editing.id}` : "合致";
    const m = mateById(asm.pendingMate);
    fill(box, 
      el("div", { class: "confirm" },
        el("button", { class: "ok", id: "btn-mate-ok", title: "合致を終える (Enter)", onclick: () => mateExit(true) }, "✓ OK"),
        el("button", { class: "cancel", id: "btn-mate-close", title: "作りかけの合致を取り消して終える (Esc)", onclick: () => mateExit(false) }, "✗ キャンセル")),
      el("h3", {}, "合致設定のエンティティ"),
      el("ul", { class: "facelist" }, asm.sel.length ? asm.sel.map((x, i) => el("li", {}, el("span", { class: "name" }, `${x.comp} / ${x.face}`),
        el("button", { title: "外す", onclick: async () => { if (asm.pendingMate) await mateCancel(); asm.sel.splice(i, 1); asm._highlight(); renderAsmProps(); } }, "×")))
        : el("li", { class: "tag" }, "3D で面をクリック（2 つ目は別の部品）")),
      el("h3", {}, "標準合致"),
      ...mateControls("pm"),
      el("div", { class: "hint" }, m ? "種類・値・整列を変えると部品がすぐ動きます。✓ で確定して次の合致へ、✗ で取り消し。"
        : "面を 2 つ選ぶと、合う種類（平面どうし → 一致、円筒どうし → 同心、平面と円筒 → 正接）で合致が付いて部品が動きます。"));
    return;
  }
  $("#prop-title").textContent = `アセンブリ: ${st.name}`;
  fill(box, 
    el("div", { class: "btns" }, el("button", { class: "primary", id: "btn-mate-start", onclick: () => mateEnter() }, iconEl("mate"), "合致")),
    asm.sel.length ? el("div", { class: "selfaces" }, asm.sel.map((x) => el("div", {}, `${x.comp} / ${x.face}`))) : null,
    el("div", { class: "hint" }, "「合致」を押して面を 2 つクリックすると合致が付きます（先に 2 面選んでから押しても同じ）。"
      + " 部品の面を左ドラッグするとその部品を動かせます（合致で許される方向にだけ動きます）。"
      + " 何もない所の左ドラッグで回転、右ドラッグで移動。部品の「開く」でパートを別ウィンドウで編集すると、ここにも反映されます。"),
  );
}
asm.onMatePick = (ref, pos) => matePick(ref, pos);

// ---------------------------------------------------------------- 部品のパターン・干渉チェック（D56）
function patternStart(kind) {
  if (app.state?.kind !== "assembly" || !app.state.components.length) return;
  if (asm.mateMode) mateExit(true);
  const seed = asm.sel[0]?.comp || app.state.components.find((c) => !c.fixed)?.id || app.state.components[0].id;
  asm.patternMode = { kind, comp: seed, axis: kind === "linear" ? "x" : "z", ref: null, count: kind === "linear" ? 3 : 4,
    spacing: 20, angle: 360, flip: false };
  asm.sel = []; asm._highlight();
  renderAsmProps();
}

function renderPatternPM() {
  const P = asm.patternMode, box = $("#prop"), lin = P.kind === "linear";
  if (asm.sel.length) { P.ref = [asm.sel[asm.sel.length - 1].comp, asm.sel[asm.sel.length - 1].face]; P.axis = null; }
  $("#prop-title").textContent = lin ? "直線パターン（部品）" : "円形パターン（部品）";
  const num = (key, label, unit, int) => el("div", { class: "param" }, el("label", {}, label), el("div", { class: "row" },
    el("input", { type: "text", value: P[key], size: 7, id: `pat-${key}`, onchange: (e) => {
      const v = Number(e.target.value); if (Number.isFinite(v)) P[key] = int ? Math.round(v) : v; } }), unit ? el("span", { class: "unit" }, unit) : null));
  fill(box, 
    el("div", { class: "confirm" },
      el("button", { class: "ok", id: "btn-pattern-ok", onclick: async () => {
        document.activeElement?.blur?.();
        const st = await act("asm.pattern", { comp: P.comp, kind: P.kind, ref: P.ref || undefined, axis: P.ref ? undefined : P.axis,
          count: P.count, spacing: P.spacing, angle: P.angle, flip: P.flip });
        if (st) { asm.patternMode = null; asm.sel = []; asm._highlight(); renderAsmProps(); toast(`${st.created.length} 個並べました（種の部品にロックの合致で追従します）`); }
      } }, "✓ OK"),
      el("button", { class: "cancel", onclick: () => { asm.patternMode = null; asm.sel = []; asm._highlight(); renderAsmProps(); } }, "✗ キャンセル")),
    el("div", { class: "param" }, el("label", {}, "パターンにする部品"), el("select", { id: "pat-comp", onchange: (e) => { P.comp = e.target.value; } },
      app.state.components.map((c) => el("option", { value: c.id, selected: c.id === P.comp }, c.id)))),
    el("div", { class: "param" }, el("label", {}, lin ? "方向" : "軸"),
      el("div", { class: "row" }, ...["x", "y", "z"].map((a) => el("button", { class: !P.ref && P.axis === a ? "on" : "", "data-axis": a,
        onclick: () => { P.axis = a; P.ref = null; asm.sel = []; asm._highlight(); renderAsmProps(); } }, `${a.toUpperCase()} 軸`))),
      el("div", { class: "tag" }, P.ref ? `面: ${P.ref.join(" / ")}` : lin ? "または 3D で面をクリック（平面なら法線、円筒なら軸の向き）"
        : "または 3D で円筒の面をクリック（その軸のまわり）")),
    el("label", { class: "chk" }, el("input", { type: "checkbox", checked: P.flip, onchange: (e) => { P.flip = e.target.checked; } }), "向きを反転"),
    num("count", "個数（種を含む）", "", true),
    lin ? num("spacing", "間隔", "mm") : num("angle", "角度（360 で 1 周を等分）", "°"),
    el("div", { class: "hint" }, "並べた部品は種の部品に「ロック」の合致でつながり、種を動かすとついてきます。"),
  );
}
$("#btn-apattern").onclick = () => patternStart("linear");
$("#btn-acpattern").onclick = () => patternStart("circular");

async function interferenceCheck() {
  let r;
  try { r = await rpc("asm.interference"); } catch (e) { toast(e.message, true); return; }
  app.interf = r.interferences; app.interfSel = 0;
  const p = $("#float-panel");
  const list = r.interferences.map((x, k) => el("tr", { class: "link", onclick: () => { app.interfSel = k; viewer.redraw(); } },
    el("td", {}, `${x.a} ↔ ${x.b}`), el("td", {}, `${fmt(x.volume)} mm³`)));
  fill(p, 
    el("h4", {}, "干渉チェック", el("button", { title: "閉じる", onclick: () => { p.hidden = true; app.interf = null; viewer.redraw(); } }, "×")),
    r.interferences.length ? el("table", { id: "interf-table" }, list) : el("div", { id: "interf-none" }, `干渉はありません（部品 ${r.checked} 個）`),
    el("div", { class: "hint" }, "体積は表示用メッシュの格子で数えた近似です。面が接しているだけは干渉に数えません。行をクリックで、その場所を赤い枠で示します。"));
  p.hidden = false; viewer.redraw();
}
$("#btn-interference").onclick = () => interferenceCheck();

/** 干渉している場所の枠（赤）。 */
export function drawInterference() {
  const I = app.interf; if (!I || !I.length || app.state?.kind !== "assembly") return;
  const x = I[app.interfSel || 0], [lo, hi] = x.bbox, ctx = $("#overlay").getContext("2d");
  const C = [0, 1, 2, 3, 4, 5, 6, 7].map((k) => [k & 1 ? hi[0] : lo[0], k & 2 ? hi[1] : lo[1], k & 4 ? hi[2] : lo[2]]).map((p) => viewer.project(p));
  if (C.some((p) => !p)) return;
  ctx.strokeStyle = "#d0342c"; ctx.lineWidth = 2;
  for (const [a, b] of [[0, 1], [2, 3], [4, 5], [6, 7], [0, 2], [1, 3], [4, 6], [5, 7], [0, 4], [1, 5], [2, 6], [3, 7]]) {
    ctx.beginPath(); ctx.moveTo(...C[a]); ctx.lineTo(...C[b]); ctx.stroke();
  }
}

$("#asm-add").addEventListener("submit", (e) => {
  e.preventDefault();
  const part = e.target.part.value;
  if (!part) { toast("先にパートを作ってください", true); return; }
  act("asm.add", { part });
});


// ---------------------------------------------------------------- 動かしてみる（D70）
/** 部品を回転軸のまわりにドラッグで回すモード。合致は解かず画面の中だけで動かすので軽い。やめると元の姿勢に戻る。 */
async function motionToggle() {
  if (asm.motion) asm.stopMotion();
  else {
    try {
      const n = await asm.startMotion();
      if (!n) { asm.motion = null; toast("回せる部品がありません（同心円の合致で回転軸を付けてください）", true); }
      else toast("動かしてみる: 部品をドラッグで回転軸のまわりに回せます（合致・当たりは無視）。Esc かもう一度押すと元に戻ります");
    } catch (e) { toast(e.message, true); }
  }
  $("#btn-asm-motion").classList.toggle("on", !!asm.motion);
}
$("#btn-asm-motion").onclick = motionToggle;
