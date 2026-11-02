"use client";

import { useEffect, useRef, useState } from "react";
import { transcribeAudio } from "./transcribeAudio.mjs";

export default function useTranscription(audio, language, apiBaseUrl) {
  const [result, setResult] = useState(null);
  const requestRef = useRef(null);
  const current = Boolean(result && result.audio === audio && result.language === language && result.apiBaseUrl === apiBaseUrl);

  // Changing or discarding the source also invalidates its transcript and request.
  useEffect(() => () => {
    requestRef.current?.controller.abort();
    requestRef.current = null;
  }, [audio, language, apiBaseUrl]);

  useEffect(() => {
    if (!current || !result?.retryAfterSeconds) return;
    const blockedRequest = requestRef.current;
    const timer = setTimeout(() => {
      if (requestRef.current === blockedRequest) requestRef.current = null;
      setResult((previous) => previous === result ? { ...previous, retryAfterSeconds: 0 } : previous);
    }, result.retryAfterSeconds * 1000);
    return () => clearTimeout(timer);
  }, [current, result]);

  async function transcribe() {
    if (!audio || requestRef.current || (current && (result.status === "success" || result.retryAfterSeconds))) return;
    const request = { controller: new AbortController() };
    const source = { audio, language, apiBaseUrl };
    requestRef.current = request;
    setResult({ ...source, status: "loading", text: "", error: "", retryAfterSeconds: 0 });
    try {
      const text = await transcribeAudio(audio, language, { apiBaseUrl, signal: request.controller.signal });
      if (requestRef.current !== request || request.controller.signal.aborted) return;
      request.keep = true;
      setResult({ ...source, status: "success", text, error: "", retryAfterSeconds: 0 });
    } catch (error) {
      if (requestRef.current !== request || request.controller.signal.aborted) return;
      request.keep = Boolean(error.retryAfterSeconds);
      setResult({ ...source, status: "error", text: "", error: error.message, retryAfterSeconds: error.retryAfterSeconds || 0 });
    } finally {
      if (requestRef.current === request && !request.keep) requestRef.current = null;
    }
  }

  return {
    status: current ? result.status : "idle",
    text: current ? result.text : "",
    error: current ? result.error : "",
    retryAfterSeconds: current ? result.retryAfterSeconds : 0,
    transcribe,
  };
}
