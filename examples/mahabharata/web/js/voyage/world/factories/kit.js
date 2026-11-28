// Shared toolkit for the procedural factories (private to world/factories/).
// Param readers, cached materials/geometries, a geometry batcher that merges static parts by material,
// deterministic noise, canvas-generated textures and a reusable flame. Never throws on bad params.
import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

// ---------------------------------------------------------------- palette (mirrors base.css tokens)
export const PAL = {
  sandstone: '#C8A57A', sandLight: '#DCC39C', sandDark: '#9A774F', gold: '#C9A24B', goldSoft: '#E3C77E',
  ivory: '#F3EAD7', marble: '#ECE4D4', vermilion: '#B5452F', forest: '#2F4A3A', leaf: '#4F6E3B',
  wood: '#6B4A2F', woodDark: '#3F2A1C', thatch: '#A88B57', clay: '#9C5B3B', brick: '#94513A',
  night: '#0B0E22', skin: '#B98A68', water: '#3D6F86', iron: '#5E6168', cloth: '#E9DFC8', celestial: '#6F8FC9',
};

// ---------------------------------------------------------------- param readers
export const P = (p) => (p && typeof p === 'object' && !Array.isArray(p) ? p : {});
export function num(v, d, lo = -Infinity, hi = Infinity) {
  const n = typeof v === 'number' ? v : (typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN);
  return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : d;
}
export const int = (v, d, lo, hi) => Math.round(num(v, d, lo, hi));
export function bool(v, d) {
  if (typeof v === 'boolean') return v;
  if (v === 'true' || v === 1) return true;
  if (v === 'false' || v === 0) return false;
  return d;
}
export function hex(v, d) {
  return typeof v === 'string' && /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(v.trim()) ? v.trim() : d;
}
export function str(v, d, allowed) {
  if (typeof v !== 'string' || !v) return d;
  const s = v.trim().toLowerCase();
  return !allowed || allowed.includes(s) ? s : d;
}
/** First defined value among keys; keys may be dotted ('materials.base'). */
export function pick(p, keys, d) {
  for (const k of keys) {
    let cur = p;
    for (const part of k.split('.')) cur = cur && typeof cur === 'object' ? cur[part] : undefined;
    if (cur !== undefined && cur !== null) return cur;
  }
  return d;
}
export const col = (p, keys, d) => hex(pick(p, keys), d);
/**
 * Common colour keys: base/primary colour and accent (the accent drives gilded parts, so an explicit `gold`
 * wins). Registry `materials` blocks are honoured too (index.js passes ctx.spec in as params.materials).
 */
export const baseColor = (p, d) => col(p, ['color', 'base', 'primary', 'stone', 'materials.base'], d);
export const accentColor = (p, d) => col(p, ['gold', 'accent', 'secondary', 'trim', 'materials.accent'], d);

// ---------------------------------------------------------------- registry context (optional ctx fields)
/** Registry footprint [w, h, d] from ctx.bounds.size (AssetLibrary passes it), or null. */
export function boundsOf(ctx) {
  const s = ctx && ctx.bounds && Array.isArray(ctx.bounds.size) ? ctx.bounds.size : null;
  if (!s || s.length < 3) return null;
  const v = s.slice(0, 3).map((x) => num(x, NaN, 0, 1e5));
  return v.every((x) => Number.isFinite(x) && x > 0) ? v : null;
}
/** Asset / character id hint from params or ctx (e.g. 'char.draupadi' -> 'draupadi'). Never throws. */
export function idOf(p, ctx) {
  const raw = (p && (p.character ?? p.character_id ?? p.assetId ?? p.asset_id)) ?? (ctx && ctx.assetId) ?? '';
  return String(raw).toLowerCase().replace(/^(char|obj|arch|env)\./, '');
}
/** Purely atmospheric parts (sky, stars, mist, particles, glows) must never occlude hotspot raycasts. */
const NO_RAYCAST = () => {};
export function noRaycast(obj) {
  if (obj && typeof obj.traverse === 'function') obj.traverse((o) => { o.raycast = NO_RAYCAST; });
  return obj;
}

// ---------------------------------------------------------------- quality
export const QUALITY = { low: 0.35, medium: 0.7, high: 1 };
export const qname = (ctx) => (ctx && QUALITY[ctx.quality] ? ctx.quality : 'medium');
export const qf = (ctx) => QUALITY[qname(ctx)];
export const seg = (ctx, lo, med, hi) => ({ low: lo, medium: med, high: hi })[qname(ctx)];
export const qcount = (ctx, n, min = 1) => Math.max(min, Math.round(n * qf(ctx)));

