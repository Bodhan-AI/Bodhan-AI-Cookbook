// Visions: real chart results from /api/chart drawn with the classic JarvisCharts renderer,
// themed with MAHA_THEME and given a Mahabharata "skin" that changes ONLY decoration
// (borders, glows, rings, fonts, palettes) — never data, series values, axes, labels or units.
// Fallback: the server-built Plotly figure; if that fails too, an honest error.

import { esc, prefersReducedMotion } from '../core/util.js';

// Canvas colours have to be literal values (ECharts cannot read CSS variables); they mirror the
// design tokens in css/voyage/base.css.
const T = {
  night: '#0B0E22', night2: '#12152E', night3: '#1A1D3A',
  gold: '#C9A24B', goldSoft: '#E3C77E', ivory: '#F3EAD7', ivoryDim: '#CFC4AE',
  vermilion: '#B5452F', forestLight: '#6E9C7E', sandstone: '#C8A57A', celestial: '#6F8FC9',
};

/** Theme object passed as ctx to JarvisCharts.build (keys the classic renderer reads). */
export const MAHA_THEME = Object.freeze({
  dark: true,
  colors: [T.gold, T.celestial, T.vermilion, T.forestLight, T.sandstone, T.goldSoft, '#B07AA1', '#5FA3A0', '#D98E4A', '#8E86C9'],
  text: T.ivory,
  sub: T.ivoryDim,
  faint: 'rgba(243,234,215,0.42)',
  axis: 'rgba(201,162,75,0.32)',
  split: 'rgba(243,234,215,0.08)',
  surface: T.night2,
  surface2: T.night3,
  tooltip: 'rgba(10,12,30,0.94)',
  border: 'rgba(201,162,75,0.38)',
  up: T.gold,
  down: T.vermilion,
  total: T.celestial,
  warn: T.goldSoft,
  glow: 0.6,
});

/** Mahabharata names for the 24 chart tools (shown with the raw tool name, for honesty). */
export const METAPHORS = {
  line: 'Golden path', area: 'Golden tide', band: 'Path of certainty',
  bar: 'Royal banners', lollipop: 'Standards', waterfall: 'Cascade of fortunes', pareto: 'Banners of weight',
  histogram: 'Ranks of the host', box: 'Measure of the host', violin: 'Shape of the host',
  scatter: 'Constellation', bubble: 'Celestial spheres', heatmap: 'Manuscript of heat', correlation: 'Bonds of fate',
  pie: 'Mandala', donut: 'Mandala', treemap: 'Lineage', sunburst: 'Lineage', funnel: 'Narrowing path',
  candlestick: 'Lamps of fortune', gauge: 'Chakra', radar: 'Yantra', sankey: 'Rivers of consequence', gantt: 'Chronicle',
};

const SERIF = "'Cormorant Garamond', 'Iowan Old Style', Georgia, serif";
const SANS = "'DM Sans', system-ui, sans-serif";
const HEAT = ['#16193A', '#2E2A4E', '#5A3E4A', '#8E5A3E', '#C9A24B', '#F1DFAE']; // night → sandstone → gold → ivory
const DIVERGING = [T.celestial, T.night3, T.gold];                             // −1 … 0 … +1

const PLOTLY_URL = 'https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js';
let plotlyLoading = null;
function loadPlotly() {
  if (window.Plotly) return Promise.resolve(window.Plotly);
  if (!plotlyLoading) {
    plotlyLoading = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = PLOTLY_URL;
      s.async = true;
      s.onload = () => (window.Plotly ? resolve(window.Plotly) : reject(new Error('Plotly did not initialise')));
      s.onerror = () => { plotlyLoading = null; reject(new Error('could not load the Plotly fallback renderer')); };
      document.head.appendChild(s);
    });
  }
  return plotlyLoading;
}

