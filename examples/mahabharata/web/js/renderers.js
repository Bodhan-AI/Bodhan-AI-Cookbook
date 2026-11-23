/* JARVIS chart renderers:  (tool name, LLM args)  ->  animated ECharts option.
 *
 * Every builder mirrors the argument semantics of the matching Python function in charts/.
 * A builder throws on input it cannot draw faithfully; the UI then falls back to the Plotly
 * figure that the server already built from the same args.
 */
(function (global) {
  'use strict';

  // ------------------------------------------------------------------ themes
  const THEMES = {
    light: {
      dark: false,
      colors: ['#1D1D1F', '#8E8E93', '#48484A', '#AEAEB2', '#2C2C2E', '#6E6E73', '#636366', '#99999E', '#3A3A3C', '#7C7C80'],
      text: '#1D1D1F', sub: '#6E6E73', faint: '#A1A1A6',
      axis: 'rgba(0,0,0,0.20)', split: 'rgba(0,0,0,0.07)',
      surface: '#FFFFFF', surface2: '#F2F2F7', tooltip: 'rgba(255,255,255,0.96)', border: 'rgba(0,0,0,0.14)',
      up: '#1D1D1F', down: '#A1A1A6', total: '#48484A', warn: '#6E6E73', glow: 0,
    },
  };
  THEMES.dark = THEMES.light;  // single monochrome theme
  const theme = () => THEMES.light;

  const DEFAULT_TITLES = {
    line: 'Line chart', area: 'Area chart', band: 'Line with confidence band', bar: 'Bar chart',
    lollipop: 'Lollipop chart', waterfall: 'Waterfall chart', pareto: 'Pareto chart', histogram: 'Histogram',
    box: 'Box plot', violin: 'Violin plot', scatter: 'Scatter plot', bubble: 'Bubble chart', heatmap: 'Heatmap',
    correlation: 'Correlation matrix', pie: 'Pie chart', donut: 'Donut chart', treemap: 'Treemap',
    sunburst: 'Sunburst', funnel: 'Funnel chart', candlestick: 'Candlestick chart', gauge: 'Gauge',
    radar: 'Radar chart', sankey: 'Sankey diagram', gantt: 'Gantt chart',
  };

  // ------------------------------------------------------------------ small helpers
  const isNum = v => typeof v === 'number' && isFinite(v);
  function num(v) {
    if (typeof v === 'number') return isFinite(v) ? v : null;
    if (typeof v === 'string' && v.trim() !== '') {
      const n = Number(v.replace(/[,_\s]/g, ''));
      return isFinite(n) ? n : null;
    }
    return null;
  }
  function list(v, name) {
    if (Array.isArray(v)) return v;
    if (v == null) throw new Error(`missing "${name}"`);
    throw new Error(`"${name}" must be a list`);
  }
  const nums = (v, name) => list(v, name).map(num);
  const strs = (v, name) => list(v, name).map(x => (x == null ? '' : String(x)));

  /** Python as_series(): list -> [[default, list]], {name: list} -> [[name, list], ...] */
  function asSeries(y, def = 'value', name = 'y') {
    if (Array.isArray(y)) return [[def, y.map(num)]];
    if (y && typeof y === 'object') {
      const out = Object.entries(y).map(([k, v]) => [String(k), (Array.isArray(v) ? v : [v]).map(num)]);
      if (!out.length) throw new Error(`"${name}" is empty`);
      return out;
    }
    throw new Error(`"${name}" must be a list or {name: list}`);
  }
  function groups(data, name = 'data') {
    if (Array.isArray(data)) return [['value', data.map(num).filter(isNum)]];
    if (data && typeof data === 'object') {
      const out = Object.entries(data).map(([k, v]) => [String(k), (Array.isArray(v) ? v : [v]).map(num).filter(isNum)]);
      if (!out.length) throw new Error(`"${name}" is empty`);
      return out;
    }
    throw new Error(`"${name}" must be {group: list}`);
  }

  function rgba(hex, a) {
    if (typeof hex !== 'string' || hex[0] !== '#') return hex;
    let h = hex.slice(1);
    if (h.length === 3) h = h.split('').map(c => c + c).join('');
    const n = parseInt(h.slice(0, 6), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }
  function mix(hex, other, t) {
    const p = x => { let h = x.slice(1); if (h.length === 3) h = h.split('').map(c => c + c).join(''); const n = parseInt(h, 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; };
    const a = p(hex), b = p(other);
    return '#' + a.map((v, i) => Math.round(v + (b[i] - v) * t).toString(16).padStart(2, '0')).join('');
  }
  const G = () => global.echarts.graphic;
  const vgrad = (c, a0, a1) => new (G().LinearGradient)(0, 0, 0, 1, [{ offset: 0, color: rgba(c, a0) }, { offset: 1, color: rgba(c, a1) }]);
  const hgrad = (c, a0, a1) => new (G().LinearGradient)(0, 0, 1, 0, [{ offset: 0, color: rgba(c, a0) }, { offset: 1, color: rgba(c, a1) }]);
  const glow = (ctx, c, blur = 14) => ({ shadowBlur: blur * ctx.glow, shadowColor: rgba(c, ctx.dark ? 0.55 : 0.35) });
  const col = (ctx, i) => ctx.colors[((i % ctx.colors.length) + ctx.colors.length) % ctx.colors.length];

  function fmt(v) {
    if (v == null) return '–';
    if (typeof v !== 'number' || !isFinite(v)) return String(v);
    const a = Math.abs(v);
    const s = (x, u) => (Math.abs(x) >= 100 ? x.toFixed(0) : x.toFixed(1)).replace(/\.0$/, '') + u;
    if (a >= 1e12) return s(v / 1e12, 'T');
    if (a >= 1e9) return s(v / 1e9, 'B');
    if (a >= 1e6) return s(v / 1e6, 'M');
    if (a >= 1e4) return s(v / 1e3, 'K');
    return v.toLocaleString('en-US', { maximumFractionDigits: a < 1 && a > 0 ? 3 : 2 });
  }
  function fmtFull(v) {
    if (v == null) return '–';
    return isNum(v) ? v.toLocaleString('en-US', { maximumFractionDigits: 4 }) : esc(String(v));
  }
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const dot = c => `<span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${c};margin-right:7px;box-shadow:0 0 8px ${c}"></span>`;
  const row = (c, k, v) => `<div style="display:flex;justify-content:space-between;gap:18px;align-items:center"><span>${c ? dot(c) : ''}${esc(k)}</span><b style="font-variant-numeric:tabular-nums">${v}</b></div>`;
  const head = t => `<div style="font-weight:600;margin-bottom:6px;opacity:.85">${esc(t)}</div>`;

  function parseDate(v) {
    if (typeof v === 'number') return v;
    const s = String(v).trim();
    const m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(s);
    if (m) return new Date(+m[1], +m[2] - 1, +m[3]).getTime();  // local midnight, no TZ shift
    const t = Date.parse(s);
    if (!isFinite(t)) throw new Error(`cannot parse date "${s}"`);
    return t;
  }
  function niceCeil(v) {
    if (!(v > 0)) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
    return 10 * p;
  }

  /** Decide how to place x values like Plotly does: ISO dates -> time axis, numbers -> value axis, else categories. */
  const ISO = /^\d{4}-\d{1,2}(-\d{1,2})?([T ]\d{1,2}:\d{2}(:\d{2})?)?/;
  function xScale(x0, name = 'x') {
    const raw = list(x0, name), labels = strs(raw, name);
    const nn = raw.filter(v => v != null && v !== '');
    let kind = 'category', xs = null;
    if (nn.length && nn.every(v => typeof v === 'number' || (typeof v === 'string' && /^-?\d+(\.\d+)?$/.test(v.trim())))) { kind = 'value'; xs = raw.map(num); }
    else if (nn.length && nn.every(v => typeof v === 'string' && ISO.test(v.trim()) && isFinite(Date.parse(v.trim())))) { kind = 'time'; xs = raw.map(v => (v == null || v === '' ? null : parseDate(v))); }
    return {
      kind, labels,
      pair: vals => (xs ? vals.map((v, i) => [xs[i], v]) : vals),
      axis: (ctx, title) => axis(ctx, kind === 'category'
        ? { pos: 'x', type: 'category', data: labels, name: title, extra: { boundaryGap: false } }
        : { pos: 'x', type: kind, name: title, scale: true, plain: kind === 'value' }),
    };
  }

  // ------------------------------------------------------------------ shared option pieces
  function tooltip(ctx, extra) {
    return Object.assign({
      backgroundColor: ctx.tooltip, borderColor: ctx.border, borderWidth: 1, padding: [10, 14],
      textStyle: { color: ctx.text, fontSize: 13, fontFamily: 'Inter, system-ui, sans-serif' },
      extraCssText: `border-radius:12px;backdrop-filter:blur(14px);box-shadow:0 12px 40px rgba(0,0,0,${ctx.dark ? 0.45 : 0.15});min-width:120px`,
      confine: true, valueFormatter: fmtFull,
    }, extra);
  }
  function legend(ctx, extra) {
    return Object.assign({
      type: 'scroll', top: 0, left: 'center', icon: 'roundRect', itemWidth: 14, itemHeight: 8, itemGap: 18,
      textStyle: { color: ctx.sub, fontSize: 12 }, pageTextStyle: { color: ctx.sub },
      pageIconColor: ctx.colors[0], pageIconInactiveColor: ctx.faint,
      inactiveColor: ctx.faint,
    }, extra);
  }
  function grid(a, hasLegend, extra) {
    return Object.assign({
      left: 14, right: 22, containLabel: true,
      top: hasLegend ? 46 : (a && a.y_label ? 38 : 20),
      bottom: a && a.x_label ? 36 : 12,
    }, extra);
  }
  function axis(ctx, o) {
    const isX = o.pos === 'x';
    const cat = o.type === 'category';
    const label = { color: ctx.sub, fontSize: 12, hideOverlap: true, margin: 10 };
    if (o.type === 'value') label.formatter = o.plain ? (v => (Math.abs(v) >= 1e6 ? fmt(v) : String(+(+v).toFixed(6)))) : (o.suffix ? v => fmt(v) + o.suffix : fmt);
    if (cat && isX) {
      const n = (o.data || []).length, slot = Math.round(((ctx.width || 1000) - 90) / Math.max(1, n)) - 8;
      if (n && n <= 12 && slot >= 56) { label.interval = 0; label.width = Math.min(120, slot); label.overflow = 'break'; label.lineHeight = 14; }
      else if (n && n <= 12) { label.interval = 0; label.rotate = 35; label.width = 84; label.overflow = 'truncate'; }
      else { label.width = 110; label.overflow = 'truncate'; }
    }
    const ax = {
      type: o.type,
      nameLocation: isX ? 'middle' : 'end',
      nameGap: isX ? 32 : 16,
      nameTextStyle: { color: ctx.sub, fontSize: 12, fontWeight: 500, align: isX ? 'center' : 'left' },
      axisLine: { show: cat, lineStyle: { color: ctx.axis } },
      axisTick: { show: false },
      axisLabel: label,
      splitLine: { show: !cat, lineStyle: { color: ctx.split, type: [4, 4] } },
      axisPointer: { label: { backgroundColor: ctx.surface2, color: ctx.text, borderRadius: 4 } },
    };
    if (o.data) ax.data = o.data;
    if (o.name) ax.name = o.name;
    if (o.scale) ax.scale = true;
    return Object.assign(ax, o.extra || {});
  }
  function zoom(ctx, n, axisIndex = [0], dim = 'x') {
    const key = dim === 'x' ? 'xAxisIndex' : 'yAxisIndex';
    const z = [{ type: 'inside', [key]: axisIndex, zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: false, moveOnMouseMove: true }];
    if (n > 40 && dim === 'x') {
      z.push({
        type: 'slider', [key]: axisIndex, height: 18, bottom: 6, borderColor: 'transparent',
        backgroundColor: rgba(ctx.colors[0], 0.05), fillerColor: rgba(ctx.colors[0], 0.15),
        dataBackground: { lineStyle: { color: rgba(ctx.colors[0], 0.5) }, areaStyle: { color: rgba(ctx.colors[0], 0.12) } },
        selectedDataBackground: { lineStyle: { color: ctx.colors[0] }, areaStyle: { color: rgba(ctx.colors[0], 0.25) } },
        handleStyle: { color: ctx.colors[0], borderColor: ctx.colors[0] }, moveHandleSize: 4,
        textStyle: { color: ctx.sub }, labelFormatter: '',
      });
    }
    return z;
  }
  function base(ctx, opt) {
    return Object.assign({
      backgroundColor: 'transparent',
      color: ctx.colors,
      textStyle: { fontFamily: 'Inter, system-ui, sans-serif', color: ctx.text },
      animationDuration: 1100, animationEasing: 'cubicOut',
      animationDurationUpdate: 850, animationEasingUpdate: 'cubicInOut',
      tooltip: tooltip(ctx),
    }, opt);
  }
  function checkLen(name, arr, n) {
    if (arr.length < n) throw new Error(`"${name}" has ${arr.length} items, expected ${n}`);
  }

  // ------------------------------------------------------------------ colour scales (heatmaps)
  const SCALES = {
    blues: { dark: ['#0B1E3A', '#123A6B', '#1F63B0', '#2FA3E6', '#38E1FF', '#C8F7FF'], light: ['#F0F7FF', '#C6DEFB', '#8DBDF4', '#4A90E2', '#1D5FB4', '#0B356E'] },
    reds: { dark: ['#2A0E18', '#5C1427', '#9B1C3A', '#E3365A', '#FF7A93', '#FFD3DC'], light: ['#FFF1F2', '#FECDD3', '#FB7185', '#E11D48', '#9F1239', '#4C0519'] },
    greens: { dark: ['#06231C', '#0B4A38', '#11805E', '#1FBF8A', '#2EE6A6', '#C9FCE6'], light: ['#F0FDF6', '#BBF7D0', '#4ADE80', '#16A34A', '#166534', '#052E16'] },
    purples: { dark: ['#170F33', '#2D1F66', '#4B36A8', '#7462E8', '#8B7CFF', '#E1DCFF'], light: ['#F5F3FF', '#DDD6FE', '#A78BFA', '#7C3AED', '#5B21B6', '#2E1065'] },
    oranges: { dark: ['#2A1406', '#5C2A0A', '#A04A12', '#E8711F', '#FF8A4C', '#FFE0CC'], light: ['#FFF7ED', '#FED7AA', '#FB923C', '#EA580C', '#9A3412', '#431407'] },
    viridis: { both: ['#440154', '#414487', '#2A788E', '#22A884', '#7AD151', '#FDE725'] },
    plasma: { both: ['#0D0887', '#6A00A8', '#B12A90', '#E16462', '#FCA636', '#F0F921'] },
    inferno: { both: ['#000004', '#420A68', '#932667', '#DD513A', '#FCA50A', '#FCFFA4'] },
    magma: { both: ['#000004', '#3B0F70', '#8C2981', '#DE4968', '#FE9F6D', '#FCFDBF'] },
    cividis: { both: ['#00224E', '#35456C', '#666970', '#948E77', '#C8B866', '#FEE838'] },
    turbo: { both: ['#30123B', '#4686FB', '#1AE4B6', '#A2FC3C', '#FABA39', '#E4460A', '#7A0403'] },
    jet: { both: ['#00007F', '#0000FF', '#007FFF', '#00FFFF', '#7FFF7F', '#FFFF00', '#FF7F00', '#FF0000', '#7F0000'] },
    hot: { both: ['#0B0000', '#8B0000', '#FF2A00', '#FF9A00', '#FFFF33', '#FFFFFF'] },
    ylorrd: { both: ['#FFFFCC', '#FED976', '#FD8D3C', '#E31A1C', '#800026'] },
    ylgnbu: { both: ['#FFFFD9', '#C7E9B4', '#41B6C4', '#225EA8', '#081D58'] },
    rdbu: { both: ['#B2182B', '#EF8A62', '#F7F7F7', '#67A9CF', '#2166AC'] },
    rdylgn: { both: ['#D73027', '#FC8D59', '#FFFFBF', '#91CF60', '#1A9850'] },
    spectral: { both: ['#9E0142', '#F46D43', '#FFFFBF', '#66C2A5', '#5E4FA2'] },
    picnic: { both: ['#0000FF', '#3399FF', '#66CCFF', '#FFFFFF', '#FF6666', '#FF0000'] },
    portland: { both: ['#0C3383', '#0A88BA', '#F2D338', '#F28F38', '#D91E1E'] },
    electric: { both: ['#000000', '#1E0064', '#780064', '#A05A00', '#E6C800', '#FFFADC'] },
  };
  function hexRgb(h) { h = h.replace('#', ''); if (h.length === 3) h = h.split('').map(c => c + c).join(''); const n = parseInt(h, 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
  function sampleScale(scale, t) {
    const pos = t * (scale.length - 1), i = Math.min(scale.length - 2, Math.floor(pos)), f = pos - i;
    return mix(scale[i], scale[i + 1], f);
  }
  function luminance(hex) {
    const [r, g, b] = hexRgb(hex).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }
  function colorScale() { return ['#F5F5F7', '#D2D2D7', '#A1A1A6', '#6E6E73', '#3A3A3C', '#1D1D1F']; }

  // ================================================================== BUILDERS
  const B = {};

  // ---------------------------------------------------------------- line family
  B.line = (a, ctx) => {
    const xs = xScale(a.x), x = xs.labels;
    const ser = asSeries(a.y);
    const markers = a.markers !== false;
    const multi = ser.length > 1;
    return base(ctx, {
      legend: multi ? legend(ctx) : undefined,
      grid: grid(a, multi, x.length > 40 ? { bottom: (a.x_label ? 36 : 12) + 30 } : null),
      tooltip: tooltip(ctx, { trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: ctx.border, width: 1.5 } } }),
      xAxis: xs.axis(ctx, a.x_label),
      yAxis: axis(ctx, { pos: 'y', type: 'value', name: a.y_label, scale: true }),
      dataZoom: zoom(ctx, x.length),
      series: ser.map(([name, vals], i) => ({
        name, type: 'line', data: xs.pair(vals), smooth: a.smooth ? 0.4 : false,
        showSymbol: markers, symbol: 'circle', symbolSize: 8, connectNulls: false,
        lineStyle: Object.assign({ width: 3, color: col(ctx, i) }, glow(ctx, col(ctx, i), 16)),
        itemStyle: { color: col(ctx, i), borderColor: ctx.surface, borderWidth: 2 },
        areaStyle: multi ? undefined : { color: vgrad(col(ctx, i), 0.18, 0) },
        emphasis: { focus: 'series', scale: 1.6 },
        universalTransition: { enabled: true },
        animationDuration: 1800, animationDelay: i * 250,
      })),
    });
  };

  B.area = (a, ctx) => {
    const xs = xScale(a.x), x = xs.labels;
    const ser = asSeries(a.y);
    const multi = ser.length > 1;
    return base(ctx, {
      legend: multi ? legend(ctx) : undefined,
      grid: grid(a, multi, x.length > 40 ? { bottom: (a.x_label ? 36 : 12) + 30 } : null),
      tooltip: tooltip(ctx, { trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: ctx.border, width: 1.5 } } }),
      xAxis: xs.axis(ctx, a.x_label),
      yAxis: axis(ctx, { pos: 'y', type: 'value', name: a.y_label }),
      dataZoom: zoom(ctx, x.length),
      series: ser.map(([name, vals], i) => ({
        name, type: 'line', data: xs.pair(vals), smooth: 0.3, showSymbol: false, symbol: 'circle', symbolSize: 7,
        stack: a.stacked ? 'total' : undefined, stackStrategy: 'all', connectNulls: false,
        lineStyle: Object.assign({ width: 2, color: col(ctx, i) }, glow(ctx, col(ctx, i), 10)),
        itemStyle: { color: col(ctx, i), borderColor: ctx.surface, borderWidth: 2 },
        areaStyle: { color: vgrad(col(ctx, i), a.stacked ? 0.75 : 0.5, a.stacked ? 0.35 : 0.02) },
        emphasis: { focus: 'series' },
        universalTransition: { enabled: true },
        animationDuration: 1700, animationDelay: i * 220,
      })),
    });
  };

  B.band = (a, ctx) => {
    const xs = xScale(a.x), x = xs.labels;
    const y = nums(a.y, 'y'), lo = nums(a.lower, 'lower'), hi = nums(a.upper, 'upper');
    checkLen('lower', lo, y.length); checkLen('upper', hi, y.length);
    const c0 = col(ctx, 0);
    return base(ctx, {
      grid: grid(a, false),
      tooltip: tooltip(ctx, {
        trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: ctx.border } },
        formatter: ps => {
          const i = ps[0].dataIndex;
          return head(x[i]) + row(c0, 'value', fmtFull(y[i])) + row(rgba(c0, 0.35), 'range', `${fmtFull(lo[i])} – ${fmtFull(hi[i])}`);
        },
      }),
      xAxis: xs.axis(ctx, a.x_label),
      yAxis: axis(ctx, { pos: 'y', type: 'value', name: a.y_label, scale: true }),
      dataZoom: zoom(ctx, x.length),
      series: [
        { name: 'lower', type: 'line', data: xs.pair(lo), stack: 'band', stackStrategy: 'all', symbol: 'none', lineStyle: { opacity: 0 }, silent: true, animationDuration: 1400 },
        {
          name: 'range', type: 'line', data: xs.pair(hi.map((u, i) => (u == null || lo[i] == null ? null : u - lo[i]))),
          stack: 'band', stackStrategy: 'all', symbol: 'none', lineStyle: { opacity: 0 }, silent: true,
          areaStyle: { color: vgrad(c0, 0.32, 0.12) }, animationDuration: 1400,
        },
        { name: 'upper edge', type: 'line', data: xs.pair(hi), symbol: 'none', silent: true, lineStyle: { width: 1, type: [4, 4], color: rgba(c0, 0.55) }, animationDuration: 1600 },
        { name: 'lower edge', type: 'line', data: xs.pair(lo), symbol: 'none', silent: true, lineStyle: { width: 1, type: [4, 4], color: rgba(c0, 0.55) }, animationDuration: 1600 },
        {
          name: 'value', type: 'line', data: xs.pair(y), symbol: 'circle', symbolSize: 7, showSymbol: y.length <= 40,
          lineStyle: Object.assign({ width: 3, color: c0 }, glow(ctx, c0, 16)),
          itemStyle: { color: c0, borderColor: ctx.surface, borderWidth: 2 }, animationDuration: 1900, z: 5,
        },
      ],
    });
  };

  // ---------------------------------------------------------------- bar family
  B.bar = (a, ctx) => {
    const x = strs(a.x, 'x');
    const ser = asSeries(a.y);
    const horiz = !!a.horizontal;
    const mode = ['group', 'stack', 'relative'].includes(a.mode) ? a.mode : 'group';
    const stacked = mode !== 'group' && ser.length > 1;
    const showVals = a.show_values !== false;
    const multi = ser.length > 1;
    const catAx = axis(ctx, { pos: horiz ? 'y' : 'x', type: 'category', data: x, name: a.x_label, extra: horiz ? { inverse: true, nameLocation: 'start' } : {} });
    const valAx = axis(ctx, { pos: horiz ? 'x' : 'y', type: 'value', name: a.y_label });
    const n = x.length * ser.length;
    return base(ctx, {
      legend: multi ? legend(ctx) : undefined,
      grid: grid(horiz ? { x_label: a.y_label, y_label: a.x_label } : a, multi, x.length > 40 && !horiz ? { bottom: (a.x_label ? 36 : 12) + 30 } : { right: horiz && showVals && !stacked ? 48 : 22 }),
      tooltip: tooltip(ctx, { trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: rgba(ctx.colors[0], 0.06) } } }),
      xAxis: horiz ? valAx : catAx,
      yAxis: horiz ? catAx : valAx,
      dataZoom: horiz ? undefined : zoom(ctx, x.length),
      series: ser.map(([name, vals], si) => {
        const c = col(ctx, multi ? si : 0);
        return {
          name, type: 'bar', stack: stacked ? 'total' : undefined, stackStrategy: mode === 'stack' ? 'all' : 'samesign', barMaxWidth: 54, barGap: '18%', barCategoryGap: multi ? '28%' : '34%',
          data: vals.map(v => {
            const neg = v != null && v < 0;
            const r = stacked ? 3 : 7;
            const radius = horiz ? (neg ? [r, 0, 0, r] : [0, r, r, 0]) : (neg ? [0, 0, r, r] : [r, r, 0, 0]);
            return { value: v, itemStyle: { borderRadius: radius } };
          }),
          itemStyle: Object.assign({
            color: horiz ? hgrad(c, 0.55, 1) : new (G().LinearGradient)(0, 0, 0, 1, [{ offset: 0, color: c }, { offset: 1, color: rgba(c, 0.45) }]),
          }, glow(ctx, c, 10)),
          label: showVals ? {
            show: n <= 60, position: stacked ? 'inside' : (horiz ? 'right' : 'top'),
            color: stacked ? '#fff' : ctx.text, fontSize: 11, fontWeight: 600, distance: 6,
            formatter: p => fmt(p.value),
          } : { show: false },
          emphasis: { focus: multi ? 'series' : 'none', itemStyle: { shadowBlur: 24 * ctx.glow + 6, shadowColor: rgba(c, 0.7) } },
          universalTransition: { enabled: true },
          animationDuration: 1000, animationEasing: 'elasticOut',
          animationDelay: idx => idx * 70 + si * 140,
        };
      }),
    });
  };

  B.lollipop = (a, ctx) => {
    const x = strs(a.x, 'x');
    const y = asSeries(a.y)[0][1];
    const c = col(ctx, 0);
    return base(ctx, {
      grid: grid(a, false),
      tooltip: tooltip(ctx, { trigger: 'item', formatter: p => head(x[p.dataIndex]) + row(c, a.y_label || 'value', fmtFull(y[p.dataIndex])) }),
      xAxis: axis(ctx, { pos: 'x', type: 'category', data: x, name: a.x_label }),
      yAxis: axis(ctx, { pos: 'y', type: 'value', name: a.y_label }),
      dataZoom: zoom(ctx, x.length),
      series: [
        {
          type: 'bar', data: y, barWidth: 3, silent: true, tooltip: { show: false },
          itemStyle: { color: new (G().LinearGradient)(0, 0, 0, 1, [{ offset: 0, color: rgba(c, 0.9) }, { offset: 1, color: rgba(c, 0.1) }]) },
          animationDuration: 900, animationDelay: i => i * 80,
          universalTransition: { enabled: true },
        },
        {
          type: 'scatter', name: a.y_label || 'value', data: y, symbolSize: 18, z: 3,
          itemStyle: Object.assign({ color: c, borderColor: ctx.surface, borderWidth: 3 }, glow(ctx, c, 20)),
          label: { show: y.length <= 30, position: 'top', distance: 8, color: ctx.text, fontWeight: 600, fontSize: 11, formatter: p => fmt(p.value) },
          emphasis: { scale: 1.5 },
          animationDuration: 900, animationEasing: 'elasticOut', animationDelay: i => i * 80 + 450,
        },
      ],
    });
  };

  B.waterfall = (a, ctx) => {
    const labels = strs(a.labels, 'labels');
    const vals = nums(a.values, 'values').map(v => v || 0);
    checkLen('values', vals, labels.length);
    const steps = [];
    let run = 0;
    labels.forEach((l, i) => { const from = run; run += vals[i]; steps.push({ label: l, from, to: run, delta: vals[i], kind: vals[i] >= 0 ? 'up' : 'down' }); });
    if (a.show_total !== false) steps.push({ label: 'Total', from: 0, to: run, delta: run, kind: 'total' });
    const color = k => (k === 'up' ? ctx.up : k === 'down' ? ctx.down : ctx.total);
    const baseD = [], pos = [], neg = [];
    steps.forEach(s => {
      const lo = Math.min(s.from, s.to), hi = Math.max(s.from, s.to);
      if (lo >= 0) { baseD.push(lo); pos.push(hi - lo); neg.push(0); }
      else if (hi <= 0) { baseD.push(hi); pos.push(0); neg.push(lo - hi); }
      else { baseD.push(0); pos.push(hi); neg.push(lo); }
    });
    const item = (v, i) => {
      const c = color(steps[i].kind);
      return { value: v, itemStyle: Object.assign({ color: new (G().LinearGradient)(0, 0, 0, 1, [{ offset: 0, color: c }, { offset: 1, color: rgba(c, 0.6) }]), borderRadius: 5 }, glow(ctx, c, 10)) };
    };
    const lbl = which => ({
      show: true, position: which === 'pos' ? 'top' : 'bottom', color: ctx.text, fontWeight: 600, fontSize: 11,
      formatter: p => {
        const s = steps[p.dataIndex];
        const owner = pos[p.dataIndex] !== 0 ? 'pos' : 'neg';
        if (owner !== which) return '';
        return s.kind === 'total' ? fmt(s.delta) : (s.delta >= 0 ? '+' : '') + fmt(s.delta);
      },
    });
    const anim = { animationDuration: 800, animationEasing: 'cubicOut', animationDelay: i => i * 160 };
    return base(ctx, {
      grid: grid(a, false),
      tooltip: tooltip(ctx, {
        trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: rgba(ctx.colors[0], 0.06) } },
        formatter: ps => {
          const s = steps[ps[0].dataIndex];
          return head(s.label) + (s.kind === 'total'
            ? row(ctx.total, 'total', fmtFull(s.to))
            : row(color(s.kind), 'change', (s.delta >= 0 ? '+' : '') + fmtFull(s.delta)) + row(null, 'running total', fmtFull(s.to)));
        },
      }),
      xAxis: axis(ctx, { pos: 'x', type: 'category', data: steps.map(s => s.label) }),
      yAxis: axis(ctx, { pos: 'y', type: 'value', name: a.y_label }),
      series: [
        { type: 'bar', stack: 'wf', data: baseD, itemStyle: { color: 'transparent' }, emphasis: { disabled: true }, silent: true, barCategoryGap: '30%', ...anim },
        { type: 'bar', stack: 'wf', name: 'increase', data: pos.map(item), label: lbl('pos'), ...anim },
        { type: 'bar', stack: 'wf', name: 'decrease', data: neg.map(item), label: lbl('neg'), ...anim },
        {
          type: 'line', data: steps.map(s => s.to), step: 'end', symbol: 'none', silent: true, z: 1,
          lineStyle: { color: ctx.faint, width: 1, type: [3, 4] }, animationDuration: 1600, tooltip: { show: false },
        },
      ],
    });
  };

  B.pareto = (a, ctx) => {
    const cats = strs(a.categories, 'categories');
    const v = nums(a.values, 'values');
    checkLen('values', v, cats.length);
    const pairs = cats.map((c, i) => [c, v[i] || 0]).sort((p, q) => q[1] - p[1]);
    const total = pairs.reduce((s, p) => s + p[1], 0) || 1;
    let run = 0;
    const cum = pairs.map(p => (run += p[1], +(run / total * 100).toFixed(2)));
    const c0 = col(ctx, 0), c1 = col(ctx, 5);
    return base(ctx, {
      legend: legend(ctx, { data: ['value', 'cumulative %'] }),
      grid: grid(a, true, { right: 18 }),
      tooltip: tooltip(ctx, { trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: rgba(c0, 0.06) } } }),
      xAxis: axis(ctx, { pos: 'x', type: 'category', data: pairs.map(p => p[0]) }),
      yAxis: [
        axis(ctx, { pos: 'y', type: 'value', name: a.y_label }),
        axis(ctx, { pos: 'y', type: 'value', suffix: '%', extra: { min: 0, max: 100, splitLine: { show: false }, nameTextStyle: { align: 'right' } } }),
      ],
      series: [
        {
          name: 'value', type: 'bar', data: pairs.map(p => p[1]), barMaxWidth: 54,
          itemStyle: Object.assign({ color: new (G().LinearGradient)(0, 0, 0, 1, [{ offset: 0, color: c0 }, { offset: 1, color: rgba(c0, 0.4) }]), borderRadius: [7, 7, 0, 0] }, glow(ctx, c0, 10)),
          label: { show: pairs.length <= 20, position: 'top', color: ctx.text, fontSize: 11, fontWeight: 600, formatter: p => fmt(p.value) },
          animationDelay: i => i * 90, animationEasing: 'elasticOut', universalTransition: { enabled: true },
        },
        {
          name: 'cumulative %', type: 'line', yAxisIndex: 1, data: cum, smooth: 0.2, symbol: 'circle', symbolSize: 8,
          lineStyle: Object.assign({ width: 3, color: c1 }, glow(ctx, c1, 14)), itemStyle: { color: c1, borderColor: ctx.surface, borderWidth: 2 },
          tooltip: { valueFormatter: v => v.toFixed(1) + '%' },
          markLine: {
            silent: true, symbol: 'none', label: { formatter: '80%', color: ctx.warn, position: 'insideEndTop' },
            lineStyle: { color: ctx.warn, type: [6, 4], width: 1.5 }, data: [{ yAxis: 80 }],
          },
          animationDuration: 1800, animationDelay: 400,
        },
      ],
    });
  };

  // ---------------------------------------------------------------- distributions
  function quant(s, p) {
    const pos = Math.min(s.length - 1, Math.max(0, s.length * p - 0.5)), lo = Math.floor(pos), hi = Math.ceil(pos);
    return s[lo] + (s[hi] - s[lo]) * (pos - lo);
  }
  function stats(vals) {
    const s = vals.filter(isNum).sort((p, q) => p - q);
    if (!s.length) return null;
    const q1 = quant(s, 0.25), med = quant(s, 0.5), q3 = quant(s, 0.75), iqr = q3 - q1;
    const lf = q1 - 1.5 * iqr, uf = q3 + 1.5 * iqr;
    const inl = s.filter(v => v >= lf && v <= uf);
    const mean = s.reduce((t, v) => t + v, 0) / s.length;
    const sd = Math.sqrt(s.reduce((t, v) => t + (v - mean) ** 2, 0) / Math.max(1, s.length - 1));
    return {
      s, n: s.length, min: s[0], max: s[s.length - 1], q1, med, q3, mean, sd,
      lo: inl.length ? inl[0] : q1, hi: inl.length ? inl[inl.length - 1] : q3,
      out: s.filter(v => v < lf || v > uf),
    };
  }
  const statRows = (st, c) => row(c, 'n', st.n) + row(null, 'max', fmtFull(st.max)) + row(null, 'Q3', fmtFull(+st.q3.toFixed(4)))
    + row(null, 'median', fmtFull(+st.med.toFixed(4))) + row(null, 'Q1', fmtFull(+st.q1.toFixed(4))) + row(null, 'min', fmtFull(st.min))
    + row(null, 'mean', fmtFull(+st.mean.toFixed(4)));

  B.histogram = (a, ctx) => {
    const ser = asSeries(a.values, 'value', 'values').map(([n, v]) => [n, v.filter(isNum)]);
    const all = ser.flatMap(s => s[1]);
    if (!all.length) throw new Error('"values" has no numbers');
    let min = Math.min(...all), max = Math.max(...all);
    if (min === max) { min -= 0.5; max += 0.5; }
    const nb = Math.max(1, Math.min(200, Math.round(num(a.bins) > 0 ? num(a.bins) : Math.ceil(Math.log2(all.length) + 1))));
    const w = (max - min) / nb;
    const dec = Math.max(0, Math.min(4, -Math.floor(Math.log10(w)) + 1));
    const f = v => (+v.toFixed(dec)).toLocaleString('en-US', { maximumFractionDigits: dec });
    const ranges = Array.from({ length: nb }, (_, i) => `${f(min + i * w)} – ${f(min + (i + 1) * w)}`);
    const labels = Array.from({ length: nb }, (_, i) => f(min + i * w));
    const multi = ser.length > 1;
    return base(ctx, {
      legend: multi ? legend(ctx) : undefined,
      grid: grid({ x_label: a.x_label, y_label: a.y_label || 'Count' }, multi),
      tooltip: tooltip(ctx, { trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: rgba(ctx.colors[0], 0.06) } },
        formatter: ps => head(ranges[ps[0].dataIndex]) + ps.map(p => row(p.color && typeof p.color === 'string' ? p.color : col(ctx, p.seriesIndex), multi ? p.seriesName : (a.y_label || 'count'), fmtFull(p.value))).join('') }),
      xAxis: axis(ctx, { pos: 'x', type: 'category', data: labels, name: a.x_label, extra: { axisLabel: { color: ctx.sub, fontSize: 11, hideOverlap: true } } }),
      yAxis: axis(ctx, { pos: 'y', type: 'value', name: a.y_label || 'Count', extra: { minInterval: 1 } }),
      series: ser.map(([name, vals], si) => {
        const counts = new Array(nb).fill(0);
        vals.forEach(v => { counts[Math.min(nb - 1, Math.max(0, Math.floor((v - min) / w)))]++; });
        const c = col(ctx, si);
        return {
          name, type: 'bar', data: counts, barCategoryGap: '4%', barGap: '-100%',
          itemStyle: Object.assign({ color: new (G().LinearGradient)(0, 0, 0, 1, [{ offset: 0, color: rgba(c, multi ? 0.8 : 1) }, { offset: 1, color: rgba(c, multi ? 0.3 : 0.45) }]), borderRadius: [4, 4, 0, 0] }, glow(ctx, c, 8)),
          emphasis: { focus: multi ? 'series' : 'none' },
          animationDelay: i => i * 35 + si * 200, animationEasing: 'elasticOut', animationDuration: 1100,
        };
      }),
    });
  };

  B.box = (a, ctx) => {
    const gs = groups(a.data);
    const st = gs.map(([, v]) => stats(v));
    if (st.some(s => !s)) throw new Error('every group needs at least one number');
    const names = gs.map(g => g[0]);
    const showPts = a.show_points !== false;
    return base(ctx, {
      grid: grid({ y_label: a.y_label }, false),
      tooltip: tooltip(ctx, { trigger: 'item' }),
      xAxis: axis(ctx, { pos: 'x', type: 'category', data: names }),
      yAxis: axis(ctx, { pos: 'y', type: 'value', name: a.y_label, scale: true }),
      series: [
        {
          type: 'boxplot', boxWidth: [18, 64],
          data: st.map((s, i) => {
            const c = col(ctx, i);
            return {
              value: [s.lo, s.q1, s.med, s.q3, s.hi],
              itemStyle: Object.assign({ color: rgba(c, 0.22), borderColor: c, borderWidth: 2 }, glow(ctx, c, 12)),
            };
          }),
          tooltip: { formatter: p => head(names[p.dataIndex]) + statRows(st[p.dataIndex], col(ctx, p.dataIndex)) },
          emphasis: { itemStyle: { borderWidth: 3 } },
          animationDuration: 1200, animationEasing: 'elasticOut', animationDelay: i => i * 120,
          universalTransition: { enabled: true },
        },
        {
          type: 'scatter', name: 'outliers', symbolSize: 8,
          data: showPts ? st.flatMap((s, i) => s.out.map(v => ({ value: [i, v], itemStyle: { color: col(ctx, i), opacity: 0.9 } }))) : [],
          tooltip: { formatter: p => head(names[p.value[0]]) + row(col(ctx, p.value[0]), 'outlier', fmtFull(p.value[1])) },
          animationDelay: 900,
        },
        {
          type: 'scatter', name: 'mean', symbol: 'diamond', symbolSize: 10, z: 4,
          data: st.map((s, i) => ({ value: [i, +s.mean.toFixed(6)], itemStyle: { color: ctx.surface, borderColor: col(ctx, i), borderWidth: 2 } })),
          tooltip: { formatter: p => head(names[p.value[0]]) + row(col(ctx, p.value[0]), 'mean', fmtFull(p.value[1])) },
          animationDelay: 1000,
        },
      ],
    });
  };

  function kde(st) {
    const iqr = st.q3 - st.q1;
    let h = 0.9 * Math.min(st.sd || Infinity, iqr > 0 ? iqr / 1.34 : (st.sd || Infinity)) * Math.pow(st.n, -0.2);
    if (!(h > 0 && isFinite(h))) h = st.sd > 0 ? st.sd * 0.5 : (Math.abs(st.mean) * 0.1 || 1);
    const lo = st.min - 2 * h, hi = st.max + 2 * h, N = 72, pts = [];
    let dmax = 0;
    for (let i = 0; i <= N; i++) {
      const y = lo + (hi - lo) * i / N;
      let d = 0;
      for (const v of st.s) { const u = (y - v) / h; d += Math.exp(-0.5 * u * u); }
      d /= st.n * h * Math.sqrt(2 * Math.PI);
      pts.push([y, d]);
      if (d > dmax) dmax = d;
    }
    return { pts, dmax: dmax || 1, lo, hi };
  }

  B.violin = (a, ctx) => {
    const gs = groups(a.data);
    const V = gs.map(([name, v]) => {
      const st = stats(v);
      if (!st) throw new Error(`group "${name}" has no numbers`);
      return { name, st, k: kde(st) };
    });
    return base(ctx, {
      grid: grid({ y_label: a.y_label }, false),
      tooltip: tooltip(ctx, { trigger: 'item', formatter: p => head(V[p.dataIndex].name) + statRows(V[p.dataIndex].st, col(ctx, p.dataIndex)) }),
      xAxis: axis(ctx, { pos: 'x', type: 'category', data: V.map(g => g.name) }),
      yAxis: axis(ctx, { pos: 'y', type: 'value', name: a.y_label, scale: true }),
      series: [{
        type: 'custom', name: 'violin',
        data: V.map((g, i) => [i, g.k.lo, g.k.hi]),
        encode: { x: 0, y: [1, 2] },
        renderItem: (params, api) => {
          const i = params.dataIndex, g = V[i], c = col(ctx, i);
          const cx = api.coord([i, g.st.med])[0];
          const half = api.size([1, 0])[0] * 0.38;
          const Y = v => api.coord([i, v])[1];
          const right = g.k.pts.map(([y, d]) => [cx + (d / g.k.dmax) * half, Y(y)]);
          const left = right.map(([px, py]) => [2 * cx - px, py]).reverse();
          const mid = Y(g.st.med);
          return {
            type: 'group', originX: cx, originY: mid, scaleX: 1, scaleY: 1,
            enterFrom: { scaleX: 0, scaleY: 0.2 },
            children: [
              {
                type: 'polygon', shape: { points: right.concat(left), smooth: 0.25 },
                style: Object.assign({ fill: new (G().LinearGradient)(0, 0, 1, 0, [{ offset: 0, color: rgba(c, 0.12) }, { offset: 0.5, color: rgba(c, 0.42) }, { offset: 1, color: rgba(c, 0.12) }]), stroke: c, lineWidth: 2 }, glow(ctx, c, 14)),
              },
              { type: 'line', shape: { x1: cx, y1: Y(g.st.lo), x2: cx, y2: Y(g.st.hi) }, style: { stroke: rgba(c, 0.9), lineWidth: 1.5 } },
              { type: 'rect', shape: { x: cx - 5, y: Y(g.st.q3), width: 10, height: Math.max(1, Y(g.st.q1) - Y(g.st.q3)), r: 4 }, style: { fill: c } },
              { type: 'line', shape: { x1: cx - half * 0.55, y1: Y(g.st.mean), x2: cx + half * 0.55, y2: Y(g.st.mean) }, style: { stroke: ctx.text, lineWidth: 1.5, lineDash: [4, 3], opacity: 0.8 } },
              { type: 'circle', shape: { cx, cy: mid, r: 4.5 }, style: { fill: ctx.surface, stroke: ctx.text, lineWidth: 2 } },
            ],
          };
        },
        animationDuration: 1200, animationEasing: 'elasticOut',
        universalTransition: { enabled: true },
      }],
    });
  };

  // ---------------------------------------------------------------- relationships
  function colorHandling(ctx, color, n) {
    if (!Array.isArray(color) || color.length < n) return { mode: 'none' };
    const ns = color.slice(0, n).map(num);
    if (ns.every(isNum)) {
      const mn = Math.min(...ns), mx = Math.max(...ns);
      return { mode: 'num', values: ns, min: mn, max: mx === mn ? mn + 1 : mx };
    }
    const cats = color.slice(0, n).map(v => String(v));
    return { mode: 'cat', values: cats, uniq: [...new Set(cats)] };
  }
  function visualMapNum(ctx, ch, dim) {
    return {
      type: 'continuous', min: ch.min, max: ch.max, dimension: dim, calculable: true, orient: 'vertical',
      right: 0, top: 'middle', itemHeight: 140, itemWidth: 12, precision: 2,
      inRange: { color: colorScale('viridis', ctx) },
      textStyle: { color: ctx.sub, fontSize: 11 }, handleStyle: { borderColor: ctx.text },
      formatter: v => fmt(+v),
    };
  }
  function xyAxes(ctx, a, xs) {
    const present = xs.filter(v => v != null && v !== '');
    const numeric = present.length > 0 && present.every(v => num(v) != null);
    return {
      numeric,
      xAxis: numeric
        ? axis(ctx, { pos: 'x', type: 'value', name: a.x_label, scale: true, extra: { splitLine: { show: true, lineStyle: { color: ctx.split, type: [4, 4] } } } })
        : axis(ctx, { pos: 'x', type: 'category', name: a.x_label, data: [...new Set(xs.map(String))] }),
      yAxis: axis(ctx, { pos: 'y', type: 'value', name: a.y_label, scale: true }),
    };
  }

  B.scatter = (a, ctx) => {
    const xs = list(a.x, 'x'), ys = nums(a.y, 'y');
    const n = Math.min(xs.length, ys.length);
    const ax = xyAxes(ctx, a, xs.slice(0, n));
    const X = i => (ax.numeric ? num(xs[i]) : String(xs[i]));
    const labels = Array.isArray(a.labels) ? a.labels.map(String) : null;
    const ch = colorHandling(ctx, a.color, n);
    const pt = i => {
      const d = { value: [X(i), ys[i]] };
      if (ch.mode === 'num') d.value.push(ch.values[i]);
      if (labels && labels[i] != null) d.name = labels[i];
      return d;
    };
    const tip = p => {
      const v = p.value;
      return (p.name ? head(p.name) : '') + row(p.color, a.x_label || 'x', fmtFull(v[0])) + row(null, a.y_label || 'y', fmtFull(v[1]))
        + (ch.mode === 'num' ? row(null, 'color', fmtFull(v[2])) : '') + (ch.mode === 'cat' ? row(null, 'group', esc(p.seriesName)) : '');
    };
    const mk = (name, idxs, c) => ({
      name, type: 'scatter', data: idxs.map(pt), symbolSize: 13,
      itemStyle: Object.assign({ color: c, opacity: 0.88, borderColor: rgba(ctx.surface, 0.9), borderWidth: 1 }, glow(ctx, c || ctx.colors[0], 12)),
      emphasis: { focus: 'series', scale: 1.7 },
      animationDuration: 900, animationEasing: 'elasticOut', animationDelay: i => Math.min(i * 22, 1200),
      universalTransition: { enabled: true },
    });
    const all = Array.from({ length: n }, (_, i) => i);
    const series = ch.mode === 'cat'
      ? ch.uniq.map((g, gi) => mk(g, all.filter(i => ch.values[i] === g), col(ctx, gi)))
      : [mk('data', all, ch.mode === 'num' ? undefined : col(ctx, 0))];
    if (a.trendline && ax.numeric && n >= 2) {
      const px = all.map(i => num(xs[i])), mx = px.reduce((s, v) => s + v, 0) / n, my = ys.slice(0, n).reduce((s, v) => s + (v || 0), 0) / n;
      const den = px.reduce((s, v) => s + (v - mx) ** 2, 0) || 1;
      const slope = px.reduce((s, v, i) => s + (v - mx) * ((ys[i] || 0) - my), 0) / den;
      const b0 = my - slope * mx, lo = Math.min(...px), hi = Math.max(...px);
      const tc = col(ctx, 5);
      series.push({
        name: 'trend', type: 'line', data: [[lo, slope * lo + b0], [hi, slope * hi + b0]], symbol: 'none', z: 1, itemStyle: { color: tc },
        lineStyle: Object.assign({ color: tc, width: 2.5, type: [8, 5] }, glow(ctx, tc, 10)),
        tooltip: { formatter: () => head('least-squares trend') + row(tc, 'slope', fmtFull(+slope.toFixed(4))) + row(null, 'intercept', fmtFull(+b0.toFixed(4))) },
        animationDuration: 1400, animationDelay: 700,
      });
    }
    const hasLegend = ch.mode === 'cat' || !!a.trendline;
    return base(ctx, {
      legend: hasLegend ? legend(ctx, ch.mode === 'num' ? { data: ['trend'] } : null) : undefined,
      grid: grid(a, hasLegend, ch.mode === 'num' ? { right: 70 } : null),
      tooltip: tooltip(ctx, { trigger: 'item', formatter: tip }),
      xAxis: ax.xAxis, yAxis: ax.yAxis,
      visualMap: ch.mode === 'num' ? visualMapNum(ctx, ch, 2) : undefined,
      series,
    });
  };

  B.bubble = (a, ctx) => {
    const xs = list(a.x, 'x'), ys = nums(a.y, 'y'), sz = nums(a.size, 'size');
    const n = Math.min(xs.length, ys.length, sz.length);
    if (!n) throw new Error('no points');
    const ax = xyAxes(ctx, a, xs.slice(0, n));
    const mx = Math.max(...sz.slice(0, n).map(v => Math.abs(v || 0))) || 1;
    const labels = Array.isArray(a.labels) ? a.labels.map(String) : null;
    const ch = colorHandling(ctx, a.color, n);
    const diam = s => Math.max(8, 64 * Math.sqrt(Math.abs(s || 0) / mx));
    const mkItem = (i, c) => {
      const d = { value: [ax.numeric ? num(xs[i]) : String(xs[i]), ys[i], sz[i]], name: labels ? labels[i] : undefined };
      if (diam(sz[i]) < 34) d.label = { position: 'right', color: ctx.text, textShadowBlur: 0, fontWeight: 500 };
      if (ch.mode === 'num') d.value.push(ch.values[i]);
      else d.itemStyle = { color: new (G().RadialGradient)(0.35, 0.3, 0.9, [{ offset: 0, color: mix(c, '#ffffff', 0.45) }, { offset: 1, color: rgba(c, 0.78) }]) };
      return d;
    };
    const tip = p => (p.name ? head(p.name) : '') + row(p.color && typeof p.color === 'string' ? p.color : null, a.x_label || 'x', fmtFull(p.value[0]))
      + row(null, a.y_label || 'y', fmtFull(p.value[1])) + row(null, 'size', fmtFull(p.value[2])) + (ch.mode === 'num' ? row(null, 'color', fmtFull(p.value[3])) : '');
    const all = Array.from({ length: n }, (_, i) => i).sort((p, q) => Math.abs(sz[q] || 0) - Math.abs(sz[p] || 0)); // big first, small on top
    const mk = (name, idxs, ci) => ({
      name, type: 'scatter', data: idxs.map(i => mkItem(i, col(ctx, ci === null ? i : ci))),
      symbolSize: v => diam(v[2]),
      itemStyle: { opacity: 0.92, borderColor: rgba(ctx.dark ? '#ffffff' : '#0F1B2D', 0.25), borderWidth: 1, shadowBlur: 18 * ctx.glow, shadowColor: 'rgba(0,0,0,0.25)' },
      label: { show: !!labels && n <= 24, formatter: p => p.name || '', color: '#fff', fontSize: 11, fontWeight: 600, textShadowColor: 'rgba(0,0,0,.5)', textShadowBlur: ctx.dark ? 4 : 2 },
      emphasis: { focus: 'self', scale: 1.15, label: { show: true } },
      animationDuration: 1300, animationEasing: 'elasticOut', animationDelay: i => Math.min(i * 60, 1500),
      universalTransition: { enabled: true },
    });
    const series = ch.mode === 'cat'
      ? ch.uniq.map((g, gi) => mk(g, all.filter(i => ch.values[i] === g), gi))
      : [mk('data', all, ch.mode === 'num' ? 0 : null)];
    return base(ctx, {
      legend: ch.mode === 'cat' ? legend(ctx) : undefined,
      grid: grid(a, ch.mode === 'cat', Object.assign({ top: 40 }, ch.mode === 'num' ? { right: 70 } : {})),
      tooltip: tooltip(ctx, { trigger: 'item', formatter: tip }),
      xAxis: ax.xAxis, yAxis: ax.yAxis,
      visualMap: ch.mode === 'num' ? visualMapNum(ctx, ch, 3) : undefined,
      series,
    });
  };

  function heatmapOption(ctx, a, z, xl, yl, o) {
    const data = [];
    let mn = Infinity, mx = -Infinity;
    z.forEach((r, ri) => (Array.isArray(r) ? r : [r]).forEach((v, ci) => {
      const n = num(v);
      if (n == null) return;
      data.push({ value: [ci, ri, n] });
      if (n < mn) mn = n; if (n > mx) mx = n;
    }));
    if (!data.length) throw new Error('"z" has no numbers');
    if (o.min != null) mn = o.min; if (o.max != null) mx = o.max;
    if (mn === mx) mx = mn + 1;
    const cells = xl.length * yl.length;
    const scale = o.colors;
    data.forEach(d => {
      const t = Math.min(1, Math.max(0, (d.value[2] - mn) / (mx - mn)));
      const bg = sampleScale(scale, t);
      d.label = { color: luminance(bg) > 0.2 ? '#1D1D1F' : '#FFFFFF' };
    });
    return base(ctx, {
      grid: grid(a, false, { bottom: (a.x_label ? 36 : 12) + 44, right: 16 }),
      tooltip: tooltip(ctx, {
        trigger: 'item',
        formatter: p => head(`${yl[p.value[1]]}  ×  ${xl[p.value[0]]}`) + row(null, o.valueName || 'value', fmtFull(p.value[2])),
      }),
      xAxis: axis(ctx, { pos: 'x', type: 'category', data: xl, name: a.x_label, extra: { splitArea: { show: false }, axisLine: { show: false } } }),
      yAxis: axis(ctx, { pos: 'y', type: 'category', data: yl, name: a.y_label, extra: { inverse: true, axisLine: { show: false } } }),
      visualMap: {
        min: mn, max: mx, calculable: true, orient: 'horizontal', left: 'center', bottom: 0, itemWidth: 12, itemHeight: 220,
        inRange: { color: scale }, textStyle: { color: ctx.sub, fontSize: 11 }, formatter: v => fmt(+(+v).toFixed(2)),
        handleStyle: { borderColor: ctx.text },
      },
      series: [{
        type: 'heatmap', data,
        label: { show: o.showValues && cells <= 300, color: ctx.text, fontSize: cells > 100 ? 10 : 12, fontWeight: 600, formatter: p => fmt(p.value[2]) },
        itemStyle: { borderColor: ctx.surface, borderWidth: cells > 400 ? 1 : 3, borderRadius: cells > 400 ? 1 : 6 },
        emphasis: { itemStyle: { shadowBlur: 20, shadowColor: rgba(ctx.colors[0], 0.8), borderColor: ctx.colors[0], borderWidth: 2 } },
        animationDuration: 1000, animationDelay: i => Math.min(i * 12, 1400),
      }],
    });
  }

  B.heatmap = (a, ctx) => {
    const z = list(a.z, 'z');
    const rows = z.length, cols = Math.max(0, ...z.map(r => (Array.isArray(r) ? r.length : 1)));
    const xl = Array.from({ length: cols }, (_, i) => (Array.isArray(a.x) && a.x[i] != null ? String(a.x[i]) : String(i)));
    const yl = Array.from({ length: rows }, (_, i) => (Array.isArray(a.y) && a.y[i] != null ? String(a.y[i]) : String(i)));
    return heatmapOption(ctx, a, z, xl, yl, { colors: colorScale(a.colorscale, ctx), showValues: a.show_values !== false });
  };

  B.correlation = (a, ctx) => {
    if (!a.data || typeof a.data !== 'object' || Array.isArray(a.data)) throw new Error('"data" must be {column: list}');
    const names = Object.keys(a.data);
    const cols = names.map(k => list(a.data[k], k).map(num));
    const n = Math.min(...cols.map(c => c.length));
    const corr = (p, q) => {
      const P = p.slice(0, n), Q = q.slice(0, n);
      const mp = P.reduce((s, v) => s + (v || 0), 0) / n, mq = Q.reduce((s, v) => s + (v || 0), 0) / n;
      let cov = 0, vp = 0, vq = 0;
      for (let i = 0; i < n; i++) { const dp = (P[i] || 0) - mp, dq = (Q[i] || 0) - mq; cov += dp * dq; vp += dp * dp; vq += dq * dq; }
      return vp && vq ? cov / Math.sqrt(vp * vq) : 0;
    };
    const z = cols.map(r => cols.map(c => (+corr(r, c).toFixed(2)) || 0));
    const neutral = '#F5F5F7';
    return heatmapOption(ctx, a, z, names, names, { colors: ['#8E8E93', neutral, '#1D1D1F'], min: -1, max: 1, showValues: true, valueName: 'Pearson r' });
  };

  // ---------------------------------------------------------------- part of whole
  function pieOption(a, ctx, donut) {
    const labels = strs(a.labels, 'labels');
    const vals = nums(a.values, 'values');
    checkLen('values', vals, labels.length);
    const data = labels.map((l, i) => ({ name: l, value: vals[i] }));
    const total = vals.reduce((s, v) => s + (v > 0 ? v : 0), 0);
    const many = data.length > 8;
    return base(ctx, {
      tooltip: tooltip(ctx, { trigger: 'item', formatter: p => head(p.name) + row(p.color, 'value', fmtFull(p.value)) + row(null, 'share', p.percent + '%') }),
      legend: many ? legend(ctx, { orient: 'vertical', left: 'auto', right: 0, top: 'middle', type: 'scroll' }) : undefined,
      graphic: donut ? [{
        type: 'group', left: many ? '38%' : 'center', top: 'middle', bounding: 'raw',
        children: [
          { type: 'text', style: { text: fmt(total), x: 0, y: -10, textAlign: 'center', textVerticalAlign: 'middle', fill: ctx.text, font: '700 28px "Space Grotesk", Inter, sans-serif' } },
          { type: 'text', style: { text: 'TOTAL', x: 0, y: 18, textAlign: 'center', textVerticalAlign: 'middle', fill: ctx.sub, font: '600 11px Inter, sans-serif' } },
        ],
        scaleX: 1, scaleY: 1, enterFrom: { scaleX: 0.4, scaleY: 0.4 },
      }] : undefined,
      series: [{
        type: 'pie', data, radius: donut ? ['54%', '78%'] : [0, '74%'], center: [many ? '40%' : '50%', '52%'],
        padAngle: data.length > 1 ? 1.2 : 0, minAngle: 1,
        itemStyle: { borderRadius: donut ? 8 : 6, borderColor: ctx.surface, borderWidth: 2 },
        label: { show: true, color: ctx.text, fontSize: 12, formatter: p => `{n|${p.name}}\n{p|${p.percent}%}`, rich: { n: { color: ctx.sub, fontSize: 12, lineHeight: 16 }, p: { color: ctx.text, fontWeight: 700, fontSize: 13 } } },
        labelLine: { length: 12, length2: 14, smooth: 0.3, lineStyle: { color: ctx.axis } },
        minShowLabelAngle: many ? 6 : 0, avoidLabelOverlap: true,
        emphasis: { scale: true, scaleSize: 10, itemStyle: { shadowBlur: 30 * ctx.glow + 8, shadowColor: 'rgba(0,0,0,0.35)' } },
        color: ctx.colors,
        animationType: 'expansion', animationDuration: 1500, animationEasing: 'cubicOut',
        universalTransition: { enabled: true },
      }],
    });
  }
  B.pie = (a, ctx) => pieOption(a, ctx, !!a.donut);
  B.donut = (a, ctx) => pieOption(a, ctx, true);

  function buildTree(a, fill) {
    const labels = strs(a.labels, 'labels'), parents = list(a.parents, 'parents'), vals = nums(a.values, 'values');
    checkLen('parents', parents, labels.length); checkLen('values', vals, labels.length);
    const nodes = labels.map((l, i) => ({ name: l, value: vals[i] || 0, children: [] }));
    const byName = new Map();
    nodes.forEach(nd => { if (!byName.has(nd.name)) byName.set(nd.name, nd); });
    const roots = [];
    nodes.forEach((nd, i) => {
      const p = parents[i];
      const pn = p == null || p === '' ? null : byName.get(String(p));
      if (pn && pn !== nd) pn.children.push(nd); else roots.push(nd);
    });
    let seen = 0;
    const fix = nd => {
      seen++;
      if (seen > nodes.length) throw new Error('cycle in parents');
      if (!nd.children.length) { delete nd.children; return nd.value; }
      const sum = nd.children.reduce((s, ch) => s + fix(ch), 0);
      nd.value = Math.max(nd.value, sum);  // branchvalues="total"
      if (fill && nd.value - sum > nd.value * 1e-9) {
        nd.children.push({ name: '', value: nd.value - sum, _filler: true, silent: true, tooltip: { show: false },
          itemStyle: { color: 'transparent', borderWidth: 0, gapWidth: 0 }, label: { show: false }, upperLabel: { show: false }, emphasis: { disabled: true } });
      }
      return nd.value;
    };
    roots.forEach(fix);
    if (seen < nodes.length) throw new Error('cycle in parents');
    return roots;
  }

  function paintTree(nodes, ctx, startColor) {
    nodes.forEach((nd, i) => {
      if (nd._filler) return;
      const c = startColor ? startColor(i) : col(ctx, i);
      nd.itemStyle = Object.assign({}, nd.itemStyle, { color: c });
      if (nd.children) {
        nd.itemStyle.borderColor = mix(c, '#000000', ctx.dark ? 0.35 : 0.12);  // treemap header strip
        const k = nd.children.length;
        paintTree(nd.children, ctx, j => mix(c, j % 2 ? '#ffffff' : (ctx.dark ? '#000000' : '#ffffff'), j % 2 ? 0.10 + 0.14 * j / k : 0.06 + 0.12 * j / k));
      }
    });
  }

  B.treemap = (a, ctx) => {
    let roots = buildTree(a, true), rootName = a.title || 'All';
    if (roots.length === 1 && roots[0].children) { rootName = roots[0].name; roots = roots[0].children; }
    paintTree(roots, ctx);
    return base(ctx, {
      tooltip: tooltip(ctx, {
        trigger: 'item', formatter: p => {
          const path = (p.treePathInfo || []).slice(1).map(t => t.name).join(' › ');
          return head(path || p.name) + row(p.color, 'value', fmtFull(p.value));
        },
      }),
      series: [{
        type: 'treemap', name: rootName, data: roots, top: 6, left: 4, right: 4, bottom: 34,
        roam: false, nodeClick: 'zoomToNode', squareRatio: 0.5 * (1 + Math.sqrt(5)),
        breadcrumb: { show: true, bottom: 2, left: 'center', height: 24, itemStyle: { color: ctx.surface2, borderColor: ctx.border, borderWidth: 1, textStyle: { color: ctx.text, fontSize: 12 } }, emphasis: { itemStyle: { color: rgba(ctx.colors[0], 0.25), textStyle: { color: ctx.text } } } },
        label: { show: true, color: '#fff', fontSize: 13, fontWeight: 600, formatter: p => `${p.name}\n{v|${fmt(p.value)}}`, rich: { v: { fontSize: 12, fontWeight: 400, color: 'rgba(255,255,255,.85)', lineHeight: 20 } }, textShadowColor: 'rgba(0,0,0,.35)', textShadowBlur: 4 },
        upperLabel: { show: true, height: 26, color: '#fff', fontWeight: 700, fontSize: 12, formatter: p => `${p.name}  ·  ${fmt(p.value)}`, textShadowColor: 'rgba(0,0,0,.4)', textShadowBlur: 4 },
        itemStyle: { borderColor: ctx.surface, borderWidth: 2, gapWidth: 2, borderRadius: 6 },
        levels: [
          { itemStyle: { borderWidth: 0, gapWidth: 4 }, upperLabel: { color: ctx.text, textShadowBlur: 0 } },
          { itemStyle: { gapWidth: 2, borderWidth: 3 } },
          { itemStyle: { gapWidth: 1, borderWidth: 2 } },
        ],
        emphasis: { itemStyle: { shadowBlur: 20, shadowColor: 'rgba(0,0,0,.4)' } },
        animationDuration: 1200, animationEasing: 'quinticInOut', animationDurationUpdate: 900,
        universalTransition: { enabled: true },
      }],
    });
  };

  B.sunburst = (a, ctx) => {
    const roots = buildTree(a);
    if (roots.length === 1 && roots[0].children) {
      roots[0].itemStyle = { color: ctx.surface2 };
      roots[0].label = { color: ctx.text, rotate: 0 };
      paintTree(roots[0].children, ctx);
    } else paintTree(roots, ctx);
    return base(ctx, {
      tooltip: tooltip(ctx, {
        trigger: 'item', formatter: p => {
          const path = (p.treePathInfo || []).slice(1).map(t => t.name).join(' › ');
          return head(path || p.name) + row(typeof p.color === 'string' ? p.color : null, 'value', fmtFull(p.value));
        },
      }),
      series: [{
        type: 'sunburst', data: roots, radius: [0, '92%'], center: ['50%', '51%'], sort: null,
        nodeClick: 'rootToNode', emphasis: { focus: 'ancestor' },
        itemStyle: { borderRadius: 6, borderWidth: 2, borderColor: ctx.surface },
        label: { rotate: 'radial', color: '#fff', fontSize: 11, fontWeight: 600, minAngle: 9, textShadowColor: 'rgba(0,0,0,.35)', textShadowBlur: 3 },
        levels: [{}, {}, { itemStyle: { opacity: 0.92 } }, { itemStyle: { opacity: 0.8 } }, { itemStyle: { opacity: 0.7 } }],
        animationDuration: 1500, animationEasing: 'cubicOut', animationDurationUpdate: 900,
        universalTransition: { enabled: true },
      }],
    });
  };

  B.funnel = (a, ctx) => {
    const st = strs(a.stages, 'stages'), v = nums(a.values, 'values');
    checkLen('values', v, st.length);
    const first = v[0] || 1;
    const mx = Math.max(...v.map(x => x || 0)) || 1;
    return base(ctx, {
      tooltip: tooltip(ctx, {
        trigger: 'item', formatter: p => {
          const i = p.dataIndex;
          return head(p.name) + row(p.color, 'value', fmtFull(p.value)) + row(null, 'of first', (p.value / first * 100).toFixed(1) + '%')
            + (i > 0 && v[i - 1] ? row(null, 'of previous', (p.value / v[i - 1] * 100).toFixed(1) + '%') : '');
        },
      }),
      series: [{
        type: 'funnel', sort: 'none', min: 0, max: mx, minSize: '6%', maxSize: '100%', gap: 4,
        left: '6%', right: '26%', top: 10, bottom: 10, funnelAlign: 'center',
        data: st.map((s, i) => {
          const c = col(ctx, i);
          const narrow = (v[i] || 0) / mx < 0.3;
          return { name: s, value: v[i], label: narrow ? { position: 'right', color: ctx.text, textShadowBlur: 0, fontWeight: 500 } : undefined, itemStyle: Object.assign({ color: new (G().LinearGradient)(0, 0, 1, 0, [{ offset: 0, color: rgba(c, 0.7) }, { offset: 0.5, color: c }, { offset: 1, color: rgba(c, 0.7) }]) }, glow(ctx, c, 14)) };
        }),
        label: {
          show: true, position: 'inside', color: '#fff', fontSize: 13, fontWeight: 600, textBorderColor: 'transparent', textBorderWidth: 0, textShadowBlur: 0,
          formatter: p => `${p.name}  ·  ${fmt(p.value)}  ·  ${(p.value / first * 100).toFixed(p.value / first < 0.1 ? 1 : 0)}%`,
        },
        itemStyle: { borderWidth: 0 },
        emphasis: { label: { fontSize: 15 } },
        animationDuration: 1300, animationEasing: 'cubicOut',
        universalTransition: { enabled: true },
      }],
    });
  };

  // ---------------------------------------------------------------- special
  B.candlestick = (a, ctx) => {
    let d = strs(a.dates, 'dates');
    let o = nums(a.open, 'open'), h = nums(a.high, 'high'), l = nums(a.low, 'low'), c = nums(a.close, 'close');
    [['open', o], ['high', h], ['low', l], ['close', c]].forEach(([k, arr]) => checkLen(k, arr, d.length));
    let vol = Array.isArray(a.volume) && a.volume.length >= d.length ? a.volume.map(num) : null;
    const stamps = d.map(v => Date.parse(v));
    if (stamps.every(isFinite)) {
      const ord = d.map((_, i) => i).sort((p, q) => stamps[p] - stamps[q]);
      const pick = arr => ord.map(i => arr[i]);
      d = pick(d); o = pick(o); h = pick(h); l = pick(l); c = pick(c); if (vol) vol = pick(vol);
    }
    const up = i => (c[i] ?? 0) >= (o[i] ?? 0);
    const many = d.length > 40;
    const gridMain = vol ? { left: 14, right: 22, top: a.y_label ? 34 : 16, height: '58%', containLabel: true }
      : { left: 14, right: 22, top: a.y_label ? 34 : 16, bottom: many ? 44 : 12, containLabel: true };
    const grids = [gridMain];
    if (vol) grids.push({ left: 14, right: 22, top: '74%', bottom: many ? 40 : 12, containLabel: true });
    const xa = [axis(ctx, { pos: 'x', type: 'category', data: d, extra: { boundaryGap: true, axisLabel: { color: ctx.sub, fontSize: 11, hideOverlap: true, show: !vol } } })];
    const ya = [axis(ctx, { pos: 'y', type: 'value', name: a.y_label || 'Price', scale: true })];
    if (vol) {
      xa.push(axis(ctx, { pos: 'x', type: 'category', data: d, extra: { gridIndex: 1, boundaryGap: true } }));
      ya.push(axis(ctx, { pos: 'y', type: 'value', extra: { gridIndex: 1, splitNumber: 2, axisLabel: { color: ctx.faint, fontSize: 10, formatter: fmt } } }));
    }
    const series = [{
      name: 'price', type: 'candlestick', data: d.map((_, i) => [o[i], c[i], l[i], h[i]]), barMaxWidth: 18,
      itemStyle: { color: ctx.up, color0: ctx.down, borderColor: ctx.up, borderColor0: ctx.down, borderWidth: 1.5 },
      emphasis: { itemStyle: { borderWidth: 2.5, shadowBlur: 14, shadowColor: 'rgba(0,0,0,.4)' } },
      animationDuration: 1100, animationDelay: i => i * 25,
    }];
    if (vol) series.push({
      name: 'volume', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, barMaxWidth: 18,
      data: vol.map((v, i) => ({ value: v, itemStyle: { color: rgba(up(i) ? ctx.up : ctx.down, 0.45), borderRadius: [3, 3, 0, 0] } })),
      animationDuration: 900, animationDelay: i => i * 25 + 200,
    });
    return base(ctx, {
      grid: grids, xAxis: xa, yAxis: ya, series,
      axisPointer: { link: [{ xAxisIndex: 'all' }], label: { backgroundColor: ctx.surface2, color: ctx.text } },
      dataZoom: zoom(ctx, d.length, vol ? [0, 1] : [0]),
      tooltip: tooltip(ctx, {
        trigger: 'axis', axisPointer: { type: 'cross', lineStyle: { color: ctx.border }, crossStyle: { color: ctx.border } },
        formatter: ps => {
          const i = ps[0].dataIndex, cc = up(i) ? ctx.up : ctx.down;
          const chg = o[i] ? ((c[i] - o[i]) / o[i] * 100) : 0;
          return head(d[i]) + row(cc, 'open', fmtFull(o[i])) + row(null, 'high', fmtFull(h[i])) + row(null, 'low', fmtFull(l[i]))
            + row(null, 'close', fmtFull(c[i])) + row(null, 'change', `<span style="color:${cc}">${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%</span>`)
            + (vol ? row(null, 'volume', fmt(vol[i])) : '');
        },
      }),
    });
  };

  B.gauge = (a, ctx) => {
    const value = num(a.value);
    if (value == null) throw new Error('"value" must be a number');
    let mn = num(a.min_value) ?? 0, mx = num(a.max_value) ?? 100;
    if (mx <= mn) mx = mn + 1;
    const target = num(a.target);
    const c0 = col(ctx, 0), c1 = col(ctx, 1);
    const common = { type: 'gauge', min: mn, max: mx, startAngle: 215, endAngle: -35, radius: '88%', center: ['50%', '58%'] };
    const series = [
      {
        ...common, splitNumber: 5,
        progress: { show: true, width: 16, roundCap: true, itemStyle: Object.assign({ color: new (G().LinearGradient)(0, 0, 1, 0, [{ offset: 0, color: c1 }, { offset: 1, color: c0 }]) }, glow(ctx, c0, 22)) },
        axisLine: { roundCap: true, lineStyle: { width: 16, color: [[0.5, rgba(ctx.down, 0.22)], [0.8, rgba(ctx.warn, 0.22)], [1, rgba(ctx.up, 0.22)]] } },
        pointer: { show: true, length: '56%', width: 5, icon: 'path://M2.9,0.7L2.9,0.7c1.4,0,2.6,1.2,2.6,2.6v115c0,1.4-1.2,2.6-2.6,2.6l0,0c-1.4,0-2.6-1.2-2.6-2.6V3.3C0.3,1.9,1.4,0.7,2.9,0.7z', itemStyle: { color: ctx.text, shadowBlur: 10, shadowColor: 'rgba(0,0,0,.4)' } },
        anchor: { show: true, size: 18, showAbove: true, itemStyle: { color: ctx.surface, borderColor: c0, borderWidth: 4, shadowBlur: 12 * ctx.glow, shadowColor: c0 } },
        axisTick: { distance: -30, splitNumber: 5, length: 5, lineStyle: { color: ctx.faint, width: 1 } },
        splitLine: { distance: -34, length: 12, lineStyle: { color: ctx.sub, width: 2 } },
        axisLabel: { distance: -4, color: ctx.sub, fontSize: 12, formatter: v => fmt(+(+v).toFixed(2)) },
        title: { show: false },
        detail: { valueAnimation: true, offsetCenter: [0, '46%'], fontSize: 44, fontWeight: 700, fontFamily: '"Space Grotesk", Inter, sans-serif', color: ctx.text, formatter: v => fmt(+(+v).toFixed(2)) },
        data: [{ value }],
        animationDuration: 2200, animationEasing: 'cubicOut',
      },
    ];
    if (target != null) {
      series.push({
        ...common, z: 5,
        axisLine: { show: false }, splitLine: { show: false }, axisTick: { show: false }, axisLabel: { show: false }, progress: { show: false }, anchor: { show: false },
        pointer: { show: true, icon: 'triangle', length: 16, width: 14, offsetCenter: [0, '-84%'], itemStyle: { color: ctx.warn, shadowBlur: 10 * ctx.glow, shadowColor: ctx.warn } },
        title: { show: true, offsetCenter: [0, '74%'], color: ctx.sub, fontSize: 12 },
        detail: { show: true, offsetCenter: [0, '86%'], fontSize: 14, fontWeight: 600, color: ctx.warn, formatter: v => 'target ' + fmt(+(+v).toFixed(2)) },
        data: [{ value: target, name: '' }],
        animationDuration: 1800, animationEasing: 'elasticOut', tooltip: { show: false },
      });
    }
    return base(ctx, {
      tooltip: tooltip(ctx, { formatter: () => row(c0, 'value', fmtFull(value)) + (target != null ? row(ctx.warn, 'target', fmtFull(target)) : '') + row(null, 'range', `${fmtFull(mn)} – ${fmtFull(mx)}`) }),
      series,
    });
  };

  B.radar = (a, ctx) => {
    const cats = strs(a.categories, 'categories');
    const ser = asSeries(a.series, 'value', 'series');
    const allV = ser.flatMap(s => s[1]).filter(isNum);
    if (!allV.length) throw new Error('"series" has no numbers');
    const top = niceCeil(Math.max(...allV) * 1.08), bot = Math.min(0, Math.min(...allV));
    const fill = a.fill !== false;
    const multi = ser.length > 1;
    return base(ctx, {
      legend: multi ? legend(ctx) : undefined,
      tooltip: tooltip(ctx, { trigger: 'item' }),
      radar: {
        indicator: cats.map(name => ({ name, max: top, min: bot })), shape: 'polygon', radius: '66%', center: ['50%', multi ? '56%' : '53%'], splitNumber: 4,
        axisName: { color: ctx.text, fontSize: 12, fontWeight: 500 },
        splitLine: { lineStyle: { color: ctx.axis } },
        splitArea: { areaStyle: { color: [rgba(ctx.colors[0], 0.015), rgba(ctx.colors[0], 0.05)] } },
        axisLine: { lineStyle: { color: ctx.axis } },
      },
      series: [{
        type: 'radar', symbol: 'circle', symbolSize: 7,
        data: ser.map(([name, vals], i) => {
          const c = col(ctx, i);
          return {
            name, value: cats.map((_, j) => vals[j] ?? null),
            lineStyle: Object.assign({ width: 2.5, color: c }, glow(ctx, c, 14)),
            itemStyle: { color: c, borderColor: ctx.surface, borderWidth: 2 },
            areaStyle: fill ? { color: new (G().RadialGradient)(0.5, 0.5, 0.8, [{ offset: 0, color: rgba(c, 0.08) }, { offset: 1, color: rgba(c, 0.38) }]) } : undefined,
          };
        }),
        emphasis: { lineStyle: { width: 4 }, areaStyle: { opacity: 0.9 } },
        animationDuration: 1500, animationEasing: 'elasticOut',
        universalTransition: { enabled: true },
      }],
    });
  };

  B.sankey = (a, ctx) => {
    const src = strs(a.sources, 'sources'), tgt = strs(a.targets, 'targets'), v = nums(a.values, 'values');
    checkLen('targets', tgt, src.length); checkLen('values', v, src.length);
    const names = [...new Set(src.concat(tgt))];
    const adj = new Map(names.map(n => [n, []]));
    const links = [];
    src.forEach((s, i) => {
      if (!(v[i] > 0)) return;
      if (s === tgt[i]) throw new Error(`self-loop on "${s}"`);
      adj.get(s).push(tgt[i]);
      links.push({ source: s, target: tgt[i], value: v[i] });
    });
    // ECharts sankey requires a DAG; Plotly fallback handles cycles.
    const state = new Map();
    const visit = n => {
      state.set(n, 1);
      for (const m of adj.get(n)) {
        if (state.get(m) === 1) throw new Error('flows contain a cycle');
        if (!state.get(m)) visit(m);
      }
      state.set(n, 2);
    };
    names.forEach(n => { if (!state.get(n)) visit(n); });
    return base(ctx, {
      tooltip: tooltip(ctx, {
        trigger: 'item', formatter: p => p.dataType === 'edge'
          ? head(`${p.data.source}  →  ${p.data.target}`) + row(null, 'flow', fmtFull(p.data.value))
          : head(p.name) + row(typeof p.color === 'string' ? p.color : null, 'total', fmtFull(p.value)),
      }),
      series: [{
        type: 'sankey', left: 8, right: 110, top: 14, bottom: 14, nodeWidth: 14, nodeGap: 14, nodeAlign: 'justify',
        layoutIterations: 64, draggable: true, emphasis: { focus: 'adjacency' }, labelLayout: { hideOverlap: true },
        data: names.map((n, i) => ({ name: n, itemStyle: Object.assign({ color: col(ctx, i), borderRadius: 3 }, glow(ctx, col(ctx, i), 10)) })),
        links,
        lineStyle: { color: 'gradient', curveness: 0.5, opacity: ctx.dark ? 0.32 : 0.4 },
        itemStyle: { borderWidth: 0 },
        label: { color: ctx.text, fontSize: 12, fontWeight: 500, formatter: p => ((ctx.width || 1000) < 520 ? p.name : `${p.name}  {v|${fmt(p.value)}}`), rich: { v: { color: ctx.sub, fontSize: 11 } } },
        animationDuration: 1600, animationEasing: 'cubicInOut',
      }],
    });
  };

  B.gantt = (a, ctx) => {
    const tasks = strs(a.tasks, 'tasks'), s0 = list(a.starts, 'starts'), e0 = list(a.ends, 'ends');
    checkLen('starts', s0, tasks.length); checkLen('ends', e0, tasks.length);
    const isDate = typeof s0[0] === 'string' && num(s0[0]) == null;
    const T = v => (isDate ? parseDate(v) : num(v));
    const gArr = Array.isArray(a.groups) && a.groups.length >= tasks.length ? a.groups.map(String) : null;
    const gNames = gArr ? [...new Set(gArr)] : ['tasks'];
    const DAY = 864e5;
    const dur = (s, e) => (isDate ? `${Math.round((e - s) / DAY)} days` : fmtFull(e - s));
    const item = i => [i, T(s0[i]), T(e0[i]), tasks[i]];
    const series = gNames.map((g, gi) => ({
      type: 'custom', name: g,
      data: tasks.map((_, i) => i).filter(i => !gArr || gArr[i] === g).map(item),
      encode: { x: [1, 2], y: 0 },
      itemStyle: { color: col(ctx, gi) },
      renderItem: (params, api) => {
        const r = api.value(0), sP = api.coord([api.value(1), r]), eP = api.coord([api.value(2), r]);
        const bh = api.size([0, 1])[1] * 0.56;
        const cs = params.coordSys;
        const shape = G().clipRectByRect({ x: sP[0], y: sP[1] - bh / 2, width: Math.max(eP[0] - sP[0], 4), height: bh }, { x: cs.x, y: cs.y, width: cs.width, height: cs.height });
        if (!shape) return null;
        const c = col(ctx, gi);
        const label = tasks[r];
        const fits = shape.width > label.length * 7 + 20;
        return {
          type: 'group', originX: shape.x, originY: shape.y + bh / 2, scaleX: 1,
          enterFrom: { scaleX: 0 },
          children: [
            { type: 'rect', shape: Object.assign({}, shape, { r: Math.min(8, bh / 2) }), style: Object.assign({ fill: new (G().LinearGradient)(0, 0, 1, 0, [{ offset: 0, color: c }, { offset: 1, color: rgba(c, 0.6) }]) }, glow(ctx, c, 14)) },
            fits ? { type: 'text', style: { text: label, x: shape.x + 10, y: shape.y + shape.height / 2, verticalAlign: 'middle', fill: '#fff', font: '600 12px Inter, sans-serif', textShadowColor: 'rgba(0,0,0,.35)', textShadowBlur: 3 } } : null,
          ].filter(Boolean),
        };
      },
      animationDuration: 1000, animationEasing: 'cubicOut', animationDelay: i => i * 120,
    }));
    return base(ctx, {
      legend: gArr ? legend(ctx) : undefined,
      grid: grid({}, !!gArr),
      tooltip: tooltip(ctx, {
        trigger: 'item', formatter: p => {
          const i = p.value[0];
          return head(tasks[i]) + row(col(ctx, p.seriesIndex), gArr ? gArr[i] : 'task', '') + row(null, 'start', esc(s0[i])) + row(null, 'end', esc(e0[i])) + row(null, 'duration', dur(p.value[1], p.value[2]));
        },
      }),
      xAxis: axis(ctx, { pos: 'x', type: isDate ? 'time' : 'value', scale: true, extra: { splitLine: { show: true, lineStyle: { color: ctx.split, type: [4, 4] } }, axisLine: { show: true, lineStyle: { color: ctx.axis } } } }),
      yAxis: axis(ctx, { pos: 'y', type: 'category', data: tasks, extra: { inverse: true, axisLine: { show: false }, splitLine: { show: true, lineStyle: { color: ctx.split } } } }),
      dataZoom: [{ type: 'inside', xAxisIndex: 0, zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: false, moveOnMouseMove: true, filterMode: 'weakFilter' }],
      series,
    });
  };

  // ================================================================== type switching (morph)
  const CAT_TOOLS = ['bar', 'line', 'area', 'lollipop', 'pie', 'donut', 'funnel', 'pareto', 'radar'];
  function toCat(tool, a) {
    const base = { title: a.title, x_label: a.x_label, y_label: a.y_label };
    switch (tool) {
      case 'bar': case 'line': case 'area': return { ...base, cats: strs(a.x, 'x'), series: asSeries(a.y) };
      case 'lollipop': return { ...base, cats: strs(a.x, 'x'), series: [asSeries(a.y)[0]] };
      case 'pie': case 'donut': return { ...base, cats: strs(a.labels, 'labels'), series: [['value', nums(a.values, 'values')]] };
      case 'funnel': return { ...base, cats: strs(a.stages, 'stages'), series: [['value', nums(a.values, 'values')]] };
      case 'pareto': return { ...base, cats: strs(a.categories, 'categories'), series: [['value', nums(a.values, 'values')]] };
      case 'radar': return { ...base, cats: strs(a.categories, 'categories'), series: asSeries(a.series, 'value', 'series') };
    }
    return null;
  }
  const seriesObj = ser => (ser.length === 1 && ser[0][0] === 'value' ? ser[0][1] : Object.fromEntries(ser));
  function fromCat(target, c) {
    const [kind, flag] = target.split(':');
    const t = { title: c.title, x_label: c.x_label, y_label: c.y_label };
    const one = c.series[0][1];
    switch (kind) {
      case 'bar': return ['bar', { ...t, x: c.cats, y: seriesObj(c.series), mode: flag === 'stack' ? 'stack' : 'group', horizontal: flag === 'h' }];
      case 'line': return ['line', { ...t, x: c.cats, y: seriesObj(c.series), smooth: flag === 'smooth' }];
      case 'area': return ['area', { ...t, x: c.cats, y: seriesObj(c.series), stacked: flag === 'stack' }];
      case 'lollipop': return ['lollipop', { ...t, x: c.cats, y: one }];
      case 'pie': return ['pie', { title: c.title, labels: c.cats, values: one }];
      case 'donut': return ['donut', { title: c.title, labels: c.cats, values: one }];
      case 'funnel': return ['funnel', { title: c.title, stages: c.cats, values: one }];
      case 'pareto': return ['pareto', { title: c.title, y_label: c.y_label, categories: c.cats, values: one }];
      case 'radar': return ['radar', { title: c.title, categories: c.cats, series: seriesObj(c.series) }];
    }
    return null;
  }
  const VARIANT_LABELS = {
    'bar': 'Bars', 'bar:stack': 'Stacked bars', 'bar:h': 'Horizontal bars', 'line': 'Line', 'line:smooth': 'Smooth line',
    'area': 'Area', 'area:stack': 'Stacked area', 'lollipop': 'Lollipop', 'pie': 'Pie', 'donut': 'Donut', 'funnel': 'Funnel',
    'pareto': 'Pareto', 'radar': 'Radar', 'treemap': 'Treemap', 'sunburst': 'Sunburst', 'box': 'Box plot', 'violin': 'Violin',
    'histogram': 'Histogram', 'scatter': 'Scatter', 'bubble': 'Bubble',
  };
  /** Variants the original (tool, args) can be morphed into. */
  function variants(tool, a) {
    try {
      if (CAT_TOOLS.includes(tool)) {
        const c = toCat(tool, a);
        const multi = c.series.length > 1;
        const nonNeg = c.series.every(s => s[1].every(v => v == null || v >= 0));
        const out = multi ? ['bar', 'bar:stack', 'bar:h', 'line', 'line:smooth', 'area', 'area:stack']
          : ['bar', 'bar:h', 'line', 'line:smooth', 'area', 'lollipop'];
        if (!multi && nonNeg) out.push('pie', 'donut', 'funnel', 'pareto');
        if (c.cats.length >= 3) out.push('radar');
        return out;
      }
      if (tool === 'treemap' || tool === 'sunburst') return ['treemap', 'sunburst'];
      if (tool === 'box' || tool === 'violin' || tool === 'histogram') return ['box', 'violin', 'histogram'];
      if (tool === 'bubble') return ['bubble', 'scatter'];
    } catch (e) { /* not convertible */ }
    return [];
  }
  /** Convert original (tool, args) into a variant -> [tool, args]. */
  function convert(tool, a, variant) {
    if (CAT_TOOLS.includes(tool)) return fromCat(variant, toCat(tool, a));
    if (variant === 'treemap' || variant === 'sunburst') return [variant, a];
    if (['box', 'violin', 'histogram'].includes(variant)) {
      const data = tool === 'histogram' ? Object.fromEntries(asSeries(a.values, 'value', 'values')) : a.data;
      if (variant === 'histogram') return ['histogram', { title: a.title, values: data, bins: a.bins, x_label: a.y_label, y_label: 'Count' }];
      return [variant, { title: a.title, data, y_label: tool === 'histogram' ? a.x_label : a.y_label }];
    }
    if (variant === 'scatter') return ['scatter', { title: a.title, x: a.x, y: a.y, labels: a.labels, color: a.color, x_label: a.x_label, y_label: a.y_label }];
    if (variant === 'bubble') return ['bubble', a];
    return [tool, a];
  }

  // ================================================================== table view
  function table(tool, a) {
    if (tool === 'heatmap') {
      const z = list(a.z, 'z'), cols = Math.max(...z.map(r => r.length));
      const xl = Array.isArray(a.x) ? a.x : Array.from({ length: cols }, (_, i) => i);
      const yl = Array.isArray(a.y) ? a.y : z.map((_, i) => i);
      return { cols: [''].concat(xl.map(String)), rows: z.map((r, i) => [String(yl[i] ?? i)].concat(r)) };
    }
    if (tool === 'gauge') return { cols: ['value', 'min', 'max', 'target'], rows: [[a.value, a.min_value ?? 0, a.max_value ?? 100, a.target ?? '–']] };
    const cols = [], data = [];
    const add = (name, arr) => { cols.push(name); data.push(arr); };
    Object.entries(a).forEach(([k, v]) => {
      if (Array.isArray(v) && v.every(x => x == null || typeof x !== 'object')) add(k, v);
      else if (v && typeof v === 'object' && !Array.isArray(v)) Object.entries(v).forEach(([n, arr]) => { if (Array.isArray(arr)) add(Object.keys(v).length > 1 || k === 'data' ? n : k, arr); });
    });
    const n = Math.max(0, ...data.map(d => d.length));
    return { cols, rows: Array.from({ length: n }, (_, i) => data.map(d => (d[i] === undefined ? '' : d[i]))) };
  }

  /** Rough "how much data" counter for the card subtitle. */
  function countValues(a) {
    let n = 0;
    const walk = v => { if (Array.isArray(v)) v.forEach(walk); else if (v && typeof v === 'object') Object.values(v).forEach(walk); else if (typeof v === 'number' || (typeof v === 'string' && /^\d{4}-\d{1,2}-\d{1,2}/.test(v))) n++; };
    Object.entries(a).forEach(([k, v]) => { if (!['bins', 'min_value', 'max_value'].includes(k)) walk(v); });
    return n;
  }

  function build(tool, args, ctx) {
    const fn = B[tool];
    if (!fn) throw new Error(`no renderer for "${tool}"`);
    return fn(args || {}, ctx);
  }

  global.JarvisCharts = {
    build, theme, variants, convert, table, countValues, fmt, fmtFull,
    TOOLS: Object.keys(B), DEFAULT_TITLES, VARIANT_LABELS,
    _internal: { stats, kde, buildTree, asSeries },
  };
})(window);