// ---------------------------------------------------------------- deterministic randomness + noise
export function seedOf(v) {
  if (typeof v === 'number' && Number.isFinite(v)) return v | 0;
  const s = String(v ?? 'voyage');
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h | 0;
}
export function rng(seed = 1) { // mulberry32
  let a = seedOf(seed) >>> 0;
  const r = () => {
    a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  r.range = (lo, hi) => lo + (hi - lo) * r();
  r.pick = (arr) => arr[Math.floor(r() * arr.length) % arr.length];
  return r;
}
function hash2(ix, iy, seed) {
  let h = (Math.imul(ix, 374761393) + Math.imul(iy, 668265263) + Math.imul(seed, 1442695041)) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}
const fade = (t) => t * t * (3 - 2 * t);
/** Value noise in [0,1]. `period` (int) makes it tile. */
export function noise2(x, y, seed = 0, period = 0) {
  const x0 = Math.floor(x), y0 = Math.floor(y);
  const fx = fade(x - x0), fy = fade(y - y0);
  const w = (i) => (period ? ((i % period) + period) % period : i);
  const a = hash2(w(x0), w(y0), seed), b = hash2(w(x0 + 1), w(y0), seed);
  const c = hash2(w(x0), w(y0 + 1), seed), d = hash2(w(x0 + 1), w(y0 + 1), seed);
  return a + (b - a) * fx + (c - a) * fy + (a - b - c + d) * fx * fy;
}
export function fbm(x, y, seed = 0, oct = 4, period = 0) {
  let s = 0, amp = 0.5, f = 1, norm = 0;
  for (let i = 0; i < oct; i++) {
    s += amp * noise2(x * f, y * f, seed + i * 17, period ? period * f : 0);
    norm += amp; amp *= 0.5; f *= 2;
  }
  return s / norm;
}
export const smooth = (e0, e1, x) => { const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0))); return t * t * (3 - 2 * t); };

// ---------------------------------------------------------------- caches
function cached(cache, key, make) {
  if (cache && typeof cache.get === 'function') {
    try { const v = cache.get(key, make); if (v) return v; } catch (e) { /* fall through to direct build */ }
  }
  return make();
}
export const geo = (ctx, key, make) => cached(ctx && ctx.geometries, 'vy:' + key, make);

/**
 * Cached MeshStandardMaterial from a plain spec. Keys: color, roughness, metalness, emissive, ei (emissiveIntensity),
 * flat, double, opacity, vc (vertexColors), tex/alpha/normal (named canvas textures), alphaTest, side.
 */
export function mat(ctx, spec) {
  const s = Object.assign({ color: '#ffffff', roughness: 0.8, metalness: 0 }, spec);
  const key = 'vy:std:' + JSON.stringify(s);
  return cached(ctx && ctx.materials, key, () => makeStd(s));
}
function makeStd(s) {
  const m = new THREE.MeshStandardMaterial({
    color: s.color, roughness: s.roughness, metalness: s.metalness, flatShading: !!s.flat,
    side: s.double ? THREE.DoubleSide : THREE.FrontSide, vertexColors: !!s.vc,
  });
  if (s.emissive) { m.emissive = new THREE.Color(s.emissive); m.emissiveIntensity = s.ei ?? 1; }
  if (s.opacity !== undefined && s.opacity < 1) { m.transparent = true; m.opacity = s.opacity; m.depthWrite = !!s.depthWrite; }
  if (s.tex) { const t = TEX(s.tex); if (t) m.map = t; }
  if (s.alpha) { const t = TEX(s.alpha); if (t) { m.alphaMap = t; m.alphaTest = s.alphaTest ?? 0.5; } }
  if (s.normal) { const t = TEX(s.normal); if (t) { m.normalMap = t; m.normalScale.set(s.ns ?? 0.6, s.ns ?? 0.6); } }
  if (s.polyOffset) { m.polygonOffset = true; m.polygonOffsetFactor = -1; m.polygonOffsetUnits = -2; }
  return m;
}
// Named looks used everywhere.
// Wood and stone keep a minimum lightness so a backlit prop never collapses into a pure black
// silhouette under ACES (darkM stays available for deliberately black parts).
const _lift = new THREE.Color(), _hsl = { h: 0, s: 0, l: 0 };
function lifted(c, minL = 0.2) {
  try { _lift.set(c); } catch (e) { return c; }
  _lift.getHSL(_hsl, THREE.SRGBColorSpace);
  if (_hsl.l >= minL) return c;
  _lift.setHSL(_hsl.h, _hsl.s, minL, THREE.SRGBColorSpace);
  return '#' + _lift.getHexString(THREE.SRGBColorSpace);
}
export const stone = (ctx, c = PAL.sandstone, o = {}) => mat(ctx, { color: lifted(c), roughness: 0.86, metalness: 0, ...o });
export const goldM = (ctx, c = PAL.gold, o = {}) => mat(ctx, { color: c, roughness: 0.34, metalness: 0.85, ...o });
export const clothM = (ctx, c = PAL.cloth, o = {}) => mat(ctx, { color: c, roughness: 0.92, metalness: 0, double: true, ...o });
export const woodM = (ctx, c = PAL.wood, o = {}) => mat(ctx, { color: lifted(c), roughness: 0.78, metalness: 0, ...o });
export const darkM = (ctx, c = '#1a1410') => mat(ctx, { color: c, roughness: 1, metalness: 0 });
export const glowM = (ctx, c = '#ffcf7a', ei = 1.4) => mat(ctx, { color: c, emissive: c, ei, roughness: 0.6 });

