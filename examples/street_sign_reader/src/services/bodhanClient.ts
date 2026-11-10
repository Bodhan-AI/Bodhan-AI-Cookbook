// File: src/services/bodhanClient.ts
import { BoundingBox } from '../models/SignboardTypes';
import {
  getOcrApiKey,
  getTranslateApiKey,
  getTransliterateApiKey,
  getTtsApiKey
} from './secureStorage';
import { SAMPLE_SIGNS } from '../assets/sampleSigns';

const BODHAN_BASE_URL = 'https://api.bodhan.ai';

export class BodhanApiError extends Error {
  statusCode?: number;
  constructor(message: string, statusCode?: number) {
    super(message);
    this.name = 'BodhanApiError';
    this.statusCode = statusCode;
  }
}

/**
 * Detects the Indic language code from Unicode character ranges in text.
 * Used for Bodhan /transliterate where 'language' is a required Indic code.
 */
export function detectIndicLanguage(text: string): string {
  if (/[\u0B00-\u0B7F]/.test(text)) return 'or'; // Odia
  if (/[\u0B80-\u0BFF]/.test(text)) return 'ta'; // Tamil
  if (/[\u0C00-\u0C7F]/.test(text)) return 'te'; // Telugu
  if (/[\u0C80-\u0CFF]/.test(text)) return 'kn'; // Kannada
  if (/[\u0D00-\u0D7F]/.test(text)) return 'ml'; // Malayalam
  if (/[\u0980-\u09FF]/.test(text)) return 'bn'; // Bengali / Assamese
  if (/[\u0A00-\u0A7F]/.test(text)) return 'pa'; // Punjabi (Gurmukhi)
  if (/[\u0A80-\u0AFF]/.test(text)) return 'gu'; // Gujarati
  if (/[\u0600-\u06FF]/.test(text)) return 'ur'; // Urdu / Perso-Arabic
  if (/[\u0900-\u097F]/.test(text)) return 'hi'; // Devanagari (Hindi, Marathi, etc.)
  return 'hi';
}

/**
 * Capitalizes voice name to match Bodhan's 45 documented voice catalog names
 */
function normalizeVoiceName(voice: string): string {
  if (!voice) return 'Kavya';
  return voice.charAt(0).toUpperCase() + voice.slice(1);
}

/**
 * 1. Document OCR API: POST https://api.bodhan.ai/v1/chat/completions
 * Body: { model: "indic-ocr", messages: [{ role: "user", content: [{ type: "image_url", image_url: { url: base64DataUrl } }] }] }
 * Response: OpenAI-style with top-level `blocks` array containing spatial bounding boxes.
 */
export async function performBodhanOcr(base64DataUrl: string): Promise<BoundingBox[]> {
  const apiKey = getOcrApiKey();

  // Offline / zero-setup simulation when no key is entered
  if (!apiKey) {
    const matchedSample = SAMPLE_SIGNS.find((s) => s.imageUrl === base64DataUrl);
    if (matchedSample) {
      await new Promise((r) => setTimeout(r, 650));
      return matchedSample.rawBlocks;
    }

    // If user cropped a sample sign or tested without an API key, use fallback signboard blocks
    if (base64DataUrl.startsWith('data:image/')) {
      await new Promise((r) => setTimeout(r, 450));
      return SAMPLE_SIGNS[0].rawBlocks;
    }

    // If user uploaded a new custom photo but has not entered an API key
    throw new BodhanApiError(
      "No Bodhan OCR API Key entered. Please go to Settings (⚙️) and enter your API key to scan live photos, or choose 'Sample Signs' to test pre-loaded signboards.",
      401
    );
  }

  // Live Bodhan Cloud API Call
  console.log(`[Bodhan OCR] Using OCR Key: ...${apiKey.slice(-4)}`);
  try {
    const res = await fetch(`${BODHAN_BASE_URL}/v1/chat/completions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${apiKey}`
      },
      body: JSON.stringify({
        model: 'indic-ocr',
        messages: [
          {
            role: 'user',
            content: [
              {
                type: 'image_url',
                image_url: { url: base64DataUrl }
              }
            ]
          }
        ]
      })
    });

    if (!res.ok) {
      const errJson = await res.json().catch(() => null);
      const errMsg =
        errJson?.error?.message ||
        errJson?.error?.code ||
        `HTTP ${res.status} ${res.statusText}`;
      if (res.status === 401) {
        throw new BodhanApiError(`Invalid Bodhan OCR Key: ${errMsg}`, 401);
      }
      if (res.status === 429) {
        throw new BodhanApiError(`Bodhan OCR Rate Limit Exceeded: ${errMsg}`, 429);
      }
      throw new BodhanApiError(`Bodhan OCR Error (${res.status}): ${errMsg}`, res.status);
    }

    const data = await res.json();

    // Check top-level blocks field as per Bodhan Document OCR specification (page 12)
    if (data.blocks && Array.isArray(data.blocks)) {
      return data.blocks.map((b: any) => ({
        bbox_xyxy: b.bbox_xyxy as [number, number, number, number],
        text: b.text || '',
        type: b.type || b.label || 'Text'
      }));
    }

    // Fallback: Parse markdown content lines if blocks array was absent
    const content = data.choices?.[0]?.message?.content || '';
    try {
      const jsonMatch = content.match(/```(?:json)?\s*([\s\S]*?)\s*```/);
      const parsed = JSON.parse(jsonMatch ? jsonMatch[1] : content);
      if (Array.isArray(parsed)) return parsed;
      if (parsed.blocks && Array.isArray(parsed.blocks)) return parsed.blocks;
    } catch {
      // Content is raw text lines
    }

    const lines = content
      .split('\n')
      .map((l: string) => l.trim())
      .filter(Boolean);
    return lines.map((line: string, i: number) => ({
      bbox_xyxy: [50, 100 + i * 90, 750, 170 + i * 90] as [number, number, number, number],
      text: line,
      type: i === 0 ? 'Title' : 'Text'
    }));
  } catch (err: any) {
    if (err instanceof BodhanApiError) throw err;
    throw new BodhanApiError(`Failed to reach Bodhan Cloud OCR: ${err.message}`);
  }
}

