// ui/knowledge.js — KNOWLEDGE (#view-knowledge)
// Tab 1 "Relationship web": an ECharts force graph built from V.characters + V.relationships, filterable by
//   family / teacher / alliance / rivalry, coloured by faction; click a node for a character card. A text list
//   view offers the same threads to keyboard and screen-reader users.
// Tab 2 "From your investigations": what this session's real research actually returned (bus research:state
//   'done' / 'error' payloads) — the questions asked, graph paths walked, context retrieved, tools rendered and
//   names recognised in the answer. Nothing is invented; an empty session says so.

import { esc, prefersReducedMotion } from '../core/util.js';

const CATS = {
  family: { label: 'Family', types: ['parent', 'sibling', 'half_sibling', 'spouse', 'adoptive_parent', 'guardian'], token: '--gold-soft', fb: '#E3C77E', dash: 'solid' },
  teacher: { label: 'Teacher', types: ['teacher'], token: '--celestial', fb: '#6F8FC9', dash: 'solid' },
  alliance: { label: 'Alliance', types: ['ally', 'friend', 'charioteer'], token: '--forest-light', fb: '#6E9C7E', dash: 'solid' },
  rivalry: { label: 'Rivalry', types: ['rival'], token: '--vermilion', fb: '#B5452F', dash: 'dashed' },
};
const TYPE_CAT = Object.fromEntries(Object.entries(CATS).flatMap(([k, c]) => c.types.map((t) => [t, k])));
const FACTIONS = [
  ['pandava', 'Pandava', '--gold', '#C9A24B'],
  ['kaurava', 'Kaurava', '--vermilion', '#B5452F'],
  ['neutral', 'Neutral', '--sandstone', '#C8A57A'],
  ['divine', 'Divine', '--celestial', '#6F8FC9'],
  ['narrator', 'Narrator', '--ivory-dim', '#CFC4AE'],
];
const TYPE_LABEL = {
  parent: 'parent', sibling: 'sibling', half_sibling: 'half-sibling', spouse: 'spouse', adoptive_parent: 'adoptive parent',
  guardian: 'guardian', teacher: 'teacher', ally: 'ally', friend: 'friend', charioteer: 'charioteer', rival: 'rival',
};

