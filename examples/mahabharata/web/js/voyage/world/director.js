// world/director.js — the camera shot runner.
//
// For each shot: Catmull-Rom (centripetal) spline through camera_path (+ optional target_path),
// eased per camera_movement, FOV tween, animation_cues timeline (transform/material tweens) and
// sound cue emission. Shots run in sequence; after the last one the camera settles into a slow
// idle drift. Reduced motion: no spline travel — each shot holds its first key frame, then
// dissolves (true cross-fade, provided by the engine) to its last key frame.
//
// Animation hook for factory authors: if a staged object (or its first child) has
// userData.actions[<action>] = (progress 0..1, params, obj) => void, the director calls it instead
// of the built-in transform tween for that action.
import * as THREE from 'three';
import { DEG, num, isVec3, vec3, color as toColor, ensureOwnMaterials, setOpacity, clamp01 } from './assets.js';

const TAU = Math.PI * 2;
export const EASE = {
  linear: (t) => t,
  inOutSine: (t) => -(Math.cos(Math.PI * t) - 1) / 2,
  inOutCubic: (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2),
  inOutQuart: (t) => (t < 0.5 ? 8 * t * t * t * t : 1 - Math.pow(-2 * t + 2, 4) / 2),
  smoother: (t) => t * t * t * (t * (t * 6 - 15) + 10),
  outCubic: (t) => 1 - Math.pow(1 - t, 3),
  outBack: (t) => { const c1 = 1.4, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); },
  // near-constant speed with soft ends (tracking / orbit)
  glide: (t) => 0.45 * t + 0.55 * (-(Math.cos(Math.PI * t) - 1) / 2),
};
const MOVE_EASE = {
  establishing: 'inOutSine', crane: 'inOutSine', reveal: 'inOutQuart', dolly: 'smoother',
  tracking: 'glide', orbit: 'glide', close_up: 'inOutCubic', transition: 'inOutCubic', static: 'inOutSine',
};
const VEIL_SHOT_TYPES = new Set(['fade_black', 'fade_gold', 'cloud_pass', 'manuscript_turn', 'door_open', 'mandala_iris', 'river_flow', 'path_glow', 'dust_wipe']);
const TRANSIENT = new Set(['flutter', 'pulse']);
const DEFAULT_TARGET = [0, 1.5, 0];

const shortest = (a) => { a = ((a + Math.PI) % TAU + TAU) % TAU - Math.PI; return a; };
const Y_AXIS = new THREE.Vector3(0, 1, 0);
const X_AXIS = new THREE.Vector3(1, 0, 0);
const Z_AXIS = new THREE.Vector3(0, 0, 1);
const _q = new THREE.Quaternion();
const _v = new THREE.Vector3();

/** Current world-space yaw of an object's forward axis (registry orientation.forward, default -z). */
function forwardOf(obj, quat = obj.quaternion) {
  const sign = obj.userData.forward === '+z' ? 1 : -1;
  return _v.set(0, 0, sign).applyQuaternion(quat).setY(0).normalize().clone();
}
function yawDelta(obj, quat, dx, dz) {
  const f = forwardOf(obj, quat);
  if (Math.hypot(dx, dz) < 1e-4 || f.lengthSq() < 1e-6) return 0;
  return shortest(Math.atan2(dx, dz) - Math.atan2(f.x, f.z));
}
function findChild(obj, re) {
  let hit = null;
  obj.traverse((o) => { if (!hit && o !== obj && re.test(o.name || '')) hit = o; });
  return hit;
}
/** First function-valued userData[key] on the object or its descendants (factory hooks such as setRolling). */
function findHook(obj, key) {
  let fn = null;
  obj.traverse((o) => { if (!fn && o.userData && typeof o.userData[key] === 'function') fn = o.userData[key]; });
  return fn;
}
const _box = new THREE.Box3();
const _size = new THREE.Vector3();

