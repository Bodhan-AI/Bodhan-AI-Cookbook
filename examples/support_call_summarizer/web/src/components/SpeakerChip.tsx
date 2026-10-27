import { speakerColorIndex } from "../lib/speakers";

export function SpeakerChip({ speaker }: { speaker: string }) {
  return <span className={`speaker-chip speaker-chip--${speakerColorIndex(speaker)}`}>{speaker}</span>;
}