function cssVar(name, fb) {
  try { return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fb; } catch { return fb; }
}
function reducedMotion() {
  if (document.body.classList.contains('reduced-motion')) return true;
  try { return !!prefersReducedMotion(); } catch { return false; }
}
function ensureSection(id) {
  let sec = document.getElementById(id);
  if (sec) return sec;
  let views = document.getElementById('views');
  if (!views) { views = document.createElement('section'); views.id = 'views'; document.body.appendChild(views); }
  sec = document.createElement('section'); sec.id = id; views.appendChild(sec);
  return sec;
}
// main.js owns body view classes; this only fills the gap. `lastView` stops a stale timeout undoing a later change.
let lastView = null;
function ensureBodyView(view) {
  lastView = view;
  setTimeout(() => {
    if (lastView !== view) return;
    const b = document.body;
    if (b.classList.contains(`view-${view}`)) return;
    [...b.classList].filter((c) => c.startsWith('view-')).forEach((c) => b.classList.remove(c));
    b.classList.add(`view-${view}`);
  }, 0);
}
const reEsc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
function fmtTime(t) {
  const d = new Date(t);
  return isNaN(d) ? '' : d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function initKnowledge({ V, store, bus }) {
  const root = ensureSection('view-knowledge');
  const chars = (V?.characters || []).filter((c) => c && c.id);
  const charById = V?.characterById instanceof Map ? V.characterById : new Map(chars.map((c) => [c.id, c]));
  const rels = (V?.relationships || []).filter((r) => r && charById.has(r.from) && charById.has(r.to));
  const locById = V?.locationById instanceof Map ? V.locationById : new Map((V?.locations || []).map((l) => [l.id, l]));
  const eventById = V?.eventById instanceof Map ? V.eventById : new Map((V?.events || []).map((e) => [e.id, e]));
  const st = () => store?.state || {};

  /* ---------------- DOM */
  root.classList.add('vw', 'kn');
  root.setAttribute('role', 'region');
  root.setAttribute('aria-labelledby', 'view-knowledge-title');
  root.innerHTML = `
    <header class="vw-head">
      <div class="vw-titles">
        <p class="vw-eyebrow">Threads of kinship, teaching, loyalty and enmity</p>
        <h2 class="vw-title" id="view-knowledge-title">Knowledge</h2>
      </div>
      <div class="vw-tabs" role="tablist" aria-label="Knowledge views">
        <button type="button" role="tab" id="kn-tab-web" aria-controls="kn-panel-web" aria-selected="true" class="vw-tab" data-tab="web">Relationship web</button>
        <button type="button" role="tab" id="kn-tab-inv" aria-controls="kn-panel-inv" aria-selected="false" tabindex="-1" class="vw-tab" data-tab="inv">From your investigations <span class="kn-badge" hidden>0</span></button>
      </div>
      <button type="button" class="vw-close" data-act="close" aria-label="Close knowledge and return to the journey"><span aria-hidden="true">×</span></button>
    </header>

    <div class="kn-panel" id="kn-panel-web" role="tabpanel" aria-labelledby="kn-tab-web">
      <div class="kn-toolbar">
        <div class="vw-chips" role="group" aria-label="Show relationship types">
          ${Object.entries(CATS).map(([k, c]) => `<button type="button" class="chip vw-chip kn-cat cat-${k}" data-cat="${k}" aria-pressed="true"><span class="ln" aria-hidden="true"></span>${c.label}</button>`).join('')}
        </div>
        <div class="kn-tools">
          <p class="kn-stat" aria-live="polite"></p>
          <button type="button" class="btn btn-ghost vw-btn sm" data-act="listview">Text list</button>
          <button type="button" class="vw-iconbtn" data-act="recenter" aria-label="Re-centre the web">⟲</button>
        </div>
      </div>
      <div class="kn-stage">
        <div class="kn-chart" role="img" aria-label="Force-directed relationship graph. Use the Text list for an accessible version."></div>
        <div class="kn-list" hidden></div>
        <ul class="kn-legend" aria-label="Node colours by faction">
          ${FACTIONS.map(([k, label]) => `<li class="fac-${k}"><span class="dot" aria-hidden="true"></span>${label}</li>`).join('')}
          <li class="kn-legend-inv" hidden><span class="dot ring" aria-hidden="true"></span>Named in your investigations</li>
        </ul>
        <aside class="kn-card vw-sheet" hidden aria-labelledby="kn-card-title"></aside>
      </div>
    </div>

    <div class="kn-panel" id="kn-panel-inv" role="tabpanel" aria-labelledby="kn-tab-inv" hidden>
      <div class="kn-inv-scroll">
        <p class="kn-inv-intro">Everything here comes from this voyage's real requests to the knowledge graph and the model: the paths the graph walker actually took, the passages it retrieved, and the names that appear in the answers.</p>
        <div class="kn-inv-list"></div>
        <div class="kn-inv-log"></div>
      </div>
    </div>
    <p class="vh kn-live" role="status" aria-live="polite"></p>`;

  const chartEl = root.querySelector('.kn-chart');
  const listEl = root.querySelector('.kn-list');
  const statEl = root.querySelector('.kn-stat');
  const cardEl = root.querySelector('.kn-card');
  const invList = root.querySelector('.kn-inv-list');
  const invLog = root.querySelector('.kn-inv-log');
  const badge = root.querySelector('.kn-badge');
  const liveEl = root.querySelector('.kn-live'); // short announcements only — the list itself is not a live region

  /* ---------------- graph model */
  const enabled = new Set(Object.keys(CATS));
  const investigated = new Set(); // character ids named in real answers/context this session
  let chart = null, chartFailed = false, listMode = false, active = false, tab = 'web';
  let selectedNode = null, lastFocus = null;

  function filteredLinks() { return rels.filter((r) => enabled.has(TYPE_CAT[r.type])); }

  function buildOption() {
    const links = filteredLinks();
    const degree = new Map();
    for (const r of links) { degree.set(r.from, (degree.get(r.from) || 0) + 1); degree.set(r.to, (degree.get(r.to) || 0) + 1); }
    const ids = chars.map((c) => c.id).filter((id) => degree.has(id));
    const facIdx = Object.fromEntries(FACTIONS.map(([k], i) => [k, i]));
    const ivory = cssVar('--ivory', '#F3EAD7'), ivoryDim = cssVar('--ivory-dim', '#CFC4AE');
    const night = cssVar('--night', '#0B0E22'), gold = cssVar('--gold', '#C9A24B');
    const glass = cssVar('--glass-strong', 'rgba(10,12,30,.86)'), goldLine = cssVar('--gold-line', 'rgba(201,162,75,.38)');
    const serif = cssVar('--serif', 'Georgia, serif'), sans = cssVar('--sans', 'system-ui, sans-serif');
    statEl.textContent = `${ids.length} characters · ${links.length} threads`;

    // Name only the hubs (top ~12 by threads, at least 4) up front; every other name appears with its
    // neighbourhood on hover / focus, so the dense core stays readable.
    const degs = ids.map((id) => degree.get(id) || 1).sort((a, b) => b - a);
    const labelMin = Math.max(4, degs[Math.min(11, degs.length - 1)] || 1);
    const nodes = ids.map((id) => {
      const c = charById.get(id), d = degree.get(id) || 1, inv = investigated.has(id);
      return {
        id, name: c.name || id, value: d, category: facIdx[c.faction] ?? 2,
        symbolSize: Math.round(9 + Math.sqrt(d) * 6.5),
        itemStyle: inv ? { borderColor: ivory, borderWidth: 2.5, shadowBlur: 18, shadowColor: gold } : { borderColor: night, borderWidth: 1.2, shadowBlur: 8, shadowColor: 'rgba(0,0,0,.5)' },
        label: { show: d >= labelMin || inv, fontWeight: d >= 5 ? 600 : 400, fontSize: d >= 5 ? 14 : 12 },
      };
    });
    const pairCount = new Map();
    const edges = links.map((r) => {
      const cat = CATS[TYPE_CAT[r.type]];
      const key = [r.from, r.to].sort().join('|');
      const n = pairCount.get(key) || 0; pairCount.set(key, n + 1);
      return {
        source: r.from, target: r.to, rel: r,
        lineStyle: { color: cssVar(cat.token, cat.fb), type: cat.dash, width: r.type === 'rival' ? 1.6 : 1.2, opacity: 0.62, curveness: 0.08 + n * 0.14 },
      };
    });
    return {
      backgroundColor: 'transparent',
      animationDuration: reducedMotion() ? 0 : 900,
      animationDurationUpdate: reducedMotion() ? 0 : 500,
      tooltip: {
        trigger: 'item', confine: true, backgroundColor: glass, borderColor: goldLine, borderWidth: 1, padding: [8, 12],
        textStyle: { color: ivory, fontFamily: sans, fontSize: 13 }, extraCssText: 'border-radius:10px;box-shadow:0 12px 30px rgba(0,0,0,.4);max-width:300px;white-space:normal;',
        formatter: (p) => {
          if (p.dataType === 'edge') {
            const r = p.data.rel; const a = charById.get(r.from)?.name || r.from, b = charById.get(r.to)?.name || r.to;
            return `<b>${esc(a)} → ${esc(b)}</b><br>${esc(r.label || TYPE_LABEL[r.type] || r.type)} <span style="opacity:.6">(${esc(TYPE_LABEL[r.type] || r.type)})</span>${r.note ? `<br><i style="opacity:.8">${esc(r.note)}</i>` : ''}`;
          }
          const c = charById.get(p.data.id);
          return `<b style="font-family:${esc(serif)};font-size:15px">${esc(c?.name || p.name)}</b><br><span style="opacity:.8">${esc(c?.role || '')}</span><br><span style="opacity:.6">${p.data.value} thread${p.data.value === 1 ? '' : 's'} shown</span>`;
        },
      },
      series: [{
        type: 'graph', layout: 'force', roam: true, draggable: true, zoom: 0.92, scaleLimit: { min: 0.35, max: 4 },
        categories: FACTIONS.map(([, label, token, fb]) => ({ name: label, itemStyle: { color: cssVar(token, fb) } })),
        data: nodes, links: edges,
        label: { show: true, position: 'right', distance: 6, color: ivory, fontFamily: serif, textBorderColor: night, textBorderWidth: 3 },
        labelLayout: { hideOverlap: true },
        edgeSymbol: ['none', 'none'],
        force: { repulsion: [240, 620], edgeLength: [70, 130], gravity: 0.14, friction: reducedMotion() ? 0.9 : 0.6, layoutAnimation: !reducedMotion() },
        emphasis: { focus: 'adjacency', label: { show: true, color: ivory }, lineStyle: { width: 2.6, opacity: 1 } },
        blur: { itemStyle: { opacity: 0.18 }, lineStyle: { opacity: 0.06 }, label: { color: ivoryDim, opacity: 0.25 } },
      }],
    };
  }

  function renderChart() {
    if (listMode || tab !== 'web' || !active) return;
    if (!rels.length) {
      chartEl.innerHTML = `<p class="vw-empty">The relationship data has not loaded yet, so there is no web to draw.</p>`;
      statEl.textContent = '';
      return;
    }
    if (!window.echarts) {
      if (!chartFailed) { chartFailed = true; chartEl.innerHTML = `<p class="vw-empty">The graph library could not be loaded, so the web is shown as a list instead.</p>`; }
      setListMode(true);
      return;
    }
    try {
      if (!chart) {
        chart = window.echarts.init(chartEl, null, { renderer: 'canvas' });
        chart.on('click', (p) => { if (p.dataType === 'node') openCard(p.data.id, { focus: false }); });
        chart.getZr().on('click', (e) => { if (!e.target) closeCard(); });
      }
      chart.setOption(buildOption(), { notMerge: true });
      if (selectedNode) highlight(selectedNode);
    } catch (err) {
      console.warn('[knowledge] graph render failed', err);
      try { chart?.dispose(); } catch { /* already torn down */ } // free the canvas + listeners before replacing the DOM
      chart = null; chartFailed = true;
      chartEl.innerHTML = `<p class="vw-empty">The relationship web could not be drawn here. The text list below has the same threads.</p>`;
      setListMode(true);
    }
  }

  function highlight(id) {
    if (!chart) return;
    try {
      chart.dispatchAction({ type: 'downplay', seriesIndex: 0 });
      const data = chart.getOption()?.series?.[0]?.data || [];
      const idx = data.findIndex((d) => d.id === id);
      if (idx >= 0) chart.dispatchAction({ type: 'highlight', seriesIndex: 0, dataIndex: idx });
    } catch { /* highlighting is cosmetic */ }
  }

  function renderList() {
    const links = filteredLinks();
    statEl.textContent = `${new Set(links.flatMap((r) => [r.from, r.to])).size} characters · ${links.length} threads`;
    if (!links.length) { listEl.innerHTML = `<p class="vw-empty">${rels.length ? 'No threads match the selected types.' : 'The relationship data has not loaded yet.'}</p>`; return; }
    const groups = Object.entries(CATS).filter(([k]) => enabled.has(k)).map(([k, c]) => ({ k, c, items: links.filter((r) => TYPE_CAT[r.type] === k) })).filter((g) => g.items.length);
    listEl.innerHTML = groups.map(({ k, c, items }) => `
      <section class="kn-list-group cat-${k}"><h3 class="vw-h4"><span class="ln" aria-hidden="true"></span>${c.label} <span class="ct">${items.length}</span></h3>
        <ul>${items.map((r) => {
          const a = charById.get(r.from), b = charById.get(r.to);
          return `<li><button type="button" class="kn-li-name" data-act="node" data-id="${esc(r.from)}">${esc(a?.name || r.from)}</button>
            <span class="kn-li-rel">→ ${esc(r.label || TYPE_LABEL[r.type] || r.type)} →</span>
            <button type="button" class="kn-li-name" data-act="node" data-id="${esc(r.to)}">${esc(b?.name || r.to)}</button>
            ${r.note ? `<span class="kn-li-note">${esc(r.note)}</span>` : ''}</li>`;
        }).join('')}</ul></section>`).join('');
  }

  function setListMode(on) {
    listMode = !!on;
    const btn = root.querySelector('[data-act="listview"]');
    if (btn) {
      // the label names the view you switch TO, so no aria-pressed (a changing label + pressed state contradicts itself)
      btn.textContent = listMode ? 'Graph view' : 'Text list';
      btn.disabled = chartFailed;
    }
    root.querySelector('.kn-stage')?.classList.toggle('chart-failed', chartFailed);
    listEl.hidden = !listMode;
    chartEl.hidden = listMode && !chartFailed; // a failure message stays visible above the list
    if (listMode) renderList();
    else requestAnimationFrame(() => { renderChart(); chart?.resize(); });
  }

  /* ---------------- character card */
  function openCard(id, { focus = true } = {}) {
    const c = charById.get(id);
    if (!c) return;
    lastFocus = focus ? document.activeElement : null;
    selectedNode = id;
    const mine = rels.filter((r) => r.from === id || r.to === id);
    const byCat = Object.entries(CATS).map(([k, cat]) => [cat.label, mine.filter((r) => TYPE_CAT[r.type] === k).length]).filter(([, n]) => n);
    const fac = FACTIONS.find(([k]) => k === c.faction);
    const eps = Array.isArray(c.epithets) ? c.epithets.filter((x) => typeof x === 'string' && x.trim()) : [];
    cardEl.className = `kn-card vw-sheet fac-${c.faction || 'neutral'}`;
    cardEl.innerHTML = `
      <div class="vw-sheet-grip" aria-hidden="true"></div>
      <button type="button" class="vw-close sm" data-act="card-close" aria-label="Close character card"><span aria-hidden="true">×</span></button>
      <p class="vw-eyebrow"><span class="dot" aria-hidden="true"></span>${esc(fac ? fac[1] : c.faction || '')}${investigated.has(id) ? ' · named in your investigations' : ''}</p>
      <h3 class="kn-card-title" id="kn-card-title" tabindex="-1">${esc(c.name || id)}</h3>
      ${eps.length ? `<p class="kn-card-ep">${eps.slice(0, 3).map(esc).join(' · ')}</p>` : ''}
      ${c.role ? `<p class="kn-card-role">${esc(c.role)}</p>` : ''}
      ${c.summary ? `<p class="kn-card-sum">${esc(c.summary)}</p>` : ''}
      ${byCat.length ? `<p class="kn-card-threads">${byCat.map(([l, n]) => `<span>${esc(l)} <b>${n}</b></span>`).join('')}</p>` : ''}
      <div class="kn-card-actions">
        <button type="button" class="btn btn-gold vw-btn sm" data-act="profile" data-id="${esc(id)}">Full profile</button>
        <button type="button" class="btn btn-ghost vw-btn sm" data-act="ask" data-id="${esc(id)}">Ask about ${esc(c.name || id)}</button>
      </div>`;
    cardEl.hidden = false;
    highlight(id);
    if (focus) requestAnimationFrame(() => cardEl.querySelector('#kn-card-title')?.focus({ preventScroll: true }));
  }
  function closeCard({ silent = false } = {}) {
    if (cardEl.hidden) return;
    cardEl.hidden = true;
    selectedNode = null;
    try { chart?.dispatchAction({ type: 'downplay', seriesIndex: 0 }); } catch { /* cosmetic */ }
    if (!silent && lastFocus && root.contains(lastFocus)) lastFocus.focus({ preventScroll: true });
  }

  /* ---------------- tabs */
  function setTab(t, { focus = false } = {}) {
    tab = t;
    root.querySelectorAll('.vw-tab').forEach((b) => {
      const on = b.dataset.tab === t;
      b.setAttribute('aria-selected', on ? 'true' : 'false');
      b.tabIndex = on ? 0 : -1;
      if (on && focus) b.focus();
    });
    root.querySelector('#kn-panel-web').hidden = t !== 'web';
    root.querySelector('#kn-panel-inv').hidden = t !== 'inv';
    if (t === 'web') requestAnimationFrame(() => { if (listMode) renderList(); else { renderChart(); chart?.resize(); } });
    else renderInvestigations();
  }
  root.querySelector('.vw-tabs')?.addEventListener('keydown', (e) => {
    let next = null;
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') next = tab === 'web' ? 'inv' : 'web';
    else if (e.key === 'Home') next = 'web';
    else if (e.key === 'End') next = 'inv';
    if (!next) return;
    e.preventDefault(); e.stopPropagation();
    setTab(next, { focus: true });
  });

  /* ---------------- investigations (real research results only) */
  const sessions = []; // newest first: {key, questionId, query, at, elapsedMs, ok, error, paths, context, tools, entities, excerpt, retrievalNote}
  let matchers = null;
  function getMatchers() {
    if (matchers) return matchers;
    matchers = [];
    for (const n of V?.nameIndex || []) {
      if (!n || !n.id || !['character', 'location', 'event'].includes(n.type)) continue;
      const names = [...new Set((n.names || []).filter((s) => typeof s === 'string' && s.trim().length >= 3))];
      if (!names.length) continue;
      // Unicode-aware word boundaries so IAST names ("Bhīṣma", "Draupadī") match at their edges;
      // falls back to ASCII \b on engines without lookbehind / \p{} support.
      const alt = names.map(reEsc).join('|');
      let re = null;
      try { re = new RegExp(`(?<![\\p{L}\\p{N}])(?:${alt})(?![\\p{L}\\p{N}])`, 'iu'); } catch { /* older engine */ }
      if (!re) { try { re = new RegExp(`\\b(?:${alt})\\b`, 'i'); } catch { /* skip bad name */ } }
      if (re) matchers.push({ id: n.id, type: n.type, re });
    }
    return matchers;
  }
  function extract(data) {
    const retrieval = data?.retrieval || null;
    const trace = Array.isArray(retrieval?.trace) ? retrieval.trace : [];
    const leaves = trace.filter((t) => t && t.kind === 'leaf' && Array.isArray(t.path)).map((t) => t.path.join(' › '));
    const branches = trace.filter((t) => t && t.kind === 'branch' && Array.isArray(t.path) && t.path.length).length;
    const context = (Array.isArray(retrieval?.context) ? retrieval.context : []).filter((c) => c && c.path)
      .map((c) => ({ path: String(c.path).split(' > ').join(' › '), chars: c.chars, content: c.content || '' }));
    const results = Array.isArray(data?.results) ? data.results : [];
    const tools = results.filter((r) => r && r.tool).map((r) => ({ tool: r.tool, error: r.error || null }));
    const answer = results.filter((r) => r && !r.tool && typeof r.text === 'string').map((r) => r.text).join('\n\n');
    const text = `${answer}\n${context.map((c) => c.content).join('\n')}`;
    // also test an accent-stripped copy, so "Draupadī" in an answer meets the index's plain "draupadi"
    const plain = text.normalize('NFD').replace(/[̀-ͯ]/g, '');
    const entities = [];
    if (text.trim()) for (const m of getMatchers()) if (m.re.test(text) || (plain !== text && m.re.test(plain))) entities.push({ type: m.type, id: m.id });
    let retrievalNote = '';
    if (!data || typeof data !== 'object') retrievalNote = 'The response details were not available to this view.';
    else if (!retrieval) retrievalNote = 'No graph retrieval was returned for this question.';
    else if (retrieval.error) retrievalNote = `Graph retrieval reported an error: ${retrieval.error}`;
    const excerpt = answer.trim().replace(/\s+/g, ' ').slice(0, 260);
    return { paths: [...new Set(leaves)], branches, context, tools, entities, excerpt: excerpt + (answer.trim().length > 260 ? '…' : ''), retrievalNote };
  }

  function record(p) {
    if (!p || (p.state !== 'done' && p.state !== 'error')) return;
    const key = `${p.questionId || ''}|${p.query || ''}`;
    const i = sessions.findIndex((s) => s.key === key);
    if (i >= 0) sessions.splice(i, 1);
    const base = {
      key, questionId: p.questionId || null, query: p.query || '', at: Date.now(),
      elapsedMs: typeof p.elapsedMs === 'number' && p.elapsedMs > 0 ? p.elapsedMs : null,
      fromCache: !!p.fromCache, // a real response re-shown from this session's cache
    };
    if (p.state === 'error') {
      const err = p.error;
      const aborted = !!(err && typeof err === 'object' && err.aborted);
      const msg = (err && typeof err === 'object' ? err.message : err) || 'The request failed.';
      sessions.unshift({ ...base, ok: false, aborted, error: String(msg) });
    } else {
      const x = extract(p.data);
      // ok === false: the request finished but returned neither a usable answer nor a working chart
      sessions.unshift({ ...base, ok: true, empty: p.ok === false, ...x });
      for (const e of x.entities) if (e.type === 'character') investigated.add(e.id);
    }
    if (sessions.length > 40) sessions.length = 40; // keep the session log bounded
    badge.hidden = sessions.length === 0;
    badge.textContent = String(sessions.length);
    if (liveEl) liveEl.textContent = p.state === 'error' ? 'An investigation did not complete; it is listed under From your investigations.' : 'A new investigation was added to From your investigations.';
    root.querySelector('.kn-legend-inv').hidden = investigated.size === 0;
    if (active && tab === 'inv') renderInvestigations();
    if (active && tab === 'web' && chart && !listMode) renderChart();
  }

  const entityName = (e) => (e.type === 'character' ? charById.get(e.id)?.name : e.type === 'location' ? locById.get(e.id)?.name : eventById.get(e.id)?.title) || e.id;
  function questionInfo(qid) {
    const q = qid && V?.questionById?.get?.(qid);
    return q ? { text: q.question?.question || '', chapter: q.chapter || null } : null;
  }

  function renderInvestigations() {
    if (!sessions.length) {
      invList.innerHTML = `<div class="kn-inv-empty"><p class="vw-empty">No investigations yet in this voyage.</p>
        <p class="vw-muted">Ask a chapter's question — or anything, through "Ask the epic" — and the paths the knowledge graph actually walked will gather here.</p></div>`;
    } else {
      invList.innerHTML = sessions.map((s, i) => {
        const qi = questionInfo(s.questionId);
        const title = qi?.text || s.query;
        const meta = [qi?.chapter ? `${qi.chapter.title || qi.chapter.id}` : 'Asked freely', fmtTime(s.at),
          s.fromCache ? 'from this voyage' : (s.elapsedMs != null ? `${(s.elapsedMs / 1000).toFixed(1)} s` : null)].filter(Boolean).join(' · ');
        if (!s.ok) {
          return `<article class="kn-inv is-error"><p class="vw-eyebrow">${esc(meta)}</p><h3 class="kn-inv-q">${esc(title)}</h3>
            <p class="kn-inv-err">${s.aborted ? 'This investigation was withdrawn before it finished.' : `This investigation did not complete: ${esc(s.error)}`}</p>
            <button type="button" class="btn btn-ghost vw-btn sm" data-act="reask" data-i="${i}">Try again</button></article>`;
        }
        const shownPaths = s.paths.slice(0, 6), more = s.paths.slice(6);
        return `<article class="kn-inv">
          <p class="vw-eyebrow">${esc(meta)}</p>
          <h3 class="kn-inv-q">${esc(title)}</h3>
          ${qi && s.query && s.query !== title ? `<p class="kn-inv-sent"><span class="vw-label">Sent to the graph</span>${esc(s.query)}</p>` : ''}
          ${s.retrievalNote ? `<p class="vw-muted">${esc(s.retrievalNote)}</p>` : ''}
          ${s.empty ? `<p class="vw-muted">The request finished, but no written answer or working chart came back.</p>` : ''}
          ${s.paths.length ? `<div class="kn-inv-block"><p class="vw-label">Graph paths walked <span class="ct">${s.paths.length}${s.branches ? ` · ${s.branches} branch${s.branches === 1 ? '' : 'es'} considered` : ''}</span></p>
              <ol class="kn-paths">${shownPaths.map((p) => `<li>${esc(p)}</li>`).join('')}</ol>
              ${more.length ? `<details><summary>${more.length} more path${more.length === 1 ? '' : 's'}</summary><ol class="kn-paths">${more.map((p) => `<li>${esc(p)}</li>`).join('')}</ol></details>` : ''}</div>` : ''}
          ${s.context.length ? `<div class="kn-inv-block"><p class="vw-label">Retrieved from the curated knowledge graph <span class="ct">${s.context.length}</span></p>
              <ul class="kn-ctx">${s.context.map((c) => `<li><span>${esc(c.path)}</span>${typeof c.chars === 'number' ? `<span class="ct">${c.chars.toLocaleString()} chars</span>` : ''}</li>`).join('')}</ul></div>` : ''}
          ${s.tools.length ? `<div class="kn-inv-block"><p class="vw-label">Visualisations returned</p><div class="vw-chips">${s.tools.map((t) => `<span class="chip vw-chip static${t.error ? ' is-error' : ''}" title="${esc(t.error || '')}">${esc(t.tool)}${t.error ? ' — failed' : ''}</span>`).join('')}</div></div>` : ''}
          ${s.excerpt ? `<blockquote class="kn-inv-ans"><span class="vw-label">From the answer</span>${esc(s.excerpt)}</blockquote>` : ''}
          ${s.entities.length ? `<div class="kn-inv-block"><p class="vw-label">Named in the answer and retrieved passages</p><div class="vw-chips">${s.entities.map((e) => `<button type="button" class="chip vw-chip ent-${e.type}" data-act="entity" data-type="${e.type}" data-id="${esc(e.id)}">${esc(entityName(e))}</button>`).join('')}</div></div>` : ''}
          <button type="button" class="btn btn-ghost vw-btn sm" data-act="reask" data-i="${i}">Open this investigation again</button>
        </article>`;
      }).join('');
    }
    // the store's research log (question, time, ok — never the answer) for earlier visits on this device
    const log = st().research && typeof st().research === 'object' ? Object.entries(st().research) : [];
    const sessionKeys = new Set(sessions.map((s) => s.questionId).filter(Boolean));
    const sessionQueries = new Set(sessions.map((s) => s.query));
    const earlier = log.filter(([qid, r]) => r && !sessionKeys.has(qid) && !sessionQueries.has(r.query))
      .sort((a, b) => (new Date(b[1].at) - new Date(a[1].at)) || 0).slice(0, 30);
    invLog.innerHTML = earlier.length ? `<h3 class="vw-h4">Earlier on this device</h3>
      <p class="vw-muted">Only the question and time are kept between visits — answers are not stored, so asking again runs a fresh, real search.</p>
      <ul class="kn-log">${earlier.map(([qid, r]) => {
        const qi = questionInfo(qid);
        return `<li class="${r.ok === false ? 'is-error' : ''}"><span class="q">${esc(qi?.text || r.query || qid)}</span><span class="t">${esc(fmtTime(r.at))}${r.ok === false ? ' · did not complete' : ''}</span>
          <button type="button" class="btn btn-ghost vw-btn sm" data-act="relog" data-qid="${esc(qid)}">Ask again</button></li>`;
      }).join('')}</ul>` : '';
  }

  /* ---------------- actions */
  const toJourney = () => { closeCard({ silent: true }); bus?.emit?.('view:change', { view: 'journey' }); ensureBodyView('journey'); };
  function askAgain(questionId, query) {
    const qi = questionId && V?.questionById?.get?.(questionId);
    toJourney();
    if (qi) bus?.emit?.('question:select', { questionId, chapterId: qi.chapter?.id, query: query || qi.question?.search_query });
    else if (query) bus?.emit?.('question:select', { query });
  }

  root.addEventListener('click', (e) => {
    const b = e.target.closest?.('[data-act], [data-tab]');
    if (!b || !root.contains(b)) return;
    if (b.dataset.tab) { setTab(b.dataset.tab); return; }
    const act = b.dataset.act;
    if (act === 'close') toJourney();
    else if (act === 'listview') setListMode(!listMode);
    else if (act === 'recenter') { if (chart && !listMode) renderChart(); } // notMerge re-layout resets zoom/pan
    else if (act === 'card-close') closeCard();
    else if (act === 'node') openCard(b.dataset.id);
    else if (act === 'profile') { const id = b.dataset.id; closeCard({ silent: true }); bus?.emit?.('entity:open', { type: 'character', id }); }
    else if (act === 'ask') {
      const c = charById.get(b.dataset.id); if (!c) return;
      toJourney();
      bus?.emit?.('question:select', { query: `Who is ${c.name} in the Mahabharata, what choices define them, and how do those choices shape the epic?` });
    }
    else if (act === 'entity') bus?.emit?.('entity:open', { type: b.dataset.type, id: b.dataset.id });
    else if (act === 'reask') { const s = sessions[+b.dataset.i]; if (s) askAgain(s.questionId, s.query); }
    else if (act === 'relog') { const r = st().research?.[b.dataset.qid]; askAgain(b.dataset.qid, r?.query); }
  });
  root.querySelector('.kn-toolbar [role="group"]')?.addEventListener('click', (e) => {
    const b = e.target.closest('.kn-cat');
    if (!b) return;
    const k = b.dataset.cat;
    if (enabled.has(k) && enabled.size === 1) return; // keep at least one type visible
    if (enabled.has(k)) enabled.delete(k); else enabled.add(k);
    b.setAttribute('aria-pressed', enabled.has(k) ? 'true' : 'false');
    if (listMode) renderList(); else renderChart();
  });
  root.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    e.stopPropagation(); e.preventDefault();
    if (!cardEl.hidden) closeCard(); else toJourney();
  });

  /* ---------------- lifecycle */
  function onShow() {
    active = true;
    if (tab === 'web') { if (listMode) renderList(); else { renderChart(); chart?.resize(); } }
    else renderInvestigations();
  }
  function onHide() { active = false; }
  let showPending = false;
  function sync() {
    const now = document.body.classList.contains('view-knowledge');
    if (now && !active && !showPending) {
      showPending = true; // several class mutations in one frame must not queue several onShow calls
      requestAnimationFrame(() => { showPending = false; if (!active && document.body.classList.contains('view-knowledge')) onShow(); });
    } else if (!now && active) onHide();
  }
  new MutationObserver(sync).observe(document.body, { attributes: true, attributeFilter: ['class'] });
  sync();
  if (typeof ResizeObserver === 'function') new ResizeObserver(() => { if (active && chart) chart.resize(); }).observe(chartEl);
  window.addEventListener('resize', () => { if (active && chart) chart.resize(); });

  bus?.on?.('research:state', record);
  bus?.on?.('ui:escape', (evt) => { if (active && !cardEl.hidden) { closeCard(); if (evt) evt.handled = true; } });
  bus?.on?.('view:change', (p) => { if (p?.view) lastView = p.view; if (p?.view === 'knowledge') ensureBodyView('knowledge'); });
  bus?.on?.('store:change', () => { if (active && tab === 'inv') renderInvestigations(); });
  // Only a motion-preference change affects the graph; volume/quality changes must not re-run the force layout.
  // Read on the next tick so main.js has already mirrored the setting onto <body>.
  let lastRM = reducedMotion();
  bus?.on?.('settings:change', () => setTimeout(() => {
    const rm = reducedMotion();
    if (rm === lastRM) return;
    lastRM = rm;
    if (active && chart && !listMode) renderChart();
  }, 0));
  // internal (additive) event: Characters → "Show in the relationship web"
  bus?.on?.('knowledge:focus', (p) => {
    if (!p?.id || !charById.has(p.id)) return;
    setTab('web');
    setTimeout(() => openCard(p.id, { focus: true }), 120);
  });
}