// ----------------------------------------------------------------- animation cue actions
// Each returns {channel, dur, apply(p), end?()} or null. Starting state is captured at trigger time.
function makeAction(name, obj, params, dir) {
  const P = params && typeof params === 'object' ? params : {};
  const dur = (d) => Math.max(0.05, num(P.duration, d));
  const reduced = dir.isReduced();
  const hook = (obj.userData.actions && obj.userData.actions[name])
    || (obj.children[0] && obj.children[0].userData && obj.children[0].userData.actions && obj.children[0].userData.actions[name]);
  if (typeof hook === 'function') {
    return { channel: 'hook:' + name, dur: dur(1.2), apply: (p) => { try { hook(p, P, obj); } catch (e) { /* factory hook failed: ignore */ } } };
  }
  const s = { pos: obj.position.clone(), quat: obj.quaternion.clone(), scale: obj.scale.clone() };
  const base = obj.userData.base || { scale: s.scale };

  switch (name) {
    case 'appear': {
      if (reduced) return makeAction('fade_in', obj, P, dir);
      const target = base.scale.clone();
      return {
        channel: 'transform', dur: dur(0.9),
        apply: (p) => { obj.visible = true; obj.scale.copy(target).multiplyScalar(Math.max(0.001, EASE.outBack(p))); },
      };
    }
    case 'fade_in': {
      const mats = ensureOwnMaterials(obj);
      return { channel: 'material', dur: dur(1.6), apply: (p) => { obj.visible = true; setOpacity(mats, EASE.inOutSine(p)); } };
    }
    case 'fade_out': {
      const mats = ensureOwnMaterials(obj);
      return {
        channel: 'material', dur: dur(1.6),
        apply: (p) => setOpacity(mats, 1 - EASE.inOutSine(p)),
        end: () => { obj.visible = false; setOpacity(mats, 1); },
      };
    }
    case 'rise': {
      // from: [x,y,z] start | from_y: start height (ends at the authored pose) | to: [x,y,z] end | height: lift
      const from = isVec3(P.from) ? vec3(P.from) : s.pos.clone();
      if (P.from_y != null && Number.isFinite(Number(P.from_y))) from.y = Number(P.from_y);
      const lift = P.height ?? P.distance ?? P.y;
      let to;
      if (isVec3(P.to)) to = vec3(P.to);
      else if (lift != null) to = from.clone().setY(from.y + num(lift, 1.2));
      else if (from.distanceToSquared(s.pos) > 1e-6) to = s.pos.clone();
      else to = from.clone().setY(from.y + 1.2);
      return { channel: 'transform', dur: dur(2.6), apply: (p) => { obj.visible = true; obj.position.lerpVectors(from, to, EASE.inOutSine(p)); } };
    }
    case 'turn_to_camera': {
      const cam = dir.camera.position;
      const d = yawDelta(obj, s.quat, cam.x - obj.position.x, cam.z - obj.position.z);
      return {
        channel: 'transform', dur: dur(1.5),
        apply: (p) => { obj.quaternion.copy(_q.setFromAxisAngle(Y_AXIS, d * EASE.inOutCubic(p))).multiply(s.quat); },
      };
    }
    case 'glow': {
      const mats = ensureOwnMaterials(obj);
      const col = toColor(P.color, '#E3C77E');
      const I = num(P.intensity, 1.4);
      const start = mats.map((m) => (m.emissive ? { e: m.emissive.clone(), i: m.emissiveIntensity ?? 0 } : null));
      return {
        channel: 'material', dur: dur(1.3),
        apply: (p) => {
          const e = EASE.inOutSine(p);
          mats.forEach((m, i) => { const st = start[i]; if (!st) return; m.emissive.copy(st.e).lerp(col, 0.9 * e); m.emissiveIntensity = st.i + (I - st.i) * e; });
        },
      };
    }
    case 'flutter': {
      if (reduced) return null;
      // amplitude in degrees, or strength 0..1 (data uses strength; 0.3 ≈ 6°)
      const amp = P.amplitude != null ? num(P.amplitude, 6) : (P.strength != null ? num(P.strength, 0.3) * 20 : 6);
      const A = Math.min(25, Math.max(0, amp)) * DEG, f = num(P.frequency, 1.6), D = dur(4);
      return {
        channel: 'transform', dur: D,
        apply: (p) => {
          const env = Math.sin(Math.PI * p), w = p * D * TAU * f;
          obj.quaternion.copy(s.quat)
            .multiply(_q.setFromAxisAngle(Z_AXIS, Math.sin(w) * A * env))
            .multiply(_q.setFromAxisAngle(X_AXIS, Math.sin(w * 0.7 + 1) * A * 0.4 * env));
        },
        end: () => obj.quaternion.copy(s.quat),
      };
    }
    case 'roll': {
      const hasDest = isVec3(P.to) || isVec3(P.from) || P.distance != null;
      if (!hasDest) {
        // No destination (e.g. the swayamvara fish target): spin in place about Y.
        const speed = num(P.speed, 0.6); // radians per second
        const D = dur(reduced ? 6 : 120);
        return {
          channel: 'transform', dur: D,
          apply: (p) => { obj.quaternion.copy(s.quat).multiply(_q.setFromAxisAngle(Y_AXIS, speed * (reduced ? 0.35 : 1) * p * D)); },
        };
      }
      const from = isVec3(P.from) ? vec3(P.from) : s.pos.clone();
      const dest = isVec3(P.to) ? vec3(P.to) : from.clone().addScaledVector(forwardOf(obj, s.quat), num(P.distance, 8));
      const dist = from.distanceTo(dest);
      const yaw = yawDelta(obj, s.quat, dest.x - from.x, dest.z - from.z);
      const turn = Math.abs(yaw) > 0.05 && Math.abs(yaw) < Math.PI * 0.9 ? yaw : 0; // never spin a chariot round
      // Factory hook (chariot/horse): let the model spin its own wheels / gallop while it travels.
      const setRolling = findHook(obj, 'setRolling') || findHook(obj, 'setGallop');
      const wheels = [];
      if (!setRolling) obj.traverse((o) => { if (o !== obj && /wheel/i.test(o.name || '')) wheels.push(o); });
      let last = 0, rolling = false;
      const stop = () => { if (rolling && setRolling) { try { setRolling(false); } catch (e) { /* hook optional */ } } rolling = false; };
      return {
        channel: 'transform', dur: dur(Math.max(2, dist / Math.max(0.5, num(P.speed, 4)))),
        apply: (p) => {
          obj.visible = true;
          if (setRolling && !rolling && p < 1) { rolling = true; try { setRolling(true); } catch (e) { /* hook optional */ } }
          const e = EASE.inOutSine(p), travelled = dist * e;
          if (turn) obj.quaternion.copy(_q.setFromAxisAngle(Y_AXIS, turn * EASE.inOutSine(clamp01(p / 0.2)))).multiply(s.quat);
          obj.position.lerpVectors(from, dest, e);
          obj.position.y += Math.abs(Math.sin(travelled * 2.2)) * 0.02 * (reduced ? 0 : 1);
          for (const w of wheels) w.rotation.x += (travelled - last) / 0.6;
          last = travelled;
          if (p >= 1) stop();
        },
        end: stop,
        cancel: stop,
      };
    }
    case 'draw_bow': {
      const bow = findChild(obj, /bow|gandiva/i);
      if (bow && bow.userData.baseRotZ == null) bow.userData.baseRotZ = bow.rotation.z;
      return {
        channel: 'transform', dur: dur(1.4),
        apply: (p) => {
          const e = EASE.inOutCubic(p);
          obj.quaternion.copy(s.quat).multiply(_q.setFromAxisAngle(X_AXIS, -4 * DEG * e));
          if (bow) bow.rotation.z = bow.userData.baseRotZ + 0.14 * e;
        },
      };
    }
    case 'walk_to': {
      const raw = P.to || P.position || P.target;
      if (!isVec3(raw)) return null;
      const dest = vec3(raw);
      const dx = dest.x - s.pos.x, dz = dest.z - s.pos.z, dist = s.pos.distanceTo(dest);
      const d = yawDelta(obj, s.quat, dx, dz);
      return {
        channel: 'transform', dur: dur(Math.max(1.5, dist / 1.3)),
        apply: (p) => {
          obj.quaternion.copy(_q.setFromAxisAngle(Y_AXIS, d * EASE.inOutSine(clamp01(p / 0.18)))).multiply(s.quat);
          const e = EASE.inOutSine(p);
          obj.position.lerpVectors(s.pos, dest, e);
          if (!reduced) obj.position.y += Math.abs(Math.sin(e * dist * Math.PI / 0.75)) * 0.04 * Math.sin(Math.PI * p);
        },
      };
    }
    case 'kneel': {
      const depth = num(P.depth, 0.32);
      return {
        channel: 'transform', dur: dur(1.3),
        apply: (p) => {
          const e = EASE.inOutCubic(p);
          obj.position.y = s.pos.y - depth * e;
          obj.quaternion.copy(s.quat).multiply(_q.setFromAxisAngle(X_AXIS, 9 * DEG * e));
        },
      };
    }
    case 'fall': {
      // Figures topple (default 84°); objects such as chariots lurch/sink (tilt 8°, `distance` = sink depth).
      // `onto`: a staging id/asset to come to rest on (e.g. Bhishma onto the bed of arrows).
      const figure = obj.userData.family === 'character' || /^char\./.test(obj.userData.assetId || '');
      const tilt = num(P.tilt, figure ? 84 : 8) * DEG;
      const axis = figure ? X_AXIS : Z_AXIS;
      const endQuat = s.quat.clone().multiply(_q.setFromAxisAngle(axis, -tilt));
      const end = s.pos.clone();
      if (P.distance != null && !figure) end.y -= num(P.distance, 0);
      else end.y += figure ? 0.12 : 0;
      const onto = typeof P.onto === 'string' && dir.stage ? dir.stage.resolve(P.onto) : null;
      if (onto && onto !== obj) {
        _box.setFromObject(onto);
        if (!_box.isEmpty()) {
          const top = _box.min.y + (_box.max.y - _box.min.y) * 0.55;
          const centre = _box.getCenter(new THREE.Vector3());
          // place the feet so the lying body is centred on the support
          const height = _box.setFromObject(obj).isEmpty() ? 1.8 : _box.getSize(_size).y;
          const along = new THREE.Vector3(0, 1, 0).applyQuaternion(endQuat).setY(0);
          end.set(centre.x, top, centre.z).addScaledVector(along, -height * 0.5);
        }
      }
      const startQuat = s.quat.clone();
      return {
        channel: 'transform', dur: dur(1.9),
        apply: (p) => {
          const e = EASE.inOutCubic(p);
          obj.quaternion.slerpQuaternions(startQuat, endQuat, e);
          obj.position.lerpVectors(s.pos, end, e);
        },
      };
    }
    case 'pulse': {
      if (reduced) return null;
      const mats = ensureOwnMaterials(obj);
      const D = dur(2.4);
      const start = mats.map((m) => (m.emissive ? { e: m.emissive.clone(), i: m.emissiveIntensity ?? 0 } : null));
      const gold = toColor(P.color, '#E3C77E');
      return {
        channel: 'pulse', dur: D,
        apply: (p) => {
          const w = Math.abs(Math.sin(p * D * TAU * 0.55)) * Math.sin(Math.PI * p);
          obj.scale.copy(s.scale).multiplyScalar(1 + 0.05 * w);
          mats.forEach((m, i) => { const st = start[i]; if (!st) return; m.emissive.copy(st.e).lerp(gold, w * 0.8); m.emissiveIntensity = st.i + 0.9 * w; });
        },
        end: () => {
          obj.scale.copy(s.scale);
          mats.forEach((m, i) => { const st = start[i]; if (!st) return; m.emissive.copy(st.e); m.emissiveIntensity = st.i; });
        },
      };
    }
    default:
      return null;
  }
}

