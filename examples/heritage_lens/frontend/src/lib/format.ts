export const fmtDuration = (s?: number | null) => {
  if (s == null) return "—";
  const m = Math.floor(s / 60), sec = Math.round(s % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
};
export const fmtDate = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "—");
export const fmtBytes = (n?: number | null) => (n == null ? "—" : n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);
export const placeOf = (s: { village?: string | null; city?: string | null; region?: string | null; country?: string | null }) =>
  [s.village, s.city, s.region, s.country].filter(Boolean).join(", ") || "Location not given";
export const yearLabel = (y: number | null, approx: boolean) => (y == null ? "Date unknown" : `${approx ? "c. " : ""}${y < 0 ? `${-y} BCE` : y}`);
export function download(name: string, content: BlobPart, type = "text/plain;charset=utf-8") {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement("a"); a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
