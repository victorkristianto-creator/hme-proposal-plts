/* PLTS Roof Designer — penempatan PV manual di atas model 3D WebODM.
   Koordinat data (SITE): ENU lokal dalam meter — x = timur, y = utara, z = atas (tanah/jalan ≈ 0).
   three.js: X = x, Y = z, Z = -y  (utara = -Z).  Model GLB sudah dalam frame three.js ini. */
'use strict';
const $ = id => document.getElementById(id);
const SITE = JSON.parse($('site-data').textContent.trim().startsWith('@@') ? '{}' : $('site-data').textContent);
const GLB_TXT = $('glb-data').textContent.trim();
const HAS_GLB = GLB_TXT && !GLB_TXT.startsWith('@@');
SITE.id = SITE.id || 'kosong';
SITE.name = SITE.name || 'PLTS Roof Designer';
SITE.lat = SITE.lat ?? -7.29; SITE.lng = SITE.lng ?? 112.75; SITE.tz = SITE.tz ?? 7;
SITE.fit = SITE.fit || { center: [0, 0, 4], radius: 12 };
SITE.planes = SITE.planes || [];
SITE.obstacles = SITE.obstacles || [];
const BEAM_SHARE = SITE.beamShare ?? 0.55;      // porsi sinar langsung dari energi tahunan (Surabaya ±55%)
const Y_HORIZ = SITE.yieldHorizontal ?? 1300;   // kWh/kWp/th bidang datar, untuk estimasi bila PVGIS tidak ada
const ENU = (x, y, z) => new THREE.Vector3(x, z, -y);
const toENU = v => [v.x, -v.z, v.y];
const fmt = (n, d = 0) => Number(n).toLocaleString('id-ID', { minimumFractionDigits: d, maximumFractionDigits: d });
const D2R = Math.PI / 180;
const PALETTE = ['#38bdf8', '#f472b6', '#a3e635', '#fb923c', '#c084fc', '#facc15', '#2dd4bf', '#f87171'];

// ---------- Tipe modul ----------
const MODULE_TYPES = SITE.moduleTypes || [
  { id: 'trina720', name: 'Trina Vertex N 720 Wp (NEG21C.20)', L: 2384, W: 1303, T: 33, wp: 720 },
  { id: 'trina620', name: 'Trina Vertex N 620 Wp (NEG19RC.20)', L: 2382, W: 1134, T: 30, wp: 620 },
  { id: 'jinko580', name: 'Jinko Tiger Neo 580 Wp (72HL4)', L: 2278, W: 1134, T: 30, wp: 580 },
  { id: 'longi580', name: 'LONGi Hi-MO 6 580 Wp (72HTH)', L: 2278, W: 1134, T: 30, wp: 580 },
  { id: 'custom', name: 'Ukuran sendiri…', L: 2000, W: 1000, T: 35, wp: 450 },
];

// ---------- State (disimpan) ----------
const LS_KEY = 'plts-designer:' + SITE.id;
function defaultState() {
  const v = (SITE.variants && SITE.variants.length) ? SITE.variants : [{ name: 'Varian 1', modules: [] }];
  return {
    v: 2, active: 0, orient: 'P', snap: true,
    module: JSON.parse(JSON.stringify(MODULE_TYPES.find(m => m.id === (SITE.defaultModule || 'trina720')) || MODULE_TYPES[0])),
    gap: 20,
    variants: v.map(x => ({ name: x.name, modules: (x.modules || []).map(m => ({ ...m })) })),
    planeOverrides: {},   // id -> {setback, yield}
    userPlanes: [],       // bidang buatan pengguna
    northRot: 0, zUp: false,
  };
}
let S = defaultState();
try { const saved = JSON.parse(localStorage.getItem(LS_KEY)); if (saved && saved.v === 2) S = Object.assign(defaultState(), saved); } catch (e) {}
let saveTimer = null;
function save() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    try { localStorage.setItem(LS_KEY, JSON.stringify({ ...S, variants: S.variants.map(v => ({ name: v.name, modules: v.modules.map(stripMod) })) })); $('saveNote').textContent = 'Tersimpan otomatis di browser ini · ' + new Date().toLocaleTimeString('id-ID'); }
    catch (e) { $('saveNote').textContent = 'Tidak bisa menyimpan otomatis di browser ini — pakai Ekspor layout.'; }
  }, 300);
}
const V = () => S.variants[S.active];

// ---------- Bidang atap ----------
let PLANES = [];
function makePlane(src, idx) {
  // src: {id,name,color, poly:[[x,y,z]..]} ATAU {plane:{a,b,c}, poly:[[x,y]..]}  (z = a x + b y + c)
  let pts;
  if (src.plane) pts = src.poly.map(([x, y]) => [x, y, src.plane.a * x + src.plane.b * y + src.plane.c]);
  else pts = src.poly.map(p => p.slice(0, 3));
  // fit bidang (least squares z = ax+by+c, atau pakai src.plane)
  let a, b, c;
  if (src.plane) ({ a, b, c } = src.plane);
  else ({ a, b, c } = fitPlane(pts));
  pts = pts.map(([x, y]) => [x, y, a * x + b * y + c]);
  const n = new THREE.Vector3(-a, -b, 1).normalize();              // ENU
  let vdir = new THREE.Vector3(-n.y, n.x, 0);                       // horizontal, sepanjang lisplank
  if (vdir.length() < 1e-6) vdir.set(1, 0, 0); vdir.normalize();
  const u = new THREE.Vector3().crossVectors(n, vdir).normalize();  // naik lereng
  if (u.z < 0) { u.negate(); vdir.negate(); }
  const o = new THREE.Vector3(...pts[0]);
  let st = pts.map(p => { const d = new THREE.Vector3(...p).sub(o); return [d.dot(u), d.dot(vdir)]; });
  // sejajarkan grid dengan lisplank: tepi bawah terpanjang yang hampir datar (bukan horizontal murni)
  if (tilt0(n) > 0.05 && src.alignEave !== false) {
    let best = null;
    for (let i = 0; i < st.length; i++) {
      const A = st[i], B = st[(i + 1) % st.length], L = Math.hypot(B[0] - A[0], B[1] - A[1]);
      if (L < 1.5 || Math.abs(B[0] - A[0]) / L > 0.15) continue;
      const ms = (A[0] + B[0]) / 2;
      if (!best || ms < best.ms - 0.3 || (Math.abs(ms - best.ms) <= 0.3 && L > best.L)) best = { i, ms, L };
    }
    if (best) {
      const A = new THREE.Vector3(...pts[best.i]), B = new THREE.Vector3(...pts[(best.i + 1) % pts.length]);
      const e = B.sub(A).normalize(); if (e.dot(vdir) < 0) e.negate();
      vdir.copy(e); u.crossVectors(n, vdir).normalize(); if (u.z < 0) { u.negate(); vdir.negate(); }
      st = pts.map(p => { const d = new THREE.Vector3(...p).sub(o); return [d.dot(u), d.dot(vdir)]; });
    }
  }
  const tilt = Math.acos(Math.min(1, n.z));
  const az = tilt < 0.01 ? 180 : ((Math.atan2(n.x, n.y) / D2R) + 360) % 360;
  const ov = S.planeOverrides[src.id] || {};
  const p = {
    id: src.id, name: src.name || src.id, color: src.color || PALETTE[idx % PALETTE.length], user: !!src.user,
    a, b, c, n, u, v: vdir, o, pts, st, tilt, az,
    setback: ov.setback ?? src.setback ?? SITE.setback ?? 0.25,
    yieldSrc: src.yield, yieldNote: src.yieldNote || (src.yield ? 'PVGIS' : ''),
    obsSetback: src.obsSetback,
  };
  p.sMin = Math.min(...st.map(q => q[0])); p.sMax = Math.max(...st.map(q => q[0]));
  p.tMin = Math.min(...st.map(q => q[1])); p.tMax = Math.max(...st.map(q => q[1]));
  p.yield = ov.yield ?? src.yield ?? estimateYield(p);
  if (!src.yield && ov.yield == null) p.yieldNote = 'estimasi';
  if (ov.yield != null) p.yieldNote = 'manual';
  // halangan → kotak di koordinat (s,t)
  p.obs = SITE.obstacles.map(ob => {
    const q = ob.poly.map(([x, y]) => { const z = c + a * x + b * y; const d = new THREE.Vector3(x, y, z).sub(o); return [d.dot(u), d.dot(vdir)]; });
    const sb = ob.setback ?? 0.4;
    return { name: ob.name, s0: Math.min(...q.map(r => r[0])) - sb, s1: Math.max(...q.map(r => r[0])) + sb, t0: Math.min(...q.map(r => r[1])) - sb, t1: Math.max(...q.map(r => r[1])) + sb, poly: q };
  }).filter(r => r.s1 > p.sMin && r.s0 < p.sMax && r.t1 > p.tMin && r.t0 < p.tMax && obstacleNearPlane(p, r));
  return p;
}
const tilt0 = n => Math.acos(Math.min(1, n.z));
function obstacleNearPlane(p, r) { // halangan dihitung hanya bila tapaknya menyentuh poligon bidang
  return r.poly.some(q => pip(p.st, q[0], q[1])) || p.st.some(q => q[0] > r.s0 && q[0] < r.s1 && q[1] > r.t0 && q[1] < r.t1) ||
    pip(p.st, (r.s0 + r.s1) / 2, (r.t0 + r.t1) / 2);
}
function fitPlane(pts) {
  let sxx = 0, sxy = 0, syy = 0, sx = 0, sy = 0, sz = 0, sxz = 0, syz = 0; const N = pts.length;
  for (const [x, y, z] of pts) { sxx += x * x; sxy += x * y; syy += y * y; sx += x; sy += y; sz += z; sxz += x * z; syz += y * z; }
  const M = [[sxx, sxy, sx], [sxy, syy, sy], [sx, sy, N]], r = [sxz, syz, sz];
  const det = m => m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
  const D = det(M); if (Math.abs(D) < 1e-9) return { a: 0, b: 0, c: sz / N };
  const col = (i) => M.map((row, k) => row.map((v, j) => j === i ? r[k] : v));
  return { a: det(col(0)) / D, b: det(col(1)) / D, c: det(col(2)) / D };
}
const P3 = (p, s, t, h = 0) => p.o.clone().addScaledVector(p.u, s).addScaledVector(p.v, t).addScaledVector(p.n, h); // ENU
const P3T = (p, s, t, h) => { const e = P3(p, s, t, h); return ENU(e.x, e.y, e.z); };
function rebuildPlanes() {
  PLANES = [...SITE.planes, ...S.userPlanes.map(x => ({ ...x, user: true }))].map(makePlane);
}

