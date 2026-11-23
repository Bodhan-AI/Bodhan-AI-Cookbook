// The research drawer (aside#research): "Ask the epic" input, the run log, and one view per
// research run (question header, honest progress, real trace replay, answer, visions, evidence,
// discoveries, paths onward) plus footer actions. Rendering only — flow.js owns the state machine.

import { esc, fmtSecs, roman, humanize, prefersReducedMotion } from '../core/util.js';
import { renderLineage, renderContext, summarizeTrace } from './evidence.js';
import { renderResult } from './viz.js';

const arr = (v) => (Array.isArray(v) ? v : []);
const str = (v) => (typeof v === 'string' ? v : '');

const PILL = {
  focusing: 'Preparing', queued: 'In line', pending: 'Consulting', retrieved: 'Graph walked',
  rendering: 'Illuminating', done: 'Answered', error: 'Failed',
};

// Palm-leaf manuscript that unrolls and inks itself while a request is really pending.
// Pure decoration: it carries no progress information (there is no progress to report).
const MANUSCRIPT_SVG = `
<svg class="ms-svg" viewBox="0 0 320 130" aria-hidden="true" focusable="false">
  <g class="ms-sheet">
    <rect class="ms-leaf" x="42" y="24" width="236" height="82" rx="8"/>
    <rect class="ms-leaf-edge" x="42" y="24" width="236" height="82" rx="8"/>
    <g class="ms-lines">
      <line class="ms-ink" style="--i:0" x1="62" y1="44" x2="258" y2="44"/>
      <line class="ms-ink" style="--i:1" x1="62" y1="58" x2="232" y2="58"/>
      <line class="ms-ink" style="--i:2" x1="62" y1="72" x2="250" y2="72"/>
      <line class="ms-ink" style="--i:3" x1="62" y1="86" x2="196" y2="86"/>
    </g>
    <circle class="ms-hole" cx="104" cy="65" r="3.2"/>
    <circle class="ms-hole" cx="216" cy="65" r="3.2"/>
  </g>
  <g class="ms-roller ms-roller-l"><rect x="26" y="14" width="16" height="102" rx="8"/><rect class="ms-cap" x="24" y="10" width="20" height="8" rx="4"/><rect class="ms-cap" x="24" y="112" width="20" height="8" rx="4"/></g>
  <g class="ms-roller ms-roller-r"><rect x="278" y="14" width="16" height="102" rx="8"/><rect class="ms-cap" x="276" y="10" width="20" height="8" rx="4"/><rect class="ms-cap" x="276" y="112" width="20" height="8" rx="4"/></g>
</svg>`;

// ------------------------------------------------------------------ answer Markdown (safe subset)
// Escape-first: every fragment goes through esc() before any tag is added, so no model HTML is ever
// injected. Supported: paragraphs, line breaks, # headings, -/*/•/+ and 1. lists (one nesting level),
// GFM tables, --- rules, **bold**, *italic*, `code`, and a literal <br> (common inside table cells).
function mdInline(raw) {
  return esc(raw)
    .replace(/&lt;br\s*\/?&gt;/gi, '<br>')
    .replace(/`([^`\n]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^\s*](?:[^*\n]*?[^\s*])?)\*\*/g, '<strong>$1</strong>')
    .replace(/__([^\s_](?:[^_\n]*?[^\s_])?)__/g, '<strong>$1</strong>')
    .replace(/(^|[^*\w])\*([^\s*](?:[^*\n]*?[^\s*])?)\*(?![*\w])/g, '$1<em>$2</em>');
}
const RE_TSEP = /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
const RE_UL = /^(\s*)[-*•+]\s+(.*)$/;
const RE_OL = /^(\s*)(\d{1,3})[.)]\s+(.*)$/;
const RE_H = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const RE_HR = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
const cells = (line) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
const isNumCell = (c) => /^[-+~≈]?\s*[\d.,]+\s*%?$/.test(c);

