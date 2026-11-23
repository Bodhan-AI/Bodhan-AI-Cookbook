// Persistent voyage progress + settings. Every storage access is wrapped: the voyage works
// (without persistence) when localStorage is blocked, full or throws in a private window.
import { bus } from './bus.js';

const KEY = 'mahabharata.voyage.v1';
const VIEWS = ['journey', 'map', 'characters', 'knowledge', 'timeline'];

function defaults() {
  return {
    visited: [],
    answered: [],
    discoveries: [],
    current: null,
    view: 'journey',
    research: {},
    settings: { quality: 'auto', reducedMotion: null, volume: 0.6, muted: false, ambient: true },
  };
}

/** Merge persisted JSON onto defaults, discarding anything of the wrong shape. */
function sanitize(raw) {
  const d = defaults();
  if (!raw || typeof raw !== 'object') return d;
  const ids = (v) => (Array.isArray(v) ? [...new Set(v.filter((x) => typeof x === 'string'))] : []);
  d.visited = ids(raw.visited);
  d.answered = ids(raw.answered);
  d.discoveries = ids(raw.discoveries);
  d.current = typeof raw.current === 'string' ? raw.current : null;
  d.view = VIEWS.includes(raw.view) ? raw.view : 'journey';
  if (raw.research && typeof raw.research === 'object' && !Array.isArray(raw.research)) d.research = { ...raw.research };
  const s = raw.settings && typeof raw.settings === 'object' ? raw.settings : {};
  if (['auto', 'low', 'medium', 'high'].includes(s.quality)) d.settings.quality = s.quality;
  if (typeof s.reducedMotion === 'boolean' || s.reducedMotion === null) d.settings.reducedMotion = s.reducedMotion;
  if (s.volume !== null && s.volume !== '' && Number.isFinite(+s.volume)) d.settings.volume = Math.min(1, Math.max(0, +s.volume));
  if (typeof s.muted === 'boolean') d.settings.muted = s.muted;
  if (typeof s.ambient === 'boolean') d.settings.ambient = s.ambient;
  return d;
}

let voyage = null; // optional V, lets isUnlocked() accept a chapter id

export const store = {
  state: defaults(),
  persistent: true, // false once storage has proven unavailable

  /** Optional: give the store the loaded voyage so ids can be resolved. */
  bindVoyage(V) { voyage = V || null; },

  load() {
    try {
      const txt = window.localStorage.getItem(KEY);
      store.state = sanitize(txt ? JSON.parse(txt) : null);
    } catch {
      store.persistent = false;
      store.state = defaults();
    }
    return store.state;
  },

  save() {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(store.state));
      store.persistent = true;
    } catch {
      store.persistent = false; // keep working in memory
    }
  },

  /** Persist + notify after every mutation. */
  _changed() {
    store.save();
    bus.emit('store:change', { state: store.state });
  },

  visit(id) {
    if (typeof id !== 'string' || !id) return;
    const s = store.state;
    if (!s.visited.includes(id)) s.visited.push(id);
    s.current = id;
    store._changed();
  },

  answer(qid, meta = {}) {
    if (typeof qid !== 'string' || !qid) return;
    const s = store.state;
    if (!s.answered.includes(qid)) s.answered.push(qid);
    // A log entry only — never the answer text itself.
    s.research[qid] = {
      at: (meta && meta.at) || new Date().toISOString(),
      query: meta && typeof meta.query === 'string' ? meta.query : undefined,
      ok: meta && typeof meta.ok === 'boolean' ? meta.ok : true,
    };
    store._changed();
  },

  discover(id) {
    if (typeof id !== 'string' || !id) return;
    if (store.state.discoveries.includes(id)) return;
    store.state.discoveries.push(id);
    store._changed();
  },

  /** Dot-path setter, e.g. store.set('settings.volume', 0.4). */
  set(path, value) {
    const parts = String(path || '').split('.').filter(Boolean);
    if (!parts.length) return;
    let o = store.state;
    for (let i = 0; i < parts.length - 1; i++) {
      if (!o[parts[i]] || typeof o[parts[i]] !== 'object') o[parts[i]] = {};
      o = o[parts[i]];
    }
    o[parts[parts.length - 1]] = value;
    store._changed();
  },

  /** Wipe progress (settings are kept). */
  reset() {
    const settings = store.state.settings;
    store.state = { ...defaults(), settings };
    store._changed();
  },

  isVisited(id) { return store.state.visited.includes(id); },
  isAnswered(qid) { return store.state.answered.includes(qid); },
  isDiscovered(id) { return store.state.discoveries.includes(id); },

  /**
   * Story-order unlock state. Locked chapters are still openable (free exploration);
   * this only drives the "travelling ahead of the story" presentation.
   *   always            -> unlocked
   *   chapter_visited   -> every listed chapter visited
   *   question_answered -> every listed question answered
   *   any_of            -> any listed chapter visited OR any listed question answered
   */
  isUnlocked(chapter) {
    const ch = typeof chapter === 'string' ? voyage && voyage.chapterById.get(chapter) : chapter;
    if (!ch) return true;
    // Note: visiting a chapter early does not unlock it — the conditions describe story order.
    const u = ch.unlock_conditions;
    if (!u || !u.type || u.type === 'always') return true;
    const chs = Array.isArray(u.chapters) ? u.chapters : [];
    const qs = Array.isArray(u.questions) ? u.questions : [];
    switch (u.type) {
      case 'chapter_visited':
        return chs.every((id) => store.isVisited(id));
      case 'question_answered':
        return qs.every((id) => store.isAnswered(id));
      case 'any_of':
        if (!chs.length && !qs.length) return true;
        return chs.some((id) => store.isVisited(id)) || qs.some((id) => store.isAnswered(id));
      default:
        return true; // unknown condition type: never block
    }
  },
};

export default store;
