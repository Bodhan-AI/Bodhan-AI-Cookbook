// Environment factories: mountains, river, water, forest, grove, road, skyline, garden, terrain, army, clouds,
// mist, dust, rain, fire, stars, sky, mandala, snow, lake. Cheap and atmospheric: merged or instanced geometry,
// counts scaled by quality (low 0.35x, medium 0.7x, high 1x), animation via obj.userData.update(dt, t).
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import {
  THREE, PAL, P, num, int, bool, str, col, hex, baseColor, accentColor, seg, qf, qcount, qname, rng, fbm, smooth, G, lathe,
  geo, T, Batch, mat, stone, goldM, clothM, woodM, darkM, addUpdate, flame, softDotTex, cloudNoiseTex, radialFadeTex,
  waterMat, flowWater, stripGeometry, boundsOf, idOf, noRaycast,
} from './kit.js';
import { OBJECTS } from './objects.js';
import { FIGURES } from './figures.js';

const TAU = Math.PI * 2;
function group(name) { const g = new THREE.Group(); g.name = name; g.userData.kind = 'environment'; return g; }
function areaOf(p, d) {
  const a = Array.isArray(p.area) && p.area.length === 3 ? p.area.map((v, i) => num(v, d[i], 0.5, 2000)) : d;
  return a;
}
function paint(g, hexStr) {
  const c = new THREE.Color(hexStr), n = g.attributes.position.count, a = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) { a[i * 3] = c.r; a[i * 3 + 1] = c.g; a[i * 3 + 2] = c.b; }
  g.setAttribute('color', new THREE.BufferAttribute(a, 3));
  return g;
}
/** Make geometries merge-compatible: keep only listed attributes, ensure an index. */
function only(g, attrs) {
  for (const k of Object.keys(g.attributes)) if (!attrs.includes(k)) g.deleteAttribute(k);
  if (!g.index) { const n = g.attributes.position.count, idx = new Array(n); for (let i = 0; i < n; i++) idx[i] = i; g.setIndex(idx); }
  return g;
}
/** Pin a background object (sky, stars) to the camera every frame, ignoring parent transforms. */
function followCamera(obj, spin = null) {
  const m = new THREE.Matrix4(), r = new THREE.Matrix4();
  obj.onBeforeRender = (renderer, scene, camera) => {
    m.makeTranslation(camera.position.x, camera.position.y, camera.position.z);
    if (spin) m.multiply(r.makeRotationY(spin.angle));
    obj.matrixWorld.copy(m);
  };
}

// ================================================================ mountains
// Two layouts. Ring (radius/arc given, or nothing known about the footprint): peaks encircle the origin, for a
// range placed at the scene centre. Band (registry bounds, `spread` or `width`): a range laid along x inside a
// w×d footprint centred on the origin, front (+z) edge at d/2 — the engine places these at negative z, so a
// ring there would wrap peaks around the stage and behind the camera.
function mountains(params, ctx) {
  const p = P(params);
  const bw = boundsOf(ctx);
  const ring = p.radius !== undefined || p.arc !== undefined || (!bw && p.spread === undefined && p.width === undefined);
  const n = int(p.count, ring ? 24 : 9, 3, 120), R = num(p.radius, 160, 20, 900), H = num(p.height, bw ? bw[1] : 45, 4, 400);
  const spanW = num(p.spread ?? p.width, bw ? bw[0] : 300, 20, 4000), spanD = num(p.depth, bw ? bw[2] : spanW * 0.4, 10, 2000);
  const arc = (num(p.arc, 360, 10, 360) * Math.PI) / 180;
  // registry uses a snow colour and/or snowline instead of a boolean
  const snow = bool(p.snow, false) || !!hex(p.snow, null) || num(p.snowline, 0) > 0;
  const rock = col(p, ['rock'], baseColor(p, snow ? '#6F6C75' : '#76705F'));
  const snowC = col(p, ['snowColor', 'snow_color', 'snow'], '#F1F3F8'), hillC = col(p, ['foothills_color', 'hillColor'], '#5F6B4A');
  const snowLine = num(p.snowline, 0.52, 0.1, 1);
  const foothills = bool(p.foothills, true);
  const seed = p.seed ?? 'giri';
  const key = `mtn:${JSON.stringify([ring, n, R, H, spanW, spanD, arc, snow, rock, snowC, snowLine, hillC, foothills, seed, qname(ctx)])}`;
  const geometry = geo(ctx, key, () => {
    const r = rng(seed), parts = [];
    const radial = seg(ctx, 6, 8, 10), rows = seg(ctx, 3, 5, 6);
    const rockC = new THREE.Color(rock), snowCol = new THREE.Color(snowC), hillCol = new THREE.Color(hillC), tmp = new THREE.Color();
    const peak = (x, z, h, rad, isHill, sd) => {
      const g = new THREE.ConeGeometry(rad, h, radial, rows, true).toNonIndexed();
      const pos = g.attributes.position;
      for (let i = 0; i < pos.count; i++) {
        const vx = pos.getX(i), vy = pos.getY(i), vz = pos.getZ(i), t = (vy + h / 2) / h;
        const nz = fbm(vx * 0.06 + sd, vz * 0.06 - sd, 3, 3) - 0.5;
        const k = 1 + nz * 0.55 * (1 - t * 0.5);
        pos.setXYZ(i, vx * k, vy + nz * h * 0.12 * (1 - Math.abs(t - 0.5) * 2) + (t > 0.98 ? r.range(-0.03, 0.03) * h : 0), vz * k);
      }
      g.translate(x, h / 2, z);
      g.computeVertexNormals();
      const colors = new Float32Array(pos.count * 3);
      for (let i = 0; i < pos.count; i += 3) { // per face colour (flat look)
        const y = (pos.getY(i) + pos.getY(i + 1) + pos.getY(i + 2)) / 3;
        const ny = g.attributes.normal.getY(i);
        const line = H * snowLine + fbm(pos.getX(i) * 0.05, pos.getZ(i) * 0.05, 9, 2) * H * 0.25;
        if (!isHill && snow && y > line && ny > 0.25) tmp.copy(snowCol).offsetHSL(0, 0, r.range(-0.04, 0.02));
        else tmp.copy(isHill ? hillCol : rockC).offsetHSL(r.range(-0.01, 0.01), r.range(-0.03, 0.03), r.range(-0.06, 0.05) + (ny - 0.5) * 0.06);
        for (let j = 0; j < 3; j++) { colors[(i + j) * 3] = tmp.r; colors[(i + j) * 3 + 1] = tmp.g; colors[(i + j) * 3 + 2] = tmp.b; }
      }
      g.setAttribute('color', new THREE.BufferAttribute(colors, 3));
      parts.push(only(g, ['position', 'normal', 'color']));
    };
    if (ring) {
      for (let i = 0; i < n; i++) {
        const a = Math.PI + (arc >= TAU - 1e-3 ? (i / n) * TAU : (i / Math.max(1, n - 1) - 0.5) * arc) + r.range(-0.08, 0.08);
        const d = R * r.range(0.85, 1.25), h = H * r.range(0.55, 1.25);
        peak(Math.sin(a) * d, Math.cos(a) * d, h, h * r.range(0.95, 1.45), false, i * 3.1);
      }
      if (foothills) for (let i = 0; i < Math.round(n * 0.8); i++) {
        const a = Math.PI + (arc >= TAU - 1e-3 ? r() * TAU : (r() - 0.5) * arc);
        const d = R * r.range(0.6, 0.8), h = H * r.range(0.18, 0.32);
        peak(Math.sin(a) * d, Math.cos(a) * d, h, h * r.range(2.2, 3.2), true, 50 + i);
      }
    } else {
      // band: every cone's footprint stays inside z ∈ [-d/2, d/2] (steeper peaks when the band is shallow)
      for (let i = 0; i < n; i++) {
        const h = H * r.range(0.55, 1.25), rad = Math.min(h * r.range(0.95, 1.45), spanD * 0.45);
        const x = Math.max(-spanW / 2 + rad * 0.5, Math.min(spanW / 2 - rad * 0.5, -spanW / 2 + ((i + 0.5) / n) * spanW + r.range(-0.3, 0.3) * (spanW / n)));
        peak(x, r.range(-spanD * 0.15, spanD * 0.05), h, rad, false, i * 3.1);
      }
      if (foothills) for (let i = 0; i < Math.round(n * 0.8); i++) {
        const h = H * r.range(0.18, 0.32), rad = Math.min(h * r.range(2.2, 3.2), spanD * 0.25);
        peak(r.range(-0.5, 0.5) * (spanW - rad), r.range(spanD * 0.05, spanD * 0.25), h, rad, true, 50 + i);
      }
    }
    return mergeGeometries(parts, false);
  });
  const g = group('mountains');
  const m = new THREE.Mesh(geometry, mat(ctx, { color: '#ffffff', vc: true, flat: true, roughness: 1 }));
  m.receiveShadow = true; m.castShadow = false;
  g.add(m);
  return g;
}

// ================================================================ ribbon helper (rivers, roads)
/** Ribbon along a polyline in XZ. `across` = array of {o (offset), c (Color)?} columns; returns geometry with uv (u across, v along). */
function ribbon(points, across, y, uvScale = 0.15, withColor = false) {
  const pos = [], uv = [], colr = [], idx = [], cols = across.length;
  let along = 0;
  for (let i = 0; i < points.length; i++) {
    const p0 = points[Math.max(0, i - 1)], p1 = points[Math.min(points.length - 1, i + 1)];
    let tx = p1[0] - p0[0], tz = p1[1] - p0[1];
    const l = Math.hypot(tx, tz) || 1; tx /= l; tz /= l;
    const nx = -tz, nz = tx;
    if (i > 0) along += Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]);
    for (const a of across) {
      pos.push(points[i][0] + nx * a.o, y, points[i][1] + nz * a.o);
      uv.push(a.o * uvScale, along * uvScale);
      if (withColor) colr.push(a.c.r, a.c.g, a.c.b);
    }
    if (i < points.length - 1) for (let j = 0; j < cols - 1; j++) {
      const k = i * cols + j;
      idx.push(k, k + 1, k + cols, k + 1, k + cols + 1, k + cols); // counter-clockwise seen from above
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  if (withColor) g.setAttribute('color', new THREE.Float32BufferAttribute(colr, 3));
  g.setIndex(idx);
  g.computeVertexNormals();
  return g;
}

