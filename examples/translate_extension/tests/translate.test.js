import assert from 'node:assert/strict';
import { test } from 'node:test';
import { effectiveScript } from '../extension/src/lib/languages.js';
import { apiOriginPattern, clampMaxChars, validateApiKey, validateBaseUrl } from '../extension/src/lib/settings.js';
import { translateText } from '../extension/src/lib/translate.js';

function fakeClient({ truncateOn } = {}) {
  const calls = [];
  return {
    calls,
    async translate(args) {
      calls.push(args);
      return { text: args.text.toUpperCase(), truncated: args.text === truncateOn };
    },
  };
}

test('translates each chunk and keeps paragraph structure', async () => {
  const client = fakeClient();
  const result = await translateText(client, {
    text: 'one. two.\n\nthree.',
    target: 'hi',
    script: 'roman',
    maxChars: 6,
  });
  assert.deepEqual(
    client.calls.map((c) => c.text),
    ['one.', 'two.', 'three.'],
  );
  assert.ok(client.calls.every((c) => c.target === 'hi' && c.script === 'roman'));
  assert.deepEqual(result, { text: 'ONE. TWO.\n\nTHREE.', truncated: false });
});

test('reports truncation if any chunk was truncated', async () => {
  const client = fakeClient({ truncateOn: 'b.' });
  const result = await translateText(client, { text: 'a. b. c.', target: 'hi', maxChars: 2 });
  assert.equal(result.truncated, true);
});

test('effectiveScript respects what the API allows', () => {
  assert.equal(effectiveScript('hi', 'roman'), 'roman');
  assert.equal(effectiveScript('hi', 'codemix'), 'codemix');
  assert.equal(effectiveScript('en', 'roman'), 'native');
  assert.equal(effectiveScript('ks-Deva', 'codemix'), 'native');
  assert.equal(effectiveScript('sd-Arab', 'codemix'), 'native');
  assert.equal(effectiveScript('hi', 'bogus'), 'native');
});

test('apiOriginPattern drops path and port', () => {
  assert.equal(apiOriginPattern('https://api.bodhan.ai/v1/x'), 'https://api.bodhan.ai/*');
  assert.equal(apiOriginPattern('http://localhost:8000/api'), 'http://localhost/*');
});

test('validateBaseUrl allows https and local http only', () => {
  assert.equal(validateBaseUrl('https://api.bodhan.ai'), null);
  assert.equal(validateBaseUrl('http://localhost:8000'), null);
  assert.match(validateBaseUrl('http://api.example.com'), /https/);
  assert.match(validateBaseUrl('not a url'), /full URL/);
});

test('validateApiKey expects an sk- key', () => {
  assert.equal(validateApiKey('sk-abc'), null);
  assert.match(validateApiKey(''), /Enter/);
  assert.match(validateApiKey('abc'), /sk-/);
});

test('clampMaxChars keeps chunk sizes between 500 and 5,000', () => {
  assert.equal(clampMaxChars('1000'), 1000);
  assert.equal(clampMaxChars(100), 500); // a value saved under the old 100 minimum is raised
  assert.equal(clampMaxChars('9000'), 5000);
  assert.equal(clampMaxChars(''), 1000); // unusable input falls back to the default
  assert.equal(clampMaxChars('abc'), 1000);
});
