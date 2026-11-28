// Evidence: the REAL graph walk (retrieval.trace) replayed as an illuminated manuscript lineage,
// the REAL retrieved context shown as parchment cards, and entity extraction from real text.
// Nothing is invented: every node, path, excerpt and entity comes from the backend response or
// from names already present in the voyage data (V.nameIndex).

import { esc, prefersReducedMotion } from '../core/util.js';
import { renderGraphMap, loadOutline } from './graphview.js';

const arr = (v) => (Array.isArray(v) ? v : []);
const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** Summary numbers for a trace (used in headers and the collapsed lineage line). */
export function summarizeTrace(retrieval) {
  const trace = arr(retrieval && retrieval.trace);
  const branches = trace.filter((t) => t && t.kind === 'branch').length;
  const leaves = trace.filter((t) => t && t.kind === 'leaf');
  const limits = trace.filter((t) => t && t.kind === 'limit').length;
  const chars = leaves.reduce((s, t) => s + (num(t.chars) || 0), 0);
  return { branches, leaves: leaves.length, limits, chars, contexts: arr(retrieval && retrieval.context).length };
}

const fmtInt = (n) => (num(n) == null ? '' : Math.round(n).toLocaleString('en-US'));

/**
 * Render the lineage tree into `container`, revealing nodes in real trace order.
 * @returns {{done: Promise<void>, skip: () => void}}
 */
export function renderLineage(container, retrieval, { rootName = 'Mahabharata', reducedMotion } = {}) {
  const reduce = reducedMotion ?? prefersReducedMotion();
  container.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'ev-lineage';
  container.appendChild(wrap);

  if (!retrieval) {
    wrap.innerHTML = `<p class="ev-note">The server did not return a knowledge-graph walk for this question, so there is no lineage to show.</p>`;
    return { done: Promise.resolve(), skip() {} };
  }
  const trace = arr(retrieval.trace).filter((t) => t && typeof t === 'object');
  const parts = [];
  if (retrieval.error) {
    let why = retrieval.error;
    if (typeof why !== 'string') { try { why = JSON.stringify(why); } catch { why = String(why); } }
    parts.push(`<p class="ev-note ev-note-error"><b>The graph walk reported an error:</b> ${esc(why)}</p>`);
  }
  if (!trace.length) {
    parts.push(`<p class="ev-note">The graph walk returned no steps${retrieval.error ? '' : ' for this question'}.</p>`);
    wrap.innerHTML = parts.join('');
    return { done: Promise.resolve(), skip() {} };
  }

  const s = summarizeTrace(retrieval);
  const ms = num(retrieval.elapsed_ms);
  parts.push(`<p class="ev-lineage-sum">${s.branches} branch${s.branches === 1 ? '' : 'es'} weighed · ${s.leaves} manuscript${s.leaves === 1 ? '' : 's'} gathered${s.chars ? ` · ${fmtInt(s.chars)} characters` : ''}${ms != null ? ` · walked in ${(ms / 1000).toFixed(1)}\u00a0s` : ''}</p>`);

  const rows = trace.map((t) => {
    const path = arr(t.path).map(String);
    const depth = Math.max(0, num(t.depth) ?? path.length);
    const name = path.length ? path[path.length - 1] : rootName;
    let body = '';
    let cls = '';
    if (t.kind === 'branch') {
      cls = path.length ? 'ev-branch' : 'ev-root';
      const sel = arr(t.selected).map(String);
      const opts = num(t.options);
      body = `<span class="ev-name">${esc(name)}</span>`
        + (sel.length
          ? `<span class="ev-chose">chose ${sel.length}${opts != null ? ` of ${opts}` : ''}:</span><span class="ev-keys">${sel.map((k) => `<span class="ev-key">${esc(k)}</span>`).join('')}</span>`
          : `<span class="ev-chose">no branch chosen${opts != null ? ` (of ${opts})` : ''}</span>`);
    } else if (t.kind === 'leaf') {
      cls = 'ev-leaf';
      body = `<span class="ev-name">${esc(name)}</span><span class="ev-chars">${num(t.chars) != null ? `${fmtInt(t.chars)} chars` : 'leaf'}</span>`;
    } else if (t.kind === 'limit') {
      cls = 'ev-limit';
      body = `<span class="ev-name">${esc(name)}</span><span class="ev-chose">walk limit reached here</span>`;
    } else {
      return '';
    }
    const crumb = path.length > 1 ? ` title="${esc(path.join(' › '))}"` : '';
    return `<li class="ev-node ${cls}" style="--d:${Math.min(depth, 6)}"${crumb}><span class="ev-glyph" aria-hidden="true"></span><span class="ev-row">${body}</span></li>`;
  }).filter(Boolean);

  parts.push(`<div class="ev-map" data-role="graph-map"></div>`);
  parts.push(`<details class="ev-steps"><summary>Step-by-step walk (text)</summary><ol class="ev-tree" aria-label="Path through the knowledge graph">${rows.join('')}</ol></details>`);
  wrap.innerHTML = parts.join('');

  // The graph "mandala": real graph names (/api/graph) + the real walk painted on it.
  let graph = null, graphSkipped = false;
  const mapBox = wrap.querySelector('[data-role="graph-map"]');
  const graphDone = loadOutline().then((outline) => {
    graph = renderGraphMap(mapBox, retrieval, { rootName, outline, reducedMotion: reduce });
    if (graphSkipped) graph.skip();
    return graph.done;
  }).catch(() => {});
  const withGraph = (r) => ({
    done: Promise.all([r.done, graphDone]).then(() => {}),
    skip() { graphSkipped = true; r.skip(); if (graph) graph.skip(); },
  });

  const nodes = [...wrap.querySelectorAll('.ev-node')];
  if (reduce || !nodes.length) {
    nodes.forEach((n) => n.classList.add('is-in'));
    return withGraph({ done: Promise.resolve(), skip() {} });
  }

  // Staggered reveal (~120 ms per node, compressed for very long walks so it never drags).
  const step = Math.max(45, Math.min(120, 3600 / nodes.length));
  let i = 0;
  let timer = 0;
  let finish;
  const done = new Promise((r) => { finish = r; });
  const tick = () => {
    if (i >= nodes.length) { finish(); return; }
    nodes[i++].classList.add('is-in');
    timer = setTimeout(tick, step);
  };
  tick();
  return withGraph({
    done,
    skip() {
      clearTimeout(timer);
      nodes.forEach((n) => n.classList.add('is-in'));
      i = nodes.length;
      finish();
    },
  });
}

