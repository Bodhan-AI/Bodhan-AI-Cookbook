// ─────────────────────────────────────────────────────────────
// Bodhan AI — Unified API client
// Covers all 5 endpoints: STT, Translation, Transliteration,
// TTS, and Document OCR
// ─────────────────────────────────────────────────────────────

import * as FileSystem from 'expo-file-system';
import type { ScriptMode, TTSStyle } from '../constants/languages';

const BASE_URL = 'https://api.bodhan.ai';

// ── API Key Management ──────────────────────────────────────

let _apiKey: string = '';

export function setApiKey(key: string) {
  _apiKey = key;
}

export function getApiKey(): string {
  return _apiKey;
}

function authHeaders(): Record<string, string> {
  if (!_apiKey) throw new Error('Bodhan API key not set. Call setApiKey() first.');
  return { Authorization: `Bearer ${_apiKey}` };
}

// ── Error Handling ──────────────────────────────────────────

export class BodhanAPIError extends Error {
  code: string;
  status: number;
  requestId?: string;

  constructor(status: number, code: string, message: string, requestId?: string) {
    super(message);
    this.name = 'BodhanAPIError';
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let errorData: any;
    try {
      errorData = await response.json();
    } catch {
      throw new BodhanAPIError(response.status, 'unknown_error', `HTTP ${response.status}`);
    }
    const err = errorData?.error ?? errorData;
    throw new BodhanAPIError(
      response.status,
      err?.code ?? 'unknown_error',
      err?.message ?? `HTTP ${response.status}`,
      err?.request_id
    );
  }
  return response.json() as Promise<T>;
}

// ── 1. Speech to Text (STT) ────────────────────────────────

export interface STTRequest {
  /** Local URI to the audio file */
  fileUri: string;
  /** Language code (optional but strongly recommended) */
  language?: string;
}

export interface STTResponse {
  text: string;
}

/**
 * Transcribe speech to text using indic-transcribe.
 * Audio must be ≤30 seconds. WAV recommended; FLAC, OGG, MP3 also accepted.
 */
export async function speechToText(req: STTRequest): Promise<STTResponse> {
  const formData = new FormData();

  // Read the file and append as blob
  const fileInfo = await FileSystem.getInfoAsync(req.fileUri);
  if (!fileInfo.exists) throw new Error(`Audio file not found: ${req.fileUri}`);

  formData.append('file', {
    uri: req.fileUri,
    name: 'audio.wav',
    type: 'audio/wav',
  } as any);

  formData.append('model', 'indic-transcribe');

  if (req.language) {
    formData.append('language', req.language);
  }

  const response = await fetch(`${BASE_URL}/v1/audio/transcriptions`, {
    method: 'POST',
    headers: {
      ...authHeaders(),
      // Don't set Content-Type; fetch sets it with boundary for FormData
    },
    body: formData,
  });

  return handleResponse<STTResponse>(response);
}

// ── 2. Translation ──────────────────────────────────────────

export interface TranslateRequest {
  text: string;
  targetLanguage: string;
  script?: ScriptMode;
  maxOutputTokens?: number;
}

export interface TranslateResponse {
  translation: string;
  target_language: string;
  script: string;
  truncated: boolean;
  usage: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
}

/**
 * Translate text into the target language.
 * Supports 22 Indian languages + English. No source language needed.
 */