/** Render an LLM answer as escaped HTML with a small Markdown subset. */
export function formatAnswer(text) {
  let src = String(text || '').replace(/\r\n?/g, '\n');
  // Tables that arrive flattened onto one line ("| a | b ||---|---|| 1 | 2 |"): restore the rows.
  src = src.split('\n').map((l) => (/\|\s*:?-{3,}/.test(l) && /\|\|/.test(l) ? l.replace(/\|\|/g, '|\n|') : l)).join('\n');
  const lines = src.split('\n');
  const out = [];
  let para = [];
  const flushPara = () => { if (para.length) { out.push(`<p>${para.map(mdInline).join('<br>')}</p>`); para = []; } };
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { flushPara(); i++; continue; }
    // GFM table: a pipe row followed by a separator row.
    if (line.includes('|') && i + 1 < lines.length && RE_TSEP.test(lines[i + 1].trim()) && lines[i + 1].includes('-')) {
      flushPara();
      const head = cells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) { rows.push(cells(lines[i])); i++; }
      const td = (c) => `<td${isNumCell(c) ? ' class="is-num"' : ''}>${mdInline(c)}</td>`;
      out.push(`<div class="rv-md-table"><table class="rv-table"><thead><tr>${head.map((c) => `<th scope="col">${mdInline(c)}</th>`).join('')}</tr></thead><tbody>${
        rows.map((r) => `<tr>${head.map((_, k) => td(r[k] ?? '')).join('')}</tr>`).join('')}</tbody></table></div>`);
      continue;
    }
    const h = RE_H.exec(line);
    if (h) { flushPara(); const lvl = Math.min(6, h[1].length + 2); out.push(`<h${lvl} class="rv-md-h">${mdInline(h[2])}</h${lvl}>`); i++; continue; }
    if (RE_HR.test(line)) { flushPara(); out.push('<hr class="rv-md-hr">'); i++; continue; }
    if (RE_UL.test(line) || RE_OL.test(line)) {
      flushPara();
      // Collect the list block (items, their indented sub-items and wrapped continuation lines).
      const items = [];
      while (i < lines.length && lines[i].trim()) {
        const l = lines[i];
        const u = RE_UL.exec(l); const o = RE_OL.exec(l);
        const m = u ? { indent: u[1].length, ordered: false, text: u[2] } : o ? { indent: o[1].length, ordered: true, text: o[3], n: +o[2] } : null;
        if (m) items.push(m);
        else if (items.length && !RE_H.test(l) && !l.includes('|')) items[items.length - 1].text += `\n${l.trim()}`;
        else break;
        i++;
      }
      const base = Math.min(...items.map((it) => it.indent));
      let html = '';
      let k = 0;
      const renderLevel = (level) => {
        const ordered = items[k].ordered;
        const start = ordered && items[k].n > 1 ? ` start="${items[k].n}"` : '';
        let h2 = ordered ? `<ol${start}>` : '<ul>';
        while (k < items.length && items[k].indent >= level) {
          const it = items[k++];
          h2 += `<li>${it.text.split('\n').map(mdInline).join('<br>')}`;
          if (k < items.length && items[k].indent > it.indent) h2 += renderLevel(items[k].indent);
          h2 += '</li>';
        }
        return h2 + (ordered ? '</ol>' : '</ul>');
      };
      while (k < items.length) html += renderLevel(base);
      out.push(html);
      continue;
    }
    para.push(line.trim());
    i++;
  }
  flushPara();
  return out.join('');
}

function ordinal(n) {
  const v = n % 100;
  const suf = v >= 11 && v <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] || 'th');
  return `${n}${suf}`;
}

function chapterCrumb(chapter) {
  if (!chapter) return 'Ask the epic';
  const order = Number(chapter.chapter_order);
  const num = Number.isFinite(order) && Number.isInteger(order) ? `Chapter ${roman(order)}` : 'Interlude';
  const parva = chapter.story_context && str(chapter.story_context.parva);
  return [num, str(chapter.title), parva].filter(Boolean).join(' · ');
}

/**
 * @param {{root: Element, V: object, store: object, bus: object, actions: object}} opts
 *  actions: ask(query), retry(run), withdraw(run), focusRun(run), continueFrom(run), skipReplay(run)
 */
