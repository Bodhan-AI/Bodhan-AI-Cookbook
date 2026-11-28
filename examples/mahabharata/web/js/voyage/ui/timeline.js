// ui/timeline.js — TIMELINE (#view-timeline)
// A horizontal "celestial chronicle": V.events laid along a golden thread, grouped into the 18 parva bands.
// Scroll / drag / wheel / keys to travel; a "You are here" beam marks the current chapter's anchor event.
// Visiting a chapter keeps your place in the chronicle (scroll position and selection survive the trip).

import { esc, clamp, prefersReducedMotion } from '../core/util.js';

const PARVAS = [
  ['Adi', 'Ādi', 'The Beginnings'],
  ['Sabha', 'Sabhā', 'The Assembly Hall'],
  ['Vana', 'Vana', 'The Forest Years'],
  ['Virata', 'Virāṭa', "Virāṭa's Court"],
  ['Udyoga', 'Udyoga', 'The Effort for Peace'],
  ['Bhishma', 'Bhīṣma', "Bhīṣma's Command"],
  ['Drona', 'Droṇa', "Droṇa's Command"],
  ['Karna', 'Karṇa', "Karṇa's Command"],
  ['Shalya', 'Śalya', "Śalya's Command"],
  ['Sauptika', 'Sauptika', 'The Sleeping Warriors'],
  ['Stri', 'Strī', 'The Women'],
  ['Shanti', 'Śānti', 'Peace'],
  ['Anushasana', 'Anuśāsana', 'Instruction'],
  ['Ashvamedhika', 'Āśvamedhika', 'The Horse Sacrifice'],
  ['Ashramavasika', 'Āśramavāsika', 'The Hermitage'],
  ['Mausala', 'Mausala', 'The Clubs'],
  ['Mahaprasthanika', 'Mahāprasthānika', 'The Great Journey'],
  ['Svargarohana', 'Svargārohaṇa', 'Ascent to Heaven'],
];
const ROMAN = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X', 'XI', 'XII', 'XIII', 'XIV', 'XV', 'XVI', 'XVII', 'XVIII'];

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
function chapterLabel(ch) {
  if (!ch) return '';
  if (ch.kind === 'sub') return 'Side path';
  return typeof ch.chapter_order === 'number' ? `Chapter ${Math.round(ch.chapter_order)}` : 'Chapter';
}

