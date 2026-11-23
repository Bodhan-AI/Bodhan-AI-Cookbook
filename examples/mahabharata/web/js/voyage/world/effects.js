// world/effects.js — sky dome, lighting presets, particles, moods, veil transitions and the
// procedural pieces of the opening (starfield, golden mandala, fallback palm-leaf manuscript).
import * as THREE from 'three';
import { color, isHex, num, clamp01, seededRandom } from './assets.js';

const damp = (rate, dt) => 1 - Math.exp(-rate * dt);
const smooth = (a, b, x) => { const t = clamp01((x - a) / (b - a)); return t * t * (3 - 2 * t); };

// ======================================================================= lighting presets
// key_dir = direction the key light COMES FROM (light position relative to the stage).
export const LIGHT_PRESETS = {
  dawn:        { hemiSky: '#a7b4d8', hemiGround: '#5a4636', hemi: 0.72, key: '#ffc9a0', keyI: 1.6, keyDir: [-0.85, 0.28, -0.35], rim: '#8fa4e0', rimI: 0.35, torch: 0, exposure: 1.0 },
  day:         { hemiSky: '#c4d8f0', hemiGround: '#6b5a44', hemi: 0.95, key: '#fff3e0', keyI: 2.3, keyDir: [0.45, 1.0, 0.35],   rim: '#ffffff', rimI: 0.25, torch: 0, exposure: 1.0 },
  golden_hour: { hemiSky: '#f2c48f', hemiGround: '#5a3c28', hemi: 0.76, key: '#ffb46b', keyI: 2.1, keyDir: [-1.0, 0.32, 0.25],  rim: '#ffd9a0', rimI: 0.45, torch: 0, exposure: 1.05 },
  dusk:        { hemiSky: '#6e62a0', hemiGround: '#2c2030', hemi: 0.64, key: '#ff8a5c', keyI: 1.25, keyDir: [1.0, 0.16, -0.3],  rim: '#7d8cd8', rimI: 0.45, torch: 0, exposure: 1.02 },
  night:       { hemiSky: '#3a4a80', hemiGround: '#0d0f1c', hemi: 0.46, key: '#9fb4ff', keyI: 0.65, keyDir: [0.3, 1.0, -0.5],   rim: '#6f8fc9', rimI: 0.4,  torch: 0, exposure: 1.12 },
  torchlit:    { hemiSky: '#3b2a40', hemiGround: '#120c10', hemi: 0.36, key: '#ffb36b', keyI: 0.55, keyDir: [0.2, 0.9, 0.4],    rim: '#ff9a4a', rimI: 0.3,  torch: 1, exposure: 1.12 },
  storm:       { hemiSky: '#5d6878', hemiGround: '#22252c', hemi: 0.66, key: '#c8d2e0', keyI: 0.85, keyDir: [-0.3, 1.0, 0.2],   rim: '#9fb0c8', rimI: 0.3,  torch: 0, exposure: 0.96 },
  celestial:   { hemiSky: '#8090e0', hemiGround: '#2a2350', hemi: 0.74, key: '#ffe2a8', keyI: 1.5, keyDir: [0.0, 1.0, 0.25],    rim: '#6f8fc9', rimI: 0.7,  torch: 0, exposure: 1.1 },
  overcast:    { hemiSky: '#c3c8cc', hemiGround: '#5b5a52', hemi: 1.05, key: '#e6e8ea', keyI: 0.65, keyDir: [0.2, 1.0, 0.1],    rim: '#d0d4d8', rimI: 0.15, torch: 0, exposure: 0.96 },
};
export const PRESET_NAMES = Object.keys(LIGHT_PRESETS);

// Sky tints per env.sky_* asset (and per preset when a shot names no sky). w = weight vs. the
// location palette (palette leads on bright skies; night/storm tints dominate).
const SKY_TINTS = {
  'env.sky_dawn':  { top: '#33406e', horizon: '#e2a07c', sun: 0.9, stars: 0.12, sunColor: '#ffcf9e', w: 0.5 },
  'env.sky_day':   { top: '#3d6fb0', horizon: '#cdd8dc', sun: 0.7, stars: 0,    sunColor: '#fff4dc', w: 0.35 },
  'env.sky_dusk':  { top: '#1f1f48', horizon: '#c9764c', sun: 0.9, stars: 0.3,  sunColor: '#ff9a5c', w: 0.55 },
  'env.sky_night': { top: '#04061a', horizon: '#1b2040', sun: 0.15, stars: 1,   sunColor: '#aebfff', w: 0.8 },
};
const PRESET_SKY = {
  dawn: SKY_TINTS['env.sky_dawn'], day: SKY_TINTS['env.sky_day'], dusk: SKY_TINTS['env.sky_dusk'],
  night: SKY_TINTS['env.sky_night'], torchlit: { ...SKY_TINTS['env.sky_night'], horizon: '#2a1c22' },
  golden_hour: { top: '#3c4a82', horizon: '#e8a25e', sun: 1.0, stars: 0, sunColor: '#ffbf73', w: 0.5 },
  storm: { top: '#2a2f3a', horizon: '#56606e', sun: 0, stars: 0, sunColor: '#c8d2e0', w: 0.62 },
  celestial: { top: '#0d0b2e', horizon: '#3b3a7a', sun: 0.6, stars: 0.85, sunColor: '#ffe2a8', w: 0.7 },
  overcast: { top: '#79818a', horizon: '#b4b8b8', sun: 0.05, stars: 0, sunColor: '#e6e8ea', w: 0.55 },
};

/** Resolve sky colours from location palette + shot sky asset + lighting preset. */
export function skyColorsFor(palette = {}, skyAsset, preset) {
  const tint = SKY_TINTS[skyAsset] || PRESET_SKY[preset] || SKY_TINTS['env.sky_day'];
  const w = tint.w;
  const top = color(palette.sky, tint.top).lerp(color(tint.top), w);
  const horizon = color(palette.fog || palette.sky, tint.horizon).lerp(color(tint.horizon), w * 0.8);
  const bottom = horizon.clone().lerp(color(palette.ground, '#2b2620'), 0.35).multiplyScalar(0.7);
  return { top, horizon, bottom, sun: tint.sun, stars: tint.stars, sunColor: color(tint.sunColor) };
}

