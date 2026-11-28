// MAHABHARATA — The Living Epic · boot + routing.
// Loads the voyage data, creates the world and audio (each optional, with honest fallbacks),
// mounts every UI module (one failing module never takes the shell down), wires the bus,
// hash routes, keyboard shortcuts, visibility pause and a toast error boundary, then runs
// the opening sequence.
import { bus } from './core/bus.js';
import { $, el, esc, prefersReducedMotion, isTypingTarget, safeHex } from './core/util.js';
import { loadVoyage } from './core/data.js';
import { store } from './core/store.js';
import { runOpening, showOpeningError } from './ui/opening.js';
import { initNav } from './ui/nav.js';
import { initChapterPanel } from './ui/chapter.js';
import { initHotspots } from './ui/hotspots.js';

const VIEWS = ['journey', 'map', 'characters', 'knowledge', 'timeline'];
const body = document.body;

// =====================================================================================
// Toasts + error boundary
// =====================================================================================
const recentToasts = new Map(); // message -> time (dedupe)

function toast(message, kind = 'info') {
  const root = $('#toast-root');
  if (!root || !message) return;
  const msg = String(message);
  const now = Date.now();
  if (now - (recentToasts.get(msg) || 0) < 6000) return;
  recentToasts.set(msg, now);
  if (recentToasts.size > 40) { // keep the dedupe map small
    for (const [m, t] of recentToasts) if (now - t > 6000) recentToasts.delete(m);
  }
  while (root.childElementCount >= 3) root.firstElementChild.remove();
  const k = kind === 'error' ? 'error' : 'info';
  const node = el(`
    <div class="toast" data-kind="${k}" role="${k === 'error' ? 'alert' : 'status'}">
      <span class="toast-mark" aria-hidden="true"></span>
      <span class="toast-msg">${esc(msg)}</span>
      <button type="button" class="toast-close" aria-label="Dismiss">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>
      </button>
    </div>`);
  const dismiss = () => {
    if (!node.isConnected) return;
    node.classList.add('is-leaving');
    setTimeout(() => node.remove(), 380);
  };
  node.querySelector('.toast-close').addEventListener('click', dismiss);
  root.appendChild(node);
  setTimeout(dismiss, k === 'error' ? 9000 : 5200);
}
bus.on('toast', (p = {}) => toast(p.message, p.kind));

let lastBoundaryToast = 0;
function boundary(message) {
  const m = String(message || '').trim();
  if (!m || /ResizeObserver loop|^Script error\.?$/i.test(m)) return;
  const now = Date.now();
  if (now - lastBoundaryToast < 2500) return;
  lastBoundaryToast = now;
  toast(`Something faltered: ${m.length > 180 ? m.slice(0, 177) + '…' : m}`, 'error');
}
window.addEventListener('error', (e) => {
  // Resource load errors (img/script tags) have no message; module import failures are handled where they happen.
  if (e && e.message) boundary(e.message);
});
window.addEventListener('unhandledrejection', (e) => {
  const r = e && e.reason;
  if (r && r.name === 'AbortError') return;
  boundary((r && (r.message || r.toString())) || 'An operation failed');
});

// =====================================================================================
// Fallbacks for the optional engines
// =====================================================================================
const noop = () => {};
const resolved = () => Promise.resolve();

function stubWorld() {
  return {
    supported: false,
    showOpening: resolved, openingZoom: resolved, playChapter: resolved,
    playShot: noop, nextShot: noop, prevShot: noop,
    currentShot: () => ({ index: 0, count: 0, shot: null }),
    setMood: noop, highlight: noop, setQuality: noop, pause: noop, resume: noop, dispose: noop,
    onHotspots: noop,
    stats: () => ({ fps: 0, drawCalls: 0, triangles: 0, scenesCached: 0 }),
  };
}
const WORLD_METHODS = Object.keys(stubWorld()).filter((k) => k !== 'supported');