export function createPanel({ root, V, store, bus, actions }) {
  let meta = null;
  let active = null;
  let isOpen = false;
  let isFocus = false;
  let timerId = 0;
  let lastFocus = null;

  root.classList.add('rs');
  root.setAttribute('aria-label', 'Research');
  root.setAttribute('aria-hidden', 'true');
  root.innerHTML = `
    <div class="rs-shell">
      <header class="rs-top">
        <div class="rs-bar">
          <span class="rs-eyebrow"><span class="rs-eyebrow-mark" aria-hidden="true"></span>Research</span>
          <div class="rs-tools">
            <button type="button" class="rs-icon" data-act="focus" aria-pressed="false" aria-label="Focus mode" title="Focus mode">
              <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 8V3h5M17 8V3h-5M3 12v5h5M17 12v5h-5"/></svg>
            </button>
            <button type="button" class="rs-icon" data-act="close" aria-label="Close research" title="Close (Esc)">
              <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 5l10 10M15 5L5 15"/></svg>
            </button>
          </div>
        </div>
        <form class="rs-ask" autocomplete="off">
          <label class="rs-ask-label" for="rs-ask-input">Ask the epic</label>
          <div class="rs-ask-row">
            <input id="rs-ask-input" class="rs-ask-input" type="text" maxlength="600" enterkeyhint="send"
              placeholder="Ask anything about the Mahabharata…" aria-describedby="rs-ask-hint">
            <button type="submit" class="rs-ask-btn">Ask</button>
          </div>
          <p class="rs-ask-hint" id="rs-ask-hint">Your words walk the curated knowledge graph and reach the model unchanged.</p>
        </form>
        <nav class="rs-log" aria-label="Inquiries on this voyage" hidden></nav>
      </header>
      <div class="rs-scroll"><div class="rs-stage"></div></div>
      <footer class="rs-foot">
        <button type="button" class="rs-btn rs-btn-primary" data-act="continue" hidden>Continue the Voyage <span aria-hidden="true">→</span></button>
        <button type="button" class="rs-btn" data-act="another">Ask another question</button>
        <button type="button" class="rs-btn" data-act="focus" aria-pressed="false">Focus mode</button>
        <button type="button" class="rs-btn rs-btn-quiet" data-act="close">Close</button>
      </footer>
    </div>`;

  // Launcher: "Ask the epic" lives in this drawer and must never be out of reach (the contract says
  // it is never locked), so a small door stays on screen while the drawer is closed. It sits outside
  // #research because the closed drawer is inert. Its busy dot mirrors real request state only.
  let launcher = document.querySelector('.rs-launch');
  if (!launcher) {
    launcher = document.createElement('button');
    launcher.type = 'button';
    launcher.className = 'rs-launch';
    document.body.appendChild(launcher);
  }
  launcher.innerHTML = '<span class="rs-launch-mark" aria-hidden="true"></span><span class="rs-launch-text">Ask the epic</span>';
  launcher.setAttribute('aria-label', 'Ask the epic: open research');
  launcher.addEventListener('click', () => {
    if (!active) { api.focusInput(); return; }
    api.open();
    const h = active.ui && active.ui.view && active.ui.view.querySelector('.rv-q');
    if (h) requestAnimationFrame(() => { try { h.focus({ preventScroll: true }); } catch { /* ignore */ } });
  });
  const WORKING = ['focusing', 'queued', 'pending', 'retrieved', 'rendering'];
  function paintLauncher(runs) {
    const n = arr(runs).filter((r) => r && WORKING.includes(r.state)).length;
    launcher.classList.toggle('is-busy', n > 0);
    launcher.querySelector('.rs-launch-text').textContent = n ? 'Researching…' : 'Ask the epic';
    launcher.setAttribute('aria-label', n
      ? `Researching… ${n} question${n === 1 ? '' : 's'} in progress: open research`
      : 'Ask the epic: open research');
  }

  const q = (sel) => root.querySelector(sel);
  const stage = q('.rs-stage');
  const scroller = q('.rs-scroll');
  const form = q('.rs-ask');
  const input = q('.rs-ask-input');
  const logEl = q('.rs-log');
  const continueBtn = q('[data-act="continue"]');

  // ---------------------------------------------------------------- top-level controls
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) { input.focus(); return; }
    input.value = '';
    actions.ask(text);
  });
  root.addEventListener('click', (e) => {
    const b = e.target.closest('[data-act]');
    if (!b || !root.contains(b)) return;
    const act = b.dataset.act;
    if (act === 'close') api.close();
    else if (act === 'focus') api.setFocus(!isFocus);
    else if (act === 'another') api.focusInput();
    else if (act === 'continue' && active) actions.continueFrom(active);
  });

  // Esc closes the drawer when focus is inside it, or when no overlay view / modal is on top.
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !isOpen || e.defaultPrevented) return;
    const inside = root.contains(document.activeElement);
    const b = document.body;
    const overlayView = [...b.classList].some((c) => c.startsWith('view-') && c !== 'view-journey');
    const modal = document.getElementById('modal-root');
    const modalOpen = modal && modal.childElementCount > 0;
    // The chapter rail / settings popover sit above the drawer: let main.js → 'ui:escape' close those first.
    const navOverlay = b.classList.contains('rail-open') || b.classList.contains('settings-open');
    if (inside || (!overlayView && !modalOpen && !navOverlay)) {
      e.preventDefault();
      api.close();
    }
  });

  // ---------------------------------------------------------------- idle view
  function idleView() {
    const m = meta;
    const graph = m && m.graph && (m.graph.name || m.graph.root);
    return `
      <div class="rs-idle">
        <div class="rs-idle-mark" aria-hidden="true"></div>
        <h2 class="rs-idle-title">Every question opens another chapter</h2>
        <p>Choose a question in a chapter, or ask the epic in your own words. Each question walks the curated knowledge graph,
           then the model answers and draws visions from what was found. You will see the real path it took and the passages it read.</p>
        ${m ? `<p class="rs-idle-meta">${m.model ? `Model <b>${esc(m.model)}</b>` : ''}${m.model && graph ? ' · ' : ''}${graph ? `Knowledge graph <b>${esc(graph)}</b>` : ''}</p>` : ''}
      </div>`;
  }

  // ---------------------------------------------------------------- run view
  function buildView(run) {
    const qd = run.question || {};
    const view = document.createElement('article');
    view.className = 'rv';
    view.innerHTML = `
      <header class="rv-head">
        <div class="rv-crumb">${esc(chapterCrumb(run.chapter))}</div>
        <h2 class="rv-q" tabindex="-1">${esc(run.title)}</h2>
        ${str(qd.deeper_context) ? `<p class="rv-deeper">${esc(qd.deeper_context)}</p>` : ''}
        <div class="rv-status">
          <span class="rv-pill" data-role="pill"></span>
          <span class="rv-timer" data-role="timer" aria-hidden="true"></span>
          <span class="rv-cache" data-role="cache" hidden>From this voyage</span>
        </div>
        <p class="sr-only" data-role="announce" aria-live="polite"></p>
        ${run.query !== run.title ? `<details class="rv-sent"><summary>What was sent to the knowledge graph</summary><p>${esc(run.query)}</p></details>` : ''}
      </header>
      <section class="rv-invest" data-role="invest">
        <div class="rv-manuscript" data-role="manuscript">${MANUSCRIPT_SVG}</div>
        <p class="rv-stage-text" data-role="stage" aria-live="polite"></p>
        <div class="rv-invest-actions">
          <button type="button" class="rs-btn rs-btn-quiet" data-run="withdraw" hidden>Withdraw question</button>
          <button type="button" class="rs-btn rs-btn-quiet" data-run="hide" hidden>Keep researching in the background</button>
        </div>
        <div class="rv-lineage" data-role="lineage" hidden>
          <div class="rv-sec-head">
            <h3 class="rv-h">The path through the knowledge graph</h3>
            <button type="button" class="rv-link" data-run="skip">Skip replay</button>
            <button type="button" class="rv-link" data-run="fold" aria-expanded="true" hidden>Fold</button>
          </div>
          <div class="rv-lineage-body" data-role="lineage-body"></div>
        </div>
      </section>
      <section class="rv-sec" data-sec="error" hidden></section>
      <section class="rv-sec" data-sec="answer" hidden><h3 class="rv-h">The answer</h3><div class="rv-answer" data-role="answer"></div></section>
      <section class="rv-sec" data-sec="visions" hidden><h3 class="rv-h">Visions</h3><div class="rv-visions" data-role="visions"></div></section>
      <section class="rv-sec" data-sec="evidence" hidden><h3 class="rv-h">Evidence</h3><div data-role="evidence"></div></section>
      <section class="rv-sec" data-sec="discoveries" hidden><h3 class="rv-h">Discoveries</h3><div data-role="discoveries"></div></section>
      <section class="rv-sec" data-sec="paths" hidden><h3 class="rv-h">Paths onward</h3><div data-role="paths"></div></section>`;

    view.addEventListener('click', (e) => {
      const b = e.target.closest('[data-run]');
      if (b) {
        const act = b.dataset.run;
        if (act === 'withdraw') actions.withdraw(run);
        else if (act === 'hide') api.close();
        else if (act === 'retry') actions.retry(run);
        else if (act === 'skip') actions.skipReplay(run);
        else if (act === 'fold') {
          const open = b.getAttribute('aria-expanded') !== 'true';
          b.setAttribute('aria-expanded', String(open));
          b.textContent = open ? 'Fold' : 'Unfold';
          view.querySelector('[data-role="lineage-body"]').hidden = !open;
        }
        return;
      }
      const go = e.target.closest('[data-go]');
      if (go) navigate(go.dataset.go, go.dataset.id, run);
    });
    return view;
  }

  function ui(run) {
    if (!run.ui) run.ui = { view: null, charts: [], replay: null, rendered: false };
    if (!run.ui.view) run.ui.view = buildView(run);
    return run.ui;
  }
  const part = (run, role) => ui(run).view.querySelector(`[data-role="${role}"]`);
  const sec = (run, name) => ui(run).view.querySelector(`[data-sec="${name}"]`);

  function navigate(kind, id, run) {
    if (!id) return;
    if (kind === 'chapter') {
      bus.emit('chapter:enter', { chapterId: id, from: 'discovery' });
      api.close();
    } else if (kind === 'question') {
      const entry = V && V.questionById && V.questionById.get(id);
      bus.emit('question:select', { questionId: id, chapterId: entry && entry.chapter ? entry.chapter.id : undefined });
    } else if (kind === 'discovery') {
      const chapters = run && run.chapter ? [run.chapter] : [];
      const disc = chapters.flatMap((c) => arr(c.optional_discoveries)).find((d) => d && d.id === id);
      if (disc && disc.type === 'chapter' && disc.target) navigate('chapter', disc.target, run);
      else if (disc && disc.type === 'question' && disc.target) navigate('question', disc.target, run);
      else if (disc && disc.type && disc.target) bus.emit('entity:open', { type: disc.type, id: disc.target });
      else bus.emit('entity:open', { type: 'discovery', id });
      if (store && typeof store.discover === 'function') {
        try { store.discover(id); } catch (e) { console.warn('[research] store.discover failed', e); }
      }
    } else {
      bus.emit('entity:open', { type: kind, id });
    }
  }

  // ---------------------------------------------------------------- timer (real pending time only)
  function pendingSeconds(run) {
    if (!run.pendingAt) return null;
    const end = run.retrievedAt || run.endedAt || performance.now();
    return Math.max(0, (end - run.pendingAt) / 1000);
  }
  function paintTimer(run) {
    const t = part(run, 'timer');
    const cache = part(run, 'cache');
    if (run.fromCache) {
      const ms = run.response && Number(run.response.elapsed_ms);
      t.textContent = Number.isFinite(ms) ? `originally answered in ${fmtSecs(ms / 1000)}` : '';
      cache.hidden = false;
      return;
    }
    cache.hidden = true;
    const s = pendingSeconds(run);
    if (s == null) { t.textContent = ''; return; }
    const live = run.state === 'pending';
    t.textContent = live ? fmtSecs(s) : `answered in ${fmtSecs(s)}`;
    if (!live && run.state === 'error') t.textContent = `after ${fmtSecs(s)}`;
  }
  function syncTimer() {
    const need = isOpen && active && active.state === 'pending';
    if (need && !timerId) timerId = setInterval(() => active && paintTimer(active), 100);
    if (!need && timerId) { clearInterval(timerId); timerId = 0; }
  }

  // ---------------------------------------------------------------- state painting
  function paintState(run) {
    const u = ui(run);
    const v = u.view;
    v.dataset.state = run.state;
    part(run, 'pill').textContent = PILL[run.state] || run.state;
    paintTimer(run);
    const stageText = part(run, 'stage');
    const withdraw = v.querySelector('[data-run="withdraw"]');
    const hide = v.querySelector('[data-run="hide"]');
    const manuscript = part(run, 'manuscript');
    const working = run.state === 'focusing' || run.state === 'queued' || run.state === 'pending';
    manuscript.hidden = !working;
    manuscript.classList.toggle('is-waiting', run.state !== 'pending');
    withdraw.hidden = run.state !== 'queued';
    hide.hidden = run.state !== 'pending';
    switch (run.state) {
      case 'focusing': stageText.textContent = 'Preparing the question…'; break;
      case 'queued': {
        const pos = run.queuePosition || 0;
        const place = pos > 1 ? `<b>${ordinal(pos)}</b> in line` : 'next in line';
        stageText.innerHTML = `Another question is with the scribe. Yours is ${place} and will be sent the moment the scribe is free.`;
        break;
      }
      case 'pending':
        stageText.innerHTML = '<span class="spinner" aria-hidden="true"></span>Walking the knowledge graph and consulting the model…<span class="rv-stage-sub">Real requests usually take 5–40 seconds.</span>';
        break;
      case 'retrieved': stageText.textContent = run.fromCache ? 'Answered earlier on this voyage — replaying the path it took.' : 'Replaying the real path the graph walker took.'; break;
      case 'rendering': stageText.textContent = 'Illuminating the answer…'; break;
      default: stageText.textContent = '';
    }
    stageText.hidden = !stageText.textContent;
    if (run.state === 'error') paintError(run);
    else sec(run, 'error').hidden = true;
    // The stage line empties when research settles, so say the outcome to screen readers once.
    const announce = part(run, 'announce');
    const said = run.state === 'done' ? `The answer is ready${run.fromCache ? ' (from earlier on this voyage)' : ''}.`
      : run.state === 'error' ? `The research failed. ${str(run.error && run.error.message)}` : '';
    if (announce.textContent !== said) announce.textContent = said;
    if (run === active) {
      syncTimer();
      updateFooter();
    }
  }

  function paintError(run) {
    const s = sec(run, 'error');
    const err = run.error || {};
    const status = Number(err.status);
    let lead = 'The research could not be completed.';
    if (err.aborted) lead = 'This question was withdrawn before it was sent — the server never received it.';
    else if (status === 502) lead = 'The model or the network failed while answering (HTTP 502).';
    else if (status === 0) lead = 'The JARVIS server could not be reached.';
    else if (status >= 400 && status < 500) lead = `The server refused the request (HTTP ${status}).`;
    else if (status >= 500) lead = `The server failed (HTTP ${status}).`;
    s.innerHTML = `
      <div class="rv-error">
        <p class="rv-error-lead">${esc(lead)}</p>
        ${err.message && !err.aborted ? `<pre class="rv-error-code">${esc(err.message)}</pre>` : ''}
        <button type="button" class="rs-btn rs-btn-primary" data-run="retry">${err.aborted ? 'Ask it after all' : 'Retry'}</button>
      </div>`;
    s.hidden = false;
  }

  // ---------------------------------------------------------------- results
  function chip(kind, id, label, extra = '') {
    return `<button type="button" class="rv-chip rv-chip-${esc(kind)}" data-go="${esc(kind)}" data-id="${esc(id)}">${esc(label)}${extra}</button>`;
  }
  function nameOf(type, id) {
    const map = type === 'character' ? V && V.characterById : type === 'location' ? V && V.locationById : V && V.eventById;
    const item = map && typeof map.get === 'function' ? map.get(id) : null;
    return (item && (item.name || item.title)) || humanize(id);
  }
  function chapterLabel(id) {
    const ch = V && V.chapterById && V.chapterById.get(id);
    return ch ? str(ch.title) || humanize(id) : humanize(id);
  }
  function lockedNote(id) {
    const ch = V && V.chapterById && V.chapterById.get(id);
    if (!ch || !store || typeof store.isUnlocked !== 'function') return '';
    try { return store.isUnlocked(ch) ? '' : '<span class="rv-ahead">ahead in the story</span>'; } catch { return ''; }
  }

  function renderDiscoveries(run) {
    const found = run.entities || { characters: [], locations: [], events: [] };
    const groups = [['character', 'Characters', found.characters], ['location', 'Places', found.locations], ['event', 'Events', found.events]];
    const anyFound = groups.some((g) => arr(g[2]).length);
    let html = '';
    if (anyFound) {
      html += '<p class="rv-note">Named in the answer and the retrieved manuscripts:</p>';
      for (const [type, label, list] of groups) {
        if (!arr(list).length) continue;
        html += `<div class="rv-chipgroup"><span class="rv-chipgroup-label">${label}</span>${list.map((e) => chip(type, e.id, e.name)).join('')}</div>`;
      }
    } else {
      html += '<p class="rv-note">No characters, places or events from the voyage were named in this answer.</p>';
    }
    const rel = run.question && run.question.related_entities;
    if (rel && typeof rel === 'object') {
      const relGroups = [['character', arr(rel.characters)], ['location', arr(rel.locations)], ['event', arr(rel.events)]]
        .map(([t, ids]) => [t, ids.filter((x) => typeof x === 'string')]).filter(([, ids]) => ids.length);
      if (relGroups.length) {
        html += `<div class="rv-chipgroup rv-chipgroup-rel"><span class="rv-chipgroup-label">Linked to this chapter</span>${relGroups.map(([t, ids]) => ids.map((id) => chip(t, id, nameOf(t, id))).join('')).join('')}</div>`;
      }
    }
    part(run, 'discoveries').innerHTML = html;
    sec(run, 'discoveries').hidden = false;
  }

  function renderPaths(run) {
    const qd = run.question;
    const ch = run.chapter;
    let html = '';
    const branches = arr(qd && qd.branching_options).filter((b) => b && b.target);
    const ns = qd && qd.next_stage && qd.next_stage.target ? qd.next_stage : null;
    const items = [];
    if (ns) {
      const t = ns.type === 'discovery' ? 'discovery' : ns.type === 'question' ? 'question' : 'chapter';
      items.push({ kind: t, id: ns.target, label: t === 'chapter' ? chapterLabel(ns.target) : t === 'question' ? questionLabel(ns.target) : discoveryLabel(ch, ns.target), tag: 'Next stage' });
    }
    for (const b of branches) {
      const t = ['chapter', 'question', 'discovery'].includes(b.target_type) ? b.target_type : 'chapter';
      const dup = items.find((x) => x.kind === t && x.id === b.target);
      if (dup) { if (str(b.label)) dup.label = b.label; continue; }
      items.push({ kind: t, id: b.target, label: str(b.label) || (t === 'chapter' ? chapterLabel(b.target) : humanize(b.target)), tag: t === 'chapter' ? `Chapter · ${chapterLabel(b.target)}` : t === 'question' ? 'Question' : 'Discovery' });
    }
    if (items.length) {
      html += `<p class="rv-note">Paths from this chapter</p><div class="rv-paths">${items.map((it) => `
        <button type="button" class="rv-path" data-go="${esc(it.kind)}" data-id="${esc(it.id)}">
          <span class="rv-path-tag">${esc(it.tag)}</span><span class="rv-path-label">${esc(it.label)}</span>${it.kind === 'chapter' ? lockedNote(it.id) : ''}
        </button>`).join('')}</div>`;
    }
    if (ch) {
      const shown = new Set(items.filter((it) => it.kind === 'question').map((it) => it.id));
      const others = [ch.central_question, ...arr(ch.questions)].filter((x) => x && x.id && x.id !== (qd && qd.id) && !shown.has(x.id) && str(x.question));
      if (others.length) {
        const answered = (id) => { try { return !!(store && typeof store.isAnswered === 'function' && store.isAnswered(id)); } catch { return false; } };
        html += `<p class="rv-note">Other questions in this chapter</p><div class="rv-chipgroup">${others.map((o) => chip('question', o.id, o.question, answered(o.id) ? ' <span class="rv-done" aria-hidden="true">✓</span><span class="sr-only"> (answered)</span>' : '')).join('')}</div>`;
      }
    }
    if (!html && !ch) html = '<p class="rv-note">This was a free-form question, so it has no chapter paths. Ask another, or return to the journey.</p>';
    part(run, 'paths').innerHTML = html;
    sec(run, 'paths').hidden = !html;
  }
  function questionLabel(id) {
    const e = V && V.questionById && V.questionById.get(id);
    return (e && e.question && str(e.question.question)) || humanize(id);
  }
  function discoveryLabel(ch, id) {
    const d = arr(ch && ch.optional_discoveries).find((x) => x && x.id === id);
    return (d && str(d.title)) || humanize(id);
  }

  function renderResultsInto(run) {
    const u = ui(run);
    if (u.rendered) return;
    u.rendered = true;
    const resp = run.response || {};
    const results = arr(resp.results);

    // The answer (LLM text results).
    const texts = results.filter((r) => r && !r.tool && typeof r.text === 'string' && r.text.trim());
    const ans = part(run, 'answer');
    if (texts.length) {
      const full = texts.map((r) => r.text).join('\n\n');
      ans.innerHTML = `<div class="rv-answer-text">${formatAnswer(full)}</div>`;
      // Clamp by rendered height (CSS max-height), not characters, so tables/lists fold sensibly.
      // While the drawer is not laid out (clientHeight 0) fall back to a length heuristic.
      const textEl = ans.querySelector('.rv-answer-text');
      ans.classList.add('is-clamped');
      const laidOut = textEl.clientHeight > 0;
      const overflows = laidOut ? textEl.scrollHeight > textEl.clientHeight + 24 : full.length > 1400;
      if (!overflows) ans.classList.remove('is-clamped');
      else {
        const more = document.createElement('button');
        more.type = 'button';
        more.className = 'rv-link rv-more';
        more.textContent = 'Read the whole answer';
        more.setAttribute('aria-expanded', 'false');
        more.addEventListener('click', () => {
          const open = ans.classList.toggle('is-clamped') === false;
          more.setAttribute('aria-expanded', String(open));
          more.textContent = open ? 'Fold the answer' : 'Read the whole answer';
        });
        ans.appendChild(more);
      }
    } else {
      ans.innerHTML = `<p class="rv-note">${results.length ? 'The model wrote no prose answer for this question' + (results.some((r) => r && r.tool) ? ' — only the visions below.' : '.') : 'The server returned no results for this question.'}</p>`;
    }
    sec(run, 'answer').hidden = false;

    // Visions (every result with a tool, failures shown honestly).
    const charts = results.filter((r) => r && r.tool);
    const vis = part(run, 'visions');
    if (charts.length) {
      charts.forEach((r) => {
        try { u.charts.push(renderResult(vis, r, { V })); } catch (e) {
          console.error('[voyage] renderResult failed', e);
          vis.insertAdjacentHTML('beforeend', `<p class="rv-note">A vision (${esc(r.tool)}) could not be shown: ${esc(e.message || e)}</p>`);
        }
      });
    } else {
      vis.innerHTML = '<p class="rv-note">No charts were drawn for this question.</p>';
    }
    sec(run, 'visions').hidden = false;

    // Evidence (retrieved context).
    renderContext(part(run, 'evidence'), resp.retrieval || null);
    sec(run, 'evidence').hidden = false;

    renderDiscoveries(run);
    renderPaths(run);

    if (run === active && scroller.scrollTop < 60) {
      const h = sec(run, 'answer');
      requestAnimationFrame(() => h.scrollIntoView({ block: 'start', behavior: prefersReducedMotion() ? 'auto' : 'smooth' }));
    }
  }

  // ---------------------------------------------------------------- footer + log
  function nextChapterOf(run) {
    const ch = run && (run.chapter || (store && store.state && V && V.chapterById && V.chapterById.get(store.state.current)));
    const next = ch && arr(ch.next_chapters).find((id) => typeof id === 'string');
    return next && V && V.chapterById && V.chapterById.has(next) ? next : null;
  }
  function updateFooter() {
    // "Continue the Voyage" belongs to a finished inquiry (FRONTEND_CONTRACT step 7); an error must
    // not trap the traveller either. While the research is still working the button stays hidden.
    // It is the primary call to action only after a chapter's inquiry succeeds; after an error (Retry
    // leads) or a free-form question it stays available but de-emphasised.
    const settled = active && (active.state === 'done' || active.state === 'error');
    const next = settled ? nextChapterOf(active) : null;
    continueBtn.hidden = !next;
    continueBtn.classList.toggle('rs-btn-primary', !!(next && active.state === 'done' && active.chapter));
    if (next) continueBtn.title = `Next: ${chapterLabel(next)}`;
  }

  function renderLog(runs) {
    paintLauncher(runs);
    const list = arr(runs).slice(0, 8);
    logEl.hidden = list.length < 2;
    logEl.innerHTML = list.map((r) => `
      <button type="button" class="rs-log-item${r === active ? ' is-active' : ''}"${r === active ? ' aria-current="true"' : ''} data-state="${esc(r.state)}" data-run-id="${esc(r.id)}" title="${esc(r.title)}">
        <span class="rs-log-dot" aria-hidden="true"></span><span class="rs-log-text">${esc(r.title)}</span><span class="sr-only"> — ${esc(PILL[r.state] || r.state)}</span>
      </button>`).join('');
    logEl.querySelectorAll('[data-run-id]').forEach((b) => {
      b.addEventListener('click', () => {
        const r = list.find((x) => x.id === b.dataset.runId);
        if (r) actions.focusRun(r);
      });
    });
  }

  // ---------------------------------------------------------------- public API
  const api = {
    get isOpen() { return isOpen; },
    get isFocus() { return isFocus; },

    open({ silent = false } = {}) {
      if (isOpen) return false;
      isOpen = true;
      lastFocus = document.activeElement;
      root.setAttribute('aria-hidden', 'false');
      root.removeAttribute('inert');
      document.body.classList.add('research-open');
      syncTimer();
      if (!silent) bus.emit('research:open', {});
      return true;
    },
    close({ silent = false } = {}) {
      if (!isOpen) return false;
      if (isFocus) api.setFocus(false, { silent });
      isOpen = false;
      root.setAttribute('aria-hidden', 'true');
      root.setAttribute('inert', '');
      document.body.classList.remove('research-open');
      syncTimer();
      if (!silent) bus.emit('research:close', {});
      if (lastFocus && typeof lastFocus.focus === 'function' && document.contains(lastFocus) && !root.contains(lastFocus)) {
        try { lastFocus.focus({ preventScroll: true }); } catch { /* ignore */ }
      }
      return true;
    },
    setFocus(on, { silent = false } = {}) {
      on = !!on;
      if (on === isFocus) return;
      if (on && !isOpen) api.open({ silent });
      isFocus = on;
      document.body.classList.toggle('research-focus', on);
      root.querySelectorAll('[data-act="focus"]').forEach((b) => b.setAttribute('aria-pressed', String(on)));
      if (!silent) bus.emit('research:focus', { on });
    },

    setMeta(m) {
      meta = m && typeof m === 'object' ? m : null;
      if (!active) stage.innerHTML = idleView();
    },

    /** Show a run (or the idle view when null). */
    show(run, { focusHeading = false } = {}) {
      active = run || null;
      [...stage.children].forEach((c) => c.remove());
      if (!run) {
        stage.innerHTML = idleView();
      } else {
        const u = ui(run);
        stage.appendChild(u.view);
        paintState(run);
        if (focusHeading && isOpen) {
          const h = u.view.querySelector('.rv-q');
          requestAnimationFrame(() => { try { h.focus({ preventScroll: true }); } catch { /* ignore */ } });
        }
      }
      scroller.scrollTop = 0;
      updateFooter();
      syncTimer();
    },

    /** Repaint a run after a state change (works for runs not currently shown, too). */
    update(run) {
      if (!run) return;
      paintState(run);
    },

    /** Reveal the real trace. Resolves when the replay finishes (or is skipped). */
    renderRetrieval(run, rootName) {
      const u = ui(run);
      const wrap = part(run, 'lineage');
      wrap.hidden = false;
      const skip = u.view.querySelector('[data-run="skip"]');
      const fold = u.view.querySelector('[data-run="fold"]');
      skip.hidden = false;
      fold.hidden = true;
      const retrieval = run.response ? run.response.retrieval || null : null;
      u.replay = renderLineage(part(run, 'lineage-body'), retrieval, { rootName });
      // Nobody is watching a background run: show its lineage at once instead of animating it.
      if (run !== active || !isOpen) u.replay.skip();
      return u.replay.done.then(() => {
        skip.hidden = true;
        const s = summarizeTrace(retrieval);
        fold.hidden = !(s.branches + s.leaves > 0);
      });
    },
    skipReplay(run) {
      if (run && run.ui && run.ui.replay) run.ui.replay.skip();
    },

    renderResults(run) { renderResultsInto(run); },

    setLog(runs) { renderLog(runs); },

    focusInput() {
      if (!isOpen) api.open();
      scroller.scrollTop = 0;
      requestAnimationFrame(() => { try { input.focus(); } catch { /* ignore */ } });
    },

    /** Release a run's DOM and charts. */
    disposeRun(run) {
      if (!run || !run.ui) return;
      run.ui.charts.forEach((c) => { try { c.dispose(); } catch { /* ignore */ } });
      run.ui.charts = [];
      if (run.ui.replay) run.ui.replay.skip();
      if (run.ui.view) run.ui.view.remove();
      run.ui = null;
    },
  };

  root.setAttribute('inert', '');
  stage.innerHTML = idleView();
  return api;
}
