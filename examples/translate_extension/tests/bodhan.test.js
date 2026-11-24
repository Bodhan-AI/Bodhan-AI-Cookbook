// The extension's Bodhan client (extension/src/lib/bodhan.js), with fetch replaced by a fake.
import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';
import {
  BodhanError,
  CHAT_PATH,
  DEFAULT_BASE_URL,
  TRANSLATE_PATH,
  createClient,
  forgetMissingRoutes,
  normalizeBaseUrl,
  parseTranslateResponse,
  retryAfterSeconds,
} from '../extension/src/lib/bodhan.js';

/** Each call takes the next [status, body] from `replies` (the last one repeats). */
function fakeFetch(...replies) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    const [status, body] = replies[Math.min(calls.length, replies.length - 1)];
    calls.push({ url: String(url), init, body: JSON.parse(init.body) });
    return { ok: status >= 200 && status < 300, status, json: async () => body };
  };
  return { calls, fetchImpl };
}

// Shape of a real indic-translate chat response (trimmed).
const chatResponse = (content, finish_reason = 'stop') => ({
  object: 'chat.completion',
  model: 'indic-translate',
  choices: [{ index: 0, finish_reason, message: { role: 'assistant', content } }],
  usage: { prompt_tokens: 12, completion_tokens: 9, total_tokens: 21 },
});
const NOT_FOUND = [404, { detail: 'Not Found' }];

const config = { baseUrl: 'https://api.bodhan.ai/', apiKey: 'sk-test' };

beforeEach(() => forgetMissingRoutes());

test('translate calls POST /translate with target language and script', async () => {
  const { calls, fetchImpl } = fakeFetch([200, { translation: 'ಸಭೆಯನ್ನು ಮುಂದೂಡಲಾಗಿದೆ.' }]);
  const result = await createClient({ ...config, fetchImpl }).translate({
    text: 'The meeting has been postponed.',
    target: 'kn',
    script: 'roman',
  });
  assert.deepEqual(result, { text: 'ಸಭೆಯನ್ನು ಮುಂದೂಡಲಾಗಿದೆ.', truncated: false });
  assert.equal(calls[0].url, `https://api.bodhan.ai${TRANSLATE_PATH}`);
  assert.equal(calls[0].init.headers.Authorization, 'Bearer sk-test');
  assert.deepEqual(calls[0].body, { text: 'The meeting has been postponed.', target_language: 'kn', script: 'roman' });
});

test('after a 404 on /translate it sends an indic-translate chat request', async () => {
  const { calls, fetchImpl } = fakeFetch(NOT_FOUND, [200, chatResponse('ಸಭೆಯನ್ನು ಮುಂದೂಡಲಾಗಿದೆ.')]);
  const result = await createClient({ ...config, fetchImpl }).translate({
    text: 'The meeting has been postponed.',
    target: 'kn',
    script: 'roman',
  });
  assert.deepEqual(result, { text: 'ಸಭೆಯನ್ನು ಮುಂದೂಡಲಾಗಿದೆ.', truncated: false });
  assert.equal(calls[1].url, `https://api.bodhan.ai${CHAT_PATH}`);
  assert.deepEqual(calls[1].body, {
    model: 'indic-translate',
    messages: [{ role: 'user', content: 'The meeting has been postponed.' }],
    target_language_code: 'kn',
    target_script: 'roman',
  });
});

test('the 404 is remembered across clients, so later translations go straight to chat', async () => {
  const { calls, fetchImpl } = fakeFetch(NOT_FOUND, [200, chatResponse('x')]);
  await createClient({ ...config, fetchImpl }).translate({ text: 'a', target: 'hi' });
  await createClient({ ...config, fetchImpl }).translate({ text: 'b', target: 'hi' });
  assert.deepEqual(
    calls.map((c) => new URL(c.url).pathname),
    [TRANSLATE_PATH, CHAT_PATH, CHAT_PATH],
  );
});

test('does not send a script for English targets, on either route', async () => {
  const { calls, fetchImpl } = fakeFetch(NOT_FOUND, [200, chatResponse('Hello')]);
  await createClient({ ...config, fetchImpl }).translate({ text: 'नमस्ते', target: 'en', script: 'roman' });
  assert.equal('script' in calls[0].body, false);
  assert.equal('target_script' in calls[1].body, false);
});

