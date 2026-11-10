// Live check against the real Bodhan API, using the extension's own client
// (extension/src/lib/bodhan.js). The extension itself never reads .env: in the
// browser the key is entered in Settings. This script is for checking keys and
// the API from a terminal before loading the extension.
//
//   npm run check:live
//
// Reads BODHAN_TRANSLATE_API_KEY and BODHAN_SPEAK_API_KEY from .env (Bodhan keys currently
// work with one model each; BODHAN_API_KEY is a fallback for future multi-model keys),
// translates one sentence into three languages, and saves one speech clip to
// outputs/live_check.wav. Uses a few API calls of credit.

import { existsSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createClient } from '../extension/src/lib/bodhan.js';

const envFile = fileURLToPath(new URL('../.env', import.meta.url));
if (existsSync(envFile)) process.loadEnvFile(envFile);

const shared = process.env.BODHAN_API_KEY?.trim();
const translateKey = process.env.BODHAN_TRANSLATE_API_KEY?.trim() || shared;
const speakKey = process.env.BODHAN_SPEAK_API_KEY?.trim() || shared;
const baseUrl = process.env.BODHAN_BASE_URL?.trim() || undefined;

if (!translateKey) {
  console.error('No key: copy .env.example to .env and set BODHAN_TRANSLATE_API_KEY.');
  process.exit(1);
}

let failed = false;
const sentence = 'The meeting has been postponed.';
const translator = createClient({ baseUrl, apiKey: translateKey });
for (const [target, script] of [['hi', 'native'], ['ta', 'roman'], ['bn', 'native']]) {
  try {
    const { text, truncated } = await translator.translate({ text: sentence, target, script });
    console.log(`translate → ${target} (${script}): ${text}${truncated ? '  [truncated]' : ''}`);
  } catch (err) {
    failed = true;
    console.error(`translate → ${target} failed: ${err.message}`);
  }
}

if (speakKey) {
  try {
    const wav = await createClient({ baseUrl, apiKey: speakKey }).speak({ text: 'बैठक स्थगित कर दी गई है।', lang: 'hi', voice: 'Kavya' });
    const out = fileURLToPath(new URL('../outputs/live_check.wav', import.meta.url));
    writeFileSync(out, Buffer.from(wav));
    console.log(`speak (hi, Kavya): ${wav.byteLength} bytes → outputs/live_check.wav`);
  } catch (err) {
    failed = true;
    console.error(`speak failed: ${err.message}`);
  }
} else {
  console.log('speak: skipped (no BODHAN_SPEAK_API_KEY)');
}

process.exit(failed ? 1 : 0);