// ======================================================================= sky dome
const SKY_VERT = /* glsl */`
varying vec3 vDir;
void main() {
  vDir = position;
  vec4 p = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  gl_Position = p.xyww; // always on the far plane
}`;
const SKY_FRAG = /* glsl */`
uniform vec3 uTop; uniform vec3 uHorizon; uniform vec3 uBottom;
uniform vec3 uSunDir; uniform vec3 uSunColor;
uniform float uSun; uniform float uStars; uniform float uTime; uniform float uNebula; uniform float uDesat;
varying vec3 vDir;
float hash3(vec3 p) { p = fract(p * 0.3183099 + 0.1); p *= 17.0; return fract(p.x * p.y * p.z * (p.x + p.y + p.z)); }
float vnoise(vec3 x) {
  vec3 i = floor(x); vec3 f = fract(x); f = f * f * (3.0 - 2.0 * f);
  return mix(mix(mix(hash3(i), hash3(i + vec3(1,0,0)), f.x), mix(hash3(i + vec3(0,1,0)), hash3(i + vec3(1,1,0)), f.x), f.y),
             mix(mix(hash3(i + vec3(0,0,1)), hash3(i + vec3(1,0,1)), f.x), mix(hash3(i + vec3(0,1,1)), hash3(i + vec3(1,1,1)), f.x), f.y), f.z);
}
void main() {
  vec3 d = normalize(vDir);
  float h = d.y;
  vec3 col = mix(uHorizon, uTop, smoothstep(0.0, 0.6, h));
  col = mix(col, uBottom, smoothstep(0.0, -0.3, h));
  col += uHorizon * 0.10 * exp(-abs(h) * 16.0);                    // horizon haze
  float s = max(dot(d, normalize(uSunDir)), 0.0);
  col += uSunColor * (pow(s, 700.0) * 2.4 + pow(s, 14.0) * 0.32 + pow(s, 3.0) * 0.10) * uSun;
  if (uNebula > 0.001) {                                           // faint indigo/violet nebula
    float n = vnoise(d * 3.0 + vec3(0.0, uTime * 0.004, 0.0)) * 0.65 + vnoise(d * 7.0) * 0.35;
    n = smoothstep(0.45, 0.95, n);
    col += mix(vec3(0.10, 0.06, 0.22), vec3(0.20, 0.12, 0.06), smoothstep(0.2, 0.9, d.x * 0.5 + 0.5)) * n * uNebula;
  }
  if (uStars > 0.001) {
    vec3 q = d * 300.0; vec3 id = floor(q); float r = hash3(id);
    float star = step(0.9972, r);
    float dd = length(fract(q) - 0.5);
    float tw = 0.65 + 0.35 * sin(uTime * (0.8 + r * 2.6) + r * 80.0);
    col += vec3(1.0, 0.95, 0.86) * star * smoothstep(0.32, 0.0, dd) * tw * uStars * smoothstep(-0.02, 0.22, h);
  }
  col = mix(col, vec3(dot(col, vec3(0.2126, 0.7152, 0.0722))), uDesat); // 'error' mood
  gl_FragColor = vec4(col, 1.0);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}`;

export class SkyDome {
  constructor() {
    this.uniforms = {
      uTop: { value: new THREE.Color('#1A1D3A') }, uHorizon: { value: new THREE.Color('#2a2d4a') },
      uBottom: { value: new THREE.Color('#14121c') }, uSunDir: { value: new THREE.Vector3(0.4, 0.6, -0.5) },
      uSunColor: { value: new THREE.Color('#fff2d8') }, uSun: { value: 0.5 }, uStars: { value: 0 },
      uTime: { value: 0 }, uNebula: { value: 0 }, uDesat: { value: 0 },
    };
    const mat = new THREE.ShaderMaterial({
      uniforms: this.uniforms, vertexShader: SKY_VERT, fragmentShader: SKY_FRAG,
      side: THREE.BackSide, depthWrite: false, fog: false,
    });
    this.mesh = new THREE.Mesh(new THREE.SphereGeometry(100, 48, 24), mat);
    this.mesh.frustumCulled = false;
    this.mesh.renderOrder = -100;
    this.mesh.name = 'sky';
    this.target = {
      top: this.uniforms.uTop.value.clone(), horizon: this.uniforms.uHorizon.value.clone(),
      bottom: this.uniforms.uBottom.value.clone(), sunColor: this.uniforms.uSunColor.value.clone(),
      sunDir: this.uniforms.uSunDir.value.clone(), sun: 0.5, stars: 0, nebula: 0,
    };
  }
  /** colours from skyColorsFor(); sunDir Vector3; instant snaps. */
  set(c, sunDir, instant = false) {
    const t = this.target;
    if (c.top) t.top.copy(c.top);
    if (c.horizon) t.horizon.copy(c.horizon);
    if (c.bottom) t.bottom.copy(c.bottom);
    if (c.sunColor) t.sunColor.copy(c.sunColor);
    if (sunDir) t.sunDir.copy(sunDir).normalize();
    if (c.sun != null) t.sun = c.sun;
    if (c.stars != null) t.stars = c.stars;
    if (c.nebula != null) t.nebula = c.nebula;
    if (instant) this.update(100, 0, null);
  }
  get horizon() { return this.uniforms.uHorizon.value; }
  setDesaturation(v) { this.uniforms.uDesat.value = clamp01(num(v, 0)); }
  update(dt, time, camera) {
    const k = damp(1.4, dt), u = this.uniforms, t = this.target;
    u.uTop.value.lerp(t.top, k); u.uHorizon.value.lerp(t.horizon, k); u.uBottom.value.lerp(t.bottom, k);
    u.uSunColor.value.lerp(t.sunColor, k); u.uSunDir.value.lerp(t.sunDir, k).normalize();
    u.uSun.value += (t.sun - u.uSun.value) * k;
    u.uStars.value += (t.stars - u.uStars.value) * k;
    u.uNebula.value += (t.nebula - u.uNebula.value) * k;
    u.uTime.value = time;
    if (camera) this.mesh.position.copy(camera.position);
  }
  dispose() { this.mesh.geometry.dispose(); this.mesh.material.dispose(); }
}

// ======================================================================= light rig
const GOLD_WARM = new THREE.Color('#ffcf8a');
export function desaturate(c, amt) {
  if (amt <= 0) return c;
  const l = c.r * 0.2126 + c.g * 0.7152 + c.b * 0.0722;
  c.r += (l - c.r) * amt; c.g += (l - c.g) * amt; c.b += (l - c.b) * amt;
  return c;
}
/** key_direction from data: treat as "where light comes from"; a negative y means a travel vector. */
export function keyDirection(arr, fallback) {
  if (Array.isArray(arr) && arr.length >= 3) {
    const v = new THREE.Vector3(num(arr[0]), num(arr[1]), num(arr[2]));
    if (v.lengthSq() > 1e-6) { if (v.y < 0) v.negate(); return v.normalize(); }
  }
  return new THREE.Vector3(...fallback).normalize();
}