// ---------------------------------------------------------------- primitive geometries (unit, cached)
export const G = {
  box: (ctx) => geo(ctx, 'box1', () => new THREE.BoxGeometry(1, 1, 1)),
  cyl: (ctx, n = 12) => geo(ctx, 'cyl1:' + n, () => new THREE.CylinderGeometry(0.5, 0.5, 1, n)),
  cone: (ctx, n = 12) => geo(ctx, 'cone1:' + n, () => new THREE.ConeGeometry(0.5, 1, n)),
  frustum: (ctx, top, n = 8) => geo(ctx, `frus:${top}:${n}`, () => new THREE.CylinderGeometry(0.5 * top, 0.5, 1, n)),
  sphere: (ctx, w = 12, h = 8) => geo(ctx, `sph1:${w}:${h}`, () => new THREE.SphereGeometry(0.5, w, h)),
  hemi: (ctx, w = 16, h = 6) => geo(ctx, `hemi:${w}:${h}`, () => new THREE.SphereGeometry(0.5, w, h, 0, Math.PI * 2, 0, Math.PI / 2)),
  ico: (ctx, d = 0) => geo(ctx, 'ico:' + d, () => new THREE.IcosahedronGeometry(0.5, d)),
  torus: (ctx, tube = 0.08, rs = 6, ts = 24, arc = Math.PI * 2) =>
    geo(ctx, `tor:${tube}:${rs}:${ts}:${arc.toFixed(3)}`, () => new THREE.TorusGeometry(0.5, tube, rs, ts, arc)),
  capsule: (ctx, r, len, cs = 3, rs = 8) =>
    geo(ctx, `cap:${r}:${len}:${cs}:${rs}`, () => new THREE.CapsuleGeometry(r, len, cs, rs)),
  plane: (ctx) => geo(ctx, 'plane1', () => new THREE.PlaneGeometry(1, 1)),
};

/** Lathe from [[r, y], ...] points (bottom → top for outward-facing walls). */
export function lathe(ctx, key, pts, segs = 12, phiStart = 0, phiLength = Math.PI * 2) {
  return geo(ctx, `lathe:${key}:${segs}:${phiStart.toFixed(3)}:${phiLength.toFixed(3)}`, () =>
    new THREE.LatheGeometry(pts.map(([r, y]) => new THREE.Vector2(Math.max(0, r), y)), segs, phiStart, phiLength));
}

// ---------------------------------------------------------------- transforms
const _q = new THREE.Quaternion(), _e = new THREE.Euler(), _v = new THREE.Vector3(), _s = new THREE.Vector3();
/** Matrix from position, rotation (radians, XYZ) and scale (number or 3 numbers). */
export function T(x = 0, y = 0, z = 0, rx = 0, ry = 0, rz = 0, sx = 1, sy = sx, sz = sx) {
  return new THREE.Matrix4().compose(_v.set(x, y, z), _q.setFromEuler(_e.set(rx, ry, rz)), _s.set(sx, sy, sz));
}

/**
 * Batch: collects transformed copies of geometries per material and merges them into one mesh per material
 * (few draw calls, static). `within(matrix, fn)` nests local coordinate frames for reusable components.
 */
