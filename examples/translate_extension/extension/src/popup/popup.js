import { readAloud } from '../lib/player.js';

const $ = (id) => document.getElementById(id);
const form = $('form');
const input = $('input');
const target = $('target');
const output = $('output');
const note = $('note');
const go = $('go');
const listen = $('listen');
let stopReading = null;
let translatedTarget = '';

const config = await chrome.runtime.sendMessage({ type: 'get-config' });
target.replaceChildren(
  ...config.languages.map((l) => new Option(`${l.native} · ${l.name}`, l.code, false, l.code === config.target)),
);
$('mock').hidden = !config.mock;

input.addEventListener('input', () => ($('count').textContent = `${input.value.length} / 5000`));
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) form.requestSubmit();
});

$('settings').addEventListener('click', () => chrome.runtime.openOptionsPage());

$('copy').addEventListener('click', async () => {
  await navigator.clipboard.writeText(output.textContent);
  $('copy').textContent = 'Copied';
  setTimeout(() => ($('copy').textContent = 'Copy'), 1200);
});

listen.addEventListener('click', () => {
  if (stopReading) return endReading();
  stopReading = readAloud({
    text: output.textContent,
    target: translatedTarget,
    send: (message) => chrome.runtime.sendMessage(message),
    onState(state, res) {
      if (state === 'loading') listen.textContent = 'Loading…';
      else if (state === 'playing') listen.textContent = 'Stop';
      else {
        if (state === 'error') {
          note.textContent = res.error;
          note.className = 'error';
        }
        stopReading = null;
        listen.textContent = 'Listen';
      }
    },
  });
});

function endReading() {
  stopReading?.();
  stopReading = null;
  listen.textContent = 'Listen';
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!input.value.trim()) return;
  endReading();
  $('result').hidden = false;
  output.textContent = 'Translating…';
  output.className = 'loading';
  note.textContent = '';
  note.className = 'muted';
  go.disabled = true;

  const res = await chrome.runtime.sendMessage({
    type: 'translate',
    text: input.value,
    target: target.value, // for this translation only; the default is set in Settings
  });
  go.disabled = false;
  output.className = '';
  $('copy').disabled = !res.ok;
  listen.disabled = !res.ok;
  if (res.ok) {
    output.textContent = res.text;
    output.lang = res.target;
    translatedTarget = res.target;
    if (res.truncated) note.textContent = 'Part of this translation may be missing.';
  } else {
    output.textContent = '';
    note.textContent = res.error;
    note.className = 'error';
  }
});
