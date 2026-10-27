const pad2 = (n: number) => String(n).padStart(2, "0");

/** 65 -> "01:05", 3725 -> "1:02:05". Fractions are truncated, negatives clamp to 0. */
export function formatTimestamp(totalSeconds: number): string {
  const s = Number.isFinite(totalSeconds) ? Math.max(0, Math.floor(totalSeconds)) : 0;
  const hours = Math.floor(s / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const seconds = s % 60;
  return hours > 0 ? `${hours}:${pad2(minutes)}:${pad2(seconds)}` : `${pad2(minutes)}:${pad2(seconds)}`;
}

export const formatDuration = formatTimestamp;

/** "[00:05 – 00:12]" */
export function formatTimeRange(start: number, end: number): string {
  return `[${formatTimestamp(start)} – ${formatTimestamp(end)}]`;
}

export function formatDateTime(iso: string, locale?: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" });
}

export function formatTime(iso: string, locale?: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleTimeString(locale, { hour: "numeric", minute: "2-digit" });
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(kb < 10 ? 1 : 0)} KB`;
  const mb = kb / 1024;
  return `${mb.toFixed(mb < 10 ? 1 : 0)} MB`;
}

/** 45 -> "45 seconds", 1800 -> "30 minutes", 3600 -> "1 hour", 5400 -> "90 minutes". */
export function formatLimitDuration(seconds: number): string {
  if (seconds < 120) return `${Math.round(seconds)} seconds`;
  if (seconds % 3600 === 0) {
    const hours = seconds / 3600;
    return hours === 1 ? "1 hour" : `${hours} hours`;
  }
  return `${Math.round(seconds / 60)} minutes`;
}

export function percent(fraction: number): number {
  if (!Number.isFinite(fraction)) return 0;
  return Math.round(Math.min(1, Math.max(0, fraction)) * 100);
}