export class Batch {
  constructor() { this.buckets = new Map(); this.stack = [new THREE.Matrix4()]; }
  get top() { return this.stack[this.stack.length - 1]; }
  within(m, fn) { this.stack.push(this.top.clone().multiply(m)); try { fn(); } finally { this.stack.pop(); } }
  add(geometry, material, m) {
    if (!geometry || !material) return this;
    const g = prepGeometry(geometry);
    g.applyMatrix4(m ? this.top.clone().multiply(m) : this.top);
    let b = this.buckets.get(material);
    if (!b) this.buckets.set(material, (b = []));
    b.push(g);
    return this;
  }
  /** Convenience: unit box scaled to w×h×d, centred at x,y,z. */
  box(ctx, material, w, h, d, x = 0, y = 0, z = 0, rx = 0, ry = 0, rz = 0) {
    return this.add(G.box(ctx), material, T(x, y, z, rx, ry, rz, w, h, d));
  }
  /**
   * Merge into meshes added to `target`. cacheKey shares merged geometry across identical builds. The key is
   * suffixed with the bucket's material identity and vertex count, so a caller key that misses a param can
   * never pair a cached geometry with the wrong material or the wrong shape.
   */
  build(target, ctx, cacheKey, { cast = true, receive = true } = {}) {
    let i = 0;
    for (const [material, list] of this.buckets) {
      const make = () => {
        const merged = list.length === 1 ? list[0] : mergeGeometries(list, false);
        if (merged) merged.computeBoundingSphere();
        return merged;
      };
      let verts = 0;
      for (const g of list) verts += g.attributes.position.count;
      const merged = cacheKey ? geo(ctx, `batch:${cacheKey}:${i}:${material.uuid}:${list.length}:${verts}`, make) : make();
      i++;
      if (!merged) continue;
      const mesh = new THREE.Mesh(merged, material);
      mesh.castShadow = cast && !material.transparent && !material.alphaMap;
      mesh.receiveShadow = receive;
      target.add(mesh);
    }
    this.buckets.clear();
    return target;
  }
}
function prepGeometry(src) {
  const g = src.clone();
  for (const name of Object.keys(g.attributes)) if (!['position', 'normal', 'uv'].includes(name)) g.deleteAttribute(name);
  if (!g.attributes.normal) g.computeVertexNormals();
  const n = g.attributes.position.count;
  if (!g.attributes.uv) g.setAttribute('uv', new THREE.BufferAttribute(new Float32Array(n * 2), 2));
  if (!g.index) { const idx = new Array(n); for (let k = 0; k < n; k++) idx[k] = k; g.setIndex(idx); }
  g.clearGroups();
  return g;
}