// ================================================================ river
function river(params, ctx) {
  const p = P(params);
  const L = num(p.length, 400, 20, 3000), W = num(p.width, 18, 1, 400), amp = num(p.meander ?? p.amplitude, W * 1.2, 0, 1000);
  // `speed` is ripple scroll in texture units/s; registry `flow_speed` is roughly metres/s (texture repeats every 10 m)
  const bends = num(p.bends, 1.5, 0, 12), speed = num(p.speed ?? (p.flow_speed !== undefined ? num(p.flow_speed, 0.6) * 0.1 : undefined), 0.06, 0, 2), off = num(p.offset, 0, -2000, 2000);
  const color = baseColor(p, PAL.water), bankC = col(p, ['bank', 'bankColor', 'banks', 'sand'], '#B9A27A');
  const g = group('river');
  const n = seg(ctx, 50, 90, 140), pts = [];
  for (let i = 0; i <= n; i++) { const x = -L / 2 + (i / n) * L; pts.push([x, off + amp * Math.sin((x / L) * bends * TAU)]); }
  const wm = waterMat(ctx, color, num(p.opacity, 0.92, 0.2, 1), 'river:' + speed);
  const water = new THREE.Mesh(geo(ctx, `riverW:${L}:${W}:${amp}:${bends}:${off}:${n}`, () => ribbon(pts, [{ o: -W / 2 }, { o: 0 }, { o: W / 2 }], 0.06, 1 / 10)), wm);
  water.name = 'river_water'; water.receiveShadow = true;
  const bankW = num(p.bankWidth, Math.max(3, W * 0.35), 0, 200);
  const banks = new THREE.Mesh(geo(ctx, `riverB:${L}:${W}:${amp}:${bends}:${off}:${n}:${bankW}`, () => ribbon(pts, [{ o: -W / 2 - bankW }, { o: W / 2 + bankW }], 0.035)), mat(ctx, { color: bankC, roughness: 1, polyOffset: true }));
  banks.receiveShadow = true;
  g.add(banks, water);
  // reeds along the banks
  const nr = qcount(ctx, int(p.reeds, 120, 0, 2000), 0);
  if (nr > 0) {
    const r = rng(p.seed ?? 'reeds');
    const im = new THREE.InstancedMesh(G.cone(ctx, 4), mat(ctx, { color: col(p, ['reedColor'], '#6E7F3E'), roughness: 1, flat: true }), nr);
    const m = new THREE.Matrix4(), q = new THREE.Quaternion(), e = new THREE.Euler();
    for (let i = 0; i < nr; i++) {
      const k = Math.floor(r() * pts.length), side = r() < 0.5 ? -1 : 1;
      const [x, z] = pts[k], dist = W / 2 + r.range(-0.3, bankW * 0.8);
      const p1 = pts[Math.min(pts.length - 1, k + 1)], p0 = pts[Math.max(0, k - 1)];
      let tx = p1[0] - p0[0], tz = p1[1] - p0[1]; const l = Math.hypot(tx, tz) || 1; tx /= l; tz /= l;
      const h = r.range(0.8, 1.8);
      m.compose(new THREE.Vector3(x - tz * dist * side, h / 2, z + tx * dist * side), q.setFromEuler(e.set(r.range(-0.15, 0.15), 0, r.range(-0.15, 0.15))), new THREE.Vector3(0.18, h, 0.18));
      im.setMatrixAt(i, m);
    }
    im.instanceMatrix.needsUpdate = true; im.computeBoundingSphere();
    g.add(im);
  }
  // Native course runs along z, as the registry bounds declare ([14, 0.5, 400]); the voyage data rotates rivers
  // by 90° to lay them across the view behind the stage. `direction: 'x'` keeps the course along x.
  if (str(p.direction, 'z') === 'z') g.children.forEach((c) => { c.rotation.y = Math.PI / 2; });
  flowWater(g, wm, 0, -speed);
  return g;
}

// ================================================================ water (large sheet: Ganga, sea)
// Square `size` sheet, or a width×length strip when width/length or registry bounds are known. Like river, the
// strip's length runs along z (registry bounds [60, 0.5, 400]; the data rotates it 90° to lie behind the stage).
// A 300 m square placed at z≈-50 would otherwise flood the stage at the origin.
function water(params, ctx) {
  const p = P(params);
  const bw = boundsOf(ctx);
  const S = num(p.size, 300, 4, 4000), color = baseColor(p, '#3E7189');
  const speed = num(p.speed ?? (p.flow_speed !== undefined ? num(p.flow_speed, 0.4) * 0.05 : undefined), 0.02, 0, 2);
  const strip = p.size === undefined && (p.width !== undefined || p.length !== undefined || !!bw);
  const LX = strip ? num(p.width, bw ? bw[0] : S, 2, 4000) : S; // extent along x
  const WZ = strip ? num(p.length, bw ? bw[2] : S, 4, 4000) : S; // extent along z
  const g = group('water');
  const wm = waterMat(ctx, color, num(p.opacity, 0.94, 0.2, 1), 'sheet:' + speed);
  const plane = new THREE.Mesh(geo(ctx, `waterSheet:${LX}:${WZ}`, () => {
    const pg = new THREE.PlaneGeometry(LX, WZ).rotateX(-Math.PI / 2);
    const uv = pg.attributes.uv; for (let i = 0; i < uv.count; i++) uv.setXY(i, uv.getX(i) * LX / 12, uv.getY(i) * WZ / 12);
    return pg;
  }), wm);
  plane.position.y = num(p.y, 0.05, -50, 50); plane.receiveShadow = true; plane.name = 'water_sheet';
  g.add(plane);
  if (strip) flowWater(g, wm, 0, -speed); // current runs along the strip
  else flowWater(g, wm, speed * 0.4, speed);
  // sun glints
  const tex = softDotTex(), ng = qcount(ctx, int(p.glints, 260, 0, 4000), 0);
  if (tex && ng > 0) {
    const r = rng(p.seed ?? 'glint'), pos = new Float32Array(ng * 3);
    for (let i = 0; i < ng; i++) {
      const a = r() * TAU, d = Math.sqrt(r()) * 0.6;
      pos[i * 3] = Math.sin(a) * d * LX * 0.5; pos[i * 3 + 1] = plane.position.y + 0.05; pos[i * 3 + 2] = Math.cos(a) * d * WZ * 0.5;
    }
    const bg = new THREE.BufferGeometry(); bg.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    const pm = new THREE.PointsMaterial({ color: col(p, ['glint', 'glintColor'], '#FFE7B0'), size: 0.5, map: tex, transparent: true, opacity: 0.45, blending: THREE.AdditiveBlending, depthWrite: false });
    const pts = new THREE.Points(bg, pm); pts.name = 'water_glints';
    g.add(noRaycast(pts));
    // shimmer by point size, read through pts.material so an engine material swap (fades) keeps working
    addUpdate(g, (dt, t) => { pts.material.size = 0.5 * (0.75 + 0.35 * Math.sin(t * 1.7) * Math.sin(t * 0.63)); });
  }
  return g;
}

