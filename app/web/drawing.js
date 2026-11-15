// 図面のシートを描いて操作する（D60）。形・寸法の「描く要素」はサーバ（cad/drawing/render.py）が作る。
// ここは 2D のキャンバスに描くことと、入力（ズーム・移動、ビューと寸法の選択・ドラッグ、寸法・ビューを置くツール）だけ。
// 座標: 用紙の mm（原点 = 左下、y は上向き）。画面へは scale（px / mm）と origin で変える。

const LAYER = {   // レイヤー → [色, 線の太さ mm, 破線 mm]
  frame: ["#111", 0.5, null], title: ["#111", 0.18, null], visible: ["#111", 0.35, null],
  hidden: ["#555", 0.18, [2, 1]], dim: ["#0b3d91", 0.18, null],
};
const SNAP_PX = 9, EDGE_PX = 6;
const KIND_LABEL = { vertex: "端点", mid: "中点", center: "中心" };

export class DrawingEditor {
  /** hooks: { rpc(method, params) → 新しい状態を描く, onSelect(), status(text), toast(text, err) } */
  constructor(canvas, hooks) {
    this.cv = canvas; this.ctx = canvas.getContext("2d"); this.h = hooks;
    this.st = null; this.scale = 2; this.ox = 0; this.oy = 0;
    this.sel = null;            // 選んでいるビュー / 寸法の ID
    this.tool = "select";       // select / dim / place（モデルビューを置く）/ project（投影図を置く）/ note（注記を置く）
    this.pending = [];          // 寸法ツール: 選んだ相手 [{view, anchor | circle, p（用紙の座標）}]
    this.placing = null;        // place: { orient, label }
    this.hover = null; this.cursor = null; this.drag = null;
    canvas.addEventListener("wheel", (e) => this._wheel(e), { passive: false });
    canvas.addEventListener("pointerdown", (e) => this._down(e));
    canvas.addEventListener("pointermove", (e) => this._move(e));
    canvas.addEventListener("pointerup", (e) => this._up(e));
    canvas.addEventListener("contextmenu", (e) => e.preventDefault());
    new ResizeObserver(() => { this._resize(); this.draw(); }).observe(canvas);
  }

  get active() { return !!this.st; }

  set(st, { fit = false } = {}) {
    this.st = st;
    if (!st) { this.sel = null; this.tool = "select"; this.pending = []; this.placing = null; this.hover = null; this.drag = null; return; }
    if (this.sel && !this.view(this.sel) && !this.dim(this.sel)) this.sel = null;
    this._resize();
    if (fit) this.fit();
    this.draw();
  }

  view(id) { return this.st?.views.find((v) => v.id === id); }
  dim(id) { return this.st?.dims.find((d) => d.id === id); }

  setTool(t, opts = {}) {
    this.tool = t; this.pending = []; this.placing = t === "place" ? opts : null; this.hover = null;
    this.cv.style.cursor = t === "select" ? "default" : "crosshair";
    this.h.onTool?.(t);
    this._status();
    this.draw();
  }

  // ------------------------------------------------------------ 座標
  _resize() {
    const r = this.cv.getBoundingClientRect(), d = window.devicePixelRatio || 1;
    if (!r.width) return;
    if (this.cv.width !== Math.round(r.width * d) || this.cv.height !== Math.round(r.height * d)) {
      this.cv.width = Math.round(r.width * d); this.cv.height = Math.round(r.height * d);
    }
    this.w = r.width; this.hgt = r.height; this.dpr = d;
  }
  fit() {
    if (!this.st || !this.w) return;
    const { width, height } = this.st.sheet;
    this.scale = Math.min((this.w - 40) / width, (this.hgt - 40) / height);
    this.ox = (this.w - width * this.scale) / 2; this.oy = (this.hgt + height * this.scale) / 2;
    this.draw();
  }
  /** 用紙 → 画面 */
  S(x, y) { return [this.ox + x * this.scale, this.oy - y * this.scale]; }
  /** 画面 → 用紙 */
  P(sx, sy) { return [(sx - this.ox) / this.scale, (this.oy - sy) / this.scale]; }
  _pos(e) { const r = this.cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }

  // ------------------------------------------------------------ 描く
  draw() {
    const c = this.ctx; if (!this.w) return;
    c.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    c.fillStyle = "#cfd6df"; c.fillRect(0, 0, this.w, this.hgt);
    const st = this.st; if (!st) return;
    const [x0, y0] = this.S(0, st.sheet.height), W = st.sheet.width * this.scale, H = st.sheet.height * this.scale;
    c.fillStyle = "rgba(0,0,0,.18)"; c.fillRect(x0 + 4, y0 + 4, W, H);
    c.fillStyle = "#fff"; c.fillRect(x0, y0, W, H);
    this._items(st.sheet.items, 0, 0);
    if (st.error) {
      c.fillStyle = "#d0342c"; c.font = "14px sans-serif"; c.textAlign = "center";
      c.fillText(st.error, x0 + W / 2, y0 + H / 2);
    }
    for (const v of st.views) {
      const [dx, dy] = this._offset(v.id);
      this._items(v.items, v.x + dx, v.y + dy);
      if (v.id === this.sel || this.hover?.view === v.id && this.tool === "select" && !this.hover.dim) this._box(v, dx, dy, v.id === this.sel);
    }
    for (const d of st.dims) {
      const [dx, dy] = this._offset(d.view);
      this._items(d.items, d.x + dx, d.y + dy, d.id === this.sel ? "#ff7a00" : d.dangling ? "#d0342c" : null);
    }
    this._overlay();
  }

  /** ドラッグ中のビューのずれ（用紙の mm）。 */
  _offset(viewId) {
    const g = this.drag;
    return g && g.kind === "view" && g.id === viewId ? [g.dx, g.dy] : [0, 0];
  }

  _items(items, ox, oy, color = null) {
    const c = this.ctx, k = this.scale;
    for (const it of items) {
      const [col, w, dash] = LAYER[it.layer] || LAYER.visible;
      c.strokeStyle = c.fillStyle = color || col;
      c.lineWidth = Math.max(w * k, it.layer === "visible" || it.layer === "frame" ? 1.3 : 0.8);
      c.setLineDash(dash ? dash.map((d) => d * k) : []);
      c.lineCap = "round";
      if (it.k === "lines") {
        c.beginPath();
        let px = NaN, py = NaN;                      // 前の線分の終点から続くなら 1 本の線として描く（破線が途切れない）
        for (const s of it.segs) {
          if (Math.abs(s[0] - px) > 1e-6 || Math.abs(s[1] - py) > 1e-6) { const a = this.S(s[0] + ox, s[1] + oy); c.moveTo(a[0], a[1]); }
          const b = this.S(s[2] + ox, s[3] + oy); c.lineTo(b[0], b[1]); px = s[2]; py = s[3];
        }
        c.stroke();
      } else if (it.k === "poly") {
        c.beginPath();
        it.pts.forEach((p, i) => { const q = this.S(p[0] + ox, p[1] + oy); if (i) c.lineTo(q[0], q[1]); else c.moveTo(q[0], q[1]); });
        c.closePath(); c.fill();
      } else if (it.k === "text") {
        const q = this.S(it.p[0] + ox, it.p[1] + oy);
        c.save(); c.translate(q[0], q[1]); c.rotate(-(it.angle || 0) * Math.PI / 180);
        c.font = `${Math.max(1, it.h * k / 0.72)}px Helvetica, Arial, "Yu Gothic", "Meiryo", sans-serif`;
        c.textAlign = { l: "left", c: "center", r: "right" }[it.align || "l"]; c.textBaseline = "alphabetic";
        c.fillText(it.s, 0, 0); c.restore();
      }
    }
    c.setLineDash([]);
  }

