// ui/map.js — EPIC MAP (#view-map)
// A layered 2.5D schematic SVG of the epic's world on a 1000×700 canvas: night-parchment base with paper
// grain, a stylised Himalaya along the north, Ganga & Yamuna as flowing rivers, narrative pathways from
// locations[].paths_to, and location markers with visited / current / locked states. Zoom (wheel, pinch,
// buttons, keys), pan (drag, arrows) and pointer parallax (off under reduced motion).
// Data is read strictly from V (DATA_CONTRACT §3.1); every optional field degrades gracefully.

import { esc, clamp, prefersReducedMotion, safeHex } from '../core/util.js';

const W = 1000, H = 700;
const MIN_K = 1, MAX_K = 5;
const DISCLAIMER = "A schematic map of the epic's world — not a historical or archaeological map";

const REGION_LABEL = {
  himalaya: 'The Himalaya', north: 'Northern Kingdoms', ganga_plain: 'The Gaṅgā Plain',
  west: 'Western Lands', east: 'Eastern Lands', south: 'Southern Lands',
};
const REGION_FALLBACK = {
  himalaya: [500, 40], north: [290, 196], ganga_plain: [600, 330], west: [150, 440], east: [870, 380], south: [520, 640],
};

// Small line glyphs, drawn in a ±9 box (stroke = currentColor).
const GLYPH = {
  palace: 'M-8 6H8M-6 6V0H6V6M-6 0Q0-10 6 0M0-8.5V-11.5M-2.5 6V2.5H2.5V6',
  city: 'M-8 6V-2H-5V-5H-2V-2H2V-5H5V-2H8V6ZM-1.6 6V2H1.6V6',
  forest: 'M0-9.5L5.5-.5H-5.5ZM0-4.5L7 5H-7ZM0 5V9',
  river: 'M-8-3.5q2-3 4 0t4 0t4 0t4 0M-8 2.5q2-3 4 0t4 0t4 0t4 0',
  battlefield: 'M-7-7L7 7M7-7L-7 7M-7-7h3.2M-7-7v3.2M7-7h-3.2M7-7v3.2M-4.5 7.5l3-3M4.5 7.5l-3-3',
  assembly: 'M-8.5-3L0-9.5L8.5-3ZM-6-3V5M-2-3V5M2-3V5M6-3V5M-8.5 6.5H8.5',
  mountain: 'M-9.5 6.5L-3.5-4.5L.5 1.5L4-3L9.5 6.5ZM-5.6-1L-3.5-4.5L-1.4-1',
  hermitage: 'M-7.5 6.5V-.5L0-7L7.5-.5V6.5ZM-2.2 6.5V2.2H2.2V6.5M4-8.5q1.6 1.6 0 3.2',
};
const LOCK = 'M-2.6-.5V-2.4a2.6 2.6 0 0 1 5.2 0V-.5M-3.6-.5H3.6V4H-3.6Z';
const ICON_NAMES = {
  palace: 'Palace', city: 'City', forest: 'Forest', river: 'River', battlefield: 'Battlefield',
  assembly: 'Assembly hall', mountain: 'Mountain', hermitage: 'Hermitage',
};

