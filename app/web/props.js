// 右のパネル（フィーチャーの寸法・設定）と、参照の選び直し、作成直後の ✓ / ✗。
import { $, act, app, el, fill, rpc, sketch, toast, ui, viewer } from "./core.js";

// ---------------------------------------------------------------- プロパティ（通常）
export function clearProps() {
  $("#prop-title").textContent = "プロパティ";
  $("#prop").replaceChildren("フィーチャーツリーで項目を選ぶと、ここで寸法やパラメータを編集できます。");
}

export async function loadProps(id) {
  let p;
  try { p = await rpc("feature.params", { id }); } catch (e) { toast(e.message, true); return; }
  if (app.selected !== id || sketch.active) return;
  $("#prop-title").textContent = `${p.type_label}: ${p.name}`;
  const box = $("#prop");
  fill(box);
  if (app.pending === id) box.append(el("div", { class: "confirm" },       // PropertyManager の ✓ / ✗
    el("button", { class: "ok", id: "btn-confirm", title: "確定 (Enter)", onclick: () => confirmPending(true) }, "✓ OK"),
    el("button", { class: "cancel", id: "btn-cancel", title: "取り消して削除 (Esc)", onclick: () => confirmPending(false) }, "✗ キャンセル")));
  if (app.pickFor?.id === id) box.append(el("div", { class: "picking" }, "3D ビューで対象をクリックしてください（Esc で取り消し）"));
  if (p.state === "error" || p.state === "warning") box.append(el("div", { class: `state ${p.state}` }, p.message));
  if (p.type === "sketch") {
    const vis = ui.sketchShowItem(id);
    box.append(el("div", { class: "btns" },
      el("button", { class: "primary", onclick: () => act("sketch.begin", { id }) }, "✎ スケッチを編集"),
      vis ? el("button", { id: "btn-sketch-visible", title: "3D に常に表示するか（選んでいる間はいつも表示）", onclick: vis.onclick }, vis.label) : null));
  }
  const first = (x) => x.kind === "choice" && ["kind", "size", "end"].includes(x.key);   // 種類・サイズ・押し出し状態を先に
  for (const m of p.option_meta.filter(first)) box.append(optionRow(id, m));
  if (!p.params.length && !p.option_meta.length) box.append(el("div", {}, "編集できる寸法はありません。"));
  for (const q of p.params) box.append(paramRow(id, q));
  for (const m of p.option_meta.filter((x) => !first(x))) box.append(optionRow(id, m));
  if (p.options.edges) box.append(el("div", { class: "hint" }, `対象エッジ: ${p.options.edges.join("、")}`));
  const sup = el("input", { type: "checkbox", checked: p.suppressed });
  sup.addEventListener("change", () => act("feature.suppress", { id, value: sup.checked }));
  const idx = app.state.tree.findIndex((t) => t.id === id);
  box.append(el("div", { class: "actions" },
    el("label", {}, sup, "抑制する"),
    el("button", { onclick: () => act("rollback.set", { index: idx + 1 }) }, "このフィーチャーの後までロールバック"),
    el("button", { onclick: () => act("rollback.set", { index: null }) }, "ロールバックを解除（最後まで）"),
    el("button", { onclick: () => ui.deleteFeature(id) }, "このフィーチャーを削除")));
  box.append(el("div", { class: "hint" },
    "数値を入れて Enter で確定。式を入れると値は式で決まります（例: width / 2、Sketch1.K7 * 2）。式を空にすると解除。"));
}

const AXIS_CHOICES = [["X", "X 軸"], ["Y", "Y 軸"], ["Z", "Z 軸"]];

