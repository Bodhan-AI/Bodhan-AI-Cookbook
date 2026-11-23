// Object factories: bow, chakra, conch, crown, scroll, dice, throne, weapon, banner, shield, arrows, drum, door,
// lamp, tree, target, arrow_bed, mace, vessel, pyre. Hand-held objects are sized for ~1.8-unit figures and built
// with their grip at the origin so figures.js can attach them to a hand.
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import {
  THREE, PAL, P, num, int, bool, str, col, hex, baseColor, accentColor, seg, qcount, qname, rng, G, lathe, geo, T, rod,
  Batch, mat, stone, goldM, clothM, woodM, darkM, glowM, addUpdate, flame, softDotTex, waterMat, flowWater, noRaycast,
} from './kit.js';

const TAU = Math.PI * 2;
const FINIAL_PTS = [[0, 0], [0.18, 0], [0.22, 0.08], [0.12, 0.16], [0.3, 0.3], [0.32, 0.42], [0.2, 0.52], [0.08, 0.58], [0.14, 0.66], [0.06, 0.72], [0.025, 1], [0, 1]];
const kalasha = (ctx) => lathe(ctx, 'finial', FINIAL_PTS, seg(ctx, 6, 8, 10));
function group(name, kind = 'object') { const g = new THREE.Group(); g.name = name; g.userData.kind = kind; return g; }
function glowSprite(color, size, opacity = 0.5) {
  const tex = softDotTex();
  if (!tex) return null;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, color, transparent: true, opacity, blending: THREE.AdditiveBlending, depthWrite: false }));
  s.scale.set(size, size, 1);
  s.userData.baseSize = size;
  return noRaycast(s);
}
/** Breathe a glow sprite by scale (never by opacity: engine fades work on cloned materials and must win). */
const pulseSprite = (s, k) => { if (s) s.scale.setScalar(s.userData.baseSize * k); };

// ================================================================ banner (dhvaja) with fluttering cloth
function banner(params, ctx) {
  const p = P(params);
  const H = num(p.height ?? p.pole_height ?? p.poleHeight, 4.5, 0, 40);
  const poleless = bool(p.poleless, false) || H < 0.3;
  const fw = num(p.width ?? p.cloth_width, 1.6, 0.2, 14), fh = num(p.flagHeight ?? p.flag_height ?? p.cloth_height, fw * 0.56, 0.1, 10);
  const color = baseColor(p, PAL.vermilion), accent = accentColor(p, PAL.gold);
  const rawEmblem = str(p.emblem, 'yantra');
  const emblem = /kapi|monkey|vanara|hanuman/.test(rawEmblem) ? 'monkey' : /naga|serpent|cobra|elephant/.test(rawEmblem) ? 'serpent' : rawEmblem;
  const shape = str(p.shape, 'swallowtail', ['rect', 'pennant', 'swallowtail']);
  const amp = num(p.flutter, 0.16, 0, 1) * fw * num(p.wind, 1, 0, 3);
  const g = group('banner');
  const top = poleless ? 0 : H;
  if (!poleless) {
    const b = new Batch();
    b.add(G.cyl(ctx, 6), woodM(ctx, col(p, ['pole', 'poleColor'], PAL.woodDark)), T(0, H / 2, 0, 0, 0, 0, Math.max(0.05, H * 0.014), H, Math.max(0.05, H * 0.014)));
    for (const y of [0.25, 0.55, 0.85]) b.add(G.cyl(ctx, 6), goldM(ctx, accent), T(0, H * y, 0, 0, 0, 0, H * 0.022, 0.06, H * 0.022));
    b.add(kalasha(ctx), goldM(ctx, accent), T(0, H, 0, 0, 0, 0, Math.max(0.18, H * 0.05)));
    b.build(g, ctx, `bannerpole:${H}:${accent}`);
  }
  // cloth: top-left corner at the pole top, extending +x and down
  const sx = seg(ctx, 8, 12, 16), sy = seg(ctx, 3, 5, 6);
  const cloth = new THREE.PlaneGeometry(fw, fh, sx, sy);
  cloth.translate(fw / 2, -fh / 2, 0);
  const pos = cloth.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i), u = x / fw, yc = pos.getY(i) + fh / 2;
    let y = yc, xx = x;
    if (shape === 'pennant') y = yc * (1 - 0.62 * u);
    if (shape === 'swallowtail') { y = yc * (1 - 0.25 * u); xx = x - (1 - Math.abs(yc) / (fh / 2)) * fw * 0.2 * Math.pow(u, 4); }
    pos.setXY(i, xx, y - fh / 2);
  }
  const basePos = Float32Array.from(pos.array);
  const flagM = clothM(ctx, '#ffffff', { tex: `emblem:${color}:${accent}:${emblem}`, roughness: 0.85 });
  const flag = new THREE.Mesh(cloth, flagM);
  flag.name = 'banner_cloth';
  flag.position.set(poleless ? 0 : 0.04, top - (poleless ? 0 : 0.12), 0);
  flag.castShadow = true;
  flag.userData.flutter = { amplitude: amp, speed: 3.2 };
  g.add(flag);
  const phase = rng(p.seed ?? color + emblem)() * 10;
  let frame = 0;
  addUpdate(g, (dt, t) => {
    if (amp <= 0) return;
    for (let i = 0; i < pos.count; i++) {
      const bx = basePos[i * 3], by = basePos[i * 3 + 1], u = bx / fw;
      const w = Math.sin(u * 5.5 - t * 3.2 + phase) + 0.35 * Math.sin(u * 13 - t * 5.1 + by * 2.5 + phase);
      pos.setZ(i, amp * u * w);
      pos.setY(i, by - amp * 0.15 * u * u * (1 + Math.sin(t * 1.3 + phase)));
    }
    pos.needsUpdate = true;
    if ((frame++ & 1) === 0) cloth.computeVertexNormals();
  });
  g.userData.anchors = { top: [0, top, 0] };
  return g;
}

// ================================================================ bow (Gandiva or generic)
function bow(params, ctx) {
  const p = P(params);
  const L = num(p.length, 1.6, 0.4, 4);
  const divine = bool(p.divine, bool(p.glow, false));
  const wood = col(p, ['wood'], baseColor(p, divine ? '#8A5A2B' : '#6B4426')), accent = col(p, ['gold_bands'], accentColor(p, PAL.gold));
  const g = group('bow');
  const tipY = L * 0.5, tipZ = -L * 0.07, braceZ = -L * 0.075;
  const limb = (s) => [[0, 0, 0], [0, s * L * 0.18, -L * 0.012], [0, s * L * 0.34, -L * 0.05], [0, s * L * 0.46, -L * 0.1], [0, s * tipY, tipZ - L * 0.04], [0, s * (tipY + L * 0.03), tipZ]];
  const pts = [...limb(-1).reverse(), ...limb(1).slice(1)].map(([x, y, z]) => new THREE.Vector3(x, y, z));
  const tube = geo(ctx, `bowtube:${L}:${qname(ctx)}`, () => new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), seg(ctx, 24, 36, 48), L * 0.011, 5));
  const b = new Batch();
  const woodMat = divine ? mat(ctx, { color: wood, roughness: 0.5, metalness: 0.3, emissive: '#7a4a12', ei: 0.35 }) : woodM(ctx, wood);
  b.add(tube, woodMat);
  b.add(G.cyl(ctx, 8), goldM(ctx, accent), T(0, 0, 0, 0, 0, 0, L * 0.034, L * 0.12, L * 0.034));
  for (const s of [-1, 1]) {
    b.add(G.cone(ctx, 6), goldM(ctx, accent), T(0, s * (tipY + L * 0.04), tipZ, s < 0 ? Math.PI : 0, 0, 0, L * 0.02, L * 0.05, L * 0.02));
    b.add(G.cyl(ctx, 8), goldM(ctx, accent), T(0, s * L * 0.3, -L * 0.03, 0, 0, 0, L * 0.03, L * 0.025, L * 0.03));
  }
  b.build(g, ctx, `bow:${L}:${wood}:${accent}:${divine}:${qname(ctx)}`);
  // string: two segments meeting at the nocking point so the draw can animate
  const strM = mat(ctx, { color: '#EDE4CF', roughness: 0.6 });
  const top = [0, tipY + L * 0.03, tipZ], bot = [0, -tipY - L * 0.03, tipZ];
  const s1 = new THREE.Mesh(G.cyl(ctx, 4), strM), s2 = new THREE.Mesh(G.cyl(ctx, 4), strM);
  s1.name = 'bow_string_upper'; s2.name = 'bow_string_lower';
  g.add(s1, s2);
  const setDraw = (v) => {
    const d = Math.min(1, Math.max(0, num(v, 0)));
    const nock = [0, 0, braceZ - d * L * 0.42];
    rod(nock, top, 0.006).decompose(s1.position, s1.quaternion, s1.scale);
    rod(bot, nock, 0.006).decompose(s2.position, s2.quaternion, s2.scale);
    g.userData.draw = d;
  };
  setDraw(p.draw ?? 0);
  g.userData.setDraw = setDraw;
  if (divine) { const gl = glowSprite('#FFD27A', L * 0.9, 0.18); if (gl) g.add(gl); }
  return g;
}