// ---------- Geometri 2D ----------
function pip(poly, x, y) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
function distSeg(px, py, [ax, ay], [bx, by]) {
  const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
  const k = l2 ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / l2)) : 0;
  return Math.hypot(px - ax - k * dx, py - ay - k * dy);
}
const modDims = (o) => { const L = S.module.L / 1000, W = S.module.W / 1000; return o === 'L' ? [W, L] : [L, W]; }; // [sepanjang lereng, sepanjang lisplank]
function rectOf(m) { const [ds, dt] = modDims(m.o); return { s0: m.s - ds / 2, s1: m.s + ds / 2, t0: m.t - dt / 2, t1: m.t + dt / 2 }; }
// Alasan tidak valid ('' = valid)
function checkModule(p, m, others) {
  const r = rectOf(m), sb = p.setback;
  const corners = [[r.s0, r.t0], [r.s1, r.t0], [r.s1, r.t1], [r.s0, r.t1]];
  for (const [s, t] of corners) {
    if (!pip(p.st, s, t)) return 'keluar bidang';
    for (let i = 0; i < p.st.length; i++) if (distSeg(s, t, p.st[i], p.st[(i + 1) % p.st.length]) < sb - 1e-4) return `setback < ${fmt(sb, 2)} m`;
  }
  for (const q of p.st) if (q[0] > r.s0 - sb + 1e-4 && q[0] < r.s1 + sb - 1e-4 && q[1] > r.t0 - sb + 1e-4 && q[1] < r.t1 + sb - 1e-4) return `setback < ${fmt(sb, 2)} m`;
  for (const ob of p.obs) if (r.s1 > ob.s0 && r.s0 < ob.s1 && r.t1 > ob.t0 && r.t0 < ob.t1) return 'kena ' + (ob.name || 'halangan');
  for (const o of others) {
    if (o === m || o.p !== m.p) continue;
    const q = rectOf(o);
    if (r.s1 > q.s0 + 0.002 && r.s0 < q.s1 - 0.002 && r.t1 > q.t0 + 0.002 && r.t0 < q.t1 - 0.002) return 'tumpang tindih';
  }
  return '';
}
const planeById = id => PLANES.find(p => p.id === id);

// ---------- Snap ----------
// Snap magnet: posisi bebas (1 cm), hanya menempel bila ≤ SNAP_R dari posisi "rapi":
//  - s: baris pertama di garis setback lisplank, baris modul lain (sejajar) atau tepat di atas/bawahnya
//  - t: sejajar kolom modul lain, atau tepat bersebelahan (celah S.gap), atau rapat ke garis setback tepi
// Alt ditahan / snap dimatikan = bebas total.
const SNAP_R = 0.3;
let freeMove = false;
function snapPos(p, s, t, o, exclude) {
  const r1 = v => Math.round(v * 100) / 100;
  if (!S.snap || freeMove) return { s: r1(s), t: r1(t) };
  const [ds, dt] = modDims(o), g = S.gap / 1000;
  const cs = [p.sMin + p.setback + ds / 2], ct = [];
  for (const m of V().modules) {
    if (m === exclude || m.p !== p.id) continue;
    const [mds, mdt] = modDims(m.o);
    if (Math.abs(m.t - t) < mdt / 2 + dt / 2 + 1.5) {            // modul di kolom sekitar -> tawarkan baris
      cs.push(m.s, m.s + mds / 2 + g + ds / 2, m.s - mds / 2 - g - ds / 2, m.s - mds / 2 + ds / 2);   // sejajar, atas, bawah, rata bawah
    }
    if (Math.abs(m.s - s) < mds / 2 + ds / 2 + 0.5) {            // modul di baris sekitar -> tawarkan kolom
      ct.push(m.t, m.t + mdt / 2 + g + dt / 2, m.t - mdt / 2 - g - dt / 2);
    }
  }
  const near = (v, list) => { let b = null, bd = SNAP_R; for (const c of list) { const d = Math.abs(c - v); if (d < bd) { bd = d; b = c; } } return b; };
  const ss = near(s, cs), tt = near(t, ct);
  return { s: ss ?? r1(s), t: tt ?? r1(t) };
}

// ---------- Scene ----------
const canvas = $('c');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: false });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.outputEncoding = THREE.sRGBEncoding;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
const scene = new THREE.Scene();
scene.background = new THREE.Color(0xc9dcea);
const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 1000);
const controls = new THREE.OrbitControls(camera, canvas);
controls.enableDamping = true; controls.dampingFactor = 0.12;
controls.maxPolarAngle = Math.PI * 0.495;
controls.screenSpacePanning = true;
controls.rotateSpeed = 0.65; controls.zoomSpeed = 0.9; controls.panSpeed = 0.8;
controls.touches = { ONE: THREE.TOUCH.ROTATE, TWO: THREE.TOUCH.DOLLY_PAN };
const hemi = new THREE.HemisphereLight(0xffffff, 0x8a8a8a, 0.75); scene.add(hemi);
const sun = new THREE.DirectionalLight(0xfffbeb, 0.9); sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048); sun.shadow.bias = -0.0004; sun.shadow.normalBias = 0.03;
scene.add(sun, sun.target);
const FC = ENU(...SITE.fit.center), FR = SITE.fit.radius;
{ const sc = sun.shadow.camera, R = FR * 1.9; sc.left = -R; sc.right = R; sc.top = R; sc.bottom = -R; sc.near = 1; sc.far = 400; }
sun.target.position.copy(FC);

const modelRoot = new THREE.Group(); scene.add(modelRoot);
const planeGroup = new THREE.Group(); scene.add(planeGroup);
const pvGroup = new THREE.Group(); scene.add(pvGroup);
const helperGroup = new THREE.Group(); scene.add(helperGroup);   // busur matahari, kompas (tidak ikut hitung bayangan)
const labels = [];
function mkLabel(text, color, pos, kind) {
  const el = document.createElement('div'); el.className = 'lbl'; el.style.color = color; el.textContent = text; document.body.appendChild(el);
  const l = { el, pos, kind }; labels.push(l); return l;
}
function clearLabels(kind) { for (let i = labels.length - 1; i >= 0; i--) if (labels[i].kind === kind) { labels[i].el.remove(); labels.splice(i, 1); } }

// tanah
const ground = new THREE.Mesh(new THREE.CircleGeometry(FR * 4, 64), new THREE.MeshLambertMaterial({ color: 0xb7c2a8 }));
ground.rotation.x = -Math.PI / 2; ground.position.set(FC.x, -0.05, FC.z); ground.receiveShadow = true; scene.add(ground);

