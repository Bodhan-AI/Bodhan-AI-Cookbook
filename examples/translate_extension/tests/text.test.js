import assert from 'node:assert/strict';
import { test } from 'node:test';
import { chunkText, joinChunks } from '../extension/src/lib/text.js';

test('short text is a single chunk', () => {
  assert.deepEqual(chunkText('Hello world.', 100), [['Hello world.']]);
});

test('paragraphs are kept separate', () => {
  assert.deepEqual(chunkText('One.\n\nTwo.\r\n\r\nThree.', 100), [['One.'], ['Two.'], ['Three.']]);
});

test('long paragraphs split on sentence boundaries, including the danda', () => {
  const chunks = chunkText('यह पहला वाक्य है। यह दूसरा वाक्य है। Third one.', 20);
  assert.deepEqual(chunks, [['यह पहला वाक्य है।', 'यह दूसरा वाक्य है।', 'Third one.']]);
});

test('sentences are packed together up to the limit', () => {
  assert.deepEqual(chunkText('A b. C d. E f.', 9), [['A b. C d.', 'E f.']]);
});

test('a sentence longer than the limit is split on spaces', () => {
  const chunks = chunkText('aaaa bbbb cccc dddd', 10).flat();
  assert.deepEqual(chunks, ['aaaa bbbb', 'cccc dddd']);
  assert.ok(chunks.every((c) => c.length <= 10));
});

test('a word longer than the limit is hard-split', () => {
  assert.deepEqual(chunkText('abcdefghij', 4).flat(), ['abcd', 'efgh', 'ij']);
});

test('no chunk ever exceeds maxChars', () => {
  const text = 'Lorem ipsum dolor sit amet. '.repeat(200);
  for (const chunk of chunkText(text, 120).flat()) assert.ok(chunk.length <= 120);
});

test('empty and whitespace-only text produce no chunks', () => {
  assert.deepEqual(chunkText('   \n\n  ', 10), []);
});

test('rejects an invalid limit', () => {
  assert.throws(() => chunkText('x', 0), RangeError);
});

test('joinChunks restores paragraph breaks', () => {
  assert.equal(joinChunks([['a', 'b'], ['c']]), 'a b\n\nc');
});

test('speechSegments keeps the first segment short and the rest up to 200 characters', async () => {
  const { speechSegments } = await import('../extension/src/lib/text.js');
  const sentence = 'This sentence is about forty-five characters.';
  const segments = speechSegments(Array(8).fill(sentence).join(' '));
  assert.ok(segments[0].length <= 100);
  assert.ok(segments.slice(1).every((s) => s.length <= 200));
  assert.equal(segments.join(' '), Array(8).fill(sentence).join(' '));
  assert.deepEqual(speechSegments('   '), []);
});
