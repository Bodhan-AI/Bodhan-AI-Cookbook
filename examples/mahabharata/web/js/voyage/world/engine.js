// world/engine.js — the 3D World: renderer, location scenes (LRU ≤3), staging, moods,
// highlights, hotspot projection, quality management and the cosmic opening.
//
// export async function createWorld(canvas, {V, store, bus}) -> World   (contract §World)
// When WebGL2 is unavailable the World is a resolved no-op with supported:false.
import * as THREE from 'three';
import {
  AssetLibrary, MaterialCache, GeometryCache, disposeObject3D, collectResources, ensureOwnMaterials,
  seededRandom, color, num, isHex, isVec3, vec3, DEG,
} from './assets.js';
import {
  SkyDome, LightRig, ParticleManager, ParticleField, Veil, skyColorsFor, MOODS, LIGHT_PRESETS,
  ENV_PARTICLES, VEIL_TYPES, makeGlowSprite, buildMandala, buildOpeningStars, buildPalmLeafFallback,
  desaturate,
} from './effects.js';
import { Director, EASE } from './director.js';

// ----------------------------------------------------------------- constants
const QUALITY = {
  low:    { dpr: 1,    shadows: false, terrainSeg: 80,  scatter: 0.5, stars: 1400 },
  medium: { dpr: 1.25, shadows: false, terrainSeg: 128, scatter: 0.8, stars: 2200 },
  high:   { dpr: 1.5,  shadows: true,  terrainSeg: 176, scatter: 1.0, stars: 3200 },
};
const MAX_SCENES = 3;
const STAGE_RADIUS = 14;
const TERRAIN_SIZE = 1400; // well past the ±200 authored area so fog swallows the edge
const NIGHT = '#0B0E22';
const DEFAULT_PALETTE = { sky: '#1A1D3A', fog: '#2a2d4a', ground: '#3b3a2e', accent: '#C9A24B', light: '#F3EAD7' };
const KIND_RELIEF = { mountain: 26, forest: 7, hermitage: 6, kingdom: 6, river: 4, battlefield: 4.5, city: 3.5, palace: 3, assembly: 2.5 };
const KIND_FOG = { mountain: 0.004, forest: 0.009, hermitage: 0.007, river: 0.006, battlefield: 0.0075, city: 0.005, palace: 0.005, assembly: 0.005, kingdom: 0.006 };
const TAU = Math.PI * 2;
const BUILD_WATCHDOG_MS = 9000; // a slow scene build never leaves the veil covering the UI longer than this

// Shot `environment.elements` carry no position. Place them where the authored base layouts put the
// same assets (backdrop beyond the stage disc), never on top of the stage. Factories the engine
// already provides (ground terrain, sky dome) are skipped so they cannot z-fight the real ground.
const ENV_SKIP_FACTORIES = new Set(['terrain', 'sky']);
const ENV_PLACEMENT = {
  'env.city_skyline':   { position: [0, 0, -150], scale: 1.3 },
  'env.mountains':      { position: [0, 0, -250], scale: 1.6 },
  'env.himalaya_peaks': { position: [0, 0, -190], scale: 1.8 },
  'env.clouds':         { position: [0, 55, -130] },
  'env.cosmic_mandala': { position: [0, 34, -80] },
  'env.forest':         { position: [0, 0, -78] },
  'env.sacred_grove':   { position: [-40, 0, -58] },
  'env.palace_garden':  { position: [-46, 0, 6] },
  'env.road':           { position: [0, 0, 45] },
  'env.river':          { position: [0, 0, -62], rotation: [0, 90, 0], scale: 0.6 },
  'env.ganga_water':    { position: [0, 0, -52], rotation: [0, 90, 0] },
  'env.lake':           { position: [-30, 0, 18], scale: 0.5 },
  'env.army_formation': { position: [0, 0, -95] },
  'env.fire':           { position: [0, 0, -10], scale: 0.7 },
};

const smooth = (a, b, x) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
const damp = (rate, dt) => 1 - Math.exp(-rate * dt);
const nextFrame = () => new Promise((r) => requestAnimationFrame(() => r()));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function webgl2Available() {
  try {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl2');
    if (!gl) return false;
    const lose = gl.getExtension('WEBGL_lose_context');
    if (lose) lose.loseContext();
    return true;
  } catch (e) { return false; }
}

function noopWorld() {
  const done = () => Promise.resolve();
  try { document.body.classList.add('no-webgl'); } catch (e) { /* noop */ }
  return {
    supported: false,
    showOpening: done, openingZoom: done, playChapter: done,
    playShot() {}, nextShot() {}, prevShot() {},
    currentShot: () => ({ index: -1, count: 0, shot: null }),
    setMood() {}, highlight() {}, setQuality() {}, pause() {}, resume() {}, dispose() {},
    onHotspots() { return () => {}; },
    stats: () => ({ fps: 0, drawCalls: 0, triangles: 0, scenesCached: 0 }),
  };
}

// Seeded 2D value noise + fbm for terrain.
function makeNoise2D(rand) {
  const perm = new Uint8Array(512), vals = new Float32Array(256), p = [];
  for (let i = 0; i < 256; i++) { p.push(i); vals[i] = rand() * 2 - 1; }
  for (let i = 255; i > 0; i--) { const j = Math.floor(rand() * (i + 1)); [p[i], p[j]] = [p[j], p[i]]; }
  for (let i = 0; i < 512; i++) perm[i] = p[i & 255];
  const fade = (t) => t * t * (3 - 2 * t);
  const at = (x, y) => vals[perm[(x & 255) + perm[y & 255]]];
  const noise = (x, y) => {
    const xi = Math.floor(x), yi = Math.floor(y), xf = x - xi, yf = y - yi, u = fade(xf), v = fade(yf);
    const a = at(xi, yi), b = at(xi + 1, yi), c = at(xi, yi + 1), d = at(xi + 1, yi + 1);
    return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
  };
  const fbm = (x, y) => { let s = 0, amp = 0.5, f = 1, norm = 0; for (let o = 0; o < 4; o++) { s += noise(x * f, y * f) * amp; norm += amp; amp *= 0.5; f *= 2.03; } return s / norm; };
  return { noise, fbm };
}

/** Default transform for a shot-only env element (see ENV_PLACEMENT); bounds decide unlisted ones. */
function envPlacement(id, entry) {
  if (ENV_PLACEMENT[id]) return ENV_PLACEMENT[id];
  const size = entry && entry.bounds && Array.isArray(entry.bounds.size) ? entry.bounds.size : null;
  const w = size ? num(size[0], 0) : 0, d = size ? num(size[2], 0) : 0;
  if (Math.max(w, d) <= 3) return { position: [0, 0, -(STAGE_RADIUS + 2)] };
  if (Math.max(w, d) >= 300) return { position: [0, 0, 0] }; // designed to surround the scene
  return { position: [0, 0, -(STAGE_RADIUS + 4 + d / 2)] };
}

/**
 * Factories may attach self-animation as `userData.update(dt, t)` (flames, water, breathing figures,
 * chariot wheels). Collect those objects once so the render loop can drive them cheaply.
 * Clones made by Object3D.clone() lose functions (userData is JSON-copied), so only originals count.
 */
function collectUpdaters(root, list = []) {
  if (!root) return list;
  root.traverse((o) => { if (o.userData && typeof o.userData.update === 'function') list.push(o); });
  return list;
}
function visibleChain(o) { for (let p = o; p; p = p.parent) if (!p.visible) return false; return true; }
function runUpdaters(list, dt, t) {
  for (const o of list) {
    if (o.userData.updateFailed || !visibleChain(o)) continue;
    try { o.userData.update(dt, t); } catch (e) {
      o.userData.updateFailed = true; // one warning, then leave it still
      console.warn('[world] self-animation failed for', o.name || o.type, e);
    }
  }
}

/** Largest camera→target distance across a shot's key frames (0 when the shot has no camera data). */
function shotReach(shot) {
  if (!shot || typeof shot !== 'object') return 0;
  const path = (Array.isArray(shot.camera_path) ? shot.camera_path : []).filter(isVec3);
  if (!path.length && isVec3(shot.camera_position)) path.push(shot.camera_position);
  if (!path.length) return 0;
  const tpath = (Array.isArray(shot.target_path) ? shot.target_path : []).filter(isVec3);
  const t0 = isVec3(shot.camera_target) ? shot.camera_target : [0, 1.5, 0];
  let max = 0;
  path.forEach((p, i) => {
    const t = tpath[i] || tpath[tpath.length - 1] || t0;
    max = Math.max(max, Math.hypot(num(p[0]) - num(t[0]), num(p[1]) - num(t[1]), num(p[2]) - num(t[2])));
  });
  return max;
}

