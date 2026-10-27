// Remembers the call being processed (URL ?call=<id> + localStorage) so a
// reload, a backgrounded tab or a dropped connection can resume the job.

const STORAGE_KEY = "support-call-summarizer.activeCallId";
const QUERY_PARAM = "call";

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** True when the page was opened with an explicit ?call=<id> link. */
export function hasCallIdInUrl(): boolean {
  try {
    return Boolean(new URL(window.location.href).searchParams.get(QUERY_PARAM)?.trim());
  } catch {
    return false;
  }
}

export function readActiveCallId(): string | null {
  try {
    const fromUrl = new URL(window.location.href).searchParams.get(QUERY_PARAM)?.trim();
    if (fromUrl) return fromUrl;
  } catch {
    // malformed URL; fall back to storage
  }
  try {
    return storage()?.getItem(STORAGE_KEY) || null;
  } catch {
    return null;
  }
}

function writeUrl(callId: string | null): void {
  try {
    const url = new URL(window.location.href);
    if (callId) url.searchParams.set(QUERY_PARAM, callId);
    else url.searchParams.delete(QUERY_PARAM);
    if (url.href !== window.location.href) window.history.replaceState(window.history.state, "", url.href);
  } catch {
    // history can be unavailable in sandboxed frames
  }
}

export function saveActiveCallId(callId: string): void {
  try {
    storage()?.setItem(STORAGE_KEY, callId);
  } catch {
    // storage blocked or full; the URL still carries the id
  }
  writeUrl(callId);
}

export function clearActiveCallId(): void {
  try {
    storage()?.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
  writeUrl(null);
}