/**
 * 2. Transliteration API
 * Converts proper nouns / brand names phonetically into the target script.
 * Uses Bodhan indic-translate via /v1/chat/completions (active gateway route)
 * which preserves proper names phonetically while translating.
 */
export async function performBodhanTransliterate(
  text: string,
  targetLanguage: string
): Promise<string> {
  if (!text.trim()) return '';

  const apiKey = getTransliterateApiKey();

  // Offline simulation fallback
  if (!apiKey) {
    await new Promise((r) => setTimeout(r, 200));
    const matched = SAMPLE_SIGNS.find(
      (s) =>
        s.name.includes(text) ||
        text.includes(s.name) ||
        s.rawBlocks.some((b) => b.text.includes(text) || text.includes(b.text))
    );
    if (matched && matched.mockTransliteration[targetLanguage]) {
      return matched.mockTransliteration[targetLanguage];
    }
    return text;
  }

  const detectedSourceLang = detectIndicLanguage(text);

  // If already in target language, return as-is
  if (targetLanguage === detectedSourceLang) {
    return text;
  }

  // Use translation engine to adapt into target script
  return performBodhanTranslate(text, targetLanguage);
}

/**
 * 3. Translation API
 * The live Bodhan API gateway routes translation through the OpenAI-compatible endpoint:
 * POST https://api.bodhan.ai/v1/chat/completions (model: indic-translate)
 * with fallback to POST https://api.bodhan.ai/translate.
 */
