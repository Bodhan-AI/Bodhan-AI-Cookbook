// Living figures: figure (stylised human, ~1.8 units), horse, dog, chariot (with optional horses).
// Figures are original, serene forms: no facial features, no likeness of any actor or artwork.
// Facing +z; right hand at -x. All posing lives on inner groups so the engine can freely set the root transform.
import {
  THREE, PAL, P, num, int, bool, str, col, hex, baseColor, accentColor, seg, qname, rng, G, lathe, geo, T, rod,
  Batch, mat, goldM, clothM, woodM, darkM, glowM, addUpdate, idOf,
} from './kit.js';
import { OBJECTS, MUKUTA_PTS } from './objects.js';

const TAU = Math.PI * 2;
const V = (x, y, z) => new THREE.Vector3(x, y, z);
const arr = (v) => [v.x, v.y, v.z];

// ---------------------------------------------------------------- two-bone IK for arms
/** Elbow + (possibly clamped) hand for shoulder S → target H with bone lengths a, b and an elbow hint direction. */
function solveArm(S, H, a, b, hint) {
  const d0 = H.clone().sub(S), dist = Math.min(Math.max(d0.length(), Math.abs(a - b) + 1e-3), a + b - 1e-3);
  const n = d0.normalize();
  const hand = S.clone().addScaledVector(n, dist);
  const x = (a * a - b * b + dist * dist) / (2 * dist), h = Math.sqrt(Math.max(0, a * a - x * x));
  const pole = hint.clone().addScaledVector(n, -hint.dot(n));
  if (pole.lengthSq() < 1e-6) pole.set(0, 0, -1);
  pole.normalize();
  const elbow = S.clone().addScaledVector(n, x).addScaledVector(pole, h);
  return { elbow, hand };
}

// Pose table in standing coordinates (waist at y≈0.98). L = figure's left hand (+x), R = right hand (-x).
const POSES = {
  standing: { L: [0.27, 0.86, 0.05], Lh: [0.3, 0, -1], R: [-0.27, 0.86, 0.05], Rh: [-0.3, 0, -1] },
  walking: { L: [0.25, 0.9, 0.22], Lh: [0.3, 0, -1], R: [-0.25, 0.92, -0.18], Rh: [-0.3, 0, -1], lean: 0.04, walk: true },
  blessing: { L: [0.28, 0.95, 0.2], Lh: [0.4, -0.3, -1], R: [-0.3, 1.42, 0.25], Rh: [-0.6, -1, 0] },
  archer: { L: [0.02, 1.36, 0.55], Lh: [0.5, -1, 0], R: [-0.12, 1.47, -0.02], Rh: [-1, 0.2, -0.8], stance: 1.6, twist: 0.12 },
  charioteer: { L: [0.16, 1.05, 0.36], Lh: [0.5, -1, -0.3], R: [-0.16, 1.05, 0.36], Rh: [-0.5, -1, -0.3], lean: 0.08 },
  mace: { L: [0.28, 1.0, -0.02], Lh: [1, 0, -0.3], R: [-0.25, 1.48, 0.12], Rh: [-0.5, -1, 0.1], stance: 1.4 },
  seated: { L: [0.26, 0.88, 0.22], Lh: [0.4, 0, -1], R: [-0.26, 0.88, 0.22], Rh: [-0.4, 0, -1], seat: 'lotus', drop: 0.68 },
  kneeling: { L: [0.03, 1.22, 0.2], Lh: [1, -0.6, -0.4], R: [-0.03, 1.22, 0.2], Rh: [-1, -0.6, -0.4], seat: 'kneel', drop: 0.43 },
  lying: { L: [0.24, 0.86, 0.06], Lh: [0.3, 0, -1], R: [-0.24, 0.86, 0.06], Rh: [-0.3, 0, -1], lying: true },
};
const POSE_NAMES = Object.keys(POSES);

function attrKind(id) {
  const s = String(id || '').toLowerCase();
  if (!s) return null;
  if (/gandiva|bow/.test(s)) return 'bow';
  if (/sudarshana|chakra/.test(s)) return 'chakra';
  if (/panchajanya|conch|shankha/.test(s)) return 'conch';
  if (/mace|gada/.test(s)) return 'mace';
  if (/divine_weapon|weapon|astra|trident|spear|sword/.test(s)) return 'weapon';
  if (/manuscript|palm_leaf|scroll/.test(s)) return 'scroll';
  if (/shield/.test(s)) return 'shield';
  if (/arrows|quiver/.test(s)) return 'quiver';
  if (/flute|bansuri|venu/.test(s)) return 'flute';
  if (/lamp|diya/.test(s)) return 'lamp';
  if (/vessel|kalasha|pot/.test(s)) return 'vessel';
  if (/crown|mukuta/.test(s)) return 'crown';
  if (/peacock|feather/.test(s)) return 'feather';
  if (/dice|pasa|aksha/.test(s)) return 'dice';
  return null;
}
// Canonical character ids (DATA_CONTRACT §1) of the women of the epic. The registry's figure params carry no
// explicit form, so the asset id is the only reliable hint; explicit params (form/gender) always win.
const FEMININE_IDS = new Set(['draupadi', 'kunti', 'gandhari', 'madri', 'ganga_devi', 'satyavati', 'amba',
  'uttara_kumari', 'subhadra', 'hidimbi', 'ulupi', 'chitrangada']);