// ----------------------------------------------------------------- Director
export class Director {
  /**
   * @param {object} o
   *   camera, isReducedMotion(): bool,
   *   onShotStart(shot, index, {instant}) — environment/lighting for the shot
   *   onShotChange(index, shot)           — UI notification
   *   transition(type, swap) -> Promise   — 'dissolve' or a veil type; must call swap()
   *   sound(cue, volume)
   */
  constructor(o = {}) {
    this.camera = o.camera;
    this.isReduced = o.isReducedMotion || (() => false);
    this.hooks = o;
    this.chapter = null;
    this.shots = [];
    this.stage = null;
    this.index = -1;
    this.run = null;
    this.state = 'idle'; // idle | shot | transition | drift
    this.tweens = [];
    this.time = 0;
    this.token = 0;
    this.lookTarget = new THREE.Vector3(...DEFAULT_TARGET);
    this.initialHidden = new Set();
    this._pos = new THREE.Vector3();
    this._tgt = new THREE.Vector3();
    this._qa = new THREE.Quaternion();
  }

  /** stage: {objects: Object3D[], resolve(target) -> Object3D|null} */
  load(chapter, stage) {
    this.unload();
    this.chapter = chapter || null;
    this.shots = Array.isArray(chapter && chapter.shots) ? chapter.shots.filter((s) => s && typeof s === 'object') : [];
    this.stage = stage || { objects: [], resolve: () => null };
    // Base pose is captured once per object (fresh staging, or at layout placement), so replaying
    // a chapter in a cached scene resets to the authored pose, not to where the last run ended.
    for (const obj of this.stage.objects) {
      if (!obj.userData.base) obj.userData.base = { pos: obj.position.clone(), quat: obj.quaternion.clone(), scale: obj.scale.clone() };
    }
    this.initialHidden = this._computeInitialHidden();
    this._resetStage();
  }

