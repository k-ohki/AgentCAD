// AI アシスタントのパネル（D67）。依頼を送り（agent.chat）、経過を取りに行って（agent.poll）描く。
// モデル・文書の操作はすべてサーバ側の AI が行う。ここは会話の表示と、AI からの画面操作の依頼（断面など）を app.js に渡すだけ。
import { $, app, el, rpcRaw, toast } from "./core.js";

let actions = { showDoc: async () => {}, ui: () => {} };
let next = 0, polling = false, pending = [], newModel = false, cfg = null, lastTool = null;

const MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-2", "gpt-5", "gpt-5-mini", "llama3.1"];

/** app.js が起動時に呼ぶ。showDoc(doc): その文書を画面に出す / ui(action, args): 画面だけの操作。 */
export function initAgent(handlers) {
  actions = { ...actions, ...handlers };
  build();
  rpcRaw("agent.poll", { since: 0 }).then((r) => {               // 読み直したときは、それまでの会話を出す
    if (r.events.length) { $("#ai-log").replaceChildren(); next = 0; consume(r); if (r.running) poll(); }
  }).catch(() => {});
}

/** パネルを開く。forNew: 新しいモデルを作る依頼として（新規 ▾ →「AI で作成」）。 */
export function openAgent(forNew = false) {
  document.body.classList.add("ai-open");
  $("#ai").hidden = false;
  newModel = forNew;
  const ta = $("#ai-input");
  ta.placeholder = forNew ? "作りたいものを書いてください（例: 2 階建ての家、歯車、テーブル）" : "やりたいことを書いてください（例: 図面を書いて、断面を表示して）";
  ta.focus();
  loadSettings();
}

export function closeAgent() { document.body.classList.remove("ai-open"); $("#ai").hidden = true; }
export function toggleAgent() { if ($("#ai").hidden) openAgent(); else closeAgent(); }

// ---------------------------------------------------------------- DOM
function build() {
  const box = $("#ai");
  box.replaceChildren(
    el("div", { class: "ai-head" },
      el("b", {}, "AI アシスタント"), el("span", { id: "ai-model", class: "ai-model" }, ""),
      el("span", { class: "grow" }),
      el("button", { title: "新しい会話", onclick: resetChat }, "新規"),
      el("button", { id: "ai-gear", title: "設定（API キー・モデル）", onclick: () => toggleSettings() }, "⚙"),
      el("button", { title: "閉じる", onclick: closeAgent }, "×")),
    el("form", { id: "ai-settings", hidden: true, autocomplete: "off", onsubmit: (e) => { e.preventDefault(); saveSettings(); } }),
    el("div", { id: "ai-log" }, el("div", { class: "ai-empty" },
      "AI に、自然な言葉で頼めます。例:", el("ul", {},
        el("li", {}, "幅 300 の 2 階建ての家を作って"), el("li", {}, "この部品の図面を書いて"),
        el("li", {}, "中央で断面を表示して"), el("li", {}, "（画像を付けて）この建物を作って")))),
    el("div", { id: "ai-attach" }),
    el("div", { class: "ai-input-row" },
      el("textarea", { id: "ai-input", rows: "3", onkeydown: (e) => {
        e.stopPropagation();                                     // 画面のキー操作（Del・矢印など）に渡さない
        if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
      } }),
      el("div", { class: "ai-btns" },
        el("label", { class: "ai-file", title: "画像を付ける" }, "🖼",
          el("input", { type: "file", accept: "image/png,image/jpeg,image/gif,image/webp", multiple: true, hidden: true,
            onchange: (e) => { addFiles(e.target.files); e.target.value = ""; } })),
        el("button", { id: "ai-send", class: "primary", onclick: () => (polling ? stop() : send()) }, "送信"))));
  box.addEventListener("paste", (e) => {                          // 画像の貼り付け
    const files = [...(e.clipboardData?.files || [])].filter((f) => f.type.startsWith("image/"));
    if (files.length) { e.preventDefault(); addFiles(files); }
  });
  box.addEventListener("dragover", (e) => e.preventDefault());
  box.addEventListener("drop", (e) => { e.preventDefault(); addFiles(e.dataTransfer.files); });
}