  _box(v, dx, dy, selected) {
    const c = this.ctx, pad = 4;
    const a = this.S(v.x + dx + v.bbox[0] - pad, v.y + dy + v.bbox[3] + pad), b = this.S(v.x + dx + v.bbox[2] + pad, v.y + dy + v.bbox[1] - pad);
    c.strokeStyle = selected ? "#1e6fff" : "#8fb3e8"; c.lineWidth = 1; c.setLineDash([5, 3]);
    c.strokeRect(a[0], a[1], b[0] - a[0], b[1] - a[1]); c.setLineDash([]);
    if (selected) { c.fillStyle = "#1e6fff"; c.font = "11px sans-serif"; c.textAlign = "left"; c.fillText(v.label, a[0], a[1] - 3); }
  }

  _overlay() {
    const c = this.ctx, h = this.hover;
    const mark = (p, color, r = 5) => { const q = this.S(p[0], p[1]); c.strokeStyle = color; c.lineWidth = 2; c.strokeRect(q[0] - r, q[1] - r, 2 * r, 2 * r); };
    if (this.tool === "dim") {
      for (const p of this.pending) {
        if (p.circle) { const q = this.S(p.c[0], p.c[1]); c.strokeStyle = "#ff7a00"; c.lineWidth = 2; c.beginPath(); c.arc(q[0], q[1], p.r * this.scale, 0, 7); c.stroke(); }
        else mark(p.p, "#ff7a00");
      }
      if (h?.snap) mark(h.p, "#18a058");
      if (h?.edge) {
        c.strokeStyle = "#18a058"; c.lineWidth = 2.5; c.beginPath();
        if (h.edge.kind === "line") { const a = this.S(...h.a), b = this.S(...h.b); c.moveTo(a[0], a[1]); c.lineTo(b[0], b[1]); }
        else { const q = this.S(...h.c); c.arc(q[0], q[1], h.edge.r * this.scale, 0, 7); }
        c.stroke();
      }
      if (this._ready() && this.cursor) {                    // 置く位置の目安
        const q = this.S(...this.cursor); c.strokeStyle = "#0b3d91"; c.setLineDash([4, 3]); c.lineWidth = 1;
        for (const p of this.pending) { const a = this.S(...(p.circle ? p.c : p.p)); c.beginPath(); c.moveTo(a[0], a[1]); c.lineTo(q[0], q[1]); c.stroke(); }
        c.setLineDash([]);
      }
    }
    if ((this.tool === "place" || this.tool === "project") && this.cursor) {
      let [x, y] = this.cursor, label = this.placing?.label || "投影図";
      if (this.tool === "project") {
        const g = this._projectAt(this.cursor); if (!g) return;
        [x, y] = g.at; label = { right: "右から見た図", left: "左から見た図", up: "上から見た図", down: "下から見た図" }[g.side];
      }
      const a = this.S(x - 20, y + 14), b = this.S(x + 20, y - 14);
      c.strokeStyle = "#1e6fff"; c.setLineDash([5, 3]); c.lineWidth = 1.2; c.strokeRect(a[0], a[1], b[0] - a[0], b[1] - a[1]); c.setLineDash([]);
      c.fillStyle = "#1e6fff"; c.font = "12px sans-serif"; c.textAlign = "center"; c.fillText(label, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + 4);
    }
    if (this.drag?.kind === "dim" && this.cursor) {          // 寸法の文字を動かしている
      const q = this.S(...this.cursor); c.strokeStyle = "#ff7a00"; c.setLineDash([3, 3]); c.strokeRect(q[0] - 14, q[1] - 8, 28, 16); c.setLineDash([]);
    }
  }