// ---------- Model ----------
function b64ToBuf(b64) { const s = atob(b64), u = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i); return u.buffer; }
let modelMeshes = [];
function setModel(gltfScene, recentre) {
  modelRoot.clear(); modelMeshes = [];
  const inner = new THREE.Group(); inner.add(gltfScene); modelRoot.add(inner);
  if (recentre) {
    if (S.zUp) inner.rotation.x = -Math.PI / 2;
    inner.updateMatrixWorld(true);
    const bb = new THREE.Box3().setFromObject(inner), c = bb.getCenter(new THREE.Vector3());
    inner.position.set(-c.x, -bb.min.y, -c.z);
    modelRoot.rotation.y = S.northRot * D2R;
    const sz = bb.getSize(new THREE.Vector3());
    SITE.fit = { center: [0, 0, sz.y * 0.5], radius: Math.max(6, Math.min(40, Math.max(sz.x, sz.z) * 0.45)) };
    FC.copy(ENU(...SITE.fit.center)); FIT.r = SITE.fit.radius * 1.15;
    ground.position.set(0, -0.05, 0); buildSunPath(); updateSun();
  }
  gltfScene.traverse(o => {
    if (!o.isMesh) return;
    const old = o.material, map = old.map || null;
    if (map) map.anisotropy = renderer.capabilities.getMaxAnisotropy();
    if (!o.geometry.attributes.normal) o.geometry.computeVertexNormals();
    o.material = new THREE.MeshLambertMaterial({ map, color: map ? 0xffffff : (old.color || 0xcccccc), side: THREE.DoubleSide });
    o.castShadow = true; o.receiveShadow = true; modelMeshes.push(o);
  });
  $('load').classList.add('hide');
  fit('persp', true);
}
if (HAS_GLB) {
  $('load').textContent = 'Memuat model 3D WebODM…';
  setTimeout(() => new THREE.GLTFLoader().parse(b64ToBuf(GLB_TXT), '', g => setModel(g.scene, false), e => { $('load').textContent = 'Gagal memuat model: ' + e; }), 30);
} else {
  $('load').innerHTML = 'Belum ada model.<br>Seret file <b>.glb</b> ke jendela ini, atau buka lewat “Bidang atap & model”.';
  $('glbAdj').classList.remove('hide');
}
function loadGlbFile(file) {
  file.arrayBuffer().then(buf => {
    $('load').classList.remove('hide'); $('load').textContent = 'Memuat ' + file.name + '…';
    new THREE.GLTFLoader().parse(buf, '', g => {
      lastDropped = g.scene; setModel(g.scene, true); $('glbAdj').classList.remove('hide');
      toast('Model dimuat. Buat bidang atap dengan “＋ Bidang dari klik”.');
    }, e => { $('load').textContent = 'Gagal memuat (' + (e.message || e) + '). File Draco-compressed perlu diproses lewat skill /plts-residensial.'; });
  });
}
let lastDropped = null;
addEventListener('dragover', e => { e.preventDefault(); $('drop').classList.add('show'); });
addEventListener('dragleave', e => { if (e.target === $('drop')) $('drop').classList.remove('show'); });
addEventListener('drop', e => {
  e.preventDefault(); $('drop').classList.remove('show');
  const f = [...e.dataTransfer.files].find(f => /\.glb$/i.test(f.name)); if (f) loadGlbFile(f);
  const j = [...e.dataTransfer.files].find(f => /\.json$/i.test(f.name)); if (j) importFile(j);
});
$('bLoadGlb').onclick = () => $('fGlb').click();
$('fGlb').onchange = e => { if (e.target.files[0]) loadGlbFile(e.target.files[0]); e.target.value = ''; };
$('northRot').value = S.northRot;
$('northRot').oninput = e => { S.northRot = +e.target.value || 0; modelRoot.rotation.y = S.northRot * D2R; save(); };
$('bZup').onclick = () => { S.zUp = !S.zUp; save(); if (lastDropped) setModel(lastDropped, true); };

// ---------- Kompas ----------
(function () {
  const g = new THREE.Group(), r = FR * 1.25;
  const ring = new THREE.Mesh(new THREE.RingGeometry(r, r + 0.12, 96), new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.8 }));
  ring.rotation.x = -Math.PI / 2; g.add(ring);
  const ar = new THREE.Mesh(new THREE.ConeGeometry(0.45, 1.4, 3), new THREE.MeshBasicMaterial({ color: 0xdc2626 }));
  ar.rotation.x = -Math.PI / 2; ar.position.z = -r - 0.9; g.add(ar);
  g.position.set(FC.x, 0.03, FC.z); helperGroup.add(g);
  [['U', 0, '#dc2626'], ['T', 90, '#fff'], ['S', 180, '#fff'], ['B', 270, '#fff']].forEach(([t, a, c]) =>
    mkLabel(t, c, new THREE.Vector3(FC.x + Math.sin(a * D2R) * (r + 2), 0.3, FC.z - Math.cos(a * D2R) * (r + 2)), 'fixed'));
})();

// ---------- Matahari (NOAA) ----------
function sunPosition(doy, minutes) {
  const g = 2 * Math.PI / 365 * (doy - 1);
  const decl = 0.006918 - 0.399912 * Math.cos(g) + 0.070257 * Math.sin(g) - 0.006758 * Math.cos(2 * g) + 0.000907 * Math.sin(2 * g) - 0.002697 * Math.cos(3 * g) + 0.00148 * Math.sin(3 * g);
  const eqt = 229.18 * (0.000075 + 0.001868 * Math.cos(g) - 0.032077 * Math.sin(g) - 0.014615 * Math.cos(2 * g) - 0.040849 * Math.sin(2 * g));
  const ha = ((minutes + eqt + 4 * SITE.lng - 60 * SITE.tz) / 4 - 180) * D2R;
  const lat = SITE.lat * D2R;
  const cz = Math.sin(lat) * Math.sin(decl) + Math.cos(lat) * Math.cos(decl) * Math.cos(ha);
  const el = Math.PI / 2 - Math.acos(Math.max(-1, Math.min(1, cz)));
  const az = (Math.atan2(Math.sin(ha), Math.cos(ha) * Math.sin(lat) - Math.tan(decl) * Math.cos(lat)) + Math.PI) % (2 * Math.PI);
  return { el, az };
}
// arah ke matahari, three.js (memperhitungkan rotasi utara model hasil seret-lepas)
function sunDir(el, az) {
  return ENU(Math.sin(az) * Math.cos(el), Math.cos(az) * Math.cos(el), Math.sin(el)).normalize();
}
function dni(el) { // langit cerah sederhana (Meinel), W/m²
  if (el <= 0) return 0;
  const ed = el / D2R, am = 1 / (Math.sin(el) + 0.50572 * Math.pow(ed + 6.07995, -1.6364));
  return 1353 * Math.pow(0.7, Math.pow(am, 0.678));
}
function estimateYield(p) { // rasio POA/GHI dengan model langit cerah + difus isotropik → dikali hasil bidang datar
  const n = ENU(p.n.x, p.n.y, p.n.z); let poa = 0, ghi = 0;
  for (let d = 15; d < 365; d += 30) for (let m = 330; m <= 1110; m += 20) {
    const { el, az } = sunPosition(d, m); if (el <= 0) continue;
    const b = dni(el), s = sunDir(el, az), dif = 0.45 * b * Math.sin(el) / 0.55;
    ghi += b * Math.sin(el) + dif;
    poa += b * Math.max(0, s.dot(n)) + dif * (1 + Math.cos(p.tilt)) / 2 + 0.2 * (b * Math.sin(el) + dif) * (1 - Math.cos(p.tilt)) / 2;
  }
  return Math.round(Y_HORIZ * poa / ghi / 5) * 5;
}
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des'];
const doyLabel = doy => { const d = new Date(Date.UTC(2026, 0, doy)); return d.getUTCDate() + ' ' + MONTHS[d.getUTCMonth()]; };
const hm = m => String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0');
const sunBall = new THREE.Mesh(new THREE.SphereGeometry(0.7, 16, 12), new THREE.MeshBasicMaterial({ color: 0xfacc15 }));
const sunRay = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]), new THREE.LineDashedMaterial({ color: 0xfacc15, dashSize: 0.6, gapSize: 0.4 }));
helperGroup.add(sunBall, sunRay);
const pathGroup = new THREE.Group(); helperGroup.add(pathGroup);
function buildSunPath() {
  pathGroup.clear(); clearLabels('path');
  const R = FR * 1.9, C = new THREE.Vector3(FC.x, 0, FC.z);
  [[172, '21 Jun', 0x60a5fa], [80, 'Ekuinoks', 0xfbbf24], [355, '21 Des', 0xf87171]].forEach(([d, name, col]) => {
    const pts = [];
    for (let m = 300; m <= 1140; m += 5) { const { el, az } = sunPosition(d, m); if (el > -0.01) pts.push(C.clone().addScaledVector(sunDir(el, az), R)); }
    pathGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: col })));
    for (let h = 6; h <= 18; h++) {
      const { el, az } = sunPosition(d, h * 60); if (el <= 0) continue;
      const b = new THREE.Mesh(new THREE.SphereGeometry(h === 12 ? 0.32 : 0.2, 8, 6), new THREE.MeshBasicMaterial({ color: col }));
      b.position.copy(C.clone().addScaledVector(sunDir(el, az), R)); pathGroup.add(b);
    }
    const { el, az } = sunPosition(d, 12 * 60);
    mkLabel(name, '#' + col.toString(16), C.clone().addScaledVector(sunDir(el, az), R + 1.5), 'path');
  });
}
const sunState = { doy: 355, min: 720 };
{ const now = new Date(); sunState.doy = Math.floor((now - new Date(now.getFullYear(), 0, 0)) / 864e5); sunState.min = 600; }
function updateSun() {
  const { doy, min } = sunState;
  $('sDate').value = doy; $('sTime').value = min;
  $('vDate').textContent = doyLabel(doy); $('vTime').textContent = hm(min);
  const { el, az } = sunPosition(doy, min);
  const azd = az / D2R;
  const dirTxt = ['utara', 'timur laut', 'timur', 'tenggara', 'selatan', 'barat daya', 'barat', 'barat laut'][Math.round(azd / 45) % 8];
  $('vSun').textContent = el > 0 ? `Matahari: elevasi ${fmt(el / D2R, 1)}° · azimut ${fmt(azd, 0)}° (${dirTxt}) · bayangan ke arah ${['selatan', 'barat daya', 'barat', 'barat laut', 'utara', 'timur laut', 'timur', 'tenggara'][Math.round(azd / 45) % 8]}` : 'Matahari di bawah horizon.';
  const d = sunDir(el, az), R = FR * 1.9;
  sunBall.position.copy(FC).setY(0).addScaledVector(d, R); sunBall.visible = el > 0;
  sunRay.geometry.setFromPoints([new THREE.Vector3(FC.x, 0, FC.z), sunBall.position]); sunRay.computeLineDistances(); sunRay.visible = el > 0 && $('cPath').checked;
  sun.position.copy(FC).addScaledVector(d, 120);
  sun.target.position.copy(FC);
  const s = Math.max(0, Math.sin(el));
  sun.intensity = el > 0 ? 0.25 + 0.85 * Math.min(1, s * 1.6) : 0;
  sun.color.setHex(s < 0.25 ? 0xfdba74 : 0xfffbeb);
  hemi.intensity = $('cShadow').checked ? 0.62 : 0.95;
}
$('sDate').oninput = e => { sunState.doy = +e.target.value; updateSun(); };
$('sTime').oninput = e => { sunState.min = +e.target.value; updateSun(); };
document.querySelectorAll('[data-day]').forEach(b => b.onclick = () => { sunState.doy = +b.dataset.day; updateSun(); });
let playTimer = null;
$('bPlay').onclick = () => {
  if (playTimer) { clearInterval(playTimer); playTimer = null; $('bPlay').textContent = '▶ 1 hari'; return; }
  if (sunState.min >= 1100) sunState.min = 330;
  $('bPlay').textContent = '■ Stop';
  playTimer = setInterval(() => {
    sunState.min += 5;
    if (sunState.min > 1110) { clearInterval(playTimer); playTimer = null; $('bPlay').textContent = '▶ 1 hari'; return; }
    updateSun();
  }, 60);
};
$('cShadow').onchange = e => { renderer.shadowMap.enabled = sun.castShadow = e.target.checked; scene.traverse(o => { if (o.material) o.material.needsUpdate = true; }); updateSun(); };
$('cPath').onchange = e => { pathGroup.visible = e.target.checked; labels.forEach(l => { if (l.kind === 'path') l.hidden = !e.target.checked; }); updateSun(); };

