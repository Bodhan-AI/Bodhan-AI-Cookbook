// MAHABHARATA — The Living Epic · procedural sound
// ------------------------------------------------------------------------------------------------
// Everything here is synthesized live with the WebAudio API: no audio files, no samples.
//
//   audio.unlock()            call INSIDE a user gesture; creates/resumes the AudioContext. Nothing
//                             makes a sound before this.
//   audio.setBed(bed, vol)    ambient bed from the DATA_CONTRACT vocabulary, 2.5 s equal-power crossfade.
//                             Also accepts a chapter's `ambient_audio` object {bed, volume, cues:[{cue, at}]}.
//   audio.cue(name, vol)      one-shot cue (conch, war_drum, ...).
//   audio.setVolume(v) · audio.setMuted(b) · audio.muted · audio.volume · audio.unlocked
//
// Extras (additive, optional): setAmbient(b), setReducedMotion(b|null), applySettings(settings),
// connect({store, bus}), level(), bed, ambient, supported, beds, cues.
//
// Mix philosophy: restrained. Beds sit far below the narration-free UI (a bus compressor is SET to hold
// them near -30 dBFS with peaks under about -24 dBFS; these are design targets from the compressor
// settings, not measured loudness — verify by ear / meter in a real browser); cues are capped and, in reduced-motion mode, softened
// (lower ceiling, slower attacks, a gentle low-pass, no thunder crack). A master limiter guards the
// output. If WebAudio is unavailable every method is a safe no-op.

const BEDS = Object.freeze(['forest', 'river', 'palace', 'wind', 'battlefield_distant', 'battlefield_near',
  'celestial', 'rain', 'fire_crackle', 'assembly_murmur', 'mountain_wind', 'silence']);
const CUES = Object.freeze(['conch', 'war_drum', 'chariot_roll', 'bell', 'dice_roll', 'bowstring', 'gong',
  'wind_gust', 'water_flow', 'crowd_gasp', 'heartbeat', 'choir_drone', 'thunder', 'footsteps']);

const XFADE = 2.5;            // bed crossfade, seconds
const LOOKAHEAD = 0.6;        // scheduler lookahead for sparse bed events, seconds
const BED_GAIN = 1.0;         // bed level at volume 1 (the bed compressor does the taming)
const CUE_BUS = 0.7;          // cue bus level
const CUE_CAP = 1.0;          // per-cue ceiling (normal)
const CUE_CAP_REDUCED = 0.5;  // per-cue ceiling (reduced motion)
const MAX_CUES = 8;           // concurrent one-shots
const DEDUPE_MS = 90;         // identical cue requests closer than this are merged

const AC = typeof window !== 'undefined' ? (window.AudioContext || window.webkitAudioContext) : null;

const S = {
  ctx: null, failed: false, unlocked: false,
  volume: 0.6, muted: false, ambient: true, reduced: systemReducedMotion(), reducedForced: null,
  bedName: null, bedVolume: 0.5, current: null,
  activeCues: 0, lastCue: new Map(), chapterTimers: [], buffers: {}, ks: new Map(),
  g: null, connected: null, warned: new Set(), suspendTimer: 0, visBound: false,
};
const isHidden = () => typeof document !== 'undefined' && !!document.hidden;
/** 'suspended' and Safari's 'interrupted' both need a resume(); 'closed' cannot be resumed. */
const needsResume = (c) => !!c && c.state !== 'running' && c.state !== 'closed';

// ------------------------------------------------------------------------------------ small helpers
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const clamp01 = (v) => clamp(Number.isFinite(+v) ? +v : 0, 0, 1);
const rand = (a, b) => a + Math.random() * (b - a);
const pick = (arr) => arr[(Math.random() * arr.length) | 0];
const EPS = 0.0001;

function systemReducedMotion() {
  try { return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches); }
  catch { return false; }
}
// Follow the OS setting live unless the user forced reduced motion on/off in settings.
try {
  const mq = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)');
  const onMq = () => { if (S.reducedForced == null) setReducedMotion(null); };
  if (mq && mq.addEventListener) mq.addEventListener('change', onMq);
  else if (mq && mq.addListener) mq.addListener(onMq);
} catch { /* no matchMedia (tests / old browsers) */ }

function warnOnce(key, msg) {
  if (S.warned.has(key)) return;
  S.warned.add(key);
  try { console.warn('[voyage audio] ' + msg); } catch { /* ignore */ }
}
function ready() { return !!(S.ctx && S.unlocked && S.ctx.state !== 'closed'); }

/** Simple attack / hold / exponential-release envelope on a gain AudioParam. */
function env(param, t, a, peak, hold, rel) {
  param.cancelScheduledValues(t);
  param.setValueAtTime(EPS, t);
  param.linearRampToValueAtTime(Math.max(peak, EPS * 2), t + a);
  if (hold > 0) param.setValueAtTime(Math.max(peak, EPS * 2), t + a + hold);
  param.exponentialRampToValueAtTime(EPS, t + a + Math.max(0, hold) + rel);
}
/** Percussive envelope: instant-ish attack then exponential decay. */
function perc(param, t, peak, decay, attack = 0.003) {
  param.setValueAtTime(EPS, t);
  param.linearRampToValueAtTime(Math.max(peak, EPS * 2), t + attack);
  param.exponentialRampToValueAtTime(EPS, t + attack + decay);
}

// ------------------------------------------------------------------------- buffers (generated once)
function noiseBuffer(kind) {
  if (S.buffers[kind]) return S.buffers[kind];
  const c = S.ctx, sr = c.sampleRate, len = (sr * 4) | 0;
  const buf = c.createBuffer(1, len, sr);
  const d = buf.getChannelData(0);
  if (kind === 'pink') {            // Paul Kellet's refined pink filter
    let b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0;
    for (let i = 0; i < len; i++) {
      const w = Math.random() * 2 - 1;
      b0 = 0.99886 * b0 + w * 0.0555179; b1 = 0.99332 * b1 + w * 0.0750759;
      b2 = 0.96900 * b2 + w * 0.1538520; b3 = 0.86650 * b3 + w * 0.3104856;
      b4 = 0.55000 * b4 + w * 0.5329522; b5 = -0.7616 * b5 - w * 0.0168980;
      d[i] = b0 + b1 + b2 + b3 + b4 + b5 + b6 + w * 0.5362; b6 = w * 0.115926;
    }
  } else if (kind === 'brown') {    // leaky integrated white noise
    let last = 0;
    for (let i = 0; i < len; i++) { last = (last + 0.02 * (Math.random() * 2 - 1)) / 1.02; d[i] = last; }
  } else {
    for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
  }
  // Remove DC drift and the loop-seam jump, then normalise to a sane peak.
  let mean = 0;
  for (let i = 0; i < len; i++) mean += d[i];
  mean /= len;
  const jump = (d[len - 1] - mean) - (d[0] - mean);
  let peak = 0;
  for (let i = 0; i < len; i++) { d[i] = d[i] - mean - jump * (i / len); peak = Math.max(peak, Math.abs(d[i])); }
  const k = peak > 0 ? 0.9 / peak : 1;
  for (let i = 0; i < len; i++) d[i] *= k;
  return (S.buffers[kind] = buf);
}

/** A small, dark, stereo hall impulse response — generated, not sampled. */
function impulseResponse(seconds = 3.2) {
  const c = S.ctx, sr = c.sampleRate, len = (sr * seconds) | 0, pre = (sr * 0.012) | 0;
  const ir = c.createBuffer(2, len, sr);
  for (let ch = 0; ch < 2; ch++) {
    const d = ir.getChannelData(ch);
    let y = 0;
    for (let i = pre; i < len; i++) {
      const t = i / len;
      const a = 0.85 - 0.7 * t;                    // tail gets darker as it decays
      y += a * ((Math.random() * 2 - 1) - y);
      d[i] = y * Math.pow(1 - t, 2.4) * 0.6;
    }
  }
  return ir;
}

/** Karplus–Strong plucked string rendered into a buffer (cached per pitch). */
function ksBuffer(freq, seconds = 1.6, damping = 0.996) {
  const key = Math.round(freq) + ':' + seconds;
  if (S.ks.has(key)) return S.ks.get(key);
  const c = S.ctx, sr = c.sampleRate, n = Math.max(2, Math.round(sr / freq)), len = (sr * seconds) | 0;
  const line = new Float32Array(n);
  let lp = 0;
  for (let i = 0; i < n; i++) { lp += 0.5 * ((Math.random() * 2 - 1) - lp); line[i] = lp; } // softened excitation
  const buf = c.createBuffer(1, len, sr);
  const d = buf.getChannelData(0);
  let idx = 0;
  for (let i = 0; i < len; i++) {
    const cur = line[idx], nxt = line[(idx + 1) % n];
    d[i] = cur;
    line[idx] = damping * 0.5 * (cur + nxt);
    idx = (idx + 1) % n;
  }
  if (S.ks.size > 24) S.ks.clear();
  S.ks.set(key, buf);
  return buf;
}