// ================================================================ chakra (Sudarshana): spinning serrated disc
function chakra(params, ctx) {
  const p = P(params);
  const R = num(p.radius, 0.35, 0.05, 6);
  const color = baseColor(p, '#F2C45A'), glowC = col(p, ['glow', 'emissive', 'materials.emissive'], '#FFB43A');
  const g = group('chakra');
  const spin = new THREE.Group(); spin.name = 'chakra_spin';
  spin.userData.spin = { axis: 'z', speed: num(p.speed, 5) };
  const m = mat(ctx, { color, roughness: 0.3, metalness: 0.8, emissive: glowC, ei: num(p.intensity, 0.9) });
  const b = new Batch();
  b.add(G.cyl(ctx, 12), m, T(0, 0, 0, Math.PI / 2, 0, 0, R * 0.32, R * 0.08, R * 0.32));
  b.add(G.torus(ctx, 0.06, 5, 32), m, T(0, 0, 0, 0, 0, 0, R * 1.0));
  b.add(G.torus(ctx, 0.08, 5, 40), m, T(0, 0, 0, 0, 0, 0, R * 1.85));
  const spokes = 12, teeth = seg(ctx, 18, 24, 32);
  for (let i = 0; i < spokes; i++) {
    const a = (i / spokes) * TAU;
    b.box(ctx, m, R * 0.05, R * 0.62, R * 0.04, Math.cos(a) * R * 0.62, Math.sin(a) * R * 0.62, 0, 0, 0, a - Math.PI / 2);
  }
  for (let i = 0; i < teeth; i++) {
    const a = (i / teeth) * TAU;
    b.add(G.cone(ctx, 4), m, T(Math.cos(a) * R * 1.02, Math.sin(a) * R * 1.02, 0, 0, 0, a - Math.PI / 2 - 0.35, R * 0.1, R * 0.22, R * 0.03));
  }
  b.build(spin, ctx, `chakra:${R}:${color}:${glowC}:${qname(ctx)}`, { cast: false });
  g.add(spin);
  const halo = glowSprite(glowC, R * 4.2, 0.45);
  if (halo) g.add(halo);
  const speed = spin.userData.spin.speed;
  addUpdate(g, (dt, t) => {
    spin.rotation.z -= dt * speed;
    pulseSprite(halo, 0.95 + 0.06 * Math.sin(t * 3.1));
  });
  return g;
}

// ================================================================ conch (Panchajanya or generic)
function conch(params, ctx) {
  const p = P(params);
  const L = num(p.length, 0.38, 0.1, 3);
  const color = col(p, ['shell'], baseColor(p, '#F4EDE2')), accent = col(p, ['gold_mount', 'mount'], accentColor(p, PAL.gold));
  const glow = bool(p.glow, bool(p.divine, false));
  const g = group('conch'), b = new Batch();
  const shell = mat(ctx, { color, roughness: 0.32, metalness: 0.05, emissive: glow ? '#ffe9c9' : undefined, ei: glow ? 0.25 : undefined });
  const pts = [[0, 0], [0.06, 0.03], [0.11, 0.09], [0.17, 0.2], [0.2, 0.32], [0.21, 0.45], [0.19, 0.58], [0.15, 0.68], [0.13, 0.72], [0.14, 0.76], [0.1, 0.82], [0.07, 0.88], [0.05, 0.93], [0.02, 0.98], [0, 1]];
  b.within(T(0, 0, -L * 0.5, Math.PI / 2, 0, 0, L), () => { // axis along +z, spire forward
    b.add(lathe(ctx, 'conch', pts, seg(ctx, 10, 14, 18)), shell);
    for (const [y, r] of [[0.75, 0.125], [0.82, 0.095], [0.88, 0.07], [0.93, 0.045]]) b.add(G.torus(ctx, 0.12, 4, 16), shell, T(0, y, 0, Math.PI / 2, 0, 0, r * 2));
    b.add(G.sphere(ctx, 10, 8), shell, T(0.16, 0.42, 0, 0, 0, 0.1, 0.1, 0.6, 0.26));
    b.add(G.cone(ctx, 8), shell, T(0, -0.06, 0, Math.PI, 0, 0, 0.08, 0.14, 0.08));
    if (bool(p.ornate, true)) {
      b.add(G.torus(ctx, 0.1, 5, 24), goldM(ctx, accent), T(0, 0.22, 0, Math.PI / 2, 0, 0, 0.36));
      b.add(G.torus(ctx, 0.1, 5, 24), goldM(ctx, accent), T(0, 0.62, 0, Math.PI / 2, 0, 0, 0.36));
    }
  });
  b.build(g, ctx, `conch:${L}:${color}:${accent}:${glow}:${p.ornate}:${qname(ctx)}`);
  if (glow) { const s = glowSprite('#FFF1D6', L * 2.4, 0.25); if (s) g.add(s); }
  return g;
}

// ================================================================ crown (mukuta)
export const MUKUTA_PTS = [[0.115, 0], [0.12, 0.01], [0.12, 0.05], [0.105, 0.06], [0.112, 0.075], [0.1, 0.12], [0.085, 0.17], [0.09, 0.19], [0.07, 0.23], [0.055, 0.27], [0.06, 0.285], [0.035, 0.31], [0.02, 0.34], [0, 0.36]];
function crown(params, ctx) {
  const p = P(params);
  const k = num(p.size, 1, 0.2, 10);
  const color = col(p, ['gold'], baseColor(p, PAL.gold));
  const jewel = col(p, ['jewel', 'gem'], (Array.isArray(p.gems) && hex(p.gems[0], null)) || col(p, ['accent', 'secondary', 'materials.accent'], '#A3242C'));
  const g = group('crown'), b = new Batch();
  const G1 = goldM(ctx, color), J = mat(ctx, { color: jewel, roughness: 0.2, metalness: 0.1, emissive: jewel, ei: 0.25 });
  b.within(T(0, 0, 0, 0, 0, 0, k), () => {
    b.add(lathe(ctx, 'mukuta', MUKUTA_PTS, seg(ctx, 10, 12, 16)), G1);
    for (let i = 0; i < 8; i++) { const a = (i / 8) * TAU; b.add(G.sphere(ctx, 6, 4), J, T(Math.sin(a) * 0.122, 0.032, Math.cos(a) * 0.122, 0, 0, 0, 0.024)); }
    b.add(G.sphere(ctx, 10, 6), G1, T(0, 0.12, 0.1, -0.2, 0, 0, 0.11, 0.13, 0.03)); // front plaque
    b.add(G.sphere(ctx, 6, 4), J, T(0, 0.12, 0.118, 0, 0, 0, 0.035));
    for (const s of [-1, 1]) b.add(G.sphere(ctx, 8, 6), G1, T(s * 0.105, 0.07, 0.02, 0, 0, s * 0.3, 0.05, 0.09, 0.02)); // side wings
    if (bool(p.feather, false)) { // peacock feather tucked at the back
      b.add(G.sphere(ctx, 8, 6), mat(ctx, { color: '#2E7D6B', roughness: 0.5, double: true }), T(-0.03, 0.36, -0.06, -0.35, 0, 0.25, 0.05, 0.3, 0.012));
      b.add(G.sphere(ctx, 8, 6), mat(ctx, { color: '#2B2F7A', roughness: 0.4, emissive: '#1b1f5a', ei: 0.3 }), T(-0.06, 0.47, -0.1, -0.35, 0, 0.25, 0.028, 0.04, 0.016));
    }
  });
  if (bool(p.cushion, false)) b.box(ctx, clothM(ctx, PAL.vermilion, { double: false }), 0.4 * k, 0.08 * k, 0.4 * k, 0, -0.04 * k, 0);
  b.build(g, ctx, `crown:${k}:${color}:${jewel}:${p.feather}:${p.cushion}:${qname(ctx)}`);
  return g;
}

