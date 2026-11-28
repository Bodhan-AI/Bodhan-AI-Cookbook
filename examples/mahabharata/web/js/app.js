/* JARVIS UI controller: composer, conversation feed, chart cards, morphing, gallery, history, theme. */
(function () {
  'use strict';

  const C = window.JarvisCharts, K = window.JarvisCatalog, FX = window.JarvisFX;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const el = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; };
  const store = {
    get(k, d) { try { const v = localStorage.getItem('jarvis.' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('jarvis.' + k, JSON.stringify(v)); } catch (e) { /* storage unavailable */ } },
  };
  const ICON = {
    replay: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>',
    table: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M9 4v16"/></svg>',
    code: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m8 7-5 5 5 5M16 7l5 5-5 5M14 4l-4 16"/></svg>',
    png: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12M7 10l5 5 5-5M4 21h16"/></svg>',
    expand: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>',
    chev: '<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>',
    warn: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2 21h20z"/><path d="M12 10v5M12 18h.01"/></svg>',
  };

  const state = {
    theme: 'light',
    entries: [],          // live chart cards
    busy: false,
    model: null,
    history: store.get('history', []),
    lastQuery: null,
    session: 0,           // bumped by 'new session' so late responses are dropped
  };

  // ================================================================== theme
  let bg;
  function setTheme(t) {
    state.theme = t;
    document.documentElement.dataset.theme = t;
    store.set('theme', t);
    if (bg) bg.recolor();
    state.entries.forEach(e => { if (e.chart) draw(e, { fresh: true }); });
    if (modal.entry) openModal(modal.entry);
  }

  // ================================================================== toasts
  function toast(msg, kind = '') {
    const t = el(`<div class="toast ${kind}">${esc(msg)}</div>`);
    $('#toasts').appendChild(t);
    setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 300); }, 3200);
  }

  // ================================================================== hero
  function greeting() {
    const h = new Date().getHours();
    const part = h < 5 ? 'Working late' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
    return `${part}. What shall we visualize?`;
  }
  function typeInto(node, text, speed = 32) {
    node.textContent = '';
    let i = 0;
    const tick = () => { node.textContent = text.slice(0, ++i); if (i < text.length) setTimeout(tick, speed + Math.random() * 30); };
    tick();
  }
  function renderSuggestions() {
    const box = $('#suggestions');
    box.innerHTML = '';
    K.SUGGESTIONS.forEach((s, i) => {
      const b = el(`<button class="sugg" style="animation-delay:${0.25 + i * 0.07}s">
        <span class="s-tool">${esc(K.META[s.tool].label)}</span>${K.icon(s.tool)}<span class="s-text">${esc(s.text)}</span></button>`);
      b.addEventListener('click', () => ask(s.text));
      spotlight(b);
      box.appendChild(b);
    });
  }
  function hideHero() {
    const hero = $('#hero');
    if (!hero || hero.hidden) return;
    hero.classList.add('leaving');
    setTimeout(() => { hero.hidden = true; hero.classList.remove('leaving'); }, 420);
  }

  // ================================================================== cursor spotlight
  function spotlight(node) {
    node.addEventListener('pointermove', e => {
      const r = node.getBoundingClientRect();
      node.style.setProperty('--mx', `${e.clientX - r.left}px`);
      node.style.setProperty('--my', `${e.clientY - r.top}px`);
    });
  }

  // ================================================================== composer
  const q = $('#q');
  function autosize() { q.style.height = 'auto'; q.style.height = Math.min(180, q.scrollHeight) + 'px'; }
  function setBusy(b) {
    state.busy = b;
    $('#composer').classList.toggle('busy', b);
    $('#btn-send').disabled = b;
  }

  // ================================================================== thinking indicator
  const STEPS = ['Navigating knowledge graph', 'Retrieving context', 'Selecting chart tools & data', 'Rendering visualization'];
  function thinking(container) {
    const box = el(`<div class="thinking"><ul class="steps">${STEPS.map(s => `<li><span class="st"></span>${s}</li>`).join('')}</ul></div>`);
    container.appendChild(box);
    const lis = $$('li', box);
    let i = 0;
    lis[0].classList.add('active');
    const timer = setInterval(() => {
      if (i < lis.length - 2) { lis[i].classList.replace('active', 'done'); lis[++i].classList.add('active'); }
    }, 900);
    return {
      finish() {
        clearInterval(timer);
        return new Promise(res => {
          let k = 0;
          const step = () => {
            if (k >= lis.length) { setTimeout(() => { box.style.transition = 'opacity .35s, transform .35s'; box.style.opacity = '0'; box.style.transform = 'translateY(-6px)'; setTimeout(() => { box.remove(); res(); }, 350); }, 160); return; }
            lis[k].classList.remove('active'); lis[k].classList.add('done');
            if (lis[k + 1] && !lis[k + 1].classList.contains('done')) lis[k + 1].classList.add('active');
            k++; setTimeout(step, 110);
          };
          step();
        });
      },
      fail() { clearInterval(timer); box.remove(); },
    };
  }

  // ================================================================== turns
  function newTurn(userHtml) {
    hideHero();
    const turn = el(`<section class="turn">
      <div class="msg-user">${userHtml}</div>
      <div class="jarvis">
        <div class="jarvis-head"><span class="reactor reactor-md busy" data-reactor></span><span class="who">JARVIS</span><span class="status">thinking…</span></div>
        <div class="jarvis-body"></div>
      </div></section>`);
    $('#feed').appendChild(turn);
    FX.mountReactors(turn);
    requestAnimationFrame(() => turn.scrollIntoView({ behavior: 'smooth', block: 'start' }));
    return {
      turn, body: $('.jarvis-body', turn), status: $('.status', turn), reactor: $('.reactor', turn),
    };
  }

  async function post(url, body) {
    const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
    return data;
  }

  async function ask(query) {
    query = String(query || '').trim();
    if (!query) return;
    if (state.busy) { toast('Still working on the previous request…'); return; }
    const sid = state.session;
    state.lastQuery = query;
    addHistory(query);
    q.value = ''; autosize();
    setBusy(true);
    const t = newTurn(esc(query));
    const th = thinking(t.body);
    GraphPanel.begin(query);
    try {
      const data = await post('/api/chart', { query });
      if (sid !== state.session) return;
      await GraphPanel.play(data.retrieval);   // paced replay of the graph traversal
      if (sid !== state.session) return;
      await th.finish();
      if (sid !== state.session) return;
      showResults(t, data, () => ask(query));
    } catch (err) {
      th.fail();
      GraphPanel.fail(`Request failed: ${err.message || err}`);
      if (sid === state.session) fail(t, err, () => ask(query));
    } finally {
      setBusy(false);
    }
  }

  async function demo(tool) {
    if (!K.DEMOS[tool]) return;
    if (state.busy) { toast('Still working on the previous request…'); return; }
    const sid = state.session;
    closeDrawer();
    setBusy(true);
    const t = newTurn(`<span class="demo-tag">Gallery demo</span>${esc(K.META[tool].label)} chart`);
    const th = thinking(t.body);
    try {
      const data = await post('/api/render', { tool, args: K.DEMOS[tool] });
      if (sid !== state.session) return;
      await th.finish();
      if (sid !== state.session) return;
      showResults(t, data, () => demo(tool), true);
    } catch (err) {
      th.fail();
      if (sid === state.session) fail(t, err, () => demo(tool));
    } finally {
      setBusy(false);
    }
  }

  function fail(t, err, retry) {
    t.reactor.classList.remove('busy'); t.reactor.classList.add('error');
    t.status.innerHTML = '<span class="chip err">request failed</span>';
    const box = el(`<div class="error-box"><b>I couldn't complete that.</b><br><code>${esc(err.message || err)}</code><br><button class="retry-btn">Retry</button></div>`);
    $('.retry-btn', box).addEventListener('click', () => retry());
    t.body.appendChild(box);
  }

  function showResults(t, data, retry, isDemo) {
    const results = (data && data.results) || [];
    const charts = results.filter(r => r.tool);
    const bad = charts.filter(r => r.error).length;
    t.reactor.classList.remove('busy'); t.reactor.classList.add(bad === charts.length && bad ? 'error' : 'done');
    const secs = data.elapsed_ms ? `${(data.elapsed_ms / 1000).toFixed(2)}s` : null;
    const chips = [];
    const good = charts.length - bad;
    if (good) chips.push(`<span class="chip ok">${good} chart${good > 1 ? 's' : ''}</span>`);
    if (bad) chips.push(`<span class="chip err">${bad} failed</span>`);
    if (secs) chips.push(`<span class="chip">${secs}</span>`);
    if (isDemo) chips.push('<span class="chip">no LLM call</span>');
    else if (data.model) chips.push(`<span class="chip">${esc(data.model)}</span>`);
    const rv = data.retrieval;
    if (rv) {
      const leaves = (rv.trace || []).filter(s => s.kind === 'leaf').length;
      chips.push(`<button class="chip link" data-graph title="Replay the graph traversal">graph · ${(rv.trace || []).filter(s => s.kind !== 'limit').length} nodes · ${leaves} contexts</button>`);
    }
    t.status.innerHTML = chips.join(' ');
    const gb = t.status.querySelector('[data-graph]');
    if (gb) gb.addEventListener('click', () => GraphPanel.show(rv, data.query || ''));

    const texts = results.filter(r => !r.tool && r.text);
    texts.forEach(r => t.body.appendChild(el(`<div class="reply-text">${esc(r.text)}</div>`)));
    if (!charts.length) {
      if (!texts.length) t.body.appendChild(el('<div class="reply-text">No chart could be chosen for that request.</div>'));
      return;
    }
    const grid = el(`<div class="cards ${charts.length > 1 && charts.length !== 3 ? 'two' : ''}"></div>`);
    t.body.appendChild(grid);
    charts.forEach((r, i) => {
      const card = r.error ? errorCard(r) : chartCard(r, isDemo ? null : data);
      grid.appendChild(card);
      setTimeout(() => card.classList.add('in'), 120 + i * 180);
    });
    setTimeout(() => grid.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 250);
  }

  // ================================================================== cards
  const titleOf = (tool, args) => (args && args.title) || C.DEFAULT_TITLES[tool] || tool;

  function errorCard(r) {
    const card = el(`<article class="card error-card">
      <div class="card-head"><div class="card-icon">${ICON.warn}</div>
        <div class="card-titles"><h3>${esc(titleOf(r.tool, r.args))}</h3>
        <div class="card-meta"><span class="badge warn">${esc(r.tool)}</span><span>the chart tool rejected these arguments</span></div></div></div>
      <div class="card-body"><div class="error-box" style="max-width:none"><code>${esc(r.error)}</code></div>
      <div class="view-pane pane-code" style="display:block;height:auto;max-height:320px;margin-top:12px"><pre class="code">${highlight(JSON.stringify(r.args, null, 2))}</pre></div></div>
    </article>`);
    spotlight(card);
    return card;
  }

  function chartCard(r, data) {
    const entry = {
      origTool: r.tool, origArgs: r.args || {}, tool: r.tool, args: r.args || {}, figure: r.figure,
      variant: null, chart: null, mode: null, option: null,
    };
    const meta = K.META[r.tool] || { label: r.tool };
    const variants = C.variants(r.tool, entry.origArgs);
    entry.origVariant = originalVariant(r.tool, entry.origArgs, variants);
    if (variants.length && !variants.includes(entry.origVariant)) variants.unshift(entry.origVariant);
    entry.variants = variants;
    const card = el(`<article class="card" data-view="chart">
      <div class="card-head">
        <div class="card-icon">${K.icon(r.tool)}</div>
        <div class="card-titles"><h3>${esc(titleOf(r.tool, entry.args))}</h3>
          <div class="card-meta"><span class="badge" data-role="badge">${esc(r.tool)}</span><span>${C.countValues(entry.args).toLocaleString()} values</span><span data-role="mode"></span></div></div>
        <div class="card-actions">
          ${variants.length > 1 ? `<div class="switcher"><button class="switch-btn" title="Morph into another chart type">${K.icon(r.tool)}<span>${esc(meta.label)}</span>${ICON.chev}</button><div class="menu" role="menu"></div></div>` : ''}
          <button class="icon-btn" data-act="replay" title="Replay animation">${ICON.replay}</button>
          <button class="icon-btn" data-act="table" title="Data table">${ICON.table}</button>
          <button class="icon-btn" data-act="code" title="Tool call (JSON)">${ICON.code}</button>
          <button class="icon-btn" data-act="png" title="Download PNG">${ICON.png}</button>
          <button class="icon-btn" data-act="expand" title="Full screen">${ICON.expand}</button>
        </div>
      </div>
      <div class="card-body">
        <div class="chart"></div>
        <div class="view-pane pane-table"></div>
        <div class="view-pane pane-code"><button class="copy-btn">Copy</button><pre class="code"></pre></div>
      </div>
      <div class="card-foot"><span class="hint">${hintFor(r.tool)}</span><span>${data && data.elapsed_ms ? `generated by ${esc(data.model)}` : 'rendered locally'}</span></div>
    </article>`);
    entry.card = card;
    entry.chartEl = $('.chart', card);
    spotlight(card);

    // switcher
    if (variants.length > 1) {
      const sw = $('.switcher', card), menu = $('.menu', sw);
      const fill = () => {
        const cur = entry.variant || null;
        menu.innerHTML = `<div class="menu-label">Morph into</div>` + variants.map(v => {
          const tool = v.split(':')[0];
          const isCur = v === (cur || entry.origVariant);
          return `<button data-v="${v}" class="${isCur ? 'current' : ''}">${K.icon(tool)}${esc(C.VARIANT_LABELS[v] || v)}</button>`;
        }).join('');
      };
      $('.switch-btn', sw).addEventListener('click', e => { e.stopPropagation(); fill(); closeMenus(sw); sw.classList.toggle('open'); });
      menu.addEventListener('click', e => {
        const b = e.target.closest('button[data-v]');
        if (!b) return;
        sw.classList.remove('open');
        morph(entry, b.dataset.v);
      });
    }

    card.addEventListener('click', e => {
      const b = e.target.closest('[data-act]');
      if (!b) return;
      const act = b.dataset.act;
      if (act === 'replay') replay(entry);
      if (act === 'table' || act === 'code') toggleView(entry, act, b);
      if (act === 'png') downloadPNG(entry);
      if (act === 'expand') openModal(entry);
    });
    $('.copy-btn', card).addEventListener('click', () => {
      navigator.clipboard?.writeText(JSON.stringify({ tool: entry.tool, args: entry.args }, null, 2)).then(() => toast('Tool call copied'), () => toast('Copy failed', 'err'));
    });

    state.entries.push(entry);
    lazyMount(entry);
    return card;
  }

  function originalVariant(tool, a, variants) {
    const v = tool === 'bar' ? (a.horizontal ? 'bar:h' : (a.mode === 'stack' || a.mode === 'relative') ? 'bar:stack' : 'bar')
      : tool === 'area' ? (a.stacked ? 'area:stack' : 'area')
        : tool === 'line' ? (a.smooth ? 'line:smooth' : 'line') : tool;
    return variants.includes(v) ? v : tool;
  }

  function hintFor(tool) {
    const h = {
      treemap: 'Click a block to zoom in · breadcrumb to go back',
      sunburst: 'Click a ring to drill down · center to go back',
      sankey: 'Hover a node to trace its flows · drag nodes to rearrange',
      candlestick: 'Drag to pan · Ctrl + scroll to zoom',
      line: 'Click legend items to toggle series · Ctrl + scroll to zoom',
      area: 'Click legend items to toggle series · Ctrl + scroll to zoom',
      band: 'Hover for value and range · Ctrl + scroll to zoom',
      gantt: 'Drag to pan the timeline · Ctrl + scroll to zoom',
      heatmap: 'Drag the color-scale handles to filter cells',
      correlation: 'Drag the color-scale handles to filter by strength',
      scatter: 'Hover points for details',
      bubble: 'Hover bubbles for details',
    };
    return h[tool] || 'Hover for details · use the toolbar to morph, inspect or export';
  }

  // lazy init so the entrance animation plays when the card scrolls into view
  const io = new IntersectionObserver(entries => {
    entries.forEach(en => {
      if (!en.isIntersecting) return;
      const entry = state.entries.find(x => x.chartEl === en.target);
      io.unobserve(en.target);
      if (entry && !entry.chart && entry.mode !== 'plotly') mount(entry);
    });
  }, { threshold: 0.2 });
  const EAGER = new URLSearchParams(location.search).has('eager');
  function lazyMount(entry) { if (EAGER) requestAnimationFrame(() => mount(entry)); else io.observe(entry.chartEl); }

  function mount(entry) {
    entry.chart = echarts.init(entry.chartEl, null, { renderer: 'canvas' });
    draw(entry);
    entry.ro = new ResizeObserver(() => entry.chart && entry.chart.resize());
    entry.ro.observe(entry.chartEl);
  }

  function draw(entry, opts = {}) {
    if (entry.mode === 'plotly') return renderPlotly(entry);
    try {
      const option = C.build(entry.tool, entry.args, Object.assign({}, C.theme(state.theme), { width: entry.chartEl.clientWidth || 800 }));
      entry.option = option;
      if (opts.fresh) entry.chart.clear();
      entry.chart.setOption(option, { notMerge: true });
      entry.mode = 'echarts';
      setMode(entry, '');
    } catch (err) {
      console.warn(`[JARVIS] ECharts renderer failed for ${entry.tool}:`, err);
      if (entry.tool !== entry.origTool) {      // a morph failed: go back to the original
        toast(`Can't show this data as ${entry.tool}: ${err.message}`, 'err');
        entry.tool = entry.origTool; entry.args = entry.origArgs; entry.variant = null;
        syncHeader(entry);
        return draw(entry, opts);
      }
      entry.mode = 'plotly';
      if (entry.chart) { entry.chart.dispose(); entry.chart = null; }
      renderPlotly(entry, err);
    }
  }

  function setMode(entry, text) { const m = $('[data-role="mode"]', entry.card); if (m) m.textContent = text; }

  // ---------------------------------------------------------------- Plotly fallback
  let plotlyLoading = null;
  function loadPlotly() {
    if (window.Plotly) return Promise.resolve();
    if (!plotlyLoading) {
      plotlyLoading = new Promise((res, rej) => {
        const s = document.createElement('script');
        s.src = 'https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js';
        s.onload = res; s.onerror = () => rej(new Error('could not load Plotly'));
        document.head.appendChild(s);
      });
    }
    return plotlyLoading;
  }
  async function renderPlotly(entry, why) {
    setMode(entry, '· Plotly fallback');
    const sw = $('.switcher', entry.card); if (sw) sw.remove();
    const box = entry.chartEl;
    if (!entry.figure) { box.innerHTML = `<div class="fallback-msg">Couldn't render this chart${why ? `: ${esc(why.message)}` : ''}.</div>`; return; }
    try {
      await loadPlotly();
      const ctx = C.theme(state.theme);
      const layout = Object.assign({}, entry.figure.layout, {
        paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', autosize: true, width: null, height: null,
        font: { family: 'Inter, sans-serif', color: ctx.text }, title: { text: '' }, margin: { l: 50, r: 20, t: 30, b: 40 },
      });
      ['xaxis', 'yaxis'].forEach(k => { layout[k] = Object.assign({}, layout[k], { gridcolor: ctx.split, linecolor: ctx.axis, zerolinecolor: ctx.axis }); });
      box.innerHTML = '';
      await Plotly.newPlot(box, entry.figure.data, layout, { responsive: true, displaylogo: false });
    } catch (e) {
      box.innerHTML = `<div class="fallback-msg">Couldn't render this chart: ${esc(e.message)}</div>`;
    }
  }

  // ---------------------------------------------------------------- card actions
  function morph(entry, variant) {
    if (entry.mode !== 'echarts') return;
    if (variant === entry.origVariant) {           // back to exactly what the LLM asked for
      entry.tool = entry.origTool; entry.args = entry.origArgs; entry.variant = null;
    } else {
      const [tool, args] = C.convert(entry.origTool, entry.origArgs, variant);
      entry.tool = tool; entry.args = args; entry.variant = variant;
    }
    syncHeader(entry);
    if (entry.card.dataset.view !== 'chart') toggleView(entry, entry.card.dataset.view);
    draw(entry);
  }

  function syncHeader(entry) {
    const label = entry.variant ? (C.VARIANT_LABELS[entry.variant] || entry.tool) : ((K.META[entry.origTool] || {}).label || entry.origTool);
    const sw = $('.switch-btn', entry.card);
    if (sw) sw.innerHTML = `${K.icon(entry.tool)}<span>${esc(label)}</span>${ICON.chev}`;
    $('.card-icon', entry.card).innerHTML = K.icon(entry.tool);
    $('[data-role="badge"]', entry.card).textContent = entry.tool;
  }

  function replay(entry) {
    if (entry.mode === 'plotly') return renderPlotly(entry);
    if (!entry.chart) return;
    entry.chart.clear();
    entry.chart.setOption(entry.option, { notMerge: true });
  }

  function toggleView(entry, view, btn) {
    const card = entry.card;
    const next = card.dataset.view === view ? 'chart' : view;
    card.dataset.view = next;
    $$('[data-act="table"],[data-act="code"]', card).forEach(b => b.classList.toggle('active', b.dataset.act === next));
    if (next === 'table') {
      let t;
      try { t = C.table(entry.tool, entry.args); } catch (e) { t = { cols: [], rows: [] }; }
      $('.pane-table', card).innerHTML = t.rows.length
        ? `<table class="data-table"><thead><tr>${t.cols.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${t.rows.map(r => `<tr>${r.map(v => `<td>${esc(typeof v === 'number' ? C.fmtFull(v) : v)}</td>`).join('')}</tr>`).join('')}</tbody></table>`
        : '<div class="empty">No tabular data.</div>';
    }
    if (next === 'code') $('.pane-code .code', card).innerHTML = highlight(JSON.stringify({ tool: entry.tool, args: entry.args }, null, 2));
    if (next === 'chart' && entry.chart) setTimeout(() => entry.chart.resize(), 30);
  }

  function downloadPNG(entry) {
    const name = titleOf(entry.tool, entry.args).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'chart';
    let url;
    if (entry.mode === 'echarts' && entry.chart) {
      url = entry.chart.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: C.theme(state.theme).surface });
      save(url, name + '.png');
    } else if (window.Plotly) {
      Plotly.toImage(entry.chartEl, { format: 'png', scale: 2 }).then(u => save(u, name + '.png'));
    }
  }
  function save(url, name) { const a = document.createElement('a'); a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove(); toast(`Saved ${name}`); }

  function highlight(json) {
    return String(json).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/("(\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, m => {
        let c = 'n';
        if (m[0] === '"') c = /:$/.test(m) ? 'k' : 's';
        else if (m === 'true' || m === 'false') c = 'b';
        else if (m === 'null') c = 'z';
        return `<span class="j-${c}">${m}</span>`;
      });
  }

  function closeMenus(except) { $$('.switcher.open').forEach(s => { if (s !== except) s.classList.remove('open'); }); }
  document.addEventListener('click', e => { if (!e.target.closest('.switcher')) closeMenus(); });

  // ================================================================== modal (full screen)
  const modal = { chart: null, entry: null };
  function openModal(entry) {
    if (entry.mode !== 'echarts') { toast('Full screen is available for animated charts only'); return; }
    modal.entry = entry;
    $('#modal-title').textContent = titleOf(entry.tool, entry.args);
    $('#modal').classList.add('open');
    $('#modal').setAttribute('aria-hidden', 'false');
    setTimeout(() => {
      if (modal.chart) modal.chart.dispose();
      modal.chart = echarts.init($('#modal-chart'), null, { renderer: 'canvas' });
      modal.chart.setOption(C.build(entry.tool, entry.args, Object.assign({}, C.theme(state.theme), { width: $('#modal-chart').clientWidth || 1000 })), { notMerge: true });
    }, 60);
  }
  function closeModal() {
    $('#modal').classList.remove('open');
    $('#modal').setAttribute('aria-hidden', 'true');
    modal.entry = null;
    setTimeout(() => { if (modal.chart && !modal.entry) { modal.chart.dispose(); modal.chart = null; } }, 300);
  }
  addEventListener('resize', () => modal.chart && modal.chart.resize());

  // ================================================================== drawer: gallery + history
  function renderGallery() {
    const fam = {};
    Object.entries(K.META).forEach(([tool, m]) => { (fam[m.family] = fam[m.family] || []).push(tool); });
    $('#gallery').innerHTML = Object.entries(fam).map(([f, tools]) =>
      `<div class="g-family">${esc(f)}</div><div class="g-grid">${tools.map(t => `<button class="g-tile" data-tool="${t}">${K.icon(t)}<span>${esc(K.META[t].label)}</span></button>`).join('')}</div>`).join('');
    $('#gallery').addEventListener('click', e => { const b = e.target.closest('[data-tool]'); if (b) demo(b.dataset.tool); });
  }
  function addHistory(query) {
    state.history = [{ q: query, t: Date.now() }].concat(state.history.filter(h => h.q !== query)).slice(0, 40);
    store.set('history', state.history);
    renderHistory();
  }
  function ago(t) {
    const s = (Date.now() - t) / 1000;
    if (s < 60) return 'now'; if (s < 3600) return `${Math.floor(s / 60)}m`; if (s < 86400) return `${Math.floor(s / 3600)}h`; return `${Math.floor(s / 86400)}d`;
  }
  function renderHistory() {
    const box = $('#history');
    if (!state.history.length) { box.innerHTML = '<div class="empty">Your prompts will appear here.</div>'; return; }
    box.innerHTML = state.history.map((h, i) => `<button class="h-item" data-i="${i}"><span class="h-q">${esc(h.q)}</span><span class="h-time">${ago(h.t)}</span></button>`).join('')
      + '<button class="h-clear" id="h-clear">Clear history</button>';
  }
  $('#history').addEventListener('click', e => {
    if (e.target.id === 'h-clear') { state.history = []; store.set('history', []); renderHistory(); return; }
    const b = e.target.closest('.h-item');
    if (b) { closeDrawer(); ask(state.history[+b.dataset.i].q); }
  });
  function openDrawer(tab) {
    if (tab) selectTab(tab);
    renderHistory();
    $('#drawer').classList.add('open'); $('#drawer').setAttribute('aria-hidden', 'false');
    $('#scrim').classList.add('show');
  }
  function closeDrawer() {
    $('#drawer').classList.remove('open'); $('#drawer').setAttribute('aria-hidden', 'true');
    $('#scrim').classList.remove('show');
  }
  function selectTab(name) {
    $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
    $$('.tab-panel').forEach(p => { p.hidden = p.dataset.panel !== name; });
  }

  // ================================================================== voice input
  function setupVoice() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const btn = $('#btn-mic');
    if (!SR) return;
    btn.hidden = false;
    let rec = null;
    btn.addEventListener('click', () => {
      if (rec) { rec.stop(); return; }
      rec = new SR();
      rec.lang = navigator.language || 'en-US';
      rec.interimResults = true;
      rec.onresult = e => { q.value = [...e.results].map(r => r[0].transcript).join(''); autosize(); if (e.results[e.results.length - 1].isFinal) { const v = q.value; rec.stop(); ask(v); } };
      rec.onerror = e => { if (e.error !== 'aborted' && e.error !== 'no-speech') toast(`Voice input: ${e.error}`, 'err'); };
      rec.onend = () => { rec = null; btn.classList.remove('listening'); };
      btn.classList.add('listening');
      rec.start();
    });
  }

  // ================================================================== new session
  function resetFeed() {
    state.session++;
    setBusy(false);
    GraphPanel.reset();
    state.entries.forEach(e => { try { io.unobserve(e.chartEl); e.ro && e.ro.disconnect(); e.chart && e.chart.dispose(); } catch (x) { /* ignore */ } });
    state.entries = [];
    $$('#feed .turn').forEach(n => n.remove());
    const hero = $('#hero');
    hero.hidden = false;
    typeInto($('#greeting'), greeting());
    renderSuggestions();
    scrollTo({ top: 0, behavior: 'smooth' });
    q.focus();
  }

  // ================================================================== boot
  async function loadMeta() {
    const pill = $('#model-pill');
    try {
      const res = await fetch('/api/meta');
      const m = await res.json();
      state.model = m.model;
      $('#model-name').textContent = m.model;
      pill.classList.add('online');
      pill.title = `Connected · ${m.tools.length} chart tools available`;
    } catch (e) {
      $('#model-name').textContent = 'server offline';
      pill.classList.add('offline');
    }
  }

  function bind() {
    $('#composer').addEventListener('submit', e => { e.preventDefault(); ask(q.value); });
    q.addEventListener('input', autosize);
    q.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask(q.value); }
      if (e.key === 'ArrowUp' && !q.value && state.history[0]) { e.preventDefault(); q.value = state.history[0].q; autosize(); }
    });
    $('#btn-gallery').addEventListener('click', () => openDrawer());
    $('#hero-gallery').addEventListener('click', () => openDrawer('gallery'));
    $('#btn-new').addEventListener('click', resetFeed);
    $('#drawer-close').addEventListener('click', closeDrawer);
    $('#scrim').addEventListener('click', closeDrawer);
    $$('.tab').forEach(t => t.addEventListener('click', () => selectTab(t.dataset.tab)));
    $('#modal-close').addEventListener('click', closeModal);
    $('#modal').addEventListener('click', e => { if (e.target.id === 'modal') closeModal(); });
    document.addEventListener('keydown', e => {
      const typing = /^(INPUT|TEXTAREA)$/.test(document.activeElement?.tagName);
      if (e.key === 'Escape') { closeModal(); closeDrawer(); closeMenus(); }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === '/') { e.preventDefault(); q.focus(); }
      if (e.key === 'g' || e.key === 'G') { $('#drawer').classList.contains('open') ? closeDrawer() : openDrawer(); }
    });
  }

  function boot() {
    bg = FX.startBackground($('#bg'));
    FX.mountReactors();
    typeInto($('#greeting'), greeting());
    renderSuggestions();
    renderGallery();
    renderHistory();
    setupVoice();
    bind();
    loadMeta();
    q.focus();
    // test hook: ?demo=all renders every gallery demo (used for visual QA)
    const p = new URLSearchParams(location.search);
    if (p.get('demo')) {
      const tools = p.get('demo') === 'all' ? Object.keys(K.DEMOS) : p.get('demo').split(',');
      (async () => { for (const t of tools) { await demo(t); } })();
    }
  }

  window.JARVIS = { ask, demo, state };
  boot();
})();