// ---------------------------------------------------------------- 設定
async function loadSettings() {
  try { cfg = await rpcRaw("agent.settings"); } catch { return; }
  $("#ai-model").textContent = cfg.has_key || cfg.provider === "openai" ? cfg.model_name : "API キーが未設定";
  if (!cfg.has_key && cfg.provider === "anthropic") toggleSettings(true);
}

function toggleSettings(open) {
  const f = $("#ai-settings");
  f.hidden = open === undefined ? !f.hidden : !open;
  if (f.hidden || !cfg) return;
  const opt = (v, label, cur) => el("option", { value: v, selected: v === cur }, label);
  const row = (label, input) => el("label", { class: "ai-row" }, el("span", {}, label), input);
  f.replaceChildren(
    row("接続先", el("select", { name: "provider" }, ...Object.entries(cfg.providers).map(([k, v]) => opt(k, v, cfg.provider)))),
    row("API キー", el("input", { name: "api_key", type: "password", placeholder: cfg.key_hint ? `設定済み（${cfg.key_hint}）` : "sk-…" })),
    row("モデル", el("input", { name: "model", list: "ai-models", value: cfg.model, placeholder: cfg.model_name })),
    el("datalist", { id: "ai-models" }, ...MODELS.map((m) => el("option", { value: m }))),
    row("接続先 URL", el("input", { name: "base_url", value: cfg.base_url, placeholder: "空なら既定（OpenAI 互換は https://api.openai.com/v1）" })),
    row("思考の深さ", el("select", { name: "effort" }, opt("", "既定", cfg.effort), ...cfg.efforts.map((x) => opt(x, x, cfg.effort)))),
    el("label", { class: "ai-row" }, el("input", { type: "checkbox", name: "web_search", checked: cfg.web_search }), "ウェブ検索を使う（Claude）"),
    el("div", { class: "hint" }, "設定は ~/.agentcad/agent.json に保存します（API キーもそのまま）。接続先を OpenAI 互換にすると ChatGPT・Ollama・LM Studio などが使えます。"),
    el("div", { class: "btns" }, el("button", { class: "primary" }, "保存"),
      el("button", { type: "button", onclick: () => toggleSettings(false) }, "閉じる")));
}

async function saveSettings() {
  const f = $("#ai-settings"), v = {};
  for (const i of f.querySelectorAll("[name]")) v[i.name] = i.type === "checkbox" ? i.checked : i.value.trim();
  if (!v.api_key) delete v.api_key;                               // 空なら変えない
  try { cfg = await rpcRaw("agent.settings", v); toast("AI の設定を保存しました"); toggleSettings(false); loadSettings(); }
  catch (e) { toast(e.message, true); }
}

// ---------------------------------------------------------------- 画像
async function addFiles(files) {
  for (const f of files) {
    if (!f.type.startsWith("image/")) continue;
    pending.push(await shrink(f));
  }
  drawAttach();
}

/** 画像を長い辺 1568 px までに縮めて base64 にする（API の画像の上限に合わせる）。 */
function shrink(file) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const k = Math.min(1, 1568 / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      const url = c.toDataURL("image/png");
      resolve({ media_type: "image/png", data: url.split(",")[1], url });
      URL.revokeObjectURL(img.src);
    };
    img.onerror = reject;
    img.src = URL.createObjectURL(file);
  });
}

function drawAttach() {
  $("#ai-attach").replaceChildren(...pending.map((p, i) => el("span", { class: "ai-thumb" }, el("img", { src: p.url }),
    el("button", { title: "外す", onclick: () => { pending.splice(i, 1); drawAttach(); } }, "×"))));
}

// ---------------------------------------------------------------- 会話
async function send() {
  const ta = $("#ai-input");
  let text = ta.value.trim();
  if (!text && !pending.length) return;
  if (newModel) text = `新しいパートとして、次のものを作ってください: ${text}`;
  try {
    await rpcRaw("agent.chat", { text, images: pending.map(({ media_type, data }) => ({ media_type, data })), doc: app.doc });
  } catch (e) { toast(e.message, true); if (/API キー/.test(e.message)) toggleSettings(true); return; }
  ta.value = ""; pending = []; drawAttach(); newModel = false;
  $("#ai-input").placeholder = "続けて頼めます";
  poll();
}