// ================================================================ scroll (palm-leaf manuscript)
function scroll(params, ctx) {
  const p = P(params);
  const k = num(p.size, 1, 0.1, 30), n = int(p.leaves, str(p.style, '') === 'single_leaf' ? 3 : 16, 3, 60), open = bool(p.open, false);
  const glow = bool(p.glow, false);
  const g = group('scroll'), b = new Batch();
  const leafM = mat(ctx, { color: '#ffffff', tex: 'manuscript', roughness: 0.85, emissive: glow ? '#6b4a1a' : undefined, ei: glow ? 0.45 : undefined });
  const coverM = woodM(ctx, col(p, ['boards', 'cover'], baseColor(p, '#5A3A22'))), Gd = goldM(ctx, accentColor(p, PAL.gold));
  b.within(T(0, 0, 0, 0, 0, 0, k), () => {
    const lw = 0.55, lh = 0.006, ld = 0.065;
    b.box(ctx, coverM, lw + 0.03, 0.018, ld + 0.01, 0, 0.009, 0);
    for (let i = 0; i < n; i++) {
      const y = 0.018 + lh * (i + 0.5);
      if (open) b.within(T(-0.19, y, 0, 0, (i / (n - 1)) * 0.9, 0), () => b.box(ctx, leafM, lw, lh, ld, 0.19, 0, 0));
      else b.box(ctx, leafM, lw, lh, ld, 0, y, 0, 0, Math.sin(i * 7.1) * 0.01, 0);
    }
    const topY = 0.018 + lh * n;
    if (!open) b.box(ctx, coverM, lw + 0.03, 0.018, ld + 0.01, 0, topY + 0.009, 0);
    for (const x of open ? [-0.19] : [-0.19, 0.19]) b.add(G.cyl(ctx, 6), Gd, T(x, topY / 2 + 0.01, 0, 0, 0, 0, 0.008, topY + 0.04, 0.008));
    b.add(G.torus(ctx, 0.15, 4, 16), Gd, T(-0.19, topY + 0.025, 0, Math.PI / 2, 0, 0, 0.03));
  });
  b.build(g, ctx, `scroll:${k}:${n}:${open}:${glow}:${p.color}:${p.accent}`);
  if (glow) { const s = glowSprite('#FFD99A', 0.9 * k, 0.3); if (s) { s.position.y = 0.05 * k; g.add(s); } }
  return g;
}

// ================================================================ dice (elongated pasa dice on a cloth board)
function dice(params, ctx) {
  const p = P(params);
  const k = num(p.size, 1, 0.2, 20), count = int(p.count, 3, 1, 8);
  const ivory = baseColor(p, '#EFE6D2');
  const g = group('dice'), b = new Batch();
  const D = mat(ctx, { color: ivory, roughness: 0.4 }), dot = mat(ctx, { color: '#2A1A12', roughness: 0.6 });
  const r = rng(p.seed ?? 'dice');
  b.within(T(0, 0, 0, 0, 0, 0, k), () => {
    if (bool(p.board, true)) b.add(G.box(ctx), clothM(ctx, '#ffffff', { tex: `board:${col(p, ['board_color', 'boardColor'], '#7A2A22')}:${accentColor(p, PAL.gold)}`, double: false }), T(0, 0.002, 0, 0, 0.3, 0, 0.9, 0.004, 0.9));
    for (let i = 0; i < count; i++) {
      const L = 0.12, s = 0.028;
      b.within(T(r.range(-0.18, 0.18), 0.004 + s / 2, r.range(-0.18, 0.18), 0, r() * TAU, 0), () => {
        b.box(ctx, D, L, s, s);
        const faces = [[0, s / 2 + 0.001, 0, 1], [0, -s / 2 - 0.001, 0, 2], [0, 0, s / 2 + 0.001, 3], [0, 0, -s / 2 - 0.001, 4]];
        for (const [fx, fy, fz, nDots] of faces) for (let j = 0; j < nDots; j++) {
          const x = (j - (nDots - 1) / 2) * (L / 5);
          b.add(G.sphere(ctx, 6, 4), dot, T(x + fx, fy, fz, 0, 0, 0, 0.007));
        }
      });
    }
  });
  b.build(g, ctx, `dice:${k}:${count}:${ivory}:${p.board}:${p.seed ?? ''}:${p.boardColor ?? p.board_color ?? ''}`);
  g.userData.roll = { count };
  return g;
}

// ================================================================ throne (simhasana)
function throne(params, ctx) {
  const p = P(params);
  const k = num(p.size, 1, 0.3, 6);
  const base = baseColor(p, '#7A2E22'), accent = accentColor(p, PAL.gold), stoneC = col(p, ['dais', 'stone'], PAL.sandLight);
  const g = group('throne'), b = new Batch();
  const Gd = goldM(ctx, accent), Bm = mat(ctx, { color: base, roughness: 0.6, metalness: 0.1 }), Cu = clothM(ctx, col(p, ['cushion'], PAL.vermilion), { double: false });
  b.within(T(0, 0, 0, 0, 0, 0, k), () => {
    b.box(ctx, stone(ctx, stoneC), 2.4, 0.2, 2.0, 0, 0.1, 0);
    b.box(ctx, stone(ctx, stoneC), 2.0, 0.2, 1.7, 0, 0.3, -0.05);
    b.box(ctx, Gd, 2.05, 0.04, 1.75, 0, 0.41, -0.05);
    b.box(ctx, Bm, 1.4, 0.45, 1.0, 0, 0.4 + 0.225, -0.05);
    b.box(ctx, Gd, 1.44, 0.06, 1.04, 0, 0.86, -0.05);
    b.box(ctx, Cu, 1.25, 0.1, 0.85, 0, 0.94, 0);
    // arched back panel with gold surround and halo
    const back = geo(ctx, 'throneBack', () => {
      const sh = new THREE.Shape();
      sh.moveTo(-0.65, 0); sh.lineTo(0.65, 0); sh.lineTo(0.65, 1.0); sh.absarc(0, 1.0, 0.65, 0, Math.PI, false); sh.lineTo(-0.65, 0);
      const e = new THREE.ExtrudeGeometry(sh, { depth: 0.12, bevelEnabled: false, curveSegments: 16 });
      e.translate(0, 0, -0.06);
      return e;
    });
    b.add(back, Bm, T(0, 0.86, -0.5));
    b.add(back, Gd, T(0, 0.82, -0.56, 0, 0, 0, 1.12, 1.08, 0.8));
    b.add(G.torus(ctx, 0.05, 6, 40), Gd, T(0, 0.86 + 1.0, -0.43, 0, 0, 0, 0.9));
    b.add(kalasha(ctx), Gd, T(0, 0.86 + 1.65, -0.5, 0, 0, 0, 0.28));
    // lion-pedestal armrests
    for (const s of [-1, 1]) {
      b.box(ctx, Bm, 0.18, 0.4, 0.95, s * 0.78, 1.05, -0.05);
      b.box(ctx, Gd, 0.22, 0.05, 0.99, s * 0.78, 1.26, -0.05);
      b.add(G.sphere(ctx, 10, 8), Gd, T(s * 0.78, 1.3, 0.42, 0, 0, 0, 0.17));
      b.add(lathe(ctx, 'throneLeg', [[0.06, 0], [0.09, 0.03], [0.05, 0.08], [0.045, 0.16], [0.08, 0.2], [0, 0.2]], 8), Gd, T(s * 0.62, 0.4, 0.38));
    }
  });
  b.build(g, ctx, `throne:${k}:${base}:${accent}:${stoneC}:${p.cushion ?? ''}:${qname(ctx)}`);
  if (bool(p.umbrella ?? p.canopy, false)) {
    const u = new Batch();
    u.add(G.cyl(ctx, 6), goldM(ctx, accent), T(0, 2.1 * k, -0.85 * k, 0, 0, 0, 0.04 * k, 4.2 * k, 0.04 * k));
    u.add(G.cone(ctx, 16), clothM(ctx, '#F3EAD7'), T(0, 3.85 * k, -0.2 * k, 0.25, 0, 0, 2.2 * k, 0.45 * k, 2.2 * k));
    u.build(g, ctx, `throneUmb:${k}:${accent}`);
  }
  g.userData.anchors = { seat: [0, 0.99 * k, 0] };
  return g;
}