// ================================================================ forest (instanced)
function forest(params, ctx) {
  const p = P(params);
  const count = qcount(ctx, int(p.count, 220, 1, 6000), 12);
  // Footprint: explicit radius, else the registry bounds (a patch with no clearing), else a 100 m ring with a
  // clearing for use at the scene centre. Without this a 100 m disc placed at z=-100 reaches the stage.
  const bw = boundsOf(ctx), fromBounds = p.radius === undefined && !!bw;
  const R = num(p.radius, fromBounds ? Math.max(bw[0], bw[2]) / 2 : 100, 5, 1500);
  const inner = num(p.inner ?? p.innerRadius ?? p.clearing, fromBounds ? 0 : 18, 0, R - 1);
  const type = str(p.type ?? p.variant, 'mixed', ['mixed', 'tropical', 'pine', 'deciduous']);
  const leaf = col(p, ['foliage', 'canopy', 'leaves', 'color', 'leaf', 'base', 'materials.base'], type === 'pine' ? '#2F4A36' : '#3F5F34');
  const hr = Array.isArray(p.height_range) ? p.height_range.map((v) => num(v, NaN, 1, 60)).filter(Number.isFinite) : [];
  const trunkC = col(p, ['trunk', 'bark'], '#4E3A2A'), H = num(p.height, hr.length ? hr.reduce((a, b) => a + b, 0) / hr.length : 8, 1, 60);
  const arc = (num(p.arc, 360, 10, 360) * Math.PI) / 180;
  const r = rng(p.seed ?? 'vana');
  const g = group('forest');
  const pineFrac = type === 'pine' ? 1 : type === 'mixed' ? 0.3 : 0;
  const trees = [];
  for (let i = 0; i < count; i++) {
    const a = Math.PI + (arc >= TAU - 1e-3 ? r() * TAU : (r() - 0.5) * arc);
    const d = Math.sqrt(r.range((inner / R) ** 2, 1)) * R;
    trees.push({ x: Math.sin(a) * d, z: Math.cos(a) * d, s: (H / 8) * r.range(0.7, 1.3), pine: r() < pineFrac, rot: r() * TAU, j: r.range(0.85, 1.15) });
  }
  const pineGeo = geo(ctx, 'pineCanopy', () => mergeGeometries([
    only(new THREE.ConeGeometry(0.5, 0.6, 7).translate(0, 0.3, 0), ['position', 'normal']),
    only(new THREE.ConeGeometry(0.38, 0.5, 7).translate(0, 0.62, 0), ['position', 'normal']),
  ], false));
  const roundGeo = G.ico(ctx, qname(ctx) === 'low' ? 0 : 1);
  const leafM = mat(ctx, { color: '#ffffff', roughness: 0.95, flat: true });
  const trunks = new THREE.InstancedMesh(G.frustum(ctx, 0.6, 5), mat(ctx, { color: trunkC, roughness: 1 }), count);
  const nPine = trees.filter((t) => t.pine).length, nRound = count - nPine;
  const rounds = nRound ? new THREE.InstancedMesh(roundGeo, leafM, nRound) : null;
  const pines = nPine ? new THREE.InstancedMesh(pineGeo, leafM, nPine) : null;
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), e = new THREE.Euler(), c = new THREE.Color(), base = new THREE.Color(leaf);
  let ri = 0, pi = 0;
  trees.forEach((t, i) => {
    m.compose(new THREE.Vector3(t.x, t.s * 1.5, t.z), q.setFromEuler(e.set(0, t.rot, 0)), new THREE.Vector3(0.45 * t.s, 3 * t.s, 0.45 * t.s));
    trunks.setMatrixAt(i, m);
    c.copy(base).offsetHSL(r.range(-0.02, 0.03), r.range(-0.08, 0.06), r.range(-0.07, 0.06));
    if (t.pine) {
      m.compose(new THREE.Vector3(t.x, t.s * 1.1, t.z), q, new THREE.Vector3(3.2 * t.s * t.j, 8 * t.s, 3.2 * t.s * t.j));
      pines.setMatrixAt(pi, m); pines.setColorAt(pi++, c);
    } else {
      m.compose(new THREE.Vector3(t.x, t.s * 4.7, t.z), q.setFromEuler(e.set(r() * 3, t.rot, r() * 3)), new THREE.Vector3(4.4 * t.s * t.j, 3.6 * t.s, 4.4 * t.s * t.j));
      rounds.setMatrixAt(ri, m); rounds.setColorAt(ri++, c);
    }
  });
  const nb = qcount(ctx, int(p.bushes, Math.round(count * 0.5), 0, 4000), 0);
  let bushes = null;
  if (nb > 0) {
    bushes = new THREE.InstancedMesh(G.ico(ctx, 0), leafM, nb);
    for (let i = 0; i < nb; i++) {
      const a = r() * TAU, d = Math.sqrt(r.range((Math.max(0, inner - 2) / R) ** 2, 1)) * R, s = r.range(0.7, 1.7);
      m.compose(new THREE.Vector3(Math.sin(a) * d, s * 0.3, Math.cos(a) * d), q.setFromEuler(e.set(0, r() * TAU, 0)), new THREE.Vector3(s * 1.4, s, s * 1.4));
      bushes.setMatrixAt(i, m); bushes.setColorAt(i, c.copy(base).offsetHSL(0, 0, r.range(-0.12, -0.02)));
    }
  }
  for (const im of [trunks, rounds, pines, bushes]) {
    if (!im) continue;
    im.castShadow = im !== bushes; im.receiveShadow = false;
    im.instanceMatrix.needsUpdate = true; if (im.instanceColor) im.instanceColor.needsUpdate = true;
    im.computeBoundingSphere();
    g.add(im);
  }
  return g;
}

// ================================================================ grove (sacred grove ring)
function grove(params, ctx) {
  const p = P(params);
  const bw = boundsOf(ctx);
  const n = int(p.count ?? p.trees, qname(ctx) === 'low' ? 4 : 6, 1, 16), R = num(p.radius, bw ? Math.max(6, Math.min(bw[0], bw[2]) * 0.4) : 19, 6, 200);
  const gap = (num(p.gap, 70, 0, 300) * Math.PI) / 180;
  const leaf = col(p, ['foliage', 'canopy', 'color', 'leaf', 'base'], '#4C6E3A');
  const g = group('grove');
  const r = rng(p.seed ?? 'grove');
  for (let i = 0; i < n; i++) {
    const a = gap / 2 + ((i + 0.5) / n) * (TAU - gap);
    const tr = OBJECTS.tree({ height: r.range(8, 12), seed: 'grove' + i, foliage: leaf, roots: i % 2 === 0 }, ctx);
    tr.position.set(Math.sin(a) * R * r.range(0.95, 1.1), 0, Math.cos(a) * R * r.range(0.95, 1.1));
    tr.rotation.y = r() * TAU;
    g.add(tr);
  }
  // flowering shrubs + a ring of stones
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), e = new THREE.Euler(), c = new THREE.Color();
  const ns = qcount(ctx, 80, 16);
  const shrubs = new THREE.InstancedMesh(G.ico(ctx, 0), mat(ctx, { color: '#ffffff', roughness: 0.9, flat: true }), ns);
  const blossom = ['#E58FA6', '#F3EAD7', '#E8A03A', '#C9475A'];
  for (let i = 0; i < ns; i++) {
    const a = gap / 2 + r() * (TAU - gap), d = R * r.range(0.7, 1.25), s = r.range(0.5, 1.1);
    m.compose(new THREE.Vector3(Math.sin(a) * d, s * 0.4, Math.cos(a) * d), q.setFromEuler(e.set(0, r() * 3, 0)), new THREE.Vector3(s * 1.3, s, s * 1.3));
    shrubs.setMatrixAt(i, m);
    shrubs.setColorAt(i, c.set(r() < 0.6 ? leaf : r.pick(blossom)).offsetHSL(0, 0, r.range(-0.05, 0.05)));
  }
  const nst = 28;
  const stones = new THREE.InstancedMesh(geo(ctx, 'rock', () => new THREE.DodecahedronGeometry(1, 0)), mat(ctx, { color: '#ffffff', roughness: 1, flat: true }), nst);
  for (let i = 0; i < nst; i++) {
    const a = (i / nst) * TAU, d = R - 5, s = r.range(0.25, 0.45);
    m.compose(new THREE.Vector3(Math.sin(a) * d, s * 0.4, Math.cos(a) * d), q.setFromEuler(e.set(r(), r(), r())), new THREE.Vector3(s, s * 0.7, s));
    stones.setMatrixAt(i, m); stones.setColorAt(i, c.set('#8C8274').offsetHSL(0, 0, r.range(-0.06, 0.06)));
  }
  for (const im of [shrubs, stones]) { im.instanceMatrix.needsUpdate = true; im.instanceColor.needsUpdate = true; im.computeBoundingSphere(); im.castShadow = true; g.add(im); }
  // fireflies
  const tex = softDotTex(), nf = bool(p.fireflies, true) ? qcount(ctx, 90, 20) : 0;
  if (tex && nf) {
    const pos = new Float32Array(nf * 3), seedA = [];
    for (let i = 0; i < nf; i++) seedA.push([r() * TAU, R * r.range(0.4, 1.2), r.range(0.4, 4), r() * 10]);
    const bg = new THREE.BufferGeometry(); bg.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    const pm = new THREE.PointsMaterial({ color: '#E9F59A', size: 0.22, map: tex, transparent: true, opacity: 0.8, blending: THREE.AdditiveBlending, depthWrite: false });
    const pts = new THREE.Points(bg, pm); pts.frustumCulled = false; pts.name = 'grove_fireflies';
    g.add(noRaycast(pts));
    addUpdate(g, (dt, t) => {
      for (let i = 0; i < nf; i++) {
        const [a, d, h, ph] = seedA[i];
        pos[i * 3] = Math.sin(a + Math.sin(t * 0.13 + ph) * 0.2) * d + Math.sin(t * 0.7 + ph) * 0.6;
        pos[i * 3 + 1] = h + Math.sin(t * 0.9 + ph * 2) * 0.4;
        pos[i * 3 + 2] = Math.cos(a + Math.sin(t * 0.13 + ph) * 0.2) * d + Math.cos(t * 0.6 + ph) * 0.6;
      }
      bg.attributes.position.needsUpdate = true;
      pts.material.size = 0.22 * (0.8 + 0.3 * Math.sin(t * 2.3)); // glow by size: material opacity belongs to engine fades
    });
  }
  return g;
}

// ================================================================ road
function road(params, ctx) {
  const p = P(params);
  const L = num(p.length, 220, 10, 3000), W = num(p.width, 6, 1, 60), amp = num(p.meander ?? p.amplitude, 6, 0, 500);
  const color = col(p, ['surface'], baseColor(p, '#A68B62'));
  const g = group('road');
  const n = seg(ctx, 40, 70, 100), pts = [];
  for (let i = 0; i <= n; i++) { const z = L / 2 - (i / n) * L; pts.push([amp * Math.sin((z / L) * Math.PI * 1.3 + 0.4) - amp * Math.sin(0.4), z]); }
  const c0 = new THREE.Color(color), dark = c0.clone().offsetHSL(0, -0.05, -0.08), rut = c0.clone().offsetHSL(0, -0.05, -0.14), light = c0.clone().offsetHSL(0, 0, 0.05);
  const across = [{ o: -W / 2, c: dark }, { o: -W * 0.3, c: c0 }, { o: -W * 0.2, c: rut }, { o: 0, c: light }, { o: W * 0.2, c: rut }, { o: W * 0.3, c: c0 }, { o: W / 2, c: dark }];
  const roadM = new THREE.Mesh(geo(ctx, `road:${L}:${W}:${amp}:${color}:${n}`, () => ribbon(pts, across, 0.03, 0.15, true)), mat(ctx, { color: '#ffffff', vc: true, roughness: 1, polyOffset: true }));
  roadM.receiveShadow = true; roadM.name = 'road_surface';
  g.add(roadM);
  if (bool(p.stones ?? p.edge_stones, true)) {
    const r = rng(p.seed ?? 'marga');
    const ns = qcount(ctx, Math.round(L / 2.2) * 2, 4);
    const im = new THREE.InstancedMesh(geo(ctx, 'rock', () => new THREE.DodecahedronGeometry(1, 0)), mat(ctx, { color: col(p, ['stoneColor'], '#8A7F6E'), roughness: 1, flat: true }), ns);
    const m = new THREE.Matrix4(), q = new THREE.Quaternion(), e = new THREE.Euler();
    for (let i = 0; i < ns; i++) {
      const k = Math.min(pts.length - 2, Math.floor(r() * (pts.length - 1))), side = i % 2 ? 1 : -1;
      const [x0, z0] = pts[k], [x1, z1] = pts[k + 1];
      let tx = x1 - x0, tz = z1 - z0; const l = Math.hypot(tx, tz) || 1; tx /= l; tz /= l;
      const u = r(), d = W / 2 + r.range(0.1, 0.6), s = r.range(0.15, 0.4);
      m.compose(new THREE.Vector3(x0 + tx * u * l - tz * d * side, s * 0.3, z0 + tz * u * l + tx * d * side), q.setFromEuler(e.set(r(), r(), r())), new THREE.Vector3(s, s * 0.6, s));
      im.setMatrixAt(i, m);
    }
    im.instanceMatrix.needsUpdate = true; im.computeBoundingSphere();
    g.add(im);
  }
  return g;
}