// ---------- Visual bidang ----------
function buildPlaneVisuals() {
  planeGroup.clear(); clearLabels('plane');
  PLANES.forEach(p => {
    const pts = p.st.map(([s, t]) => P3T(p, s, t, 0.04));
    const shape = new THREE.Shape(p.st.map(([s, t]) => new THREE.Vector2(s, t)));
    const g = new THREE.ShapeGeometry(shape);
    const pos = g.attributes.position;
    for (let i = 0; i < pos.count; i++) { const v = P3T(p, pos.getX(i), pos.getY(i), 0.04); pos.setXYZ(i, v.x, v.y, v.z); }
    g.computeBoundingSphere(); g.computeBoundingBox();
    const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: p.color, transparent: true, opacity: 0.3, side: THREE.DoubleSide, depthWrite: false }));
    m.userData.plane = p; p.mesh = m; planeGroup.add(m);
    const line = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: p.color }));
    line.userData.outline = true; planeGroup.add(line);
    // garis setback
    const inner = insetPoly(p.st, p.setback);
    if (inner) { const l2 = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(inner.map(([s, t]) => P3T(p, s, t, 0.05))), new THREE.LineDashedMaterial({ color: p.color, dashSize: 0.15, gapSize: 0.1 })); l2.computeLineDistances(); l2.userData.outline = true; planeGroup.add(l2); }
    p.obs.forEach(ob => {
      const q = [[ob.s0, ob.t0], [ob.s1, ob.t0], [ob.s1, ob.t1], [ob.s0, ob.t1]].map(([s, t]) => P3T(p, s, t, 0.06));
      const l = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(q), new THREE.LineDashedMaterial({ color: 0xef4444, dashSize: 0.2, gapSize: 0.12 }));
      l.computeLineDistances(); l.userData.outline = true; planeGroup.add(l);
    });
  });
  applyPlaneVis();
}
function insetPoly(poly, d) { // offset sederhana untuk poligon (garis bantu)
  const n = poly.length; let area = 0;
  for (let i = 0; i < n; i++) { const A = poly[i], B = poly[(i + 1) % n]; area += A[0] * B[1] - B[0] * A[1]; }
  const sg = area > 0 ? 1 : -1, lines = [];
  for (let i = 0; i < n; i++) {
    const A = poly[i], B = poly[(i + 1) % n], ex = B[0] - A[0], ey = B[1] - A[1], L = Math.hypot(ex, ey) || 1;
    const nx = -ey * sg / L, ny = ex * sg / L; lines.push([A[0] + nx * d, A[1] + ny * d, ex, ey]);
  }
  const out = [];
  for (let i = 0; i < n; i++) {
    const [x1, y1, dx1, dy1] = lines[(i - 1 + n) % n], [x2, y2, dx2, dy2] = lines[i];
    const den = dx1 * dy2 - dy1 * dx2; if (Math.abs(den) < 1e-9) { out.push([x2, y2]); continue; }
    const k = ((x2 - x1) * dy2 - (y2 - y1) * dx2) / den; out.push([x1 + dx1 * k, y1 + dy1 * k]);
  }
  return out;
}
function applyPlaneVis() {
  const show = $('cPlanes').checked || editMode;
  planeGroup.children.forEach(o => {
    if (o.userData.plane) { o.visible = true; o.material.opacity = show ? (editMode ? 0.22 : 0.32) : 0; }
    else o.visible = show;
  });
}

// ---------- Modul PV ----------
function cellTex() {
  const c = document.createElement('canvas'); c.width = 132; c.height = 240;
  const g = c.getContext('2d'); g.fillStyle = '#0b1530'; g.fillRect(0, 0, 132, 240);
  const cw = 22, ch = 240 / 11;
  for (let i = 0; i < 6; i++) for (let j = 0; j < 11; j++) {
    const grd = g.createLinearGradient(i * cw, j * ch, (i + 1) * cw, (j + 1) * ch);
    grd.addColorStop(0, '#22468f'); grd.addColorStop(1, '#10275a');
    g.fillStyle = grd; g.fillRect(i * cw + 1.5, j * ch + 1.5, cw - 3, ch - 3);
  }
  g.fillStyle = '#c0c8d6'; g.fillRect(0, 119, 132, 2);
  const t = new THREE.CanvasTexture(c); t.encoding = THREE.sRGBEncoding; return t;
}
const TEX = cellTex();
const pvTopP = new THREE.MeshStandardMaterial({ map: TEX, roughness: 0.35, metalness: 0.35 });
const TEXL = TEX.clone(); TEXL.center.set(0.5, 0.5); TEXL.rotation = Math.PI / 2; TEXL.needsUpdate = true;
const pvTopL = new THREE.MeshStandardMaterial({ map: TEXL, roughness: 0.35, metalness: 0.35 });
const pvFrame = new THREE.MeshStandardMaterial({ color: 0xd1d5db, roughness: 0.35, metalness: 0.7 });
const badMat = new THREE.MeshStandardMaterial({ color: 0xef4444, emissive: 0x7f1d1d, roughness: 0.5 });
const ghostOk = new THREE.MeshBasicMaterial({ color: 0x22c55e, transparent: true, opacity: 0.45, depthWrite: false });
const ghostBad = new THREE.MeshBasicMaterial({ color: 0xef4444, transparent: true, opacity: 0.45, depthWrite: false });
const H_OFF = SITE.moduleHeight ?? 0.12;
function moduleMatrix(p, m) {
  const [ds, dt] = modDims(m.o), T = (S.module.T || 35) / 1000;
  const c = P3T(p, m.s, m.t, H_OFF + T / 2);
  const u = ENU(p.u.x, p.u.y, p.u.z), n = ENU(p.n.x, p.n.y, p.n.z), v = new THREE.Vector3().crossVectors(u, n);
  return new THREE.Matrix4().makeBasis(u.clone().multiplyScalar(ds), n.clone().multiplyScalar(T), v.multiplyScalar(dt)).setPosition(c);
}
const unitBox = new THREE.BoxGeometry(1, 1, 1), unitEdges = new THREE.EdgesGeometry(unitBox);
function buildModules() {
  pvGroup.clear(); clearLabels('pv');
  const mods = V().modules;
  mods.forEach(m => {
    const p = planeById(m.p); if (!p) { m.bad = 'bidang tidak ada'; return; }
    m.bad = checkModule(p, m, mods);
    const top = m.bad ? badMat : (heatOn && m.loss != null ? heatMat(m.loss) : (m.o === 'L' ? pvTopP : pvTopL));
    const mesh = new THREE.Mesh(unitBox, [pvFrame, pvFrame, top, m.bad ? badMat : pvFrame, pvFrame, pvFrame]);
    mesh.matrixAutoUpdate = false; mesh.matrix.copy(moduleMatrix(p, m)); mesh.castShadow = true; mesh.receiveShadow = true;
    mesh.userData.mod = m; m.mesh = mesh; pvGroup.add(mesh);
    const e = new THREE.LineSegments(unitEdges, new THREE.LineBasicMaterial({ color: m === selected ? 0xfacc15 : (m.bad ? 0xef4444 : p.color) }));
    e.matrixAutoUpdate = false; e.matrix.copy(mesh.matrix); pvGroup.add(e);
  });
  // label jumlah per bidang
  PLANES.forEach(p => {
    const ms = mods.filter(m => m.p === p.id); if (!ms.length) return;
    const s = Math.max(...ms.map(m => rectOf(m).s1)) + 0.6, t = ms.reduce((a, m) => a + m.t, 0) / ms.length;
    mkLabel(`${p.name}: ${ms.length}`, p.color, P3T(p, s, t, 0.8), 'pv');
  });
  renderResults(); save();
}
const heatCache = {};
function lossColor(v) {
  const t = Math.min(1, v / 0.10);
  return t < 0.5 ? new THREE.Color(0x16a34a).lerp(new THREE.Color(0xfacc15), t * 2) : new THREE.Color(0xfacc15).lerp(new THREE.Color(0xdc2626), (t - 0.5) * 2);
}
function heatMat(v) { const k = Math.round(v * 400); return heatCache[k] || (heatCache[k] = new THREE.MeshStandardMaterial({ color: lossColor(v), emissive: lossColor(v), emissiveIntensity: 0.3, roughness: 0.6 })); }
let heatOn = false;
$('cHeat').onchange = e => { heatOn = e.target.checked; $('heatLegend').classList.toggle('hide', !heatOn); if (heatOn && !V().modules.some(m => m.loss != null) && V().modules.length) computeShade(); else buildModules(); };