// ================================================================ weapon (divine astra)
function weapon(params, ctx) {
  const p = P(params);
  const kind = str(p.kind ?? p.type, 'spear', ['spear', 'trident', 'sword', 'arrow']);
  const L = num(p.length, kind === 'sword' ? 1.0 : 1.7, 0.2, 12);
  const color = baseColor(p, '#FFE7A8'), aura = accentColor(p, '#8FB4FF');
  const float = num(p.float, 1.2, 0, 50);
  const g = group('weapon');
  const core = new THREE.Group(); core.name = 'weapon_core'; core.position.y = float;
  const b = new Batch();
  const Gd = goldM(ctx, PAL.gold), Lm = mat(ctx, { color, emissive: color, ei: 1.3, roughness: 0.3, metalness: 0.3 });
  if (kind === 'sword') {
    b.add(G.cyl(ctx, 6), Gd, T(0, -L * 0.42, 0, 0, 0, 0, 0.035, L * 0.2, 0.035));
    b.box(ctx, Gd, L * 0.22, 0.03, 0.05, 0, -L * 0.3, 0);
    b.add(G.cone(ctx, 4), Lm, T(0, L * 0.12, 0, 0, Math.PI / 4, 0, L * 0.08, L * 0.8, 0.02));
  } else {
    b.add(G.cyl(ctx, 6), Gd, T(0, 0, 0, 0, 0, 0, 0.035, L * 0.8, 0.035));
    b.add(G.sphere(ctx, 8, 6), Gd, T(0, -L * 0.4, 0, 0, 0, 0, 0.07));
    if (kind === 'trident') {
      b.box(ctx, Gd, L * 0.22, 0.03, 0.04, 0, L * 0.4, 0);
      b.add(G.ico(ctx, 0), Lm, T(0, L * 0.5, 0, 0, 0, 0, 0.07, L * 0.25, 0.05));
      for (const s of [-1, 1]) b.add(G.ico(ctx, 0), Lm, T(s * L * 0.11, L * 0.46, 0, 0, 0, -s * 0.15, 0.05, L * 0.16, 0.04));
    } else if (kind === 'arrow') {
      b.add(G.cone(ctx, 4), Lm, T(0, L * 0.45, 0, 0, 0, 0, 0.08, L * 0.12, 0.08));
      for (let i = 0; i < 3; i++) b.box(ctx, Lm, 0.004, L * 0.1, 0.07, 0, -L * 0.34, 0, 0, (i / 3) * Math.PI, 0);
    } else {
      b.add(G.ico(ctx, 0), Lm, T(0, L * 0.5, 0, 0, 0, 0, 0.09, L * 0.24, 0.06));
      b.add(G.torus(ctx, 0.2, 4, 16), Gd, T(0, L * 0.39, 0, Math.PI / 2, 0, 0, 0.12));
    }
  }
  b.build(core, ctx, `weapon:${kind}:${L}:${color}:${qname(ctx)}`, { cast: false });
  const ringM = new THREE.MeshBasicMaterial({ color: aura, transparent: true, opacity: 0.55, blending: THREE.AdditiveBlending, depthWrite: false });
  const rings = [0, 1].map((i) => {
    const m = new THREE.Mesh(G.torus(ctx, 0.02, 4, 48), ringM);
    m.scale.setScalar(L * (0.42 + i * 0.12)); m.rotation.x = Math.PI / 2 + (i ? 0.5 : -0.4);
    core.add(m); return m;
  });
  const halo = glowSprite(color, L * 1.3, 0.55);
  if (halo) core.add(halo);
  g.add(core);
  g.userData.pulse = { speed: 2.2 };
  addUpdate(g, (dt, t) => {
    core.position.y = float + (float > 0 ? Math.sin(t * 1.6) * 0.06 * L : 0);
    core.rotation.y += dt * 0.5;
    rings[0].rotation.z += dt * 1.4; rings[1].rotation.z -= dt * 1.1;
    pulseSprite(halo, 0.92 + 0.1 * Math.sin(t * 2.2));
  });
  return g;
}

// ================================================================ shield
function shield(params, ctx) {
  const p = P(params);
  const R = num(p.radius, 0.38, 0.1, 4);
  const base = col(p, ['face'], baseColor(p, '#6B3F24')), accent = col(p, ['boss'], accentColor(p, PAL.gold));
  const g = group('shield'), b = new Batch();
  const S = mat(ctx, { color: base, roughness: 0.55, metalness: 0.35 }), Gd = goldM(ctx, accent);
  b.within(T(0, 0, 0, Math.PI / 2, 0, 0, R / 0.38), () => { // dome faces +z
    b.add(lathe(ctx, 'shieldDome', [[0.38, 0], [0.34, 0.035], [0.25, 0.065], [0.12, 0.085], [0, 0.09]], seg(ctx, 12, 18, 24)), S);
    b.add(geo(ctx, 'shieldBack', () => new THREE.CircleGeometry(0.38, 24).rotateX(Math.PI / 2)), S);
    b.add(G.torus(ctx, 0.05, 5, 32), Gd, T(0, 0.005, 0, Math.PI / 2, 0, 0, 0.76));
    b.add(G.sphere(ctx, 10, 6), Gd, T(0, 0.09, 0, 0, 0, 0, 0.12, 0.08, 0.12));
    for (let i = 0; i < 4; i++) { const a = (i / 4) * TAU + Math.PI / 4; b.add(G.sphere(ctx, 6, 4), Gd, T(Math.cos(a) * 0.22, 0.072, Math.sin(a) * 0.22, 0, 0, 0, 0.045)); }
  });
  b.build(g, ctx, `shield:${R}:${base}:${accent}:${qname(ctx)}`);
  return g;
}