// --------------------------------------------------------------------------------- node groups
const OWNER = new WeakMap();   // group.out → group, so nested one-shots can find a post-fader reverb send

/** Where a one-shot feeding `dest` should send its reverb: the nearest enclosing group's post-fader
 *  send (so bed fades / ambient-off / cue levels also scale the reverb), else the hall directly. */
function wetTargetFor(dest) {
  let o = OWNER.get(dest), guard = 0;
  while (o && guard++ < 8) { if (o.wetIn) return o.wetIn; o = OWNER.get(o.dest); }
  return S.g.reverbIn;
}

/**
 * A Group owns a set of nodes and sources and tears them down together.
 *   beds:      new Group(dest, 0, {wet: true})        → disposed explicitly on crossfade
 *   one-shots: new Group(dest, 1, {until: t})         → disposes itself when all its sources end
 * With {wet: true} the group also owns a post-fader reverb send whose fader mirrors `out` (see faders()).
 */
class Group {
  constructor(dest, level = 1, opts = {}) {
    this.c = S.ctx;
    this.nodes = []; this.sources = []; this.timers = [];
    this.live = 0; this.dead = false;
    this.dest = dest;
    this.until = opts.until;                       // default stop time for one-shot sources
    this.auto = opts.until != null;
    this.onDispose = opts.onDispose || null;
    this.out = this.gain(level);
    this.out.connect(dest);
    OWNER.set(this.out, this);
    if (opts.wet) {
      this.wetIn = this.gain(1);
      this.wetFader = this.gain(level);
      this.wetIn.connect(this.wetFader);
      this.wetFader.connect(opts.wetDest || wetTargetFor(dest));
    }
  }
  /** Gain params that must move together when this group is faded. */
  faders() { return this.wetFader ? [this.out.gain, this.wetFader.gain] : [this.out.gain]; }
  /** Silent source that keeps a one-shot container alive until its `until` time. */
  keepAlive(t) { const o = this.osc('sine', 1), z = this.gain(0); o.connect(z); z.connect(this.out); this.play(o, t); }
  track(n) { this.nodes.push(n); return n; }
  gain(v = 1) { const g = this.c.createGain(); g.gain.value = v; return this.track(g); }
  filter(type, freq, Q = 0.707, gainDb = 0) {
    const f = this.c.createBiquadFilter();
    f.type = type; f.frequency.value = freq; f.Q.value = Q; f.gain.value = gainDb;
    return this.track(f);
  }
  pan(p = 0) {
    if (typeof this.c.createStereoPanner === 'function') {
      const s = this.c.createStereoPanner(); s.pan.value = clamp(p, -1, 1); return this.track(s);
    }
    return this.gain(1);                           // old Safari: no panning, still routes
  }
  osc(type, freq, detune = 0) {
    const o = this.c.createOscillator();
    o.type = type; o.frequency.value = freq; o.detune.value = detune;
    return this.track(o);
  }
  noise(kind = 'white', rate = 1) {
    const s = this.c.createBufferSource();
    s.buffer = noiseBuffer(kind); s.loop = true; s.playbackRate.value = rate;
    return this.track(s);
  }
  buffer(buf, rate = 1) {
    const s = this.c.createBufferSource();
    s.buffer = buf; s.playbackRate.value = rate;
    return this.track(s);
  }
  /** Start a source (and stop it at t1 / the group's `until`). Looping noise starts at a random offset. */
  play(src, t0, t1) {
    const stopAt = t1 != null ? t1 : this.until;
    try {
      if (src.loop && src.buffer) src.start(t0, Math.random() * Math.max(0, src.buffer.duration - 0.05));
      else src.start(t0);
      if (stopAt != null) src.stop(Math.max(stopAt, t0 + 0.01));
    } catch { return src; }
    this.live++;
    this.sources.push(src);
    src.onended = () => { this.live--; if (this.auto && this.live <= 0) this.dispose(); };
    return src;
  }
  /** Low-frequency oscillator added onto an AudioParam. */
  lfo(param, rate, depth, type = 'sine', t0, t1) {
    const o = this.osc(type, rate);
    const g = this.gain(depth);
    o.connect(g); g.connect(param);
    this.play(o, t0 != null ? t0 : this.c.currentTime, t1);
    return o;
  }
  /** Sparse event scheduler in AudioContext time: fn(t) roughly every minS..maxS seconds. */
  every(minS, maxS, fn, firstDelay) {
    let next = this.c.currentTime + (firstDelay != null ? firstDelay : rand(minS, maxS));
    const tick = () => {
      if (this.dead) return;
      const now = this.c.currentTime;
      if (next < now) next = now + rand(0.02, 0.25);        // throttled tab: no backlog bursts
      let guard = 0;
      while (next < now + LOOKAHEAD && guard++ < 64) {
        try { fn(next); } catch (e) { warnOnce('bed-event', 'bed event failed: ' + (e && e.message)); }
        next += rand(minS, maxS);
      }
    };
    this.timers.push(setInterval(tick, 200));
    tick();
  }
  /** Stop everything at `at` (context time) and disconnect shortly after. Idempotent. */
  dispose(at) {
    if (this.dead) return;
    this.dead = true;
    const now = this.c.currentTime, when = Math.max(now, at != null ? at : now);
    this.timers.forEach((id) => clearInterval(id));
    this.timers = [];
    for (const s of this.sources) { try { s.stop(when); } catch { /* already stopped */ } }
    const cleanup = () => {
      for (const n of this.nodes) { try { n.disconnect(); } catch { /* ignore */ } }
      this.nodes = []; this.sources = [];
    };
    const ms = (when - now) * 1000 + 120;
    if (ms <= 130) cleanup(); else setTimeout(cleanup, ms);
    if (this.onDispose) { const f = this.onDispose; this.onDispose = null; try { f(); } catch { /* ignore */ } }
  }
}

/** A self-cleaning one-shot group feeding `dest`, optionally panned, optionally sent to the hall. */
function shot(dest, t, life, { level = 1, pan = 0, wet = 0 } = {}) {
  const v = new Group(dest, 1, { until: t + life });
  if (pan) {
    const p = v.pan(pan);
    v.out.disconnect(); v.out.connect(p); p.connect(dest);
  }
  v.out.gain.value = level;
  if (wet > 0 && S.g) { const w = v.gain(wet); v.out.connect(w); w.connect(wetTargetFor(dest)); }
  return v;
}

/** Glide one or more gain params to `target` over `dur` s. shape: 'linear' | 'power' (equal-power
 *  sin/cos, built from short linear segments — no setValueCurveAtTime, so it can be interrupted safely). */
function rampTo(params, target, dur, shape = 'power') {
  const now = S.ctx.currentTime;
  for (const p of params) {
    const from = p.value;
    let held = false;
    if (typeof p.cancelAndHoldAtTime === 'function') { try { p.cancelAndHoldAtTime(now); held = true; } catch { /* fall through */ } }
    if (!held) { p.cancelScheduledValues(now); p.setValueAtTime(from, now); }
    const steps = shape === 'power' ? 10 : 1;
    for (let i = 1; i <= steps; i++) {
      const x = i / steps;
      const k = shape !== 'power' ? x : (target >= from ? Math.sin(x * Math.PI / 2) : 1 - Math.cos(x * Math.PI / 2));
      p.linearRampToValueAtTime(from + (target - from) * k, now + dur * x);
    }
  }
}

