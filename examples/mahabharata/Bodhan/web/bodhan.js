/* Bodhan frontend: language picker + voice input in the "Ask the epic" form, and auto-spoken answers.
 *   typed/spoken question (te/hi/en) -> server translates to English -> LLM -> answer translated back
 *   mic:   record -> WAV -> /api/bodhan/transcribe -> fills the question box -> submits
 *   voice: after the answer is shown -> /api/bodhan/speak -> auto-plays
 * Exposes window.BODHAN.lang() (read by api/backend.js). Classic script, loaded before the voyage module. */
(() => {
  'use strict';
  const LS = 'bodhan.lang';
  const LANGS = { en: 'English', te: 'తెలుగు', hi: 'हिन्दी' };
  const HINT = { en: 'Ask anything about the Mahabharata…', te: 'మహాభారతం గురించి ఏదైనా అడగండి…', hi: 'महाभारत के बारे में कुछ भी पूछें…' };
  const MAX_REC_MS = 30000;
  const ENDPOINT = (u) => location.origin + u;

  let lang = 'en';
  try { const v = localStorage.getItem(LS); if (LANGS[v]) lang = v; } catch { /* storage blocked */ }
  window.BODHAN = { lang: () => lang, languages: LANGS };

  const player = new Audio();
  let speakToken = 0;

  // ------------------------------------------------------------------ speech output
  function stopSpeech() { speakToken++; player.pause(); }

  async function fetchSpeech(text, l) {
    const res = await fetch('/api/bodhan/speak', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, lang: l }) });
    if (!res.ok) throw new Error('speak failed (' + res.status + ')');
    return URL.createObjectURL(await res.blob());
  }

  function whenAnswerShown(timeoutMs) {
    return new Promise((resolve) => {
      const t0 = Date.now();
      (function poll() {
        const el = document.querySelector('#research [data-sec="answer"]:not([hidden])');
        if (el || Date.now() - t0 > timeoutMs) return resolve();
        setTimeout(poll, 250);
      })();
    });
  }

  async function speakAnswer(text, l) {
    const token = ++speakToken;
    try {
      const [url] = await Promise.all([fetchSpeech(text, l), whenAnswerShown(90000)]); // prefetch while the trace replays
      if (token !== speakToken) return URL.revokeObjectURL(url);
      player.src = url;
      await player.play();
    } catch (e) {
      console.warn('[bodhan] speech not played:', e);
      toast('Could not play the spoken answer.');
    }
  }

  // Hook /api/chart responses (the answer text is already translated into the query language by the server).
  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const res = await realFetch(input, init);
    try {
      const url = typeof input === 'string' ? input : input.url;
      if (res.ok && /\/api\/chart$/.test(url)) {
        res.clone().json().then((d) => {
          const text = (d.results || []).filter((r) => !r.tool && r.text).map((r) => r.text).join('\n\n');
          if (text) speakAnswer(text, d.lang || 'en');
        }).catch(() => {});
      }
    } catch { /* never break the app */ }
    return res;
  };

  // ------------------------------------------------------------------ voice input
  let rec = null;

  function toWav(buf) { // AudioBuffer (mono, 16 kHz) -> WAV bytes
    const pcm = buf.getChannelData(0), n = pcm.length, out = new DataView(new ArrayBuffer(44 + n * 2));
    const w = (o, s) => { for (let i = 0; i < s.length; i++) out.setUint8(o + i, s.charCodeAt(i)); };
    w(0, 'RIFF'); out.setUint32(4, 36 + n * 2, true); w(8, 'WAVEfmt '); out.setUint32(16, 16, true);
    out.setUint16(20, 1, true); out.setUint16(22, 1, true); out.setUint32(24, 16000, true);
    out.setUint32(28, 32000, true); out.setUint16(32, 2, true); out.setUint16(34, 16, true);
    w(36, 'data'); out.setUint32(40, n * 2, true);
    for (let i = 0; i < n; i++) out.setInt16(44 + i * 2, Math.max(-1, Math.min(1, pcm[i])) * 0x7fff, true);
    return new Uint8Array(out.buffer);
  }

  async function blobToWavBase64(blob) {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const decoded = await ctx.decodeAudioData(await blob.arrayBuffer());
    ctx.close();
    const off = new OfflineAudioContext(1, Math.ceil(decoded.duration * 16000), 16000);
    const src = off.createBufferSource(); src.buffer = decoded; src.connect(off.destination); src.start();
    const wav = toWav(await off.startRendering());
    let s = ''; for (let i = 0; i < wav.length; i += 0x8000) s += String.fromCharCode.apply(null, wav.subarray(i, i + 0x8000));
    return btoa(s);
  }

  async function toggleMic(btn, input, form) {
    if (rec) { rec.stop(); return; }
    stopSpeech();
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
    catch { return toast('Microphone access was denied.'); }
    const chunks = [], mr = new MediaRecorder(stream), spokenLang = lang;
    rec = mr;
    mr.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    const timer = setTimeout(() => mr.state === 'recording' && mr.stop(), MAX_REC_MS);
    mr.onstop = async () => {
      clearTimeout(timer); rec = null;
      stream.getTracks().forEach((t) => t.stop());
      btn.classList.remove('is-rec'); btn.classList.add('is-busy'); btn.setAttribute('aria-pressed', 'false');
      try {
        const audio = await blobToWavBase64(new Blob(chunks, { type: mr.mimeType }));
        const res = await realFetch('/api/bodhan/transcribe', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ audio, lang: spokenLang }) });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'transcription failed');
        if (!data.text) return toast('No speech was recognised.');
        input.value = data.text;
        form.requestSubmit();   // transcribe -> (server) translate -> LLM -> translate back -> speak
      } catch (e) { toast('Voice input failed: ' + e.message); }
      finally { btn.classList.remove('is-busy'); }
    };
    mr.start();
    btn.classList.add('is-rec'); btn.setAttribute('aria-pressed', 'true');
  }

  // ------------------------------------------------------------------ UI injection
  function toast(message) {
    const root = document.getElementById('toast-root');
    if (!root) return alert(message);
    const n = document.createElement('div'); n.className = 'bodhan-toast'; n.textContent = message;
    root.appendChild(n); setTimeout(() => n.remove(), 4000);
  }

  function inject(form) {
    if (form.querySelector('.bodhan-lang')) return;
    const row = form.querySelector('.rs-ask-row'), input = form.querySelector('.rs-ask-input');
    if (!row || !input) return;
    const sel = document.createElement('select');
    sel.className = 'bodhan-lang'; sel.setAttribute('aria-label', 'Question language');
    sel.innerHTML = Object.entries(LANGS).map(([k, v]) => `<option value="${k}">${v}</option>`).join('');
    sel.value = lang;
    const mic = document.createElement('button');
    mic.type = 'button'; mic.className = 'bodhan-mic'; mic.setAttribute('aria-pressed', 'false');
    mic.setAttribute('aria-label', 'Ask by voice'); mic.title = 'Ask by voice (click again to stop)';
    mic.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 12.5a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v4.5a3 3 0 0 0 3 3zM5 9.5a5 5 0 0 0 10 0M10 14.5V18"/></svg>';
    const apply = () => { input.placeholder = HINT[lang]; input.lang = lang; };
    sel.addEventListener('change', () => {
      lang = sel.value; try { localStorage.setItem(LS, lang); } catch { /* ignore */ }
      document.querySelectorAll('.bodhan-lang').forEach((s) => { s.value = lang; }); apply();
    });
    mic.addEventListener('click', () => {
      if (!navigator.mediaDevices || !window.MediaRecorder) return toast('Voice input is not supported in this browser.');
      toggleMic(mic, input, form);
    });
    form.addEventListener('submit', stopSpeech, true);
    row.prepend(sel); row.insertBefore(mic, row.querySelector('.rs-ask-btn')); apply();
  }

  const scan = () => document.querySelectorAll('form.rs-ask').forEach(inject);
  new MutationObserver(scan).observe(document.documentElement, { childList: true, subtree: true });
  scan();
})();
