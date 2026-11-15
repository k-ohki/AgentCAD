// スケッチ編集モード: 2D オーバーレイへの描画、作図ツール、選択・ドラッグ、寸法の表示と編集。
// 形や拘束の計算はすべて Python 側。ここは操作を API に送り、返ってきたスケッチを描く。

const COLOR = { under: "#1f5fd6", full: "#15181d", over: "#d32f2f", sel: "#ff8a00", hover: "#18a058",
  preview: "#6b7380", dim: "#0b3d91" };
const SNAP_PX = 9, HIT_PX = 6, ARROW_PX = 9, DIM_GAP_PX = 24;
const GLYPH = { horizontal: "H", vertical: "V", parallel: "∥", perpendicular: "⊥", equal: "=", tangent: "T",
  concentric: "◎", midpoint: "M", point_on_line: "◦", point_on_circle: "◦", symmetric: "⇋", fix: "⚓",
  coincident: "●", on_edge: "⊡", collinear: "⋯", ext_point: "⊙", ext_line: "⊡" };
export const TOOLS = [
  ["select", "選択", "S"], ["line", "線", "L"], ["cline", "中心線", ""], ["rect", "矩形", "R"], ["crect", "中心矩形", ""],
  ["circle", "円", "C"], ["arc", "円弧", "A"], ["arc3", "3 点円弧", ""], ["tarc", "正接円弧", ""], ["slot", "スロット", ""],
  ["polygon", "多角形", ""], ["point", "点", "P"], ["dim", "寸法", "D"], ["trim", "トリム", "T"], ["extend", "延長", ""],
  ["fillet", "フィレット", ""], ["chamfer", "面取り", ""], ["offset", "オフセット", "O"],
];
/** ツールごとの説明（ステータスバーに出す）。 */
export const TOOL_HINT = {
  line: "始点・終点をクリック（続けて描けます。Esc / ダブルクリックで終了）", cline: "中心線（作図線）: 始点・終点をクリック",
  rect: "対角の 2 点をクリック", crect: "中心 → 角 をクリック", circle: "中心 → 円周上の点 をクリック",
  arc: "中心 → 始点 → 終点 をクリック", arc3: "始点 → 終点 → 円弧が通る点 をクリック",
  tarc: "線か円弧の端点 → 終点 をクリック（端点で正接する円弧）", slot: "中心 1 → 中心 2 → 幅 をクリック",
  polygon: "中心 → 頂点 をクリック", point: "点を置く位置をクリック", dim: "要素をクリック（2 つまで。ほかのスケッチ・モデルの点やエッジも可）→ 置く位置をクリック",
  trim: "消したい部分をクリック", extend: "伸ばしたい線の端の近くをクリック",
  fillet: "角の点（または線を 2 本）をクリック", chamfer: "角の点（または線を 2 本）をクリック",
  offset: "要素をクリックして選び、オフセットする側の何もない所をクリック",
};
/** ツールを選んだときに値を聞くもの: ツール → [質問, 設定の名前] */
const TOOL_VALUE = { fillet: ["フィレットの半径（mm）", "filletR"], chamfer: ["面取りの距離（mm）", "chamferD"],
  offset: ["オフセットの距離（mm）", "offsetD"], polygon: ["辺の数", "polySides"] };
const DRAW_NEED = { line: 2, cline: 2, rect: 2, crect: 2, circle: 2, arc: 3, arc3: 3, tarc: 2, slot: 3, polygon: 2, point: 1 };
const PICK_TOOLS = ["select", "dim", "trim", "extend", "fillet", "chamfer", "offset"];
export const CONSTRAINTS = [
  ["coincident", "一致"], ["horizontal", "水平"], ["vertical", "垂直"], ["parallel", "平行"],
  ["perpendicular", "直角"], ["tangent", "正接"], ["equal", "等しい"], ["concentric", "同心"],
  ["midpoint", "中点"], ["symmetric", "対称"], ["fix", "固定"],
];

/** 選んだ要素の種類（point / line / round）から、付けられる拘束（一般的な 3D CAD の「拘束の追加」に出るもの）。 */
export function applicableConstraints(kinds) {
  const k = kinds.map((t) => (t === "circle" || t === "arc" ? "round" : t === "origin" ? "point" : t));
  const n = (t) => k.filter((x) => x === t).length, all = (t) => k.every((x) => x === t);
  const out = new Set();
  if (!k.length || k.includes("constraint")) return out;
  out.add("fix");
  if (all("line")) { out.add("horizontal"); out.add("vertical"); }
  if (k.length === 2) {
    const key = [...k].sort().join("+");
    if (key === "point+point") ["coincident", "horizontal", "vertical"].forEach((t) => out.add(t));
    if (key === "line+point") ["coincident", "midpoint"].forEach((t) => out.add(t));
    if (key === "point+round") out.add("coincident");
    if (key === "line+line") ["parallel", "perpendicular", "equal"].forEach((t) => out.add(t));
    if (key === "line+round") out.add("tangent");
    if (key === "round+round") ["tangent", "equal", "concentric"].forEach((t) => out.add(t));
  }
  if (k.length === 3 && n("point") === 2 && n("line") === 1) out.add("symmetric");
  if (kinds.includes("origin")) out.delete("fix");
  return out;
}
const DIM_PREFIX = { radius: "R", diameter: "⌀", angle: "", offset: "⇥ " };
const add2 = (a, b) => [a[0] + b[0], a[1] + b[1]], sub2 = (a, b) => [a[0] - b[0], a[1] - b[1]];
const mul2 = (a, k) => [a[0] * k, a[1] * k], dot2 = (a, b) => a[0] * b[0] + a[1] * b[1];
const len2 = (a) => Math.hypot(a[0], a[1]), unit2 = (a, fb = [1, 0]) => { const l = len2(a); return l > 1e-12 ? mul2(a, 1 / l) : fb; };

export class SketchEditor {
  constructor(viewer, overlay, { rpc, onState, toast, onSelection, ask }) {
    this.v = viewer; this.cv = overlay; this.ctx = overlay.getContext("2d");
    this.rpc = rpc; this.onState = onState; this.toast = toast; this.onSelection = onSelection; this.ask = ask;
    this.opt = { filletR: 5, chamferD: 5, offsetD: 5, polySides: 6 };
    this.sk = null; this.tool = "select"; this.pending = []; this.cursor = null; this.snap = null;
    this.sel = new Set(); this.hover = null; this.drag = null; this.hits = []; this.showGlyphs = true;
    this.labelDrag = null; this.box = null;
    /** モデル（3D）の選択（エンティティ変換用）。app.js が { hover(pos), pick(pos, ctrl), clear() } を入れる */
    this.model = null;
    this.v.afterDraw = () => this.draw();
  }

  // ------------------------------------------------------------ 状態
  get active() { return !!this.sk; }

  set(sk) {
    const entering = !this.sk || this.sk.id !== sk?.id;
    this.sk = sk;
    if (!sk || entering) this.patternMode = null;
    if (!sk) { this.v.interceptor = null; this.pending = []; this.sel.clear(); this.v.redraw(); return; }
    this.v.interceptor = this;
    this.E = Object.fromEntries(sk.entities.map((e) => [e.id, e]));
    // 原点: スケッチにまだ原点の固定点が無ければ、選択・寸法の相手にできる仮の点（"ORIGIN"）を置く。
    // サーバは "ORIGIN" を受け取ると原点の固定点を作って使う
    this.originId = sk.entities.find((e) => e.type === "point" && e.fixed && Math.abs(e.x) < 1e-12 && Math.abs(e.y) < 1e-12)?.id;
    if (!this.originId) this.E.ORIGIN = { id: "ORIGIN", type: "point", x: 0, y: 0, fixed: true, virtual: true };
    for (const id of [...this.sel]) if (!this.E[id] && !sk.constraints.some((c) => c.id === id)) this.sel.delete(id);
    if (this.sel.has("ORIGIN") && this.originId) { this.sel.delete("ORIGIN"); this.sel.add(this.originId); }
    if (this.patternMode) {
      this.patternMode.ids = this.patternMode.ids.filter((id) => this.E[id]);
      if (!this.patternMode.ids.length) this.patternMode = null;
    }
    if (entering && sk.plane) {
      this.sel.clear(); this.tool = "select"; this.pending = []; this.onToolChange?.("select");
      this.v.lookNormal(sk.plane.origin, sk.plane.normal);
      this._fitSketch();
    }
    this.onSelection?.([...this.sel]);
    this.v.redraw();
  }

  /** ツールを切り替える。フィレット・面取り・オフセット・多角形は、先に値を聞く（一般的な 3D CAD の PropertyManager の代わり）。 */
  async setTool(t) {
    const q = TOOL_VALUE[t];
    if (q && this.ask) {
      const v = await this.ask(q[0], this.opt[q[1]]);
      if (v === null) return;
      const n = Number(v);
      if (!(n > 0) || (t === "polygon" && !Number.isInteger(n))) { this.toast("正の数を入れてください", true); return; }
      this.opt[q[1]] = n;
    }
    // スマート寸法は、先に選んだ要素があればそれを使う（一般的な 3D CAD と同じ。選んでから D）
    this.patternMode = null;
    const pre = t === "dim" && this.canDimension() ? [...this.sel] : [];
    if (t !== "offset") this.sel.clear();
    this.tool = t; this.pending = pre;
    pre.forEach((id) => this.sel.add(id));
    this._selChanged(); this.onToolChange?.(t);
    if (pre.length) this.toast(pre.length === 1 && this.E[pre[0]].type === "point"
      ? "もう 1 つの要素をクリックしてください" : "寸法を置く所をクリックしてください（2 つ目の要素をクリックすると相手を足せます）");
  }

