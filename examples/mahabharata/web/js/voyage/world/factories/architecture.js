// Architecture factories: palace, hall, corridor, gate, walls, courtyard, temple, hermitage, camp, shelter,
// balcony, arena, city, altar. Vocabulary: stepped plinths, octagonal lathe pillars with bracket capitals,
// cusped arches, jaali screens, chhatris, domes, shikharas, toranas. Static parts are merged per material.
// Convention: front faces +z (towards the stage when the asset sits at negative z), ground at y = 0.
import {
  THREE, PAL, P, num, int, bool, str, col, baseColor, accentColor, seg, qcount, qname, rng, G, lathe, geo, T,
  Batch, mat, stone, goldM, clothM, woodM, darkM, glowM, addUpdate, flame, boundsOf, idOf,
} from './kit.js';
import { OBJECTS } from './objects.js';

const TAU = Math.PI * 2;

// ================================================================ shared architectural elements
function pillarPts(h, r) {
  return [[r * 1.55, 0], [r * 1.55, h * 0.045], [r * 1.3, h * 0.06], [r * 1.3, h * 0.085], [r * 1.08, h * 0.1], [r, h * 0.12],
    [r, h * 0.4], [r * 1.18, h * 0.415], [r * 1.18, h * 0.44], [r * 0.97, h * 0.455],
    [r * 0.93, h * 0.7], [r * 1.12, h * 0.72], [r * 1.12, h * 0.74], [r * 0.9, h * 0.755],
    [r * 0.86, h * 0.83], [r * 1.05, h * 0.85], [r * 1.32, h * 0.885], [r * 1.3, h * 0.915], [r * 1.05, h * 0.93],
    [r * 1.45, h * 0.945], [r * 1.45, h], [0, h]];
}
/** Octagonal carved pillar (8 facets read as dressed stone). */
const pillarGeo = (ctx, h, r) => lathe(ctx, `pillar:${h.toFixed(2)}:${r.toFixed(3)}`, pillarPts(h, r), seg(ctx, 6, 8, 8), Math.PI / 8);
/** Pillar + cross-shaped bracket capital (potika). Total height h + 0.05h. */
function pillar(b, ctx, mP, mCap, x, y, z, h, r) {
  b.add(pillarGeo(ctx, h, r), mP, T(x, y, z));
  b.box(ctx, mCap, r * 4.6, h * 0.05, r * 1.5, x, y + h * 1.025, z);
  b.box(ctx, mCap, r * 1.5, h * 0.05, r * 4.6, x, y + h * 1.025, z);
}
/** Stepped plinth; returns top height. */
function plinth(b, ctx, m, w, d, steps = 3, stepH = 0.5, inset = 0.7, x = 0, z = 0, y0 = 0) {
  for (let i = 0; i < steps; i++) b.box(ctx, m, w - 2 * i * inset, stepH, d - 2 * i * inset, x, y0 + stepH * (i + 0.5), z);
  return y0 + steps * stepH;
}
/** Front stairs rising towards -z, ending at zBack. */
function stairs(b, ctx, m, w, h, zBack, x = 0) {
  const n = Math.max(2, Math.round(h / 0.22)), hs = h / n, tread = 0.4;
  for (let k = 0; k < n; k++) {
    const top = hs * (n - k);
    b.box(ctx, m, w, top, tread, x, top / 2, zBack + tread * (k + 0.5));
  }
  const len = n * tread; // side balustrades
  for (const s of [-1, 1]) b.box(ctx, m, 0.45, h * 0.6 + 0.3, len, x + s * (w / 2 + 0.22), (h * 0.6 + 0.3) / 2, zBack + len / 2);
}
/** Row of kangura merlons along local x, centred. */
function parapet(b, ctx, m, len, spacing = 1.1, size = 0.5) {
  const n = Math.max(1, Math.floor(len / spacing));
  b.box(ctx, m, len, size * 0.35, size * 0.6, 0, size * 0.175, 0);
  for (let i = 0; i < n; i++) {
    const x = -len / 2 + (i + 0.5) * (len / n);
    b.box(ctx, m, size * 0.7, size * 0.7, size * 0.45, x, size * 0.7, 0);
    b.add(G.cone(ctx, 4), m, T(x, size * 1.2, 0, 0, Math.PI / 4, 0, size * 0.62, size * 0.4, size * 0.45));
  }
}
const DOME_PTS = (() => {
  const pts = [[1.04, 0], [1.09, 0.06], [1.0, 0.12]];
  for (let i = 1; i <= 10; i++) { const a = (i / 10) * Math.PI / 2; pts.push([Math.cos(a) * (1 + 0.07 * Math.sin(a * 2)), 0.12 + Math.sin(a) * 1.05]); }
  return pts;
})();
const domeGeo = (ctx) => lathe(ctx, 'dome', DOME_PTS, seg(ctx, 10, 14, 18));
const FINIAL_PTS = [[0, 0], [0.18, 0], [0.22, 0.08], [0.12, 0.16], [0.3, 0.3], [0.32, 0.42], [0.2, 0.52], [0.08, 0.58], [0.14, 0.66], [0.06, 0.72], [0.025, 1], [0, 1]];
const finialGeo = (ctx) => lathe(ctx, 'finial', FINIAL_PTS, seg(ctx, 6, 8, 10));
const SHIKHARA_PTS = (() => {
  const pts = [[1.05, 0], [1.05, 0.03]], tiers = 7;
  const r = (y) => 1 - Math.pow(y / 0.92, 1.7) * 0.64;
  for (let i = 0; i < tiers; i++) {
    const y0 = 0.03 + i * (0.86 / tiers), y1 = y0 + 0.86 / tiers;
    pts.push([r(y0) * 1.05, y0 + 0.006], [r(y0) * 0.98, y0 + 0.02], [r(y1), y1]);
  }
  pts.push([0.3, 0.9], [0.26, 0.92], [0, 0.925]);
  return pts;
})();
const shikharaGeo = (ctx) => lathe(ctx, 'shikhara', SHIKHARA_PTS, seg(ctx, 8, 12, 16), Math.PI / 4);