/** Render retrieved context excerpts as parchment cards (expandable). */
export function renderContext(container, retrieval) {
  container.innerHTML = '';
  const ctx = arr(retrieval && retrieval.context).filter((c) => c && typeof c === 'object');
  if (!retrieval) {
    container.innerHTML = `<p class="ev-note">No retrieval data came back with this answer.</p>`;
    return;
  }
  if (!ctx.length) {
    container.innerHTML = `<p class="ev-note">${retrieval.error ? 'Because the graph walk failed, no' : 'No'} excerpts were retrieved from the knowledge graph for this question${retrieval.error ? '' : ' — the answer rests on the model alone'}.</p>`;
    return;
  }
  const list = document.createElement('div');
  list.className = 'ev-cards';
  ctx.forEach((c, idx) => {
    const path = String(c.path || '').split('>').map((p) => p.trim()).filter(Boolean);
    const content = String(c.content || '');
    const chars = num(c.chars);
    const truncated = chars != null && chars > content.length;
    const card = document.createElement('article');
    card.className = 'ev-card';
    card.innerHTML = `
      <div class="ev-card-label">Retrieved from the curated knowledge graph</div>
      <nav class="ev-crumbs" aria-label="Location in the knowledge graph">${path.map((p) => `<span>${esc(p)}</span>`).join('<i aria-hidden="true">›</i>') || '<span>(unnamed branch)</span>'}</nav>
      <p class="ev-content" id="ev-content-${idx}-${Math.random().toString(36).slice(2, 7)}">${esc(content)}</p>
      <div class="ev-card-foot">
        <span class="ev-size">${chars != null ? `${fmtInt(chars)} characters` : ''}${truncated ? ` · showing the first ${fmtInt(content.length)}` : ''}</span>
        <button type="button" class="ev-more" aria-expanded="false">Unroll</button>
      </div>`;
    const btn = card.querySelector('.ev-more');
    const p = card.querySelector('.ev-content');
    btn.setAttribute('aria-controls', p.id);
    btn.addEventListener('click', () => {
      const open = card.classList.toggle('is-open');
      btn.setAttribute('aria-expanded', String(open));
      btn.textContent = open ? 'Roll up' : 'Unroll';
    });
    // Hide the toggle when the excerpt already fits (only measurable once laid out).
    requestAnimationFrame(() => {
      if (p.isConnected && p.clientHeight > 0 && p.scrollHeight <= p.clientHeight + 2) btn.hidden = true;
    });
    list.appendChild(card);
  });
  container.appendChild(list);
}

