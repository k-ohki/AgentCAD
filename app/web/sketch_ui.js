// スケッチ編集の画面: ツールのボタン、ステータス、右のパネル（拘束・寸法の一覧、選択に付けられる拘束）、エンティティ変換。
import { CONSTRAINTS, TOOLS, TOOL_HINT, applicableConstraints } from "./sketch.js";
import { $, act, app, edgeAt, el, fill, sketch, SOLVE_LABEL, toast, viewer } from "./core.js";

sketch.onToolChange = () => { renderSketchTools(); sketchStatus(); };

/** スケッチ中のステータスバー: ツールを使っている間はその使い方、選択ツールでは定義の状態。 */
export function sketchStatus() {
  const sk = app.state?.sketch; if (!sk) return;
  const t = sketch.tool, s = sk.solve;
  $("#status-pick").textContent = TOOL_HINT[t] ? `${TOOLS.find((x) => x[0] === t)[1]}: ${TOOL_HINT[t]}`
    : `スケッチ編集中: ${SOLVE_LABEL[s.state]}${s.state === "under" ? `（自由度 ${s.dof}）` : ""}`;
}

/** 面 f に接するエッジの名前。 */
function faceEdges(f) {
  const m = app.mesh;
  return m.edgeNames.filter((_, ei) => m.edgeFaces[ei].includes(f));
}

// スケッチ中のモデル（3D）の選択: エッジ、または面（その面の周りのエッジ全部）。エンティティ変換に使う
sketch.model = {
  hover(pos) {
    const e = pos && app.mesh ? edgeAt(pos, viewer.pick(...pos)) : null;
    if (e !== app.hoverEdge) { app.hoverEdge = e; viewer.redraw(); }
    if (e !== null) $("#status-pick").textContent = `モデルのエッジ: ${app.mesh.edgeNames[e]}（クリックで選択 → エンティティ変換）`;
  },
  pick(pos, ctrl) {
    if (!app.mesh) return false;
    const f = viewer.pick(...pos), e = edgeAt(pos, f);
    if (e === null && f < 0) return false;
    if (!ctrl) app.selEdges.clear();
    const names = e !== null ? [app.mesh.edgeNames[e]] : faceEdges(f);
    const all = names.every((n) => app.selEdges.has(n));
    for (const n of names) all ? app.selEdges.delete(n) : app.selEdges.add(n);
    renderSketchTools(); renderSketchProps(); viewer.redraw();
    return true;
  },
  clear() { if (app.selEdges.size) { app.selEdges.clear(); renderSketchTools(); renderSketchProps(); viewer.redraw(); } },
};

async function convertEntities() {
  const edges = [...app.selEdges];
  if (!edges.length) { toast("変換するモデルのエッジか面を選んでください", true); return; }
  const st = await sketch._send("sketch.convert", { edges });
  if (!st) return;
  app.selEdges.clear(); renderSketchTools(); viewer.redraw();
  if (st.warnings?.length) toast(`変換できなかったエッジ:\n${st.warnings.join("\n")}`, true);
  else toast(`${st.created.length} 個の要素をモデルのエッジに合わせて作りました`);
}

// ---------------------------------------------------------------- スケッチ編集のパネル
/** スケッチのタブのボタン: 今のツールを押した状態に。スケッチ中でなければ作図ツールは使えない。 */
export function renderSketchTools() {
  const on = !!app.state?.sketch;
  for (const b of document.querySelectorAll("#cmd [data-tool]")) {
    b.classList.toggle("on", on && sketch.tool === b.dataset.tool);
    b.disabled = !on;
  }
  const cv = $("#btn-convert");
  cv.disabled = !on || !app.selEdges.size;
  cv.querySelector(".lbl").innerHTML = `エンティティ<br>変換${app.selEdges.size ? `（${app.selEdges.size}）` : ""}`;
  $("#btn-smirror").disabled = !on || sketch.sel.size < 2;
  for (const id of ["#btn-slpattern", "#btn-scpattern"]) $(id).disabled = !on || !sketch.patternIds().length;
}
document.querySelectorAll("#cmd [data-tool]").forEach((b) => (b.onclick = () => sketch.setTool(b.dataset.tool)));
$("#btn-convert").onclick = () => convertEntities();
$("#btn-smirror").onclick = () => sketch.mirror();
$("#btn-slpattern").onclick = () => sketch.startPattern("linear");
$("#btn-scpattern").onclick = () => sketch.startPattern("circular");

const CON_LABEL = Object.fromEntries(CONSTRAINTS);
Object.assign(CON_LABEL, { point_on_line: "点-線上", point_on_circle: "点-円上", distance: "距離", hdistance: "水平距離",
  vdistance: "垂直距離", length: "長さ", radius: "半径", diameter: "直径", angle: "角度", on_edge: "エッジ上",
  ext_point: "一致（ほかのスケッチ）", ext_line: "線上（ほかのスケッチ）" });