function isFeminine(p, ctx) {
  const f = str(p.form ?? p.gender ?? p.body, '');
  if (['female', 'feminine', 'woman', 'f'].includes(f)) return true;
  if (['male', 'masculine', 'man', 'm'].includes(f)) return false;
  if (FEMININE_IDS.has(idOf(p, ctx))) return true;
  return /\b(woman|queen|princess|female|goddess|her)\b/i.test(String(p.silhouette || ''));
}
/** Flag that may be a boolean or a colour (registry uses e.g. blindfold: "#e8dcc0", beard: "#eeeae2"). */
const flagOrHex = (v, d) => (hex(v, null) ? true : bool(v, d));

// ================================================================ figure
function figure(params, ctx) {
  const p = P(params);
  const H = num(p.height, 1.8, 0.4, 4), s = H / 1.8;
  const poseName = str(p.pose, 'standing', POSE_NAMES);
  const pose = POSES[poseName];
  const fem = isFeminine(p, ctx);
  const robe = col(p, ['robe', 'primary', 'color', 'base'], '#E9DFC8');
  const sash = col(p, ['sash', 'secondary', 'accent'], PAL.gold);
  const skin = col(p, ['skin'], PAL.skin);
  const hair = col(p, ['hair'], '#1E1611');
  const goldC = col(p, ['gold', 'jewellery', 'jewelry'], PAL.gold);
  const kinds = new Set();
  for (const a of [p.attribute, ...(Array.isArray(p.attributes) ? p.attributes : [])]) { const k = attrKind(a); if (k) kinds.add(k); }
  // registry boolean extras (char.* fallback params)
  if (bool(p.flute, false)) kinds.add('flute');
  if (bool(p.peacock_feather ?? p.peacockFeather, false)) kinds.add('feather');
  if (bool(p.bow, false)) kinds.add('bow');
  if (bool(p.quiver, false)) kinds.add('quiver');
  const crownOn = bool(p.crown, false) || kinds.has('crown');
  const beard = flagOrHex(p.beard, /\b(beard|bearded|sage|rishi|elder)\b/i.test(String(p.silhouette || '')));
  const beardC = hex(p.beard, hair);
  const hood = bool(p.veil, false) || bool(p.hood, false);
  const bald = bool(p.bald, false);
  const defHead = bald ? 'none' : hood ? 'hood' : fem ? 'hair' : beard ? 'bun' : 'turban';
  const headwear = crownOn ? 'crown' : str(p.headwear, defHead, ['turban', 'bun', 'hair', 'none', 'hood']);
  const halo = bool(p.halo, false), blindfold = flagOrHex(p.blindfold, false);
  const blindC = hex(p.blindfold, '#3A3A44');

  const Skin = mat(ctx, { color: skin, roughness: 0.62 });
  const Robe = clothM(ctx, robe, { double: false }), Sash = clothM(ctx, sash), Gold = goldM(ctx, goldC);
  const Hair = mat(ctx, { color: hair, roughness: 0.75 });
  const robeDark = new THREE.Color(robe).lerp(new THREE.Color('#000'), 0.12);
  const RobeD = clothM(ctx, '#' + robeDark.getHexString(), { double: false });
  const segs = seg(ctx, 8, 12, 16);

  const root = new THREE.Group(); root.name = 'figure'; root.userData.kind = 'figure';
  const body = new THREE.Group(); body.name = 'figure_body'; body.scale.setScalar(s); root.add(body);
  const lower = new THREE.Group(); lower.name = 'figure_lower'; body.add(lower);
  const waist = 0.98, drop = pose.drop || 0;
  const upper = new THREE.Group(); upper.name = 'figure_upper'; upper.position.y = waist - drop; body.add(upper);
  upper.rotation.set(pose.lean || 0, pose.twist || 0, 0);
  const key = JSON.stringify([poseName, fem, robe, sash, skin, hair, goldC, headwear, beard, beardC, bald, halo, blindfold, blindC, kinds.has('flute'), kinds.has('feather'), qname(ctx)]);

  // ---------------- lower body
  const lb = new Batch();
  const stance = pose.stance || 1;
  if (pose.seat === 'lotus') {
    lb.add(G.sphere(ctx, segs, 8), Robe, T(0, 0.13, 0.05, 0, 0, 0, 0.56, 0.24, 0.42));
    for (const sx of [-1, 1]) {
      lb.add(G.sphere(ctx, 10, 8), Robe, T(sx * 0.25, 0.12, 0.2, 0, 0, 0, 0.2, 0.17, 0.22));
      lb.add(G.sphere(ctx, 8, 6), Skin, T(-sx * 0.12, 0.2, 0.2, 0, sx * 0.5, 0, 0.07, 0.05, 0.13));
    }
  } else if (pose.seat === 'kneel') {
    lb.add(G.sphere(ctx, segs, 8), Robe, T(0, 0.3, 0.06, 0, 0, 0, 0.36, 0.5, 0.48));
    for (const sx of [-1, 1]) {
      lb.add(G.cyl(ctx, 8), Robe, rod([sx * 0.09, 0.07, 0.3], [sx * 0.09, 0.07, -0.18], 0.13));
      lb.add(G.sphere(ctx, 8, 6), Skin, T(sx * 0.09, 0.045, -0.22, 0, 0, 0, 0.07, 0.05, 0.12));
    }
  } else {
    const fx = 0.08 * stance;
    const fz = pose.walk ? [0.12, -0.1] : [0.04, 0.04];
    [1, -1].forEach((sx, i) => {
      lb.add(G.sphere(ctx, 8, 6), Skin, T(sx * fx, 0.035, fz[i] + 0.04, 0, 0, 0, 0.075, 0.05, 0.13));
      lb.add(G.cyl(ctx, 6), Skin, T(sx * fx, 0.1, fz[i], 0, 0, 0, 0.07, 0.13, 0.07));
    });
    const hem = fem ? 0.03 : 0.11;
    const pts = fem
      ? [[0.23, hem], [0.245, hem + 0.03], [0.225, 0.3], [0.2, 0.55], [0.175, 0.8], [0.15, 0.95], [0.142, 1.0], [0, 1.0]]
      : [[0.2, hem], [0.215, hem + 0.03], [0.205, 0.3], [0.19, 0.55], [0.17, 0.8], [0.16, 0.95], [0.155, 1.0], [0, 1.0]];
    lb.add(lathe(ctx, fem ? 'sari' : 'dhoti', pts, segs), Robe, T(0, 0, 0, pose.walk ? 0.04 : 0, 0, 0, stance > 1 ? 1.12 : 1, 1, 0.8));
    lb.add(G.box(ctx), RobeD, T(0, 0.6, 0.165, -0.05, 0, 0, 0.08, 0.66, 0.02)); // front pleats
    lb.add(G.box(ctx), Sash, T(0.06, 0.74, 0.16, -0.06, 0, 0.06, 0.06, 0.42, 0.015)); // sash end
  }
  if (!pose.seat) lb.add(G.torus(ctx, 0.08, 4, 20), Gold, T(0, 0.98, 0, Math.PI / 2, 0, 0, 0.33, 0.27, 0.33));
  lb.build(lower, ctx, `figL:${key}`);

  // ---------------- upper body (built in standing coordinates, shifted so the waist is the pivot)
  const ub = new Batch();
  const hands = {};
  ub.within(T(0, -waist, 0), () => {
    const torsoPts = fem
      ? [[0.14, 0.97], [0.135, 1.05], [0.15, 1.15], [0.172, 1.25], [0.178, 1.33], [0.17, 1.4], [0.14, 1.46], [0.065, 1.5], [0.045, 1.52], [0, 1.52]]
      : [[0.155, 0.97], [0.16, 1.06], [0.172, 1.18], [0.188, 1.29], [0.198, 1.36], [0.19, 1.42], [0.15, 1.47], [0.07, 1.5], [0.05, 1.52], [0, 1.52]];
    ub.add(lathe(ctx, fem ? 'torsoF' : 'torsoM', torsoPts, segs), fem ? Robe : Skin, T(0, 0, 0, 0, 0, 0, 1, 1, 0.72));
    if (pose.seat) ub.add(G.torus(ctx, 0.08, 4, 20), Gold, T(0, 0.98, 0, Math.PI / 2, 0, 0, 0.33, 0.27, 0.33));
    // uttariya / pallu across the chest (diagonal band)
    ub.within(T(0, 1.22, 0, 0, 0, 0.55), () => ub.add(G.torus(ctx, fem ? 0.14 : 0.09, 5, 24), Sash, T(0, 0, 0, Math.PI / 2, 0, 0, 0.42, 0.31, 0.42)));
    ub.add(G.box(ctx), Sash, T(0.07, 1.06, -0.135, 0.1, 0, 0.04, fem ? 0.17 : 0.1, fem ? 0.78 : 0.5, 0.02)); // drape behind the left shoulder
    ub.add(G.cyl(ctx, 8), Skin, T(0, 1.55, 0, 0, 0, 0, 0.09, 0.1, 0.09)); // neck
    ub.add(G.sphere(ctx, segs, 10), Skin, T(0, 1.66, 0, 0, 0, 0, 0.2, 0.235, 0.215)); // head — a calm, featureless form
    ub.add(G.torus(ctx, 0.1, 4, 20), Gold, T(0, 1.45, 0.01, Math.PI / 2, 0, 0, 0.21, 0.16, 0.21)); // necklace
    ub.add(G.sphere(ctx, 6, 4), Gold, T(0, 1.39, 0.125, 0, 0, 0, 0.03));
    for (const sx of [-1, 1]) ub.add(G.sphere(ctx, 6, 4), Gold, T(sx * 0.1, 1.61, 0, 0, 0, 0, 0.034));
    // hair + headwear
    if (headwear !== 'hood' && !(bald && headwear === 'none')) ub.add(G.sphere(ctx, segs, 8), Hair, T(0, 1.705, -0.03, 0, 0, 0, 0.212, 0.19, 0.2)); // hairline sits back so the face reads
    if ((fem && headwear !== 'hood' && !bald) || headwear === 'hair') {
      ub.add(G.sphere(ctx, 8, 6), Hair, T(0, 1.65, -0.12, 0, 0, 0, 0.13));
      ub.add(G.cyl(ctx, 6), Hair, rod([0, 1.62, -0.14], [0, 1.0, -0.17], 0.06));
    }
    if (headwear === 'crown') {
      const fea = kinds.has('feather');
      ub.add(lathe(ctx, 'mukuta', MUKUTA_PTS, seg(ctx, 10, 12, 16)), Gold, T(0, 1.72, -0.005, 0, 0, 0, 1, 0.85, 1));
      ub.add(G.sphere(ctx, 6, 4), mat(ctx, { color: '#A3242C', roughness: 0.2, emissive: '#A3242C', ei: 0.25 }), T(0, 1.75, 0.118, 0, 0, 0, 0.032));
      if (fea) ub.add(G.sphere(ctx, 8, 6), mat(ctx, { color: '#2E7D6B', roughness: 0.5 }), T(-0.03, 2.02, -0.06, -0.35, 0, 0.25, 0.05, 0.3, 0.012));
    } else if (headwear === 'turban') {
      ub.add(G.torus(ctx, 0.3, 6, 20), Sash, T(0, 1.73, 0, Math.PI / 2, 0, 0, 0.22));
      ub.add(G.sphere(ctx, 10, 6), Sash, T(0, 1.77, 0, 0, 0, 0, 0.2, 0.12, 0.2));
      ub.add(G.sphere(ctx, 6, 4), Gold, T(0, 1.75, 0.13, 0, 0, 0, 0.035));
    } else if (headwear === 'bun') {
      ub.add(G.sphere(ctx, 8, 6), Hair, T(0, 1.81, -0.01, 0, 0, 0, 0.13));
      ub.add(G.torus(ctx, 0.2, 4, 12), Gold, T(0, 1.78, -0.01, Math.PI / 2, 0, 0, 0.1));
    } else if (headwear === 'hood') {
      ub.add(G.sphere(ctx, segs, 8), Sash, T(0, 1.68, -0.02, 0, 0, 0, 0.24, 0.25, 0.25));
    }
    if (blindfold) ub.add(G.torus(ctx, 0.15, 4, 20), clothM(ctx, blindC, { double: false }), T(0, 1.675, 0, Math.PI / 2, 0, 0, 0.226, 0.218, 0.35));
    if (beard) ub.add(G.sphere(ctx, 10, 8), beardC === hair ? Hair : mat(ctx, { color: beardC, roughness: 0.75 }), T(0, 1.545, 0.045, 0.15, 0, 0, 0.13, 0.2, 0.09)); // soft rounded beard under the chin
    if (halo) ub.add(G.torus(ctx, 0.04, 4, 40), glowM(ctx, '#F2C866', 1.1), T(0, 1.7, -0.14, 0, 0, 0, 0.6));
    if (kinds.has('flute')) {
      // bansuri tucked into the waist sash, angled across the hip
      ub.add(G.cyl(ctx, 6), woodM(ctx, '#7A5530'), rod([0.2, 0.9, 0.12], [-0.12, 1.04, 0.16], 0.026));
      for (const k of [0.15, 0.85]) ub.add(G.cyl(ctx, 6), Gold, rod([0.2 - 0.32 * k, 0.9 + 0.14 * k, 0.12 + 0.04 * k], [0.2 - 0.32 * (k + 0.03), 0.9 + 0.14 * (k + 0.03), 0.12 + 0.04 * (k + 0.03)], 0.032));
    }
    // arms via two-bone IK
    const A = 0.3, B = 0.27;
    for (const side of ['L', 'R']) {
      const sx = side === 'L' ? 1 : -1;
      const S = V(sx * (fem ? 0.2 : 0.215), 1.4, 0);
      const { elbow, hand } = solveArm(S, V(...pose[side]), A, B, V(...pose[side + 'h']));
      hands[side] = hand;
      ub.add(G.sphere(ctx, 8, 6), fem ? Robe : Skin, T(S.x, S.y, S.z, 0, 0, 0, 0.12));
      ub.add(G.cyl(ctx, 8), Skin, rod(arr(S), arr(elbow), fem ? 0.08 : 0.09));
      ub.add(G.sphere(ctx, 8, 6), Skin, T(elbow.x, elbow.y, elbow.z, 0, 0, 0, 0.085));
      ub.add(G.cyl(ctx, 8), Skin, rod(arr(elbow), arr(hand), 0.075));
      ub.add(G.sphere(ctx, 8, 6), Skin, T(hand.x, hand.y, hand.z, 0, 0, 0, 0.08, 0.095, 0.07));
      const band = (a0, a1, r) => ub.add(G.cyl(ctx, 8), Gold, rod(arr(S.clone().lerp(elbow, a0)), arr(S.clone().lerp(elbow, a1)), r));
      band(0.32, 0.4, 0.1);
      const w0 = elbow.clone().lerp(hand, 0.8), w1 = elbow.clone().lerp(hand, 0.88);
      ub.add(G.cyl(ctx, 8), Gold, rod(arr(w0), arr(w1), 0.088));
    }
  });
  ub.build(upper, ctx, `figU:${key}`);

  // ---------------- attributes (separate factories, attached to the upper body)
  const local = (v) => V(v.x, v.y - waist, v.z); // standing coords → upper-group coords
  const attach = (obj, name, pos, rot = [0, 0, 0]) => { obj.name = 'attr_' + name; obj.position.copy(pos); obj.rotation.set(...rot); upper.add(obj); return obj; };
  const isGandiva = [p.attribute, ...(Array.isArray(p.attributes) ? p.attributes : [])].some((a) => /gandiva/i.test(String(a || '')));
  if (kinds.has('bow')) {
    attach(OBJECTS.bow({ length: 1.55, divine: isGandiva, draw: poseName === 'archer' ? 0.64 : 0 }, ctx), 'bow', local(hands.L));
  }
  if (kinds.has('chakra')) {
    const raised = hands.R.y > 1.3;
    attach(OBJECTS.chakra({ radius: raised ? 0.14 : 0.12 }, ctx), 'chakra', raised ? local(hands.R).add(V(0, 0.2, 0.02)) : local(V(-0.34, 1.86, -0.08)));
  }
  if (kinds.has('conch')) attach(OBJECTS.conch({ length: 0.3 }, ctx), 'conch', local(hands.L).add(V(0.02, 0.03, 0.02)));
  if (kinds.has('mace')) {
    if (poseName === 'mace') attach(OBJECTS.mace({ length: 1.0 }, ctx), 'mace', local(hands.R), [-0.55, 0, 0.25]);
    else attach(OBJECTS.mace({ length: Math.max(0.4, hands.R.y + 0.04) }, ctx), 'mace', local(hands.R), [Math.PI, 0, 0]);
  }
  if (kinds.has('weapon')) attach(OBJECTS.weapon({ float: 0.35, length: 0.9 }, ctx), 'weapon', local(hands.R));
  if (kinds.has('scroll')) attach(OBJECTS.scroll({ size: 0.7 }, ctx), 'scroll', local(hands.L).add(V(-0.05, 0.03, 0.02)), [0, 0.4, 0]);
  if (kinds.has('shield')) attach(OBJECTS.shield({ radius: 0.32 }, ctx), 'shield', local(hands.L).add(V(0.07, 0.06, 0.05)), [0, 0.6, 0]);
  if (kinds.has('quiver')) attach(OBJECTS.arrows({ mode: 'quiver', size: 0.9, count: 8 }, ctx), 'quiver', local(V(0.1, 1.0, -0.19)), [0.12, 0, 0.42]);
  if (kinds.has('lamp')) attach(OBJECTS.lamp({ stand: false, size: 0.9 }, ctx), 'lamp', local(hands.L).add(V(0, 0.03, 0.02)));
  if (kinds.has('vessel')) attach(OBJECTS.vessel({ size: 0.75, kalasha: true }, ctx), 'vessel', local(hands.L).add(V(0, 0.02, 0.03)));
  if (kinds.has('dice')) attach(OBJECTS.dice({ board: false, count: 2, size: 1.1, seed: 'hand' }, ctx), 'dice', local(hands.L).add(V(0, 0.035, 0.02)));

  // ---------------- final posing + life
  let topY = headwear === 'crown' ? 2.03 : 1.79;
  if (pose.lying) {
    body.rotation.x = -Math.PI / 2; body.position.y = 0.13 * s;
    root.userData.anchors = { head: [0, 0.25 * s, -1.68 * s] };
  } else {
    topY -= drop;
    root.userData.anchors = { head: [0, topY * s, 0], hand_left: arr(hands.L).map((v, i) => (i === 1 ? v - drop : v) * s), hand_right: arr(hands.R).map((v, i) => (i === 1 ? v - drop : v) * s) };
  }
  root.userData.pose = poseName;
  root.userData.height = H;
  root.userData.attributes = [...kinds];
  const ph = rng(key + String(p.seed ?? ''))() * 10;
  const baseRx = upper.rotation.x;
  addUpdate(root, (dt, t) => { // breath + a barely-there sway
    upper.scale.y = 1 + 0.007 * Math.sin(t * 1.25 + ph);
    upper.rotation.x = baseRx + 0.006 * Math.sin(t * 0.6 + ph);
    if (!pose.lying && !pose.seat) body.rotation.z = 0.004 * Math.sin(t * 0.45 + ph * 2);
  });
  root.userData.breathe = { amplitude: 0.007 };
  return root;
}