/** 数値以外の設定 1 つ分（選択肢 / チェック / 平面・向き・軸の参照）。 */
function optionRow(id, m) {
  const set = (value) => act("feature.set", { id, key: m.key, value });
  if (m.kind === "bool") {
    const cb = el("input", { type: "checkbox", checked: !!m.value, "data-opt": m.key });
    cb.addEventListener("change", () => set(cb.checked));
    return el("div", { class: "param" }, el("label", { style: "display:flex;gap:6px;align-items:center" }, cb, m.label));
  }
  if (m.kind === "choice") {
    const sel = el("select", { "data-opt": m.key }, m.choices.map(([v, l]) => el("option", { value: v, selected: m.value === v }, l)));
    sel.addEventListener("change", () => set(sel.value));
    return el("div", { class: "param" }, el("label", {}, m.label), sel);
  }
  if (m.kind === "faces") {                               // シェルの取り除く面: 一覧 + 3D で足す / 外す
    const list = m.value || [];
    const rows = list.map((f) => el("li", {}, el("span", { class: "name" }, f),
      el("button", { title: "外す", onclick: () => set(list.filter((x) => x !== f)) }, "×")));
    const add = el("button", { "data-pick": m.key, title: "3D で面をクリックして足します（もう一度クリックで外す）。Esc で終了" },
      app.pickFor?.id === id && app.pickFor.key === m.key ? "選んでいます…（Esc で終了）" : "面を選ぶ");
    add.addEventListener("click", async () => {
      app.pickFor = { id, key: m.key, want: ["face"], multi: true }; app.selEdges.clear();
      await act("view.rollback", { id }); ui.showPick(-1, false);
    });
    return el("div", { class: "param" }, el("label", {}, `${m.label}（${list.length}）`),
      list.length ? el("ul", { class: "facelist" }, rows) : el("div", { class: "hint" }, "なし（閉じた中空の立体になります）"),
      el("div", { class: "row" }, add, el("button", { disabled: !list.length, onclick: () => set([]) }, "すべて外す")));
  }
  if (m.kind === "seeds") {
    const names = (m.value || []).map((i) => app.state.tree.find((t) => t.id === i)?.name || i);
    return el("div", { class: "param" }, el("label", {}, m.label), el("div", {}, names.join("、") || "（なし）"));
  }
  // 参照: 決まった選択肢（平面の一覧 / X・Y・Z）＋ 3D で選ぶ
  const v = m.value, cur = v ? JSON.stringify(v) : "";
  const opts = [];
  if (m.kind === "dir?") opts.push(["", "なし（1 方向）"]);
  if (m.kind === "plane") {
    for (const p of app.state.planes) if (p.ref.feature !== id) opts.push([JSON.stringify(p.ref), p.name]);
  } else {
    for (const [a, l] of AXIS_CHOICES) opts.push([JSON.stringify({ axis: a }), l]);
  }
  if (v && !opts.some(([k]) => k === cur)) opts.push([cur, v.face ? `面: ${v.face}` : v.edge ? `エッジ: ${v.edge}` : cur]);
  const sel = el("select", { "data-opt": m.key }, opts.map(([k, l]) => el("option", { value: k, selected: k === cur }, l)));
  sel.addEventListener("change", () => set(sel.value ? JSON.parse(sel.value) : null));
  const want = { plane: ["face", "plane"], dir: ["edge"], "dir?": ["edge"], axis: ["face", "edge"] }[m.kind];
  const label = { plane: "面を選ぶ", dir: "エッジを選ぶ", "dir?": "エッジを選ぶ", axis: "円筒面・エッジを選ぶ" }[m.kind];
  const pick = el("button", { "data-pick": m.key, title: "3D ビューでクリックして指定します" }, label);
  pick.addEventListener("click", async () => {
    // このフィーチャーの直前の形を見せて選ばせる（後のフィーチャーで面やエッジの名前が変わるため）
    app.pickFor = { id, key: m.key, want }; app.selEdges.clear();
    await act("view.rollback", { id });
    ui.showPick(-1, false);
  });
  return el("div", { class: "param" }, el("label", {}, m.label), el("div", { class: "row" }, sel, pick));
}

/** 3D（またはツリーの平面）で選んだものを、フィーチャーの設定に入れる。 */
export async function finishPick(ref) {
  const pf = app.pickFor;
  viewer.setSelected(-1);
  if (!pf) return;
  if (pf.multi) {                                         // 面を足す / 外す。Esc か ✓ まで選び続ける
    if (!ref?.face) { toast("面をクリックしてください", true); return; }
    const p = await rpc("feature.params", { id: pf.id });
    const cur = p.options[pf.key] || [];
    const next = cur.includes(ref.face) ? cur.filter((f) => f !== ref.face) : [...cur, ref.face];
    if (await act("feature.set", { id: pf.id, key: pf.key, value: next })) await act("view.rollback", { id: pf.id });
    app.pickFor = pf; loadProps(pf.id); ui.showPick(-1, false);
    return;
  }
  app.pickFor = null;
  const kind = ref && Object.keys(ref)[0];
  if (!ref || !(pf.want.includes(kind) || (pf.want.includes("plane") && (kind === "plane" || kind === "feature")))) {
    toast("ここでは使えません。選び直してください", true);
    await act("view.rollback", {}); ui.showPick(-1, false); return;
  }
  if (!(await act("feature.set", { id: pf.id, key: pf.key, value: ref }))) await act("view.rollback", {});
  ui.showPick(-1, false);
}

/** 作ったばかりのフィーチャーを確定する / 取り消して削除する。 */
export async function confirmPending(ok) {
  const id = app.pending; if (!id) return;
  const picking = !!app.pickFor;
  app.pending = null; app.pickFor = null;
  if (picking) await act("view.rollback", {});
  if (ok) { loadProps(id); return; }
  await act("feature.delete", { id });
}

function paramRow(id, q) {
  const fmt = (v) => (Math.round(v * 1e6) / 1e6).toString();
  const val = el("input", { class: "value" + (q.expr ? " driven" : ""), type: "text", inputmode: "decimal", value: fmt(q.value),
    title: q.expr ? "式で決まっています（数値を入れると式は解除されます）" : "" });
  const expr = el("input", { class: "expr", type: "text", value: q.expr ?? "", placeholder: "式", spellcheck: "false" });
  val.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const v = Number(val.value);
      if (!Number.isFinite(v)) { toast("数値を入れてください", true); return; }
      act("feature.edit", { id, key: q.key, value: v });
    } else if (e.key === "Escape") val.value = fmt(q.value);
  });
  expr.addEventListener("keydown", (e) => {
    if (e.key === "Enter") act("feature.edit", { id, key: q.key, expr: expr.value });
    else if (e.key === "Escape") expr.value = q.expr ?? "";
  });
  return el("div", { class: "param" }, el("label", {}, q.label),
    el("div", { class: "row" }, val, el("span", { class: "unit" }, q.unit), expr));
}
