// 3D ビューア（WebGL2、外部ライブラリなし）。
// - 面ごとの陰影（面の中はなめらか、面の境目は折れる）＋エッジの線
// - 平行投影（既定。一般的な 3D CAD と同じ）/ 透視投影
// - 回転（中ドラッグ、何もない所の左ドラッグ）/ パン（Ctrl+中ドラッグ、右ドラッグ、Shift+左）/
//   ズーム（ホイール = カーソルの位置を中心に、Shift+中ドラッグ）。右クリック（動かさない）は onContext
// - 表示スタイル: shaded_edges（エッジ付きシェーディング）/ shaded / hlr（隠線消去）/ wire（ワイヤーフレーム）
// - 視点の切り替えはなめらかに動かす（animMs）
// - 面のピック（ID を色に書いた別バッファを読む）。ホバーと選択の強調表示
// - 断面表示（D62）: 平面の +法線 側を消し（シェーダで discard）、切り口をステンシルで塗る（閉じたメッシュの偶奇）。
//   sectionGizmo のときは平面のハンドル（矢印 = 法線の向きに移動、輪 = 平面の中の軸のまわりに回転）を出し、
//   ドラッグの量を onSectionGizmo({ move } / { rotate: 軸, angle, about }) で知らせる（平面を変えるのは呼び出し側）
// 座標系は Y 上（一般的な 3D CAD と同じ。正面 = XY 平面を +Z から見る）。

const VS = `#version 300 es
in vec3 aPos; in vec3 aNor; in float aFace; in vec3 aCol;
uniform mat4 uMVP; uniform mat4 uView;
out vec3 vN; out float vFace; out vec3 vW; out vec3 vCol;
void main() { vN = mat3(uView) * aNor; vFace = aFace; vW = aPos; vCol = aCol; gl_Position = uMVP * vec4(aPos, 1.0); }`;

// 断面: uClip = (法線, 距離)。dot(法線, 位置) > 距離 の側を消す
const CLIP = `uniform vec4 uClip; uniform float uClipOn;
bool clipped(vec3 p) { return uClipOn > 0.5 && dot(uClip.xyz, p) > uClip.w; }`;

const FS = `#version 300 es
precision highp float;
in vec3 vN; in float vFace; in vec3 vW; in vec3 vCol;
uniform float uHover; uniform float uSel; uniform float uSel2; uniform float uFlat;
${CLIP}
out vec4 o;
void main() {
  if (clipped(vW)) discard;
  vec3 n = normalize(vN);
  if (!gl_FrontFacing) n = -n;
  vec3 L1 = normalize(vec3(0.2, 0.4, 1.0)), L2 = normalize(vec3(-0.6, -0.3, 0.4));
  float d = 0.42 + 0.5 * max(dot(n, L1), 0.0) + 0.15 * max(dot(n, L2), 0.0);
  float s = pow(max(dot(n, normalize(L1 + vec3(0, 0, 1))), 0.0), 40.0) * 0.25;
  vec3 c = vCol;                                    // 面の色（外観。D69）
  float f = floor(vFace + 0.5);
  if (f == uSel || f == uSel2) c = vec3(0.30, 0.56, 0.98);
  else if (f == uHover) c = mix(vCol, vec3(1.0, 0.75, 0.25), 0.55);
  if (uFlat > 0.5) { o = vec4(mix(vec3(0.985), c, (f == uSel || f == uSel2 || f == uHover) ? 0.45 : 0.0), 1.0); return; }
  o = vec4(c * d + s, 1.0);
}`;

const PICK_FS = `#version 300 es
precision highp float;
in vec3 vN; in float vFace; in vec3 vW;
${CLIP}
out vec4 o;
void main() {
  if (clipped(vW)) discard;
  float id = floor(vFace + 0.5) + 1.0;
  o = vec4(mod(id, 256.0), mod(floor(id / 256.0), 256.0), floor(id / 65536.0), 255.0) / 255.0;
}`;

const LINE_VS = `#version 300 es
in vec3 aPos; in vec3 aCol;
uniform mat4 uMVP;
out vec3 vCol; out vec3 vW;
void main() { vCol = aCol; vW = aPos; gl_Position = uMVP * vec4(aPos, 1.0); }`;

const LINE_FS = `#version 300 es
precision highp float;
in vec3 vCol; in vec3 vW; out vec4 o;
${CLIP}
void main() { if (clipped(vW)) discard; o = vec4(vCol, 1.0); }`;