export class LightRig {
  constructor(scene, { shadows = false } = {}) {
    this.group = new THREE.Group();
    this.group.name = 'lights';
    this.hemi = new THREE.HemisphereLight(0xffffff, 0x444444, 0.6);
    this.key = new THREE.DirectionalLight(0xffffff, 1.5);
    this.rim = new THREE.DirectionalLight(0xffffff, 0.3);
    this.key.target.position.set(0, 0, -6);
    this.rim.target.position.set(0, 1, 0);
    this.torches = [new THREE.PointLight(0xff9a4a, 0, 38, 2), new THREE.PointLight(0xff9a4a, 0, 38, 2)];
    this.torches[0].position.set(-7, 3.4, -3);
    this.torches[1].position.set(7, 3.4, -5);
    this.group.add(this.hemi, this.key, this.key.target, this.rim, this.rim.target, ...this.torches);
    scene.add(this.group);
    const p = LIGHT_PRESETS.day;
    this.cur = this._state(p, 1, null, null);
    this.tgt = this._state(p, 1, null, null);
    this.flash = 0; this.nextFlash = 8;
    this.preset = 'day';
    this.setShadows(shadows);
  }
  _state(p, intensity, colorHex, dir) {
    const key = color(p.key);
    if (isHex(colorHex)) key.lerp(color(colorHex), 0.7);
    return {
      hemiSky: color(p.hemiSky), hemiGround: color(p.hemiGround), hemi: p.hemi * intensity,
      key, keyI: p.keyI * intensity, keyDir: dir || new THREE.Vector3(...p.keyDir).normalize(),
      rim: color(p.rim), rimI: p.rimI * intensity, torch: p.torch, exposure: p.exposure,
    };
  }
  /** lighting: {preset, intensity 0..2 (1 = preset default), color, key_direction} */
  apply(lighting = {}, instant = false) {
    const name = LIGHT_PRESETS[lighting.preset] ? lighting.preset : 'day';
    const p = LIGHT_PRESETS[name];
    const inten = Math.max(0, Math.min(2, num(lighting.intensity, 1)));
    this.preset = name;
    this.tgt = this._state(p, inten, lighting.color, keyDirection(lighting.key_direction, p.keyDir));
    if (instant) this.cur = this._state(p, inten, lighting.color, this.tgt.keyDir.clone());
  }
  get keyDir() { return this.cur.keyDir; }
  get exposure() { return this.cur.exposure; }
  setShadows(on) {
    this.key.castShadow = !!on;
    if (on) {
      const s = this.key.shadow;
      s.mapSize.set(2048, 2048);
      const c = s.camera; c.left = -45; c.right = 45; c.top = 45; c.bottom = -45; c.near = 1; c.far = 260;
      c.updateProjectionMatrix();
      s.bias = -0.0004; s.normalBias = 0.03;
    } else if (this.key.shadow.map) { this.key.shadow.map.dispose(); this.key.shadow.map = null; }
  }
  /** mood: {light, warm, desat}; reduced: no lightning flashes */
  update(dt, time, mood = {}, reduced = false) {
    const k = damp(1.1, dt), c = this.cur, t = this.tgt;
    c.hemiSky.lerp(t.hemiSky, k); c.hemiGround.lerp(t.hemiGround, k); c.key.lerp(t.key, k); c.rim.lerp(t.rim, k);
    c.hemi += (t.hemi - c.hemi) * k; c.keyI += (t.keyI - c.keyI) * k; c.rimI += (t.rimI - c.rimI) * k;
    c.torch += (t.torch - c.torch) * k; c.exposure += (t.exposure - c.exposure) * k;
    c.keyDir.lerp(t.keyDir, k).normalize();

    // Storm: soft, infrequent lightning (never under reduced motion; never a full-frame white).
    if (this.preset === 'storm' && !reduced) {
      this.nextFlash -= dt;
      if (this.nextFlash <= 0) { this.flash = 0.7 + Math.random() * 0.3; this.nextFlash = 7 + Math.random() * 9; }
    }
    this.flash = Math.max(0, this.flash - dt * 2.4);

    const lm = num(mood.light, 1), warm = num(mood.warm, 0) * 0.35, ds = num(mood.desat, 0) * 0.7;
    this.hemi.color.copy(c.hemiSky).lerp(GOLD_WARM, warm * 0.5); desaturate(this.hemi.color, ds);
    this.hemi.groundColor.copy(c.hemiGround); desaturate(this.hemi.groundColor, ds);
    this.hemi.intensity = (c.hemi + this.flash * 1.2) * lm;
    this.key.color.copy(c.key).lerp(GOLD_WARM, warm); desaturate(this.key.color, ds);
    this.key.intensity = (c.keyI + this.flash * 2.5) * lm;
    this.key.position.copy(c.keyDir).multiplyScalar(90).add(this.key.target.position);
    this.rim.color.copy(c.rim); desaturate(this.rim.color, ds);
    this.rim.intensity = c.rimI * lm;
    this.rim.position.set(-c.keyDir.x * 60, 25, -c.keyDir.z * 60 - 20);
    const flicker = 0.84 + 0.1 * Math.sin(time * 11.3) * Math.sin(time * 7.1 + 1.3) + 0.06 * Math.sin(time * 23.7);
    for (let i = 0; i < this.torches.length; i++) {
      const L = this.torches[i];
      L.intensity = c.torch * 42 * lm * (reduced ? 0.92 : flicker + i * 0.03);
      L.visible = c.torch > 0.01;
    }
  }
  dispose() { if (this.key.shadow && this.key.shadow.map) this.key.shadow.map.dispose(); this.group.parent && this.group.parent.remove(this.group); }
}

// ======================================================================= particles
// Cheap THREE.Points systems; all motion is in the vertex shader (drift + sway + wrap in a box).
// size = pixels at 10 m (attenuated) unless att:false. shape: 0 soft disc, 1 streak, 2 petal, 3 sparkle.
export const PARTICLE_TYPES = {
  dust:       { count: 900,  vel: [0.25, 0.04, 0.1],  sway: 0.6, size: 3.2, color: '#d9c29a', opacity: 0.38, shape: 0, add: false, twinkle: 0,   area: [70, 14, 70] },
  embers:     { count: 420,  vel: [0.1, 1.15, 0.05],  sway: 0.5, size: 3.6, color: '#ff8a3d', opacity: 0.95, shape: 0, add: true,  twinkle: 0.8, area: [30, 16, 30] },
  petals:     { count: 260,  vel: [0.35, -0.45, 0.15], sway: 1.2, size: 9,  color: '#f2a7b8', opacity: 0.9, shape: 2, add: false, twinkle: 0,   area: [40, 18, 40] },
  fireflies:  { count: 180,  vel: [0, 0.05, 0],       sway: 1.6, size: 4.5, color: '#d8f27a', opacity: 1,   shape: 0, add: true,  twinkle: 1,   area: [50, 6, 50] },
  mist:       { count: 70,   vel: [0.3, 0, 0.08],     sway: 0.8, size: 700, color: '#c9cfd6', opacity: 0.1, shape: 0, add: false, twinkle: 0,   area: [120, 6, 120] },
  rain:       { count: 2400, vel: [0.6, -18, 0.2],    sway: 0,   size: 9,  color: '#9fb2c8', opacity: 0.42, shape: 1, add: false, twinkle: 0,   area: [70, 30, 70] },
  snow:       { count: 1600, vel: [0.3, -1.1, 0.1],   sway: 0.9, size: 4,  color: '#f4f6ff', opacity: 0.85, shape: 0, add: false, twinkle: 0,   area: [70, 24, 70] },
  stars:      { count: 1400, vel: [0, 0, 0],          sway: 0,   size: 2.2, color: '#fff6e0', opacity: 0.9, shape: 0, add: true,  twinkle: 0.7, area: [800, 260, 800], y: 70, att: false },
  gold_motes: { count: 520,  vel: [0.05, 0.18, 0.03], sway: 0.7, size: 3.4, color: '#e3c77e', opacity: 0.85, shape: 3, add: true,  twinkle: 0.5, area: [36, 10, 36] },
  sparks:     { count: 300,  vel: [0.3, 4.5, 0.2],    sway: 0.3, size: 2.6, color: '#ffd27a', opacity: 1,   shape: 3, add: true,  twinkle: 0.6, area: [16, 10, 16] },
};
// env.* shot elements that are better expressed as particles.
export const ENV_PARTICLES = { 'env.mist': 'mist', 'env.dust': 'dust', 'env.rain': 'rain', 'env.snow': 'snow', 'env.stars': 'stars' };
export const QUALITY_PARTICLES = { low: 0.4, medium: 0.7, high: 1 };