/** Dome on a lotus band with gold kalasha finial; origin at dome base. */
function dome(b, ctx, mS, mG, r) {
  b.add(domeGeo(ctx), mS, T(0, 0, 0, 0, 0, 0, r));
  b.add(finialGeo(ctx), mG, T(0, r * 1.15, 0, 0, 0, 0, r * 0.55));
}
/** Curvilinear Nagara spire with amalaka + kalasha; base radius R, height H. */
function shikhara(b, ctx, mS, mG, R, H, subs = true) {
  b.add(shikharaGeo(ctx), mS, T(0, 0, 0, 0, 0, 0, R, H, R));
  b.add(G.sphere(ctx, seg(ctx, 10, 14, 16), 6), mS, T(0, H * 0.925, 0, 0, 0, 0, R * 0.78, R * 0.22, R * 0.78)); // amalaka
  b.add(finialGeo(ctx), mG, T(0, H * 0.925 + R * 0.08, 0, 0, 0, 0, R * 0.45));
  if (subs) for (let i = 0; i < 4; i++) { // urushringa sub-spires on each face
    const a = i * Math.PI / 2;
    b.add(shikharaGeo(ctx), mS, T(Math.sin(a) * R * 0.78, H * 0.18, Math.cos(a) * R * 0.78, 0, 0, 0, R * 0.42, H * 0.4, R * 0.42));
  }
}
const CPILLAR_PTS = [[0.13, 0], [0.13, 0.07], [0.085, 0.11], [0.075, 0.86], [0.11, 0.9], [0.13, 1], [0, 1]];
/** Chhatri pavilion (4 slender pillars + dome); origin at base centre, footprint ≈ s. */
function chhatri(b, ctx, mS, mG, s) {
  const ph = s * 1.05;
  b.box(ctx, mS, s * 1.15, s * 0.12, s * 1.15, 0, s * 0.06, 0);
  const cp = lathe(ctx, 'cpillar', CPILLAR_PTS, 6);
  for (const [x, z] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) b.add(cp, mS, T(x * s * 0.42, s * 0.12, z * s * 0.42, 0, 0, 0, s, ph, s));
  b.box(ctx, mS, s * 1.35, s * 0.09, s * 1.35, 0, s * 0.12 + ph + s * 0.045, 0);
  b.within(T(0, s * 0.21 + ph, 0), () => dome(b, ctx, mS, mG, s * 0.5));
}
/** Cusped (multifoil) arch panel: extruded U-shaped block, opening at bottom. Width w, height h, opening half-width a, springing s. */
function archPanelGeo(ctx, w, h, a, s, depth, lobes = 7) {
  const key = `arch:${[w, h, a, s, depth, lobes].map((v) => (+v).toFixed(2)).join(':')}`;
  return geo(ctx, key, () => {
    const sh = new THREE.Shape();
    sh.moveTo(-w / 2, 0); sh.lineTo(-a, 0); sh.lineTo(-a, s);
    const N = lobes * 6, ry = Math.min(a * 1.1, (h - s) * 0.85); // elliptical when the panel is shallow
    for (let i = 0; i <= N; i++) {
      const th = Math.PI - (i / N) * Math.PI, local = ((i / N) * lobes) % 1;
      const rr = a * (0.86 + 0.14 * Math.sin(Math.PI * local));
      sh.lineTo(Math.cos(th) * rr, s + Math.sin(th) * (rr / a) * ry);
    }
    sh.lineTo(a, s); sh.lineTo(a, 0); sh.lineTo(w / 2, 0); sh.lineTo(w / 2, h); sh.lineTo(-w / 2, h); sh.closePath();
    const g = new THREE.ExtrudeGeometry(sh, { depth, bevelEnabled: false, curveSegments: 2 });
    g.translate(0, 0, -depth / 2);
    return g;
  });
}
/** Jaali panel geometry (UVs scaled so the lattice keeps its size). */
function jaaliGeo(ctx, w, h) {
  return geo(ctx, `jaali:${w.toFixed(2)}:${h.toFixed(2)}`, () => {
    const g = new THREE.PlaneGeometry(w, h);
    const uv = g.attributes.uv;
    for (let i = 0; i < uv.count; i++) uv.setXY(i, uv.getX(i) * w / 0.9, uv.getY(i) * h / 0.9);
    return g;
  });
}
const jaaliMat = (ctx, c) => mat(ctx, { color: c, roughness: 0.85, alpha: 'jaali', alphaTest: 0.5, double: true });
/** Window: dark recess + jaali screen + trim frame, on a wall face looking +z (local). */
function jaaliWindow(b, ctx, mTrim, mJ, w, h) {
  b.box(ctx, darkM(ctx), w, h, 0.04, 0, 0, 0.02);
  b.add(jaaliGeo(ctx, w, h), mJ, T(0, 0, 0.07));
  b.box(ctx, mTrim, w + 0.3, 0.16, 0.22, 0, h / 2 + 0.08, 0.08);
  b.box(ctx, mTrim, w + 0.4, 0.14, 0.32, 0, -h / 2 - 0.07, 0.12);
}
/** Torana gateway: two pillars, three architraves with volute ends, kalasha finials; width w, height h. */
function torana(b, ctx, mS, mG, w, h) {
  const r = 0.19 * (h / 7), ph = h * 0.6;
  for (const s of [-1, 1]) pillar(b, ctx, mS, mS, s * w / 2, 0, 0, ph, r);
  const beams = [[h * 0.66, w * 1.1], [h * 0.78, w * 1.16], [h * 0.9, w * 1.24]];
  for (const [y, len] of beams) {
    b.box(ctx, mS, len, h * 0.055, r * 2.2, 0, y, 0);
    for (const s of [-1, 1]) b.add(G.cyl(ctx, 10), mG, T(s * len / 2, y, 0, Math.PI / 2, 0, 0, h * 0.08, r * 2.3, h * 0.08));
  }
  for (const s of [-1, 1]) {
    b.box(ctx, mS, r * 1.6, h * 0.26, r * 1.6, s * w / 2, h * 0.78, 0);
    b.box(ctx, mS, r * 1.0, h * 0.1, r * 1.0, s * w * 0.18, h * 0.72, 0);
    b.box(ctx, mS, r * 1.0, h * 0.1, r * 1.0, s * w * 0.18, h * 0.84, 0);
    b.add(finialGeo(ctx), mG, T(s * w * 0.45, h * 0.93, 0, 0, 0, 0, h * 0.09));
  }
  b.add(finialGeo(ctx), mG, T(0, h * 0.93, 0, 0, 0, 0, h * 0.13));
}

const trimOf = (p, base) => col(p, ['trim', 'secondary', 'materials.trim'], lighten(base, 0.12));
function lighten(hexStr, k) { const c = new THREE.Color(hexStr); c.lerp(new THREE.Color('#ffffff'), k); return '#' + c.getHexString(); }
function darken(hexStr, k) { const c = new THREE.Color(hexStr); c.lerp(new THREE.Color('#000000'), k); return '#' + c.getHexString(); }
function group(name, kind) { const g = new THREE.Group(); g.name = name; g.userData.kind = kind; return g; }
function addBanner(target, ctx, x, y, z, color, accent, emblem, h = 4) {
  const bn = OBJECTS.banner({ height: h, color, accent, emblem, width: h * 0.32, flagHeight: h * 0.2 }, ctx);
  bn.position.set(x, y, z);
  target.add(bn);
  return bn;
}

// ================================================================ palace
function palace(params, ctx) {
  const p = P(params);
  const variant = str(p.variant, 'royal');
  const w = num(p.width, 40, 16, 120), d = num(p.depth, 26, 12, 80);
  const base = baseColor(p, variant === 'lac' ? '#8A4A2C' : variant === 'marble' || variant === 'indraprastha' ? PAL.marble : PAL.sandstone);
  const trim = trimOf(p, base), accent = accentColor(p, PAL.gold);
  const floors = int(p.floors, 2, 1, 2);
  const useDome = bool(p.dome, variant !== 'lac'), useShikhara = bool(p.shikhara, false);
  const S = stone(ctx, base), Tm = stone(ctx, trim), Gd = goldM(ctx, accent), J = jaaliMat(ctx, trim);
  const g = group('palace', 'architecture'), b = new Batch();
  const hi = qname(ctx) !== 'low';

  // plinth + stairs
  const pw = w + 5, pd = d + 5;
  const y = plinth(b, ctx, S, pw, pd, 3, 0.55, 0.8);
  b.box(ctx, Tm, pw - 3.2 + 0.1, 0.14, pd - 3.2 + 0.1, 0, y - 0.07, 0);
  stairs(b, ctx, S, 9, y, pd / 2);

  // ground floor: recessed body + front colonnade with cusped arches
  const h1 = 6, faceZ = d / 2 - 5, colZ = d / 2 - 1.2;
  b.box(ctx, S, w - 2, h1, d - 6, 0, y + h1 / 2, -2);
  let n = Math.max(6, Math.round(w / 3.6)); if (n % 2) n++;
  const span = w - 3, bay = span / (n - 1), pr = 0.34;
  for (let i = 0; i < n; i++) pillar(b, ctx, S, Tm, -span / 2 + i * bay, y, colZ, h1 * 0.95 - 0.35, pr);
  const bw = bay - pr * 2.4, ph = h1 * 0.42;
  const arch = archPanelGeo(ctx, bw, ph, bw / 2 - 0.08, ph * 0.08, 0.42);
  for (let i = 0; i < n - 1; i++) b.add(arch, Tm, T(-span / 2 + (i + 0.5) * bay, y + h1 - ph, colZ));
  // windows and the great door on the recessed facade
  for (let i = 0; i < n - 1; i++) {
    const x = -span / 2 + (i + 0.5) * bay;
    if (i === (n - 2) / 2) continue;
    b.within(T(x, y + 2.6, faceZ), () => jaaliWindow(b, ctx, Tm, J, Math.min(1.8, bw * 0.6), 2.2));
  }
  b.within(T(0, y, faceZ), () => {
    b.box(ctx, darkM(ctx, '#2a1a10'), 3, 4.6, 0.06, 0, 2.3, 0.03);
    b.add(archPanelGeo(ctx, 4.6, 5.4, 1.5, 3.4, 0.35), Tm, T(0, 0, 0.18));
  });
  // roof slab + sloped chajja eave + parapet
  const y2 = y + h1 + 0.5;
  b.box(ctx, S, w, 0.5, d - 1.2, 0, y + h1 + 0.25, 0.4);
  b.box(ctx, Tm, w + 1.6, 0.16, 1.8, 0, y + h1 - 0.05, d / 2 + 0.3, 0.22, 0, 0);
  b.within(T(0, y2, d / 2 - 0.2), () => parapet(b, ctx, Tm, w, 1.15, 0.55));
  for (const s of [-1, 1]) b.within(T(s * (w / 2 - 0.2), y2, 0.4, 0, Math.PI / 2, 0), () => parapet(b, ctx, Tm, d - 1.6, 1.15, 0.55));
  // roof chhatris
  const cs = 2.4;
  const corners = [[-1, d / 2 - 1.9], [1, d / 2 - 1.9]];
  if (hi) corners.push([-1, -d / 2 + 2.6], [1, -d / 2 + 2.6]);
  for (const [sx, z] of corners) b.within(T(sx * (w / 2 - 1.9), y2, z), () => chhatri(b, ctx, S, Gd, cs));

  // upper floor pavilion
  let yTop = y2, cz = -d * 0.1;
  if (floors > 1) {
    const w2 = w * 0.56, d2 = d * 0.5, h2 = 4.4;
    b.box(ctx, S, w2, h2, d2, 0, y2 + h2 / 2, cz);
    let m = Math.max(4, Math.round(w2 / 3)); if (m % 2) m++;
    const sp2 = w2 - 1, bay2 = sp2 / (m - 1), fz = cz + d2 / 2 + 0.9;
    for (let i = 0; i < m; i++) pillar(b, ctx, Tm, Tm, -sp2 / 2 + i * bay2, y2, fz, h2 * 0.9 - 0.3, 0.22);
    b.box(ctx, S, w2 + 0.4, 0.4, 2.2, 0, y2 + h2 * 0.9 + 0.05, cz + d2 / 2 + 0.7);
    b.box(ctx, Tm, w2 + 1.4, 0.14, 1.4, 0, y2 + h2 * 0.9 + 0.2, fz + 0.6, 0.25, 0, 0);
    for (let i = 0; i < m - 1; i++) {
      if (i === (m - 2) / 2) continue;
      b.within(T(-sp2 / 2 + (i + 0.5) * bay2, y2 + 2, cz + d2 / 2), () => jaaliWindow(b, ctx, Tm, J, Math.min(1.4, bay2 * 0.55), 1.8));
    }
    yTop = y2 + h2 + 0.4;
    b.box(ctx, S, w2 + 0.6, 0.4, d2 + 0.6, 0, yTop - 0.2, cz);
    b.within(T(0, yTop, cz + d2 / 2 + 0.1), () => parapet(b, ctx, Tm, w2 + 0.4, 1.0, 0.45));
    for (const sx of [-1, 1]) b.within(T(sx * (w2 / 2 - 1), yTop, cz + d2 / 2 - 1), () => chhatri(b, ctx, S, Gd, 1.7));
  }
  // crowning dome or shikhara on a drum
  const dr = Math.min(w, d * 1.6) * 0.11;
  b.add(G.cyl(ctx, seg(ctx, 10, 14, 18)), S, T(0, yTop + 0.8, cz, 0, 0, 0, dr * 2.1, 1.6, dr * 2.1));
  b.add(G.cyl(ctx, seg(ctx, 10, 14, 18)), Tm, T(0, yTop + 1.65, cz, 0, 0, 0, dr * 2.3, 0.2, dr * 2.3));
  if (useShikhara) b.within(T(0, yTop + 1.7, cz), () => shikhara(b, ctx, S, Gd, dr * 1.1, w * 0.32, hi));
  else if (useDome) b.within(T(0, yTop + 1.75, cz), () => dome(b, ctx, S, Gd, dr * 1.05));

  b.build(g, ctx, `palace:${JSON.stringify([w, d, base, trim, accent, floors, useDome, useShikhara, qname(ctx)])}`);
  if (bool(p.banners, true)) {
    const bc = col(p, ['bannerColor', 'banner_color'], PAL.vermilion);
    const by = floors > 1 ? yTop : y2;
    const bx = floors > 1 ? w * 0.28 - 0.4 : w / 2 - 4;
    for (const s of [-1, 1]) addBanner(g, ctx, s * bx, by, cz + 1, bc, accent, p.emblem || 'sun', 4.2);
  }
  g.userData.anchors = { roof: [0, y2, 0], door: [0, y, d / 2 + 2] };
  return g;
}

