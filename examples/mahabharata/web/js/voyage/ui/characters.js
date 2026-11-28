// ui/characters.js — CHARACTERS (#view-characters)
// Filterable gallery by faction with search; each card opens a detail drawer with the character's summary,
// relationships grouped by type (each clickable), chapters where they appear and an "Ask about" action.

import { esc } from '../core/util.js';

const FACTIONS = [
  ['pandava', 'Pandava'], ['kaurava', 'Kaurava'], ['neutral', 'Neutral'], ['divine', 'Divine'], ['narrator', 'Narrators'],
];
const FACTION_LABEL = { pandava: 'Pandava side', kaurava: 'Kaurava side', neutral: 'Neutral', divine: 'Divine', narrator: 'Narrator' };

// Relationship groups. "from" is read as the first party of the relation (e.g. parent → child).
const REL_GROUPS = [
  ['parent', 'to', 'Parents'], ['parent', 'from', 'Children'],
  ['adoptive_parent', 'to', 'Adoptive parents'], ['adoptive_parent', 'from', 'Adopted children'],
  ['guardian', 'to', 'Guardians'], ['guardian', 'from', 'Wards'],
  ['spouse', '*', 'Spouses'], ['sibling', '*', 'Siblings'], ['half_sibling', '*', 'Half-siblings'],
  ['teacher', 'to', 'Teachers'], ['teacher', 'from', 'Students'],
  ['charioteer', 'from', 'Charioteer to'], ['charioteer', 'to', 'Charioteers'],
  ['ally', '*', 'Allies'], ['friend', '*', 'Friends'], ['rival', '*', 'Rivals'],
];

const epithetsOf = (c) => (Array.isArray(c?.epithets) ? c.epithets.filter((x) => typeof x === 'string' && x.trim()) : []);
const norm = (s) => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
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
const HEX = /^#[0-9a-f]{3,8}$/i;
const safeColor = (c) => (typeof c === 'string' && HEX.test(c.trim()) ? c.trim() : '');
function initials(name) {
  const parts = String(name || '?').replace(/[^\p{L}\s]/gu, '').trim().split(/\s+/);
  return (parts[0]?.[0] || '?').toUpperCase();
}