  /** Remove tweens (all, or those matching pred), letting each release factory hooks it started. */
  _dropTweens(pred) {
    const keep = [];
    for (const tw of this.tweens) {
      if (pred && !pred(tw)) { keep.push(tw); continue; }
      if (tw.act.cancel) { try { tw.act.cancel(); } catch (e) { /* noop */ } }
    }
    this.tweens = keep;
  }

  unload() {
    this.token++;
    this._dropTweens();
    this.run = null;
    this.state = 'idle';
    this.index = -1;
  }

  current() {
    return { index: this.index, count: this.shots.length, shot: this.index >= 0 ? this.shots[this.index] || null : null };
  }

  /** Begin the chapter at shot `index` with the camera placed instantly (veil hides the cut). */
  start(index = 0) {
    if (!this.shots.length) { this._noShots(); return; }
    this._jump(index, 'instant');
  }

  /** Jump to a shot (user navigation). */
  playShot(i) {
    if (!this.shots.length) return;
    const idx = Math.max(0, Math.min(this.shots.length - 1, Math.floor(num(i, 0))));
    const reduced = this.isReduced();
    const jump = this.camera.position.distanceTo(this._startPose(this.shots[idx]));
    if (reduced || jump > 40) {
      const tok = ++this.token;
      this.state = 'transition';
      this._transition('dissolve', () => { if (tok === this.token) this._jump(idx, 'instant'); });
    } else {
      this._jump(idx, 'blend');
    }
  }
  next() { if (this.index < this.shots.length - 1) this.playShot(this.index + 1); }
  prev() { if (this.shots.length) this.playShot(Math.max(0, this.index - 1)); }