// ------------------------------------------------------------------------------- graph creation
function init() {
  let c;
  try { c = new AC({ latencyHint: 'playback' }); }
  catch { c = new AC(); }                          // old webkitAudioContext rejects the options bag
  S.ctx = c;
  S.buffers = {}; S.ks.clear();                    // generated buffers belong to one context
  const g = {};
  g.master = c.createGain(); g.master.gain.value = 0;
  g.limiter = c.createDynamicsCompressor();
  setComp(g.limiter, -10, 3, 20, 0.003, 0.25);
  g.master.connect(g.limiter); g.limiter.connect(c.destination);

  g.analyser = c.createAnalyser(); g.analyser.fftSize = 512;
  g.limiter.connect(g.analyser);
  g.meter = new Float32Array(g.analyser.fftSize);

  // Beds: bus compressor keeps ambience tame and even, then the ambient on/off fader.
  g.bedComp = c.createDynamicsCompressor();
  setComp(g.bedComp, -32, 10, 6, 0.05, 0.6);
  g.bedBus = c.createGain(); g.bedBus.gain.value = S.ambient ? 1 : 0;
  g.bedComp.connect(g.bedBus); g.bedBus.connect(g.master);
  g.bedWet = c.createGain(); g.bedWet.gain.value = S.ambient ? 1 : 0;   // beds' reverb sends, same on/off fader

  // Cues: tone filter (opened fully unless reduced motion) then the cue bus.
  g.cueTone = c.createBiquadFilter(); g.cueTone.type = 'lowpass';
  g.cueTone.frequency.value = S.reduced ? 4200 : 20000; g.cueTone.Q.value = 0.5;
  g.cueBus = c.createGain(); g.cueBus.gain.value = CUE_BUS;
  g.cueTone.connect(g.cueBus); g.cueBus.connect(g.master);

  // Shared hall reverb (procedural impulse response).
  g.reverbIn = c.createGain();
  g.reverb = c.createConvolver();
  try { g.reverb.buffer = impulseResponse(); } catch { /* reverb optional */ }
  g.reverbOut = c.createGain(); g.reverbOut.gain.value = 0.45;
  g.reverbIn.connect(g.reverb); g.reverb.connect(g.reverbOut); g.reverbOut.connect(g.master);
  g.bedWet.connect(g.reverbIn);

  S.g = g;
  try { c.onstatechange = () => { if (c.state === 'closed' && S.ctx === c) S.unlocked = false; }; } catch { /* ignore */ }
  if (!S.visBound && typeof document !== 'undefined') { document.addEventListener('visibilitychange', onVisibility); S.visBound = true; }
}
/** Forget a closed context so the next unlock() can build a fresh one. */
function resetClosed() {
  clearChapterCues();
  clearTimeout(S.suspendTimer);
  S.ctx = null; S.g = null; S.current = null; S.unlocked = false;
  S.activeCues = 0; S.lastCue.clear();
}
function setComp(k, threshold, knee, ratio, attack, release) {
  try {
    k.threshold.value = threshold; k.knee.value = knee; k.ratio.value = ratio;
    k.attack.value = attack; k.release.value = release;
  } catch { /* defaults are fine */ }
}
function onVisibility() {
  if (!S.ctx || !S.unlocked) return;
  if (document.hidden) { if (S.ctx.state === 'running') S.ctx.suspend().catch(() => {}); }
  else if (!S.muted && needsResume(S.ctx)) { S.ctx.resume().catch(() => {}); }
}

/** Master level: perceptual curve, smoothed. Muted → fade out, then suspend to save CPU. */
function applyMaster() {
  if (!S.ctx || !S.g) return;
  const c = S.ctx, now = c.currentTime;
  const target = S.muted || !S.unlocked ? 0 : Math.pow(S.volume, 1.7);
  const p = S.g.master.gain;
  p.cancelScheduledValues(now);
  p.setValueAtTime(p.value, now);
  p.setTargetAtTime(target, now, 0.12);
  clearTimeout(S.suspendTimer);
  if (S.muted) {
    S.suspendTimer = setTimeout(() => { if (S.muted && S.ctx) S.ctx.suspend().catch(() => {}); }, 900);
  } else if (S.unlocked && needsResume(c) && !isHidden()) {
    c.resume().catch(() => {});
  }
}

// ======================================================================================= BEDS
// Each builder fills a bed Group `g` (its g.out is faded by the crossfader). Levels are pre-bus-
// compressor; keep them modest.

/** Looping filtered-noise layer. */
function noiseLayer(g, kind, ftype, freq, Q, level, dest) {
  const src = g.noise(kind);
  const f = g.filter(ftype, freq, Q);
  const a = g.gain(level);
  src.connect(f); f.connect(a); a.connect(dest || g.out);
  g.play(src, g.c.currentTime);
  return { src, f, a };
}
/** Post-fader reverb send of the whole bed. */
function bedWet(g, amount) {
  const w = g.gain(amount);
  g.out.connect(w); w.connect(S.g.bedWet);
  return w;
}

/** Tanpura-like drone: the four-string Pa–Sa–Sa–Sa(low) pluck cycle with a slow "jawari" bloom,
 *  over a sustained, gently shimmering Sa–Pa pad of detuned oscillators. */
function tanpura(g, sa, level) {
  const bus = g.gain(level);
  bus.connect(g.out);
  const cycle = [0.75, 1, 1, 0.5];                 // Pa (lower octave), Sa, Sa, Sa (lower octave)
  let step = 0;
  g.every(1.0, 1.3, (t) => tanpuraPluck(bus, t, sa * cycle[step++ % cycle.length]), 0.3);

  // sustained pad: Sa and Pa, each a detuned pair, amplitude shimmering at slightly different rates
  const pad = g.gain(0.18);
  const padLp = g.filter('lowpass', 1400, 0.5);
  pad.connect(padLp); padLp.connect(bus);
  [[sa, 0.5], [sa * 1.5, 0.3], [sa * 2, 0.22], [sa * 0.5, 0.4]].forEach(([f, lv], i) => {
    const amp = g.gain(lv);
    amp.connect(pad);
    [-5, 4].forEach((cents) => {
      const o = g.osc(i === 3 ? 'sine' : 'triangle', f, cents);
      o.connect(amp);
      g.play(o, g.c.currentTime);
    });
    g.lfo(amp.gain, 0.07 + i * 0.031, lv * 0.35);  // shimmer
  });
}
function tanpuraPluck(dest, t, f) {
  const dur = 4.8;
  const v = shot(dest, t, dur + 0.1);
  const jaw = v.filter('peaking', 700, 3.5, 9);    // the buzzing bridge: a resonance sweeping upward
  jaw.frequency.setValueAtTime(650, t);
  jaw.frequency.exponentialRampToValueAtTime(3400, t + 3.0);
  const lp = v.filter('lowpass', 2800, 0.6);
  lp.frequency.setValueAtTime(3000, t);
  lp.frequency.exponentialRampToValueAtTime(650, t + dur);
  const amp = v.gain(0);
  jaw.connect(lp); lp.connect(amp); amp.connect(v.out);
  [-4, 3].forEach((cents) => { const o = v.osc('sawtooth', f, cents); o.connect(jaw); v.play(o, t); });
  const sub = v.osc('sine', f); const subG = v.gain(0.6); sub.connect(subG); subG.connect(amp); v.play(sub, t);
  amp.gain.setValueAtTime(EPS, t);
  amp.gain.linearRampToValueAtTime(0.5, t + 0.035);
  amp.gain.exponentialRampToValueAtTime(0.26, t + 0.6);
  amp.gain.exponentialRampToValueAtTime(EPS, t + dur);
}

/** Bird calls: whistles, trills and (rarely) a koel-like rising call. */
function bird(dest, t) {
  const kind = Math.random() < 0.12 ? 'koel' : pick(['whistle', 'whistle', 'trill', 'chirp']);
  const life = kind === 'koel' ? 3.2 : 1.4;
  const v = shot(dest, t, life, { pan: rand(-0.85, 0.85), wet: 0.35 });
  const o = v.osc('sine', 3000);
  const a = v.gain(0);
  o.connect(a); a.connect(v.out);
  v.play(o, t);
  const f = o.frequency, p = a.gain;
  f.setValueAtTime(3000, t); p.setValueAtTime(EPS, t);
  if (kind === 'whistle') {
    const base = rand(2100, 3400), n = 2 + ((Math.random() * 2) | 0);
    for (let i = 0; i < n; i++) {
      const s = t + i * rand(0.14, 0.2);
      f.setValueAtTime(base * rand(0.95, 1.05), s);
      f.exponentialRampToValueAtTime(base * rand(1.2, 1.45), s + 0.1);
      p.setValueAtTime(EPS, s); p.linearRampToValueAtTime(0.045, s + 0.02); p.exponentialRampToValueAtTime(EPS, s + 0.12);
    }
  } else if (kind === 'trill') {
    const base = rand(3800, 5200), n = 6 + ((Math.random() * 6) | 0);
    for (let i = 0; i < n; i++) {
      const s = t + i * 0.055;
      f.setValueAtTime(base, s); f.exponentialRampToValueAtTime(base * 0.78, s + 0.04);
      p.setValueAtTime(EPS, s); p.linearRampToValueAtTime(0.022, s + 0.008); p.exponentialRampToValueAtTime(EPS, s + 0.045);
    }
  } else if (kind === 'chirp') {
    const base = rand(2600, 4200);
    f.setValueAtTime(base * 1.3, t); f.exponentialRampToValueAtTime(base * 0.7, t + 0.07);
    p.linearRampToValueAtTime(0.035, t + 0.01); p.exponentialRampToValueAtTime(EPS, t + 0.08);
  } else { // koel-like: rising two-part "ku-oo", each repetition a little higher
    const base = rand(620, 760);
    for (let i = 0; i < 3; i++) {
      const s = t + i * 0.9, k = 1 + i * 0.08;
      f.setValueAtTime(base * k, s); f.exponentialRampToValueAtTime(base * k * 1.35, s + 0.32);
      p.setValueAtTime(EPS, s); p.linearRampToValueAtTime(0.03, s + 0.05);
      p.setValueAtTime(0.03, s + 0.28); p.exponentialRampToValueAtTime(EPS, s + 0.42);
    }
  }
}