// ---------- Hasil ----------
function renderResults() {
  const mods = V().modules, wp = S.module.wp, okMods = mods.filter(m => !m.bad);
  const kwp = okMods.length * wp / 1000;
  $('kwp').innerHTML = `${fmt(kwp, 2)} kWp <small>· ${okMods.length} modul${mods.length - okMods.length ? ` · <span class="warn">${mods.length - okMods.length} tidak valid</span>` : ''}</small>`;
  let rows = `<tr><th>Bidang</th><th class="n">Modul</th><th class="n">kWp</th><th class="n">kWh/th</th><th class="n">Bayangan</th></tr>`;
  let totE = 0, totLostE = 0, shadeKnown = true;
  PLANES.forEach(p => {
    const ms = okMods.filter(m => m.p === p.id); if (!ms.length) return;
    const k = ms.length * wp / 1000, e = k * p.yield;
    const sl = ms.every(m => m.loss != null) ? ms.reduce((a, m) => a + m.loss, 0) / ms.length : null;
    if (sl == null) shadeKnown = false;
    const lost = sl == null ? 0 : e * sl * BEAM_SHARE;
    totE += e - lost; totLostE += lost;
    const nP = ms.filter(m => m.o === 'P').length, nL = ms.length - nP;
    rows += `<tr><td><span class="dot" style="background:${p.color}"></span>${p.name}<br><span style="color:var(--mut)">az ${fmt(p.az)}° · ${fmt(p.tilt / D2R, 1)}° · ${fmt(p.yield)} kWh/kWp ${p.yieldNote ? '(' + p.yieldNote + ')' : ''}${nL ? ` · ${nP}P+${nL}L` : ''}</span></td>
      <td class="n">${ms.length}</td><td class="n">${fmt(k, 2)}</td><td class="n">${fmt(e - lost)}</td><td class="n">${sl == null ? '–' : fmt(sl * BEAM_SHARE * 100, 1) + '%'}</td></tr>`;
  });
  if (okMods.length) rows += `<tr><td><b>Total</b><br><span style="color:var(--mut)">${fmt(totE / 365, 1)} kWh/hari · luas modul ${fmt(okMods.length * S.module.L * S.module.W / 1e6, 1)} m²</span></td><td class="n"><b>${okMods.length}</b></td><td class="n"><b>${fmt(kwp, 2)}</b></td><td class="n"><b>${fmt(totE)}</b></td><td class="n">${shadeKnown ? fmt(totLostE / (totE + totLostE) * 100, 1) + '%' : '–'}</td></tr>`;
  else rows += `<tr><td colspan="5" style="color:var(--mut)">Belum ada modul. Nyalakan mode edit lalu klik bidang atap.</td></tr>`;
  $('tbl').innerHTML = rows;
  const bad = mods.filter(m => m.bad);
  $('warnBox').textContent = bad.length ? `Modul merah tidak dihitung: ${[...new Set(bad.map(m => m.bad))].join(', ')}.` : '';
  $('warnBox').textContent += (!shadeKnown && okMods.length && shadeDirty) ? ' Rugi bayangan belum dihitung untuk susunan ini.' : '';
  renderTabs();
}
function renderTabs() {
  $('tabs').innerHTML = '';
  S.variants.forEach((v, i) => {
    const b = document.createElement('button');
    const n = v.modules.length;
    b.textContent = `${v.name} (${n})`; if (i === S.active) b.className = 'on';
    b.onclick = () => { if (i === S.active) { const nm = prompt('Nama varian (kosongkan untuk menghapus varian):', v.name); if (nm === null) return; if (nm.trim() === '') { if (S.variants.length > 1 && confirm('Hapus varian "' + v.name + '"?')) { S.variants.splice(i, 1); S.active = 0; } } else v.name = nm.trim(); } else S.active = i; selected = null; undoStack = []; redoStack = []; buildModules(); };
    $('tabs').appendChild(b);
  });
  const add = document.createElement('button'); add.className = 'add'; add.textContent = '＋ Varian'; add.title = 'Salin varian aktif sebagai varian baru';
  add.onclick = () => { const cur = V(); S.variants.push({ name: 'Varian ' + (S.variants.length + 1), modules: cur.modules.map(stripMod) }); S.active = S.variants.length - 1; buildModules(); };
  $('tabs').appendChild(add);
}
const stripMod = m => ({ p: m.p, s: +m.s.toFixed(4), t: +m.t.toFixed(4), o: m.o });

// ---------- Tabel bidang ----------
function renderPlaneTable() {
  let h = `<tr><th>Bidang</th><th>Setback m</th><th>kWh/kWp</th><th></th></tr>`;
  PLANES.forEach(p => {
    h += `<tr><td><span class="dot" style="background:${p.color}"></span>${p.name}<br><span style="color:var(--mut)">az ${fmt(p.az)}° · ${fmt(p.tilt / D2R, 1)}°</span></td>
      <td><input type="number" step="0.05" min="0" value="${p.setback}" data-sb="${p.id}" style="width:62px"></td>
      <td><input type="number" step="10" value="${p.yield}" data-y="${p.id}" style="width:66px"></td>
      <td>${p.user ? `<button data-delp="${p.id}" title="Hapus bidang">✕</button>` : ''}</td></tr>`;
  });
  if (!PLANES.length) h += `<tr><td colspan="4" style="color:var(--mut)">Belum ada bidang atap.</td></tr>`;
  $('planeTbl').innerHTML = h;
  $('planeTbl').querySelectorAll('[data-sb]').forEach(i => i.onchange = () => { (S.planeOverrides[i.dataset.sb] ||= {}).setback = Math.max(0, +i.value); refreshPlanes(); });
  $('planeTbl').querySelectorAll('[data-y]').forEach(i => i.onchange = () => { (S.planeOverrides[i.dataset.y] ||= {}).yield = +i.value; refreshPlanes(); });
  $('planeTbl').querySelectorAll('[data-delp]').forEach(b => b.onclick = () => {
    if (!confirm('Hapus bidang ini beserta modulnya?')) return;
    S.userPlanes = S.userPlanes.filter(x => x.id !== b.dataset.delp); S.variants.forEach(v => v.modules = v.modules.filter(m => m.p !== b.dataset.delp)); refreshPlanes();
  });
  $('accNote').innerHTML = SITE.note || (SITE.planes.length ? '' : 'Bidang dibuat manual: klik titik-titik sudut bidang atap pada model (searah keliling), lalu tekan Selesai / Enter.');
}
function refreshPlanes() { rebuildPlanes(); buildPlaneVisuals(); renderPlaneTable(); markShadeDirty(); buildModules(); }

// ---------- Interaksi ----------
let lastPlaneId = null, editMode = false, selected = null, dragging = null, downInfo = null;
let undoStack = [], redoStack = [];
const snapshot = () => JSON.stringify(V().modules.map(stripMod));
function pushUndo() { undoStack.push(snapshot()); if (undoStack.length > 200) undoStack.shift(); redoStack = []; markShadeDirty(); }
function restore(js) { V().modules = JSON.parse(js); selected = null; buildModules(); }
function undo() { if (!undoStack.length) return; redoStack.push(snapshot()); restore(undoStack.pop()); markShadeDirty(); }
function redo() { if (!redoStack.length) return; undoStack.push(snapshot()); restore(redoStack.pop()); markShadeDirty(); }
function setEdit(on) {
  editMode = on; $('bEdit').textContent = '✎ Mode edit: ' + (on ? 'hidup' : 'mati'); $('bEdit').classList.toggle('on', on); $('tEdit').classList.toggle('on', on);
  if (!on) { ghost.visible = false; }
  if (on && planeMode) endPlaneMode(false);
  applyPlaneVis(); canvas.style.cursor = on ? 'crosshair' : '';
  if (on && !PLANES.length) toast('Belum ada bidang atap — buat dulu lewat “Bidang atap & model → ＋ Bidang dari klik”.');
}
$('bEdit').onclick = $('tEdit').onclick = () => setEdit(!editMode);
$('bUndo').onclick = undo; $('bRedo').onclick = redo;
$('orient').onclick = () => toggleOrient();
function toggleOrient() {
  if (selected) {
    const p = planeById(selected.p); pushUndo(); selected.o = selected.o === 'P' ? 'L' : 'P';
    if (S.snap) Object.assign(selected, snapPos(p, selected.s, selected.t, selected.o, selected));
    buildModules();
  }
  S.orient = S.orient === 'P' ? 'L' : 'P'; $('orient').textContent = S.orient === 'P' ? 'Portrait' : 'Landscape'; save();
}
const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
function pick(ev, objs) {
  const r = canvas.getBoundingClientRect();
  ndc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
  ray.setFromCamera(ndc, camera);
  return ray.intersectObjects(objs, false)[0];
}
function planeHit(ev) {
  const h = pick(ev, PLANES.map(p => p.mesh).filter(Boolean)); if (!h) return null;
  const p = h.object.userData.plane, e = toENU(h.point), d = new THREE.Vector3(...e).sub(p.o);
  return { p, s: d.dot(p.u), t: d.dot(p.v) };
}
const moduleHit = ev => { const h = pick(ev, pvGroup.children.filter(o => o.userData.mod)); return h && h.object.userData.mod; };
const ghost = new THREE.Mesh(unitBox, ghostOk); ghost.matrixAutoUpdate = false; ghost.visible = false; scene.add(ghost);