// ================================================================ arrows (quiver, or arrows struck in the ground)
/** One arrow along +y (nock at 0, tip at 0.92), vertex-coloured so instancing needs a single material. */
export function arrowGeometry(ctx) {
  return geo(ctx, 'arrow:v1', () => {
    const paint = (g, hexStr) => {
      const c = new THREE.Color(hexStr), n = g.attributes.position.count, a = new Float32Array(n * 3);
      for (let i = 0; i < n; i++) { a[i * 3] = c.r; a[i * 3 + 1] = c.g; a[i * 3 + 2] = c.b; }
      g.setAttribute('color', new THREE.BufferAttribute(a, 3));
      return g;
    };
    const shaft = paint(new THREE.CylinderGeometry(0.007, 0.007, 0.85, 4).translate(0, 0.425, 0), '#8B6A44');
    const head = paint(new THREE.ConeGeometry(0.018, 0.07, 4).translate(0, 0.885, 0), '#B9BCC4');
    const f1 = paint(new THREE.PlaneGeometry(0.03, 0.11).translate(0, 0.08, 0), '#F1E9DA');
    const f2 = paint(new THREE.PlaneGeometry(0.03, 0.11).rotateY(Math.PI / 2).translate(0, 0.08, 0), '#B5452F');
    return mergeGeometries([shaft, head, f1, f2], false);
  });
}
const arrowMat = (ctx) => mat(ctx, { color: '#ffffff', vc: true, roughness: 0.7, double: true });
function arrows(params, ctx) {
  const p = P(params);
  const mode = str(p.mode, 'quiver', ['quiver', 'ground']);
  const g = group('arrows');
  const r = rng(p.seed ?? 'arrows');
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), e = new THREE.Euler();
  if (mode === 'ground') {
    const n = qcount(ctx, int(p.count, 60, 1, 2000), 6), R = num(p.radius, 6, 0.5, 200);
    const im = new THREE.InstancedMesh(arrowGeometry(ctx), arrowMat(ctx), n);
    for (let i = 0; i < n; i++) {
      const a = r() * TAU, d = Math.sqrt(r()) * R, s = r.range(0.85, 1.1);
      e.set(Math.PI + r.range(-0.5, 0.5), r() * TAU, r.range(-0.5, 0.5));
      m.compose(new THREE.Vector3(Math.sin(a) * d, 0.92 * s * 0.75, Math.cos(a) * d), q.setFromEuler(e), new THREE.Vector3(s, s, s));
      im.setMatrixAt(i, m);
    }
    im.instanceMatrix.needsUpdate = true; im.computeBoundingSphere(); im.castShadow = true;
    g.add(im);
    return g;
  }
  const k = num(p.size, 1, 0.2, 10), n = int(p.count, 12, 1, 40);
  const b = new Batch();
  const leather = mat(ctx, { color: baseColor(p, '#6A4128'), roughness: 0.8, double: true }), Gd = goldM(ctx, accentColor(p, PAL.gold));
  b.within(T(0, 0, 0, 0, 0, 0, k), () => {
    b.add(lathe(ctx, 'quiver', [[0.06, 0], [0.075, 0.02], [0.085, 0.3], [0.09, 0.58], [0.095, 0.6]], seg(ctx, 8, 10, 12)), leather);
    b.add(geo(ctx, 'quiverBase', () => new THREE.CircleGeometry(0.06, 10).rotateX(Math.PI / 2)), leather);
    for (const y of [0.05, 0.3, 0.57]) b.add(G.torus(ctx, 0.08, 4, 16), Gd, T(0, y, 0, Math.PI / 2, 0, 0, 0.18));
  });
  b.build(g, ctx, `quiver:${k}:${p.color}:${p.accent}:${qname(ctx)}`);
  const im = new THREE.InstancedMesh(arrowGeometry(ctx), arrowMat(ctx), n);
  for (let i = 0; i < n; i++) {
    const a = r() * TAU, d = r() * 0.05;
    e.set(r.range(-0.12, 0.12), r() * TAU, r.range(-0.12, 0.12));
    m.compose(new THREE.Vector3(Math.sin(a) * d * k, 0.06 * k, Math.cos(a) * d * k), q.setFromEuler(e), new THREE.Vector3(k * 0.82, k * 0.82, k * 0.82));
    im.setMatrixAt(i, m);
  }
  im.instanceMatrix.needsUpdate = true; im.computeBoundingSphere();
  g.add(im);
  return g;
}

// ================================================================ arrow_bed (Bhishma's bed of arrows)
function arrow_bed(params, ctx) {
  const p = P(params);
  const len = num(p.length, 2.2, 0.8, 6), wid = num(p.width, 0.85, 0.3, 3), top = num(p.height ?? p.height_above_ground, 0.74, 0.2, 2);
  const n = qcount(ctx, int(p.count ?? p.arrow_count, 120, 10, 1000), 36);
  const g = group('arrow_bed');
  const r = rng(p.seed ?? 'shara-shayya');
  const im = new THREE.InstancedMesh(arrowGeometry(ctx), arrowMat(ctx), n + 3);
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), e = new THREE.Euler();
  for (let i = 0; i < n; i++) {
    const u = r.range(-0.5, 0.5), v = r.range(-0.5, 0.5), s = r.range(0.92, 1.08);
    e.set(v * 0.5 + r.range(-0.12, 0.12), 0, -u * 0.5 + r.range(-0.12, 0.12));
    const tipTop = top + r.range(-0.04, 0.04);
    m.compose(new THREE.Vector3(u * wid, tipTop - 0.92 * s, v * len), q.setFromEuler(e), new THREE.Vector3(s, s, s));
    im.setMatrixAt(i, m);
  }
  // three crossed arrows at the head end (a pillow of arrows, as the story tells)
  for (let j = 0; j < 3; j++) {
    e.set(-0.15, 0, (j - 1) * 0.55);
    m.compose(new THREE.Vector3((j - 1) * 0.08, top + 0.1 - 0.92 * 0.5, -len / 2 - 0.05), q.setFromEuler(e), new THREE.Vector3(1, 1, 1));
    im.setMatrixAt(n + j, m);
  }
  im.instanceMatrix.needsUpdate = true; im.computeBoundingSphere(); im.castShadow = true;
  g.add(im);
  const earth = new THREE.Mesh(geo(ctx, 'bedEarth', () => new THREE.CircleGeometry(1, 24).rotateX(-Math.PI / 2)), mat(ctx, { color: col(p, ['ground'], '#5B4433'), roughness: 1, polyOffset: true }));
  earth.scale.set(wid * 1.2, 1, len * 0.75); earth.position.y = 0.01; earth.receiveShadow = true;
  g.add(earth);
  g.userData.anchors = { body: [0, top + 0.04, 0] };
  return g;
}

// ================================================================ drum (nagara war drum)
function drum(params, ctx) {
  const p = P(params);
  const k = num(p.size, 1, 0.2, 6);
  const body = col(p, ['body'], baseColor(p, '#8A4E2A')), accent = col(p, ['bands'], accentColor(p, PAL.gold)), skin = col(p, ['skin', 'head'], '#D9C8A4');
  const g = group('drum'), b = new Batch();
  const Bm = mat(ctx, { color: body, roughness: 0.4, metalness: 0.6 }), Wd = woodM(ctx), Gd = goldM(ctx, accent), Sk = mat(ctx, { color: skin, roughness: 0.9 });
  b.within(T(0, 0, 0, 0, 0, 0, k), () => {
    for (let i = 0; i < 4; i++) { const a = (i / 4) * TAU + Math.PI / 4; b.add(G.cyl(ctx, 5), Wd, rod([Math.cos(a) * 0.55, 0, Math.sin(a) * 0.55], [Math.cos(a) * 0.3, 0.78, Math.sin(a) * 0.3], 0.06)); }
    b.add(G.torus(ctx, 0.08, 4, 20), Wd, T(0, 0.45, 0, Math.PI / 2, 0, 0, 0.95));
    b.add(lathe(ctx, 'nagara', [[0, 0], [0.18, 0.02], [0.38, 0.12], [0.5, 0.3], [0.56, 0.48], [0.58, 0.56], [0.6, 0.6]], seg(ctx, 12, 16, 24)), Bm, T(0, 0.55, 0));
    for (let i = 0; i < 12; i++) { const a = (i / 12) * TAU; b.add(G.cyl(ctx, 3), Gd, rod([Math.cos(a) * 0.32, 0.62, Math.sin(a) * 0.32], [Math.cos(a + 0.26) * 0.58, 1.1, Math.sin(a + 0.26) * 0.58], 0.012)); }
    b.add(G.torus(ctx, 0.07, 5, 32), Gd, T(0, 1.14, 0, Math.PI / 2, 0, 0, 1.2));
    // sticks resting across the rim
    for (const s of [-1, 1]) b.add(G.cyl(ctx, 5), Wd, rod([s * 0.15, 1.2, -0.5], [s * 0.35, 1.22, 0.55], 0.025));
  });
  b.build(g, ctx, `drum:${k}:${body}:${accent}:${qname(ctx)}`);
  // slightly domed skin: a spherical cap whose rim matches the 0.6 bowl radius
  const head = new THREE.Mesh(geo(ctx, `drumHead:${qname(ctx)}`, () => new THREE.SphereGeometry(1.25, seg(ctx, 12, 16, 24), 3, 0, TAU, 0, 0.5).translate(0, -1.25 * Math.cos(0.5), 0)), Sk);
  head.name = 'drum_head'; head.position.y = 1.15 * k; head.scale.setScalar(k);
  g.add(head);
  let pulse = 0;
  g.userData.pulse = { target: 'drum_head' };
  g.userData.strike = (power = 1) => { pulse = Math.min(1.5, pulse + num(power, 1)); };
  addUpdate(g, (dt) => {
    if (pulse <= 0) return;
    pulse = Math.max(0, pulse - dt * 4);
    head.scale.set(k, k * (1 - 0.08 * pulse), k);
  });
  g.userData.anchors = { head: [0, 1.15 * k, 0] };
  return g;
}

