import { useCallback, useEffect, useRef, useState } from "react";
import { blob } from "./api";

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [state, setState] = useState<{ data?: T; error?: Error; loading: boolean }>({ loading: true });
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    let cancelled = false;
    setState((s) => ({ data: s.data, loading: true }));
    fnRef.current().then(
      (data) => { if (!cancelled) setState({ data, loading: false }); },
      (error: Error) => { if (!cancelled) setState({ error, loading: false }); },
    );
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { ...state, reload, setData: (data: T) => setState({ data, loading: false }) };
}

/** Fetch a protected/public media path with the auth header and expose it as an object URL. */
export function useBlobUrl(path: string | null | undefined, enabled = true) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!path || !enabled) return;
    let revoked = false; let objectUrl: string | null = null;
    setLoading(true); setError(null);
    blob(path).then((b) => { if (revoked) return; objectUrl = URL.createObjectURL(b); setUrl(objectUrl); })
      .catch((e: Error) => !revoked && setError(e.message))
      .finally(() => !revoked && setLoading(false));
    return () => { revoked = true; if (objectUrl) URL.revokeObjectURL(objectUrl); setUrl(null); };
  }, [path, enabled]);
  return { url, error, loading };
}

export function useDebounced<T>(value: T, ms = 350): T {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}