// ================================================================ skyline (distant city silhouette)
function skyline(params, ctx) {
  const p = P(params);
  const R = num(p.radius, 160, 20, 1500), arc = (num(p.arc, 140, 10, 360) * Math.PI) / 180;
  const n = qcount(ctx, int(p.count ?? p.buildings, 80, 4, 1000), 16), color = baseColor(p, '#857C8C'), accent = accentColor(p, '#B59A62');
  const r = rng(p.seed ?? 'nagara');
  const g = group('skyline');
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), c = new THREE.Color(), base = new THREE.Color(color);
  const blocks = new THREE.InstancedMesh(G.box(ctx), mat(ctx, { color: '#ffffff', roughness: 1 }), n);
  const domes = new THREE.InstancedMesh(G.hemi(ctx, 10, 5), mat(ctx, { color: accent, roughness: 0.7, metalness: 0.3 }), n);
  const nsp = Math.max(2, Math.round(n * 0.15));
  const spires = new THREE.InstancedMesh(lathe(ctx, 'skySpire', [[1, 0], [0.9, 0.3], [0.6, 0.7], [0.3, 0.92], [0, 1]], 6), mat(ctx, { color: '#ffffff', roughness: 1, flat: true }), nsp);
  let dn = 0;
  const lights = [];
  for (let i = 0; i < n; i++) {
    const a = Math.PI + (r() - 0.5) * arc, d = R * r.range(0.9, 1.15);
    const w = r.range(5, 12), h = r.range(6, 22) * (1 - Math.abs(r() - 0.5)), dd = r.range(5, 12);
    const x = Math.sin(a) * d, z = Math.cos(a) * d;
    m.compose(new THREE.Vector3(x, h / 2, z), q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), a), new THREE.Vector3(w, h, dd));
    blocks.setMatrixAt(i, m); blocks.setColorAt(i, c.copy(base).offsetHSL(0, r.range(-0.03, 0.03), r.range(-0.06, 0.05)));
    if (r() < 0.4) { m.compose(new THREE.Vector3(x, h, z), q.identity(), new THREE.Vector3(w * 0.7, w * 0.6, w * 0.7)); domes.setMatrixAt(dn++, m); }
    if (r() < 0.5) lights.push(x * 0.995, h * r.range(0.3, 0.8), z * 0.995);
  }
  domes.count = dn;
  for (let i = 0; i < nsp; i++) {
    const a = Math.PI + (r() - 0.5) * arc * 0.9, d = R * r.range(0.95, 1.1), H = r.range(22, 40);
    m.compose(new THREE.Vector3(Math.sin(a) * d, 0, Math.cos(a) * d), q.identity(), new THREE.Vector3(5, H, 5));
    spires.setMatrixAt(i, m); spires.setColorAt(i, c.copy(base).offsetHSL(0, 0, 0.03));
  }
  for (const im of [blocks, domes, spires]) { im.instanceMatrix.needsUpdate = true; if (im.instanceColor) im.instanceColor.needsUpdate = true; im.computeBoundingSphere(); g.add(im); }
  if (bool(p.lights, false) && lights.length) {
    const tex = softDotTex();
    const bg = new THREE.BufferGeometry(); bg.setAttribute('position', new THREE.Float32BufferAttribute(lights, 3));
    const pts = new THREE.Points(bg, new THREE.PointsMaterial({ color: '#FFC56E', size: 1.6, map: tex || null, transparent: true, opacity: 0.9, blending: THREE.AdditiveBlending, depthWrite: false }));
    pts.name = 'skyline_lights'; g.add(pts);
  }
  return g;
}

// ================================================================ garden (charbagh)
function garden(params, ctx) {
  const p = P(params);
  const bw = boundsOf(ctx);
  const S = num(p.size ?? p.width, bw ? Math.max(bw[0], bw[2]) : 36, 10, 300);
  const lawn = col(p, ['lawn', 'color', 'base'], '#5E7F45'), path = col(p, ['path', 'path_color', 'paving'], '#D8C7A4');
  const g = group('garden'), b = new Batch();
  const Lm = mat(ctx, { color: lawn, roughness: 1 }), Pm = stone(ctx, path), Sm = stone(ctx, PAL.sandstone), wm = waterMat(ctx, col(p, ['water'], '#4D8396'), 0.95, 'garden');
  b.box(ctx, Pm, S + 2, 0.12, S + 2, 0, 0.06, 0);
  const q = S / 2 - 2.2;
  for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
    b.box(ctx, Sm, q + 0.3, 0.32, q + 0.3, sx * (q / 2 + 1.6), 0.16, sz * (q / 2 + 1.6));
    b.box(ctx, Lm, q, 0.34, q, sx * (q / 2 + 1.6), 0.17, sz * (q / 2 + 1.6));
  }
  b.box(ctx, wm, 1.2, 0.02, S, 0, 0.13, 0); b.box(ctx, wm, S, 0.02, 1.2, 0, 0.13, 0);
  b.box(ctx, Sm, 5.6, 0.3, 5.6, 0, 0.15, 0); b.box(ctx, wm, 4.8, 0.02, 4.8, 0, 0.31, 0);
  b.add(lathe(ctx, 'fountain', [[0.9, 0], [0.9, 0.3], [0.3, 0.4], [0.18, 1.0], [0.7, 1.1], [0.72, 1.2], [0.15, 1.25], [0.1, 1.6], [0.2, 1.7], [0, 1.85]], 12), Sm, T(0, 0.3, 0));
  b.build(g, ctx, `garden:${S}:${lawn}:${path}:${p.water ?? ''}`);
  flowWater(g, wm, 0.004, 0.008);
  // flower borders + cypress rows
  const r = rng(p.seed ?? 'bagh'), m = new THREE.Matrix4(), qq = new THREE.Quaternion(), c = new THREE.Color();
  const nf = qcount(ctx, 160, 30), flowers = new THREE.InstancedMesh(G.ico(ctx, 0), mat(ctx, { color: '#ffffff', roughness: 0.8, flat: true }), nf);
  const palette = ['#E58FA6', '#F3EAD7', '#E8A03A', '#C9475A', '#F2D16B'];
  for (let i = 0; i < nf; i++) {
    const sx = r() < 0.5 ? -1 : 1, sz = r() < 0.5 ? -1 : 1, edge = r() < 0.5;
    const t = r.range(-q / 2, q / 2), x = sx * (q / 2 + 1.6) + (edge ? t : (r() < 0.5 ? -1 : 1) * (q / 2 - 0.3)), z = sz * (q / 2 + 1.6) + (edge ? (r() < 0.5 ? -1 : 1) * (q / 2 - 0.3) : t);
    const s = r.range(0.18, 0.32);
    m.compose(new THREE.Vector3(x, 0.4, z), qq, new THREE.Vector3(s, s, s));
    flowers.setMatrixAt(i, m); flowers.setColorAt(i, c.set(r.pick(palette)));
  }
  const nc = qcount(ctx, 24, 8), cyp = new THREE.InstancedMesh(G.cone(ctx, 7), mat(ctx, { color: '#2E4A30', roughness: 1, flat: true }), nc);
  for (let i = 0; i < nc; i++) {
    const side = i % 4, t = (Math.floor(i / 4) + 0.5) / Math.ceil(nc / 4), o = S / 2 + 0.4, along = -S / 2 + t * S;
    const [x, z] = side === 0 ? [along, -o] : side === 1 ? [along, o] : side === 2 ? [-o, along] : [o, along];
    const h = r.range(4.5, 6);
    m.compose(new THREE.Vector3(x, h / 2, z), qq, new THREE.Vector3(1.4, h, 1.4));
    cyp.setMatrixAt(i, m);
  }
  for (const im of [flowers, cyp]) { im.instanceMatrix.needsUpdate = true; if (im.instanceColor) im.instanceColor.needsUpdate = true; im.computeBoundingSphere(); im.castShadow = true; g.add(im); }
  if (qname(ctx) !== 'low' && bool(p.lamps, true)) for (const [x, z] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
    const l = OBJECTS.lamp({ stand: true, size: 1.2 }, ctx); l.position.set(x * 3.6, 0.12, z * 3.6); g.add(l);
  }
  return g;
}

