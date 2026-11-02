import { isTranslationLanguage } from "../../../packages/bhashavoice/src/languages.mjs";
export class TranslationError extends Error {
  constructor(code, message, status) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

export function validateTranslation(text, targetLanguage) {
  if (typeof text !== "string" || !text.trim() || text.length > 2000) {
    throw new TranslationError("INVALID_TRANSLATION_TEXT", "Provide between 1 and 2,000 characters of text.", 400);
  }
  if (!isTranslationLanguage(targetLanguage)) {
    throw new TranslationError("INVALID_TARGET_LANGUAGE", "Choose a supported translation language.", 400);
  }
}

function providerError(code, message, status, response, apiKey) {
  const error = new TranslationError(code, message, status);
  const requestId = response?.headers.get("x-request-id");
  if (requestId && /^[a-zA-Z0-9._:-]{1,128}$/.test(requestId) && !requestId.includes(apiKey.trim())) error.requestId = requestId;
  const delay = response?.headers.get("retry-after");
  if (delay) {
    const seconds = /^\d+$/.test(delay) ? Number(delay) : Math.ceil((Date.parse(delay) - Date.now()) / 1000);
    if (Number.isFinite(seconds) && seconds >= 0) error.retryAfterSeconds = Math.min(3600, Math.max(1, seconds));
  }
  return error;
}

// Bound both memory and time, including a response that stops streaming midway.
export async function readBoundedJson(body, signal, maxBytes) {
  const reader = body?.getReader();
  if (!reader) throw new Error("Missing body");
  const chunks = [];
  let size = 0;
  let onAbort;
  const aborted = new Promise((_, reject) => {
    onAbort = () => {
      void reader.cancel().catch(() => {});
      reject(new Error("Request aborted"));
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
  try {
    signal.throwIfAborted();
    while (true) {
      const { value, done } = await Promise.race([reader.read(), aborted]);
      signal.throwIfAborted();
      if (done) break;
      size += value.byteLength;
      if (size > maxBytes) {
        void reader.cancel().catch(() => {});
        throw new TranslationError("PAYLOAD_TOO_LARGE", "The request or response is too large.", 413);
      }
      chunks.push(value);
    }
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } finally {
    signal.removeEventListener("abort", onAbort);
    reader.releaseLock();
  }
}

export async function translateText(text, targetLanguage, {
  signal,
  apiKey = process.env.BODHAN_TRANSLATE_API_KEY || process.env.BODHAN_TRANSCRIBE_API_KEY || process.env.BODHAN_API_KEY,
  fetchImpl = fetch,
  timeoutSignal = AbortSignal.timeout(20000),
} = {}) {
  validateTranslation(text, targetLanguage);
  if (typeof apiKey !== "string" || !apiKey.trim() || /[\r\n]/.test(apiKey)) {
    throw new TranslationError("TRANSLATION_NOT_CONFIGURED", "Translation is not configured on this server.", 503);
  }
  const combined = AbortSignal.any([timeoutSignal, ...(signal ? [signal] : [])]);
  let response;
  const fail = (code, message, status) => providerError(code, message, status, response, apiKey);
  try {
    combined.throwIfAborted();
    response = await fetchImpl("https://api.bodhan.ai/v1/chat/completions", {
      method: "POST", cache: "no-store", redirect: "error", signal: combined,
      headers: { Authorization: `Bearer ${apiKey.trim()}`, "Content-Type": "application/json" },
      // This documented transport is available on the live gateway; /translate returned 404.
      // Bodhan detects the source language. No generation controls or custom prompts.
      body: JSON.stringify({ model: "indic-translate", messages: [{ role: "user", content: text }], target_language_code: targetLanguage }),
    });
    if (!response.ok) {
      void response.body?.cancel().catch(() => {});
      if ([401, 403].includes(response.status)) throw fail("TRANSLATION_AUTH_FAILED", "Translation credentials were rejected.", 502);
      if (response.status === 402) throw fail("TRANSLATION_CREDIT_EXHAUSTED", "The translation account needs credit.", 503);
      if (response.status === 429) throw fail("TRANSLATION_RATE_LIMITED", "Translation is busy. Please try again later.", 429);
      if ([408, 504].includes(response.status)) throw fail("TRANSLATION_TIMEOUT", "Translation timed out. Please try again.", 504);
      throw fail("TRANSLATION_FAILED", "Translation failed. Your original text is still available.", 502);
    }
    let result;
    try {
      result = await readBoundedJson(response.body, combined, 64 * 1024);
    } catch (error) {
      if (combined.aborted) throw error;
      throw fail("INVALID_TRANSLATION_RESPONSE", "Translation did not return usable text.", 502);
    }
    combined.throwIfAborted();
    const choice = result?.choices?.[0];
    if (result?.truncated === true || choice?.finish_reason === "length") throw fail("TRANSLATION_INCOMPLETE", "The translation was incomplete. Your original text is still available.", 502);
    if (result?.error || !Array.isArray(result?.choices) || result.choices.length !== 1 || choice?.finish_reason !== "stop" || typeof choice?.message?.content !== "string" || !choice.message.content.trim()) {
      throw fail("INVALID_TRANSLATION_RESPONSE", "Translation did not return usable text.", 502);
    }
    return choice.message.content.trim();
  } catch (error) {
    if (signal?.aborted) throw new TranslationError("TRANSLATION_CANCELLED", "Translation was cancelled or timed out.", 408);
    if (timeoutSignal.aborted) throw fail("TRANSLATION_TIMEOUT", "Translation timed out. Please try again.", 504);
    if (error instanceof TranslationError) throw error;
    throw fail("TRANSLATION_UNAVAILABLE", "Translation could not be reached. Please try again.", 502);
  }
}