  get target() { return this.lookTarget; }

  // ------------------------------------------------------------- internals
  _noShots() {
    // No shot data: frame the stage from a respectful distance and drift.
    this.camera.position.set(0, 7, 26);
    this.lookTarget.set(...DEFAULT_TARGET);
    this.camera.lookAt(this.lookTarget);
    this._enterDrift();
  }

  _transition(type, swap) {
    const t = this.hooks.transition;
    if (typeof t === 'function') {
      Promise.resolve(t(type, swap)).catch(() => swap());
    } else swap();
  }

  _computeInitialHidden() {
    const first = new Map();
    for (const shot of this.shots) {
      const cues = (Array.isArray(shot.animation_cues) ? shot.animation_cues : [])
        .filter((c) => c && typeof c.target === 'string').slice().sort((a, b) => num(a.at, 0) - num(b.at, 0));
      for (const c of cues) {
        const obj = this.stage.resolve(c.target);
        if (obj && !first.has(obj)) first.set(obj, c.action);
      }
    }
    const hidden = new Set();
    for (const [obj, action] of first) if (action === 'appear' || action === 'fade_in') hidden.add(obj);
    return hidden;
  }

  _resetStage() {
    for (const obj of this.stage.objects) {
      const b = obj.userData.base;
      if (b) { obj.position.copy(b.pos); obj.quaternion.copy(b.quat); obj.scale.copy(b.scale); }
      obj.visible = !this.initialHidden.has(obj);
      for (const m of obj.userData.ownMaterials || []) {
        const mb = m.userData.base;
        if (!mb) continue;
        m.opacity = mb.opacity;
        if (m.transparent !== mb.transparent) { m.transparent = mb.transparent; m.needsUpdate = true; }
        m.depthWrite = mb.depthWrite;
        if (m.emissive && mb.emissive) { m.emissive.copy(mb.emissive); m.emissiveIntensity = mb.emissiveIntensity; }
      }
    }
  }