// ================================================================ hall (sabha / throne room / dice hall / maya sabha)
function hall(params, ctx) {
  const p = P(params);
  const variant = str(p.variant, 'sabha');
  const w = num(p.width, 34, 14, 90), d = num(p.depth, 56, 16, 120), h = num(p.height, 10, 5, 24);
  // the registry describes the Maya Sabha by its features rather than a variant name
  const maya = variant === 'maya' || bool(p.water_illusion, false) || str(p.floor, '') === 'crystal' || /maya/.test(idOf(p, ctx));
  const base = baseColor(p, maya ? '#D9DCE6' : PAL.sandstone), trim = trimOf(p, base), accent = accentColor(p, PAL.gold);
  const floorC = col(p, ['floor', 'floorColor', 'floor_color'], maya ? '#7E93B8' : '#B89A72');
  const reflective = bool(p.reflective, maya || num(p.reflectivity, 0, 0, 1) >= 0.5);
  const S = stone(ctx, base), Tm = stone(ctx, trim), Gd = goldM(ctx, accent), J = jaaliMat(ctx, trim);
  const F = reflective ? mat(ctx, { color: floorC, roughness: 0.12, metalness: 0.35 }) : stone(ctx, floorC, { roughness: 0.6 });
  const g = group('hall', 'architecture'), b = new Batch();

  // floor + inlay + carpet runner
  b.box(ctx, F, w + 2, 0.4, d + 2, 0, -0.19, 0);
  b.box(ctx, Tm, w - 4, 0.02, d - 4, 0, 0.02, 0);
  b.box(ctx, F, w - 4.6, 0.022, d - 4.6, 0, 0.025, 0);
  const daisZ = -d / 2 + 4;
  if (bool(p.carpet, true)) {
    const carpet = clothM(ctx, col(p, ['carpet', 'carpetColor', 'carpet_color'], PAL.vermilion), { double: false });
    const len = d / 2 - 2 - (daisZ + 3);
    b.box(ctx, carpet, 3.2, 0.03, len, 0, 0.04, daisZ + 3 + len / 2);
    b.box(ctx, Gd, 3.5, 0.025, len, 0, 0.035, daisZ + 3 + len / 2);
  }
  // pillar rows (stage disc r≈15 at the hall centre kept clear)
  const ph = h - 0.6, pr = 0.42;
  const xs = [w / 2 - 2, w / 2 - 6.5].filter((x) => x > 2);
  for (let z = -d / 2 + 4; z <= d / 2 - 1.9; z += 6.5) {
    for (const x0 of xs) for (const s of [-1, 1]) {
      const x = s * x0;
      if (Math.hypot(x, z) < 15 && x0 !== xs[0]) continue;
      pillar(b, ctx, S, Tm, x, 0, z, ph * 0.95, pr);
    }
  }
  // walls: solid lower band, jaali clerestory, beams
  const solid = h * 0.62;
  b.box(ctx, S, w + 2, h, 1, 0, h / 2, -d / 2 - 0.5);
  for (const s of [-1, 1]) {
    b.box(ctx, S, 1, solid, d, s * (w / 2 + 0.5), solid / 2, 0);
    b.box(ctx, S, 1, h * 0.12 + 0.05, d, s * (w / 2 + 0.5), h * 0.94, 0);
    const nP = Math.max(2, Math.floor(d / 3.2));
    for (let i = 0; i < nP; i++) {
      const z = -d / 2 + (i + 0.5) * (d / nP);
      b.add(jaaliGeo(ctx, d / nP - 0.35, h * 0.26), J, T(s * (w / 2 + 0.5), solid + h * 0.13, z, 0, Math.PI / 2, 0));
      b.box(ctx, S, 1, h * 0.26, 0.35, s * (w / 2 + 0.5), solid + h * 0.13, -d / 2 + i * (d / nP));
    }
    // wall lamps (emissive only — no extra lights)
    for (let z = -d / 2 + 4; z <= d / 2 - 2; z += 6.5) {
      b.box(ctx, Tm, 0.4, 0.15, 0.4, s * (w / 2 - 0.15), 3.0, z + 3.25);
      b.add(G.sphere(ctx, 8, 6), glowM(ctx, '#FFC873', 1.6), T(s * (w / 2 - 0.2), 3.22, z + 3.25, 0, 0, 0, 0.22));
    }
  }
  // roof with central skylight + cross beams + facade arch
  const ow = w * 0.42, od = d * 0.46, rt = 0.7, ry = h + rt / 2;
  b.box(ctx, S, w + 2, rt, (d + 2 - od) / 2, 0, ry, -(od / 2 + (d + 2 - od) / 4));
  b.box(ctx, S, w + 2, rt, (d + 2 - od) / 2, 0, ry, od / 2 + (d + 2 - od) / 4);
  for (const s of [-1, 1]) b.box(ctx, S, (w + 2 - ow) / 2, rt, od, s * (ow / 2 + (w + 2 - ow) / 4), ry, 0);
  b.box(ctx, Gd, ow + 0.3, 0.18, 0.3, 0, h - 0.05, od / 2); b.box(ctx, Gd, ow + 0.3, 0.18, 0.3, 0, h - 0.05, -od / 2);
  for (const s of [-1, 1]) b.box(ctx, Gd, 0.3, 0.18, od, s * ow / 2, h - 0.05, 0);
  for (let z = -d / 2 + 4; z <= d / 2 - 1.9; z += 6.5) b.box(ctx, Tm, w, 0.55, 0.5, 0, h - 0.3, z);
  b.box(ctx, Tm, w + 2, 1.1, 1.2, 0, h - 0.55, d / 2 + 0.4);
  b.add(archPanelGeo(ctx, w * 0.5, h * 0.36, w * 0.21, 0.3, 0.6), Tm, T(0, h * 0.64 - 0.6, d / 2 + 0.4));
  // exterior crown: parapets + chhatris
  b.within(T(0, h + rt, d / 2 + 0.7), () => parapet(b, ctx, Tm, w + 1.6, 1.2, 0.6));
  b.within(T(0, h + rt, -d / 2 - 0.7), () => parapet(b, ctx, Tm, w + 1.6, 1.2, 0.6));
  for (const s of [-1, 1]) b.within(T(s * (w / 2 + 0.7), h + rt, 0, 0, Math.PI / 2, 0), () => parapet(b, ctx, Tm, d + 1.6, 1.2, 0.6));
  for (const [sx, sz] of [[-1, 1], [1, 1], [-1, -1], [1, -1]]) b.within(T(sx * (w / 2 - 1.2), h + rt, sz * (d / 2 - 1.2)), () => chhatri(b, ctx, S, Gd, 2.8));
  // dais (top at y = 1.2, centred at z = -d/2 + 4) with torana backdrop and niche
  if (bool(p.dais, true)) {
    for (let i = 0; i < 3; i++) b.box(ctx, i === 2 ? Tm : S, 11 - 1.4 * i, 0.4, 6.4 - 1.1 * i, 0, 0.2 + 0.4 * i, daisZ - 0.55 * i + 0.55);
    b.box(ctx, Gd, 8.2, 0.05, 0.12, 0, 1.2, daisZ + 1.55);
    b.box(ctx, darkM(ctx, maya ? '#1d2440' : '#24170e'), 6, 7, 0.05, 0, 3.5 + 0.2, -d / 2 + 0.03);
    b.add(archPanelGeo(ctx, 8.6, 8.4, 2.9, 4.4, 0.5), Tm, T(0, 0, -d / 2 + 0.25));
    b.within(T(0, 1.2, daisZ - 2.4), () => torana(b, ctx, Tm, Gd, 7, 6.6));
  }
  b.build(g, ctx, `hall:${JSON.stringify([w, d, h, base, trim, accent, floorC, reflective, maya, p.carpet, p.carpetColor, p.carpet_color, bool(p.dais, true), qname(ctx)])}`, { cast: true, receive: true });
  if (maya) { // crystalline glow along the skylight: a single soft emissive frame
    const glow = new THREE.Mesh(G.box(ctx), mat(ctx, { color: '#9DB7E8', emissive: '#7FA2E6', ei: 0.9, roughness: 0.2, metalness: 0.2 }));
    glow.scale.set(ow - 0.4, 0.06, 0.06); glow.position.set(0, h - 0.18, 0);
    g.add(glow);
  }
  g.userData.anchors = { dais: [0, 1.2, daisZ] };
  return g;
}