/** Matrix placing a unit (y-axis, length 1) primitive between points a and b, with cross-section scale r (x, z). */
const _up = new THREE.Vector3(0, 1, 0), _dir = new THREE.Vector3();
export function rod(a, b, r = 0.05, rz = r) {
  _dir.set(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
  const len = _dir.length() || 1e-6;
  _q.setFromUnitVectors(_up, _dir.multiplyScalar(1 / len));
  return new THREE.Matrix4().compose(_v.set((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2), _q, _s.set(r, len, rz));
}

export function mesh(geometry, material, m, { cast = true, receive = false, name } = {}) {
  const o = new THREE.Mesh(geometry, material);
  if (m) m.decompose(o.position, o.quaternion, o.scale);
  o.castShadow = cast; o.receiveShadow = receive;
  if (name) o.name = name;
  return o;
}
export function setTransform(obj, m) { m.decompose(obj.position, obj.quaternion, obj.scale); return obj; }

// ---------------------------------------------------------------- animation plumbing
/** Chain a self-animation function onto obj.userData.update(dt, t). Only plain data goes into userData. */
export function addUpdate(obj, fn) {
  const prev = obj.userData.update;
  obj.userData.update = prev ? (dt, t) => { prev(dt, t); fn(dt, t); } : fn;
  obj.userData.selfAnimated = true;
  return obj;
}
/** Move every descendant's update into the root (so callers only need root.userData.update). */
export function hoistUpdates(root) {
  const fns = [];
  root.traverse((o) => {
    if (o !== root && typeof o.userData.update === 'function') {
      fns.push(o.userData.update);
      delete o.userData.update;
      delete o.userData.selfAnimated;
    }
  });
  if (fns.length) addUpdate(root, (dt, t) => { for (const f of fns) f(dt, t); });
  return root;
}

// ---------------------------------------------------------------- canvas textures (module cache; regenerate-safe)
const HAS_DOM = typeof document !== 'undefined' && typeof document.createElement === 'function';
const texCache = new Map();
function canvasTex(key, w, h, draw, { srgb = false, repeat = true } = {}) {
  if (texCache.has(key)) return texCache.get(key);
  if (!HAS_DOM) { texCache.set(key, null); return null; }
  try {
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    const g = c.getContext('2d');
    if (!g) throw new Error('no 2d context');
    draw(g, w, h);
    const t = new THREE.CanvasTexture(c);
    if (srgb) t.colorSpace = THREE.SRGBColorSpace;
    if (repeat) t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.anisotropy = 4;
    texCache.set(key, t);
    return t;
  } catch (e) {
    texCache.set(key, null);
    return null;
  }
}
/** Named textures usable from mat() specs. Unknown name → null. */
export function TEX(name) {
  if (typeof name !== 'string') return null;
  if (name === 'jaali') return jaaliTex();
  if (name === 'ripple') return rippleTex();
  if (name === 'manuscript') return manuscriptTex();
  if (name.startsWith('emblem:')) { const [, c, a, e] = name.split(':'); return emblemTex(c, a, e); }
  if (name.startsWith('board:')) { const [, c, a] = name.split(':'); return boardTex(c, a); }
  return null;
}
/** Jaali (pierced stone lattice) alpha map: white = stone, black = hole. */
export function jaaliTex() {
  return canvasTex('jaali', 128, 128, (g, w) => {
    g.fillStyle = '#fff'; g.fillRect(0, 0, w, w);
    g.fillStyle = '#000';
    const cell = w / 4;
    for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) {
      const cx = (i + 0.5) * cell, cy = (j + 0.5) * cell, r = cell * 0.34;
      // quatrefoil hole
      for (const [dx, dy] of [[0, -1], [1, 0], [0, 1], [-1, 0]]) {
        g.beginPath(); g.arc(cx + dx * r * 0.55, cy + dy * r * 0.55, r * 0.5, 0, Math.PI * 2); g.fill();
      }
      // small diamond between motifs
      g.beginPath(); g.moveTo(i * cell, j * cell - 4); g.lineTo(i * cell + 4, j * cell); g.lineTo(i * cell, j * cell + 4); g.lineTo(i * cell - 4, j * cell); g.fill();
    }
  });
}
/** Tileable ripple normal map for water. */
export function rippleTex() {
  return canvasTex('ripple', 128, 128, (g, w, h) => {
    const img = g.createImageData(w, h), P8 = 8;
    const H = (x, y) => fbm((x / w) * P8, (y / h) * P8, 7, 3, P8);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const dx = (H((x - 1 + w) % w, y) - H((x + 1) % w, y)) * 6;
      const dy = (H(x, (y - 1 + h) % h) - H(x, (y + 1) % h)) * 6;
      const l = Math.hypot(dx, dy, 1);
      const i = (y * w + x) * 4;
      img.data[i] = ((dx / l) * 0.5 + 0.5) * 255; img.data[i + 1] = ((dy / l) * 0.5 + 0.5) * 255;
      img.data[i + 2] = ((1 / l) * 0.5 + 0.5) * 255; img.data[i + 3] = 255;
    }
    g.putImageData(img, 0, 0);
  });
}
/** Soft round dot (points, glows). */
export function softDotTex() {
  return canvasTex('softdot', 64, 64, (g, w) => {
    const gr = g.createRadialGradient(w / 2, w / 2, 0, w / 2, w / 2, w / 2);
    gr.addColorStop(0, 'rgba(255,255,255,1)'); gr.addColorStop(0.35, 'rgba(255,255,255,0.55)'); gr.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = gr; g.fillRect(0, 0, w, w);
  }, { repeat: false });
}
/** Tileable soft cloud noise (alpha in all channels) for mist layers. */
export function cloudNoiseTex() {
  return canvasTex('cloudnoise', 128, 128, (g, w, h) => {
    const img = g.createImageData(w, h);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const v = smooth(0.38, 0.8, fbm((x / w) * 4, (y / h) * 4, 11, 4, 4));
      const i = (y * w + x) * 4;
      img.data[i] = img.data[i + 1] = img.data[i + 2] = 255; img.data[i + 3] = v * 255;
    }
    g.putImageData(img, 0, 0);
  });
}
/** Radial fade (alpha map) so horizontal layers have no hard edges. */
export function radialFadeTex() {
  return canvasTex('radialfade', 128, 128, (g, w) => {
    const gr = g.createRadialGradient(w / 2, w / 2, 0, w / 2, w / 2, w / 2);
    gr.addColorStop(0, '#fff'); gr.addColorStop(0.6, '#bbb'); gr.addColorStop(1, '#000');
    g.fillStyle = gr; g.fillRect(0, 0, w, w);
  }, { repeat: false });
}
/** Palm-leaf surface with abstract (non-textual) ink strokes. */
export function manuscriptTex() {
  return canvasTex('manuscript', 256, 64, (g, w, h) => {
    g.fillStyle = '#D9C28C'; g.fillRect(0, 0, w, h);
    const r = rng(42);
    for (let i = 0; i < 400; i++) { g.fillStyle = `rgba(120,90,40,${r() * 0.08})`; g.fillRect(r() * w, r() * h, r() * 30, 1); }
    g.strokeStyle = 'rgba(52,34,18,0.55)'; g.lineWidth = 1.2;
    for (let line = 0; line < 4; line++) {
      const y = 12 + line * 13;
      let x = 26;
      while (x < w - 26) {
        const len = 4 + r() * 9;
        g.beginPath(); g.moveTo(x, y); g.quadraticCurveTo(x + len / 2, y - 3 - r() * 3, x + len, y); g.stroke();
        g.beginPath(); g.moveTo(x, y - 5); g.lineTo(x + len, y - 5); g.stroke(); // headline stroke (abstract)
        x += len + 2 + r() * 4;
      }
    }
    g.fillStyle = 'rgba(60,40,20,0.6)';
    g.beginPath(); g.arc(14, h / 2, 3, 0, Math.PI * 2); g.arc(w - 14, h / 2, 3, 0, Math.PI * 2); g.fill();
  }, { srgb: true, repeat: false });
}
/** Banner cloth with a gold border and an emblem glyph. */
export function emblemTex(color = '#B5452F', accent = '#C9A24B', emblem = 'yantra') {
  const c = hex(color, PAL.vermilion), a = hex(accent, PAL.gold);
  return canvasTex(`emblem:${c}:${a}:${emblem}`, 256, 160, (g, w, h) => {
    g.fillStyle = c; g.fillRect(0, 0, w, h);
    g.strokeStyle = a; g.lineWidth = 7; g.strokeRect(8, 8, w - 16, h - 16);
    g.lineWidth = 2; g.strokeRect(18, 18, w - 36, h - 36);
    const cx = w * 0.42, cy = h / 2, R = h * 0.3;
    g.strokeStyle = a; g.fillStyle = a; g.lineWidth = 5; g.lineCap = 'round'; g.lineJoin = 'round';
    g.beginPath(); g.arc(cx, cy, R + 8, 0, Math.PI * 2); g.lineWidth = 2; g.stroke(); g.lineWidth = 5;
    const em = String(emblem || 'yantra');
    if (em === 'monkey') { // stylised leaping vanara glyph
      g.beginPath(); g.arc(cx + R * 0.35, cy - R * 0.45, R * 0.2, 0, Math.PI * 2); g.fill();
      g.beginPath(); g.moveTo(cx + R * 0.25, cy - R * 0.25); g.quadraticCurveTo(cx - R * 0.1, cy, cx - R * 0.35, cy + R * 0.45); g.stroke();
      g.beginPath(); g.moveTo(cx + R * 0.15, cy - R * 0.15); g.lineTo(cx + R * 0.75, cy - R * 0.7); g.stroke();
      g.beginPath(); g.moveTo(cx - R * 0.05, cy + R * 0.05); g.lineTo(cx + R * 0.35, cy + R * 0.6); g.stroke();
      g.beginPath(); g.moveTo(cx - R * 0.35, cy + R * 0.45); g.bezierCurveTo(cx - R * 1.0, cy + R * 0.2, cx - R * 0.9, cy - R * 0.8, cx - R * 0.45, cy - R * 0.55); g.stroke();
    } else if (em === 'sun') {
      g.beginPath(); g.arc(cx, cy, R * 0.42, 0, Math.PI * 2); g.fill();
      for (let i = 0; i < 12; i++) { const t = (i / 12) * Math.PI * 2; g.beginPath(); g.moveTo(cx + Math.cos(t) * R * 0.6, cy + Math.sin(t) * R * 0.6); g.lineTo(cx + Math.cos(t) * R * 0.95, cy + Math.sin(t) * R * 0.95); g.stroke(); }
    } else if (em === 'lotus') {
      for (let i = 0; i < 8; i++) { const t = (i / 8) * Math.PI * 2; g.save(); g.translate(cx, cy); g.rotate(t); g.beginPath(); g.ellipse(0, -R * 0.5, R * 0.18, R * 0.45, 0, 0, Math.PI * 2); g.stroke(); g.restore(); }
      g.beginPath(); g.arc(cx, cy, R * 0.15, 0, Math.PI * 2); g.fill();
    } else if (em === 'serpent' || em === 'naga' || em === 'elephant') {
      g.beginPath(); g.moveTo(cx - R * 0.2, cy + R * 0.85); g.bezierCurveTo(cx + R * 0.8, cy + R * 0.4, cx - R * 0.8, cy, cx, cy - R * 0.4); g.stroke();
      g.beginPath(); g.ellipse(cx, cy - R * 0.55, R * 0.3, R * 0.22, 0, 0, Math.PI * 2); g.fill();
    } else if (em === 'palm') {
      g.beginPath(); g.moveTo(cx, cy + R * 0.9); g.lineTo(cx, cy - R * 0.3); g.stroke();
      for (let i = -3; i <= 3; i++) { g.beginPath(); g.moveTo(cx, cy - R * 0.3); g.quadraticCurveTo(cx + i * R * 0.2, cy - R * 0.9, cx + i * R * 0.32, cy - R * 0.2); g.stroke(); }
    } else if (em === 'bow') {
      g.beginPath(); g.arc(cx + R * 0.5, cy, R * 0.85, Math.PI * 0.62, Math.PI * 1.38); g.stroke();
      g.beginPath(); g.moveTo(cx - R * 0.55, cy); g.lineTo(cx + R * 0.7, cy); g.stroke();
    } else if (em !== 'none') { // yantra: interlocked triangles
      for (const s of [1, -1]) { g.beginPath(); for (let i = 0; i < 3; i++) { const t = -Math.PI / 2 * s + (i / 3) * Math.PI * 2; g[i ? 'lineTo' : 'moveTo'](cx + Math.cos(t) * R * 0.8, cy + Math.sin(t) * R * 0.8); } g.closePath(); g.stroke(); }
      g.beginPath(); g.arc(cx, cy, R * 0.1, 0, Math.PI * 2); g.fill();
    }
  }, { srgb: true, repeat: false });
}
/** Cross-shaped dice board (chaupar-like) cloth. */
export function boardTex(color = '#7A2A22', accent = '#C9A24B') {
  const c = hex(color, '#7A2A22'), a = hex(accent, PAL.gold);
  return canvasTex(`board:${c}:${a}`, 192, 192, (g, w) => {
    g.fillStyle = c; g.fillRect(0, 0, w, w);
    const s = w / 8;
    g.strokeStyle = a; g.lineWidth = 2;
    for (let i = 0; i < 8; i++) for (let j = 0; j < 8; j++) {
      const inArm = (i >= 3 && i <= 4) || (j >= 3 && j <= 4);
      if (!inArm) continue;
      g.strokeRect(i * s + 2, j * s + 2, s - 4, s - 4);
      if ((i + j) % 5 === 0) { g.beginPath(); g.moveTo(i * s + 4, j * s + 4); g.lineTo(i * s + s - 4, j * s + s - 4); g.moveTo(i * s + s - 4, j * s + 4); g.lineTo(i * s + 4, j * s + s - 4); g.stroke(); }
    }
    g.lineWidth = 5; g.strokeRect(3 * s, 3 * s, 2 * s, 2 * s);
  }, { srgb: true, repeat: false });
}
/** Text label texture (placeholder obelisk). Not cached by text forever: small, keyed. */
export function labelTex(text) {
  const t = String(text || 'unknown').slice(0, 40);
  return canvasTex('label:' + t, 512, 96, (g, w, h) => {
    g.fillStyle = 'rgba(11,14,34,0.82)'; g.fillRect(0, 0, w, h);
    g.strokeStyle = PAL.gold; g.lineWidth = 3; g.strokeRect(3, 3, w - 6, h - 6);
    g.fillStyle = PAL.ivory; g.font = '600 38px "DM Sans", system-ui, sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
    g.fillText(t, w / 2, h / 2, w - 24);
  }, { srgb: true, repeat: false });
}

