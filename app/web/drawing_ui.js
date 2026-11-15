// 図面の画面（D60）: 左の一覧（シート・ビュー・寸法）、右のパネル（シート / ビュー / 寸法の設定）、図面のタブのボタン、PDF・DXF の書き出し。
import { setTab } from "./toolbar.js";
import { DrawingEditor } from "./drawing.js";
import { $, act, app, askText, el, fill, pickPath, rpc, rpcRaw, toast } from "./core.js";
import { measureEnd } from "./evaluate.js";
import { docTitle, presentDoc } from "./windows.js";

export const drawing = new DrawingEditor($("#sheet"), {
  rpc: (method, params) => act(method, params),
  onSelect: () => { renderDrawingTree(); renderDrawingProps(); },
  onTool: (t) => { $("#btn-drw-dim").classList.toggle("on", t === "dim"); $("#btn-drw-proj").classList.toggle("on", t === "project"); },
  status: (t) => { $("#status-pick").textContent = t; },
  toast,
});

export function renderDrawing(st, switching) {
  measureEnd(); $("#float-panel").hidden = true; $("#confirm-corner").hidden = true;
  setTab("drawing");
  app.mesh = null;
  docTitle(st);
  $("#btn-undo").disabled = !st.undo; $("#btn-undo").title = st.undo ? `元に戻す: ${st.undo} (Ctrl+Z)` : "元に戻す";
  $("#btn-redo").disabled = !st.redo; $("#btn-redo").title = st.redo ? `やり直し: ${st.redo} (Ctrl+Y)` : "やり直し";
  $("#kernel").textContent = `カーネル: ${st.kernel}`;
  if (switching) { drawing.sel = null; drawing.setTool("select"); }
  drawing.set(st, { fit: switching });
  $("#status-info").textContent = `${st.sheet.size} ${st.sheet.landscape ? "横" : "縦"} / 縮尺 ${st.sheet.scale_text} / モデル: ${st.model.name ?? "（開かれていません）"}`;
  $("#btn-drw-proj").disabled = !drawing.view(drawing.sel);
  renderDrawingTree(); renderDrawingProps();
}

function select(id) { drawing.sel = id; drawing.draw(); renderDrawingTree(); renderDrawingProps(); }

function renderDrawingTree() {
  const st = app.state; if (st?.kind !== "drawing") return;
  $("#btn-drw-proj").disabled = !drawing.view(drawing.sel);
  const row = (id, ico, name, title) => el("li", { class: `item${drawing.sel === id ? " selected" : ""}`, "data-id": id ?? "sheet", title,
    onclick: () => select(id) }, el("span", { class: "ico" }, ico), el("span", { class: "name" }, name));
  fill($("#drw-tree"),
    row(null, "▭", `シート（${st.sheet.size}、${st.sheet.scale_text}）`, "用紙・縮尺・名称"),
    st.views.map((v) => row(v.id, "◫", `${v.label}${v.scale_text ? `（${v.scale_text}）` : ""}`, v.parent ? `${v.parent} の投影図` : "モデルビュー")),
    st.dims.map((d) => row(d.id, "↔", `寸法 ${d.text}${d.dangling ? "（参照切れ）" : ""}`, "寸法")));
}