  // ------------------------------------------------------------ 座標
  w(u, v) { const p = this.sk.plane; return [0, 1, 2].map((i) => p.origin[i] + u * p.x[i] + v * p.y[i]); }
  s(u, v) { return this.v.project(this.w(u, v)); }
  uv(sx, sy) {
    const p = this.sk.plane, r = this.v.ray(sx, sy), n = p.normal;
    const dn = r.d[0] * n[0] + r.d[1] * n[1] + r.d[2] * n[2];
    if (Math.abs(dn) < 1e-12) return null;
    const t = ((p.origin[0] - r.o[0]) * n[0] + (p.origin[1] - r.o[1]) * n[1] + (p.origin[2] - r.o[2]) * n[2]) / dn;
    const q = [0, 1, 2].map((i) => r.o[i] + t * r.d[i] - p.origin[i]);
    return [q[0] * p.x[0] + q[1] * p.x[1] + q[2] * p.x[2], q[0] * p.y[0] + q[1] * p.y[1] + q[2] * p.y[2]];
  }
  P(id) { const e = this.E[id]; return [e.x, e.y]; }

  _fitSketch() {
    const pts = this.sk.entities.filter((e) => e.type === "point").map((e) => [e.x, e.y]);
    for (const e of this.sk.entities) if (e.type === "circle") {
      const [cx, cy] = this.P(e.center); pts.push([cx - e.radius, cy - e.radius], [cx + e.radius, cy + e.radius]);
    }
    if (!pts.length) return;
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const c = this.w((Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2);
    const R = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys), 1) * 0.75;
    this.v.target = c;
    this.v.dist = Math.max(this.v.dist, R / Math.tan(this.v.fovy / 2));
    this.v.redraw();
  }

  // ------------------------------------------------------------ 形の折れ線（画面座標）
  _curve(e) {
    if (e.type === "line") return [this.s(...this.P(e.p1)), this.s(...this.P(e.p2))];
    const [cx, cy] = this.P(e.center);
    let a0 = 0, sweep = 2 * Math.PI, r = e.radius;
    if (e.type === "arc") {
      const [sx, sy] = this.P(e.start), [ex, ey] = this.P(e.end);
      r = Math.hypot(sx - cx, sy - cy);
      a0 = Math.atan2(sy - cy, sx - cx);
      sweep = ((Math.atan2(ey - cy, ex - cx) - a0) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) || 2 * Math.PI;
    }
    const n = Math.max(8, Math.ceil(64 * sweep / (2 * Math.PI))), out = [];
    for (let i = 0; i <= n; i++) { const a = a0 + sweep * i / n; out.push(this.s(cx + r * Math.cos(a), cy + r * Math.sin(a))); }
    return out;
  }

  // ------------------------------------------------------------ 描画
  draw() {
    const c = this.cv, ctx = this.ctx, dpr = window.devicePixelRatio || 1;
    const W = c.clientWidth, H = c.clientHeight;
    if (c.width !== Math.round(W * dpr) || c.height !== Math.round(H * dpr)) { c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    this.hits = [];
    if (!this.sk || !this.sk.plane) return;
    const st = this.sk.solve.entity_state;
    const color = (id) => this.sel.has(id) ? COLOR.sel : this.hover === id || this.relHi === id ? COLOR.hover : COLOR[st[id] || "under"];
    this._origin();
    this._drawExt();
    for (const e of this.sk.entities) {
      if (e.type === "point") continue;
      const pts = this._curve(e);
      if (pts.some((p) => !p)) continue;
      ctx.beginPath(); pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
      ctx.setLineDash(e.construction ? [7, 4] : []);
      ctx.lineWidth = this.sel.has(e.id) || this.hover === e.id ? 2.5 : 1.6;
      ctx.strokeStyle = color(e.id); ctx.stroke();
      this.hits.push({ id: e.id, kind: e.type, pts });
    }
    ctx.setLineDash([]);
    for (const e of this.sk.entities) {
      if (e.type !== "point") continue;
      const p = this.s(e.x, e.y); if (!p) continue;
      const r = this.sel.has(e.id) || this.hover === e.id || this.snap === e.id ? 4 : 2.6;
      ctx.fillStyle = color(e.id);
      ctx.fillRect(p[0] - r, p[1] - r, 2 * r, 2 * r);
      if (e.fixed) { ctx.strokeStyle = color(e.id); ctx.strokeRect(p[0] - 5, p[1] - 5, 10, 10); }
      this.hits.push({ id: e.id, kind: "point", p });
    }
    this._constraints();
    this._preview();
    this._patternPreview();
    if (this.box?.moved) {                       // 範囲選択の枠（右→左は破線・緑 = かかったものも選ぶ）
      const { a, b } = this.box, cross = b[0] < a[0];
      ctx.setLineDash(cross ? [5, 4] : []); ctx.lineWidth = 1;
      ctx.strokeStyle = cross ? "#18a058" : "#1f5fd6"; ctx.fillStyle = cross ? "rgba(24,160,88,.10)" : "rgba(31,95,214,.10)";
      ctx.fillRect(a[0], a[1], b[0] - a[0], b[1] - a[1]); ctx.strokeRect(a[0], a[1], b[0] - a[0], b[1] - a[1]);
      ctx.setLineDash([]);
    }
  }

  _origin() {
    const ctx = this.ctx, o = this.s(0, 0), px = this.v.pixelSize() * 28;
    const ax = this.s(px, 0), ay = this.s(0, px);
    if (!o || !ax || !ay) return;
    ctx.lineWidth = 1.5;
    for (const [p, col] of [[ax, "#d23c3c"], [ay, "#2f9a44"]]) {
      ctx.strokeStyle = col; ctx.beginPath(); ctx.moveTo(...o); ctx.lineTo(...p); ctx.stroke();
    }
    const id = this.originId || "ORIGIN", sel = this.sel.has(id) || this.pending.includes(id);
    const hot = this.snap === "ORIGIN" || this.hover === id;
    ctx.fillStyle = sel ? COLOR.sel : hot ? COLOR.hover : "#d23c3c";
    ctx.beginPath(); ctx.arc(o[0], o[1], sel || hot ? 5 : 3.5, 0, 2 * Math.PI); ctx.fill();
    if (!this.originId) this.hits.push({ id: "ORIGIN", kind: "point", p: o });      // 原点も選べる・寸法の相手にできる
  }

  _label(text, x, y, id, color) {
    const ctx = this.ctx;
    ctx.font = "12px Segoe UI, Yu Gothic UI, Meiryo, sans-serif";
    const w = ctx.measureText(text).width + 8, h = 17;
    const sel = this.sel.has(id), hov = this.hover === id;
    ctx.fillStyle = sel ? "#fff1dc" : hov ? "#e8f6ee" : "rgba(255,255,255,0.88)";
    ctx.strokeStyle = sel ? COLOR.sel : hov ? COLOR.hover : color;
    ctx.lineWidth = 1;
    ctx.fillRect(x - w / 2, y - h / 2, w, h); ctx.strokeRect(x - w / 2, y - h / 2, w, h);
    ctx.fillStyle = color; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(text, x, y + 0.5);
    if (id !== "__preview") this.hits.push({ id, kind: "label", box: [x - w / 2, y - h / 2, w, h], center: [x, y] });
  }

  _constraints() {
    const glyphCount = {};
    const conflicting = new Set([...this.sk.solve.conflicting, ...this.sk.solve.redundant]);
    for (const c of this.sk.constraints) {
      const color = conflicting.has(c.id) ? COLOR.over : COLOR.dim;
      if (c.value !== null && c.value !== undefined) {
        const g = this._dimShape(c.type, c.refs, c.label); if (!g) continue;
        this._drawDim(g, this._dimText(c.type, c.value, c.expr), c.id, this.sel.has(c.id) ? COLOR.sel : this.hover === c.id ? COLOR.hover : color);
      } else if (this.showGlyphs && GLYPH[c.type]) {
        const anchor = this._glyphAnchor(c); if (!anchor) continue;
        const k = `${Math.round(anchor[0] / 4)},${Math.round(anchor[1] / 4)}`;
        const n = glyphCount[k] = (glyphCount[k] || 0) + 1;
        this._label(GLYPH[c.type], anchor[0] + 14 * n, anchor[1] - 12, c.id, color);
      }
    }
  }

  _dimText(type, value, expr) {
    const v = Math.round(Math.abs(value) * 1000) / 1000;
    return (DIM_PREFIX[type] ?? "") + v + (type === "angle" ? "°" : "") + (expr ? " ƒ" : "");
  }

  _mid(id) {
    const e = this.E[id];
    if (!e) return null;
    if (e.type === "point") return this.s(e.x, e.y);
    if (e.type === "line") { const a = this.P(e.p1), b = this.P(e.p2); return this.s((a[0] + b[0]) / 2, (a[1] + b[1]) / 2); }
    const [cx, cy] = this.P(e.center); return this.s(cx + e.radius * 0.7071, cy + e.radius * 0.7071);
  }

  _glyphAnchor(c) {
    if (c.type === "symmetric") return this._mid(c.refs[1]);       // 写しの点のそば（軸に並べると重なる）
    return this._mid(c.refs[c.refs.length - 1]) || this._mid(c.refs[0]);
  }

  /** 寸法の形（スケッチ座標）。label は文字の位置の、既定の位置（anchor）からのずれ（null なら自動）。
   *  戻り値 { anchor, T（文字の位置）, segs: [[a, b, 破線?]], arrows: [[先端, 向き]] } */
  _dimShape(type, refs, label) {
    const E = this.E, px = this.v.pixelSize(), gap = DIM_GAP_PX * px;
    if (refs.some((r) => !E[r])) return null;
    const P = (id) => this.P(id);
    const lab = (anchor, def) => (label ? add2(anchor, label) : add2(anchor, def));
    if (["length", "distance", "hdistance", "vdistance"].includes(type)) {
      let A, B, dir = null; const segs = [];
      if (type === "length") { A = P(E[refs[0]].p1); B = P(E[refs[0]].p2); }
      else if (E[refs[1]].type === "line") {                         // 点と線: 垂線の足
        A = P(refs[0]); const L = E[refs[1]], q1 = P(L.p1), q2 = P(L.p2), d = sub2(q2, q1);
        const t = dot2(sub2(A, q1), d) / (dot2(d, d) || 1);
        B = add2(q1, mul2(d, t));
        if (t < 0 || t > 1) segs.push([t < 0 ? q1 : q2, B, true]);  // 線の延長
      } else { A = P(refs[0]); B = P(refs[1]); }
      if (type === "hdistance") dir = [1, 0];
      if (type === "vdistance") dir = [0, 1];
      const d = dir || unit2(sub2(B, A)), n = [-d[1], d[0]];
      const anchor = mul2(add2(A, B), 0.5), T = lab(anchor, mul2(n, gap));
      const h = dot2(T, n), A2 = add2(A, mul2(n, h - dot2(A, n))), B2 = add2(B, mul2(n, h - dot2(B, n)));
      const over = (P0, P1) => { const v = sub2(P1, P0), l = len2(v); return l > 1e-12 ? add2(P1, mul2(v, 4 * px / l)) : P1; };
      if (len2(sub2(A2, A)) > px) segs.push([A, over(A, A2)]);
      if (len2(sub2(B2, B)) > px) segs.push([B, over(B, B2)]);
      const ta = dot2(A2, d), tb = dot2(B2, d), tt = dot2(T, d);
      const lo = Math.min(ta, tb, tt), hi = Math.max(ta, tb, tt), at = (t) => add2(mul2(d, t), mul2(n, h));
      segs.push([at(lo), at(hi)]);
      const out = Math.sign(ta - tb) || 1;
      return { anchor, T, segs, arrows: [[A2, mul2(d, out)], [B2, mul2(d, -out)]] };
    }
    if (type === "radius" || type === "diameter") {
      const e = E[refs[0]], C = P(e.center), r = e.radius;
      const T = lab(C, mul2([0.7071, 0.7071], r + gap)), u = unit2(sub2(T, C), [0.7071, 0.7071]);
      const rim = add2(C, mul2(u, r)), far = len2(sub2(T, C)) > r ? T : rim;
      if (type === "radius") return { anchor: C, T, segs: [[C, far]], arrows: [[rim, u]] };
      const rim2 = add2(C, mul2(u, -r));
      return { anchor: C, T, segs: [[rim2, far]], arrows: [[rim, u], [rim2, mul2(u, -1)]] };
    }
    if (type === "offset") {                                           // 同心の 2 つの円・円弧の間隔
      const C = P(E[refs[0]].center), r1 = E[refs[0]].radius, r0 = E[refs[1]].radius, rm = Math.max(r0, r1);
      const T = lab(C, mul2([0.7071, -0.7071], rm + gap)), u = unit2(sub2(T, C), [0.7071, -0.7071]);
      const a = add2(C, mul2(u, r0)), b = add2(C, mul2(u, r1)), far = len2(sub2(T, C)) > rm ? T : add2(C, mul2(u, rm));
      const o = Math.sign(r1 - r0) || 1;
      return { anchor: C, T, segs: [[add2(C, mul2(u, Math.min(r0, r1))), far]], arrows: [[a, mul2(u, -o)], [b, mul2(u, o)]] };
    }
    if (type === "angle") {
      const a = E[refs[0]], b = E[refs[1]], a1 = P(a.p1), a2 = P(a.p2), b1 = P(b.p1), b2 = P(b.p2);
      const da = sub2(a2, a1), db = sub2(b2, b1), cr = da[0] * db[1] - da[1] * db[0];
      let V;
      if (Math.abs(cr) < 1e-12 * len2(da) * len2(db)) V = mul2(add2(add2(a1, a2), add2(b1, b2)), 0.25);
      else { const t = ((b1[0] - a1[0]) * db[1] - (b1[1] - a1[1]) * db[0]) / cr; V = add2(a1, mul2(da, t)); }
      const away = (p, q) => unit2(len2(sub2(p, V)) > len2(sub2(q, V)) ? sub2(p, V) : sub2(q, V));
      const u1 = away(a1, a2), u2 = away(b1, b2);
      const T = lab(V, mul2(unit2(add2(u1, u2), [-u1[1], u1[0]]), 2 * gap)), R = Math.max(len2(sub2(T, V)), px);
      const t1 = Math.atan2(u1[1], u1[0]);
      let dt = Math.atan2(u2[1], u2[0]) - t1; dt = ((dt + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI;
      const segs = [], N = 24, pt = (k) => add2(V, mul2([Math.cos(t1 + dt * k / N), Math.sin(t1 + dt * k / N)], R));
      for (let k = 0; k < N; k++) segs.push([pt(k), pt(k + 1)]);
      const sg = Math.sign(dt) || 1, tan = (t) => [-Math.sin(t) * sg, Math.cos(t) * sg];
      return { anchor: V, T, segs, arrows: [[pt(0), mul2(tan(t1), -1)], [pt(N), tan(t1 + dt)]] };
    }
    return null;
  }

  _drawDim(g, text, id, color) {
    const ctx = this.ctx, S = (p) => this.s(p[0], p[1]);
    ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 1;
    for (const [a, b, dashed] of g.segs) {
      const p = S(a), q = S(b); if (!p || !q) continue;
      ctx.setLineDash(dashed ? [4, 3] : []); ctx.beginPath(); ctx.moveTo(...p); ctx.lineTo(...q); ctx.stroke();
    }
    ctx.setLineDash([]);
    for (const [tip, dir] of g.arrows) {
      const p = S(tip), q = S(sub2(tip, dir)); if (!p || !q) continue;
      const u = unit2(sub2(p, q)), w = [-u[1], u[0]];
      const base = sub2(p, mul2(u, ARROW_PX));
      ctx.beginPath(); ctx.moveTo(...p); ctx.lineTo(...add2(base, mul2(w, ARROW_PX / 3))); ctx.lineTo(...sub2(base, mul2(w, ARROW_PX / 3)));
      ctx.closePath(); ctx.fill();
    }
    const T = S(g.T);
    if (T) this._label(text, T[0], T[1], id, color);
  }

  /** スマート寸法: 選んだ要素（1〜2 個）と置く位置 T から、寸法の種類・参照・API に渡す type を決める（一般的な 3D CAD と同じく、
   *  線・2 点は置いた位置で 水平 / 垂直 / 平行 が変わる）。 */
  _dimPlan(ids, T) {
    const E = this.E, k = ids.map((id) => E[id]?.type);
    if (k.some((x) => !x)) return null;
    const ctr = (id) => (E[id].type === "point" ? id : E[id].center);
    const orient = (pa, pb) => {
      if (!T) return null;
      const A = this.P(pa), B = this.P(pb), L = len2(sub2(B, A));
      if (L < 1e-12 || Math.abs(B[0] - A[0]) < 1e-6 * L || Math.abs(B[1] - A[1]) < 1e-6 * L) return null;
      const inX = T[0] > Math.min(A[0], B[0]) && T[0] < Math.max(A[0], B[0]);
      const inY = T[1] > Math.min(A[1], B[1]) && T[1] < Math.max(A[1], B[1]);
      return inX && !inY ? "hdistance" : inY && !inX ? "vdistance" : null;
    };
    if (ids.length === 1) {
      if (k[0] === "line") {
        const L = E[ids[0]], o = orient(L.p1, L.p2);
        return o ? { type: o, refs: [L.p1, L.p2], send: { type: o } } : { type: "length", refs: ids, send: {} };
      }
      if (k[0] === "circle") return { type: "diameter", refs: ids, send: {} };
      if (k[0] === "arc") return { type: "radius", refs: ids, send: {} };
      return null;
    }
    const [a, b] = ids;
    if (a === b) return null;
    if (k[0] === "line" && k[1] === "line") {
      const A = E[a], B = E[b], da = sub2(this.P(A.p2), this.P(A.p1)), db = sub2(this.P(B.p2), this.P(B.p1));
      if (Math.abs(da[0] * db[1] - da[1] * db[0]) < 1e-9 * len2(da) * len2(db))        // 平行な線: 距離
        return { type: "distance", refs: [A.p1, b], send: { type: "distance" } };
      return { type: "angle", refs: ids, send: {} };
    }
    if (k.includes("line")) {
      const [o, l] = k[0] === "line" ? [b, a] : [a, b];
      return { type: "distance", refs: [ctr(o), l], send: {} };
    }
    const pa = ctr(a), pb = ctr(b);
    if (pa === pb) return null;
    const o = orient(pa, pb);
    return o ? { type: o, refs: [pa, pb], send: { type: o } } : { type: "distance", refs: [pa, pb], send: {} };
  }

  /** 置こうとしている寸法の見た目（文字はカーソルの位置）と、その label。 */
  _dimPreview(ids, T) {
    const plan = this._dimPlan(ids, T); if (!plan) return null;
    const g0 = this._dimShape(plan.type, plan.refs, null); if (!g0) return null;
    const label = sub2(T, g0.anchor);
    return { plan, label, g: this._dimShape(plan.type, plan.refs, label) };
  }

  _measure(type, refs) {
    const E = this.E, P = (id) => this.P(id);
    if (type === "length") return len2(sub2(P(E[refs[0]].p2), P(E[refs[0]].p1)));
    if (type === "hdistance") return Math.abs(P(refs[1])[0] - P(refs[0])[0]);
    if (type === "vdistance") return Math.abs(P(refs[1])[1] - P(refs[0])[1]);
    if (type === "radius") return E[refs[0]].radius;
    if (type === "diameter") return 2 * E[refs[0]].radius;
    if (type === "distance") {
      if (E[refs[1]].type !== "line") return len2(sub2(P(refs[1]), P(refs[0])));
      const L = E[refs[1]], q1 = P(L.p1), d = sub2(P(L.p2), q1), A = P(refs[0]);
      return Math.abs(d[0] * (A[1] - q1[1]) - d[1] * (A[0] - q1[0])) / (len2(d) || 1);
    }
    if (type === "angle") {
      const a = E[refs[0]], b = E[refs[1]], u = sub2(P(a.p2), P(a.p1)), w = sub2(P(b.p2), P(b.p1));
      return Math.atan2(u[0] * w[1] - u[1] * w[0], dot2(u, w)) * 180 / Math.PI;
    }
    return 0;
  }

  _preview() {
    if (this.tool === "dim" && this.pending.length && this.cursorUV && !(this.hover && this.E[this.hover] && !this.pending.includes(this.hover))) {
      const pv = this._dimPreview(this.pending, this.cursorUV);
      if (pv) this._drawDim(pv.g, this._dimText(pv.plan.type, this._measure(pv.plan.type, pv.plan.refs), null), "__preview", COLOR.preview);
    }
    if (!this.cursor || !(this.tool in DRAW_NEED)) return;
    const ctx = this.ctx, q = this._pt(this.cursor);
    ctx.strokeStyle = COLOR.preview; ctx.lineWidth = 1.2; ctx.setLineDash([5, 4]);
    const pts = this.pending.map((p) => this._pt(p));
    const S = (p) => this.s(...p);
    ctx.beginPath();
    const path = (ps) => ps.forEach((p, i) => { const s = S(p); if (s) (i ? ctx.lineTo(...s) : ctx.moveTo(...s)); });
    const arcPts = (c, r, a0, sweep) => Array.from({ length: 33 }, (_, i) => [c[0] + r * Math.cos(a0 + sweep * i / 32), c[1] + r * Math.sin(a0 + sweep * i / 32)]);
    if (["line", "cline", "tarc"].includes(this.tool) && pts.length === 1) path([pts[0], q]);
    if ((this.tool === "arc3" || this.tool === "slot") && pts.length === 1) path([pts[0], q]);
    if (this.tool === "arc3" && pts.length === 2) {
      const c = circum(pts[0], pts[1], q);
      if (c) {
        const ang = (p) => Math.atan2(p[1] - c[1], p[0] - c[0]), a0 = ang(pts[0]), TAU = 2 * Math.PI;
        const ccw = (ang(pts[1]) - a0 + 2 * TAU) % TAU, mid = (ang(q) - a0 + 2 * TAU) % TAU;
        path(arcPts(c, Math.hypot(pts[0][0] - c[0], pts[0][1] - c[1]), a0, mid <= ccw ? ccw : ccw - TAU));
      } else path([pts[0], pts[1]]);
    }
    if (this.tool === "slot" && pts.length === 2) {
      const d = sub2(pts[1], pts[0]), L = len2(d);
      if (L > 1e-9) {
        const u = mul2(d, 1 / L), n = [-u[1], u[0]], r = Math.abs(dot2(sub2(q, pts[0]), n)), a = Math.atan2(n[1], n[0]);
        path([...arcPts(pts[1], r, a - Math.PI, Math.PI), ...arcPts(pts[0], r, a, Math.PI), add2(pts[1], mul2(n, -r))]);
      }
    }
    if (this.tool === "polygon" && pts.length === 1) {
      const n = this.opt.polySides, r = len2(sub2(q, pts[0])), a0 = Math.atan2(q[1] - pts[0][1], q[0] - pts[0][0]);
      path(Array.from({ length: n + 1 }, (_, i) => [pts[0][0] + r * Math.cos(a0 + 2 * Math.PI * i / n), pts[0][1] + r * Math.sin(a0 + 2 * Math.PI * i / n)]));
    }
    if ((this.tool === "rect" || this.tool === "crect") && pts.length === 1) {
      const [a, b] = [this.tool === "crect" ? sub2(mul2(pts[0], 2), q) : pts[0], q];
      [[a[0], a[1]], [b[0], a[1]], [b[0], b[1]], [a[0], b[1]], [a[0], a[1]]].forEach((p, i) => (i ? ctx.lineTo(...S(p)) : ctx.moveTo(...S(p))));
    }
    if ((this.tool === "circle" && pts.length === 1) || (this.tool === "arc" && pts.length >= 1)) {
      const r = Math.hypot((pts[1] || q)[0] - pts[0][0], (pts[1] || q)[1] - pts[0][1]);
      for (let i = 0; i <= 64; i++) {
        const a = i / 64 * 2 * Math.PI, p = S([pts[0][0] + r * Math.cos(a), pts[0][1] + r * Math.sin(a)]);
        i ? ctx.lineTo(...p) : ctx.moveTo(...p);
      }
    }
    ctx.stroke(); ctx.setLineDash([]);
    if (this.hint) {
      const p = S(q); ctx.fillStyle = COLOR.hover; ctx.font = "bold 11px sans-serif"; ctx.fillText(this.hint, p[0] + 12, p[1] + 14);
    }
  }

  /** スケッチの外のスナップ先（D52・D54）を描き、当たり判定に入れる。
   *  - ほかのスケッチ（表示中のもの）: 灰色の破線。点・線の中点・線
   *  - モデル（this.modelGeo、app.js がメッシュから作る）: エッジの端点・中点・円 / 円弧の中心・直線エッジ。
   *    印はカーソルの近くだけ出す（一般的な 3D CAD と同じ）。 */
  _drawExt() {
    const ctx = this.ctx, cur = this.cursorS;
    const near = (q, r = 45) => cur && Math.hypot(q[0] - cur[0], q[1] - cur[1]) < r;
    this.extHits = [];
    const mark = (h, kind) => {                      // スナップ先の印: 端点 = 四角、中点 = 三角、中心 = 円 + 十字
      const on = this.extSnap === h.src, q = h.p;
      if (!on && !near(q)) return;
      ctx.strokeStyle = ctx.fillStyle = on ? COLOR.hover : "#8794a6"; ctx.lineWidth = on ? 2 : 1.2;
      ctx.beginPath();
      if (kind === "center") { ctx.arc(q[0], q[1], 5, 0, 2 * Math.PI); ctx.moveTo(q[0] - 7, q[1]); ctx.lineTo(q[0] + 7, q[1]); ctx.moveTo(q[0], q[1] - 7); ctx.lineTo(q[0], q[1] + 7); }
      else if (kind === "mid") { ctx.moveTo(q[0], q[1] - 5); ctx.lineTo(q[0] + 5, q[1] + 4); ctx.lineTo(q[0] - 5, q[1] + 4); ctx.closePath(); }
      else ctx.rect(q[0] - 4, q[1] - 4, 8, 8);
      ctx.stroke();
    };
    for (const s of this.ext || []) {
      for (const c of s.curves) {
        const P = c.pts.map((p) => this.v.project(p));
        if (P.some((p) => !p)) continue;
        const src = `${s.id}:${c.id}`, on = this.extSnap === src;
        ctx.strokeStyle = on ? COLOR.hover : "#8794a6"; ctx.lineWidth = on ? 2.2 : 1.2;
        ctx.setLineDash(c.construction ? [7, 4] : [3, 3]);
        ctx.beginPath(); P.forEach((p, i) => (i ? ctx.lineTo(...p) : ctx.moveTo(...p))); ctx.stroke();
        this.extHits.push({ src, name: s.name, label: `線上（${s.name}）`, type: c.type, pts: P, w: c.pts });
        if (c.type === "line") {
          const w = [0, 1, 2].map((i) => (c.pts[0][i] + c.pts[1][i]) / 2), q = this.v.project(w);
          if (q) this.extHits.push({ src: `${src}@mid`, name: s.name, label: `中点（${s.name}）`, type: "point", kind: "mid", p: q, w });
        }
      }
      ctx.setLineDash([]);
      for (const p of s.points) {
        const q = this.v.project(p.p); if (!q) continue;
        const src = `${s.id}:${p.id}`, r = this.extSnap === src ? 4 : 2;
        ctx.fillStyle = this.extSnap === src ? COLOR.hover : "#8794a6"; ctx.fillRect(q[0] - r, q[1] - r, 2 * r, 2 * r);
        this.extHits.push({ src, name: s.name, label: `点（${s.name}）`, type: "point", p: q, w: p.p });
      }
    }
    for (const h of this.extHits) if (h.kind === "mid") mark(h, "mid");
    const LBL = { vertex: "端点", mid: "中点", center: "中心" };
    const G = this.modelGeoFn?.();
    for (const m of G?.snaps || []) {
      const q = this.v.project(m.w); if (!q) continue;
      const h = { src: m.src, name: "モデル", label: `${LBL[m.kind]}（モデル）`, type: "point", kind: m.kind, p: q, w: m.w, model: true };
      this.extHits.push(h); mark(h, m.kind);
    }
    for (const e of G?.edges || []) {
      const P = e.pts.map((p) => this.v.project(p));
      if (P.some((p) => !p)) continue;
      const src = `edge:${e.name}`;
      this.extHits.push({ src, name: "モデル", label: "エッジ上（モデル）", type: e.straight ? "line" : "curve", pts: P, w: [e.pts[0], e.pts[e.pts.length - 1]], model: true });
      if (this.extSnap === src) {
        ctx.strokeStyle = COLOR.hover; ctx.lineWidth = 2.5;
        ctx.beginPath(); P.forEach((p, i) => (i ? ctx.lineTo(...p) : ctx.moveTo(...p))); ctx.stroke();
      }
    }
    // 推測線（一般的な 3D CAD の点線）: 揃えた相手の点からカーソルまで
    for (const f of this.infer || []) {
      const a = this.s(...f.from), b = this.s(...f.to); if (!a || !b) continue;
      ctx.strokeStyle = "#2f7fd8"; ctx.lineWidth = 1; ctx.setLineDash([2, 3]);
      ctx.beginPath(); ctx.moveTo(...a); ctx.lineTo(...b); ctx.stroke(); ctx.setLineDash([]);
    }
  }

  /** ワールド座標 → このスケッチの座標。 */
  _uvOf(w) {
    const p = this.sk.plane, q = [0, 1, 2].map((i) => w[i] - p.origin[i]);
    return [q[0] * p.x[0] + q[1] * p.x[1] + q[2] * p.x[2], q[0] * p.y[0] + q[1] * p.y[1] + q[2] * p.y[2]];
  }

  _extHit(sx, sy, points, { curves = false } = {}) {
    let best = null, bd = points ? SNAP_PX : HIT_PX;
    for (const h of this.extHits || []) {
      if (points !== (h.type === "point")) continue;
      if (!points && h.type === "curve" && !curves) continue;      // モデルの曲線エッジは寸法の相手だけ
      if (points) { const d = Math.hypot(h.p[0] - sx, h.p[1] - sy) + (h.kind ? 0.5 : 0); if (d < bd) { bd = d; best = h; } continue; }
      for (let i = 1; i < h.pts.length; i++) { const d = segDist([sx, sy], h.pts[i - 1], h.pts[i]); if (d < bd) { bd = d; best = h; } }
    }
    return best;
  }

  // ------------------------------------------------------------ 当たり判定
  _hit(sx, sy, { pointsOnly = false, noLabels = false, noPoints = false } = {}) {
    let best = null, bd = Infinity;
    for (const h of this.hits) {
      if (h.kind === "point") {
        if (noPoints) continue;
        const d = Math.hypot(h.p[0] - sx, h.p[1] - sy);
        if (d < SNAP_PX && d - 100 < bd) { best = h; bd = d - 100; }        // 点を優先
      } else if (!pointsOnly && !noLabels && h.kind === "label") {
        const [x, y, w, hh] = h.box;
        if (sx >= x && sx <= x + w && sy >= y && sy <= y + hh && -50 < bd) { best = h; bd = -50; }
      } else if (!pointsOnly && h.pts) {
        for (let i = 1; i < h.pts.length; i++) {
          const d = segDist([sx, sy], h.pts[i - 1], h.pts[i]);
          if (d < HIT_PX && d < bd) { best = h; bd = d; }
        }
      }
    }
    return best;
  }

  /** カーソル位置 → 確定に使う点（既存の点 ID / "ORIGIN" / [u, v]）。水平・垂直へのスナップもここで。 */
  _resolve(sx, sy) {
    this.hint = null; this.extSnap = null; this.infer = []; this.rel = null; this.relHi = null;
    const h = this._hit(sx, sy, { pointsOnly: true });
    if (h) return h.id;
    const o = this.s(0, 0);
    if (o && Math.hypot(o[0] - sx, o[1] - sy) < SNAP_PX) return "ORIGIN";
    const xp = this._extHit(sx, sy, true);                  // ほかのスケッチの点（一致の外部参照）
    if (xp) {
      const [u, v] = this._uvOf(xp.w);
      this.hint = xp.label; this.extSnap = xp.src;
      return { ext: xp.src, x: u, y: v };
    }
    let q = this.uv(sx, sy);
    if (!q) return null;
    const last = this.pending[this.pending.length - 1];
    let hv = null;
    if (last && (["line", "cline"].includes(this.tool) || (this.tool === "slot" && this.pending.length === 1))) {
      const a = this._pt(last), dx = q[0] - a[0], dy = q[1] - a[1], ang = Math.abs(Math.atan2(dy, dx) * 180 / Math.PI);
      if (Math.min(ang, 180 - ang) < 3) { q = [q[0], a[1]]; this.hint = hv = "H"; }
      else if (Math.abs(ang - 90) < 3) { q = [a[0], q[1]]; this.hint = hv = "V"; }
    }
    let ray = null;                                         // 直角・正接・平行に吸い付いた線の向き（線上へのスナップで保つ）
    if (last && !hv && ["line", "cline"].includes(this.tool)) {
      const r = this._relInfer(last, q);
      if (r) { q = r.q; ray = [this._pt(last), r.u]; this.hint = r.glyph; this.rel = [r.type, r.id]; this.relHi = r.id; }
    }
    // 線の中点・線上・円上へのスナップ（確定すると 中点 / 一致 の拘束が付く。一般的な 3D CAD の自動拘束）
    const g = this._hit(sx, sy), e = g && this.E[g.id];
    if (e && e.type !== "point") {
      if (e.type === "line") {
        const A = this.P(e.p1), B = this.P(e.p2), M = mul2(add2(A, B), 0.5), ms = this.s(...M);
        if (ms && Math.hypot(ms[0] - sx, ms[1] - sy) < SNAP_PX) { this.hint = "中点"; return { mid: e.id, x: M[0], y: M[1] }; }
        const d = sub2(B, A);
        let t = dot2(sub2(q, A), d) / (dot2(d, d) || 1);
        if (hv === "H" && Math.abs(d[1]) > 1e-9) t = (q[1] - A[1]) / d[1];        // 水平・垂直を保ったまま線に乗せる
        if (hv === "V" && Math.abs(d[0]) > 1e-9) t = (q[0] - A[0]) / d[0];
        if (ray) { const t2 = rayHit(ray, A, d); if (t2 != null) t = t2; }         // 直角・正接・平行も保つ
        const p = add2(A, mul2(d, t));
        this.hint = (hv ? hv + " " : this.rel ? this.hint + " " : "") + "線上";
        return { on: e.id, x: p[0], y: p[1] };
      }
      const C = this.P(e.center), u = unit2(sub2(q, C)), p = add2(C, mul2(u, e.radius));
      this.hint = "円上"; this.rel = null; this.relHi = null;
      return { on: e.id, x: p[0], y: p[1] };
    }
    const xl = this._extHit(sx, sy, false);                 // ほかのスケッチの線の上
    if (xl && xl.type === "line") {
      const A = this._uvOf(xl.w[0]), B = this._uvOf(xl.w[1]), d = sub2(B, A);
      if (dot2(d, d) > 1e-12) {
        let t = dot2(sub2(q, A), d) / dot2(d, d);
        if (hv === "H" && Math.abs(d[1]) > 1e-9) t = (q[1] - A[1]) / d[1];
        if (hv === "V" && Math.abs(d[0]) > 1e-9) t = (q[0] - A[0]) / d[0];
        const p = add2(A, mul2(d, t));
        this.hint = (hv ? hv + " " : "") + xl.label; this.extSnap = xl.src;
        return { ext: xl.src, x: p[0], y: p[1] };
      }
    }
    if (!hv && !this.rel && this.tool in DRAW_NEED) q = this._infer(q, sx, sy);
    return q;
  }

  /** 推測線: カーソルが既存の点（自分・ほかのスケッチ・モデル）と縦・横に揃ったら、その座標に合わせる（拘束は付けない）。 */
  _infer(q, sx, sy) {
    const cands = [];
    for (const h of this.hits) if (h.kind === "point" && !this.pending.includes(h.id)) cands.push([h.p, this.P(h.id)]);
    for (const h of this.extHits || []) if (h.type === "point") cands.push([h.p, this._uvOf(h.w)]);
    let bx = 6, by = 6, ax = null, ay = null;
    for (const [s, u] of cands) {
      if (!u) continue;
      const dx = Math.abs(s[0] - sx), dy = Math.abs(s[1] - sy);
      if (dx < bx && dy > 2) { bx = dx; ax = u; }
      if (dy < by && dx > 2) { by = dy; ay = u; }
    }
    // 画面の縦・横がスケッチの u・v とずれている（正対していない）ときは使わない
    const o = this.s(0, 0), ux = this.s(1, 0);
    if (!o || !ux || Math.abs(ux[1] - o[1]) > 1e-3 * Math.abs(ux[0] - o[0])) return q;
    const out = [q[0], q[1]];
    if (ax) { out[0] = ax[0]; this.infer.push({ from: ax, to: null }); }
    if (ay) { out[1] = ay[1]; this.infer.push({ from: ay, to: null }); }
    for (const f of this.infer) f.to = out;
    if (ax || ay) this.hint = this.hint || "推測線";
    return out;
  }

  /** 線の作図中の推測（一般的な 3D CAD の自動拘束）: 始点につながった線に直角・円弧に正接、ほかの斜めの線に平行。
   *  3° 以内なら向きを合わせ、確定するとその拘束が付く。 */
  _relInfer(last, q) {
    const a = this._pt(last), d = sub2(q, a), L = len2(d);
    if (!a || L < 1e-9) return null;
    const cands = [];
    const id = typeof last === "string" && last !== "ORIGIN" ? last : null;
    for (const e of this.sk.entities) {
      if (e.virtual) continue;
      if (e.type === "line" && id && (e.p1 === id || e.p2 === id)) {
        const v = unit2(sub2(this.P(e.p2), this.P(e.p1)));
        cands.push({ type: "perpendicular", id: e.id, u: [-v[1], v[0]], glyph: "⊥" });
      } else if (e.type === "arc" && id && (e.start === id || e.end === id)) {
        const r = unit2(sub2(a, this.P(e.center)));
        cands.push({ type: "tangent", id: e.id, u: [-r[1], r[0]], glyph: "T" });
      } else if (e.type === "line") {
        const v = unit2(sub2(this.P(e.p2), this.P(e.p1)));
        if (Math.abs(v[0]) > 1e-6 && Math.abs(v[1]) > 1e-6) cands.push({ type: "parallel", id: e.id, u: v, glyph: "∥" });  // 水平・垂直の線は H / V に任せる
      }
    }
    let best = null, bang = 3;
    for (const c of cands) {
      const ang = Math.acos(Math.min(1, Math.abs(dot2(d, c.u)) / L)) * 180 / Math.PI;
      if (ang < bang - (c.type === "parallel" ? 0.5 : 0)) { best = c; bang = ang; }      // つながった相手を少し優先
    }
    if (!best) return null;
    return { ...best, q: add2(a, mul2(best.u, dot2(d, best.u))) };
  }

  _pt(p) { return p === "ORIGIN" ? [0, 0] : typeof p === "string" ? this.P(p) : Array.isArray(p) ? p : [p.x, p.y]; }

  // ------------------------------------------------------------ 入力（viewer.interceptor）
  down(e, [sx, sy]) {
    if (!this.sk?.plane) return false;
    if (this.patternMode?.pick) {                      // 円形パターンの中心を選ぶ
      const h = this._hit(sx, sy, { pointsOnly: true }), o = this.s(0, 0);
      const id = h ? h.id : o && Math.hypot(o[0] - sx, o[1] - sy) < SNAP_PX ? "ORIGIN" : null;
      if (!id) { this.toast("中心にする点をクリックしてください", true); return true; }
      this.patternMode.center = id === this.originId ? "ORIGIN" : id; this.patternMode.pick = false;
      this._selChanged(); return true;
    }
    if (this.tool === "select") {
      const h = this._hit(sx, sy);
      if (!h) {                                 // 何もない所: 離したときにクリック（モデルの選択）か範囲選択かを決める
        this.box = { a: [sx, sy], b: [sx, sy], ctrl: e.ctrlKey, moved: false };
        return true;
      }
      if (e.ctrlKey) { this.sel.has(h.id) ? this.sel.delete(h.id) : this.sel.add(h.id); }
      else if (!this.sel.has(h.id)) { this.sel.clear(); this.sel.add(h.id); }
      this._selChanged();
      if (e.ctrlKey) return true;
      const q = this.uv(sx, sy);
      if (h.kind === "label") {
        const c = this.sk.constraints.find((x) => x.id === h.id);
        if (c?.value != null) {                                             // 寸法の文字: ドラッグで移動
          const g = this._dimShape(c.type, c.refs, c.label);
          if (g && q) this.labelDrag = { c, grab: q, start: c.label || sub2(g.T, g.anchor), moved: false, orig: c.label };
        }
      } else if (h.kind === "point") {
        if (!this.E[h.id].fixed) this.drag = { point: h.id, busy: false, next: null, moved: false };
      } else if (q) {                                                       // 線は平行移動、円・円弧は大きさ
        this.drag = { entity: h.id, grab: q, busy: false, next: null, moved: false };
      }
      return true;
    }
    if (this.tool === "dim") { this._dimClick(sx, sy); return true; }
    if (this.tool === "extend") {
      const h = this._hit(sx, sy, { noLabels: true, noPoints: true }), q = this.uv(sx, sy);
      if (h && this.E[h.id]?.type === "line" && q) this._send("sketch.extend", { id: h.id, x: q[0], y: q[1] });
      else this.toast("延長する線の、伸ばしたい端の近くをクリックしてください", true);
      return true;
    }
    if (this.tool === "fillet" || this.tool === "chamfer") { this._cornerClick(sx, sy); return true; }
    if (this.tool === "offset") { this._offsetClick(sx, sy); return true; }
    if (this.tool === "trim") {
      const h = this._hit(sx, sy, { noLabels: true, noPoints: true });
      if (h && h.kind !== "label" && h.kind !== "point") {
        const q = this.uv(sx, sy); this._send("sketch.trim", { id: h.id, x: q[0], y: q[1] });
      }
      return true;
    }
    const p = this._resolve(sx, sy);
    if (p === null) return true;
    if (this.tool === "tarc" && !this.pending.length && (typeof p !== "string" || p === "ORIGIN")) {
      this.toast("正接円弧は、線か円弧の端点から描き始めます", true); return true;
    }
    this.pending.push(p);
    this.pendRel = this.rel;
    this._advance();
    return true;
  }

  /** フィレット・面取り: 角の点をクリック、または線を 2 本クリック。 */
  async _cornerClick(sx, sy) {
    const fil = this.tool === "fillet";
    const method = fil ? "sketch.fillet" : "sketch.chamfer";
    const val = fil ? { radius: this.opt.filletR } : { distance: this.opt.chamferD };
    const hp = this._hit(sx, sy, { pointsOnly: true });
    if (hp) { this.pending = []; await this._send(method, { point: hp.id, ...val }); return; }
    const h = this._hit(sx, sy, { noLabels: true });
    if (!h || this.E[h.id]?.type !== "line") { this.toast("角の点か、角をつくる線をクリックしてください", true); return; }
    if (this.pending.includes(h.id)) return;
    this.pending.push(h.id); this.sel.clear(); this.pending.forEach((id) => this.sel.add(id)); this.v.redraw();
    if (this.pending.length < 2) return;
    const [l1, l2] = this.pending; this.pending = []; this.sel.clear();
    await this._send(method, { l1, l2, ...val });
  }

  /** オフセット: 要素をクリックして選ぶ（先に範囲選択してあってもよい）→ オフセットする側の何もない所をクリック。 */
  async _offsetClick(sx, sy) {
    const h = this._hit(sx, sy, { noLabels: true, noPoints: true });
    if (h && h.kind !== "label" && h.kind !== "point") {
      this.sel.has(h.id) ? this.sel.delete(h.id) : this.sel.add(h.id);
      this._selChanged(); return;
    }
    const ids = [...this.sel].filter((id) => this.E[id] && this.E[id].type !== "point"), q = this.uv(sx, sy);
    if (!ids.length || !q) { this.toast("オフセットする線・円・円弧を先にクリックしてください", true); return; }
    const st = await this._send("sketch.offset", { ids, distance: this.opt.offsetD, x: q[0], y: q[1] });
    if (st?.created) { this.sel.clear(); this._selChanged(); }
  }

  move(e, [sx, sy]) {
    if (!this.sk?.plane) return;
    const q = this.uv(sx, sy);
    this.cursorS = [sx, sy];
    if (this.labelDrag) {
      if (!q) return;
      const L = this.labelDrag;
      L.moved = true;
      L.c.label = add2(L.start, sub2(q, L.grab));
      this.v.redraw();
      return;
    }
    if (this.box) {
      this.box.b = [sx, sy];
      if (Math.hypot(sx - this.box.a[0], sy - this.box.a[1]) > 4) this.box.moved = true;
      this.v.redraw();
      return;
    }
    if (this.drag) {
      if (!q) return;
      this.drag.moved = true;
      this.drag.next = q;
      this._pumpDrag();
      return;
    }
    const r = this._resolve(sx, sy);
    this.snap = typeof r === "string" ? r : null;
    this.cursor = r;
    this.cursorUV = q;
    const pickOnly = !["select", "dim"].includes(this.tool);
    const h = PICK_TOOLS.includes(this.tool)
      ? this._hit(sx, sy, { noLabels: pickOnly, noPoints: ["trim", "extend", "offset"].includes(this.tool) }) : null;
    this.hover = h ? h.id : null;
    if (this.tool === "dim") {                      // スケッチの外の相手も光らせる
      const x = h ? null : this._dimExt(sx, sy);
      this.extSnap = x?.src || null; this.hint = x?.label || null;
    }
    this.model?.hover(this.tool === "select" && !h ? [sx, sy] : null);
    this.v.redraw();
  }

  /** 範囲選択（一般的な 3D CAD と同じ）: 左→右は枠に完全に入ったもの、右→左は枠にかかったものも。 */
  _boxSelect(B) {
    const x0 = Math.min(B.a[0], B.b[0]), x1 = Math.max(B.a[0], B.b[0]), y0 = Math.min(B.a[1], B.b[1]), y1 = Math.max(B.a[1], B.b[1]);
    const inside = (p) => p[0] >= x0 && p[0] <= x1 && p[1] >= y0 && p[1] <= y1;
    const crossing = B.b[0] < B.a[0];
    const corners = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
    const cuts = (p, q) => corners.some((c, i) => segCross(p, q, c, corners[(i + 1) % 4]));
    if (!B.ctrl) this.sel.clear();
    for (const h of this.hits) {
      let ok;
      if (h.id === "ORIGIN" || h.id === this.originId) continue;          // 原点は範囲選択では選ばない（一般的な 3D CAD と同じ）
      if (h.kind === "point") ok = inside(h.p);
      else if (h.kind === "label") ok = inside(h.center) && this.sk.constraints.find((c) => c.id === h.id)?.value != null;
      else ok = h.pts.every(inside) || (crossing && (h.pts.some(inside) || h.pts.some((p, i) => i && cuts(h.pts[i - 1], p))));
      if (ok) this.sel.add(h.id);
    }
    this._selChanged();
  }

  async up() {
    const B = this.box; this.box = null;
    if (B) {
      if (B.moved) this._boxSelect(B);
      else if (this.model?.pick(B.a, B.ctrl)) { if (!B.ctrl) { this.sel.clear(); this._selChanged(); } }   // モデルのエッジ・面
      else { if (!B.ctrl) { this.sel.clear(); this.model?.clear(); } this._selChanged(); }
      this.v.redraw();
      return;
    }
    const L = this.labelDrag; this.labelDrag = null;
    if (L) {
      if (L.moved) {
        const label = L.c.label; L.c.label = L.orig;
        await this._send("sketch.dim_label", { id: L.c.id, label });
      }
      return;
    }
    const d = this.drag; this.drag = null;
    if (!d || !d.moved) return;
    while (d.busy) await new Promise((r) => setTimeout(r, 5));
    const q = d.next || d.last;
    if (!q) return;
    await this._send("sketch.drag", { ...this._dragTarget(d), x: q[0], y: q[1], final: true });
  }

  dblclick(e, [sx, sy] = []) {
    if (["line", "cline", "tarc"].includes(this.tool)) { this.pending = []; return; }
    if (this.tool !== "select") return;
    const h = this._hit(sx, sy);
    if (h?.kind === "label" && this.sk.constraints.find((c) => c.id === h.id)?.value != null) this.editDimension(h.id);
  }

  _dragTarget(d) { return d.point ? { point: d.point } : { entity: d.entity, grab: d.grab }; }

  async _pumpDrag() {
    const d = this.drag;
    if (!d || d.busy || !d.next) return;
    d.busy = true;
    const q = d.next; d.next = null; d.last = q;
    try {
      const r = await this.rpc("sketch.drag", { ...this._dragTarget(d), x: q[0], y: q[1] });
      for (const e of r.entities) Object.assign(this.E[e.id] || {}, e);
      this.v.redraw();
    } catch (err) { this.toast(err.message, true); }
    d.busy = false;
    if (d.next && this.drag === d) this._pumpDrag();
  }

  async _advance() {
    const P = this.pending, t = this.tool, need = DRAW_NEED[t];
    this.v.redraw();
    if (P.length < need) return;
    const pts = P.map((p) => p);
    if (t === "line" || t === "cline") {
      if (pts[0] === pts[1]) { this.pending = [pts[0]]; return; }
      const rel = this.pendRel; this.pendRel = null;
      const st = await this._send("sketch.add", { kind: "line", points: pts, construction: t === "cline", ...(rel ? { relations: [rel] } : {}) });
      const line = st?.created?.[0] && st.sketch.entities.find((e) => e.id === st.created[0]);
      this.pending = line ? [line.p2] : [];                       // 続けて次の線を引く（Esc / ダブルクリックで終了）
      if (typeof pts[1] === "string" && pts[1] !== "ORIGIN" && line) this.pending = [];   // 既存の点で閉じたら終了
      return;
    }
    if (t === "tarc") {
      const st = await this._send("sketch.add", { kind: "tarc", points: pts });
      const arc = st?.created?.[0] && st.sketch.entities.find((e) => e.id === st.created[0]);
      this.pending = arc && typeof pts[1] !== "string" ? [arc.start === pts[0] ? arc.end : arc.start] : [];   // 続けて描ける
      return;
    }
    this.pending = [];
    await this._send("sketch.add", { kind: t, points: pts, ...(t === "polygon" ? { sides: this.opt.polySides } : {}) });
  }

  /** スマート寸法（一般的な 3D CAD と同じ流れ）: 要素をクリック（2 つまで）→ 寸法がカーソルについてくる → 何もない所をクリックして置く
   *  → 値の入力。線・2 点は置く位置で 水平 / 垂直 / 平行 が変わる。円は直径、円弧は半径。 */
  async _dimClick(sx, sy) {
    const h = this._hit(sx, sy);
    let ent = h && h.kind !== "label" ? h.id : null;
    const x = !ent && this.pending.length < 2 ? this._dimExt(sx, sy) : null;
    if (x) {                                   // スケッチの外（ほかのスケッチ・モデル）: 参照の要素を作って相手にする（D54）
      const keep = this.pending, q = this.uv(sx, sy);
      const st = await this._send("sketch.reference", { source: x.src, ...(q ? { x: q[0], y: q[1] } : {}) });
      this.pending = keep;
      ent = st?.created?.[0];
      if (!ent) return;
    }
    if (ent && !this.pending.includes(ent) && this.pending.length < 2) {
      const next = [...this.pending, ent];
      if (this.pending.length === 1 && !this._dimPlan(next, null)) { this.toast("この組み合わせには寸法を付けられません", true); return; }
      this.pending = next; this.sel.clear(); next.forEach((id) => this.sel.add(id)); this.v.redraw();
      return;
    }
    if (!this.pending.length) return;
    const q = this.uv(sx, sy), pv = q && this._dimPreview(this.pending, q);
    const ids = this.pending; this.pending = []; this.sel.clear();
    if (!pv) { this.toast("この要素だけでは寸法を付けられません（もう 1 つ選んでください）", true); this.v.redraw(); return; }
    const st = await this._send("sketch.dimension", { refs: ids, ...pv.plan.send, label: pv.label });
    if (st?.created?.[0]) this.editDimension(st.created[0]);
  }

  /** スマート寸法の相手になる、スケッチの外のもの（点 → 線 → モデルの曲線エッジの順）。 */
  _dimExt(sx, sy) {
    return this._extHit(sx, sy, true) || this._extHit(sx, sy, false, { curves: true });
  }

  /** 寸法の値を画面上で編集する（数値か、= で始まる式）。 */
  editDimension(cid) {
    const c = this.sk.constraints.find((x) => x.id === cid);
    this.draw();                                   // 当たり判定（ラベルの位置）を最新にする
    const h = this.hits.find((x) => x.id === cid && x.kind === "label");
    if (!c || !h) return;
    document.querySelector(".dim-editor")?.remove();
    const inp = document.createElement("input");
    inp.className = "dim-editor";
    inp.value = c.expr ? "=" + c.expr : String(Math.round(c.value * 1e6) / 1e6);
    inp.title = "数値か、= で始まる式（例: =width/2）。Enter で確定、Esc で取り消し";
    const r = this.cv.getBoundingClientRect();
    Object.assign(inp.style, { left: `${r.left + h.center[0] - 45}px`, top: `${r.top + h.center[1] - 12}px` });
    document.body.append(inp);
    inp.focus(); inp.select();
    const close = () => inp.remove();
    inp.addEventListener("keydown", async (ev) => {
      ev.stopPropagation();
      if (ev.key === "Escape") close();
      if (ev.key !== "Enter") return;
      const s = inp.value.trim();
      const params = s.startsWith("=") ? { expr: s.slice(1) } : { value: Number(s) };
      if (!s.startsWith("=") && !Number.isFinite(params.value)) { this.toast("数値か = で始まる式を入れてください", true); return; }
      close();
      await this._send("feature.edit", { id: this.sk.id, key: cid, ...params });
    });
    inp.addEventListener("blur", () => setTimeout(close, 100));
  }

  async _send(method, params) {
    try {
      const st = await this.rpc(method, params);
      if (st && st.tree) this.onState(st);
      return st;
    } catch (err) { this.toast(err.message, true); return null; }
  }

  // ------------------------------------------------------------ 選択に対する操作
  _selChanged() { this.onSelection?.([...this.sel]); this.v.redraw(); }

  selectionKinds() {
    return [...this.sel].map((id) => (id === "ORIGIN" || id === this.originId ? "origin" : this.E[id]?.type || "constraint"));
  }

  /** 選択に寸法を付けられるか（スマート寸法の相手になるか）。 */
  canDimension() {
    const ids = [...this.sel].filter((id) => this.E[id]);
    if (ids.length !== this.sel.size || !ids.length || ids.length > 2) return false;
    if (ids.length === 1) return ["line", "circle", "arc"].includes(this.E[ids[0]].type);
    return !!this._dimPlan(ids, null);
  }

  constrain(type) {
    const refs = [...this.sel].filter((id) => this.E[id]);
    if (!refs.length) { this.toast("要素を選んでください", true); return; }
    return this._send("sketch.constrain", { type, refs });
  }

  deleteSelected() {
    const ids = [...this.sel].filter((id) => id !== "ORIGIN"); this.sel.clear();
    if (!ids.length) { this._selChanged(); return; }
    return this._send("sketch.delete", { ids });
  }

  toggleConstruction() {
    const ids = [...this.sel].filter((id) => this.E[id] && this.E[id].type !== "point");
    if (!ids.length) { this.toast("線・円・円弧を選んでください", true); return; }
    return this._send("sketch.construction", { ids });
  }

  // ------------------------------------------------------------ スケッチパターン（D61）
  /** パターンにできる選択（線・円・円弧・点。原点などの仮の要素は除く）。 */
  patternIds() { return [...this.sel].filter((id) => this.E[id] && !this.E[id].virtual && id !== this.originId); }

  startPattern(kind) {
    const ids = this.patternIds();
    if (!ids.length) { this.toast("パターンにする要素を先に選んでください", true); return; }
    const prev = this.patternMode || this.lastPattern;
    this.tool = "select"; this.pending = []; this.onToolChange?.("select");
    this.patternMode = { kind, ids, count: kind === prev?.kind ? prev.count : (kind === "linear" ? 3 : 4),
      spacing: prev?.spacing || 20, angle: prev?.angle || 0, total: prev?.total || 360, center: "ORIGIN", pick: false };
    this._selChanged();
  }

  cancelPattern() { this.patternMode = null; this._selChanged(); }

  async commitPattern() {
    const m = this.patternMode; if (!m) return;
    if (!(m.count >= 2) || !Number.isInteger(m.count)) { this.toast("個数は 2 以上の整数にしてください", true); return; }
    const params = m.kind === "linear" ? { spacing: m.spacing, angle: m.angle } : { total: m.total, center: m.center };
    const st = await this._send("sketch.pattern", { ids: m.ids, kind: m.kind, count: m.count, ...params });
    if (!st?.created) return;
    this.lastPattern = m; this.patternMode = null; this.sel.clear(); this._selChanged();
  }

  /** パターンの k 番目の写しへの変換（スケッチの座標 → スケッチの座標）。 */
  _patStep(k) {
    const m = this.patternMode;
    if (m.kind === "linear") {
      const a = m.angle * Math.PI / 180, d = [k * m.spacing * Math.cos(a), k * m.spacing * Math.sin(a)];
      return (p) => add2(p, d);
    }
    const c = this._pt(m.center) || [0, 0];
    const da = (Math.abs(Math.abs(m.total) - 360) < 1e-9 ? m.total / m.count : m.total / (m.count - 1)) * Math.PI / 180 * k;
    const cs = Math.cos(da), sn = Math.sin(da);
    return (p) => { const v = sub2(p, c); return [c[0] + cs * v[0] - sn * v[1], c[1] + sn * v[0] + cs * v[1]]; };
  }

  /** 要素の形（スケッチの座標の折れ線）。 */
  _uvCurve(e) {
    if (e.type === "point") return [[e.x, e.y]];
    if (e.type === "line") return [this.P(e.p1), this.P(e.p2)];
    const [cx, cy] = this.P(e.center);
    let a0 = 0, sweep = 2 * Math.PI, r = e.radius;
    if (e.type === "arc") {
      const [sx, sy] = this.P(e.start), [ex, ey] = this.P(e.end);
      r = Math.hypot(sx - cx, sy - cy); a0 = Math.atan2(sy - cy, sx - cx);
      sweep = ((Math.atan2(ey - cy, ex - cx) - a0) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) || 2 * Math.PI;
    }
    const n = Math.max(8, Math.ceil(48 * sweep / (2 * Math.PI))), out = [];
    for (let i = 0; i <= n; i++) { const a = a0 + sweep * i / n; out.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]); }
    return out;
  }

  _patternPreview() {
    const m = this.patternMode, ctx = this.ctx;
    if (!m || !(m.count >= 2) || m.count > 500) return;
    ctx.save(); ctx.strokeStyle = COLOR.preview; ctx.fillStyle = COLOR.preview; ctx.setLineDash([5, 4]); ctx.lineWidth = 1.4;
    for (let k = 1; k < Math.floor(m.count); k++) {
      const f = this._patStep(k);
      for (const id of m.ids) {
        const e = this.E[id]; if (!e) continue;
        const pts = this._uvCurve(e).map((p) => this.s(...f(p)));
        if (pts.some((p) => !p)) continue;
        if (pts.length === 1) { ctx.fillRect(pts[0][0] - 2.5, pts[0][1] - 2.5, 5, 5); continue; }
        ctx.beginPath(); pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.stroke();
      }
    }
    if (m.kind === "circular") {                     // 中心の印
      const c = this.s(...(this._pt(m.center) || [0, 0]));
      if (c) { ctx.setLineDash([]); ctx.beginPath(); ctx.arc(c[0], c[1], 6, 0, 2 * Math.PI); ctx.stroke(); }
    }
    ctx.restore();
  }

  /** ミラー: 選択の中の作図線（1 本）を軸に、ほかの要素を鏡映コピーする。 */
  async mirror() {
    const ids = [...this.sel].filter((id) => this.E[id] && !this.E[id].virtual);
    const st = await this._send("sketch.mirror", { ids });
    if (st?.created) { this.sel.clear(); st.created.forEach((id) => this.sel.add(id)); this._selChanged(); }
  }

  cancel() {
    if (this.patternMode) { this.cancelPattern(); return; }
    if (this.pending.length) { this.pending = []; this.sel.clear(); this._selChanged(); }
    else if (this.tool !== "select") this.setTool("select");
    else { this.sel.clear(); this._selChanged(); }
    this.v.redraw();
  }
}

