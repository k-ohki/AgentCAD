// アセンブリ画面: 部品の表示（部品ごとの変換をかけて 1 つのメッシュにまとめる）、面の選択、合致の作成、部品のドラッグ。
// 合致の計算はすべて Python 側。ここは操作を送り、返ってきた状態を描く。

export const MATES = [
  ["coincident", "一致"], ["parallel", "平行"], ["perpendicular", "直角"], ["distance", "距離"],
  ["angle", "角度"], ["concentric", "同心"], ["tangent", "正接"], ["lock", "ロック"],
];
const STATE_LABEL = { ok: "", conflict: "矛盾", redundant: "冗長", flipped: "向きが逆", suppressed: "抑制" };

/** 行優先 4×4（numpy の ravel）で点を変換する。 */
function xf(m, x, y, z, w = 1) {
  return [m[0] * x + m[1] * y + m[2] * z + m[3] * w, m[4] * x + m[5] * y + m[6] * z + m[7] * w,
    m[8] * x + m[9] * y + m[10] * z + m[11] * w];
}

const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
/** 行優先 4×4 の積。 */
const mul4 = (a, b) => Array.from({ length: 16 }, (_, i) => [0, 1, 2, 3].reduce((s, k) => s + a[(i & ~3) + k] * b[4 * k + (i & 3)], 0));
/** 軸 {origin, dir} のまわりに角度 t（ラジアン）回す行優先 4×4。 */
function rotAbout({ origin: o, dir }, t) {
  const L = Math.hypot(...dir), [x, y, z] = dir.map((v) => v / L), c = Math.cos(t), s = Math.sin(t), C = 1 - c;
  const R = [[c + x * x * C, x * y * C - z * s, x * z * C + y * s], [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
    [z * x * C - y * s, z * y * C + x * s, c + z * z * C]];
  const T = [0, 1, 2].map((i) => o[i] - dot(R[i], o));
  return [...R[0], T[0], ...R[1], T[1], ...R[2], T[2], 0, 0, 0, 1];
}

export class AsmEditor {
  constructor(viewer, { rpc, onState, toast, decode, onSelection }) {
    this.v = viewer; this.rpc = rpc; this.onState = onState; this.toast = toast; this.decode = decode;
    this.onSelection = onSelection;
    this.st = null; this.parts = {}; this.sel = []; this.faceRef = []; this.drag = null;
    // 合致の PropertyManager（一般的な 3D CAD と同じ手順）: mateMode の間、面のクリックは合致の相手を選ぶ。
    // pendingMate = 付けて部品を動かして見せている、まだ ✓ を押していない合致の ID
    this.mateMode = false; this.pendingMate = null; this.onMatePick = null;
    // 「動かしてみる」モード（D70）: 部品 → 回転軸 {origin, dir}。合致は解かず、画面の中で軸のまわりに回すだけ
    this.motion = null;
  }

  /** 「動かしてみる」を始める / やめる（やめると元の姿勢に戻す。文書は変えない）。 */
  async startMotion() {
    const joints = await this.rpc("asm.joints");
    this.motion = { joints: Object.fromEntries(joints.map((j) => [j.comp, j])),
      saved: this.st.components.map((c) => [c.id, c.matrix.slice()]) };
    return joints.length;
  }

  stopMotion() {
    if (!this.motion) return;
    for (const [id, m] of this.motion.saved) Object.assign(this.comp(id) || {}, { matrix: m });
    this.motion = null; this.rebuild();
  }

  /** 軸に垂直な平面の上で、カーソルが軸のまわりのどの角度にあるか（視線が平面とほぼ平行なら横の移動量）。 */
  _axisAngle(j, pos) {
    const r = this.v.ray(...pos), n = j.dir, o = j.origin, dn = dot(r.d, n);
    if (Math.abs(dn) < 0.2) return pos[0] * 0.01;
    const t = dot(sub(o, r.o), n) / dn, p = sub(r.o.map((v, k) => v + t * r.d[k]), o);
    const u = Math.abs(n[0]) < 0.9 ? cross(n, [1, 0, 0]) : cross(n, [0, 1, 0]), w = cross(n, u);
    return Math.atan2(dot(p, w), dot(p, u));
  }

  get active() { return !!this.st; }

  /** 状態を受け取る。parts（部品のメッシュ）が含まれていれば差し替える。 */
  set(st, { first = false } = {}) {
    this.st = st; this.motion = null;                            // 新しい状態が来たら「動かしてみる」は終わり
    if (!st) { if (this.v.interceptor === this) this.v.interceptor = null; return; }
    if (st.parts) this.parts = Object.fromEntries(Object.entries(st.parts).map(([k, m]) => [k, m && this.decode(m)]));
    const ids = new Set(st.components.map((c) => c.id));
    this.sel = this.sel.filter((s) => ids.has(s.comp));
    this.v.interceptor = this;
    const refit = first || st.components.length !== this._nComps;     // 部品が増減したら全体を見せる
    this._nComps = st.components.length;
    this.rebuild({ keepView: !refit });
    if (refit && this.faceRef.length) this.v.setView("iso");
    this.onSelection?.();
  }

  /** 部品ごとのメッシュに変換をかけて 1 つにまとめる。面番号 → [部品, 面の永続名] の表も作る。 */
  rebuild({ keepView = true } = {}) {
    const P = [], I = [], F = [], E = [], refs = [], C = [];
    let off = 0, lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (const c of this.st.components) {
      const m = this.parts[c.source]; if (!m) continue;
      const M = c.matrix, base = refs.length;
      for (let i = 0; i < m.positions.length; i += 3) {
        const p = xf(M, m.positions[i], m.positions[i + 1], m.positions[i + 2]);
        P.push(...p);
        for (let k = 0; k < 3; k++) { lo[k] = Math.min(lo[k], p[k]); hi[k] = Math.max(hi[k], p[k]); }
      }
      for (const i of m.indices) I.push(i + off);
      for (const f of m.faceOfTriangle) F.push(f + base);
      for (let i = 0; i < m.edges.length; i += 3) E.push(...xf(M, m.edges[i], m.edges[i + 1], m.edges[i + 2]));
      m.faceNames.forEach((n, i) => { refs.push([c.id, n]); C.push(m.faceColors?.[i] || m.partColor || null); });
      off += m.positions.length / 3;
    }
    this.faceRef = refs;
    if (!refs.length) { this.v.setMesh(null); return; }
    this.v.setMesh({ positions: new Float32Array(P), indices: new Uint32Array(I), faceOfTriangle: new Uint32Array(F),
      edges: new Float32Array(E), bbox: [lo, hi], faceColors: C }, { keepView });
    this._highlight();
  }

  _highlight() {
    const idx = this.sel.map((s) => this.faceRef.findIndex(([c, n]) => c === s.comp && n === s.face));
    this.v.setSelected(idx[0] ?? -1, idx[1] ?? -1);
  }

  comp(id) { return this.st.components.find((c) => c.id === id); }

  // ------------------------------------------------------------ 入力（viewer.interceptor）
  down(e, pos) {
    const f = this.v.pick(...pos);
    if (f < 0) { this.drag = null; return false; }                // 何もない所 → 回転
    const [comp] = this.faceRef[f];
    const c = this.comp(comp);
    if (this.motion) {
      const j = this.motion.joints[comp];
      if (!j) { this.toast("この部品には回転軸（同心円の合致）が無いので動かせません"); this.drag = null; return true; }
      this.drag = { spin: j, comp, m0: c.matrix.slice(), a0: this._axisAngle(j, pos) };
      return true;
    }
    const r = this.v.ray(...pos), fwd = this.v.cam.fwd;
    // 掴んだ位置: 部品の原点を通り、視線に垂直な平面との交点
    const o = c.position, t = ((o[0] - r.o[0]) * fwd[0] + (o[1] - r.o[1]) * fwd[1] + (o[2] - r.o[2]) * fwd[2])
      / (r.d[0] * fwd[0] + r.d[1] * fwd[1] + r.d[2] * fwd[2]);
    this.drag = { f, comp, fixed: c.fixed, start: c.position.slice(), grab: [0, 1, 2].map((k) => r.o[k] + t * r.d[k]),
      origin: o.slice(), moved: false, busy: false, next: null, x0: pos[0], y0: pos[1] };
    return true;
  }

  move(e, pos) {
    const d = this.drag;
    if (!d) {                                                     // ホバー
      const f = this.v.pick(...pos);
      this.v.setHover(f);
      this.onHover?.(f >= 0 ? this.faceRef[f] : null);
      return;
    }
    if (d.spin) {                                                 // 軸のまわりに回すだけ（サーバも合致も使わない）
      this.comp(d.comp).matrix = mul4(rotAbout(d.spin, this._axisAngle(d.spin, pos) - d.a0), d.m0);
      if (!this._raf) this._raf = requestAnimationFrame(() => { this._raf = 0; this.rebuild(); });
      return;
    }
    if (Math.hypot(pos[0] - d.x0, pos[1] - d.y0) > 3) d.moved = true;
    if (!d.moved || d.fixed) return;
    const r = this.v.ray(...pos), fwd = this.v.cam.fwd, o = d.origin;
    const t = ((o[0] - r.o[0]) * fwd[0] + (o[1] - r.o[1]) * fwd[1] + (o[2] - r.o[2]) * fwd[2])
      / (r.d[0] * fwd[0] + r.d[1] * fwd[1] + r.d[2] * fwd[2]);
    const hit = [0, 1, 2].map((k) => r.o[k] + t * r.d[k]);
    d.next = d.last = [0, 1, 2].map((k) => d.start[k] + hit[k] - d.grab[k]);
    this._pump();
  }

  async _pump() {
    const d = this.drag;
    if (!d || d.busy || !d.next) return;
    d.busy = true;
    const p = d.next; d.next = null;
    try {
      const r = await this.rpc("asm.drag", { comp: d.comp, position: p });
      for (const c of r.components) Object.assign(this.comp(c.id) || {}, { matrix: c.matrix });
      this.rebuild();
    } catch (err) { this.toast(err.message, true); }
    d.busy = false;
    if (this.drag === d && d.next) this._pump();
  }

  async up(e) {
    const d = this.drag; this.drag = null;
    if (!d || d.spin) return;
    if (!d.moved) {
      if (this.mateMode && this.onMatePick) { const [comp, face] = this.faceRef[d.f]; this.onMatePick({ comp, face }, [d.x0, d.y0]); return; }
      this._clickFace(d.f, e.ctrlKey); return;
    }
    if (d.fixed) { this.toast("固定された部品は動かせません（部品の一覧で固定を外せます）", true); return; }
    while (d.busy) await new Promise((r) => setTimeout(r, 5));
    if (!d.last) return;
    const st = await this._send("asm.drag", { comp: d.comp, position: d.last, final: true });
    if (st) this.onState(st);
  }

  _clickFace(f, add) {
    const [comp, face] = this.faceRef[f];
    const i = this.sel.findIndex((s) => s.comp === comp && s.face === face);
    if (i >= 0) this.sel.splice(i, 1);
    else {
      if (!add && this.sel.length >= 2) this.sel = [];
      this.sel = this.sel.filter((s) => s.comp !== comp || add);     // 同じ部品の面は置き換える（合致は別の部品どうし）
      this.sel.push({ comp, face });
      if (this.sel.length > 2) this.sel.shift();
    }
    this._highlight();
    this.onSelection?.();
  }

  clearSelection() { this.sel = []; this._highlight(); this.onSelection?.(); }

  async mate(type, value) {
    if (type === "lock") {
      const comps = [...new Set(this.sel.map((s) => s.comp))];
      if (comps.length !== 2) { this.toast("2 つの部品の面を 1 つずつ選んでください", true); return; }
      return this._apply("asm.mate", { type, a: [comps[0], ""], b: [comps[1], ""] });
    }
    if (this.sel.length !== 2 || this.sel[0].comp === this.sel[1].comp) {
      this.toast("別々の部品の面を 2 つ選んでください", true); return;
    }
    const params = { type, a: [this.sel[0].comp, this.sel[0].face], b: [this.sel[1].comp, this.sel[1].face] };
    if (value !== undefined && value !== null && value !== "") params.value = Number(value);
    return this._apply("asm.mate", params);
  }

  async _apply(method, params) {
    const st = await this._send(method, params);
    if (st) { this.sel = []; this.onState(st); }
    return st;
  }

  async _send(method, params) {
    try { return await this.rpc(method, params); } catch (err) { this.toast(err.message, true); return null; }
  }
}

export { STATE_LABEL };
