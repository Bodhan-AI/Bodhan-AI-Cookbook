import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, isAbortError, isTerminalStatus, type ApiClient, type JobView } from "../api";

export interface CallJobState {
  job: JobView | null;
  /** The last poll failed with a network/5xx error; we're retrying with backoff. */
  connectionLost: boolean;
  /** 404: unknown id or the result expired. */
  notFound: boolean;
  /** A non-retryable error (e.g. 401). */
  error: string | null;
}

export interface CallJobOptions {
  pollIntervalMs?: number;
  maxBackoffMs?: number;
}

const EMPTY: CallJobState = { job: null, connectionLost: false, notFound: false, error: null };

export function retryDelay(failures: number, base: number, cap: number): number {
  return Math.min(cap, base * 2 ** Math.max(0, failures));
}

/**
 * Polls GET /api/calls/{id} until the job reaches a terminal status.
 * Survives network drops (exponential backoff, capped) and re-polls
 * immediately when the tab becomes visible again or the device comes online.
 */
export function useCallJob(
  api: Pick<ApiClient, "getCall">,
  callId: string | null,
  { pollIntervalMs = 2000, maxBackoffMs = 15000 }: CallJobOptions = {},
): CallJobState & { retryNow: () => void } {
  const [state, setState] = useState<CallJobState & { callId: string | null }>({ ...EMPTY, callId });
  const pollNowRef = useRef<() => void>(() => undefined);

  useEffect(() => {
    setState({ ...EMPTY, callId });
    if (!callId) return;

    let cancelled = false;
    let finished = false;
    let failures = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let inFlight: AbortController | null = null;

    const schedule = (ms: number) => {
      clearTimeout(timer);
      timer = setTimeout(poll, ms);
    };

    async function poll() {
      if (cancelled || finished || inFlight) return;
      clearTimeout(timer);
      const controller = new AbortController();
      inFlight = controller;
      try {
        const job = await api.getCall(callId as string, controller.signal);
        if (cancelled) return;
        failures = 0;
        setState({ job, connectionLost: false, notFound: false, error: null, callId });
        if (isTerminalStatus(job.status)) finished = true;
        else schedule(pollIntervalMs);
      } catch (err) {
        if (cancelled || isAbortError(err)) return;
        if (err instanceof ApiError && err.status === 404) {
          finished = true;
          setState((s) => ({ ...s, connectionLost: false, notFound: true }));
        } else if (!(err instanceof ApiError) || err.isRetryable) {
          failures += 1;
          setState((s) => ({ ...s, connectionLost: true }));
          schedule(retryDelay(failures, pollIntervalMs, maxBackoffMs));
        } else {
          finished = true;
          setState((s) => ({ ...s, connectionLost: false, error: err.message }));
        }
      } finally {
        if (inFlight === controller) inFlight = null;
      }
    }

    const onVisible = () => {
      if (document.visibilityState === "visible") void poll();
    };
    const onOnline = () => void poll();

    pollNowRef.current = () => void poll();
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", onOnline);
    void poll();

    return () => {
      cancelled = true;
      clearTimeout(timer);
      inFlight?.abort();
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("online", onOnline);
      pollNowRef.current = () => undefined;
    };
  }, [api, callId, pollIntervalMs, maxBackoffMs]);

  const retryNow = useCallback(() => pollNowRef.current(), []);
  // Hide stale state from a previous call id during the render before the effect resets it.
  const current = state.callId === callId ? state : { ...EMPTY, callId };
  return {
    job: current.job,
    connectionLost: current.connectionLost,
    notFound: current.notFound,
    error: current.error,
    retryNow,
  };
}