addEventListener('pointerdown', ev => {
  if (ev.target !== canvas) return;
  downInfo = { x: ev.clientX, y: ev.clientY, t: performance.now(), btn: ev.button };
  if (planeMode) return;
  if (!editMode) return;
  const m = moduleHit(ev);
  if (m && ev.button === 0) {
    controls.enabled = false; selected = m;
    const h0 = planeHit(ev);   // ingat titik pegangan supaya modul tidak meloncat ke tengah kursor
    const off = h0 && h0.p.id === m.p ? { s: m.s - h0.s, t: m.t - h0.t } : { s: 0, t: 0 };
    dragging = { m, before: snapshot(), moved: false, off };
    buildModules();
  }
}, true);
addEventListener('pointermove', ev => {
  if (ev.target !== canvas && !dragging) return;
  if (planeMode) return;
  if (dragging) {
    const h = planeHit(ev); if (!h) return;
    freeMove = ev.altKey;
    const m = dragging.m, off = h.p.id === m.p ? dragging.off : { s: 0, t: 0 };
    const q = snapPos(h.p, h.s + off.s, h.t + off.t, m.o, m);
    if (m.p !== h.p.id || Math.abs(m.s - q.s) > 1e-4 || Math.abs(m.t - q.t) > 1e-4) {
      m.p = h.p.id; m.s = q.s; m.t = q.t; dragging.moved = true; m.loss = null; buildModules();
    }
    return;
  }
  if (editMode && ev.pointerType === 'mouse' && !ev.buttons) {
    freeMove = ev.altKey;
    const h = !moduleHit(ev) && planeHit(ev);
    if (h) {
      const q = snapPos(h.p, h.s, h.t, S.orient), gm = { p: h.p.id, ...q, o: S.orient };
      const bad = checkModule(h.p, gm, V().modules);
      ghost.material = bad ? ghostBad : ghostOk; ghost.matrix.copy(moduleMatrix(h.p, gm)); ghost.visible = true;
      $('editHint').textContent = bad ? '✕ ' + bad : '';
    } else { ghost.visible = false; $('editHint').textContent = ''; }
  }
});
addEventListener('pointerup', ev => {
  const di = downInfo; downInfo = null;
  if (dragging) {
    controls.enabled = true;
    if (dragging.moved) { undoStack.push(dragging.before); redoStack = []; markShadeDirty(); }
    dragging = null; buildModules(); return;
  }
  if (!di || ev.target !== canvas) return;
  const click = Math.hypot(ev.clientX - di.x, ev.clientY - di.y) < 6 && performance.now() - di.t < 1500;
  if (!click) return;
  if (planeMode) { if (di.btn === 0) addPlanePoint(ev); return; }
  if (!editMode) return;
  const m = moduleHit(ev);
  if (di.btn === 2) { if (m) { pushUndo(); V().modules.splice(V().modules.indexOf(m), 1); if (selected === m) selected = null; buildModules(); } return; }
  if (m) { selected = m; lastPlaneId = m.p; buildModules(); return; }
  const h = planeHit(ev);
  if (!h) { selected = null; buildModules(); return; }
  lastPlaneId = h.p.id; freeMove = ev.altKey;
  const q = snapPos(h.p, h.s, h.t, S.orient);
  let nm = { p: h.p.id, ...q, o: S.orient };
  let bad = checkModule(h.p, nm, V().modules);
  if (bad) { const alt = nearestValid(h.p, nm, h); if (alt) { nm = alt; bad = ''; } }
  if (bad === 'tumpang tindih') { toast('Sudah ada modul di situ.'); return; }
  pushUndo(); V().modules.push(nm); selected = nm; buildModules();
  if (bad) toast('Modul merah: ' + bad);
});
canvas.addEventListener('contextmenu', e => e.preventDefault());
addEventListener('keydown', ev => {
  if (/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) return;
  const k = ev.key.toLowerCase();
  if ((ev.ctrlKey || ev.metaKey) && k === 'z') { ev.preventDefault(); ev.shiftKey ? redo() : undo(); }
  else if ((ev.ctrlKey || ev.metaKey) && k === 'y') { ev.preventDefault(); redo(); }
  else if (k === 'delete' || k === 'backspace') delSelected();
  else if (k === 'r') toggleOrient();
  else if (k === 'e') setEdit(!editMode);
  else if (k === 'escape') { if (planeMode) endPlaneMode(false); selected = null; buildModules(); }
  else if (k === 'enter' && planeMode) endPlaneMode(true);
});
// cari posisi valid terdekat dari titik klik (maks. ±1,5 m), tetap di grid bila snap aktif
function nearestValid(p, nm, click) {
  const [ds, dt] = modDims(nm.o), g = S.gap / 1000;
  const step = 0.05, R = 1.2;
  let best = null, bd = 1e9;
  for (let i = -R / step; i <= R / step; i++) for (let k = -R / step; k <= R / step; k++) {
    const c = { p: p.id, s: nm.s + i * step, t: nm.t + k * step, o: nm.o };
    const d = Math.hypot(c.s - click.s, c.t - click.t); if (d >= bd || d > R) continue;
    if (!checkModule(p, c, V().modules)) { best = c; bd = d; }
  }
  return best;
}
function delSelected() { if (!selected) return; pushUndo(); V().modules.splice(V().modules.indexOf(selected), 1); selected = null; buildModules(); }
$('bDel').onclick = delSelected;
$('bClear').onclick = () => { if (!V().modules.length || !confirm('Hapus semua modul di varian ini?')) return; pushUndo(); V().modules = []; selected = null; buildModules(); };

// isi satu baris dari modul terpilih (kiri-kanan sepanjang lisplank)
$('bRow').onclick = () => {
  if (!selected) { toast('Pilih (klik) satu modul dulu di baris yang ingin diisi.'); return; }
  const p = planeById(selected.p), [, dt] = modDims(selected.o), pt = dt + S.gap / 1000;
  pushUndo(); let added = 0;
  for (const dir of [1, -1]) for (let k = 1; k < 60; k++) {
    const nm = { p: p.id, s: selected.s, t: selected.t + dir * k * pt, o: selected.o };
    const bad = checkModule(p, nm, V().modules);
    if (bad === 'tumpang tindih') continue;
    if (bad) break;
    V().modules.push(nm); added++;
  }
  if (!added) { undoStack.pop(); toast('Baris sudah penuh.'); }
  buildModules();
};
// isi penuh bidang: baris dari lisplank ke atas, tiap baris diisi maksimum & dipusatkan pada rentang valid
// isi penuh dengan baris mulai dari offset d di atas lisplank terluar (dt = langkah pindai t)
function fillRows(p, o, d, step) {
  const [ds, dt] = modDims(o), g = S.gap / 1000, out = [], mods = V().modules;
  for (let s = p.sMin + p.setback + ds / 2 + d; s + ds / 2 <= p.sMax - p.setback + 1e-6; s += ds + g) {
    const runs = []; let run = null;
    for (let t = p.tMin; t <= p.tMax + 1e-9; t += step) {
      const ok = !checkModule(p, { p: p.id, s, t, o }, mods.concat(out));
      if (ok) { if (!run) run = [t, t]; else run[1] = t; } else if (run) { runs.push(run); run = null; }
    }
    if (run) runs.push(run);
    for (const [a, b] of runs) {
      const n = Math.floor((b - a) / (dt + g) + 1e-6) + 1, w = (n - 1) * (dt + g), t0 = (a + b) / 2 - w / 2;
      for (let i = 0; i < n; i++) out.push({ p: p.id, s, t: t0 + i * (dt + g), o });
    }
  }
  return out;
}
// Lisplank nyata sering tidak lurus (mis. terpotong dak/dinding), jadi baris pertama dicoba digeser naik 0..1 jarak baris;
// dipilih susunan dengan modul terbanyak (seri: geser paling kecil).
function fillPlane(p, o) {
  const [ds] = modDims(o), pitch = ds + S.gap / 1000;
  let best = fillRows(p, o, 0, 0.005), bestD = 0;
  for (let d = 0.05; d < pitch - 1e-6; d += 0.05) {
    const r = fillRows(p, o, d, 0.01);
    if (r.length > best.length) { best = r; bestD = d; }
  }
  if (bestD) {   // haluskan di sekitar offset terbaik
    for (let d = Math.max(0, bestD - 0.04); d <= bestD + 0.04; d += 0.01) { const r = fillRows(p, o, d, 0.005); if (r.length > best.length) best = r; }
    const r = fillRows(p, o, bestD, 0.005); if (r.length >= best.length) best = r;
  }
  return best;
}
$('bFill').onclick = () => {
  const p = (selected && planeById(selected.p)) || (lastPlaneId && planeById(lastPlaneId)) || (PLANES.length === 1 ? PLANES[0] : null);
  if (!p) { toast('Nyalakan mode edit lalu klik satu modul / bidang atap yang ingin diisi.'); return; }
  const add = fillPlane(p, S.orient);
  if (!add.length) { toast('Tidak ada tempat kosong di ' + p.name + ' (' + (S.orient === 'P' ? 'portrait' : 'landscape') + ').'); return; }
  pushUndo(); V().modules.push(...add); buildModules(); toast(`${add.length} modul ditambahkan di ${p.name}.`);
};

