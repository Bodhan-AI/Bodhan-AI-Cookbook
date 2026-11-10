import { DEFAULT_BASE_URL, normalizeBaseUrl } from '../lib/bodhan.js';
import { LANGUAGES, SCRIPTS, effectiveScript } from '../lib/languages.js';
import { apiOriginPattern, clampMaxChars, getSettings, saveSettings, validateApiKey, validateBaseUrl } from '../lib/settings.js';

const $ = (id) => document.getElementById(id);
const form = $('form');
const status = $('status');
const fields = ['baseUrl', 'apiKey', 'speechKey', 'voiceGender', 'target', 'script', 'mock', 'showBubble', 'maxChars'];

$('target').replaceChildren(...LANGUAGES.map((l) => new Option(`${l.native} · ${l.name}`, l.code)));
$('script').replaceChildren(...SCRIPTS.map((s) => new Option(s.name, s.code)));

const settings = await getSettings();
for (const id of fields) {
  const el = $(id);
  if (el.type === 'checkbox') el.checked = settings[id];
  else el.value = settings[id];
}
syncFormState();
$('mock').addEventListener('change', syncFormState);
$('target').addEventListener('change', syncFormState);
$('resetUrl').addEventListener('click', () => {
  $('baseUrl').value = DEFAULT_BASE_URL;
  setStatus('API URL reset. Click Save to keep it.');
});

function syncFormState() {
  const mock = $('mock').checked;
  $('baseUrl').disabled = mock;
  $('resetUrl').disabled = mock;
  $('apiKey').disabled = mock;
  $('speechKey').disabled = mock;
  // English output has no script choice.
  $('script').disabled = $('target').value === 'en';
}

function setStatus(text, kind = '') {
  status.textContent = text;
  status.className = kind;
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const target = $('target').value;
  const next = {
    baseUrl: normalizeBaseUrl($('baseUrl').value),
    apiKey: $('apiKey').value.trim(),
    speechKey: $('speechKey').value.trim(),
    voiceGender: $('voiceGender').value,
    target,
    script: effectiveScript(target, $('script').value),
    mock: $('mock').checked,
    showBubble: $('showBubble').checked,
    maxChars: clampMaxChars($('maxChars').value),
  };

  if (!next.mock) {
    const error =
      validateBaseUrl(next.baseUrl) ??
      validateApiKey(next.apiKey) ??
      (next.speechKey ? validateApiKey(next.speechKey) : null);
    if (error) return setStatus(error, 'error');
    // permissions.request must run before any other await so it keeps the click's user gesture.
    // For the default api.bodhan.ai it is already granted by the manifest and returns at once.
    const granted = await chrome.permissions.request({ origins: [apiOriginPattern(next.baseUrl)] });
    if (!granted) return setStatus('Access to the API URL is needed to translate.', 'error');
  }

  await saveSettings(next);
  $('baseUrl').value = next.baseUrl;
  $('script').value = next.script;
  $('maxChars').value = next.maxChars;
  if (e.submitter?.value !== 'test') return setStatus('Saved.', 'ok');

  setStatus('Saved. Testing…');
  const res = await chrome.runtime.sendMessage({
    type: 'translate',
    text: target === 'en' ? 'नमस्ते, आप कैसे हैं?' : 'Hello, how are you?',
    target,
  });
  if (!res.ok) return setStatus(`Translation test failed: ${res.error}`, 'error');

  setStatus(`Translation works: “${res.text}”. Testing voice…`);
  const voice = await chrome.runtime.sendMessage({ type: 'speak', text: res.text, target, index: 0 });
  if (voice.ok) setStatus(`Translation works: “${res.text}”. Voice works too.`, 'ok');
  else setStatus(`Translation works: “${res.text}”. Voice test failed: ${voice.error}`, 'error');
});