// ================================================================ door (grand double palace door)
function door(params, ctx) {
  const p = P(params);
  const w = num(p.width, 4, 1, 20), h = num(p.height, 6, 1.5, 30);
  const wood = col(p, ['wood'], baseColor(p, PAL.woodDark)), accent = col(p, ['studs'], accentColor(p, PAL.gold)), frameC = col(p, ['frame', 'stone'], PAL.sandstone);
  const g = group('door');
  const F = new Batch(), S = stone(ctx, frameC), Gd = goldM(ctx, accent), Wd = woodM(ctx, wood);
  const lh = h * 0.78, fw = 0.55;
  for (const s of [-1, 1]) {
    F.add(lathe(ctx, `doorpost:${lh}`, [[0.32, 0], [0.32, 0.1], [0.24, 0.16], [0.22, lh - 0.3], [0.3, lh - 0.18], [0.3, lh], [0, lh]], 8, Math.PI / 8), S, T(s * (w / 2 + fw / 2), 0, 0));
  }
  F.box(ctx, S, w + fw * 2 + 0.4, 0.45, 0.7, 0, lh + 0.22, 0);
  const tymp = geo(ctx, `doorTymp:${w}:${h}`, () => {
    const sh = new THREE.Shape();
    const r = w / 2 + fw;
    sh.moveTo(-r, 0); sh.lineTo(r, 0); sh.absarc(0, 0, r, 0, Math.PI, false);
    const hole = new THREE.Path(); hole.absarc(0, 0.05, r * 0.72, 0, Math.PI, false); hole.lineTo(r * 0.72, 0.05);
    sh.holes.push(hole);
    const e = new THREE.ExtrudeGeometry(sh, { depth: 0.4, bevelEnabled: false, curveSegments: 18 });
    e.translate(0, 0, -0.2); e.scale(1, (h - lh) / r, 1);
    return e;
  });
  F.add(tymp, S, T(0, lh + 0.45, 0));
  F.add(geo(ctx, `doorFan:${w}:${h}`, () => new THREE.CircleGeometry((w / 2 + fw) * 0.72, 18, 0, Math.PI).scale(1, (h - lh) / (w / 2 + fw), 1)), Gd, T(0, lh + 0.5, -0.05));
  F.add(kalasha(ctx), Gd, T(0, h + 0.4, 0, 0, 0, 0, 0.6));
  F.box(ctx, S, w + fw * 2 + 0.6, 0.2, 0.9, 0, 0.1, 0);
  F.build(g, ctx, `doorFrame:${w}:${h}:${frameC}:${accent}`);
  // leaves on hinge pivots
  const leaves = [];
  for (const s of [-1, 1]) {
    const pivot = new THREE.Group(); pivot.name = s < 0 ? 'door_left' : 'door_right';
    pivot.position.set(s * w / 2, 0.2, 0);
    const L = new Batch(), lw = w / 2;
    L.box(ctx, Wd, lw, lh - 0.2, 0.18, -s * lw / 2, (lh - 0.2) / 2, 0);
    for (let i = 0; i < 4; i++) L.box(ctx, Gd, lw * 0.98, 0.07, 0.22, -s * lw / 2, ((i + 0.5) / 4) * (lh - 0.2), 0);
    const studs = qname(ctx) === 'low' ? 3 : 5;
    for (let i = 0; i < studs; i++) for (let j = 0; j < 7; j++) {
      L.add(G.ico(ctx, 0), Gd, T(-s * (lw * (i + 0.5)) / studs, ((j + 0.5) / 7) * (lh - 0.2), 0.11, 0, 0, 0, 0.07));
    }
    L.build(pivot, ctx, `doorLeaf:${s}:${w}:${h}:${wood}:${accent}:${qname(ctx)}`);
    g.add(pivot); leaves.push([pivot, s]);
  }
  const setOpen = (v) => {
    const a = Math.min(1, Math.max(0, num(v, 0)));
    for (const [pv, s] of leaves) pv.rotation.y = s * a * 1.75; // swing inward (towards -z)
    g.userData.open = a;
  };
  setOpen(p.open ?? 0);
  g.userData.setOpen = setOpen;
  return g;
}

// ================================================================ lamp (deepa stambha or clay diya)
function lamp(params, ctx) {
  const p = P(params);
  const stand = bool(p.stand, true);
  // `height` (metres, registry) maps onto the 1.2 m standing lamp; `size` stays a plain scale factor
  const k = num(p.size, stand && num(p.height, 0) > 0 ? num(p.height, 1.2, 0.2, 12) / 1.2 : 1, 0.1, 10);
  const brass = col(p, ['brass'], baseColor(p, '#B8893E'));
  const g = group('lamp');
  const flames = [];
  const b = new Batch();
  const Br = mat(ctx, { color: brass, roughness: 0.32, metalness: 0.85 }), Cl = mat(ctx, { color: '#9C5B3B', roughness: 0.9 });
  let topY = 0.05;
  b.within(T(0, 0, 0, 0, 0, 0, k), () => {
    if (stand) {
      b.add(lathe(ctx, 'lampstand', [[0.2, 0], [0.22, 0.03], [0.12, 0.08], [0.05, 0.14], [0.035, 0.5], [0.06, 0.52], [0.16, 0.56], [0.17, 0.58], [0.04, 0.6], [0.03, 1.05], [0.05, 1.1], [0.1, 1.16], [0.12, 1.2], [0, 1.2]], seg(ctx, 8, 12, 14)), Br);
      for (let i = 0; i < 4; i++) { const a = (i / 4) * TAU; b.add(G.sphere(ctx, 8, 4), Br, T(Math.cos(a) * 0.15, 0.585, Math.sin(a) * 0.15, 0, 0, 0, 0.07, 0.035, 0.07)); }
      topY = 1.2;
    } else {
      b.add(lathe(ctx, 'diya', [[0, 0], [0.05, 0.005], [0.08, 0.025], [0.085, 0.04], [0.07, 0.04], [0, 0.03]], 10), Cl, T(0, 0, 0, 0, 0, 0, 1.4));
    }
  });
  b.build(g, ctx, `lamp:${stand}:${k}:${brass}:${qname(ctx)}`);
  const big = flame(ctx, { size: 0.09 * k, embers: 0, tongues: 3, light: bool(p.light, false), intensity: num(p.intensity, 1.2), seed: p.seed ?? 'lamp' });
  big.position.y = topY * k + 0.01 * k; g.add(big); flames.push(big);
  if (stand && qname(ctx) !== 'low') for (let i = 0; i < 4; i++) {
    const a = (i / 4) * TAU;
    const f = flame(ctx, { size: 0.05 * k, embers: 0, tongues: 2, seed: i });
    f.position.set(Math.cos(a) * 0.15 * k, 0.61 * k, Math.sin(a) * 0.15 * k); g.add(f);
  }
  g.userData.flicker = { intensity: 1 };
  g.userData.anchors = { flame: [0, topY * k, 0] };
  return g;
}