async function stop() { try { await rpcRaw("agent.stop"); } catch (e) { toast(e.message, true); } }

async function resetChat() {
  try { await rpcRaw("agent.reset"); } catch (e) { toast(e.message, true); return; }
  next = 0; $("#ai-log").replaceChildren(); build(); openAgent(false);
}

async function poll() {
  if (polling) return;
  polling = true; busy(true);
  try {
    for (;;) {
      const r = await rpcRaw("agent.poll", { since: next, wait: 15 });
      await consume(r);
      if (!r.running && !r.events.length) break;
      if (!r.running) { const last = await rpcRaw("agent.poll", { since: next }); await consume(last); break; }
    }
  } catch (e) { toast(`AI との通信が切れました: ${e.message}`, true); }
  polling = false; busy(false);
}

function busy(on) {
  const b = $("#ai-send");
  b.textContent = on ? "■ 止める" : "送信"; b.classList.toggle("primary", !on);
  $("#ai").classList.toggle("running", on);
}

async function consume(r) {
  const log = $("#ai-log");
  log.querySelector(".ai-empty")?.remove();
  for (const e of r.events) {
    if (e.type === "user") {
      log.append(el("div", { class: "ai-msg user" }, ...e.images.map((u) => el("img", { src: u })), el("div", {}, e.text)));
    } else if (e.type === "text" || e.type === "thinking") {
      let last = log.lastElementChild;
      const cls = e.type === "text" ? "assistant" : "thinking";
      if (!last || !last.classList.contains(cls)) {
        last = e.type === "text" ? el("div", { class: "ai-msg assistant" }) : el("details", { class: "ai-msg thinking" }, el("summary", {}, "考えていること"), el("div", {}));
        last.dataset.raw = "";
        log.append(last);
      }
      last.dataset.raw += e.text;
      (e.type === "text" ? last : last.lastElementChild).innerHTML = md(last.dataset.raw);
    } else if (e.type === "tool") {
      lastTool = el("details", { class: "ai-tool" }, el("summary", {}, `🔧 ${toolLabel(e)}`), el("pre", {}, e.input));
      log.append(lastTool);
    } else if (e.type === "tool_result") {
      const t = lastTool;
      if (t) { if (e.is_error) t.classList.add("err"); t.append(el("pre", { class: e.is_error ? "err" : "" }, e.text)); }
      if (e.image) log.append(el("img", { class: "ai-render", src: e.image, title: "AI が確かめた画像" }));
    } else if (e.type === "doc") {
      await actions.showDoc(e.doc);
    } else if (e.type === "ui") {
      try { await actions.ui(e.action, e.args || {}); } catch (err) { toast(err.message, true); }
    } else if (e.type === "error") {
      log.append(el("div", { class: "ai-msg error" }, e.message));
    }
  }
  next = r.next;
  log.scrollTop = log.scrollHeight;
}

function toolLabel(e) {
  let x = {};
  try { x = JSON.parse(e.input); } catch { /* 長くて切れた */ }
  if (e.name === "cad_call") return `${x.method || ""}`;
  if (e.name === "cad_batch") return `まとめて ${x.calls?.length ?? ""} 件`;
  if (e.name === "render_view") return `形を確認（${x.view || "iso"}）`;
  if (e.name === "ui_action") return `画面: ${x.action || ""}`;
  if (e.name === "run_python") return "Python を実行";
  if (e.name === "api_help") return `API を調べる ${x.method || ""}`;
  return e.name;
}

/** 簡単な Markdown（太字・コード・見出し・箇条書き・改行）。HTML は先にエスケープする。 */
function md(s) {
  const esc = s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return esc
    .replace(/```([\s\S]*?)```/g, (_, c) => `<pre>${c.replace(/^\w*\n/, "")}</pre>`)
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
    .replace(/^#{1,4} (.*)$/gm, "<b>$1</b>")
    .replace(/^[-*] (.*)$/gm, "・$1")
    .replace(/\n/g, "<br>");
}