// ---------------------------------------------------------------- water material (scrolling ripples)
/** Water material; one per (colour, flow) pair with its own texture transform so speeds differ. */
export function waterMat(ctx, color = PAL.water, opacity = 0.9, flowKey = 'still') {
  return cached(ctx && ctx.materials, `vy:water:${color}:${opacity}:${flowKey}`, () => {
    const m = new THREE.MeshStandardMaterial({ color, roughness: 0.12, metalness: 0.25, transparent: opacity < 1, opacity });
    const base = rippleTex();
    if (base) { m.normalMap = base.clone(); m.normalMap.needsUpdate = true; m.normalScale.set(0.55, 0.55); }
    m.depthWrite = opacity >= 1;
    m.polygonOffset = true; m.polygonOffsetFactor = -1; m.polygonOffsetUnits = -1;
    return m;
  });
}
/** Scroll a water material's ripples by absolute time (idempotent across several users of one material). */
export function flowWater(obj, material, speedU, speedV) {
  obj.userData.flow = { speed: [speedU, speedV] };
  return addUpdate(obj, (dt, t) => { if (material.normalMap) material.normalMap.offset.set(t * speedU, t * speedV); });
}

// ---------------------------------------------------------------- flame
/**
 * Stylised living flame: additive teardrop layers + glow + optional embers and point light.
 * opts: {size, color, core, embers (count), light (bool), intensity, seed}
 */