  _jump(idx, mode) {
    this.token++;
    this._dropTweens();
    this._resetStage();
    // replay persistent cues of earlier shots so the stage matches the story so far
    for (let j = 0; j < idx; j++) for (const c of this._cuesOf(this.shots[j])) if (!TRANSIENT.has(c.action)) this._trigger(c, true);
    this._begin(idx, mode);
  }

  _cuesOf(shot) {
    return (Array.isArray(shot && shot.animation_cues) ? shot.animation_cues : [])
      .filter((c) => c && typeof c.target === 'string' && typeof c.action === 'string')
      .map((c) => ({ ...c, at: Math.max(0, num(c.at, 0)) }))
      .sort((a, b) => a.at - b.at);
  }

  _startPose(shot) {
    const path = Array.isArray(shot && shot.camera_path) ? shot.camera_path.filter(isVec3) : [];
    if (path.length) return vec3(path[0]);
    return isVec3(shot && shot.camera_position) ? vec3(shot.camera_position) : new THREE.Vector3(0, 6, 26);
  }

  _prepare(shot) {
    const pts = (Array.isArray(shot.camera_path) ? shot.camera_path : []).filter(isVec3).map((a) => vec3(a));
    const t0 = isVec3(shot.camera_target) ? vec3(shot.camera_target) : null;
    if (!pts.length) pts.push(isVec3(shot.camera_position) ? vec3(shot.camera_position) : new THREE.Vector3(0, 6, 26));
    if (pts.length === 1) {
      // a single key frame still breathes: a slow push toward the subject
      const p = pts[0], look = t0 || new THREE.Vector3(...DEFAULT_TARGET);
      pts.push(p.clone().lerp(look, shot.camera_movement === 'static' ? 0.015 : 0.06));
    }
    const makeCurve = (list) => {
      let len = 0;
      for (let i = 1; i < list.length; i++) len += list[i].distanceTo(list[i - 1]);
      if (len < 1e-3) { const p = list[0].clone(); return (e, out) => out.copy(p); }
      const c = new THREE.CatmullRomCurve3(list, false, 'centripetal', 0.5);
      c.arcLengthDivisions = 400;
      return (e, out) => c.getPointAt(clamp01(e), out);
    };
    const posAt = makeCurve(pts);
    const tpts = (Array.isArray(shot.target_path) ? shot.target_path : []).filter(isVec3).map((a) => vec3(a));
    let tgtAt;
    if (tpts.length >= 2) tgtAt = makeCurve(tpts);
    else { const t = (tpts[0] || t0 || new THREE.Vector3(...DEFAULT_TARGET)).clone(); tgtAt = (e, out) => out.copy(t); }
    const duration = Math.max(2, Math.min(40, num(shot.duration, 8)));
    const fov = Math.max(15, Math.min(100, num(shot.fov, 45)));
    const ease = EASE[MOVE_EASE[shot.camera_movement]] || EASE.smoother;
    const sounds = (Array.isArray(shot.sound_cues) ? shot.sound_cues : [])
      .filter((c) => c && typeof c.cue === 'string').map((c) => ({ ...c, at: Math.max(0, num(c.at, 0)) })).sort((a, b) => a.at - b.at);
    const startPos = posAt(0, new THREE.Vector3()), endPos = posAt(1, new THREE.Vector3());
    const startTgt = tgtAt(0, new THREE.Vector3()), endTgt = tgtAt(1, new THREE.Vector3());
    return {
      shot, posAt, tgtAt, duration, fov, fovFrom: fov, ease, t: 0,
      cues: this._cuesOf(shot), cueIdx: 0, sounds, soundIdx: 0,
      startPos, endPos, startTgt, endTgt,
      keyframeGap: startPos.distanceTo(endPos) + startTgt.distanceTo(endTgt) * 0.5,
      useEnd: false, rmPending: false, blend: null,
    };
  }

