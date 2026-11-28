// Knowledge-graph "mandala": the REAL graph (names from GET /api/graph) with the REAL walk (retrieval.trace)
// painted on it. Nothing is invented:
//   activated   = branches the model chose and leaves whose text was gathered   (gold, glowing)
//   deactivated = real siblings the model weighed at that step and did not choose (hollow, dim)
//   not reached = the rest of the graph, never visited                           (faint dots)
// Gold threads are the edges (parent -> child) the walk actually followed. If /api/graph is unavailable the
// view falls back to the trace alone (unchosen siblings are then drawn as unnamed ghosts, from `options`).

import { esc, prefersReducedMotion } from '../core/util.js';

const arr = (v) => (Array.isArray(v) ? v : []);
const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const KEY = (p) => p.join('\u0001');
const NS = 'http://www.w3.org/2000/svg';
const SIZE = 560, C = SIZE / 2;
const RING = [0, 100, 215, 262, 300];            // radius of each depth

// ---------------------------------------------------------------- css + outline (loaded once)
let cssLoaded = false;
function ensureCss() {
  if (cssLoaded || typeof document === 'undefined') return;
  cssLoaded = true;
  const l = document.createElement('link');
  l.rel = 'stylesheet';
  l.href = '/css/voyage/graph.css';
  document.head.appendChild(l);
}
let outlinePromise = null;
export function loadOutline() {
  if (!outlinePromise) {
    outlinePromise = fetch('/api/graph', { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => (j && j.tree && typeof j.tree === 'object' ? j : null))
      .catch(() => null);
  }
  return outlinePromise;
}

// ---------------------------------------------------------------- model
function lookup(tree, path) {
  let n = tree;
  for (const k of path) { if (!n || typeof n !== 'object' || !(k in n)) return undefined; n = n[k]; }
  return n;
}

function buildModel(retrieval, outline, rootName) {
  const trace = arr(retrieval && retrieval.trace).filter((t) => t && typeof t === 'object');
  const branchBy = new Map(), leafBy = new Map(), limitBy = new Map();
  trace.forEach((t) => {
    const k = KEY(arr(t.path).map(String));
    if (t.kind === 'branch') branchBy.set(k, t);
    else if (t.kind === 'leaf') leafBy.set(k, t);
    else if (t.kind === 'limit') limitBy.set(k, t);
  });
  if (!branchBy.has('')) return null;               // no root step: nothing meaningful to draw

  const tree = outline && outline.tree;
  const nodes = [];
  const mk = (path, name, parent, ghost) => {
    const n = { id: KEY(path), path, name, depth: path.length, parent, children: [], ghost: !!ghost,
      state: 'dim', expanded: false, chars: null };
    nodes.push(n);
    if (parent) parent.children.push(n);
    return n;
  };
  const root = mk([], rootName, null, false);

  const expand = (node) => {
    const entry = branchBy.get(node.id);
    if (!entry) return;
    node.expanded = true;
    const sel = arr(entry.selected).map(String);
    const sub = tree ? lookup(tree, node.path) : undefined;
    let names;
    if (sub && typeof sub === 'object') names = Object.keys(sub);
    else {
      const extra = Math.max(0, (num(entry.options) ?? sel.length) - sel.length);
      names = sel.concat(Array(extra).fill(null));  // unnamed ghosts: the model weighed them, we only know how many
    }
    names.forEach((nm, i) => {
      const path = node.path.concat(nm == null ? `…${i}` : nm);
      const c = mk(path, nm == null ? '…' : nm, node, nm == null);
      c.chosen = nm != null && sel.includes(nm);
      c.isLeafKind = !!(sub && typeof sub === 'object' && typeof sub[nm] === 'number') || leafBy.has(c.id);
    });
    node.children.forEach((c) => expand(c));
  };
  // The root's children are always drawn (all real top-level topics), even those never explored.
  expand(root);
  // every real node of the whole graph (so "not reached" counts the parts that were never even drawn)
  const countAll = (t) => (t && typeof t === 'object' ? Object.values(t).reduce((n, v) => n + 1 + countAll(v), 0) : 0);
  const totalNodes = tree ? countAll(tree) : nodes.length - 1;
  return { root, nodes, trace, branchBy, leafBy, limitBy, haveOutline: !!tree, totalNodes };
}

// ---------------------------------------------------------------- layout (radial, weighted wedges)
function layout(model) {
  const weight = (n) => (n.expanded ? 1 + n.children.length * 0.7 : 1);
  const place = (n, a0, a1) => {
    n.angle = (a0 + a1) / 2;
    n.wedge = a1 - a0;
    const kids = n.children;
    if (!kids.length) return;
    const total = kids.reduce((s, k) => s + weight(k), 0);
    const span = n.depth === 0 ? a1 - a0 : (a1 - a0) * 0.92;
    let a = n.depth === 0 ? a0 : n.angle - span / 2;
    kids.forEach((k) => {
      const w = (weight(k) / total) * span;
      place(k, a, a + w);
      a += w;
    });
  };
  place(model.root, -Math.PI / 2, Math.PI * 1.5);
  model.nodes.forEach((n) => {
    const r = RING[Math.min(n.depth, RING.length - 1)];
    n.r = r;
    n.x = C + r * Math.cos(n.angle);
    n.y = C + r * Math.sin(n.angle);
    // node radius shrinks when neighbours are tight (arc length per sibling)
    const sib = n.parent ? n.parent.children.length : 1;
    const arc = n.depth ? (r * (n.parent.depth === 0 ? Math.PI * 2 : n.parent.wedge * 0.92)) / Math.max(1, sib) : 99;
    n.cap = Math.max(2.5, Math.min(11, arc * 0.4));
  });
}

// ---------------------------------------------------------------- svg helpers
const el = (name, attrs = {}, cls) => {
  const e = document.createElementNS(NS, name);
  Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, String(v)));
  if (cls) e.setAttribute('class', cls);
  return e;
};
const f1 = (n) => Math.round(n * 10) / 10;
function edgePath(a, b) {
  const rm = (a.r + b.r) / 2;
  const c1 = [C + rm * Math.cos(a.angle), C + rm * Math.sin(a.angle)];
  const c2 = [C + rm * Math.cos(b.angle), C + rm * Math.sin(b.angle)];
  return `M${f1(a.x)} ${f1(a.y)}C${f1(c1[0])} ${f1(c1[1])} ${f1(c2[0])} ${f1(c2[1])} ${f1(b.x)} ${f1(b.y)}`;
}
function mandalaBackdrop() {
  const g = el('g', {}, 'gm-back');
  RING.slice(1, 3).forEach((r, i) => g.appendChild(el('circle', { cx: C, cy: C, r, fill: 'none' }, `gm-ring gm-ring-${i + 1}`)));
  g.appendChild(el('circle', { cx: C, cy: C, r: RING[2] + 34, fill: 'none' }, 'gm-ring gm-ring-outer'));
  for (let i = 0; i < 24; i++) {            // fine spokes like the lines of a yantra
    const a = (i / 24) * Math.PI * 2;
    g.appendChild(el('line', { x1: f1(C + 40 * Math.cos(a)), y1: f1(C + 40 * Math.sin(a)), x2: f1(C + (RING[2] + 34) * Math.cos(a)), y2: f1(C + (RING[2] + 34) * Math.sin(a)) }, 'gm-spoke'));
  }
  return g;
}
function lotus(cx, cy, r) {                 // the root: eight-petal lotus
  const g = el('g', { transform: `translate(${cx} ${cy})` }, 'gm-lotus');
  for (let i = 0; i < 8; i++) {
    g.appendChild(el('path', { d: `M0 0C${r * 0.55} ${-r * 0.35} ${r * 0.55} ${-r * 0.95} 0 ${-r * 1.25}C${-r * 0.55} ${-r * 0.95} ${-r * 0.55} ${-r * 0.35} 0 0Z`, transform: `rotate(${i * 45})` }, 'gm-petal'));
  }
  g.appendChild(el('circle', { r: r * 0.34 }, 'gm-lotus-core'));
  return g;
}