// ================================================================ terrain (ground with gentle hills; flat stage)
function terrain(params, ctx) {
  const p = P(params);
  const variant = str(p.variant, 'plain');
  const S = num(p.size, 400, 20, 4000), color = baseColor(p, variant === 'battlefield' ? '#8A7552' : '#6F6A45');
  const color2 = col(p, ['color2', 'secondary', 'accent'], variant === 'battlefield' ? '#6B5A40' : '#857A52');
  const hills = num(p.hills ?? p.amplitude, variant === 'battlefield' ? 2.5 : 4, 0, 200), flat = num(p.flat ?? p.flatRadius, 18, 0, 1000);
  const rim = num(p.rim, 1, 0, 10), seed = (p.seed ?? 'bhumi');
  const sd = typeof seed === 'number' ? seed : String(seed).length * 13;
  const heightAt = (x, z) => {
    const d = Math.hypot(x, z);
    return hills * (fbm(x * 0.012 + sd, z * 0.012, 21, 4) - 0.45) * 2 * smooth(flat, flat + 40, d) + rim * smooth(S * 0.22, S * 0.5, d) * hills * 3;
  };
  const N = seg(ctx, 64, 96, 128);
  const geometry = geo(ctx, `terrain:${JSON.stringify([S, color, color2, hills, flat, rim, seed, N])}`, () => {
    const pg = new THREE.PlaneGeometry(S, S, N, N).rotateX(-Math.PI / 2);
    const pos = pg.attributes.position, colors = new Float32Array(pos.count * 3);
    const c1 = new THREE.Color(color), c2 = new THREE.Color(color2), tmp = new THREE.Color();
    for (let i = 0; i < pos.count; i++) {
      const x = pos.getX(i), z = pos.getZ(i);
      pos.setY(i, heightAt(x, z));
      const k = smooth(0.3, 0.7, fbm(x * 0.03, z * 0.03, 5, 3));
      tmp.copy(c1).lerp(c2, k).offsetHSL(0, 0, (fbm(x * 0.2, z * 0.2, 8, 2) - 0.5) * 0.06);
      colors[i * 3] = tmp.r; colors[i * 3 + 1] = tmp.g; colors[i * 3 + 2] = tmp.b;
    }
    pg.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    pg.computeVertexNormals();
    return pg;
  });
  const g = group('terrain');
  const ground = new THREE.Mesh(geometry, mat(ctx, { color: '#ffffff', vc: true, roughness: 1 }));
  ground.receiveShadow = true; ground.name = 'terrain_ground';
  g.add(ground);
  const nr = qcount(ctx, int(p.rocks, variant === 'battlefield' ? 140 : 40, 0, 5000), 0);
  if (nr > 0) {
    const r = rng(seed + ':rocks'), m = new THREE.Matrix4(), q = new THREE.Quaternion(), e = new THREE.Euler(), c = new THREE.Color();
    const im = new THREE.InstancedMesh(geo(ctx, 'rock', () => new THREE.DodecahedronGeometry(1, 0)), mat(ctx, { color: '#ffffff', roughness: 1, flat: true }), nr);
    for (let i = 0; i < nr; i++) {
      const a = r() * TAU, d = Math.sqrt(r.range(((flat + 2) / (S * 0.45)) ** 2, 1)) * S * 0.45, s = r.range(0.3, 1.6);
      const x = Math.sin(a) * d, z = Math.cos(a) * d;
      m.compose(new THREE.Vector3(x, heightAt(x, z) + s * 0.15, z), q.setFromEuler(e.set(r(), r(), r())), new THREE.Vector3(s, s * r.range(0.4, 0.8), s));
      im.setMatrixAt(i, m); im.setColorAt(i, c.set(col(p, ['rockColor'], '#6D655C')).offsetHSL(0, 0, r.range(-0.06, 0.06)));
    }
    im.instanceMatrix.needsUpdate = true; im.instanceColor.needsUpdate = true; im.computeBoundingSphere(); im.castShadow = true; im.receiveShadow = true;
    g.add(im);
  }
  g.userData.heightAt = heightAt;
  return g;
}

// ================================================================ army (instanced formation)
function soldierGeometry(ctx) {
  const body = geo(ctx, 'soldierBody:v2', () => only(new THREE.LatheGeometry([[0.25, 0], [0.21, 0.7], [0.25, 1.25], [0.12, 1.45], [0, 1.48]].map(([x, y]) => new THREE.Vector2(x, y)), 5), ['position', 'normal']));
  const gear = geo(ctx, 'soldierGear:v2', () => {
    const parts = [
      paint(only(new THREE.IcosahedronGeometry(0.12, 0).translate(0, 1.58, 0), ['position', 'normal']), PAL.skin),
      paint(only(new THREE.ConeGeometry(0.13, 0.18, 6).translate(0, 1.74, 0), ['position', 'normal']), '#8B8E96'),
      paint(only(new THREE.CylinderGeometry(0.015, 0.015, 2.5, 3).translate(0.27, 1.35, 0.02), ['position', 'normal']), '#6B4A2F'),
      paint(only(new THREE.ConeGeometry(0.035, 0.16, 4).translate(0.27, 2.68, 0.02), ['position', 'normal']), '#C9CCD4'),
      paint(only(new THREE.CylinderGeometry(0.24, 0.24, 0.04, 6).rotateX(Math.PI / 2).translate(-0.22, 1.0, 0.18), ['position', 'normal']), '#7A4A2A'),
    ];
    return mergeGeometries(parts, false);
  });
  return { body, gear };
}
function army(params, ctx) {
  const p = P(params);
  const formation = str(p.formation, 'block', ['block', 'crescent', 'wedge', 'circle']);
  const blocks = int(p.blocks, 3, 1, 12), sq = Math.sqrt(qf(ctx));
  const rows = Math.max(2, Math.round(int(p.rows, 8, 1, 60) * sq)), cols = Math.max(3, Math.round(int(p.cols, 18, 1, 120) * sq));
  const sp = num(p.spacing, 1.25, 0.6, 6) / Math.max(0.75, sq), gap = num(p.gap, 6, 0, 60);
  const color = baseColor(p, PAL.vermilion), accent = accentColor(p, PAL.gold);
  const r = rng(p.seed ?? 'sena');
  const spots = [];
  const blockW = (cols - 1) * sp, totalW = blocks * blockW + (blocks - 1) * gap;
  for (let b = 0; b < blocks; b++) {
    const bx = -totalW / 2 + b * (blockW + gap);
    for (let row = 0; row < rows; row++) {
      const n = formation === 'wedge' ? Math.max(1, Math.round(cols * (0.3 + 0.7 * (row / Math.max(1, rows - 1))))) : cols;
      for (let ci = 0; ci < n; ci++) {
        let x = bx + (formation === 'wedge' ? (blockW - (n - 1) * sp) / 2 : 0) + ci * sp, z = -row * sp;
        if (formation === 'crescent') z -= (x * x) / Math.max(30, totalW * 1.2);
        spots.push([x + r.range(-0.15, 0.15), z + r.range(-0.15, 0.15)]);
      }
    }
  }
  if (formation === 'circle') { // concentric rings (mandala-like array)
    spots.length = 0;
    const total = blocks * rows * cols;
    let ring = 0, placed = 0;
    while (placed < total) { const rr = 6 + ring * sp * 1.1, k = Math.max(6, Math.floor((TAU * rr) / sp)); for (let i = 0; i < k && placed < total; i++, placed++) { const a = (i / k) * TAU; spots.push([Math.sin(a) * rr, Math.cos(a) * rr]); } ring++; }
  }
  const n = spots.length;
  const { body, gear } = soldierGeometry(ctx);
  const g = group('army');
  const bodies = new THREE.InstancedMesh(body, clothM(ctx, '#ffffff', { double: false }), n);
  const gears = new THREE.InstancedMesh(gear, mat(ctx, { color: '#ffffff', vc: true, roughness: 0.6, metalness: 0.2 }), n);
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), c = new THREE.Color(), base = new THREE.Color(color);
  spots.forEach(([x, z], i) => {
    const s = r.range(0.94, 1.06);
    q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), formation === 'circle' ? Math.atan2(x, z) : r.range(-0.08, 0.08));
    m.compose(new THREE.Vector3(x, 0, z), q, new THREE.Vector3(s, s, s));
    bodies.setMatrixAt(i, m); gears.setMatrixAt(i, m);
    bodies.setColorAt(i, c.copy(base).offsetHSL(r.range(-0.015, 0.015), r.range(-0.08, 0.04), r.range(-0.08, 0.06)));
  });
  for (const im of [bodies, gears]) { im.instanceMatrix.needsUpdate = true; if (im.instanceColor) im.instanceColor.needsUpdate = true; im.computeBoundingSphere(); im.castShadow = true; g.add(im); }
  // standards above each block
  const nb = Math.min(int(p.banners, blocks * 2, 0, 24), qname(ctx) === 'low' ? blocks : 24);
  for (let i = 0; i < nb; i++) {
    const bx = formation === 'circle' ? Math.sin((i / nb) * TAU) * 8 : -totalW / 2 + ((i + 0.5) / nb) * totalW;
    const bz = formation === 'circle' ? Math.cos((i / nb) * TAU) * 8 : -rows * sp * 0.5;
    const bn = OBJECTS.banner({ height: 5.5, color: col(p, ['bannerColor', 'banner_color'], accent), accent: color, emblem: p.emblem || 'sun', width: 1.3, seed: 'army' + i }, ctx);
    bn.position.set(bx, 0, bz); g.add(bn);
  }
  const nc = int(p.chariots, 0, 0, 8);
  if (nc > 0) {
    for (let i = 0; i < nc; i++) { const o = FIGURES.chariot({ horses: 2, banner: false, color: p.chariotColor }, ctx); o.position.set(-totalW / 2 + ((i + 0.5) / nc) * totalW, 0, sp * 3 + 2); g.add(o); }
  }
  g.userData.count = n;
  return g;
}

// ================================================================ clouds (stylised low-poly cumulus)
function clouds(params, ctx) {
  const p = P(params);
  // With registry bounds the bank stays inside its footprint (the engine already lifts it to cloud altitude)
  const bw = boundsOf(ctx);
  const n = qcount(ctx, int(p.count, 14, 1, 200), 4), R = num(p.radius, bw ? Math.max(bw[0], bw[2]) / 2 : 180, 10, 1500), H = num(p.height, bw ? bw[1] / 2 : 70, 0, 800);
  const sc = num(p.scale, 1, 0.1, 20), color = baseColor(p, '#F4F1EA'), opacity = num(p.opacity, 1, 0.05, 1);
  const r = rng(p.seed ?? 'megha');
  const geometry = geo(ctx, `clouds:${JSON.stringify([n, R, H, sc, p.seed ?? '', qname(ctx)])}`, () => {
    const parts = [], puff = qname(ctx) === 'low' ? 0 : 1;
    for (let i = 0; i < n; i++) {
      const a = r() * TAU, d = R * r.range(0.45, 1.1), cx = Math.sin(a) * d, cz = Math.cos(a) * d, cy = H * r.range(0.8, 1.2);
      const k = sc * r.range(6, 13), puffs = 4 + Math.floor(r() * 4);
      for (let j = 0; j < puffs; j++) {
        const s = k * r.range(0.5, 1);
        parts.push(only(new THREE.IcosahedronGeometry(1, puff).scale(s, s * 0.62, s).translate(cx + (j - puffs / 2) * k * 0.55, cy + r.range(0, k * 0.25), cz + r.range(-k * 0.3, k * 0.3)), ['position', 'normal']));
      }
    }
    return mergeGeometries(parts, false);
  });
  const g = group('clouds');
  const drift = new THREE.Group(); drift.name = 'clouds_drift'; g.add(drift);
  const m = new THREE.Mesh(geometry, mat(ctx, { color, roughness: 1, flat: true, emissive: color, ei: 0.28, opacity }));
  m.castShadow = false;
  drift.add(m);
  const speed = num(p.speed, 1, 0, 50);
  drift.userData.drift = { speed };
  addUpdate(g, (dt) => { drift.rotation.y += dt * 0.004 * speed; });
  return g;
}