/* ------------------------------------------------------------------ shared tiny helpers */
const fmt = (n) => (Math.round(n * 10) / 10).toString();
const isNum = (n) => typeof n === 'number' && Number.isFinite(n);
const arr = (v) => (Array.isArray(v) ? v : []);
const accentOf = (l) => safeHex(l?.palette?.accent, ''); // only plain hex colours from data reach style attributes
function rng(seed) { // mulberry32 — deterministic decoration, same map every visit
  let a = seed >>> 0;
  return () => { a |= 0; a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
function hashStr(s) { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
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
// main.js owns body view classes; this only fills the gap if nobody applied the class after a view:change.
// `lastView` tracks the most recent request so a stale timeout can never undo a later view change.
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
function smoothPath(pts) { // Catmull-Rom through points → cubic Béziers
  if (!pts || pts.length < 2) return '';
  let d = `M${fmt(pts[0][0])},${fmt(pts[0][1])}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C${fmt(c1[0])},${fmt(c1[1])} ${fmt(c2[0])},${fmt(c2[1])} ${fmt(p2[0])},${fmt(p2[1])}`;
  }
  return d;
}
function chapterLabel(ch) {
  if (!ch) return '';
  if (ch.kind === 'sub') return 'Side path';
  return isNum(ch.chapter_order) ? `Chapter ${Math.round(ch.chapter_order)}` : 'Chapter';
}

/* ------------------------------------------------------------------ module */
export function initMap({ V, store, bus }) {
  const root = ensureSection('view-map');
  const locations = (V?.locations || []).filter((l) => l && l.id);
  const byId = V?.locationById instanceof Map ? V.locationById : new Map(locations.map((l) => [l.id, l]));
  const placed = locations.filter((l) => isNum(l?.map?.x) && isNum(l?.map?.y));
  const chapters = (V?.chapters || []).filter((c) => c && c.id);

  // chapters per location: union of chapter.location_id and location.chapters (merge-time list)
  const chaptersAt = new Map();
  for (const l of locations) chaptersAt.set(l.id, []);
  for (const c of chapters) { if (chaptersAt.has(c.location_id)) chaptersAt.get(c.location_id).push(c); }
  for (const l of locations) {
    for (const cid of arr(l.chapters)) {
      const c = V?.chapterById?.get?.(cid);
      if (c && !chaptersAt.get(l.id).includes(c)) chaptersAt.get(l.id).push(c);
    }
    chaptersAt.get(l.id).sort((a, b) => (a.chapter_order ?? 0) - (b.chapter_order ?? 0));
  }

  const st = () => store?.state || {};
  const isVisited = (id) => { try { return store?.isVisited ? !!store.isVisited(id) : (st().visited || []).includes(id); } catch { return false; } };
  const isUnlocked = (ch) => { try { return store?.isUnlocked ? !!store.isUnlocked(ch) : true; } catch { return true; } };
  const currentLocId = () => { const c = V?.chapterById?.get?.(st().current); return c ? c.location_id : null; };
  const locVisited = (id) => (chaptersAt.get(id) || []).some((c) => isVisited(c.id));
  const locLocked = (id) => { const cs = chaptersAt.get(id) || []; return cs.length > 0 && !cs.some((c) => isVisited(c.id) || isUnlocked(c)); };

  // story order for markers (keyboard tab order follows the epic)
  const firstOrder = (l) => { const cs = chaptersAt.get(l.id) || []; return cs.length ? (cs[0].chapter_order ?? 999) : 999; };
  const markersOrdered = [...placed].sort((a, b) => firstOrder(a) - firstOrder(b) || a.map.x - b.map.x);
  const unplaced = locations.length - placed.length;

  /* ---------------- DOM skeleton */
  root.classList.add('vw', 'mp');
  root.setAttribute('role', 'region');
  root.setAttribute('aria-labelledby', 'view-map-title');
  root.innerHTML = `
    <header class="vw-head">
      <div class="vw-titles">
        <p class="vw-eyebrow">Bhāratavarṣa as the epic imagines it</p>
        <h2 class="vw-title" id="view-map-title">The Epic Map</h2>
      </div>
      <div class="vw-tools mp-tools" role="group" aria-label="Map controls">
        <label class="mp-find"><span class="vh">Find a place</span>
          <select class="mp-select" aria-label="Find a place on the map">
            <option value="">Find a place…</option>
            ${[...placed].sort((a, b) => String(a.name).localeCompare(String(b.name))).map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join('')}
          </select>
        </label>
        <button type="button" class="vw-iconbtn" data-act="zoom-in" aria-label="Zoom in">+</button>
        <button type="button" class="vw-iconbtn" data-act="zoom-out" aria-label="Zoom out">−</button>
        <button type="button" class="vw-iconbtn" data-act="reset" aria-label="Reset map view">⟲</button>
      </div>
      <button type="button" class="vw-close" data-act="close" aria-label="Close the map and return to the journey"><span aria-hidden="true">×</span></button>
    </header>
    <div class="mp-body">
      <div class="mp-stage" tabindex="0" aria-label="Epic map. Drag or use arrow keys to pan, plus and minus to zoom, Tab to move between places." aria-describedby="mp-disclaimer">
        ${placed.length ? '' : `<p class="vw-empty">The map's places are still being charted — no location data has loaded yet.</p>`}
      </div>
      <details class="mp-legend">
        <summary>Legend</summary>
        <div class="mp-legend-body">
          <ul class="mp-legend-list" aria-label="Place types">
            ${Object.keys(GLYPH).map((k) => `<li><svg viewBox="-12 -12 24 24" aria-hidden="true"><path d="${GLYPH[k]}"/></svg>${ICON_NAMES[k]}</li>`).join('')}
          </ul>
          <ul class="mp-legend-list mp-legend-states" aria-label="Marker states">
            <li><span class="mp-key mp-key-current" aria-hidden="true"></span>You are here</li>
            <li><span class="mp-key mp-key-visited" aria-hidden="true"></span>Visited</li>
            <li><span class="mp-key mp-key-open" aria-hidden="true"></span>Not yet visited</li>
            <li><span class="mp-key mp-key-locked" aria-hidden="true"></span>Ahead in the story (still open to you)</li>
            <li><span class="mp-key mp-key-route" aria-hidden="true"></span>Narrative pathway</li>
            <li><span class="mp-key mp-key-route on" aria-hidden="true"></span>Pathway you have travelled</li>
            <li><span class="mp-key mp-key-river" aria-hidden="true"></span>River (schematic course)</li>
          </ul>
          ${unplaced > 0 ? `<p class="mp-note">${unplaced} place${unplaced > 1 ? 's are' : ' is'} not yet placed on the map.</p>` : ''}
        </div>
      </details>
      <p class="mp-disclaimer" id="mp-disclaimer">${esc(DISCLAIMER)}</p>
      <aside class="mp-card vw-sheet" hidden aria-labelledby="mp-card-title"></aside>
    </div>`;

  const regionBoxes = []; // user-space boxes of the region names (filled by buildSVG, read by declutter)
  const stage = root.querySelector('.mp-stage');
  const card = root.querySelector('.mp-card');
  const legend = root.querySelector('.mp-legend');
  // The legend starts folded to a 'Legend' pill on every screen: opened, it covers the western places and the compass.

  /* ---------------- SVG art */
  if (placed.length) stage.insertAdjacentHTML('afterbegin', buildSVG());
  const svg = stage.querySelector('svg');
  const viewport = svg?.querySelector('#mp-viewport');
  const layers = svg ? [...svg.querySelectorAll('[data-depth]')] : [];
  const markerEls = new Map();
  svg?.querySelectorAll('.mk').forEach((g) => markerEls.set(g.dataset.id, g));
  const routeEls = svg ? [...svg.querySelectorAll('.mp-route')] : [];
  const markerBodies = svg ? [...svg.querySelectorAll('.mk-body')] : [];
  const regionEls = svg ? [...svg.querySelectorAll('.mp-region:not(.hima)')] : [];

  function buildSVG() {
    const R = rng(1847);
    const parts = [];

    // --- defs: gradients, paper grain, hatch
    parts.push(`<defs>
      <radialGradient id="mp-g-base" cx="52%" cy="46%" r="75%">
        <stop offset="0" class="mp-stop-base-a"/><stop offset=".62" class="mp-stop-base-b"/><stop offset="1" class="mp-stop-base-c"/>
      </radialGradient>
      <radialGradient id="mp-g-vignette" cx="50%" cy="50%" r="72%">
        <stop offset=".55" class="mp-stop-vig-a"/><stop offset="1" class="mp-stop-vig-b"/>
      </radialGradient>
      <linearGradient id="mp-g-sky" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" class="mp-stop-sky-a"/><stop offset="1" class="mp-stop-sky-b"/>
      </linearGradient>
      <linearGradient id="mp-g-river" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" class="mp-stop-river-a"/><stop offset=".55" class="mp-stop-river-b"/><stop offset="1" class="mp-stop-river-a"/>
      </linearGradient>
      <radialGradient id="mp-g-cloud" cx="50%" cy="50%" r="50%">
        <stop offset="0" class="mp-stop-cloud-a"/><stop offset="1" class="mp-stop-cloud-b"/>
      </radialGradient>
      <filter id="mp-f-grain" x="0" y="0" width="100%" height="100%">
        <feTurbulence type="fractalNoise" baseFrequency=".85" numOctaves="2" stitchTiles="stitch" seed="7"/>
        <feColorMatrix type="saturate" values="0"/>
      </filter>
      <filter id="mp-f-mottle" x="0" y="0" width="100%" height="100%">
        <feTurbulence type="fractalNoise" baseFrequency=".011 .016" numOctaves="3" seed="3"/>
        <feColorMatrix type="matrix" values="0 0 0 0 .78  0 0 0 0 .64  0 0 0 0 .44  0 0 0 -1.6 1.05"/>
      </filter>
      <clipPath id="mp-clip"><rect x="15" y="15" width="${W - 30}" height="${H - 30}"/></clipPath>
      <pattern id="mp-p-hatch" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(35)">
        <line x1="0" y1="0" x2="0" y2="7" class="mp-hatch-line"/>
      </pattern>
    </defs>`);

    // --- static base (does not zoom: keeps the costly filters from re-rasterising)
    parts.push(`<g class="mp-static" aria-hidden="true">
      <rect width="${W}" height="${H}" fill="url(#mp-g-base)"/>
      <rect width="${W}" height="${H}" filter="url(#mp-f-mottle)" class="mp-mottle"/>
    </g>`);

    parts.push(`<g clip-path="url(#mp-clip)"><g id="mp-viewport">`);

    // --- far sky band + stars above the range
    let stars = '';
    for (let i = 0; i < 70; i++) {
      const x = R() * W, y = R() * 92, r = R() < 0.12 ? 1.25 : 0.6 + R() * 0.45;
      stars += `<circle cx="${fmt(x)}" cy="${fmt(y)}" r="${fmt(r)}" class="mp-star${R() < 0.3 ? ' tw' : ''}" style="--d:${fmt(R() * 6)}s"/>`;
    }
    parts.push(`<g data-depth="-0.7" aria-hidden="true"><rect x="-60" y="-60" width="${W + 120}" height="200" fill="url(#mp-g-sky)"/>${stars}</g>`);

    // --- Himalaya: three ridges of faceted peaks, back to front
    let mtn = '';
    const rows = [
      { base: 112, hmin: 34, hmax: 62, step: [38, 58], cls: 'back' },
      { base: 136, hmin: 40, hmax: 78, step: [46, 70], cls: 'mid' },
      { base: 162, hmin: 28, hmax: 64, step: [40, 66], cls: 'front' },
    ];
    for (const row of rows) {
      let x = -60 + R() * 30;
      while (x < W + 60) {
        const w = row.step[0] + R() * (row.step[1] - row.step[0]);
        // taller in the centre-north, lower toward the edges
        const centre = 1 - Math.min(1, Math.abs(x - W * 0.55) / (W * 0.62)) * 0.45;
        const h = (row.hmin + R() * (row.hmax - row.hmin)) * centre;
        const b = row.base + (R() - 0.5) * 8;
        const T = [x, b - h], L = [x - w * 0.62, b], Rr = [x + w * 0.58, b], M = [x + w * 0.12, b];
        const s1 = [T[0] + (L[0] - T[0]) * 0.27, T[1] + (L[1] - T[1]) * 0.27];
        const s2 = [T[0] + (M[0] - T[0]) * 0.18 - 2, T[1] + (M[1] - T[1]) * 0.18];
        const s3 = [T[0] + (Rr[0] - T[0]) * 0.3, T[1] + (Rr[1] - T[1]) * 0.3];
        const p = (pt) => `${fmt(pt[0])},${fmt(pt[1])}`;
        mtn += `<g class="mp-peak ${row.cls}">
          <polygon class="lit" points="${p(T)} ${p(L)} ${p(M)}"/>
          <polygon class="shade" points="${p(T)} ${p(M)} ${p(Rr)}"/>
          ${h > 36 ? `<polygon class="snow" points="${p(T)} ${p(s1)} ${fmt(s1[0] + 4)},${fmt(s1[1] - 3)} ${p(s2)} ${fmt(s3[0] - 3)},${fmt(s3[1] - 2)} ${p(s3)}"/>` : ''}
        </g>`;
        x += w * (0.55 + R() * 0.25);
      }
    }
    // foothill wash under the front ridge
    mtn += `<path class="mp-foothill" d="M-60 158 Q 250 182 500 170 T 1060 164 L1060 196 Q 520 214 -60 194 Z"/>`;
    parts.push(`<g data-depth="-0.35" class="mp-l-mountains" aria-hidden="true">${mtn}</g>`);

    // --- region labels (centroids of each region's places, else a fallback position)
    const regionPts = {};
    for (const l of placed) { const r = l.map.region; if (r) (regionPts[r] ||= []).push([l.map.x, l.map.y]); }
    let regions = '';
    // region names avoid marker discs and their default name boxes (and each other): try a few offsets from the
    // region's centroid and keep the first clear one
    const blockers = placed.flatMap((l) => {
      const w = String(l.name || l.id).length * 7 + 8;
      return [[l.map.x - 20, l.map.y - 20, l.map.x + 20, l.map.y + 20], [l.map.x - w / 2, l.map.y + 17, l.map.x + w / 2, l.map.y + 34]];
    });
    const overlaps = (a, b) => a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1];
    regionBoxes.length = 0;
    for (const key of Object.keys(REGION_LABEL)) {
      const pts = regionPts[key];
      const hima = key === 'himalaya';
      if (!hima && !pts) continue; // only label regions that have places
      const text = String(REGION_LABEL[key]);
      const fs = hima ? 19 : 16, half = (text.length * fs * 1.06) / 2;
      let [x, y] = REGION_FALLBACK[key];
      const cands = [[x, y]];
      if (!hima && pts.length) {
        const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length, cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
        cands.length = 0;
        for (const dy of [-44, -72, 52, -100, 80, -128, 108]) for (const dx of [0, -70, 70]) cands.push([cx + dx, cy + dy]);
      }
      const boxAt = ([cx, cy]) => { const bx = clamp(cx, Math.max(120, half + 24), Math.min(W - 120, W - half - 24)), by = clamp(cy, 34, H - 24); return [bx, by, [bx - half, by - fs + 2, bx + half, by + 4]]; };
      let pick = boxAt(cands[0]);
      if (!hima) {
        for (const c of cands) { const b = boxAt(c); if (!blockers.some((o) => overlaps(b[2], o)) && !regionBoxes.some((o) => overlaps(b[2], o))) { pick = b; break; } }
      }
      [x, y] = pick;
      regionBoxes.push(pick[2]);
      regions += `<text class="mp-region${hima ? ' hima' : ''}" data-ri="${regionBoxes.length - 1}" x="${fmt(x)}" y="${fmt(y)}" text-anchor="middle">${esc(text)}</text>`;
    }
    parts.push(`<g data-depth="0.25" aria-hidden="true">${regions}</g>`);

    // --- ground layer: decoration, rivers, routes, markers move together (keeps rivers beside their cities)
    parts.push(`<g data-depth="1" class="mp-l-ground">`);

    // forest clusters and battlefield hatching near their places
    let deco = '';
    for (const l of placed) {
      const { x, y, icon } = l.map;
      const r2 = rng(hashStr(l.id));
      if (icon === 'forest' || l.kind === 'forest' || l.kind === 'hermitage') {
        for (let i = 0; i < 14; i++) {
          const a = r2() * Math.PI * 2;
          if (a > Math.PI * 0.28 && a < Math.PI * 0.72) continue; // keep the label (below) clear
          const d = 22 + r2() * 30, tx = x + Math.cos(a) * d, ty = y + Math.sin(a) * d, s = 0.7 + r2() * 0.6;
          deco += `<path class="mp-tree" d="M${fmt(tx)} ${fmt(ty - 6 * s)}l${fmt(4 * s)} ${fmt(7.5 * s)}h${fmt(-8 * s)}z"/>`;
        }
      }
      if (icon === 'battlefield' || l.kind === 'battlefield') {
        deco += `<circle cx="${fmt(x)}" cy="${fmt(y)}" r="38" class="mp-field"/><circle cx="${fmt(x)}" cy="${fmt(y)}" r="38" class="mp-field-ring"/>`;
      }
    }
    // sparse groves across the plains for texture (kept away from markers)
    for (let i = 0; i < 46; i++) {
      const tx = 40 + R() * (W - 80), ty = 210 + R() * (H - 260);
      if (placed.some((l) => Math.hypot(l.map.x - tx, l.map.y - ty) < 48)) continue;
      const s = 0.55 + R() * 0.4;
      deco += `<path class="mp-tree faint" d="M${fmt(tx)} ${fmt(ty - 6 * s)}l${fmt(4 * s)} ${fmt(7.5 * s)}h${fmt(-8 * s)}z"/>`;
    }
    parts.push(`<g aria-hidden="true">${deco}</g>`);

    // rivers — schematic courses routed past the places the epic sets on their banks
    const rv = riverGeometry();
    const riverSvg = (id, d, label, offset) => `
      <g class="mp-river" aria-hidden="true">
        <path d="${d}" class="halo"/><path d="${d}" class="body"/>
        <path id="${id}" d="${d}" class="core"/><path d="${d}" class="flow"/>
        <text class="mp-river-label" dy="-7"><textPath href="#${id}" startOffset="${offset}">${label}</textPath></text>
      </g>`;
    parts.push(riverSvg('mp-yamuna', rv.yamuna, 'Yamunā', '30%'));
    parts.push(riverSvg('mp-ganga', rv.ganga, 'Gaṅgā', '58%'));

    // narrative pathways (deduplicated pairs)
    const seen = new Set();
    let routes = '';
    for (const l of placed) {
      for (const to of arr(l.paths_to)) {
        const m = byId.get(to);
        if (!m || !isNum(m?.map?.x) || !isNum(m?.map?.y)) continue;
        const key = [l.id, to].sort().join('|');
        if (seen.has(key)) continue;
        seen.add(key);
        const [x1, y1, x2, y2] = [l.map.x, l.map.y, m.map.x, m.map.y];
        const dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy) || 1;
        const bend = (hashStr(key) % 2 ? 1 : -1) * Math.min(46, len * 0.14);
        const cx = (x1 + x2) / 2 - (dy / len) * bend, cy = (y1 + y2) / 2 + (dx / len) * bend;
        routes += `<path class="mp-route" data-a="${esc(l.id)}" data-b="${esc(to)}" d="M${fmt(x1)},${fmt(y1)} Q${fmt(cx)},${fmt(cy)} ${fmt(x2)},${fmt(y2)}"/>`;
      }
    }
    parts.push(`<g aria-hidden="true">${routes}</g>`);

    // compass rose (south-west corner)
    parts.push(`<g class="mp-compass" transform="translate(84 ${H - 86})" aria-hidden="true">
      <circle r="30" class="ring"/><circle r="22" class="ring thin"/>
      <path class="star" d="M0-38L5-5L38 0L5 5L0 38L-5 5L-38 0L-5-5Z"/>
      <path class="star mini" d="M0-20L3-3L20 0L3 3L0 20L-3 3L-20 0L-3-3Z" transform="rotate(45)"/>
      <text y="-44" text-anchor="middle">N</text>
    </g>`);

    // markers
    let mk = '';
    for (const l of markersOrdered) {
      const icon = GLYPH[l.map.icon] ? l.map.icon : 'city';
      const acc = accentOf(l);
      const n = (chaptersAt.get(l.id) || []).length;
      mk += `<g class="mk" data-id="${esc(l.id)}" transform="translate(${fmt(l.map.x)} ${fmt(l.map.y)})" tabindex="0" role="button"
                ${acc ? `style="--mk-accent:${esc(acc)}"` : ''} aria-label="${esc(l.name)}${n ? `, ${n} chapter${n > 1 ? 's' : ''}` : ''}">
        <g class="mk-body">
          <circle r="24" class="mk-hit"/>
          <circle r="19" class="mk-halo"/>
          <circle r="13" class="mk-ring"/>
          <path d="${GLYPH[icon]}" class="mk-glyph" transform="scale(.82)"/>
          <g class="mk-lock" transform="translate(11 -11)"><circle r="6"/><path d="${LOCK}" transform="scale(.9)"/></g>
          <text class="mk-label" y="29" text-anchor="middle">${esc(l.name)}</text>
        </g>
      </g>`;
    }
    parts.push(`<g class="mp-markers">${mk}</g>`);
    parts.push(`</g>`); // ground

    // foreground mist (nearest layer, strongest parallax)
    let mist = '';
    for (let i = 0; i < 7; i++) {
      const cx = R() * W, cy = 180 + R() * 460, rx = 90 + R() * 140, ry = 16 + R() * 18;
      mist += `<ellipse class="mp-mist" cx="${fmt(cx)}" cy="${fmt(cy)}" rx="${fmt(rx)}" ry="${fmt(ry)}" fill="url(#mp-g-cloud)" style="--d:${fmt(i * 3.1)}s"/>`;
    }
    parts.push(`<g data-depth="1.9" aria-hidden="true" class="mp-l-fore">${mist}</g>`);
    parts.push(`</g></g>`); // viewport + clip

    // --- static overlays: grain, vignette, frame
    parts.push(`<g class="mp-static" aria-hidden="true" pointer-events="none">
      <rect width="${W}" height="${H}" filter="url(#mp-f-grain)" class="mp-grain"/>
      <rect width="${W}" height="${H}" fill="url(#mp-g-vignette)"/>
      <rect x="9" y="9" width="${W - 18}" height="${H - 18}" class="mp-frame"/>
      <rect x="15" y="15" width="${W - 30}" height="${H - 30}" class="mp-frame thin"/>
      ${[[15, 15], [W - 15, 15], [15, H - 15], [W - 15, H - 15]].map(([x, y]) => `<path class="mp-corner" d="M${x} ${y - 6}L${x + 6} ${y}L${x} ${y + 6}L${x - 6} ${y}Z"/>`).join('')}
    </g>`);

    return `<svg class="mp-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="group" aria-label="Map of the epic's places">${parts.join('')}</svg>`;
  }

  // Schematic river courses. The epic places Hastinapura on the Ganga and Indraprastha by the Yamuna, so the
  // rivers are routed past those markers when they exist; otherwise they follow a default course.
  function riverGeometry() {
    const P = (id) => { const l = byId.get(id); return l && isNum(l?.map?.x) && isNum(l?.map?.y) ? [l.map.x, l.map.y] : null; };
    const hast = P('hastinapura') || [440, 260];
    const gangaLoc = P('ganga');
    const indra = P('indraprastha') || [hast[0] - 46, hast[1] + 58];
    const src = [clamp((gangaLoc ? gangaLoc[0] : hast[0] + 60) + 24, 300, 780), 150];

    const way = [];
    if (gangaLoc) way.push([gangaLoc[0], gangaLoc[1]]);
    way.push([hast[0] + 22, hast[1] + 6]); // runs beside the city, not through the marker
    way.sort((a, b) => a[1] - b[1]);
    const mono = [src];
    for (const p of way) { const prev = mono[mono.length - 1]; if (p[1] > prev[1] + 14) mono.push(p); }
    const last = mono[mono.length - 1];
    const end = [W + 60, clamp(last[1] + 170, 420, 650)];
    const e1 = [last[0] + (end[0] - last[0]) * 0.32, last[1] + (end[1] - last[1]) * 0.5];
    const e2 = [last[0] + (end[0] - last[0]) * 0.66, last[1] + (end[1] - last[1]) * 0.86];
    const gangaPts = [[src[0] + 8, 126], ...mono, e1, e2, end];

    const conf = (e1[0] > indra[0] + 60 && e1[1] > indra[1]) ? e1 : e2; // confluence on the Ganga
    const ySrc = [clamp(src[0] - 130, 120, 700), 124];
    const iBank = [indra[0] + 20, indra[1]];
    const yPts = [ySrc];
    if (iBank[1] > ySrc[1] + 40) yPts.push([(ySrc[0] + iBank[0]) / 2 - 8, (ySrc[1] + iBank[1]) / 2]);
    yPts.push(iBank);
    if (Math.hypot(conf[0] - iBank[0], conf[1] - iBank[1]) > 120) yPts.push([(iBank[0] + conf[0]) / 2, (iBank[1] + conf[1]) / 2 + 22]);
    yPts.push(conf);
    return { ganga: smoothPath(gangaPts), yamuna: smoothPath(yPts) };
  }

  /* ---------------- view transform: zoom / pan / parallax */
  const view = { k: 1, tx: 0, ty: 0 };
  let fit = { s: 1, visX: 0, visY: 0, visW: W, visH: H, left: 0, top: 0, mode: 'meet' };
  const par = { x: 0, y: 0, tx: 0, ty: 0, raf: 0 };

  function measure() {
    if (!svg) return;
    const r = stage.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const mode = r.width / r.height < 1.05 ? 'slice' : 'meet'; // portrait/mobile: the map fills the screen
    svg.setAttribute('preserveAspectRatio', `xMidYMid ${mode}`);
    const s = mode === 'slice' ? Math.max(r.width / W, r.height / H) : Math.min(r.width / W, r.height / H);
    const visW = r.width / s, visH = r.height / s;
    fit = { s, visW, visH, visX: (W - visW) / 2, visY: (H - visH) / 2, left: r.left, top: r.top, mode };
  }
  const toUser = (cx, cy) => [fit.visX + (cx - fit.left) / fit.s, fit.visY + (cy - fit.top) / fit.s];

  function clampView() {
    const cw = W * view.k, ch = H * view.k;
    // The map is only ever drawn inside the clip rect [0,W]×[0,H], so the pannable window is the part of that
    // rect that is on screen: the whole rect when letterboxed ('meet'), the visible crop when 'slice'.
    // (Clamping against the full letterboxed viewport instead made the map's edges unreachable when zoomed.)
    // While the preview card is open, allow extra travel equal to the area it covers so edge places can be
    // brought beside it; closing the card snaps back (closeCard re-applies the view).
    const fitAxis = (t, c, visStart, visLen, size, slack) => {
      const a = Math.max(visStart, 0), len = Math.min(visStart + visLen, size) - a;
      return c >= len ? clamp(t, a + len - c - slack, a) : a + (len - c) / 2;
    };
    let slackX = 0, slackY = 0;
    if (card && !card.hidden && fit.s > 0) {
      const sr = stage.getBoundingClientRect(), cr = card.getBoundingClientRect();
      if (cr.width && cr.height) {
        // screen edges of the drawn map window (clip rect ∩ viewport)
        const winRight = fit.left + (Math.min(fit.visX + fit.visW, W) - fit.visX) * fit.s;
        const winBottom = fit.top + (Math.min(fit.visY + fit.visH, H) - fit.visY) * fit.s;
        if (cr.top > sr.top + sr.height * 0.2) slackY = Math.max(0, winBottom - cr.top) / fit.s;
        else if (cr.left > sr.left + sr.width * 0.3) slackX = Math.max(0, winRight - cr.left) / fit.s;
      }
    }
    view.tx = fitAxis(view.tx, cw, fit.visX, fit.visW, W, slackX);
    view.ty = fitAxis(view.ty, ch, fit.visY, fit.visH, H, slackY);
  }
  function applyView() {
    if (!viewport) return;
    clampView();
    viewport.setAttribute('transform', `translate(${fmt(view.tx)} ${fmt(view.ty)}) scale(${view.k.toFixed(4)})`);
    // markers keep a constant on-screen size whatever the zoom
    const ms = 1 / (view.k * clamp(fit.s, 0.72, 1.6));
    for (const g of markerBodies) g.setAttribute('transform', `scale(${ms.toFixed(4)})`);
    svg.style.setProperty('--mk-scale', ms.toFixed(3));
    lastMs = ms;
    declutter();
  }

  // Label declutter: where places sit close together (e.g. the Kurukshetra fields) overlapping names are faded
  // until zoomed apart, keeping the selected / current / visited names first. Every marker keeps its aria-label,
  // and a faded name reappears on hover or keyboard focus.
  let lastMs = 1;
  const labelInfo = markersOrdered.map((l, i) => ({ id: l.id, i, x: l.map.x, y: l.map.y, w: String(l.name || l.id).length * 7 + 8, pos: 0 }));
  // Candidate name positions in the marker's local (unscaled) space: below, above, right, left.
  const LABEL_POS = [
    { x: 0, y: 29, anchor: 'middle' }, { x: 0, y: -23, anchor: 'middle' },
    { x: 21, y: 4, anchor: 'start' }, { x: -21, y: 4, anchor: 'end' },
  ];
  const DISC = 16; // marker ring + a little air, local units
  function declutter() {
    if (!labelInfo.length) return;
    const cur = currentLocId();
    const prio = (id) => (id === selected ? 0 : id === cur ? 1 : locVisited(id) ? 2 : 3);
    const m = lastMs;
    const hits = (box, list) => list.some((b) => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1]);
    const boxFor = (it, p) => {
      const x0 = p.anchor === 'middle' ? p.x - it.w / 2 : p.anchor === 'start' ? p.x : p.x - it.w;
      return [it.x + x0 * m, it.y + (p.y - 11) * m, it.x + (x0 + it.w) * m, it.y + (p.y + 4) * m];
    };
    // every marker disc is an obstacle for every other marker's name; region names are soft obstacles
    const discs = labelInfo.map((it) => ({ id: it.id, b: [it.x - DISC * m, it.y - DISC * m, it.x + DISC * m, it.y + DISC * m] }));
    const kept = [];
    for (const it of [...labelInfo].sort((a, b) => prio(a.id) - prio(b.id) || a.i - b.i)) {
      const others = discs.filter((d) => d.id !== it.id).map((d) => d.b);
      const hard = [...kept, ...others];
      let pi = LABEL_POS.findIndex((p) => { const b = boxFor(it, p); return !hits(b, hard) && !hits(b, regionBoxes); });
      if (pi < 0) pi = LABEL_POS.findIndex((p) => !hits(boxFor(it, p), hard));
      const el = markerEls.get(it.id);
      el?.classList.toggle('lbl-off', pi < 0);
      const use = pi < 0 ? 0 : pi;
      if (use !== it.pos && el) {
        const t = el.querySelector('.mk-label'), p = LABEL_POS[use];
        if (t) { t.setAttribute('x', p.x); t.setAttribute('y', p.y); t.setAttribute('text-anchor', p.anchor); }
        it.pos = use;
      }
      if (pi >= 0) kept.push(boxFor(it, LABEL_POS[pi]));
    }
    // a region name that still sits under a shown place name or a marker fades until zoomed apart
    const busy = [...kept, ...discs.map((d) => d.b)];
    regionEls.forEach((t) => { const b = regionBoxes[+t.dataset.ri]; t.classList.toggle('rg-off', !!b && hits(b, busy)); });
  }
  function zoomAt(ux, uy, factor) {
    const k2 = clamp(view.k * factor, MIN_K, MAX_K);
    const px = (ux - view.tx) / view.k, py = (uy - view.ty) / view.k;
    view.k = k2; view.tx = ux - px * k2; view.ty = uy - py * k2;
    applyView();
  }
  const zoomCentre = (f) => zoomAt(fit.visX + fit.visW / 2, fit.visY + fit.visH / 2, f);
  function resetView() { view.k = 1; view.tx = 0; view.ty = 0; applyView(); }
  function centreOn(x, y, k) {
    if (k) view.k = clamp(k, MIN_K, MAX_K);
    // centre in the part of the stage the preview card does not cover (side panel on desktop, bottom sheet on mobile)
    const sr = stage.getBoundingClientRect();
    let x1 = sr.right, y1 = sr.bottom;
    if (!card.hidden) {
      const cr = card.getBoundingClientRect();
      if (cr.width && cr.height) {
        if (cr.top > sr.top + sr.height * 0.2) y1 = Math.max(sr.top + 60, Math.min(y1, cr.top)); // bottom sheet
        else if (cr.left > sr.left + sr.width * 0.3) x1 = Math.min(x1, cr.left); // side panel
      }
    }
    const [cx, cy] = sr.width ? toUser((sr.left + x1) / 2, (sr.top + y1) / 2) : [fit.visX + fit.visW / 2, fit.visY + fit.visH / 2];
    view.tx = cx - x * view.k; view.ty = cy - y * view.k;
    applyView();
  }

  function parallaxTick() {
    par.x += (par.tx - par.x) * 0.08; par.y += (par.ty - par.y) * 0.08;
    for (const g of layers) {
      const d = parseFloat(g.dataset.depth) || 0;
      g.setAttribute('transform', `translate(${fmt(par.x * d * 7)} ${fmt(par.y * d * 5)})`);
    }
    if (Math.abs(par.tx - par.x) > 0.002 || Math.abs(par.ty - par.y) > 0.002) par.raf = requestAnimationFrame(parallaxTick);
    else par.raf = 0;
  }
  function setParallax(nx, ny) {
    if (reducedMotion()) { nx = 0; ny = 0; }
    par.tx = nx; par.ty = ny;
    if (!par.raf) par.raf = requestAnimationFrame(parallaxTick);
  }

  /* ---------------- pointer: drag pan, pinch zoom, parallax */
  const pointers = new Map();
  let drag = null, pinch = null, moved = false;
  if (svg) {
    stage.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 && e.pointerType === 'mouse') return;
      measure();
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pointers.size === 1) { drag = { x: e.clientX, y: e.clientY, tx: view.tx, ty: view.ty }; moved = false; }
      if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        pinch = { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, k: view.k };
        drag = null;
      }
    });
    stage.addEventListener('pointermove', (e) => {
      const r = stage.getBoundingClientRect();
      if (e.pointerType === 'mouse') setParallax(((e.clientX - r.left) / r.width - 0.5) * 2, ((e.clientY - r.top) / r.height - 0.5) * 2);
      if (!pointers.has(e.pointerId)) return;
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pinch && pointers.size >= 2) {
        const [a, b] = [...pointers.values()];
        const d = Math.hypot(a.x - b.x, a.y - b.y) || 1;
        const [ux, uy] = toUser((a.x + b.x) / 2, (a.y + b.y) / 2);
        zoomAt(ux, uy, clamp((pinch.k * d) / pinch.d, MIN_K, MAX_K) / view.k);
        moved = true;
      } else if (drag) {
        const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        if (!moved && Math.hypot(dx, dy) > 5) { moved = true; stage.setPointerCapture?.(e.pointerId); stage.classList.add('is-dragging'); }
        if (moved) { view.tx = drag.tx + dx / fit.s; view.ty = drag.ty + dy / fit.s; applyView(); }
      }
    });
    const end = (e) => {
      pointers.delete(e.pointerId);
      if (pointers.size < 2) pinch = null;
      if (pointers.size === 0) { drag = null; stage.classList.remove('is-dragging'); }
    };
    stage.addEventListener('pointerup', end);
    stage.addEventListener('pointercancel', end);
    stage.addEventListener('pointerleave', (e) => { if (e.pointerType === 'mouse') setParallax(0, 0); });
    stage.addEventListener('wheel', (e) => {
      e.preventDefault();
      measure();
      const [ux, uy] = toUser(e.clientX, e.clientY);
      const f = Math.exp(-clamp(e.deltaY, -120, 120) * (e.ctrlKey ? 0.01 : 0.0022));
      zoomAt(ux, uy, f);
    }, { passive: false });

    // marker activation (click suppressed after a drag)
    stage.addEventListener('click', (e) => {
      const g = e.target.closest?.('.mk');
      if (!g) return;
      if (moved) { moved = false; return; }
      // detail === 0: activated without a pointer (screen-reader virtual click) → move focus into the card
      openCard(g.dataset.id, { focus: e.detail === 0 });
    });
    stage.addEventListener('keydown', (e) => {
      const g = e.target.closest?.('.mk');
      if (g && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); openCard(g.dataset.id, { focus: true }); return; }
      const step = 40 / view.k;
      const keys = {
        ArrowLeft: () => { view.tx += step * view.k; applyView(); },
        ArrowRight: () => { view.tx -= step * view.k; applyView(); },
        ArrowUp: () => { view.ty += step * view.k; applyView(); },
        ArrowDown: () => { view.ty -= step * view.k; applyView(); },
        '+': () => zoomCentre(1.25), '=': () => zoomCentre(1.25), '-': () => zoomCentre(0.8), '0': resetView,
      };
      if (keys[e.key] && !e.altKey && !e.metaKey && !e.ctrlKey) { e.preventDefault(); e.stopPropagation(); measure(); keys[e.key](); }
    });
  }

  /* ---------------- toolbar */
  root.addEventListener('click', (e) => {
    const b = e.target.closest?.('[data-act]');
    if (!b || !root.contains(b)) return;
    const act = b.dataset.act;
    measure();
    if (act === 'zoom-in') zoomCentre(1.35);
    else if (act === 'zoom-out') zoomCentre(1 / 1.35);
    else if (act === 'reset') resetView();
    else if (act === 'close') close();
    else if (act === 'card-close') closeCard();
    else if (act === 'travel') travel(b.dataset.chapter);
    else if (act === 'ask') ask(b.dataset.loc);
    else if (act === 'goto-loc') openCard(b.dataset.loc, { focus: true, centre: true });
  });
  root.querySelector('.mp-select')?.addEventListener('change', (e) => {
    const id = e.target.value;
    if (id) { openCard(id, { focus: true, centre: true }); e.target.value = ''; }
  });
  root.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    e.stopPropagation(); e.preventDefault();
    if (!card.hidden) closeCard(); else close();
  });

  function close() {
    closeCard({ silent: true });
    bus?.emit?.('view:change', { view: 'journey' });
    ensureBodyView('journey');
  }

  /* ---------------- states */
  function refreshStates() {
    const cur = currentLocId();
    for (const [id, g] of markerEls) {
      const visited = locVisited(id), current = id === cur, locked = !visited && !current && locLocked(id);
      const n = (chaptersAt.get(id) || []).length;
      g.classList.toggle('is-visited', visited);
      g.classList.toggle('is-current', current);
      g.classList.toggle('is-locked', locked);
      g.classList.toggle('is-empty', n === 0);
      g.classList.toggle('is-selected', id === selected);
      const l = byId.get(id);
      const stateTxt = current ? 'you are here' : visited ? 'visited' : locked ? 'ahead in the story' : n ? 'not yet visited' : 'no chapter set here yet';
      g.setAttribute('aria-label', `${l?.name || id}, ${stateTxt}${n ? `, ${n} chapter${n > 1 ? 's' : ''}` : ''}`);
      g.setAttribute('aria-pressed', id === selected ? 'true' : 'false');
    }
    for (const p of routeEls) {
      const a = p.dataset.a, b = p.dataset.b;
      p.classList.toggle('is-visited', locVisited(a) && locVisited(b));
      p.classList.toggle('is-live', !!cur && (a === cur || b === cur));
      p.classList.toggle('is-selected', !!selected && (a === selected || b === selected));
    }
    declutter(); // label priority follows selection / current / visited
  }

  /* ---------------- preview card */
  let selected = null, lastFocus = null;

  function identLine(t) {
    if (!t || typeof t !== 'string') return '';
    const s = t.trim();
    if (!s) return '';
    // The data is authored as "traditionally identified with …"; never lowercase the rest (proper nouns).
    const phrased = /^traditionally\b/i.test(s) ? s : /^identified\b/i.test(s) ? `Traditionally ${s.charAt(0).toLowerCase()}${s.slice(1)}` : `Traditionally identified with ${s}`;
    return `${phrased.charAt(0).toUpperCase()}${phrased.slice(1)}`.replace(/\.*$/, '.');
  }

  function openCard(id, { focus = false, centre = false } = {}) {
    const l = byId.get(id);
    if (!l) return;
    lastFocus = focus ? (markerEls.get(id) || document.activeElement) : markerEls.get(id);
    selected = id;
    const cs = chaptersAt.get(id) || [];
    const cur = st().current;
    const acc = accentOf(l);
    const isPlaced = isNum(l?.map?.x) && isNum(l?.map?.y);
    const rows = cs.map((c) => {
      const visited = isVisited(c.id), current = c.id === cur, unlocked = isUnlocked(c);
      const state = current ? 'current' : visited ? 'visited' : unlocked ? 'open' : 'locked';
      const stateTxt = { current: 'You are here', visited: 'Visited', open: 'Not yet visited', locked: 'Ahead in the story' }[state];
      const q = c?.central_question?.question;
      return `<li class="mp-ch is-${state}">
        <div class="mp-ch-head"><span class="mp-ch-state" aria-hidden="true"></span>
          <span class="mp-ch-num">${esc(chapterLabel(c))}</span><span class="mp-ch-st">${stateTxt}</span></div>
        <p class="mp-ch-title">${esc(c.title || c.id)}</p>
        ${q ? `<p class="mp-ch-q">${esc(q)}</p>` : ''}
        ${state === 'locked' ? `<p class="mp-ch-note">You'd be jumping ahead of the story — free exploration is always open.</p>` : ''}
        <button type="button" class="btn btn-gold vw-btn" data-act="travel" data-chapter="${esc(c.id)}"
          aria-label="Travel here: ${esc(c.title || c.id)}">${current ? 'Return here' : 'Travel here'}</button>
      </li>`;
    }).join('');
    const roads = arr(l.paths_to).map((t) => byId.get(t)).filter(Boolean);
    const ident = identLine(l.traditional_identification);

    card.innerHTML = `
      <div class="vw-sheet-grip" aria-hidden="true"></div>
      <button type="button" class="vw-close sm" data-act="card-close" aria-label="Close place details"><span aria-hidden="true">×</span></button>
      <div class="mp-card-accent" ${acc ? `style="--acc:${esc(acc)}"` : ''} aria-hidden="true"></div>
      <p class="vw-eyebrow">${esc([ICON_NAMES[l?.map?.icon] || l.kind, REGION_LABEL[l?.map?.region]].filter(Boolean).join(' · '))}</p>
      <h3 class="mp-card-title" id="mp-card-title" tabindex="-1">${esc(l.name)}</h3>
      ${l.sanskrit_name ? `<p class="mp-card-sk" lang="sa-Latn">${esc(l.sanskrit_name)}</p>` : ''}
      ${isPlaced ? '' : `<p class="vw-muted">This place is not yet placed on the map.</p>`}
      ${l.description ? `<p class="mp-card-desc">${esc(l.description)}</p>` : ''}
      ${ident ? `<p class="mp-card-trad"><span class="vw-label">Tradition</span>${esc(ident)} <em>A traditional association, not an archaeological finding.</em></p>` : ''}
      ${(acc || l.theme) ? `<p class="mp-card-theme"><span class="vw-swatch" ${acc ? `style="--sw:${esc(acc)}"` : ''} aria-hidden="true"></span>${l.theme ? `Theme: <strong>${esc(l.theme)}</strong>` : 'Theme colour'}</p>` : ''}
      <h4 class="vw-h4">Chapters here</h4>
      ${cs.length ? `<ol class="mp-chs">${rows}</ol>` : `<p class="vw-muted">No chapter of this voyage is set here yet.</p>`}
      <button type="button" class="btn btn-ghost vw-btn mp-ask" data-act="ask" data-loc="${esc(l.id)}">Ask about this place</button>
      ${roads.length ? `<h4 class="vw-h4">Pathways from here</h4><div class="vw-chips">${roads.map((r) => `<button type="button" class="chip vw-chip" data-act="goto-loc" data-loc="${esc(r.id)}">${esc(r.name)}</button>`).join('')}</div>` : ''}
    `;
    card.hidden = false;
    root.classList.add('has-card');
    if (acc) card.style.setProperty('--acc', acc); else card.style.removeProperty('--acc');
    refreshStates();
    measure();
    // On a cropped portrait stage the bottom sheet covers the lower map; a little zoom gives room to pan the place into view.
    // On desktop, a place hidden behind the side card is brought out (pan first, then the smallest zoom that frees it).
    const cardLeft = card.getBoundingClientRect().left;
    const isCovered = () => fit.mode !== 'slice' && cardLeft > 0 && fit.left + (view.tx + l.map.x * view.k - fit.visX) * fit.s > cardLeft - 40;
    const covered = isPlaced && isCovered();
    const wantK = centre && view.k < 1.6 ? 1.8 : fit.mode === 'slice' ? Math.max(view.k, 1.5) : view.k;
    if (isPlaced && (centre || covered || view.k > 1 || fit.mode === 'slice')) centreOn(l.map.x, l.map.y, wantK);
    if (covered) for (const k of [1.4, 1.8, 2.4]) { if (!isCovered() || k <= view.k) continue; centreOn(l.map.x, l.map.y, k); }
    if (focus) requestAnimationFrame(() => card.querySelector('#mp-card-title')?.focus({ preventScroll: true }));
  }

  function closeCard({ silent = false } = {}) {
    if (card.hidden) return;
    card.hidden = true;
    root.classList.remove('has-card');
    selected = null;
    refreshStates();
    if (active) applyView(); // drop the extra travel the open card allowed
    if (!silent && lastFocus && root.contains(lastFocus)) lastFocus.focus?.({ preventScroll: true });
  }

  function travel(chapterId) {
    if (!chapterId) return;
    closeCard({ silent: true });
    bus?.emit?.('view:change', { view: 'journey' });
    ensureBodyView('journey');
    bus?.emit?.('chapter:enter', { chapterId, from: 'map' });
  }

  function ask(locId) {
    const l = byId.get(locId);
    if (!l) return;
    closeCard({ silent: true });
    bus?.emit?.('view:change', { view: 'journey' });
    ensureBodyView('journey');
    bus?.emit?.('question:select', { query: `What happens at ${l.name} in the Mahabharata and why does it matter?` });
  }

  /* ---------------- lifecycle */
  let active = false, showPending = false, shownOnce = false;
  function onShow() {
    active = true;
    measure(); applyView(); refreshStates();
    // First showing on a cropped (portrait) stage: land the eye on the current place, not the crop centre.
    if (!shownOnce) {
      shownOnce = true;
      const cl = byId.get(currentLocId());
      if (cl && fit.mode === 'slice' && card.hidden && isNum(cl?.map?.x) && isNum(cl?.map?.y)) centreOn(cl.map.x, cl.map.y, view.k);
    }
  }
  function onHide() { active = false; setParallax(0, 0); }
  function sync() {
    const now = document.body.classList.contains('view-map');
    if (now && !active && !showPending) {
      showPending = true; // several class mutations in one frame must not queue several onShow calls
      requestAnimationFrame(() => { showPending = false; if (!active && document.body.classList.contains('view-map')) onShow(); });
    } else if (!now && active) onHide();
  }
  new MutationObserver(sync).observe(document.body, { attributes: true, attributeFilter: ['class'] });
  sync();

  if (typeof ResizeObserver === 'function') new ResizeObserver(() => { if (active) { measure(); applyView(); } }).observe(stage);
  window.addEventListener('resize', () => { if (active) { measure(); applyView(); } });

  bus?.on?.('view:change', (p) => { if (p?.view) lastView = p.view; if (p?.view === 'map') ensureBodyView('map'); });
  // global Esc (main.js asks first): close the preview card before closing the whole view
  bus?.on?.('ui:escape', (evt) => { if (active && !card.hidden) { closeCard(); if (evt) evt.handled = true; } });
  bus?.on?.('store:change', () => { refreshStates(); });
  bus?.on?.('chapter:entered', () => { refreshStates(); });
  bus?.on?.('entity:open', (p) => {
    if (p?.type !== 'location' || !byId.has(p.id)) return;
    if (!document.body.classList.contains('view-map')) { bus.emit('view:change', { view: 'map' }); ensureBodyView('map'); }
    setTimeout(() => { measure(); openCard(p.id, { focus: true, centre: true }); }, 60);
  });

  refreshStates();
  applyView();
}