export function initTimeline({ V, store, bus }) {
  const root = ensureSection('view-timeline');
  const events = [...(V?.events || [])].filter((e) => e && e.id).sort((a, b) => (+a.order || 0) - (+b.order || 0));
  const eventById = V?.eventById instanceof Map ? V.eventById : new Map(events.map((e) => [e.id, e]));
  const chapterById = V?.chapterById instanceof Map ? V.chapterById : new Map((V?.chapters || []).map((c) => [c.id, c]));
  const charById = V?.characterById instanceof Map ? V.characterById : new Map((V?.characters || []).map((c) => [c.id, c]));
  const locById = V?.locationById instanceof Map ? V.locationById : new Map((V?.locations || []).map((l) => [l.id, l]));

  const st = () => store?.state || {};
  const isVisited = (id) => { try { return store?.isVisited ? !!store.isVisited(id) : (st().visited || []).includes(id); } catch { return false; } };
  const isUnlocked = (ch) => { try { return store?.isUnlocked ? !!store.isUnlocked(ch) : true; } catch { return true; } };

  // group into parva bands (unknown parva → a final "Unplaced" band, only if needed)
  const bands = PARVAS.map(([key, iast, gloss], i) => ({ key, iast, gloss, roman: ROMAN[i], events: [] }));
  const bandByKey = new Map(bands.map((b) => [b.key, b]));
  const other = { key: 'Other', iast: 'Unplaced', gloss: 'Parva not recorded', roman: '·', events: [] };
  for (const e of events) (bandByKey.get(e.parva) || other).events.push(e);
  if (other.events.length) bands.push(other);

  /* ---------------- DOM */
  root.classList.add('vw', 'tl');
  root.setAttribute('role', 'region');
  root.setAttribute('aria-labelledby', 'view-timeline-title');
  root.innerHTML = `
    <header class="vw-head">
      <div class="vw-titles">
        <p class="vw-eyebrow">The celestial chronicle · eighteen parvas</p>
        <h2 class="vw-title" id="view-timeline-title">Timeline of the Epic</h2>
      </div>
      <div class="vw-tools" role="group" aria-label="Timeline controls">
        <button type="button" class="vw-iconbtn" data-act="prev" aria-label="Previous parva">‹</button>
        <button type="button" class="vw-iconbtn" data-act="next" aria-label="Next parva">›</button>
        <button type="button" class="btn btn-ghost vw-btn sm" data-act="here">Your place</button>
      </div>
      <button type="button" class="vw-close" data-act="close" aria-label="Close the timeline and return to the journey"><span aria-hidden="true">×</span></button>
    </header>
    <nav class="tl-parvas" aria-label="Jump to a parva">
      ${bands.map((b) => `<button type="button" class="tl-pv${b.events.length ? '' : ' is-empty'}" data-act="parva" data-parva="${esc(b.key)}"
          aria-label="${esc(b.iast)} Parva, ${b.events.length} event${b.events.length === 1 ? '' : 's'}"><span class="r">${esc(b.roman)}</span><span class="n">${esc(b.iast)}</span></button>`).join('')}
    </nav>
    <div class="tl-scroller" tabindex="0" aria-label="Timeline. Scroll sideways, drag, or use the arrow keys.">
      ${events.length ? `<div class="tl-track">${bands.map(bandHTML).join('')}</div>`
        : `<p class="vw-empty">The chronicle's events have not loaded yet — the timeline will fill once the voyage data is available.</p>`}
    </div>
    <p class="tl-foot">Narrative order as the epic tells it — the Mahabharata's recensions differ in detail and sequence, and it gives no fixed historical dates.</p>
    <aside class="tl-detail vw-sheet" hidden aria-labelledby="tl-detail-title"></aside>`;

  function bandHTML(b, bi) {
    const n = b.events.length;
    return `<section class="tl-band${n ? '' : ' is-empty'}" data-parva="${esc(b.key)}" style="--n:${Math.max(n, 1)}" aria-labelledby="tl-band-${bi}">
      <header class="tl-band-head">
        <span class="tl-band-num" aria-hidden="true">${esc(b.roman)}</span>
        <h3 id="tl-band-${bi}" class="tl-band-name">${esc(b.iast)} Parva</h3>
        <p class="tl-band-gloss">${esc(b.gloss)}</p>
      </header>
      ${n ? `<ol class="tl-events">${b.events.map((e, i) => eventHTML(e, i)).join('')}</ol>`
          : `<div class="tl-events tl-quiet"><p>No milestone of this voyage is charted in this parva.</p></div>`}
    </section>`;
  }

  function eventHTML(e, i) {
    const ch = chapterById.get(e.chapter);
    const loc = locById.get(e.location_id);
    const meta = [loc?.name, ch ? chapterLabel(ch) : null].filter(Boolean).join(' · ');
    return `<li class="tl-ev ${i % 2 ? 'below' : 'above'}" data-id="${esc(e.id)}">
      <span class="tl-node" aria-hidden="true"><span class="tl-star"></span></span>
      <span class="tl-stem" aria-hidden="true"></span>
      <button type="button" class="tl-card" data-act="event" data-id="${esc(e.id)}">
        <span class="tl-card-meta">${esc(meta)}</span>
        <span class="tl-card-title">${esc(e.title || e.id)}</span>
        ${e.summary ? `<span class="tl-card-sum">${esc(e.summary)}</span>` : ''}
        ${e.uncertainty ? `<span class="tl-card-unc"><span aria-hidden="true">◇</span> Traditions differ</span>` : ''}
        <span class="tl-card-here" aria-hidden="true">You are here</span>
      </button>
    </li>`;
  }

  const scroller = root.querySelector('.tl-scroller');
  const detail = root.querySelector('.tl-detail');
  const hereBtn = root.querySelector('[data-act="here"]');
  // hidden note read by screen readers on the "here" card only
  const srNote = document.createElement('span');
  srNote.id = 'tl-here-note'; srNote.className = 'vh';
  root.appendChild(srNote);

  /* ---------------- state */
  let selectedId = null, savedLeft = null, active = false, lastFocus = null;

  function hereEventId() {
    const ch = chapterById.get(st().current);
    if (!ch) return null;
    const anchor = ch?.navigation?.timeline_anchor;
    if (anchor && eventById.has(anchor)) return anchor;
    const e = events.find((x) => x.chapter === ch.id);
    return e ? e.id : null;
  }

  function refreshStates() {
    const here = hereEventId();
    const cur = st().current;
    root.querySelectorAll('.tl-ev').forEach((li) => {
      const e = eventById.get(li.dataset.id);
      const ch = e ? chapterById.get(e.chapter) : null;
      li.classList.toggle('is-here', li.dataset.id === here);
      li.classList.toggle('is-visited', !!ch && isVisited(ch.id));
      li.classList.toggle('is-current-ch', !!ch && ch.id === cur);
      li.classList.toggle('is-locked', !!ch && !isVisited(ch.id) && !isUnlocked(ch));
      li.classList.toggle('is-selected', li.dataset.id === selectedId);
      const btn = li.querySelector('.tl-card');
      if (btn) {
        if (li.dataset.id === here) btn.setAttribute('aria-describedby', 'tl-here-note');
        else btn.removeAttribute('aria-describedby');
      }
    });
    srNote.textContent = here ? 'You are here.' : '';
    if (hereBtn) {
      hereBtn.disabled = !here;
      hereBtn.title = here ? 'Scroll to your current place in the story' : 'Enter a chapter to mark your place';
    }
    if (active) requestAnimationFrame(fitSummaries); // the "You are here" badge changes a card's height
  }

  /* ---------------- scrolling helpers */
  const behavior = () => (reducedMotion() ? 'auto' : 'smooth');
  function scrollToEl(el, align = 'center') {
    if (!el) return;
    const sr = scroller.getBoundingClientRect(), er = el.getBoundingClientRect();
    const delta = align === 'start' ? er.left - sr.left - 24 : (er.left + er.width / 2) - (sr.left + sr.width / 2);
    scroller.scrollTo({ left: scroller.scrollLeft + delta, behavior: behavior() });
  }
  const bandEls = () => [...root.querySelectorAll('.tl-band')];
  function currentBandIndex() {
    const sr = scroller.getBoundingClientRect();
    const probe = sr.left + sr.width * 0.35;
    const els = bandEls();
    let idx = 0;
    els.forEach((b, i) => { if (b.getBoundingClientRect().left <= probe) idx = i; });
    return idx;
  }
  function markParvaNav() {
    const idx = currentBandIndex();
    root.querySelectorAll('.tl-pv').forEach((b, i) => {
      if (i === idx) b.setAttribute('aria-current', 'true'); else b.removeAttribute('aria-current');
    });
    const curBtn = root.querySelector('.tl-pv[aria-current]');
    const nav = root.querySelector('.tl-parvas');
    if (curBtn && nav && nav.scrollWidth > nav.clientWidth) {
      const nr = nav.getBoundingClientRect(), br = curBtn.getBoundingClientRect();
      if (br.left < nr.left || br.right > nr.right) nav.scrollLeft += br.left - nr.left - nr.width / 2 + br.width / 2;
    }
  }
  let scrollRaf = 0;
  scroller.addEventListener('scroll', () => {
    if (active) savedLeft = scroller.scrollLeft;
    if (!scrollRaf) scrollRaf = requestAnimationFrame(() => { scrollRaf = 0; markParvaNav(); });
  }, { passive: true });

  // vertical wheel → horizontal travel
  scroller.addEventListener('wheel', (e) => {
    if (e.ctrlKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
    if (scroller.scrollWidth <= scroller.clientWidth) return;
    e.preventDefault();
    scroller.scrollLeft += e.deltaY * (e.deltaMode === 1 ? 32 : 1);
  }, { passive: false });

  // mouse drag-to-scroll (touch scrolls natively)
  let drag = null, dragged = false;
  scroller.addEventListener('pointerdown', (e) => {
    if (e.pointerType !== 'mouse' || e.button !== 0) return;
    drag = { x: e.clientX, left: scroller.scrollLeft, id: e.pointerId };
    dragged = false;
  });
  scroller.addEventListener('pointermove', (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    const dx = e.clientX - drag.x;
    if (!dragged && Math.abs(dx) > 5) { dragged = true; scroller.setPointerCapture?.(e.pointerId); scroller.classList.add('is-dragging'); }
    if (dragged) scroller.scrollLeft = drag.left - dx;
  });
  const endDrag = () => { drag = null; scroller.classList.remove('is-dragging'); };
  scroller.addEventListener('pointerup', endDrag);
  scroller.addEventListener('pointercancel', endDrag);
  // swallow the click that ends a drag
  scroller.addEventListener('click', (e) => { if (dragged) { e.stopPropagation(); e.preventDefault(); dragged = false; } }, true);

  // keyboard: arrows move between event cards; Home/End jump to the ends
  scroller.addEventListener('keydown', (e) => {
    const cards = [...root.querySelectorAll('.tl-card')];
    if (!cards.length) return;
    const i = cards.indexOf(document.activeElement);
    let next = null;
    if (e.key === 'ArrowRight') next = i < 0 ? null : cards[Math.min(cards.length - 1, i + 1)];
    else if (e.key === 'ArrowLeft') next = i < 0 ? null : cards[Math.max(0, i - 1)];
    else if (e.key === 'Home') next = cards[0];
    else if (e.key === 'End') next = cards[cards.length - 1];
    if (next) { e.preventDefault(); e.stopPropagation(); next.focus({ preventScroll: true }); scrollToEl(next); }
    else if (i < 0 && (e.key === 'ArrowRight' || e.key === 'ArrowLeft')) {
      e.preventDefault(); e.stopPropagation();
      scroller.scrollBy({ left: e.key === 'ArrowRight' ? 260 : -260, behavior: behavior() });
    }
  });

  /* ---------------- actions */
  root.addEventListener('click', (e) => {
    const b = e.target.closest?.('[data-act]');
    if (!b || !root.contains(b)) return;
    const act = b.dataset.act;
    if (act === 'close') close();
    // detail === 0: keyboard / assistive activation → move focus into the detail panel
    else if (act === 'event') openDetail(b.dataset.id, { focus: e.detail === 0 });
    else if (act === 'detail-close') closeDetail();
    else if (act === 'parva') scrollToEl(root.querySelector(`.tl-band[data-parva="${CSS.escape(b.dataset.parva)}"]`), 'start');
    else if (act === 'prev' || act === 'next') {
      const els = bandEls();
      const idx = clamp(currentBandIndex() + (act === 'next' ? 1 : -1), 0, els.length - 1);
      scrollToEl(els[idx], 'start');
    }
    else if (act === 'here') goHere();
    else if (act === 'visit') visit(b.dataset.chapter);
    else if (act === 'char') bus?.emit?.('entity:open', { type: 'character', id: b.dataset.id });
    else if (act === 'loc') bus?.emit?.('entity:open', { type: 'location', id: b.dataset.id });
  });
  root.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    e.stopPropagation(); e.preventDefault();
    if (!detail.hidden) closeDetail(); else close();
  });

  function goHere() {
    const id = hereEventId();
    const li = id && root.querySelector(`.tl-ev[data-id="${CSS.escape(id)}"]`);
    if (li) scrollToEl(li);
  }

  function openDetail(id, { focus = true } = {}) {
    const e = eventById.get(id);
    if (!e) return;
    lastFocus = root.querySelector(`.tl-card[data-id="${CSS.escape(id)}"]`);
    selectedId = id;
    const ch = chapterById.get(e.chapter);
    const loc = locById.get(e.location_id);
    const band = bandByKey.get(e.parva);
    const visited = ch && isVisited(ch.id), locked = ch && !visited && !isUnlocked(ch), current = ch && ch.id === st().current;
    const chars = (Array.isArray(e.characters) ? e.characters : []).filter((cid) => typeof cid === 'string')
      .map((cid) => charById.get(cid) || { id: cid, name: cid, missing: true });
    detail.innerHTML = `
      <div class="vw-sheet-grip" aria-hidden="true"></div>
      <button type="button" class="vw-close sm" data-act="detail-close" aria-label="Close event details"><span aria-hidden="true">×</span></button>
      <p class="vw-eyebrow">${esc([band ? `${band.iast} Parva` : e.parva, loc?.name].filter(Boolean).join(' · '))}</p>
      <h3 class="tl-detail-title" id="tl-detail-title" tabindex="-1">${esc(e.title || e.id)}</h3>
      ${e.summary ? `<p class="tl-detail-sum">${esc(e.summary)}</p>` : ''}
      ${e.uncertainty ? `<div class="vw-caveat"><span class="vw-label">Where traditions differ</span><p>${esc(e.uncertainty)}</p></div>` : ''}
      ${chars.length ? `<h4 class="vw-h4">Who is present</h4><div class="vw-chips">${chars.map((c) => (c.missing
        ? `<span class="chip vw-chip static">${esc(c.id)}</span>` // no profile in the data: show the id, don't offer a dead link
        : `<button type="button" class="chip vw-chip" data-act="char" data-id="${esc(c.id)}">${esc(c.name || c.id)}</button>`)).join('')}</div>` : ''}
      <div class="tl-detail-actions">
        ${ch ? `<div class="tl-detail-ch">
            <span class="vw-label">${esc(chapterLabel(ch))}${current ? ' · you are here' : visited ? ' · visited' : locked ? ' · ahead in the story' : ''}</span>
            <span class="tl-detail-ch-title">${esc(ch.title || ch.id)}</span>
            ${locked ? `<span class="vw-muted">You'd be jumping ahead — the way is still open.</span>` : ''}
          </div>
          <button type="button" class="btn btn-gold vw-btn" data-act="visit" data-chapter="${esc(ch.id)}">${current ? 'Return to this chapter' : 'Visit'}</button>`
        : `<p class="vw-muted">The chapter for this event is not available in this voyage yet.</p>`}
        ${loc ? `<button type="button" class="btn btn-ghost vw-btn" data-act="loc" data-id="${esc(loc.id)}">Show on the map</button>` : ''}
      </div>
      <p class="vw-muted tl-keep">Your place in the chronicle is kept — press T or choose Timeline to come back.</p>`;
    detail.hidden = false;
    root.classList.add('has-detail');
    refreshStates();
    if (focus) requestAnimationFrame(() => detail.querySelector('#tl-detail-title')?.focus({ preventScroll: true }));
  }

  function closeDetail({ silent = false } = {}) {
    if (detail.hidden) return;
    detail.hidden = true;
    root.classList.remove('has-detail');
    selectedId = null;
    refreshStates();
    if (!silent && lastFocus && root.contains(lastFocus)) lastFocus.focus({ preventScroll: true });
  }

  function visit(chapterId) {
    if (!chapterId) return;
    savedLeft = scroller.scrollLeft; // the chronicle stays where you left it
    bus?.emit?.('view:change', { view: 'journey' });
    ensureBodyView('journey');
    bus?.emit?.('chapter:enter', { chapterId, from: 'timeline' });
  }

  function close() {
    bus?.emit?.('view:change', { view: 'journey' });
    ensureBodyView('journey');
  }

  /* ---------------- lifecycle */
  let shownOnce = false;
  function onShow() {
    active = true;
    refreshStates();
    if (!shownOnce) {
      shownOnce = true;
      const id = hereEventId();
      const li = id && root.querySelector(`.tl-ev[data-id="${CSS.escape(id)}"]`);
      if (li) requestAnimationFrame(() => scrollToElInstant(li)); // first visit: land on your place
    } else if (savedLeft != null) {
      scroller.scrollLeft = savedLeft;
    }
    markParvaNav();
    requestAnimationFrame(fitSummaries);
  }
  // Cards have a max-height (half the track). Clamp each summary to the whole lines that fit, so a card never
  // ends on a half-sliced line of text; a summary with no room at all is hidden (the detail panel still has it).
  function fitSummaries() {
    if (!active) return;
    for (const sum of root.querySelectorAll('.tl-card-sum')) {
      const card = sum.closest('.tl-card');
      if (!card) continue;
      sum.style.webkitLineClamp = ''; sum.style.display = '';
      const lh = parseFloat(getComputedStyle(sum).lineHeight) || 19;
      const over = card.scrollHeight - card.clientHeight;
      if (over <= 1) continue;
      const lines = Math.floor((sum.getBoundingClientRect().height - over) / lh + 0.02);
      if (lines >= 1) sum.style.webkitLineClamp = String(lines);
      else sum.style.display = 'none';
    }
  }
  let fitRaf = 0;
  window.addEventListener('resize', () => { cancelAnimationFrame(fitRaf); fitRaf = requestAnimationFrame(fitSummaries); });
  function scrollToElInstant(el) {
    const sr = scroller.getBoundingClientRect(), er = el.getBoundingClientRect();
    scroller.scrollLeft += (er.left + er.width / 2) - (sr.left + sr.width / 2);
    savedLeft = scroller.scrollLeft;
  }
  function onHide() { active = false; }
  let showPending = false;
  function sync() {
    const now = document.body.classList.contains('view-timeline');
    if (now && !active && !showPending) {
      showPending = true; // several class mutations in one frame must not queue several onShow calls
      requestAnimationFrame(() => { showPending = false; if (!active && document.body.classList.contains('view-timeline')) onShow(); });
    } else if (!now && active) onHide();
  }
  new MutationObserver(sync).observe(document.body, { attributes: true, attributeFilter: ['class'] });
  sync();

  bus?.on?.('view:change', (p) => { if (p?.view) lastView = p.view; if (p?.view === 'timeline') ensureBodyView('timeline'); });
  bus?.on?.('ui:escape', (evt) => { if (active && !detail.hidden) { closeDetail(); if (evt) evt.handled = true; } });
  bus?.on?.('store:change', refreshStates);
  bus?.on?.('chapter:entered', refreshStates);
  bus?.on?.('entity:open', (p) => {
    if (p?.type !== 'event' || !eventById.has(p.id)) return;
    if (!document.body.classList.contains('view-timeline')) { bus.emit('view:change', { view: 'timeline' }); ensureBodyView('timeline'); }
    shownOnce = true; // an explicit request wins over "scroll to your place"
    setTimeout(() => {
      const li = root.querySelector(`.tl-ev[data-id="${CSS.escape(p.id)}"]`);
      if (li) scrollToElInstant(li);
      openDetail(p.id, { focus: true });
    }, 60);
  });

  refreshStates();
}
