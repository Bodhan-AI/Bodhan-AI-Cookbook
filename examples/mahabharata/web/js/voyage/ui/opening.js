// The opening sequence: a minimal, letter-spaced title card over world.showOpening().
// Resolves once the traveller presses "Begin the Voyage" (or "Continue from …") and the
// first chapter has been requested.
import { $, el, esc, sleep, roman, prefersReducedMotion } from '../core/util.js';

const MANDALA = `
<svg class="op-mandala" viewBox="0 0 200 200" aria-hidden="true">
  <g fill="none" stroke="currentColor" stroke-width=".8">
    <circle class="op-draw" cx="100" cy="100" r="92" pathLength="1"/>
    <circle class="op-draw" cx="100" cy="100" r="70" pathLength="1"/>
    <circle class="op-draw" cx="100" cy="100" r="18" pathLength="1"/>
    ${Array.from({ length: 12 }, (_, i) => `<path class="op-draw" pathLength="1" transform="rotate(${i * 30} 100 100)" d="M100 30 C112 52 112 70 100 82 C88 70 88 52 100 30Z"/>`).join('')}
    ${Array.from({ length: 24 }, (_, i) => `<path class="op-draw" pathLength="1" transform="rotate(${i * 15} 100 100)" d="M100 8 L100 22"/>`).join('')}
  </g>
</svg>`;

/** Visible chapter label, e.g. "Chapter IV · The Dice Game". */
function chapterLabel(ch) {
  if (!ch) return '';
  const n = Number(ch.chapter_order);
  const num = ch.kind === 'sub' || !Number.isInteger(n) ? '' : `Chapter ${roman(n)} · `;
  return `${num}${ch.title || ch.id}`;
}

/** Replace the overlay with an elegant data-error card (used by main.js when loading fails). */
export function showOpeningError(message, { onRetry } = {}) {
  const root = $('#opening');
  if (!root) return;
  document.body.classList.add('is-opening');
  root.hidden = false;
  root.classList.remove('is-leaving');
  root.innerHTML = '';
  const card = el(`
    <div class="op-error" role="alert">
      ${MANDALA}
      <p class="eyebrow">The manuscript could not be unrolled</p>
      <h1 class="op-error-title">The voyage data did not load</h1>
      <p class="op-error-msg">${esc(message || 'Unknown error')}</p>
      <p class="op-error-hint">The chart assistant itself is unaffected — the classic interface still answers questions with live charts.</p>
      <div class="op-actions">
        <button type="button" class="btn btn-gold" data-retry>Try again</button>
        <a class="btn" href="/classic">Open Classic JARVIS</a>
      </div>
    </div>`);
  root.appendChild(card);
  const retry = card.querySelector('[data-retry]');
  retry.addEventListener('click', () => (onRetry ? onRetry() : window.location.reload()));
  retry.focus({ preventScroll: true });
}