  /** mode: 'instant' | 'blend' */
  _begin(idx, mode = 'instant') {
    const shot = this.shots[idx];
    if (!shot) return;
    const cam = this.camera;
    this.index = idx;
    const r = this._prepare(shot);
    if (mode === 'blend' && !this.isReduced()) {
      r.blend = { from: { pos: cam.position.clone(), quat: cam.quaternion.clone(), tgt: this.lookTarget.clone() }, t: 0, dur: 1.8 };
      r.fovFrom = cam.fov;
    }
    this.run = r;
    this.state = 'shot';
    try { this.hooks.onShotStart && this.hooks.onShotStart(shot, idx, { instant: mode !== 'blend' }); } catch (e) { console.warn('[director] onShotStart', e); }
    try { this.hooks.onShotChange && this.hooks.onShotChange(idx, shot); } catch (e) { console.warn('[director] onShotChange', e); }
    this._applyCamera(0);
  }

  _trigger(cue, instant) {
    const obj = this.stage && this.stage.resolve(cue.target);
    if (!obj) return;
    const act = makeAction(cue.action, obj, cue.params, this);
    if (!act) return;
    // a new action on the same channel supersedes the old one (which stops where it is)
    this._dropTweens((tw) => tw.obj === obj && tw.act.channel === act.channel);
    if (instant) { act.apply(1); if (act.end) act.end(); return; }
    act.apply(0);
    this.tweens.push({ obj, act, t: 0 });
  }

  _updateTweens(dt) {
    if (!this.tweens.length) return;
    const keep = [];
    for (const tw of this.tweens) {
      tw.t += dt;
      const p = clamp01(tw.t / tw.act.dur);
      tw.act.apply(p);
      if (p >= 1) { if (tw.act.end) tw.act.end(); } else keep.push(tw);
    }
    this.tweens = keep;
  }

  update(dt) {
    this.time += dt;
    this._updateTweens(dt);
    if (this.state === 'drift') { this._drift(dt); return; }
    const r = this.run;
    if (!r) return;
    if (this.state === 'shot') r.t += dt;
    while (r.cueIdx < r.cues.length && r.cues[r.cueIdx].at <= r.t) this._trigger(r.cues[r.cueIdx++], false);
    while (r.soundIdx < r.sounds.length && r.sounds[r.soundIdx].at <= r.t) {
      const s = r.sounds[r.soundIdx++];
      try { this.hooks.sound && this.hooks.sound(s.cue, s.volume); } catch (e) { /* audio optional */ }
    }
    // reduced motion: dissolve from first to last key frame halfway through the shot
    if (this.state === 'shot' && this.isReduced() && !r.useEnd && !r.rmPending && r.keyframeGap > 0.75 && r.t >= r.duration * 0.5) {
      r.rmPending = true;
      const tok = this.token;
      this._transition('dissolve', () => { if (tok === this.token && this.run === r) r.useEnd = true; });
    }
    this._applyCamera(dt);
    if (this.state === 'shot' && r.t >= r.duration) this._shotEnded();
  }

