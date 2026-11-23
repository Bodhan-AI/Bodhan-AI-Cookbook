// Top navigation, chapter rail, settings popover, audio toggle and the mobile tab bar.
// Nav never touches the world/audio directly for settings: it writes the store and emits
// 'settings:change'; main.js applies the effects (single source of truth).
import { $, $$, el, esc, roman, humanize } from '../core/util.js';

const VIEWS = [
  { id: 'journey', label: 'Journey', key: 'J' },
  { id: 'map', label: 'Epic Map', key: 'M' },
  { id: 'characters', label: 'Characters', key: 'C' },
  { id: 'knowledge', label: 'Knowledge', key: 'K' },
  { id: 'timeline', label: 'Timeline', key: 'T' },
];

const I = {
  journey: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19c3-1 4-5 8-5s5 3 8 2"/><circle cx="6" cy="7" r="2"/><path d="M12 4l1.2 2.6L16 7l-2 2 .5 2.8L12 10.5 9.5 11.8 10 9 8 7l2.8-.4z"/></svg>',
  map: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6.5 9 4l6 2.5L21 4v13.5L15 20l-6-2.5L3 20z"/><path d="M9 4v13.5M15 6.5V20"/></svg>',
  characters: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3"/><path d="M3.5 19c.8-3.2 3-5 5.5-5s4.7 1.8 5.5 5"/><circle cx="17" cy="9" r="2.3"/><path d="M15.5 14.3c2.3-.3 4.2 1.2 5 4.2"/></svg>',
  knowledge: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5.5C6.5 4.5 9.5 4.5 12 6c2.5-1.5 5.5-1.5 8-.5V19c-2.5-1-5.5-1-8 .5-2.5-1.5-5.5-1.5-8-.5z"/><path d="M12 6v13.5"/></svg>',
  timeline: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h18"/><circle cx="6" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="18" cy="12" r="1.8"/><path d="M6 6v3.5M12 15v3.5M18 6v3.5"/></svg>',
  settings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/></svg>',
  soundOn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9.5h3.5L12 6v12l-4.5-3.5H4z"/><path d="M15.5 9.2a4 4 0 0 1 0 5.6M18 6.8a7.5 7.5 0 0 1 0 10.4"/></svg>',
  soundOff: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9.5h3.5L12 6v12l-4.5-3.5H4z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/></svg>',
  back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M10 6 4 12l6 6"/><path d="M4 12h11a5 5 0 0 1 5 5v1"/></svg>',
  rail: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="5" r="1.6"/><circle cx="6" cy="12" r="1.6"/><circle cx="6" cy="19" r="1.6"/><path d="M6 6.6v3.8M6 13.6v3.8M10 5h10M10 12h10M10 19h7"/></svg>',
  lock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><rect x="5.5" y="10.5" width="13" height="9" rx="2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m5.5 12.5 4 4 9-9"/></svg>',
  chevron: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"/></svg>',
  close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  external: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
};

/** The brand mark: a fine-line golden mandala (also reused by other modules if needed). */
export const MANDALA_MARK = `
<svg class="brand-mandala" viewBox="0 0 48 48" aria-hidden="true">
  <g fill="none" stroke="currentColor" stroke-width="1">
    <circle cx="24" cy="24" r="21.5" opacity=".55"/>
    <circle cx="24" cy="24" r="16"/>
    <circle cx="24" cy="24" r="4.2"/>
    ${Array.from({ length: 8 }, (_, i) => `<path transform="rotate(${i * 45} 24 24)" d="M24 8.5c3 4 3 7.5 0 11.3-3-3.8-3-7.3 0-11.3z"/>`).join('')}
    ${Array.from({ length: 16 }, (_, i) => `<path transform="rotate(${i * 22.5} 24 24)" d="M24 2.5v2.5" opacity=".7"/>`).join('')}
  </g>
  <circle cx="24" cy="24" r="1.6" fill="currentColor"/>
</svg>`;

