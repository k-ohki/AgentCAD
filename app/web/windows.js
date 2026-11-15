// 文書の切り替えと、文書ごとのウィンドウ（D50）。ほかのウィンドウでの変更の取り込み。
import { $, app, asm, CLIENT_ID, el, rpc, rpcRaw, showMenu, sketch, toast, ui } from "./core.js";
import { clearProps } from "./props.js";

// ---------------------------------------------------------------- 文書の切り替え
export function docTitle(st) {
  document.title = `${st.name} - AgentCAD`;
  const t = $("#doc-title");
  t.textContent = st.name + (st.path ? (st.modified ? " *" : "") : "（未保存）");
  t.title = st.path ? st.path + (st.modified ? "（変更あり）" : "") : "まだ保存していません";
}

export async function refreshDocs() {
  let docs;
  try { docs = await rpcRaw("doc.list"); } catch { return; }
  const sel = $("#doc-select");
  sel.replaceChildren(...docs.map((d) => el("option", { value: d.doc, selected: d.doc === app.doc },
    `${d.kind === "assembly" ? "◫ " : "▰ "}${d.name}${d.modified && d.path ? " *" : ""}`)));
}

// ---------------------------------------------------------------- ウィンドウ（部品・アセンブリごと。D50）
/** 文書を自分のウィンドウで開く。デスクトップ版は殻がウィンドウを作り、ブラウザでは新しいタブ / ウィンドウ。 */
export async function openDocWindow(doc) {
  if (!app.shellWindows) {
    const w = window.open(`/?doc=${encodeURIComponent(doc)}`, `agentcad-${doc}`);
    if (!w) toast("ポップアップがブロックされました。ブラウザでこのページのポップアップを許可してください", true);
    return;
  }
  try { await rpcRaw("ui.open_window", { doc }); } catch (e) { toast(e.message, true); }
}

/** 作った・開いた文書を見せる。デスクトップ版では、今の文書はそのままにして新しいウィンドウで開く（一般的な 3D CAD と同じ）。
 *  here = true なら、デスクトップ版でもこのウィンドウで切り替える（AI が作った文書。会話はこのウィンドウにあるので）。 */
export async function presentDoc(st, here = false) {
  if (!here && app.shellWindows && app.state && app.state.doc !== st.doc) { refreshDocs(); await openDocWindow(st.doc); return; }
  if (app.state?.sketch && app.state.doc !== st.doc) { try { await rpc("sketch.end"); } catch { /* 閉じていてもよい */ } }
  app.doc = st.doc; app.selected = null; app.pickedName = null; app.selEdges.clear(); app.firstMesh = true;
  clearProps(); ui.render(st);
}

$("#btn-window").onclick = (e) => {
  e.stopPropagation();
  rpcRaw("doc.list").then((docs) => {
    const b = $("#btn-window").getBoundingClientRect();
    showMenu(b.left, b.bottom + 2, docs.map((d) => ({
      label: `${d.kind === "assembly" ? "◫" : "▰"} ${d.name}${d.doc === app.doc ? "（このウィンドウ）" : ""}`,
      disabled: d.doc === app.doc, onclick: () => openDocWindow(d.doc) })));
  });
};

/** ほかのウィンドウが文書を変えたら、表示を取り直す（1 秒ごとに確かめる）。 */
async function pollOthers() {
  if (!app.doc || document.body.dataset.ready !== "1" || sketch.drag || asm.drag || pollOthers.busy) return;
  pollOthers.busy = true;
  try {
    const r = await rpcRaw("ws.rev", { since: app.rev || 0, me: CLIENT_ID });
    app.rev = r.rev;
    if (r.others) {
      try { ui.render(await rpcRaw("doc.state", { doc: app.doc })); }
      catch {                                              // ほかのウィンドウで閉じられた
        const docs = await rpcRaw("doc.list");
        if (docs.length) { app.doc = docs[0].doc; app.selected = null; clearProps(); ui.render(await rpcRaw("doc.state", { doc: app.doc })); }
      }
    }
  } catch { /* サーバが止まっている */ }
  pollOthers.busy = false;
}
setInterval(pollOthers, 1000);

$("#doc-select").addEventListener("change", async (e) => {
  try {
    if (app.state?.sketch) await rpc("sketch.end");
    const st = await rpcRaw("doc.state", { doc: e.target.value });
    app.doc = st.doc; app.selected = null; app.pickedName = null; app.selEdges.clear();
    clearProps(); ui.render(st);
  } catch (err) { toast(err.message, true); }
});

$("#btn-newasm").onclick = async () => presentDoc(await rpcRaw("asm.new"));
