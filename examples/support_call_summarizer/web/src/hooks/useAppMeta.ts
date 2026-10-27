import { useCallback, useEffect, useState } from "react";
import { isAbortError, type ApiClient, type AppConfig, type Language } from "../api";

export type AppMeta =
  | { status: "loading"; config: null; languages: Language[]; error: null }
  | { status: "ready"; config: AppConfig; languages: Language[]; error: null }
  | { status: "error"; config: null; languages: Language[]; error: string };

const LOADING: AppMeta = { status: "loading", config: null, languages: [], error: null };

/** Loads /api/config and /api/languages once; `reload` retries after a failure. */
export function useAppMeta(api: Pick<ApiClient, "getConfig" | "getLanguages">): AppMeta & { reload: () => void } {
  const [meta, setMeta] = useState<AppMeta>(LOADING);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setMeta(LOADING);
    Promise.all([api.getConfig(controller.signal), api.getLanguages(controller.signal)])
      .then(([config, languages]) => setMeta({ status: "ready", config, languages, error: null }))
      .catch((err: unknown) => {
        if (isAbortError(err)) return;
        const message = err instanceof Error ? err.message : "Could not load settings from the server.";
        setMeta({ status: "error", config: null, languages: [], error: message });
      });
    return () => controller.abort();
  }, [api, attempt]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  return { ...meta, reload };
}
