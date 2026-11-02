import { isTranscriptionLanguage } from "../../../packages/bhashavoice/src/languages.mjs";
import { AudioError, MAX_AUDIO_BYTES, prepareAudio } from "./prepareAudio.mjs";
import { transcribeWav } from "../bodhan/transcribe.mjs";

const MAX_BODY_BYTES = MAX_AUDIO_BYTES + 64 * 1024;
const AUDIO_TYPES = new Set([
  "audio/webm", "video/webm", "audio/mp4", "video/mp4", "audio/x-m4a", "audio/aac",
  "audio/wav", "audio/wave", "audio/x-wav", "audio/vnd.wave",
  "audio/mpeg", "audio/mp3", "audio/ogg", "application/ogg", "audio/flac", "audio/x-flac",
]);
let activeRequests = 0;

function reply(body, status = 200, extraHeaders = {}) {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store", ...extraHeaders } });
}

async function readForm(request, signal) {
  const contentType = request.headers.get("content-type") || "";
  if (!/^multipart\/form-data\s*;/i.test(contentType)) {
    throw new AudioError("INVALID_CONTENT_TYPE", "Send the recording as multipart/form-data.", 415);
  }
  const length = request.headers.get("content-length");
  if (length && (!/^\d+$/.test(length) || Number(length) > MAX_BODY_BYTES)) {
    throw new AudioError("UPLOAD_TOO_LARGE", "The upload is too large.", 413);
  }
  if (!request.body) throw new AudioError("MISSING_AUDIO", "Attach an audio file in the file field.", 400);

  const reader = request.body.getReader();
  const chunks = [];
  let size = 0;
  let onAbort;
  const aborted = new Promise((_, reject) => {
    onAbort = () => {
      void reader.cancel().catch(() => {});
      reject(new AudioError("PROCESSING_TIMEOUT", "The upload was cancelled or timed out.", 408));
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
      if (size > MAX_BODY_BYTES) {
        await reader.cancel();
        throw new AudioError("UPLOAD_TOO_LARGE", "The upload is too large.", 413);
      }
      chunks.push(value);
    }
  } finally {
    signal.removeEventListener("abort", onAbort);
    reader.releaseLock();
  }
  try {
    return await new Response(Buffer.concat(chunks), { headers: { "Content-Type": contentType } }).formData();
  } catch {
    throw new AudioError("INVALID_FORM_DATA", "The upload could not be read. Please try again.", 400);
  }
}

export async function handleAudioUpload(request, { transcribe = transcribeWav } = {}) {
  if (activeRequests >= 2) {
    return reply({ error: { code: "SERVER_BUSY", message: "Audio processing is busy. Please try again shortly." } }, 429);
  }
  activeRequests++;
  const signal = AbortSignal.any([request.signal, AbortSignal.timeout(30000)]);
  try {
    const form = await readForm(request, signal);
    const file = form.get("file");
    const language = form.get("language");
    if (!(file instanceof File) || form.getAll("file").length !== 1) {
      throw new AudioError("MISSING_AUDIO", "Attach one audio file in the file field.", 400);
    }
    if (!isTranscriptionLanguage(language) || form.getAll("language").length !== 1) {
      throw new AudioError("INVALID_LANGUAGE", "Choose a supported spoken language.", 400);
    }
    if (!AUDIO_TYPES.has(file.type.split(";")[0].trim().toLowerCase())) {
      throw new AudioError("UNSUPPORTED_AUDIO_TYPE", "Upload a supported audio recording.", 415);
    }
    const { wav, durationSeconds, sampleRate, channels } = await prepareAudio(file, { signal });
    signal.throwIfAborted();
    const text = await transcribe(wav, language, { signal });
    signal.throwIfAborted();
    return reply({
      status: "transcribed",
      language,
      audio: { mimeType: "audio/wav", sizeBytes: wav.length, durationSeconds, sampleRate, channels },
      text,
    });
  } catch (error) {
    if (error instanceof AudioError) {
      return reply({ error: {
        code: error.code, message: error.message,
        ...(error.requestId ? { requestId: error.requestId } : {}),
        ...(error.retryAfterSeconds ? { retryAfterSeconds: error.retryAfterSeconds } : {}),
      } }, error.status, error.retryAfterSeconds ? { "Retry-After": String(error.retryAfterSeconds) } : {});
    }
    if (signal.aborted) return reply({ error: { code: "PROCESSING_TIMEOUT", message: "The request was cancelled or timed out." } }, 408);
    return reply({ error: { code: "AUDIO_PROCESSING_FAILED", message: "Audio processing failed. Please try again." } }, 500);
  } finally {
    activeRequests--;
  }
}
