// 3D の選択と計測の計算（D55）。表示用メッシュ（app.mesh）だけを使う純粋な関数。
// - modelGeometry: エッジごとの折れ線・端点・直線か・円（中心・半径）と、スケッチのスナップ先（D54）
// - edgeLoop / tangentChain: ループ選択・正接の連続の選択（一般的な 3D CAD の右クリック「ループ選択」「正接の連続を選択」）
// - facesUnder: 視線が通る面を手前から（「別の選択」）
// - faceInfo / measure / massProps: 計測・質量特性
// 平面・直線・円は表示用メッシュでも正確（平面の三角形・直線の線分・円の点はどれも真の形の上にある）。
// それ以外の組み合わせの最短距離は、メッシュの頂点どうしの近似として「（近似）」を付けて出す。

const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const mul = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const len = (a) => Math.hypot(a[0], a[1], a[2]);
const unit = (a) => { const l = len(a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
const key = (p) => p.map((v) => Math.round(v * 1e4)).join(",");
const deg = (r) => (r * 180) / Math.PI;
/** 平行・垂直とみなす（表示用メッシュは float32 なので少し緩める）。 */
const PAR = 1e-5;
export const fmt = (v, d = 3) => (Math.abs(v) < 5e-10 ? "0" : String(+v.toFixed(d)));

/** エッジの形とスナップ先。edges[i] は app.mesh.edgeNames[i] に対応（形が取れないエッジは null）。メッシュごとに 1 回。 */
export function modelGeometry(m) {
  if (!m || !m.edgeNames?.length) return null;
  if (m._geo) return m._geo;
  const E = m.edges, segs = m.edgeNames.map(() => []);
  for (let k = 0; k < m.edgeOfSegment.length; k++)
    segs[m.edgeOfSegment[k]].push([[E[6 * k], E[6 * k + 1], E[6 * k + 2]], [E[6 * k + 3], E[6 * k + 4], E[6 * k + 5]]]);
  const edges = [], snaps = [], seen = new Set();
  m.edgeNames.forEach((name, ei) => {
    const S = segs[ei];
    if (!S.length) { edges.push(null); return; }
    const P = [S[0][0], S[0][1]], rest = S.slice(1);           // 線分をつないで折れ線に
    while (rest.length) {
      const t = P[P.length - 1];
      let bi = 0, bd = Infinity, flip = false;
      rest.forEach((s, i) => { const d0 = len(sub(s[0], t)), d1 = len(sub(s[1], t)); if (Math.min(d0, d1) < bd) { bd = Math.min(d0, d1); bi = i; flip = d1 < d0; } });
      const s = rest.splice(bi, 1)[0]; P.push(flip ? s[0] : s[1]);
    }
    const a = P[0], b = P[P.length - 1], L = len(sub(b, a)), closed = P.length > 2 && L < 1e-6;
    const straight = !closed && L > 1e-9 && P.every((p) => len(cross(sub(p, a), sub(b, a))) / L < 1e-5 * Math.max(1, L));
    let length = 0;
    for (let i = 1; i < P.length; i++) length += len(sub(P[i], P[i - 1]));
    const g = { name, index: ei, pts: P, a, b, closed, straight, length, circle: null };
    edges.push(g);
    const addSnap = (kind, w) => { const k = kind + key(w); if (!seen.has(k)) { seen.add(k); snaps.push({ src: `edge:${name}@${kind}`, kind, w, edge: ei }); } };
    if (!closed) { addSnap("vertex", a); addSnap("vertex", b); }
    if (straight) { addSnap("mid", mul(add(a, b), 0.5)); return; }
    const n = P.length - (closed ? 1 : 0);                     // 円・円弧（3 点を通る円）
    if (n < 3) return;
    const p0 = P[0], p1 = P[Math.floor(n / 3)], p2 = P[Math.floor((2 * n) / 3)];
    const ab = sub(p1, p0), ac = sub(p2, p0), nn = cross(ab, ac), q = dot(nn, nn);
    if (q < 1e-18) return;
    const t1 = cross(nn, ab), t2 = cross(ac, nn), A2 = dot(ac, ac), B2 = dot(ab, ab);
    const c = [0, 1, 2].map((i) => p0[i] + (t1[i] * A2 + t2[i] * B2) / (2 * q));
    const r = P.map((p) => len(sub(p, c))), rm = r.reduce((x, y) => x + y) / r.length;
    if (Math.max(...r) - Math.min(...r) < 1e-3 * rm) { g.circle = { c, r: rm, n: unit(nn) }; addSnap("center", c); }
  });
  return (m._geo = { edges: edges.filter(Boolean), byIndex: edges, snaps });
}

/** 端点での接線の向き（端から内向きではなく、エッジに沿って外へ向かう向き）。 */
function endTangent(g, atStart) {
  const P = g.pts;
  return atStart ? unit(sub(P[0], P[1])) : unit(sub(P[P.length - 1], P[P.length - 2]));
}

/** エッジ ei を含む、面 face の境界のループ（エッジ番号の配列）。face が無ければ ei の最初の面。 */
export function edgeLoop(m, ei, face = -1) {
  const G = modelGeometry(m); if (!G) return [ei];
  const faces = m.edgeFaces[ei] || [];
  const f = faces.includes(face) ? face : faces[0];
  if (f === undefined) return [ei];
  const onFace = m.edgeNames.map((_, i) => i).filter((i) => G.byIndex[i] && (m.edgeFaces[i] || []).includes(f));
  const out = new Set([ei]), todo = [ei];
  while (todo.length) {
    const g = G.byIndex[todo.pop()]; if (!g || g.closed) continue;
    for (const j of onFace) {
      if (out.has(j)) continue;
      const h = G.byIndex[j];
      if (h.closed) continue;
      if ([key(g.a), key(g.b)].some((k) => k === key(h.a) || k === key(h.b))) { out.add(j); todo.push(j); }
    }
  }
  return [...out];
}

/** エッジ ei から、なめらか（接線が 1° 以内で続く）につながるエッジ（フィレットを掛けるときの「正接の連続」）。 */
export function tangentChain(m, ei) {
  const G = modelGeometry(m); if (!G) return [ei];
  const out = new Set([ei]), todo = [ei];
  while (todo.length) {
    const g = G.byIndex[todo.pop()]; if (!g || g.closed) continue;
    for (const [pt, tg] of [[g.a, endTangent(g, true)], [g.b, endTangent(g, false)]]) {
      for (const h of G.edges) {
        if (out.has(h.index) || h.closed) continue;
        let th = null;
        if (key(h.a) === key(pt)) th = endTangent(h, true);
        else if (key(h.b) === key(pt)) th = endTangent(h, false);
        if (th && dot(tg, th) < -Math.cos((1 * Math.PI) / 180)) { out.add(h.index); todo.push(h.index); }
      }
    }
  }
  return [...out];
}

/** 視線 {o, d} が通る面（手前から順）。[{face, t}]。 */
export function facesUnder(m, ray) {
  const P = m.positions, I = m.indices, best = new Map();
  for (let t = 0; t < m.faceOfTriangle.length; t++) {
    const A = [P[3 * I[3 * t]], P[3 * I[3 * t] + 1], P[3 * I[3 * t] + 2]];
    const B = [P[3 * I[3 * t + 1]], P[3 * I[3 * t + 1] + 1], P[3 * I[3 * t + 1] + 2]];
    const C = [P[3 * I[3 * t + 2]], P[3 * I[3 * t + 2] + 1], P[3 * I[3 * t + 2] + 2]];
    const e1 = sub(B, A), e2 = sub(C, A), p = cross(ray.d, e2), det = dot(e1, p);
    if (Math.abs(det) < 1e-12) continue;
    const s = sub(ray.o, A), u = dot(s, p) / det; if (u < 0 || u > 1) continue;
    const q = cross(s, e1), v = dot(ray.d, q) / det; if (v < 0 || u + v > 1) continue;
    const d = dot(e2, q) / det; if (d <= 0) continue;
    const f = m.faceOfTriangle[t];
    if (!best.has(f) || d < best.get(f)) best.set(f, d);
  }
  return [...best].map(([face, t]) => ({ face, t })).sort((a, b) => a.t - b.t);
}

/** 面の情報: 面積、平面なら法線と面上の点、円筒らしければ半径（境界の円エッジから）。 */
export function faceInfo(m, fi) {
  const P = m.positions, I = m.indices;
  let area = 0, n0 = null, planar = true, pt = null;
  const verts = [];
  for (let t = 0; t < m.faceOfTriangle.length; t++) {
    if (m.faceOfTriangle[t] !== fi) continue;
    const v = [0, 1, 2].map((k) => [P[3 * I[3 * t + k]], P[3 * I[3 * t + k] + 1], P[3 * I[3 * t + k] + 2]]);
    verts.push(...v);
    const c = cross(sub(v[1], v[0]), sub(v[2], v[0])), a = len(c) / 2;
    area += a;
    if (a < 1e-14) continue;
    const n = unit(c);
    if (!n0) { n0 = n; pt = v[0]; } else if (dot(n, n0) < 1 - 1e-6) planar = false;
  }
  if (planar && n0) planar = verts.every((v) => Math.abs(dot(sub(v, pt), n0)) < 1e-5 * Math.max(1, len(sub(v, pt))));
  const G = modelGeometry(m);
  const circles = G ? G.edges.filter((g) => g.circle && (m.edgeFaces[g.index] || []).includes(fi)).map((g) => g.circle) : [];
  const cyl = !planar && circles.length && circles.every((c) => Math.abs(c.r - circles[0].r) < 1e-4 * c.r) ? circles[0].r : null;
  return { area, planar: planar && !!n0, normal: n0, point: pt, verts, radius: cyl, axisDir: cyl ? circles[0].n : null, axisPt: cyl ? circles[0].c : null };
}

/** 選んだもの（面・エッジ・頂点）の計測。rows = 表示する [名前, 値]、lines = 3D に描く寸法線 [{a, b, label}]。
 *  item: {kind: "face", i} / {kind: "edge", i} / {kind: "vertex", w}。 */
export function measure(m, items) {
  const G = modelGeometry(m), rows = [], lines = [];
  const R = (k, v) => rows.push([k, v]);
  const xyz = (p) => `(${fmt(p[0])}, ${fmt(p[1])}, ${fmt(p[2])})`;
  if (!items.length) return { rows, lines };
  if (items.length === 1) {
    const it = items[0];
    if (it.kind === "vertex") R("座標", xyz(it.w));
    if (it.kind === "edge") {
      const g = G.byIndex[it.i];
      R("長さ", `${fmt(g.length)} mm`);
      if (g.circle) { R("半径", `${fmt(g.circle.r)} mm`); R("直径", `${fmt(2 * g.circle.r)} mm`); R("中心", xyz(g.circle.c)); }
      if (g.straight) R("向き", xyz(unit(sub(g.b, g.a))));
    }
    if (it.kind === "face") {
      const f = faceInfo(m, it.i);
      R("面積", `${fmt(f.area)} mm²`);
      if (f.planar) R("法線", xyz(f.normal));
      if (f.radius) { R("半径（円筒）", `${fmt(f.radius)} mm`); R("直径（円筒）", `${fmt(2 * f.radius)} mm`); }
    }
    return { rows, lines };
  }
  if (items.length > 2) {                                      // 3 つ以上: 合計
    const L = items.filter((x) => x.kind === "edge").reduce((s, x) => s + G.byIndex[x.i].length, 0);
    const A = items.filter((x) => x.kind === "face").reduce((s, x) => s + faceInfo(m, x.i).area, 0);
    if (L) R("エッジの長さの合計", `${fmt(L)} mm`);
    if (A) R("面積の合計", `${fmt(A)} mm²`);
    R("", "距離・角度は 2 つ選んだときに出します");
    return { rows, lines };
  }
  // 2 つ: 形に合わせて（中心間・垂直距離・平行面の距離・角度）
  const geo = (it) => {
    if (it.kind === "vertex") return { t: "point", p: it.w, pts: [it.w] };
    if (it.kind === "edge") {
      const g = G.byIndex[it.i];
      if (g.circle) return { t: "circle", c: g.circle.c, r: g.circle.r, n: g.circle.n, pts: g.pts };
      if (g.straight) return { t: "line", a: g.a, b: g.b, d: unit(sub(g.b, g.a)), pts: g.pts };
      return { t: "curve", pts: g.pts };
    }
    const f = faceInfo(m, it.i);
    if (f.planar) return { t: "plane", p: f.point, n: f.normal, pts: f.verts };
    if (f.radius) return { t: "cyl", c: f.axisPt, d: f.axisDir, r: f.radius, pts: f.verts };
    return { t: "surface", pts: f.verts };
  };
  let A = geo(items[0]), B = geo(items[1]);
  const order = ["point", "circle", "line", "cyl", "plane", "curve", "surface"];
  if (order.indexOf(A.t) > order.indexOf(B.t)) [A, B] = [B, A];
  const center = (g) => (g.t === "point" ? g.p : g.t === "circle" ? g.c : null);
  const footLine = (p, a, d) => add(a, mul(d, dot(sub(p, a), d)));
  const footPlane = (p, q, n) => sub(p, mul(n, dot(sub(p, q), n)));
  const dist = (a, b, label = "距離") => { const d = len(sub(b, a)); R(label, `${fmt(d)} mm`); lines.push({ a, b, label: fmt(d) }); return d; };
  const angle = (u, v, label = "角度") => {
    const c = Math.min(1, Math.abs(dot(unit(u), unit(v))));
    R(label, `${fmt(deg(Math.acos(c)), 2)}°`);
  };
  const ca = center(A), cb = center(B);
  if (ca && cb) {
    dist(ca, cb, A.t === "circle" || B.t === "circle" ? "中心間の距離" : "距離");
    const d = sub(cb, ca); R("dX / dY / dZ", `${fmt(d[0])} / ${fmt(d[1])} / ${fmt(d[2])}`);
  } else if (ca && B.t === "line") dist(ca, footLine(ca, B.a, B.d), "垂直距離");
  else if (ca && B.t === "plane") dist(ca, footPlane(ca, B.p, B.n), "垂直距離");
  else if (ca && B.t === "cyl") { const f = footLine(ca, B.c, B.d); dist(ca, f, "軸までの距離"); }
  else if (A.t === "line" && B.t === "line") {
    if (len(cross(A.d, B.d)) < PAR) dist(A.a, footLine(A.a, B.a, B.d), "平行な線の距離");
    else { angle(A.d, B.d); approxMin(A.pts, B.pts); }
  } else if (A.t === "line" && B.t === "plane") {
    if (Math.abs(dot(A.d, B.n)) < PAR) dist(A.a, footPlane(A.a, B.p, B.n), "平行な距離");
    else angle(A.d, B.n, "面の法線との角度");
  } else if (A.t === "plane" && B.t === "plane") {
    if (len(cross(A.n, B.n)) < PAR) dist(A.p, footPlane(A.p, B.p, B.n), "平行な面の距離");
    else { R("角度", `${fmt(deg(Math.acos(Math.max(-1, Math.min(1, -dot(A.n, B.n))))), 2)}°`); }
  } else if (A.t === "cyl" && B.t === "cyl" && len(cross(A.d, B.d)) < PAR) dist(A.c, footLine(A.c, B.c, B.d), "軸間の距離");
  else if (A.t === "line" && B.t === "cyl" && len(cross(A.d, B.d)) < PAR) dist(A.a, footLine(A.a, B.c, B.d), "軸までの距離");
  else if (A.t === "cyl" && B.t === "plane" && Math.abs(dot(A.d, B.n)) < PAR) dist(A.c, footPlane(A.c, B.p, B.n), "軸から面までの距離");
  else approxMin(A.pts, B.pts);
  return { rows, lines };

  function approxMin(P, Q) {                                   // メッシュの点どうしの最短（近似）
    let best = Infinity, pa = null, pb = null;
    const step = Math.max(1, Math.floor((P.length * Q.length) / 4e6));
    for (let i = 0; i < P.length; i += step) for (const q of Q) { const d = len(sub(P[i], q)); if (d < best) { best = d; pa = P[i]; pb = q; } }
    if (pa) { R("最短距離（近似）", `${fmt(best)} mm`); lines.push({ a: pa, b: pb, label: `≈${fmt(best)}` }); }
  }
}

/** 質量特性（密度 1 の体積として）: 体積・表面積・重心・外形寸法。閉じたメッシュの符号付き四面体の和。 */
export function massProps(m) {
  const P = m.positions, I = m.indices;
  let V = 0, area = 0;
  const C = [0, 0, 0], lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (let t = 0; t < I.length / 3; t++) {
    const v = [0, 1, 2].map((k) => [P[3 * I[3 * t + k]], P[3 * I[3 * t + k] + 1], P[3 * I[3 * t + k] + 2]]);
    const vol = dot(v[0], cross(v[1], v[2])) / 6;
    V += vol;
    for (let i = 0; i < 3; i++) C[i] += (vol * (v[0][i] + v[1][i] + v[2][i])) / 4;
    area += len(cross(sub(v[1], v[0]), sub(v[2], v[0]))) / 2;
  }
  for (let i = 0; i < P.length; i += 3) for (let k = 0; k < 3; k++) { lo[k] = Math.min(lo[k], P[i + k]); hi[k] = Math.max(hi[k], P[i + k]); }
  return { volume: V, area, centroid: Math.abs(V) > 1e-12 ? mul(C, 1 / V) : null, size: sub(hi, lo) };
}