function renderDrawingProps() {
  const st = app.state; if (st?.kind !== "drawing") return;
  const box = $("#prop"), v = drawing.view(drawing.sel), d = drawing.dim(drawing.sel);
  const scaleSelect = (id, value, onpick) => {
    const std = st.scales.includes(value);
    const s = el("select", { id, onchange: async (e) => {
      let val = e.target.value;
      if (val === "custom") { val = await askText("縮尺（例 1:3、3:2）", value || "1:1"); if (!val) { renderDrawingProps(); return; } }
      onpick(val);
    } }, st.scales.map((x) => el("option", { value: x, selected: x === value }, x)),
    !std && value ? el("option", { value, selected: true }, value) : null, el("option", { value: "custom" }, "カスタム…"));
    return s;
  };
  if (v) {
    $("#prop-title").textContent = `ビュー: ${v.label}`;
    fill(box,
      el("div", { class: "param" }, el("label", {}, "縮尺"),
        el("label", { class: "chk" }, el("input", { type: "checkbox", id: "drw-view-sheet-scale", checked: !v.scale,
          onchange: (e) => act("drw.view_set", { id: v.id, scale: e.target.checked ? null : st.sheet.scale }) }), "シートの縮尺を使用"),
        v.scale ? scaleSelect("drw-view-scale", v.scale_text, (val) => act("drw.view_set", { id: v.id, scale: val })) : null),
      el("label", { class: "chk" }, el("input", { type: "checkbox", id: "drw-view-hidden", checked: v.hidden,
        onchange: (e) => act("drw.view_set", { id: v.id, hidden: e.target.checked }) }), "隠線を表示"),
      el("div", { class: "tag" }, v.parent ? "投影図: 親のビューとそろう方向にだけ動きます" : "ドラッグで移動できます"),
      el("div", { class: "btns" },
        el("button", { onclick: () => { drawing.setTool("project"); } }, "このビューから投影図"),
        el("button", { onclick: () => act("drw.delete", { ids: [v.id] }) }, "ビューを削除")));
    return;
  }
  if (d) {
    $("#prop-title").textContent = `寸法: ${d.text}`;
    fill(box,
      el("div", { class: "tag" }, d.dangling ? "相手のエッジが見つかりません（モデルが変わりました）。消して付け直してください"
        : "値はモデルから決まります（モデルを変えると追従します）。文字をドラッグで移動"),
      el("div", { class: "btns" }, el("button", { onclick: () => act("drw.delete", { ids: [d.id] }) }, "寸法を削除")));
    return;
  }
  $("#prop-title").textContent = "シート";
  const title = el("input", { type: "text", id: "drw-title", value: st.sheet.title, placeholder: st.name, size: 18 });
  title.addEventListener("change", () => act("drw.sheet", { title: title.value }));
  fill(box,
    el("div", { class: "param" }, el("label", {}, "縮尺"), scaleSelect("drw-scale", st.sheet.scale_text, (val) => act("drw.sheet", { scale: val }))),
    el("div", { class: "param" }, el("label", {}, "用紙"), el("div", { class: "row" },
      el("select", { id: "drw-size", onchange: (e) => act("drw.sheet", { size: e.target.value }) },
        st.sizes.map((x) => el("option", { value: x, selected: x === st.sheet.size }, x))),
      el("select", { id: "drw-landscape", onchange: (e) => act("drw.sheet", { landscape: e.target.value === "1" }) },
        el("option", { value: "1", selected: st.sheet.landscape }, "横"), el("option", { value: "0", selected: !st.sheet.landscape }, "縦")))),
    el("div", { class: "param" }, el("label", {}, "名称（表題欄）"), el("div", { class: "row" }, title)),
    el("div", { class: "tag" }, `モデル: ${st.model.name ?? "開かれていません"} / 投影法: 第三角法`),
    st.error ? el("div", { class: "err" }, st.error) : null,
    el("div", { class: "hint" }, "ビューはドラッグで移動、ホイールで拡大・縮小、右ドラッグ（か何もない所のドラッグ）で画面を移動。"
      + " ビューや寸法をクリックすると、ここに設定が出ます。モデル（パート）を変えると図面も追従します。"));
}

// ---------------------------------------------------------------- 図面のタブ
$("#btn-drw-std").onclick = () => act("drw.view_add", { standard: true });
$("#btn-drw-view").onclick = (e) => { e.stopPropagation(); const m = $("#drw-view-menu"); m.hidden = !m.hidden; };
document.addEventListener("click", () => { $("#drw-view-menu").hidden = true; });
document.querySelectorAll("#drw-view-menu [data-orient]").forEach((b) => (b.onclick = () =>
  drawing.setTool("place", { orient: b.dataset.orient, label: b.textContent })));
$("#btn-drw-proj").onclick = () => {
  if (!drawing.view(drawing.sel)) { toast("投影図の元にするビューを選んでください", true); return; }
  drawing.setTool(drawing.tool === "project" ? "select" : "project");
};
$("#btn-drw-dim").onclick = () => drawing.setTool(drawing.tool === "dim" ? "select" : "dim");
$("#btn-drw-fit").onclick = () => drawing.fit();

async function exportDrawing(format, scale) {
  const st = app.state; if (st?.kind !== "drawing") return;
  const hint = (st.path ? st.path.replace(/\.usda$/i, "") : st.name) + "." + format;
  const path = await pickPath("save", hint, `書き出し先のパス（拡張子 .${format} は自動で付きます）`, format);
  if (!path) return;
  try {
    const r = await rpc("drw.export", { format, path, scale });
    toast(`書き出しました: ${r.path}`);
  } catch (e) { toast(e.message, true); }
}
$("#btn-drw-pdf").onclick = () => exportDrawing("pdf", "sheet");
$("#btn-drw-dxf").onclick = () => exportDrawing("dxf", "sheet");
$("#btn-drw-dxf-real").onclick = () => exportDrawing("dxf", "model");

/** 新規 → 図面: 今のパート（アセンブリ・図面を開いているときは、開いているパートが 1 つならそれ）の図面を作る。 */
$("#btn-newdrw").onclick = async () => {
  let model = app.state?.kind === "part" ? app.doc : null;
  if (!model) {
    const parts = (await rpcRaw("doc.list")).filter((d) => d.kind === "part");
    if (parts.length === 1) model = parts[0].doc;
    else { toast(parts.length ? "図面にするパートを開いて（文書を切り替えて）から、新規 → 図面 を選んでください" : "図面にするパートがありません", true); return; }
  }
  if (app.state?.sketch) { try { await rpc("sketch.end"); } catch { /* 閉じていてもよい */ } }
  try { await presentDoc(await rpcRaw("drw.new", { model })); } catch (e) { toast(e.message, true); }
};

/** 図面のときのキー。処理したら true。 */
export function drawingKey(e) {
  const key = e.key.toLowerCase();
  if (e.key === "Escape") { drawing.cancel(); return true; }
  if ((e.key === "Delete" || e.key === "Backspace") && drawing.sel) { act("drw.delete", { ids: [drawing.sel] }); return true; }
  if (key === "d" && !e.ctrlKey && !e.metaKey) { drawing.setTool("dim"); return true; }
  if (key === "f" && !e.ctrlKey && !e.metaKey) { drawing.fit(); return true; }
  return false;
}