/** Short filtered noise click (rain drops, fire crackles, dice). */
function click(dest, t, { freq = 3000, Q = 1, level = 0.03, decay = 0.012, pan = 0, type = 'bandpass' } = {}) {
  const v = shot(dest, t, decay + 0.06, { pan });
  const n = v.noise('white');
  const f = v.filter(type, freq, Q);
  const a = v.gain(0);
  n.connect(f); f.connect(a); a.connect(v.out);
  v.play(n, t);
  perc(a.gain, t, level, decay, 0.001);
}

/** Pitched membrane thump (drums, heartbeat, footsteps). */
function thump(dest, t, { f0 = 110, f1 = 50, level = 0.3, decay = 0.6, sweep = 0.18, attack = 0.004, wet = 0 } = {}) {
  const v = shot(dest, t, decay + 0.1, { wet });
  const o = v.osc('sine', f0);
  const a = v.gain(0);
  o.connect(a); a.connect(v.out);
  o.frequency.setValueAtTime(f0, t);
  o.frequency.exponentialRampToValueAtTime(Math.max(20, f1), t + sweep);
  v.play(o, t);
  perc(a.gain, t, level, decay, attack);
}

/** Inharmonic metallic ring (bells, distant blade clashes). */
function ring(dest, t, f0, partials, { level = 0.1, wet = 0.3, pan = 0, attack = 0.002, scale = 1 } = {}) {
  const life = Math.max(...partials.map((p) => p[2])) * scale + 0.2;
  const v = shot(dest, t, life, { pan, wet });
  v.out.gain.value = level;
  partials.forEach(([ratio, amp, decay], i) => {
    const o = v.osc('sine', f0 * ratio, i < 2 ? rand(-3, 3) : 0);
    const a = v.gain(0);
    o.connect(a); a.connect(v.out);
    v.play(o, t);
    perc(a.gain, t, amp, decay * scale, attack);
  });
}

const BED_BUILDERS = {
  forest(g) {
    const base = noiseLayer(g, 'pink', 'lowpass', 1200, 0.5, 0.16);
    const hp = g.filter('highpass', 110); base.f.disconnect(); base.f.connect(hp); hp.connect(base.a);
    g.lfo(base.a.gain, 0.07, 0.05);                                  // leaves breathing
    const leaves = noiseLayer(g, 'white', 'bandpass', 3600, 0.6, 0.018);
    g.lfo(leaves.a.gain, 0.11, 0.014);
    // faint insect shimmer, gated in and out slowly
    const ins = g.osc('sine', 4800), am = g.gain(0.5), insG = g.gain(0.004);   // 0.5 ± 0.5 square = on/off gate
    ins.connect(am); am.connect(insG); insG.connect(g.out); g.play(ins, g.c.currentTime);
    g.lfo(am.gain, 31, 0.5, 'square'); g.lfo(insG.gain, 0.045, 0.004);
    g.every(1.6, 6.5, (t) => bird(g.out, t), 1.2);
    bedWet(g, 0.12);
  },
  river(g) {
    const body = noiseLayer(g, 'pink', 'bandpass', 520, 0.5, 0.24);
    g.lfo(body.f.frequency, 0.09, 140);
    const babble = noiseLayer(g, 'white', 'bandpass', 2400, 1.2, 0.05);
    g.lfo(babble.f.frequency, 3.1, 500); g.lfo(babble.a.gain, 4.7, 0.02);
    noiseLayer(g, 'brown', 'lowpass', 180, 0.6, 0.18);
    g.every(0.35, 1.8, (t) => {                                       // gurgles
      const v = shot(g.out, t, 0.12, { pan: rand(-0.6, 0.6) });
      const o = v.osc('sine', 700), a = v.gain(0);
      o.connect(a); a.connect(v.out); v.play(o, t);
      const f = rand(500, 1300);
      o.frequency.setValueAtTime(f, t); o.frequency.exponentialRampToValueAtTime(f * 1.7, t + 0.035);
      perc(a.gain, t, 0.016, 0.05, 0.003);
    });
    bedWet(g, 0.1);
  },
  palace(g) {
    noiseLayer(g, 'brown', 'lowpass', 220, 0.5, 0.07);                // room tone
    tanpura(g, 138.59, 0.05);                                         // Sa ≈ C#3
    g.every(18, 34, (t) => ring(g.out, t, rand(600, 680), BELL_PARTIALS, { level: 0.012, wet: 0.7, pan: rand(-0.5, 0.5), attack: 0.01 }), 9);
    bedWet(g, 0.25);
  },
  wind(g) {
    const w = noiseLayer(g, 'pink', 'bandpass', 520, 0.8, 0.22);
    g.lfo(w.f.frequency, 0.06, 300); g.lfo(w.a.gain, 0.09, 0.1);
    noiseLayer(g, 'brown', 'lowpass', 300, 0.5, 0.14);
    const wh = noiseLayer(g, 'white', 'bandpass', 900, 12, 0.014);
    g.lfo(wh.f.frequency, 0.05, 250); g.lfo(wh.a.gain, 0.07, 0.01);
    bedWet(g, 0.08);
  },
  mountain_wind(g) {
    const w = noiseLayer(g, 'pink', 'bandpass', 650, 0.7, 0.2);
    g.lfo(w.f.frequency, 0.045, 380); g.lfo(w.a.gain, 0.07, 0.1);
    noiseLayer(g, 'brown', 'lowpass', 220, 0.5, 0.2);
    [[1400, 18, 0.05], [2100, 22, 0.037]].forEach(([f, Q, r]) => {     // cold whistling edges
      const wh = noiseLayer(g, 'white', 'bandpass', f, Q, 0.012);
      g.lfo(wh.f.frequency, r, f * 0.2); g.lfo(wh.a.gain, r * 1.6, 0.009);
    });
    g.every(7, 16, (t) => gust(g.out, t, 0.12, false), 4);
    bedWet(g, 0.14);
  },
  battlefield_distant(g) {
    const rumble = noiseLayer(g, 'brown', 'lowpass', 140, 0.5, 0.26);
    g.lfo(rumble.a.gain, 0.05, 0.08);
    const mass = noiseLayer(g, 'pink', 'bandpass', 360, 0.7, 0.035);
    g.lfo(mass.a.gain, 0.08, 0.015);
    const air = noiseLayer(g, 'pink', 'bandpass', 700, 0.6, 0.05);
    g.lfo(air.f.frequency, 0.05, 250);
    g.every(2.5, 6, (t) => {                                          // far drums, mostly reverb
      const n = 2 + ((Math.random() * 3) | 0);
      for (let i = 0; i < n; i++) thump(g.out, t + i * rand(0.28, 0.4), { f0: 80, f1: 44, level: 0.05, decay: 0.9, wet: 0.6 });
    }, 1.5);
    g.every(28, 55, (t) => conchVoice(g.out, t, { level: 0.012, lowpass: 700, f: rand(200, 240), soft: true }), 14);
  },
  battlefield_near(g) {
    noiseLayer(g, 'brown', 'lowpass', 180, 0.5, 0.28);
    [[420, 0.13], [800, 0.17], [1300, 0.21]].forEach(([f, r], i) => {  // crowd roar
      const l = noiseLayer(g, 'pink', 'bandpass', f, 1.2, 0.05);
      g.lfo(l.a.gain, r, 0.025); g.lfo(l.f.frequency, r * 0.6, f * 0.12, i === 1 ? 'triangle' : 'sine');
    });
    g.every(0.9, 2.4, (t) => {
      const n = 1 + ((Math.random() * 3) | 0);
      for (let i = 0; i < n; i++) thump(g.out, t + i * 0.21, { f0: 105, f1: 48, level: 0.07, decay: 0.7 });
    }, 0.5);
    g.every(1.2, 4, (t) => {                                          // blade clashes, panned
      ring(g.out, t, rand(1500, 2100), CLASH_PARTIALS, { level: 0.014, wet: 0.35, pan: rand(-0.9, 0.9) });
      click(g.out, t, { freq: 5000, Q: 0.7, level: 0.02, decay: 0.02, type: 'highpass' });
    }, 1);
    g.every(8, 15, (t) => rumbleSweep(g.out, t, 2.8, 0.06, rand(-0.8, 0.8)), 3);
    bedWet(g, 0.1);
  },
  celestial(g) {
    const sa = 146.83;                                                // Sa ≈ D3
    tanpura(g, sa, 0.035);
    const hp = g.filter('highpass', 300);
    const padG = g.gain(1); hp.connect(padG); padG.connect(g.out);
    [1, 1.5, 2, 3, 4].forEach((r, i) => {                             // shimmering partials
      const amp = g.gain(0.0055);
      amp.connect(hp);
      [-4, 4].forEach((c) => { const o = g.osc('sine', sa * 2 * r, c); o.connect(amp); g.play(o, g.c.currentTime); });
      g.lfo(amp.gain, 0.05 + i * 0.023, 0.004);
    });
    const air = noiseLayer(g, 'white', 'bandpass', 6000, 0.5, 0.007);
    g.lfo(air.a.gain, 0.06, 0.005);
    const scale = [1, 9 / 8, 5 / 4, 3 / 2, 5 / 3];                    // Sa Re Ga Pa Dha
    g.every(2, 5, (t) => {
      const f = sa * pick([8, 16]) * pick(scale);
      const v = shot(g.out, t, 2.8, { pan: rand(-0.7, 0.7), wet: 0.8 });
      const o = v.osc('sine', f), a = v.gain(0);
      o.connect(a); a.connect(v.out); v.play(o, t);
      perc(a.gain, t, 0.007, 2.5, 0.012);
    }, 1);
    bedWet(g, 0.35);
  },
  rain(g) {
    const sheet = noiseLayer(g, 'white', 'highpass', 900, 0.5, 0.11);
    const lp = g.filter('lowpass', 7000); sheet.f.disconnect(); sheet.f.connect(lp); lp.connect(sheet.a);
    g.lfo(sheet.a.gain, 0.08, 0.02);
    noiseLayer(g, 'pink', 'bandpass', 1800, 0.4, 0.05);
    noiseLayer(g, 'brown', 'lowpass', 120, 0.5, 0.12);
    g.every(0.08, 0.3, (t) => click(g.out, t, { freq: rand(2800, 6000), Q: 2, level: rand(0.008, 0.022), decay: 0.008, pan: rand(-0.9, 0.9) }));
    g.every(25, 60, (t) => thunderVoice(g.out, t, { level: 0.08, soft: true, distant: true }), 12);
    bedWet(g, 0.06);
  },
  fire_crackle(g) {
    const roar = noiseLayer(g, 'brown', 'lowpass', 260, 0.6, 0.15);
    g.lfo(roar.a.gain, 0.4, 0.04); g.lfo(roar.a.gain, 0.13, 0.03);
    noiseLayer(g, 'white', 'bandpass', 2500, 0.3, 0.01);
    g.every(0.05, 0.38, (t) => {
      const n = 1 + ((Math.random() * 4) | 0), pan = rand(-0.5, 0.5);
      for (let i = 0; i < n; i++) {
        const s = t + i * rand(0.008, 0.03);
        if (Math.random() < 0.08) click(g.out, s, { freq: rand(500, 800), Q: 1.4, level: 0.05, decay: 0.03, pan });
        else click(g.out, s, { freq: rand(1500, 4200), Q: 0.9, level: rand(0.01, 0.045), decay: rand(0.002, 0.012), pan, type: 'highpass' });
      }
    });
    bedWet(g, 0.06);
  },
  assembly_murmur(g) {
    noiseLayer(g, 'brown', 'lowpass', 250, 0.5, 0.06);
    const src = g.noise('pink');
    g.play(src, g.c.currentTime);
    [350, 520, 800, 1150, 1700, 2500].forEach((f, i) => {             // speech-band formants, syllabic motion
      const bp = g.filter('bandpass', f * rand(0.95, 1.05), 2.5);
      const a = g.gain(0.024);
      const p = g.pan(((i % 3) - 1) * 0.6 + rand(-0.15, 0.15));
      src.connect(bp); bp.connect(a); a.connect(p); p.connect(g.out);
      g.lfo(a.gain, rand(2.6, 5.4), 0.014);
      g.lfo(a.gain, rand(0.06, 0.2), 0.008);
      g.lfo(bp.frequency, rand(0.3, 0.9), f * 0.08);
    });
    g.every(6, 14, (t) => {                                           // a nearer whisper swells and passes
      const v = shot(g.out, t, 2.4, { pan: rand(-0.8, 0.8), wet: 0.2 });
      const n = v.noise('pink'), bp = v.filter('bandpass', rand(900, 1600), 3), a = v.gain(0);
      n.connect(bp); bp.connect(a); a.connect(v.out); v.play(n, t);
      env(a.gain, t, 0.6, 0.03, 0.6, 1.0);
    }, 3);
    bedWet(g, 0.2);
  },
  silence() { /* intentionally empty: the bed fades to nothing */ },
};