  _applyCamera(dt) {
    const r = this.run, cam = this.camera;
    const reduced = this.isReduced();
    const pos = this._pos, tgt = this._tgt;
    if (reduced) {
      pos.copy(r.useEnd ? r.endPos : r.startPos);
      tgt.copy(r.useEnd ? r.endTgt : r.startTgt);
    } else {
      const e = r.ease(clamp01(r.t / r.duration));
      r.posAt(e, pos);
      r.tgtAt(e, tgt);
      // barely perceptible breathing so held frames never feel frozen
      pos.x += Math.sin(this.time * 0.31) * 0.018;
      pos.y += Math.sin(this.time * 0.23 + 1.3) * 0.014;
    }
    cam.position.copy(pos);
    cam.lookAt(tgt);
    this.lookTarget.copy(tgt);
    if (r.blend) {
      r.blend.t += dt;
      const b = EASE.inOutCubic(clamp01(r.blend.t / r.blend.dur));
      this._qa.copy(cam.quaternion);
      cam.position.lerpVectors(r.blend.from.pos, pos, b);
      cam.quaternion.slerpQuaternions(r.blend.from.quat, this._qa, b);
      this.lookTarget.lerpVectors(r.blend.from.tgt, tgt, b);
      if (b >= 1) r.blend = null;
    }
    const fu = reduced ? 1 : clamp01(r.t / Math.min(r.duration * 0.6, 3));
    const fov = r.fovFrom + (r.fov - r.fovFrom) * EASE.inOutSine(fu);
    if (Math.abs(cam.fov - fov) > 1e-3) { cam.fov = fov; cam.updateProjectionMatrix(); }
  }

  _shotEnded() {
    const r = this.run;
    while (r.cueIdx < r.cues.length) this._trigger(r.cues[r.cueIdx++], false);
    if (this.index < this.shots.length - 1) this._advance(this.index + 1);
    else this._enterDrift();
  }

  _advance(next) {
    const type = this.run.shot.transition_type;
    const tok = ++this.token;
    const go = (mode) => () => { if (tok === this.token) this._begin(next, mode); };
    if (type === 'cut') { go('instant')(); return; }
    if (this.isReduced()) { this.state = 'transition'; this._transition('dissolve', go('instant')); return; }
    const jump = this.camera.position.distanceTo(this._startPose(this.shots[next]));
    if (VEIL_SHOT_TYPES.has(type) && jump >= 12) { this.state = 'transition'; this._transition(type, go('instant')); return; }
    if (jump >= 28) { this.state = 'transition'; this._transition('dissolve', go('instant')); return; }
    go('blend')();
  }

  _enterDrift() {
    const cam = this.camera;
    const off = cam.position.clone().sub(this.lookTarget);
    this.drift = {
      target: this.lookTarget.clone(), radius: Math.hypot(off.x, off.z), angle: Math.atan2(off.x, off.z),
      y: cam.position.y, t: 0, dir: this.index % 2 ? -1 : 1,
    };
    this.state = 'drift';
  }

  _drift(dt) {
    const d = this.drift;
    if (!d || this.isReduced()) return; // reduced motion: hold the final frame still
    d.t += dt;
    const ramp = d.t < 6 ? EASE.inOutSine(d.t / 6) : 1; // ease in from rest: no jolt after the last shot
    const cam = this.camera;
    if (d.radius > 0.5) {
      d.angle += dt * 0.016 * ramp * d.dir;
      cam.position.set(d.target.x + Math.sin(d.angle) * d.radius, d.y + Math.sin(d.t * 0.13) * 0.28 * ramp, d.target.z + Math.cos(d.angle) * d.radius);
    } else {
      cam.position.y = d.y + Math.sin(d.t * 0.13) * 0.2 * ramp;
    }
    cam.lookAt(d.target.x, d.target.y + Math.sin(d.t * 0.09) * 0.08 * ramp, d.target.z);
  }
}