// ------------------------------------------------------------------ colour helpers
function rgba(hex, a) {
  if (typeof hex !== 'string' || hex[0] !== '#') return hex;
  let h = hex.slice(1);
  if (h.length === 3) h = h.split('').map((c) => c + c).join('');
  const n = parseInt(h.slice(0, 6), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}
function hexRgb(hex) {
  let h = hex.replace('#', '');
  if (h.length === 3) h = h.split('').map((c) => c + c).join('');
  const n = parseInt(h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function sample(scale, t) {
  const pos = Math.min(1, Math.max(0, t)) * (scale.length - 1);
  const i = Math.min(scale.length - 2, Math.floor(pos));
  const f = pos - i;
  const a = hexRgb(scale[i]); const b = hexRgb(scale[i + 1]);
  return '#' + a.map((v, k) => Math.round(v + (b[k] - v) * f).toString(16).padStart(2, '0')).join('');
}
function luminance(hex) {
  const [r, g, b] = hexRgb(hex).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
const asArray = (v) => (Array.isArray(v) ? v : v ? [v] : []);
const isObj = (v) => v && typeof v === 'object' && !Array.isArray(v);

// ------------------------------------------------------------------ skin
/** Lotus medallion drawn as an ECharts graphic group centred at (cx, cy) with radius r (px). */
function lotus(id, cx, cy, r, { fill = 'transparent', alpha = 0.55, z = 100 } = {}) {
  const petals = [];
  const n = 8;
  const bez = (p0, p1, p2, p3, t) => {
    const u = 1 - t;
    return [0, 1].map((k) => u * u * u * p0[k] + 3 * u * u * t * p1[k] + 3 * u * t * t * p2[k] + t * t * t * p3[k]);
  };
  const petal = [];
  for (let k = 0; k <= 10; k++) petal.push(bez([0, 0], [r * 0.32, -r * 0.35], [r * 0.22, -r * 0.8], [0, -r], k / 10));
  for (let k = 1; k < 10; k++) petal.push(bez([0, -r], [-r * 0.22, -r * 0.8], [-r * 0.32, -r * 0.35], [0, 0], k / 10));
  for (let i = 0; i < n; i++) {
    petals.push({
      // The ECharts graphic component has no 'path' element (that is a custom-series type and
      // throws "e is not a constructor" here), so each petal is a polygon sampled from its curves.
      type: 'polygon',
      rotation: (i * Math.PI * 2) / n,
      shape: { points: petal },
      style: { fill: rgba(T.gold, 0.08 * alpha), stroke: rgba(T.gold, 0.8 * alpha), lineWidth: 1 },
    });
  }
  return {
    id, type: 'group', x: cx, y: cy, silent: true, z, bounding: 'raw',
    children: [
      { type: 'circle', shape: { cx: 0, cy: 0, r: r * 1.05 }, style: { fill, stroke: rgba(T.gold, 0.6 * alpha), lineWidth: 1 } },
      ...petals,
      { type: 'circle', shape: { cx: 0, cy: 0, r: r * 0.16 }, style: { fill: rgba(T.goldSoft, 0.9 * alpha) } },
    ],
  };
}

/** Pixel centre and inner radius of a pie series (ECharts radius % is of min(w,h)/2). */
function pieGeometry(series, w, h) {
  const c = asArray(series.center);
  const pos = (v, total) => (typeof v === 'string' && v.endsWith('%') ? (parseFloat(v) / 100) * total : (Number.isFinite(+v) ? +v : total / 2));
  const cx = pos(c[0] ?? '50%', w);
  const cy = pos(c[1] ?? '50%', h);
  const half = Math.min(w, h) / 2;
  const rr = asArray(series.radius);
  let innerR = 0;
  if (rr.length > 1) innerR = typeof rr[0] === 'string' && rr[0].endsWith('%') ? (parseFloat(rr[0]) / 100) * half : (Number(rr[0]) || 0);
  return { cx, cy, innerR };
}

/** Longest chain (in nodes) of a sankey edge list; cycles are cut, never followed. */
export function sankeyDepth(sources, targets) {
  const out = new Map();
  const src = asArray(sources); const tgt = asArray(targets);
  for (let i = 0; i < Math.min(src.length, tgt.length); i++) {
    const a = String(src[i]); const b = String(tgt[i]);
    if (!out.has(a)) out.set(a, []);
    out.get(a).push(b);
  }
  const memo = new Map(); const onStack = new Set();
  const depth = (n) => {
    if (memo.has(n)) return memo.get(n);
    if (onStack.has(n)) return 0;
    onStack.add(n);
    let d = 1;
    for (const m of out.get(n) || []) d = Math.max(d, 1 + depth(m));
    onStack.delete(n);
    memo.set(n, d);
    return d;
  };
  let best = 0;
  for (const n of out.keys()) best = Math.max(best, depth(n));
  return best;
}
/** A long chain squeezed into a narrow card turns into unreadable full-height stripes: stand it up. */
const SANKEY_TALL = (depth, width) => depth > 4 && width < 600;
const sankeyLinks = (s) => asArray(s && s.links);

/** Decorative graphics that depend on the chart size (recomputed on resize). */
function sizedGraphics(tool, option, w, h) {
  const out = [];
  if (tool === 'pie' || tool === 'donut') {
    const s = asArray(option.series).find((x) => x && x.type === 'pie'); // the data series is always first
    if (s) {
      const { cx, cy, innerR } = pieGeometry(s, w, h);
      // Donut: a faint lotus behind the centre total. Pie: a small opaque medallion at the hub.
      if (innerR > 24) out.push(lotus('maha-lotus', cx, cy, innerR * 0.62, { alpha: 0.35, z: -1 }));
      else out.push(lotus('maha-lotus', cx, cy, Math.max(10, Math.min(w, h) * 0.07), { fill: T.night, alpha: 1 }));
    }
  }
  return out;
}

function mergeGraphic(option, extra) {
  if (!extra.length) return;
  const g = option.graphic;
  if (Array.isArray(g)) option.graphic = g.concat(extra);
  else if (isObj(g) && Array.isArray(g.elements)) g.elements = g.elements.concat(extra);
  else if (isObj(g)) option.graphic = [g].concat(extra);
  else option.graphic = extra;
}

/**
 * Add Mahabharata semantics to an ECharts option built by JarvisCharts.
 * Only decoration is touched; data arrays, series values, axes, axis labels and units stay as built.
 */
export function skin(option, tool, { width = 520, height = 320, reducedMotion = false } = {}) {
  if (!isObj(option)) return option;
  const series = asArray(option.series);

  // Typography + tooltip glass (fonts only; formatters untouched).
  option.textStyle = Object.assign({}, option.textStyle, { fontFamily: SANS, color: T.ivory });
  asArray(option.tooltip).forEach((tt) => {
    if (!isObj(tt)) return;
    tt.backgroundColor = MAHA_THEME.tooltip;
    tt.borderColor = rgba(T.gold, 0.45);
    tt.textStyle = Object.assign({}, tt.textStyle, { color: T.ivory, fontFamily: SANS });
    tt.extraCssText = 'border-radius:12px;backdrop-filter:blur(12px);box-shadow:0 14px 40px rgba(0,0,0,.55),inset 0 0 0 1px rgba(201,162,75,.12);min-width:120px';
  });
  asArray(option.legend).forEach((lg) => {
    if (isObj(lg)) lg.textStyle = Object.assign({}, lg.textStyle, { fontFamily: SANS, color: T.ivoryDim });
  });
  if (reducedMotion) option.animation = false;

  const visible = (s) => s && !s.silent && !(s.itemStyle && s.itemStyle.color === 'transparent');

  switch (tool) {
    case 'pie': case 'donut': {
      const main = series.find((s) => s && s.type === 'pie');
      if (!main) break;
      main.padAngle = Math.max(Number(main.padAngle) || 0, (main.data || []).length > 1 ? 1.6 : 0);
      main.itemStyle = Object.assign({}, main.itemStyle, { borderColor: rgba(T.gold, 0.55), borderWidth: 1 });
      if (main.labelLine) main.labelLine.lineStyle = Object.assign({}, main.labelLine.lineStyle, { color: rgba(T.gold, 0.5) });
      // Thin gold rings around the mandala (decorative, silent, no data meaning).
      const rr = asArray(main.radius);
      const outer = parseFloat(rr.length > 1 ? rr[1] : rr[0]) || 74;
      const ring = (r0, r1, a) => ({
        type: 'pie', _mahaDecor: true, silent: true, legendHoverLink: false, tooltip: { show: false },
        center: main.center, radius: [`${r0}%`, `${r1}%`], label: { show: false }, labelLine: { show: false },
        emphasis: { disabled: true }, animation: false, z: 0,
        data: [{ value: 1, name: '', itemStyle: { color: rgba(T.gold, a) } }],
      });
      series.push(ring(outer + 3, outer + 3.6, 0.55), ring(outer + 6, outer + 6.3, 0.25));
      if (rr.length > 1 && parseFloat(rr[0]) > 10) {
        const inner = parseFloat(rr[0]);
        series.push(ring(inner - 3.4, inner - 3, 0.45));
      }
      // Keep the visible legend exactly as built (decor rings must not add entries).
      asArray(option.legend).forEach((lg) => { if (isObj(lg)) lg.data = (main.data || []).map((d) => (isObj(d) ? d.name : d)); });
      // Centre total text in the serif voice.
      asArray(option.graphic).forEach((g) => asArray(g && g.children).forEach((ch) => {
        if (ch && ch.type === 'text' && ch.style && /700/.test(ch.style.font || '')) ch.style.font = `600 30px ${SERIF}`;
        else if (ch && ch.type === 'text' && ch.style) ch.style.font = `600 10px ${SANS}`;
        if (ch && ch.style && ch.style.fill === MAHA_THEME.text) ch.style.fill = T.goldSoft;
      }));
      option.series = series;
      mergeGraphic(option, sizedGraphics(tool, option, width, height));
      break;
    }
    case 'line': case 'area': case 'band': {
      series.forEach((s) => {
        if (!s || s.type !== 'line' || s.silent) return;
        if (tool === 'line' && !s.smooth) { s.smooth = 0.3; s.smoothMonotone = 'x'; } // monotone: no overshoot past real values
        s.lineStyle = Object.assign({}, s.lineStyle, { cap: 'round', join: 'round' });
        const c = (s.lineStyle && s.lineStyle.color) || T.gold;
        if (typeof c === 'string') Object.assign(s.lineStyle, { shadowBlur: 14, shadowColor: rgba(c, 0.6) });
        s.itemStyle = Object.assign({}, s.itemStyle, { borderColor: T.night });
      });
      break;
    }
    case 'bar': case 'lollipop': case 'pareto': case 'waterfall': case 'histogram': {
      series.forEach((s) => {
        if (!visible(s) || s.type !== 'bar') return;
        s.itemStyle = Object.assign({}, s.itemStyle, { borderColor: rgba(T.goldSoft, 0.65), borderWidth: 1 });
      });
      break;
    }
    case 'sankey': {
      series.forEach((s) => {
        if (!s || s.type !== 'sankey') return;
        s.lineStyle = Object.assign({}, s.lineStyle, { color: 'gradient', curveness: 0.55, opacity: 0.36 });
        s.emphasis = Object.assign({}, s.emphasis, { lineStyle: { opacity: 0.72 } });
        s.itemStyle = Object.assign({}, s.itemStyle, { borderColor: rgba(T.goldSoft, 0.6), borderWidth: 1 });
        s.label = Object.assign({}, s.label, { color: T.ivory, fontFamily: SANS });
        const links = sankeyLinks(s);
        const depth = sankeyDepth(links.map((l) => l && l.source), links.map((l) => l && l.target));
        if (SANKEY_TALL(depth, width)) {
          // Layout only (data untouched): top-to-bottom levels, bars on the left, names to their right.
          Object.assign(s, {
            orient: 'vertical', nodeAlign: 'justify', nodeWidth: 12, nodeGap: 12,
            left: 10, top: 16, bottom: 16, right: Math.round(Math.min(230, Math.max(150, width * 0.46))),
          });
          s.label = Object.assign(s.label, { position: 'right', distance: 8, fontSize: 12, overflow: 'truncate', width: Math.round(Math.min(220, Math.max(140, width * 0.44))) });
          s.labelLayout = { hideOverlap: false };
          s.lineStyle.curveness = 0.4;
          s.lineStyle.opacity = 0.5;
        }
      });
      break;
    }
    case 'treemap': case 'sunburst': {
      series.forEach((s) => {
        if (!s || (s.type !== 'treemap' && s.type !== 'sunburst')) return;
        s.itemStyle = Object.assign({}, s.itemStyle, { borderColor: rgba(T.gold, 0.5) });
        if (s.label) s.label.color = T.ivory;
        if (s.upperLabel) s.upperLabel.color = T.ivory;
        if (s.breadcrumb && s.breadcrumb.itemStyle) {
          s.breadcrumb.itemStyle.borderColor = rgba(T.gold, 0.5);
          s.breadcrumb.itemStyle.textStyle = Object.assign({}, s.breadcrumb.itemStyle.textStyle, { color: T.ivory, fontFamily: SANS });
        }
      });
      break;
    }
    case 'radar': {
      asArray(option.radar).forEach((r) => {
        if (!isObj(r)) return;
        r.splitLine = { lineStyle: { color: [rgba(T.gold, 0.5), rgba(T.gold, 0.22), rgba(T.gold, 0.38), rgba(T.gold, 0.22)], width: 1 } };
        r.splitArea = { areaStyle: { color: [rgba(T.gold, 0.02), rgba(T.gold, 0.06)] } };
        r.axisLine = { lineStyle: { color: rgba(T.gold, 0.35), type: [3, 4] } };
        r.axisName = Object.assign({}, r.axisName, { color: T.ivory, fontFamily: SANS });
      });
      break;
    }
    case 'heatmap': case 'correlation': {
      const scale = tool === 'correlation' ? DIVERGING : HEAT;
      const vm = asArray(option.visualMap)[0];
      const mn = vm && Number.isFinite(vm.min) ? vm.min : null;
      const mx = vm && Number.isFinite(vm.max) ? vm.max : null;
      if (vm) vm.inRange = Object.assign({}, vm.inRange, { color: scale });
      series.forEach((s) => {
        if (!s || s.type !== 'heatmap') return;
        s.itemStyle = Object.assign({}, s.itemStyle, { borderColor: T.night });
        // Recompute only the per-cell LABEL colour so numbers stay legible on the new palette.
        if (mn != null && mx != null && mx > mn) {
          (s.data || []).forEach((d) => {
            const v = isObj(d) && Array.isArray(d.value) ? d.value[2] : null;
            if (typeof v !== 'number') return;
            const bg = sample(scale, (v - mn) / (mx - mn));
            d.label = Object.assign({}, d.label, { color: luminance(bg) > 0.22 ? T.night : T.ivory });
          });
        }
      });
      break;
    }
    case 'scatter': case 'bubble': {
      asArray(option.visualMap).forEach((vm) => { if (isObj(vm)) vm.inRange = Object.assign({}, vm.inRange, { color: HEAT.slice(1) }); });
      break;
    }
    case 'gauge': {
      series.forEach((s) => {
        if (!s || s.type !== 'gauge') return;
        if (s.splitLine && s.splitLine.lineStyle) s.splitLine.lineStyle.color = rgba(T.gold, 0.8);
        if (s.axisTick && s.axisTick.lineStyle) s.axisTick.lineStyle.color = rgba(T.gold, 0.4);
        if (s.anchor && s.anchor.itemStyle) Object.assign(s.anchor.itemStyle, { color: T.night, borderColor: T.gold });
        if (s.detail && s.detail.fontSize > 20) Object.assign(s.detail, { fontFamily: SERIF, color: T.goldSoft });
      });
      break;
    }
    case 'candlestick': case 'gantt': case 'funnel': case 'box': case 'violin':
    default:
      break; // palette alone (MAHA_THEME colours, up = gold, down = vermilion) carries the manuscript look
  }
  return option;
}

// ------------------------------------------------------------------ card
function chartHeight(tool, args, width) {
  const a = isObj(args) ? args : {};
  switch (tool) {
    case 'gauge': return 270;
    case 'sankey': {
      const depth = sankeyDepth(a.sources, a.targets);
      return SANKEY_TALL(depth, width) ? Math.max(380, Math.min(720, 40 + 64 * depth)) : 380;
    }
    case 'treemap': case 'sunburst': return 360;
    case 'radar': case 'pie': case 'donut': return Math.max(300, Math.min(360, width * 0.72));
    case 'gantt': return Math.max(240, Math.min(560, 70 + 34 * (Array.isArray(a.tasks) ? a.tasks.length : 6)));
    case 'heatmap': case 'correlation': {
      const rows = tool === 'heatmap' ? (Array.isArray(a.z) ? a.z.length : 6) : Object.keys(a.data || {}).length;
      return Math.max(280, Math.min(540, 110 + rows * 34));
    }
    default: return 320;
  }
}

function tableHTML(tbl) {
  const cols = Array.isArray(tbl && tbl.cols) ? tbl.cols : [];
  const rows = Array.isArray(tbl && tbl.rows) ? tbl.rows : [];
  if (!cols.length || !rows.length) return '<p class="rv-empty">No tabular data in this result.</p>';
  const fmtFull = window.JarvisCharts && window.JarvisCharts.fmtFull;
  const cell = (v) => {
    if (typeof v === 'number' && Number.isFinite(v)) {
      let shown = String(v);
      try { if (fmtFull) shown = fmtFull(v); } catch { /* keep the raw number */ }
      return `<td class="is-num">${esc(shown)}</td>`;
    }
    return `<td>${esc(v == null ? '' : v)}</td>`;
  };
  return `<div class="rv-table-scroll" tabindex="0" role="region" aria-label="Data table"><table class="rv-table">
    <thead><tr>${cols.map((c) => `<th scope="col">${esc(c)}</th>`).join('')}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${(Array.isArray(r) ? r : [r]).map(cell).join('')}</tr>`).join('')}</tbody>
  </table></div>`;
}

function errorCard(container, result, why) {
  const tool = result && result.tool ? String(result.tool) : 'chart';
  const a = result && isObj(result.args) ? result.args : {};
  const named = (typeof a.title === 'string' && a.title.trim()) || '';
  const card = document.createElement('article');
  card.className = 'rv-card rv-card-error';
  card.innerHTML = `
    <header class="rv-card-head">
      <span class="rv-badge">${esc(METAPHORS[tool] || 'Vision')} · ${esc(tool)}</span>
      <h4 class="rv-title">${named ? `${esc(named)} — could not be drawn` : 'This vision could not be drawn'}</h4>
    </header>
    <p class="rv-error-msg">The <b>${esc(tool)}</b> tool failed on the server:</p>
    <pre class="rv-error-code">${esc(why)}</pre>`;
  container.appendChild(card);
  return { el: card, dispose() { card.remove(); }, setView() {} };
}

/**
 * Render one backend result into `container`.
 * @returns {{el: Element, dispose(): void, setView(view: 'chart'|'table'): void}}
 */
export function renderResult(container, result, opts = {}) {
  if (!result || typeof result !== 'object') return errorCard(container, { tool: null }, 'The result was empty.');
  if (result.error) return errorCard(container, result, String(result.error));
  const tool = String(result.tool || '');
  const args = isObj(result.args) ? result.args : {};
  const C = window.JarvisCharts;
  const titles = (C && C.DEFAULT_TITLES) || {};
  const title = (typeof args.title === 'string' && args.title.trim()) || titles[tool] || tool || 'Vision';
  const reduce = opts.reducedMotion ?? prefersReducedMotion();

  let count = null;
  try { count = C && C.countValues ? C.countValues(args) : null; } catch { count = null; }

  const card = document.createElement('article');
  card.className = 'rv-card';
  card.innerHTML = `
    <header class="rv-card-head">
      <span class="rv-badge" title="Chart tool: ${esc(tool)}"><i class="rv-sigil rv-sigil-${esc(tool)}" aria-hidden="true"></i>${esc(METAPHORS[tool] || 'Vision')} · ${esc(tool)}</span>
      <h4 class="rv-title">${esc(title)}</h4>
      <div class="rv-meta"><span data-role="count">${count ? `${count.toLocaleString('en-US')} values` : ''}</span><span data-role="mode"></span></div>
      <div class="rv-views" role="group" aria-label="View">
        <button type="button" class="rv-view-btn" data-view="chart" aria-pressed="true">Chart</button>
        <button type="button" class="rv-view-btn" data-view="table" aria-pressed="false">Table</button>
      </div>
    </header>
    <div class="rv-chart" role="img" aria-label="${esc(title)}"></div>
    <div class="rv-tablewrap" hidden></div>`;
  container.appendChild(card);

  const chartEl = card.querySelector('.rv-chart');
  const tableEl = card.querySelector('.rv-tablewrap');
  const modeEl = card.querySelector('[data-role="mode"]');
  const initialW = Math.max(280, chartEl.clientWidth || container.clientWidth || 520);
  chartEl.style.height = `${Math.round(chartHeight(tool, args, initialW))}px`;

  let chart = null;
  let ro = null;
  let mode = 'pending';  // 'echarts' | 'plotly' | 'failed'
  let disposed = false;
  let lastW = 0;
  let view = 'chart';

  const setMode = (text) => { modeEl.textContent = text; };

  function buildOption(w, h) {
    const option = C.build(tool, args, Object.assign({}, MAHA_THEME, { width: w }));
    return skin(option, tool, { width: w, height: h, reducedMotion: reduce });
  }

  function drawECharts() {
    const w = chartEl.clientWidth; const h = chartEl.clientHeight;
    if (!window.echarts || !C || typeof C.build !== 'function') {
      return fallback(new Error('the ECharts renderer is not loaded'));
    }
    let option;
    try {
      option = buildOption(w, h);
    } catch (err) {
      console.warn(`[voyage] ECharts renderer failed for ${tool}: ${(err && err.message) || err}`);
      return fallback(err);
    }
    try {
      chart = window.echarts.init(chartEl, null, { renderer: 'canvas' });
      chart.setOption(option, { notMerge: true });
      mode = 'echarts';
      lastW = w;
      setMode('');
    } catch (err) {
      console.warn(`[voyage] ECharts setOption failed for ${tool}: ${(err && err.message) || err}`, err && err.stack);
      if (chart) { try { chart.dispose(); } catch { /* ignore */ } chart = null; }
      fallback(err);
    }
  }

  async function fallback(why) {
    mode = 'plotly';
    setMode('· Plotly fallback');
    const fig = result.figure;
    if (!fig || !Array.isArray(fig.data)) {
      mode = 'failed';
      setMode('');
      chartEl.innerHTML = `<div class="rv-fallback-msg">This vision could not be drawn${why ? `: ${esc(why.message || why)}` : ''}. The data is still available in the table view.</div>`;
      return;
    }
    chartEl.innerHTML = '<div class="rv-fallback-msg">Loading the fallback renderer…</div>';
    try {
      const Plotly = await loadPlotly();
      if (disposed) return;
      const lay = isObj(fig.layout) ? fig.layout : {};
      const axisStyle = (ax) => Object.assign({}, ax, {
        gridcolor: MAHA_THEME.split, linecolor: MAHA_THEME.axis, zerolinecolor: MAHA_THEME.axis,
        tickfont: Object.assign({}, ax && ax.tickfont, { color: T.ivoryDim }),
        title: isObj(ax && ax.title) ? Object.assign({}, ax.title, { font: Object.assign({}, ax.title.font, { color: T.ivoryDim }) }) : (ax && ax.title),
      });
      const layout = Object.assign({}, lay, {
        paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', autosize: true, width: null, height: null,
        font: { family: SANS, color: T.ivory }, title: { text: '' }, colorway: MAHA_THEME.colors,
        margin: { l: 50, r: 20, t: 30, b: 40 },
        legend: Object.assign({}, lay.legend, { font: { color: T.ivoryDim, family: SANS }, bgcolor: 'rgba(0,0,0,0)' }),
        hoverlabel: { bgcolor: T.night2, bordercolor: T.gold, font: { color: T.ivory, family: SANS } },
      });
      Object.keys(layout).forEach((k) => { if (/^[xy]axis\d*$/.test(k)) layout[k] = axisStyle(layout[k]); });
      if (!layout.xaxis) layout.xaxis = axisStyle({});
      if (!layout.yaxis) layout.yaxis = axisStyle({});
      chartEl.innerHTML = '';
      await Plotly.newPlot(chartEl, fig.data, layout, { responsive: true, displaylogo: false, displayModeBar: false });
    } catch (e) {
      if (disposed) return;
      mode = 'failed';
      setMode('');
      chartEl.innerHTML = `<div class="rv-fallback-msg">This vision could not be drawn: ${esc((why && why.message) || '')}${why ? '; ' : ''}fallback failed: ${esc(e.message || e)}. The data is still available in the table view.</div>`;
    }
  }

  // Init only once the element has a real size (the drawer may still be sliding in / detached).
  ro = new ResizeObserver(() => {
    if (disposed) return;
    const w = chartEl.clientWidth;
    if (!w) return;
    if (mode === 'pending') { drawECharts(); return; }
    if (mode === 'echarts' && chart) {
      chart.resize();
      // Size-dependent decorations (lotus medallion) follow the new size.
      if (Math.abs(w - lastW) > 24 && (tool === 'pie' || tool === 'donut')) {
        lastW = w;
        try {
          const g = sizedGraphics(tool, chart.getOption(), w, chartEl.clientHeight);
          if (g.length) chart.setOption({ graphic: g });
        } catch { /* decoration only */ }
      }
    } else if (mode === 'plotly' && window.Plotly) {
      try { window.Plotly.Plots.resize(chartEl); } catch { /* ignore */ }
    }
  });
  ro.observe(chartEl);

  function setView(v) {
    view = v === 'table' ? 'table' : 'chart';
    card.querySelectorAll('.rv-view-btn').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.view === view)));
    if (view === 'table') {
      if (!tableEl.dataset.built) {
        try {
          tableEl.innerHTML = C && typeof C.table === 'function' ? tableHTML(C.table(tool, args)) : '<p class="rv-empty">Table view is unavailable (renderer not loaded).</p>';
        } catch (e) {
          tableEl.innerHTML = `<p class="rv-empty">No table view for this result: ${esc(e.message || e)}</p>`;
        }
        tableEl.dataset.built = '1';
      }
      tableEl.hidden = false;
      chartEl.hidden = true;
    } else {
      tableEl.hidden = true;
      chartEl.hidden = false;
      if (chart) requestAnimationFrame(() => chart && chart.resize());
    }
  }
  card.querySelectorAll('.rv-view-btn').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));

  return {
    el: card,
    get view() { return view; },
    setView,
    dispose() {
      disposed = true;
      if (ro) { ro.disconnect(); ro = null; }
      if (chart) { try { chart.dispose(); } catch { /* ignore */ } chart = null; }
      if (mode === 'plotly' && window.Plotly) { try { window.Plotly.purge(chartEl); } catch { /* ignore */ } }
      card.remove();
    },
  };
}
