import { useBlobUrl } from "../lib/hooks";
import { api } from "../lib/api";

const TINTS = ["#985541", "#B8755D", "#713D31", "#D5A18B"];
/** Decorative motif used when a story has no photograph. Not a photograph of anything. */
export function PatternArt({ seed = 0, className = "" }: { seed?: number; className?: string }) {
  const c = TINTS[seed % TINTS.length];
  return (
    <svg viewBox="0 0 200 120" className={className} role="img" aria-label="Decorative terracotta pattern" preserveAspectRatio="xMidYMid slice">
      <rect width="200" height="120" fill="#E8CFC0" />
      {Array.from({ length: 6 }).map((_, i) => <circle key={i} cx={20 + i * 32} cy={60} r={26 - (i % 3) * 5} fill="none" stroke={c} strokeOpacity={0.55} strokeWidth="2" />)}
      {Array.from({ length: 6 }).map((_, i) => <path key={i} d={`M${4 + i * 32} 112 q12 -30 24 0`} fill="none" stroke={c} strokeOpacity={0.4} strokeWidth="2" />)}
      <circle cx="100" cy="60" r="8" fill={c} fillOpacity={0.7} />
    </svg>
  );
}

export function StoryCover({ imageId, seed, alt = "", className = "h-44 w-full" }: { imageId?: number | null; seed?: number; alt?: string; className?: string }) {
  const { url } = useBlobUrl(imageId ? api.imageFile(imageId) : null);
  if (imageId && url) return <img src={url} alt={alt} className={`${className} object-cover`} loading="lazy" />;
  return <PatternArt seed={seed} className={`${className} object-cover`} />;
}
