// Settings live in chrome.storage.local (never synced) because they include the API key.

import { DEFAULT_BASE_URL, normalizeBaseUrl } from './bodhan.js';

export const DEFAULTS = Object.freeze({
  baseUrl: DEFAULT_BASE_URL,
  apiKey: '',
  speechKey: '', // the indic-speak key: Bodhan keys currently work with one model each
  voiceGender: 'female',
  target: 'hi',
  script: 'native',
  mock: false,
  showBubble: true,
  maxChars: 1000,
});

// Characters per translation request. A 500 minimum keeps long selections (up to 5,000 characters)
// to a handful of requests, well inside a key's request limit (120).
export const MIN_CHUNK_CHARS = 500;
export const MAX_CHUNK_CHARS = 5000;

/** Clamps a "max characters per request" value into range; anything unusable becomes the default. */
export function clampMaxChars(value) {
  const n = Number.parseInt(value, 10);
  if (!Number.isFinite(n)) return DEFAULTS.maxChars;
  return Math.min(MAX_CHUNK_CHARS, Math.max(MIN_CHUNK_CHARS, n));
}

export async function getSettings() {
  const stored = await chrome.storage.local.get(Object.keys(DEFAULTS));
  // An empty or pasted-endpoint URL (e.g. saved by an older version) resolves to the base URL,
  // and a chunk size saved under an older, lower minimum is raised to the current one.
  return {
    ...DEFAULTS,
    ...stored,
    baseUrl: normalizeBaseUrl(stored.baseUrl),
    maxChars: clampMaxChars(stored.maxChars ?? DEFAULTS.maxChars),
  };
}

export function saveSettings(patch) {
  return chrome.storage.local.set(patch);
}

/** Host-permission match pattern for the configured API, e.g. "https://api.example.com/*". */
export function apiOriginPattern(baseUrl) {
  const url = new URL(baseUrl);
  return `${url.protocol}//${url.hostname}/*`;
}

/** Returns an error message, or null if the key looks like a Bodhan key. */
export function validateApiKey(value) {
  if (!value) return 'Enter your Bodhan API key, or turn on mock mode.';
  if (!value.startsWith('sk-')) return 'Bodhan API keys start with "sk-". Copy it from Virtual Keys in the Bodhan dashboard.';
  return null;
}

/** Returns an error message, or null if the URL is usable as an API base URL. */
export function validateBaseUrl(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    return 'Enter a full URL, e.g. https://api.bodhan.ai';
  }
  const isLocal = url.hostname === 'localhost' || url.hostname === '127.0.0.1';
  if (url.protocol === 'https:' || (url.protocol === 'http:' && isLocal)) return null;
  return 'The API URL must use https:// (http:// is allowed only for localhost).';
}
