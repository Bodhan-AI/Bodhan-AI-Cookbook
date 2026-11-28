// world/assets.js — shared GPU caches + the AssetLibrary.
//
// AssetLibrary.instance(assetId, overrides) resolves an asset from the registry:
//   1. registry entry has a non-null `glb`  -> GLTFLoader (lazy import), cloned per instance
//   2. otherwise / on GLB failure            -> procedural factory `fallback.factory` from FACTORIES
//   3. unknown id / missing or failing factory -> small labelled placeholder obelisk (never throws)
//
// Factories are called as factory(params, ctx) with
//   ctx = {THREE, materials: MaterialCache, geometries: GeometryCache, quality,
//          assetId, entry, spec (= entry.materials), bounds, rng (seeded, deterministic)}
//
// Both caches hand out SHARED resources. Scene disposal skips anything a cache owns; the engine
// calls cache.prune(liveSet) after evicting a scene so unused shared resources are freed too.
import * as THREE from 'three';

// ---------------------------------------------------------------- small helpers (exported)
const HEX_RE = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i;
export const DEG = Math.PI / 180;

export function isHex(v) { return typeof v === 'string' && HEX_RE.test(v.trim()); }
export function num(v, d = 0) { const n = Number(v); return Number.isFinite(n) ? n : d; }
export function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }
export function color(hex, fallback = '#888888') {
  return new THREE.Color(isHex(hex) ? hex.trim() : (isHex(fallback) ? fallback : '#888888'));
}
export function vec3(arr, fallback = [0, 0, 0]) {
  const a = Array.isArray(arr) ? arr : fallback;
  return new THREE.Vector3(num(a[0], fallback[0] || 0), num(a[1], fallback[1] || 0), num(a[2], fallback[2] || 0));
}
export function isVec3(arr) { return Array.isArray(arr) && arr.length >= 3 && arr.slice(0, 3).every((n) => Number.isFinite(Number(n))); }