/** Wrap every world method so a throw degrades to the stub behaviour instead of breaking the UI. */
function guardWorld(w) {
  const stub = stubWorld();
  if (!w || typeof w !== 'object') return stub;
  const g = { raw: w };
  Object.defineProperty(g, 'supported', { get: () => !!w.supported, enumerable: true });
  for (const m of WORLD_METHODS) {
    g[m] = (...args) => {
      if (typeof w[m] !== 'function') return stub[m](...args);
      try {
        const out = w[m](...args);
        if (out && typeof out.catch === 'function') {
          return out.catch((err) => { console.warn(`[world] ${m} rejected`, err); });
        }
        return out;
      } catch (err) {
        console.warn(`[world] ${m} failed`, err);
        return stub[m](...args);
      }
    };
  }
  return g;
}

function stubAudio() {
  let volume = 0.6, muted = false;
  return {
    unlock: noop, setBed: noop, cue: noop,
    setVolume: (v) => { volume = v; }, setMuted: (m) => { muted = !!m; },
    get muted() { return muted; }, get volume() { return volume; }, get unlocked() { return false; },
    stub: true,
  };
}

function guardAudio(a) {
  const stub = stubAudio();
  if (!a || typeof a !== 'object') return stub;
  const safe = (m) => (...args) => {
    try { return typeof a[m] === 'function' ? a[m](...args) : undefined; } catch (err) { console.warn(`[audio] ${m} failed`, err); return undefined; }
  };
  const g = {
    unlock: safe('unlock'), setBed: safe('setBed'), cue: safe('cue'), setVolume: safe('setVolume'), setMuted: safe('setMuted'),
    get muted() { try { return !!a.muted; } catch { return false; } },
    get volume() { try { return Number(a.volume) || 0; } catch { return 0; } },
    get unlocked() { try { return !!a.unlocked; } catch { return false; } },
    raw: a,
  };
  // Optional extras some audio builds provide (used when present).
  for (const extra of ['setAmbient', 'setReducedMotion', 'applySettings', 'connect', 'level']) {
    if (typeof a[extra] === 'function') g[extra] = safe(extra);
  }
  return g;
}

