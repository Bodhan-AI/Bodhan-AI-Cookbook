const MESSAGES = {
  TRANSLATION_NOT_CONFIGURED: "Translation has not been configured on this server.",
  TRANSLATION_AUTH_FAILED: "Translation needs a server API key with access to indic-translate. You can still insert the original text.",
  TRANSLATION_CREDIT_EXHAUSTED: "The translation account needs credit. Your original text is still available.",
  TRANSLATION_RATE_LIMITED: "Translation is busy. Please try again later.",
  SERVER_BUSY: "Translation is busy. Please try again shortly.",
  TRANSLATION_INCOMPLETE: "The translation was incomplete. You can still insert the original text.",
  TRANSLATION_TIMEOUT: "Translation timed out. You can try again or insert the original text.",
  TRANSLATION_CANCELLED: "Translation was cancelled or timed out. Your original text is still available.",
  INVALID_TRANSLATION_TEXT: "Translation supports between 1 and 2,000 characters.",
};

export async function requestTranslation(text, targetLanguage, {
  apiBaseUrl = "/api/bhasha", signal, fetchImpl = fetch,
} = {}) {
  const timeout = AbortSignal.timeout(35000);
  const combined = AbortSignal.any([timeout, ...(signal ? [signal] : [])]);
  try {
    combined.throwIfAborted();
    const response = await fetchImpl(`${apiBaseUrl.replace(/\/+$/, "")}/translate`, {
      method: "POST", cache: "no-store", credentials: "same-origin", signal: combined,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, target_language: targetLanguage }),
    });
    let result;
    try { result = await response.json(); }
    catch { throw new Error("Translation returned an unreadable response. Your original text is still available."); }
    combined.throwIfAborted();
    if (!response.ok || result?.error) {
      const error = new Error(MESSAGES[result?.error?.code] || "Translation failed. Your original text is still available.");
      const delay = result?.error?.retryAfterSeconds ?? response.headers.get("retry-after");
      if (delay !== null && /^\d+$/.test(String(delay))) error.retryAfterSeconds = Math.min(3600, Math.max(1, Number(delay)));
      throw error;
    }
    if (result?.status !== "translated" || result?.truncated !== false || result?.target_language !== targetLanguage || typeof result?.translation !== "string" || !result.translation.trim()) {
      throw new Error("No complete translation was returned. Your original text is still available.");
    }
    return result.translation.trim();
  } catch (error) {
    if (signal?.aborted) throw error;
    if (timeout.aborted) throw new Error(MESSAGES.TRANSLATION_TIMEOUT);
    if (error instanceof TypeError) throw new Error("Could not reach the translation server. Your original text is still available.");
    throw error;
  }
}