// ================================================================ horse
function horse(params, ctx) {
  const p = P(params);
  const k = num(p.size, 0.86, 0.3, 4); // ≈2.4 long, withers ≈1.35
  const coat = col(p, ['coat'], baseColor(p, '#EFE9DE'));
  const maneC = col(p, ['mane', 'hair'], '#' + new THREE.Color(coat).lerp(new THREE.Color('#7a6a58'), 0.35).getHexString());
  const clothC = col(p, ['cloth', 'blanket', 'accent'], PAL.vermilion), goldC = col(p, ['gold'], PAL.gold);
  const ornate = bool(p.ornate, true);
  const root = new THREE.Group(); root.name = 'horse'; root.userData.kind = 'horse';
  const body = new THREE.Group(); body.name = 'horse_body'; body.scale.setScalar(k); root.add(body);
  const Coat = mat(ctx, { color: coat, roughness: 0.55 }), Mane = mat(ctx, { color: maneC, roughness: 0.8 }), Gd = goldM(ctx, goldC);
  const Cloth = clothM(ctx, clothC, { double: false });
  const segs = seg(ctx, 10, 12, 16);
  const b = new Batch();
  b.add(G.sphere(ctx, segs, 10), Coat, T(0, 1.22, 0, 0, 0, 0, 0.56, 0.6, 1.25));
  b.add(G.sphere(ctx, segs, 10), Coat, T(0, 1.28, 0.48, 0, 0, 0, 0.52, 0.6, 0.55));
  b.add(G.sphere(ctx, segs, 10), Coat, T(0, 1.27, -0.5, 0, 0, 0, 0.56, 0.58, 0.6));
  b.add(G.frustum(ctx, 0.62, segs), Coat, rod([0, 1.38, 0.62], [0, 1.92, 0.98], 0.34, 0.4));
  b.add(G.frustum(ctx, 0.55, segs), Coat, rod([0, 2.0, 0.98], [0, 1.72, 1.34], 0.24, 0.26));
  b.add(G.sphere(ctx, 10, 8), Coat, T(0, 2.0, 0.98, 0, 0, 0, 0.22, 0.24, 0.26));
  b.add(G.sphere(ctx, 10, 8), Coat, T(0, 1.69, 1.35, 0, 0, 0, 0.15, 0.13, 0.16));
  for (const sx of [-1, 1]) b.add(G.cone(ctx, 5), Coat, T(sx * 0.06, 2.15, 0.94, -0.2, 0, -sx * 0.15, 0.06, 0.13, 0.05));
  b.add(G.box(ctx), Mane, rod([0, 1.62, 0.5], [0, 2.1, 0.93], 0.05, 0.16));
  b.add(G.box(ctx), Mane, T(0, 2.06, 1.07, 0.6, 0, 0, 0.05, 0.12, 0.08));
  if (ornate) {
    b.add(G.cyl(ctx, segs), Gd, rod([0, 1.56, 0.72], [0, 1.64, 0.8], 0.36, 0.42));
    b.add(G.box(ctx), Cloth, T(0, 1.55, -0.05, 0, 0, 0, 0.66, 0.04, 0.7));
    b.add(G.box(ctx), Gd, T(0, 1.53, -0.05, 0, 0, 0, 0.68, 0.03, 0.72));
    b.add(G.cone(ctx, 6), Cloth, T(0, 2.22, 0.98, -0.15, 0, 0, 0.07, 0.2, 0.07));
    b.add(G.sphere(ctx, 6, 4), Gd, T(0, 2.1, 1.0, 0, 0, 0, 0.06));
    b.add(G.cyl(ctx, 8), Gd, rod([0, 1.85, 1.1], [0, 1.83, 1.14], 0.25, 0.24)); // bridle band
  }
  b.add(G.frustum(ctx, 0.35, 8), Mane, rod([0, 1.42, -1.02], [0, 0.66, -1.28], 0.2));
  b.build(body, ctx, `horse:${coat}:${maneC}:${clothC}:${goldC}:${ornate}:${qname(ctx)}`);
  // legs on hip/shoulder pivots
  const legs = [];
  for (const [x, z, front] of [[0.17, 0.5, 1], [-0.17, 0.5, 1], [0.17, -0.52, 0], [-0.17, -0.52, 0]]) {
    const pivot = new THREE.Group(); pivot.name = 'horse_leg'; pivot.position.set(x, 1.1, z);
    const lb = new Batch();
    lb.add(G.frustum(ctx, 0.6, 8), Coat, rod([0, 0.05, 0], [0, -0.48, front ? 0.02 : -0.04], 0.22));
    lb.add(G.sphere(ctx, 8, 6), Coat, T(0, -0.48, front ? 0.02 : -0.04, 0, 0, 0, 0.13));
    lb.add(G.cyl(ctx, 8), Coat, rod([0, -0.48, front ? 0.02 : -0.04], [0, -0.98, 0.02], 0.1));
    lb.add(G.frustum(ctx, 1.25, 8), Coat, T(0, -1.04, 0.03, 0, 0, 0, 0.14, 0.12, 0.15)); // hoof
    lb.build(pivot, ctx, `horseLeg:${front}:${coat}`);
    body.add(pivot); legs.push([pivot, front, x]);
  }
  // tail lives in the body batch (fewer draw calls); its swish is carried by a gentle body sway instead
  root.userData.gallop = bool(p.gallop, false);
  root.userData.setGallop = (on) => { root.userData.gallop = !!on; };
  const ph = rng(String(p.seed ?? coat) + legs.length)() * 10;
  let gt = 0, blend = 0;
  addUpdate(root, (dt, t) => {
    blend += ((root.userData.gallop ? 1 : 0) - blend) * Math.min(1, dt * 4);
    gt += dt * (6 + 3 * blend);
    for (const [pv, front, x] of legs) {
      const off = (front ? 0 : Math.PI * 0.9) + (x > 0 ? 0 : 0.6);
      pv.rotation.x = blend * 0.55 * Math.sin(gt + off);
    }
    body.position.y = blend * 0.05 * Math.abs(Math.sin(gt));
    body.rotation.y = 0.01 * Math.sin(t * 0.7 + ph);
  });
  root.userData.anchors = { back: [0, 1.55 * k, 0], head: [0, 2.2 * k, 1.1 * k] };
  return root;
}