// ---------------------------------------------------------------- 行列（列優先）
const M = {
  mul(a, b) {
    const o = new Float32Array(16);
    for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
      let s = 0; for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k]; o[c * 4 + r] = s;
    }
    return o;
  },
  perspective(fovy, aspect, near, far) {
    const f = 1 / Math.tan(fovy / 2), o = new Float32Array(16);
    o[0] = f / aspect; o[5] = f; o[10] = (far + near) / (near - far); o[11] = -1; o[14] = 2 * far * near / (near - far);
    return o;
  },
  ortho(hw, hh, near, far) {
    const o = new Float32Array(16);
    o[0] = 1 / hw; o[5] = 1 / hh; o[10] = -2 / (far - near); o[14] = -(far + near) / (far - near); o[15] = 1;
    return o;
  },
  lookAt(e, t, up) {
    let z = norm(sub(e, t)), x = norm(cross(up, z)), y = cross(z, x);
    return new Float32Array([x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0,
      -dot(x, e), -dot(y, e), -dot(z, e), 1]);
  },
};
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const scale = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
/** 部品の色（面の地の色）。断面の切り口もこの色で塗る（一般的な 3D CAD と同じ。D62） */
const BASE = [0.72, 0.76, 0.83];
/** "#rrggbb" → [r, g, b]（0〜1） */
const hexRgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
/** 画面の点 p と線分 ab の距離。 */
const segDist2 = (p, a, b) => {
  const dx = b[0] - a[0], dy = b[1] - a[1], l = dx * dx + dy * dy;
  const t = l ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l)) : 0;
  return Math.hypot(a[0] + t * dx - p[0], a[1] + t * dy - p[1]);
};
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = (a) => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };

export const VIEWS = {
  iso: [45, 35.264], front: [0, 0], back: [180, 0], right: [90, 0], left: [-90, 0], top: [0, 89.999], bottom: [0, -89.999],
};

