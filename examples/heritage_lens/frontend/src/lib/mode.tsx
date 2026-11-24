import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "./api";
import type { ConfigStatus } from "./types";

export type Mode = "demo" | "live";
const KEY = "hl_mode";
interface ModeCtx {
  mode: Mode; live: boolean; setMode: (m: Mode) => void;
  status: ConfigStatus | null; statusError: string | null; refreshStatus: () => void;
}
const Ctx = createContext<ModeCtx | null>(null);

export function readStoredMode(): Mode {
  try { return localStorage.getItem(KEY) === "live" ? "live" : "demo"; } catch { return "demo"; }
}

/** The mode is an explicit user choice. Nothing in the app changes it automatically. */
export function ModeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<Mode>(readStoredMode);
  const [status, setStatus] = useState<ConfigStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);

  const refreshStatus = useCallback(() => {
    api.configStatus().then((s) => { setStatus(s); setStatusError(null); })
      .catch((e: Error) => { setStatus(null); setStatusError(e.message); });
  }, []);
  useEffect(refreshStatus, [refreshStatus]);

  const setMode = useCallback((m: Mode) => { setModeState(m); try { localStorage.setItem(KEY, m); } catch { /* ignore */ } }, []);
  const value = useMemo(() => ({ mode, live: mode === "live", setMode, status, statusError, refreshStatus }), [mode, setMode, status, statusError, refreshStatus]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useMode(): ModeCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useMode must be used inside <ModeProvider>");
  return v;
}