// ================================================================ corridor
// Runs along x by default, matching the registry footprint ([30, 8, 6]); the voyage data rotates corridors ±90°
// to flank the approach. `axis: 'z'` builds it along z. The run is built along z and turned on an inner group.
function corridor(params, ctx) {
  const p = P(params);
  const L = num(p.length, 40, 8, 160), W = num(p.width, 6, 3, 20), h = num(p.height, 5.5, 3, 14);
  const base = baseColor(p, PAL.sandstone), trim = trimOf(p, base), accent = accentColor(p, PAL.gold);
  const S = stone(ctx, base), Tm = stone(ctx, trim), Gd = goldM(ctx, accent);
  const root = group('corridor', 'architecture');
  const g = new THREE.Group(); g.name = 'corridor_run';
  if (str(p.axis, 'x') === 'x') g.rotation.y = Math.PI / 2;
  root.add(g);
  const b = new Batch();
  b.box(ctx, stone(ctx, darken(base, 0.08)), W + 2, 0.3, L, 0, -0.14, 0);
  b.box(ctx, Tm, 1.2, 0.02, L - 1, 0, 0.015, 0);
  const n = Math.max(2, Math.floor(L / 4) + 1), bay = (L - 1) / (n - 1), ph = h - 0.55;
  for (let i = 0; i < n; i++) {
    const z = -(L - 1) / 2 + i * bay;
    for (const s of [-1, 1]) pillar(b, ctx, S, Tm, s * W / 2, 0, z, ph * 0.95, 0.27);
  }
  for (const s of [-1, 1]) {
    b.box(ctx, Tm, 0.75, 0.6, L, s * W / 2, h - 0.3, 0);
    const side = archPanelGeo(ctx, bay - 0.55, h * 0.34, (bay - 0.55) / 2 - 0.08, 0.15, 0.35);
    for (let i = 0; i < n - 1; i++) b.add(side, Tm, T(s * W / 2, h - 0.6 - h * 0.34, -(L - 1) / 2 + (i + 0.5) * bay, 0, Math.PI / 2, 0));
    b.within(T(s * (W / 2 + 0.85), h + 0.4, 0, 0, Math.PI / 2, 0), () => parapet(b, ctx, Tm, L, 1.1, 0.45));
  }
  const cross = archPanelGeo(ctx, W + 0.4, h * 0.4, W / 2 - 0.4, 0.2, 0.4);
  for (let i = 0; i < n; i += 2) {
    const z = -(L - 1) / 2 + i * bay;
    b.add(cross, Tm, T(0, h - 0.6 - h * 0.4 + 0.1, z));
    b.add(G.cyl(ctx, 4), Gd, T(0, h - 1.1 - h * 0.2, z, 0, 0, 0, 0.03, h * 0.18, 0.03));
    b.add(G.sphere(ctx, 8, 6), glowM(ctx, '#FFC873', 1.5), T(0, h - 1.35 - h * 0.3, z, 0, 0, 0, 0.2));
  }
  b.box(ctx, S, W + 2.4, 0.4, L + 0.4, 0, h + 0.2, 0);
  b.build(g, ctx, `corridor:${JSON.stringify([L, W, h, base, trim, accent, qname(ctx)])}`);
  return root;
}

// ================================================================ gate (city gate / gopura-torana)
function gate(params, ctx) {
  const p = P(params);
  const h = num(p.height, 16, 6, 40), ow = num(p.opening, h * 0.44, 2, 20), tw = h * 0.4, td = h * 0.47;
  const base = baseColor(p, PAL.sandstone), trim = trimOf(p, base), accent = accentColor(p, PAL.gold);
  const S = stone(ctx, base), Tm = stone(ctx, trim), Gd = goldM(ctx, accent), J = jaaliMat(ctx, trim);
  const g = group('gate', 'architecture'), b = new Batch();
  // towers
  for (const s of [-1, 1]) {
    const x = s * (ow / 2 + tw / 2);
    const h1 = h * 0.58, h2 = h * 0.17;
    b.box(ctx, S, tw + 0.6, 0.8, td + 0.6, x, 0.4, 0);
    b.box(ctx, S, tw, h1, td, x, h1 / 2, 0);
    b.box(ctx, Tm, tw + 0.3, 0.3, td + 0.3, x, h1, 0);
    b.box(ctx, S, tw * 0.84, h2, td * 0.84, x, h1 + h2 / 2, 0);
    b.box(ctx, Tm, tw * 0.84 + 0.3, 0.25, td * 0.84 + 0.3, x, h1 + h2, 0);
    for (const px of [-0.38, 0.38]) b.box(ctx, Tm, 0.35, h1 - 1, 0.25, x + px * tw, h1 / 2 + 0.4, td / 2 + 0.1);
    b.within(T(x, h1 * 0.62, td / 2), () => jaaliWindow(b, ctx, Tm, J, tw * 0.4, h * 0.11));
    b.within(T(x, h1 + h2, 0), () => chhatri(b, ctx, S, Gd, tw * 0.62));
  }
  // central arched gateway block + chamber above
  const spring = h * 0.36, gh = h * 0.7;
  b.add(archPanelGeo(ctx, ow + 0.4, gh, ow / 2 - 0.3, spring, td * 0.9, 9), S, T(0, 0, 0));
  b.box(ctx, Tm, ow + 0.6, 0.3, td * 0.95, 0, gh, 0);
  b.box(ctx, S, ow + 1.6, h * 0.12, td * 0.7, 0, gh + h * 0.06 + 0.15, 0);
  b.within(T(0, gh + h * 0.07, td * 0.35), () => jaaliWindow(b, ctx, Tm, J, ow * 0.6, h * 0.07));
  b.within(T(0, gh + h * 0.12 + 0.3, td * 0.35), () => parapet(b, ctx, Tm, ow + 1.6, 1.0, 0.5));
  b.within(T(0, gh + h * 0.12 + 0.3, 0), () => dome(b, ctx, S, Gd, ow * 0.18));
  // door leaves, hinged at the jambs and swung inward
  if (bool(p.doors, true)) {
    const open = num(p.open, 0.65, 0, 1), lw = ow / 2 - 0.3, lh = spring, W = woodM(ctx, col(p, ['door_color', 'doorColor'], PAL.woodDark));
    for (const s of [-1, 1]) {
      const a = s * open * 1.6;
      b.within(T(s * (ow / 2 - 0.3), 0, -td * 0.2, 0, a, 0), () => {
        b.box(ctx, W, lw, lh, 0.22, -s * lw / 2, lh / 2, 0);
        for (let r = 1; r < 6; r++) b.box(ctx, Gd, lw * 0.96, 0.1, 0.26, -s * lw / 2, (r / 6) * lh, 0);
      });
    }
  }
  // flanking wall stubs
  for (const s of [-1, 1]) {
    const x = s * (ow / 2 + tw + 3);
    b.box(ctx, S, 6, h * 0.4, td * 0.5, x, h * 0.2, -td * 0.1);
    b.within(T(x, h * 0.4, td * 0.15 - td * 0.1), () => parapet(b, ctx, Tm, 6, 1.1, 0.5));
  }
  b.build(g, ctx, `gate:${JSON.stringify([h, ow, base, trim, accent, bool(p.doors, true), p.open, p.door_color, qname(ctx)])}`);
  if (bool(p.banners, true)) for (const s of [-1, 1]) addBanner(g, ctx, s * (ow / 2 + tw / 2) + s * tw * 0.3, h * 0.75, -td * 0.3, col(p, ['bannerColor', 'banner_color'], PAL.vermilion), accent, p.emblem || 'sun', 4);
  return g;
}