export class Viewer {
  constructor(canvas, { onPick = () => {}, onHover = () => {}, onContext = () => {} } = {}) {
    this.canvas = canvas;
    this.onPick = onPick; this.onHover = onHover; this.onContext = onContext;
    this.ortho = true; this.style = "shaded_edges"; this.animMs = 200;
    const gl = canvas.getContext("webgl2", { antialias: true, alpha: true, preserveDrawingBuffer: true, stencil: true });
    if (!gl) throw new Error("WebGL2 が使えません");
    this.gl = gl;
    this.prog = this._program(VS, FS);
    this.pickProg = this._program(VS, PICK_FS);
    this.lineProg = this._program(LINE_VS, LINE_FS);
    this.mesh = null; this.lines = null; this.axes = null;
    this.yaw = 45; this.pitch = 35.264; this.dist = 100; this.target = [0, 0, 0]; this.radius = 50;
    this.hover = -1; this.sel = -1;
    this.section = null; this._cap = null;
    this.capColor = null;     // 切り口の色。null なら部品の色に面と同じ光を当てた色（_capShade） this.sectionGizmo = false; this.onSectionGizmo = null;
    this._gHover = null; this._gDrag = null;
    this.fovy = 30 * Math.PI / 180;
    this._pickFbo = null;
    this._dirty = true;
    this._bindInput();
    new ResizeObserver(() => this.redraw()).observe(canvas);
    const loop = () => { if (this._dirty) { this._dirty = false; this._draw(); } requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  }

  // ------------------------------------------------------------ データ
  setMesh(m, { keepView = false } = {}) {
    const gl = this.gl;
    this._free();
    if (!m || !m.indices.length) { this.mesh = null; this.redraw(); return; }
    const { positions: P, indices: I, faceOfTriangle: F } = m;
    this._tri = { P, I }; this._cutKey = null;
    // 頂点ごとの面番号と法線（面の中で平均。面ごとに頂点が分かれているので境目は折れる）
    const nv = P.length / 3, face = new Float32Array(nv), nor = new Float32Array(nv * 3), col = new Float32Array(nv * 3);
    // 面の色（外観。D69）: faceColors（面ごとの "#rrggbb"）が無ければ部品の既定の色
    this.partColor = m.partColor ? hexRgb(m.partColor) : BASE;
    const fc = (m.faceColors || []).map((c) => (c ? hexRgb(c) : this.partColor));
    for (let t = 0; t < I.length / 3; t++) {
      const a = I[3 * t], b = I[3 * t + 1], c = I[3 * t + 2];
      const pa = [P[3 * a], P[3 * a + 1], P[3 * a + 2]], pb = [P[3 * b], P[3 * b + 1], P[3 * b + 2]],
        pc = [P[3 * c], P[3 * c + 1], P[3 * c + 2]];
      const n = cross(sub(pb, pa), sub(pc, pa));
      const k = fc[F[t]] || this.partColor;
      for (const v of [a, b, c]) {
        nor[3 * v] += n[0]; nor[3 * v + 1] += n[1]; nor[3 * v + 2] += n[2]; face[v] = F[t];
        col[3 * v] = k[0]; col[3 * v + 1] = k[1]; col[3 * v + 2] = k[2];
      }
    }
    this.mesh = { vao: gl.createVertexArray(), count: I.length, bufs: [] };
    gl.bindVertexArray(this.mesh.vao);
    this._attr(this.prog, "aPos", P, 3); this._attr(this.prog, "aNor", nor, 3); this._attr(this.prog, "aFace", face, 1);
    this._attr(this.prog, "aCol", col, 3);
    const ib = gl.createBuffer(); this.mesh.bufs.push(ib);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ib); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, I, gl.STATIC_DRAW);
    gl.bindVertexArray(null);
    // エッジ
    const E = m.edges;
    if (E && E.length) this.lines = this._lineVao(E, [0.12, 0.13, 0.16]);
    // 原点の軸
    const [lo, hi] = m.bbox;
    this.bbox = [lo, hi]; this._capKey = null;
    if (!(keepView && this.center)) {           // ドラッグ中の更新では範囲（クリップ面の基準）を変えない
      this.radius = Math.max(1e-6, 0.5 * Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]));
      this.center = scale(add(lo, hi), 0.5);
    }
    const L = this.radius * 0.25;
    this.axes = this._lineVao(new Float32Array([0, 0, 0, L, 0, 0, 0, 0, 0, 0, L, 0, 0, 0, 0, 0, 0, L]),
      null, new Float32Array([0.85, 0.2, 0.2, 0.85, 0.2, 0.2, 0.2, 0.7, 0.25, 0.2, 0.7, 0.25, 0.2, 0.35, 0.9, 0.2, 0.35, 0.9]));
    this.redraw();
  }

  /** 全体が入る距離と注視点。 */
  _fitGoal() {
    const c = this.canvas, aspect = (c.clientWidth || 1) / (c.clientHeight || 1);
    let dist = this.radius * 1.15 / (this.ortho ? Math.tan(this.fovy / 2) : Math.sin(this.fovy / 2));
    if (aspect < 1) dist /= aspect;
    return { target: this.center.slice(), dist };
  }

  fit() { if (this.mesh) this.animateTo(this._fitGoal()); }

  setView(name) {
    const [yaw, pitch] = VIEWS[name];
    this.animateTo({ yaw, pitch, ...(this.mesh ? this._fitGoal() : {}) });
  }

  /** 視点をなめらかに動かす。goal は { yaw, pitch, dist, target } のうち変えたいものだけ。 */
  animateTo(goal, ms = this.animMs) {
    cancelAnimationFrame(this._anim);
    const from = { yaw: this.yaw, pitch: this.pitch, dist: this.dist, target: this.target.slice() };
    const to = { ...from, ...goal };
    to.yaw = from.yaw + (((to.yaw - from.yaw) % 360 + 540) % 360 - 180);         // 近い方へ回る
    const set = (u) => {
      const e = u * u * (3 - 2 * u), L = (a, b) => a + (b - a) * e;
      this.yaw = L(from.yaw, to.yaw); this.pitch = L(from.pitch, to.pitch);
      this.dist = Math.exp(L(Math.log(from.dist), Math.log(to.dist)));
      this.target = from.target.map((v, i) => L(v, to.target[i]));
      this.redraw();
    };
    if (!ms || document.hidden) { set(1); return; }
    const t0 = performance.now();
    const step = () => {
      const u = Math.min(1, (performance.now() - t0) / ms);
      set(u);
      if (u < 1) this._anim = requestAnimationFrame(step);
    };
    step();
  }

  setStyle(s) { this.style = s; this.redraw(); }

  /** 断面表示（D62）。s = null（やめる）か { normal: [x, y, z], offset: 距離 }。dot(normal, p) > offset の側を消す。 */
  setSection(s) {
    this.section = s ? { normal: norm(s.normal), offset: Number(s.offset) || 0 } : null;
    this.redraw();
  }

  /** モデルの範囲を向き n に投影した [最小, 最大]（断面の位置のスライダーに使う）。 */
  sectionRange(n) {
    if (!this.bbox) return [-50, 50];
    const [lo, hi] = this.bbox, u = norm(n);
    let a = Infinity, b = -Infinity;
    for (const x of [lo[0], hi[0]]) for (const y of [lo[1], hi[1]]) for (const z of [lo[2], hi[2]]) {
      const t = u[0] * x + u[1] * y + u[2] * z; a = Math.min(a, t); b = Math.max(b, t);
    }
    return [a, b];
  }

  // ------------------------------------------------------------ 断面のハンドル
  /** ハンドルの置き場所: c = モデルの中心を平面に下ろした点、n = 法線、u・v = 平面の中の軸。 */
  _gizmoFrame() {
    const s = this.section;
    if (!s || !this.sectionGizmo || !this.bbox) return null;
    const n = s.normal, c0 = this.center || [0, 0, 0];
    const c = sub(c0, scale(n, dot(n, c0) - s.offset));
    const u = norm(cross(n, Math.abs(n[1]) < 0.9 ? [0, 1, 0] : [1, 0, 0])), v = cross(n, u), R = this.radius;
    return { c, n, u, v, L: 0.55 * R, r: 0.35 * R, P: 0.7 * R };
  }

  /** ハンドルの部品（ワールド座標の折れ線）。 */
  _gizmoParts(g) {
    const tip = add(g.c, scale(g.n, g.L)), back = add(g.c, scale(g.n, g.L * 0.8)), w = g.L * 0.08;
    const ring = (a, b) => Array.from({ length: 65 }, (_, i) => {
      const t = 2 * Math.PI * i / 64;
      return add(g.c, add(scale(a, g.r * Math.cos(t)), scale(b, g.r * Math.sin(t))));
    });
    const P = (a, b) => add(g.c, add(scale(g.u, a * g.P), scale(g.v, b * g.P)));
    return {
      move: [[g.c, tip], [add(back, scale(g.u, w)), tip, add(back, scale(g.u, -w))], [add(back, scale(g.v, w)), tip, add(back, scale(g.v, -w))]],
      rotU: [ring(g.n, g.v)],              // u のまわりに回す輪（n と v の面）
      rotV: [ring(g.n, g.u)],              // v のまわりに回す輪
      frame: [[P(-1, -1), P(1, -1), P(1, 1), P(-1, 1), P(-1, -1)]],
    };
  }

  /** ハンドルを太い線で描く。WebGL の線は 1 ピクセルにしかならないので、画面上で幅を持たせた四角（三角形 2 枚）にする。
   *  白い縁取り（太め）の上に色の線。掴める所にカーソルが載っていれば黄色で少し太く。 */
  _drawGizmo(cam) {
    const g = this._gizmoFrame(); if (!g) return;
    const gl = this.gl, parts = this._gizmoParts(g), pos = [], col = [];
    const COL = { move: [0.12, 0.42, 1.0], rotU: [0.85, 0.2, 0.2], rotV: [0.15, 0.62, 0.25], frame: [0.93, 0.55, 0.2] };
    const W = { move: 3.5, rotU: 3, rotV: 3, frame: 2 };
    const ndc = (p) => [2 * p[0] / cam.cw - 1, 1 - 2 * p[1] / cam.ch, 0];
    const quad = (a, b, w, c) => {                 // 画面の線分 a → b を幅 w（ピクセル）の四角に。端を w/2 伸ばして継ぎ目を埋める
      const dx = b[0] - a[0], dy = b[1] - a[1], l = Math.hypot(dx, dy);
      if (l < 1e-6) return;
      const ux = dx / l * w / 2, uy = dy / l * w / 2, A = [a[0] - ux, a[1] - uy], B = [b[0] + ux, b[1] + uy];
      const P = [[A[0] - uy, A[1] + ux], [A[0] + uy, A[1] - ux], [B[0] + uy, B[1] - ux], [B[0] - uy, B[1] + ux]].map(ndc);
      for (const k of [0, 1, 2, 0, 2, 3]) { pos.push(...P[k]); col.push(...c); }
    };
    const segs = [];
    for (const [k, lines] of Object.entries(parts)) {
      const hot = this._gHover === k || this._gDrag?.part === k;
      for (const pl of lines) {
        const S = pl.map((w) => this.project(w));
        for (let i = 1; i < S.length; i++) if (S[i - 1] && S[i]) segs.push([S[i - 1], S[i], W[k] + (hot ? 1.5 : 0), hot ? [1.0, 0.72, 0.1] : COL[k]]);
      }
    }
    for (const [a, b, w] of segs) quad(a, b, w + 2.5, [1, 1, 1]);      // 縁取り
    for (const [a, b, w, c] of segs) quad(a, b, w, c);
    if (!pos.length) return;
    const o = this._lineVao(new Float32Array(pos), null, new Float32Array(col));
    gl.disable(gl.DEPTH_TEST);
    gl.useProgram(this.lineProg); this._setClip(this.lineProg, false);
    gl.uniformMatrix4fv(this._u(this.lineProg, "uMVP"), false, new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]));
    gl.bindVertexArray(o.vao); gl.drawArrays(gl.TRIANGLES, 0, o.count);
    gl.enable(gl.DEPTH_TEST);
    gl.deleteVertexArray(o.vao); o.bufs.forEach((b) => gl.deleteBuffer(b));
  }

  /** 画面の点の近くにあるハンドルの部品（move / rotU / rotV。枠は掴めない）。 */
  _gizmoHit(p) {
    const g = this._gizmoFrame(); if (!g) return null;
    const parts = this._gizmoParts(g);
    let best = null, bd = 8;
    for (const k of ["move", "rotU", "rotV"]) {
      for (const pl of parts[k]) {
        const S = pl.map((w) => this.project(w));
        for (let i = 1; i < S.length; i++) {
          if (!S[i - 1] || !S[i]) continue;
          const d = segDist2(p, S[i - 1], S[i]);
          if (d < bd) { bd = d; best = k; }
        }
      }
    }
    return best ? { part: best, g } : null;
  }

  _gizmoMove(p) {
    const D = this._gDrag, g = this._gizmoFrame(); if (!D || !g) return;
    if (D.part === "move") {
      const s0 = this.project(g.c), s1 = this.project(add(g.c, scale(g.n, g.L)));
      if (!s0 || !s1) return;
      const d = [s1[0] - s0[0], s1[1] - s0[1]], l2 = d[0] * d[0] + d[1] * d[1];
      if (l2 < 4) return;                                   // 矢印が視線の向き（ほぼ点）: 動かせない
      const m = [p[0] - D.last[0], p[1] - D.last[1]];
      D.last = p;
      this.onSectionGizmo?.({ move: (m[0] * d[0] + m[1] * d[1]) / l2 * g.L });
      return;
    }
    const s0 = this.project(D.about); if (!s0) return;
    const a0 = Math.atan2(D.last[1] - s0[1], D.last[0] - s0[0]), a1 = Math.atan2(p[1] - s0[1], p[0] - s0[0]);
    let da = a1 - a0;
    if (da > Math.PI) da -= 2 * Math.PI;
    if (da < -Math.PI) da += 2 * Math.PI;
    D.last = p;
    // 画面は y が下向き: 画面の角度の増加 = 見た目の時計回り。軸がこちらを向いていれば右ねじの回転は反時計回りに見える
    const fwd = this.cam?.fwd || [0, 0, -1];
    this.onSectionGizmo?.({ rotate: D.axis, angle: dot(D.axis, fwd) >= 0 ? da : -da, about: D.about });
  }

  _setClip(prog, on) {
    const gl = this.gl, s = this.section;
    gl.uniform1f(this._u(prog, "uClipOn"), on && s ? 1 : 0);
    if (s) gl.uniform4f(this._u(prog, "uClip"), s.normal[0], s.normal[1], s.normal[2], s.offset);
  }

  /** 切り口の板（断面の平面上の、モデルより大きい四角）。 */
  _capVao() {
    const s = this.section, col = this.capColor || this._capShade();
    const key = `${s.normal}|${s.offset}|${this.radius}|${this.center}|${col.map((c) => c.toFixed(3))}`;
    if (this._cap && this._capKey === key) return this._cap;
    if (this._cap) { this.gl.deleteVertexArray(this._cap.vao); this._cap.bufs.forEach((b) => this.gl.deleteBuffer(b)); }
    const n = s.normal, c0 = this.center || [0, 0, 0];
    const c = sub(c0, scale(n, n[0] * c0[0] + n[1] * c0[1] + n[2] * c0[2] - s.offset));
    const u = norm(cross(n, Math.abs(n[1]) < 0.9 ? [0, 1, 0] : [1, 0, 0])), v = cross(n, u), R = 3 * this.radius;
    const P = (a, b) => add(c, add(scale(u, a * R), scale(v, b * R)));
    const pos = new Float32Array([...P(-1, -1), ...P(1, -1), ...P(1, 1), ...P(-1, -1), ...P(1, 1), ...P(-1, 1)]);
    this._cap = this._lineVao(pos, col); this._capKey = key;
    return this._cap;
  }

  /** 切り口の色: 部品の色に、面のシェーダ（FS）と同じ光を当てる（切り口は消した側 = +法線 を向いた平らな面）。 */
  _capShade() {
    if (this.style === "hlr") return [0.985, 0.985, 0.985];
    const V = this.cam?.view, n = this.section.normal;
    const B = this.partColor || BASE;                       // 切り口は部品の色（面ごとの色ではない）
    if (!V) return B;
    let v = [0, 1, 2].map((i) => V[i] * n[0] + V[4 + i] * n[1] + V[8 + i] * n[2]);
    v = norm(v);
    if (v[2] < 0) v = scale(v, -1);                        // 裏から見ている（両面とも同じ明るさ。FS と同じ）
    const L1 = norm([0.2, 0.4, 1.0]), L2 = norm([-0.6, -0.3, 0.4]);
    const d = 0.42 + 0.5 * Math.max(dot(v, L1), 0) + 0.15 * Math.max(dot(v, L2), 0);
    const sp = Math.pow(Math.max(dot(v, norm(add(L1, [0, 0, 1]))), 0), 40) * 0.25;
    return B.map((c) => Math.min(1, c * d + sp));
  }

  /** 切り口の輪郭線: 三角形と断面の平面の交線（平面をまたぐ三角形ごとに 1 本）。平面かメッシュが変わったときだけ作り直す。 */
  _cutLines() {
    const s = this.section, T = this._tri;
    if (!s || !T) return null;
    const key = `${s.normal}|${s.offset}`;
    if (this._cut && this._cutKey === key) return this._cut;
    if (this._cut) { this.gl.deleteVertexArray(this._cut.vao); this._cut.bufs.forEach((b) => this.gl.deleteBuffer(b)); this._cut = null; }
    this._cutKey = key;
    const { P, I } = T, [nx, ny, nz] = s.normal, off = s.offset, out = [];
    const dist = (v) => nx * P[3 * v] + ny * P[3 * v + 1] + nz * P[3 * v + 2] - off;
    const at = (a, b, da, db) => { const t = da / (da - db); return [0, 1, 2].map((i) => P[3 * a + i] + t * (P[3 * b + i] - P[3 * a + i])); };
    for (let t = 0; t < I.length; t += 3) {
      const v = [I[t], I[t + 1], I[t + 2]], d = v.map(dist), pts = [];
      for (let i = 0; i < 3; i++) {
        const j = (i + 1) % 3;
        if ((d[i] > 0) !== (d[j] > 0)) pts.push(at(v[i], v[j], d[i], d[j]));      // 0 は「消さない側」に数える
      }
      if (pts.length === 2) out.push(...pts[0], ...pts[1]);
    }
    if (!out.length) return null;
    this._cut = this._lineVao(new Float32Array(out), [0.1, 0.11, 0.14]);
    return this._cut;
  }

  /** 切り口を塗る: 消していない面を奥行きなしで全部描いてステンシルの偶奇を取り、奇数（立体の中）の所に板を描く。 */
  _drawCaps(cam) {
    const gl = this.gl;
    gl.enable(gl.STENCIL_TEST); gl.clearStencil(0); gl.clear(gl.STENCIL_BUFFER_BIT);
    gl.colorMask(false, false, false, false); gl.depthMask(false); gl.disable(gl.DEPTH_TEST);
    gl.stencilFunc(gl.ALWAYS, 0, 0xff); gl.stencilOp(gl.KEEP, gl.KEEP, gl.INVERT);
    gl.useProgram(this.prog); this._setClip(this.prog, true);
    gl.bindVertexArray(this.mesh.vao);
    gl.drawElements(gl.TRIANGLES, this.mesh.count, gl.UNSIGNED_INT, 0);
    gl.colorMask(true, true, true, true); gl.depthMask(true); gl.enable(gl.DEPTH_TEST);
    gl.stencilFunc(gl.NOTEQUAL, 0, 0xff); gl.stencilOp(gl.KEEP, gl.KEEP, gl.KEEP);
    gl.useProgram(this.lineProg); this._setClip(this.lineProg, false);
    gl.uniformMatrix4fv(this._u(this.lineProg, "uMVP"), false, cam.mvp);
    const cap = this._capVao();
    gl.enable(gl.POLYGON_OFFSET_FILL); gl.polygonOffset(1, 1);          // 輪郭線が板に埋もれないように板を少し奥へ
    gl.bindVertexArray(cap.vao); gl.drawArrays(gl.TRIANGLES, 0, cap.count);
    gl.disable(gl.POLYGON_OFFSET_FILL);
    gl.disable(gl.STENCIL_TEST);
    const cut = this._cutLines();                                       // 切り口の輪郭（一般的な 3D CAD のように濃い線）
    if (cut) { gl.bindVertexArray(cut.vao); gl.drawArrays(gl.LINES, 0, cut.count); }
  }
  setOrtho(v) { this.ortho = !!v; this.redraw(); }
  setHover(i) { if (i !== this.hover) { this.hover = i; this.redraw(); } }
  setSelected(i, i2 = -1) { this.sel = i; this.sel2 = i2; this.redraw(); }
  redraw() { this._dirty = true; }

  // ------------------------------------------------------------ 描画
  _camera() {
    const gl = this.gl, c = this.canvas, dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(c.clientWidth * dpr)), h = Math.max(1, Math.round(c.clientHeight * dpr));
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    gl.viewport(0, 0, w, h);
    const y = this.yaw * Math.PI / 180, p = this.pitch * Math.PI / 180;
    const dir = [Math.cos(p) * Math.sin(y), Math.sin(p), Math.cos(p) * Math.cos(y)];
    const R = this.radius, ctr = this.center || [0, 0, 0];
    const hh = this.dist * Math.tan(this.fovy / 2), hw = hh * w / h;     // 注視点の奥行きで見える範囲の半分
    // 平行投影では目の位置は奥行き（クリップ面）にしか効かないので、モデルの外まで下げる
    const back = this.ortho ? Math.max(this.dist, 10 * R + Math.hypot(...sub(this.target, ctr))) : this.dist;
    const eye = add(this.target, scale(dir, back));
    const view = M.lookAt(eye, this.target, [0, 1, 0]);
    const d = Math.hypot(...sub(eye, ctr));
    const proj = this.ortho ? M.ortho(hw, hh, d - 4 * R, d + 4 * R)
      : M.perspective(this.fovy, w / h, Math.max(d - 3 * R, d * 1e-3, 1e-4), d + 3 * R);
    const fwd = norm(sub(this.target, eye)), right = norm(cross(fwd, [0, 1, 0])), up = cross(right, fwd);
    this.cam = { view, mvp: M.mul(proj, view), w, h, eye, fwd, right, up, hw, hh,
      cw: c.clientWidth, ch: c.clientHeight };
    return this.cam;
  }

  /** ワールド座標 → 画面座標（CSS ピクセル）。カメラの後ろなら null。 */
  project(p) {
    const cam = this.cam || this._camera(), m = cam.mvp;
    const x = m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12], y = m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13],
      w = m[3] * p[0] + m[7] * p[1] + m[11] * p[2] + m[15];
    if (w <= 1e-9) return null;
    return [(x / w + 1) / 2 * cam.cw, (1 - y / w) / 2 * cam.ch];
  }

  /** 画面座標 → 視線（始点 o、向き d）。 */
  ray(sx, sy) {
    const cam = this.cam || this._camera(), t = Math.tan(this.fovy / 2), a = cam.cw / cam.ch;
    const nx = (sx / cam.cw) * 2 - 1, ny = 1 - (sy / cam.ch) * 2;
    if (this.ortho) return { o: add(cam.eye, add(scale(cam.right, nx * cam.hw), scale(cam.up, ny * cam.hh))), d: cam.fwd };
    return { o: cam.eye, d: norm(add(cam.fwd, add(scale(cam.right, nx * t * a), scale(cam.up, ny * t)))) };
  }

  /** 平面に正対する（法線の向きから見る）。 */
  lookNormal(origin, normal) {
    const n = norm(normal);
    let yaw = Math.atan2(n[0], n[2]) * 180 / Math.PI;
    const pitch = Math.max(-89.999, Math.min(89.999, Math.asin(Math.max(-1, Math.min(1, n[1]))) * 180 / Math.PI));
    if (Math.abs(n[1]) > 0.9999) yaw = 0;
    this.animateTo({ yaw, pitch });
  }

  /** 1 ピクセルが画面中央の奥行きで何 mm か（スナップの距離などに使う）。 */
  pixelSize() { return 2 * this.dist * Math.tan(this.fovy / 2) / (this.cam?.ch || this.canvas.clientHeight || 1); }

  _draw() {
    const gl = this.gl, cam = this._camera();
    gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST);
    const st = this.style;
    if (this.mesh && st !== "wire") {
      gl.useProgram(this.prog);
      gl.uniform1f(this._u(this.prog, "uFlat"), st === "hlr" ? 1 : 0);
      gl.uniformMatrix4fv(this._u(this.prog, "uMVP"), false, cam.mvp);
      gl.uniformMatrix4fv(this._u(this.prog, "uView"), false, cam.view);
      gl.uniform1f(this._u(this.prog, "uHover"), this.hover);
      gl.uniform1f(this._u(this.prog, "uSel"), this.sel);
      gl.uniform1f(this._u(this.prog, "uSel2"), this.sel2 ?? -1);
      this._setClip(this.prog, true);
      gl.enable(gl.POLYGON_OFFSET_FILL); gl.polygonOffset(1, 1);
      gl.bindVertexArray(this.mesh.vao);
      gl.drawElements(gl.TRIANGLES, this.mesh.count, gl.UNSIGNED_INT, 0);
      gl.disable(gl.POLYGON_OFFSET_FILL);
      if (this.section) this._drawCaps(cam);
    }
    gl.useProgram(this.lineProg);
    gl.uniformMatrix4fv(this._u(this.lineProg, "uMVP"), false, cam.mvp);
    for (const l of [st === "shaded" ? null : this.lines, this.axes]) {
      if (!l) continue;
      this._setClip(this.lineProg, l === this.lines);
      gl.bindVertexArray(l.vao); gl.drawArrays(gl.LINES, 0, l.count);
    }
    if (this.section && this.sectionGizmo) this._drawGizmo(cam);
    gl.bindVertexArray(null);
    this.afterDraw?.(this);
  }

  pick(x, y) {
    if (!this.mesh) return -1;
    const gl = this.gl, cam = this._camera();
    if (!this._pickFbo || this._pickFbo.w !== cam.w || this._pickFbo.h !== cam.h) this._makePickFbo(cam.w, cam.h);
    gl.bindFramebuffer(gl.FRAMEBUFFER, this._pickFbo.fbo);
    gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST);
    gl.useProgram(this.pickProg);
    this._setClip(this.pickProg, true);
    gl.uniformMatrix4fv(this._u(this.pickProg, "uMVP"), false, cam.mvp);
    gl.uniformMatrix4fv(this._u(this.pickProg, "uView"), false, cam.view);
    gl.bindVertexArray(this.mesh.vao);
    gl.drawElements(gl.TRIANGLES, this.mesh.count, gl.UNSIGNED_INT, 0);
    const dpr = window.devicePixelRatio || 1, px = new Uint8Array(4);
    gl.readPixels(Math.round(x * dpr), Math.round(cam.h - y * dpr), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.bindVertexArray(null);
    this.redraw();
    return px[0] + px[1] * 256 + px[2] * 65536 - 1;
  }

  // ------------------------------------------------------------ 入力
  _bindInput() {
    const c = this.canvas;
    let drag = null;
    c.addEventListener("contextmenu", (e) => e.preventDefault());
    const local = (e) => { const r = c.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    let captured = false;
    c.addEventListener("pointerdown", (e) => {
      c.setPointerCapture(e.pointerId);
      cancelAnimationFrame(this._anim);
      if (e.button === 0 && !e.shiftKey) {                    // 断面のハンドルを掴む
        const h = this._gizmoHit(local(e));
        if (h) {
          const axis = h.part === "rotU" ? h.g.u : h.part === "rotV" ? h.g.v : null;
          this._gDrag = { part: h.part, last: local(e), axis, about: h.g.c };
          this.redraw(); return;
        }
      }
      if (this.interceptor && e.button === 0 && !e.shiftKey && this.interceptor.down(e, local(e))) { captured = true; return; }
      // 中ボタン: 回転（Ctrl でパン、Shift でズーム）。右ボタン・Shift+左: パン。左: 回転
      const mode = e.button === 1 ? (e.ctrlKey ? "pan" : e.shiftKey ? "zoom" : "rotate")
        : (e.button === 2 || e.shiftKey) ? "pan" : "rotate";
      if (e.button === 1) e.preventDefault();
      drag = { x: e.clientX, y: e.clientY, x0: e.clientX, y0: e.clientY, mode, moved: false, button: e.button };
    });
    c.addEventListener("pointermove", (e) => {
      const r = c.getBoundingClientRect();
      if (this._gDrag) { this._gizmoMove(local(e)); return; }
      if (!drag && this.sectionGizmo && this.section) {
        const h = this._gizmoHit(local(e))?.part || null;
        if (h !== this._gHover) { this._gHover = h; c.style.cursor = h ? (h === "move" ? "move" : "grab") : ""; this.redraw(); }
        if (h) return;
      }
      if (captured || (this.interceptor && !drag)) { this.interceptor.move(e, local(e)); return; }
      if (!drag) {
        if (!this._hoverPending) {
          this._hoverPending = true;
          requestAnimationFrame(() => {
            this._hoverPending = false;
            const pos = [e.clientX - r.left, e.clientY - r.top], i = this.pick(...pos);
            this.setHover(i); this.onHover(i, pos);
          });
        }
        return;
      }
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      drag.x = e.clientX; drag.y = e.clientY;
      if (Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) > 3) drag.moved = true;
      if (drag.mode === "pan") {
        const s = 2 * this.dist * Math.tan(this.fovy / 2) / c.clientHeight;
        const y = this.yaw * Math.PI / 180, p = this.pitch * Math.PI / 180;
        const fwd = [-Math.cos(p) * Math.sin(y), -Math.sin(p), -Math.cos(p) * Math.cos(y)];
        const right = norm(cross(fwd, [0, 1, 0])), up = cross(right, fwd);
        this.target = add(this.target, add(scale(right, -dx * s), scale(up, dy * s)));
      } else if (drag.mode === "zoom") {
        this.dist *= Math.exp(dy * 0.006);
      } else {
        this.yaw -= dx * 0.4;
        this.pitch = Math.max(-89.999, Math.min(89.999, this.pitch + dy * 0.4));
      }
      this.redraw();
    });
    c.addEventListener("pointerup", (e) => {
      if (this._gDrag) { this._gDrag = null; this.onSectionGizmo?.({ end: true }); this.redraw(); return; }
      if (captured) { captured = false; this.interceptor?.up(e, local(e)); return; }
      if (drag && !drag.moved && drag.button === 2) { drag = null; this.onContext(local(e), e); return; }
      if (drag && !drag.moved && drag.button === 0 && !this.interceptor) {
        const r = c.getBoundingClientRect();
        const pos = [e.clientX - r.left, e.clientY - r.top], i = this.pick(...pos);
        this.setSelected(i); this.onPick(i, pos, e);
      }
      drag = null;
    });
    c.addEventListener("auxclick", (e) => e.preventDefault());
    c.addEventListener("pointerleave", () => { if (!drag && !this.interceptor) { this.setHover(-1); this.onHover(-1); } });
    c.addEventListener("dblclick", (e) => this.interceptor?.dblclick?.(e, local(e)));
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      cancelAnimationFrame(this._anim);
      // カーソルの下の点が動かないように、注視点も寄せる
      const cam = this._camera(), pos = local(e), k = Math.exp(e.deltaY * 0.0015);
      const nx = (pos[0] / cam.cw) * 2 - 1, ny = 1 - (pos[1] / cam.ch) * 2;
      const hh = this.dist * Math.tan(this.fovy / 2), hw = hh * cam.cw / cam.ch;
      const off = add(scale(cam.right, nx * hw), scale(cam.up, ny * hh));
      this.target = add(this.target, scale(off, 1 - k));
      this.dist *= k;
      this.redraw();
    }, { passive: false });
  }

  // ------------------------------------------------------------ GL の下回り
  _program(vs, fs) {
    const gl = this.gl, p = gl.createProgram();
    for (const [type, src] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]]) {
      const s = gl.createShader(type);
      gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      gl.attachShader(p, s);
    }
    // 属性の番号を固定する（面とピックのプログラムが同じ VAO を使うので、使わない属性が最適化で消えても番号がずれないように）
    ["aPos", "aNor", "aFace", "aCol"].forEach((n, i) => gl.bindAttribLocation(p, i, n));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    p._u = {};
    return p;
  }

  _u(p, name) { return p._u[name] ?? (p._u[name] = this.gl.getUniformLocation(p, name)); }

  _attr(prog, name, data, size, owner = this.mesh) {
    const gl = this.gl, loc = gl.getAttribLocation(prog, name);
    const b = gl.createBuffer(); owner.bufs.push(b);
    gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
    if (loc < 0) return;
    gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0);
  }

  _lineVao(pos, color, colors = null) {
    const gl = this.gl, n = pos.length / 3, o = { vao: gl.createVertexArray(), count: n, bufs: [] };
    if (!colors) { colors = new Float32Array(n * 3); for (let i = 0; i < n; i++) colors.set(color, 3 * i); }
    gl.bindVertexArray(o.vao);
    this._attr(this.lineProg, "aPos", pos, 3, o); this._attr(this.lineProg, "aCol", colors, 3, o);
    gl.bindVertexArray(null);
    return o;
  }

  _makePickFbo(w, h) {
    const gl = this.gl;
    if (this._pickFbo) { gl.deleteFramebuffer(this._pickFbo.fbo); gl.deleteTexture(this._pickFbo.tex); gl.deleteRenderbuffer(this._pickFbo.rb); }
    const fbo = gl.createFramebuffer(), tex = gl.createTexture(), rb = gl.createRenderbuffer();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.bindRenderbuffer(gl.RENDERBUFFER, rb);
    gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT24, w, h);
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, rb);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    this._pickFbo = { fbo, tex, rb, w, h };
  }

  _free() {
    const gl = this.gl;
    for (const o of [this.mesh, this.lines, this.axes, this._cut]) {
      if (!o) continue;
      gl.deleteVertexArray(o.vao); o.bufs.forEach((b) => gl.deleteBuffer(b));
    }
    this.mesh = this.lines = this.axes = this._cut = null;
  }
}