/** 半直線 ray = [始点, 向き] と 線 A + t·d の交点の t（平行なら null）。 */
function rayHit([o, u], A, d) {
  const den = u[0] * d[1] - u[1] * d[0];
  if (Math.abs(den) < 1e-12) return null;
  return ((A[0] - o[0]) * u[1] - (A[1] - o[1]) * u[0]) / den;
}

/** 3 点を通る円の中心（一直線なら null）。 */
function circum(a, b, c) {
  const d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]));
  if (Math.abs(d) < 1e-12) return null;
  const q = (p) => p[0] * p[0] + p[1] * p[1];
  return [(q(a) * (b[1] - c[1]) + q(b) * (c[1] - a[1]) + q(c) * (a[1] - b[1])) / d,
    (q(a) * (c[0] - b[0]) + q(b) * (a[0] - c[0]) + q(c) * (b[0] - a[0])) / d];
}

function segCross(p, q, a, b) {
  const o = (u, v, w) => Math.sign((v[0] - u[0]) * (w[1] - u[1]) - (v[1] - u[1]) * (w[0] - u[0]));
  return o(p, q, a) !== o(p, q, b) && o(a, b, p) !== o(a, b, q);
}

function segDist(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1], l = dx * dx + dy * dy;
  const t = l ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l)) : 0;
  return Math.hypot(a[0] + t * dx - p[0], a[1] + t * dy - p[1]);
}