// ================================================================ walls (city walls with bastions)
function walls(params, ctx) {
  const p = P(params);
  const L = num(p.length, 80, 10, 600), h = num(p.height, 8, 2, 30), th = num(p.thickness, 3, 0.6, 10);
  const towers = int(p.towers, 0, 0, 200); // registry gives a tower count instead of a spacing
  const spacing = num(p.spacing, towers > 1 ? L / (towers - 1) : 16, 6, 60), R = num(p.radius, 0, 0, 2000), arcDeg = num(p.arc, 0, 0, 360);
  const base = baseColor(p, PAL.sandstone), trim = trimOf(p, base), accent = accentColor(p, PAL.gold);
  const S = stone(ctx, base), Tm = stone(ctx, trim), Gd = goldM(ctx, accent);
  const g = group('walls', 'architecture'), b = new Batch();
  const pts = [];
  if (R > 0 && arcDeg > 0) { // convex toward +z, ends curving away
    const arc = (arcDeg * Math.PI) / 180, n = Math.max(2, Math.round((R * arc) / spacing));
    for (let i = 0; i <= n; i++) { const t = -arc / 2 + (i / n) * arc; pts.push([R * Math.sin(t), -R + R * Math.cos(t)]); }
  } else {
    const n = Math.max(1, Math.round(L / spacing));
    for (let i = 0; i <= n; i++) pts.push([-L / 2 + (i / n) * L, 0]);
  }
  const bs = seg(ctx, 8, 10, 14);
  for (let i = 0; i < pts.length; i++) {
    const [x, z] = pts[i];
    if (i < pts.length - 1) {
      const [x2, z2] = pts[i + 1], len = Math.hypot(x2 - x, z2 - z), ang = Math.atan2(-(z2 - z), x2 - x);
      b.within(T((x + x2) / 2, 0, (z + z2) / 2, 0, ang, 0), () => {
        b.box(ctx, S, len, h, th, 0, h / 2, 0);
        b.box(ctx, Tm, len, 0.3, th + 0.3, 0, h * 0.35, 0);
        b.within(T(0, h, th / 2 - 0.25), () => parapet(b, ctx, Tm, len, 1.3, 0.75));
      });
    }
    // bastion
    b.add(G.cyl(ctx, bs), S, T(x, h * 0.575, z, 0, 0, 0, th * 1.9, h * 1.15, th * 1.9));
    b.add(G.cyl(ctx, bs), Tm, T(x, h * 1.15, z, 0, 0, 0, th * 2.1, 0.35, th * 2.1));
    b.add(G.cyl(ctx, bs), Tm, T(x, h * 0.35, z, 0, 0, 0, th * 2.0, 0.3, th * 2.0));
    b.within(T(x, h * 1.15 + 0.15, z), () => dome(b, ctx, S, Gd, th * 0.55));
  }
  b.build(g, ctx, `walls:${JSON.stringify([L, h, th, spacing, R, arcDeg, base, trim, accent, qname(ctx)])}`);
  return g;
}

// ================================================================ courtyard
// W×D footprint: `size` (square), else width/depth, else the registry bounds, else 40×40. Colonnades on the
// back (-z) and both sides; the front (+z, towards the stage) stays open.
function courtyard(params, ctx) {
  const p = P(params);
  const bw = boundsOf(ctx), sq = num(p.size, NaN, 12, 120);
  const W = Number.isFinite(sq) ? sq : num(p.width, bw ? bw[0] : 40, 12, 120);
  const D = Number.isFinite(sq) ? sq : num(p.depth, bw ? bw[2] : 40, 10, 120);
  const h = num(p.height, 5, 3, 12);
  const base = baseColor(p, PAL.sandstone), trim = trimOf(p, base), accent = col(p, ['pattern_color'], accentColor(p, PAL.gold));
  const paving = col(p, ['floor', 'paving'], lighten(base, 0.18));
  const S = stone(ctx, base), Tm = stone(ctx, trim), Gd = goldM(ctx, accent);
  const g = group('courtyard', 'architecture'), b = new Batch();
  b.box(ctx, S, W + 2, 0.3, D + 2, 0, -0.14, 0);
  b.box(ctx, stone(ctx, paving, { roughness: 0.7 }), W - 2, 0.02, D - 2, 0, 0.02, 0);
  // floor mandala inlay (flat; stage stays clear), scaled to fit inside the colonnades
  const km = Math.min(1, Math.max(0.3, (Math.min(W, D) / 2 - 2.5) / 8.6));
  const ringM = goldM(ctx, accent, { roughness: 0.45 });
  for (const [r0, wdt] of [[3, 0.12], [5.2, 0.18], [5.6, 0.06], [8.5, 0.1]]) {
    const r = +(r0 * km).toFixed(2);
    b.add(geo(ctx, `ringflat:${r}:${wdt}`, () => new THREE.RingGeometry(r - wdt / 2, r + wdt / 2, 64, 1).rotateX(-Math.PI / 2)), ringM, T(0, 0.04, 0));
  }
  for (let i = 0; i < 8; i++) { const a = (i / 8) * TAU; b.box(ctx, ringM, 0.1, 0.01, 2.2 * km, Math.sin(a) * 4.1 * km, 0.04, Math.cos(a) * 4.1 * km, 0, a, 0); }
  // colonnades on back + sides ([centre x, centre z, rotation, run length])
  const sides = [[0, -(D / 2 - 2), 0, W], [-(W / 2 - 2), 0, Math.PI / 2, D], [W / 2 - 2, 0, -Math.PI / 2, D]];
  if (!bool(p.back, true)) sides.shift();
  for (const [cx, cz, ry, L] of sides) b.within(T(cx, 0, cz, 0, ry, 0), () => {
    const n = Math.max(3, Math.round(L / 4.5));
    for (let i = 0; i < n; i++) pillar(b, ctx, S, Tm, -L / 2 + 2 + (i * (L - 4)) / (n - 1), 0, 0, h * 0.92, 0.26);
    b.box(ctx, S, L, 0.45, 3.4, 0, h + 0.2, -1.3);
    b.box(ctx, S, L, h, 0.6, 0, h / 2, -2.8);
    b.box(ctx, Tm, L + 0.6, 0.14, 1.4, 0, h + 0.05, 0.7, 0.25, 0, 0);
    b.within(T(0, h + 0.42, 0.2), () => parapet(b, ctx, Tm, L, 1.1, 0.45));
  });
  // corner lamp pillars, kept inside the paving
  const lx = Math.max(1.5, W / 2 - 4.5), lz = Math.max(1.5, D / 2 - 4.5);
  for (const [x, z] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
    b.add(lathe(ctx, 'lamppost', [[0.3, 0], [0.3, 0.15], [0.16, 0.25], [0.12, 1.5], [0.25, 1.6], [0.32, 1.75], [0, 1.78]], 8), S, T(x * lx, 0, z * lz));
    b.add(G.sphere(ctx, 8, 6), glowM(ctx, '#FFC46A', 1.6), T(x * lx, 1.9, z * lz, 0, 0, 0, 0.26));
  }
  b.build(g, ctx, `court:${JSON.stringify([W, D, h, base, trim, accent, paving, bool(p.back, true), qname(ctx)])}`);
  g.userData.footprint = [W + 2, D + 2];
  return g;
}