export function runOpening({ world, store, bus, audio, V = null, resumeChapterId = null } = {}) {
  const root = $('#opening');
  const body = document.body;
  if (!root) return Promise.resolve();

  const meta = (V && V.meta) || {};
  const title = 'MAHABHARATA';
  const subtitle = 'The Living Epic';
  const tagline = typeof meta.tagline === 'string' && meta.tagline.trim()
    ? meta.tagline.trim()
    : 'Every question opens another chapter. Every discovery reveals another truth.';
  // Break the tagline into its sentences so each can reveal on its own line.
  const lines = tagline.match(/[^.!?]+[.!?]*/g)?.map((s) => s.trim()).filter(Boolean) || [tagline];

  const startId = (V && V.chapterById.has(meta.start_chapter) && meta.start_chapter) || (V && V.chapters[0]?.id) || null;
  const resumeRaw = resumeChapterId || (store && store.state.current) || null;
  const resume = V && resumeRaw && V.chapterById.get(resumeRaw);
  const showContinue = !!(resume && resume.id !== startId) || !!(resume && store && store.state.visited.length > 1);

  const reduced = prefersReducedMotion();
  // Only promise sound when an audio engine actually loaded and WebAudio exists in this browser.
  const raw = audio && audio.raw;
  const soundOk = !!(audio && !audio.stub && !(raw && raw.supported === false));
  body.classList.add('is-opening');
  root.hidden = false;
  root.classList.remove('is-leaving', 'is-revealed');
  root.innerHTML = '';

  const letters = [...title].map((ch, i) => `<span class="op-l" style="--i:${i}" aria-hidden="true">${esc(ch)}</span>`).join('');
  const view = el(`
    <div class="op-stage">
      <div class="op-vignette" aria-hidden="true"></div>
      <div class="op-inner">
        <div class="op-mark">${MANDALA}</div>
        <h1 class="op-title"><span class="sr-only">${esc(title)}</span>${letters}</h1>
        <p class="op-subtitle">${esc(subtitle)}</p>
        <div class="op-rule" aria-hidden="true"><i></i></div>
        <p class="op-tagline">${lines.map((l, i) => `<span class="op-line" style="--j:${i}">${esc(l)}</span>`).join('')}</p>
        <div class="op-actions">
          <button type="button" class="btn btn-gold btn-lg op-begin" data-begin>
            <span>Begin the Voyage</span>
          </button>
          ${showContinue ? `<button type="button" class="btn op-continue" data-continue>
            <span class="op-continue-k">Continue from</span> <span class="op-continue-v">${esc(chapterLabel(resume))}</span>
          </button>` : ''}
        </div>
      </div>
      <div class="op-foot">
        ${soundOk ? `<span class="op-sound" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M4 14v-4M8 17V7M12 20V4M16 16V8M20 13v-2"/></svg>
        </span>
        <span>Best with sound · the ambience is synthesised live</span>` : ''}
        <a class="op-classic" href="/classic">Classic JARVIS</a>
      </div>
    </div>`);
  root.appendChild(view);

  // The 3D prologue runs underneath; failures are tolerated (2D backdrop remains).
  try {
    const p = world && world.showOpening && world.showOpening();
    if (p && typeof p.catch === 'function') p.catch((e) => console.warn('[opening] world.showOpening failed', e));
  } catch (e) {
    console.warn('[opening] world.showOpening threw', e);
  }

  // Trigger the CSS reveal on the next frame (so transitions actually run).
  requestAnimationFrame(() => requestAnimationFrame(() => root.classList.add('is-revealed')));

  const beginBtn = view.querySelector('[data-begin]');
  const contBtn = view.querySelector('[data-continue]');
  // Focus once the button has faded in, so Enter starts the voyage.
  setTimeout(() => { if (!root.classList.contains('is-leaving')) (contBtn || beginBtn).focus({ preventScroll: true }); }, reduced ? 50 : 2600);

  return new Promise((resolve) => {
    let started = false;

    const go = async (chapterId) => {
      if (started) return;
      started = true;
      try {
        // 1) Audio must unlock synchronously inside the click gesture.
        try { const u = audio && audio.unlock && audio.unlock(); if (u && typeof u.catch === 'function') u.catch(() => {}); } catch (e) { console.warn('[opening] audio.unlock failed', e); }
        try { bus && bus.emit('audio:cue', { cue: 'conch', volume: 0.5 }); } catch { /* optional */ }

        beginBtn.disabled = true;
        if (contBtn) contBtn.disabled = true;
        root.classList.add('is-leaving');

        // 2) Camera glides toward the manuscript; never wait more than ~3.4 s for it.
        let zoom = Promise.resolve();
        try { zoom = Promise.resolve(world && world.openingZoom && world.openingZoom()); } catch (e) { console.warn(e); }
        await Promise.race([zoom.catch(() => {}), sleep(reduced ? 600 : 3400)]);

        // 3) Fade into the world and request the first chapter.
        body.classList.remove('is-opening');
        if (chapterId && bus) bus.emit('chapter:enter', { chapterId, from: 'opening' });
        await sleep(reduced ? 150 : 900);
      } catch (e) {
        console.warn('[opening] transition failed', e);
        body.classList.remove('is-opening');
      } finally {
        // Always release the overlay, even if something above failed.
        root.hidden = true;
        root.innerHTML = '';
        // Hand keyboard focus to the chapter panel so it is not lost on <body>.
        const stage = $('#stage-ui');
        if (stage && (!document.activeElement || document.activeElement === document.body)) {
          try { stage.focus({ preventScroll: true }); } catch { /* ignore */ }
        }
        resolve();
      }
    };

    beginBtn.addEventListener('click', () => go(showContinue ? startId : (resume && resume.id) || startId));
    if (contBtn) contBtn.addEventListener('click', () => go(resume.id));
  });
}
