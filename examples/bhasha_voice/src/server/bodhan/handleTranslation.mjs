import { readBoundedJson, translateText, TranslationError, validateTranslation } from "./translate.mjs";

let activeRequests = 0;
const MAX_BODY_BYTES = 32 * 1024;
const reply = (body, status = 200, headers = {}) => Response.json(body, {
  status, headers: { "Cache-Control": "no-store", ...headers },
});

export async function handleTranslation(request, { translate = translateText } = {}) {
  if (activeRequests >= 2) return reply({ error: { code: "SERVER_BUSY", message: "Translation is busy. Please try again shortly." } }, 429, { "Retry-After": "1" });
  activeRequests++;
  const signal = AbortSignal.any([request.signal, AbortSignal.timeout(30000)]);
  try {
    if (!/^application\/json(?:\s*;|$)/i.test(request.headers.get("content-type") || "")) {
      throw new TranslationError("INVALID_CONTENT_TYPE", "Send text as application/json.", 415);
    }
    const length = request.headers.get("content-length");
    if (length && (!/^\d+$/.test(length) || Number(length) > MAX_BODY_BYTES)) {
      throw new TranslationError("PAYLOAD_TOO_LARGE", "The translation request is too large.", 413);
    }
    let body;
    try {
      body = await readBoundedJson(request.body, signal, MAX_BODY_BYTES);
    } catch (error) {
      if (signal.aborted || error instanceof TranslationError) throw error;
      throw new TranslationError("INVALID_JSON", "Send a valid JSON request.", 400);
    }
    validateTranslation(body?.text, body?.target_language);
    signal.throwIfAborted();
    const translation = await translate(body.text, body.target_language, { signal });
    signal.throwIfAborted();
    return reply({ status: "translated", translation, target_language: body.target_language, truncated: false });
  } catch (error) {
    if (signal.aborted) return reply({ error: { code: "TRANSLATION_CANCELLED", message: "Translation was cancelled or timed out." } }, 408);
    if (error instanceof TranslationError) return reply({ error: {
      code: error.code, message: error.message,
      ...(error.requestId ? { requestId: error.requestId } : {}),
      ...(error.retryAfterSeconds ? { retryAfterSeconds: error.retryAfterSeconds } : {}),
    } }, error.status, error.retryAfterSeconds ? { "Retry-After": String(error.retryAfterSeconds) } : {});
    return reply({ error: { code: "TRANSLATION_FAILED", message: "Translation failed. Your original text is still available." } }, 500);
  } finally {
    activeRequests--;
  }
}
