// Bodhan API client. Every network request the extension makes goes through here,
// so wiring a new Bodhan API (OCR, Transcribe) means adding a method below.
// Reference: https://console.bodhan.ai/api-docs/
//
// Translation tries the documented POST /translate first. If it returns 404, the
// documented equivalent, chat completions, is used until the service worker restarts.
//
// Bodhan keys are scoped to models: a key made for indic-translate is refused
// (403 key_model_access_denied) by indic-speak, and the other way round.

export const DEFAULT_BASE_URL = 'https://api.bodhan.ai';
export const TRANSLATE_PATH = '/translate';
export const CHAT_PATH = '/v1/chat/completions';
export const SPEECH_PATH = '/v1/audio/speech';
export const TRANSLATE_MODEL = 'indic-translate';
export const SPEECH_MODEL = 'indic-speak';

export class BodhanError extends Error {
  constructor(message, { code = 'unknown', status, requestId } = {}) {
    super(message);
    this.name = 'BodhanError';
    this.code = code;
    this.status = status;
    this.requestId = requestId; // Bodhan's id for the failed request, for support questions
  }
}

// Base URLs whose /translate route answered 404. A client is made per request,
// so this lives at module level: one wasted call per service-worker lifetime.
const translateRouteMissing = new Set();

/** For tests: forget which base URLs lacked /translate. */
export function forgetMissingRoutes() {
  translateRouteMissing.clear();
}

