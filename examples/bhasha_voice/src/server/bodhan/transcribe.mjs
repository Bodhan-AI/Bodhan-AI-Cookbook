import { isTranscriptionLanguage } from "../../../packages/bhashavoice/src/languages.mjs";
import { AudioError } from "../audio/prepareAudio.mjs";

const ENDPOINT = "https://api.bodhan.ai/v1/audio/transcriptions";
const MAX_RESPONSE_BYTES = 64 * 1024;

function providerError(code, message, status, response, apiKey) {
  const error = new AudioError(code, message, status);
  const requestId = response?.headers.get("x-request-id");
  if (requestId && /^[a-zA-Z0-9._:-]{1,128}$/.test(requestId) && !requestId.includes(apiKey.trim())) {
    error.requestId = requestId;
  }
  const retryAfter = response?.headers.get("retry-after");
  if (retryAfter) {
    const seconds = /^\d+$/.test(retryAfter) ? Number(retryAfter) : Math.ceil((Date.parse(retryAfter) - Date.now()) / 1000);
    if (Number.isFinite(seconds) && seconds >= 0) error.retryAfterSeconds = Math.min(3600, Math.max(1, seconds));
  }
  return error;
}

async function readTranscript(response, signal) {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Missing response body");
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      signal.throwIfAborted();
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_RESPONSE_BYTES) {
        await reader.cancel();
        throw new Error("Response too large");
      }
      chunks.push(value);
    }
    signal.throwIfAborted();
    const result = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    if (result.error || typeof result.text !== "string" || !result.text.trim()) {
      throw new Error("Missing transcript");
    }
    return result.text.trim();
  } finally {
    reader.releaseLock();
  }
}

export async function transcribeWav(wav, language, {
  signal,
  apiKey = process.env.BODHAN_TRANSCRIBE_API_KEY || process.env.BODHAN_API_KEY,
  fetchImpl = fetch,
  timeoutSignal = AbortSignal.timeout(20000),
} = {}) {
  if (typeof apiKey !== "string" || !apiKey.trim() || /[\r\n]/.test(apiKey)) {
    throw new AudioError("TRANSCRIPTION_NOT_CONFIGURED", "Transcription is not configured on this server.", 503);
  }
  if (!isTranscriptionLanguage(language)) {
    throw new AudioError("INVALID_LANGUAGE", "Choose a supported spoken language.", 400);
  }
  const combined = AbortSignal.any([timeoutSignal, ...(signal ? [signal] : [])]);
  const body = new FormData();
  body.set("file", new Blob([wav], { type: "audio/wav" }), "recording.wav");
  body.set("model", "indic-transcribe");
  body.set("language", language);

  let response;
  try {
    combined.throwIfAborted();
    // One request only. Automatic retries could duplicate paid transcription.
    response = await fetchImpl(ENDPOINT, {
      method: "POST", headers: { Authorization: `Bearer ${apiKey.trim()}` },
      body, signal: combined, redirect: "error", cache: "no-store",
    });
    if (!response.ok) {
      await response.body?.cancel().catch(() => {});
      const status = response.status;
      if (status === 401 || status === 403) {
        throw providerError("PROVIDER_AUTH_FAILED", "The transcription service rejected the server credentials.", 502, response, apiKey);
      }
      if (status === 402) {
        throw providerError("PROVIDER_CREDIT_EXHAUSTED", "The transcription account needs credit before it can process audio.", 503, response, apiKey);
      }
      if (status === 429) {
        throw providerError("PROVIDER_RATE_LIMITED", "The transcription service is busy. Please try again later.", 429, response, apiKey);
      }
      if (status === 408 || status === 504) {
        throw providerError("PROVIDER_TIMEOUT", "The transcription service timed out. Please try again.", 504, response, apiKey);
      }
      throw providerError("PROVIDER_REQUEST_FAILED", "The transcription service could not process this recording. Please try again.", 502, response, apiKey);
    }
    let text;
    try {
      text = await readTranscript(response, combined);
    } catch (error) {
      if (combined.aborted) throw error;
      throw providerError("INVALID_PROVIDER_RESPONSE", "The transcription service did not return a usable transcript.", 502, response, apiKey);
    }
    // Never accept a response for an upload that has since been cancelled.
    combined.throwIfAborted();
    return text;
  } catch (error) {
    if (signal?.aborted) {
      throw new AudioError("PROCESSING_TIMEOUT", "The transcription request was cancelled or timed out.", 408);
    }
    if (timeoutSignal.aborted) {
      throw providerError("PROVIDER_TIMEOUT", "The transcription service timed out. Please try again.", 504, response, apiKey);
    }
    if (error instanceof AudioError) throw error;
    throw providerError("PROVIDER_UNAVAILABLE", "The transcription service could not be reached. Please try again.", 502, response, apiKey);
  }
}