/** 拘束の表示名。外部参照は参照先が分かるように（例: 一致（モデル: 円の中心）/ 中点（Sketch1））。 */
function conLabel(c) {
  if (!["ext_point", "ext_line"].includes(c.type) || !c.source) return CON_LABEL[c.type] || c.type;
  const [base, at] = c.source.split("@");
  const AT = { vertex: "端点", mid: "中点", center: "中心" };
  if (base.startsWith("edge:")) return c.type === "ext_line" ? "線上（モデルのエッジ）" : `一致（モデルの${AT[at] || "点"}）`;
  const sk = app.state?.sketches?.find((s) => s.id === base.split(":")[0])?.name || base.split(":")[0];
  return c.type === "ext_line" ? `線上（${sk}）` : at === "mid" ? `中点（${sk}）` : `一致（${sk}）`;
}

export function renderSketchProps() {
  const sk = app.state?.sketch; if (!sk) return;
  $("#btn-smirror").disabled = sketch.sel.size < 2;
  for (const id of ["#btn-slpattern", "#btn-scpattern"]) $(id).disabled = !sketch.patternIds().length;
  if (sketch.patternMode) { renderPatternPanel(); return; }
  $("#prop-title").textContent = `スケッチ: ${sk.name}`;
  const s = sk.solve, box = $("#prop");
  const sel = [...sketch.sel];
  fill(box, ...[
    el("div", { class: `sk-state ${s.state}` }, SOLVE_LABEL[s.state] + (s.state === "under" ? `（自由度 ${s.dof}）` : "")
      + (s.status !== "ok" ? "：解けません" : "")),
    sk.plane_error ? el("div", { class: "state error" }, sk.plane_error) : null,
    sk.profiles ? el("div", {}, `閉じた輪郭: ${sk.profiles}`) : el("div", {}, "閉じた輪郭はまだありません"),
    ...sketchSelectionPanel(sel),
    el("div", { class: "btns" },
      el("button", { disabled: !sel.length, onclick: () => sketch.toggleConstruction() }, "作図線の切り替え"),
      el("button", { id: "btn-mirror", disabled: sel.length < 2, onclick: () => sketch.mirror(),
        title: "選択した要素を、選択の中の作図線（1 本）について鏡映コピーします" }, "ミラー"),
      el("button", { disabled: !sel.length, onclick: () => sketch.deleteSelected() }, "削除 (Del)")),
    el("h3", {}, `拘束・寸法（${sk.constraints.length}）`),
    el("ul", { class: "cons" }, sk.constraints.map((c) => {
      const bad = s.conflicting.includes(c.id) ? "矛盾" : s.redundant.includes(c.id) ? "冗長" : "";
      return el("li", { class: (bad ? "bad " : "") + (sketch.sel.has(c.id) ? "sel" : ""),
        onmouseenter: () => { sketch.hover = c.id; viewer.redraw(); },
        onmouseleave: () => { sketch.hover = null; viewer.redraw(); },
        onclick: () => { sketch.sel.clear(); sketch.sel.add(c.id); renderSketchProps(); viewer.redraw(); },
        title: c.source ? `投影元: ${c.source}` : "",
      }, el("span", {}, `${conLabel(c)}${c.value != null ? ` = ${+c.value.toFixed(4)}` : ""}${c.expr ? `（${c.expr}）` : ""}${bad ? `［${bad}］` : ""}`),
      el("button", { title: "削除", onclick: (e) => { e.stopPropagation(); act("sketch.delete", { ids: [c.id] }); } }, "×"));
    })),
    app.selEdges.size ? el("div", { class: "btns" },
      el("button", { class: "primary", onclick: convertEntities }, `エンティティ変換（モデルのエッジ ${app.selEdges.size} 本）`)) : null,
    el("div", { class: "hint" }, "寸法の数字はダブルクリックで値を変更（= で始めると式）、ドラッグで移動。"
      + " 点・線・円はドラッグで動かせます（線は平行移動、円は大きさ）。"
      + " 何もない所からドラッグで範囲選択（左→右: 完全に入ったもの、右→左: かかったものも）。"
      + " 作図中に線の中点・線上・円上へ合わせると拘束が付きます。"
      + " モデルのエッジ・面をクリックして「エンティティ変換」でスケッチに取り込めます。"
      + " 右ドラッグで視点の移動。Esc で取り消し。"),
  ].filter(Boolean));
}