// FNV-1a string hash + mulberry32 PRNG: deterministic scatter / noise per location.
export function hashString(s) {
  let h = 2166136261 >>> 0;
  s = String(s);
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}
export function mulberry32(seed) {
  let a = seed >>> 0;
  return function rand() {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
export const seededRandom = (key) => mulberry32(hashString(key));

/** Apply data-convention transforms: position [x,y,z], rotation [x,y,z] DEGREES, scale n | [x,y,z]. */
export function applyTransform(obj, { position, rotation, scale } = {}) {
  if (isVec3(position)) obj.position.set(num(position[0]), num(position[1]), num(position[2]));
  if (isVec3(rotation)) obj.rotation.set(num(rotation[0]) * DEG, num(rotation[1]) * DEG, num(rotation[2]) * DEG);
  if (Array.isArray(scale) && scale.length >= 3) {
    obj.scale.set(Math.max(1e-3, num(scale[0], 1)), Math.max(1e-3, num(scale[1], 1)), Math.max(1e-3, num(scale[2], 1)));
  } else if (scale != null && num(scale, 0) > 0) {
    obj.scale.setScalar(num(scale, 1));
  }
  return obj;
}

// Stable cache key for constructor args / material options.
const objIds = new WeakMap();
let objIdSeq = 0;
function identity(o) { if (!objIds.has(o)) objIds.set(o, 'o' + (++objIdSeq)); return objIds.get(o); }
export function stableKey(v) {
  if (v === null || v === undefined) return String(v);
  const t = typeof v;
  if (t === 'number') return Number.isFinite(v) ? String(Math.round(v * 1e5) / 1e5) : String(v);
  if (t === 'string') return JSON.stringify(v);
  if (t !== 'object') return String(v);
  if (v.isColor) return '#' + v.getHexString();
  if (v.isTexture || v.isMaterial || v.isBufferGeometry) return v.uuid;
  if (v.isVector2) return `v2(${stableKey(v.x)},${stableKey(v.y)})`;
  if (v.isVector3) return `v3(${stableKey(v.x)},${stableKey(v.y)},${stableKey(v.z)})`;
  if (Array.isArray(v)) return '[' + v.map(stableKey).join(',') + ']';
  const proto = Object.getPrototypeOf(v);
  if (proto !== Object.prototype && proto !== null) return identity(v); // Shapes, Curves, …: identity
  return '{' + Object.keys(v).sort().map((k) => k + ':' + stableKey(v[k])).join(',') + '}';
}

const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const clean = (opts) => {
  const o = {};
  if (opts && typeof opts === 'object') for (const k of Object.keys(opts)) if (opts[k] !== undefined && opts[k] !== null) o[k] = opts[k];
  return o;
};

// ---------------------------------------------------------------- disposal helpers (exported)
export function disposeMaterial(m) {
  if (!m) return;
  for (const k of Object.keys(m)) { const v = m[k]; if (v && v.isTexture) v.dispose(); }
  if (m.uniforms) for (const u of Object.values(m.uniforms)) if (u && u.value && u.value.isTexture) u.value.dispose();
  m.dispose();
}
const materialsOf = (o) => (Array.isArray(o.material) ? o.material : o.material ? [o.material] : []);

/** Free GPU resources under root, skipping anything owned by the shared caches. */
export function disposeObject3D(root, { geometries, materials } = {}) {
  if (!root) return;
  const doneG = new Set();
  const doneM = new Set();
  root.traverse((o) => {
    if (o.isInstancedMesh && typeof o.dispose === 'function') o.dispose(); // frees instance buffers only
    if (o.isLight && o.shadow && o.shadow.map) { o.shadow.map.dispose(); o.shadow.map = null; }
    // THREE.Sprite instances all share one module-level geometry: never dispose it.
    const g = o.isSprite ? null : o.geometry;
    if (g && !doneG.has(g) && !(geometries && geometries.owns(g))) { g.dispose(); doneG.add(g); }
    for (const m of materialsOf(o)) {
      if (!doneM.has(m) && !(materials && materials.owns(m))) { disposeMaterial(m); doneM.add(m); }
    }
  });
}

/** Collect every geometry / material referenced under root (for cache pruning). */
export function collectResources(root, set = new Set()) {
  if (!root) return set;
  root.traverse((o) => { if (o.geometry) set.add(o.geometry); for (const m of materialsOf(o)) set.add(m); });
  return set;
}

/**
 * Give an object its own material clones (once) so per-object tweens (fade, glow, highlight)
 * never leak into shared cached materials. Clones remember their base opacity/emissive.
 */
export function ensureOwnMaterials(root) {
  if (root.userData.ownMaterials) return root.userData.ownMaterials;
  const list = [];
  const conv = (m) => {
    const c = m.clone();
    const { cached, ...rest } = c.userData || {}; // the clone is NOT cache-owned
    c.userData = {
      ...rest,
      base: {
        opacity: m.opacity, transparent: m.transparent, depthWrite: m.depthWrite,
        emissive: m.emissive ? m.emissive.clone() : null,
        emissiveIntensity: m.emissiveIntensity != null ? m.emissiveIntensity : 0,
      },
    };
    list.push(c);
    return c;
  };
  root.traverse((o) => {
    if (!o.material || !(o.isMesh || o.isLine || o.isPoints || o.isSprite)) return;
    o.material = Array.isArray(o.material) ? o.material.map(conv) : conv(o.material);
  });
  root.userData.ownMaterials = list;
  return list;
}

/** Set opacity on own materials (call ensureOwnMaterials first). */
export function setOpacity(mats, a) {
  for (const m of mats) {
    const base = (m.userData.base && m.userData.base.opacity) ?? 1;
    const wantT = a < 0.999 || (m.userData.base && m.userData.base.transparent);
    if (m.transparent !== wantT) { m.transparent = wantT; m.needsUpdate = true; }
    m.opacity = base * a;
    m.depthWrite = a > 0.98 ? ((m.userData.base && m.userData.base.depthWrite) ?? true) : false;
  }
}

// ---------------------------------------------------------------- caches
class BaseCache {
  constructor(kind) { this.kind = kind; this.map = new Map(); this.owned = new Set(); }
  /** get(key, make) -> cached resource (make() called once). */
  get(key, make) {
    const k = String(key);
    if (this.map.has(k)) return this.map.get(k);
    if (typeof make !== 'function') return this._fallbackMake ? this._fallbackMake(k, make) : undefined;
    const r = make();
    if (r) { this.map.set(k, r); this.owned.add(r); if (r.userData) r.userData.cached = true; }
    return r;
  }
  has(key) { return this.map.has(String(key)); }
  owns(r) { return this.owned.has(r); }
  /** Take ownership of an externally created resource (e.g. GLB geometry). */
  adopt(r) { if (r && !this.owned.has(r)) { this.map.set('adopt:' + r.uuid, r); this.owned.add(r); } return r; }
  /** Dispose every cached resource not in `live` (a Set of resources still referenced). */
  prune(live) {
    let n = 0;
    for (const [k, r] of this.map) {
      if (live && live.has(r)) continue;
      this._disposeOne(r); this.map.delete(k); this.owned.delete(r); n++;
    }
    return n;
  }
  dispose() { this.prune(null); }
  get size() { return this.map.size; }
}

// Dynamic convenience methods via Proxy: geometries.box(1,2,1) -> cached THREE.BoxGeometry(1,2,1);
// materials.phong({...}) -> cached THREE.MeshPhongMaterial({...}). Unknown names return undefined.
function wrapDynamic(target, resolve) {
  return new Proxy(target, {
    get(t, prop, recv) {
      if (typeof prop !== 'string' || prop in t) return Reflect.get(t, prop, recv);
      const make = resolve(prop);
      return make ? make.bind(t) : undefined;
    },
  });
}

const GEOM_ALIASES = {
  box: 'BoxGeometry', cylinder: 'CylinderGeometry', cone: 'ConeGeometry', sphere: 'SphereGeometry',
  plane: 'PlaneGeometry', torus: 'TorusGeometry', torusKnot: 'TorusKnotGeometry', circle: 'CircleGeometry',
  ring: 'RingGeometry', capsule: 'CapsuleGeometry', icosahedron: 'IcosahedronGeometry', ico: 'IcosahedronGeometry',
  octahedron: 'OctahedronGeometry', dodecahedron: 'DodecahedronGeometry', tetrahedron: 'TetrahedronGeometry',
  lathe: 'LatheGeometry', tube: 'TubeGeometry', extrude: 'ExtrudeGeometry', shape: 'ShapeGeometry',
  polyhedron: 'PolyhedronGeometry', edges: 'EdgesGeometry',
};

export class GeometryCache extends BaseCache {
  constructor() {
    super('geometry');
    return wrapDynamic(this, (name) => {
      const C = THREE[GEOM_ALIASES[name] || cap(name) + 'Geometry'];
      if (typeof C !== 'function') return null;
      return function (...args) { return this.get(`${name}(${stableKey(args)})`, () => new C(...args)); };
    });
  }
  _disposeOne(g) { g.dispose(); }
}

export class MaterialCache extends BaseCache {
  constructor() {
    super('material');
    return wrapDynamic(this, (name) => {
      const C = THREE['Mesh' + cap(name) + 'Material'] || THREE[cap(name) + 'Material'];
      if (typeof C !== 'function') return null;
      return function (opts) { const o = clean(opts); return this.get(`${name}:${stableKey(o)}`, () => new C(o)); };
    });
  }
  _disposeOne(m) { disposeMaterial(m); }
  // get(key, {opts}) with a plain object instead of a function -> cached standard material.
  _fallbackMake(key, opts) {
    if (!opts || typeof opts !== 'object') return undefined;
    const m = new THREE.MeshStandardMaterial(clean(opts));
    this.map.set(key, m); this.owned.add(m); m.userData.cached = true;
    return m;
  }
  standard(opts) { const o = clean(opts); return this.get(`standard:${stableKey(o)}`, () => new THREE.MeshStandardMaterial(o)); }
  std(opts) { return this.standard(opts); }
  /** Quick lit material from a hex colour. */
  color(hex, extra = {}) { return this.standard({ color: isHex(hex) ? hex : '#888888', roughness: 0.8, metalness: 0, ...extra }); }
  /**
   * Material from a registry `materials` spec {base, accent, emissive, roughness, metalness}.
   * part: 'base' | 'accent'.
   */
  fromSpec(spec = {}, part = 'base', extra = {}) {
    const s = spec || {};
    const col = part === 'accent' ? (s.accent || s.base) : s.base;
    const o = {
      color: isHex(col) ? col : '#9a8a70',
      roughness: Number.isFinite(Number(s.roughness)) ? Number(s.roughness) : 0.75,
      metalness: Number.isFinite(Number(s.metalness)) ? Number(s.metalness) : 0.05,
      ...extra,
    };
    if (isHex(s.emissive) && !('emissive' in extra)) { o.emissive = s.emissive; o.emissiveIntensity = extra.emissiveIntensity ?? 0.35; }
    return this.standard(o);
  }
}

// ---------------------------------------------------------------- asset library
export class AssetLibrary {
  /**
   * @param registry asset_registry.assets object (or the whole registry with .assets)
   * @param factories FACTORIES map {name: (params, ctx) -> Object3D}
   * @param opts {materials, geometries, quality}
   */
  constructor(registry, factories, opts = {}) {
    this.registry = (registry && registry.assets && typeof registry.assets === 'object') ? registry.assets : (registry || {});
    this.factories = factories || {};
    this.materials = opts.materials || new MaterialCache();
    this.geometries = opts.geometries || new GeometryCache();
    this.quality = opts.quality || 'medium';
    this._status = new Map();
    this._gltf = new Map(); // url -> Promise<gltf>
    this._loader = null;
    this._skeleton = null;
  }

  setQuality(q) { this.quality = q; }
  entry(id) { return (typeof id === 'string' && this.registry && this.registry[id]) || null; }

  ctx(extra = {}) {
    return { THREE, materials: this.materials, geometries: this.geometries, quality: this.quality, ...extra };
  }

  /** 'glb' | 'procedural' | 'failed' — actual result if instanced before, otherwise the expected path. */
  status(assetId) {
    if (this._status.has(assetId)) return this._status.get(assetId);
    const e = this.entry(assetId);
    if (!e) return 'failed';
    if (e.glb) return 'glb';
    const f = e.fallback && this.factories[e.fallback.factory];
    return typeof f === 'function' ? 'procedural' : 'failed';
  }

  /**
   * Build an instance. overrides: {position, rotation (deg), scale, pose, params, seed}.
   * Returns a Group root (userData: assetId, status, animations, forward) wrapping the model.
   */
  async instance(assetId, overrides = {}) {
    const id = typeof assetId === 'string' ? assetId : String(assetId || 'unknown');
    const ov = overrides || {};
    const entry = this.entry(id);
    let inner = null;
    let status = 'failed';
    try {
      if (entry && entry.glb) {
        try { inner = await this._fromGlb(entry); status = 'glb'; }
        catch (e) { console.warn(`[assets] GLB failed for ${id}, using procedural fallback`, e); inner = null; }
      }
      if (!inner && entry) {
        inner = await this._fromFactory(id, entry, ov);
        // factories/index.js wraps a throwing factory in its own labelled placeholder and sets
        // userData.error: keep that marker, but report the asset honestly as 'failed'.
        if (inner) status = inner.userData && inner.userData.error ? 'failed' : 'procedural';
      }
    } catch (e) {
      console.warn(`[assets] instance(${id}) failed`, e);
      inner = null;
    }
    if (!inner) { inner = this._placeholder(id, entry); status = 'failed'; }
    // Status reflects the best result seen: one failed variant must not mask working instances.
    if (status !== 'failed' || !this._status.has(id)) this._status.set(id, status);

    const root = new THREE.Group();
    root.name = id;
    root.add(inner);
    root.userData.assetId = id;
    root.userData.status = status;
    root.userData.forward = (entry && entry.orientation && entry.orientation.forward) || '-z';
    root.userData.animations = Array.isArray(entry && entry.animations)
      ? entry.animations.map((a) => (a && a.name) || '').filter(Boolean) : [];
    root.userData.family = (entry && entry.family) || null;
    applyTransform(root, ov);
    return root;
  }

  async _fromFactory(id, entry, ov) {
    const fb = entry.fallback || {};
    const make = this.factories[fb.factory];
    if (typeof make !== 'function') {
      if (fb.factory) console.warn(`[assets] factory "${fb.factory}" not available for ${id}`);
      return null;
    }
    const params = { ...(fb.params && typeof fb.params === 'object' ? fb.params : {}), ...(ov.params && typeof ov.params === 'object' ? ov.params : {}) };
    if (ov.pose) params.pose = ov.pose;
    if (params.seed == null && ov.seed != null) params.seed = ov.seed; // factories vary per placement
    const ctx = this.ctx({
      assetId: id, entry, spec: entry.materials || {}, bounds: entry.bounds || null,
      rng: seededRandom(id + '|' + (ov.seed != null ? ov.seed : '')),
    });
    try {
      const o = await make(params, ctx);
      return o && o.isObject3D ? o : null;
    } catch (e) {
      console.warn(`[assets] factory "${fb.factory}" threw for ${id}`, e);
      return null;
    }
  }

  async _loadGltf(url) {
    if (!this._loader) {
      const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js');
      this._loader = new GLTFLoader();
    }
    return this._loader.loadAsync(url);
  }

  async _fromGlb(entry) {
    const url = entry.glb;
    if (!this._gltf.has(url)) this._gltf.set(url, this._loadGltf(url)); // failed promise stays: no retry storms
    const gltf = await this._gltf.get(url);
    if (!this._skeleton) this._skeleton = await import('three/addons/utils/SkeletonUtils.js');
    const obj = this._skeleton.clone(gltf.scene);
    obj.traverse((o) => {
      if (o.geometry) this.geometries.adopt(o.geometry);
      for (const m of materialsOf(o)) this.materials.adopt(m);
    });
    // Normalise: explicit registry scale wins; else fit the model's height to bounds.size[1].
    const s = num(entry.scale, 1);
    const size = entry.bounds && Array.isArray(entry.bounds.size) ? entry.bounds.size : null;
    if (s > 0 && s !== 1) obj.scale.multiplyScalar(s);
    else if (size && num(size[1], 0) > 0) {
      const box = new THREE.Box3().setFromObject(obj);
      const h = box.max.y - box.min.y;
      if (h > 1e-4) obj.scale.multiplyScalar(num(size[1]) / h);
    }
    obj.userData.clips = gltf.animations || [];
    return obj;
  }

  /** Small labelled obelisk: honest "this asset is missing" marker. */
  _placeholder(id, entry) {
    const g = new THREE.Group();
    g.name = 'placeholder:' + id;
    const h = Math.min(6, Math.max(1.2, num(entry && entry.bounds && entry.bounds.size && entry.bounds.size[1], 2.4)));
    const shaft = this.geometries.get('placeholder:shaft', () => {
      const c = new THREE.CylinderGeometry(0.17, 0.4, 1, 4, 1); c.rotateY(Math.PI / 4); c.translate(0, 0.5, 0); return c;
    });
    const tip = this.geometries.get('placeholder:tip', () => {
      const c = new THREE.ConeGeometry(0.17, 0.32, 4, 1); c.rotateY(Math.PI / 4); c.translate(0, 0.16, 0); return c;
    });
    const stone = this.materials.standard({ color: '#7d6a4f', roughness: 0.72, metalness: 0.05, emissive: '#C9A24B', emissiveIntensity: 0.06 });
    const gold = this.materials.standard({ color: '#C9A24B', roughness: 0.35, metalness: 0.8, emissive: '#C9A24B', emissiveIntensity: 0.25 });
    const body = new THREE.Mesh(shaft, stone);
    body.scale.set(1, h, 1);
    const cap = new THREE.Mesh(tip, gold);
    cap.position.y = h;
    g.add(body, cap);
    const label = makeLabelSprite(id || 'unknown asset');
    if (label) { label.position.y = h + 0.65; g.add(label); }
    g.userData.placeholder = true;
    return g;
  }
}

/** Canvas-text sprite (per instance; disposed with its scene). */
export function makeLabelSprite(text) {
  if (typeof document === 'undefined') return null;
  try {
    const c = document.createElement('canvas');
    c.width = 512; c.height = 96;
    const x = c.getContext('2d');
    x.fillStyle = 'rgba(11,14,34,0.72)';
    const r = 40;
    x.beginPath();
    x.moveTo(r, 8); x.lineTo(512 - r, 8); x.arc(512 - r, 48, 40, -Math.PI / 2, Math.PI / 2);
    x.lineTo(r, 88); x.arc(r, 48, 40, Math.PI / 2, Math.PI * 1.5); x.closePath(); x.fill();
    x.strokeStyle = 'rgba(201,162,75,0.6)'; x.lineWidth = 2; x.stroke();
    x.fillStyle = '#F3EAD7';
    x.font = '500 30px "DM Sans", system-ui, sans-serif';
    x.textAlign = 'center'; x.textBaseline = 'middle';
    x.fillText(String(text).slice(0, 34), 256, 50);
    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false, fog: false });
    const s = new THREE.Sprite(mat);
    s.scale.set(2.4, 0.45, 1);
    s.renderOrder = 5;
    return s;
  } catch (e) {
    return null;
  }
}