// ================================================================ mist (drifting translucent layers)
function mist(params, ctx) {
  const p = P(params);
  const bw = boundsOf(ctx);
  const layers = int(p.layers, qname(ctx) === 'low' ? 2 : 3, 1, 8), S = num(p.size, bw ? Math.max(bw[0], bw[2]) : 160, 4, 3000);
  const color = baseColor(p, '#D8DCE6'), opacity = num(p.opacity ?? p.density, 0.32, 0.02, 1), y0 = num(p.height, 0.6, -10, 200);
  const g = group('mist');
  const noise = cloudNoiseTex(), fadeT = radialFadeTex();
  const mats = [];
  for (let i = 0; i < layers; i++) {
    const map = noise ? noise.clone() : null;
    if (map) { map.needsUpdate = true; map.repeat.set(2 + i, 2 + i); }
    const mm = new THREE.MeshBasicMaterial({ color, map, alphaMap: fadeT, transparent: true, opacity: opacity * (1 - i * 0.18), depthWrite: false, side: THREE.DoubleSide });
    const plane = new THREE.Mesh(geo(ctx, 'mistPlane', () => new THREE.PlaneGeometry(1, 1).rotateX(-Math.PI / 2)), mm);
    plane.scale.set(S * (1 - i * 0.12), 1, S * (1 - i * 0.12)); plane.position.y = y0 + i * 1.4; plane.renderOrder = 2;
    plane.name = 'mist_layer';
    g.add(plane); mats.push([mm, i]);
  }
  g.userData.flow = { speed: [0.004, 0.002] };
  noRaycast(g); // mist planes span the stage: they must never hide hotspots
  // (cloned materials made by engine fades share these map textures, so the drift survives a material swap)
  addUpdate(g, (dt, t) => { for (const [mm, i] of mats) if (mm.map) mm.map.offset.set(t * 0.004 * (i % 2 ? -1 : 1), t * 0.0025 * (1 + i * 0.3)); });
  return g;
}

// ================================================================ particles: dust, snow (points) and rain (streaks)
function particleCount(ctx, p, base) {
  const density = num(p.density, 0.5, 0, 1);
  return qcount(ctx, p.count !== undefined ? int(p.count, base, 0, 20000) : Math.round(base * 2 * density), 0);
}
function pointsField(name, ctx, p, { base, area, color, size, opacity, blending = THREE.NormalBlending, step, minH = 0 }) {
  const [w, h0, d] = areaOf(p, area), n = particleCount(ctx, p, base);
  const h = Math.max(h0, minH);
  const g = group(name);
  if (n === 0) return g;
  const r = rng(p.seed ?? name), pos = new Float32Array(n * 3), ph = new Float32Array(n);
  for (let i = 0; i < n; i++) { pos[i * 3] = r.range(-w / 2, w / 2); pos[i * 3 + 1] = r() * h; pos[i * 3 + 2] = r.range(-d / 2, d / 2); ph[i] = r() * 100; }
  const bg = new THREE.BufferGeometry(); bg.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  const tex = softDotTex();
  const pm = new THREE.PointsMaterial({ color: baseColor(p, color), size: num(p.size, size, 0.01, 10), map: tex || null, transparent: true, opacity: num(p.opacity, opacity, 0, 1), depthWrite: false, blending });
  const pts = new THREE.Points(bg, pm); pts.frustumCulled = false; pts.name = name + '_points';
  g.add(noRaycast(pts));
  const wrap = (v, lo, hi) => (v < lo ? v + (hi - lo) : v > hi ? v - (hi - lo) : v);
  addUpdate(g, (dt, t) => {
    for (let i = 0; i < n; i++) {
      const k = i * 3;
      step(pos, k, ph[i], dt, t);
      pos[k] = wrap(pos[k], -w / 2, w / 2); pos[k + 1] = wrap(pos[k + 1], 0, h); pos[k + 2] = wrap(pos[k + 2], -d / 2, d / 2);
    }
    bg.attributes.position.needsUpdate = true;
  });
  return g;
}
function dust(params, ctx) {
  const p = P(params), wind = num(p.wind, 0.4, -10, 10);
  return pointsField('dust', ctx, p, {
    base: 500, area: [60, 8, 60], color: '#D9C29A', size: 0.14, opacity: 0.5,
    step: (pos, k, ph, dt, t) => { pos[k] += (wind + Math.sin(t * 0.3 + ph) * 0.3) * dt; pos[k + 1] += Math.sin(t * 0.5 + ph) * 0.08 * dt + 0.02 * dt; pos[k + 2] += Math.cos(t * 0.27 + ph) * 0.25 * dt; },
  });
}
function snow(params, ctx) {
  const p0 = P(params), fall = num(p0.speed, 0.9, 0, 20);
  const p = p0.density === undefined && p0.flake_density !== undefined ? { ...p0, density: p0.flake_density } : p0; // never mutate caller params
  // falling snow needs a fall height even when the registry area is a flat ground-cover footprint ([w, 0.5, d])
  return pointsField('snow', ctx, p, {
    base: 1400, area: [70, 30, 70], color: '#FFFFFF', size: 0.16, opacity: 0.85, minH: bool(p.falling, true) ? 14 : 0,
    step: (pos, k, ph, dt, t) => { pos[k + 1] -= fall * (0.7 + (ph % 1) * 0.6) * dt; pos[k] += Math.sin(t * 0.8 + ph) * 0.3 * dt; pos[k + 2] += Math.cos(t * 0.6 + ph) * 0.2 * dt; },
  });
}
function rain(params, ctx) {
  const p = P(params);
  const [w, h, d] = areaOf(p, [80, 40, 80]), n = particleCount(ctx, p, 1800);
  const speed = num(p.speed, 22, 1, 100), slant = num(p.wind, 0.15, -2, 2), len = num(p.length, 0.7, 0.1, 5);
  const g = group('rain');
  if (n === 0) return g;
  const r = rng(p.seed ?? 'varsha'), pos = new Float32Array(n * 6);
  for (let i = 0; i < n; i++) { const x = r.range(-w / 2, w / 2), y = r() * h, z = r.range(-d / 2, d / 2); pos.set([x, y, z, x - slant * len, y - len, z], i * 6); }
  const bg = new THREE.BufferGeometry(); bg.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  const lm = new THREE.LineBasicMaterial({ color: baseColor(p, '#AFC0D6'), transparent: true, opacity: num(p.opacity, 0.45, 0, 1), depthWrite: false });
  const lines = new THREE.LineSegments(bg, lm); lines.frustumCulled = false; lines.name = 'rain_streaks';
  g.add(noRaycast(lines));
  addUpdate(g, (dt) => {
    const dy = speed * dt, dx = slant * speed * dt;
    for (let i = 0; i < n; i++) {
      const k = i * 6;
      pos[k + 1] -= dy; pos[k + 4] -= dy; pos[k] -= dx; pos[k + 3] -= dx;
      if (pos[k + 4] < 0) { const x = r.range(-w / 2, w / 2), z = r.range(-d / 2, d / 2); pos.set([x, h, z, x - slant * len, h - len, z], k); }
    }
    bg.attributes.position.needsUpdate = true;
  });
  return g;
}

// ================================================================ fire (campfire / sacrificial blaze)
function fire(params, ctx) {
  const p = P(params);
  const k = num(p.size, 1, 0.1, 20);
  const g = group('fire');
  const b = new Batch();
  b.add(geo(ctx, 'scorch', () => new THREE.CircleGeometry(1, 20).rotateX(-Math.PI / 2)), mat(ctx, { color: '#1c120c', roughness: 1, emissive: '#5a1f08', ei: 0.5, polyOffset: true }), T(0, 0.02, 0, 0, 0, 0, 1.1 * k));
  for (let i = 0; i < 5; i++) b.add(G.cyl(ctx, 6), woodM(ctx, PAL.woodDark), T(0, 0.12 * k, 0, Math.PI / 2, 0, (i / 5) * Math.PI + 0.3, 0.16 * k, 1.5 * k, 0.16 * k));
  b.build(g, ctx, `firebed:${k}`);
  const f = flame(ctx, { size: 0.8 * k, color: col(p, ['color', 'base'], undefined), light: bool(p.light, qname(ctx) !== 'low'), intensity: num(p.intensity, 2.5, 0, 50), embers: p.embers, seed: p.seed ?? 'agni' });
  f.position.y = 0.1 * k;
  g.add(f);
  g.userData.flicker = { intensity: 1 };
  return g;
}

