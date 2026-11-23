// Real backend access for the voyage: POST /api/chart and GET /api/meta.
//
// Guarantees (FRONTEND_CONTRACT §Backend contract):
//  - single-flight per query: asking the same query while it is queued or in flight joins that request;
//  - at most ONE /api/chart request on the wire at a time (Groq rate limits), the rest wait in a FIFO queue;
//  - successful responses are cached in memory for this session (returned with fromCache:true);
//  - an AbortSignal withdraws a request that is still QUEUED; an in-flight request is never aborted
//    (the server keeps working on it anyway, and its result is cached for later);
//  - failures throw Error objects carrying {status, message, serverMessage}.
// Nothing here ever fabricates a response.

const CHART_URL = '/api/chart';
const META_URL = '/api/meta';
// Generous ceiling so a hung connection cannot block the queue forever (real calls take ~5–40 s).
const REQUEST_TIMEOUT_MS = 180000;

const cache = new Map();   // key -> response JSON (successful only)
const entries = new Map(); // key -> pending entry (queued or in flight)
const queue = [];          // FIFO of entries not yet sent
let current = null;        // the entry on the wire
let metaPromise = null;

/** Normalise a query into a cache / single-flight key. */
function keyOf(query) {
  const lang = (window.BODHAN && window.BODHAN.lang()) || 'en'; // same question, different language = different answer
  return lang + '|' + String(query).trim().replace(/\s+/g, ' ').toLowerCase();
}

function backendError(status, message, serverMessage) {
  const err = new Error(message);
  err.name = 'BackendError';
  err.status = status;
  if (serverMessage !== undefined) err.serverMessage = serverMessage;
  return err;
}

function abortError() {
  const err = new Error('The question was withdrawn before it was sent.');
  err.name = 'AbortError';
  err.status = 0;
  err.aborted = true;
  return err;
}

/** Read a fetch Response as JSON without throwing on an empty/non-JSON body. */
async function readJSON(res) {
  let text = '';
  try { text = await res.text(); } catch { return null; }
  if (!text) return null;
  try { return JSON.parse(text); } catch { return { _raw: text.slice(0, 400) }; }
}

function describeHttp(status, data) {
  const server = data && typeof data.error === 'string' ? data.error : null;
  if (server) return { message: server, server };
  if (data && data._raw) return { message: `HTTP ${status}: ${data._raw}`, server: null };
  return { message: `HTTP ${status}`, server: null };
}

/** Send the next queued entry if nothing is on the wire. */
function pump() {
  if (current || !queue.length) return;
  const entry = queue.shift();
  current = entry;
  entry.started = true;
  entry.startedAt = performance.now();
  for (const w of entry.waiters) {
    try { w.onStart && w.onStart(entry.startedAt); } catch (e) { console.error('[backend] onStart failed', e); }
  }
  run(entry).then(
    (data) => {
      cache.set(entry.key, data);
      for (const w of entry.waiters) w.resolve(data);
    },
    (err) => {
      for (const w of entry.waiters) w.reject(err);
    },
  ).finally(() => {
    entries.delete(entry.key);
    current = null;
    pump();
  });
}

async function run(entry) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  let res;
  try {
    res = await fetch(CHART_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: entry.query, lang: entry.lang }),
      signal: ctrl.signal,
    });
  } catch (e) {
    clearTimeout(timer);
    if (ctrl.signal.aborted) {
      throw backendError(0, `No response from the server after ${Math.round(REQUEST_TIMEOUT_MS / 1000)} s.`);
    }
    throw backendError(0, `Could not reach the JARVIS server (${(e && e.message) || 'network error'}).`);
  }
  const data = await readJSON(res);
  clearTimeout(timer);
  // The timeout also covers reading the body: report it as a timeout, not as "invalid JSON".
  if (ctrl.signal.aborted && (!data || data._raw !== undefined)) {
    throw backendError(0, `The server stopped responding after ${Math.round(REQUEST_TIMEOUT_MS / 1000)} s.`);
  }
  if (!res.ok) {
    const { message, server } = describeHttp(res.status, data);
    throw backendError(res.status, message, server);
  }
  if (!data || typeof data !== 'object' || Array.isArray(data) || data._raw !== undefined) {
    throw backendError(res.status, 'The server returned a response that is not valid JSON.');
  }
  return data;
}

