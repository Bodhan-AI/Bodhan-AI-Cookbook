/* Knowledge-graph traversal panel: shows how the Graph RAG walked helios_group.json before the charts were made.
 * The traversal is replayed step by step on a timer (deliberately paced, even when retrieval was fast). */
(function (global) {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const ROOT = 'Helios Dynamics Group';
  const WIDE = matchMedia('(min-width: 1180px)');

  const panel = $('#gpanel');
  const tree = $('.gp-tree', panel), ctxBox = $('.gp-ctx', panel), ctxHead = $('.gp-ctx-head', panel);
  const statusEl = $('.gp-status-text', panel), timeEl = $('[data-stat="time"]', panel);
  const nodesEl = $('[data-stat="nodes"]', panel), ctxEl = $('[data-stat="ctx"]', panel);
  let run = 0, clockRaf = 0, clockStart = 0, autoOpened = false;

  // ---------------------------------------------------------------- open / close
  function open(auto) {
    if (!panel.classList.contains('open')) autoOpened = !!auto;
    panel.classList.add('open');
    document.body.classList.toggle('gp-open', WIDE.matches);
    $('#btn-graph')?.classList.add('active');
    try { localStorage.setItem('jarvis.gpanel', '1'); } catch (e) { /* ignore */ }
  }
  function close() {
    panel.classList.remove('open');
    document.body.classList.remove('gp-open');
    $('#btn-graph')?.classList.remove('active');
    try { localStorage.setItem('jarvis.gpanel', '0'); } catch (e) { /* ignore */ }
  }
  const toggle = () => (panel.classList.contains('open') ? close() : open());
  WIDE.addEventListener('change', () => document.body.classList.toggle('gp-open', WIDE.matches && panel.classList.contains('open')));

  // ---------------------------------------------------------------- clock + stats
  const fmtTime = ms => { const s = ms / 1000; return `${String(Math.floor(s / 60)).padStart(2, '0')}:${(s % 60).toFixed(1).padStart(4, '0')}`; };
  function startClock() {
    stopClock(); clockStart = performance.now();
    const tick = () => { timeEl.textContent = fmtTime(performance.now() - clockStart); clockRaf = requestAnimationFrame(tick); };
    tick();
  }
  function stopClock() { cancelAnimationFrame(clockRaf); clockRaf = 0; }
  function setStats(nodes, ctx) { nodesEl.textContent = nodes; ctxEl.textContent = ctx; }
  function setStatus(text, kind = '') { statusEl.textContent = text; panel.dataset.state = kind; }

  // ---------------------------------------------------------------- rows
  const nameOf = path => (path.length ? path[path.length - 1] : ROOT);
  function row(step, cls = '') {
    const kind = step.kind || 'branch';
    const el = document.createElement('div');
    el.className = `gp-row ${kind} ${cls}`;
    el.style.setProperty('--d', step.depth || 0);
    el.innerHTML = `<span class="gp-node"></span><div class="gp-main">
      <div class="gp-name" title="${esc(nameOf(step.path || []))}">${esc(nameOf(step.path || []))}</div>
      <div class="gp-meta"></div><div class="gp-bar"><i></i></div><div class="gp-chips"></div></div>`;
    tree.appendChild(el);
    requestAnimationFrame(() => el.classList.add('in'));
    tree.scrollTo({ top: tree.scrollHeight, behavior: 'smooth' });
    return el;
  }
  function scanning(el, step, ms) {
    el.classList.add('scanning');
    el.style.setProperty('--scan', `${ms}ms`);
    const meta = $('.gp-meta', el);
    meta.textContent = step.kind === 'leaf' ? 'reading node…'
      : step.kind === 'limit' ? 'checking limits…'
        : step.options ? `scanning ${step.options} branch${step.options > 1 ? 'es' : ''}…` : 'scanning…';
  }
  function settle(el, step, contextByPath) {
    el.classList.remove('scanning'); el.classList.add('done');
    const meta = $('.gp-meta', el), chips = $('.gp-chips', el);
    if (step.kind === 'branch') {
      const n = (step.selected || []).length;
      meta.textContent = n ? `${step.options} scanned · ${n} selected` : `${step.options} scanned · nothing relevant`;
      if (!n) el.classList.add('dead');
      chips.innerHTML = (step.selected || []).map(k => `<span class="gp-chip" title="${esc(k)}">${esc(k)}</span>`).join('');
    } else if (step.kind === 'leaf') {
      meta.textContent = `${(step.chars || 0).toLocaleString()} characters retrieved`;
      const key = (step.path || []).join(' > ');
      const c = contextByPath.get(key);
      if (c) addContext(c);
    } else {
      meta.textContent = 'depth / node budget reached';
      el.classList.add('dead');
    }
  }
  function addContext(c) {
    ctxHead.hidden = false;
    const parts = c.path.split(' > ');
    const card = document.createElement('div');
    card.className = 'gp-card';
    card.innerHTML = `<div class="gp-card-path">${esc(parts.slice(0, -1).join(' › ') || ROOT)}</div>
      <div class="gp-card-title">${esc(parts[parts.length - 1])}</div>
      <div class="gp-card-body">${esc(c.content)}${c.chars > c.content.length ? '…' : ''}</div>`;
    card.addEventListener('click', () => card.classList.toggle('expanded'));
    ctxBox.appendChild(card);
    requestAnimationFrame(() => card.classList.add('in'));
  }

  // ---------------------------------------------------------------- public API
  function reset() {
    run++; stopClock();
    tree.innerHTML = ''; ctxBox.innerHTML = ''; ctxHead.hidden = true;
    timeEl.textContent = '00:00.0'; setStats(0, 0);
    setStatus('Idle — ask a question to see the traversal', 'idle');
    panel.querySelector('.gp-empty').hidden = false;
  }

  /** Request sent: show the root being scanned and start the clock. */
  function begin(query) {
    reset();
    const id = run;
    panel.querySelector('.gp-empty').hidden = true;
    $('.gp-query', panel).textContent = query;
    open(true);
    startClock();
    setStatus('Navigating knowledge graph…', 'busy');
    const root = row({ kind: 'branch', path: [], depth: 0 }, 'root');
    scanning(root, { kind: 'branch' }, 1600);
    root.classList.add('waiting');
    return id;
  }

  /** Response arrived: replay the recorded traversal on a timer. Resolves when the replay is finished. */
  async function play(retrieval, opts = {}) {
    const id = run;
    const steps = (retrieval && retrieval.trace) || [];
    const contextByPath = new Map(((retrieval && retrieval.context) || []).map(c => [c.path, c]));
    if (retrieval && retrieval.error) { fail(`Retrieval failed: ${retrieval.error}`); return; }
    setStatus('Replaying graph traversal…', 'busy');
    // pacing: ~0.5s per node, never faster than 2.4s total, never slower than ~8s
    const per = opts.fast ? 140 : Math.max(260, Math.min(650, Math.max(2400, Math.min(8000, steps.length * 520)) / Math.max(1, steps.length)));
    let root = tree.querySelector('.gp-row.root');
    let nodes = 0, ctx = 0;
    for (let i = 0; i < steps.length; i++) {
      if (id !== run) return;            // reset / new query started
      const step = steps[i];
      let el;
      if (i === 0 && root && (step.path || []).length === 0) { el = root; root.classList.remove('waiting'); }
      else el = row(step);
      scanning(el, step, per * 0.72);
      await sleep(per * 0.72);
      if (id !== run) return;
      settle(el, step, contextByPath);
      if (step.kind !== 'limit') nodes++;
      if (step.kind === 'leaf') ctx++;
      setStats(nodes, ctx);
      await sleep(per * 0.28);
    }
    if (id !== run) return;
    if (!steps.length && root) { root.classList.remove('waiting', 'scanning'); root.classList.add('done', 'dead'); $('.gp-meta', root).textContent = 'no traversal recorded'; }
    stopClock();
    const secs = retrieval && retrieval.elapsed_ms ? ` · retrieval ${(retrieval.elapsed_ms / 1000).toFixed(1)}s` : '';
    setStatus(ctx ? `Retrieved ${ctx} context node${ctx > 1 ? 's' : ''}${secs}` : `No relevant context — answering from model knowledge${secs}`, 'done');
    if (autoOpened && !WIDE.matches) { await sleep(500); close(); }
  }

  function fail(msg) {
    stopClock();
    const root = tree.querySelector('.gp-row.root');
    if (root) { root.classList.remove('waiting', 'scanning'); root.classList.add('done', 'dead'); }
    setStatus(msg || 'Request failed', 'error');
  }

  /** Re-show a previous turn's traversal (quick replay). */
  function show(retrieval, query) {
    begin(query);
    return play(retrieval, { fast: true });
  }

  $('#gp-close').addEventListener('click', close);
  $('#btn-graph').addEventListener('click', toggle);
  reset();
  let pref = null;
  try { pref = localStorage.getItem('jarvis.gpanel'); } catch (e) { /* ignore */ }
  if (WIDE.matches && pref !== '0') open();

  global.GraphPanel = { begin, play, fail, show, reset, open, close, toggle };
})(window);