// ================================================================ tree (sacred banyan / ashvattha)
function tree(params, ctx) {
  const p = P(params);
  const H = num(p.height, 9, 1, 60);
  const trunkC = col(p, ['trunk', 'bark'], '#5C4331'), leaf = col(p, ['foliage', 'leaves', 'canopy', 'color', 'leaf', 'base'], PAL.leaf);
  const r = rng(p.seed ?? 'vata');
  const g = group('tree'), b = new Batch();
  const sway = new THREE.Group(); sway.name = 'tree_sway'; g.add(sway);
  const Tk = mat(ctx, { color: trunkC, roughness: 1 });
  const L1 = mat(ctx, { color: leaf, roughness: 0.95, flat: true }), L2 = mat(ctx, { color: shade(leaf, 0.12), roughness: 0.95, flat: true });
  const s = H / 9;
  b.add(lathe(ctx, 'trunk', [[1.15, 0], [0.8, 0.25], [0.62, 0.8], [0.55, 2.2], [0.5, 3.4], [0.42, 3.9], [0, 4.0]], seg(ctx, 7, 9, 12)), Tk, T(0, 0, 0, 0, r() * TAU, 0, s));
  const nb = int(p.branches, 6, 3, 10), ends = [];
  for (let i = 0; i < nb; i++) {
    const a = (i / nb) * TAU + r.range(-0.3, 0.3), tilt = r.range(0.75, 1.1), len = r.range(2.4, 3.4) * s;
    const a0 = [0, r.range(2.9, 3.6) * s, 0];
    const e = [Math.sin(a) * Math.sin(tilt) * len, a0[1] + Math.cos(tilt) * len, Math.cos(a) * Math.sin(tilt) * len];
    b.add(G.frustum(ctx, 0.45, 6), Tk, rod(a0, e, 0.7 * s));
    ends.push(e);
  }
  // broad, layered canopy: a ring of flattened blobs over the branch ends plus a crown cluster
  const blob = G.ico(ctx, 1);
  for (const e of ends) b.add(blob, r() < 0.5 ? L1 : L2, T(e[0], e[1] + 0.5 * s, e[2], r() * 3, r() * 3, 0, r.range(2.6, 3.4) * s, r.range(1.6, 2.1) * s, r.range(2.6, 3.4) * s));
  const ringN = qname(ctx) === 'low' ? 5 : 8;
  for (let i = 0; i < ringN; i++) {
    const a = (i / ringN) * TAU + r.range(-0.2, 0.2), d = r.range(2.6, 3.6) * s;
    b.add(blob, i % 2 ? L1 : L2, T(Math.sin(a) * d, r.range(6.0, 6.8) * s, Math.cos(a) * d, r(), r() * 3, 0, r.range(2.6, 3.2) * s, r.range(1.5, 1.9) * s, r.range(2.6, 3.2) * s));
  }
  for (let i = 0; i < 3; i++) { const a = r() * TAU, d = r.range(0, 1.4) * s; b.add(blob, L1, T(Math.sin(a) * d, r.range(7.2, 7.9) * s, Math.cos(a) * d, r(), r(), 0, r.range(2.4, 3.0) * s, r.range(1.5, 1.8) * s, r.range(2.4, 3.0) * s)); }
  if (bool(p.roots, true)) { // banyan aerial roots
    const nr = qcount(ctx, 14, 5);
    for (let i = 0; i < nr; i++) {
      const a = r() * TAU, d = r.range(1.8, 3.6) * s;
      b.add(G.cyl(ctx, 4), Tk, rod([Math.sin(a) * d, 4.6 * s, Math.cos(a) * d], [Math.sin(a) * d * 1.08, 0, Math.cos(a) * d * 1.08], 0.07 * s));
    }
  }
  if (bool(p.sacred, true)) { // sacred threads around the trunk
    const thread = mat(ctx, { color: '#B3262A', roughness: 0.8 });
    b.add(G.torus(ctx, 0.04, 4, 20), thread, T(0, 1.2 * s, 0, Math.PI / 2, 0, 0, 1.24 * s));
    b.add(G.torus(ctx, 0.04, 4, 20), mat(ctx, { color: '#E6B73C', roughness: 0.8 }), T(0, 1.35 * s, 0, Math.PI / 2, 0, 0, 1.22 * s));
  }
  b.build(sway, ctx, `tree:${H}:${trunkC}:${leaf}:${p.seed ?? ''}:${p.roots}:${p.sacred}:${nb}:${qname(ctx)}`);
  const ph = r() * 10;
  sway.userData.sway = { amplitude: 0.006 };
  addUpdate(g, (dt, t) => { sway.rotation.z = 0.006 * Math.sin(t * 0.7 + ph); sway.rotation.x = 0.004 * Math.sin(t * 0.53 + ph); });
  return g;
}
function shade(hexStr, k) { const c = new THREE.Color(hexStr); c.offsetHSL(0.02, 0, k); return '#' + c.getHexString(); }

// ================================================================ target (Matsya yantra of the swayamvara)
function target(params, ctx) {
  const p = P(params);
  const Hp = num(p.height ?? p.pole_height, 7, 1, 40), R = num(p.radius ?? p.wheel_radius, 0.9, 0.2, 6);
  const accent = accentColor(p, PAL.gold), fishC = col(p, ['fish', 'fish_color', 'color', 'base'], '#D8A84A');
  const g = group('target'), b = new Batch();
  const Wd = woodM(ctx, PAL.wood), Gd = goldM(ctx, accent), S = stone(ctx, PAL.sandstone);
  b.add(lathe(ctx, 'targetBase', [[0.7, 0], [0.7, 0.2], [0.45, 0.3], [0.3, 0.55], [0, 0.55]], 10), S);
  b.add(G.cyl(ctx, 8), Wd, T(0, Hp / 2, 0, 0, 0, 0, 0.14, Hp, 0.14));
  for (const y of [0.3, 0.6, 0.9]) b.add(G.cyl(ctx, 8), Gd, T(0, Hp * y, 0, 0, 0, 0, 0.19, 0.08, 0.19));
  b.build(g, ctx, `targetPole:${Hp}:${accent}`);
  const wheel = new THREE.Group(); wheel.name = 'target_wheel'; wheel.position.y = Hp;
  wheel.userData.spin = { axis: 'y', speed: num(p.speed ?? p.rotation_speed, 1.4, -20, 20) };
  const w = new Batch();
  w.add(G.torus(ctx, 0.05, 5, 32), Gd, T(0, 0, 0, Math.PI / 2, 0, 0, R * 2));
  w.add(G.cyl(ctx, 10), Gd, T(0, 0, 0, 0, 0, 0, 0.2, 0.12, 0.2));
  for (let i = 0; i < 8; i++) { const a = (i / 8) * TAU; w.box(ctx, Wd, 0.04, 0.04, R, Math.sin(a) * R / 2, 0, Math.cos(a) * R / 2, 0, a, 0); }
  w.add(G.cyl(ctx, 5), Gd, T(0, 0.3, 0, 0, 0, 0, 0.03, 0.6, 0.03));
  const fm = mat(ctx, { color: fishC, roughness: 0.35, metalness: 0.7 });
  w.add(G.sphere(ctx, 12, 8), fm, T(0, 0.62, 0, 0, 0, 0, 0.62, 0.2, 0.14));
  w.add(G.cone(ctx, 4), fm, T(-0.38, 0.62, 0, 0, 0, Math.PI / 2, 0.24, 0.18, 0.04));
  w.build(wheel, ctx, `targetWheel:${R}:${accent}:${fishC}`);
  g.add(wheel);
  if (bool(p.pool ?? p.reflection_basin, true)) { // the reflecting pool the archer looks into
    const pr = num(p.poolRadius, 1.6, 0.4, 10);
    const rim = new THREE.Mesh(G.torus(ctx, 0.1, 6, 40), S); rim.rotation.x = Math.PI / 2; rim.scale.setScalar(pr * 2); rim.position.set(2.4, 0.1, 1.2);
    const wm = waterMat(ctx, col(p, ['basin_color', 'water'], '#2F5F74'), 0.95, 'pool');
    const water = new THREE.Mesh(geo(ctx, 'poolDisc', () => new THREE.CircleGeometry(1, 32).rotateX(-Math.PI / 2)), wm);
    water.scale.setScalar(pr); water.position.set(2.4, 0.12, 1.2); water.name = 'target_pool';
    g.add(rim, water);
    flowWater(water, wm, 0.01, 0.015);
  }
  const speed = wheel.userData.spin.speed;
  addUpdate(g, (dt) => { wheel.rotation.y += dt * speed; });
  return g;
}

