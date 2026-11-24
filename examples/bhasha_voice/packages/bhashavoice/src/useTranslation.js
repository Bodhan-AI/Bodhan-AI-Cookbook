"use client";

import { useEffect, useRef, useState } from "react";
import { requestTranslation } from "./translateText.mjs";

// Translation UI is keyed by source, target and backend: each edit starts fresh.
export default function useTranslation(text, targetLanguage, apiBaseUrl) {
  const [result, setResult] = useState({ status: "idle", text: "", error: "", retryAfterSeconds: 0 });
  const requestRef = useRef(null);
  useEffect(() => () => {
    requestRef.current?.controller.abort();
    requestRef.current = null;
  }, []);

  useEffect(() => {
    if (!result.retryAfterSeconds) return;
    const blocked = requestRef.current;
    const timer = setTimeout(() => {
      if (requestRef.current === blocked) requestRef.current = null;
      setResult((previous) => previous === result ? { ...previous, retryAfterSeconds: 0 } : previous);
    }, result.retryAfterSeconds * 1000);
    return () => clearTimeout(timer);
  }, [result]);

  async function translate() {
    if (requestRef.current || !text.trim() || text.length > 2000) return;
    const request = { controller: new AbortController() };
    requestRef.current = request;
    setResult({ status: "loading", text: "", error: "", retryAfterSeconds: 0 });
    try {
      const translated = await requestTranslation(text, targetLanguage, { apiBaseUrl, signal: request.controller.signal });
      if (requestRef.current !== request || request.controller.signal.aborted) return;
      request.keep = true;
      setResult({ status: "success", text: translated, error: "", retryAfterSeconds: 0 });
    } catch (error) {
      if (requestRef.current !== request || request.controller.signal.aborted) return;
      request.keep = Boolean(error.retryAfterSeconds);
      setResult({ status: "error", text: "", error: error.message, retryAfterSeconds: error.retryAfterSeconds || 0 });
    } finally {
      if (requestRef.current === request && !request.keep) requestRef.current = null;
    }
  }

  function edit(value) {
    if (result.status === "success") setResult({ ...result, text: value });
  }

  return { ...result, translate, edit };
}