const PARTICLE_VERT = /* glsl */`
attribute float aRand;
uniform float uTime; uniform float uSize; uniform float uOpacity; uniform float uPx;
uniform float uTwinkle; uniform float uSway; uniform float uGather; uniform float uAtt;
uniform vec3 uArea; uniform vec3 uOrigin; uniform vec3 uVel; uniform vec3 uGatherTarget;
varying float vAlpha; varying float vRand;
void main() {
  vRand = aRand;
  vec3 drift = uVel * uTime * (0.6 + aRand * 0.8);
  float sw = uSway * (0.5 + aRand);
  drift.x += sin(uTime * 0.7 + aRand * 40.0) * sw;
  drift.z += cos(uTime * 0.5 + aRand * 31.0) * sw;
  drift.y += sin(uTime * 0.9 + aRand * 17.0) * sw * 0.3;
  vec3 q = mod(position * uArea + drift, uArea);
  vec3 qn = q / uArea;
  float edge = min(min(min(qn.x, 1.0 - qn.x), min(qn.z, 1.0 - qn.z)), min(qn.y, 1.0 - qn.y));
  vec3 p = q + uOrigin - vec3(uArea.x * 0.5, 0.0, uArea.z * 0.5);
  float g = uGather * (0.35 + 0.65 * aRand);
  p += (uGatherTarget - p) * g * 0.85;
  p += vec3(sin(uTime * 1.3 + aRand * 50.0) * 0.7, sin(uTime * 1.7 + aRand * 20.0) * 0.45, cos(uTime * 1.1 + aRand * 30.0) * 0.7) * g;
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  gl_Position = projectionMatrix * mv;
  float depth = max(-mv.z, 0.05);
  float sz = uSize * (0.6 + aRand * 0.8) * uPx;
  gl_PointSize = min(mix(sz, sz * 10.0 / depth, uAtt), 320.0);
  float tw = 1.0 - uTwinkle * 0.5 * (1.0 + sin(uTime * (1.5 + aRand * 3.0) + aRand * 60.0));
  vAlpha = uOpacity * tw * smoothstep(0.0, 0.07, edge) * mix(1.0, smoothstep(0.4, 2.0, depth), uAtt);
}`;
const PARTICLE_FRAG = /* glsl */`
uniform vec3 uColor; uniform float uShape;
varying float vAlpha; varying float vRand;
void main() {
  vec2 uv = gl_PointCoord * 2.0 - 1.0;
  float a;
  vec3 col = uColor;
  if (uShape < 0.5) { float d = length(uv); a = smoothstep(1.0, 0.0, d); a *= a; }
  else if (uShape < 1.5) { a = smoothstep(0.16, 0.0, abs(uv.x)) * smoothstep(1.0, 0.1, abs(uv.y)); }
  else if (uShape < 2.5) {
    float ang = vRand * 6.2831; vec2 p = mat2(cos(ang), -sin(ang), sin(ang), cos(ang)) * uv; p.x *= 1.9;
    a = smoothstep(1.0, 0.7, length(p)); col *= 0.78 + 0.22 * vRand;
  } else {
    float d = length(uv); a = pow(smoothstep(1.0, 0.0, d), 3.0);
    a += max(0.0, 1.0 - abs(uv.x) * 9.0) * max(0.0, 1.0 - abs(uv.y)) * 0.55;
    a += max(0.0, 1.0 - abs(uv.y) * 9.0) * max(0.0, 1.0 - abs(uv.x)) * 0.55;
  }
  a *= vAlpha;
  if (a < 0.003) discard;
  gl_FragColor = vec4(col, a);
  #include <colorspace_fragment>
}`;

export class ParticleField {
  constructor(type, { quality = 'medium', px = 1 } = {}) {
    const def = PARTICLE_TYPES[type] || PARTICLE_TYPES.dust;
    this.type = type; this.def = def;
    const n = def.count;
    const rnd = seededRandom('particles|' + type);
    const pos = new Float32Array(n * 3), rand = new Float32Array(n);
    for (let i = 0; i < n; i++) { pos[i * 3] = rnd(); pos[i * 3 + 1] = rnd(); pos[i * 3 + 2] = rnd(); rand[i] = rnd(); }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geo.setAttribute('aRand', new THREE.BufferAttribute(rand, 1));
    geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1e5);
    this.uniforms = {
      uTime: { value: 0 }, uSize: { value: def.size }, uOpacity: { value: 0 }, uPx: { value: px },
      uTwinkle: { value: def.twinkle }, uSway: { value: def.sway }, uGather: { value: 0 }, uAtt: { value: def.att === false ? 0 : 1 },
      uArea: { value: new THREE.Vector3(...def.area) }, uOrigin: { value: new THREE.Vector3(0, def.y || 0, 0) },
      uVel: { value: new THREE.Vector3(...def.vel) }, uGatherTarget: { value: new THREE.Vector3(0, 1.5, 0) },
      uColor: { value: color(def.color) }, uShape: { value: def.shape },
    };
    const mat = new THREE.ShaderMaterial({
      uniforms: this.uniforms, vertexShader: PARTICLE_VERT, fragmentShader: PARTICLE_FRAG,
      transparent: true, depthWrite: false, fog: false, toneMapped: false,
      blending: def.add ? THREE.AdditiveBlending : THREE.NormalBlending,
    });
    this.points = new THREE.Points(geo, mat);
    this.points.frustumCulled = false;
    this.points.renderOrder = 2;
    this.points.name = 'particles:' + type;
    this.density = 0.5; this.quality = quality;
    this.opacityTarget = 0; this.fade = 0;
    this._applyCount();
  }
  _applyCount() {
    const q = QUALITY_PARTICLES[this.quality] ?? 0.7;
    const n = Math.max(8, Math.floor(this.def.count * Math.min(1, Math.max(0.05, this.density)) * q));
    this.points.geometry.setDrawRange(0, Math.min(n, this.def.count));
  }
  configure({ density, color: c, area } = {}) {
    if (density != null) { this.density = clamp01(num(density, 0.5)); this._applyCount(); }
    if (isHex(c)) this.uniforms.uColor.value.set(c); else this.uniforms.uColor.value.set(this.def.color);
    if (Array.isArray(area) && area.length >= 3 && area.every((v) => num(v, 0) > 0)) {
      this.uniforms.uArea.value.set(num(area[0]), num(area[1]), num(area[2]));
    } else this.uniforms.uArea.value.set(...this.def.area);
  }
  setQuality(q) { this.quality = q; this._applyCount(); }
  show(on) { this.opacityTarget = on ? 1 : 0; }
  update(dt, time, opts = {}) {
    this.fade += (this.opacityTarget - this.fade) * damp(1.3, dt);
    const u = this.uniforms;
    u.uTime.value = time;
    u.uOpacity.value = this.def.opacity * this.fade * num(opts.opacityMul, 1);
    if (opts.px) u.uPx.value = opts.px;
    if (opts.gatherTarget) u.uGatherTarget.value.copy(opts.gatherTarget);
    if (opts.gather != null) u.uGather.value = opts.gather;
    this.points.visible = u.uOpacity.value > 0.002;
  }
  dispose() { this.points.geometry.dispose(); this.points.material.dispose(); this.points.parent && this.points.parent.remove(this.points); }
}