// ================================================================ stars (twinkling dome with an optional Milky Way band)
function stars(params, ctx) {
  const p = P(params);
  const n = qcount(ctx, int(p.count, 3000, 50, 40000), 400), R = num(p.radius, 700, 50, 5000);
  const milky = bool(p.milkyWay ?? p.milky_way, true);
  const r = rng(p.seed ?? 'nakshatra');
  const total = n + (milky ? Math.round(n * 0.7) : 0);
  const pos = new Float32Array(total * 3), size = new Float32Array(total), phase = new Float32Array(total), colr = new Float32Array(total * 3);
  const tints = [new THREE.Color('#FFFFFF'), new THREE.Color('#CFDCFF'), new THREE.Color('#FFE6C2'), new THREE.Color('#E3C77E')];
  const tilt = new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(1.05, 0.3, 0.4)), v = new THREE.Vector3();
  for (let i = 0; i < total; i++) {
    if (i < n) { const u = r() * 2 - 1, a = r() * TAU, s = Math.sqrt(1 - u * u); v.set(s * Math.cos(a), Math.abs(u) * 0.98 + 0.02, s * Math.sin(a)); if (r() < 0.12) v.y = -v.y * 0.2; }
    else { const a = r() * TAU, spread = (r() + r() + r() - 1.5) * 0.12; v.set(Math.cos(a), spread, Math.sin(a)).applyMatrix4(tilt); }
    v.normalize().multiplyScalar(R);
    pos.set([v.x, v.y, v.z], i * 3);
    const bright = i < n ? Math.pow(r(), 6) : Math.pow(r(), 10) * 0.5;
    size[i] = 1.0 + bright * 3.4; phase[i] = r();
    const c = tints[Math.floor(r() * tints.length)];
    const dim = i < n ? 0.55 + bright * 0.45 : 0.35;
    colr.set([c.r * dim, c.g * dim, c.b * dim], i * 3);
  }
  const bg = new THREE.BufferGeometry();
  bg.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  bg.setAttribute('aSize', new THREE.BufferAttribute(size, 1));
  bg.setAttribute('aPhase', new THREE.BufferAttribute(phase, 1));
  bg.setAttribute('aColor', new THREE.BufferAttribute(colr, 3));
  const sm = new THREE.ShaderMaterial({
    uniforms: { uTime: { value: 0 }, uPixelRatio: { value: 1 }, uOpacity: { value: num(p.opacity, 1, 0, 1) } },
    vertexShader: `attribute float aSize; attribute float aPhase; attribute vec3 aColor; uniform float uTime; uniform float uPixelRatio;
      varying vec3 vColor; varying float vTw;
      void main(){ vColor = aColor; vTw = 0.72 + 0.28 * sin(uTime * (0.6 + aPhase * 1.4) + aPhase * 40.0);
        vec4 mv = modelViewMatrix * vec4(position, 1.0); gl_Position = projectionMatrix * mv; gl_PointSize = aSize * uPixelRatio; }`,
    fragmentShader: `varying vec3 vColor; varying float vTw; uniform float uOpacity;
      void main(){ float d = length(gl_PointCoord - 0.5); float a = smoothstep(0.5, 0.0, d); a *= a; gl_FragColor = vec4(vColor * vTw, a * vTw * uOpacity);
        #include <colorspace_fragment>
      }`,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  });
  const pts = new THREE.Points(bg, sm);
  pts.name = 'stars_points'; pts.frustumCulled = false; pts.renderOrder = -999;
  noRaycast(pts); // thousands of points around the camera: never part of hotspot occlusion tests
  const spin = { angle: 0 };
  if (bool(p.followCamera ?? p.follow_camera, true)) followCamera(pts, spin);
  const prev = pts.onBeforeRender;
  pts.onBeforeRender = function (renderer, ...rest) { sm.uniforms.uPixelRatio.value = renderer.getPixelRatio(); prev.call(this, renderer, ...rest); };
  const g = group('stars'); g.add(pts);
  const rot = num(p.rotation, 0.004, -1, 1);
  addUpdate(g, (dt, t) => { sm.uniforms.uTime.value = t; spin.angle += dt * rot; pts.rotation.y = spin.angle; });
  return g;
}