export function initCharacters({ V, store, bus }) {
  const root = ensureSection('view-characters');
  const chars = (V?.characters || []).filter((c) => c && c.id);
  const charById = V?.characterById instanceof Map ? V.characterById : new Map(chars.map((c) => [c.id, c]));
  const chapters = (V?.chapters || []).filter((c) => c && c.id);

  // relationships by character (both directions) — use V's index when present
  let relsBy = V?.relationshipsByChar instanceof Map ? V.relationshipsByChar : null;
  if (!relsBy) {
    relsBy = new Map();
    for (const r of V?.relationships || []) {
      if (!r || !r.from || !r.to) continue;
      for (const id of [r.from, r.to]) { if (!relsBy.has(id)) relsBy.set(id, []); relsBy.get(id).push(r); }
    }
  }
  // chapters where each character appears (chapter.related_characters)
  const appearsIn = new Map();
  for (const ch of chapters) {
    for (const cid of ch.related_characters || []) { if (!appearsIn.has(cid)) appearsIn.set(cid, []); appearsIn.get(cid).push(ch); }
  }
  for (const list of appearsIn.values()) list.sort((a, b) => (a.chapter_order ?? 0) - (b.chapter_order ?? 0));

  const st = () => store?.state || {};
  const isVisited = (id) => { try { return store?.isVisited ? !!store.isVisited(id) : (st().visited || []).includes(id); } catch { return false; } };
  const isUnlocked = (ch) => { try { return store?.isUnlocked ? !!store.isUnlocked(ch) : true; } catch { return true; } };
  const encountered = (c) => (appearsIn.get(c.id) || []).some((ch) => isVisited(ch.id)) || (c.first_chapter && isVisited(c.first_chapter));

  const counts = Object.fromEntries(FACTIONS.map(([k]) => [k, chars.filter((c) => c.faction === k).length]));

  /* ---------------- DOM */
  root.classList.add('vw', 'cx');
  root.setAttribute('role', 'region');
  root.setAttribute('aria-labelledby', 'view-characters-title');
  root.innerHTML = `
    <header class="vw-head">
      <div class="vw-titles">
        <p class="vw-eyebrow">Dramatis personae</p>
        <h2 class="vw-title" id="view-characters-title">Characters</h2>
      </div>
      <button type="button" class="vw-close" data-act="close" aria-label="Close characters and return to the journey"><span aria-hidden="true">×</span></button>
    </header>
    <div class="cx-toolbar">
      <label class="vw-search">
        <span class="vh">Search characters</span>
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L21 21"/></svg>
        <input type="search" class="cx-q" placeholder="Search by name, epithet, role or side…" autocomplete="off" spellcheck="false">
      </label>
      <div class="vw-chips cx-filters" role="group" aria-label="Filter by faction">
        <button type="button" class="chip vw-chip fchip" data-faction="" aria-pressed="true">All <span class="ct">${chars.length}</span></button>
        ${FACTIONS.map(([k, label]) => `<button type="button" class="chip vw-chip fchip fac-${k}" data-faction="${k}" aria-pressed="false"${counts[k] ? '' : ' disabled'}>
          <span class="dot" aria-hidden="true"></span>${label} <span class="ct">${counts[k]}</span></button>`).join('')}
      </div>
      <p class="cx-count" aria-live="polite"></p>
    </div>
    <div class="cx-scroll">
      ${chars.length ? `<ul class="cx-grid" role="list">${chars.map(cardHTML).join('')}</ul>` : `<p class="vw-empty">The cast is still being gathered — character data has not loaded yet.</p>`}
      <p class="cx-none vw-empty" hidden></p>
    </div>
    <aside class="cx-drawer vw-sheet" hidden role="dialog" aria-modal="false" aria-labelledby="cx-d-title"></aside>`;

  function cardHTML(c) {
    const vi = c.visual_identity || {};
    const c1 = safeColor(vi.primary), c2 = safeColor(vi.secondary);
    const ep = epithetsOf(c).slice(0, 2);
    return `<li class="cx-item fac-${esc(c.faction || 'neutral')}" data-id="${esc(c.id)}">
      <button type="button" class="cx-card" data-act="open" data-id="${esc(c.id)}" style="${c1 ? `--c1:${c1};` : ''}${c2 ? `--c2:${c2};` : ''}">
        <span class="cx-medal" aria-hidden="true"><span>${esc(initials(c.name))}</span></span>
        <span class="cx-name">${esc(c.name || c.id)}</span>
        ${ep.length ? `<span class="cx-ep">${ep.map(esc).join(' · ')}</span>` : ''}
        ${c.role ? `<span class="cx-role">${esc(c.role)}</span>` : ''}
        <span class="cx-foot">
          <span class="cx-fac">${esc(FACTION_LABEL[c.faction] || c.faction || '')}</span>
          <span class="cx-sw" aria-hidden="true">${c1 ? '<i class="a"></i>' : ''}${c2 ? '<i class="b"></i>' : ''}</span>
        </span>
        <span class="cx-met">Encountered</span>
      </button>
    </li>`;
  }

  const qInput = root.querySelector('.cx-q');
  const countEl = root.querySelector('.cx-count');
  const noneEl = root.querySelector('.cx-none');
  const drawer = root.querySelector('.cx-drawer');
  const hay = new Map(chars.map((c) => [c.id, norm([c.name, c.id, ...epithetsOf(c), c.role, c.faction, FACTION_LABEL[c.faction]].join(' '))]));
  let faction = '', query = '';

  function applyFilter() {
    const q = norm(query.trim());
    let shown = 0;
    root.querySelectorAll('.cx-item').forEach((li) => {
      const c = charById.get(li.dataset.id);
      const ok = c && (!faction || c.faction === faction) && (!q || (hay.get(c.id) || '').includes(q));
      li.hidden = !ok;
      if (ok) shown++;
    });
    countEl.textContent = chars.length ? `Showing ${shown} of ${chars.length}` : '';
    noneEl.hidden = shown > 0 || !chars.length;
    if (!noneEl.hidden) noneEl.textContent = query.trim() ? `No one matches “${query.trim()}”${faction ? ' in this faction' : ''}.` : 'No characters in this faction.';
  }
  function refreshStates() {
    root.querySelectorAll('.cx-item').forEach((li) => {
      const c = charById.get(li.dataset.id);
      li.classList.toggle('is-met', !!c && encountered(c));
    });
  }

  qInput?.addEventListener('input', () => { query = qInput.value; applyFilter(); });
  root.querySelector('.cx-filters')?.addEventListener('click', (e) => {
    const b = e.target.closest('.fchip');
    if (!b || b.disabled) return;
    faction = b.dataset.faction || '';
    root.querySelectorAll('.fchip').forEach((x) => x.setAttribute('aria-pressed', x === b ? 'true' : 'false'));
    applyFilter();
  });

  /* ---------------- drawer */
  let openId = null, history = [], lastFocus = null;

  function relGroups(id) {
    const rels = relsBy.get(id) || [];
    const groups = [];
    for (const [type, side, label] of REL_GROUPS) {
      const items = rels.filter((r) => r.type === type && (side === '*' || (side === 'from' ? r.from === id : r.to === id)));
      if (items.length) groups.push({ label, items });
    }
    // anything of an unrecognised type still shows, honestly labelled
    const known = new Set(REL_GROUPS.map((g) => g[0]));
    const rest = rels.filter((r) => !known.has(r.type));
    if (rest.length) groups.push({ label: 'Other ties', items: rest });
    return groups;
  }

  // mode: 'root' (fresh open, clears history) | 'push' (followed a relationship) | 'back' (popped history)
  function openDrawer(id, { mode = 'root', focus = true } = {}) {
    const c = charById.get(id);
    if (!c) return;
    if (mode === 'push' && openId && openId !== id) history.push(openId);
    if (mode === 'root') { history = []; lastFocus = root.querySelector(`.cx-card[data-id="${CSS.escape(id)}"]`) || document.activeElement; }
    openId = id;
    const vi = c.visual_identity || {};
    const c1 = safeColor(vi.primary), c2 = safeColor(vi.secondary);
    const groups = relGroups(id);
    const apps = appearsIn.get(id) || [];
    const cur = st().current;
    const back = history.length ? charById.get(history[history.length - 1]) : null;

    drawer.className = `cx-drawer vw-sheet fac-${c.faction || 'neutral'}`;
    drawer.style.cssText = `${c1 ? `--c1:${c1};` : ''}${c2 ? `--c2:${c2};` : ''}`;
    drawer.innerHTML = `
      <div class="vw-sheet-grip" aria-hidden="true"></div>
      <div class="cx-d-top">
        ${back ? `<button type="button" class="btn btn-ghost vw-btn sm" data-act="back">← ${esc(back.name || back.id)}</button>` : '<span></span>'}
        <button type="button" class="vw-close sm" data-act="drawer-close" aria-label="Close character details"><span aria-hidden="true">×</span></button>
      </div>
      <div class="cx-d-hero">
        <span class="cx-medal lg" aria-hidden="true"><span>${esc(initials(c.name))}</span></span>
        <div>
          <p class="vw-eyebrow">${esc(FACTION_LABEL[c.faction] || c.faction || '')}</p>
          <h3 class="cx-d-title" id="cx-d-title" tabindex="-1">${esc(c.name || c.id)}</h3>
          ${epithetsOf(c).length ? `<p class="cx-d-ep">${epithetsOf(c).map(esc).join(' · ')}</p>` : ''}
        </div>
      </div>
      ${c.role ? `<p class="cx-d-role">${esc(c.role)}</p>` : ''}
      ${c.summary ? `<p class="cx-d-sum">${esc(c.summary)}</p>` : ''}
      ${(c1 || c2 || vi.silhouette) ? `<div class="cx-d-look">
          <span class="cx-sw lg" aria-hidden="true">${c1 ? '<i class="a"></i>' : ''}${c2 ? '<i class="b"></i>' : ''}</span>
          ${vi.silhouette ? `<p><span class="vw-label">Depicted in this voyage as</span>${esc(vi.silhouette)}</p>` : ''}
        </div>` : ''}
      <div class="cx-d-actions">
        <button type="button" class="btn btn-gold vw-btn" data-act="ask" data-id="${esc(c.id)}">Ask about ${esc(c.name || c.id)}</button>
        <button type="button" class="btn btn-ghost vw-btn" data-act="web" data-id="${esc(c.id)}">Show in the relationship web</button>
      </div>
      <h4 class="vw-h4">Relationships</h4>
      ${groups.length ? groups.map((g) => `<div class="cx-rel-group"><p class="vw-label">${esc(g.label)}</p><ul class="cx-rels">
          ${g.items.map((r) => relItem(r, id)).join('')}</ul></div>`).join('')
        : '<p class="vw-muted">No relationships are recorded for this character yet.</p>'}
      <h4 class="vw-h4">Where they appear</h4>
      ${apps.length ? `<ol class="cx-apps">${apps.map((ch) => {
          const visited = isVisited(ch.id), current = ch.id === cur, locked = !visited && !isUnlocked(ch);
          const s = current ? 'You are here' : visited ? 'Visited' : locked ? 'Ahead in the story' : 'Not yet visited';
          return `<li><button type="button" class="cx-app is-${current ? 'current' : visited ? 'visited' : locked ? 'locked' : 'open'}" data-act="chapter" data-chapter="${esc(ch.id)}">
            <span class="n">${esc(chapterLabel(ch))}</span><span class="t">${esc(ch.title || ch.id)}</span><span class="s">${s}</span></button></li>`;
        }).join('')}</ol>`
        : '<p class="vw-muted">No chapter of this voyage features them yet.</p>'}`;
    drawer.hidden = false;
    root.classList.add('has-drawer');
    root.querySelectorAll('.cx-card').forEach((b) => b.classList.toggle('is-open', b.dataset.id === id));
    drawer.scrollTop = 0;
    if (focus) requestAnimationFrame(() => drawer.querySelector('#cx-d-title')?.focus({ preventScroll: true }));
  }

  function relItem(r, selfId) {
    const otherId = r.from === selfId ? r.to : r.from;
    const o = charById.get(otherId);
    const name = o?.name || otherId;
    const fromName = charById.get(r.from)?.name || r.from, toName = charById.get(r.to)?.name || r.to;
    const sw = safeColor(o?.visual_identity?.primary);
    return `<li><button type="button" class="cx-rel" data-act="rel" data-id="${esc(otherId)}" ${o ? '' : 'disabled'} title="${esc(r.note || '')}">
      <span class="dot" ${sw ? `style="--sw:${sw}"` : ''} aria-hidden="true"></span>
      <span class="nm">${esc(name)}</span>
      ${r.label ? `<span class="lb">${esc(fromName)} → ${esc(toName)} · ${esc(r.label)}</span>` : ''}
      ${r.note ? `<span class="nt">${esc(r.note)}</span>` : ''}
    </button></li>`;
  }

  function closeDrawer({ silent = false } = {}) {
    if (drawer.hidden) return;
    drawer.hidden = true;
    root.classList.remove('has-drawer');
    root.querySelectorAll('.cx-card.is-open').forEach((b) => b.classList.remove('is-open'));
    openId = null; history = [];
    if (!silent && lastFocus && root.contains(lastFocus)) lastFocus.focus({ preventScroll: true });
  }

  const toJourney = () => { closeDrawer({ silent: true }); bus?.emit?.('view:change', { view: 'journey' }); ensureBodyView('journey'); };

  root.addEventListener('click', (e) => {
    const b = e.target.closest?.('[data-act]');
    if (!b || !root.contains(b)) return;
    const act = b.dataset.act;
    if (act === 'close') toJourney();
    else if (act === 'open') openDrawer(b.dataset.id);
    else if (act === 'drawer-close') closeDrawer();
    else if (act === 'rel') openDrawer(b.dataset.id, { mode: 'push' });
    else if (act === 'back') { const prev = history.pop(); if (prev) openDrawer(prev, { mode: 'back' }); }
    else if (act === 'chapter') { const id = b.dataset.chapter; toJourney(); bus?.emit?.('chapter:enter', { chapterId: id, from: 'characters' }); }
    else if (act === 'ask') {
      const c = charById.get(b.dataset.id); if (!c) return;
      toJourney();
      bus?.emit?.('question:select', { query: `Who is ${c.name} in the Mahabharata, what choices define them, and how do those choices shape the epic?` });
    }
    else if (act === 'web') {
      const id = b.dataset.id;
      closeDrawer({ silent: true });
      bus?.emit?.('view:change', { view: 'knowledge' });
      ensureBodyView('knowledge');
      bus?.emit?.('knowledge:focus', { id });
    }
  });
  root.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    e.stopPropagation(); e.preventDefault();
    if (!drawer.hidden) closeDrawer();
    else if (document.activeElement === qInput && qInput.value) { qInput.value = ''; query = ''; applyFilter(); }
    else toJourney();
  });

  /* ---------------- lifecycle */
  bus?.on?.('view:change', (p) => { if (p?.view) lastView = p.view; if (p?.view === 'characters') ensureBodyView('characters'); });
  bus?.on?.('ui:escape', (evt) => {
    if (document.body.classList.contains('view-characters') && !drawer.hidden) { closeDrawer(); if (evt) evt.handled = true; }
  });
  bus?.on?.('store:change', refreshStates);
  bus?.on?.('entity:open', (p) => {
    if (p?.type !== 'character' || !charById.has(p.id)) return;
    if (!document.body.classList.contains('view-characters')) { bus.emit('view:change', { view: 'characters' }); ensureBodyView('characters'); }
    setTimeout(() => openDrawer(p.id, { focus: true }), 40);
  });

  applyFilter();
  refreshStates();
}