export async function translate(req: TranslateRequest): Promise<TranslateResponse> {
  const body: Record<string, any> = {
    text: req.text,
    target_language: req.targetLanguage,
  };

  if (req.script) body.script = req.script;
  if (req.maxOutputTokens) body.max_output_tokens = req.maxOutputTokens;

  const response = await fetch(`${BASE_URL}/translate`, {
    method: 'POST',
    headers: {
      ...authHeaders(),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });

  return handleResponse<TranslateResponse>(response);
}

// ── 3. Transliteration ──────────────────────────────────────

export interface TransliterateRequest {
  text: string;
  language: string;
  script?: 'roman' | 'native';
  maxOutputTokens?: number;
}

export interface TransliterateResponse {
  transliteration: string;
  language: string;
  script: string;
  truncated: boolean;
  usage?: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
}

/**
 * Rewrite text into a different script without translating.
 * Default script is 'roman' (Latin letters).
 */
export async function transliterate(req: TransliterateRequest): Promise<TransliterateResponse> {
  const body: Record<string, any> = {
    text: req.text,
    language: req.language,
  };

  if (req.script) body.script = req.script;
  if (req.maxOutputTokens) body.max_output_tokens = req.maxOutputTokens;

  const response = await fetch(`${BASE_URL}/transliterate`, {
    method: 'POST',
    headers: {
      ...authHeaders(),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });

  return handleResponse<TransliterateResponse>(response);
}

// ── 4. Text to Speech (TTS) ────────────────────────────────

export interface TTSRequest {
  /** Text to read aloud (keep to 1-2 sentences, ~30s of speech) */
  text: string;
  /** Voice persona name (e.g., 'Kavya', 'Amit', 'Deepika') */
  voice: string;
  /** Language code for the text */
  language: string;
  /** Optional speaking style */
  style?: TTSStyle;
}

/**
 * Convert text to speech. Returns a local file URI to the WAV audio.
 * Audio is PCM16, 24 kHz, mono.
 */
export async function textToSpeech(req: TTSRequest): Promise<string> {
  const instructions: Record<string, string> = { lang: req.language };
  if (req.style) instructions.style = req.style;

  const body = {
    model: 'indic-speak',
    input: req.text,
    voice: req.voice,
    instructions: JSON.stringify(instructions),
  };

  // We need to download the audio as a file since the response is binary
  const outputUri = `${FileSystem.cacheDirectory}tts_${Date.now()}.wav`;

  const response = await FileSystem.downloadAsync(
    `${BASE_URL}/v1/audio/speech`,
    outputUri,
    {
      headers: {
        ...authHeaders(),
        'Content-Type': 'application/json',
      },
      httpMethod: 'POST',
      body: JSON.stringify(body),
    }
  );

  if (response.status !== 200) {
    // Try to read error from the downloaded file
    try {
      const errorText = await FileSystem.readAsStringAsync(outputUri);
      const errorData = JSON.parse(errorText);
      throw new BodhanAPIError(
        response.status,
        errorData?.error?.code ?? 'tts_error',
        errorData?.error?.message ?? `TTS failed with status ${response.status}`
      );
    } catch (e) {
      if (e instanceof BodhanAPIError) throw e;
      throw new BodhanAPIError(response.status, 'tts_error', `TTS failed with status ${response.status}`);
    }
  }

  return outputUri;
}

// ── 5. Document OCR ─────────────────────────────────────────

export interface OCRBlock {
  order: number;
  label: string;
  type: string;
  bbox_xyxy: [number, number, number, number];
  conf: number;
  text: string;
}

export interface OCRResponse {
  id: string;
  created: number;
  model: string;
  choices: Array<{
    finish_reason: string;
    index: number;
    message: {
      role: string;
      content: string;
    };
  }>;
  usage: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
  blocks: OCRBlock[];
}

export interface OCRRequest {
  /** Local URI to the image file (PNG or JPEG) */
  imageUri: string;
  /** Table format: 'html' (default) or 'markdown' */
  tableFormat?: 'html' | 'markdown';
  /** Max tokens per block (default 2048, raise for large tables) */
  maxTokens?: number;
}

/**
 * Extract text and layout from a document image using Indic-OCR.
 * Supports PNG and JPEG only. Returns reading-ordered Markdown + layout blocks.
 */
export async function documentOCR(req: OCRRequest): Promise<OCRResponse> {
  // Read image as base64
  const base64 = await FileSystem.readAsStringAsync(req.imageUri, {
    encoding: FileSystem.EncodingType.Base64,
  });

  // Determine MIME type from URI
  const isJpeg = req.imageUri.toLowerCase().match(/\.jpe?g$/);
  const mimeType = isJpeg ? 'image/jpeg' : 'image/png';

  const body: Record<string, any> = {
    model: 'indic-ocr',
    messages: [
      {
        role: 'user',
        content: [
          {
            type: 'image_url',
            image_url: {
              url: `data:${mimeType};base64,${base64}`,
            },
          },
        ],
      },
    ],
  };

  if (req.tableFormat) body.table_format = req.tableFormat;
  if (req.maxTokens) body.max_tokens = req.maxTokens;

  const response = await fetch(`${BASE_URL}/v1/chat/completions`, {
    method: 'POST',
    headers: {
      ...authHeaders(),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });

  return handleResponse<OCRResponse>(response);
}

// ── Utility: Pipeline helpers ───────────────────────────────

/**
 * OCR → Translate pipeline.
 * Scans an image and translates each text block into the target language.
 */
export async function scanAndTranslate(
  imageUri: string,
  targetLanguage: string,
  script?: ScriptMode
): Promise<{
  originalBlocks: OCRBlock[];
  translatedBlocks: Array<{ block: OCRBlock; translation: string; truncated: boolean }>;
  fullMarkdown: string;
  translatedMarkdown: string;
}> {
  // Step 1: OCR
  const ocrResult = await documentOCR({ imageUri, tableFormat: 'markdown' });
  const originalBlocks = ocrResult.blocks;
  const fullMarkdown = ocrResult.choices[0]?.message?.content ?? '';

  // Step 2: Translate each text block
  const textBlocks = originalBlocks.filter(b => b.text && b.text.trim().length > 0);

  const translatedBlocks = await Promise.all(
    textBlocks.map(async (block) => {
      try {
        const result = await translate({
          text: block.text,
          targetLanguage,
          script,
        });
        return {
          block,
          translation: result.translation,
          truncated: result.truncated,
        };
      } catch (error) {
        return {
          block,
          translation: `[Translation failed: ${(error as Error).message}]`,
          truncated: false,
        };
      }
    })
  );

  // Build translated markdown
  const translatedMarkdown = translatedBlocks
    .map(tb => tb.translation)
    .join('\n\n');

  return { originalBlocks, translatedBlocks, fullMarkdown, translatedMarkdown };
}

/**
 * STT → Translate pipeline.
 * Transcribes audio and translates the transcript.
 */
export async function listenAndTranslate(
  audioUri: string,
  targetLanguage: string,
  sourceLanguage?: string,
  script?: ScriptMode
): Promise<{
  transcript: string;
  translation: string;
  truncated: boolean;
}> {
  // Step 1: STT
  const sttResult = await speechToText({
    fileUri: audioUri,
    language: sourceLanguage,
  });

  // Step 2: Translate
  const translateResult = await translate({
    text: sttResult.text,
    targetLanguage,
    script,
  });

  return {
    transcript: sttResult.text,
    translation: translateResult.translation,
    truncated: translateResult.truncated,
  };
}

