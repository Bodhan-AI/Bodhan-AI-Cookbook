/* Chart catalog: icons, labels, gallery demo data (sent through /api/render — no LLM call), prompt ideas. */
(function (global) {
  'use strict';

  // deterministic pseudo-random numbers so demos look the same every time
  function rng(seed) {
    return function () {
      seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  const R = rng(42);
  const gauss = (mu, sd) => { let u = 0, v = 0; while (!u) u = R(); while (!v) v = R(); return mu + sd * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); };
  const round = (v, d = 1) => +v.toFixed(d);
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  // candlestick walk
  const cDates = [], cO = [], cH = [], cL = [], cC = [], cV = [];
  let px = 182;
  for (let i = 0; i < 45; i++) {
    const d = new Date(2026, 8, 1 + i);
    cDates.push(d.toISOString().slice(0, 10));
    const o = px, c = o + gauss(0.3, 3.2);
    cO.push(round(o, 2)); cC.push(round(c, 2));
    cH.push(round(Math.max(o, c) + Math.abs(gauss(0, 1.6)), 2)); cL.push(round(Math.min(o, c) - Math.abs(gauss(0, 1.6)), 2));
    cV.push(Math.round(800 + Math.abs(gauss(0, 420)) + Math.abs(c - o) * 160));
    px = c;
  }
  const heights = Array.from({ length: 48 }, () => round(gauss(172, 9)));
  const weights = heights.map(h => round(h * 0.9 - 85 + gauss(0, 6)));
  const ages = heights.map(() => Math.round(20 + R() * 45));
  const corrBase = Array.from({ length: 40 }, () => gauss(0, 1));

  const DEMOS = {
    line: { title: 'Monthly revenue, 2025 vs 2026 ($K)', x: months, y: { '2025': [42, 45, 51, 49, 56, 61, 64, 62, 68, 72, 79, 88], '2026': [50, 55, 59, 64, 70, 77, 81, 86, 90, 97, 104, 115] }, smooth: true, y_label: '$K' },
    area: { title: 'Website traffic by source (weekly sessions)', x: Array.from({ length: 16 }, (_, i) => `W${i + 1}`), y: { Organic: [12, 13, 13, 15, 16, 18, 17, 19, 21, 22, 24, 23, 25, 27, 29, 31].map(v => v * 1000), Social: [5, 6, 8, 7, 9, 11, 10, 12, 11, 13, 14, 16, 15, 17, 18, 20].map(v => v * 1000), Paid: [8, 8, 9, 9, 10, 9, 11, 10, 12, 12, 11, 13, 12, 14, 13, 15].map(v => v * 1000) }, stacked: true },
    band: (() => { const x = Array.from({ length: 18 }, (_, i) => `Q${(i % 4) + 1} '${22 + Math.floor(i / 4)}`); let v = 100; const y = x.map((_, i) => (v += 4 + Math.sin(i / 2) * 3, round(v))); return { title: 'Demand forecast with 90% confidence band', x, y, lower: y.map((v, i) => round(v - 3 - i * 0.9)), upper: y.map((v, i) => round(v + 3 + i * 0.9)), y_label: 'Units (K)' }; })(),
    bar: { title: 'Smartphone shipments by brand (millions)', x: ['Samsung', 'Apple', 'Xiaomi', 'Transsion', 'Oppo', 'vivo'], y: { Q1: [60.1, 57.9, 41.8, 28.5, 25.2, 22.4], Q2: [53.9, 45.2, 42.5, 25.0, 25.9, 23.7] }, y_label: 'Millions' },
    lollipop: { title: 'Developer survey: most-loved languages (%)', x: ['Rust', 'Elixir', 'TypeScript', 'Python', 'Go', 'Kotlin', 'Swift', 'C#'], y: [83, 73, 72, 69, 66, 62, 59, 57], y_label: '%' },
    waterfall: { title: 'FY26 profit bridge ($M)', labels: ['Revenue', 'COGS', 'Gross margin adj.', 'R&D', 'Sales & marketing', 'G&A', 'Other income', 'Tax'], values: [480, -190, 12, -85, -70, -32, 18, -31], y_label: '$M' },
    pareto: { title: 'Customer support tickets by root cause', categories: ['Login issues', 'Billing', 'Slow app', 'Crashes', 'Feature request', 'Data export', 'Notifications', 'Other'], values: [412, 288, 190, 141, 77, 49, 31, 22] },
    histogram: { title: 'Order delivery time distribution', values: Array.from({ length: 400 }, () => round(Math.max(0.5, gauss(3.2, 1.1)), 2)), bins: 24, x_label: 'Days', y_label: 'Orders' },
    box: { title: 'API latency by region (ms)', data: { 'us-east': Array.from({ length: 60 }, () => round(Math.max(5, gauss(48, 12)))).concat([140, 155]), 'eu-west': Array.from({ length: 60 }, () => round(Math.max(5, gauss(62, 15)))).concat([170]), 'ap-south': Array.from({ length: 60 }, () => round(Math.max(5, gauss(95, 22)))), 'sa-east': Array.from({ length: 60 }, () => round(Math.max(5, gauss(110, 28)))).concat([240, 260]) }, y_label: 'ms' },
    violin: { title: 'Exam scores by teaching method', data: { Lecture: Array.from({ length: 120 }, () => round(Math.min(100, Math.max(20, gauss(64, 11))))), Flipped: Array.from({ length: 120 }, () => round(Math.min(100, Math.max(20, R() < 0.5 ? gauss(58, 7) : gauss(82, 6))))), 'Project-based': Array.from({ length: 120 }, () => round(Math.min(100, Math.max(20, gauss(74, 9))))), Online: Array.from({ length: 120 }, () => round(Math.min(100, Math.max(20, gauss(61, 15))))) }, y_label: 'Score' },
    scatter: { title: 'Height vs weight (colored by age)', x: heights, y: weights, color: ages, trendline: true, x_label: 'Height (cm)', y_label: 'Weight (kg)' },
    bubble: { title: 'Wealth & health of nations (bubble = population)', x: [65000, 48000, 12500, 2400, 42000, 11000, 9000, 34000, 2200, 46000, 15000, 7600], y: [79, 81, 78, 70, 84, 76, 75, 83, 67, 82, 73, 72], size: [335, 84, 1410, 1430, 125, 216, 145, 52, 223, 68, 128, 278], labels: ['USA', 'Germany', 'China', 'India', 'Japan', 'Brazil', 'Russia', 'Korea', 'Nigeria', 'UK', 'Mexico', 'Indonesia'], x_label: 'GDP per capita ($)', y_label: 'Life expectancy (yrs)' },
    heatmap: { title: 'App activity by weekday and hour', z: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((_, d) => Array.from({ length: 12 }, (_, h) => Math.round((d < 5 ? 40 : 25) + 50 * Math.exp(-((h - (d < 5 ? 6 : 8)) ** 2) / 8) + R() * 15))), x: Array.from({ length: 12 }, (_, i) => `${(i * 2).toString().padStart(2, '0')}:00`), y: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'], colorscale: 'Blues' },
    correlation: { title: 'Correlation of marketing metrics', data: { Spend: corrBase.map(v => round(50 + v * 10)), Clicks: corrBase.map(v => round(400 + v * 80 + gauss(0, 30))), Signups: corrBase.map(v => round(40 + v * 6 + gauss(0, 6))), Churn: corrBase.map(v => round(10 - v * 1.5 + gauss(0, 2))), NPS: corrBase.map(() => round(gauss(45, 8))) } },
    pie: { title: 'Global cloud market share', labels: ['AWS', 'Azure', 'Google Cloud', 'Alibaba', 'Oracle', 'Others'], values: [31, 25, 11, 4, 3, 26] },
    donut: { title: 'Household budget allocation', labels: ['Housing', 'Food', 'Transport', 'Savings', 'Health', 'Leisure'], values: [1650, 620, 380, 750, 290, 410] },
    treemap: { title: 'Company revenue by division and product ($M)', labels: ['Acme', 'Cloud', 'Devices', 'Services', 'Compute', 'Storage', 'AI', 'Phones', 'Laptops', 'Wearables', 'Consulting', 'Support'], parents: ['', 'Acme', 'Acme', 'Acme', 'Cloud', 'Cloud', 'Cloud', 'Devices', 'Devices', 'Devices', 'Services', 'Services'], values: [1000, 480, 340, 180, 210, 120, 150, 190, 110, 40, 120, 60] },
    sunburst: { title: 'World population by continent and country (M)', labels: ['World', 'Asia', 'Africa', 'Europe', 'Americas', 'India', 'China', 'Indonesia', 'Nigeria', 'Ethiopia', 'Egypt', 'Russia', 'Germany', 'UK', 'USA', 'Brazil', 'Mexico'], parents: ['', 'World', 'World', 'World', 'World', 'Asia', 'Asia', 'Asia', 'Africa', 'Africa', 'Africa', 'Europe', 'Europe', 'Europe', 'Americas', 'Americas', 'Americas'], values: [3990, 2120, 452, 297, 1121, 1430, 1410, 280, 224, 127, 113, 144, 84, 69, 335, 216, 570] },
    funnel: { title: 'SaaS conversion funnel', stages: ['Visitors', 'Sign-ups', 'Activated', 'Trial → paid', 'Retained 90d'], values: [48000, 9600, 5100, 1450, 1120] },
    candlestick: { title: 'ACME stock — daily OHLC', dates: cDates, open: cO, high: cH, low: cL, close: cC, volume: cV, y_label: 'USD' },
    gauge: { title: 'Customer satisfaction score', value: 78, min_value: 0, max_value: 100, target: 85 },
    radar: { title: 'Flagship phones compared (0–10)', categories: ['Camera', 'Battery', 'Display', 'Performance', 'Price value', 'Build'], series: { 'Phone A': [9.2, 7.5, 9.0, 9.4, 6.0, 9.1], 'Phone B': [8.8, 8.6, 8.4, 8.1, 8.2, 7.9], 'Phone C': [8.1, 9.3, 7.8, 7.6, 9.0, 7.2] } },
    sankey: { title: 'Energy flow: sources → sectors (quads)', sources: ['Petroleum', 'Petroleum', 'Petroleum', 'Natural gas', 'Natural gas', 'Natural gas', 'Coal', 'Coal', 'Nuclear', 'Renewables', 'Renewables', 'Electricity', 'Electricity', 'Electricity'], targets: ['Transport', 'Industry', 'Residential', 'Electricity', 'Industry', 'Residential', 'Electricity', 'Industry', 'Electricity', 'Electricity', 'Transport', 'Residential', 'Commercial', 'Industry'], values: [25, 9, 1, 12, 10, 5, 8, 1, 8, 6, 2, 13, 12, 9] },
    gantt: { title: 'Product launch plan', tasks: ['Research', 'Design', 'Prototype', 'Backend build', 'Frontend build', 'QA & testing', 'Beta', 'Marketing', 'Launch'], starts: ['2026-01-05', '2026-01-26', '2026-02-16', '2026-03-02', '2026-03-09', '2026-04-20', '2026-05-11', '2026-04-06', '2026-06-08'], ends: ['2026-01-30', '2026-02-20', '2026-03-13', '2026-04-24', '2026-04-30', '2026-05-15', '2026-06-05', '2026-06-08', '2026-06-12'], groups: ['Discovery', 'Discovery', 'Discovery', 'Build', 'Build', 'Build', 'Release', 'Release', 'Release'] },
  };

  const I = s => s; // icon paths (24x24, stroke)
  const META = {
    line: { label: 'Line', family: 'Trend', icon: I('<path d="M3 3v18h18"/><path d="m7 15 4-5 3 3 6-7"/>') },
    area: { label: 'Area', family: 'Trend', icon: I('<path d="M3 3v18h18"/><path d="M7 17l4-5 3 3 6-6v8H7z" fill="currentColor" fill-opacity=".25"/>') },
    band: { label: 'Band', family: 'Trend', icon: I('<path d="M3 3v18h18"/><path d="M6 14c3-1 5-5 8-5s4 2 6 1"/><path d="M6 10c3-1 5-5 8-5s4 2 6 1M6 18c3-1 5-5 8-5s4 2 6 1" stroke-opacity=".45"/>') },
    bar: { label: 'Bar', family: 'Compare', icon: I('<path d="M3 3v18h18"/><rect x="7" y="11" width="3" height="7" rx="1"/><rect x="12" y="6" width="3" height="12" rx="1"/><rect x="17" y="13" width="3" height="5" rx="1"/>') },
    lollipop: { label: 'Lollipop', family: 'Compare', icon: I('<path d="M3 3v18h18"/><path d="M8 18v-7M13 18V8M18 18v-4"/><circle cx="8" cy="9.5" r="1.8"/><circle cx="13" cy="6.5" r="1.8"/><circle cx="18" cy="12.5" r="1.8"/>') },
    waterfall: { label: 'Waterfall', family: 'Compare', icon: I('<path d="M3 3v18h18"/><rect x="6" y="11" width="3" height="7" rx=".5"/><rect x="10.5" y="6" width="3" height="5" rx=".5"/><rect x="15" y="6" width="3" height="3" rx=".5"/>') },
    pareto: { label: 'Pareto', family: 'Compare', icon: I('<path d="M3 3v18h18"/><rect x="6" y="9" width="3" height="9" rx=".5"/><rect x="11" y="13" width="3" height="5" rx=".5"/><rect x="16" y="15.5" width="3" height="2.5" rx=".5"/><path d="M6 8c4-3 8-4 13-4"/>') },
    histogram: { label: 'Histogram', family: 'Distribution', icon: I('<path d="M3 21h18"/><path d="M4 21v-5h4v5M8 21V9h4v12M12 21V5h4v16M16 21v-9h4v9"/>') },
    box: { label: 'Box plot', family: 'Distribution', icon: I('<path d="M12 3v4M12 17v4M9 3h6M9 21h6"/><rect x="6" y="7" width="12" height="10" rx="1.5"/><path d="M6 12h12"/>') },
    violin: { label: 'Violin', family: 'Distribution', icon: I('<path d="M12 3c-2 3-5 5-5 9s3 6 5 9c2-3 5-5 5-9s-3-6-5-9z"/><path d="M12 8v8"/>') },
    scatter: { label: 'Scatter', family: 'Relationship', icon: I('<path d="M3 3v18h18"/><circle cx="8" cy="15" r="1.4"/><circle cx="11" cy="11" r="1.4"/><circle cx="15" cy="12.5" r="1.4"/><circle cx="17" cy="7" r="1.4"/><circle cx="13" cy="7" r="1.4"/>') },
    bubble: { label: 'Bubble', family: 'Relationship', icon: I('<circle cx="8" cy="15" r="4"/><circle cx="16" cy="8" r="5"/><circle cx="17.5" cy="18.5" r="2"/>') },
    heatmap: { label: 'Heatmap', family: 'Relationship', icon: I('<rect x="3" y="3" width="5" height="5" rx="1" fill="currentColor" fill-opacity=".9"/><rect x="9.5" y="3" width="5" height="5" rx="1" fill="currentColor" fill-opacity=".35"/><rect x="16" y="3" width="5" height="5" rx="1" fill="currentColor" fill-opacity=".6"/><rect x="3" y="9.5" width="5" height="5" rx="1" fill="currentColor" fill-opacity=".2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1" fill="currentColor" fill-opacity=".75"/><rect x="16" y="9.5" width="5" height="5" rx="1" fill="currentColor" fill-opacity=".3"/><rect x="3" y="16" width="5" height="5" rx="1" fill="currentColor" fill-opacity=".5"/><rect x="9.5" y="16" width="5" height="5" rx="1" fill="currentColor" fill-opacity=".15"/><rect x="16" y="16" width="5" height="5" rx="1" fill="currentColor" fill-opacity=".85"/>') },
    correlation: { label: 'Correlation', family: 'Relationship', icon: I('<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/><rect x="3" y="3" width="6" height="6" fill="currentColor" fill-opacity=".5"/><rect x="9" y="9" width="6" height="6" fill="currentColor" fill-opacity=".5"/><rect x="15" y="15" width="6" height="6" fill="currentColor" fill-opacity=".5"/>') },
    pie: { label: 'Pie', family: 'Part of whole', icon: I('<path d="M21.2 15.9A10 10 0 1 1 8 2.8"/><path d="M22 12A10 10 0 0 0 12 2v10z" fill="currentColor" fill-opacity=".25"/>') },
    donut: { label: 'Donut', family: 'Part of whole', icon: I('<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/><path d="M12 3v5M20.6 9.6l-4.8 1.2"/>') },
    treemap: { label: 'Treemap', family: 'Part of whole', icon: I('<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M12 3v18M12 11h9M16.5 11v10M3 14h9"/>') },
    sunburst: { label: 'Sunburst', family: 'Part of whole', icon: I('<circle cx="12" cy="12" r="3"/><path d="M12 5.5a6.5 6.5 0 0 1 6.5 6.5M12 18.5A6.5 6.5 0 0 1 5.5 12"/><path d="M12 1.5A10.5 10.5 0 0 1 22.5 12M1.5 12A10.5 10.5 0 0 1 8 2.3M16 21.7A10.5 10.5 0 0 1 9 22"/>') },
    funnel: { label: 'Funnel', family: 'Part of whole', icon: I('<path d="M3 4h18l-2 4H5zM6 10h12l-2 4H8zM9 16h6l-1.5 4h-3z"/>') },
    candlestick: { label: 'Candlestick', family: 'Special', icon: I('<path d="M7 3v4M7 15v6M17 2v4M17 13v5M12 6v3M12 17v3"/><rect x="5" y="7" width="4" height="8" rx="1"/><rect x="15" y="6" width="4" height="7" rx="1" fill="currentColor" fill-opacity=".35"/><rect x="10" y="9" width="4" height="8" rx="1"/>') },
    gauge: { label: 'Gauge', family: 'Special', icon: I('<path d="M4 18a9 9 0 1 1 16 0"/><path d="m12 14 4-5"/><circle cx="12" cy="14" r="1.6"/>') },
    radar: { label: 'Radar', family: 'Special', icon: I('<path d="M12 2l9.5 7-3.6 11H6.1L2.5 9z"/><path d="M12 7l5 4-2 6H9l-2-6z" fill="currentColor" fill-opacity=".25"/>') },
    sankey: { label: 'Sankey', family: 'Special', icon: I('<path d="M3 5h3c6 0 6 9 12 9h3M3 12h3c5 0 7-7 12-7h3M3 19h3c6 0 6-5 12-5h3"/>') },
    gantt: { label: 'Gantt', family: 'Special', icon: I('<path d="M3 3v18"/><rect x="5" y="5" width="9" height="3" rx="1.5"/><rect x="9" y="10.5" width="10" height="3" rx="1.5"/><rect x="13" y="16" width="7" height="3" rx="1.5"/>') },
  };

  const SUGGESTIONS = [
    { tool: 'line', text: 'Revenue and net income of Helios Dynamics from FY2019 to FY2025' },
    { tool: 'donut', text: 'FY2025 revenue share across the five business segments' },
    { tool: 'candlestick', text: 'Helios (HLDX) daily share price Sep-Nov 2025 with volume' },
    { tool: 'sankey', text: 'Energy flow FY2025 from sources to end uses' },
    { tool: 'treemap', text: 'R&D investment by area in FY2025' },
    { tool: 'heatmap', text: 'App activity by weekday and hour' },
    { tool: 'bar', text: 'Compliance incidents by category' },
    { tool: 'sunburst', text: 'Headcount by department and region' },
  ];

  const icon = (name, cls = '') => `<svg class="ico ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${(META[name] || META.bar).icon}</svg>`;

  global.JarvisCatalog = { DEMOS, META, SUGGESTIONS, icon };
})(window);