// ======================================================================================= CUES
const BELL_PARTIALS = [   // [ratio, amplitude, decay seconds] — inharmonic, temple-bell-like
  [0.5, 0.35, 4.5], [1, 1, 3.6], [1.19, 0.5, 2.6], [1.56, 0.4, 2.2], [2, 0.45, 1.8],
  [2.51, 0.25, 1.4], [2.66, 0.2, 1.2], [3.01, 0.15, 1.0], [4.1, 0.08, 0.7],
];
const CLASH_PARTIALS = [[1, 1, 0.5], [1.47, 0.6, 0.35], [2.09, 0.5, 0.3], [2.56, 0.35, 0.22], [3.3, 0.25, 0.15]];
const GONG_PARTIALS = [   // [ratio, amp, decay, bloom (seconds to peak)]
  [1, 1, 7, 0.01], [1.52, 0.6, 6, 0.2], [2.03, 0.5, 5.5, 0.45], [2.48, 0.4, 5, 0.7], [2.97, 0.35, 4.5, 0.9],
  [3.53, 0.25, 4, 1.1], [4.12, 0.2, 3.5, 1.3], [5.2, 0.12, 3, 1.5], [6.4, 0.08, 2.5, 1.6],
];

/** Conch (shankha): sawtooth rising into pitch, vibrato blooming in, through vowel-like formants. */
function conchVoice(dest, t, { level = 0.3, f = 220, soft = false, lowpass = 0, wet = 0.45 } = {}) {
  const dur = 3.0, att = soft ? 0.6 : 0.35;
  const v = shot(dest, t, dur + 0.2, { wet });
  v.out.gain.value = level;
  const amp = v.gain(0);
  let tail = amp;
  if (lowpass) { const lp = v.filter('lowpass', lowpass); amp.connect(lp); tail = lp; }
  tail.connect(v.out);
  const mix = v.gain(1);
  [[650, 6, 1], [1080, 8, 0.6], [2450, 10, 0.25], [3300, 12, 0.12]].forEach(([fr, Q, lv]) => {
    const bp = v.filter('bandpass', fr, Q), g = v.gain(lv * 2.2);
    mix.connect(bp); bp.connect(g); g.connect(amp);
  });
  const vib = v.osc('sine', 5.2), vibDepth = v.gain(0);
  vib.connect(vibDepth); v.play(vib, t);
  vibDepth.gain.setValueAtTime(0, t); vibDepth.gain.linearRampToValueAtTime(22, t + 0.9);   // cents
  [0, 7].forEach((cents, i) => {
    const o = v.osc('sawtooth', f, cents), og = v.gain(i ? 0.5 : 1);
    o.frequency.setValueAtTime(f * 0.82, t);
    o.frequency.exponentialRampToValueAtTime(f, t + 0.5);
    o.frequency.setValueAtTime(f, t + 2.1);
    o.frequency.exponentialRampToValueAtTime(f * 0.96, t + dur);
    vibDepth.connect(o.detune);
    o.connect(og); og.connect(mix); v.play(o, t);
  });
  const br = v.noise('white'), brF = v.filter('bandpass', 1400, 1), brG = v.gain(0.05);   // breath
  br.connect(brF); brF.connect(brG); brG.connect(amp); v.play(br, t);
  amp.gain.setValueAtTime(EPS, t);
  amp.gain.linearRampToValueAtTime(1, t + att);
  amp.gain.setValueAtTime(1, t + 2.0);
  amp.gain.exponentialRampToValueAtTime(EPS, t + dur);
}