// ================================================================ mace (gada)
function mace(params, ctx) {
  const p = P(params);
  const L = num(p.length, 1.1, 0.3, 6);
  const color = col(p, ['head'], baseColor(p, PAL.gold)), accent = col(p, ['shaft', 'grip'], col(p, ['accent', 'secondary', 'materials.accent'], '#8C6A2A'));
  const g = group('mace'), b = new Batch();
  const Gm = mat(ctx, { color, roughness: 0.3, metalness: 0.85, flat: true }), Am = goldM(ctx, accent);
  b.within(T(0, 0, 0, 0, 0, 0, L / 1.1), () => { // origin at the grip end, head up (+y)
    b.add(G.sphere(ctx, 8, 6), Am, T(0, 0, 0, 0, 0, 0, 0.06));
    b.add(G.cyl(ctx, 8), Am, T(0, 0.38, 0, 0, 0, 0, 0.05, 0.76, 0.05));
    for (const y of [0.08, 0.16, 0.24, 0.62, 0.72]) b.add(G.cyl(ctx, 8), Gm, T(0, y, 0, 0, 0, 0, 0.068, 0.03, 0.068));
    b.add(lathe(ctx, 'gadaHead', [[0.04, 0], [0.1, 0.04], [0.15, 0.12], [0.165, 0.2], [0.15, 0.28], [0.1, 0.35], [0.04, 0.38], [0, 0.385]], seg(ctx, 8, 10, 12)), Gm, T(0, 0.74, 0));
    b.add(G.torus(ctx, 0.15, 4, 16), Am, T(0, 0.94, 0, Math.PI / 2, 0, 0, 0.33));
    b.add(G.cone(ctx, 6), Am, T(0, 1.18, 0, 0, 0, 0, 0.05, 0.1, 0.05));
  });
  b.build(g, ctx, `mace:${L}:${color}:${accent}:${qname(ctx)}`);
  return g;
}

// ================================================================ vessel (kalasha / lota)
function vessel(params, ctx) {
  const p = P(params);
  const k = num(p.size, num(p.height, 0) > 0 ? num(p.height, 0.385, 0.05, 8) / 0.385 : 1, 0.1, 20); // pot is 0.385 m tall
  const color = col(p, ['metal'], baseColor(p, '#B06A3B'));
  const g = group('vessel'), b = new Batch();
  const M = mat(ctx, { color, roughness: 0.35, metalness: 0.75 });
  b.within(T(0, 0, 0, 0, 0, 0, k), () => {
    b.add(lathe(ctx, 'kalashaPot', [[0, 0], [0.06, 0], [0.08, 0.02], [0.1, 0.06], [0.14, 0.12], [0.155, 0.18], [0.14, 0.24], [0.1, 0.29], [0.06, 0.31], [0.055, 0.34], [0.075, 0.37], [0.08, 0.385], [0.065, 0.385], [0.058, 0.36], [0, 0.355]], seg(ctx, 10, 14, 18)), M);
    if (bool(p.water, false)) b.add(geo(ctx, 'potWater', () => new THREE.CircleGeometry(0.058, 12).rotateX(-Math.PI / 2)), waterMat(ctx, '#4F8299', 1, 'still'), T(0, 0.357, 0));
    if (bool(p.kalasha, false)) {
      const lf = mat(ctx, { color: '#3F6B2E', roughness: 0.8, double: true });
      for (let i = 0; i < 5; i++) { const a = (i / 5) * TAU; b.add(G.sphere(ctx, 6, 4), lf, T(Math.sin(a) * 0.08, 0.41, Math.cos(a) * 0.08, Math.cos(a) * 0.9, 0, -Math.sin(a) * 0.9, 0.035, 0.11, 0.008)); }
      b.add(G.sphere(ctx, 10, 8), mat(ctx, { color: '#6B4A2B', roughness: 0.95 }), T(0, 0.45, 0, 0, 0, 0, 0.14, 0.16, 0.14));
    }
  });
  b.build(g, ctx, `vessel:${k}:${color}:${p.water}:${p.kalasha}:${qname(ctx)}`);
  return g;
}

// ================================================================ pyre (funeral pyre)
function pyre(params, ctx) {
  let p = P(params);
  const k = num(p.size, 1, 0.3, 6), layers = int(p.layers, 5, 2, 10);
  // `lit` (or the registry's boolean `flame`) toggles the fire; a numeric `flame` scales it
  if (p.lit === undefined && typeof p.flame === 'boolean') p = { ...p, lit: p.flame, flame: undefined };
  const g = group('pyre'), b = new Batch();
  const Wd = woodM(ctx, col(p, ['wood', 'color', 'base'], '#5A3B24')), Wd2 = woodM(ctx, '#7A5232');
  const r = rng(p.seed ?? 'pyre');
  b.within(T(0, 0, 0, 0, 0, 0, k), () => {
    b.box(ctx, stone(ctx, '#6E5A48'), 2.8, 0.2, 1.6, 0, 0.1, 0);
    for (let l = 0; l < layers; l++) {
      const y = 0.29 + l * 0.17, alongX = l % 2 === 0;
      const n = alongX ? 6 : 9;
      for (let i = 0; i < n; i++) {
        const o = -((n - 1) / 2) * 0.24 + i * 0.24;
        const len = alongX ? 2.4 : 1.3;
        b.add(G.cyl(ctx, 6), r() < 0.5 ? Wd : Wd2, alongX ? T(r.range(-0.05, 0.05), y, o * 0.55, 0, 0, Math.PI / 2, 0.17, len, 0.17) : T(o, y, 0, Math.PI / 2, 0, 0, 0.17, len, 0.17));
      }
    }
  });
  b.build(g, ctx, `pyre:${k}:${layers}:${p.wood ?? p.color ?? ''}:${p.seed ?? ''}`);
  const topY = (0.29 + layers * 0.17) * k;
  if (bool(p.lit, true)) {
    const f = flame(ctx, { size: num(p.flame, 1.0, 0.1, 5) * k, color: col(p, ['flame_color'], undefined), light: bool(p.light, true), intensity: num(p.intensity, 3), seed: 'pyre', tongues: 7 });
    f.position.y = topY - 0.1 * k; g.add(f);
    for (const x of [-0.8, 0.8]) { const f2 = flame(ctx, { size: 0.6 * k, embers: 0, seed: x }); f2.position.set(x * k, topY - 0.15 * k, 0); g.add(f2); }
  }
  g.userData.anchors = { top: [0, topY, 0] };
  return g;
}

export const OBJECTS = { banner, bow, chakra, conch, crown, scroll, dice, throne, weapon, shield, arrows, arrow_bed, drum, door, lamp, tree, target, mace, vessel, pyre };