/** 直線・円形スケッチパターンの PropertyManager: 値を変えるとプレビューが変わる。✓ で作る、✗ / Esc で取り消し。 */
function renderPatternPanel() {
  const m = sketch.patternMode, lin = m.kind === "linear";
  $("#prop-title").textContent = lin ? "直線スケッチパターン" : "円形スケッチパターン";
  const num = (key, label, opt = {}) => el("label", { class: "row" }, el("span", {}, label),
    el("input", { type: "number", id: `pat-${key}`, value: String(m[key]), step: opt.step || "any", min: opt.min ?? "",
      oninput: (e) => { const v = Number(e.target.value); if (Number.isFinite(v)) { m[key] = v; viewer.redraw(); } },
      onkeydown: (e) => { e.stopPropagation(); if (e.key === "Enter") sketch.commitPattern(); if (e.key === "Escape") sketch.cancelPattern(); } }));
  const cname = m.center === "ORIGIN" ? "原点" : m.center;
  fill($("#prop"), ...[
    el("div", { class: "confirm" },
      el("button", { class: "ok", id: "btn-pat-ok", title: "確定 (Enter)", onclick: () => sketch.commitPattern() }, "✓ OK"),
      el("button", { class: "ng", title: "取り消し (Esc)", onclick: () => sketch.cancelPattern() }, "✗")),
    el("div", {}, `パターンにする要素: ${m.ids.length} 個`),
    num("count", "個数（元を含む）", { step: "1", min: "2" }),
    ...(lin ? [num("spacing", "間隔（mm）"), num("angle", "向き（°、横から反時計回り）")]
      : [num("total", "角度（°。360 なら等間隔）"),
        el("div", { class: "row" }, el("span", {}, `中心: ${cname}`),
          el("button", { id: "btn-pat-center", class: m.pick ? "on" : "", onclick: () => { m.pick = !m.pick; renderPatternPanel(); } },
            m.pick ? "点をクリック…" : "点を選ぶ"))]),
    el("div", { class: "hint" }, lin ? "写しは元と同じ大きさ（等しい・平行の拘束）。位置は寸法で決めてください。"
      : "写しは元と同じ大きさ（等しいの拘束）。中心は「点を選ぶ」を押してスケッチの点をクリック。"),
  ]);
}

const KIND_LABEL = { point: "点", line: "線", circle: "円", arc: "円弧", origin: "原点", constraint: "拘束" };

/** スケッチの右パネルの「選択」部分: 選んだものの内訳、付けられる拘束だけのボタン、寸法、使い方。 */
function sketchSelectionPanel(sel) {
  const kinds = sketch.selectionKinds();
  if (!sel.length) return [
    el("h3", {}, "拘束・寸法の付け方"),
    el("ol", { class: "howto" },
      el("li", {}, "要素をクリックして選ぶ（Ctrl+クリックで追加）。赤い点の", el("b", {}, "原点"), "も選べます"),
      el("li", {}, "ここに、選んだものに付けられる拘束のボタンが出ます"),
      el("li", {}, "寸法は、選んで ", el("kbd", {}, "D"), "（または「寸法を付ける」）→ 寸法を置く所をクリック → 値を入力")),
    el("div", { class: "hint" }, "例: 矩形を原点から 20 mm 離す → 原点と矩形の辺を Ctrl+クリック → D → 置く → 20。"
      + " 角の点と原点なら、置く位置で水平距離・垂直距離になります。"),
  ];
  const counts = {};
  for (const k of kinds) counts[k] = (counts[k] || 0) + 1;
  const what = Object.entries(counts).map(([k, n]) => `${KIND_LABEL[k] || k}${n > 1 ? ` ×${n}` : ""}`).join("、");
  const ok = applicableConstraints(kinds);
  const btns = CONSTRAINTS.filter(([t]) => ok.has(t)).map(([t, label]) =>
    el("button", { "data-con": t, onclick: () => sketch.constrain(t) }, label));
  const canDim = sketch.canDimension();
  return [
    el("h3", {}, `選択中: ${what}`),
    canDim ? el("div", { class: "btns" }, el("button", { class: "primary", id: "btn-dim-sel", onclick: () => sketch.setTool("dim"),
      title: "選んだ要素に寸法を付けます。押したあと、寸法を置く所をクリック (D)" }, "寸法を付ける (D)")) : null,
    btns.length ? el("div", { class: "btns" }, btns)
      : el("div", { class: "hint" }, kinds.includes("constraint") ? "拘束・寸法は Del で削除できます。"
        : "この組み合わせに付けられる拘束はありません。2 つ目の要素を Ctrl+クリックで追加してください。"),
  ];
}

$("#btn-sketch-end").onclick = () => act("sketch.end");
$("#show-glyphs").onchange = (e) => { sketch.showGlyphs = e.target.checked; viewer.redraw(); };
