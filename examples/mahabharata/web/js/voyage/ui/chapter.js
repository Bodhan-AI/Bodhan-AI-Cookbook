// The chapter panel (#stage-ui): roundel + title, collapsible story, the central question as
// hero, further questions, related people/places, optional discoveries, the cinematic shot bar
// and the onward paths. Question cards mirror the REAL research state reported on the bus.
import { $, $$, el, esc, roman, humanize, safeHex, fmtSecs, prefersReducedMotion } from '../core/util.js';

const IC = {
  arrow: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
  back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5M11 6l-6 6 6 6"/></svg>',
  prev: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="m15 6-6 6 6 6"/></svg>',
  next: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"/></svg>',
  pin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/></svg>',
  lock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><rect x="5.5" y="10.5" width="13" height="9" rx="2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m5.5 12.5 4 4 9-9"/></svg>',
  leaf: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7.5c0-1.1.9-2 2-2h12a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/><path d="M8 9.5h8M8 12h8M8 14.5h5"/></svg>',
  spark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/></svg>',
  min: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="m14 6-6 6 6 6"/></svg>',
  branch: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="9" r="2"/><path d="M6 7v10M6 13c0-3 4-4 10-4"/></svg>',
};

const STATE_TEXT = {
  focusing: 'Opening the research…',
  queued: 'Waiting for the scribe (another question is being researched)…',
  pending: 'Walking the knowledge graph and consulting the model…',
  retrieved: 'Manuscripts retrieved — composing the answer…',
  rendering: 'Drawing the charts…',
  done: 'Answered — open the research to read it',
  error: 'The search faltered — try again',
};

/** "Chapter IV" style label + the roundel glyph. */
function numbering(ch) {
  const n = Number(ch.chapter_order);
  if (ch.kind === 'sub') {
    const m = /^ch(\d+)([a-z]+)$/i.exec(ch.id || '');
    return { label: 'A side path', glyph: m ? `${roman(+m[1])}<small>${esc(m[2])}</small>` : '◇' };
  }
  if (Number.isInteger(n)) return { label: `Chapter ${roman(n)}`, glyph: roman(n) };
  return { label: 'Chapter', glyph: esc(ch.chapter_order ?? '') };
}