export function createClient({
  baseUrl = DEFAULT_BASE_URL,
  apiKey,
  mock = false,
  fetchImpl = (...args) => fetch(...args),
}) {
  const base = normalizeBaseUrl(baseUrl);

  async function post(path, body, { model = body.model, allow404 = false } = {}) {
    if (!apiKey) {
      throw new BodhanError('Add your Bodhan API key in Settings, or turn on mock mode.', { code: 'not_configured' });
    }
    let res;
    try {
      res = await fetchImpl(base + path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${apiKey}` },
        body: JSON.stringify(body),
      });
    } catch (err) {
      throw new BodhanError(`Could not reach the Bodhan API (${err.message}).`, { code: 'network' });
    }
    if (allow404 && res.status === 404) return res;
    if (!res.ok) throw httpError(res, await res.json().catch(() => null), model);
    return res;
  }

  /**
   * The API has no source-language field: the model infers the source itself.
   * script is "native" | "roman" | "codemix"; it is not sent for English targets.
   */
  async function translate({ text, target, script = 'native' }) {
    if (mock) return mockTranslate({ text, target });
    if (!translateRouteMissing.has(base)) {
      const body = { text, target_language: target };
      if (target !== 'en') body.script = script;
      const res = await post(TRANSLATE_PATH, body, { model: TRANSLATE_MODEL, allow404: true });
      if (res.status !== 404) return parseTranslateResponse(await res.json().catch(() => null));
      translateRouteMissing.add(base);
    }
    const body = {
      model: TRANSLATE_MODEL,
      messages: [{ role: 'user', content: text }],
      target_language_code: target,
    };
    if (target !== 'en') body.target_script = script;
    const res = await post(CHAT_PATH, body);
    return parseTranslateResponse(await res.json().catch(() => null));
  }

  /**
   * Reads text aloud. Returns WAV bytes (PCM16, 24 kHz, mono) as an ArrayBuffer.
   * Keep each call to a sentence or two: the API does not split long text.
   */
  async function speak({ text, lang, voice }) {
    if (mock) return mockSpeak(text);
    const res = await post(SPEECH_PATH, {
      model: SPEECH_MODEL,
      input: text,
      voice,
      instructions: JSON.stringify({ lang }),
    });
    return res.arrayBuffer();
  }

  return { translate, speak };
}

/** Accepts both the /translate shape ({ translation, truncated }) and the chat-completions shape. */
export function parseTranslateResponse(body) {
  let text;
  let truncated;
  if (Array.isArray(body?.choices)) {
    const choice = body.choices[0];
    text = choice?.message?.content;
    // "length" means the output hit the token budget. Truncated text reads fluently, so flag it.
    truncated = choice?.finish_reason === 'length';
  } else {
    text = body?.translation;
    truncated = Boolean(body?.truncated);
  }
  if (typeof text !== 'string') {
    throw new BodhanError('Unexpected response from the Bodhan API.', { code: 'bad_response' });
  }
  return { text, truncated };
}

function httpError(res, body, model) {
  // Error envelope: { error: { message, code, request_id } } (some routes send `type` instead of `code`).
  const { status } = res;
  const err = body?.error;
  const message = err?.message ?? body?.detail;
  const detail = typeof message === 'string' ? `: ${message}` : '';
  const requestId = pickRequestId(err?.request_id, res);
  const make = (text, code) =>
    new BodhanError(requestId ? `${text} (Bodhan request id: ${requestId})` : text, { code, status, requestId });

  if (status === 403 && (err?.type === 'key_model_access_denied' || err?.code === 'key_model_access_denied')) {
    return make(`This API key isn't allowed to use ${model}. Add a key for it in Settings.`, 'model_denied');
  }
  if (status === 401 || status === 403) {
    // The server's auth message exposes gateway internals, so it isn't shown to users.
    return make('The Bodhan API rejected the API key. Check it in Settings.', 'auth');
  }
  if (status === 413) {
    return make('That text is too long for one request. Lower "Max characters per request" in Settings.', 'too_long');
  }
  if (status === 429) {
    // Retries count against the limit, so the extension never retries on its own: it says when to try again.
    const wait = retryAfterSeconds(header(res, 'retry-after'));
    return make(wait ? `Rate limit reached. Try again in ${formatWait(wait)}.` : 'Rate limit reached. Wait a moment and try again.', 'rate_limited');
  }
  if ((status === 400 || status === 422) && typeof message === 'string') {
    return make(message, 'bad_request');
  }
  return make(`Bodhan API error ${status}${detail}`, 'http');
}

function header(res, name) {
  return typeof res.headers?.get === 'function' ? res.headers.get(name) : null;
}

/** Bodhan's request id: from the error body, else a response header. Only plain ids are shown. */
function pickRequestId(fromBody, res) {
  const id = fromBody ?? header(res, 'x-request-id') ?? header(res, 'llm_provider-x-request-id');
  return typeof id === 'string' && /^[\w.:-]{1,100}$/.test(id) ? id : undefined;
}

/** Seconds to wait from a Retry-After header (delay in seconds, or an HTTP date); null if absent or unusable. */
export function retryAfterSeconds(value, now = Date.now()) {
  if (value == null || value === '') return null;
  const text = String(value).trim();
  if (/^\d+$/.test(text)) return Number(text) || null;
  const at = Date.parse(text);
  if (Number.isNaN(at)) return null;
  const seconds = Math.ceil((at - now) / 1000);
  return seconds > 0 ? seconds : null;
}

function formatWait(seconds) {
  if (seconds < 90) return `${seconds} second${seconds === 1 ? '' : 's'}`;
  const minutes = Math.ceil(seconds / 60);
  return `about ${minutes} minute${minutes === 1 ? '' : 's'}`;
}

/**
 * Reduces a pasted endpoint URL to the API base, e.g. "https://api.bodhan.ai/translate"
 * or ".../v1/chat/completions" becomes "https://api.bodhan.ai". Empty means the default.
 */
export function normalizeBaseUrl(value) {
  const base = String(value ?? '').trim() || DEFAULT_BASE_URL;
  return base
    .replace(/\/+$/, '')
    .replace(/(?:\/v1)?(?:\/chat\/completions|\/translate|\/audio\/speech)?$/, '')
    .replace(/\/+$/, '');
}

async function mockTranslate({ text, target }) {
  await new Promise((resolve) => setTimeout(resolve, 250));
  return { text: `[${target}] ${text}`, truncated: false };
}

// A short quiet beep per segment, so the Listen button can be tried without credits.
async function mockSpeak(text) {
  await new Promise((resolve) => setTimeout(resolve, 250));
  const rate = 24000;
  const seconds = Math.min(2, 0.2 + text.length * 0.01);
  const samples = new Int16Array(Math.round(rate * seconds));
  for (let i = 0; i < samples.length; i++) samples[i] = Math.sin((2 * Math.PI * 440 * i) / rate) * 3000;
  return pcm16ToWav(samples, rate);
}

export function pcm16ToWav(samples, rate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const ascii = (offset, s) => [...s].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  ascii(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  ascii(8, 'WAVE');
  ascii(12, 'fmt ');
  view.setUint32(16, 16, true); // fmt chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  ascii(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  new Int16Array(buffer, 44).set(samples);
  return buffer;
}
