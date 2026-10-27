import type { CallRecord, Segment } from "../api";
import { formatTimeRange } from "./format";

export function filterSegments(segments: Segment[], query: string): Segment[] {
  const q = query.trim().toLocaleLowerCase();
  if (!q) return segments;
  return segments.filter(
    (s) =>
      s.original_text.toLocaleLowerCase().includes(q) ||
      (s.english_text ?? "").toLocaleLowerCase().includes(q),
  );
}

/** segment_no of the segment playing at `time`, or null. */
export function activeSegmentNo(segments: Segment[], time: number): number | null {
  return segments.find((s) => time >= s.start && time < s.end)?.segment_no ?? null;
}

/** Plain-text transcript, one timestamped line per segment. */
export function transcriptText(record: CallRecord, which: "english" | "original"): string {
  return record.segments
    .map((s) => {
      const text = which === "english" ? s.english_text ?? "(translation not available)" : s.original_text;
      const speaker = s.speaker ? `${s.speaker}: ` : "";
      return `${formatTimeRange(s.start, s.end)} ${speaker}${text}`;
    })
    .join("\n");
}