export function flame(ctx, opts = {}) {
  const size = num(opts.size, 1, 0.05, 20);
  const color = hex(opts.color, '#FF8A2A'), core = hex(opts.core, '#FFE3A0');
  const r = rng(opts.seed ?? 3);
  const g = new THREE.Group();
  g.name = 'flame';
  const tear = lathe(ctx, 'teardrop', [[0, 0], [0.32, 0.12], [0.38, 0.32], [0.27, 0.58], [0.12, 0.84], [0, 1]], 9);
  const mk = (c, op) => new THREE.MeshBasicMaterial({ color: c, transparent: true, opacity: op, blending: THREE.AdditiveBlending, depthWrite: false, fog: true });
  const outerM = mk(color, 0.55), innerM = mk(core, 0.8);
  // tongues share one InstancedMesh (one draw call); each instance breathes on its own phase
  const n = Math.max(1, opts.tongues ?? 5);
  const tongues = new THREE.InstancedMesh(tear, outerM, n);
  tongues.name = 'flame_tongues';
  tongues.frustumCulled = false;
  const tData = [];
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2, d = i === 0 ? 0 : 0.18 * size;
    const s = (i === 0 ? 1 : r.range(0.45, 0.75)) * size;
    tData.push({ x: Math.cos(a) * d, z: Math.sin(a) * d, bx: s * 0.8, by: s * 1.5, ph: r() * 10 });
  }
  const _m = new THREE.Matrix4(), _p = new THREE.Vector3(), _sc = new THREE.Vector3(), _qq = new THREE.Quaternion(), _yy = new THREE.Vector3(0, 1, 0);
  const placeTongues = (t) => {
    for (let i = 0; i < n; i++) {
      const d = tData[i], f = 1 + 0.18 * Math.sin(t * 9 + d.ph) + 0.1 * Math.sin(t * 23 + d.ph * 2);
      _m.compose(_p.set(d.x, 0, d.z), _qq.setFromAxisAngle(_yy, t * 0.6 + d.ph), _sc.set(d.bx * (2 - f), d.by * f, d.bx * (2 - f)));
      tongues.setMatrixAt(i, _m);
    }
    tongues.instanceMatrix.needsUpdate = true;
  };
  placeTongues(0);
  g.add(tongues);
  const coreMesh = new THREE.Mesh(tear, innerM);
  coreMesh.scale.set(size * 0.42, size * 0.9, size * 0.42);
  g.add(coreMesh);
  const dot = softDotTex();
  let glow = null;
  if (dot) {
    glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: dot, color, transparent: true, opacity: 0.55, blending: THREE.AdditiveBlending, depthWrite: false }));
    glow.scale.set(size * 3.2, size * 3.2, 1); glow.position.y = size * 0.55;
    g.add(glow);
  }
  // embers
  let embers = null;
  const ne = opts.embers === undefined ? qcount(ctx, 24, 6) : Math.max(0, opts.embers | 0);
  if (ne > 0) {
    const pos = new Float32Array(ne * 3), life = new Float32Array(ne);
    for (let i = 0; i < ne; i++) { life[i] = r(); }
    const bg = new THREE.BufferGeometry();
    bg.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    embers = new THREE.Points(bg, new THREE.PointsMaterial({ color: '#FFB25A', size: 0.06 * Math.max(1, size), map: dot || null, transparent: true, opacity: 0.9, blending: THREE.AdditiveBlending, depthWrite: false }));
    embers.frustumCulled = false;
    embers.userData.life = Array.from(life);
    g.add(embers);
  }
  let light = null;
  if (opts.light) {
    light = new THREE.PointLight(color, num(opts.intensity, 2.2), 14 * Math.max(1, size), 1.6);
    light.position.y = size * 0.8; light.castShadow = false;
    g.add(light);
  }
  g.userData.flicker = { intensity: 1 };
  noRaycast(g); // a flame never hides a hotspot behind it
  const baseI = num(opts.intensity, 2.2);
  // Pulses use scale / light intensity, never material opacity: the engine fades objects by cloning their
  // materials (ensureOwnMaterials) and a per-frame opacity write here would override those fades.
  addUpdate(g, (dt, t) => {
    placeTongues(t);
    coreMesh.scale.y = size * (0.9 + 0.12 * Math.sin(t * 13));
    if (glow) glow.scale.setScalar(size * 3.2 * (0.92 + 0.07 * Math.sin(t * 11) + 0.04 * Math.sin(t * 29)));
    if (light) light.intensity = baseI * (0.85 + 0.15 * Math.sin(t * 12) + 0.08 * Math.sin(t * 31));
    if (embers) {
      const p = embers.geometry.attributes.position, L = embers.userData.life;
      for (let i = 0; i < L.length; i++) {
        L[i] += dt * 0.45; if (L[i] > 1) L[i] -= 1;
        const k = L[i], a = i * 2.39996;
        p.setXYZ(i, Math.cos(a + t * 0.5) * 0.25 * size * (1 + k), k * 2.6 * size, Math.sin(a + t * 0.5) * 0.25 * size * (1 + k));
      }
      p.needsUpdate = true;
    }
  });
  return g;
}

/** Thin strip mesh along a polyline in the XY plane (used for mandala line-work). */
export function stripGeometry(points, width = 0.05, closed = false) {
  const pts = closed ? [...points, points[0]] : points;
  const pos = [], idx = [];
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i], prev = pts[Math.max(0, i - 1)], next = pts[Math.min(pts.length - 1, i + 1)];
    let dx = next[0] - prev[0], dy = next[1] - prev[1];
    const l = Math.hypot(dx, dy) || 1; dx /= l; dy /= l;
    const nx = -dy * width / 2, ny = dx * width / 2;
    pos.push(p[0] + nx, p[1] + ny, 0, p[0] - nx, p[1] - ny, 0);
    if (i < pts.length - 1) { const k = i * 2; idx.push(k, k + 1, k + 2, k + 1, k + 3, k + 2); }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setIndex(idx);
  g.computeVertexNormals();
  return g;
}

export { THREE };