function gust(dest, t, level = 0.4, panSweep = true) {
  const dur = 3.0;
  const v = shot(dest, t, dur + 0.1, { wet: 0.15 });
  v.out.gain.value = level;
  let out = v.out;
  if (panSweep) {
    const p = v.pan(-0.6);
    if (p.pan) { p.pan.setValueAtTime(-0.6, t); p.pan.linearRampToValueAtTime(0.6, t + dur); }
    p.connect(v.out); out = p;
  }
  const amp = v.gain(0); amp.connect(out);
  const n = v.noise('pink'), bp = v.filter('bandpass', 300, 1.2);
  bp.frequency.setValueAtTime(300, t); bp.frequency.exponentialRampToValueAtTime(1300, t + 1.2);
  bp.frequency.exponentialRampToValueAtTime(450, t + dur);
  n.connect(bp); bp.connect(amp); v.play(n, t);
  const w = v.noise('white'), wb = v.filter('bandpass', 900, 8), wg = v.gain(0.12);
  wb.frequency.setValueAtTime(900, t); wb.frequency.exponentialRampToValueAtTime(1700, t + 1.3);
  wb.frequency.exponentialRampToValueAtTime(800, t + dur);
  w.connect(wb); wb.connect(wg); wg.connect(amp); v.play(w, t);
  amp.gain.setValueAtTime(EPS, t);
  amp.gain.linearRampToValueAtTime(1, t + 1.1);
  amp.gain.exponentialRampToValueAtTime(EPS, t + dur);
}

function rumbleSweep(dest, t, dur, level, pan = 0) {
  const v = shot(dest, t, dur + 0.1, { pan });
  v.out.gain.value = level;
  const n = v.noise('brown'), lp = v.filter('lowpass', 90, 0.9), amp = v.gain(0), wheel = v.gain(0.75);
  lp.frequency.setValueAtTime(90, t); lp.frequency.exponentialRampToValueAtTime(420, t + dur * 0.45);
  lp.frequency.exponentialRampToValueAtTime(140, t + dur);
  n.connect(lp); lp.connect(wheel); wheel.connect(amp); amp.connect(v.out); v.play(n, t);
  v.lfo(wheel.gain, rand(5, 7), 0.25, 'sine', t);              // spokes turning
  env(amp.gain, t, dur * 0.4, 1, dur * 0.15, dur * 0.45);
  return v;
}

function thunderVoice(dest, t, { level = 0.6, soft = false, distant = false } = {}) {
  const dur = 6;
  const v = shot(dest, t, dur + 0.2, { wet: 0.4 });
  v.out.gain.value = level;
  if (!soft && !distant) {                                     // the crack (omitted in reduced motion)
    const c = v.noise('white'), hp = v.filter('highpass', 1800), cg = v.gain(0), p = v.pan(rand(-0.5, 0.5));
    c.connect(hp); hp.connect(cg); cg.connect(p); p.connect(v.out); v.play(c, t);
    perc(cg.gain, t, 0.35, 0.35, 0.005);
  }
  const n = v.noise('brown'), lp = v.filter('lowpass', distant ? 160 : 260, 0.7), amp = v.gain(0);
  lp.frequency.setValueAtTime(distant ? 160 : 260, t);
  lp.frequency.exponentialRampToValueAtTime(distant ? 70 : 90, t + dur);
  n.connect(lp); lp.connect(amp); amp.connect(v.out); v.play(n, t);
  // rolling: a chain of decaying swells
  const p = amp.gain;
  p.setValueAtTime(EPS, t);
  let s = t + (soft ? 0.25 : 0.05), peak = 1;
  const bumps = 6 + ((Math.random() * 4) | 0);
  for (let i = 0; i < bumps && s < t + dur - 0.6; i++) {
    p.linearRampToValueAtTime(peak * rand(0.6, 1), s + (soft ? 0.35 : 0.12));
    s += rand(0.35, 0.8);
    p.linearRampToValueAtTime(peak * rand(0.2, 0.45), s);
    peak *= 0.78;
  }
  p.exponentialRampToValueAtTime(EPS, t + dur);
}

