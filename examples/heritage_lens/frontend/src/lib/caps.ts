import { useEffect, useState } from "react";
import { api } from "./api";
import type { Capabilities } from "./types";

let cached: Promise<Capabilities> | null = null;
export const loadCapabilities = () => (cached ??= api.capabilities().catch((e) => { cached = null; throw e; }));

/** Per-service capability tables from the backend, so unsupported options can be disabled. */
export function useCapabilities() {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { loadCapabilities().then(setCaps).catch((e: Error) => setError(e.message)); }, []);
  return { caps, error };
}

const EXTRA: Record<string, string> = { ja: "Japanese", fr: "French", es: "Spanish", de: "German", zh: "Chinese", ar: "Arabic", pt: "Portuguese", ru: "Russian", sw: "Swahili" };
export function langName(code?: string | null, caps?: Capabilities | null): string {
  if (!code) return "Unknown";
  const s = caps?.services;
  return s?.["indic-transcribe"].languages[code] ?? s?.["indic-translate"].target_languages[code] ?? EXTRA[code] ?? code;
}

/** Languages offered when describing a story: all Bodhan transcription languages plus a few common others. */
export function languageOptions(caps?: Capabilities | null): [string, string][] {
  const base = caps?.services["indic-transcribe"].languages ?? { te: "Telugu", hi: "Hindi", en: "English" };
  return [...Object.entries(base), ...Object.entries(EXTRA).filter(([k]) => !(k in base))].sort((a, b) => a[1].localeCompare(b[1]));
}