  // ------------------------------------------------------------ 当たり判定
  _hitDim(p) {
    for (const d of [...this.st.dims].reverse()) {
      const b = d.box, x = p[0] - d.x, y = p[1] - d.y;
      if (x >= b[0] && x <= b[2] && y >= b[1] && y <= b[3]) return d;
    }
    return null;
  }
  _hitView(p, pad = 3) {
    for (const v of [...this.st.views].reverse()) {
      const x = p[0] - v.x, y = p[1] - v.y;
      if (x >= v.bbox[0] - pad && x <= v.bbox[2] + pad && y >= v.bbox[1] - pad && y <= v.bbox[3] + pad) return v;
    }
    return null;
  }
  /** 寸法の相手: 近くのスナップ先（端点・中点・中心）→ 近くのエッジ（直線・正面の円）。 */
  _hitGeo(p) {
    const tolS = SNAP_PX / this.scale, tolE = EDGE_PX / this.scale;
    let best = null, bd = tolS;
    for (const v of this.st.views) {
      for (const s of v.snaps) {
        const d = Math.hypot(s.p[0] + v.x - p[0], s.p[1] + v.y - p[1]) + (s.kind === "vertex" ? 0 : 0.3 / this.scale);
        if (d < bd) { bd = d; best = { view: v.id, snap: s, p: [s.p[0] + v.x, s.p[1] + v.y] }; }
      }
    }
    if (best) return best;
    bd = tolE;
    for (const v of this.st.views) {
      const x = p[0] - v.x, y = p[1] - v.y;
      for (const e of v.edges) {
        let d;
        if (e.kind === "line") {
          const ax = e.p1[0], ay = e.p1[1], bx = e.p2[0] - ax, by = e.p2[1] - ay, l = bx * bx + by * by;
          const t = l ? Math.max(0, Math.min(1, ((x - ax) * bx + (y - ay) * by) / l)) : 0;
          d = Math.hypot(ax + t * bx - x, ay + t * by - y);
        } else d = Math.abs(Math.hypot(x - e.c[0], y - e.c[1]) - e.r);
        if (d < bd) {
          bd = d;
          best = e.kind === "line" ? { view: v.id, edge: e, a: [e.p1[0] + v.x, e.p1[1] + v.y], b: [e.p2[0] + v.x, e.p2[1] + v.y] }
            : { view: v.id, edge: e, c: [e.c[0] + v.x, e.c[1] + v.y] };
        }
      }
    }
    return best;
  }
  _vertexAt(v, q) {      // ビュー v の、相対座標 q にある端点のスナップ先
    return v.snaps.find((s) => s.kind === "vertex" && Math.hypot(s.p[0] - q[0], s.p[1] - q[1]) < 1e-4);
  }
  _ready() { return this.pending.length === 2 || (this.pending.length === 1 && this.pending[0].circle); }

  /** 投影図を置く場所: 親（選んでいるビュー）から見てカーソルがある側。 */
  _projectAt(p) {
    const v = this.view(this.sel); if (!v) return null;
    const cx = v.x + (v.bbox[0] + v.bbox[2]) / 2, cy = v.y + (v.bbox[1] + v.bbox[3]) / 2, dx = p[0] - cx, dy = p[1] - cy;
    const hw = (v.bbox[2] - v.bbox[0]) / 2, hh = (v.bbox[3] - v.bbox[1]) / 2;
    if (Math.abs(dx) <= hw && Math.abs(dy) <= hh) return null;                    // 親の上
    const side = Math.abs(dx) - hw > Math.abs(dy) - hh ? (dx > 0 ? "right" : "left") : (dy > 0 ? "up" : "down");
    return { side, at: side === "right" || side === "left" ? [p[0], cy] : [cx, p[1]], x: p[0], y: p[1] };
  }

  // ------------------------------------------------------------ 入力
  _wheel(e) {
    e.preventDefault();
    const [sx, sy] = this._pos(e), [px, py] = this.P(sx, sy);
    this.scale = Math.max(0.2, Math.min(80, this.scale * Math.exp(-e.deltaY * 0.0015)));
    this.ox = sx - px * this.scale; this.oy = sy + py * this.scale;
    this.draw();
  }