test('falls back to the default base URL', async () => {
  const { calls, fetchImpl } = fakeFetch([200, { translation: 'x' }]);
  await createClient({ baseUrl: '', apiKey: 'sk-test', fetchImpl }).translate({ text: 'x', target: 'hi' });
  assert.equal(calls[0].url, DEFAULT_BASE_URL + TRANSLATE_PATH);
});

test('normalizeBaseUrl trims pasted endpoint paths', () => {
  for (const pasted of [
    'https://api.bodhan.ai',
    'https://api.bodhan.ai/',
    'https://api.bodhan.ai/translate',
    'https://api.bodhan.ai/v1',
    'https://api.bodhan.ai/v1/',
    'https://api.bodhan.ai/v1/chat/completions',
    '  https://api.bodhan.ai/v1/translate  ',
  ]) {
    assert.equal(normalizeBaseUrl(pasted), 'https://api.bodhan.ai', pasted);
  }
  assert.equal(normalizeBaseUrl(''), DEFAULT_BASE_URL);
  assert.equal(normalizeBaseUrl('https://proxy.example.com/bodhan'), 'https://proxy.example.com/bodhan');
});

test('a pasted endpoint URL still calls the right routes', async () => {
  const { calls, fetchImpl } = fakeFetch(NOT_FOUND, [200, chatResponse('x')]);
  await createClient({ baseUrl: 'https://api.bodhan.ai/v1/chat/completions', apiKey: 'sk-test', fetchImpl }).translate({
    text: 'x',
    target: 'hi',
  });
  assert.equal(calls[0].url, 'https://api.bodhan.ai/translate');
  assert.equal(calls[1].url, 'https://api.bodhan.ai/v1/chat/completions');
});

test('mock mode never calls fetch', async () => {
  const { calls, fetchImpl } = fakeFetch([500, null]);
  const result = await createClient({ mock: true, fetchImpl }).translate({ text: 'Hi', target: 'ta' });
  assert.equal(result.text, '[ta] Hi');
  assert.equal(calls.length, 0);
});

test('missing key is a not_configured error', async () => {
  await assert.rejects(
    createClient({ apiKey: '' }).translate({ text: 'x', target: 'hi' }),
    (err) => err instanceof BodhanError && err.code === 'not_configured',
  );
});

for (const [status, code] of [[401, 'auth'], [413, 'too_long'], [429, 'rate_limited'], [500, 'http']]) {
  test(`HTTP ${status} maps to "${code}" without retrying`, async () => {
    const { calls, fetchImpl } = fakeFetch([status, { error: { message: 'nope', type: 'x', code: String(status) } }]);
    await assert.rejects(
      createClient({ ...config, fetchImpl }).translate({ text: 'x', target: 'hi' }),
      (err) => err.code === code && err.status === status,
    );
    assert.equal(calls.length, 1);
  });
}

test('a key scoped to another model names indic-translate, on the /translate route too', async () => {
  const { fetchImpl } = fakeFetch([403, { error: { message: 'no', type: 'key_model_access_denied' } }]);
  await assert.rejects(
    createClient({ ...config, fetchImpl }).translate({ text: 'x', target: 'hi' }),
    (err) => err.code === 'model_denied' && err.message.includes('indic-translate'),
  );
});

test('400 errors surface the API message', async () => {
  const message = "'bho' (Bhojpuri) is not supported";
  const { fetchImpl } = fakeFetch([400, { error: { message, type: 'invalid_request_error', code: '400' } }]);
  await assert.rejects(
    createClient({ ...config, fetchImpl }).translate({ text: 'x', target: 'bho' }),
    (err) => err.code === 'bad_request' && err.message === message,
  );
});

test('network failure is a network error', async () => {
  const fetchImpl = async () => {
    throw new TypeError('Failed to fetch');
  };
  await assert.rejects(
    createClient({ ...config, fetchImpl }).translate({ text: 'x', target: 'hi' }),
    (err) => err.code === 'network',
  );
});

test('truncation is reported from either response shape', () => {
  assert.deepEqual(parseTranslateResponse(chatResponse('partial', 'length')), { text: 'partial', truncated: true });
  assert.deepEqual(parseTranslateResponse({ translation: 'part', truncated: true }), { text: 'part', truncated: true });
});