// ================================================================ dog
function dog(params, ctx) {
  const p = P(params);
  const k = num(p.size, 1, 0.3, 4);
  const coat = col(p, ['coat'], baseColor(p, '#D9CDB8'));
  const root = new THREE.Group(); root.name = 'dog'; root.userData.kind = 'dog';
  const body = new THREE.Group(); body.scale.setScalar(k); root.add(body);
  const C = mat(ctx, { color: coat, roughness: 0.75 }), Dk = darkM(ctx, '#2a211b');
  const b = new Batch();
  b.add(G.sphere(ctx, 12, 8), C, T(0, 0.5, -0.02, 0, 0, 0, 0.28, 0.3, 0.64));
  b.add(G.sphere(ctx, 10, 8), C, T(0, 0.55, 0.22, 0, 0, 0, 0.3, 0.34, 0.3));
  b.add(G.frustum(ctx, 0.75, 8), C, rod([0, 0.58, 0.26], [0, 0.76, 0.42], 0.2));
  b.add(G.sphere(ctx, 10, 8), C, T(0, 0.79, 0.46, 0, 0, 0, 0.2, 0.19, 0.22));
  b.add(G.frustum(ctx, 0.6, 8), C, rod([0, 0.77, 0.52], [0, 0.73, 0.67], 0.12));
  b.add(G.sphere(ctx, 6, 4), Dk, T(0, 0.735, 0.675, 0, 0, 0, 0.04));
  for (const sx of [-1, 1]) {
    b.add(G.cone(ctx, 4), C, T(sx * 0.06, 0.92, 0.42, -0.2, 0, -sx * 0.25, 0.07, 0.12, 0.04));
    for (const z of [0.2, -0.22]) b.add(G.cyl(ctx, 6), C, rod([sx * 0.08, 0.48, z], [sx * 0.085, 0.02, z + 0.01], 0.075));
  }
  b.build(body, ctx, `dog:${coat}`);
  const tail = new THREE.Group(); tail.name = 'dog_tail'; tail.position.set(0, 0.6, -0.3);
  const tb = new Batch();
  tb.add(G.frustum(ctx, 0.4, 6), C, rod([0, 0, 0], [0, 0.18, -0.16], 0.07));
  tb.build(tail, ctx, `dogTail:${coat}`);
  body.add(tail);
  addUpdate(root, (dt, t) => { tail.rotation.z = 0.35 * Math.sin(t * 5.5); body.scale.y = k * (1 + 0.01 * Math.sin(t * 2.2)); });
  root.userData.anchors = { head: [0, 0.95 * k, 0.46 * k] };
  return root;
}

