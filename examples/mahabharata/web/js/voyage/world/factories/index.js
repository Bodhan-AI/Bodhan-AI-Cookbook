// Procedural factory registry for the voyage world.
// export const FACTORIES: { [name]: (params, ctx) => THREE.Object3D }, ctx = {THREE, materials, geometries, quality}
// Every factory is wrapped so it never throws: a failure returns a labelled placeholder obelisk (userData.error set).
// Each returned root may expose userData.update(dt, t) (dt clamped to 0.1 s); descendants' updates are hoisted into it.
// Roots are never transformed by the factories themselves, so the engine can set position/rotation/scale freely.
// Note: Object3D.clone() drops userData functions — build a fresh instance per placement (caches make that cheap).
// Optional ctx fields honoured when present (AssetLibrary passes them): spec (registry materials, used as a colour
// fallback), bounds ({size:[w,h,d]}: default footprint of environment / courtyard factories so they stay inside
// the registry's declared extent) and assetId (e.g. figure form for canonical character ids).
import * as THREE from 'three';
import { ARCHITECTURE } from './architecture.js';
import { FIGURES } from './figures.js';
import { OBJECTS } from './objects.js';
import { ENVIRONMENT } from './environment.js';
import { P, PAL, G, T, Batch, stone, goldM, labelTex, hoistUpdates } from './kit.js';

/** Unknown / failed asset: a small sandstone obelisk with a floating label. */
function placeholder(params, ctx) {
  const p = P(params);
  const label = String(p.label ?? p.assetId ?? p.asset_id ?? p.asset ?? p.id ?? p.name ?? 'unknown asset');
  const g = new THREE.Group();
  g.name = 'placeholder';
  g.userData.kind = 'placeholder';
  g.userData.label = label;
  const b = new Batch();
  b.box(ctx, stone(ctx, PAL.sandDark), 1.3, 0.2, 1.3, 0, 0.1, 0);
  b.box(ctx, stone(ctx, PAL.sandstone), 1.0, 0.16, 1.0, 0, 0.28, 0);
  b.add(G.frustum(ctx, 0.62, 4), stone(ctx, PAL.sandLight), T(0, 1.56, 0, 0, Math.PI / 4, 0, 0.8, 2.4, 0.8));
  b.add(G.cone(ctx, 4), goldM(ctx), T(0, 2.95, 0, 0, Math.PI / 4, 0, 0.5, 0.38, 0.5));
  b.build(g, ctx, 'placeholder:v1');
  const tex = labelTex(label);
  if (tex) {
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }));
    s.scale.set(2.6, 0.49, 1);
    s.position.y = 3.6;
    s.name = 'placeholder_label';
    g.add(s);
  }
  g.userData.anchors = { head: [0, 3.2, 0] };
  return g;
}

function safe(name, fn) {
  return function factory(params, ctx) {
    const c = ctx && typeof ctx === 'object' ? ctx : {};
    let prm = params && typeof params === 'object' && !Array.isArray(params) ? params : {};
    // AssetLibrary passes the registry entry's `materials` block as ctx.spec; expose it as params.materials
    // (a low-priority colour fallback read by kit.baseColor/accentColor) unless params bring their own.
    if (prm.materials === undefined && c.spec && typeof c.spec === 'object') prm = { ...prm, materials: c.spec };
    let obj;
    try {
      obj = fn(prm, c);
      if (!obj || !obj.isObject3D) throw new Error('factory returned no Object3D');
    } catch (err) {
      console.warn(`[voyage factories] "${name}" failed; using a placeholder.`, err);
      try { obj = placeholder({ label: `${name} (unavailable)` }, c); } catch (e) { obj = new THREE.Group(); }
      obj.userData.error = String((err && err.message) || err);
    }
    hoistUpdates(obj);
    const upd = obj.userData.update;
    if (typeof upd === 'function') {
      let warned = false;
      obj.userData.update = (dt, t) => {
        const d = Math.min(Math.max(Number(dt) || 0, 0), 0.1);
        try { upd(d, Number(t) || 0); } catch (e) {
          if (!warned) { warned = true; console.warn(`[voyage factories] "${name}" update failed`, e); }
        }
      };
    }
    obj.userData.factory = name;
    return obj;
  };
}

const RAW = { ...ARCHITECTURE, ...FIGURES, ...OBJECTS, ...ENVIRONMENT, placeholder };
export const FACTORIES = Object.freeze(Object.fromEntries(Object.entries(RAW).map(([k, f]) => [k, safe(k, f)])));
export const FACTORY_NAMES = Object.freeze(Object.keys(FACTORIES));
