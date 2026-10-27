export const SPEAKER_PALETTE_SIZE = 8;

/** Stable 1-based palette slot: "Speaker 3" -> 3; other labels are hashed. */
export function speakerColorIndex(speaker: string): number {
  const match = /(\d+)\s*$/.exec(speaker);
  if (match) {
    const n = Number.parseInt(match[1] ?? "", 10);
    if (n > 0) return ((n - 1) % SPEAKER_PALETTE_SIZE) + 1;
  }
  let hash = 0;
  for (let i = 0; i < speaker.length; i++) hash = (hash * 31 + speaker.charCodeAt(i)) | 0;
  return (Math.abs(hash) % SPEAKER_PALETTE_SIZE) + 1;
}