export async function performBodhanTranslate(
  text: string,
  targetLanguage: string
): Promise<string> {
  if (!text.trim()) return '';

  const apiKey = getTranslateApiKey();

  // Offline simulation fallback
  if (!apiKey) {
    await new Promise((r) => setTimeout(r, 250));
    const matched = SAMPLE_SIGNS.find(
      (s) =>
        s.rawBlocks.some((b) => b.text.includes(text) || text.includes(b.text)) ||
        s.name.includes(text)
    );
    if (matched && matched.mockTranslation[targetLanguage]) {
      return matched.mockTranslation[targetLanguage];
    }
    return text;
  }

  console.log(`[Bodhan Translate] Using Translate Key: ...${apiKey.slice(-4)} -> Target: ${targetLanguage}`);

  // Primary Transport: POST /v1/chat/completions (model: indic-translate)
  try {
    const res = await fetch(`${BODHAN_BASE_URL}/v1/chat/completions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${apiKey}`
      },
      body: JSON.stringify({
        model: 'indic-translate',
        messages: [
          {
            role: 'user',
            content: text.trim()
          }
        ],
        target_language: targetLanguage,
        target_language_code: targetLanguage
      })
    });

    if (res.ok) {
      const data = await res.json();
      const translated =
        data.choices?.[0]?.message?.content ||
        data.translation ||
        data.translated_text;
      if (translated && typeof translated === 'string') {
        return translated.trim();
      }
    } else {
      console.warn(`[Bodhan /v1/chat/completions translate] returned ${res.status}, attempting /translate fallback...`);
    }
  } catch (chatErr) {
    console.warn('[Bodhan /v1/chat/completions translate] network error, trying fallback:', chatErr);
  }

  // Fallback Transport: POST /translate (Product surface)
  try {
    const body: Record<string, any> = {
      text: text.trim(),
      target_language: targetLanguage
    };
    if (targetLanguage !== 'en') {
      body.script = 'native';
    }

    const res = await fetch(`${BODHAN_BASE_URL}/translate`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${apiKey}`
      },
      body: JSON.stringify(body)
    });

    if (res.ok) {
      const data = await res.json();
      return data.translation || data.translated_text || text;
    } else {
      const errJson = await res.json().catch(() => null);
      console.warn('[Bodhan /translate] failed:', errJson?.error?.message || res.statusText);
    }
  } catch (fallbackErr) {
    console.warn('[Bodhan /translate] fallback error:', fallbackErr);
  }

  return text;
}

/**
 * 4. Text to Speech API: POST https://api.bodhan.ai/v1/audio/speech
 * Per Bodhan API reference (pages 8-11):
 * Body: { model: "indic-speak", input: string, voice: enum (45 voices), instructions: string }
 * instructions is a JSON string with lang (required) and optional style.
 * Response: binary audio/wav PCM16 at 24 kHz mono.
 */
export async function performBodhanSpeak(
  text: string,
  voice: string,
  languageCode: string
): Promise<{ audioUrl: string; durationSec: number }> {
  if (!text.trim()) {
    throw new Error('No text to synthesize');
  }

  const apiKey = getTtsApiKey();

  if (!apiKey) {
    return generateOfflineSpeechAudio(text, languageCode);
  }

  const validVoice = normalizeVoiceName(voice);
  console.log(`[Bodhan Speech] Using TTS Key: ...${apiKey.slice(-4)} -> Voice: ${validVoice}, Lang: ${languageCode}`);

  try {
    const res = await fetch(`${BODHAN_BASE_URL}/v1/audio/speech`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${apiKey}`
      },
      body: JSON.stringify({
        model: 'indic-speak',
        input: text.trim(),
        voice: validVoice,
        instructions: JSON.stringify({
          lang: languageCode
        })
      })
    });

    if (!res.ok) {
      const errJson = await res.json().catch(() => null);
      const errMsg =
        errJson?.error?.message ||
        errJson?.error?.code ||
        `HTTP ${res.status} ${res.statusText}`;

      if (res.status === 401) {
        throw new BodhanApiError(`Invalid Bodhan TTS Key (401): ${errMsg}`, 401);
      }
      throw new BodhanApiError(`Bodhan TTS Error (${res.status}): ${errMsg}`, res.status);
    }

    const blob = await res.blob();
    const audioUrl = URL.createObjectURL(blob);
    const wordCount = text.split(/\s+/).length;
    const durationSec = Math.max(2, Math.min(30, Math.round(wordCount / 2.2)));

    return { audioUrl, durationSec };
  } catch (err: any) {
    console.warn('Bodhan TTS API call error, falling back to Web Speech:', err);
    return generateOfflineSpeechAudio(text, languageCode);
  }
}

/**
 * Browser Web Audio / Web Speech fallback for offline demonstration
 */
async function generateOfflineSpeechAudio(
  text: string,
  _lang: string
): Promise<{ audioUrl: string; durationSec: number }> {
  const wordCount = text.split(/\s+/).length;
  const durationSec = Math.max(3, Math.min(25, Math.round(wordCount / 2.3)));

  const sampleRate = 24000;
  const numSamples = sampleRate * durationSec;
  const buffer = new ArrayBuffer(44 + numSamples * 2);
  const view = new DataView(buffer);

  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + numSamples * 2, true);
  writeString(view, 8, 'WAVE');
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // Mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true); // Block align
  view.setUint16(34, 16, true); // 16-bit
  writeString(view, 36, 'data');
  view.setUint32(40, numSamples * 2, true);

  for (let i = 0; i < numSamples; i++) {
    const t = i / sampleRate;
    const freq = 440 * Math.exp(-t * 0.4);
    const sample = Math.sin(2 * Math.PI * freq * t) * 0.15 * Math.exp(-t * 0.3);
    view.setInt16(44 + i * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  }

  const blob = new Blob([buffer], { type: 'audio/wav' });
  const audioUrl = URL.createObjectURL(blob);

  if ('speechSynthesis' in window) {
    const utterance = new SpeechSynthesisUtterance(text);
    // Browser Web Speech API fallback locale mapping
    const bcpLocale = (c: string) => `${c}-IN`;
    const langMap: Record<string, string> = {
      hi: bcpLocale('hi'),
      ta: bcpLocale('ta'),
      te: bcpLocale('te'),
      bn: bcpLocale('bn'),
      mr: bcpLocale('mr'),
      gu: bcpLocale('gu'),
      kn: bcpLocale('kn'),
      ml: bcpLocale('ml'),
      pa: bcpLocale('pa'),
      ur: bcpLocale('ur'),
      or: bcpLocale('or'),
      en: bcpLocale('en')
    };
    utterance.lang = langMap[_lang] || bcpLocale('hi');
    utterance.rate = 0.95;
    window.speechSynthesis.speak(utterance);
  }

  return { audioUrl, durationSec };
}

function writeString(view: DataView, offset: number, string: string) {
  for (let i = 0; i < string.length; i++) {
    view.setUint8(offset + i, string.charCodeAt(i));
  }
}
