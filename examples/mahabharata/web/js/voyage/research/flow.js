// Question → real pipeline (FRONTEND_CONTRACT §Question flow).
//
// Each question becomes a "run" with an honest state machine:
//   focusing → queued → pending → retrieved → rendering → done | error
// queued   = another /api/chart request is on the wire, ours waits (askBackend's FIFO queue);
// pending  = our request is really on the wire (timer runs only here);
// retrieved= the real response arrived; we replay its real graph walk;
// rendering= answer, visions, evidence, discoveries are being drawn; done = finished.
// Cached answers (same query earlier this voyage) skip queued/pending and are labelled as such.

import { askBackend, getMeta, inFlight, queuePosition, isCached } from '../api/backend.js';
import { createPanel } from './panel.js';
import { extractEntities } from './evidence.js';

const MAX_RUNS = 10;
const arr = (v) => (Array.isArray(v) ? v : []);
const str = (v) => (typeof v === 'string' ? v : '');
const keyOf = (q) => String(q).trim().replace(/\s+/g, ' ').toLowerCase();
const clip = (s, n = 80) => { const t = str(s); return t.length > n ? `${t.slice(0, n - 1)}…` : t; };

export function initResearch({ V, store, bus, world, audio } = {}) {
  let root = document.getElementById('research');
  if (!root) { // the shell owns #research; create it only so research never breaks
    root = document.createElement('aside');
    root.id = 'research';
    document.body.appendChild(root);
  }

  const runs = new Map(); // query key -> run
  const order = [];       // most recent first
  let active = null;
  let rootName = 'Mahabharata';
  let seq = 0;
  let calmTimer = 0;

  // ---------------------------------------------------------------- world / audio (all optional)
  // The world is driven through the bus only (main.js forwards 'world:mood' / 'world:highlight' to
  // world.setMood / world.highlight); calling the world directly as well would repeat every call.
  void world;
  function mood(m) {
    clearTimeout(calmTimer);
    bus.emit('world:mood', { mood: m });
  }
  function highlight(ids) {
    const clean = [...new Set(arr(ids).filter((x) => typeof x === 'string' && x))];
    bus.emit('world:highlight', { ids: clean });
  }
  // A run is "in the foreground" only when it is the one shown AND the drawer is open.
  const foreground = (run) => run === active && panel.isOpen;
  // main.js reacts to every 'research:state' (investigate / reveal / error). When the event came
  // from a background run, put the world back to what the foreground actually shows.
  function reassert() {
    if (active && panel.isOpen) moodFor(active);
    else { mood('calm'); highlight([]); }
  }
  function cue(name, volume) {
    try { if (audio && typeof audio.cue === 'function' && audio.unlocked !== false) audio.cue(name, volume); } catch { /* audio is decoration */ }
  }
  const relatedChars = (run) => arr(run.question && run.question.related_entities && run.question.related_entities.characters);
  const foundIds = (run) => {
    const e = run.entities || {};
    return [...arr(e.characters), ...arr(e.locations)].map((x) => x.id).concat(relatedChars(run));
  };
  function moodFor(run) {
    if (!run) return;
    if (['focusing', 'queued', 'pending', 'retrieved', 'rendering'].includes(run.state)) { mood('investigate'); highlight(relatedChars(run)); }
    else if (run.state === 'done') { mood('reveal'); highlight(foundIds(run)); }
    else if (run.state === 'error') { mood('calm'); }
  }

  // ---------------------------------------------------------------- panel
  const panel = createPanel({
    root, V, store, bus,
    actions: {
      ask: (query) => bus.emit('question:select', { query }),
      retry: (run) => retry(run),
      withdraw: (run) => { if (run && run.state === 'queued') run.controller.abort(); },
      focusRun: (run) => focusRun(run),
      skipReplay: (run) => panel.skipReplay(run),
      continueFrom: (run) => {
        const ch = run.chapter || (store && store.state && V && V.chapterById && V.chapterById.get(store.state.current));
        const next = ch && arr(ch.next_chapters).find((id) => V && V.chapterById && V.chapterById.has(id));
        if (!next) return;
        panel.close();
        bus.emit('chapter:enter', { chapterId: next, from: 'continue' });
      },
    },
  });

  getMeta()
    .then((m) => {
      if (m && m.graph && typeof m.graph.root === 'string' && m.graph.root.trim()) rootName = m.graph.root.trim();
      panel.setMeta(m);
    })
    .catch((e) => console.warn('[research] /api/meta unavailable:', e && e.message));

  // ---------------------------------------------------------------- state machine
  function pendingMs(run) {
    if (!run.pendingAt) return 0;
    return Math.round((run.retrievedAt || run.endedAt || performance.now()) - run.pendingAt);
  }

  function setState(run, state, extra = {}) {
    run.state = state;
    run.times[state] = performance.now();
    panel.update(run);
    // Queue positions shift whenever anything changes.
    for (const r of order) {
      if (r.state !== 'queued') continue;
      const pos = queuePosition(r.query);
      if (pos && pos !== r.queuePosition) { r.queuePosition = pos; panel.update(r); }
    }
    panel.setLog(order);
    const fg = foreground(run);
    bus.emit('research:state', Object.assign({
      state,
      questionId: run.qid || null,
      chapterId: run.chapter ? run.chapter.id : null,
      query: run.query,
      runId: run.id,
      elapsedMs: pendingMs(run),
      fromCache: !!run.fromCache,
      background: !fg, // true when the run is not what the open drawer shows
    }, extra));
    if (!fg) reassert();
  }

  function makeRun({ qid, question, chapter, query, title }) {
    return {
      id: `run${++seq}`, key: keyOf(query), qid, question, chapter, query, title,
      state: null, times: {}, t0: performance.now(), pendingAt: 0, retrievedAt: 0, endedAt: 0,
      queuePosition: 0, response: null, error: null, fromCache: false, entities: null,
      controller: new AbortController(), ui: null,
    };
  }

  function remember(run) {
    runs.set(run.key, run);
    order.unshift(run);
    // Forget old finished runs (the backend cache still holds their real answers).
    while (order.length > MAX_RUNS) {
      const idx = order.map((r, i) => [r, i]).reverse().find(([r]) => r !== active && (r.state === 'done' || r.state === 'error'));
      if (!idx) break;
      const [old, i] = idx;
      order.splice(i, 1);
      if (runs.get(old.key) === old) runs.delete(old.key);
      panel.disposeRun(old);
    }
  }

  async function start(run) {
    remember(run);
    active = run;
    panel.open(); // open first so show() can move focus to the question heading
    panel.show(run, { focusHeading: true });
    setState(run, 'focusing');
    mood('investigate');
    highlight(relatedChars(run));

    let promise;
    try {
      promise = askBackend(run.query, {
        signal: run.controller.signal,
        onStart: (t) => {
          run.pendingAt = t || performance.now();
          run.queuePosition = 0;
          setState(run, 'pending');
        },
      });
      // Not on the wire yet and not answered from cache → it is waiting behind another request.
      if (run.state === 'focusing' && !isCached(run.query)) {
        const busy = inFlight();
        if (busy && keyOf(busy.query) !== run.key) {
          run.queuePosition = queuePosition(run.query) || 1;
          setState(run, 'queued');
        }
      }
      const data = await promise;
      await onResponse(run, data);
    } catch (err) {
      onError(run, err);
    }
  }

  async function onResponse(run, data) {
    run.response = data && typeof data === 'object' ? data : {};
    run.fromCache = !!run.response.fromCache;
    run.retrievedAt = performance.now();

    const results = arr(run.response.results);
    const texts = results.filter((r) => r && !r.tool && typeof r.text === 'string').map((r) => r.text);
    const ctx = arr(run.response.retrieval && run.response.retrieval.context).map((c) => str(c && c.content));
    run.entities = extractEntities(V, texts.concat(ctx));

    setState(run, 'retrieved', { data: run.response, serverElapsedMs: run.response.elapsed_ms ?? null });
    try { await panel.renderRetrieval(run, rootName); } catch (e) { console.warn('[research] trace replay failed', e); }

    setState(run, 'rendering');
    try { panel.renderResults(run); } catch (e) { console.error('[research] rendering results failed', e); }
    run.endedAt = performance.now();

    const ok = results.some((r) => r && ((r.tool && !r.error) || (!r.tool && str(r.text).trim())));
    if (run.qid && store && typeof store.answer === 'function') {
      try { store.answer(run.qid, { at: new Date().toISOString(), query: run.query, ok }); } catch (e) { console.warn('[research] store.answer failed', e); }
    }
    const ids = foundIds(run);
    setState(run, 'done', { data: run.response, entities: run.entities, highlight: ids, ok });

    if (foreground(run)) {
      mood('reveal');
      highlight(ids);
      cue('bell', 0.35);
    } else {
      bus.emit('toast', { message: `Answer ready: “${clip(run.title)}” — open Research or choose the question again to read it.`, kind: 'info' });
    }
  }

  function onError(run, err) {
    const e = err || {};
    run.error = {
      status: Number.isFinite(e.status) ? e.status : 0,
      message: str(e.message) || 'Unknown error',
      serverMessage: e.serverMessage,
      aborted: e.name === 'AbortError' || !!e.aborted,
    };
    run.endedAt = performance.now();
    // A failure is NOT recorded with store.answer (that would mark the question answered).
    if (!run.error.aborted) console.warn(`[research] "${run.title}" failed:`, run.error.message);
    setState(run, 'error', { error: run.error });
    if (foreground(run)) {
      if (run.error.aborted) {
        mood('calm');
      } else {
        mood('error');
        calmTimer = setTimeout(() => { if (active === run && run.state === 'error') bus.emit('world:mood', { mood: 'calm' }); }, 1800);
      }
    } else if (!run.error.aborted) {
      bus.emit('toast', { message: `Research failed: “${clip(run.title)}” — ${clip(run.error.message, 120)}`, kind: 'error' });
    }
  }

  function retry(run) {
    if (!run || run.state !== 'error') return;
    const fresh = makeRun(run);
    const i = order.indexOf(run);
    if (i >= 0) order.splice(i, 1);
    if (runs.get(run.key) === run) runs.delete(run.key);
    panel.disposeRun(run);
    start(fresh);
  }

  function focusRun(run) {
    if (!run) return;
    active = run;
    panel.open();
    panel.show(run, { focusHeading: true });
    panel.setLog(order);
    moodFor(run);
  }

  // ---------------------------------------------------------------- question:select
  function onSelect(payload) {
    const p = payload && typeof payload === 'object' ? payload : {};
    let question = null; let chapter = null; let qid = null; let query = ''; let title = '';

    if (typeof p.questionId === 'string' && p.questionId) {
      const entry = V && V.questionById && V.questionById.get(p.questionId);
      if (entry && entry.question) {
        question = entry.question;
        chapter = entry.chapter || (V.chapterById && V.chapterById.get(p.chapterId)) || null;
        qid = str(question.id) || p.questionId;
        title = str(question.question).trim();
        query = str(question.search_query).trim() || title; // search_query is what the backend is asked
        title = title || query;
      }
    }
    if (!query && typeof p.query === 'string' && p.query.trim()) { // free-form "Ask the epic"
      query = p.query.trim().slice(0, 600);
      title = query;
    }
    if (!query) {
      bus.emit('toast', { message: 'That question could not be found in the voyage data.', kind: 'error' });
      return;
    }

    const existing = runs.get(keyOf(query));
    if (existing) { focusRun(existing); return; } // duplicate clicks focus the existing run
    start(makeRun({ qid, question, chapter, query, title }));
  }

  bus.on('question:select', onSelect);
  // Reopening the drawer brings the shown run's mood back (main.js calms the world on close).
  bus.on('research:open', () => { panel.open({ silent: true }); if (active && active.state) moodFor(active); });
  bus.on('research:close', () => panel.close({ silent: true }));
  bus.on('research:focus', (p) => panel.setFocus(!!(p && p.on), { silent: true }));

  return {
    ask: (query) => onSelect({ query }),
    select: onSelect,
    open: () => panel.open(),
    close: () => panel.close(),
    get active() { return active; },
  };
}
