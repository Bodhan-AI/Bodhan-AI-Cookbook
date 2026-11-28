// Service worker: the only place that talks to the Bodhan API, so the API key
// never reaches web pages. The popup and content script send it messages.

import { bufferToBase64 } from './lib/base64.js';
import { BodhanError, createClient } from './lib/bodhan.js';
import { LANGUAGES, effectiveScript, isLanguageCode, pickVoice, speechLang } from './lib/languages.js';
import { apiOriginPattern, getSettings } from './lib/settings.js';
import { speechSegments } from './lib/text.js';
import { translateText } from './lib/translate.js';

const MENU_ID = 'bodhan-translate';
const MAX_INPUT_CHARS = 5000;
const CACHE_LIMIT = 100;
const AUDIO_CACHE_LIMIT = 20;
const cache = new Map();
const audioCache = new Map();

chrome.runtime.onInstalled.addListener(async ({ reason }) => {
  await chrome.contextMenus.removeAll();
  chrome.contextMenus.create({ id: MENU_ID, title: 'Translate with Bodhan', contexts: ['selection'] });
  if (reason === 'install') chrome.runtime.openOptionsPage();
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== MENU_ID || !tab?.id) return;
  const message = { type: 'show-translation', text: info.selectionText };
  const target = { frameId: info.frameId ?? 0 };
  try {
    await chrome.tabs.sendMessage(tab.id, message, target);
  } catch {
    // The tab was open before the extension was installed, so it has no content script yet.
    try {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id, frameIds: [target.frameId] },
        files: ['src/content/content.js'],
      });
      await chrome.tabs.sendMessage(tab.id, message, target);
    } catch (err) {
      console.warn('Bodhan Translate cannot run on this page:', err);
    }
  }
});

const handlers = {
  // There is no source language: the Bodhan model infers it from the text. `target` is for this
  // request only; the default language changes only in Settings.
  async translate({ text, target }) {
    const settings = await getSettings();
    text = String(text ?? '').trim();
    target ??= settings.target;

    if (!text) throw new BodhanError('Nothing to translate.', { code: 'empty' });
    if (text.length > MAX_INPUT_CHARS) {
      throw new BodhanError(
        `That's ${text.length.toLocaleString()} characters. Select at most ${MAX_INPUT_CHARS.toLocaleString()}.`,
        { code: 'too_long' },
      );
    }
    if (!isLanguageCode(target)) throw new BodhanError('Unsupported language.', { code: 'bad_language' });
    if (!settings.mock) await assertHostPermission(settings.baseUrl);

    const script = effectiveScript(target, settings.script);
    const key = `${settings.mock}|${target}|${script}|${text}`;
    let result = cache.get(key);
    if (!result) {
      result = await translateText(createClient(settings), { text, target, script, maxChars: settings.maxChars });
      cacheResult(cache, CACHE_LIMIT, key, result);
    }
    return { ...result, target, script, mock: settings.mock };
  },

  // Returns segment `index` of `text` as base64 WAV, plus how many segments there are.
  async speak({ text, target, index = 0 }) {
    const settings = await getSettings();
    text = String(text ?? '').trim();
    if (!text) throw new BodhanError('Nothing to read aloud.', { code: 'empty' });
    if (text.length > MAX_INPUT_CHARS) throw new BodhanError('That text is too long to read aloud.', { code: 'too_long' });
    if (!isLanguageCode(target)) throw new BodhanError('Unsupported language.', { code: 'bad_language' });

    // indic-speak wants a sentence or two per request; the page plays one segment while fetching the next.
    const segments = speechSegments(text);
    const segment = segments.at(index);
    if (segment === undefined) throw new BodhanError('Nothing left to read.', { code: 'empty' });
    if (!settings.mock) await assertHostPermission(settings.baseUrl);

    const lang = speechLang(target);
    const voice = pickVoice(lang, settings.voiceGender);
    const key = `${settings.mock}|${voice}|${lang}|${segment}`;
    let audio = audioCache.get(key);
    if (!audio) {
      // Bodhan keys currently work with one model each, so speech uses its own key. Falling back to
      // the translation key keeps working if Bodhan later issues keys for several models; today it
      // gets a 403, shown as "add a key for indic-speak in Settings".
      const client = createClient({ ...settings, apiKey: settings.speechKey || settings.apiKey });
      audio = bufferToBase64(await client.speak({ text: segment, lang, voice }));
      cacheResult(audioCache, AUDIO_CACHE_LIMIT, key, audio);
    }
    return { audio, index, count: segments.length };
  },

  async 'get-config'() {
    const { target, mock, showBubble } = await getSettings();
    return { languages: LANGUAGES, target, mock, showBubble };
  },

  async 'open-options'() {
    await chrome.runtime.openOptionsPage();
    return {};
  },
};

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const handler = handlers[message?.type];
  if (!handler) return false;
  handler(message).then(
    (result) => sendResponse({ ok: true, ...result }),
    (err) => sendResponse({ ok: false, error: err.message, code: err.code ?? 'unknown' }),
  );
  return true; // keep the channel open for the async response
});

async function assertHostPermission(baseUrl) {
  let granted = false;
  try {
    granted = await chrome.permissions.contains({ origins: [apiOriginPattern(baseUrl)] });
  } catch {
    // Invalid or empty base URL: fall through to the error below.
  }
  if (!granted) {
    throw new BodhanError('Open Settings and save your API URL to grant access to the Bodhan API.', {
      code: 'not_configured',
    });
  }
}

function cacheResult(map, limit, key, value) {
  if (map.size >= limit) map.delete(map.keys().next().value);
  map.set(key, value);
}
