// DOM hotspot layer: pulsing gold markers + labels at the screen positions the world projects
// from 3D (~10 Hz). Markers are pooled by id and glide between updates with a short CSS
// transition. Markers hidden behind the UI panels fade so they never fight the chrome.
import { $, el, esc } from '../core/util.js';

const TYPE_LABEL = {
  character: 'Character',
  location: 'Place',
  event: 'Moment',
  question: 'Question',
  discovery: 'Discovery',
  chapter: 'Chapter',
};

export function initHotspots({ world, bus, V } = {}) {
  const layer = $('#hotspots');
  if (!layer || !world || typeof world.onHotspots !== 'function') return;

  const pool = new Map(); // id -> {node, data}
  let blockers = [];      // cached DOMRects of UI panels
  let blockersAt = 0;

  /** Rects of the chrome that markers should not sit underneath. */
  function readBlockers() {
    const now = performance.now();
    if (now - blockersAt < 400) return blockers;
    blockersAt = now;
    const rects = [];
    const add = (node) => {
      if (!node) return;
      const r = node.getBoundingClientRect();
      const cs = getComputedStyle(node);
      if (r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && Number(cs.opacity) > 0.05) rects.push(r);
    };
    add($('#stage-ui .cp-panel'));
    add($('#stage-ui .shotbar:not([hidden])'));
    add($('#chapter-rail'));
    add($('#topnav .tn-bar'));
    add($('#topnav .tn-tabbar'));
    add($('.rs-launch'));
    if (document.body.classList.contains('research-open')) add($('#research'));
    blockers = rects;
    return rects;
  }

  const covered = (x, y, rects) => rects.some((r) => x >= r.left - 8 && x <= r.right + 8 && y >= r.top - 8 && y <= r.bottom + 8);

  function targetName(h) {
    if (!V) return '';
    if (h.target_type === 'character') return V.characterById.get(h.target)?.name || '';
    if (h.target_type === 'location') return V.locationById.get(h.target)?.name || '';
    if (h.target_type === 'event') return V.eventById.get(h.target)?.title || '';
    if (h.target_type === 'question') return V.questionById.get(h.target)?.question?.question || '';
    return '';
  }

  function create(h) {
    const type = TYPE_LABEL[h.target_type] ? h.target_type : 'discovery';
    const label = h.label || targetName(h) || TYPE_LABEL[type];
    const node = el(`
      <button type="button" class="hs" data-type="${esc(type)}" data-hs="${esc(h.id)}"
        aria-label="${esc(`${label} — ${type === 'question' ? 'ask this question' : `open ${TYPE_LABEL[type].toLowerCase()}`}`)}">
        <span class="hs-mark" aria-hidden="true"><span class="hs-pulse"></span><span class="hs-core"></span></span>
        <span class="hs-label"><span class="hs-type">${esc(TYPE_LABEL[type])}</span><span class="hs-name">${esc(label)}</span></span>
      </button>`);
    node.addEventListener('click', (e) => {
      e.stopPropagation();
      const d = pool.get(h.id)?.data || h;
      if (d.target_type === 'question') {
        const chapterId = V?.questionById.get(d.target)?.chapter?.id;
        bus.emit('question:select', { questionId: d.target, chapterId });
      } else {
        bus.emit('entity:open', { type: d.target_type || 'discovery', id: d.target });
      }
      bus.emit('audio:cue', { cue: 'bell', volume: 0.25 });
    });
    layer.appendChild(node);
    // Fade in on the next frame
    requestAnimationFrame(() => node.classList.add('is-in'));
    return node;
  }

  function update(list) {
    if (!Array.isArray(list)) list = [];
    const rects = readBlockers();
    const seen = new Set();
    for (const h of list) {
      if (!h || !h.id) continue;
      seen.add(h.id);
      let entry = pool.get(h.id);
      if (!entry) { entry = { node: create(h), data: h }; pool.set(h.id, entry); }
      entry.data = h;
      const x = Number(h.x), y = Number(h.y);
      // Points projected just outside the viewport would leave a clipped label at the edge.
      const onScreen = h.visible !== false && Number.isFinite(x) && Number.isFinite(y)
        && x >= 4 && y >= 4 && x <= window.innerWidth - 4 && y <= window.innerHeight - 4;
      const node = entry.node;
      const occluded = onScreen && covered(x, y, rects);
      node.classList.toggle('is-hidden', !onScreen);
      node.classList.toggle('is-occluded', occluded);
      // Flip the label to the left near the right edge so it never overflows.
      node.classList.toggle('is-flip', onScreen && x > window.innerWidth - 220);
      if (onScreen) node.style.transform = `translate3d(${x.toFixed(1)}px, ${y.toFixed(1)}px, 0)`;
      // Invisible markers (off-screen or under a panel) must not be keyboard stops.
      const reachable = onScreen && !occluded;
      node.tabIndex = reachable ? 0 : -1;
      if (!reachable && node === document.activeElement) node.blur();
    }
    spreadLabels();
    for (const [id, entry] of pool) {
      if (seen.has(id)) continue;
      pool.delete(id);
      entry.node.classList.remove('is-in');
      entry.node.tabIndex = -1;
      setTimeout(() => entry.node.remove(), 400);
    }
  }

  /** Nudge labels of nearby markers apart vertically (markers stay on their projected points). */
  function spreadLabels() {
    const placed = [];
    const items = [];
    for (const [, entry] of pool) {
      const n = entry.node;
      if (n.classList.contains('is-hidden') || n.classList.contains('is-occluded')) { n.style.removeProperty('--hs-dy'); continue; }
      const label = n.querySelector('.hs-label');
      if (!label) continue;
      if (!entry.lw) { entry.lw = label.offsetWidth; entry.lh = label.offsetHeight; }
      items.push({ n, x: Number(entry.data.x), y: Number(entry.data.y), w: entry.lw || 120, h: entry.lh || 32, flip: n.classList.contains('is-flip') });
    }
    items.sort((a, b) => a.y - b.y);
    for (const it of items) {
      const left = it.flip ? it.x - 32 - it.w : it.x + 28;
      const right = left + it.w;
      let top = it.y - it.h / 2;
      for (let guard = 0; guard < 8; guard++) {
        const hit = placed.find((p) => left < p.right && right > p.left && top < p.bottom + 4 && top + it.h > p.top - 4);
        if (!hit) break;
        top = hit.bottom + 4;
      }
      placed.push({ left, right, top, bottom: top + it.h });
      const dy = Math.round(top - (it.y - it.h / 2));
      if (dy) it.n.style.setProperty('--hs-dy', `${dy}px`); else it.n.style.removeProperty('--hs-dy');
    }
  }

  function clear() {
    for (const [, entry] of pool) entry.node.remove();
    pool.clear();
  }

  try {
    world.onHotspots(update);
  } catch (err) {
    console.warn('[hotspots] world.onHotspots failed', err);
  }

  // A new chapter means a new scene: drop stale markers immediately.
  bus.on('chapter:enter', clear);
  window.addEventListener('resize', () => { blockersAt = 0; }, { passive: true });
  bus.on('view:change', () => { blockersAt = 0; });
  bus.on('research:open', () => { blockersAt = 0; });
  bus.on('research:close', () => { blockersAt = 0; });
}