/** Per-scene set of particle systems keyed by type; cross-fades between shots. */
export class ParticleManager {
  constructor(scene, { quality = 'medium', px = 1 } = {}) {
    this.group = new THREE.Group(); this.group.name = 'particles';
    scene.add(this.group);
    this.fields = new Map(); this.quality = quality; this.px = px;
  }
  /** list: [{type, density, color, area}] */
  setEffects(list = [], instant = false) {
    const want = new Map();
    for (const e of Array.isArray(list) ? list : []) {
      if (!e || !PARTICLE_TYPES[e.type]) continue;
      const prev = want.get(e.type);
      if (!prev || num(e.density, 0.5) > num(prev.density, 0.5)) want.set(e.type, e);
    }
    for (const [type, f] of this.fields) if (!want.has(type)) f.show(false);
    for (const [type, e] of want) {
      let f = this.fields.get(type);
      if (!f) { f = new ParticleField(type, { quality: this.quality, px: this.px }); this.fields.set(type, f); this.group.add(f.points); }
      f.configure(e);
      f.show(true);
      if (instant) f.fade = 1;
    }
  }
  setQuality(q) { this.quality = q; for (const f of this.fields.values()) f.setQuality(q); }
  update(dt, time, opts) { for (const f of this.fields.values()) f.update(dt, time, opts); }
  dispose() { for (const f of this.fields.values()) f.dispose(); this.fields.clear(); this.group.parent && this.group.parent.remove(this.group); }
}

// ======================================================================= moods
// Smoothly blended multipliers the engine applies to lights / fog / exposure / particles.
export const MOODS = {
  calm:        { light: 1.0,  fog: 1.0,  exposure: 1.0,  warm: 0,   desat: 0, motes: 0,    gather: 0,   glow: 0 },
  investigate: { light: 0.72, fog: 1.35, exposure: 0.9,  warm: 0.1, desat: 0, motes: 1,    gather: 0.6, glow: 0.12 },
  reveal:      { light: 1.22, fog: 0.8,  exposure: 1.16, warm: 0.6, desat: 0, motes: 0.7,  gather: 0,   glow: 0.55 },
  error:       { light: 0.82, fog: 1.1,  exposure: 0.93, warm: 0,   desat: 1, motes: 0,    gather: 0,   glow: 0 },
};

export function makeGlowTexture(inner = 'rgba(255,226,160,1)', mid = 'rgba(201,162,75,0.35)') {
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  const x = c.getContext('2d');
  const g = x.createRadialGradient(128, 128, 0, 128, 128, 128);
  g.addColorStop(0, inner); g.addColorStop(0.25, mid); g.addColorStop(1, 'rgba(0,0,0,0)');
  x.fillStyle = g; x.fillRect(0, 0, 256, 256);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}
export function makeGlowSprite(scale = 30) {
  const mat = new THREE.SpriteMaterial({ map: makeGlowTexture(), color: 0xffffff, transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending, fog: false });
  const s = new THREE.Sprite(mat);
  s.scale.set(scale, scale, 1);
  s.renderOrder = 3;
  s.name = 'glow';
  return s;
}

// ======================================================================= veil transitions
// Drives div#transition-veil with inline styles (so it always works) and state classes the
// shell may decorate: `veil--<type>` (+ `veil--in` while covering/covered, `veil--out` while
// revealing). data-state = clear|covering|covered|revealing. Never a white frame: every colour
// is clamped to a dark/golden range.
export const VEIL_TYPES = ['fade_black', 'fade_gold', 'cloud_pass', 'manuscript_turn', 'door_open', 'mandala_iris', 'river_flow', 'path_glow', 'dust_wipe', 'cut'];
const NIGHT = '#0B0E22';
let irisPropRegistered = null;
function registerIrisProp() {
  if (irisPropRegistered !== null) return irisPropRegistered;
  try {
    if (window.CSS && CSS.registerProperty) {
      CSS.registerProperty({ name: '--veil-r', syntax: '<percentage>', inherits: false, initialValue: '0%' });
      irisPropRegistered = true;
    } else irisPropRegistered = false;
  } catch (e) { irisPropRegistered = /already/i.test(String(e && e.message)); }
  return irisPropRegistered;
}
function veilColorRGB(hex, maxL) {
  // THREE.Color parses hex as sRGB and stores linear; convert back to sRGB for CSS.
  const c = color(hex, NIGHT); const hsl = {}; c.getHSL(hsl, THREE.SRGBColorSpace);
  if (hsl.l > maxL) c.setHSL(hsl.h, hsl.s, maxL, THREE.SRGBColorSpace);
  return '#' + c.getHexString(THREE.SRGBColorSpace);
}

function veilLook(type, tint) {
  const t = isHex(tint) ? tint : null;
  const deep = t ? veilColorRGB(t, 0.16) : NIGHT;
  switch (type) {
    case 'fade_gold': {
      const g = t ? veilColorRGB(t, 0.55) : '#C9A24B';
      return { background: `radial-gradient(circle at 50% 55%, ${veilColorRGB(g, 0.62)} 0%, ${g} 24%, #3a2a10 62%, ${NIGHT} 100%)` };
    }
    case 'cloud_pass': {
      const c = t ? veilColorRGB(t, 0.6) : '#9aa2b4';
      return {
        background: `radial-gradient(42% 36% at 22% 38%, ${c}e6, transparent 72%), radial-gradient(38% 30% at 70% 60%, ${c}cc, transparent 70%), radial-gradient(30% 26% at 48% 22%, ${c}b3, transparent 70%), radial-gradient(34% 30% at 86% 28%, ${c}bf, transparent 70%), linear-gradient(180deg, #2a2f45, #1a1d3a)`,
        backgroundSize: '200% 100%',
      };
    }
    case 'manuscript_turn':
      return { background: 'linear-gradient(100deg, #1e160d 0%, #4a3a22 38%, #6e5838 49%, #7f6744 51%, #4a3a22 62%, #1e160d 100%)' };
    case 'door_open':
      return { background: 'linear-gradient(90deg, #120c06 0%, #3b2a14 46%, #8f7232 49.4%, #E3C77E 50%, #8f7232 50.6%, #3b2a14 54%, #120c06 100%)' };
    case 'mandala_iris':
      return { background: `repeating-conic-gradient(from 0deg at 50% 50%, rgba(201,162,75,0.2) 0deg 3deg, rgba(0,0,0,0) 3deg 15deg), radial-gradient(circle at 50% 50%, #3a2a10 0%, ${deep} 62%)` };
    case 'river_flow':
      return { background: 'linear-gradient(90deg, #0f2238 0%, #1d3a5c 30%, #2a5884 52%, #1a3550 76%, #0B0E22 100%)', backgroundSize: '200% 100%' };
    case 'path_glow':
      return { background: `radial-gradient(ellipse at 50% 100%, rgba(227,199,126,0.92) 0%, rgba(201,162,75,0.55) 28%, #1a1208 62%, ${NIGHT} 100%)` };
    case 'dust_wipe':
      return { background: 'linear-gradient(120deg, #241a10 0%, #5f4930 46%, #6b5236 52%, #241a10 100%)' };
    case 'fade_black':
    case 'cut':
    default:
      return { background: deep };
  }
}