export function initChapterPanel({ V, store, bus, world = null } = {}) {
  const host = $('#stage-ui');
  if (!host || !V) return;

  host.innerHTML = '';
  const root = el(`
    <div class="cp">
      <div class="cp-panel panel">
        <button type="button" class="cp-grip" aria-expanded="false" aria-label="Expand the chapter panel"><span></span></button>
        <button type="button" class="icon-btn cp-min" aria-label="Hide the chapter panel" title="Hide the panel to watch the scene">${IC.min}</button>
        <div class="cp-scroll scroll-fine" tabindex="-1">
          <article class="cp-body" aria-live="off"></article>
        </div>
        <div class="cp-dock" hidden>
          <span class="cp-dock-k">The question of this chapter</span>
          <button type="button" class="btn btn-gold btn-sm cp-dock-seek"><span>Seek the answer</span>${IC.arrow}</button>
        </div>
      </div>
      <button type="button" class="cp-tab" aria-label="Show the chapter panel" hidden>
        <span class="cp-tab-glyph"></span><span class="cp-tab-label">Chapter</span>
      </button>
      <p class="sr-only cp-announce" aria-live="polite"></p>
      <div class="shotbar panel" role="group" aria-label="Camera shots" hidden>
        <button type="button" class="icon-btn shot-prev" aria-label="Previous shot" aria-keyshortcuts="ArrowLeft">${IC.prev}</button>
        <div class="shot-mid">
          <div class="shot-meta"><span class="shot-k num"></span><span class="shot-name"></span></div>
          <div class="shot-dots" role="group" aria-label="Jump to shot"></div>
        </div>
        <button type="button" class="icon-btn shot-next" aria-label="Next shot" aria-keyshortcuts="ArrowRight">${IC.next}</button>
      </div>
    </div>`);
  host.appendChild(root);

  const panel = $('.cp-panel', root);
  const scroller = $('.cp-scroll', root);
  const body = $('.cp-body', root);
  const grip = $('.cp-grip', root);
  const tab = $('.cp-tab', root);
  const shotbar = $('.shotbar', root);
  const dock = $('.cp-dock', root);
  const dockBtn = $('.cp-dock-seek', root);

  // The chapter's main call to action stays reachable: while the central question is still below
  // the fold, a slim dock pinned to the panel's foot offers it (and hides once the question is in view).
  let dockSeek = null;
  function syncDock() {
    if (!dockSeek || !dockSeek.isConnected) { dock.hidden = true; return; }
    const r = dockSeek.getBoundingClientRect();
    const box = scroller.getBoundingClientRect();
    // shown only while the real button is still below the visible part of the panel
    dock.hidden = !(box.height > 0 && r.top > box.bottom - 8);
  }
  function watchCentral() {
    const central = $('.cp-central', body);
    dockSeek = central && $('.cp-seek', central);
    if (dockSeek) dockBtn.dataset.question = central.dataset.qid;
    syncDock();
  }
  scroller.addEventListener('scroll', syncDock, { passive: true });
  window.addEventListener('resize', syncDock);
  dockBtn.addEventListener('click', () => {
    const qid = dockBtn.dataset.question;
    if (qid) bus.emit('question:select', { questionId: qid, chapterId: chapter?.id });
  });

  let chapter = null;
  const qState = new Map();   // questionId -> {state, since}
  let ticker = null;

  // ------------------------------------------------------------------ helpers
  const charName = (id) => V.characterById.get(id)?.name || humanize(id);
  const locName = (id) => V.locationById.get(id)?.name || humanize(id);
  const evTitle = (id) => V.eventById.get(id)?.title || humanize(id);
  const chTitle = (id) => V.chapterById.get(id)?.title || humanize(id);

  function chip(type, id, label, color) {
    const c = safeHex(color);
    return `<button type="button" class="chip" data-type="${type}" data-entity="${esc(type)}:${esc(id)}">
      <span class="chip-dot"${c ? ` style="background:${c};color:${c}"` : ''}></span>${esc(label)}</button>`;
  }

  function questionStatus(q) {
    const live = qState.get(q.id);
    if (live && live.state !== 'done') return live.state;
    if (live && live.state === 'done') return 'done';
    return store.isAnswered(q.id) ? 'answered' : 'idle';
  }

  function statusHTML(q) {
    const st = questionStatus(q);
    if (st === 'idle') return '';
    if (st === 'answered') return `<span class="q-status is-answered">${IC.check}<span>Answered in this voyage</span></span>`;
    const live = qState.get(q.id);
    const busy = ['focusing', 'queued', 'pending', 'retrieved', 'rendering'].includes(st);
    const secs = busy && live ? ` <span class="num q-secs" data-since="${live.since}">${fmtSecs((performance.now() - live.since) / 1000)}</span>` : '';
    return `<span class="q-status is-${st}">${busy ? '<span class="spinner" aria-hidden="true"></span>' : st === 'done' ? IC.check : ''}<span>${esc(STATE_TEXT[st] || '')}</span>${secs}</span>`;
  }

  // ------------------------------------------------------------------ render
  function render(ch) {
    chapter = ch;
    const num = numbering(ch);
    const loc = V.locationById.get(ch.location_id);
    const sc = ch.story_context || {};
    const crumbs = Array.isArray(ch.navigation?.breadcrumbs) && ch.navigation.breadcrumbs.length
      ? ch.navigation.breadcrumbs : [sc.parva ? `${sc.parva}${/parva/i.test(sc.parva) ? '' : ' Parva'}` : null].filter(Boolean);
    const locked = !store.isUnlocked(ch);
    const prev = ch.previous_chapter && V.chapterById.get(ch.previous_chapter);
    const narrative = ch.narrative.filter((p) => typeof p === 'string' && p.trim());
    const cq = ch.central_question;
    const others = ch.questions.filter((q) => q && q.id && (!cq || q.id !== cq.id));
    const nexts = ch.next_chapters.filter((id) => V.chapterById.has(id));
    const people = ch.related_characters.filter(Boolean);
    const places = ch.related_locations.filter((id) => id && id !== ch.location_id);
    const moments = ch.related_events.filter(Boolean);
    const discoveries = ch.optional_discoveries.filter((d) => d && d.id);
    let d = 0; // stagger index for the entrance choreography
    const st = () => `style="--d:${d++}"`;

    body.innerHTML = `
      <header class="cp-head" ${st()}>
        <div class="cp-roundel" aria-hidden="true"><span class="cp-roundel-ring"></span><span class="cp-roundel-glyph">${num.glyph}</span></div>
        <div class="cp-head-text">
          <p class="cp-crumbs"><span class="eyebrow cp-crumbs-num">${esc(num.label)}</span>${crumbs.length ? `<span class="cp-crumbs-path">${crumbs.map((c, i) => `<span class="cp-crumb">${esc(c)}${i < crumbs.length - 1 ? '<span class="cp-crumb-sep" aria-hidden="true"> ·</span>' : ''}</span>`).join(' ')}</span>` : ''}</p>
          ${loc ? `<button type="button" class="cp-loc" data-entity="location:${esc(loc.id)}">${IC.pin}<span>${esc(loc.name)}</span></button>` : ''}
        </div>
      </header>
      <h1 class="cp-title display" id="cp-title" ${st()}>${esc(ch.title || humanize(ch.id))}</h1>
      ${ch.subtitle ? `<p class="cp-subtitle" ${st()}>${esc(ch.subtitle)}</p>` : ''}

      ${locked ? `
      <aside class="cp-locked" ${st()} aria-label="Ahead of the story">
        <span class="cp-locked-icon" aria-hidden="true">${IC.lock}</span>
        <span class="cp-locked-title">Ahead of the story</span>
        ${prev ? `<button type="button" class="cp-locked-go" data-chapter="${esc(prev.id)}" data-from="nav" title="The threads that lead here are spun in earlier chapters">${IC.back}<span>${esc(numbering(prev).label)}: ${esc(prev.title || prev.id)}</span></button>` : ''}
      </aside>` : ''}

      ${narrative.length || sc.previously ? `
      <section class="cp-story" ${st()} aria-label="The story">
        ${sc.previously ? `<p class="cp-previously"><span class="eyebrow">Previously</span> ${esc(sc.previously)}</p>` : ''}
        ${narrative.length ? `
        <div class="cp-narrative" id="cp-narrative" data-open="false">
          ${narrative.map((p, i) => `<p${i === 0 ? ' class="cp-first"' : ''}>${esc(p)}</p>`).join('')}
        </div>
        <button type="button" class="cp-read" aria-expanded="false" aria-controls="cp-narrative">
          <span class="cp-read-label">Read the story</span><span class="cp-read-rule" aria-hidden="true"></span>
        </button>` : ''}
      </section>` : ''}

      ${cq && cq.id ? `
      <section class="cp-central" ${st()} aria-labelledby="cp-cq-text" data-qid="${esc(cq.id)}">
        <span class="cp-central-corner tl" aria-hidden="true"></span><span class="cp-central-corner tr" aria-hidden="true"></span>
        <span class="cp-central-corner bl" aria-hidden="true"></span><span class="cp-central-corner br" aria-hidden="true"></span>
        <p class="eyebrow">The question of this chapter</p>
        <h2 class="cp-cq" id="cp-cq-text">${esc(cq.question || 'An unwritten question')}</h2>
        ${cq.deeper_context ? `<p class="cp-cq-context">${esc(cq.deeper_context)}</p>` : ''}
        <div class="cp-cq-actions">
          <button type="button" class="btn btn-gold cp-seek" data-question="${esc(cq.id)}">
            <span>${store.isAnswered(cq.id) ? 'Revisit the answer' : 'Seek the answer'}</span>${IC.arrow}
          </button>
        </div>
        <div class="cp-qstatus" data-status-for="${esc(cq.id)}" aria-live="polite">${statusHTML(cq)}</div>
        <p class="cp-cq-note">Researched live from the curated knowledge graph — nothing is pre-written.</p>
      </section>` : ''}

      ${sc.sources_note ? `
      <details class="cp-sources" ${st()}>
        <summary>${IC.leaf}<span class="cp-sources-k">On the sources</span></summary>
        <p>${esc(sc.sources_note)}</p>
      </details>` : ''}

      ${others.length ? `
      <section class="cp-section cp-questions" ${st()} aria-labelledby="cp-more-q">
        <h3 class="cp-h" id="cp-more-q"><span class="eyebrow">Further questions</span></h3>
        <ul class="cp-qlist">
          ${others.map((q, i) => `
          <li>
            <button type="button" class="q-card" data-question="${esc(q.id)}" data-qid="${esc(q.id)}" data-answered="${store.isAnswered(q.id)}">
              <span class="q-index num" aria-hidden="true">${String(i + 1).padStart(2, '0')}</span>
              <span class="q-main">
                <span class="q-text">${esc(q.question || '')}</span>
                ${q.deeper_context ? `<span class="q-ctx">${esc(q.deeper_context)}</span>` : ''}
                <span class="cp-qstatus" data-status-for="${esc(q.id)}">${statusHTML(q)}</span>
              </span>
              <span class="q-go" aria-hidden="true">${IC.arrow}</span>
            </button>
          </li>`).join('')}
        </ul>
      </section>` : ''}

      ${people.length || places.length || moments.length ? `
      <section class="cp-section cp-related" ${st()} aria-label="In this chapter">
        ${people.length ? `<div class="cp-rel"><h3 class="cp-h"><span class="eyebrow">Who walks here</span></h3>
          <div class="chip-row">${people.map((id) => chip('character', id, charName(id), V.characterById.get(id)?.visual_identity?.primary)).join('')}</div></div>` : ''}
        ${places.length ? `<div class="cp-rel"><h3 class="cp-h"><span class="eyebrow">Places</span></h3>
          <div class="chip-row">${places.map((id) => chip('location', id, locName(id))).join('')}</div></div>` : ''}
        ${moments.length ? `<div class="cp-rel"><h3 class="cp-h"><span class="eyebrow">Moments</span></h3>
          <div class="chip-row">${moments.map((id) => chip('event', id, evTitle(id))).join('')}</div></div>` : ''}
      </section>` : ''}

      ${discoveries.length ? `
      <section class="cp-section cp-discoveries" ${st()} aria-labelledby="cp-disc">
        <h3 class="cp-h" id="cp-disc"><span class="eyebrow">Optional discoveries</span></h3>
        <ul class="cp-disc-list">
          ${discoveries.map((x) => `
          <li><button type="button" class="disc-card" data-discovery="${esc(x.id)}" data-found="${store.isDiscovered(x.id)}">
            <span class="disc-icon" aria-hidden="true">${IC.spark}</span>
            <span class="disc-main">
              <span class="disc-type">${esc(x.type || 'discovery')}${store.isDiscovered(x.id) ? ' · discovered' : ''}</span>
              <span class="disc-title">${esc(x.title || humanize(x.target))}</span>
              ${x.teaser ? `<span class="disc-teaser">${esc(x.teaser)}</span>` : ''}
            </span>
          </button></li>`).join('')}
        </ul>
      </section>` : ''}

      <footer class="cp-onward" ${st()}>
        ${nexts.length ? `
          <button type="button" class="cp-continue" data-chapter="${esc(nexts[0])}" data-from="continue">
            <span class="cp-continue-k">Continue the Voyage <span aria-hidden="true">→</span></span>
            <span class="cp-continue-v">${esc(chTitle(nexts[0]))}</span>
          </button>
          ${nexts.length > 1 ? `
          <div class="cp-branches">
            <p class="cp-branches-k">${IC.branch}<span>Or turn aside</span></p>
            ${nexts.slice(1).map((id) => `<button type="button" class="btn btn-sm cp-branch" data-chapter="${esc(id)}" data-from="continue">${esc(chTitle(id))}</button>`).join('')}
          </div>` : ''}
        ` : `
          <div class="cp-end">
            <p class="cp-end-title">Here the road of the epic comes to rest.</p>
            <button type="button" class="btn btn-sm" data-view="timeline">Walk the whole timeline</button>
          </div>`}
        ${prev ? `<button type="button" class="btn btn-sm btn-ghost cp-prev" data-chapter="${esc(prev.id)}" data-from="nav">${IC.back}<span>${esc(prev.title || prev.id)}</span></button>` : ''}
      </footer>`;

    // Tab label for the minimised state
    $('.cp-tab-glyph', root).innerHTML = num.glyph;
    $('.cp-tab-label', root).textContent = ch.title || num.label;
    // Screen readers hear the arrival in a new chapter (the panel itself is aria-live="off").
    $('.cp-announce', root).textContent = `${num.label}: ${ch.title || humanize(ch.id)}${loc ? `, ${loc.name}` : ''}`;

    // Fresh entrance
    root.classList.remove('is-entering');
    void root.offsetWidth; // restart the CSS choreography
    root.classList.add('is-entering');
    scroller.scrollTop = 0;
    setExpanded(false);
    renderShots({ index: 0, count: ch.shots.length, shot: ch.shots[0] || null });
    watchCentral();
    setTimeout(syncDock, 1000); // after the entrance choreography settles
    refreshDock();
    syncTicker();
  }

  function refreshDock() {
    const qid = dockBtn.dataset.question;
    if (qid) $('span', dockBtn).textContent = store.isAnswered(qid) ? 'Revisit the answer' : 'Seek the answer';
  }

  // ------------------------------------------------------------------ question state (real research events)
  function refreshStatuses() {
    if (!chapter) return;
    // An unlock condition met while this chapter is open (e.g. a question answered) retires the note.
    const aheadNote = $('.cp-locked', body);
    if (aheadNote && store.isUnlocked(chapter)) aheadNote.remove();
    for (const node of $$('[data-status-for]', body)) {
      const qid = node.dataset.statusFor;
      const q = V.questionById.get(qid)?.question;
      if (q) node.innerHTML = statusHTML(q);
    }
    for (const card of $$('.q-card', body)) {
      const qid = card.dataset.qid;
      card.dataset.answered = String(store.isAnswered(qid));
      card.dataset.state = qState.get(qid)?.state || '';
    }
    const central = $('.cp-central', body);
    if (central) {
      const qid = central.dataset.qid;
      central.dataset.state = qState.get(qid)?.state || '';
      const seek = $('.cp-seek span', central);
      if (seek) seek.textContent = store.isAnswered(qid) ? 'Revisit the answer' : 'Seek the answer';
    }
    refreshDock();
    for (const card of $$('.disc-card', body)) {
      const found = store.isDiscovered(card.dataset.discovery);
      card.dataset.found = String(found);
    }
  }

  function syncTicker() {
    const busy = [...qState.values()].some((s) => ['focusing', 'queued', 'pending', 'retrieved', 'rendering'].includes(s.state));
    if (busy && !ticker) {
      ticker = setInterval(() => {
        for (const n of $$('.q-secs', body)) n.textContent = fmtSecs((performance.now() - Number(n.dataset.since)) / 1000);
      }, 500);
    } else if (!busy && ticker) {
      clearInterval(ticker);
      ticker = null;
    }
  }

  bus.on('research:state', (p = {}) => {
    const qid = p.questionId;
    if (!qid) return;
    const prev = qState.get(qid);
    // Keep the original start time across intermediate states so the clock is honest.
    const since = prev && !['done', 'error'].includes(prev.state) ? prev.since : performance.now();
    qState.set(qid, { state: p.state, since });
    refreshStatuses();
    syncTicker();
  });

  // ------------------------------------------------------------------ shots
  function renderShots({ index = 0, count = 0, shot = null } = {}) {
    const usable = !!(world && world.supported) && count > 0;
    shotbar.hidden = !usable;
    if (!usable) return;
    $('.shot-k', shotbar).textContent = `Shot ${index + 1} / ${count}`;
    $('.shot-name', shotbar).textContent = shot?.shot_name || '';
    const dots = $('.shot-dots', shotbar);
    if (dots.childElementCount !== count) {
      dots.innerHTML = Array.from({ length: count }, (_, i) =>
        `<button type="button" class="shot-dot" data-shot="${i}" aria-label="Shot ${i + 1}${chapter?.shots[i]?.shot_name ? `: ${esc(chapter.shots[i].shot_name)}` : ''}"><i></i></button>`).join('');
    }
    $$('.shot-dot', dots).forEach((b, i) => {
      b.classList.toggle('is-done', i < index);
      b.classList.toggle('is-active', i === index);
      if (i === index) b.setAttribute('aria-current', 'step'); else b.removeAttribute('aria-current');
      const dur = Number(chapter?.shots[i]?.duration);
      b.style.setProperty('--dur', `${Number.isFinite(dur) && dur > 0 ? dur : 8}s`);
    });
    // restart the fill on the active dot
    const active = $('.shot-dot.is-active', dots);
    if (active) { active.classList.remove('is-filling'); void active.offsetWidth; active.classList.add('is-filling'); }
    $('.shot-prev', shotbar).disabled = index <= 0;
    $('.shot-next', shotbar).disabled = index >= count - 1;
  }

  bus.on('shot:change', (p = {}) => {
    if (!chapter || (p.chapterId && p.chapterId !== chapter.id)) return;
    renderShots(p);
  });
  const safe = (fn) => { try { fn(); } catch (e) { console.warn('[chapter] shot control failed', e); } };
  $('.shot-prev', shotbar).addEventListener('click', () => safe(() => world.prevShot()));
  $('.shot-next', shotbar).addEventListener('click', () => safe(() => world.nextShot()));
  $('.shot-dots', shotbar).addEventListener('click', (e) => {
    const b = e.target.closest('[data-shot]');
    if (b) safe(() => world.playShot(Number(b.dataset.shot)));
  });

  // ------------------------------------------------------------------ interactions (delegated)
  body.addEventListener('click', (e) => {
    const t = e.target;

    const read = t.closest('.cp-read');
    if (read) {
      const nar = $('#cp-narrative', body);
      const open = nar.dataset.open !== 'true';
      nar.dataset.open = String(open);
      read.setAttribute('aria-expanded', String(open));
      $('.cp-read-label', read).textContent = open ? 'Fold the story' : 'Read the story';
      if (!open) read.scrollIntoView({ block: 'nearest', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
      setTimeout(syncDock, 850); // the narrative's max-height transition moves the question
      return;
    }

    const q = t.closest('[data-question]');
    if (q) {
      bus.emit('question:select', { questionId: q.dataset.question, chapterId: chapter?.id });
      return;
    }

    const ent = t.closest('[data-entity]');
    if (ent) {
      const [type, ...rest] = ent.dataset.entity.split(':');
      bus.emit('entity:open', { type, id: rest.join(':') });
      return;
    }

    const disc = t.closest('[data-discovery]');
    if (disc) {
      const dObj = chapter?.optional_discoveries.find((x) => x.id === disc.dataset.discovery);
      if (!dObj) return;
      store.discover(dObj.id);
      if (dObj.type === 'chapter' && V.chapterById.has(dObj.target)) bus.emit('chapter:enter', { chapterId: dObj.target, from: 'discovery' });
      else if (dObj.type === 'question') bus.emit('question:select', { questionId: dObj.target, chapterId: V.questionById.get(dObj.target)?.chapter?.id || chapter.id });
      else if (dObj.target) bus.emit('entity:open', { type: dObj.type || 'discovery', id: dObj.target });
      return;
    }

    const go = t.closest('[data-chapter]');
    if (go) {
      bus.emit('chapter:enter', { chapterId: go.dataset.chapter, from: go.dataset.from || 'continue' });
      return;
    }

    const v = t.closest('[data-view]');
    if (v) bus.emit('view:change', { view: v.dataset.view });
  });

  // ------------------------------------------------------------------ mobile sheet + desktop minimise
  function setExpanded(open) {
    root.classList.toggle('is-expanded', open);
    grip.setAttribute('aria-expanded', String(open));
    grip.setAttribute('aria-label', open ? 'Collapse the chapter panel' : 'Expand the chapter panel');
    setTimeout(syncDock, 550); // the sheet height animates (phones)
  }
  grip.addEventListener('click', () => setExpanded(!root.classList.contains('is-expanded')));
  // Scrolling inside the peeking sheet expands it (phones).
  scroller.addEventListener('scroll', () => {
    if (scroller.scrollTop > 24 && !root.classList.contains('is-expanded') && window.matchMedia('(max-width: 760px)').matches) setExpanded(true);
  }, { passive: true });

  function setMinimised(min, { focus = true } = {}) {
    root.classList.toggle('is-min', min);
    tab.hidden = !min;
    panel.setAttribute('aria-hidden', String(min));
    panel.inert = min;
    if (focus) (min ? tab : $('.cp-min', root)).focus({ preventScroll: true });
  }
  $('.cp-min', root).addEventListener('click', () => setMinimised(true));
  tab.addEventListener('click', () => setMinimised(false));
  // Phones have no minimise control: a panel minimised on a wide window must come back (and lose
  // `inert`) when the layout switches to the bottom sheet.
  try {
    const phone = window.matchMedia('(max-width: 760px)');
    const onPhone = () => { if (phone.matches && root.classList.contains('is-min')) setMinimised(false, { focus: false }); };
    if (phone.addEventListener) phone.addEventListener('change', onPhone);
    else if (phone.addListener) phone.addListener(onPhone);
  } catch { /* matchMedia unavailable */ }

  // ------------------------------------------------------------------ bus
  bus.on('chapter:enter', ({ chapterId } = {}) => {
    const ch = V.chapterById.get(chapterId);
    if (!ch) return;
    if (root.classList.contains('is-min')) setMinimised(false, { focus: false });
    render(ch);
  });
  bus.on('store:change', refreshStatuses);
}