/** Dynamic import that never throws; resolves null on failure or after `timeoutMs`. */
async function optionalImport(path, label, failures, timeoutMs = 20000) {
  let timer;
  try {
    return await Promise.race([
      import(path),
      new Promise((_, rej) => { timer = setTimeout(() => rej(new Error('timed out')), timeoutMs); }),
    ]);
  } catch (err) {
    console.warn(`[voyage] could not load ${path}:`, err);
    if (failures && label) failures.push(label);
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function safeInit(label, fn, failures) {
  try {
    fn();
  } catch (err) {
    console.error(`[voyage] ${label} failed to start:`, err);
    if (failures) failures.push(label);
  }
}

// =====================================================================================
// Settings application (motion, quality, audio)
// =====================================================================================
let systemMotionQuery = null;
try { systemMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)'); } catch { /* old browser */ }

function applyMotion(settings) {
  const rm = settings ? settings.reducedMotion : null;
  const reduced = rm === true || (rm !== false && !!(systemMotionQuery && systemMotionQuery.matches));
  body.classList.toggle('reduced-motion', reduced);
  if (rm === false) body.dataset.motion = 'full'; else delete body.dataset.motion;
}

// =====================================================================================
// Boot
// =====================================================================================
async function boot() {
  store.load();
  applyMotion(store.state.settings);
  if (systemMotionQuery && systemMotionQuery.addEventListener) {
    systemMotionQuery.addEventListener('change', () => applyMotion(store.state.settings));
  }

  const failures = [];

  // Kick off optional modules in parallel with the data load.
  const audioP = optionalImport('./audio/audio.js', 'Ambient audio', failures);
  const engineP = optionalImport('./world/engine.js', '3D world', failures);
  const viewsP = Promise.all([
    optionalImport('./ui/map.js', 'Epic Map', failures),
    optionalImport('./ui/timeline.js', 'Timeline', failures),
    optionalImport('./ui/characters.js', 'Characters', failures),
    optionalImport('./ui/knowledge.js', 'Knowledge', failures),
    optionalImport('./research/flow.js', 'Research', failures),
  ]);

  let V;
  try {
    V = await loadVoyage();
  } catch (err) {
    console.error('[voyage] data failed to load', err);
    showOpeningError(err && err.message ? err.message : String(err), { onRetry: () => window.location.reload() });
    return;
  }
  store.bindVoyage(V);
  if (store.state.current && !V.chapterById.has(store.state.current)) {
    store.state.current = null; // stale progress from an older data version
    store.save();
  }

  // ---------------------------------------------------------------- audio
  const audioMod = await audioP;
  const audio = guardAudio(audioMod && (audioMod.audio || audioMod.default));
  const extendedAudio = typeof audio.connect === 'function';
  if (extendedAudio) {
    audio.connect({ store, bus }); // mirrors settings + plays 'audio:cue' itself
  } else {
    audio.setVolume(store.state.settings.volume);
    audio.setMuted(store.state.settings.muted);
    bus.on('audio:cue', (p = {}) => { if (p.cue) audio.cue(p.cue, p.volume); });
  }

  // ---------------------------------------------------------------- world
  const engine = await engineP;
  let world = stubWorld();
  if (engine && typeof engine.createWorld === 'function') {
    let timedOut = false;
    let timer = 0;
    // Started inside try so a synchronous throw from createWorld is caught too.
    let creating = null;
    try {
      creating = Promise.resolve(engine.createWorld($('#world-canvas'), { V, store, bus }));
      const created = await Promise.race([
        creating,
        new Promise((_, rej) => { timer = setTimeout(() => { timedOut = true; rej(new Error('the 3D world took too long to start')); }, 25000); }),
      ]);
      if (created) world = created;
    } catch (err) {
      console.error('[voyage] createWorld failed', err);
      failures.push('3D world');
      // A world that finishes building after we gave up must not keep a hidden render loop / GPU memory alive.
      if (timedOut && creating) {
        creating.then((late) => { try { late && typeof late.dispose === 'function' && late.dispose(); } catch { /* ignore */ } }, noop);
      }
    } finally {
      clearTimeout(timer);
    }
  } else if (engine) {
    failures.push('3D world');
  }
  world = guardWorld(world);
  body.classList.toggle('no-webgl', !world.supported);
  world.setQuality(store.state.settings.quality);

  // ---------------------------------------------------------------- UI modules
  safeInit('Navigation', () => initNav({ V, store, bus, audio, world }), failures);
  safeInit('Chapter panel', () => initChapterPanel({ V, store, bus, world }), failures);
  safeInit('Hotspots', () => initHotspots({ world, bus, V }), failures);

  const [mapMod, timelineMod, charsMod, knowMod, flowMod] = await viewsP;
  /** Mount one optional module; returns true only when it really started. */
  const mount = (mod, exportName, label, args) => {
    if (!mod) return false; // import failure already recorded
    if (typeof mod[exportName] !== 'function') { failures.push(label); return false; }
    const before = failures.length;
    safeInit(label, () => mod[exportName](args), failures);
    return failures.length === before;
  };
  const viewOk = {
    map: mount(mapMod, 'initMap', 'Epic Map', { V, store, bus }),
    timeline: mount(timelineMod, 'initTimeline', 'Timeline', { V, store, bus }),
    characters: mount(charsMod, 'initCharacters', 'Characters', { V, store, bus }),
    knowledge: mount(knowMod, 'initKnowledge', 'Knowledge', { V, store, bus }),
  };
  const researchOk = mount(flowMod, 'initResearch', 'Research', { V, store, bus, world, audio });

  // Honest empty states for views whose module did not start (instead of a blank overlay).
  const VIEW_LABEL = { map: 'The Epic Map', timeline: 'The Timeline', characters: 'The Characters', knowledge: 'The Knowledge view' };
  for (const [view, ok] of Object.entries(viewOk)) {
    if (ok) continue;
    const host = $(`#view-${view}`);
    if (!host) continue;
    host.innerHTML = `
      <div class="view-unavailable" role="status">
        <p class="eyebrow">Unavailable</p>
        <p class="view-unavailable-title">${esc(VIEW_LABEL[view])} could not start in this session.</p>
        <p class="view-unavailable-text">The rest of the voyage still works. Try reloading, or use the classic interface.</p>
        <div class="view-unavailable-actions">
          <button type="button" class="btn btn-sm" data-back>Back to the journey</button>
          <a class="btn btn-sm btn-ghost" href="/classic">Classic JARVIS</a>
        </div>
      </div>`;
    host.querySelector('[data-back]').addEventListener('click', () => bus.emit('view:change', { view: 'journey' }));
  }
  // Without the research module, question buttons must not silently do nothing.
  if (!researchOk) {
    bus.on('question:select', () => toast('The research module could not start, so live answers are unavailable right now. The classic interface (/classic) can still answer questions.', 'error'));
  }

  wire({ V, world, audio, extendedAudio });

  // ---------------------------------------------------------------- opening
  const deep = parseHash(window.location.hash);
  const resumeChapterId = deep && deep.type === 'chapter' && V.chapterById.has(deep.id) ? deep.id : null;
  await runOpening({ world, store, bus, audio, V, resumeChapterId });
  routing.opened = true;
  if (deep && deep.type === 'view' && deep.view !== 'journey') bus.emit('view:change', { view: deep.view });
  if (failures.length) {
    // Shown once the opening has finished so the title card stays clean.
    const uniq = [...new Set(failures)];
    setTimeout(() => toast(`Some parts of the voyage could not start: ${uniq.join(', ')}. The rest works normally.`, 'error'), 400);
  }
  if (deep && deep.type === 'chapter' && !resumeChapterId) {
    toast(`The linked chapter (“${deep.id}”) is not part of this voyage, so the journey began from the start.`, 'info');
  }
}

// =====================================================================================
// Routing helpers
// =====================================================================================
const routing = { opened: false };

function parseHash(hash) {
  const h = String(hash || '').replace(/^#\/?/, '');
  if (!h) return null;
  const parts = h.split('/').filter(Boolean).map((p) => { try { return decodeURIComponent(p); } catch { return p; } });
  if (parts[0] === 'chapter' && parts[1]) return { type: 'chapter', id: parts[1] };
  if (VIEWS.includes(parts[0])) return { type: 'view', view: parts[0] };
  return null;
}

function writeHash(hash, push) {
  if (window.location.hash === hash) return;
  try {
    if (push) history.pushState(null, '', hash); else history.replaceState(null, '', hash);
  } catch {
    window.location.hash = hash; // sandboxed contexts may block the History API
  }
}

// =====================================================================================
// Bus wiring
// =====================================================================================
function wire({ V, world, audio, extendedAudio }) {
  let currentChapter = null;
  let currentView = 'journey';
  let enterToken = 0;
  let lastBed = null;           // {bed, volume, cues}
  const cueTimers = [];

  // ---------------------------------------------------------------- location palette -> CSS vars
  function applyLocationVars(ch) {
    const loc = V.locationById.get(ch.location_id);
    const pal = (loc && loc.palette) || {};
    const rootStyle = document.documentElement.style;
    const set = (name, val) => { if (val) rootStyle.setProperty(name, val); else rootStyle.removeProperty(name); };
    set('--loc-accent', safeHex(pal.accent));
    set('--loc-sky', safeHex(pal.sky));
    set('--loc-fog', safeHex(pal.fog));
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', safeHex(pal.sky, '#0B0E22'));
  }

  // ---------------------------------------------------------------- ambient beds
  function chapterBed(ch) {
    const loc = V.locationById.get(ch.location_id);
    const a = ch.ambient_audio || {};
    const la = (loc && loc.ambient_audio) || {};
    const bed = typeof a.bed === 'string' ? a.bed : typeof la.bed === 'string' ? la.bed : 'silence';
    const volume = Number.isFinite(+a.volume) && a.volume !== null ? +a.volume : Number.isFinite(+la.volume) && la.volume !== null ? +la.volume : 0.5;
    const cues = Array.isArray(a.cues) ? a.cues : [];
    return { bed, volume, cues };
  }

  function clearCues() { while (cueTimers.length) clearTimeout(cueTimers.pop()); }

  function playBed(b) {
    lastBed = b;
    clearCues();
    if (extendedAudio) {
      audio.setBed({ bed: b.bed, volume: b.volume, cues: b.cues }); // schedules chapter cues itself
      return;
    }
    const ambientOn = store.state.settings.ambient !== false;
    audio.setBed(ambientOn ? b.bed : 'silence', ambientOn ? b.volume : 0);
    for (const c of b.cues) {
      if (!c || typeof c.cue !== 'string') continue;
      const at = Math.max(0, Number(c.at) || 0);
      cueTimers.push(setTimeout(() => audio.cue(c.cue, c.volume), at * 1000));
    }
  }

  // ---------------------------------------------------------------- views
  function applyView(view) {
    if (!VIEWS.includes(view)) return;
    currentView = view;
    for (const v of VIEWS) body.classList.toggle(`view-${v}`, v === view);
    // The chapter stage fades out over a long transition; take it out of the tab order at once.
    const stageUi = $('#stage-ui');
    if (stageUi) stageUi.inert = view !== 'journey';
    if (store.state.view !== view) store.set('view', view);
    if (routing.opened || view !== 'journey') {
      writeHash(view === 'journey' ? (currentChapter ? `#/chapter/${currentChapter}` : '#/journey') : `#/${view}`, true);
    }
  }
  bus.on('view:change', (p = {}) => applyView(p.view));

  // ---------------------------------------------------------------- chapters
  bus.on('chapter:enter', (p = {}) => {
    const ch = V.chapterById.get(p.chapterId);
    if (!ch) {
      toast(`That chapter (“${p.chapterId}”) is not part of this voyage yet.`, 'error');
      return;
    }
    if (ch.id === currentChapter && p.from === 'hash') { // already here: just make sure it is visible
      if (currentView !== 'journey') bus.emit('view:change', { view: 'journey' });
      return;
    }
    const token = ++enterToken;
    currentChapter = ch.id; // set first so the view change below writes the new chapter's hash
    if (currentView !== 'journey') bus.emit('view:change', { view: 'journey' });

    store.visit(ch.id);
    applyLocationVars(ch);
    writeHash(`#/chapter/${ch.id}`, p.from !== 'hash' && p.from !== 'opening');
    document.title = `${ch.title || ch.id} · Mahabharata — The Living Epic`;
    playBed(chapterBed(ch));

    // Without WebGL, still give the 2D backdrop a gentle veil between places.
    if (!world.supported) {
      const veil = $('#transition-veil');
      if (veil && !prefersReducedMotion()) {
        const c = safeHex(ch.transition && ch.transition.color);
        if (c) veil.style.setProperty('--veil-color', c); else veil.style.removeProperty('--veil-color');
        veil.classList.add('is-covering');
        setTimeout(() => veil.classList.remove('is-covering'), 520);
      }
    }

    let finished = false;
    const finish = () => {
      if (finished || token !== enterToken) return;
      finished = true;
      bus.emit('chapter:entered', { chapterId: ch.id });
    };
    // Resolve 'entered' when the scene is ready — or after 4 s so the UI never waits on a slow build.
    Promise.resolve(world.playChapter(ch.id, { instant: false })).then(finish, finish);
    setTimeout(finish, 4000);
  });

  // ---------------------------------------------------------------- entities -> views / chapters
  bus.on('entity:open', (p = {}) => {
    const { type, id } = p;
    switch (type) {
      case 'character': bus.emit('view:change', { view: 'characters' }); break;
      case 'location': bus.emit('view:change', { view: 'map' }); break;
      case 'event': bus.emit('view:change', { view: 'timeline' }); break;
      case 'chapter':
        if (V.chapterById.has(id)) bus.emit('chapter:enter', { chapterId: id, from: 'discovery' });
        break;
      case 'question': {
        const entry = V.questionById.get(id);
        if (entry) bus.emit('question:select', { questionId: id, chapterId: entry.chapter.id });
        break;
      }
      case 'discovery': {
        let found = null;
        for (const ch of V.chapters) {
          found = ch.optional_discoveries.find((d) => d && d.id === id);
          if (found) break;
        }
        if (!found) return;
        store.discover(found.id);
        if (found.type && found.type !== 'discovery' && found.target) bus.emit('entity:open', { type: found.type, id: found.target });
        break;
      }
      default: break;
    }
  });

  // ---------------------------------------------------------------- research -> world mood / highlight
  // research/flow.js is the owner: it emits 'world:mood' / 'world:highlight' for the run the open drawer
  // shows (forwarded below) and sends `highlight` (entities matched in the real response by
  // research/evidence.js) on 'done'. This handler only mirrors foreground states so the contract holds even
  // for a research module that does not emit moods itself; background runs and withdrawn questions are ignored.
  let calmTimer = 0;
  const highlightFor = (p) => {
    if (Array.isArray(p.highlight)) return p.highlight;
    const entry = p.questionId && V.questionById.get(p.questionId);
    const rel = entry && entry.question.related_entities;
    return rel && Array.isArray(rel.characters) ? rel.characters.filter((c) => typeof c === 'string') : [];
  };
  bus.on('research:state', (p = {}) => {
    if (p.background === true) return; // flow.js restores the foreground run's mood itself
    clearTimeout(calmTimer);
    switch (p.state) {
      case 'focusing':
      case 'queued':
      case 'pending':
      case 'retrieved':
      case 'rendering':
        world.setMood('investigate');
        break;
      case 'done':
        world.setMood('reveal');
        world.highlight(highlightFor(p));
        break;
      case 'error':
        if (p.error && p.error.aborted) { world.setMood('calm'); break; } // withdrawn, not failed
        world.setMood('error');
        calmTimer = setTimeout(() => world.setMood('calm'), 2400);
        break;
      default: break;
    }
  });
  bus.on('world:mood', (p = {}) => {
    if (typeof p.mood !== 'string') return;
    // Any later mood request (e.g. the user focusing another run) supersedes a pending "error → calm".
    if (p.mood !== 'error') clearTimeout(calmTimer);
    world.setMood(p.mood);
  });
  bus.on('world:highlight', (p = {}) => world.highlight(Array.isArray(p.ids) ? p.ids : []));
  bus.on('research:open', () => body.classList.add('research-open'));
  bus.on('research:close', () => {
    body.classList.remove('research-open', 'research-focus');
    world.setMood('calm');
    world.highlight([]);
  });
  bus.on('research:focus', (p = {}) => body.classList.toggle('research-focus', !!p.on));

  // ---------------------------------------------------------------- settings
  bus.on('settings:change', (p = {}) => {
    const s = p.settings || store.state.settings;
    applyMotion(s);
    world.setQuality(s.quality || 'auto');
    if (extendedAudio) return; // audio.connect() mirrors the rest
    audio.setVolume(s.volume);
    audio.setMuted(!!s.muted);
    if (lastBed) playBed({ ...lastBed, cues: [] }); // re-apply ambient on/off
  });

  // ---------------------------------------------------------------- hash navigation (back/forward, edits)
  window.addEventListener('hashchange', () => {
    if (!routing.opened) return;
    const r = parseHash(window.location.hash);
    if (!r) { if (currentView !== 'journey') bus.emit('view:change', { view: 'journey' }); return; }
    if (r.type === 'chapter') {
      if (!V.chapterById.has(r.id)) { // unknown chapter: say so, then make the address bar match the screen again
        toast(`That chapter (“${r.id}”) is not part of this voyage yet.`, 'error');
        if (currentView !== 'journey') bus.emit('view:change', { view: 'journey' });
        writeHash(currentChapter ? `#/chapter/${currentChapter}` : '#/journey', false);
        return;
      }
      if (r.id !== currentChapter) bus.emit('chapter:enter', { chapterId: r.id, from: 'hash' });
      else if (currentView !== 'journey') bus.emit('view:change', { view: 'journey' });
    } else if (r.view !== currentView) {
      bus.emit('view:change', { view: r.view });
    }
  });

  // ---------------------------------------------------------------- keyboard
  const KEY_VIEWS = { m: 'map', t: 'timeline', c: 'characters', k: 'knowledge', j: 'journey' };
  window.addEventListener('keydown', (e) => {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
    if (body.classList.contains('is-opening')) return;
    if (isTypingTarget(e.target)) return;
    const key = e.key;

    if (key === 'Escape') {
      const evt = { handled: false };
      bus.emit('ui:escape', evt);
      if (evt.handled) return;
      const modal = $('#modal-root');
      if (modal && modal.childElementCount > 0) return; // the modal's owner handles it
      if (body.classList.contains('research-open')) { bus.emit('research:close', {}); return; }
      if (currentView !== 'journey') bus.emit('view:change', { view: 'journey' });
      return;
    }

    if (key === 'ArrowLeft' || key === 'ArrowRight') {
      const t = e.target;
      if (t instanceof Element && (t.matches('input[type="range"], [role="slider"], [role="tab"], [role="radio"]') || t.closest('[role="listbox"], [role="tablist"], [role="radiogroup"]'))) return;
      if (currentView !== 'journey') return;
      if (key === 'ArrowLeft') world.prevShot(); else world.nextShot();
      e.preventDefault();
      return;
    }

    const lower = key.length === 1 ? key.toLowerCase() : '';
    if (lower === 'f') { toggleFullscreen(); e.preventDefault(); return; }
    if (KEY_VIEWS[lower]) {
      const target = KEY_VIEWS[lower];
      // Pressing the key of the open view returns to the journey.
      bus.emit('view:change', { view: target === currentView && target !== 'journey' ? 'journey' : target });
      e.preventDefault();
    }
  });

  function toggleFullscreen() {
    try {
      if (document.fullscreenElement) {
        document.exitFullscreen && document.exitFullscreen().catch(noop);
      } else {
        const req = document.documentElement.requestFullscreen;
        if (!req) { toast('Fullscreen is not available in this browser.', 'info'); return; }
        req.call(document.documentElement).catch(() => toast('Fullscreen was blocked by the browser.', 'info'));
      }
    } catch {
      toast('Fullscreen is not available here.', 'info');
    }
  }

  // ---------------------------------------------------------------- visibility: pause rendering when hidden
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      world.pause();
      if (!extendedAudio) audio.setMuted(true); // the extended audio engine suspends itself
    } else {
      world.resume();
      if (!extendedAudio) audio.setMuted(!!store.state.settings.muted);
    }
  });

  window.addEventListener('pagehide', () => { try { store.save(); } catch { /* ignore */ } });
}

// =====================================================================================
boot().catch((err) => {
  console.error('[voyage] boot failed', err);
  showOpeningError(err && err.message ? err.message : String(err), { onRetry: () => window.location.reload() });
});