  _down(e) {
    if (!this.st) return;
    const s = this._pos(e), p = this.P(...s);
    this.cv.setPointerCapture(e.pointerId);
    if (e.button !== 0) { this.drag = { kind: "pan", sx: s[0], sy: s[1], ox: this.ox, oy: this.oy, moved: false, button: e.button }; return; }
    if (this.tool === "select") {
      const d = this._hitDim(p), v = d ? null : this._hitView(p);
      this.sel = d?.id || v?.id || null;
      this.h.onSelect?.();
      if (d) this.drag = { kind: "dim", id: d.id, moved: false, start: p };
      else if (v) this.drag = { kind: "view", id: v.id, start: p, dx: 0, dy: 0, moved: false };
      else this.drag = { kind: "pan", sx: s[0], sy: s[1], ox: this.ox, oy: this.oy, moved: false, button: 0 };
      this.draw();
    }
  }

  _move(e) {
    if (!this.st) return;
    const s = this._pos(e), p = this.P(...s), g = this.drag;
    this.cursor = p;
    if (g?.kind === "pan") { this.ox = g.ox + s[0] - g.sx; this.oy = g.oy + s[1] - g.sy; g.moved = g.moved || Math.hypot(s[0] - g.sx, s[1] - g.sy) > 3; this.draw(); return; }
    if (g?.kind === "view") {
      const v = this.view(g.id);
      g.dx = p[0] - g.start[0]; g.dy = p[1] - g.start[1];
      if (v.parent && this.view(v.parent)) { if (v.side === "right" || v.side === "left") g.dy = 0; else g.dx = 0; }   // 投影図は親とそろえたまま
      g.moved = g.moved || Math.hypot(g.dx, g.dy) * this.scale > 3;
      this.draw(); return;
    }
    if (g?.kind === "dim") { g.moved = g.moved || Math.hypot(p[0] - g.start[0], p[1] - g.start[1]) * this.scale > 3; this.draw(); return; }
    if (this.tool === "dim") this.hover = this._ready() ? null : this._hitGeo(p);
    else if (this.tool === "select") { const d = this._hitDim(p), v = d ? null : this._hitView(p); this.hover = d ? { dim: d.id } : v ? { view: v.id } : null; }
    this._status();
    this.draw();
  }

  async _up(e) {
    if (!this.st) return;
    const s = this._pos(e), p = this.P(...s), g = this.drag;
    this.drag = null;
    if (g?.kind === "pan") {
      if (!g.moved && g.button === 2 && this.tool !== "select") this.setTool("select");       // 右クリックでツールを終える
      else if (!g.moved && g.button === 0) this.draw();
      return;
    }
    if (g?.kind === "view") {
      if (g.moved) { const v = this.view(g.id); await this.h.rpc("drw.view_set", { id: g.id, x: v.x + g.dx, y: v.y + g.dy }); }
      else this.draw();
      return;
    }
    if (g?.kind === "dim") {
      if (g.moved) { const d = this.dim(g.id); await this.h.rpc("drw.dim_set", { id: g.id, pos: [p[0] - d.x, p[1] - d.y] }); }
      else this.draw();
      return;
    }
    if (e.button !== 0) return;
    if (this.tool === "place") {
      const o = this.placing; this.setTool("select");
      const st = await this.h.rpc("drw.view_add", { orient: o.orient, x: p[0], y: p[1] });
      if (st?.created) { this.sel = st.created[0]; this.h.onSelect?.(); this.draw(); }
    } else if (this.tool === "project") {
      const at = this._projectAt(p); if (!at) return;
      const parent = this.sel; this.setTool("select");
      const st = await this.h.rpc("drw.view_add", { parent, side: at.side, x: at.x, y: at.y });
      if (st?.created) { this.sel = st.created[0]; this.h.onSelect?.(); this.draw(); }
    } else if (this.tool === "dim") await this._dimClick(p);
    else if (this.tool === "note") {
      // 注記はビューに付ける（ビューと一緒に動く）: 中にあるビュー、無ければいちばん近いビュー
      const vs = this.st.views; if (!vs.length) { this.h.toast("先にビューを置いてください", true); return; }
      const dist = (v) => Math.hypot(Math.max(v.x + v.bbox[0] - p[0], 0, p[0] - v.x - v.bbox[2]), Math.max(v.y + v.bbox[1] - p[1], 0, p[1] - v.y - v.bbox[3]));
      const v = this._hitView(p) || vs.reduce((a, b) => (dist(b) < dist(a) ? b : a));
      const text = await this.h.ask?.("注記の文字（室名など）");
      if (!text) return;
      const st = await this.h.rpc("drw.note_add", { view: v.id, text, pos: [p[0] - v.x, p[1] - v.y] });
      if (st?.created) { this.sel = st.created[0]; this.h.onSelect?.(); this.draw(); }
    }
  }

