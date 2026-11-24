import assert from 'node:assert/strict';
import test from 'node:test';
import { TRANSCRIPTION_LANGUAGES, TRANSLATION_LANGUAGES, isTranscriptionLanguage, isTranslationLanguage } from '../packages/bhashavoice/src/languages.mjs';
import { transcribeWav } from '../src/server/bodhan/transcribe.mjs';
import { translateText } from '../src/server/bodhan/translate.mjs';
import { handleAudioUpload } from '../src/server/audio/handleAudioUpload.mjs';
import { handleTranslation } from '../src/server/bodhan/handleTranslation.mjs';

// Independent published contract, rather than deriving expected coverage from implementation.
const speech = 'as bhb bho bn brx doi en gu hi kn kok ks mai ml mni mr ne or pa sa sat sd ta te ur'.split(' ');
const translation = 'as bn brx doi en gu hi kn kok ks ks-Deva mai ml mni mni-Beng mr ne or pa sa sat sd sd-Arab ta te ur'.split(' ');
const apiKey = 'test-only';
const wav = Buffer.alloc(44 + 3200);
wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22); wav.writeUInt32LE(16000, 24); wav.writeUInt32LE(32000, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(3200, 40);

test('capability lists match every enumerated Bodhan code, including script variants', () => {
  assert.deepEqual(TRANSCRIPTION_LANGUAGES.map(x => x.code).sort(), speech.toSorted());
  assert.deepEqual(TRANSLATION_LANGUAGES.map(x => x.code).sort(), translation.toSorted());
  for (const code of ['bhb', 'bho']) { assert.ok(isTranscriptionLanguage(code)); assert.equal(isTranslationLanguage(code), false); }
  for (const code of ['ks-Deva', 'mni-Beng', 'sd-Arab']) { assert.ok(isTranslationLanguage(code)); assert.equal(isTranscriptionLanguage(code), false); }
  for (const code of ['fr', 'hi-IN', '', null, undefined, '__proto__']) { assert.equal(isTranscriptionLanguage(code), false); assert.equal(isTranslationLanguage(code), false); }
});
for (const code of speech) test(`speech upload and provider preserve ${code}`, async () => {
  const form = new FormData(); form.set('language', code); form.set('file', new Blob([wav], {type:'audio/wav'}), 'synthetic.wav');
  const response = await handleAudioUpload(new Request('http://localhost/api/bhasha/transcribe', {method:'POST', body:form}), {transcribe: (audio, language, options) => transcribeWav(audio, language, {...options, apiKey, fetchImpl: async (_url, request) => {
    assert.equal(request.body.get('language'), code);
    return Response.json({text:'Test transcript'});
  }})});
  assert.equal(response.status, 200);
  assert.equal((await response.json()).language, code);
});
for (const code of translation) test(`translation handler and provider preserve ${code}`, async () => {
  const response = await handleTranslation(new Request('http://localhost/api/bhasha/translate', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text:'Good morning.', target_language:code})}), {translate: (text, language, options) => translateText(text, language, {...options, apiKey, fetchImpl: async (_url, request) => {
    assert.equal(JSON.parse(request.body).target_language_code, code);
    return Response.json({choices:[{message:{content:'Test translation'},finish_reason:'stop'}]});
  }})});
  assert.equal(response.status, 200);
  assert.equal((await response.json()).target_language, code);
});
test('unsupported capability combinations never reach the provider', async () => {
  for (const code of ['bhb','bho','hi-IN','fr']) await assert.rejects(translateText('Hello',code,{apiKey,fetchImpl:()=>assert.fail('Paid request')}),{code:'INVALID_TARGET_LANGUAGE'});
  for (const code of ['ks-Deva','mni-Beng','sd-Arab','fr']) await assert.rejects(transcribeWav(wav,code,{apiKey,fetchImpl:()=>assert.fail('Paid request')}),{code:'INVALID_LANGUAGE'});
});
