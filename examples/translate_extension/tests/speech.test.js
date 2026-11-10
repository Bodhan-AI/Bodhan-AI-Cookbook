import assert from 'node:assert/strict';
import { test } from 'node:test';
import { base64ToBuffer, bufferToBase64 } from '../extension/src/lib/base64.js';
import { SPEECH_PATH, createClient, pcm16ToWav } from '../extension/src/lib/bodhan.js';
import { pickVoice, speechLang } from '../extension/src/lib/languages.js';

function fakeFetch(status, { json = null, audio = new ArrayBuffer(8) } = {}) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url: String(url), init });
    return { ok: status >= 200 && status < 300, status, json: async () => json, arrayBuffer: async () => audio };
  };
  return { calls, fetchImpl };
}

const ascii = (buffer, start, end) => String.fromCharCode(...new Uint8Array(buffer).slice(start, end));

test('speak sends an indic-speak request with lang inside the instructions string', async () => {
  const audio = new Uint8Array([82, 73, 70, 70]).buffer;
  const { calls, fetchImpl } = fakeFetch(200, { audio });
  const result = await createClient({ apiKey: 'sk-speak', fetchImpl }).speak({
    text: 'कक्षा नौ बजे शुरू होती है।',
    lang: 'hi',
    voice: 'Kavya',
  });

  assert.equal(result, audio);
  assert.equal(calls[0].url, `https://api.bodhan.ai${SPEECH_PATH}`);
  assert.equal(calls[0].init.headers.Authorization, 'Bearer sk-speak');
  assert.deepEqual(JSON.parse(calls[0].init.body), {
    model: 'indic-speak',
    input: 'कक्षा नौ बजे शुरू होती है।',
    voice: 'Kavya',
    instructions: '{"lang":"hi"}',
  });
});

test('a key scoped to another model gets a model_denied error naming the model', async () => {
  const { fetchImpl } = fakeFetch(403, {
    json: { error: { message: 'key not allowed to access model', type: 'key_model_access_denied', code: '403' } },
  });
  await assert.rejects(
    createClient({ apiKey: 'sk-translate-only', fetchImpl }).speak({ text: 'x', lang: 'hi', voice: 'Kavya' }),
    (err) => err.code === 'model_denied' && err.message.includes('indic-speak'),
  );
});

test('mock speak returns a playable 24 kHz mono WAV without calling fetch', async () => {
  const { calls, fetchImpl } = fakeFetch(500);
  const wav = await createClient({ mock: true, fetchImpl }).speak({ text: 'hello', lang: 'hi', voice: 'Kavya' });
  assert.equal(calls.length, 0);
  assert.equal(ascii(wav, 0, 4), 'RIFF');
  assert.equal(ascii(wav, 8, 12), 'WAVE');
  const view = new DataView(wav);
  assert.equal(view.getUint16(22, true), 1); // mono
  assert.equal(view.getUint32(24, true), 24000);
  assert.equal(view.getUint32(40, true), wav.byteLength - 44);
});

test('pcm16ToWav writes the samples after a 44-byte header', () => {
  const wav = pcm16ToWav(new Int16Array([1, -1, 300]), 24000);
  assert.equal(wav.byteLength, 44 + 6);
  assert.deepEqual([...new Int16Array(wav, 44)], [1, -1, 300]);
});

test('base64 round-trips binary audio, including large buffers', () => {
  const bytes = new Uint8Array(200_000).map((_, i) => (i * 37) % 256);
  const back = new Uint8Array(base64ToBuffer(bufferToBase64(bytes.buffer)));
  assert.deepEqual(back, bytes);
});

test('speechLang drops the script suffix', () => {
  assert.equal(speechLang('hi'), 'hi');
  assert.equal(speechLang('ks-Deva'), 'ks');
  assert.equal(speechLang('mni-Beng'), 'mni');
  assert.equal(speechLang('sd-Arab'), 'sd');
});

test('pickVoice picks by gender and falls back to Hindi voices', () => {
  assert.equal(pickVoice('ta', 'female'), 'Anitha');
  assert.equal(pickVoice('ta', 'male'), 'Arun');
  assert.equal(pickVoice('en', 'female'), 'Kavya');
  assert.equal(pickVoice('en', 'male'), 'Amit');
});