const CUE_BUILDERS = {
  conch(out, t, soft) { conchVoice(out, t, { level: 0.32, f: rand(214, 228), soft }); },

  war_drum(out, t, soft) {
    [[0, 1], [0.34, 0.6], [0.68, 0.85]].forEach(([dt, k], i) => {
      if (i === 2 && Math.random() < 0.4) return;
      const s = t + dt;
      thump(out, s, { f0: 120, f1: 52, level: 0.55 * k, decay: 0.9, attack: soft ? 0.02 : 0.004, wet: 0.35 });
      thump(out, s, { f0: 190, f1: 85, level: 0.16 * k, decay: 0.25, attack: soft ? 0.02 : 0.004 });   // membrane overtone
      if (!soft) click(out, s, { freq: 900, Q: 0.7, level: 0.18 * k, decay: 0.05, type: 'lowpass' });
    });
  },

  chariot_roll(out, t) {
    const v = rumbleSweep(out, t, 3.2, 0.55, 0);
    // pan the whole pass left → right
    const p = v.pan(-0.7);
    if (p.pan) {
      v.out.disconnect(); v.out.connect(p); p.connect(out);
      p.pan.setValueAtTime(-0.7, t); p.pan.linearRampToValueAtTime(0.7, t + 3.2);
    }
    const n = v.noise('pink'), bp = v.filter('bandpass', 220, 2), g = v.gain(0);         // wooden creak
    bp.frequency.setValueAtTime(180, t); bp.frequency.linearRampToValueAtTime(320, t + 1.6); bp.frequency.linearRampToValueAtTime(200, t + 3.2);
    n.connect(bp); bp.connect(g); g.connect(v.out); v.play(n, t);
    env(g.gain, t, 1.2, 0.18, 0.6, 1.3);
  },

  bell(out, t, soft) {
    ring(out, t, rand(630, 660), BELL_PARTIALS, { level: 0.17, wet: 0.45, attack: soft ? 0.015 : 0.002 });
    if (!soft) click(out, t, { freq: 3200, Q: 0.7, level: 0.08, decay: 0.015, type: 'highpass' });
  },

  dice_roll(out, t, soft) {
    // one noise source gated per click (cheap), plus a short bone/ivory ping per click
    const times = [];
    let s = t, gap = rand(0.035, 0.065);
    const n = 7 + ((Math.random() * 5) | 0);
    for (let i = 0; i < n; i++) { times.push(s); s += gap * rand(0.7, 1.3); gap *= 1.17; }
    times.push(s + 0.12, s + 0.32);                            // settling
    const life = times[times.length - 1] - t + 0.2;
    const v = shot(out, t, life, { wet: 0.15 });
    v.out.gain.value = 0.38;
    const src = v.noise('white'), bp = v.filter('bandpass', 3000, 3), gate = v.gain(0);
    src.connect(bp); bp.connect(gate); gate.connect(v.out); v.play(src, t);
    times.forEach((ct, i) => {
      const k = i >= times.length - 2 ? 0.55 : rand(0.5, 1);
      bp.frequency.setValueAtTime(rand(2200, 4200), ct);
      perc(gate.gain, ct, k, 0.008, soft ? 0.004 : 0.001);
      const o = v.osc('sine', rand(1600, 2800)), a = v.gain(0);
      o.connect(a); a.connect(v.out); v.play(o, ct, ct + 0.06);
      perc(a.gain, ct, 0.12 * k, 0.025, 0.001);
    });
  },

  bowstring(out, t) {
    const v = shot(out, t, 1.7, { wet: 0.2 });
    const src = v.buffer(ksBuffer(rand(84, 98), 1.6, 0.995), rand(0.97, 1.03));
    const lp = v.filter('lowpass', 3000), a = v.gain(0.9);
    src.connect(lp); lp.connect(a); a.connect(v.out); v.play(src, t);
    a.gain.setValueAtTime(0.9, t + 1.2); a.gain.linearRampToValueAtTime(EPS, t + 1.6);
    thump(v.out, t, { f0: 190, f1: 150, level: 0.18, decay: 0.05 });               // wood knock
    // the arrow leaving: a quick airy "thwip" travelling across the field
    const w = v.noise('white'), hp = v.filter('highpass', 1500, 0.8), wg = v.gain(0), p = v.pan(-0.2);
    hp.frequency.setValueAtTime(1500, t + 0.02); hp.frequency.exponentialRampToValueAtTime(6000, t + 0.22);
    if (p.pan) { p.pan.setValueAtTime(-0.2, t); p.pan.linearRampToValueAtTime(0.6, t + 0.25); }
    w.connect(hp); hp.connect(wg); wg.connect(p); p.connect(v.out); v.play(w, t, t + 0.4);
    env(wg.gain, t + 0.02, 0.03, 0.08, 0, 0.18);
  },

  gong(out, t, soft) {
    const f0 = rand(80, 84);
    const v = shot(out, t, 7.4, { wet: 0.5 });
    v.out.gain.value = soft ? 0.14 : 0.24;
    GONG_PARTIALS.forEach(([r, amp, decay, bloom], i) => {
      const o = v.osc('sine', f0 * r, i % 3 === 1 ? rand(-4, 4) : 0), a = v.gain(0);
      if (i === 0) { o.frequency.setValueAtTime(f0 * 1.02, t); o.frequency.exponentialRampToValueAtTime(f0, t + 1.5); }
      o.connect(a); a.connect(v.out); v.play(o, t);
      a.gain.setValueAtTime(EPS, t);
      a.gain.linearRampToValueAtTime(amp * (bloom > 0.1 ? 0.25 : 1), t + (soft ? 0.03 : 0.006));
      if (bloom > 0.1) a.gain.linearRampToValueAtTime(amp, t + bloom);                  // shimmer blooms in
      a.gain.exponentialRampToValueAtTime(EPS, t + bloom + decay);
    });
    if (!soft) thump(v.out, t, { f0: 70, f1: 50, level: 0.5, decay: 0.3 });           // mallet
  },

  wind_gust(out, t) { gust(out, t, 0.42, true); },

  water_flow(out, t) {
    const dur = 2.6;
    const v = shot(out, t, dur + 0.2, { wet: 0.2 });
    v.out.gain.value = 0.4;
    const amp = v.gain(0); amp.connect(v.out);
    const a = v.noise('pink'), af = v.filter('bandpass', 700, 0.7), ag = v.gain(0.6);
    a.connect(af); af.connect(ag); ag.connect(amp); v.play(a, t);
    const b = v.noise('white'), bf = v.filter('bandpass', 2600, 1.5), bg = v.gain(0.18);
    b.connect(bf); bf.connect(bg); bg.connect(amp); v.play(b, t);
    v.lfo(bf.frequency, 6.3, 400, 'sine', t); v.lfo(bf.frequency, 9.1, 300, 'triangle', t);
    env(amp.gain, t, 0.4, 1, 1.6, 0.6);
    const drops = 8 + ((Math.random() * 7) | 0);
    for (let i = 0; i < drops; i++) {
      const s = t + rand(0.1, dur - 0.3), f = rand(500, 1300);
      const o = v.osc('sine', f), g = v.gain(0), p = v.pan(rand(-0.6, 0.6));
      o.frequency.setValueAtTime(f, s); o.frequency.exponentialRampToValueAtTime(f * 1.8, s + 0.04);
      o.connect(g); g.connect(p); p.connect(v.out); v.play(o, s, s + 0.1);
      perc(g.gain, s, 0.12, 0.05, 0.003);
    }
  },

  crowd_gasp(out, t, soft) {
    const v = shot(out, t, 2.2, { wet: 0.3 });
    v.out.gain.value = 0.45;
    const src = v.noise('pink');
    v.play(src, t);
    const att = soft ? 0.45 : 0.22;
    [700, 1100, 1600, 2300, 3200].forEach((f, i) => {
      const bp = v.filter('bandpass', f * rand(0.92, 1.08), 2.5), g = v.gain(0), p = v.pan((i - 2) * 0.3);
      bp.frequency.setValueAtTime(f * 0.9, t); bp.frequency.linearRampToValueAtTime(f * 1.08, t + att);  // inhale
      src.connect(bp); bp.connect(g); g.connect(p); p.connect(v.out);
      g.gain.setValueAtTime(EPS, t + i * 0.02);
      g.gain.linearRampToValueAtTime(0.5 - i * 0.06, t + att + i * 0.02);
      g.gain.exponentialRampToValueAtTime(EPS, t + att + 1.4);
    });
    const m = v.noise('brown'), lp = v.filter('lowpass', 500), mg = v.gain(0);
    m.connect(lp); lp.connect(mg); mg.connect(v.out); v.play(m, t);
    env(mg.gain, t, att + 0.1, 0.25, 0.2, 1.2);
  },

  heartbeat(out, t, soft) {
    for (let b = 0; b < 3; b++) {                              // lub-dub ×3 at ~70 bpm, pure low sines
      const s = t + b * 0.86;
      [[0, 0.7], [0.28, 0.5]].forEach(([dt, k]) => {
        thump(out, s + dt, { f0: 58, f1: 38, level: k, decay: 0.18, sweep: 0.12, attack: soft ? 0.02 : 0.01, wet: 0.05 });
        thump(out, s + dt, { f0: 116, f1: 80, level: k * 0.15, decay: 0.08, sweep: 0.06, attack: 0.01 });
      });
    }
  },

  choir_drone(out, t, soft) {
    const sa = 146.83, att = soft ? 2.2 : 1.6, hold = 2.9, rel = 2.5, life = att + hold + rel + 0.2;
    const v = shot(out, t, life, { wet: 0.55 });
    v.out.gain.value = 0.2;
    const amp = v.gain(0); amp.connect(v.out);
    const mix = v.gain(1);
    const f1 = v.filter('bandpass', 800, 5);
    f1.frequency.setValueAtTime(800, t); f1.frequency.linearRampToValueAtTime(560, t + life - 0.5); // "aa" → "oo"
    [[f1, 1], [v.filter('bandpass', 1150, 6), 0.6], [v.filter('bandpass', 2900, 8), 0.2]].forEach(([bp, lv]) => {
      const g = v.gain(lv * 2.5); mix.connect(bp); bp.connect(g); g.connect(amp);
    });
    const vib = v.osc('sine', 4.8), vd = v.gain(12); vib.connect(vd); v.play(vib, t);            // ±12 cents
    [0.5, 1, 1.5, 2].forEach((r) => {
      [-9, 0, 8].forEach((c) => {
        const o = v.osc('sawtooth', sa * r, c), g = v.gain(r === 0.5 ? 0.5 : 0.33);
        vd.connect(o.detune); o.connect(g); g.connect(mix); v.play(o, t);
      });
    });
    env(amp.gain, t, att, 1, hold, rel);
  },

  thunder(out, t, soft) { thunderVoice(out, t, { level: soft ? 0.32 : 0.55, soft }); },

  footsteps(out, t) {
    const n = 4 + ((Math.random() * 3) | 0);
    const v = shot(out, t, n * 0.5 + 0.4, { wet: 0.15 });
    v.out.gain.value = 0.35;
    for (let i = 0; i < n; i++) {
      const s = t + i * rand(0.44, 0.52), p = i % 2 ? 0.15 : -0.15;
      thump(v.out, s, { f0: 90, f1: 55, level: 0.6, decay: 0.08, sweep: 0.05 });
      click(v.out, s, { freq: 600, Q: 0.8, level: 0.5, decay: 0.06, pan: p });
      click(v.out, s + 0.035, { freq: 1100, Q: 0.8, level: 0.18, decay: 0.03, pan: p });          // scuff
    }
    v.keepAlive(t);                                            // container lives until its last step
  },
};

// ================================================================================== bed control
function startBed() {
  if (!ready()) return;
  const now = S.ctx.currentTime;
  const old = S.current;
  if (old) {                                       // equal-power fade out, then tear down
    try { rampTo(old.faders(), 0, XFADE, 'power'); } catch { /* still disposed below */ }
    old.dispose(now + XFADE + 0.05);
    S.current = null;
  }
  const name = S.bedName;
  if (!name || name === 'silence' || !S.ambient) return;
  const g = new Group(S.g.bedComp, 0, { wet: true, wetDest: S.g.bedWet });
  g.name = name; g.level = BED_GAIN * S.bedVolume;
  try { BED_BUILDERS[name](g); }
  catch (e) { warnOnce('bed-' + name, 'could not build bed "' + name + '": ' + (e && e.message)); g.dispose(); return; }
  try { rampTo(g.faders(), g.level, XFADE, 'power'); }
  catch { g.faders().forEach((p) => { p.value = g.level; }); }
  S.current = g;
}

function clearChapterCues() {
  S.chapterTimers.forEach((id) => clearTimeout(id));
  S.chapterTimers = [];
}