// ---------- Bidang dari klik ----------
let planeMode = false, planePts = [], planeMarkers = new THREE.Group(); scene.add(planeMarkers);
$('bNewPlane').onclick = () => { if (planeMode) endPlaneMode(true); else startPlaneMode(); };
function startPlaneMode() {
  if (!modelMeshes.length) { toast('Muat model dulu.'); return; }
  setEdit(false); planeMode = true; planePts = []; planeMarkers.clear();
  $('bNewPlane').textContent = '✓ Selesai (Enter)'; $('bNewPlane').classList.add('on');
  toast('Klik sudut-sudut bidang atap berurutan (min. 3, sebaiknya 4+). Enter = selesai, Esc = batal.', 6000);
}
function addPlanePoint(ev) {
  const h = pick(ev, modelMeshes); if (!h) return;
  planePts.push(toENU(h.point));
  const b = new THREE.Mesh(new THREE.SphereGeometry(0.12, 10, 8), new THREE.MeshBasicMaterial({ color: 0xfacc15, depthTest: false }));
  b.position.copy(h.point); planeMarkers.add(b);
  if (planePts.length > 1) planeMarkers.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(planePts.map(p => ENU(...p))), new THREE.LineBasicMaterial({ color: 0xfacc15, depthTest: false })));
}
function endPlaneMode(commit) {
  planeMode = false; $('bNewPlane').textContent = '＋ Bidang dari klik'; $('bNewPlane').classList.remove('on');
  planeMarkers.clear();
  if (!commit) return;
  if (planePts.length < 3) { toast('Butuh minimal 3 titik.'); return; }
  const n = S.userPlanes.length + SITE.planes.length;
  // nama otomatis dari arah hadap (azimut turun lereng)
  const f = fitPlane(planePts), azd = ((Math.atan2(-f.a, -f.b) / D2R) + 360) % 360;
  const base = ['Utara', 'Timur Laut', 'Timur', 'Tenggara', 'Selatan', 'Barat Daya', 'Barat', 'Barat Laut'][Math.round(azd / 45) % 8];
  const same = PLANES.filter(q => q.name.startsWith(base)).length;
  S.userPlanes.push({ id: 'u' + Date.now().toString(36), name: same ? `${base} ${same + 1}` : base, color: PALETTE[n % PALETTE.length], poly: planePts });
  refreshPlanes(); $('cPlanes').checked = true; applyPlaneVis();
  const p = PLANES[PLANES.length - 1]; toast(`Bidang ${p.name}: azimut ${fmt(p.az)}°, kemiringan ${fmt(p.tilt / D2R, 1)}°.`, 5000);
}

// ---------- Modul: tipe & ukuran ----------
function renderModuleUI() {
  $('modSel').innerHTML = MODULE_TYPES.map(m => `<option value="${m.id}">${m.name}</option>`).join('');
  $('modSel').value = MODULE_TYPES.some(m => m.id === S.module.id) ? S.module.id : 'custom';
  $('mL').value = S.module.L; $('mW').value = S.module.W; $('mWp').value = S.module.wp; $('gap').value = S.gap;
  $('orient').textContent = S.orient === 'P' ? 'Portrait' : 'Landscape'; $('snap').checked = S.snap;
}
$('modSel').onchange = e => { const t = MODULE_TYPES.find(m => m.id === e.target.value); S.module = { ...t }; renderModuleUI(); markShadeDirty(); buildModules(); };
['mL', 'mW', 'mWp'].forEach(id => $(id).onchange = () => { S.module.L = +$('mL').value || 2000; S.module.W = +$('mW').value || 1000; S.module.wp = +$('mWp').value || 0; S.module.id = 'custom'; S.module.name = 'Ukuran sendiri'; $('modSel').value = 'custom'; markShadeDirty(); buildModules(); });
$('gap').onchange = () => { S.gap = Math.max(0, +$('gap').value || 0); save(); };
$('snap').onchange = e => { S.snap = e.target.checked; save(); };
$('cPlanes').onchange = applyPlaneVis;
$('cModel').onchange = e => { modelRoot.visible = e.target.checked; };

// ---------- Rugi bayangan tahunan (peta kedalaman dari arah matahari, GPU) ----------
let shadeDirty = true;
function markShadeDirty() { shadeDirty = true; V().modules.forEach(m => m.loss = null); }
const depthMat = new THREE.ShaderMaterial({
  side: THREE.DoubleSide,
  vertexShader: 'void main(){ gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }',
  fragmentShader: '#include <packing>\nvoid main(){ gl_FragColor = packDepthToRGBA(gl_FragCoord.z); }',
});
let shadeBusy = false;
async function computeShade() {
  const mods = V().modules.filter(m => !m.bad && planeById(m.p));
  if (!mods.length) { toast('Belum ada modul valid.'); return; }
  if (shadeBusy) return; shadeBusy = true;
  const SZ = 1024, rt = new THREE.WebGLRenderTarget(SZ, SZ, { minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter });
  const R = Math.min(60, FR * 2.2), cam = new THREE.OrthographicCamera(-R, R, R, -R, 1, 600);
  const buf = new Uint8Array(SZ * SZ * 4);
  // titik sampel tiap modul: 3 × 2 di permukaan atas
  const samples = mods.map(m => {
    const p = planeById(m.p), [ds, dt] = modDims(m.o), pts = [];
    for (const fs of [-1 / 3, 0, 1 / 3]) for (const ft of [-0.25, 0.25]) pts.push(P3T(p, m.s + fs * ds, m.t + ft * dt, H_OFF + (S.module.T || 35) / 1000 + 0.01));
    return { m, p, pts, n: ENU(p.n.x, p.n.y, p.n.z), shaded: 0, total: 0 };
  });
  const suns = [];
  for (let mo = 0; mo < 12; mo++) { const d = Math.round(15 + mo * 30.42); for (let mi = 330; mi <= 1110; mi += 30) { const { el, az } = sunPosition(d, mi); if (el > 2 * D2R) suns.push({ el, az }); } }
  const prevVis = { helper: helperGroup.visible, plane: planeGroup.visible, ghost: ghost.visible, bg: scene.background, gr: ground.visible };
  $('prog').style.display = 'block'; $('bShade').disabled = true; renderer.shadowMap.autoUpdate = false;
  const v = new THREE.Vector3();
  for (let i = 0; i < suns.length; i++) {
    const { el, az } = suns[i], d = sunDir(el, az), w = dni(el);
    helperGroup.visible = planeGroup.visible = ghost.visible = ground.visible = false; planeMarkers.visible = false;
    cam.up.set(0, Math.abs(d.y) > 0.9 ? 0 : 1, Math.abs(d.y) > 0.9 ? -1 : 0);
    cam.position.copy(FC).addScaledVector(d, 300); cam.lookAt(FC); cam.updateMatrixWorld(); cam.updateProjectionMatrix();
    scene.overrideMaterial = depthMat; scene.background = null;
    renderer.setRenderTarget(rt); renderer.setClearColor(0xffffff, 1); renderer.clear(); renderer.render(scene, cam);
    renderer.readRenderTargetPixels(rt, 0, 0, SZ, SZ, buf);
    renderer.setRenderTarget(null); scene.overrideMaterial = null; renderer.setClearColor(0x000000, 0);
    const bias = 0.06 / (600 - 1);
    for (const smp of samples) {
      const cosi = d.dot(smp.n); if (cosi <= 0) continue;   // matahari di belakang modul: bukan bayangan
      const wt = w * cosi;
      for (const pt of smp.pts) {
        v.copy(pt).project(cam);
        const px = Math.floor((v.x + 1) / 2 * SZ), py = Math.floor((v.y + 1) / 2 * SZ);
        smp.total += wt;
        if (px < 0 || py < 0 || px >= SZ || py >= SZ) continue;
        const k = (py * SZ + px) * 4;
        const dep = buf[k + 3] / 256 + buf[k + 2] / 65536 + buf[k + 1] / 16777216 + buf[k] / 4294967296;
        const my = (v.z + 1) / 2;
        if (dep < my - bias) smp.shaded += wt;
      }
    }
    if (i % 12 === 11) { $('prog').firstChild.style.width = (100 * i / suns.length) + '%'; await new Promise(r => setTimeout(r, 0)); }
  }
  helperGroup.visible = prevVis.helper; planeGroup.visible = prevVis.plane; ground.visible = prevVis.gr; scene.background = prevVis.bg; planeMarkers.visible = true;
  rt.dispose(); renderer.shadowMap.autoUpdate = true; renderer.shadowMap.needsUpdate = true;
  samples.forEach(s => s.m.loss = s.total ? s.shaded / s.total : 0);
  shadeDirty = false; shadeBusy = false;
  $('prog').style.display = 'none'; $('bShade').disabled = false;
  const worst = samples.reduce((a, s) => s.m.loss > a.m.loss ? s : a, samples[0]);
  toast(`Bayangan dihitung (${suns.length} posisi matahari). Modul terburuk: ${worst.p.name} ${fmt(worst.m.loss * BEAM_SHARE * 100, 1)}% energi/th.`, 5000);
  buildModules();
}
$('bShade').onclick = () => { computeShade(); };