test('rejects a response without text', () => {
  assert.throws(() => parseTranslateResponse({ choices: [] }), (err) => err.code === 'bad_response');
  assert.throws(() => parseTranslateResponse(null), (err) => err.code === 'bad_response');
});

// ---- rate limits and request ids ----

/** A fake fetch whose single reply also carries response headers. */
function fakeFetchWithHeaders(status, body, headers = {}) {
  const lower = Object.fromEntries(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]));
  return async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    headers: { get: (name) => lower[name.toLowerCase()] ?? null },
  });
}

test('a 429 with Retry-After says when to try again, and is not retried', async () => {
  let calls = 0;
  const reply = fakeFetchWithHeaders(429, { error: { message: 'slow down', code: 'rate_limit_exceeded_error' } }, { 'Retry-After': '20' });
  const fetchImpl = async (...args) => {
    calls++;
    return reply(...args);
  };
  await assert.rejects(
    createClient({ ...config, fetchImpl }).translate({ text: 'x', target: 'hi' }),
    (err) => err.code === 'rate_limited' && err.message.startsWith('Rate limit reached. Try again in 20 seconds.'),
  );
  assert.equal(calls, 1);
});

test('a 429 without Retry-After keeps the general message', async () => {
  const fetchImpl = fakeFetchWithHeaders(429, { error: { message: 'slow down' } });
  await assert.rejects(
    createClient({ ...config, fetchImpl }).translate({ text: 'x', target: 'hi' }),
    (err) => err.message === 'Rate limit reached. Wait a moment and try again.',
  );
});

test('retryAfterSeconds reads seconds or an HTTP date, and ignores anything else', () => {
  const now = Date.parse('2026-11-10T10:00:00Z');
  assert.equal(retryAfterSeconds('15', now), 15);
  assert.equal(retryAfterSeconds('Tue, 10 Nov 2026 10:02:00 GMT', now), 120);
  assert.equal(retryAfterSeconds('Tue, 10 Nov 2026 09:59:00 GMT', now), null); // already past
  assert.equal(retryAfterSeconds('0', now), null);
  assert.equal(retryAfterSeconds('soon', now), null);
  assert.equal(retryAfterSeconds(null, now), null);
});

test('long waits are shown in minutes', async () => {
  const fetchImpl = fakeFetchWithHeaders(429, null, { 'Retry-After': '600' });
  await assert.rejects(
    createClient({ ...config, fetchImpl }).translate({ text: 'x', target: 'hi' }),
    (err) => err.message.startsWith('Rate limit reached. Try again in about 10 minutes.'),
  );
});

test("an error shows Bodhan's request id from the error body", async () => {
  const fetchImpl = fakeFetchWithHeaders(500, { error: { message: 'boom', code: 'internal_error', request_id: 'req_abc-123' } });
  await assert.rejects(
    createClient({ ...config, fetchImpl }).translate({ text: 'x', target: 'hi' }),
    (err) => err.requestId === 'req_abc-123' && err.message.endsWith('(Bodhan request id: req_abc-123)'),
  );
});

test('the request id can also come from the x-request-id header', async () => {
  const fetchImpl = fakeFetchWithHeaders(401, { error: { message: 'internal gateway detail' } }, { 'x-request-id': '20261110_25de2f1a' });
  await assert.rejects(
    createClient({ ...config, fetchImpl }).translate({ text: 'x', target: 'hi' }),
    (err) =>
      err.code === 'auth' &&
      err.message === 'The Bodhan API rejected the API key. Check it in Settings. (Bodhan request id: 20261110_25de2f1a)' &&
      !err.message.includes('gateway detail'),
  );
});

test('a request id that is not a plain id is not shown', async () => {
  const fetchImpl = fakeFetchWithHeaders(500, { error: { message: 'x', request_id: '<script>alert(1)</script>' } });
  await assert.rejects(
    createClient({ ...config, fetchImpl }).translate({ text: 'x', target: 'hi' }),
    (err) => err.requestId === undefined && !err.message.includes('request id'),
  );
});

test('key_model_access_denied is recognised in error.code as well as error.type', async () => {
  const fetchImpl = fakeFetchWithHeaders(403, { error: { message: 'no', code: 'key_model_access_denied' } });
  await assert.rejects(
    createClient({ ...config, fetchImpl }).speak({ text: 'x', lang: 'hi', voice: 'Kavya' }),
    (err) => err.code === 'model_denied' && err.message.includes('indic-speak'),
  );
});