function validPalette(p) {
  const out = { ...DEFAULT_PALETTE };
  if (p && typeof p === 'object') for (const k of Object.keys(DEFAULT_PALETTE)) if (isHex(p[k])) out[k] = p[k];
  return out;
}

// ================================================================= createWorld
export async function createWorld(canvas, { V, store, bus } = {}) {
  if (!canvas || typeof window === 'undefined' || !webgl2Available()) return noopWorld();

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: 'high-performance', stencil: false });
  } catch (e) {
    console.warn('[world] WebGL renderer could not start', e);
    return noopWorld();
  }
  canvas.style.background = NIGHT; // never a white frame, even before the first render
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1;
  renderer.setClearColor(NIGHT, 1);
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  // Factories are authored in parallel: load defensively, fall back to placeholders.
  let FACTORIES = {};
  try { const mod = await import('./factories/index.js'); FACTORIES = mod.FACTORIES || mod.default || {}; }
  catch (e) { console.warn('[world] procedural factories unavailable — assets will show as placeholders', e); }

  const materials = new MaterialCache();
  const geometries = new GeometryCache();
  const caches = { materials, geometries };

  let qualitySetting = ['auto', 'low', 'medium', 'high'].includes(store?.state?.settings?.quality) ? store.state.settings.quality : 'auto';
  let effQuality = qualitySetting === 'auto' ? 'medium' : qualitySetting;
  const Q = () => QUALITY[effQuality];

  const library = new AssetLibrary(V?.registry || {}, FACTORIES, { materials, geometries, quality: effQuality });
  const camera = new THREE.PerspectiveCamera(45, 16 / 9, 0.1, 2500);
  camera.position.set(0, 6, 26);
  const veil = new Veil(document.getElementById('transition-veil'));

  // ---- state
  const cache = new Map(); // locationKey -> bundle (LRU order)
  const building = new Map(); // locationKey -> Promise<bundle>
  let active = null; // bundle on screen (location bundle or opening)
  let opening = null;
  let currentChapter = null;
  let chapterToken = 0;
  let chapterTransitioning = false;
  let disposed = false, manualPause = false, contextLost = false;
  let raf = 0;
  const clock = new THREE.Clock();
  let elapsed = 0, particleTime = 0, sinceSwap = 0;
  let fps = 60, lowFpsTime = 0, hotspotAcc = 0;
  let moodName = 'calm', errorTime = 0;
  const moodCur = { ...MOODS.calm };
  let highlightIds = [];
  let highlighted = []; // [{obj, mats, snap}]
  const hotspotFns = new Set();
  let snapshotReq = null; // {run(), cancel()} — performed right after the next render
  let dissolveEl = null;
  let holdVeilTimer = 0;
  let openingBuild = null; // Promise of the opening scene while it is being built
  let animTime = 0; // clock for factory self-animations (slowed under reduced motion)
  const unsubs = [];

  // Persistent mood props that travel with the active scene.
  const motes = new ParticleField('gold_motes', { quality: effQuality, px: 1 });
  motes.configure({ density: 0.8, area: [26, 9, 26] });
  const moodGlow = makeGlowSprite(34);

  // ---- helpers
  function isReduced() {
    const s = store && store.state && store.state.settings && store.state.settings.reducedMotion;
    if (s === true || s === false) return s;
    try {
      if (document.body.classList.contains('reduced-motion')) return true;
      return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    } catch (e) { return false; }
  }
  function emit(evt, payload) { try { bus && bus.emit && bus.emit(evt, payload); } catch (e) { console.warn('[world] bus emit failed', evt, e); } }

  // If the canvas has no CSS size we own its inline size and follow the window from then on
  // (otherwise the inline px size we wrote would pin it after the first resize).
  let ownCanvasSize = false;
  function resize() {
    let w = canvas.clientWidth, h = canvas.clientHeight;
    if (ownCanvasSize || !w || !h) { ownCanvasSize = true; w = window.innerWidth; h = window.innerHeight; }
    w = Math.max(1, w); h = Math.max(1, h);
    renderer.setSize(w, h, ownCanvasSize);
    camera.aspect = w / h;
    viewW = w; viewH = h;
    applyViewOffset();
  }

  // Compose shots in the stage area that the desktop chapter panel (#stage-ui .cp-panel) leaves free:
  // shift the projection centre right by half the panel's right edge. Cleared on phones/tablets
  // (bottom sheet), when the panel is minimised or hidden, and during the opening.
  let viewW = 1, viewH = 1, viewInset = 0, viewInsetTarget = 0, insetPoll = 1;
  function applyViewOffset() {
    if (Math.abs(viewInset) < 0.5) camera.clearViewOffset();
    else camera.setViewOffset(viewW, viewH, -viewInset, 0, viewW, viewH);
  }
  function measureInset() {
    try {
      if (!active || active.kind === 'opening' || viewW < 900) return 0;
      const body = document.body;
      if (!body.classList.contains('view-journey') || body.classList.contains('research-focus')) return 0;
      const host = document.getElementById('stage-ui');
      const cp = host && host.querySelector('.cp');
      const panel = host && host.querySelector('.cp-panel');
      if (!panel || (cp && cp.classList.contains('is-min'))) return 0;
      const r = panel.getBoundingClientRect();
      const cr = canvas.getBoundingClientRect();
      if (r.width < 40 || r.height < 40 || r.right <= cr.left) return 0;
      const right = Math.min(r.right - cr.left, viewW * 0.45);
      return right > viewW * 0.12 ? right / 2 : 0;
    } catch (e) { return 0; }
  }
  function updateViewOffset(dt) {
    insetPoll += dt;
    if (insetPoll >= 0.25) { insetPoll = 0; viewInsetTarget = measureInset(); }
    if (viewInset === viewInsetTarget) return;
    viewInset += (viewInsetTarget - viewInset) * damp(isReduced() ? 20 : 4, dt);
    if (Math.abs(viewInset - viewInsetTarget) < 0.5) viewInset = viewInsetTarget;
    applyViewOffset();
  }

  function setShadowFlags(root, on) {
    root.traverse((o) => {
      if (!(o.isMesh || o.isInstancedMesh)) return;
      const transparent = Array.isArray(o.material) ? o.material.some((m) => m.transparent) : o.material && o.material.transparent;
      o.castShadow = !!on && !o.userData.noCastShadow && !transparent;
      o.receiveShadow = !!on;
    });
  }
  function refreshMaterials(root) {
    root.traverse((o) => { const ms = Array.isArray(o.material) ? o.material : o.material ? [o.material] : []; for (const m of ms) m.needsUpdate = true; });
  }

  function allBundles() { return [...cache.values()]; }

  function applyQuality() {
    const q = Q();
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, q.dpr));
    resize();
    const changed = renderer.shadowMap.enabled !== q.shadows;
    renderer.shadowMap.enabled = q.shadows;
    library.setQuality(effQuality);
    motes.setQuality(effQuality);
    for (const b of allBundles()) {
      b.lights.setShadows(q.shadows);
      b.particles.setQuality(effQuality);
      if (changed) { setShadowFlags(b.scene, q.shadows); refreshMaterials(b.scene); }
    }
  }

  // ================================================================ terrain
  function buildTerrain(loc, palette) {
    const rand = seededRandom('terrain|' + (loc.id || 'void'));
    const { noise, fbm } = makeNoise2D(rand);
    const relief = KIND_RELIEF[loc.kind] ?? 5;
    // Flat stage disc (r < 16) and flat architecture band (r < 60); relief rises toward the horizon.
    const heightAt = (x, z) => {
      const r = Math.hypot(x, z);
      let h = 0;
      const m = smooth(60, 190, r);
      if (m > 0) {
        const n = fbm(x * 0.010, z * 0.010);
        const ridge = 1 - Math.abs(noise(x * 0.0042 + 11.3, z * 0.0042 - 7.1));
        h += (n * 0.65 + ridge * ridge * 0.8) * relief * m + m * m * relief * 0.5;
      }
      const m2 = smooth(16, 60, r);
      if (m2 > 0) h += Math.abs(noise(x * 0.06 + 3.1, z * 0.06 + 1.7)) * 0.28 * m2; // never below 0 near buildings
      return h;
    };
    const seg = Q().terrainSeg;
    const geo = new THREE.PlaneGeometry(TERRAIN_SIZE, TERRAIN_SIZE, seg, seg);
    geo.rotateX(-Math.PI / 2);
    const pos = geo.attributes.position;
    const cols = new Float32Array(pos.count * 3);
    const ground = color(palette.ground), dark = ground.clone().multiplyScalar(0.6);
    const light = ground.clone().lerp(color(palette.accent), 0.1).multiplyScalar(1.12);
    const haze = color(palette.fog);
    const snow = color('#e6eaf0');
    const c = new THREE.Color();
    for (let i = 0; i < pos.count; i++) {
      const x = pos.getX(i), z = pos.getZ(i);
      const h = heightAt(x, z);
      pos.setY(i, h);
      const t = clamp(0.5 + fbm(x * 0.03 + 5.0, z * 0.03 - 2.0) * 0.9, 0, 1);
      c.copy(dark).lerp(light, t);
      const r = Math.hypot(x, z);
      c.lerp(haze, smooth(220, 650, r) * 0.35);
      if (loc.kind === 'mountain') c.lerp(snow, smooth(relief * 0.9, relief * 1.5, h));
      cols[i * 3] = c.r; cols[i * 3 + 1] = c.g; cols[i * 3 + 2] = c.b;
    }
    geo.setAttribute('color', new THREE.BufferAttribute(cols, 3));
    geo.computeVertexNormals();
    const mat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.96, metalness: 0 });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.name = 'terrain';
    mesh.userData.noCastShadow = true;
    return { mesh, heightAt };
  }

  // ================================================================ base layout
  async function placeLayoutItem(b, loc, it, i) {
    if (!it || typeof it.asset !== 'string') return;
    const n = Math.floor(num(it.instances, 1));
    if (n > 1) { await scatterItem(b, loc, it, i, n); return; }
    const obj = await library.instance(it.asset, { position: it.position, rotation: it.rotation, scale: it.scale, seed: `${loc.id}|${i}` });
    if (b.disposed) { disposeObject3D(obj, caches); return; }
    obj.position.y += b.heightAt(obj.position.x, obj.position.z);
    obj.userData.base = { pos: obj.position.clone(), quat: obj.quaternion.clone(), scale: obj.scale.clone() };
    b.layout.add(obj);
    b.layoutObjects.push(obj);
    b.layoutAssets.add(it.asset);
    collectUpdaters(obj, b.updaters);
  }

  // instances + scatter_radius -> deterministic seeded scatter; InstancedMesh when the model allows it.
  async function scatterItem(b, loc, it, i, n) {
    const rnd = seededRandom(`${loc.id}|scatter|${i}|${it.asset}`);
    const count = Math.max(1, Math.round(n * Q().scatter));
    const proto = await library.instance(it.asset, { seed: `${loc.id}|${i}` });
    if (b.disposed) { disposeObject3D(proto, caches); return; }
    b.layoutAssets.add(it.asset);
    const center = vec3(it.position);
    const R = Math.max(1, num(it.scatter_radius, 20));
    const rot = isVec3(it.rotation) ? it.rotation.map((v) => num(v) * DEG) : [0, 0, 0];
    const sc = Array.isArray(it.scale) && it.scale.length >= 3 ? new THREE.Vector3(num(it.scale[0], 1), num(it.scale[1], 1), num(it.scale[2], 1))
      : new THREE.Vector3().setScalar(num(it.scale, 1) > 0 ? num(it.scale, 1) : 1);
    const mats = [];
    const p = new THREE.Vector3(), q = new THREE.Quaternion(), e = new THREE.Euler(), s = new THREE.Vector3();
    const minR = STAGE_RADIUS + 2;
    for (let k = 0; k < count; k++) {
      let x = center.x, z = center.z, tries = 0;
      do {
        const a = rnd() * TAU, rr = Math.sqrt(rnd()) * R;
        x = center.x + Math.cos(a) * rr; z = center.z + Math.sin(a) * rr; tries++;
      } while (Math.hypot(x, z) < minR && tries < 8);
      const d = Math.hypot(x, z);
      if (d < minR) { const f = (minR + rnd() * 4) / (d || 1); x = (d ? x : 1) * f; z *= f; } // keep the stage clear
      p.set(x, b.heightAt(x, z) + center.y, z);
      e.set(rot[0], rot[1] + rnd() * TAU, rot[2]);
      q.setFromEuler(e);
      s.copy(sc).multiplyScalar(0.8 + rnd() * 0.4);
      mats.push(new THREE.Matrix4().compose(p, q, s));
    }
    proto.updateMatrixWorld(true);
    const parts = []; // Mesh and InstancedMesh (factories often instance trees/soldiers themselves)
    let instanceable = library.entry(it.asset)?.instancing !== false;
    let hasLights = false;
    proto.traverse((o) => {
      if (o === proto) return;
      if (o.isLight) { hasLights = true; instanceable = false; }
      else if (o.isSkinnedMesh || o.isPoints || o.isLine || o.isSprite) instanceable = false;
      else if (o.isMesh && visibleChain(o)) parts.push(o);
    });
    let total = 0;
    for (const m of parts) total += (m.isInstancedMesh ? m.count : 1) * count;
    if (!parts.length || parts.length > 32 || total > 60000) instanceable = false;
    const group = new THREE.Group();
    group.name = 'scatter:' + it.asset;
    group.userData.assetId = it.asset;
    if (instanceable) {
      // One InstancedMesh per part; nested instances are flattened (scatter matrix × part × instance).
      const m = new THREE.Matrix4(), inner = new THREE.Matrix4(), col = new THREE.Color();
      for (const mesh of parts) {
        const per = mesh.isInstancedMesh ? mesh.count : 1;
        const im = new THREE.InstancedMesh(mesh.geometry, mesh.material, per * count);
        let n2 = 0;
        for (let k = 0; k < count; k++) {
          for (let j = 0; j < per; j++) {
            m.multiplyMatrices(mats[k], mesh.matrixWorld);
            if (mesh.isInstancedMesh) { mesh.getMatrixAt(j, inner); m.multiply(inner); }
            im.setMatrixAt(n2, m);
            if (mesh.isInstancedMesh && mesh.instanceColor) { mesh.getColorAt(j, col); im.setColorAt(n2, col); }
            n2++;
          }
        }
        im.instanceMatrix.needsUpdate = true;
        if (im.instanceColor) im.instanceColor.needsUpdate = true;
        im.computeBoundingSphere();
        im.name = mesh.name || 'instanced';
        im.userData.noCastShadow = !!mesh.userData.noCastShadow;
        group.add(im);
      }
    } else {
      const placeholder = proto.userData.status === 'failed';
      const lim = Math.min(count, placeholder ? 3 : hasLights ? 6 : 48);
      for (let k = 0; k < lim; k++) {
        const c = proto.clone(true);
        mats[k].decompose(c.position, c.quaternion, c.scale);
        group.add(c);
      }
    }
    // Clones/instances lose userData functions; the prototype's self-animation still drives the
    // shared materials (water flow, flame shaders), so keep it ticking off-scene.
    collectUpdaters(proto, b.updaters);
    b.protos.push(proto); // freed with the scene (parts not reused by the instances/clones)
    b.layout.add(group);
  }

  // ================================================================ location scene
  async function buildLocationScene(loc) {
    const palette = validPalette(loc.palette);
    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(color(palette.fog), KIND_FOG[loc.kind] ?? 0.0065);
    const sky = new SkyDome();
    scene.add(sky.mesh);
    const lights = new LightRig(scene, { shadows: Q().shadows });
    const { mesh: terrain, heightAt } = buildTerrain(loc, palette);
    scene.add(terrain);
    const layout = new THREE.Group(); layout.name = 'layout';
    const stage = new THREE.Group(); stage.name = 'stage';
    const env = new THREE.Group(); env.name = 'env';
    scene.add(layout, stage, env);
    const particles = new ParticleManager(scene, { quality: effQuality, px: renderer.getPixelRatio() });
    const b = {
      kind: 'location', key: loc.id || '__void', loc, palette, scene, sky, lights, terrain, heightAt,
      layout, stage, env, particles, layoutObjects: [], layoutAssets: new Set(),
      staged: new Map(), stageByAsset: new Map(), stageObjects: [], stageChapter: null,
      envObjs: new Map(), defaultFog: KIND_FOG[loc.kind] ?? 0.0065,
      fogTarget: { color: color(palette.fog), density: KIND_FOG[loc.kind] ?? 0.0065 },
      fogColor: color(palette.fog), // un-desaturated fog colour (mood desaturation is applied on top)
      updaters: [], stageUpdaters: [], protos: [],
      disposed: false,
    };
    // initial look from the location defaults (first shot refines it)
    applyShotEnvironment(b, null, true);
    const items = Array.isArray(loc.base_layout) ? loc.base_layout : [];
    await Promise.all(items.map((it, i) => placeLayoutItem(b, loc, it, i).catch((e) => console.warn('[world] layout item failed', it && it.asset, e))));
    setShadowFlags(scene, Q().shadows);
    return b;
  }

  function disposeBundle(b) {
    if (!b || b.disposed) return;
    b.disposed = true;
    if (motes.points.parent === b.scene) b.scene.remove(motes.points);
    if (moodGlow.parent === b.scene) b.scene.remove(moodGlow);
    b.particles.dispose();
    b.sky.dispose();
    b.lights.dispose();
    disposeObject3D(b.scene, caches);
    for (const p of b.protos) disposeObject3D(p, caches);
    b.protos.length = 0; b.updaters.length = 0; b.stageUpdaters.length = 0;
    b.scene.clear();
  }

  function pruneCaches() {
    const live = new Set();
    for (const b of cache.values()) collectResources(b.scene, live);
    if (opening) collectResources(opening.scene, live);
    geometries.prune(live);
    materials.prune(live);
  }

  function evict() {
    let evicted = false;
    for (const [k, b] of [...cache]) {
      if (cache.size <= MAX_SCENES) break;
      if (b === active) continue;
      cache.delete(k);
      disposeBundle(b);
      evicted = true;
    }
    if (evicted) pruneCaches();
  }

  async function getScene(loc) {
    const key = loc.id || '__void';
    if (cache.has(key)) { const b = cache.get(key); cache.delete(key); cache.set(key, b); return b; }
    if (building.has(key)) return building.get(key);
    const p = buildLocationScene(loc).then((b) => {
      building.delete(key);
      if (disposed) { disposeBundle(b); pruneCaches(); throw new Error('world disposed'); } // never cache after dispose
      cache.set(key, b); evict(); return b;
    }, (e) => { building.delete(key); throw e; });
    building.set(key, p);
    return p;
  }

  // ================================================================ staging
  function register(map, key, obj) { if (!key) return; if (!map.has(key)) map.set(key, []); map.get(key).push(obj); }

  // Two phases so concurrent chapter requests never mix their staging, and the scene on screen
  // keeps its figures until the veil has covered it:
  //   prepareStaging(chapter) -> detached objects (async, no scene mutation)
  //   commitStaging(b, chapter, objs) -> swap them in (sync, only by the current request)
  async function prepareStaging(chapter) {
    const items = Array.isArray(chapter.staging) ? chapter.staging : [];
    const objs = await Promise.all(items.map(async (s, i) => {
      if (!s || typeof s.asset !== 'string') return null;
      try {
        const obj = await library.instance(s.asset, {
          position: s.position, rotation: s.rotation, scale: s.scale, pose: s.pose, seed: s.id || String(i),
        });
        obj.userData.stagingId = typeof s.id === 'string' && s.id ? s.id : `${s.asset}#${i}`;
        return obj;
      } catch (e) { console.warn('[world] staging failed', s.asset, e); return null; }
    }));
    return objs.filter(Boolean);
  }
  function discardStaging(objs) { if (objs) for (const o of objs) disposeObject3D(o, caches); }
  function commitStaging(b, chapter, objs) {
    if (b.disposed) { discardStaging(objs); return; }
    for (const child of [...b.stage.children]) { b.stage.remove(child); disposeObject3D(child, caches); }
    b.staged = new Map(); b.stageByAsset = new Map(); b.stageObjects = [];
    for (const obj of objs) {
      b.stage.add(obj);
      b.staged.set(obj.userData.stagingId, obj);
      register(b.stageByAsset, obj.userData.assetId, obj);
      b.stageObjects.push(obj);
    }
    b.stageUpdaters = [];
    for (const obj of objs) collectUpdaters(obj, b.stageUpdaters);
    setShadowFlags(b.stage, Q().shadows);
    b.stageChapter = chapter.id;
  }

  function stageApi(b) {
    const layoutByAsset = new Map();
    for (const o of b.layoutObjects) register(layoutByAsset, o.userData.assetId, o);
    return {
      objects: [...b.stageObjects, ...b.layoutObjects],
      resolve(target) {
        if (typeof target !== 'string') return null;
        return b.staged.get(target)
          || (b.stageByAsset.get(target) || [])[0]
          || (b.stageByAsset.get('char.' + target) || [])[0]
          || (layoutByAsset.get(target) || [])[0]
          || null;
      },
    };
  }

  // ================================================================ shot environment
  function applyEnvElements(b, shot) {
    const els = (shot && shot.environment && Array.isArray(shot.environment.elements)) ? shot.environment.elements : [];
    const extra = [];
    const wanted = new Set();
    for (const id of els) {
      if (typeof id !== 'string' || id.startsWith('env.sky')) continue;
      if (ENV_PARTICLES[id]) { extra.push({ type: ENV_PARTICLES[id], density: 0.5 }); continue; }
      if (id === 'env.fire') extra.push({ type: 'embers', density: 0.4 });
      if (b.layoutAssets.has(id)) continue; // already part of the location
      const entry = library.entry(id);
      if (entry && entry.fallback && ENV_SKIP_FACTORIES.has(entry.fallback.factory) && !entry.glb) continue; // engine provides ground/sky
      wanted.add(id);
    }
    for (const [id, rec] of b.envObjs) { rec.want = wanted.has(id); if (rec.obj) rec.obj.visible = rec.want; }
    for (const id of wanted) {
      if (b.envObjs.has(id)) continue;
      const rec = { obj: null, want: true };
      b.envObjs.set(id, rec);
      const place = envPlacement(id, library.entry(id));
      library.instance(id, { ...place, seed: b.key + '|' + id }).then((obj) => {
        if (b.disposed) { disposeObject3D(obj, caches); return; }
        obj.position.y += b.heightAt(obj.position.x, obj.position.z);
        rec.obj = obj; obj.visible = rec.want;
        setShadowFlags(obj, Q().shadows);
        b.env.add(obj);
        collectUpdaters(obj, b.updaters);
      }).catch((e) => console.warn('[world] env element failed', id, e));
    }
    return extra;
  }

  function applyShotEnvironment(b, shot, instant) {
    const dl = (b.loc.default_lighting && typeof b.loc.default_lighting === 'object') ? b.loc.default_lighting : {};
    const L = (shot && shot.lighting && typeof shot.lighting === 'object') ? shot.lighting : {};
    const preset = LIGHT_PRESETS[L.preset] ? L.preset : (LIGHT_PRESETS[dl.preset] ? dl.preset : 'day');
    b.lights.apply({
      preset,
      intensity: L.intensity ?? dl.intensity ?? 1,
      color: isHex(L.color) ? L.color : dl.color,
      key_direction: L.key_direction,
    }, instant);
    const sc = skyColorsFor(b.palette, shot && shot.environment && shot.environment.sky, preset);
    b.sky.set(sc, b.lights.tgt.keyDir, instant);
    b.fogTarget.color.copy(sc.horizon);
    const fd = shot && shot.environment ? num(shot.environment.fog_density, b.defaultFog) : b.defaultFog;
    // Authored densities are read as "how hazy", capped so the shot's subject stays legible:
    // FogExp2 visibility at distance d is exp(-(density*d)^2); keep >= ~55% at the widest framing.
    const reach = shotReach(shot);
    const legible = reach > 0 ? 0.77 / Math.max(reach, 12) : 0.05;
    b.fogTarget.density = clamp(Math.min(fd, legible), 0.0022, 0.05); // floor keeps the far terrain edge hidden
    if (instant) {
      b.fogColor.copy(b.fogTarget.color);
      desaturate(b.scene.fog.color.copy(b.fogColor), moodCur.desat * 0.75);
      b.scene.fog.density = b.fogTarget.density * moodCur.fog;
    }
    const extra = applyEnvElements(b, shot);
    const fx = (shot && Array.isArray(shot.particle_effects)) ? shot.particle_effects.slice() : [];
    for (const e of extra) if (!fx.some((f) => f && f.type === e.type)) fx.push(e);
    b.particles.setEffects(fx, instant);
  }

  // ================================================================ director
  const director = new Director({
    camera,
    isReducedMotion: isReduced,
    onShotStart: (shot, index, { instant }) => { if (active && active.kind === 'location') applyShotEnvironment(active, shot, instant); },
    onShotChange: (index, shot) => {
      emit('shot:change', { chapterId: currentChapter ? currentChapter.id : null, index, count: director.shots.length, shot });
      emitHotspots();
    },
    transition: (type, swap) => shotTransition(type, swap),
    sound: (cue, volume) => emit('audio:cue', volume != null ? { cue, volume } : { cue }),
  });

  async function shotTransition(type, swap) {
    if (type === 'dissolve') return dissolve(swap, isReduced() ? 1.6 : 1.1);
    if (chapterTransitioning || veil.state !== 'clear') { swap(); return; }
    const tok = chapterToken;
    const reduced = isReduced();
    await veil.cover(type, { duration: 0.55, reduced, color: currentChapter && currentChapter.transition && currentChapter.transition.color });
    if (tok !== chapterToken) return; // a chapter change now owns the veil
    swap();
    await nextFrame();
    if (tok !== chapterToken || chapterTransitioning) return;
    await veil.reveal(type, { duration: 0.7, reduced });
  }

  // True cross-fade: snapshot the last rendered frame onto a 2D overlay, swap, fade the overlay out.
  function ensureDissolveEl() {
    if (dissolveEl) return dissolveEl;
    dissolveEl = document.createElement('canvas');
    dissolveEl.className = 'world-dissolve';
    dissolveEl.setAttribute('aria-hidden', 'true');
    Object.assign(dissolveEl.style, { position: 'fixed', inset: '0', width: '100%', height: '100%', pointerEvents: 'none', opacity: '0', zIndex: '1' });
    (canvas.parentNode || document.body).insertBefore(dissolveEl, canvas.nextSibling);
    return dissolveEl;
  }
  function dissolve(swap, seconds) {
    if (!raf || !active) { swap(); return Promise.resolve(); }
    // A newer dissolve supersedes a pending one: perform the old swap now (its own token
    // guard decides whether it still applies) so no promise is left hanging.
    if (snapshotReq) { const prev = snapshotReq; snapshotReq = null; prev.cancel(); }
    return new Promise((resolve) => {
      let settled = false;
      const finish = () => { if (!settled) { settled = true; resolve(); } };
      snapshotReq = {
        cancel: () => { try { swap(); } finally { finish(); } },
        run: () => {
          const el = ensureDissolveEl();
          try {
            const w = Math.max(1, Math.round(canvas.clientWidth || window.innerWidth));
            const h = Math.max(1, Math.round(canvas.clientHeight || window.innerHeight));
            if (el.width !== w || el.height !== h) { el.width = w; el.height = h; }
            el.getContext('2d').drawImage(renderer.domElement, 0, 0, w, h);
          } catch (e) { swap(); finish(); return; }
          if (el._anim) { try { el._anim.cancel(); } catch (e) { /* noop */ } }
          el.style.opacity = '1';
          swap();
          if (typeof el.animate !== 'function') { el.style.opacity = '0'; finish(); return; }
          const a = el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: seconds * 1000, easing: 'cubic-bezier(.45,.05,.35,1)', fill: 'forwards' });
          el._anim = a;
          a.finished.then(() => { el.style.opacity = '0'; try { a.cancel(); } catch (e) { /* noop */ } finish(); }, finish);
        },
      };
    });
  }

  // ================================================================ activation / chapters
  function activate(b) {
    active = b;
    sinceSwap = 0;
    if (b.kind === 'location') {
      b.scene.add(motes.points);
      b.scene.add(moodGlow);
    }
    renderer.setClearColor(b.kind === 'location' ? b.scene.fog.color : new THREE.Color('#05061a'), 1);
  }

  async function compileScene(b) {
    if (typeof renderer.compileAsync !== 'function') return;
    try { await Promise.race([renderer.compileAsync(b.scene, camera), sleep(1500)]); } catch (e) { /* compile is an optimisation only */ }
  }

  function disposeOpening() {
    if (!opening || active === opening) return;
    const o = opening;
    opening = null;
    o.dispose();
    pruneCaches();
  }

  const DEFAULT_LOCATION = { id: '__void', kind: 'kingdom', palette: DEFAULT_PALETTE, base_layout: [], default_lighting: { preset: 'dusk' } };

  async function playChapter(chapterId, opts = {}) {
    if (disposed) return;
    const chapter = (V && V.chapterById && typeof V.chapterById.get === 'function' && V.chapterById.get(chapterId))
      || (V && Array.isArray(V.chapters) ? V.chapters.find((c) => c && c.id === chapterId) : null);
    if (!chapter) { console.warn('[world] unknown chapter', chapterId); return; }
    const tok = ++chapterToken;
    clearTimeout(holdVeilTimer);
    chapterTransitioning = true;
    const loc = (V && V.locationById && typeof V.locationById.get === 'function' && V.locationById.get(chapter.location_id))
      || { ...DEFAULT_LOCATION, id: chapter.location_id || '__void' };
    const tr = chapter.transition && typeof chapter.transition === 'object' ? chapter.transition : {};
    const reduced = isReduced();
    const instant = !!(opts && opts.instant);
    const inType = instant ? 'cut' : (VEIL_TYPES.includes(tr.in) ? tr.in : 'fade_black');
    const prevOut = currentChapter && currentChapter.transition && VEIL_TYPES.includes(currentChapter.transition.out) ? currentChapter.transition.out : null;
    const outType = instant ? 'cut' : (prevOut && prevOut !== 'cut' ? prevOut : inType);
    const dur = clamp(num(tr.duration, 1.6), 0.4, 4);
    const onScreen = !!active;
    // Prepared staging objects are owned here until committed; any superseded path frees them once.
    const held = { objs: null };
    const release = () => { if (held.objs) { discardStaging(held.objs); held.objs = null; } };
    const stale = () => tok !== chapterToken || disposed;
    try {
      // Build the location (cached) and this chapter's staging in parallel with the out-transition.
      const ready = (async () => {
        const b = await getScene(loc);
        if (b.stageChapter !== chapter.id) {
          const objs = await prepareStaging(chapter);
          if (stale()) discardStaging(objs); else held.objs = objs;
        }
        return b;
      })();
      ready.catch(() => {}); // handled below; avoid an unhandled rejection if we return early
      if (!onScreen) await veil.cover('cut');                    // nothing drawn yet: hold dark, reveal with style
      else if (outType !== 'cut') await veil.cover(outType, { duration: dur * 0.45, color: tr.color, reduced });
      if (stale()) { release(); return; }
      // Watchdog: a slow build (big location, slow factories) must not leave the UI hidden.
      let b = await Promise.race([ready, sleep(BUILD_WATCHDOG_MS).then(() => null)]);
      if (stale()) { release(); return; }
      if (!b) {
        console.warn('[world] scene build is slow; revealing while it finishes');
        await veil.reveal('fade_black', { duration: 0.6, reduced });
        b = await ready;
        if (stale()) { release(); return; }
        await veil.cover('fade_black', { duration: 0.6, reduced });
        if (stale()) { release(); return; }
      }
      if (!held.objs && b.stageChapter !== chapter.id) { // another chapter took this stage meanwhile
        const objs = await prepareStaging(chapter);
        if (stale()) { discardStaging(objs); return; }
        held.objs = objs;
      }
      if (held.objs) { const objs = held.objs; held.objs = null; commitStaging(b, chapter, objs); }
      activate(b);
      currentChapter = chapter;
      director.load(chapter, stageApi(b));
      director.start(0);
      if (!director.shots.length) applyShotEnvironment(b, null, true); // no shots: location defaults
      applyHighlight(highlightIds);
      await compileScene(b);
      if (tok !== chapterToken || disposed) return;
      disposeOpening();
      chapterTransitioning = false;
      const revealType = !onScreen ? (inType === 'cut' ? 'fade_black' : inType) : inType;
      await nextFrame();
      await veil.reveal(revealType, { duration: dur * 0.55, reduced });
    } catch (e) {
      release();
      if (disposed) return;
      console.error('[world] playChapter failed', e);
      if (tok === chapterToken) { chapterTransitioning = false; await veil.reveal('fade_black', { duration: 0.6 }); }
    }
  }

  // ================================================================ opening
  async function buildOpening() {
    const scene = new THREE.Scene();
    const sky = new SkyDome();
    sky.set({
      top: color('#05061c'), horizon: color('#191541'), bottom: color('#04050f'),
      sunColor: color('#E3C77E'), sun: 0, stars: 0, nebula: 0,
    }, new THREE.Vector3(0, 0.2, -1), true);
    sky.target.nebula = 0.9;
    scene.add(sky.mesh);
    const stars = buildOpeningStars(Q().stars);
    scene.add(stars.points);
    const mandala = buildMandala();
    // Pushed back and enlarged so it reads as a faint halo behind the overlay's title, not strokes through it.
    mandala.group.position.set(0, 0.3, -12);
    mandala.group.scale.setScalar(1.55);
    scene.add(mandala.group);
    const glow = makeGlowSprite(15);
    glow.position.set(0, 0.3, -12.3);
    scene.add(glow);

    const hemi = new THREE.HemisphereLight('#6f7fc9', '#0b0e22', 0);
    const rim = new THREE.DirectionalLight('#E3C77E', 0);
    rim.position.set(0, 3.2, -5);
    const key = new THREE.DirectionalLight('#9fb0ff', 0);
    key.position.set(-3.5, 2.6, 5);
    const warm = new THREE.PointLight('#ffcf8a', 0, 14, 2);
    warm.position.set(0.6, 1.3, 1.8);
    scene.add(hemi, rim, rim.target, key, key.target, warm);

    // Palm-leaf manuscript via the asset library; a local procedural stand-in if it cannot be built.
    // Fanned-open leaves with a warm glow: the inked leaf faces show instead of the dark wooden top board.
    let leaf = await library.instance('obj.palm_leaf', { seed: 'opening', params: { open: true, glow: true, leaves: 14 } });
    if (leaf.userData.status === 'failed') {
      disposeObject3D(leaf, caches);
      leaf = new THREE.Group();
      leaf.add(buildPalmLeafFallback(library.ctx()));
    }
    const holder = new THREE.Group();
    const box = new THREE.Box3().setFromObject(leaf);
    const size = box.getSize(new THREE.Vector3()), mid = box.getCenter(new THREE.Vector3());
    const s = 3.0 / Math.max(size.x, size.y, size.z, 1e-3);
    leaf.scale.multiplyScalar(s);
    leaf.position.copy(mid).multiplyScalar(-s);
    holder.add(leaf);
    // Own (cloned) materials so the leaf can fade in and keep a warm minimum glow without touching
    // the shared material cache used by chapter scenes. It must never render as a black silhouette.
    const leafMats = [];
    const LEAF_GLOW = new THREE.Color('#5a4220');
    leaf.traverse((o) => {
      if (!o.isMesh || !o.material || o.isSprite) return;
      const clone = (m) => {
        const c = m.clone();
        c.transparent = true;
        c.userData.baseOpacity = m.opacity ?? 1;
        if (c.emissive && c.emissive.getHex() === 0) { c.emissive.copy(LEAF_GLOW); c.emissiveIntensity = 0.3; }
        leafMats.push(c);
        return c;
      };
      o.material = Array.isArray(o.material) ? o.material.map(clone) : clone(o.material);
    });
    const flat = size.y <= size.x && size.y <= size.z;
    holder.userData.tilt = flat ? 0.6 : 0.12; // turn a lying manuscript's face toward the camera
    holder.rotation.x = holder.userData.tilt;
    // The opening overlay (ui/opening.js) centres its title, tagline and Begin button on screen; the
    // manuscript rests in the free band below the Begin button instead of behind the tagline.
    const LEAF_Y = -2.12; // clears the Begin pill with a gap on 16:10 and shorter viewports
    holder.position.set(0, LEAF_Y - 0.35, 0);
    warm.position.set(0.4, LEAF_Y + 1.2, 1.6); // keeps the leaf's face lit from the viewer's side at its new height
    scene.add(holder);
    const leafUpdaters = collectUpdaters(leaf);

    const o = {
      kind: 'opening', scene, sky, stars, mandala, glow, holder, t: 0, zoom: null, zoomPromise: null,
      update(dt) {
        const R = isReduced();
        o.t += dt;
        const t = o.t;
        const starsP = R ? smooth(0, 2.5, t) : clamp(t / 3.5, 0, 1);
        const mandP = R ? 1 : EASE.inOutSine(clamp((t - 1.2) / 8.3, 0, 1));
        const leafP = R ? smooth(0.8, 3.5, t) : EASE.inOutSine(clamp((t - 2.8) / 5.2, 0, 1));
        stars.uniforms.uProgress.value = starsP;
        stars.uniforms.uTime.value = t;
        stars.uniforms.uPx.value = renderer.getPixelRatio();
        mandala.setProgress(mandP);
        // Dims once the overlay's title is revealed so its strokes do not compete with the type.
        mandala.uniforms.uOpacity.value = (R ? 0.85 * smooth(0.5, 3, t) : 0.85) * (1 - 0.55 * smooth(4, 8, t));
        if (!R) mandala.spin(dt, 1);
        hemi.intensity = 0.3 + 0.2 * leafP;
        rim.intensity = 2.8 * leafP;
        key.intensity = 0.2 + 0.45 * leafP;
        warm.intensity = 2 + 12 * leafP;
        // Fade the manuscript in with its light (it is never shown as an unlit silhouette).
        const leafA = smooth(0, 0.45, leafP);
        holder.visible = leafA > 0.01;
        for (const m of leafMats) {
          const a = m.userData.baseOpacity * leafA;
          if (m.opacity !== a) m.opacity = a;
        }
        holder.position.y = LEAF_Y + (R ? 0 : -0.35 + 0.35 * leafP);
        // Fit narrow (portrait) screens: ~85% of the visible width at the manuscript's distance.
        const fitW = 2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * Math.max(1, camera.position.z) * camera.aspect * 0.85;
        holder.scale.setScalar(Math.min(0.62, fitW / 3.0));
        holder.rotation.y = R ? 0 : -0.3 * (1 - leafP) + Math.sin(t * 0.25) * 0.035 * leafP;
        let boost = 0;
        let glowOp = 0.32 * smooth(4, 10, t) + (R ? 0.2 * smooth(0.5, 3, t) : 0);
        const z = o.zoom;
        if (z) {
          z.t += dt;
          const p = clamp(z.t / z.dur, 0, 1);
          const e = EASE.inOutCubic(p);
          z.curve.getPointAt(e, camera.position);
          z.tcurve.getPointAt(e, z.look);
          camera.lookAt(z.look);
          const fov = 38 - 8 * e;
          if (Math.abs(camera.fov - fov) > 1e-3) { camera.fov = fov; camera.updateProjectionMatrix(); }
          boost = p * 1.4;
          glowOp += p * 0.6;
          glow.scale.setScalar(15 + 20 * e);
          if (p > 0.7 && !z.covering) { z.covering = true; veil.cover('fade_gold', { duration: z.dur * 0.32 }).then(z.resolve); }
        } else if (!R) {
          const k = EASE.inOutSine(clamp(t / 16, 0, 1));
          camera.position.set(0, 0.55 - 0.1 * k, 9.5 - 1.7 * k);
          camera.lookAt(0, 0.1, -0.6);
        }
        mandala.setBoost(boost);
        glow.material.opacity = Math.min(1, glowOp);
        sky.update(dt, t, camera);
        sky.setDesaturation(moodCur.desat * 0.75);
        if (leafUpdaters.length) runUpdaters(leafUpdaters, dt * (R ? 0.35 : 1), t * (R ? 0.35 : 1));
        renderer.toneMappingExposure = 1.0;
      },
      dispose() {
        sky.dispose();
        disposeObject3D(scene, caches);
        scene.clear();
      },
    };
    return o;
  }

  async function showOpening() {
    if (disposed) return;
    if (active && active.kind === 'location') return; // a chapter is already on screen (deep link)
    if (!opening) {
      // single build even if showOpening is called twice before it finishes
      if (!openingBuild) openingBuild = buildOpening().finally(() => { openingBuild = null; });
      const o = await openingBuild;
      if (!opening) opening = o; else if (opening !== o) o.dispose();
    }
    // A chapter (deep link / fast Begin) won the race, or the world was disposed: free the opening.
    if (disposed || (active && active.kind === 'location')) { disposeOpening(); return; }
    director.unload();
    camera.position.set(0, 0.55, 9.5);
    camera.fov = 38; camera.updateProjectionMatrix();
    camera.lookAt(0, 0.1, -0.6);
    opening.t = 0;
    activate(opening);
    await compileScene(opening);
    await nextFrame();
  }

  function openingZoom() {
    if (!opening || active !== opening) return Promise.resolve();
    if (opening.zoomPromise) return opening.zoomPromise;
    const o = opening;
    o.zoomPromise = new Promise((resolve) => {
      if (isReduced()) { veil.cover('fade_gold', { duration: 1.2, reduced: true }).then(resolve); return; }
      const look = new THREE.Vector3(0, 0.1, -0.6);
      o.zoom = {
        t: 0, dur: 3.4, covering: false, resolve, look: look.clone(),
        curve: new THREE.CatmullRomCurve3([camera.position.clone(), new THREE.Vector3(0, 1.0, 4.6), new THREE.Vector3(0, 0.66, 0.9), new THREE.Vector3(0, 0.32, -4.3)], false, 'centripetal'),
        tcurve: new THREE.CatmullRomCurve3([look, new THREE.Vector3(0, 0.05, -1.6), new THREE.Vector3(0, 0.2, -6), new THREE.Vector3(0, 0.3, -12)], false, 'centripetal'),
      };
    }).then(() => {
      // The first chapter normally reveals the veil; if none arrives, do not leave the screen covered.
      clearTimeout(holdVeilTimer);
      holdVeilTimer = setTimeout(() => {
        if (!chapterTransitioning && active === opening && veil.covered) veil.reveal('fade_gold', { duration: 1 });
      }, 6000);
    });
    return o.zoomPromise;
  }

  // ================================================================ mood & highlight
  function applyMood(m) {
    if (!MOODS[m]) return false;
    if (m === moodName) return false;
    moodName = m;
    if (m === 'error') errorTime = 0;
    return true;
  }
  function setMood(m) { if (applyMood(m)) emit('world:mood', { mood: m }); }

  function matchesIds(obj, ids) {
    const a = obj.userData.assetId, sid = obj.userData.stagingId;
    for (const id of ids) {
      if (id === a || id === sid || 'char.' + id === a) return true;
    }
    return false;
  }
  function restoreHighlight() {
    for (const h of highlighted) {
      h.mats.forEach((m, i) => { const sn = h.snap[i]; if (sn && m.emissive) { m.emissive.copy(sn.e); m.emissiveIntensity = sn.i; } });
    }
    highlighted = [];
  }
  function applyHighlight(ids) {
    highlightIds = Array.isArray(ids) ? ids.filter((x) => typeof x === 'string' && x) : [];
    restoreHighlight();
    if (!highlightIds.length || !active || active.kind !== 'location') return;
    for (const obj of [...active.stageObjects, ...active.layoutObjects]) {
      if (!matchesIds(obj, highlightIds)) continue;
      const mats = ensureOwnMaterials(obj);
      highlighted.push({ obj, mats, snap: mats.map((m) => (m.emissive ? { e: m.emissive.clone(), i: m.emissiveIntensity ?? 0 } : null)) });
    }
  }
  function highlight(ids) {
    const next = Array.isArray(ids) ? ids.filter((x) => typeof x === 'string' && x) : [];
    const same = next.length === highlightIds.length && next.every((x, i) => x === highlightIds[i]);
    applyHighlight(next);
    if (!same) emit('world:highlight', { ids: next });
  }
  const HL_GOLD = new THREE.Color('#E3C77E');
  const HL_TMP = new THREE.Color();
  function updateHighlight(t, reduced) {
    if (!highlighted.length) return;
    const pulse = reduced ? 0.6 : 0.5 + 0.5 * Math.sin(t * 2.4);
    for (const h of highlighted) {
      h.mats.forEach((m, i) => {
        const sn = h.snap[i];
        if (!sn || !m.emissive) return;
        // Keep the material's own glow exactly and add a modest gold on top (radiance, not a lerp that
        // would expose a large base intensity): a warm tint that keeps shading, never clipped to white.
        m.emissive.copy(sn.e).multiplyScalar(sn.i).add(HL_TMP.copy(HL_GOLD).multiplyScalar(0.1 + 0.14 * pulse));
        m.emissiveIntensity = 1;
      });
    }
  }

  // Procedural idle animations named in the registry (applied to the model inside each root,
  // so cue tweens on the root never fight them).
  function updateAmbient(b, dt, t, reduced) {
    if (reduced) return;
    for (const obj of b.stageObjects) {
      const names = obj.userData.animations;
      if (!names || !names.length || !obj.visible) continue;
      const inner = obj.children[0];
      if (!inner || inner.userData.selfAnimated) continue; // the factory animates it (userData.update)
      if (!inner.userData.amb) inner.userData.amb = { rx: inner.rotation.x, ry: inner.rotation.y, rz: inner.rotation.z, sy: inner.scale.y, phase: (obj.id % 17) * 0.7 };
      const a = inner.userData.amb;
      for (const n of names) {
        if (n === 'idle_sway') inner.rotation.z = a.rz + Math.sin(t * 0.6 + a.phase) * 0.012;
        else if (n === 'breathe') inner.scale.y = a.sy * (1 + Math.sin(t * 1.4 + a.phase) * 0.006);
        else if (n === 'rotate') inner.rotation.y += dt * 0.5;
        else if (n === 'flutter') inner.rotation.z = a.rz + Math.sin(t * 3.1 + a.phase) * 0.035;
        else if (n === 'flicker') {
          const f = 1 + (Math.sin(t * 13.1 + a.phase) * Math.sin(t * 7.3) * 0.5 + Math.sin(t * 21.7) * 0.5) * 0.03;
          inner.scale.set(inner.scale.x, a.sy * f, inner.scale.z);
          inner.traverse((o) => { if (o.isPointLight) { if (o.userData.baseI == null) o.userData.baseI = o.intensity; o.intensity = o.userData.baseI * (0.85 + 0.15 * f * Math.sin(t * 9 + a.phase)); } });
        }
      }
    }
  }

  // ================================================================ hotspots
  const raycaster = new THREE.Raycaster();
  raycaster.params.Line.threshold = 0.05;
  raycaster.params.Points.threshold = 0.05;
  const _hv = new THREE.Vector3(), _hc = new THREE.Vector3(), _hd = new THREE.Vector3(), _hq = new THREE.Vector3();
  function occluded(b, p) {
    const from = camera.position;
    _hd.copy(p).sub(from);
    const dist = _hd.length();
    for (let i = 1; i < 10; i++) { // terrain: cheap height sampling along the sight line
      _hq.copy(from).addScaledVector(_hd, i / 10);
      if (_hq.y < b.heightAt(_hq.x, _hq.z) - 0.2) return true;
    }
    if (dist < 2) return false;
    raycaster.set(from, _hd.normalize());
    raycaster.near = 0.1;
    raycaster.far = dist - 1.2; // ignore the subject the hotspot sits on
    raycaster.camera = camera;
    // Placed architecture only: scattered vegetation (thousands of instances) is skipped for cost.
    const hits = raycaster.intersectObjects(b.layoutObjects, true);
    // A hotspot placed on/in a building must not be hidden by that same building.
    return hits.some((h) => visibleChain(h.object) && !layoutRootBox(b, h.object).containsPoint(p));
  }
  function layoutRootBox(b, o) {
    let r = o;
    while (r.parent && r.parent !== b.layout) r = r.parent;
    if (!r.userData._occBox) r.userData._occBox = new THREE.Box3().setFromObject(r).expandByScalar(1.0);
    return r.userData._occBox;
  }
  function computeHotspots() {
    if (!active || active.kind !== 'location') return [];
    const spots = director.current().shot && director.current().shot.interactive_hotspots;
    if (!Array.isArray(spots) || !spots.length) return [];
    const rect = canvas.getBoundingClientRect();
    const w = rect.width || window.innerWidth, h = rect.height || window.innerHeight;
    const hidden = chapterTransitioning || veil.state !== 'clear';
    camera.updateMatrixWorld();
    const out = [];
    for (const s of spots) {
      if (!s || !isVec3(s.position)) continue;
      _hv.set(num(s.position[0]), num(s.position[1]), num(s.position[2]));
      _hc.copy(_hv).applyMatrix4(camera.matrixWorldInverse);
      const front = _hc.z < -0.1;
      _hq.copy(_hv).project(camera);
      const x = rect.left + (_hq.x * 0.5 + 0.5) * w;
      const y = rect.top + (-_hq.y * 0.5 + 0.5) * h;
      let visible = !hidden && front && x >= rect.left - 24 && x <= rect.left + w + 24 && y >= rect.top - 24 && y <= rect.top + h + 24;
      if (visible) { try { visible = !occluded(active, _hv); } catch (e) { /* keep visible */ } }
      out.push({ id: s.id, label: s.label, target_type: s.target_type, target: s.target, x: Math.round(x * 10) / 10, y: Math.round(y * 10) / 10, visible });
    }
    return out;
  }
  function emitHotspots() {
    if (!hotspotFns.size) return;
    const list = computeHotspots();
    for (const fn of hotspotFns) { try { fn(list); } catch (e) { console.warn('[world] hotspot listener failed', e); } }
  }

  // ================================================================ render loop
  function updateLocation(b, dt, t) {
    const reduced = isReduced();
    director.update(dt);
    b.sky.update(dt, t, camera);
    // 'error' mood desaturates inside the render (sky, fog, lights) — no CSS filter on the canvas,
    // which would override the shell's research-focus dimming filter.
    const ds = moodCur.desat * 0.75;
    b.sky.setDesaturation(ds);
    b.lights.update(dt, t, moodCur, reduced);
    const k = damp(1.2, dt);
    b.fogColor.lerp(b.fogTarget.color, k);
    desaturate(b.scene.fog.color.copy(b.fogColor), ds);
    b.scene.fog.density += (b.fogTarget.density * moodCur.fog - b.scene.fog.density) * k;
    renderer.setClearColor(b.scene.fog.color, 1);
    renderer.toneMappingExposure = b.lights.exposure * moodCur.exposure;
    particleTime += dt * (reduced ? 0.35 : 1);
    const adt = dt * (reduced ? 0.35 : 1);
    animTime += adt;
    runUpdaters(b.updaters, adt, animTime);
    runUpdaters(b.stageUpdaters, adt, animTime);
    const px = renderer.getPixelRatio();
    b.particles.update(dt, particleTime, { px });
    motes.show(moodCur.motes > 0.04);
    motes.uniforms.uOrigin.value.copy(director.target).y -= 3;
    motes.update(dt, particleTime, { px, gatherTarget: director.target, gather: moodCur.gather, opacityMul: Math.min(1, moodCur.motes) });
    moodGlow.position.copy(director.target);
    moodGlow.material.opacity = moodCur.glow * 0.3;
    moodGlow.visible = moodGlow.material.opacity > 0.003;
    updateAmbient(b, dt, t, reduced);
    updateHighlight(t, reduced);
    occAcc += dt;
    if (occAcc >= 0.15) { occAcc = 0; updateOccluders(b); }
  }

  // Staged objects that sit between the camera and its look target (or swallow the camera) are hidden
  // for as long as they block the shot, so close shots never fill the frame with a trunk or a figure's
  // inside. The subject (any object containing the look target) is never hidden. The model inside each
  // root is hidden, not the root, so cue tweens on root.visible keep working.
  let occAcc = 0;
  const OCC_BOX = new THREE.Box3(), OCC_S = new THREE.Sphere(), OCC_D = new THREE.Vector3(), OCC_C = new THREE.Vector3();
  function updateOccluders(b) {
    const cam = camera.position, tgt = director.target;
    OCC_D.copy(tgt).sub(cam);
    const D = OCC_D.length();
    if (D < 1e-3) return;
    OCC_D.divideScalar(D);
    for (const obj of [...b.stageObjects, ...b.layoutObjects]) {
      const inner = obj.children[0];
      if (!inner) continue;
      let block = false;
      if (obj.visible) {
        OCC_BOX.setFromObject(obj);
        if (!OCC_BOX.isEmpty()) {
          OCC_BOX.getBoundingSphere(OCC_S);
          const r = OCC_S.radius;
          if (r < 14 && !OCC_BOX.containsPoint(tgt) && OCC_S.center.distanceTo(tgt) > r * 0.8) {
            const camIn = OCC_BOX.containsPoint(cam) || OCC_S.center.distanceTo(cam) < r * 0.6 + camera.near;
            const sAlong = OCC_C.copy(OCC_S.center).sub(cam).dot(OCC_D);
            const perp = OCC_C.addScaledVector(OCC_D, -sAlong).length();
            block = camIn || (sAlong > 0 && sAlong < D * 0.7 && perp < r * 0.75);
          }
        }
      }
      const u = obj.userData;
      if (block) { u.occClear = 0; if (!u.occHidden) { u.occHidden = true; u.occWasVisible = inner.visible; inner.visible = false; } }
      else if (u.occHidden && ++u.occClear >= 2) { u.occHidden = false; inner.visible = u.occWasVisible !== false; }
    }
  }

  function updateMood(dt) {
    const target = { ...MOODS[moodName] };
    if (moodName === 'error') { errorTime += dt; target.desat = errorTime < 1.8 ? 1 : 0.2; } // desaturate briefly
    const k = damp(1.5, dt);
    for (const key of Object.keys(moodCur)) moodCur[key] += (target[key] - moodCur[key]) * k;
  }

  function frame() {
    raf = requestAnimationFrame(frame);
    const rawDt = clock.getDelta();
    const dt = Math.min(0.1, rawDt);
    elapsed += dt;
    sinceSwap += dt;
    updateMood(dt);
    const b = active;
    if (!b) { renderer.setClearColor(NIGHT, 1); renderer.clear(); return; }
    updateViewOffset(dt);
    // The opening is a timed reveal: follow wall-clock time (up to 0.5 s steps) so slow GPUs do not
    // stretch it out and leave the manuscript unrevealed when the visitor is ready to begin.
    if (b.kind === 'opening') b.update(Math.min(0.5, rawDt)); else updateLocation(b, dt, elapsed);
    renderer.render(b.scene, camera);
    if (snapshotReq) { const req = snapshotReq; snapshotReq = null; req.run(); }
    // fps + quality auto-downgrade (ignore the first seconds after a swap: shader compiles)
    if (dt > 0) fps += (1 / dt - fps) * 0.05;
    if (qualitySetting === 'auto' && effQuality !== 'low' && sinceSwap > 2.5) {
      lowFpsTime = fps < 30 ? lowFpsTime + dt : Math.max(0, lowFpsTime - dt * 0.5);
      if (lowFpsTime > 3) { effQuality = 'low'; lowFpsTime = 0; applyQuality(); if (window.__VOYAGE_DEBUG) console.info('[world] auto quality -> low'); }
    }
    hotspotAcc += dt;
    if (hotspotAcc >= 0.1) { hotspotAcc = 0; emitHotspots(); }
  }

  function startLoop() {
    if (raf || disposed || manualPause || contextLost || document.hidden) return;
    clock.getDelta(); // drop the paused interval
    raf = requestAnimationFrame(frame);
  }
  function stopLoop() { if (raf) cancelAnimationFrame(raf); raf = 0; }

  // ---- environment listeners
  const onVis = () => { if (document.hidden) stopLoop(); else startLoop(); };
  const onResize = () => resize();
  const onLost = (e) => { e.preventDefault(); contextLost = true; stopLoop(); };
  const onRestored = () => { contextLost = false; startLoop(); };
  document.addEventListener('visibilitychange', onVis);
  window.addEventListener('resize', onResize);
  canvas.addEventListener('webglcontextlost', onLost);
  canvas.addEventListener('webglcontextrestored', onRestored);
  let ro = null;
  if (typeof ResizeObserver === 'function') { ro = new ResizeObserver(() => resize()); ro.observe(canvas); }

  if (bus && typeof bus.on === 'function') {
    const on = (evt, fn) => { const u = bus.on(evt, fn); if (typeof u === 'function') unsubs.push(u); else unsubs.push(() => bus.off && bus.off(evt, fn)); };
    on('settings:change', (p) => {
      const q = p && p.settings && p.settings.quality;
      if (q && q !== qualitySetting) world.setQuality(q);
    });
    on('world:mood', (p) => { if (p && p.mood) applyMood(p.mood); });
    on('world:highlight', (p) => { if (p && Array.isArray(p.ids)) applyHighlight(p.ids); });
  }

  applyQuality();
  startLoop();

  // ================================================================ public API
  const world = {
    supported: true,
    showOpening: () => showOpening().catch((e) => console.error('[world] opening failed', e)),
    openingZoom,
    playChapter: (id, opts) => playChapter(id, opts),
    playShot(i) { if (active && active.kind === 'location') director.playShot(i); },
    nextShot() { if (active && active.kind === 'location') director.next(); },
    prevShot() { if (active && active.kind === 'location') director.prev(); },
    currentShot() { return director.current(); },
    setMood,
    highlight,
    setQuality(q) {
      const next = ['auto', 'low', 'medium', 'high'].includes(q) ? q : 'auto';
      // main.js re-sends the quality on every settings change (volume, motion…): an unchanged value
      // must not undo an auto downgrade or re-run the resize/shadow work.
      if (next === qualitySetting) return;
      qualitySetting = next;
      effQuality = qualitySetting === 'auto' ? 'medium' : qualitySetting;
      lowFpsTime = 0;
      applyQuality();
    },
    pause() { manualPause = true; stopLoop(); },
    resume() { manualPause = false; startLoop(); },
    onHotspots(fn) {
      if (typeof fn !== 'function') return () => {};
      hotspotFns.add(fn);
      try { fn(computeHotspots()); } catch (e) { /* listener error */ }
      return () => hotspotFns.delete(fn);
    },
    stats() {
      return {
        fps: Math.round(raf ? fps : 0),
        drawCalls: renderer.info.render.calls,
        triangles: renderer.info.render.triangles,
        scenesCached: cache.size,
        quality: effQuality,
      };
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      stopLoop();
      clearTimeout(holdVeilTimer);
      unsubs.forEach((u) => { try { u(); } catch (e) { /* noop */ } });
      document.removeEventListener('visibilitychange', onVis);
      window.removeEventListener('resize', onResize);
      canvas.removeEventListener('webglcontextlost', onLost);
      canvas.removeEventListener('webglcontextrestored', onRestored);
      if (ro) ro.disconnect();
      director.unload();
      if (snapshotReq) { const req = snapshotReq; snapshotReq = null; try { req.cancel(); } catch (e) { /* noop */ } }
      veil.clear(); // never leave the shell covered by a world that no longer renders
      active = null;
      for (const b of cache.values()) disposeBundle(b);
      cache.clear();
      if (opening) { opening.dispose(); opening = null; }
      motes.dispose();
      moodGlow.material.map && moodGlow.material.map.dispose();
      moodGlow.material.dispose();
      geometries.dispose();
      materials.dispose();
      if (dissolveEl && dissolveEl.parentNode) dissolveEl.parentNode.removeChild(dissolveEl);
      hotspotFns.clear();
      renderer.dispose();
    },
  };
  return world;
}