// ================================================================ chariot (ratha)
function chariot(params, ctx) {
  const p = P(params);
  const nH = int(p.horses ?? p.horse_count ?? p.horseCount, 2, 0, 8);
  const wood = col(p, ['body', 'wood'], baseColor(p, '#8A5A2E')), accent = accentColor(p, PAL.gold);
  const canopyC = col(p, ['canopyColor', 'canopy_color', 'canopy'], PAL.vermilion); // `canopy` may also be a boolean
  const horseC = col(p, ['horseColor', 'horse_color'], '#EFE9DE');
  const root = new THREE.Group(); root.name = 'chariot'; root.userData.kind = 'chariot';
  const rig = new THREE.Group(); rig.name = 'chariot_rig'; root.add(rig);
  const Wd = woodM(ctx, wood), WdD = mat(ctx, { color: wood, roughness: 0.75, double: true }), Gd = goldM(ctx, accent);
  const Rw = 0.75, wx = 0.92, wz = -0.25;
  const b = new Batch();
  b.box(ctx, Wd, 1.5, 0.1, 1.3, 0, 0.95, 0);
  b.add(geo(ctx, 'rathaFront', () => new THREE.CylinderGeometry(0.75, 0.75, 0.7, 14, 1, true, -Math.PI / 2, Math.PI)), WdD, T(0, 1.35, 0.1, 0, 0, 0, 1, 1, 0.55));
  b.add(G.torus(ctx, 0.04, 4, 16, Math.PI), Gd, T(0, 1.7, 0.1, Math.PI / 2, 0, 0, 1.5, 0.82, 1.5));
  b.add(G.torus(ctx, 0.04, 4, 16, Math.PI), Gd, T(0, 1.02, 0.1, Math.PI / 2, 0, 0, 1.5, 0.82, 1.5));
  for (const sx of [-1, 1]) {
    b.box(ctx, Wd, 0.06, 0.55, 0.9, sx * 0.74, 1.28, -0.3);
    b.box(ctx, Gd, 0.09, 0.05, 0.94, sx * 0.74, 1.57, -0.3);
  }
  b.add(G.sphere(ctx, 10, 8), Gd, T(0, 1.32, 0.52, 0, 0, 0, 0.26, 0.26, 0.08));
  b.add(G.cyl(ctx, 8), Wd, T(0, Rw, wz, 0, 0, Math.PI / 2, 0.1, wx * 2 + 0.3, 0.1)); // axle
  if (bool(p.canopy, true)) {
    for (const sx of [-1, 1]) for (const z of [0.3, -0.62]) b.add(G.cyl(ctx, 6), Gd, T(sx * 0.62, 1.8, z, 0, 0, 0, 0.05, 1.7, 0.05));
    b.add(lathe(ctx, 'rathaCanopy', [[0.98, 0], [0.92, 0.06], [0.72, 0.24], [0.46, 0.44], [0.22, 0.6], [0.07, 0.7], [0, 0.73]], 12, Math.PI / 12), clothM(ctx, canopyC, { double: true }), T(0, 2.64, -0.16, 0, 0, 0, 0.9, 0.9, 0.75));
    b.add(G.torus(ctx, 0.04, 4, 24), Gd, T(0, 2.65, -0.16, Math.PI / 2, 0, 0, 1.76, 1.47, 1.76));
    b.add(kalashaLike(ctx), Gd, T(0, 3.28, -0.16, 0, 0, 0, 0.3));
    for (let i = 0; i < 16; i++) { const a = (i / 16) * TAU; b.add(G.cone(ctx, 4), Gd, T(Math.cos(a) * 0.88, 2.56, -0.16 + Math.sin(a) * 0.73, Math.PI, 0, 0, 0.05, 0.12, 0.05)); }
  }
  // pole (isha) + yoke when harnessed
  const zH = 0.65 + 1.3; // horse centre
  if (nH > 0) {
    const yY = 1.42, yZ = zH + 0.55; // yoke rests just ahead of the withers
    b.add(G.cyl(ctx, 6), Wd, rod([0, 0.95, 0.5], [0, yY, yZ], 0.08));
    for (const f of [0.3, 0.6]) { const lp = (k) => [0, 0.95 + (yY - 0.95) * k, 0.5 + (yZ - 0.5) * k]; b.add(G.cyl(ctx, 6), Gd, rod(lp(f), lp(f + 0.03), 0.1)); }
    b.box(ctx, Wd, Math.max(1, nH * 0.78), 0.08, 0.1, 0, yY, yZ);
  }
  b.build(rig, ctx, `ratha:${wood}:${accent}:${canopyC}:${p.canopy}:${nH > 0}:${nH}`);
  // wheels on axle pivots
  const wheels = [];
  for (const sx of [-1, 1]) {
    const w = new THREE.Group(); w.name = sx < 0 ? 'wheel_right' : 'wheel_left';
    w.position.set(sx * wx, Rw, wz);
    w.userData.spin = { axis: 'x' };
    const wb = new Batch();
    wb.add(G.torus(ctx, 0.06, 5, seg(ctx, 20, 28, 36)), Wd, T(0, 0, 0, 0, Math.PI / 2, 0, Rw * 2));
    wb.add(G.torus(ctx, 0.02, 4, seg(ctx, 20, 28, 36)), Gd, T(sx * 0.03, 0, 0, 0, Math.PI / 2, 0, Rw * 2.04));
    wb.add(G.cyl(ctx, 10), Gd, T(0, 0, 0, 0, 0, Math.PI / 2, 0.24, 0.22, 0.24));
    const spokes = qname(ctx) === 'low' ? 8 : 12;
    for (let i = 0; i < spokes; i++) {
      const a = (i / spokes) * TAU;
      wb.add(G.cyl(ctx, 5), Wd, rod([0, Math.sin(a) * 0.1, Math.cos(a) * 0.1], [0, Math.sin(a) * Rw * 0.96, Math.cos(a) * Rw * 0.96], 0.045));
    }
    wb.build(w, ctx, `rathaWheel:${sx}:${wood}:${accent}:${qname(ctx)}`);
    rig.add(w); wheels.push(w);
  }
  // banner on a tall staff at the back
  if (p.banner !== false && p.banner !== 'none') {
    const raw = String((typeof p.banner === 'string' ? p.banner : '') || p.emblem || 'yantra').toLowerCase();
    const emblem = /kapi|monkey|vanara|hanuman/.test(raw) ? 'monkey' : raw.replace(/^obj\./, '');
    const bn = OBJECTS.banner({ height: num(p.bannerHeight ?? p.banner_height, 3.8, 1, 12), color: col(p, ['bannerColor', 'banner_color'], '#D9822B'), accent, emblem, width: 1.1, flagHeight: 0.62 }, ctx);
    bn.position.set(0, 0.95, -0.66);
    rig.add(bn);
  }
  // horses
  const horses = [];
  for (let i = 0; i < nH; i++) {
    const h = horse({ color: horseC, cloth: col(p, ['horseCloth', 'horse_cloth'], canopyC), seed: 'h' + i, ornate: true }, ctx);
    h.position.set((i - (nH - 1) / 2) * 0.78, 0, zH);
    rig.add(h); horses.push(h);
  }
  root.userData.rolling = bool(p.rolling, false);
  const speed = num(p.speed, 4, 0, 40);
  const setRolling = (on) => { root.userData.rolling = !!on; for (const h of horses) h.userData.setGallop?.(!!on); };
  setRolling(root.userData.rolling);
  root.userData.setRolling = setRolling;
  let v = 0;
  addUpdate(root, (dt) => {
    v += ((root.userData.rolling ? speed : 0) - v) * Math.min(1, dt * 2);
    if (Math.abs(v) > 1e-3) for (const w of wheels) w.rotation.x += (dt * v) / Rw;
    for (const h of horses) if (h.userData.gallop !== root.userData.rolling) h.userData.setGallop?.(root.userData.rolling);
  });
  root.userData.anchors = { driver: [0, 1.0, 0.25], warrior: [0, 1.0, -0.3] };
  return root;
}
const kalashaLike = (ctx) => lathe(ctx, 'finial', [[0, 0], [0.18, 0], [0.22, 0.08], [0.12, 0.16], [0.3, 0.3], [0.32, 0.42], [0.2, 0.52], [0.08, 0.58], [0.14, 0.66], [0.06, 0.72], [0.025, 1], [0, 1]], seg(ctx, 6, 8, 10));

export const FIGURES = { figure, horse, dog, chariot };