// ---------- Simpan / muat ----------
function exportState() {
  return { app: 'plts-roof-designer', v: 2, site: SITE.id, name: SITE.name, saved: new Date().toISOString(), module: S.module, gap: S.gap,
    variants: S.variants.map(v => ({ name: v.name, modules: v.modules.map(stripMod) })), planeOverrides: S.planeOverrides, userPlanes: S.userPlanes, northRot: S.northRot, zUp: S.zUp };
}
function download(name, blob) { const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500); }
const slug = s => s.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
$('bExport').onclick = () => download(`layout_${slug(SITE.id)}_${new Date().toISOString().slice(0, 10)}.json`, new Blob([JSON.stringify(exportState(), null, 1)], { type: 'application/json' }));
$('bImport').onclick = () => $('fImport').click();
$('fImport').onchange = e => { if (e.target.files[0]) importFile(e.target.files[0]); e.target.value = ''; };
function importFile(f) {
  f.text().then(t => {
    const d = JSON.parse(t);
    if (d.app !== 'plts-roof-designer' || !Array.isArray(d.variants)) throw new Error('bukan file layout PLTS Roof Designer');
    if (d.site && d.site !== SITE.id && !confirm(`Layout ini untuk "${d.name || d.site}", bukan "${SITE.name}". Tetap impor?`)) return;
    pushUndo();
    Object.assign(S, { module: d.module || S.module, gap: d.gap ?? S.gap, variants: d.variants, planeOverrides: d.planeOverrides || {}, userPlanes: d.userPlanes || [], active: 0, northRot: d.northRot || 0, zUp: !!d.zUp });
    renderModuleUI(); refreshPlanes(); toast(`Layout diimpor: ${d.variants.length} varian.`);
  }).catch(err => toast('Gagal impor: ' + err.message, 5000));
}
$('bPng').onclick = () => {
  const ghostVis = ghost.visible; ghost.visible = false;
  renderer.render(scene, camera); updateLabels();
  const W = canvas.width, H = canvas.height, c = document.createElement('canvas'); c.width = W; c.height = H;
  const g = c.getContext('2d'); g.drawImage(canvas, 0, 0);
  // label HTML ikut digambar
  const k = W / innerWidth; g.font = `bold ${12 * k}px system-ui`; g.textAlign = 'center'; g.textBaseline = 'middle';
  labels.forEach(l => { if (l.el.style.display === 'none') return; const x = parseFloat(l.el.style.left) * k, y = parseFloat(l.el.style.top) * k, tw = g.measureText(l.el.textContent).width;
    g.fillStyle = '#000a'; g.fillRect(x - tw / 2 - 5 * k, y - 9 * k, tw + 10 * k, 18 * k); g.fillStyle = l.el.style.color; g.fillText(l.el.textContent, x, y); });
  // kotak judul
  const okMods = V().modules.filter(m => !m.bad), kwp = okMods.length * S.module.wp / 1000;
  const lines = [SITE.name, `${V().name}: ${okMods.length} × ${S.module.wp} Wp = ${fmt(kwp, 2)} kWp`, PLANES.map(p => { const n = okMods.filter(m => m.p === p.id).length; return n ? `${p.name} ${n}` : ''; }).filter(Boolean).join(' · '), S.module.name || ''];
  g.textAlign = 'left'; g.textBaseline = 'top';
  const bw = Math.max(...lines.map((t, i) => { g.font = `${i ? '' : 'bold '}${(i ? 13 : 17) * k}px system-ui`; return g.measureText(t).width; })) + 24 * k;
  const bx = W - bw - 14 * k, by = H - (lines.length * 20 + 22) * k - 14 * k;
  g.fillStyle = '#0f172ae6'; g.fillRect(bx, by, bw, (lines.length * 20 + 22) * k);
  lines.forEach((t, i) => { g.font = `${i ? '' : 'bold '}${(i ? 13 : 17) * k}px system-ui`; g.fillStyle = i ? '#e5e7eb' : '#f59e0b'; g.fillText(t, bx + 12 * k, by + (10 + i * 20) * k); });
  ghost.visible = ghostVis;
  c.toBlob(b => download(`desain_${slug(SITE.id)}_${slug(V().name)}.png`, b), 'image/png');
};

// ---------- Kamera ----------
const FIT = { r: FR * 1.15 };
const DIRS = { persp: [0.35, 0.75, 0.75], top: [0, 1, 0.001] };
let curView = 'persp', anim = null;
const uiEl = $('ui');
function applyViewport() {
  const w = innerWidth, h = innerHeight, mob = w <= 760;
  let x0 = 0, vw = w, vh = h;
  if (mob) vh = Math.max(h * 0.38, h - uiEl.offsetHeight);
  else { x0 = uiEl.offsetWidth + 20; vw = Math.max(w * 0.45, w - x0); }
  renderer.setSize(w, h);
  camera.fov = mob ? 50 : 40; camera.aspect = vw / vh;
  camera.setViewOffset(vw, vh, -x0, 0, w, h); camera.updateProjectionMatrix();
}
function fit(view, instant) {
  curView = view;
  const d = new THREE.Vector3(...(DIRS[view] || DIRS.persp)).normalize();
  const vf = camera.fov * D2R, hf = 2 * Math.atan(Math.tan(vf / 2) * camera.aspect);
  const dist = FIT.r / Math.sin(Math.min(vf, hf) / 2);
  controls.maxDistance = dist * 4; controls.minDistance = 2;
  const pos = FC.clone().addScaledVector(d, dist);
  if (instant) { camera.position.copy(pos); controls.target.copy(FC); controls.update(); anim = null; }
  else anim = { p0: camera.position.clone(), t0: controls.target.clone(), p1: pos, t1: FC.clone(), s: performance.now() };
}
controls.addEventListener('start', () => { anim = null; });
$('tFit').onclick = () => fit('persp'); $('tTop').onclick = () => fit('top');
function togglePanel() { uiEl.classList.toggle('min'); $('toggle').textContent = uiEl.classList.contains('min') ? '▴' : '▾'; }
$('toggle').onclick = togglePanel;
$('title').onclick = () => { if (innerWidth <= 760) togglePanel(); };
if (innerWidth <= 760) { uiEl.classList.add('min'); $('toggle').textContent = '▴'; }
if ('ResizeObserver' in window) new ResizeObserver(applyViewport).observe(uiEl);
addEventListener('resize', applyViewport);

let toastTimer;
function toast(t, ms = 3000) { const el = $('status'); el.textContent = t; el.classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove('show'), ms); }

// ---------- Mulai ----------
$('title').textContent = SITE.name;
$('subtitle').textContent = SITE.subtitle || (HAS_GLB ? '' : 'Seret model .glb ke jendela untuk mulai.');
document.title = SITE.name + ' · PLTS Roof Designer';
rebuildPlanes(); buildPlaneVisuals(); renderPlaneTable(); renderModuleUI(); buildSunPath(); updateSun(); buildModules();
if (SITE.accuracy === 'manual') $('accNote').innerHTML = '⚠ Model atap dibuat dari ukuran manual (bukan WebODM) — akurasi lebih rendah. ' + (SITE.note || '');
applyViewport(); fit('persp', true);
if (!SITE.planes.length && HAS_GLB) $('advanced').open = true;

const tmp = new THREE.Vector3();
(function loop() {
  requestAnimationFrame(loop);
  if (anim) {
    const k = Math.min(1, (performance.now() - anim.s) / 600), e = 1 - Math.pow(1 - k, 3);
    camera.position.lerpVectors(anim.p0, anim.p1, e); controls.target.lerpVectors(anim.t0, anim.t1, e);
    if (k >= 1) anim = null;
  }
  controls.update();
  renderer.render(scene, camera);
  updateLabels();
})();
function updateLabels() {
  labels.forEach(l => {
    tmp.copy(l.pos).project(camera);
    l.el.style.display = (tmp.z < 1 && !l.hidden) ? '' : 'none';
    l.el.style.left = ((tmp.x + 1) / 2 * innerWidth) + 'px';
    l.el.style.top = ((1 - tmp.y) / 2 * innerHeight) + 'px';
  });
}
window.__designer = { scene, modelRoot, exportState, importFile, S, PLANES: () => PLANES, fillPlane, checkModule, sunPosition, sunDir, computeShade, buildModules, V, planeHit, moduleHit, snapPos, nearestValid, camera, controls, get editMode() { return editMode; } };