// ------------------------------------------------------------------ entity extraction

const stripMarks = (s) => String(s).normalize('NFD').replace(/[̀-ͯ]/g, '');
const reEsc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

let compiled = null; // {index, re, byName}

function compile(V) {
  const index = arr(V && V.nameIndex);
  if (compiled && compiled.index === index) return compiled;
  const byName = new Map(); // stripped lowercase name -> [{id,type}]
  for (const ent of index) {
    if (!ent || typeof ent.id !== 'string') continue;
    for (const raw of arr(ent.names)) {
      if (typeof raw !== 'string') continue;
      const n = stripMarks(raw.trim().toLowerCase());
      if (n.length < 3) continue;
      if (!byName.has(n)) byName.set(n, []);
      const list = byName.get(n);
      if (!list.some((x) => x.id === ent.id && x.type === ent.type)) list.push({ id: ent.id, type: ent.type });
    }
  }
  const names = [...byName.keys()].sort((a, b) => b.length - a.length);
  let re = null;
  if (names.length) {
    try {
      re = new RegExp(`(?<![\\p{L}\\p{N}])(?:${names.map(reEsc).join('|')})(?![\\p{L}\\p{N}])`, 'giu');
    } catch {
      re = null;
    }
  }
  compiled = { index, re, byName };
  return compiled;
}

function displayName(V, type, id, fallback) {
  const map = type === 'character' ? V.characterById : type === 'location' ? V.locationById : V.eventById;
  const item = map && typeof map.get === 'function' ? map.get(id) : null;
  return (item && (item.name || item.title)) || fallback;
}

/**
 * Find voyage entities named in real text (answer + retrieved context).
 * Case-insensitive with word boundaries; names shorter than 4 characters only count when the
 * occurrence is capitalised in the text (a proper noun), to avoid matching ordinary words.
 * @returns {{characters: Ent[], locations: Ent[], events: Ent[]}}  Ent = {id, type, name, count}
 */
export function extractEntities(V, texts) {
  const out = { characters: [], locations: [], events: [] };
  if (!V) return out;
  const { re, byName } = compile(V);
  if (!re) return out;
  const counts = new Map(); // `${type}:${id}` -> {id,type,count,matched}
  for (const t of arr(texts)) {
    if (typeof t !== 'string' || !t) continue;
    const text = stripMarks(t);
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(text))) {
      const hit = m[0];
      const key = hit.toLowerCase();
      if (key.length < 4 && hit[0] !== hit[0].toUpperCase()) continue;
      for (const ent of byName.get(key) || []) {
        const k = `${ent.type}:${ent.id}`;
        const rec = counts.get(k) || { id: ent.id, type: ent.type, count: 0, matched: hit };
        rec.count++;
        counts.set(k, rec);
      }
    }
  }
  const sorted = [...counts.values()].sort((a, b) => b.count - a.count);
  for (const r of sorted) {
    const ent = { id: r.id, type: r.type, name: displayName(V, r.type, r.id, r.matched), count: r.count };
    if (r.type === 'character') out.characters.push(ent);
    else if (r.type === 'location') out.locations.push(ent);
    else if (r.type === 'event') out.events.push(ent);
  }
  return out;
}