// ---------------------------------------------------------------- main
/**
 * Draw the graph into `container` and replay the walk step by step.
 * @returns {{done: Promise<void>, skip: () => void}}
 */
export function renderGraphMap(container, retrieval, { rootName = 'Mahabharata', outline = null, reducedMotion, stepMs } = {}) {
  ensureCss();
  const model = buildModel(retrieval, outline, rootName);
  if (!model) return { done: Promise.resolve(), skip() {} };
  layout(model);
  const reduce = reducedMotion ?? prefersReducedMotion();

  const wrap = document.createElement('figure');
  wrap.className = 'gm';
  const svg = el('svg', { viewBox: `0 0 ${SIZE} ${SIZE}`, role: 'img', preserveAspectRatio: 'xMidYMid meet' }, 'gm-svg');
  const defs = el('defs');
  defs.innerHTML = `
    <radialGradient id="gm-bg" cx="50%" cy="50%" r="60%"><stop offset="0" stop-color="#2a2150" stop-opacity=".55"/><stop offset="1" stop-color="#0b0e22" stop-opacity="0"/></radialGradient>
    <filter id="gm-glow" x="-80%" y="-80%" width="260%" height="260%"><feGaussianBlur stdDeviation="3.2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
    <filter id="gm-glow-soft" x="-120%" y="-120%" width="340%" height="340%"><feGaussianBlur stdDeviation="6"/></filter>`;
  svg.appendChild(defs);
  svg.appendChild(el('rect', { x: 0, y: 0, width: SIZE, height: SIZE, fill: 'url(#gm-bg)' }));
  svg.appendChild(mandalaBackdrop());

  const eg = el('g', {}, 'gm-edges'), ng = el('g', {}, 'gm-nodes');
  svg.appendChild(eg); svg.appendChild(ng);

  const view = new Map();                       // node id -> {g, edge, node}
  model.nodes.forEach((n) => {
    if (!n.parent) return;
    const edge = el('path', { d: edgePath(n.parent, n), pathLength: 1 }, 'gm-edge is-dim');
    eg.appendChild(edge);
    const g = el('g', { transform: `translate(${f1(n.x)} ${f1(n.y)})` }, `gm-node d${Math.min(n.depth, 3)} is-dim${n.parent.depth > 0 ? ' is-hidden' : ''}${n.ghost ? ' is-ghost' : ''}`);
    const r = Math.max(2.5, n.cap * (n.depth === 1 ? 0.62 : 0.78));
    g.dataset.r = f1(r);
    g.appendChild(el('circle', { r: f1(r * 1.9) }, 'gm-aura'));
    g.appendChild(el('circle', { r: f1(r + 3.5) }, 'gm-halo'));
    g.appendChild(el('circle', { r: f1(r) }, 'gm-dot'));
    const badge = el('text', { y: f1(r * 0.36), 'font-size': f1(Math.max(7, r * 1.15)) }, 'gm-num');
    g.appendChild(badge);
    ng.appendChild(g);
    view.set(n.id, { g, edge, badge, node: n, r });
    if (n.parent.depth > 0) edge.classList.add('is-hidden');
  });
  svg.appendChild(lotus(C, C, 17));
  const rootLabel = el('text', { x: C, y: C + 38, 'text-anchor': 'middle' }, 'gm-root-label');
  rootLabel.textContent = rootName;
  svg.appendChild(rootLabel);
  wrap.appendChild(svg);

  // tooltip
  const tip = document.createElement('div');
  tip.className = 'gm-tip';
  tip.hidden = true;
  wrap.appendChild(tip);

  // ---- state machine over real trace entries
  let seq = 0;
  const awakened = [];                           // [{n, num}] in activation order
  const stats = { activated: 0, weighed: 0 };
  const setState = (n, state) => {
    const v = view.get(n.id);
    if (!v) return;
    n.state = state;
    v.g.classList.remove('is-dim', 'is-weighed', 'is-chosen', 'is-gathered', 'is-limit', 'is-visited');
    v.edge.classList.remove('is-dim', 'is-weighed', 'is-taken', 'is-limit');
    v.g.classList.add(`is-${state}`);
    v.g.classList.remove('is-hidden'); v.edge.classList.remove('is-hidden');
    if (state === 'chosen' || state === 'gathered' || state === 'visited') v.edge.classList.add('is-taken');
    else if (state === 'limit') v.edge.classList.add('is-limit');
    else v.edge.classList.add(state === 'weighed' ? 'is-weighed' : 'is-dim');
  };
  const awaken = (n) => {
    if (n.numbered) return;
    n.numbered = ++seq;
    awakened.push(n);
    const v = view.get(n.id);
    if (v) { v.badge.textContent = String(n.numbered); v.g.setAttribute('tabindex', '0'); }
    stats.activated++;
  };
  const apply = (t) => {
    const path = arr(t.path).map(String);
    const n = model.nodes.find((x) => x.id === KEY(path));
    if (!n) return;
    if (t.kind === 'branch') {
      if (n.parent) setState(n, n.state === 'gathered' ? 'gathered' : 'visited');
      n.children.forEach((c) => {
        if (c.chosen) { setState(c, c.expanded ? 'visited' : 'chosen'); awaken(c); }
        else { setState(c, 'weighed'); stats.weighed++; }
      });
    } else if (t.kind === 'leaf') {
      n.chars = num(t.chars);
      if (n.parent) { setState(n, 'gathered'); awaken(n); }
    } else if (t.kind === 'limit') {
      if (n.parent) setState(n, 'limit');
    }
  };
  const entries = model.trace.filter((t) => t.kind === 'branch' || t.kind === 'leaf' || t.kind === 'limit');

  // ---- tooltip + keyboard
  const describe = (n) => {
    const crumb = n.path.length ? n.path.filter((p) => !p.startsWith('…')).join(' › ') : rootName;
    const lines = {
      dim: 'Not reached by this walk',
      weighed: 'Weighed at this step, not chosen — set aside',
      chosen: 'Chosen — awakened',
      visited: 'Chosen — and walked further inside',
      gathered: `Chosen — its text was gathered${n.chars ? ` (${n.chars.toLocaleString('en-US')} characters)` : ''}`,
      limit: 'The walk reached its limit here',
    };
    return `<b>${esc(n.ghost ? 'An unnamed branch' : n.name)}</b><span>${esc(crumb)}</span><em class="st-${n.state}">${esc(lines[n.state] || '')}</em>`;
  };
  const showTip = (n, ev) => {
    tip.hidden = false;
    tip.innerHTML = describe(n);
    const box = wrap.getBoundingClientRect();
    const g = ev.currentTarget.getBoundingClientRect();
    const x = g.left + g.width / 2 - box.left, y = g.top - box.top;
    tip.style.left = `${Math.max(8, Math.min(box.width - 8, x))}px`;
    tip.style.top = `${y}px`;
    tip.classList.toggle('below', y < 90);
  };
  view.forEach((v, id) => {
    const n = v.node;
    v.g.addEventListener('pointerenter', (e) => showTip(n, e));
    v.g.addEventListener('focus', (e) => showTip(n, e));
    v.g.addEventListener('pointerleave', () => { tip.hidden = true; });
    v.g.addEventListener('blur', () => { tip.hidden = true; });
    v.g.addEventListener('click', (e) => showTip(n, e));
  });

  // ---- legend + awakened paths (accessible text alternative)
  const legend = document.createElement('div');
  legend.className = 'gm-legend';
  legend.innerHTML = `
    <span class="gm-lg gm-lg-on"><i></i>Awakened</span>
    <span class="gm-lg gm-lg-off"><i></i>Weighed, set aside</span>
    <span class="gm-lg gm-lg-dim"><i></i>Not reached</span>
    <span class="gm-lg gm-lg-thread"><i></i>Path followed</span>`;
  const list = document.createElement('ol');
  list.className = 'gm-paths';
  list.setAttribute('aria-label', 'Awakened parts of the knowledge graph, in the order the model chose them');
  const summary = document.createElement('p');
  summary.className = 'gm-sum';
  wrap.appendChild(legend);
  wrap.appendChild(summary);
  wrap.appendChild(list);
  container.appendChild(wrap);

  const refresh = (final) => {
    const total = model.totalNodes;
    summary.textContent = `${stats.activated} awakened · ${stats.weighed} weighed and set aside · ${Math.max(0, total - stats.activated - stats.weighed)} not reached`;
    list.innerHTML = awakened.map((n) => {
      const crumb = n.path.filter((p) => !p.startsWith('…')).map(esc).join(' › ');
      return `<li><span class="gm-n">${n.numbered}</span><span>${crumb}${n.state === 'gathered' && n.chars ? ` <em>${n.chars.toLocaleString('en-US')} chars</em>` : ''}</span></li>`;
    }).join('');
    svg.setAttribute('aria-label', `Knowledge graph of ${rootName}: ${stats.activated} parts awakened by this question, ${stats.weighed} weighed and set aside.`);
    if (final) wrap.classList.add('is-final');
  };

  const finishNow = () => { while (i < entries.length) apply(entries[i++]); refresh(true); };
  let i = 0, timer = 0, finish;
  const done = new Promise((r) => { finish = r; });
  if (reduce) {
    wrap.classList.add('no-anim');
    finishNow();
    finish();
    return { done, skip() {} };
  }
  refresh(false);
  const step = stepMs || Math.max(70, Math.min(260, 3600 / Math.max(1, entries.length)));
  const tick = () => {
    if (i >= entries.length) { refresh(true); finish(); return; }
    apply(entries[i++]);
    refresh(false);
    timer = setTimeout(tick, step);
  };
  tick();
  return {
    done,
    skip() { clearTimeout(timer); wrap.classList.add('no-anim'); finishNow(); finish(); },
  };
}