// keyframes [from, to] for cover; reveal uses its own pair. Types share the same properties so
// WAAPI can interpolate clip-path shapes.
function veilFrames(type, phase, reduced) {
  if (reduced || type === 'fade_black' || type === 'fade_gold' || type === 'cut') {
    return phase === 'cover' ? [{ opacity: 0 }, { opacity: 1 }] : [{ opacity: 1 }, { opacity: 0 }];
  }
  const cover = phase === 'cover';
  switch (type) {
    case 'cloud_pass':
      return cover
        ? [{ opacity: 0, backgroundPosition: '100% 0', transform: 'translateX(6%) scale(1.04)' }, { opacity: 1, backgroundPosition: '50% 0', transform: 'translateX(0) scale(1)' }]
        : [{ opacity: 1, backgroundPosition: '50% 0', transform: 'translateX(0) scale(1)' }, { opacity: 0, backgroundPosition: '0% 0', transform: 'translateX(-6%) scale(1.04)' }];
    case 'manuscript_turn':
      return cover
        ? [{ opacity: 1, clipPath: 'polygon(100% 0%, 100% 0%, 100% 100%, 114% 100%)' }, { opacity: 1, clipPath: 'polygon(-14% 0%, 100% 0%, 100% 100%, 0% 100%)' }]
        : [{ opacity: 1, clipPath: 'polygon(0% 0%, 114% 0%, 100% 100%, 0% 100%)' }, { opacity: 1, clipPath: 'polygon(0% 0%, 0% 0%, -14% 100%, 0% 100%)' }];
    case 'door_open':
      return cover
        ? [{ opacity: 1, clipPath: 'inset(0% 50% 0% 50%)' }, { opacity: 1, clipPath: 'inset(0% 0% 0% 0%)' }]
        : [{ opacity: 1, clipPath: 'inset(0% 0% 0% 0%)' }, { opacity: 1, clipPath: 'inset(0% 50% 0% 50%)' }];
    case 'mandala_iris':
      if (cover) return [{ opacity: 1, clipPath: 'circle(0% at 50% 50%)' }, { opacity: 1, clipPath: 'circle(80% at 50% 50%)' }];
      if (registerIrisProp()) {
        // Iris opens from the centre (mask hole grows).
        return [{ opacity: 1, '--veil-r': '0%' }, { opacity: 1, '--veil-r': '120%' }];
      }
      return [{ opacity: 1, clipPath: 'circle(80% at 50% 50%)' }, { opacity: 1, clipPath: 'circle(0% at 50% 50%)' }];
    case 'river_flow':
      return cover
        ? [{ opacity: 1, clipPath: 'inset(0% 100% 0% 0%)', backgroundPosition: '100% 0' }, { opacity: 1, clipPath: 'inset(0% 0% 0% 0%)', backgroundPosition: '50% 0' }]
        : [{ opacity: 1, clipPath: 'inset(0% 0% 0% 0%)', backgroundPosition: '50% 0' }, { opacity: 1, clipPath: 'inset(0% 0% 0% 100%)', backgroundPosition: '0% 0' }];
    case 'path_glow':
      return cover
        ? [{ opacity: 1, clipPath: 'ellipse(0% 0% at 50% 100%)' }, { opacity: 1, clipPath: 'ellipse(160% 160% at 50% 100%)' }]
        : [{ opacity: 1, transform: 'translateY(0)' }, { opacity: 0, transform: 'translateY(-4%)' }];
    case 'dust_wipe':
      return cover
        ? [{ opacity: 1, clipPath: 'polygon(0% 0%, 0% 0%, -30% 100%, 0% 100%)' }, { opacity: 1, clipPath: 'polygon(0% 0%, 130% 0%, 100% 100%, 0% 100%)' }]
        : [{ opacity: 1, clipPath: 'polygon(0% 0%, 130% 0%, 100% 100%, -30% 100%)' }, { opacity: 1, clipPath: 'polygon(130% 0%, 130% 0%, 100% 100%, 100% 100%)' }];
    default:
      return cover ? [{ opacity: 0 }, { opacity: 1 }] : [{ opacity: 1 }, { opacity: 0 }];
  }
}

export class Veil {
  constructor(el) {
    if (!el && typeof document !== 'undefined') {
      el = document.createElement('div');
      el.id = 'transition-veil';
      document.body.appendChild(el);
    }
    this.el = el;
    this.state = 'clear';
    this.anim = null;
    this.type = null;
    this.op = 0;          // increments on every cover/reveal: a superseded call never touches state
    this.pending = null;  // promise of the running cover/reveal (joined by repeat calls)
    if (el) {
      // The veil is purely visual: it never captures input, so a stuck veil can never lock the UI.
      // `transition: none` stops the shell's CSS opacity transition fighting the WAAPI timeline.
      Object.assign(el.style, { position: 'fixed', inset: '0', pointerEvents: 'none', opacity: '0', transition: 'none' });
      if (!el.style.zIndex) el.style.zIndex = '50';
      el.setAttribute('aria-hidden', 'true');
      el.dataset.state = 'clear';
    }
  }
  get covered() { return this.state === 'covered'; }
  _setState(s) { this.state = s; if (this.el) this.el.dataset.state = s; }
  /** Drop any transition and leave the veil clear (used on world dispose). */
  clear() {
    if (!this.el) return;
    this.op++;
    if (this.anim) { try { this.anim.cancel(); } catch (e) { /* noop */ } this.anim = null; }
    this.pending = null;
    this.el.style.opacity = '0';
    this.el.style.pointerEvents = 'none';
    this._reset();
    this._classes(null, null);
    this._setState('clear');
  }
  _classes(type, phase) {
    const el = this.el;
    for (const c of [...el.classList]) if (c.startsWith('veil--')) el.classList.remove(c);
    if (type) el.classList.add('veil--' + type);
    if (phase) el.classList.add(phase === 'in' ? 'veil--in' : 'veil--out');
  }
  _reset() {
    const s = this.el.style;
    s.clipPath = ''; s.webkitClipPath = ''; s.transform = ''; s.backgroundPosition = ''; s.backgroundSize = '';
    s.maskImage = ''; s.webkitMaskImage = ''; s.filter = ''; s.removeProperty('--veil-r');
  }
  _look(type, tint) {
    this._reset();
    const look = veilLook(type, tint);
    Object.assign(this.el.style, look);
    if (type === 'dust_wipe') this.el.style.filter = 'blur(3px)';
  }
  async _run(frames, ms, easing) {
    const el = this.el;
    if (this.anim) { try { this.anim.cancel(); } catch (e) { /* noop */ } this.anim = null; }
    const last = frames[frames.length - 1];
    if (ms <= 0 || typeof el.animate !== 'function') { this._commit(last); return; }
    const a = el.animate(frames, { duration: ms, easing, fill: 'forwards' });
    this.anim = a;
    try { await a.finished; } catch (e) { return; /* cancelled by a newer transition */ }
    if (this.anim === a) { this._commit(last); try { a.cancel(); } catch (e) { /* noop */ } this.anim = null; }
  }
  _commit(frame) {
    for (const [k, v] of Object.entries(frame)) {
      if (k.startsWith('--')) this.el.style.setProperty(k, v); else this.el.style[k] = v;
    }
  }
  /** Cover the screen. Resolves when fully covered (or when a newer reveal supersedes it). */
  async cover(type = 'fade_black', { duration = 0.8, color: tint, reduced = false } = {}) {
    if (!this.el) return;
    if (!VEIL_TYPES.includes(type)) type = 'fade_black';
    if (this.state === 'covered') return;
    if (this.state === 'covering') { await this.pending; return; } // join the running cover
    const op = ++this.op;
    this.type = type;
    this._setState('covering');
    this._classes(type, 'in');
    this._look(reduced ? (type === 'fade_gold' ? 'fade_gold' : 'fade_black') : type, tint);
    const ms = type === 'cut' ? 0 : Math.max(reduced ? 500 : 250, duration * 1000);
    const run = this._run(veilFrames(type, 'cover', reduced), ms, 'cubic-bezier(.45,.05,.35,1)');
    this.pending = run;
    await run;
    if (op !== this.op) return; // superseded (e.g. a reveal started): do not claim "covered"
    this.pending = null;
    this.el.style.opacity = '1';
    this._setState('covered');
  }
  /** Reveal the scene. type defaults to the covering type. */
  async reveal(type, { duration = 0.9, reduced = false } = {}) {
    if (!this.el || this.state === 'clear') return;
    if (this.state === 'revealing') { await this.pending; return; } // join the running reveal
    const op = ++this.op;
    type = VEIL_TYPES.includes(type) ? type : (this.type || 'fade_black');
    this._setState('revealing');
    this._classes(type, 'out');
    // keep current covering look unless the reveal type has a different look
    if (!reduced && type !== this.type) this._look(type, null);
    if (!reduced && type === 'mandala_iris' && registerIrisProp()) {
      this.el.style.clipPath = '';
      const m = 'radial-gradient(circle at 50% 50%, transparent var(--veil-r), #000 calc(var(--veil-r) + 9%))';
      this.el.style.maskImage = m; this.el.style.webkitMaskImage = m;
    }
    const ms = type === 'cut' ? 0 : Math.max(reduced ? 600 : 300, duration * 1000);
    const run = this._run(veilFrames(type, 'reveal', reduced), ms, 'cubic-bezier(.55,0,.25,1)');
    this.pending = run;
    await run;
    if (op !== this.op) return; // a new cover started meanwhile
    this.pending = null;
    this.el.style.opacity = '0';
    this.el.style.pointerEvents = 'none';
    this._reset();
    this._classes(null, null);
    this._setState('clear');
  }
}