// ================================================================ sky (gradient dome with sun/moon glow)
const SKY_PRESETS = {
  dawn: { top: '#2B3A67', horizon: '#E9A97A', bottom: '#3A2E3A', sun: '#FFC890', elev: 4, glow: 1.0 },
  day: { top: '#2F62B0', horizon: '#B7D0E4', bottom: '#7E8A80', sun: '#FFF4DA', elev: 55, glow: 0.5 },
  golden_hour: { top: '#41558C', horizon: '#F0B46E', bottom: '#5A4636', sun: '#FFD08A', elev: 10, glow: 1.0 },
  dusk: { top: '#1E2148', horizon: '#D9774A', bottom: '#2A1E2A', sun: '#FF9A5A', elev: 2, glow: 1.1 },
  night: { top: '#05060F', horizon: '#1A1D3A', bottom: '#07080F', sun: '#AFC2E8', elev: 32, glow: 0.35, stars: true },
  celestial: { top: '#0B0E22', horizon: '#3A2D5A', bottom: '#0B0E22', sun: '#E3C77E', elev: 18, glow: 0.6, stars: true },
  storm: { top: '#3B4150', horizon: '#7D8290', bottom: '#2E323B', sun: '#C9CED8', elev: 30, glow: 0.15 },
  overcast: { top: '#7F8A99', horizon: '#C3C8CF', bottom: '#6F7378', sun: '#F0F0EE', elev: 40, glow: 0.12 },
};
function sky(params, ctx) {
  const p = P(params);
  // preset from params, else from the asset id (env.sky_dawn / _day / _dusk / _night carry no preset param)
  const idHint = (idOf(p, ctx).match(/(dawn|day|dusk|night|celestial|golden_hour|storm|overcast)/) || [])[1];
  let name = str(p.time ?? p.preset ?? p.variant, idHint || 'day');
  if (name === 'golden' || name === 'sunset') name = 'golden_hour';
  if (name === 'torchlit') name = 'night';
  const pr = SKY_PRESETS[name] || SKY_PRESETS.day;
  const top = col(p, ['top', 'zenith'], pr.top), horizon = col(p, ['horizon', 'color', 'base'], pr.horizon), bottom = col(p, ['bottom', 'ground'], pr.bottom);
  const sunC = col(p, ['sun', 'sunColor', 'sun_color'], pr.sun);
  const elev = (num(p.sunElevation ?? p.sun_elevation, pr.elev, -30, 90) * Math.PI) / 180, az = (num(p.sunAzimuth ?? p.sun_azimuth, 180, -360, 360) * Math.PI) / 180;
  const R = num(p.radius, 800, 50, 5000);
  const sunDir = new THREE.Vector3(Math.sin(az) * Math.cos(elev), Math.sin(elev), Math.cos(az) * Math.cos(elev)).normalize();
  const sm = new THREE.ShaderMaterial({
    uniforms: {
      uTop: { value: new THREE.Color(top) }, uHorizon: { value: new THREE.Color(horizon) }, uBottom: { value: new THREE.Color(bottom) },
      uSun: { value: new THREE.Color(sunC) }, uSunDir: { value: sunDir }, uGlow: { value: num(p.glow, pr.glow, 0, 4) },
    },
    vertexShader: `varying vec3 vDir; void main(){ vDir = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
    fragmentShader: `uniform vec3 uTop; uniform vec3 uHorizon; uniform vec3 uBottom; uniform vec3 uSun; uniform vec3 uSunDir; uniform float uGlow; varying vec3 vDir;
      void main(){ vec3 d = normalize(vDir); float h = d.y;
        vec3 c = h > 0.0 ? mix(uHorizon, uTop, pow(clamp(h, 0.0, 1.0), 0.5)) : mix(uHorizon, uBottom, pow(clamp(-h, 0.0, 1.0), 0.35));
        float s = max(dot(d, normalize(uSunDir)), 0.0);
        c += uSun * (pow(s, 900.0) * 2.0 + pow(s, 14.0) * uGlow * 0.35 + pow(s, 3.0) * uGlow * 0.08);
        gl_FragColor = vec4(c, 1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }`,
    side: THREE.BackSide, depthWrite: false, fog: false,
  });
  const dome = new THREE.Mesh(geo(ctx, 'skyDome', () => new THREE.SphereGeometry(1, 32, 16)), sm);
  dome.scale.setScalar(R); dome.name = 'sky_dome'; dome.frustumCulled = false; dome.renderOrder = -1000;
  noRaycast(dome);
  if (bool(p.followCamera ?? p.follow_camera, true)) {
    const m = new THREE.Matrix4(), sv = new THREE.Vector3(R, R, R);
    dome.onBeforeRender = (renderer, scene, camera) => { dome.matrixWorld.copy(m.makeTranslation(camera.position.x, camera.position.y, camera.position.z).scale(sv)); };
  }
  const g = group('sky'); g.add(dome);
  g.userData.preset = name;
  g.userData.sunDirection = [sunDir.x, sunDir.y, sunDir.z];
  if (bool(p.stars, !!pr.stars)) g.add(stars({ radius: R * 0.9, count: p.starCount, opacity: name === 'celestial' ? 1 : 0.9 }, ctx));
  return g;
}

// ================================================================ mandala (golden yantra that can "form")
function mandala(params, ctx) {
  const p = P(params);
  const R = num(p.radius, 6, 0.2, 400), color = baseColor(p, '#E3C77E');
  const vertical = str(p.orientation, 'vertical', ['vertical', 'horizontal']) === 'vertical';
  const g = group('mandala');
  const inner = new THREE.Group(); inner.name = 'mandala_inner'; g.add(inner);
  if (!vertical) { inner.rotation.x = -Math.PI / 2; inner.position.y = 0.05; }
  const w = R * 0.012;
  const ring = (rr, ww = w) => only(new THREE.RingGeometry(rr - ww / 2, rr + ww / 2, seg(ctx, 64, 96, 128), 1), ['position']);
  const strip = (pts, closed) => only(stripGeometry(pts, w, closed), ['position']);
  const polar = (rr, a) => [Math.sin(a) * rr, Math.cos(a) * rr];
  const tri = (rr, rot) => [0, 1, 2].map((i) => polar(rr, rot + (i / 3) * TAU));
  const petals = (n, r0, r1, rot = 0) => {
    const out = [];
    for (let i = 0; i < n; i++) {
      const a = rot + (i / n) * TAU, da = (Math.PI / n) * 0.92, pts = [];
      const b0 = polar(r0, a - da), c0 = polar((r0 + r1) / 2 * 1.04, a - da * 1.05), tip = polar(r1, a), c1 = polar((r0 + r1) / 2 * 1.04, a + da * 1.05), b1 = polar(r0, a + da);
      const qb = (A, C, B, t) => [(1 - t) * (1 - t) * A[0] + 2 * (1 - t) * t * C[0] + t * t * B[0], (1 - t) * (1 - t) * A[1] + 2 * (1 - t) * t * C[1] + t * t * B[1]];
      for (let k = 0; k <= 8; k++) pts.push(qb(b0, c0, tip, k / 8));
      for (let k = 1; k <= 8; k++) pts.push(qb(tip, c1, b1, k / 8));
      out.push(strip(pts, false));
    }
    return out;
  };
  const bhupura = (h) => {
    const gw = h * 0.22, gd = h * 0.1, pts = [];
    for (let side = 0; side < 4; side++) {
      const rot = -(side * Math.PI) / 2; // walk the square clockwise so each side continues from the last corner
      const local = [[-h, h], [-gw, h], [-gw, h + gd], [gw, h + gd], [gw, h]];
      for (const [x, y] of local) pts.push([x * Math.cos(rot) - y * Math.sin(rot), x * Math.sin(rot) + y * Math.cos(rot)]);
    }
    return strip(pts, true);
  };
  const layerDefs = [
    [only(new THREE.CircleGeometry(R * 0.035, 20), ['position'])],
    [strip(tri(R * 0.22, 0), true), strip(tri(R * 0.22, Math.PI), true), strip(tri(R * 0.15, Math.PI / 6), true), strip(tri(R * 0.15, Math.PI + Math.PI / 6), true)],
    [ring(R * 0.3)],
    petals(8, R * 0.3, R * 0.48),
    [ring(R * 0.5), ring(R * 0.53)],
    petals(16, R * 0.53, R * 0.72, Math.PI / 16),
    [ring(R * 0.74), ring(R * 0.77, w * 0.6), ring(R * 0.8)],
    [bhupura(R * 0.9), bhupura(R * 0.95)],
  ];
  const layers = layerDefs.map((list, i) => {
    const mm = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.9, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide, fog: false });
    const mesh = new THREE.Mesh(mergeGeometries(list, false), mm);
    mesh.name = 'mandala_layer_' + i; mesh.position.z = i * 0.002 * R;
    inner.add(mesh);
    return { mesh, mm };
  });
  const tex = softDotTex();
  let glow = null;
  if (tex) {
    glow = new THREE.Mesh(geo(ctx, 'unitPlane', () => new THREE.PlaneGeometry(1, 1)), new THREE.MeshBasicMaterial({ map: tex, color, transparent: true, opacity: 0.32, blending: THREE.AdditiveBlending, depthWrite: false, fog: false }));
    glow.scale.setScalar(R * 2.6); glow.position.z = -0.01 * R; glow.name = 'mandala_glow';
    inner.add(glow);
  }
  let progress = 1;
  // reveal() is an explicit API call: it writes opacity through mesh.material so it still works after the
  // engine has swapped in cloned materials (ensureOwnMaterials). The idle update never touches opacity.
  const reveal = (v) => {
    progress = Math.min(1, Math.max(0, num(v, 1)));
    const n = layers.length;
    layers.forEach(({ mesh }, i) => {
      const local = Math.min(1, Math.max(0, progress * (n + 1.5) - i));
      const e = 1 - Math.pow(1 - local, 3);
      if (mesh.material) mesh.material.opacity = 0.9 * local;
      mesh.scale.setScalar(0.9 + 0.1 * e); mesh.visible = local > 0.001;
    });
    if (glow) { glow.material.opacity = 0.32 * progress; glow.visible = progress > 0.001; }
    g.userData.revealed = progress;
  };
  reveal(p.reveal ?? 1);
  g.userData.reveal = reveal;
  const speed = num(p.speed ?? (p.rotation_speed !== undefined ? num(p.rotation_speed, 0.05) * 20 : undefined), 1, 0, 20);
  addUpdate(g, (dt, t) => {
    layers.forEach(({ mesh }, i) => { mesh.rotation.z += dt * 0.02 * speed * (i % 2 ? -1 : 1) * (1 + i * 0.15); });
    if (glow) glow.scale.setScalar(R * 2.6 * (0.96 + 0.04 * Math.sin(t * 1.2)));
  });
  return g;
}

// ================================================================ lake (with shore, reeds, lotus, ghat steps)
function lake(params, ctx) {
  const p = P(params);
  const R = num(p.radius, 22, 2, 800), color = baseColor(p, '#3D6E7E'), shoreC = col(p, ['shore', 'sand', 'bank'], '#B8A27B');
  const g = group('lake');
  const r = rng(p.seed ?? 'sarovara');
  const wm = waterMat(ctx, color, num(p.opacity, 0.94, 0.2, 1), 'lake');
  const waterMesh = new THREE.Mesh(geo(ctx, `lakeDisc:${R}:${qname(ctx)}`, () => {
    const cg = new THREE.CircleGeometry(R, seg(ctx, 32, 48, 64)).rotateX(-Math.PI / 2);
    const uv = cg.attributes.uv; for (let i = 0; i < uv.count; i++) uv.setXY(i, uv.getX(i) * R / 6, uv.getY(i) * R / 6);
    return cg;
  }), wm);
  waterMesh.position.y = 0.05; waterMesh.receiveShadow = true; waterMesh.name = 'lake_water';
  const shore = new THREE.Mesh(geo(ctx, `lakeShore:${R}:${qname(ctx)}`, () => {
    const rg = new THREE.RingGeometry(R * 0.97, R * 1.28, seg(ctx, 32, 48, 64), 2).rotateX(-Math.PI / 2);
    const pos = rg.attributes.position;
    for (let i = 0; i < pos.count; i++) {
      const x = pos.getX(i), z = pos.getZ(i), d = Math.hypot(x, z);
      if (d > R * 1.1) { const k = 1 + (fbm(x * 0.08, z * 0.08, 4, 3) - 0.5) * 0.25; pos.setX(i, x * k); pos.setZ(i, z * k); }
    }
    rg.computeVertexNormals();
    return rg;
  }), mat(ctx, { color: shoreC, roughness: 1, polyOffset: true }));
  shore.position.y = 0.03; shore.receiveShadow = true;
  g.add(shore, waterMesh);
  flowWater(g, wm, 0.003, 0.006);
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), e = new THREE.Euler(), c = new THREE.Color();
  const np = p.lotus === false ? 0 : qcount(ctx, int(p.lotus, 34, 0, 500), 0); // count, or a boolean
  if (np > 0) {
    const pads = new THREE.InstancedMesh(G.cyl(ctx, 10), mat(ctx, { color: '#3E6B35', roughness: 0.8 }), np);
    const nfl = Math.max(1, Math.round(np * 0.35));
    const blooms = new THREE.InstancedMesh(lathe(ctx, 'lotusBloom', [[0, 0], [0.3, 0.1], [0.5, 0.45], [0.35, 0.6], [0, 0.4]], 8), mat(ctx, { color: col(p, ['lotus_color', 'lotusColor'], '#E796AE'), roughness: 0.6, emissive: '#5a2030', ei: 0.2 }), nfl);
    let bi = 0;
    for (let i = 0; i < np; i++) {
      const a = r() * TAU, d = Math.sqrt(r()) * R * 0.85, s = r.range(0.5, 1.0);
      const x = Math.sin(a) * d, z = Math.cos(a) * d;
      m.compose(new THREE.Vector3(x, 0.07, z), q.setFromEuler(e.set(0, r() * TAU, 0)), new THREE.Vector3(s * 1.1, 0.02, s * 1.1));
      pads.setMatrixAt(i, m);
      if (bi < nfl && r() < 0.45) { m.compose(new THREE.Vector3(x, 0.09, z), q, new THREE.Vector3(s * 0.5, s * 0.5, s * 0.5)); blooms.setMatrixAt(bi++, m); }
    }
    blooms.count = bi;
    for (const im of [pads, blooms]) { im.instanceMatrix.needsUpdate = true; im.computeBoundingSphere(); g.add(im); }
  }
  const nr = p.reeds === false ? 0 : qcount(ctx, int(p.reeds, 90, 0, 2000), 0);
  if (nr > 0) {
    const reeds = new THREE.InstancedMesh(G.cone(ctx, 4), mat(ctx, { color: '#ffffff', roughness: 1, flat: true }), nr);
    const ghat = bool(p.ghat, true);
    let ri = 0;
    for (let i = 0; i < nr; i++) {
      let a = r() * TAU;
      if (ghat && Math.abs(Math.atan2(Math.sin(a), Math.cos(a))) < 0.35) a += Math.PI; // keep the ghat (front, +z) clear
      const d = R * r.range(0.95, 1.12), h = r.range(0.6, 1.6);
      m.compose(new THREE.Vector3(Math.sin(a) * d, h / 2, Math.cos(a) * d), q.setFromEuler(e.set(r.range(-0.15, 0.15), 0, r.range(-0.15, 0.15))), new THREE.Vector3(0.16, h, 0.16));
      reeds.setMatrixAt(ri, m); reeds.setColorAt(ri++, c.set('#6E7F3E').offsetHSL(0, 0, r.range(-0.06, 0.06)));
    }
    reeds.count = ri;
    reeds.instanceMatrix.needsUpdate = true; if (reeds.instanceColor) reeds.instanceColor.needsUpdate = true; reeds.computeBoundingSphere();
    g.add(reeds);
  }
  if (bool(p.ghat, true)) { // stone steps descending into the water on the +z side
    const b = new Batch(), S = stone(ctx, col(p, ['ghatColor'], PAL.sandLight));
    for (let i = 0; i < 5; i++) b.box(ctx, S, 9 - i * 0.2, 0.3, 1.1, 0, 0.3 - i * 0.12, R * 1.08 - i * 0.9);
    for (const sx of [-1, 1]) b.box(ctx, S, 1.2, 1.2, 5.2, sx * 5, 0.5, R * 1.08 - 1.8);
    b.build(g, ctx, `ghat:${R}:${p.ghatColor ?? ''}`);
  }
  return g;
}

export const ENVIRONMENT = { mountains, river, water, forest, grove, road, skyline, garden, terrain, army, clouds, mist, dust, rain, fire, stars, sky, mandala, snow, lake };