  async _dimClick(p) {
    if (this._ready()) {                                     // 置く
      const [a, b] = this.pending, v = this.view(a.view), pos = [p[0] - v.x, p[1] - v.y];
      this.pending = [];
      const params = a.circle ? { view: a.view, a: { src: `edge:${a.name}` }, pos } : { view: a.view, a: a.anchor, b: b.anchor, pos };
      const st = await this.h.rpc("drw.dim_add", params);
      if (st?.created) this.sel = st.created[0];
      this._status(); this.draw();
      return;
    }
    const h = this._hitGeo(p);
    if (!h) { this.pending = []; this._status(); this.draw(); return; }
    if (this.pending.length && this.pending[0].view !== h.view) { this.h.toast("同じビューの中の 2 点を選んでください", true); return; }
    const v = this.view(h.view);
    if (h.snap) this.pending.push({ view: h.view, anchor: { src: h.snap.src, hint: h.snap.w }, p: h.p });
    else if (h.edge.kind === "line") {
      const a = this._vertexAt(v, h.edge.p1), b = this._vertexAt(v, h.edge.p2);
      if (!a || !b || this.pending.length) { this.h.toast("直線は最初にクリックしてください（両端の長さになります）", true); return; }
      this.pending = [{ view: h.view, anchor: { src: a.src, hint: a.w }, p: h.a }, { view: h.view, anchor: { src: b.src, hint: b.w }, p: h.b }];
    } else {
      if (this.pending.length) { this.h.toast("円は最初にクリックしてください（直径・半径になります）", true); return; }
      this.pending = [{ view: h.view, circle: true, name: h.edge.name, c: h.c, r: h.edge.r }];
    }
    this.hover = null; this._status(); this.draw();
  }

  /** Esc: 選びかけを消す → ツールを終える → 選択を外す。 */
  cancel() {
    if (this.pending.length) { this.pending = []; this._status(); this.draw(); }
    else if (this.tool !== "select") this.setTool("select");
    else if (this.sel) { this.sel = null; this.h.onSelect?.(); this.draw(); }
  }

  _status() {
    let t = "";
    if (this.tool === "dim") {
      const h = this.hover;
      t = this._ready() ? "寸法を置く位置をクリック（上下に置くと水平、左右に置くと垂直、2 点の間に置くと平行）"
        : h?.snap ? `${KIND_LABEL[h.snap.kind]}` : h?.edge ? (h.edge.kind === "line" ? "直線（長さ）" : h.edge.kind === "circle" ? "円（直径）" : "円弧（半径）")
          : this.pending.length ? "2 つ目の点をクリック" : "スマート寸法: 点（端点・中点・中心）を 2 つ、または直線・円をクリック（Esc で終了）";
    } else if (this.tool === "place") t = `${this.placing.label}を置く位置をクリック（Esc で取り消し）`;
    else if (this.tool === "project") t = "投影図を置く側（選んだビューの上下左右）へカーソルを動かしてクリック（Esc で取り消し）";
    else if (this.tool === "note") t = "注記を置く位置をクリック（続けて置けます。Esc で終了）";
    else if (this.hover?.view) t = "ドラッグでビューを移動";
    else if (this.hover?.dim) t = this.dim(this.hover.dim)?.kind === "note" ? "ドラッグで注記を移動、Delete で削除" : "ドラッグで寸法を移動、Delete で削除";
    this.h.status?.(t);
  }
}
