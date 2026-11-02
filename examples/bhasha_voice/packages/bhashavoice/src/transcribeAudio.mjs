const MESSAGES = {
  AUDIO_TOO_LARGE: "The recording is too large. Please record a shorter clip.",
  UPLOAD_TOO_LARGE: "The recording is too large. Please record a shorter clip.",
  AUDIO_TOO_LONG: "The recording exceeds 25 seconds. Please record a shorter clip.",
  EMPTY_AUDIO: "The recording is empty. Please record again.",
  INVALID_AUDIO: "The recording could not be decoded. Please record again.",
  INVALID_LANGUAGE: "Choose a supported spoken language before transcribing.",
  SERVER_BUSY: "Audio processing is busy. Please try again shortly.",
  PROVIDER_RATE_LIMITED: "The transcription service is busy. Please try again later.",
  PROVIDER_AUTH_FAILED: "Transcription is unavailable because the server credentials were rejected.",
  PROVIDER_CREDIT_EXHAUSTED: "The transcription account needs credit. Your recording is still available.",
  TRANSCRIPTION_NOT_CONFIGURED: "Transcription has not been configured on this server.",
  CONVERTER_UNAVAILABLE: "Audio conversion is not configured on this server.",
  PROCESSING_TIMEOUT: "Transcription timed out. Your recording is still available to try again.",
  PROVIDER_TIMEOUT: "Transcription timed out. Your recording is still available to try again.",
};

export async function transcribeAudio(audio, language, {
  apiBaseUrl = "/api/bhasha", signal, fetchImpl = fetch,
} = {}) {
  const body = new FormData();
  // The server decodes the contents, so it does not depend on this filename.
  body.set("file", audio, "recording");
  body.set("language", language);
  const timeout = AbortSignal.timeout(35000);
  const combined = AbortSignal.any([timeout, ...(signal ? [signal] : [])]);
  try {
    combined.throwIfAborted();
    const response = await fetchImpl(`${apiBaseUrl.replace(/\/+$/, "")}/transcribe`, {
      method: "POST", body, signal: combined, cache: "no-store", credentials: "same-origin",
    });
    let result;
    try {
      result = await response.json();
    } catch {
      throw new Error("Transcription returned an unreadable response. Your recording is still available.");
    }
    combined.throwIfAborted();
    if (!response.ok || result?.error) {
      const error = new Error(MESSAGES[result?.error?.code] || "Transcription failed. Your recording is still available to try again.");
      const delay = result?.error?.retryAfterSeconds ?? response.headers.get("retry-after");
      if (delay !== null && /^\d+$/.test(String(delay))) {
        error.retryAfterSeconds = Math.min(3600, Math.max(1, Number(delay)));
      }
      throw error;
    }
    if (result?.status !== "transcribed" || typeof result.text !== "string" || !result.text.trim()) {
      throw new Error("No transcript was returned. Your recording is still available to try again.");
    }
    return result.text.trim();
  } catch (error) {
    if (signal?.aborted) throw error;
    if (timeout.aborted) throw new Error(MESSAGES.PROCESSING_TIMEOUT);
    if (error instanceof TypeError) {
      throw new Error("Could not reach the transcription server. Your recording is still available.");
    }
    throw error;
  }
}