// ================================================================ temple (Nagara style)
function temple(params, ctx) {
  const p = P(params);
  const k = num(p.size, 1, 0.3, 4);
  const base = baseColor(p, PAL.sandstone), trim = trimOf(p, base), accent = accentColor(p, PAL.gold);
  const S = stone(ctx, base), Tm = stone(ctx, trim), Gd = goldM(ctx, accent);
  const Sf = stone(ctx, base, { flat: true });
  const g = group('temple', 'architecture'), b = new Batch();
  b.within(T(0, 0, 0, 0, 0, 0, k), () => {
    const y = plinth(b, ctx, S, 14, 22, 3, 0.5, 0.6);
    stairs(b, ctx, S, 4, y, 11);
    // garbhagriha + shikhara
    const gz = -4;
    b.box(ctx, S, 7, 6, 7, 0, y + 3, gz);
    for (const s of [-1, 1]) for (const zz of [-1, 1]) b.box(ctx, Tm, 0.6, 6, 0.6, s * 3.4, y + 3, gz + zz * 3.4);
    b.box(ctx, Tm, 7.6, 0.4, 7.6, 0, y + 6.2, gz);
    b.within(T(0, y + 6.4, gz), () => shikhara(b, ctx, Sf, Gd, 3.6, 12, true));
    b.box(ctx, darkM(ctx), 1.6, 3, 0.05, 0, y + 1.5, gz + 3.52);
    b.add(archPanelGeo(ctx, 2.6, 3.8, 0.8, 2.2, 0.3), Tm, T(0, y, gz + 3.6));
    // mandapa
    const mz = 4.2;
    for (const sx of [-1, 1]) for (let i = 0; i < 4; i++) pillar(b, ctx, S, Tm, sx * 2.6, y, mz - 3 + i * 2.2, 3.4, 0.24);
    b.box(ctx, S, 6.6, 0.4, 8.4, 0, y + 3.8, mz + 0.3);
    for (let i = 0; i < 4; i++) b.box(ctx, i % 2 ? Tm : S, 6.2 - i * 1.3, 0.45, 7.8 - i * 1.6, 0, y + 4.2 + i * 0.45, mz + 0.3);
    b.add(finialGeo(ctx), Gd, T(0, y + 5.95, mz + 0.3, 0, 0, 0, 0.9));
    // dhvaja (flag pole) rising from the crowning kalasha
    b.add(G.cyl(ctx, 6), Gd, T(0, y + 6.4 + 12 * 0.925 + 1.9 + 1.5, gz, 0, 0, 0, 0.07, 3, 0.07));
  });
  b.build(g, ctx, `temple:${JSON.stringify([k, base, trim, accent, qname(ctx)])}`);
  const flagC = col(p, ['bannerColor', 'banner_color'], '#E0832F');
  const fl = OBJECTS.banner({ height: 0.1, poleless: true, color: flagC, accent, emblem: 'none', width: 1.6 * k, flagHeight: 0.8 * k, shape: 'pennant' }, ctx);
  fl.position.set(0, (1.5 + 6.4 + 12 * 0.925 + 1.9 + 3) * k, -4 * k);
  g.add(fl);
  return g;
}

// ================================================================ hermitage / gurukul ashram
function hermitage(params, ctx) {
  const p = P(params);
  const bw = boundsOf(ctx); // hut ring sized to the registry footprint when no radius is given
  const huts = int(p.huts, 3, 1, 9), R = num(p.radius, bw ? Math.max(6, Math.min(bw[0], bw[2]) * 0.45) : 15, 6, 60);
  const hutK = num(p.hut_radius ?? p.hutRadius, 2.2, 1, 8) / 2.2; // huts are 2.2 m in radius at scale 1
  const wallC = col(p, ['wall', 'walls', 'color', 'base', 'materials.base'], '#A8865E'), thatchC = col(p, ['thatch', 'accent', 'roof'], PAL.thatch);
  const W = stone(ctx, wallC, { roughness: 1 }), Th = mat(ctx, { color: thatchC, roughness: 1, flat: true }), Wd = woodM(ctx);
  const g = group('hermitage', 'architecture'), b = new Batch();
  const r = rng(p.seed ?? 'ashram');
  const thatch = lathe(ctx, 'thatch', [[1.25, 0], [1.2, 0.08], [1.0, 0.25], [0.62, 0.62], [0.3, 0.88], [0.08, 1.02], [0, 1.05]], seg(ctx, 8, 10, 12));
  const wallG = G.cyl(ctx, seg(ctx, 8, 10, 12));
  for (let i = 0; i < huts; i++) {
    const a = Math.PI + (huts === 1 ? 0 : (-0.55 + (1.1 * i) / (huts - 1)) * Math.PI * 0.75);
    const x = Math.sin(a) * R, z = Math.cos(a) * R, s = r.range(0.85, 1.15) * hutK;
    b.within(T(x, 0, z, 0, Math.atan2(-x, -z), 0, s), () => {
      b.add(G.cyl(ctx, 10), stone(ctx, '#8C7356'), T(0, 0.1, 0, 0, 0, 0, 4.4, 0.2, 4.4));
      b.add(wallG, W, T(0, 1.15, 0, 0, 0, 0, 3.6, 2.1, 3.6));
      b.add(thatch, Th, T(0, 2.1, 0, 0, 0, 0, 2.2, 2.3, 2.2));
      b.box(ctx, darkM(ctx), 0.9, 1.5, 0.1, 0, 0.95, 1.78);
      b.box(ctx, Wd, 1.15, 0.12, 0.18, 0, 1.75, 1.82);
    });
  }
  // wattle fence arc behind
  const fenceN = qcount(ctx, 40, 14);
  for (let i = 0; i < fenceN; i++) {
    const a = Math.PI * 0.62 + (i / (fenceN - 1)) * Math.PI * 0.76, fr = R + 6;
    b.add(G.cyl(ctx, 4), Wd, T(Math.sin(a) * fr, 0.6, Math.cos(a) * fr, r.range(-0.05, 0.05), 0, r.range(-0.05, 0.05), 0.09, 1.2, 0.09));
  }
  for (const yy of [0.45, 0.95]) for (let i = 0; i < 8; i++) {
    const a0 = Math.PI * 0.62 + (i / 8) * Math.PI * 0.76, a1 = a0 + (Math.PI * 0.76) / 8, fr = R + 6;
    const x0 = Math.sin(a0) * fr, z0 = Math.cos(a0) * fr, x1 = Math.sin(a1) * fr, z1 = Math.cos(a1) * fr;
    b.box(ctx, Wd, Math.hypot(x1 - x0, z1 - z0), 0.06, 0.06, (x0 + x1) / 2, yy, (z0 + z1) / 2, 0, Math.atan2(-(z1 - z0), x1 - x0), 0);
  }
  // kusha grass tufts
  const tufts = qcount(ctx, 60, 16), grass = mat(ctx, { color: '#7C8A45', roughness: 1, flat: true });
  for (let i = 0; i < tufts; i++) { // behind and beside the huts; the open front (+z) faces the stage
    const a = Math.PI + (r() - 0.5) * Math.PI * 1.3, d = r.range(4, R + 5);
    b.add(G.cone(ctx, 4), grass, T(Math.sin(a) * d, 0.18, Math.cos(a) * d, 0, r() * 3, 0, 0.35, 0.36, 0.35));
  }
  // sitting mats around the fire
  for (let i = 0; i < 4; i++) { const a = (i / 4) * TAU + 0.4; b.box(ctx, clothM(ctx, '#8E6A3E', { double: false }), 1.0, 0.03, 0.7, Math.sin(a) * 2.6, 0.02, -5 + Math.cos(a) * 2.6, 0, a, 0); }
  b.build(g, ctx, `herm:${JSON.stringify([huts, R, hutK, wallC, thatchC, p.seed ?? '', qname(ctx)])}`);
  // sacred fire altar + a shading tree
  if (bool(p.fire ?? p.fire_altar, true)) {
    const alt = altar({ size: 0.7, light: bool(p.light, true), intensity: 1.6 }, ctx);
    alt.position.set(0, 0, -5); g.add(alt);
  }
  if (bool(p.tree, true)) {
    const tr = OBJECTS.tree({ height: 8, roots: false, seed: 'hermtree', foliage: col(p, ['foliage'], undefined) }, ctx);
    tr.position.set(R * 0.75, 0, -R * 0.9); g.add(tr);
  }
  return g;
}

