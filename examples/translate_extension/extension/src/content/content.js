// In-page UI: a "Translate" bubble next to selected text and a result card.
// Everything renders inside a closed Shadow DOM so page CSS and ours can't collide.
// Content scripts can't be ES modules, so this file is self-contained.
(() => {
  if (globalThis.__bodhanTranslateLoaded) return; // may be injected twice via the context menu
  globalThis.__bodhanTranslateLoaded = true;

  const MAX_SELECTION = 5000;
  const GAP = 8;

  const CSS = `
    :host { all: initial; }
    * { box-sizing: border-box; font-family: system-ui, -apple-system, "Segoe UI", "Noto Sans", sans-serif; }
    [hidden] { display: none !important; }
    .bubble, .card {
      --bg: #ffffff; --fg: #1d1d1f; --muted: #6b6b73; --line: #e3e3e8;
      --accent: #c2410c; --accent-fg: #ffffff; --soft: #f5f5f7; --danger: #b42318;
      position: absolute; color: var(--fg);
    }
    @media (prefers-color-scheme: dark) {
      .bubble, .card {
        --bg: #1f1f23; --fg: #f2f2f5; --muted: #a1a1aa; --line: #34343b;
        --accent: #fb923c; --accent-fg: #1a1a1a; --soft: #2a2a30; --danger: #f97066;
      }
    }
    .bubble {
      width: 30px; height: 30px; border-radius: 15px; border: none; cursor: pointer;
      background: var(--accent); color: var(--accent-fg); font-size: 16px; font-weight: 600;
      box-shadow: 0 2px 8px rgb(0 0 0 / 0.25); display: grid; place-items: center; padding: 0;
    }
    .bubble:hover { filter: brightness(1.08); }
    .card {
      width: min(380px, calc(100vw - 16px)); background: var(--bg); border: 1px solid var(--line);
      border-radius: 12px; box-shadow: 0 8px 28px rgb(0 0 0 / 0.22); font-size: 14px; line-height: 1.5;
    }
    .head {
      display: flex; align-items: center; gap: 6px; padding: 8px 10px;
      border-bottom: 1px solid var(--line);
    }
    .brand { font-weight: 600; font-size: 13px; margin-right: auto; }
    .arrow { color: var(--muted); font-size: 12px; }
    select {
      font-size: 12px; padding: 3px 4px; border-radius: 6px; max-width: 150px;
      border: 1px solid var(--line); background: var(--soft); color: var(--fg);
    }
    .badge {
      font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em;
      padding: 1px 6px; border-radius: 999px; background: var(--soft); color: var(--muted);
    }
    .icon {
      border: none; background: transparent; color: var(--muted); cursor: pointer;
      font-size: 18px; line-height: 1; padding: 2px 4px; border-radius: 6px;
    }
    .icon:hover { background: var(--soft); color: var(--fg); }
    .body {
      padding: 10px 12px; max-height: 260px; overflow: auto; white-space: pre-wrap;
      overflow-wrap: anywhere; font-size: 15px;
    }
    .body.loading { color: var(--muted); font-style: italic; }
    .body.error { color: var(--danger); font-size: 13px; }
    .note { padding: 0 12px 8px; font-size: 12px; color: var(--muted); }
    .foot { display: flex; gap: 6px; padding: 8px 10px; border-top: 1px solid var(--line); }
    .btn {
      font-size: 12px; padding: 4px 10px; border-radius: 6px; cursor: pointer;
      border: 1px solid var(--line); background: var(--soft); color: var(--fg);
    }
    .btn:hover { border-color: var(--muted); }
    .btn.primary { background: var(--accent); color: var(--accent-fg); border-color: transparent; }
    .btn:disabled { opacity: 0.5; cursor: default; }
  `;

  const host = document.createElement('bodhan-translate');
  host.style.cssText = 'all: initial; position: absolute; top: 0; left: 0; z-index: 2147483647;';
  const root = host.attachShadow({ mode: 'closed' });
  root.innerHTML = `
    <style>${CSS}</style>
    <button class="bubble" type="button" hidden title="Translate with Bodhan" aria-label="Translate with Bodhan">अ</button>
    <div class="card" role="dialog" aria-label="Bodhan translation" hidden>
      <div class="head">
        <span class="brand">Bodhan</span>
        <span class="badge mock" hidden>mock</span>
        <span class="arrow">to</span>
        <select class="target" aria-label="Translate to"></select>
        <button class="icon close" type="button" aria-label="Close">×</button>
      </div>
      <div class="body" dir="auto" aria-live="polite"></div>
      <div class="note" hidden></div>
      <div class="foot">
        <button class="btn listen" type="button" disabled>Listen</button>
        <button class="btn copy" type="button">Copy</button>
        <button class="btn settings" type="button">Settings</button>
      </div>
    </div>`;

  const $ = (sel) => root.querySelector(sel);
  const bubble = $('.bubble');
  const card = $('.card');
  const targetSelect = $('.target');
  const body = $('.body');
  const note = $('.note');
  const mockBadge = $('.mock');
  const copyButton = $('.copy');
  const listenButton = $('.listen');
  const settingsButton = $('.settings');

  let selectedText = '';
  let anchor = null; // selection rect in document coordinates
  let requestSeq = 0;
  let configPromise = null;
  let translatedTarget = ''; // language of the text now in the card
  let player = null; // { ctx, source, stopped } while reading aloud

  function loadConfig() {
    configPromise ??= send({ type: 'get-config' }).then((res) => {
      if (!res.ok) {
        configPromise = null;
        return null;
      }
      fillSelect(targetSelect, res.languages, res.target);
      return res;
    });
    return configPromise;
  }

  function fillSelect(select, languages, value) {
    select.replaceChildren(
      ...languages.map((l) => {
        const option = new Option(l.native, l.code, false, l.code === value);
        option.title = l.name;
        return option;
      }),
    );
  }

  async function send(message) {
    try {
      return await chrome.runtime.sendMessage(message);
    } catch {
      return {
        ok: false,
        code: 'context_invalidated',
        error: 'Bodhan Translate was updated or reloaded. Refresh this page to keep using it.',
      };
    }
  }

  function mount() {
    if (!host.isConnected) document.documentElement.append(host);
  }

  // ---- selection tracking ----

  function readSelection() {
    const selection = getSelection();
    if (!selection || selection.isCollapsed || selection.rangeCount === 0) return null;
    const text = selection.toString().trim();
    if (!text) return null;
    const range = selection.getRangeAt(0);
    const rects = range.getClientRects();
    const rect = rects.length ? rects[rects.length - 1] : range.getBoundingClientRect();
    const full = range.getBoundingClientRect();
    return {
      text,
      end: { x: rect.right + scrollX, y: rect.bottom + scrollY },
      box: { left: full.left + scrollX, top: full.top + scrollY, bottom: full.bottom + scrollY },
    };
  }

  function isOwnEvent(event) {
    return event.composedPath().includes(host);
  }

  function onSelectionMaybeChanged(event) {
    if (isOwnEvent(event)) return;
    // Let the browser finish updating the selection first.
    setTimeout(async () => {
      const sel = readSelection();
      const config = await loadConfig();
      if (!sel || !config?.showBubble || sel.text.length > MAX_SELECTION) {
        bubble.hidden = true;
        return;
      }
      selectedText = sel.text;
      anchor = sel.box;
      mount();
      bubble.style.left = `${clampX(sel.end.x + 4, 30)}px`;
      bubble.style.top = `${sel.end.y + 4}px`;
      bubble.hidden = false;
    }, 0);
  }

  function clampX(x, width) {
    return Math.max(scrollX + GAP, Math.min(x, scrollX + innerWidth - width - GAP));
  }

  // ---- result card ----

  /**
   * Shows the card and translates `text`. `picked` is a language chosen in the card's dropdown:
   * it applies to this card only and never changes the default, which is set in Settings. Without
   * it (the अ bubble, the right-click menu), the card uses the default.
   */
  async function showTranslation(text, { picked = '' } = {}) {
    bubble.hidden = true;
    stopListening();
    selectedText = text;
    mount();
    card.hidden = false;
    placeCard();
    setBody('Translating…', 'loading');
    note.hidden = true;
    copyButton.disabled = true;
    listenButton.disabled = true;

    const seq = ++requestSeq;
    const config = await loadConfig();
    if (seq !== requestSeq) return; // a newer request replaced this one
    // loadConfig() may have refilled the dropdown (after a settings change), so set it explicitly:
    // the card's own pick, or else the current default.
    const target = picked || config?.target || targetSelect.value;
    targetSelect.value = target;
    const res = await send({ type: 'translate', text, target });
    if (seq !== requestSeq) return; // a newer request replaced this one

    if (!res.ok) {
      setBody(res.error, 'error');
      highlightSettings(res.code);
    } else {
      setBody(res.text, '');
      body.lang = res.target;
      translatedTarget = res.target;
      mockBadge.hidden = !res.mock;
      copyButton.disabled = false;
      listenButton.disabled = false;
      settingsButton.classList.remove('primary');
      if (res.truncated) showNote('Bodhan reported that part of this translation may be missing.');
    }
    placeCard();
  }

  function showNote(text) {
    note.textContent = text;
    note.hidden = false;
  }

  function highlightSettings(code) {
    settingsButton.classList.toggle('primary', ['not_configured', 'auth', 'model_denied'].includes(code));
  }

  function setBody(text, state) {
    body.textContent = text; // never innerHTML: API output is untrusted
    body.className = `body ${state}`.trim();
  }

  function placeCard() {
    const box = anchor ?? {
      left: scrollX + innerWidth,
      top: scrollY + GAP,
      bottom: scrollY + GAP,
    };
    const width = card.offsetWidth;
    const height = card.offsetHeight;
    let top = box.bottom + GAP;
    const fitsBelow = top + height <= scrollY + innerHeight - GAP;
    const fitsAbove = box.top - GAP - height >= scrollY + GAP;
    if (!fitsBelow && fitsAbove) top = box.top - GAP - height;
    card.style.left = `${clampX(box.left, width)}px`;
    card.style.top = `${top}px`;
  }

  function hideCard() {
    stopListening();
    card.hidden = true;
    requestSeq++; // ignore any in-flight response
  }

  async function copyResult() {
    const text = body.textContent;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Clipboard API needs focus/secure context; fall back to execCommand.
      const area = document.createElement('textarea');
      area.value = text;
      root.append(area);
      area.select();
      document.execCommand('copy');
      area.remove();
    }
    copyButton.textContent = 'Copied';
    setTimeout(() => (copyButton.textContent = 'Copy'), 1200);
  }

  // ---- read aloud ----
  // Played with Web Audio rather than an <audio> element: a page's Content Security
  // Policy can block media URLs, but not audio the extension decodes itself.

  async function toggleListen() {
    if (player) return stopListening();
    // Created inside the click, so the browser lets it play sound.
    const session = { ctx: new AudioContext(), source: null, stopped: false };
    player = session;
    listenButton.textContent = 'Loading…';
    const text = body.textContent;
    const target = translatedTarget;

    let next = send({ type: 'speak', text, target, index: 0 });
    for (let i = 0; ; i++) {
      const res = await next;
      if (session.stopped) return;
      if (!res.ok) {
        showNote(res.error);
        highlightSettings(res.code);
        break;
      }
      // Fetch the next segment while this one plays: each takes a few seconds to make.
      if (i + 1 < res.count) next = send({ type: 'speak', text, target, index: i + 1 });
      let buffer;
      try {
        buffer = await session.ctx.decodeAudioData(base64ToBuffer(res.audio));
      } catch {
        showNote('Could not play the audio from Bodhan.');
        break;
      }
      if (session.stopped) return;
      listenButton.textContent = 'Stop';
      await play(session, buffer);
      if (session.stopped || i + 1 >= res.count) break;
    }
    if (player === session) stopListening();
  }

  function play(session, buffer) {
    return new Promise((resolve) => {
      const source = session.ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(session.ctx.destination);
      source.onended = resolve;
      session.source = source;
      source.start();
    });
  }

  function stopListening() {
    if (player) {
      player.stopped = true;
      try {
        player.source?.stop();
      } catch {
        // Not started yet.
      }
      player.ctx.close();
      player = null;
    }
    listenButton.textContent = 'Listen';
  }

  function base64ToBuffer(base64) {
    return Uint8Array.from(atob(base64), (c) => c.charCodeAt(0)).buffer;
  }

  // ---- wiring ----

  bubble.addEventListener('mousedown', (e) => e.preventDefault()); // keep the page selection
  bubble.addEventListener('click', () => showTranslation(selectedText));
  $('.close').addEventListener('click', hideCard);
  copyButton.addEventListener('click', copyResult);
  listenButton.addEventListener('click', toggleListen);
  settingsButton.addEventListener('click', () => send({ type: 'open-options' }));
  // Read the pick here, before showTranslation awaits anything.
  targetSelect.addEventListener('change', () => showTranslation(selectedText, { picked: targetSelect.value }));

  document.addEventListener('mouseup', onSelectionMaybeChanged, true);
  document.addEventListener('keyup', (e) => e.shiftKey && onSelectionMaybeChanged(e), true);
  document.addEventListener(
    'mousedown',
    (e) => {
      if (isOwnEvent(e)) return;
      bubble.hidden = true;
      hideCard();
    },
    true,
  );
  document.addEventListener(
    'keydown',
    (e) => {
      if (e.key !== 'Escape') return;
      bubble.hidden = true;
      hideCard();
    },
    true,
  );

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'local') configPromise = null; // settings changed: reload on next use
  });

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== 'show-translation') return false;
    anchor = readSelection()?.box ?? null;
    showTranslation(message.text);
    sendResponse({ ok: true });
    return false;
  });
})();