// ======================================================================= opening visuals
// Golden mandala: thin line rings, lotus petals, shatkona and spokes in the XY plane, drawn on
// progressively by a per-vertex "reveal time" attribute (one draw call per rotating layer).
const MANDALA_VERT = /* glsl */`
attribute float aReveal;
varying float vReveal;
void main() { vReveal = aReveal; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;
const MANDALA_FRAG = /* glsl */`
uniform float uProgress; uniform float uOpacity; uniform float uBoost; uniform vec3 uColor; uniform vec3 uHot;
varying float vReveal;
void main() {
  float lag = uProgress - vReveal;
  if (lag < 0.0) discard;
  float head = smoothstep(0.035, 0.0, lag);         // bright leading edge of the stroke
  vec3 col = mix(uColor, uHot, head) * (1.0 + uBoost);
  gl_FragColor = vec4(col, uOpacity * (0.55 + 0.45 * head));
  #include <colorspace_fragment>
}`;

function polarPt(r, a) { return [Math.cos(a) * r, Math.sin(a) * r]; }

/** Builds line strokes; each stroke = polyline points + [start, end] reveal window (0..1). */
function mandalaStrokes() {
  const layers = { inner: [], middle: [], outer: [] };
  const TAU = Math.PI * 2;
  const ring = (layer, r, t0, t1, seg = 220) => {
    const pts = []; for (let i = 0; i <= seg; i++) pts.push(polarPt(r, (i / seg) * TAU + Math.PI / 2));
    layers[layer].push({ pts, t0, t1 });
  };
  const petals = (layer, n, r0, r1, width, t0, t1, phase = 0) => {
    for (let k = 0; k < n; k++) {
      const c = (k / n) * TAU + phase, pts = [], seg = 40;
      for (let i = 0; i <= seg; i++) { const u = i / seg; pts.push(polarPt(r0 + (r1 - r0) * u, c - width * Math.pow(Math.sin(Math.PI * u), 0.8) * (1 - 0.35 * u))); }
      for (let i = seg; i >= 0; i--) { const u = i / seg; pts.push(polarPt(r0 + (r1 - r0) * u, c + width * Math.pow(Math.sin(Math.PI * u), 0.8) * (1 - 0.35 * u))); }
      const s = t0 + (t1 - t0) * (k / n) * 0.6;
      layers[layer].push({ pts, t0: s, t1: s + (t1 - t0) * 0.4 });
    }
  };
  const triangle = (layer, r, rot, t0, t1) => {
    const pts = []; for (let i = 0; i <= 3; i++) pts.push(polarPt(r, rot + (i / 3) * TAU));
    layers[layer].push({ pts, t0, t1 });
  };
  const spokes = (layer, n, r0, r1, t0, t1) => {
    for (let k = 0; k < n; k++) { const a = (k / n) * TAU; const s = t0 + (t1 - t0) * (k / n) * 0.7; layers[layer].push({ pts: [polarPt(r0, a), polarPt(r1, a)], t0: s, t1: s + (t1 - t0) * 0.3 }); }
  };
  // inner yantra
  ring('inner', 0.22, 0.0, 0.1, 64);
  triangle('inner', 0.95, Math.PI / 2, 0.06, 0.2);
  triangle('inner', 0.95, -Math.PI / 2, 0.1, 0.24);
  ring('inner', 1.02, 0.12, 0.26);
  petals('inner', 8, 1.05, 1.75, 0.3, 0.2, 0.42);
  // middle lotus
  ring('middle', 1.8, 0.3, 0.44);
  petals('middle', 16, 1.85, 2.75, 0.16, 0.36, 0.62, Math.PI / 16);
  ring('middle', 2.82, 0.5, 0.64);
  ring('middle', 2.95, 0.54, 0.68);
  // outer gate
  petals('outer', 32, 3.0, 3.55, 0.085, 0.56, 0.84);
  ring('outer', 3.62, 0.66, 0.8);
  spokes('outer', 64, 3.68, 3.9, 0.7, 0.92);
  ring('outer', 3.96, 0.78, 0.94);
  ring('outer', 4.5, 0.86, 1.0, 260);
  return layers;
}

export function buildMandala() {
  const group = new THREE.Group();
  group.name = 'mandala';
  const uniforms = {
    uProgress: { value: 0 }, uOpacity: { value: 0.85 }, uBoost: { value: 0 },
    uColor: { value: new THREE.Color('#C9A24B') }, uHot: { value: new THREE.Color('#fff0c4') },
  };
  const mat = new THREE.ShaderMaterial({
    uniforms, vertexShader: MANDALA_VERT, fragmentShader: MANDALA_FRAG,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, fog: false, toneMapped: false,
  });
  const layers = {};
  const strokes = mandalaStrokes();
  for (const name of Object.keys(strokes)) {
    const pos = [], rev = [];
    for (const s of strokes[name]) {
      const n = s.pts.length;
      for (let i = 0; i < n - 1; i++) {
        const a = s.pts[i], b = s.pts[i + 1];
        pos.push(a[0], a[1], 0, b[0], b[1], 0);
        rev.push(s.t0 + (s.t1 - s.t0) * (i / (n - 1)), s.t0 + (s.t1 - s.t0) * ((i + 1) / (n - 1)));
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute('aReveal', new THREE.Float32BufferAttribute(rev, 1));
    const lines = new THREE.LineSegments(g, mat);
    lines.frustumCulled = false;
    lines.renderOrder = 4;
    layers[name] = lines;
    group.add(lines);
  }
  // bead ring (points) for texture
  const beads = [];
  for (let i = 0; i < 108; i++) { const a = (i / 108) * Math.PI * 2; beads.push(Math.cos(a) * 4.23, Math.sin(a) * 4.23, 0); }
  const bg = new THREE.BufferGeometry();
  bg.setAttribute('position', new THREE.Float32BufferAttribute(beads, 3));
  const beadMat = new THREE.PointsMaterial({ color: '#E3C77E', size: 0.05, transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending, fog: false });
  const beadPts = new THREE.Points(bg, beadMat);
  beadPts.renderOrder = 4;
  layers.outer.add(beadPts);
  return {
    group, uniforms, layers,
    setProgress(p) { uniforms.uProgress.value = p; beadMat.opacity = smooth(0.85, 1.0, p) * 0.9; },
    setBoost(b) { uniforms.uBoost.value = b; },
    spin(dt, speed = 1) {
      layers.inner.rotation.z += dt * 0.025 * speed;
      layers.middle.rotation.z -= dt * 0.014 * speed;
      layers.outer.rotation.z += dt * 0.007 * speed;
    },
  };
}

/** Opening starfield: stars appear progressively (uProgress 0..1); brighter stars first. */
export function buildOpeningStars(count = 2400) {
  const rnd = seededRandom('opening-stars');
  const pos = new Float32Array(count * 3), appear = new Float32Array(count), size = new Float32Array(count), tint = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    const u = rnd() * 2 - 1, th = rnd() * Math.PI * 2, s = Math.sqrt(1 - u * u);
    const r = 260 + rnd() * 80;
    pos[i * 3] = s * Math.cos(th) * r; pos[i * 3 + 1] = u * r * 0.85 + 20; pos[i * 3 + 2] = s * Math.sin(th) * r - 60;
    const bright = Math.pow(rnd(), 3.0);
    size[i] = 1.0 + bright * 3.2;
    appear[i] = Math.min(0.95, (1 - bright) * 0.75 + rnd() * 0.25);
    tint[i] = rnd();
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('aAppear', new THREE.BufferAttribute(appear, 1));
  g.setAttribute('aSize', new THREE.BufferAttribute(size, 1));
  g.setAttribute('aTint', new THREE.BufferAttribute(tint, 1));
  const uniforms = { uProgress: { value: 0 }, uTime: { value: 0 }, uPx: { value: 1 } };
  const mat = new THREE.ShaderMaterial({
    uniforms,
    vertexShader: /* glsl */`
      attribute float aAppear; attribute float aSize; attribute float aTint;
      uniform float uProgress; uniform float uTime; uniform float uPx;
      varying float vA; varying float vTint;
      void main() {
        vTint = aTint;
        float on = smoothstep(aAppear, aAppear + 0.06, uProgress);
        float tw = 0.7 + 0.3 * sin(uTime * (0.6 + aTint * 2.2) + aTint * 90.0);
        vA = on * tw;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = aSize * uPx * (0.6 + 0.4 * on);
      }`,
    fragmentShader: /* glsl */`
      varying float vA; varying float vTint;
      void main() {
        float d = length(gl_PointCoord * 2.0 - 1.0);
        float a = pow(smoothstep(1.0, 0.0, d), 2.2) * vA;
        if (a < 0.004) discard;
        vec3 c = mix(vec3(0.78, 0.84, 1.0), vec3(1.0, 0.9, 0.72), vTint);
        gl_FragColor = vec4(c, a);
        #include <colorspace_fragment>
      }`,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, fog: false, toneMapped: false,
  });
  const pts = new THREE.Points(g, mat);
  pts.frustumCulled = false;
  pts.name = 'opening-stars';
  return { points: pts, uniforms };
}

/**
 * Fallback palm-leaf manuscript (used only if obj.palm_leaf cannot be built by the asset library):
 * a bound stack of leaves between two wooden covers, tied with a cord. Lies in the XZ plane.
 */
export function buildPalmLeafFallback({ materials, geometries }) {
  const g = new THREE.Group();
  g.name = 'palm_leaf_fallback';
  const leafGeo = geometries.get('palmleaf:leaf', () => { const b = new THREE.BoxGeometry(3.0, 0.014, 0.34, 1, 1, 1); return b; });
  const coverGeo = geometries.get('palmleaf:cover', () => new THREE.BoxGeometry(3.08, 0.06, 0.38));
  const cordGeo = geometries.get('palmleaf:cord', () => new THREE.CylinderGeometry(0.012, 0.012, 0.4, 6));
  const beadGeo = geometries.get('palmleaf:bead', () => new THREE.SphereGeometry(0.035, 12, 8));
  const wood = materials.standard({ color: '#4a2f1a', roughness: 0.62, metalness: 0.05 });
  const gold = materials.standard({ color: '#C9A24B', roughness: 0.3, metalness: 0.85, emissive: '#C9A24B', emissiveIntensity: 0.18 });
  const leafTones = ['#cdb07a', '#c4a46c', '#d6bc88', '#bf9f66'];
  const rnd = seededRandom('palmleaf');
  for (let i = 0; i < 12; i++) {
    const leaf = new THREE.Mesh(leafGeo, materials.standard({ color: leafTones[i % 4], roughness: 0.85, metalness: 0 }));
    leaf.position.set((rnd() - 0.5) * 0.03, 0.04 + i * 0.016, (rnd() - 0.5) * 0.02);
    leaf.rotation.y = (rnd() - 0.5) * 0.012;
    g.add(leaf);
  }
  const bottom = new THREE.Mesh(coverGeo, wood); bottom.position.y = 0.0;
  const top = new THREE.Mesh(coverGeo, wood); top.position.y = 0.04 + 12 * 0.016 + 0.02;
  g.add(bottom, top);
  for (const x of [-0.75, 0.75]) {
    const cord = new THREE.Mesh(cordGeo, gold); cord.position.set(x, 0.12, 0); g.add(cord);
    const bead = new THREE.Mesh(beadGeo, gold); bead.position.set(x, top.position.y + 0.06, 0); g.add(bead);
  }
  // gilded edge line on the top cover
  const edge = new THREE.Mesh(geometries.get('palmleaf:edge', () => new THREE.BoxGeometry(2.9, 0.008, 0.02)), gold);
  edge.position.set(0, top.position.y + 0.034, 0.15);
  g.add(edge);
  return g;
}