// ================================================================ camp (battle camp)
function camp(params, ctx) {
  const p = P(params);
  const n = int(p.tents, 9, 1, 30), R = num(p.radius, 22, 8, 100);
  const c1 = col(p, ['tent_color', 'tentColor'], baseColor(p, '#E7DCC3')), c2 = col(p, ['pennant'], accentColor(p, PAL.vermilion));
  const g = group('camp', 'architecture'), b = new Batch();
  const r = rng(p.seed ?? 'camp');
  const A = clothM(ctx, c1, { double: false }), B = clothM(ctx, c2, { double: false }), Gd = goldM(ctx), Wd = woodM(ctx);
  const tentAt = (x, z, s, big) => b.within(T(x, 0, z, 0, Math.atan2(-x, -z), 0, s), () => {
    const wdt = big ? 6 : 4;
    b.box(ctx, A, wdt, 1.7, wdt, 0, 0.85, 0);
    b.box(ctx, B, wdt + 0.05, 0.3, wdt + 0.05, 0, 1.55, 0);
    b.add(G.cone(ctx, 4), big ? B : A, T(0, 1.7 + 1.3, 0, 0, Math.PI / 4, 0, wdt * 1.45, 2.6, wdt * 1.45));
    b.add(G.cyl(ctx, 4), Wd, T(0, 3.8, 0, 0, 0, 0, 0.07, 2.2, 0.07));
    b.add(finialGeo(ctx), Gd, T(0, 4.9, 0, 0, 0, 0, 0.35));
    b.box(ctx, darkM(ctx), wdt * 0.32, 1.3, 0.05, 0, 0.65, wdt / 2 + 0.01);
  });
  tentAt(0, -R, 1.15, true);
  for (let i = 0; i < n - 1; i++) {
    const a = Math.PI + (i % 2 ? 1 : -1) * (0.35 + Math.floor(i / 2) * 0.32) + r.range(-0.06, 0.06);
    const d = R + r.range(-3, 5);
    tentAt(Math.sin(a) * d, Math.cos(a) * d, r.range(0.85, 1.1), false);
  }
  // spear racks
  for (let k = 0; k < 4; k++) {
    const a = Math.PI + (k - 1.5) * 0.4, d = R - 6;
    b.within(T(Math.sin(a) * d, 0, Math.cos(a) * d, 0, a, 0), () => {
      b.box(ctx, Wd, 2.4, 0.1, 0.1, 0, 1.2, 0);
      for (let s = 0; s < 6; s++) b.add(G.cyl(ctx, 4), Wd, T(-1.0 + s * 0.4, 1.3, 0.1, 0.18, 0, 0, 0.04, 2.6, 0.04));
    });
  }
  b.build(g, ctx, `camp:${JSON.stringify([n, R, c1, c2, p.seed ?? '', qname(ctx)])}`);
  // camp fires (two, no lights unless asked) + banners
  for (const s of [-1, 1]) {
    const f = flame(ctx, { size: 0.55, light: bool(p.light, false), seed: s });
    f.position.set(s * 6, 0.05, -R + 7); g.add(f);
    const logs = new THREE.Mesh(G.cyl(ctx, 5), woodM(ctx, PAL.woodDark));
    logs.scale.set(0.16, 1.1, 0.16); logs.rotation.z = Math.PI / 2; logs.position.set(s * 6, 0.08, -R + 7); g.add(logs);
  }
  const bc = col(p, ['bannerColor', 'banner_color'], c2);
  for (const [x, z] of [[-10, -R + 5], [10, -R + 5], [0, -R - 6]]) addBanner(g, ctx, x, 0, z, bc, PAL.gold, p.emblem || 'yantra', 5.5);
  return g;
}

// ================================================================ shelter (chariot shelter pavilion)
function shelter(params, ctx) {
  const p = P(params);
  const w = num(p.width, 10, 4, 30), d = num(p.depth, 7, 3, 20), h = num(p.height, 4.2, 2.5, 10);
  const base = col(p, ['timber', 'wood'], baseColor(p, PAL.wood)), roofC = col(p, ['roof', 'thatch', 'accent'], PAL.thatch);
  const g = group('shelter', 'architecture'), b = new Batch();
  const S = stone(ctx, PAL.sandstone), Wd = woodM(ctx, base), Rf = mat(ctx, { color: roofC, roughness: 1, flat: true });
  b.box(ctx, S, w + 1, 0.35, d + 1, 0, 0.175, 0);
  for (const sx of [-1, 0, 1]) for (const sz of [-1, 1]) pillar(b, ctx, Wd, Wd, sx * (w / 2 - 0.4), 0.35, sz * (d / 2 - 0.4), h - 0.6, 0.18);
  for (const sz of [-1, 1]) b.box(ctx, Wd, w, 0.3, 0.3, 0, h + 0.1, sz * (d / 2 - 0.4));
  const slope = Math.atan2(1.6, d / 2 + 0.6);
  for (const s of [-1, 1]) b.box(ctx, Rf, w + 1.4, 0.25, Math.hypot(1.6, d / 2 + 0.8), 0, h + 0.3 + 0.8, s * (d / 4 + 0.2), s * slope, 0, 0);
  b.box(ctx, Wd, w + 1.5, 0.22, 0.3, 0, h + 1.95, 0);
  b.build(g, ctx, `shelter:${JSON.stringify([w, d, h, base, roofC, qname(ctx)])}`);
  return g;
}

// ================================================================ balcony (jharokha on a wall)
function balcony(params, ctx) {
  const p = P(params);
  const w = num(p.width, 6, 2, 20), fy = num(p.floorHeight ?? p.floor_height, 5, 1, 30), wallW = num(p.wallWidth ?? p.wall_width, w + 6, w, 80);
  const base = baseColor(p, PAL.sandstone), trim = trimOf(p, base), accent = accentColor(p, PAL.gold);
  const S = stone(ctx, base), Tm = stone(ctx, trim), Gd = goldM(ctx, accent), J = jaaliMat(ctx, trim);
  const g = group('balcony', 'architecture'), b = new Batch();
  const wallH = fy + 5.5, dep = 2.6;
  if (bool(p.wall, true)) {
    b.box(ctx, S, wallW, wallH, 1, 0, wallH / 2, -0.5);
    b.box(ctx, Tm, wallW + 0.4, 0.3, 1.3, 0, wallH, -0.4);
    b.within(T(0, wallH + 0.15, -0.1), () => parapet(b, ctx, Tm, wallW, 1.1, 0.5));
    for (const s of [-1, 1]) b.within(T(s * (w / 2 + 2), fy - 2.2, 0), () => jaaliWindow(b, ctx, Tm, J, 1.2, 1.8));
  }
  // corbels + floor
  for (const s of [-1, 0, 1]) for (let k = 0; k < 3; k++) b.box(ctx, Tm, 0.45, 0.35, dep * (1 - k * 0.28), s * (w / 2 - 0.5), fy - 0.35 - k * 0.35, dep * (1 - k * 0.28) / 2);
  b.box(ctx, S, w, 0.35, dep, 0, fy - 0.17, dep / 2);
  // jaali parapet front + sides
  const rh = 1.05;
  b.add(jaaliGeo(ctx, w - 0.4, rh), J, T(0, fy + rh / 2, dep - 0.1));
  for (const s of [-1, 1]) b.add(jaaliGeo(ctx, dep - 0.3, rh), J, T(s * (w / 2 - 0.12), fy + rh / 2, dep / 2, 0, Math.PI / 2, 0));
  b.box(ctx, Tm, w + 0.1, 0.12, 0.3, 0, fy + rh, dep - 0.1);
  // slender pillars + curved bangaldar roof
  const ph = 2.5;
  for (const s of [-1, 1]) for (const z of [0.3, dep - 0.2]) pillar(b, ctx, Tm, Tm, s * (w / 2 - 0.25), fy, z, ph, 0.12);
  const roof = geo(ctx, `bangla:${w}:${dep}`, () => new THREE.CylinderGeometry(1, 1, 1, 14, 1, false, 0, Math.PI).rotateZ(Math.PI / 2));
  b.add(roof, S, T(0, fy + ph + 0.2, dep / 2, 0, 0, 0, w + 0.6, 0.75, dep / 2 + 0.4));
  b.box(ctx, Tm, w + 0.9, 0.12, dep + 1.0, 0, fy + ph + 0.18, dep / 2);
  for (const s of [-1, 0, 1]) b.add(finialGeo(ctx), Gd, T(s * w * 0.32, fy + ph + 0.9, dep / 2, 0, 0, 0, s ? 0.4 : 0.6));
  // doorway into the wall
  b.box(ctx, darkM(ctx), 1.8, 2.4, 0.04, 0, fy + 1.2, 0.02);
  b.add(archPanelGeo(ctx, 2.6, 3.0, 0.9, 1.6, 0.2), Tm, T(0, fy, 0.1));
  b.build(g, ctx, `balcony:${JSON.stringify([w, fy, wallW, base, trim, accent, p.wall, qname(ctx)])}`);
  g.userData.anchors = { floor: [0, fy, dep / 2] };
  return g;
}