export function initNav({ V, store, bus, audio, world } = {}) {
  const top = $('#topnav');
  const rail = $('#chapter-rail');
  if (!top || !V) return;

  let view = VIEWS.find((v) => document.body.classList.contains(`view-${v.id}`))?.id || 'journey';
  let currentId = store.state.current;
  const mainTotal = V.mainChapters.length || V.chapters.length;

  // ------------------------------------------------------------------ top bar
  // Preserve #audio-controls (other code may look it up) while rebuilding the bar.
  const audioSlot = $('#audio-controls') || el('<div id="audio-controls"></div>');
  top.innerHTML = '';
  const bar = el(`
    <div class="tn-bar">
      <a class="tn-brand" href="#/journey" aria-label="Mahabharata — The Living Epic, back to the journey">
        <span class="tn-brand-mark">${MANDALA_MARK}</span>
        <span class="tn-brand-text"><b>Mahabharata</b><small>The Living Epic</small></span>
      </a>

      <nav class="tn-tabs" aria-label="Views">
        ${VIEWS.map((v) => `<button type="button" class="tn-tab" data-view="${v.id}" aria-keyshortcuts="${v.key}" title="${esc(v.label)} (${v.key})">${esc(v.label)}</button>`).join('')}
        <span class="tn-tab-ink" aria-hidden="true"></span>
      </nav>

      <div class="tn-right">
        <button type="button" class="btn btn-sm btn-ghost tn-return" hidden>
          ${I.back}<span>Return to chapter</span>
        </button>
        <button type="button" class="tn-progress" aria-label="Voyage progress" title="Chapters visited — open the chapter list">
          <svg class="tn-ring" viewBox="0 0 36 36" aria-hidden="true">
            <circle class="tn-ring-track" cx="18" cy="18" r="15.5"/>
            <circle class="tn-ring-fill" cx="18" cy="18" r="15.5" pathLength="100"/>
          </svg>
          <span class="tn-progress-text"><b class="num" data-visited>0</b><span class="tn-progress-of"> / <span class="num">${mainTotal}</span><span class="tn-progress-word"> chapters</span></span></span>
        </button>
        <span class="tn-audio-slot"></span>
        <div class="tn-settings">
          <button type="button" class="icon-btn tn-settings-btn" aria-label="Settings" aria-expanded="false" aria-controls="tn-settings-pop">${I.settings}</button>
        </div>
        <a class="tn-classic" href="/classic" title="Open the original JARVIS chart interface">Classic JARVIS ${I.external}</a>
      </div>
    </div>`);
  top.appendChild(bar);
  bar.querySelector('.tn-audio-slot').replaceWith(audioSlot);

  // Mobile bottom tab bar (lives inside the header so it shares its stacking context)
  const tabbar = el(`
    <nav class="tn-tabbar" aria-label="Views">
      ${VIEWS.map((v) => `<button type="button" class="tn-tabbar-btn" data-view="${v.id}">${I[v.id]}<span>${esc(v.label.replace('Epic ', ''))}</span></button>`).join('')}
    </nav>`);
  top.appendChild(tabbar);

  const tabs = $$('[data-view]', top);
  const ink = $('.tn-tab-ink', bar);
  const returnBtn = $('.tn-return', bar);

  function moveInk() {
    const active = $(`.tn-tab[data-view="${view}"]`, bar);
    if (!active || !ink) return;
    const host = active.parentElement.getBoundingClientRect();
    const r = active.getBoundingClientRect();
    ink.style.setProperty('--x', `${r.left - host.left}px`);
    ink.style.setProperty('--w', `${r.width}px`);
  }

  function renderView() {
    for (const t of tabs) {
      const on = t.dataset.view === view;
      t.classList.toggle('is-active', on);
      if (on) t.setAttribute('aria-current', 'page'); else t.removeAttribute('aria-current');
    }
    returnBtn.hidden = view === 'journey' || !currentId;
    requestAnimationFrame(moveInk);
  }

  for (const t of tabs) t.addEventListener('click', () => bus.emit('view:change', { view: t.dataset.view }));
  bar.querySelector('.tn-brand').addEventListener('click', (e) => { e.preventDefault(); bus.emit('view:change', { view: 'journey' }); });
  returnBtn.addEventListener('click', () => {
    bus.emit('view:change', { view: 'journey' });
    const stage = $('#stage-ui');
    if (stage) stage.focus({ preventScroll: true });
  });
  window.addEventListener('resize', () => requestAnimationFrame(moveInk), { passive: true });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(moveInk).catch(() => {});

  // ------------------------------------------------------------------ progress ring
  const ringFill = $('.tn-ring-fill', bar);
  const visitedEl = $('[data-visited]', bar);
  const progressBtn = $('.tn-progress', bar);
  function renderProgress() {
    const mainIds = new Set((V.mainChapters.length ? V.mainChapters : V.chapters).map((c) => c.id));
    const n = store.state.visited.filter((id) => mainIds.has(id)).length;
    visitedEl.textContent = String(n);
    ringFill.style.strokeDasharray = `${mainTotal ? (n / mainTotal) * 100 : 0} 100`;
    progressBtn.setAttribute('aria-label', `${n} of ${mainTotal} chapters visited. Open the chapter list.`);
  }
  progressBtn.addEventListener('click', () => {
    if (view !== 'journey' && window.matchMedia('(min-width: 761px)').matches) bus.emit('view:change', { view: 'journey' });
    setRailOpen(true, { focus: true });
  });

  // ------------------------------------------------------------------ audio toggle
  const muteBtn = el(`<button type="button" class="icon-btn tn-mute" aria-pressed="false"></button>`);
  audioSlot.innerHTML = '';
  audioSlot.appendChild(muteBtn);
  function renderAudio() {
    const s = store.state.settings;
    const silent = s.muted || s.volume <= 0;
    muteBtn.innerHTML = `${silent ? I.soundOff : I.soundOn}<span class="tn-eq" aria-hidden="true"><i></i><i></i><i></i></span>`;
    muteBtn.setAttribute('aria-pressed', String(!!s.muted));
    muteBtn.setAttribute('aria-label', s.muted ? 'Unmute sound' : 'Mute sound');
    muteBtn.title = s.muted ? 'Sound is muted' : 'Mute sound';
    const playing = !silent && s.ambient && !!(audio && audio.unlocked);
    muteBtn.classList.toggle('is-playing', playing);
  }
  muteBtn.addEventListener('click', () => {
    try { audio && audio.unlock && audio.unlock(); } catch { /* gesture unlock is best-effort */ }
    updateSetting('muted', !store.state.settings.muted);
  });

  // ------------------------------------------------------------------ settings popover
  const settingsWrap = $('.tn-settings', bar);
  const settingsBtn = $('.tn-settings-btn', bar);
  const pop = el(`
    <div class="tn-pop panel panel-strong" id="tn-settings-pop" role="dialog" aria-label="Settings" hidden>
      <div class="tn-pop-head">
        <p class="eyebrow">Settings</p>
        <button type="button" class="icon-btn tn-pop-close" aria-label="Close settings">${I.close}</button>
      </div>

      <fieldset class="tn-field">
        <legend>Visual quality</legend>
        <div class="seg" role="radiogroup" aria-label="Visual quality">
          ${['auto', 'low', 'medium', 'high'].map((q) => `<button type="button" role="radio" class="seg-btn" data-quality="${q}">${q[0].toUpperCase() + q.slice(1)}</button>`).join('')}
        </div>
      </fieldset>

      <fieldset class="tn-field">
        <legend>Motion</legend>
        <div class="seg" role="radiogroup" aria-label="Motion">
          <button type="button" role="radio" class="seg-btn" data-motion="system">System</button>
          <button type="button" role="radio" class="seg-btn" data-motion="reduced">Reduced</button>
          <button type="button" role="radio" class="seg-btn" data-motion="full">Full</button>
        </div>
      </fieldset>

      <div class="tn-field">
        <label class="switch">
          <input type="checkbox" data-ambient>
          <span class="switch-track" aria-hidden="true"><span class="switch-thumb"></span></span>
          <span class="switch-label">Ambient soundscape<small>Synthesised live for each place</small></span>
        </label>
      </div>

      <div class="tn-field">
        <label class="range">
          <span class="range-label">Volume <output class="num" data-volume-out></output></span>
          <input type="range" min="0" max="100" step="1" data-volume aria-label="Volume">
        </label>
      </div>

      <div class="tn-field">
        <label class="switch">
          <input type="checkbox" data-muted>
          <span class="switch-track" aria-hidden="true"><span class="switch-thumb"></span></span>
          <span class="switch-label">Mute all sound</span>
        </label>
      </div>

      <div class="tn-pop-foot">
        <p class="small muted" data-env></p>
        <button type="button" class="btn btn-sm btn-ghost tn-reset">Reset voyage progress</button>
        <a class="btn btn-sm btn-ghost tn-pop-classic" href="/classic">Classic JARVIS ${I.external}</a>
      </div>
    </div>`);
  settingsWrap.appendChild(pop);

  function renderSettings() {
    const s = store.state.settings;
    for (const b of $$('[data-quality]', pop)) b.setAttribute('aria-checked', String(b.dataset.quality === s.quality));
    const motion = s.reducedMotion === null || s.reducedMotion === undefined ? 'system' : s.reducedMotion ? 'reduced' : 'full';
    for (const b of $$('[data-motion]', pop)) b.setAttribute('aria-checked', String(b.dataset.motion === motion));
    $('[data-ambient]', pop).checked = s.ambient !== false;
    $('[data-muted]', pop).checked = !!s.muted;
    const vol = Math.round((Number(s.volume) || 0) * 100);
    const range = $('[data-volume]', pop);
    if (document.activeElement !== range) range.value = String(vol);
    range.style.setProperty('--pct', `${vol}%`);
    $('[data-volume-out]', pop).textContent = `${vol}%`;
    const env = [];
    env.push(world && world.supported ? 'Rendering in 3D (WebGL).' : 'WebGL is unavailable here — showing the painted 2D backdrop.');
    if (!store.persistent) env.push('Progress cannot be saved in this browser (storage is blocked).');
    $('[data-env]', pop).textContent = env.join(' ');
  }

  function updateSetting(key, value) {
    store.set(`settings.${key}`, value);
    bus.emit('settings:change', { settings: store.state.settings });
  }

  function setPopOpen(open) {
    pop.hidden = !open;
    settingsBtn.setAttribute('aria-expanded', String(open));
    // Lifts the header above the overlay views (z 45) so the popover is never hidden behind them.
    document.body.classList.toggle('settings-open', open);
    if (open) {
      renderSettings();
      const first = $('.seg-btn[aria-checked="true"]', pop) || $('button', pop);
      first && first.focus({ preventScroll: true });
    }
  }

  settingsBtn.addEventListener('click', () => setPopOpen(pop.hidden));
  $('.tn-pop-close', pop).addEventListener('click', () => { setPopOpen(false); settingsBtn.focus(); });
  pop.addEventListener('click', (e) => {
    const q = e.target.closest('[data-quality]');
    if (q) updateSetting('quality', q.dataset.quality);
    const m = e.target.closest('[data-motion]');
    if (m) updateSetting('reducedMotion', m.dataset.motion === 'system' ? null : m.dataset.motion === 'reduced');
  });
  // Escape inside the popover closes just the popover.
  pop.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    e.preventDefault(); e.stopPropagation();
    setPopOpen(false); settingsBtn.focus();
  });
  // Arrow keys move within a segmented radiogroup.
  pop.addEventListener('keydown', (e) => {
    const btn = e.target.closest('.seg-btn');
    if (!btn || !['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
    const group = $$('.seg-btn', btn.parentElement);
    const i = group.indexOf(btn) + (e.key === 'ArrowRight' ? 1 : -1);
    const next = group[(i + group.length) % group.length];
    next.focus(); next.click();
    e.preventDefault();
  });
  $('[data-ambient]', pop).addEventListener('change', (e) => updateSetting('ambient', e.target.checked));
  $('[data-muted]', pop).addEventListener('change', (e) => {
    try { audio && audio.unlock && audio.unlock(); } catch { /* best-effort */ }
    updateSetting('muted', e.target.checked);
  });
  const range = $('[data-volume]', pop);
  range.addEventListener('input', () => {
    const v = Math.max(0, Math.min(1, Number(range.value) / 100));
    try { audio && audio.unlock && audio.unlock(); } catch { /* best-effort */ }
    updateSetting('volume', v);
    if (v > 0 && store.state.settings.muted) updateSetting('muted', false);
  });
  $('.tn-reset', pop).addEventListener('click', () => {
    let ok = true;
    try { ok = window.confirm('Reset your voyage? Visited chapters, answered questions and discoveries will be forgotten. Settings are kept.'); } catch { ok = true; }
    if (!ok) return;
    store.reset();
    bus.emit('toast', { message: 'Your voyage has been reset. The road begins again.', kind: 'info' });
  });
  document.addEventListener('pointerdown', (e) => {
    if (!pop.hidden && !settingsWrap.contains(e.target)) setPopOpen(false);
  });

  // ------------------------------------------------------------------ chapter rail
  // The rail is a slim strip of nodes; opened, it unfolds titles as an overlay over the chapter panel.
  let railOpen = false;
  rail.innerHTML = '';
  const railHead = el(`
    <div class="rail-head">
      <button type="button" class="rail-toggle" aria-expanded="false" aria-controls="rail-list" title="Show chapter titles">
        <span class="rail-toggle-icon">${I.rail}</span>
        <span class="rail-toggle-label">Chapters</span>
        <span class="rail-toggle-chev">${I.chevron}</span>
      </button>
      <button type="button" class="icon-btn rail-close" aria-label="Close chapter list">${I.close}</button>
    </div>`);
  const railList = el('<ol class="rail-list scroll-fine" id="rail-list"></ol>');
  const railLegend = el(`
    <div class="rail-legend" aria-hidden="true">
      <span><i class="lg lg-done"></i>Visited</span><span><i class="lg lg-cur"></i>Here</span>
      <span><i class="lg lg-lock"></i>Ahead</span><span><i class="lg lg-sub"></i>Side path</span>
    </div>`);
  rail.append(railHead, railList, railLegend);
  const scrim = el('<div class="rail-scrim" aria-hidden="true"></div>');
  document.body.appendChild(scrim);

  const chNum = (ch) => {
    const n = Number(ch.chapter_order);
    if (ch.kind === 'sub') return '';
    return Number.isInteger(n) ? roman(n) : String(ch.chapter_order ?? '');
  };

  function railState(ch) {
    if (ch.id === currentId) return 'current';
    if (store.isVisited(ch.id)) return 'completed';
    if (!store.isUnlocked(ch)) return 'locked';
    return 'open';
  }
  const STATE_TEXT = { current: 'current chapter', completed: 'visited', locked: 'ahead of the story', open: 'not yet visited' };

  function renderRail() {
    const chapters = V.chapters;
    const focusedId = rail.contains(document.activeElement) ? document.activeElement.dataset.ch : null;
    railList.innerHTML = chapters.map((ch, i) => {
      const st = railState(ch);
      const sub = ch.kind === 'sub';
      const nextIsSub = chapters[i + 1] && chapters[i + 1].kind === 'sub';
      const lastMain = !sub && !chapters.slice(i + 1).some((c) => c.kind !== 'sub');
      const loc = V.locationById.get(ch.location_id);
      const cls = ['rail-item', `is-${st}`, sub ? 'is-sub' : 'is-main', nextIsSub ? 'has-branch' : '', lastMain ? 'is-last' : ''].join(' ');
      return `
        <li class="${cls}">
          <button type="button" class="rail-btn" data-ch="${esc(ch.id)}" tabindex="${st === 'current' ? 0 : -1}" title="${esc(ch.title || ch.id)}"
            ${st === 'current' ? 'aria-current="step"' : ''}
            aria-label="${esc(`${sub ? 'Side path' : `Chapter ${chNum(ch)}`}: ${ch.title || ch.id} — ${STATE_TEXT[st]}`)}">
            <span class="rail-node" aria-hidden="true">${st === 'completed' ? I.check : st === 'locked' ? I.lock : ''}</span>
            <span class="rail-text">
              <span class="rail-num">${sub ? 'Side path' : `Chapter ${esc(chNum(ch))}`}</span>
              <span class="rail-title">${esc(ch.title || humanize(ch.id))}</span>
              ${loc ? `<span class="rail-loc">${esc(loc.name)}</span>` : ''}
            </span>
          </button>
        </li>`;
    }).join('');
    if (!$('.rail-btn[tabindex="0"]', railList)) {
      const first = $('.rail-btn', railList);
      if (first) first.tabIndex = 0;
    }
    // Re-rendering must not drop keyboard focus.
    if (focusedId) {
      const again = $$('.rail-btn', railList).find((b) => b.dataset.ch === focusedId);
      if (again) {
        $$('.rail-btn', railList).forEach((b) => { b.tabIndex = -1; });
        again.tabIndex = 0;
        again.focus({ preventScroll: true });
      }
    }
  }

  function scrollRailToCurrent() {
    const cur = $('.rail-item.is-current', railList);
    if (cur) cur.scrollIntoView({ block: 'nearest' });
  }

  function setRailOpen(open, { focus = false } = {}) {
    // Closing while focus is inside: return focus to the progress button (the mobile drawer
    // becomes visibility:hidden, which would otherwise drop focus onto <body>).
    if (!open && railOpen && rail.contains(document.activeElement) && window.matchMedia('(max-width: 760px)').matches) {
      progressBtn.focus({ preventScroll: true });
    }
    railOpen = open;
    rail.classList.toggle('is-open', open);
    document.body.classList.toggle('rail-open', open);
    $('.rail-toggle', railHead).setAttribute('aria-expanded', String(open));
    if (open) scrollRailToCurrent();
    if (open && focus) ($('.rail-btn[tabindex="0"]', railList) || $('.rail-btn', railList))?.focus({ preventScroll: true });
  }

  $('.rail-toggle', railHead).addEventListener('click', () => setRailOpen(!railOpen));
  $('.rail-close', railHead).addEventListener('click', () => setRailOpen(false));
  scrim.addEventListener('click', () => setRailOpen(false));

  railList.addEventListener('click', (e) => {
    const b = e.target.closest('.rail-btn');
    if (!b) return;
    bus.emit('chapter:enter', { chapterId: b.dataset.ch, from: 'nav' });
    setRailOpen(false); // fold away so the new chapter panel and scene are visible
  });

  rail.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !railOpen) return;
    e.preventDefault(); e.stopPropagation();
    setRailOpen(false);
    focusRailHome();
  });

  /** Where focus lands after the rail folds: its toggle on desktop, the progress button on phones. */
  function focusRailHome() {
    const mobile = window.matchMedia('(max-width: 760px)').matches;
    (mobile ? progressBtn : $('.rail-toggle', railHead)).focus({ preventScroll: true });
  }

  // Roving focus: ↑/↓ (and Home/End) move between chapters; Enter/Space open.
  railList.addEventListener('keydown', (e) => {
    const b = e.target.closest('.rail-btn');
    if (!b) return;
    const all = $$('.rail-btn', railList);
    let i = all.indexOf(b);
    if (e.key === 'ArrowDown') i = Math.min(all.length - 1, i + 1);
    else if (e.key === 'ArrowUp') i = Math.max(0, i - 1);
    else if (e.key === 'Home') i = 0;
    else if (e.key === 'End') i = all.length - 1;
    else return;
    e.preventDefault();
    e.stopPropagation();
    all.forEach((x) => (x.tabIndex = -1));
    all[i].tabIndex = 0;
    all[i].focus();
  });

  // ------------------------------------------------------------------ bus wiring
  bus.on('view:change', ({ view: v } = {}) => {
    if (!VIEWS.some((x) => x.id === v)) return;
    view = v;
    renderView();
    if (railOpen) setRailOpen(false);
  });
  bus.on('chapter:enter', ({ chapterId } = {}) => {
    if (!V.chapterById.has(chapterId)) return;
    currentId = chapterId;
    renderRail();
    renderView();
    renderAudio(); // audio.unlocked may have flipped during the opening gesture
    requestAnimationFrame(scrollRailToCurrent);
  });
  bus.on('store:change', () => {
    currentId = store.state.current || currentId;
    renderProgress();
    renderRail();
    renderAudio();
    if (!pop.hidden) renderSettings();
  });
  bus.on('settings:change', () => { renderAudio(); if (!pop.hidden) renderSettings(); });
  // main.js asks open overlays to close on Escape before it handles the key itself.
  bus.on('ui:escape', (evt) => {
    if (!evt || evt.handled) return;
    if (!pop.hidden) { setPopOpen(false); settingsBtn.focus(); evt.handled = true; return; }
    if (railOpen) {
      const hadFocus = rail.contains(document.activeElement);
      setRailOpen(false); evt.handled = true;
      if (hadFocus) focusRailHome();
    }
  });

  // ------------------------------------------------------------------ first paint
  renderView();
  renderProgress();
  renderRail();
  renderAudio();
  setRailOpen(false);
}