// ======================================================================================= API
function cue(name, volume) {
  // Hidden tab: the context is suspended, so a cue would queue up and burst out on return. Drop it.
  // (A cue right after unlock() on a still-'suspended' context is kept: it plays once resume lands.)
  if (!ready() || S.muted || isHidden()) return;
  const build = CUE_BUILDERS[name];
  if (!build) { warnOnce('cue-' + name, 'unknown cue "' + name + '" (ignored)'); return; }
  const nowMs = typeof performance !== 'undefined' ? performance.now() : Date.now();
  const last = S.lastCue.get(name) || -1e9;
  if (nowMs - last < DEDUPE_MS) return;            // merges duplicate wiring of the same event
  if (S.activeCues >= MAX_CUES) return;
  S.lastCue.set(name, nowMs);
  const cap = S.reduced ? CUE_CAP_REDUCED : CUE_CAP;
  const level = clamp01(volume == null ? 0.8 : volume) * cap;
  if (level <= 0.001) return;
  const t = S.ctx.currentTime + 0.02;
  // Per-cue holder: its gain (and its post-fader reverb send) carry the capped cue level. The cue's
  // own one-shot groups clean themselves up; the holder is torn down after the cue's lifetime.
  const life = CUE_LIFE[name] || 3;
  const holder = new Group(S.g.cueTone, level, { wet: true, wetDest: S.g.reverbIn });
  S.activeCues++;
  try {
    build(holder.out, t, S.reduced);
  } catch (e) {
    warnOnce('cue-fail-' + name, 'cue "' + name + '" failed: ' + (e && e.message));
  }
  holder.dispose(t + life + 0.3);                  // no sources of its own: just schedules the disconnect
  setTimeout(() => { S.activeCues = Math.max(0, S.activeCues - 1); }, (life + 0.3) * 1000);
}
// Upper bound of each cue's audible length (seconds); the per-cue holder disconnects after this, so it
// must cover the longest random variant (dice_roll can run ~2.7 s, soft choir_drone 7.8 s).
const CUE_LIFE = { conch: 3.3, war_drum: 1.9, chariot_roll: 3.4, bell: 4.8, dice_roll: 3.0, bowstring: 1.8,
  gong: 7.6, wind_gust: 3.2, water_flow: 2.9, crowd_gasp: 2.3, heartbeat: 3.1, choir_drone: 7.9, thunder: 6.3,
  footsteps: 3.8 };

function setBed(bed, volume) {
  let cues = null;
  if (bed && typeof bed === 'object') {            // chapter.ambient_audio form; an explicit 2nd arg wins
    if (volume == null) volume = bed.volume;
    cues = Array.isArray(bed.cues) ? bed.cues : null;
    bed = bed.bed;
  }
  let name = typeof bed === 'string' ? bed : null;
  if (name && !BEDS.includes(name)) { warnOnce('bed-unknown-' + name, 'unknown bed "' + name + '" → silence'); name = 'silence'; }
  if (!name) name = 'silence';
  const vol = Number.isFinite(+volume) && volume !== null && volume !== '' ? clamp01(volume) : 0.5;
  clearChapterCues();
  const same = name === S.bedName && S.current && S.current.name === name && !S.current.dead;
  S.bedName = name; S.bedVolume = vol;
  if (ready()) {
    if (same) {                                    // same bed: just glide the level
      S.current.level = BED_GAIN * vol;
      try { rampTo(S.current.faders(), S.current.level, 1.5, 'linear'); } catch { /* keep current level */ }
    } else {
      startBed();
    }
    if (cues) {
      cues.forEach((c) => {
        if (!c || typeof c.cue !== 'string') return;
        const at = Math.max(0, Number.isFinite(+c.at) ? +c.at : 0);
        S.chapterTimers.push(setTimeout(() => cue(c.cue, c.volume), at * 1000));
      });
    }
  }
}

function setVolume(v) {
  const nv = clamp01(v);
  if (nv === S.volume) return;
  S.volume = nv;
  applyMaster();
}

function setMuted(m) {
  const nm = !!m;
  if (nm === S.muted) return;
  S.muted = nm;
  if (nm) clearChapterCues();
  applyMaster();
}

function setAmbient(on) {
  const v = on !== false;
  if (v === S.ambient) return;
  S.ambient = v;
  if (!S.ctx || !S.g) return;
  const now = S.ctx.currentTime;
  for (const p of [S.g.bedBus.gain, S.g.bedWet.gain]) {
    p.cancelScheduledValues(now); p.setValueAtTime(p.value, now); p.setTargetAtTime(v ? 1 : 0, now, 0.4);
  }
  if (v) { if (ready() && !S.current) startBed(); }
  else {
    const cur = S.current; S.current = null;
    if (cur) cur.dispose(now + 2);                 // stop synthesizing once faded
  }
}

function setReducedMotion(flag) {
  S.reducedForced = typeof flag === 'boolean' ? flag : null;
  S.reduced = S.reducedForced != null ? S.reducedForced : systemReducedMotion();
  if (S.g) {
    const now = S.ctx.currentTime, f = S.g.cueTone.frequency;
    f.cancelScheduledValues(now); f.setValueAtTime(f.value, now);
    f.setTargetAtTime(S.reduced ? 4200 : 20000, now, 0.2);
  }
}

function applySettings(settings) {
  if (!settings || typeof settings !== 'object') return;
  if (Number.isFinite(+settings.volume) && settings.volume !== null) setVolume(settings.volume);
  if (typeof settings.muted === 'boolean') setMuted(settings.muted);
  if (typeof settings.ambient === 'boolean') setAmbient(settings.ambient);
  if ('reducedMotion' in settings) {
    const rm = settings.reducedMotion;
    if ((typeof rm === 'boolean' ? rm : null) !== S.reducedForced) setReducedMotion(rm);
  }
}

/** Optional wiring: mirror store settings and play 'audio:cue' bus events. Idempotent. */
function connect({ store, bus } = {}) {
  if (S.connected) return S.connected;
  try { applySettings(store && store.state && store.state.settings); } catch { /* ignore */ }
  const offs = [];
  if (bus && typeof bus.on === 'function') {
    const add = (evt, fn) => { const off = bus.on(evt, fn); offs.push(typeof off === 'function' ? off : () => bus.off && bus.off(evt, fn)); };
    add('settings:change', (p) => applySettings(p && p.settings));
    add('store:change', (p) => applySettings(p && p.state && p.state.settings));
    add('audio:cue', (p) => { if (p && p.cue) cue(p.cue, p.volume); });
  }
  S.connected = () => { offs.forEach((f) => { try { f(); } catch { /* ignore */ } }); S.connected = null; };
  return S.connected;
}

function unlock() {
  if (!AC || S.failed) return Promise.resolve(false);
  if (S.ctx && S.ctx.state === 'closed') resetClosed();   // e.g. closed by the browser: start over
  // Cheap path: called on every slider 'input' by nav.js, so don't redo the gesture dance when running.
  if (S.ctx && S.unlocked && S.ctx.state === 'running') return Promise.resolve(true);
  try { if (!S.ctx) init(); }
  catch (e) { S.failed = true; S.ctx = null; S.g = null; warnOnce('init', 'WebAudio unavailable: ' + (e && e.message)); return Promise.resolve(false); }
  const c = S.ctx;
  // iOS/Safari: starting a silent buffer inside the gesture fully unlocks output.
  try { const b = c.createBufferSource(); b.buffer = c.createBuffer(1, 1, 22050); b.connect(c.destination); b.start(0); } catch { /* ignore */ }
  const wasUnlocked = S.unlocked;
  S.unlocked = true;
  // resume() must be requested synchronously inside the gesture; applyMaster() also sets the level now
  // so a cue fired in the same click (opening's conch) is audible as soon as the context runs.
  let resumed;
  try { resumed = needsResume(c) && !S.muted ? c.resume() : undefined; } catch (e) { resumed = Promise.reject(e); }
  applyMaster();
  const startPending = () => { if (S.ambient && S.bedName && S.bedName !== 'silence' && !S.current) startBed(); };
  startPending();                                  // bed set before unlock (remembered) starts now
  return Promise.resolve(resumed).then(() => {
    applyMaster();
    startPending();
    return true;
  }, (e) => {
    // Not allowed yet (no gesture): stay locked so a later unlock() inside a gesture retries cleanly.
    S.unlocked = wasUnlocked;
    if (!wasUnlocked && S.current) { const cur = S.current; S.current = null; cur.dispose(); }
    applyMaster();
    warnOnce('resume', 'AudioContext resume was refused: ' + (e && e.message));
    return false;
  });
}

/** Output level 0..1 (RMS, roughly perceptual) — for a subtle UI meter. 0 when silent/locked. */
function level() {
  if (!ready() || S.muted) return 0;
  try {
    const a = S.g.analyser, d = S.g.meter;
    if (a.getFloatTimeDomainData) a.getFloatTimeDomainData(d); else return 0;
    let sum = 0;
    for (let i = 0; i < d.length; i++) sum += d[i] * d[i];
    const rms = Math.sqrt(sum / d.length);
    return clamp((20 * Math.log10(rms + 1e-9) + 60) / 60, 0, 1);
  } catch { return 0; }
}

export const audio = {
  unlock, setBed, cue, setVolume, setMuted,
  get muted() { return S.muted; },
  get volume() { return S.volume; },
  get unlocked() { return ready(); },
  // extras
  setAmbient, setReducedMotion, applySettings, connect, level,
  get ambient() { return S.ambient; },
  get bed() { return S.bedName; },
  get supported() { return !!AC && !S.failed; },
  get reducedMotion() { return S.reduced; },
  beds: BEDS,
  cues: CUES,
};

export default audio;