// ================================================================ arena (swayamvara amphitheatre)
function arena(params, ctx) {
  const p = P(params);
  const tiers = int(p.tiers, 5, 2, 12), tierD = num(p.tierDepth, 2.4, 1, 6), tierH = 0.9;
  // inner radius, or derived from an outer `radius` (registry) so the bowl matches its declared footprint
  const Ri = num(p.innerRadius ?? p.inner_radius, p.radius !== undefined ? num(p.radius, 30, 12, 90) - tiers * tierD : 18, 8, 80);
  const gapDeg = num(p.gap, 50, 0, 180);
  const base = baseColor(p, PAL.sandstone), trim = trimOf(p, base), accent = accentColor(p, PAL.gold);
  const S = stone(ctx, base, { double: true }), Tm = stone(ctx, trim), Gd = goldM(ctx, accent);
  const g = group('arena', 'architecture'), b = new Batch();
  const pts = [[Ri, 0]];
  for (let i = 0; i < tiers; i++) { pts.push([Ri + i * tierD, (i + 1) * tierH], [Ri + (i + 1) * tierD, (i + 1) * tierH]); }
  const Ro = Ri + tiers * tierD, top = tiers * tierH;
  pts.push([Ro, 0]);
  const gap = (gapDeg * Math.PI) / 180, phiLen = TAU - gap;
  b.add(lathe(ctx, `arena:${Ri}:${tiers}:${tierD}`, pts, seg(ctx, 28, 44, 64), gap / 2, phiLen), S, T());
  // gilded tier edges
  for (let i = 0; i < tiers; i++) {
    const rr = Ri + i * tierD;
    b.add(geo(ctx, `arenaEdge:${rr}:${gapDeg}`, () => new THREE.TorusGeometry(rr, 0.06, 4, 64, phiLen)), Gd, T(0, (i + 1) * tierH, 0, -Math.PI / 2, 0, gap / 2 - Math.PI / 2));
  }
  // kings' chhatris along the top tier + royal pavilion at the back
  const nC = qname(ctx) === 'low' ? 6 : 10;
  for (let i = 0; i < nC; i++) {
    const a = gap / 2 + 0.15 + (i / (nC - 1)) * (phiLen - 0.3);
    if (Math.abs(a - Math.PI) < 0.2) continue;
    b.within(T(Math.sin(a) * (Ro - tierD / 2), top, Math.cos(a) * (Ro - tierD / 2), 0, a, 0), () => chhatri(b, ctx, Tm, Gd, 2.2));
  }
  b.within(T(0, top, -(Ro - tierD * 0.8)), () => {
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) pillar(b, ctx, Tm, Tm, sx * 2.4, 0, sz * 1.4, 3.4, 0.2);
    b.box(ctx, Tm, 5.8, 0.3, 3.8, 0, 3.75, 0);
    b.within(T(0, 3.9, 0), () => dome(b, ctx, Tm, Gd, 1.9));
  });
  // central ground: sand disc + entrance torana at the open side
  b.add(geo(ctx, `disc:${Ri}`, () => new THREE.CircleGeometry(Ri, 48).rotateX(-Math.PI / 2)), stone(ctx, col(p, ['floor', 'ground'], '#CDB48C'), { roughness: 1 }), T(0, 0.02, 0));
  // entrance torana mid-way through the open side (at the outer rim it would stand inside the stage disc
  // when the arena is placed ~40 m in front of the origin)
  const zt = (Ri + Ro) / 2;
  if (gapDeg > 20) b.within(T(0, 0, zt), () => torana(b, ctx, Tm, Gd, Math.min(10, 2 * zt * Math.sin(gap / 2) * 0.7), 8));
  b.build(g, ctx, `arena:${JSON.stringify([Ri, tiers, tierD, gapDeg, base, trim, accent, p.floor, qname(ctx)])}`);
  // banners between pavilions
  const bc = col(p, ['bannerColor', 'banner_color'], PAL.vermilion);
  const nb = qname(ctx) === 'low' ? 4 : 8;
  for (let i = 0; i < nb; i++) {
    const a = gap / 2 + 0.3 + ((i + 0.5) / nb) * (phiLen - 0.6);
    addBanner(g, ctx, Math.sin(a) * (Ro - 0.6), top, Math.cos(a) * (Ro - 0.6), i % 2 ? bc : PAL.gold, i % 2 ? PAL.gold : bc, 'sun', 5);
  }
  return g;
}

// ================================================================ city (dense city cluster, e.g. Dwaraka)
function city(params, ctx) {
  const p = P(params);
  const R = num(p.radius, 45, 10, 400), inner = num(p.inner ?? p.innerRadius, 16, 0, R - 4);
  const n = qcount(ctx, int(p.count ?? p.buildings, 90, 8, 600), 14);
  const base = baseColor(p, '#EFE6D3'), accent = accentColor(p, PAL.gold);
  const r = rng(p.seed ?? 'city');
  const g = group('city', 'architecture');
  const boxG = G.box(ctx), domeG = domeGeo(ctx), spireG = shikharaGeo(ctx);
  const blocks = new THREE.InstancedMesh(boxG, stone(ctx, '#ffffff'), n);
  const domes = new THREE.InstancedMesh(domeG, goldM(ctx, accent), n);
  const spN = Math.max(3, Math.round(n * 0.12));
  const spires = new THREE.InstancedMesh(spireG, stone(ctx, '#ffffff', { flat: true }), spN);
  const m = new THREE.Matrix4(), c = new THREE.Color(), bc = new THREE.Color(base);
  let dn = 0;
  for (let i = 0; i < n; i++) {
    const a = r() * TAU, d = Math.sqrt(r.range((inner / R) ** 2, 1)) * R;
    const x = Math.sin(a) * d, z = Math.cos(a) * d;
    const centre = 1 - d / R;
    const w = r.range(3, 7), h = r.range(3, 7) + centre * 10, dd = r.range(3, 7);
    m.compose(new THREE.Vector3(x, h / 2, z), new THREE.Quaternion().setFromEuler(new THREE.Euler(0, a + r.range(-0.2, 0.2), 0)), new THREE.Vector3(w, h, dd));
    blocks.setMatrixAt(i, m);
    c.copy(bc).offsetHSL(r.range(-0.015, 0.015), r.range(-0.05, 0.05), r.range(-0.07, 0.04));
    blocks.setColorAt(i, c);
    if (r() < 0.45) {
      const dr = Math.min(w, dd) * 0.38;
      m.compose(new THREE.Vector3(x, h, z), new THREE.Quaternion(), new THREE.Vector3(dr, dr, dr));
      domes.setMatrixAt(dn++, m);
    }
  }
  domes.count = dn;
  for (let i = 0; i < spN; i++) {
    const a = (i / spN) * TAU + r.range(-0.2, 0.2), d = r.range(inner + 4, R * 0.7);
    const H = r.range(14, 24);
    m.compose(new THREE.Vector3(Math.sin(a) * d, 0, Math.cos(a) * d), new THREE.Quaternion(), new THREE.Vector3(3.4, H, 3.4));
    spires.setMatrixAt(i, m);
    spires.setColorAt(i, c.copy(bc).offsetHSL(0, 0, 0.03));
  }
  for (const im of [blocks, domes, spires]) { im.castShadow = true; im.receiveShadow = true; im.instanceMatrix.needsUpdate = true; if (im.instanceColor) im.instanceColor.needsUpdate = true; im.computeBoundingSphere(); g.add(im); }
  return g;
}

// ================================================================ altar (yajna vedi with living fire)
function altar(params, ctx) {
  const p = P(params);
  // `size` up to 2.5 is a scale factor on the 3 m vedi; larger values (registry: size 4) are metres
  const sz = num(p.size, 1, 0.2, 15);
  const k = sz > 2.5 ? Math.min(5, sz / 3) : sz;
  const brick = col(p, ['brick'], baseColor(p, PAL.brick)), accent = accentColor(p, PAL.gold);
  const g = group('altar', 'architecture'), b = new Batch();
  const Bm = stone(ctx, brick, { roughness: 0.95 }), Tm = stone(ctx, lighten(brick, 0.2)), Gd = goldM(ctx, accent);
  b.within(T(0, 0, 0, 0, 0, 0, k), () => {
    b.box(ctx, Bm, 3, 0.22, 3, 0, 0.11, 0);
    b.box(ctx, Tm, 2.5, 0.2, 2.5, 0, 0.32, 0);
    b.box(ctx, Bm, 2.0, 0.2, 2.0, 0, 0.52, 0);
    b.box(ctx, darkM(ctx, '#1b0f08'), 1.2, 0.02, 1.2, 0, 0.625, 0);
    for (const [x, z] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) b.add(finialGeo(ctx), Gd, T(x * 1.32, 0.22, z * 1.32, 0, 0, 0, 0.32));
    b.add(G.torus(ctx, 0.06, 4, 32), mat(ctx, { color: '#8F9A4A', roughness: 1 }), T(0, 0.24, 0, Math.PI / 2, 0, 0, 3.4, 3.4, 1));
    for (let i = 0; i < 6; i++) b.add(G.cyl(ctx, 5), woodM(ctx, PAL.woodDark), T(0, 0.68, 0, Math.PI / 2, 0, (i / 6) * Math.PI, 0.1, 1.1, 0.1));
  });
  b.build(g, ctx, `altar:${JSON.stringify([k, brick, accent])}`);
  const f = flame(ctx, { size: 0.65 * k, color: col(p, ['flame'], undefined), light: bool(p.light, true), intensity: num(p.intensity, 2.4), seed: 'altar' });
  f.position.y = 0.66 * k;
  g.add(f);
  g.userData.anchors = { fire: [0, 0.66 * k, 0] };
  return g;
}

export const ARCHITECTURE = { palace, hall, corridor, gate, walls, courtyard, temple, hermitage, camp, shelter, balcony, arena, city, altar };