/**
 * Ask the real backend. Resolves with the /api/chart JSON.
 * @param {string} query
 * @param {{signal?: AbortSignal, onStart?: (t:number)=>void}} [opts]
 *   onStart is called when the request actually goes on the wire (never for cached answers).
 */
export async function askBackend(query, opts = {}) {
  const { signal, onStart } = opts || {};
  if (typeof query !== 'string' || !query.trim()) throw backendError(400, 'The question is empty.');
  const text = query.trim();
  const key = keyOf(text);

  if (cache.has(key)) {
    await Promise.resolve(); // stay async like a real call
    return Object.assign({}, cache.get(key), { fromCache: true });
  }
  if (signal && signal.aborted) throw abortError();

  let entry = entries.get(key);
  if (!entry) {
    entry = { key, query: text, lang: (window.BODHAN && window.BODHAN.lang()) || 'en', waiters: new Set(), started: false, startedAt: 0 };
    entries.set(key, entry);
    queue.push(entry);
  }

  const promise = new Promise((resolve, reject) => {
    let onAbort = null;
    const detach = () => { if (signal && onAbort) signal.removeEventListener('abort', onAbort); };
    const waiter = {
      resolve: (v) => { detach(); resolve(v); },
      reject: (e) => { detach(); reject(e); },
      onStart,
    };
    entry.waiters.add(waiter);
    if (entry.started && onStart) {
      try { onStart(entry.startedAt); } catch (e) { console.error('[backend] onStart failed', e); }
    }
    if (signal) {
      onAbort = () => {
        if (entry.started) return; // in-flight requests run to completion (and get cached)
        entry.waiters.delete(waiter);
        waiter.reject(abortError());
        if (!entry.waiters.size) {
          const i = queue.indexOf(entry);
          if (i >= 0) queue.splice(i, 1);
          entries.delete(key);
        }
      };
      signal.addEventListener('abort', onAbort, { once: true });
    }
  });

  pump();
  return promise;
}

/** The request currently on the wire, or null. */
export function inFlight() {
  return current ? { query: current.query, startedAt: current.startedAt } : null;
}

/** Number of requests waiting behind the one in flight. */
export function queuedCount() {
  return queue.length;
}

/** 1-based place in the wait line for a query (0 = in flight or not waiting). */
export function queuePosition(query) {
  const i = queue.findIndex((e) => e.key === keyOf(query));
  return i >= 0 ? i + 1 : 0;
}

/** True when a successful answer for this query is held in the session cache. */
export function isCached(query) {
  return typeof query === 'string' && cache.has(keyOf(query));
}

/** GET /api/meta, memoised for the session (a failure is not memoised, so it can be retried). */
export async function getMeta() {
  if (!metaPromise) {
    metaPromise = (async () => {
      let res;
      try {
        res = await fetch(META_URL, { headers: { Accept: 'application/json' } });
      } catch (e) {
        throw backendError(0, `Could not reach the JARVIS server (${(e && e.message) || 'network error'}).`);
      }
      const data = await readJSON(res);
      if (!res.ok) {
        const { message, server } = describeHttp(res.status, data);
        throw backendError(res.status, message, server);
      }
      if (!data || typeof data !== 'object' || data._raw !== undefined) {
        throw backendError(res.status, 'The server returned metadata that is not valid JSON.');
      }
      return data;
    })();
    metaPromise.catch(() => { metaPromise = null; });
  }
  return metaPromise;
}
